"""
Per-connection async handler.
Drives the full lifecycle:
  1. Read 64-byte obfuscated init frame
  2. Handle unencrypted MTProto (auth-key generation)
  3. Handle encrypted MTProto (RPC dispatch)
"""
import asyncio
import struct
import logging
import time
from typing import Optional

from . import tl, crypto
from . import faketls
from .transport import ObfuscatedTransport, PacketBuffer, FramingError
from .auth_key  import AuthKeyGenerator, HandshakeError
from .session   import Session

log = logging.getLogger(__name__)


def _decrypt_payload(auth_key: bytes, raw_after_key_id: bytes):
    """
    Decrypt an encrypted MTProto payload (bytes after auth_key_id).
    Returns (salt, session_id, msg_id, seq_no, body).
    """
    msg_key   = raw_after_key_id[:16]
    encrypted = raw_after_key_id[16:]
    aes_key, aes_iv = crypto.derive_message_key(auth_key, msg_key, from_client=True)
    plaintext = crypto.aes_ige_decrypt(encrypted, aes_key, aes_iv)
    salt       = struct.unpack_from('<q', plaintext, 0)[0]
    session_id = struct.unpack_from('<q', plaintext, 8)[0]
    msg_id     = struct.unpack_from('<q', plaintext, 16)[0]
    seq_no     = struct.unpack_from('<i', plaintext, 24)[0]
    msg_len    = struct.unpack_from('<i', plaintext, 28)[0]
    body       = plaintext[32:32 + msg_len]
    return salt, session_id, msg_id, seq_no, body


class Connection:
    def __init__(self, reader, writer, private_key, fingerprint, storage,
                 dispatcher, registry):
        self._reader      = reader
        self._writer      = writer
        self._priv        = private_key
        self._fingerprint = fingerprint
        self._storage     = storage
        self._dispatcher  = dispatcher
        self._registry    = registry

        self._transport  = ObfuscatedTransport()
        self._pkt_buf    = PacketBuffer()
        self._auth_gen   = None
        self._sessions   = {}
        self._auth_key_id: int = 0
        self._user_id: Optional[int] = None
        self._user_connected: bool = False
        self._last_session: Optional[Session] = None
        self._send_lock = asyncio.Lock()
        self._closed = False
        self._tls: Optional[faketls.FakeTlsStream] = None
        self._tls_secret: bytes = b''

        peer = writer.get_extra_info('peername')
        self._peer = f'{peer[0]}:{peer[1]}' if peer else '?'

    @property
    def alive(self) -> bool:
        return not self._closed and not self._writer.is_closing()

    def _close_connection(self, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        log.debug('[%s] Closing connection: %s', self._peer, reason)
        if self._user_id is not None:
            self._registry.detach_user_connection(self._user_id, self)
            if not self._registry.has_live_connections(self._user_id):
                self._storage.set_user_offline(self._user_id)
        if self._auth_key_id:
            self._registry.unregister(self._auth_key_id, self)
        try:
            self._writer.close()
        except Exception:
            pass

    async def run(self):
        log.info('[%s] New connection', self._peer)
        try:
            try:
                head = await asyncio.wait_for(
                    self._reader.readexactly(3), timeout=90)
                if faketls.looks_like_tls(head):
                    if not await self._fake_tls_setup(head):
                        return
                    init_frame = await asyncio.wait_for(
                        self._read_tls_exactly(64), timeout=90)
                else:
                    init_frame = head + await asyncio.wait_for(
                        self._reader.readexactly(61), timeout=90)
            except asyncio.TimeoutError:
                log.warning('[%s] No init frame within 90s', self._peer)
                return
            except asyncio.IncompleteReadError as exc:
                partial = exc.partial or b''
                log.info('[%s] Closed before init frame (%d/64 bytes, '
                         'head=%s)', self._peer, len(partial),
                         partial[:16].hex())
                return
            log.debug('[%s] Init frame head=%s', self._peer,
                      init_frame[:8].hex())
            if not self._transport.is_ready() and \
                    not self._transport.init_from_frame(init_frame):
                log.warning('[%s] Bad init frame head=%s', self._peer,
                            init_frame[:16].hex())
                return
            self._pkt_buf.set_protocol(self._transport.protocol_id)
            self._auth_gen = AuthKeyGenerator(self._priv, self._fingerprint)

            while True:
                raw = await self._read_packet()
                if raw is None:
                    break
                await self._handle_packet(raw)

        except asyncio.IncompleteReadError:
            log.debug('[%s] Disconnected', self._peer)
        except ConnectionResetError:
            log.debug('[%s] Connection reset by peer', self._peer)
        except BrokenPipeError:
            log.debug('[%s] Connection closed', self._peer)
        except OSError as exc:
            if exc.winerror in (64, 10054, 10053):  # reset / aborted
                log.debug('[%s] Connection lost: %s', self._peer, exc)
            else:
                log.exception('[%s] Error: %s', self._peer, exc)
        except Exception as exc:
            log.exception('[%s] Error: %s', self._peer, exc)
        finally:
            if not self._closed:
                self._close_connection('run loop ended')

    # ── fakeTLS (MTProto proxy dd-secret camouflage) ──

    async def _fake_tls_setup(self, head: bytes) -> bool:
        """Parse/verify the ClientHello and answer with the fake handshake."""
        import config as _config
        try:
            hello = head + await self._reader.readexactly(2)
            rec_len = struct.unpack_from('>H', hello, 3)[0]
            hello += await self._reader.readexactly(rec_len)
            frame, session_id, cipher_suite, sni = \
                faketls._parse_client_hello(hello)
            sni_name = sni.decode('ascii', 'ignore').strip().lower()
            last_err: Optional[Exception] = None
            for raw_secret, core, domains in _config.FAKETLS_SECRETS:
                if sni_name not in domains:
                    continue
                try:
                    client_random = faketls.verify_client_hello(frame, core)
                except faketls.FakeTlsError as exc:
                    last_err = exc
                    continue
                response = faketls.build_server_hello(
                    session_id, cipher_suite, client_random, core)
                self._writer.write(response)
                await self._writer.drain()
                self._tls = faketls.FakeTlsStream()
                self._tls.bind(self._reader)
                self._tls_secret = core
                log.info('[%s] fakeTLS hello ok sni=%s', self._peer, sni_name)
                return True
            log.warning('[%s] fakeTLS rejected: no secret for sni=%r (%s)',
                        self._peer, sni_name, last_err)
            return False
        except faketls.FakeTlsError as exc:
            log.warning('[%s] fakeTLS bad hello: %s', self._peer, exc)
            return False
        except (asyncio.IncompleteReadError, OSError) as exc:
            log.debug('[%s] fakeTLS setup aborted: %s', self._peer, exc)
            return False

    async def _read_tls_exactly(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            payload = await self._tls.read_payload()
            buf.extend(payload)
        if not self._transport.is_ready():
            if not self._transport.init_with_secret(
                    bytes(buf[:64]), self._tls_secret):
                raise faketls.FakeTlsError('inner init frame rejected')
        if len(buf) > n:
            self._pkt_buf.feed(self._transport.decrypt(bytes(buf[n:])))
        return bytes(buf[:n])

    # ── I/O ───────────────────────────────────

    async def _read_packet(self):
        while True:
            try:
                pkt = self._pkt_buf.next_packet()
            except FramingError as exc:
                log.warning('[%s] Transport framing error: %s', self._peer, exc)
                return None
            if pkt is not None:
                return pkt
            if self._tls is not None:
                chunk = await self._tls.read_payload()
            else:
                chunk = await self._reader.read(4096)
            if not chunk:
                return None
            self._pkt_buf.feed(self._transport.decrypt(chunk))

    async def _send(self, data: bytes):
        if self._closed:
            raise ConnectionError('connection closed')
        async with self._send_lock:
            if self._closed:
                raise ConnectionError('connection closed')
            if len(data) % 4 != 0:
                data += b'\x00' * (4 - len(data) % 4)
            try:
                payload = self._transport.encrypt(
                    self._pkt_buf.pack_packet(data))
                if self._tls is not None:
                    payload = faketls.FakeTlsStream.wrap(payload)
                self._writer.write(payload)
                await self._writer.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                self._close_connection(str(exc))
                raise

    async def _send_auth_key_not_found(self) -> None:
        """Tell client the auth key is gone so it creates a new one (MTProto -404)."""
        await self._send(struct.pack('<i', -404))

    def _link_user(self) -> None:
        """Refresh user binding after RPC may have bound the session."""
        if not self._auth_key_id:
            return
        current_user_id = self._storage.get_session_user(self._auth_key_id)
        user_id = current_user_id or self._storage.resolve_session_user(self._auth_key_id)
        if not user_id:
            return
        if self._user_id is not None and int(self._user_id) != int(user_id):
            self._registry.detach_user_connection(int(self._user_id), self)
        if current_user_id is None:
            self._storage.bind_session(self._auth_key_id, user_id)
        self._user_id = user_id
        self._registry.bind_user(self._auth_key_id, user_id)
        self._registry.attach_user_connection(user_id, self)
        # Remember which key this user last connected with so offline pushes
        # can be queued for the device that is actually going to reconnect.
        kv_key = f'last_auth_key:{int(user_id)}'
        key_str = str(int(self._auth_key_id))
        try:
            if self._storage.get_server_kv(kv_key) != key_str:
                self._storage.set_server_kv(kv_key, key_str)
        except Exception:
            pass

    # ── Packet routing ────────────────────────

    async def _handle_packet(self, raw: bytes):
        if len(raw) < 8:
            return
        auth_key_id = struct.unpack_from('<q', raw, 0)[0]
        if auth_key_id == 0:
            await self._handle_unencrypted(raw)
        else:
            await self._handle_encrypted(raw, auth_key_id)

    # ── Handshake (unencrypted) ───────────────

    async def _handle_unencrypted(self, raw: bytes):
        _, msg_id, payload = tl.unpack_unencrypted(raw)
        r  = tl.Reader(payload)
        cid = r.u32()
        try:
            if cid in (tl.REQ_PQ, tl.REQ_PQ_MULTI):
                resp = self._auth_gen.handle_req_pq(payload)
                log.info('[%s] Handshake: %s -> resPQ (fp=%#018x)',
                         self._peer,
                         'req_pq' if cid == tl.REQ_PQ else 'req_pq_multi',
                         self._fingerprint)
            elif cid == tl.REQ_DH_PARAMS:
                log.info('[%s] Handshake: req_DH_params', self._peer)
                resp = self._auth_gen.handle_req_dh_params(payload)
            elif cid == tl.SET_CLIENT_DH_PARAMS:
                log.info('[%s] Handshake: set_client_DH_params', self._peer)
                resp = self._auth_gen.handle_set_client_dh(payload)
                ak    = self._auth_gen.auth_key
                ak_id = crypto.auth_key_id(ak)
                self._storage.save_auth_key(ak_id, ak)
                log.info('[%s] Auth key created: %#018x', self._peer, ak_id)
                from .log_filter import mark_auth_key_pending
                mark_auth_key_pending(ak_id, self._peer)
                from .session import make_message_id
                await self._send(tl.pack_unencrypted(make_message_id(), resp))
                return
            elif cid == tl.MSGS_ACK:
                return
            else:
                log.warning('[%s] Unknown unencrypted cid: %#010x', self._peer, cid)
                return
            from .session import make_message_id
            await self._send(tl.pack_unencrypted(make_message_id(), resp))
        except HandshakeError as exc:
            log.warning('[%s] Handshake rejected: %s — reset for retry',
                        self._peer, exc)
            self._auth_gen = AuthKeyGenerator(self._priv, self._fingerprint)
        except Exception as exc:
            log.exception('[%s] Handshake error: %s', self._peer, exc)
            self._auth_gen = AuthKeyGenerator(self._priv, self._fingerprint)

    # ── Encrypted messages ────────────────────

    async def _handle_encrypted(self, raw: bytes, auth_key_id: int):
        from .log_filter import mark_auth_key_encrypted
        mark_auth_key_encrypted(auth_key_id)
        log.debug('[%s] Encrypted packet auth_key_id=%#018x len=%d',
                  self._peer, auth_key_id, len(raw))
        auth_key = self._storage.get_auth_key(auth_key_id)
        if auth_key is None:
            log.warning('[%s] Unknown auth_key_id %#018x — auth key not found',
                        self._peer, auth_key_id)
            await self._send_auth_key_not_found()
            # Avoid staying on a desynchronized encrypted stream with random key ids.
            try:
                self._writer.close()
            except Exception:
                pass
            return

        if self._auth_key_id != auth_key_id:
            if self._auth_key_id:
                self._registry.unregister(self._auth_key_id, self)
            self._auth_key_id = auth_key_id
            self._user_connected = False
            self._user_id = None
            self._registry.register(auth_key_id, self)
        self._link_user()
        user_id = self._user_id

        try:
            client_salt, session_id, msg_id, seq_no, body = _decrypt_payload(
                auth_key, raw[8:])
        except Exception as exc:
            log.warning('[%s] Decrypt failed auth_key_id=%#018x: %s',
                        self._peer, auth_key_id, exc)
            return

        # Get or create session
        sess = self._sessions.get(session_id)
        is_new_session = sess is None
        if is_new_session:
            sess = Session(auth_key, session_id)
            sess.connection_peer = self._peer
            try:
                from .client_info import hydrate_auth_key_info
                hydrate_auth_key_info(self._storage, sess)
            except Exception:
                pass
            self._sessions[session_id] = sess
            log.info('[%s] New session %#018x salt=%#018x',
                     self._peer, session_id, sess.salt)
        elif not sess.connection_peer:
            sess.connection_peer = self._peer

        reply_salt = None
        if client_salt != sess.salt:
            if client_salt != 0:
                log.info('[%s] adopt client salt %#018x (was %#018x)',
                         self._peer, client_salt, sess.salt)
                sess.salt = client_salt
            else:
                reply_salt = 0
                log.debug('[%s] client salt=0, replying with salt=0',
                          self._peer)
        elif client_salt == 0:
            reply_salt = 0

        body_cid = 0
        if len(body) >= 4:
            body_cid = struct.unpack_from('<I', body, 0)[0]
        if body_cid in (tl.PING, tl.PING_DELAY_DISCONNECT, tl.MSGS_ACK):
            log.debug('[%s] %s msg_id=%#018x seq=%d',
                      self._peer,
                      'ping' if body_cid != tl.MSGS_ACK else 'msgs_ack',
                      msg_id, seq_no)
        else:
            log.info('[%s] msg cid=%#010x msg_id=%#018x seq=%d body=%d',
                     self._peer, body_cid, msg_id, seq_no, len(body))

        if getattr(sess, 'pending_new_session', False):
            ns_reply = reply_salt
            if ns_reply is None and client_salt != sess.salt:
                ns_reply = client_salt if client_salt != 0 else 0
            await self._send(sess.encrypt_new_session(msg_id, reply_salt=ns_reply))
            sess.pending_new_session = False
            # new_session_created carries the real salt and is readable with the
            # old one.  The client applies it immediately, so every packet that
            # follows in this batch (the RPC result included) must be encrypted
            # with sess.salt — otherwise the client cannot decrypt it, silently
            # drops it and the login screen spins forever.
            #
            # Exception: TDesktop flips its session into ConnectedState only
            # when a packet arrives whose envelope salt differs from the salt
            # it just read from new_session_created.  Auxiliary (download)
            # sessions start with salt 0 and would otherwise stay in
            # "send only ping" forever — no upload.getFile is ever issued and
            # media / gift-upgrade animations hang.  Keep salt 0 in the reply
            # for TDesktop so that transition fires.
            is_tdesktop = (
                (getattr(sess, 'init_lang_pack', '') or '').lower() == 'tdesktop'
            )
            if ns_reply == 0 and not is_tdesktop:
                reply_salt = None
        self._last_session = sess

        if user_id and not self._user_connected:
            self._user_connected = True
            self._storage.set_user_online(user_id)
            # Do not drain pending_updates before the first RPC: a burst of
            # channel updates here blocks getState/getDialogs and leaves the
            # chat list stuck on Loading.
        if user_id:
            try:
                from .client_info import remember_user
                remember_user(sess, user_id)
            except Exception:
                pass

        await self._handle_decrypted(sess, msg_id, seq_no, body, reply_salt=reply_salt)

    # ── Decrypted payload ─────────────────────

    async def _handle_decrypted(self, sess: Session, msg_id: int, seq_no: int,
                                body: bytes, *, reply_salt: Optional[int] = None):
        body = tl.maybe_ungzip(body)
        r = tl.Reader(body)
        cid = r.u32()

        if cid == tl.MSG_CONTAINER:
            from handlers.push import PushQueue
            push_queue = PushQueue(self._registry, self._storage)
            count = r.i32()
            log.info('[%s] DBG container count=%d hex=%s',
                     self._peer, count, body.hex())
            for _ in range(count):
                i_msg_id = r.i64()
                i_seq    = r.i32()
                i_len    = r.i32()
                i_body   = r.read(i_len)
                await self._process_msg(sess, i_msg_id, i_seq, i_body,
                                        push_queue=push_queue,
                                        reply_salt=reply_salt)
            if push_queue._pending:
                await push_queue.flush()
        else:
            await self._process_msg(sess, msg_id, seq_no, body,
                                    reply_salt=reply_salt)

    async def _process_msg(self, sess: Session, msg_id: int, seq_no: int,
                           body: bytes, push_queue=None, *,
                           reply_salt: Optional[int] = None):
        try:
            body = tl.maybe_ungzip(body)
            r = tl.Reader(body)
            cid = r.u32()
            if cid not in (tl.PING, tl.PING_DELAY_DISCONNECT, tl.MSGS_ACK, tl.GET_FUTURE_SALTS):
                log.debug('[%s] msg cid=%#010x', self._peer, cid)

            if cid in (tl.PING, tl.PING_DELAY_DISCONNECT):
                ping_id = r.i64()
                if cid == tl.PING_DELAY_DISCONNECT:
                    r.i32()  # disconnect_delay
                await self._send(sess.encrypt_pong(msg_id, ping_id, salt=reply_salt))

            elif cid == tl.GET_FUTURE_SALTS:
                await self._send(sess.encrypt_future_salts(msg_id, salt=reply_salt))

            elif cid == tl.MSGS_ACK:
                pass  # ignore

            elif cid == tl.MSGS_STATE_REQ:
                assert r.u32() == tl.VECTOR
                count = r.i32()
                for _ in range(count):
                    r.i64()
                w = tl.Writer()
                w.u32(tl.MSGS_STATE_INFO)
                w.i64(msg_id)
                w.tl_bytes(bytes([1]) * count)
                await self._send(
                    sess.encrypt(w.bytes_out(), content_related=False,
                                 salt=reply_salt))

            elif cid == tl.DESTROY_SESSION:
                sid = r.i64()
                w = tl.Writer()
                w.u32(tl.DESTROY_SESSION_OK)
                w.i64(sid)
                await self._send(sess.encrypt(w.bytes_out(), salt=reply_salt))

            else:
                local_queue = push_queue is None
                if local_queue:
                    from handlers.push import PushQueue
                    push_queue = PushQueue(self._registry, self._storage)
                from handlers import packers as _packers
                from .client_info import (
                    hydrate_user_layer, persist_auth_key_info,
                    persist_user_layer, resolve_session_layer,
                )
                layer = resolve_session_layer(sess)
                if not layer and self._user_id:
                    layer = hydrate_user_layer(self._storage, self._user_id)
                    if layer:
                        sess.client_layer = layer
                if layer and self._user_id:
                    persist_user_layer(self._storage, self._user_id, layer)
                layer_token = _packers.set_pack_layer(layer)
                _rpc_t0 = time.monotonic()
                try:
                    result = self._dispatcher(
                        sess, msg_id, body, self._storage, push_queue,
                        self._registry)
                finally:
                    _packers.reset_pack_layer(layer_token)
                _rpc_dt = time.monotonic() - _rpc_t0
                if _rpc_dt >= 0.25:
                    _cid = int.from_bytes(body[:4], 'little')
                    log.warning('[%s] Slow RPC cid=%#010x %.3fs user=%s',
                                self._peer, _cid, _rpc_dt, self._user_id)
                if result is not None:
                    await self._send(sess.encrypt_rpc_result(msg_id, result,
                                                             salt=reply_salt))
                self._link_user()
                if self._user_id:
                    learned = resolve_session_layer(sess)
                    if learned:
                        persist_user_layer(self._storage, self._user_id,
                                           learned)
                persist_auth_key_info(self._storage, sess)
                if self._user_id and not self._user_connected:
                    self._user_connected = True
                    self._storage.set_user_online(self._user_id)
                if local_queue and push_queue._pending:
                    await push_queue.flush()
        except ConnectionError as exc:
            # Peer closed connection while we were still processing a
            # container; this is expected on mobile reconnects.
            log.debug('[%s] Stop processing on closed connection: %s',
                     self._peer, exc)
        except Exception as exc:
            log.exception('[%s] Message processing error: %s', self._peer, exc)
            # Never leave the shared connection inside a write transaction:
            # it would lock every other process out of the database.
            try:
                if self._storage._conn.in_transaction:
                    self._storage._conn.rollback()
            except Exception:
                pass

    async def _flush_pending_updates(self, user_id: int,
                                     auth_key_id: int = 0) -> None:
        from handlers.push import PushQueue, PENDING_UPDATES_DRAIN_LIMIT
        push_queue = PushQueue(self._registry, self._storage)
        push_queue.flush_pending_for_user(
            user_id, auth_key_id=auth_key_id or self._auth_key_id or None,
            limit=PENDING_UPDATES_DRAIN_LIMIT)
        await push_queue.flush()

    async def push_encrypted(self, update_body: bytes) -> None:
        """Deliver server push on the best available session for this auth key."""
        if not self._sessions:
            from handlers.push import PushDeliveryError
            raise PushDeliveryError('no MTProto session on connection')
        order = []
        if self._last_session is not None:
            order.append(self._last_session)
        for sess in self._sessions.values():
            if sess not in order:
                order.append(sess)
        last_exc: Exception | None = None
        for sess in order:
            try:
                await self._send(sess.encrypt(update_body, content_related=False))
                self._last_session = sess
                return
            except Exception as exc:
                last_exc = exc
                log.warning('Push on session %#018x failed: %s',
                            sess.session_id, exc)
        from handlers.push import PushDeliveryError
        raise PushDeliveryError(
            f'push not delivered on any session ({len(self._sessions)} open): '
            f'{last_exc}')
