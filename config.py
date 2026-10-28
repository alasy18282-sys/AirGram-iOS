import os
from pathlib import Path

SERVER_HOST = os.environ.get('MYTELEGRAM_SERVER_HOST', '0.0.0.0').strip() or '0.0.0.0'
SERVER_PORT = int(os.environ.get('MYTELEGRAM_SERVER_PORT', '443'))
SERVER_PUBLIC_IP = os.environ.get('MYTELEGRAM_PUBLIC_IP', '84.21.173.237').strip() or '84.21.173.237'
DC_ID = 1
# Canonical TL layer. Clients that never announce invokeWithLayer get packed
# with this layer's constructor ids; announce older layers still get their own.
LAYER = 229

# Global gift visibility switch.  Gifts are visible by default: the shop and
# profiles always reflect rows currently present in SQLite, so an import shows
# up immediately without restarting the server.
# Set AIRGRAM_GIFTS_HIDDEN=1 to temporarily hide the whole gift subsystem.
GIFTS_HIDDEN = (
    os.environ.get('AIRGRAM_GIFTS_HIDDEN', '0').strip().lower()
    in ('1', 'true', 'yes', 'on')
)

# Standard Telegram 2048-bit DH safe prime (must match Telegram Desktop GoodPrime)
DH_PRIME_BYTES = bytes([
    0xC7, 0x1C, 0xAE, 0xB9, 0xC6, 0xB1, 0xC9, 0x04, 0x8E, 0x6C, 0x52, 0x2F,
    0x70, 0xF1, 0x3F, 0x73, 0x98, 0x0D, 0x40, 0x23, 0x8E, 0x3E, 0x21, 0xC1,
    0x49, 0x34, 0xD0, 0x37, 0x56, 0x3D, 0x93, 0x0F, 0x48, 0x19, 0x8A, 0x0A,
    0xA7, 0xC1, 0x40, 0x58, 0x22, 0x94, 0x93, 0xD2, 0x25, 0x30, 0xF4, 0xDB,
    0xFA, 0x33, 0x6F, 0x6E, 0x0A, 0xC9, 0x25, 0x13, 0x95, 0x43, 0xAE, 0xD4,
    0x4C, 0xCE, 0x7C, 0x37, 0x20, 0xFD, 0x51, 0xF6, 0x94, 0x58, 0x70, 0x5A,
    0xC6, 0x8C, 0xD4, 0xFE, 0x6B, 0x6B, 0x13, 0xAB, 0xDC, 0x97, 0x46, 0x51,
    0x29, 0x69, 0x32, 0x84, 0x54, 0xF1, 0x8F, 0xAF, 0x8C, 0x59, 0x5F, 0x64,
    0x24, 0x77, 0xFE, 0x96, 0xBB, 0x2A, 0x94, 0x1D, 0x5B, 0xCD, 0x1D, 0x4A,
    0xC8, 0xCC, 0x49, 0x88, 0x07, 0x08, 0xFA, 0x9B, 0x37, 0x8E, 0x3C, 0x4F,
    0x3A, 0x90, 0x60, 0xBE, 0xE6, 0x7C, 0xF9, 0xA4, 0xA4, 0xA6, 0x95, 0x81,
    0x10, 0x51, 0x90, 0x7E, 0x16, 0x27, 0x53, 0xB5, 0x6B, 0x0F, 0x6B, 0x41,
    0x0D, 0xBA, 0x74, 0xD8, 0xA8, 0x4B, 0x2A, 0x14, 0xB3, 0x14, 0x4E, 0x0E,
    0xF1, 0x28, 0x47, 0x54, 0xFD, 0x17, 0xED, 0x95, 0x0D, 0x59, 0x65, 0xB4,
    0xB9, 0xDD, 0x46, 0x58, 0x2D, 0xB1, 0x17, 0x8D, 0x16, 0x9C, 0x6B, 0xC4,
    0x65, 0xB0, 0xD6, 0xFF, 0x9C, 0xA3, 0x92, 0x8F, 0xEF, 0x5B, 0x9A, 0xE4,
    0xE4, 0x18, 0xFC, 0x15, 0xE8, 0x3E, 0xBE, 0xA0, 0xF8, 0x7F, 0xA9, 0xFF,
    0x5E, 0xED, 0x70, 0x05, 0x0D, 0xED, 0x28, 0x49, 0xF4, 0x7B, 0xF9, 0x59,
    0xD9, 0x56, 0x85, 0x0C, 0xE9, 0x29, 0x85, 0x1F, 0x0D, 0x81, 0x15, 0xF6,
    0x35, 0xB1, 0x05, 0xEE, 0x2E, 0x4E, 0x15, 0xD0, 0x4B, 0x24, 0x54, 0xBF,
    0x6F, 0x4F, 0xAD, 0xF0, 0x34, 0xB1, 0x04, 0x03, 0x11, 0x9C, 0xD8, 0xE3,
    0xB9, 0x2F, 0xCC, 0x5B,
])
DH_PRIME = int.from_bytes(DH_PRIME_BYTES, 'big')
DH_G = 3

# Auth / logging
LOG_LEVEL = os.environ.get('MYTELEGRAM_LOG_LEVEL', 'INFO')

_auth_bot_env = os.path.join(os.path.dirname(__file__), '.telegram_auth_bot.env')
if os.path.isfile(_auth_bot_env):
    for _line in Path(_auth_bot_env).read_text(encoding='utf-8').splitlines():
        _line = _line.strip()
        if not _line or _line.startswith('#') or '=' not in _line:
            continue
        _key, _val = _line.split('=', 1)
        _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
        if _key and _val and not os.environ.get(_key):
            os.environ[_key] = _val

AUTH_BOT_TOKEN = os.environ.get('MYTELEGRAM_AUTH_BOT_TOKEN', '').strip()
AUTH_BOT_ADMIN_IDS = frozenset(
    int(part.strip())
    for part in os.environ.get('MYTELEGRAM_AUTH_BOT_ADMINS', '8014265847').split(',')
    if part.strip().isdigit()
)

SERVICE_USER_ID = 777000
SERVICE_USER_ACCESS_HASH = 0x0BCB24150E479E3F
SERVICE_USER_NAME = 'KoalGram'
SERVICE_AVATAR_PATH = os.path.join(os.path.dirname(__file__), 'airgram.jpg')

FAKE_ACCOUNT_ABOUT = (
    '⚠️ Внимание: многие жаловались, что этот аккаунт выдаёт себя '
    'за оффициальный.'
)
SCAM_ACCOUNT_ABOUT = (
    '⚠️ Внимание: на этот аккаунт много жаловались в связи с мошенничеством '
    'или попытками выдать себя за официальный аккаунт. Будьте осторожны, '
    'особенно если у Вас попросят денег.'
)

STICKERS_BOT_ID = 888903
STICKERS_BOT_ACCESS_HASH = 0x535449434B455253
STICKERS_BOT_USERNAME = 'stickers'
STICKERS_BOT_NAME = 'Stickers'
STICKERS_BOT_ABOUT = 'A bot for creating KoalGram Stickers.'
STICKERS_BOT_AVATAR_PATH = os.path.join(os.path.dirname(__file__), 'Stickers.jpg')
STICKERS_BOT_VERIFIER_ID = 888888
STICKERS_BOT_VERIFICATION_ICON_ID = 5341745680387976374
STICKERS_BOT_VERIFICATION_COMPANY = 'KoalGram 3.0'
STICKERS_BOT_VERIFICATION_DESCRIPTION = (
    'Этот аккаунт подтвержден представителями KoalGram как официальный.'
)

REPORT_BOT_ID = 888904
REPORT_BOT_ACCESS_HASH = 0x5245504F52544254
REPORT_BOT_USERNAME = 'reportbot'
REPORT_BOT_NAME = 'Report Bot'
REPORT_BOT_ABOUT = 'A bot for reporting KoalGram users.'
REPORT_BOT_AVATAR_PATH = os.path.join(os.path.dirname(__file__), 'report.jpg')

GIFTS_BOT_ID = 888906
GIFTS_BOT_ACCESS_HASH = 0x4749465453424F54
GIFTS_BOT_USERNAME = 'giftbot'
GIFTS_BOT_PHONE = '900'
GIFTS_BOT_NAME = 'Gift Bot'
GIFTS_BOT_ABOUT = 'Бот выдачи подарков из кейсов KoalGram (@giftbot).'
GIFTS_BOT_AVATAR_PATH = os.path.join(os.path.dirname(__file__), 'airgram.jpg')

PREMIUM_BOT_ID = 888905
PREMIUM_BOT_ACCESS_HASH = 0x5052454D49554D42  # 'PREMIUMB'
PREMIUM_BOT_USERNAME = 'PremiumBot'
PREMIUM_BOT_NAME = 'Premium'
PREMIUM_BOT_ABOUT = 'KoalGram Premium subscription bot.'
PREMIUM_BOT_AVATAR_PATH = os.path.join(os.path.dirname(__file__), 'prem.png')

REPORT_ADMIN_BOT_TOKEN = os.environ.get(
    'MYTELEGRAM_REPORT_ADMIN_BOT_TOKEN', ''
).strip()
REPORT_ADMIN_IDS = frozenset(
    int(part.strip())
    for part in os.environ.get(
        'MYTELEGRAM_REPORT_ADMIN_IDS',
        '8014265847,8549770368',
    ).split(',')
    if part.strip().isdigit()
)

PRIVATE_KEY_PATH = os.path.join(os.path.dirname(__file__), 'keys', 'server_rsa.key')
DB_PATH = os.path.join(os.path.dirname(__file__), 'mytelegram.db')
STICKERS_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'stickers')
STAR_GIFTS_DOC_SET_ID = 9000000001
STAR_GIFTS_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'star_gifts')
STAR_GIFT_TESTER_USER_IDS = frozenset({888909, 888913})
GIFT_NEWS_CHANNEL = os.environ.get('MYTELEGRAM_GIFT_NEWS_CHANNEL', '@KoalGramNews')
GIFT_NEWS_SESSION = os.environ.get('MYTELEGRAM_GIFT_NEWS_SESSION', 'mysession')
GIFT_NEWS_NOTIFY_ENABLED = os.environ.get('MYTELEGRAM_GIFT_NEWS_NOTIFY', '1') != '0'
GIFT_NEWS_HEADER_EMOJI_ID = int(
    os.environ.get('MYTELEGRAM_GIFT_NEWS_HEADER_EMOJI_ID', '5341745680387976374'))
GIFT_NEWS_STAR_EMOJI_ID = int(
    os.environ.get('MYTELEGRAM_GIFT_NEWS_STAR_EMOJI_ID', '5294182967738923878'))
GIFT_NEWS_AUTH_BOT_URL = os.environ.get(
    'MYTELEGRAM_GIFT_NEWS_AUTH_BOT_URL', 'https://t.me/KoalGramApp').strip()
SHOP_WEBAPP_URL = os.environ.get(
    'SHOP_WEBAPP_URL', 'https://projectre.work/').strip()
AIRGRAM_WALLET_URL = os.environ.get(
    'AIRGRAM_WALLET_URL', 'https://projectre.work').strip()
AIRGRAM_PUBLIC_BASE_URL = os.environ.get(
    'AIRGRAM_PUBLIC_BASE_URL', 'https://projectre.work').strip().rstrip('/')
AIRGRAM_CHAIN_DB_PATH = os.environ.get(
    'AIRGRAM_CHAIN_DB_PATH',
    os.path.join(os.path.dirname(__file__), 'db', 'airgram_chain.db'))
AIRGRAM_CHAIN_MEDIA_DIR = os.environ.get(
    'AIRGRAM_CHAIN_MEDIA_DIR',
    os.path.join(os.path.dirname(__file__), 'data', 'airgram_nft_media'))
AIRGRAM_CHAIN_VALIDATOR_PATH = os.environ.get(
    'AIRGRAM_CHAIN_VALIDATOR_PATH',
    os.path.join(os.path.dirname(__file__), 'keys', 'airgram_chain_validator.json'))
UPLOADS_DOC_SET_ID = 9000000002
UPLOADS_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'uploads')
PHOTOS_DATA_DIR = os.path.join(os.path.dirname(__file__), 'data', 'photos')
DEFAULT_STARS_BALANCE = int(os.environ.get('MYTELEGRAM_DEFAULT_STARS', '100000'))
INFINITE_STARS = os.environ.get('MYTELEGRAM_INFINITE_STARS', '0') != '0'
STARS_BALANCE = int(os.environ.get(
    'MYTELEGRAM_STARS_BALANCE', str(DEFAULT_STARS_BALANCE)))
ALL_USERS_PREMIUM = os.environ.get('MYTELEGRAM_ALL_PREMIUM', '0') != '0'
PREMIUM_TRIAL_DAYS = int(os.environ.get('MYTELEGRAM_PREMIUM_TRIAL_DAYS', '7'))

NANOTON_PER_GRAM = 1_000_000_000
STARS_TO_NANOTON = int(os.environ.get('MYTELEGRAM_STARS_TO_NANOTON', '10000000'))
TON_GIFT_USER_IDS = frozenset(
    int(part.strip())
    for part in os.environ.get('MYTELEGRAM_TON_GIFT_USERS', '888909').split(',')
    if part.strip().isdigit()
)


def can_use_ton_payments(user_id: int) -> bool:
    return int(user_id) in TON_GIFT_USER_IDS


def stars_to_nanoton(stars: int) -> int:
    return max(0, int(stars)) * int(STARS_TO_NANOTON)


def nanoton_to_stars(nanos: int) -> int:
    ratio = int(STARS_TO_NANOTON)
    if ratio <= 0:
        return 0
    return max(0, int(nanos)) // ratio


def format_gram_amount(nanos: int) -> str:
    value = int(nanos) / NANOTON_PER_GRAM
    text = f'{value:.6f}'.rstrip('0').rstrip('.')
    return text or '0'


TON_TOPUP_URL = os.environ.get('MYTELEGRAM_TON_TOPUP_URL', '').strip()
# USD per 1 GRAM/TON — used by the client to convert Stars ↔ GRAM in gift UI.
TON_USD_RATE = float(os.environ.get('MYTELEGRAM_TON_USD_RATE', '3'))

# Telegram Android AmountUtils stores stars as amount*1e9 in a signed long.
# Values above this overflow and break gift-offer balance checks on Android.
ANDROID_STARS_OVERFLOW_MAX = (2**63 - 1) // 1_000_000_000


def client_stars_balance(balance: int, *, peak: int = 0) -> int:
    """Map DB balance to a value Android can use without long overflow.

    Balances within ANDROID_STARS_OVERFLOW_MAX are sent as-is (unique per user,
    decrease with every spend).  Higher balances are mapped from the user's peak
    so spending still reduces the client-visible amount while staying overflow-safe.
    """
    balance = int(balance)
    peak = max(int(peak or 0), balance)
    if balance <= ANDROID_STARS_OVERFLOW_MAX:
        return balance
    spent = peak - balance
    return max(0, ANDROID_STARS_OVERFLOW_MAX - spent)

# Star gift resale / offer limits (match Telegram client config keys)
STARGIFT_OFFER_MIN_STARS = int(os.environ.get('MYTELEGRAM_OFFER_MIN_STARS', '125'))
STARGIFT_RESALE_STARS_MIN = int(os.environ.get('MYTELEGRAM_RESALE_STARS_MIN', '125'))
STARGIFT_RESALE_STARS_MAX = int(os.environ.get('MYTELEGRAM_RESALE_STARS_MAX', '1000000'))
STARGIFT_RESALE_TON_MIN = int(os.environ.get('MYTELEGRAM_RESALE_TON_MIN', '100000000'))
STARGIFT_RESALE_TON_MAX = int(os.environ.get('MYTELEGRAM_RESALE_TON_MAX', '100000000000000'))
# Seller/receiver share (permille): payout = amount * permille / 1000
STARGIFT_RESALE_COMMISSION_PERMILLE = int(
    os.environ.get('MYTELEGRAM_RESALE_COMMISSION_PERMILLE', '800'))
# Craft attribute persistence permilles per input count (layer 224 app config).
STARGIFTS_CRAFT_ATTRIBUTE_PERMILLES = [
    [60, 180, 450, 1000],
    [50, 150, 380, 900],
    [40, 120, 300, 750],
    [30, 90, 250, 600],
]
STARGIFT_OFFER_DURATIONS = (21600, 43200, 86400, 129600, 172800, 259200, 120)
# Craft success contribution per gift (permille). UI shows (permille+5)/10 %.
STARGIFT_CRAFT_CHANCE_PERMILLE = int(
    min(250, max(1, int(os.environ.get(
        'MYTELEGRAM_CRAFT_CHANCE_PERMILLE', '250')))))
CHANNEL_ID_MIN = 800000000000

# Profile star rating (core.telegram.org/api/stars — Star rating)
STARS_RATING_MAX_LEVEL = 100
STARS_RATING_MARKET_PERMILLE = 200    # +20% for market / paid messages / offers
STARS_RATING_REFUND_MULTIPLIER = 1    # refund lowers rating by returned stars (1:1)
STARS_RATING_CONVERT_PENALTY_PERMILLE = 850  # up to 85% penalty on gift exchange
STARS_RATING_LEARNMORE_URL = 'https://telegram.org/blog/post-search-story-albums-and-more#profile-rating'

# Paid messages (corefork.telegram.org/api/paid-messages)
STARS_PAID_MESSAGES_AVAILABLE = os.environ.get(
    'MYTELEGRAM_PAID_MESSAGES', '1') != '0'
STARS_PAID_MESSAGE_AMOUNT_MAX = int(
    os.environ.get('MYTELEGRAM_PAID_MESSAGE_MAX', '10000'))
STARS_PAID_MESSAGE_AMOUNT_DEFAULT = int(
    os.environ.get('MYTELEGRAM_PAID_MESSAGE_DEFAULT', '1'))
STARS_PAID_MESSAGE_COMMISSION_PERMILLE = int(
    os.environ.get('MYTELEGRAM_PAID_MESSAGE_COMMISSION', '800'))


def _parse_proxy_secret_line(line: str) -> bytes:
    """Decode one proxy secret entry into raw bytes.

    Accepts plain hex and fakeTLS ``dd`` secrets whose camouflage domain is
    appended as ASCII text (the format used in tg:// proxy links).
    """
    line = line.strip()
    if line[:2].lower() == 'dd' and len(line) >= 34:
        hex_part = line[:34]
        tail = line[34:]
        try:
            raw = bytes.fromhex(hex_part)
            if tail:
                raw += tail.encode('ascii', 'ignore')
            return raw
        except ValueError:
            pass
    return bytes.fromhex(line)


def _load_mtproxy_secrets() -> list[bytes]:
    """Secrets to try for MTProto proxy (_obf) transport. Empty = direct."""
    secrets: list[bytes] = [b'']
    seen = {b''}

    def add(raw: bytes) -> None:
        if raw not in seen:
            seen.add(raw)
            secrets.append(raw)

    env_one = os.environ.get('MYTELEGRAM_MTPROXY_SECRET', '').strip()
    if env_one:
        add(_parse_proxy_secret_line(env_one))

    env_many = os.environ.get('MYTELEGRAM_MTPROXY_SECRETS', '').strip()
    if env_many:
        for part in env_many.split(','):
            part = part.strip()
            if part:
                add(_parse_proxy_secret_line(part))

    secret_file = os.path.join(os.path.dirname(__file__), 'proxy_secret.txt')
    if os.path.isfile(secret_file):
        for line in Path(secret_file).read_text(encoding='utf-8').splitlines():
            line = line.strip().split('#', 1)[0].strip()
            if line:
                add(_parse_proxy_secret_line(line))

    # Common secrets used with local MTProto proxies
    for hex_str in (
        '00000000000000000000000000000000',
        '0123456789abcdef0123456789abcdef',
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
        'dddddddddddddddddddddddddddddddd',
    ):
        add(bytes.fromhex(hex_str))

    return secrets


MTPROXY_SECRETS = _load_mtproxy_secrets()


def _load_faketls_secrets() -> list[tuple[bytes, bytes, frozenset]]:
    """fakeTLS (``dd``) secrets as (raw, 16-byte core, domains) triples."""
    from mtproto.faketls import secret_domains

    out: list[tuple[bytes, bytes, frozenset]] = []
    seen: set[bytes] = set()

    def add(raw: bytes) -> None:
        if len(raw) < 17 or raw[0] != 0xDD or raw in seen:
            return
        seen.add(raw)
        core = raw[1:17]
        domains = frozenset(
            part.strip().lower() for part in secret_domains(raw) if part.strip())
        if domains:
            out.append((raw, core, domains))

    for env_key in ('MYTELEGRAM_FAKETLS_SECRET', 'MYTELEGRAM_FAKETLS_SECRETS'):
        value = os.environ.get(env_key, '').strip()
        if not value:
            continue
        for part in value.split(','):
            part = part.strip()
            if part:
                try:
                    add(_parse_proxy_secret_line(part))
                except ValueError:
                    pass

    secret_file = os.path.join(os.path.dirname(__file__), 'proxy_secret.txt')
    if os.path.isfile(secret_file):
        for line in Path(secret_file).read_text(encoding='utf-8').splitlines():
            line = line.strip().split('#', 1)[0].strip()
            if line:
                try:
                    add(_parse_proxy_secret_line(line))
                except ValueError:
                    pass
    return out


FAKETLS_SECRETS = _load_faketls_secrets()

# Trending custom-emoji packs (t.me/addemoji/…). Order = display order in «Popular».
FEATURED_EMOJI_CATALOG = [
    'DuckEmoji',
    'CrayonsEmoji',
    'BirthdayCollection',
    'CreepyEmoji',
    'EmoticonEmoji',       # adaptive (text_color)
    'MemeEmoji',
    'AnimatedAsianEmoji',
    'ABCEmoji',
    'GameEmoji',
    'OutlineEmoji',        # adaptive (text_color)
    'LoveDayEmoji',
    'MashupEmoji',
    'PepeEmoji',
    'MatrixFont',
    'FaceEmoji',
]

# Internal/system emoji sets — never in Trends or search; link/bootstrap only.
EMOJI_CATALOG_HIDDEN = frozenset({
    'AirgramBannerEmoji',
    'AnimatedEmojies',
    'CherryEmoji',
    'CustomVerifications',
    'EmojiAnimations',
    'EmojiGenericAnimations',
    'holdverification',
    'MajorVerification',
    'RestrictedEmoji',
    'StatusPack',
    'Topics',
    'UtilityTgs',
    'VerifiedTgUser',
    'ZVZOVZV',
})
