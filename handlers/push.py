"""Connection registry and push queue for server-initiated updates."""
import json
import logging
import time
from typing import Dict, List, Optional, Set, TYPE_CHECKING

import config

if TYPE_CHECKING:
    from mtproto.connection import Connection
    from db.storage import Storage

log = logging.getLogger(__name__)

# Drain a small batch at a time so reconnecting clients receive messages
# promptly without the large burst that previously hung the UI.
PENDING_UPDATES_DRAIN_LIMIT = 5
PENDING_UPDATES_DRAIN_PER_RPC = 0
PENDING_UPDATES_MAX_QUEUE = 50
_PUSH_SUPPRESS_UNTIL: Dict[int, float] = {}


def suppress_user_pushes(user_id: int, *, seconds: int = 3) -> None:
    """Temporarily mute server-initiated pushes for a user."""
    _PUSH_SUPPRESS_UNTIL[int(user_id)] = time.time() + max(0, int(seconds))


def _is_push_suppressed(user_id: int) -> bool:
    until = _PUSH_SUPPRESS_UNTIL.get(int(user_id), 0.0)
    now = time.time()
    if until <= now:
        if until:
            _PUSH_SUPPRESS_UNTIL.pop(int(user_id), None)
        return False
    return True


class PushDeliveryError(Exception):
    """Push could not be delivered on this connection."""


def connection_layer(conn: 'Connection') -> int:
    """TL layer of the client behind a connection (0 when unknown)."""
    sess = getattr(conn, '_last_session', None)
    if sess is None:
        sessions = getattr(conn, '_sessions', None)
        if isinstance(sessions, dict) and sessions:
            sess = next(iter(sessions.values()))
    layer = int(getattr(sess, 'client_layer', 0) or 0)
    if layer:
        return layer
    # Reconnected sessions do not repeat initConnection/invokeWithLayer.
    from mtproto.client_info import known_auth_key_layer, known_user_layer
    layer = known_auth_key_layer(int(getattr(conn, '_auth_key_id', 0) or 0))
    if layer:
        return layer
    layer = known_user_layer(int(getattr(conn, '_user_id', 0) or 0))
    return layer


def _queued_layer(user_id: int, storage=None) -> int:
    """Layer used for stored pending updates of an offline user."""
    from mtproto.client_info import hydrate_user_layer, known_user_layer
    user_id = int(user_id or 0)
    layer = known_user_layer(user_id)
    if not layer and storage is not None:
        layer = hydrate_user_layer(storage, user_id)
    return layer


def _auth_key_layer(auth_key_id: int, storage=None) -> int:
    """Last known TL layer of one auth key (survives restarts via SQLite)."""
    from mtproto.client_info import known_auth_key_layer
    auth_key_id = int(auth_key_id or 0)
    if not auth_key_id:
        return 0
    layer = known_auth_key_layer(auth_key_id)
    if layer or storage is None:
        return layer
    try:
        raw = storage.get_server_kv(f'client_info:{auth_key_id}', '')
        info = json.loads(raw) if raw else {}
        layer = int(info.get('client_layer') or 0)
    except Exception:
        return 0
    return layer if 100 <= layer <= 1000 else 0


def _resolve_push(data, layer: int) -> bytes:
    if callable(data):
        return data(layer)
    return data


class ConnectionRegistry:
    def __init__(self):
        self._by_auth_key: Dict[int, 'Connection'] = {}
        self._by_user: Dict[int, Set[int]] = {}
        self._connections_by_user: Dict[int, Set['Connection']] = {}

    def register(self, auth_key_id: int, conn: 'Connection') -> None:
        self._by_auth_key[auth_key_id] = conn

    def unregister(self, auth_key_id: int,
                   conn: Optional['Connection'] = None) -> None:
        current = self._by_auth_key.get(auth_key_id)
        closing = conn or current
        if closing is not None:
            user_id = getattr(closing, '_user_id', None)
            if user_id is not None:
                self.detach_user_connection(user_id, closing)
        # A different TCP connection may use the same Telegram auth key.
        if conn is not None and current is not None and current is not conn:
            return

        self._by_auth_key.pop(auth_key_id, None)
        replacement = None
        for conns in self._connections_by_user.values():
            candidates = [
                candidate for candidate in conns
                if candidate is not closing
                and getattr(candidate, 'alive', True)
                and int(getattr(candidate, '_auth_key_id', 0)) == auth_key_id
            ]
            if candidates:
                replacement = max(
                    candidates,
                    key=lambda candidate: (
                        bool(getattr(candidate, '_last_session', None)),
                        len(getattr(candidate, '_sessions', {})),
                    ),
                )
                break
        if replacement is not None:
            self._by_auth_key[auth_key_id] = replacement
            replacement_user = getattr(replacement, '_user_id', None)
            if replacement_user is not None:
                self._by_user.setdefault(replacement_user, set()).add(auth_key_id)
            return

        for user_id, keys in list(self._by_user.items()):
            keys.discard(auth_key_id)
            if not keys:
                del self._by_user[user_id]

    def bind_user(self, auth_key_id: int, user_id: int) -> None:
        self._by_user.setdefault(user_id, set()).add(auth_key_id)

    def attach_user_connection(self, user_id: int, conn: 'Connection') -> None:
        """Track live TCP connection for instant push delivery."""
        self._connections_by_user.setdefault(user_id, set()).add(conn)

    def detach_user_connection(self, user_id: int, conn: 'Connection') -> None:
        conns = self._connections_by_user.get(user_id)
        if not conns:
            return
        conns.discard(conn)
        if not conns:
            del self._connections_by_user[user_id]

    def prune_dead_connections(self, user_id: int) -> None:
        conns = self._connections_by_user.get(user_id)
        if not conns:
            return
        dead = [conn for conn in conns if not getattr(conn, 'alive', True)]
        for conn in dead:
            conns.discard(conn)
        if not conns:
            del self._connections_by_user[user_id]

    def has_live_connections(self, user_id: int) -> bool:
        conns = self._connections_by_user.get(user_id)
        if not conns:
            return False
        return any(getattr(conn, 'alive', True) for conn in conns)

    def get_connection(self, auth_key_id: int) -> Optional['Connection']:
        return self._by_auth_key.get(auth_key_id)

    def get_connections_for_user(self, user_id: int,
                                 storage: Optional['Storage'] = None
                                 ) -> list[tuple['Connection', int]]:
        """Choose the best live MTProto session for each auth key."""
        chosen: Dict[int, 'Connection'] = {}

        def consider(conn: 'Connection', auth_key_id: int) -> None:
            if not auth_key_id or not getattr(conn, 'alive', True):
                return
            bound_user = getattr(conn, '_user_id', None)
            if bound_user is not None and int(bound_user) != int(user_id):
                return
            current = chosen.get(auth_key_id)
            if current is None:
                chosen[auth_key_id] = conn
                return
            current_score = (
                bool(getattr(current, '_last_session', None)),
                len(getattr(current, '_sessions', {})),
            )
            candidate_score = (
                bool(getattr(conn, '_last_session', None)),
                len(getattr(conn, '_sessions', {})),
            )
            if candidate_score > current_score:
                chosen[auth_key_id] = conn

        for conn in self._connections_by_user.get(user_id, set()):
            consider(conn, int(getattr(conn, '_auth_key_id', 0)))

        for auth_key_id in self._by_user.get(user_id, set()):
            conn = self._by_auth_key.get(auth_key_id)
            if conn is not None:
                consider(conn, auth_key_id)

        if storage is not None:
            for auth_key_id in storage.get_user_auth_keys(user_id):
                conn = self._by_auth_key.get(auth_key_id)
                if conn is not None:
                    consider(conn, auth_key_id)

        return [(conn, auth_key_id) for auth_key_id, conn in chosen.items()]


class PushQueue:
    def __init__(self, registry: ConnectionRegistry,
                 storage: Optional['Storage'] = None):
        self._registry = registry
        self._storage = storage
        self._pending: list[tuple[int, 'Connection', int, bytes]] = []

    def _pending_layer(self, user_id: int,
                       conn: Optional['Connection'] = None) -> int:
        """Best TL layer for updates stored while the user is online."""
        if conn is not None:
            layer = connection_layer(conn)
            if layer:
                return layer
        if self._storage is not None:
            for live, _key in self._registry.get_connections_for_user(
                    int(user_id), self._storage):
                layer = connection_layer(live)
                if layer:
                    return layer
        return _queued_layer(user_id, self._storage)

    def push_to_user(self, user_id: int, update_bytes,
                     exclude_auth_key_id: Optional[int] = None,
                     *, allow_enqueue: bool = True) -> bool:
        """Deliver an update.

        ``update_bytes`` may be either raw TL bytes or a callable
        ``build(layer) -> bytes``.  A callable is evaluated per connection so
        every client receives constructors matching its own TL layer
        (e.g. user#b1b8cc83 for Desktop 7.2.5 / layer 228).
        """
        if int(user_id) >= config.CHANNEL_ID_MIN:
            return False
        if _is_push_suppressed(user_id):
            log.info('Push suppressed for user %s', int(user_id))
            if allow_enqueue:
                self._enqueue_for_user(user_id, update_bytes)
            return False
        self._registry.prune_dead_connections(user_id)
        online_keys: set[int] = set()
        for conn, auth_key_id in self._registry.get_connections_for_user(
                user_id, self._storage):
            if exclude_auth_key_id is not None and auth_key_id == exclude_auth_key_id:
                continue
            self._pending.append((user_id, conn, auth_key_id, update_bytes))
            online_keys.add(auth_key_id)

        if allow_enqueue and not online_keys:
            self._enqueue_for_user(user_id, update_bytes)

        if online_keys:
            return True
        log.debug('No online connection for user %s', user_id)
        return False

    def _enqueue_for_user(self, user_id: int, update_bytes) -> None:
        """Store a queued push once per active client layer, if possible.

        A user can have several auth keys with different TL layers (e.g.
        Android 225 + Desktop 229). Prefer live connections so a fresh auth
        key receives a row encoded for its actual layer. If the user is
        offline, use recently known auth keys as a reconnect fallback.
        """
        if self._storage is None:
            return
        user_id = int(user_id)
        fallback_layer = _queued_layer(user_id, self._storage)
        layers: dict[int, int] = {}
        live_connections = self._registry.get_connections_for_user(
            user_id, self._storage)
        for conn, key in live_connections:
            layer = int(connection_layer(conn) or 0)
            if not layer:
                layer = int(_auth_key_layer(int(key), self._storage) or 0)
            if layer >= 100:
                layers.setdefault(layer, layer)

        # Historical keys are useful when the user is offline, but a stale
        # device layer must not outrank the layer of a currently live client.
        if not layers:
            layers = {}
            try:
                stored_keys = self._storage.get_recent_user_auth_keys(user_id, 6)
            except Exception:
                stored_keys = []
            try:
                last_key = int(self._storage.get_server_kv(
                    f'last_auth_key:{user_id}') or 0)
            except (TypeError, ValueError):
                last_key = 0
            except Exception:
                last_key = 0
            keys: List[int] = []
            seen: set[int] = set()
            for key in ([last_key]
                        + list(self._registry._by_user.get(user_id, ()))
                        + list(stored_keys)):
                key = int(key)
                if key and key not in seen:
                    seen.add(key)
                    keys.append(key)
            for key in keys:
                layer = int(_auth_key_layer(key, self._storage) or 0)
                if layer >= 100:
                    layers.setdefault(layer, layer)
        if not layers:
            layers[fallback_layer or 0] = fallback_layer or 0
        for layer in layers:
            self._storage.enqueue_pending_update(
                user_id, _resolve_push(update_bytes, layer), layer=layer)

    def flush_pending_for_user(self, user_id: int,
                               exclude_auth_key_id: Optional[int] = None,
                               auth_key_id: Optional[int] = None,
                               *, limit: int = PENDING_UPDATES_DRAIN_LIMIT
                               ) -> int:
        if self._storage is None:
            return 0
        if _is_push_suppressed(user_id):
            return 0
        connections = [
            (conn, key_id)
            for conn, key_id in self._registry.get_connections_for_user(
                user_id, self._storage)
            if exclude_auth_key_id is None or key_id != exclude_auth_key_id
        ]
        if auth_key_id is not None:
            connections = [
                (conn, key_id) for conn, key_id in connections
                if key_id == auth_key_id
            ]
        if not connections:
            return 0
        return self._drain_compatible_pending(
            user_id, connections, limit=limit)

    def _drain_compatible_pending(self, user_id: int,
                                  connections: list, *, limit: int
                                  ) -> list:
        """Route queued updates to a connection that can consume them.

        Rows are stored per target auth key (packed for that key's layer).
        Devices routinely reconnect with a FRESH key, so matching rows by
        exact auth_key_id orphans them until the 300s purge — the message
        then only reaches the client through a later getDifference (i.e.
        after the peer sends something new).  Routing order per row:
          1. a live connection holding the row's own key;
          2. a live connection whose client layer equals the row key's
             layer (same device class reconnected under a new key);
          3. after a 60s grace, any live connection (best effort — the
             purge would otherwise drop it unseen).

        Appends routed rows straight onto ``self._pending`` and returns how
        many were routed.
        """
        rows = self._storage.peek_pending_updates(
            user_id, limit=max(limit * 8, 40))
        if not rows:
            return []
        now = time.time()
        layer_conns: dict[int, list] = {}
        all_conns: list = []
        for conn, key in connections:
            layer = int(_auth_key_layer(int(key), self._storage) or 0) \
                or int(connection_layer(conn) or 0)
            all_conns.append(conn)
            layer_conns.setdefault(layer, []).append(conn)

        consumed_ids: set = set()
        for row in rows:
            if limit > 0 and len(consumed_ids) >= limit:
                break
            row_id = int(row['id'])
            if row_id in consumed_ids:
                continue
            data = row['update_data']
            row_layer = int(row.get('layer') or 0)
            if not row_layer:
                # legacy row stored per auth key: resolve its layer
                row_key = int(row.get('auth_key_id') or 0)
                if row_key:
                    row_layer = int(
                        _auth_key_layer(row_key, self._storage) or 0)
            targets = layer_conns.get(row_layer)
            if not targets:
                targets = layer_conns.get(0)
            if not targets:
                # unknown/legacy layer with no exact match: after a grace
                # period deliver to every connection — the purge would
                # otherwise drop the row unseen.
                if now - float(row.get('created_at') or 0) >= 60:
                    targets = all_conns
                else:
                    continue
            for conn in targets:
                self._pending.append((user_id, conn, 0, data))
            consumed_ids.add(row_id)
        self._storage.delete_pending_updates(list(consumed_ids))
        return len(consumed_ids)

    async def flush(self) -> None:
        for user_id, conn, auth_key_id, data in self._pending:
            try:
                payload = _resolve_push(data, connection_layer(conn))
                await conn.push_encrypted(payload)
                log.debug('Push delivered to user %s via %s',
                         user_id, conn._peer)
            except PushDeliveryError as exc:
                log.warning('Push to user %s via %s not ready: %s',
                            user_id, conn._peer, exc)
                if self._storage is not None:
                    payload = _resolve_push(
                        data, self._pending_layer(user_id, conn))
                    pending = self._storage._count_all_pending_updates(user_id)
                    if pending < PENDING_UPDATES_MAX_QUEUE:
                        self._storage.enqueue_pending_update(
                            user_id, payload, auth_key_id=auth_key_id)
            except Exception as exc:
                log.warning('Push to user %s via %s failed: %s',
                            user_id, conn._peer, exc)
                if self._storage is not None:
                    payload = _resolve_push(
                        data, self._pending_layer(user_id, conn))
                    pending = self._storage._count_all_pending_updates(user_id)
                    if pending < PENDING_UPDATES_MAX_QUEUE:
                        self._storage.enqueue_pending_update(
                            user_id, payload, auth_key_id=auth_key_id)
        self._pending.clear()
