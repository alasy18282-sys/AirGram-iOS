"""Star gift attribute rarity helpers (aligned with tdesktop UniqueGiftRarity)."""
from __future__ import annotations

from mtproto import tl

STAR_GIFT_ATTRIBUTE_RARITY = 0x36437737
STAR_GIFT_ATTRIBUTE_RARITY_UNCOMMON = 0xdbce6389
STAR_GIFT_ATTRIBUTE_RARITY_RARE = 0xf08d516b
STAR_GIFT_ATTRIBUTE_RARITY_EPIC = 0x78fbf3a8
STAR_GIFT_ATTRIBUTE_RARITY_LEGENDARY = 0xcef7e7a8

TIER_UNCOMMON = -1
TIER_RARE = -2
TIER_EPIC = -3
TIER_LEGENDARY = -4

TIER_LABELS = {
    TIER_UNCOMMON: 'uncommon',
    TIER_RARE: 'rare',
    TIER_EPIC: 'epic',
    TIER_LEGENDARY: 'legendary',
}

_TIER_BY_TYPE = {
    'StarGiftAttributeRarityUncommon': TIER_UNCOMMON,
    'StarGiftAttributeRarityRare': TIER_RARE,
    'StarGiftAttributeRarityEpic': TIER_EPIC,
    'StarGiftAttributeRarityLegendary': TIER_LEGENDARY,
}


def parse_telegram_rarity(rarity) -> int:
    """Parse MTProto/telethon rarity into stored rarity_permille/tier value."""
    if rarity is None:
        return 100
    tier = _TIER_BY_TYPE.get(type(rarity).__name__)
    if tier is not None:
        return tier
    permille = getattr(rarity, 'permille', None)
    if permille is not None:
        value = max(int(permille), 0)
        return value if value > 0 else 100
    return 100


def rarity_is_tier(rarity_value: int) -> bool:
    return int(rarity_value) < 0


def rarity_label(rarity_value: int) -> str:
    rv = int(rarity_value)
    tier = TIER_LABELS.get(rv)
    if tier:
        return tier
    if rv <= 0:
        return '< 0.1%'
    return f'{rv / 10:g}%'


def craft_model_pick_weight(rarity_value: int) -> int:
    """Weight for random crafted-model pick; tiers use equal weight."""
    if rarity_is_tier(rarity_value):
        return 100
    return max(int(rarity_value), 1)


def pack_star_gift_rarity(rarity_value: int) -> bytes:
    rv = int(rarity_value)
    w = tl.Writer()
    if rv == TIER_UNCOMMON:
        w.u32(STAR_GIFT_ATTRIBUTE_RARITY_UNCOMMON)
    elif rv == TIER_RARE:
        w.u32(STAR_GIFT_ATTRIBUTE_RARITY_RARE)
    elif rv == TIER_EPIC:
        w.u32(STAR_GIFT_ATTRIBUTE_RARITY_EPIC)
    elif rv == TIER_LEGENDARY:
        w.u32(STAR_GIFT_ATTRIBUTE_RARITY_LEGENDARY)
    else:
        w.u32(STAR_GIFT_ATTRIBUTE_RARITY)
        w.i32(rv)
    return w.bytes_out()
