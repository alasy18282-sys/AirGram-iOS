"""
TL (Type Language) binary serializer / deserializer.
Only the constructors needed for auth-key generation and basic RPC.
"""
import struct
import os
from typing import Any, List

# ──────────────────────────────────────────────
# Constructor IDs
# ──────────────────────────────────────────────

# Handshake
REQ_PQ                = 0x60469778
REQ_PQ_MULTI          = 0xbe7e8ef1
RES_PQ                = 0x05162463
P_Q_INNER_DATA        = 0x83c95aec
P_Q_INNER_DATA_DC     = 0xa9f55f95
P_Q_INNER_DATA_TEMP   = 0x3c6a84d4
P_Q_INNER_DATA_TEMP_DC= 0x56fddf88
REQ_DH_PARAMS         = 0xd712e4be
SERVER_DH_PARAMS_OK   = 0xd0e8075c
SERVER_DH_INNER_DATA  = 0xb5890dba
CLIENT_DH_INNER_DATA  = 0x6643b654
SET_CLIENT_DH_PARAMS  = 0xf5045f1f
DH_GEN_OK             = 0x3bcbf734
DH_GEN_RETRY          = 0x46dc1fb9
DH_GEN_FAIL           = 0xa69dae02
SERVER_DH_PARAMS_FAIL = 0x79cb045d

# Core message types
MSGS_ACK              = 0x62d6b459
MSGS_STATE_REQ        = 0xda69fb52
MSGS_STATE_INFO       = 0x04deb57d
RPC_RESULT            = 0xf35c6d01
MSG_CONTAINER         = 0x73f1f8dc
PING                  = 0x7abe77ec
PING_DELAY_DISCONNECT = 0xf3427b8c
PONG                  = 0x347773c5
GET_FUTURE_SALTS      = 0xb921bd04
FUTURE_SALTS          = 0xae500895
FUTURE_SALT           = 0x0949d9dc
DESTROY_SESSION       = 0xe7512126
DESTROY_SESSION_OK    = 0xe22045fc
HTTP_WAIT             = 0x9299359f
GZIP_PACKED           = 0x3072cfa1
RPC_ERROR             = 0x2144ca19
NEW_SESSION_CREATED   = 0x9ec20908
BAD_MSG_NOTIFICATION  = 0xa7eff811
BAD_SERVER_SALT       = 0xedab447b
INVOKE_WITH_LAYER     = 0xda9b0d0d
INIT_CONNECTION       = 0xc1cd5ea9
INVOKE_AFTER_MSG      = 0xcb9f372d
INVOKE_AFTER_MSGS     = 0x3dc4b4f0
INVOKE_WITHOUT_UPDATES = 0xbf9459b7
INVOKE_WITH_MESSAGES_RANGE = 0x365275f2
INVOKE_WITH_TAKEOUT   = 0xaca9fd2e
INVOKE_WITH_APNS_SECRET = 0x0dae54f8
INVOKE_WITH_BUSINESS_CONNECTION = 0xdd289f8e
INVOKE_WITH_GOOGLE_PLAY_INTEGRITY = 0x1df92984
INVOKE_WITH_RECAPTCHA = 0xadbb0f94

# Booleans
BOOL_TRUE             = 0x997275b5
BOOL_FALSE            = 0xbc799737

# Vector
VECTOR                = 0x1cb5c415

# Auth
AUTH_SEND_CODE        = 0xa677244f
AUTH_INIT_PASSKEY_LOGIN = 0x518ad0b7
AUTH_RESEND_CODE      = 0xcae47523
AUTH_CANCEL_CODE      = 0x1f040578
AUTH_SENT_CODE        = 0x5e002502
AUTH_PASSKEY_LOGIN_OPTIONS = 0xe2037789
AUTH_SENT_CODE_TYPE_APP   = 0x3dbb5986
AUTH_SENT_CODE_TYPE_SMS   = 0xc000bba2
AUTH_SENT_CODE_TYPE_FIREBASE_SMS = 0x9fd736
AUTH_SENT_CODE_TYPE_FRAGMENT_SMS = 0xd95650c3
AUTH_CODE_TYPE_SMS        = 0x72a3158c
AUTH_REQUEST_FIREBASE_SMS = 0x8e39261e
AUTH_SIGN_IN          = 0x8d52a951
AUTH_SIGN_UP          = 0xaac7b717
AUTH_SIGN_UP_LAYER    = 0x80eee427
AUTH_AUTHORIZATION    = 0x2ea2c0d4
AUTH_AUTHORIZATION_SIGN_UP_REQUIRED = 0x44747e9a
AUTH_LOG_OUT          = 0x3e72ba19
AUTH_LOGGED_OUT       = 0xc3a2835f
HELP_PEER_COLORS_NOT_MODIFIED = 0x2ba1f5ce
HELP_GET_PEER_COLORS  = 0xda80f42f
HELP_GET_PEER_PROFILE_COLORS = 0xabcfa9fd
AUTH_EXPORT_AUTH      = 0xe5bfffcd
AUTH_IMPORT_AUTH      = 0xa57a7dad
AUTH_EXPORTED_AUTH    = 0xb434e2b8
AUTH_BIND_TEMP_AUTH_KEY = 0xcdd42a05
AUTH_EXPORT_LOGIN_TOKEN = 0xb7e085fe
AUTH_IMPORT_LOGIN_TOKEN = 0x95ac5ce4
AUTH_ACCEPT_LOGIN_TOKEN = 0xe894ad4d
AUTH_LOGIN_TOKEN        = 0x629f1980
AUTH_LOGIN_TOKEN_SUCCESS = 0x390d5c5e

CODE_SETTINGS         = 0xad253d78

# Users
USERS_GET_USERS       = 0x0d91a548
INPUT_USER_SELF       = 0xf7c1b13f
INPUT_USER_EMPTY      = 0xb98886cf
INPUT_USER            = 0xf21158c6
USER                  = 0x31774388
USERNAME              = 0xb4073647
INPUT_COLLECTIBLE_USERNAME = 0xe39460a9
INPUT_COLLECTIBLE_PHONE = 0xa2e214a4
FRAGMENT_COLLECTIBLE_INFO = 0x6ebdff91
FRAGMENT_GET_COLLECTIBLE_INFO = 0xbe1e85ba

# Help
HELP_GET_NEAREST_DC   = 0x1fb33026
NEAREST_DC            = 0x8e1a1775
HELP_GET_CONFIG       = 0xc4f9186b
CONFIG                = 0xcc1a241e
DC_OPTION             = 0x18b7a10d
HELP_GET_APP_CONFIG   = 0x61e3f854
HELP_APP_CONFIG       = 0xdd18782e
HELP_APP_CONFIG_NOT_MODIFIED = 0x7cde641d
HELP_GET_APP_UPDATE   = 0x522d5a7d
HELP_NO_APP_UPDATE    = 0xc45a6536
HELP_GET_COUNTRIES_LIST = 0x735787a8
HELP_COUNTRIES_LIST   = 0x87d0759e
HELP_GET_TIMEZONES_LIST = 0x49b30240
HELP_TIMEZONES_LIST = 0x7b74ed71
HELP_TIMEZONES_LIST_NOT_MODIFIED = 0x970708cc
TIMEZONE             = 0xff9289f5
UPDATES_GET_STATE     = 0xedd4882a
UPDATES_STATE         = 0xa56c2a3e
MESSAGES_GET_DIALOG_FILTERS = 0xefd48c89
MESSAGES_DIALOG_FILTERS = 0x2ad93719
HELP_GET_TERMS        = 0x350170f3
HELP_TERMS_OF_SERVICE = 0x780a0310

ACCOUNT_REGISTER_DEVICE = 0xec86017a
ACCOUNT_GET_GLOBAL_PRIVACY_SETTINGS = 0xeb2b4cf6
ACCOUNT_GET_DEFAULT_PROFILE_PHOTO_EMOJIS = 0xe2750328
ACCOUNT_GET_SAVED_RINGTONES = 0xe1902288
ACCOUNT_GET_PRIVACY     = 0xbea7f17b
ACCOUNT_PRIVACY_RULES   = 0x50a04e45
ACCOUNT_SAVED_RINGTONES_NOT_MODIFIED = 0xfbf6e8b1

UPDATES_GET_DIFFERENCE  = 0x19c2f763
UPDATES_GET_DIFFERENCE_LEGACY = 0x25939651
UPDATES_DIFFERENCE      = 0x0f49ca0
UPDATES_DIFFERENCE_EMPTY= 0x5d75a138
UPDATE_SHORT            = 0x78d4dec1
UPDATE_SHORT_MESSAGE    = 0x313bc7f8
UPDATE_NEW_MESSAGE      = 0x1f2b0afd
UPDATE_EDIT_MESSAGE     = 0xe40370a3
UPDATE_DELETE_MESSAGES  = 0xa20db0e5
UPDATE_STARS_BALANCE    = 0x4e80a379
UPDATE_CONFIG           = 0xa229dd06

ACCOUNT_SEND_CHANGE_PHONE_CODE = 0x82574ae5
ACCOUNT_CHANGE_PHONE    = 0x70c32edb
ACCOUNT_UPDATE_PROFILE  = 0x78515775
ACCOUNT_CHECK_USERNAME  = 0x2714d86c
ACCOUNT_UPDATE_USERNAME = 0x3e0bdd7c
ACCOUNT_UPDATE_BUSINESS_INTRO = 0xa614d034
ACCOUNT_UPDATE_BUSINESS_GREETING_MESSAGE = 0x66cdafc4
ACCOUNT_UPDATE_BUSINESS_WORK_HOURS = 0x4b00e066
ACCOUNT_UPDATE_BUSINESS_AWAY_MESSAGE = 0xa26a7fa5
ACCOUNT_UPDATE_BUSINESS_LOCATION = 0x9e6b131a
ACCOUNT_REORDER_USERNAMES = 0xef500eab
ACCOUNT_TOGGLE_USERNAME = 0x58d6b376
INPUT_BUSINESS_INTRO    = 0x09c469cd
BUSINESS_INTRO          = 0x5a0a066d
INPUT_BUSINESS_RECIPIENTS = 0x6f8b32aa
BUSINESS_RECIPIENTS     = 0x21108ff7
INPUT_BUSINESS_GREETING_MESSAGE = 0x0194cb3b
BUSINESS_GREETING_MESSAGE = 0xe519abab
INPUT_BUSINESS_AWAY_MESSAGE = 0x832175e0
BUSINESS_AWAY_MESSAGE = 0xef156a5c
BUSINESS_AWAY_MESSAGE_SCHEDULE_ALWAYS = 0xc9b9e2b9
BUSINESS_AWAY_MESSAGE_SCHEDULE_OUTSIDE_WORK_HOURS = 0xc3f2f501
BUSINESS_AWAY_MESSAGE_SCHEDULE_CUSTOM = 0xcc4d9ecc
BUSINESS_WORK_HOURS     = 0x8c92b098
BUSINESS_WEEKLY_OPEN    = 0x120b1ab9
BUSINESS_LOCATION       = 0xac5c1af7
GEO_POINT               = 0xb2a2f663
INPUT_GEO_POINT         = 0x48222faf
INPUT_GEO_POINT_EMPTY   = 0xe4c123d6
MESSAGES_CHECK_QUICK_REPLY_SHORTCUT = 0xf1d0fbd3
MESSAGES_DELETE_QUICK_REPLY_SHORTCUT = 0x3cc04740
UPDATE_DELETE_QUICK_REPLY = 0x53e6f1ec
USERS_GET_FULL_USER     = 0xb60f5918
USERS_USER_FULL         = 0x3b6d152e
USER_FULL               = 0x6cbe645  # official layer 227 (Airgram desktop)
CONTACTS_SEARCH         = 0x11f812d8
# Layer 228 renames / re-signatures (Telegram Desktop 7.2.5).
# Only the ids change: field order stays backward compatible, new options
# are trailing flags we skip.
CONTACTS_SEARCH_V228    = 0x05f58d0f
MESSAGES_EDIT_MESSAGE_V228 = 0xb106e66c
MESSAGES_EDIT_INLINE_BOT_MESSAGE_V228 = 0xa423bb51
MESSAGES_SAVE_DRAFT_V228 = 0xad0fa15c
MESSAGES_SEARCH_GLOBAL_V228 = 0x6126a43c
MESSAGES_IMPORT_CHAT_INVITE_V228 = 0xde91436e
CHANNELS_JOIN_CHANNEL_V228 = 0x7f6a1e22
CHANNELS_TOGGLE_JOIN_REQUEST_V228 = 0x0ecc2618
MESSAGES_CHAT_INVITE_JOIN_RESULT_OK = 0x445663a7
CONTACTS_GET_BIRTHDAYS  = 0xdaeda864
CONTACTS_CONTACT_BIRTHDAYS = 0x114ff30d
CONTACTS_GET_SPONSORED_PEERS = 0xb6c8c393
CONTACTS_SPONSORED_PEERS_EMPTY = 0xea32b4b1
CONTACTS_GET_CONTACTS   = 0x5dd69e12
CONTACTS_IMPORT_CONTACTS = 0x2c800be5
CONTACTS_IMPORTED_CONTACTS = 0x77d01c3b
CONTACTS_NOT_MODIFIED   = 0xb74ba9d2
CONTACTS_FOUND          = 0xb3134d9d
CONTACTS_RESOLVE_USERNAME = 0x725afbbc
CONTACTS_RESOLVED_PEER  = 0x7f077ad9
MESSAGES_GET_DIALOGS    = 0xa0f4cb4f
MESSAGES_GET_SAVED_DIALOGS = 0x1e91fc99
LANGPACK_GET_STRINGS = 0x2e1ee318
LANGPACK_GET_STRINGS_V2 = 0xefea3803
MESSAGES_GET_PINNED_SAVED_DIALOGS = 0xd63d94e0
MESSAGES_GET_WEB_PAGE = 0x8d9692a3
MESSAGES_WEB_PAGE = 0xfd5e12bd
WEB_PAGE = 0xe89c45b2
WEB_PAGE_NOT_MODIFIED = 0x7311ca11
ACCOUNT_GET_CONNECTED_BOTS = 0x4ea4c80f
ACCOUNT_GET_BUSINESS_CHAT_LINKS = 0x6f70dde1
ACCOUNT_CONNECTED_BOTS = 0x17d7f87b
ACCOUNT_BUSINESS_CHAT_LINKS = 0xec43a2d1
HELP_SAVE_APP_LOG = 0x6f02f748
MESSAGES_READ_REACTIONS = 0x9ec44f93
MESSAGES_GET_EMOJI_KEYWORDS_DIFFERENCE = 0x1508b6af
ACCOUNT_GET_CHAT_THEMES = 0xd638de89
ACCOUNT_GET_DEFAULT_BACKGROUND_EMOJIS = 0xa60ab9ce
ACCOUNT_UPDATE_COLOR = 0x684d214e
ACCOUNT_THEMES_NOT_MODIFIED = 0xf41eb622
EMOJI_LIST = 0x7a1e11d1
EMOJI_LIST_NOT_MODIFIED = 0x481eadfa
PEER_COLOR = 0xb54b5acf
MESSAGES_GET_PINNED_DIALOGS = 0xd6b94df2
MESSAGES_GET_RECENT_REACTIONS = 0x39461db2
MESSAGES_GET_TOP_REACTIONS = 0xbb8125ba
MESSAGES_GET_ARCHIVED_STICKERS = 0x57f17692
MESSAGES_GET_SAVED_GIFS = 0x5cf09635
MESSAGES_DELETE_HISTORY = 0xb08f922a
MESSAGES_GET_FAVED_STICKERS = 0x04f1aaa9
MESSAGES_GET_MESSAGES   = 0x63c66506
MESSAGES_GET_HISTORY    = 0x4423e6c5
MESSAGES_GET_SCHEDULED_HISTORY = 0xf516760b
MESSAGES_GET_QUICK_REPLY_MESSAGES = 0x94a495c3
MESSAGES_DIALOGS        = 0x15ba6c40
MESSAGES_DIALOGS_SLICE  = 0x71e094f3
MESSAGES_REACTIONS_NOT_MODIFIED = 0xb06fdbdf
MESSAGES_ARCHIVED_STICKERS = 0x4fcba9c8
MESSAGES_SAVED_GIFS_NOT_MODIFIED = 0xe8025ca2
MESSAGES_FAVED_STICKERS_NOT_MODIFIED = 0x9e8fa6d3
MESSAGES_MESSAGES       = 0x1d73e7ea
MESSAGES_MESSAGES_NOT_MODIFIED = 0x74535f21
MESSAGES_SLICE          = 0x5f206716
CONTACTS_GET_TOP_PEERS  = 0x973478b6
CONTACTS_TOP_PEERS_NOT_MODIFIED = 0xde266ef5
CONTACTS_RESOLVE_PHONE  = 0x8af94344
MESSAGES_GET_QUICK_REPLIES = 0xd483f2a8
MESSAGES_QUICK_REPLIES_NOT_MODIFIED = 0x5f91eb5b
PAYMENTS_GET_STAR_GIFT_ACTIVE_AUCTIONS = 0xa5d0514d
PAYMENTS_STAR_GIFT_ACTIVE_AUCTIONS_NOT_MODIFIED = 0xdb33dad0
PAYMENTS_GET_STAR_GIFT_AUCTION_STATE = 0x5c9ff4d6
MESSAGE                 = 0x3ae56482  # layer 224 (Android/iOS)
MESSAGE_V225            = 0x95ef6f2b
MESSAGE_V227            = 0x7600b9d3
USER_V228               = 0xb1b8cc83
CHANNEL                 = 0x1c32b11c
CHANNEL_V228            = 0xd49f34c6
CHANNEL_FULL            = 0xe4e0b29d
CHANNEL_FULL_V226       = 0xa04e8d3a
UPDATES_DIFFERENCE_TOO_LONG = 0x4afe8f6d
DIALOG                  = 0xfc89f7f3  # layer 224+ (was 0xd58a08c6)
PEER_USER               = 0x59511722
INPUT_PEER_EMPTY        = 0x7f3b18ea
INPUT_PEER_SELF         = 0x7da07ec9
INPUT_PEER_CHAT         = 0x35a95cb9
INPUT_PEER_USER         = 0xdde8a54c
INPUT_PEER_CHANNEL      = 0x27bcbbfc
INPUT_PEER_USER_FROM_MESSAGE = 0xa87b0a1c
INPUT_PEER_CHANNEL_FROM_MESSAGE = 0xbd2a0840
INPUT_REPLY_TO_MESSAGE  = 0x3bd4b7c2
INPUT_REPLY_TO_STORY    = 0x5881323a
INPUT_REPLY_TO_MONOFORUM = 0x69d66c45
PEER_NOTIFY_SETTINGS    = 0x99622c0c
PEER_SETTINGS           = 0xf47741f7
MESSAGE_ENTITY_BOLD     = 0xbd610bc9
MESSAGE_ENTITY_SPOILER  = 0x32ca960f
MESSAGE_ENTITY_CODE     = 0x28a7394c
MESSAGE_ENTITY_PRE      = 0x73924be0
MESSAGE_ENTITY_TEXT_URL = 0x76a6d327
MESSAGE_ENTITY_MENTION  = 0xfa04579d
MESSAGE_ENTITY_URL      = 0x6ed02538
MESSAGE_ENTITY_BOT_COMMAND = 0x6cef8ac7
MESSAGE_REPLY_HEADER    = 0x1b97dd66
MESSAGES_START_BOT      = 0xe6df7378
MESSAGES_SEND_MESSAGE    = 0x545cd15a
MESSAGES_SEND_MESSAGE_214 = 0xfe05dc9a
MESSAGES_SEND_MESSAGE_V227 = 0xfef48f62
MESSAGES_SEND_MEDIA      = 0x0330e77f
MESSAGES_SEND_MULTI_MEDIA= 0x1bf89d74
MESSAGE_ENTITY_CUSTOM_EMOJI = 0xc8cf05f8
MESSAGES_GET_PEER_DIALOGS = 0xe470bcfd
UPDATES                 = 0x74ae4240
UPDATE_MESSAGE_ID       = 0x4e90bfd6
UPDATE_SHORT_SENT_MESSAGE = 0x9015e101
MESSAGES_AFFECTED_HISTORY = 0xb45c69d1
MESSAGES_AFFECTED_MESSAGES = 0x84d19185
ACCOUNT_UPDATE_STATUS   = 0x6628562c
MESSAGES_READ_HISTORY   = 0x0e306d3a
MESSAGES_GET_PEER_SETTINGS = 0xefd9a6a2
MESSAGES_HIDE_PEER_SETTINGS_BAR = 0x4facb138
MESSAGES_GET_ONLINES = 0x6e2be050
MESSAGES_GET_OUTBOX_READ_DATE = 0x8c4bfe5d
MESSAGES_GET_SEARCH_RESULTS_POSITIONS = 0x9c7f2f10
MESSAGES_SEARCH_RESULTS_POSITIONS = 0x53b22baf
CHAT_ONLINES = 0xf041e250
OUTBOX_READ_DATE = 0x3bb842ac
PREMIUM_GET_BOOSTS_STATUS = 0x042f1f61
PREMIUM_BOOSTS_STATUS = 0x4959427a
MESSAGES_PEER_SETTINGS  = 0x6880b94d
BOTS_CAN_SEND_MESSAGE   = 0x1359f4e6
UPDATE_USER_STATUS      = 0xe5bdf8de
UPDATE_USER_NAME        = 0xa7848924
UPDATE_USER             = 0x20529438
UPDATE_PEER_BLOCKED     = 0xebe07752
UPDATE_READ_FEATURED_EMOJI_STICKERS = 0xfb4c496c
UPDATE_STICKER_SETS = 0x31c24808
BOT_VERIFICATION        = 0xf93cd45c
USER_STATUS_ONLINE      = 0xedb93949
USER_STATUS_OFFLINE     = 0x8c703f
USER_STATUS_RECENTLY    = 0x7b197dc8
USER_STATUS_EMPTY       = 0x9d05049

LANG_PACK_GET_LANGUAGES = 0x800fd57d
LANG_PACK_GET_STRINGS   = 0xefea3803
LANG_PACK_STRING        = 0xcad181f6
LANG_PACK_LANGUAGE      = 0xeeca5ce3
LANGPACK_GET_LANGUAGES  = 0x42c6978f
LANGPACK_GET_LANGUAGE   = 0x6a596502
LANGPACK_GET_DIFFERENCE = 0xcd984aa5
LANGPACK_GET_LANG_PACK  = 0xf2f2330a
LANGPACK_GET_LANG_PACK_LEGACY = 0x9ab5c58e
LANGPACK_GET_DIFFERENCE_LEGACY = 0xb2e4d7d
HELP_GET_INVITE_TEXT    = 0x4d392343
HELP_INVITE_TEXT        = 0x18cb9f78

# ──────────────────────────────────────────────
# Low-level reader
# ──────────────────────────────────────────────

class Reader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.pos = offset

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def read(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError(f'TL read overflow: need {n}, have {self.remaining()}')
        chunk = self.data[self.pos:self.pos + n]
        self.pos += n
        return chunk

    def i32(self) -> int:
        return struct.unpack_from('<i', self.read(4))[0]

    def u32(self) -> int:
        return struct.unpack_from('<I', self.read(4))[0]

    def i64(self) -> int:
        return struct.unpack_from('<q', self.read(8))[0]

    def f64(self) -> float:
        return struct.unpack_from('<d', self.read(8))[0]

    def u64(self) -> int:
        return struct.unpack_from('<Q', self.read(8))[0]

    def i128(self) -> bytes:
        return self.read(16)

    def i256(self) -> bytes:
        return self.read(32)

    def tl_bytes(self) -> bytes:
        first = self.data[self.pos]
        if first == 254:
            self.pos += 1
            n = struct.unpack_from('<I', self.read(3) + b'\x00')[0]
        else:
            n = first
            self.pos += 1
        result = self.read(n)
        # skip padding to 4-byte boundary
        total = (1 if first != 254 else 4) + n
        pad = (4 - total % 4) % 4
        self.pos += pad
        return result

    def string(self) -> str:
        return self.tl_bytes().decode('utf-8', errors='replace')

    def constructor(self) -> int:
        return self.u32()

    def bool(self) -> bool:
        c = self.u32()
        if c == BOOL_TRUE:  return True
        if c == BOOL_FALSE: return False
        raise ValueError(f'Not a bool constructor: {c:#010x}')

    def vector(self, reader_fn):
        assert self.u32() == VECTOR
        count = self.i32()
        return [reader_fn() for _ in range(count)]

    def long_list(self) -> List[int]:
        return self.vector(self.i64)

# ──────────────────────────────────────────────
# Low-level writer
# ──────────────────────────────────────────────

class Writer:
    def __init__(self):
        self.buf = bytearray()

    def bytes_out(self) -> bytes:
        return bytes(self.buf)

    def raw(self, data: bytes) -> 'Writer':
        self.buf.extend(data)
        return self

    def u32(self, v: int) -> 'Writer':
        self.buf.extend(struct.pack('<I', v & 0xFFFFFFFF))
        return self

    def i32(self, v: int) -> 'Writer':
        self.buf.extend(struct.pack('<i', v))
        return self

    def i64(self, v: int) -> 'Writer':
        self.buf.extend(struct.pack('<q', v))
        return self

    def f64(self, v: float) -> 'Writer':
        self.buf.extend(struct.pack('<d', v))
        return self

    def u64(self, v: int) -> 'Writer':
        self.buf.extend(struct.pack('<Q', v))
        return self

    def i128(self, v: bytes) -> 'Writer':
        assert len(v) == 16
        return self.raw(v)

    def i256(self, v: bytes) -> 'Writer':
        assert len(v) == 32
        return self.raw(v)

    def tl_bytes(self, data: bytes) -> 'Writer':
        n = len(data)
        if n < 254:
            self.buf.append(n)
            self.buf.extend(data)
            total = 1 + n
        else:
            self.buf.append(254)
            self.buf.extend(n.to_bytes(3, 'little'))
            self.buf.extend(data)
            total = 4 + n
        pad = (4 - total % 4) % 4
        self.buf.extend(b'\x00' * pad)
        return self

    def string(self, s: str) -> 'Writer':
        return self.tl_bytes(s.encode('utf-8'))

    def bool(self, v: bool) -> 'Writer':
        return self.u32(BOOL_TRUE if v else BOOL_FALSE)

    def vector_long(self, items: List[int]) -> 'Writer':
        self.u32(VECTOR)
        self.i32(len(items))
        for x in items:
            self.i64(x)
        return self

    def vector_bytes(self, items) -> 'Writer':
        self.u32(VECTOR)
        self.i32(len(items))
        for x in items:
            self.tl_bytes(x)
        return self

# ──────────────────────────────────────────────
# Utility: big-int ↔ TL bytes
# ──────────────────────────────────────────────

def int_to_bytes(n: int) -> bytes:
    length = (n.bit_length() + 7) // 8
    return n.to_bytes(length, 'big')

def bytes_to_int(b: bytes) -> int:
    return int.from_bytes(b, 'big')

# ──────────────────────────────────────────────
# Unencrypted MTProto message (auth_key_id == 0)
# ──────────────────────────────────────────────

def pack_unencrypted(message_id: int, data: bytes) -> bytes:
    w = Writer()
    w.i64(0)           # auth_key_id = 0
    w.i64(message_id)
    w.i32(len(data))
    w.raw(data)
    return w.bytes_out()

def unpack_unencrypted(raw: bytes):
    r = Reader(raw)
    auth_key_id = r.i64()
    message_id  = r.i64()
    length      = r.i32()
    data        = r.read(length)
    return auth_key_id, message_id, data

# ──────────────────────────────────────────────
# gzip unpacking
# ──────────────────────────────────────────────

def maybe_ungzip(data: bytes) -> bytes:
    r = Reader(data)
    if r.remaining() < 4:
        return data
    cid = r.u32()
    if cid == GZIP_PACKED:
        import gzip
        compressed = r.tl_bytes()
        return gzip.decompress(compressed)
    return data


def maybe_gzip(body: bytes, threshold: int = 512) -> bytes:
    """Wrap large RPC bodies in gzip_packed, like official Telegram servers."""
    if len(body) < threshold:
        return body
    import gzip
    w = Writer()
    w.u32(GZIP_PACKED)
    w.tl_bytes(gzip.compress(body, compresslevel=6))
    return w.bytes_out()

# ──────────────────────────────────────────────
# Invoke wrapper skipping (initConnection / layer)
# ──────────────────────────────────────────────

INPUT_CLIENT_PROXY = 0x75588b3f
JSON_NULL          = 0x3f6d7b68
JSON_BOOL          = 0xc7345e6a
JSON_NUMBER        = 0x2be0dfa4
JSON_STRING        = 0xb71e767a
JSON_ARRAY         = 0xf7444763
JSON_OBJECT        = 0x99c1d49d
JSON_OBJECT_VALUE  = 0xc0de1bd9
DATA_JSON          = 0x7d748d04


def skip_json_value(r: Reader) -> None:
    cid = r.u32()
    if cid == JSON_NULL:
        return
    if cid == JSON_BOOL:
        r.u32()
        return
    if cid == JSON_NUMBER:
        r.read(8)
        return
    if cid == JSON_STRING:
        r.string()
        return
    if cid == JSON_ARRAY:
        assert r.u32() == VECTOR
        for _ in range(r.i32()):
            skip_json_value(r)
        return
    if cid == JSON_OBJECT:
        assert r.u32() == VECTOR
        for _ in range(r.i32()):
            assert r.u32() == JSON_OBJECT_VALUE
            r.string()
            skip_json_value(r)
        return
    raise ValueError(f'unknown JSONValue {cid:#010x}')


def skip_init_connection(r: Reader) -> None:
    flags = r.i32()
    r.i32()       # api_id
    r.string()    # device_model
    r.string()    # system_version
    r.string()    # app_version
    r.string()    # system_lang_code
    r.string()    # lang_pack
    r.string()    # lang_code
    if flags & 1:
        assert r.u32() == INPUT_CLIENT_PROXY
        r.string()
        r.i32()
    if flags & 2:
        skip_json_value(r)


def unwrap_rpc_query(r: Reader, session=None) -> int:
    """Skip invoke* / initConnection wrappers; return inner RPC cid."""
    cid = r.u32()
    while cid in (
        INVOKE_WITH_LAYER,
        INIT_CONNECTION,
        INVOKE_AFTER_MSG,
        INVOKE_AFTER_MSGS,
        INVOKE_WITHOUT_UPDATES,
        INVOKE_WITH_MESSAGES_RANGE,
        INVOKE_WITH_TAKEOUT,
        INVOKE_WITH_APNS_SECRET,
        INVOKE_WITH_BUSINESS_CONNECTION,
        INVOKE_WITH_GOOGLE_PLAY_INTEGRITY,
        INVOKE_WITH_RECAPTCHA,
    ):
        if cid == INVOKE_WITH_LAYER:
            layer = r.i32()
            # Remember the client's TL layer: some constructors we emit
            # (user/message/channel) have different ids in newer layers.
            if session is not None and 100 <= layer <= 1000:
                session.client_layer = layer
                try:
                    from .client_info import remember_layer
                    remember_layer(session, layer)
                except Exception:
                    pass
        elif cid == INVOKE_AFTER_MSG:
            r.i64()
        elif cid == INVOKE_AFTER_MSGS:
            assert r.u32() == VECTOR
            for _ in range(r.i32()):
                r.i64()
        elif cid == INIT_CONNECTION:
            if session is not None:
                from .client_info import capture_init_connection
                flags = r.i32()
                capture_init_connection(
                    session,
                    flags=flags,
                    api_id=r.i32(),
                    device_model=r.string(),
                    system_version=r.string(),
                    app_version=r.string(),
                    system_lang_code=r.string(),
                    lang_pack=r.string(),
                    lang_code=r.string(),
                )
                if flags & 1:
                    assert r.u32() == INPUT_CLIENT_PROXY
                    r.string()
                    r.i32()
                if flags & 2:
                    skip_json_value(r)
            else:
                skip_init_connection(r)
        elif cid == INVOKE_WITH_MESSAGES_RANGE:
            assert r.u32() == 0x0ae30253  # messageRange
            r.i32()
            r.i32()
        elif cid == INVOKE_WITH_TAKEOUT:
            r.i64()
        elif cid in (
            INVOKE_WITH_APNS_SECRET,
            INVOKE_WITH_GOOGLE_PLAY_INTEGRITY,
        ):
            r.string()
            r.string()
        elif cid in (
            INVOKE_WITH_BUSINESS_CONNECTION,
            INVOKE_WITH_RECAPTCHA,
        ):
            r.string()
        cid = r.u32()
    return cid
