"""Profile star rating (levels 1–100) — mirrors core.telegram.org/api/stars."""
from __future__ import annotations

import config

MAX_LEVEL = int(getattr(config, 'STARS_RATING_MAX_LEVEL', 100))

# Cumulative rating points required to reach each level (1..MAX_LEVEL).
# Early levels and the L10–L79 +13k step / L80+ ×1.4 curve match Telegram.
_LEVEL_THRESHOLDS: list[int] | None = None


def _build_level_thresholds(max_level: int = MAX_LEVEL) -> list[int]:
    thresholds = [0] * (max_level + 2)
    thresholds[1] = 1
    thresholds[2] = 5000
    cur = 5000
    early_deltas = [7000, 7000, 8000, 9000, 10000, 11000, 11000, 13000]
    for level in range(3, 11):
        cur += early_deltas[level - 3]
        thresholds[level] = cur
    for level in range(11, 80):
        thresholds[level] = thresholds[10] + (level - 10) * 13000
    thresholds[80] = 1_000_000
    cur = 1_000_000
    for level in range(81, max_level + 1):
        cur = cur * 14 // 10
        thresholds[level] = cur
    return thresholds


def _thresholds() -> list[int]:
    global _LEVEL_THRESHOLDS
    if _LEVEL_THRESHOLDS is None:
        _LEVEL_THRESHOLDS = _build_level_thresholds()
    return _LEVEL_THRESHOLDS


def level_threshold(level: int) -> int:
    """Minimum rating points to reach `level` (1..MAX_LEVEL)."""
    if level <= 0:
        return 0
    level = min(int(level), MAX_LEVEL)
    return _thresholds()[level]


def compute_stars_rating(points: int) -> dict:
    """Build starsRating fields from cumulative rating points."""
    points = int(points)

    if points < 0:
        return {
            'level': -1,
            'current_level_stars': 0,
            'stars': points,
            'next_level_stars': level_threshold(1),
        }

    if points == 0:
        return {
            'level': 0,
            'current_level_stars': 0,
            'stars': 0,
            'next_level_stars': level_threshold(1),
        }

    thresholds = _thresholds()
    level = 0
    for lv in range(1, MAX_LEVEL + 1):
        if points >= thresholds[lv]:
            level = lv
        else:
            break

    current = thresholds[level] if level > 0 else 0
    nxt = thresholds[level + 1] if level < MAX_LEVEL else None
    return {
        'level': level,
        'current_level_stars': current,
        'stars': points,
        'next_level_stars': nxt,
    }


def compute_rating_delta(amount_delta: int, **kw) -> int:
    """Rating points change for a stars balance movement."""
    if kw.get('_skip_rating'):
        return 0

    amount = abs(int(amount_delta))
    if amount == 0:
        return 0

    title = (kw.get('title') or '').lower()

    if kw.get('refund'):
        # Refund of spent stars: heavy penalty (Telegram uses 10× returned amount).
        # Exception: releasing a gift-offer hold is not a purchase refund —
        # undo only the market rating gained when the hold was placed.
        if int(amount_delta) > 0:
            if kw.get('offer'):
                market_permille = int(
                    getattr(config, 'STARS_RATING_MARKET_PERMILLE', 200))
                return -(amount * market_permille // 1000)
            mult = int(getattr(config, 'STARS_RATING_REFUND_MULTIPLIER', 10))
            return -amount * mult
        return 0

    if int(amount_delta) > 0:
        return 0

    market_permille = int(getattr(config, 'STARS_RATING_MARKET_PERMILLE', 200))

    if kw.get('gift') and not kw.get('stargift_resale'):
        return amount

    if kw.get('stargift_upgrade'):
        return amount

    if kw.get('paid_message') or 'paid message' in title:
        return amount * market_permille // 1000

    if kw.get('stargift_resale') or kw.get('offer') or kw.get('stargift_auction_bid'):
        return amount * market_permille // 1000

    if 'gift transfer' in title:
        return amount * market_permille // 1000

    return 0


def compute_convert_penalty(gift_stars: int) -> int:
    """Penalty when exchanging a gift back to stars (up to 85% of gift cost)."""
    stars = max(0, int(gift_stars))
    if stars <= 0:
        return 0
    permille = int(getattr(config, 'STARS_RATING_CONVERT_PENALTY_PERMILLE', 850))
    return -(stars * permille // 1000)
