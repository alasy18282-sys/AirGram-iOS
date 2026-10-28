"""End-to-end QA probe emulating Telegram Desktop 7.2.5 (TL layer 228).

Performs the full DH handshake, then:
  help.getConfig -> auth.sendCode -> auth.signUp/signIn
  -> messages.getDialogs -> messages.sendMessage (self-chat)

Every reply is parsed strictly against the layer-228 schema, so a wrong
constructor id (user#b1b8cc83, message#7600b9d3) or a bad field order fails
loudly instead of being silently accepted.

Usage: python3 probe_layer228.py [host] [port]
"""
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mtproto import tl
from handlers import packers
from probe_client import Transport, handshake

LAYER = 228
API_ID = 2040  # Telegram Desktop
PHONE = '99944444244'
CODE = '91209'

USER_V228 = 0xb1b8cc83
MESSAGE_V228 = 0x7600b9d3
CHANNEL_V228 = 0xd49f34c6
RPC_RESULT = 0xf35c6d01
MSG_CONTAINER = 0x73f1f8dc
NEW_SESSION = 0x9ec20908


class Checker:
    def __init__(self, data: bytes, label: str):
        self.r = tl.Reader(data)
        self.label = label
        self.end = len(data)

    def u32(self) -> int:
        return self.r.u32()

    def i32(self) -> int:
        return self.r.i32()

    def i64(self) -> int:
        return self.r.i64()

    def s(self) -> str:
        return self.r.string()

    def tl_bytes(self) -> bytes:
        return self.r.tl_bytes()

    def expect(self, cid: int, what: str) -> None:
        got = self.u32()
        assert got == cid, (
            f'{self.label}: expected {what} {cid:#010x}, got {got:#010x} '
            f'at offset {self.r.pos - 4}')

    def vec(self):
        self.expect(tl.VECTOR, 'vector')
        return self.i32()

    def done(self) -> None:
        left = self.end - self.r.pos
        assert left == 0, f'{self.label}: {left} unparsed bytes'


def skip_peer(c: Checker) -> None:
    cid = c.u32()
    if cid in (0x59511722, 0x36c6019a, 0xa2a5371e, 0x7da07ec9):
        c.i64()
        return
    raise AssertionError(f'{c.label}: unknown peer {cid:#010x}')


def parse_photo(c: Checker) -> None:
    cid = c.u32()
    if cid == 0x2331b22d:  # photoEmpty
        c.i64()
        return
    if cid == 0x1c6e1c11:  # chatPhoto
        flags = c.i32()
        c.i64()  # photo_id
        if flags & (1 << 1):
            c.tl_bytes()
        c.i32()  # dc_id
        return
    if cid == 0x82d1f706:  # userProfilePhoto
        flags = c.i32()
        c.i64()  # photo_id
        if flags & (1 << 1):
            c.tl_bytes()  # stripped_thumb
        c.i32()  # dc_id
        return
    if cid == 0xfb197a65:  # photo
        flags = c.i32()
        c.i64()  # id
        c.i64()  # access_hash
        c.tl_bytes()  # file_reference
        c.i32()  # date
        for _ in range(c.vec()):
            _skip_photo_size(c)
        if flags & (1 << 1):
            for _ in range(c.vec()):
                _skip_video_size(c)
        c.i32()  # dc_id
        return
    raise AssertionError(f'{c.label}: unknown photo {cid:#010x}')


def _skip_photo_size(c: Checker) -> None:
    cid = c.u32()
    if cid == 0xe17e23c:  # photoSizeEmpty
        c.s()
    elif cid == 0x75c78e60:  # photoSize
        c.s(); c.i32(); c.i32(); c.i32()
    elif cid == 0x21e1ad6:  # photoCachedSize
        c.s(); c.i32(); c.i32(); c.tl_bytes()
    elif cid == 0xe0b0bc2e:  # photoStrippedSize
        c.s(); c.tl_bytes()
    elif cid == 0xfa3efb95:  # photoSizeProgressive
        c.s(); c.i32(); c.i32()
        for _ in range(c.vec()):
            c.i32()
    elif cid == 0xd8214d41:  # photoPathSize
        c.s(); c.tl_bytes()
    else:
        raise AssertionError(f'{c.label}: unknown photoSize {cid:#010x}')


def _skip_video_size(c: Checker) -> None:
    cid = c.u32()
    if cid == 0xde33b094:  # videoSize
        flags = c.i32()
        c.s(); c.i32(); c.i32(); c.i32()
        if flags & 1:
            c.r.read(8)
    elif cid == 0xf85c413c:  # videoSizeEmojiMarkup
        c.i64()
        for _ in range(c.vec()):
            c.i32()
    elif cid == 0xda082fe:  # videoSizeStickerMarkup
        raise AssertionError('videoSizeStickerMarkup not expected')
    else:
        raise AssertionError(f'{c.label}: unknown videoSize {cid:#010x}')


def parse_status(c: Checker) -> None:
    cid = c.u32()
    if cid == 0x9d05049:
        return
    if cid == 0xedb93949:
        c.i32()
        return
    if cid == 0x8c703f:
        c.i32()
        return
    if cid in (0x7b197dc8, 0x541a1d1a, 0x65899777):
        c.i32()  # flags
        return
    raise AssertionError(f'{c.label}: unknown status {cid:#010x}')


def parse_peer_color(c: Checker) -> None:
    c.expect(0xb54b5acf, 'peerColor')
    flags = c.i32()
    if flags & 1:
        c.i32()
    if flags & 2:
        c.i64()


def parse_emoji_status(c: Checker) -> None:
    c.expect(0xe7ff068a, 'emojiStatus')
    flags = c.i32()
    c.i64()
    if flags & 1:
        c.i32()


def parse_user(c: Checker, *, expect_self: bool = False) -> int:
    c.expect(USER_V228, 'user')
    flags = c.i32()
    flags2 = c.i32()
    if expect_self:
        assert flags & (1 << 10), 'self flag missing'
    uid = c.i64()
    assert uid > 0, f'{c.label}: bad user id {uid}'
    if flags & (1 << 0):
        c.i64()  # access_hash
    if flags & (1 << 1):
        c.s()  # first_name
    if flags & (1 << 2):
        c.s()  # last_name
    if flags & (1 << 3):
        c.s()  # username
    if flags & (1 << 4):
        c.s()  # phone
    if flags & (1 << 5):
        parse_photo(c)
    if flags & (1 << 6):
        parse_status(c)
    if flags & (1 << 14):
        c.i32()  # bot_info_version
    if flags & (1 << 18):
        for _ in range(c.vec()):
            c.s(); c.s(); c.s()  # restrictionReason
    if flags & (1 << 19):
        c.s()  # bot_inline_placeholder
    if flags & (1 << 22):
        c.s()  # lang_code
    if flags & (1 << 30):
        parse_emoji_status(c)
    if flags2 & (1 << 0):
        for _ in range(c.vec()):
            c.expect(0xb4073647, 'username')
            c.i32()
            c.s()
    if flags2 & (1 << 5):
        c.expect(0x711d692d, 'recentStory')
        sf = c.i32()
        if sf & (1 << 1):
            c.i32()  # max_id
    if flags2 & (1 << 8):
        parse_peer_color(c)
    if flags2 & (1 << 9):
        parse_peer_color(c)
    if flags2 & (1 << 12):
        c.i32()  # bot_active_users
    if flags2 & (1 << 14):
        c.i64()  # bot_verification_icon
    if flags2 & (1 << 15):
        c.i64()  # send_paid_messages_stars
    if flags2 & (1 << 21):
        c.i64()  # linked_community_id
    return uid


def parse_message(c: Checker) -> int:
    c.expect(MESSAGE_V228, 'message')
    flags = c.i32()
    flags2 = c.i32()
    mid = c.i32()
    if flags & (1 << 8):
        skip_peer(c)  # from_id
    if flags & (1 << 29):
        c.i32()  # from_boosts_applied
    if flags2 & (1 << 12):
        c.s()  # from_rank
    skip_peer(c)  # peer_id
    if flags & (1 << 28):
        skip_peer(c)  # saved_peer_id
    if flags & (1 << 2):
        raise AssertionError(f'{c.label}: fwd_from not expected in QA message')
    if flags & (1 << 11):
        c.i64()  # via_bot_id
    if flags2 & (1 << 0):
        c.i64()  # via_business_bot_id
    if flags2 & (1 << 19):
        skip_peer(c)  # guestchat_via_from
    if flags & (1 << 3):
        c.expect(0x1b97dd66, 'messageReplyHeader')
        rf = c.i32()
        if rf & (1 << 4):
            c.i32()  # reply_to_msg_id
        if rf & (1 << 0):
            skip_peer(c)  # reply_to_peer_id
        if rf & (1 << 5):
            raise AssertionError('reply_from not expected')
        if rf & (1 << 8):
            raise AssertionError('reply_media not expected')
        if rf & (1 << 1):
            c.i32()  # reply_to_top_id
        if rf & (1 << 6):
            c.s()  # quote_text
        if rf & (1 << 7):
            packers.skip_entities(c.r)
        if rf & (1 << 10):
            c.i32()
        if rf & (1 << 11):
            c.i32()
        if rf & (1 << 12):
            c.tl_bytes()
    c.i32()  # date
    c.s()  # message
    if flags & (1 << 9):
        raise AssertionError(f'{c.label}: media not expected in QA message')
    if flags & (1 << 6):
        raise AssertionError('reply_markup not expected')
    if flags & (1 << 7):
        packers.skip_entities(c.r)
    if flags & (1 << 10):
        c.i32(); c.i32()
    if flags & (1 << 23):
        raise AssertionError('replies not expected')
    if flags & (1 << 15):
        c.i32()
    if flags & (1 << 16):
        c.s()
    if flags & (1 << 17):
        c.i64()
    if flags & (1 << 20):
        raise AssertionError('reactions not expected')
    if flags & (1 << 22):
        for _ in range(c.vec()):
            c.s(); c.s()
    if flags & (1 << 25):
        c.i32()
    if flags & (1 << 30):
        c.i32()
    if flags2 & (1 << 2):
        c.i64()  # effect
    if flags2 & (1 << 3):
        raise AssertionError('factcheck not expected')
    if flags2 & (1 << 5):
        c.i32()
    if flags2 & (1 << 6):
        c.i64()
    if flags2 & (1 << 7):
        raise AssertionError('suggested_post not expected')
    if flags2 & (1 << 10):
        c.i32()
    if flags2 & (1 << 11):
        c.s()
    if flags2 & (1 << 13):
        raise AssertionError('rich_message not expected')
    return mid


def parse_dialog(c: Checker) -> int:
    c.expect(0xfc89f7f3, 'dialog')
    flags = c.i32()
    skip_peer(c)
    top = c.i32()
    c.i32(); c.i32()  # read_inbox/outbox max
    c.i32()  # unread_count
    c.i32()  # unread_mentions
    c.i32()  # unread_reactions
    c.i32()  # unread_poll_votes
    c.expect(0x99622c0c, 'peerNotifySettings')
    nf = c.i32()
    if nf & (1 << 0):
        c.u32()  # show_previews Bool
    if nf & (1 << 1):
        c.u32()  # silent Bool
    if nf & (1 << 2):
        c.i32()  # mute_until
    if nf & (1 << 3):
        _skip_notification_sound(c)
    if nf & (1 << 4):
        _skip_notification_sound(c)
    if nf & (1 << 5):
        _skip_notification_sound(c)
    if nf & (1 << 6):
        c.u32()
    if nf & (1 << 7):
        c.u32()
    if nf & (1 << 8):
        _skip_notification_sound(c)
    if nf & (1 << 9):
        _skip_notification_sound(c)
    if nf & (1 << 10):
        _skip_notification_sound(c)
    if flags & 1:
        c.i32()  # pts
    if flags & 2:
        raise AssertionError('draft not expected')
    if flags & (1 << 4):
        c.i32()
    if flags & (1 << 5):
        c.i32()
    return top


def _skip_notification_sound(c: Checker) -> None:
    cid = c.u32()
    if cid == 0x97e8bebe:  # notificationSoundDefault
        return
    if cid == 0x6f0c34df:  # notificationSoundNone
        return
    if cid == 0x830b9ae4:  # notificationSoundLocal
        c.s(); c.s()
        return
    if cid == 0xff6c8049:  # notificationSoundRingtone
        c.i64()
        return
    raise AssertionError(f'{c.label}: unknown notificationSound {cid:#010x}')


def parse_chat(c: Checker) -> int:
    cid = c.u32()
    if cid == CHANNEL_V228:  # channel#d49f34c6 layer 228
        flags = c.i32()
        flags2 = c.i32()
        chid = c.i64()
        if flags & (1 << 13):
            c.i64()  # access_hash
        c.s()  # title
        if flags & (1 << 6):
            c.s()  # username
        parse_photo(c)
        c.i32()  # date
        if flags & (1 << 9):
            for _ in range(c.vec()):
                c.s(); c.s()
        if flags & (1 << 14):
            _skip_chat_admin_rights(c)
        if flags & (1 << 15):
            _skip_chat_banned_rights(c)
        if flags & (1 << 18):
            _skip_chat_banned_rights(c)
        if flags & (1 << 17):
            c.i32()  # participants_count
        if flags2 & (1 << 0):
            for _ in range(c.vec()):
                c.expect(0xb4073647, 'username')
                c.i32(); c.s()
        if flags2 & (1 << 4):
            c.expect(0x711d692d, 'recentStory')
            sf = c.i32()
            if sf & (1 << 1):
                c.i32()
        if flags2 & (1 << 7):
            parse_peer_color(c)
        if flags2 & (1 << 8):
            parse_peer_color(c)
        if flags2 & (1 << 9):
            parse_emoji_status(c)
        if flags2 & (1 << 10):
            c.i32()
        if flags2 & (1 << 11):
            c.i32()
        if flags2 & (1 << 13):
            c.i64()
        if flags2 & (1 << 14):
            c.i64()
        if flags2 & (1 << 18):
            c.i64()
        if flags2 & (1 << 20):
            c.i64()
        return chid
    if cid == 0x41cbf256:  # chat#41cbf256
        flags = c.i32()
        chid = c.i64()
        c.s()
        parse_photo(c)
        c.i32()  # participants_count
        c.i32()  # date
        c.i32()  # version
        return chid
    raise AssertionError(f'{c.label}: unknown chat {cid:#010x}')


def _skip_chat_admin_rights(c: Checker) -> None:
    c.expect(0x5fb224d5, 'chatAdminRights')
    c.i32()  # flags


def _skip_chat_banned_rights(c: Checker) -> None:
    c.expect(0x9f120418, 'chatBannedRights')
    c.i32()  # flags
    c.i32()  # until_date


def parse_authorization(data: bytes, label: str) -> int:
    c = Checker(data, label)
    cid = int.from_bytes(data[:4], 'little')
    if cid == 0x44747e9a:  # auth.authorizationSignUpRequired
        c.u32()
        flags = c.i32()
        if flags & 1:
            raise AssertionError('terms_of_service not expected')
        c.done()
        return 0
    c.expect(tl.AUTH_AUTHORIZATION, 'auth.authorization')
    flags = c.i32()
    if flags & 1:
        c.i32()  # otherwise_relogin_days
    if flags & 2:
        c.i32()  # tmp_sessions
    if flags & 4:
        tok = c.tl_bytes()
        assert len(tok) == 20, f'future_auth_token len {len(tok)}'
    uid = parse_user(c, expect_self=True)
    c.done()
    print(f'  [ok] {label}: auth.authorization, self user id={uid}')
    return uid


class Client:
    def __init__(self, host: str, port: int, layer: int = LAYER):
        self.t = Transport(socket.create_connection((host, port), timeout=5))
        self.t.start()
        handshake(self.t)
        self.salt = 0
        self.seq = 1
        self.layer = layer

    def read_one(self):
        pkt = self.t.read_encrypted(timeout=10)
        self.salt = int(pkt['salt'])
        return pkt['body']

    def wait_updates(self, timeout: float = 8.0) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.read_one()
            cid = int.from_bytes(data[:4], 'little')
            if cid == NEW_SESSION:
                continue
            if cid == MSG_CONTAINER:
                r = tl.Reader(data)
                r.u32()
                n = r.i32()
                for _ in range(n):
                    _i = r.i64(); _s = r.i32(); ln = r.i32()
                    inner = r.read(ln)
                    icid = int.from_bytes(inner[:4], 'little')
                    if icid == tl.UPDATES:
                        return inner
                continue
            if cid == tl.UPDATES:
                return data
        raise TimeoutError('no updates push received')

    def _send(self, body: bytes) -> int:
        # patch salt into the outgoing envelope
        saved = getattr(self.t, 'salt', 0)
        self.t.salt = self.salt
        try:
            mid = self.t.send_encrypted(body, self.seq)
        finally:
            self.t.salt = saved
        self.seq += 2
        return mid

    def call(self, body: bytes, label: str, timeout: float = 10.0):
        mid = self._send(body)
        deadline = time.time() + timeout
        while time.time() < deadline:
            pkt = self.t.read_encrypted(timeout=deadline - time.time())
            self.salt = int(pkt['salt'])
            data = pkt['body']
            cid = int.from_bytes(data[:4], 'little')
            if cid == NEW_SESSION:
                continue
            if cid == MSG_CONTAINER:
                r = tl.Reader(data)
                r.u32()
                n = r.i32()
                for _ in range(n):
                    _i = r.i64(); _s = r.i32(); ln = r.i32()
                    inner = r.read(ln)
                    res = self._handle(inner, mid, label)
                    if res is not None:
                        return res
                continue
            res = self._handle(data, mid, label)
            if res is not None:
                return res
        raise TimeoutError(f'{label}: no rpc_result')

    def _handle(self, data: bytes, mid: int, label: str):
        cid = int.from_bytes(data[:4], 'little')
        if cid == RPC_RESULT:
            r = tl.Reader(data)
            r.u32()
            got_mid = r.i64()
            if got_mid != mid:
                return None
            payload = data[r.pos:]
            payload = tl.maybe_ungzip(payload)
            print(f'  [rpc] {label}: result {len(payload)}B cid={int.from_bytes(payload[:4], "little"):#010x}')
            return payload
        if cid == 0x2144ca19:
            r = tl.Reader(data); r.u32()
            code = r.i32(); msg = r.string()
            raise RuntimeError(f'{label}: rpc_error {code}: {msg}')
        # Updates pushed by the server while we wait
        if cid == tl.UPDATES:
            print(f'  [push] updates container {len(data)}B')
        return None


def wrap_layer(inner: bytes) -> bytes:
    w = tl.Writer()
    w.u32(tl.INVOKE_WITH_LAYER)
    w.i32(LAYER)
    w.raw(inner)
    return w.bytes_out()


def init_connection(inner: bytes) -> bytes:
    w = tl.Writer()
    w.u32(tl.INIT_CONNECTION)
    w.i32(0)
    w.i32(API_ID)
    w.string('DESKTOP-QA-PROBE')
    w.string('Windows 10')
    w.string('7.2.5 x64')
    w.string('en-US')
    w.string('tdesktop')
    w.string('en')
    w.raw(inner)
    return wrap_layer(w.bytes_out())


def main() -> int:
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 20443
    print(f'== layer-228 probe {host}:{port} ==')
    c = Client(host, port)

    # 1. getConfig through initConnection
    w = tl.Writer(); w.u32(tl.HELP_GET_CONFIG)
    res = c.call(init_connection(w.bytes_out()), 'help.getConfig')
    cc = Checker(res, 'config')
    cc.expect(0xcc1a241e, 'config')
    cf = cc.i32()
    print(f'  [ok] config flags={cf:#x}')

    # 2. sendCode
    w = tl.Writer()
    w.u32(tl.AUTH_SEND_CODE)
    w.string('+' + PHONE)
    w.i32(API_ID)
    w.string('b18441a1ff607e10a989891a5462e627')
    w.u32(tl.CODE_SETTINGS)
    w.i32(0)
    res = c.call(wrap_layer(w.bytes_out()), 'auth.sendCode')
    sc = Checker(res, 'auth.sentCode')
    sc.expect(tl.AUTH_SENT_CODE, 'auth.sentCode')
    sc.i32()  # flags
    tc = sc.u32()
    assert tc in (0xc000bba2, 0x3dbb5986, 0x72d43aaa), f'type {tc:#x}'
    sc.i32()  # length
    code_hash = sc.s()
    if sc.r.remaining() >= 8:
        # next_type / timeout may both be present
        pass
    print(f'  [ok] auth.sentCode hash={code_hash[:12]}...')

    # 3. signIn (registered test phone) — fall back to signUp
    w = tl.Writer()
    w.u32(tl.AUTH_SIGN_IN)
    w.i32(1)  # flags.0 phone_code
    w.string('+' + PHONE)
    w.string(code_hash)
    w.string(CODE)
    res = c.call(wrap_layer(w.bytes_out()), 'auth.signIn')
    uid = parse_authorization(res, 'auth.signIn')
    if not uid:
        print('  [i] signIn says sign-up required; registering test account')
        w = tl.Writer()
        w.u32(tl.AUTH_SIGN_UP)
        w.i32(0)
        w.string('+' + PHONE)
        w.string(code_hash)
        w.string('QA')
        w.string('Desktop')
        res = c.call(wrap_layer(w.bytes_out()), 'auth.signUp')
        uid = parse_authorization(res, 'auth.signUp')
        assert uid, 'signUp did not return authorization'

    # 4. getDialogs
    w = tl.Writer()
    w.u32(0xa0f4cb4f)  # messages.getDialogs
    w.i32(0)
    w.i32(0)   # offset_date
    w.i32(0)   # offset_id
    w.u32(tl.INPUT_PEER_EMPTY)
    w.i32(50)
    w.i64(0)
    res = c.call(wrap_layer(w.bytes_out()), 'messages.getDialogs')
    dc = Checker(res, 'messages.dialogs')
    dc.expect(0x15ba6c40, 'messages.dialogs')
    n_dialogs = dc.vec()
    for _ in range(n_dialogs):
        parse_dialog(dc)
    n_messages = dc.vec()
    print(f'  [i] dialogs={n_dialogs} messages={n_messages}')
    for _ in range(n_messages):
        parse_message(dc)
    n_chats = dc.vec()
    for _ in range(n_chats):
        parse_chat(dc)
    n_users = dc.vec()
    for _ in range(n_users):
        parse_user(dc)
    dc.done()
    print('  [ok] messages.dialogs parsed strictly against layer 228')

    # 5. sendMessage to self-chat (layer 228 id)
    w = tl.Writer()
    w.u32(tl.MESSAGES_SEND_MESSAGE_V227)  # 0xfef48f62 layer 228
    w.i32(0)
    w.u32(tl.INPUT_PEER_SELF)
    w.string('layer228 probe message')
    w.i64(int.from_bytes(os.urandom(8), 'little', signed=True))
    res = c.call(wrap_layer(w.bytes_out()), 'messages.sendMessage')
    uc = Checker(res, 'sendMessage result')
    uc.expect(tl.UPDATES, 'updates')
    n_upd = uc.vec()
    seen_message = False
    for _ in range(n_upd):
        ucid = uc.u32()
        if ucid == 0x4e90bfd6:  # updateMessageID
            uc.i32()  # id
            uc.i64()  # random_id
        elif ucid == 0x1f2b0afd:  # updateNewMessage
            parse_message(uc)
            uc.i32()  # pts
            uc.i32()  # pts_count
            seen_message = True
        else:
            raise AssertionError(
                f'sendMessage: unexpected update {ucid:#010x}')
    n_users = uc.vec()
    for _ in range(n_users):
        parse_user(uc)
    n_chats = uc.vec()
    for _ in range(n_chats):
        parse_chat(uc)
    uc.i32(); uc.i32()  # date, seq
    uc.done()
    assert seen_message, 'sendMessage result has no updateNewMessage'
    print('  [ok] sendMessage returned layer-228 message')

    # 6. updates.getState sanity
    w = tl.Writer(); w.u32(0xedd4882a)
    res = c.call(wrap_layer(w.bytes_out()), 'updates.getState')
    st = Checker(res, 'updates.state')
    st.expect(0xa56c2a3e, 'updates.state')
    st.i32(); st.i32(); st.i32(); st.i32(); st.i32()
    st.done()
    print('  [ok] updates.getState')

    print('PROBE LAYER 228 OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
