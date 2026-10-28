"""Per-account currency display for collectible gift valuations."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from babel.numbers import get_currency_precision, get_territory_currencies


_COUNTRIES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'phone_countries.json'


@lru_cache(maxsize=1)
def _calling_codes() -> list[tuple[str, str]]:
    try:
        countries = json.loads(_COUNTRIES_PATH.read_text(encoding='utf-8'))['countries']
    except (OSError, ValueError, KeyError):
        return []
    codes = []
    for country in countries:
        iso2 = str(country.get('iso2') or '').upper()
        for entry in country.get('country_codes') or []:
            code = ''.join(ch for ch in str(entry.get('country_code') or '')
                           if ch.isdigit())
            if code and iso2:
                codes.append((code, iso2))
    return sorted(set(codes), key=lambda item: len(item[0]), reverse=True)


def currency_for_phone(phone: str) -> str | None:
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if digits.startswith('1'):
        return 'USD'
    if digits.startswith('7'):
        return 'RUB'
    for calling_code, iso2 in _calling_codes():
        if digits.startswith(calling_code):
            currencies = get_territory_currencies(iso2, tender=True)
            if currencies:
                return currencies[0]
    return None


def _precision(currency: str) -> int:
    try:
        return int(get_currency_precision(currency))
    except Exception:
        return 2


def _convert_minor(amount: int, source: str, target: str,
                   storage) -> int | None:
    source_rate = storage.get_currency_exchange_rate(source)
    target_rate = storage.get_currency_exchange_rate(target)
    if not source_rate or not target_rate:
        return None
    major = int(amount) / (10 ** _precision(source))
    return int(round(major / source_rate * target_rate
                     * (10 ** _precision(target))))


def localize_star_gift_value(info: dict, storage, viewer_id: int) -> dict:
    """Convert cached USD-backed market estimates to the viewer's phone region."""
    user = storage.get_user_by_id(int(viewer_id)) if viewer_id else None
    target = currency_for_phone((user or {}).get('phone') or '')
    source = str(info.get('currency') or '').upper()
    if not target or not source or target == source:
        return dict(info)

    out = dict(info)
    if target == 'USD' and info.get('value_usd_amount') is not None:
        out['value'] = int(info['value_usd_amount'])
    else:
        target_rate = storage.get_currency_exchange_rate(target)
        if not target_rate:
            return out
        usd_minor = int(info.get('value_usd_amount') or 0)
        out['value'] = int(round(
            usd_minor / (10 ** _precision('USD')) * target_rate
            * (10 ** _precision(target))))

    for key in ('initial_sale_price', 'last_sale_price', 'floor_price',
                'average_price'):
        if info.get(key) is None:
            continue
        converted = _convert_minor(int(info[key]), source, target, storage)
        if converted is not None:
            out[key] = converted
    out['currency'] = target
    return out
