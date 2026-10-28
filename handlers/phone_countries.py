"""Phone display formatting and help.CountriesList for registration UI."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from mtproto import tl

from handlers.test_phones import TEST_COUNTRY, TEST_COUNTRY_CODE

log = logging.getLogger(__name__)

HELP_COUNTRY = 0xc3878e23
HELP_COUNTRY_CODE = 0x4203c5ef
HELP_COUNTRIES_LIST = 0x87d0759e
HELP_COUNTRIES_LIST_NOT_MODIFIED = 0x93cc1f32

_COUNTRIES_PATH = Path(__file__).resolve().parent.parent / 'data' / 'phone_countries.json'


def normalize_phone(phone: str) -> str:
    return ''.join(c for c in (phone or '').strip().lstrip('+') if c.isdigit())


def canonical_anon_phone(phone: str) -> str:
    """Normalize anonymous KoalGram numbers to 888-prefixed storage."""
    digits = normalize_phone(phone)
    if not digits:
        return digits
    if digits.startswith('888'):
        return digits
    if digits.startswith('9') and not digits.startswith('79'):
        rest = digits[1:]
        if len(rest) in (4, 8):
            return '888' + rest
    if digits.startswith('88') and not digits.startswith('888'):
        rest = digits[2:]
        if len(rest) in (4, 8):
            return '888' + rest
    return digits


def anon_phone_lookup_variants(phone: str) -> list[str]:
    """All stored forms that may refer to the same anonymous number."""
    digits = normalize_phone(phone)
    if not digits:
        return []
    variants: list[str] = []
    for candidate in (digits, canonical_anon_phone(digits)):
        if candidate and candidate not in variants:
            variants.append(candidate)
    canonical = canonical_anon_phone(digits)
    if canonical.startswith('888') and len(canonical) > 3:
        legacy = '9' + canonical[3:]
        if legacy not in variants:
            variants.append(legacy)
    return variants


def _format_by_pattern(digits: str, country_code: str,
                       patterns: List[str]) -> Optional[str]:
    national = digits
    if national.startswith(country_code):
        national = national[len(country_code):]
    pattern = patterns[0] if patterns else ''
    if not pattern:
        return None
    groups = pattern.split()
    needed = sum(1 for ch in pattern if ch == 'X')
    if len(national) < needed:
        return None
    parts = []
    idx = 0
    for group in groups:
        count = sum(1 for ch in group if ch == 'X')
        parts.append(national[idx:idx + count])
        idx += count
    return f'+{country_code} ' + ' '.join(parts)


@lru_cache(maxsize=1)
def _countries_payload() -> tuple[list[dict], int]:
    if not _COUNTRIES_PATH.is_file():
        log.warning('phone countries cache missing: %s', _COUNTRIES_PATH)
        countries = [_default_airgram_country(), _default_ru_country()]
        cur_hash = 1
    else:
        data = json.loads(_COUNTRIES_PATH.read_text(encoding='utf-8'))
        countries = list(data.get('countries') or [])
        cur_hash = int(data.get('hash') or 1)
    if not any(
        str(cc.get('country_code') or '') == TEST_COUNTRY_CODE
        for country in countries
        for cc in (country.get('country_codes') or [])
    ):
        countries.append(TEST_COUNTRY)
        cur_hash = int(cur_hash) + 999001
    return countries, cur_hash


def _default_ru_country() -> dict:
    return {
        'iso2': 'RU',
        'default_name': 'Russian Federation',
        'name': 'Россия',
        'hidden': False,
        'country_codes': [{
            'country_code': '7',
            'prefixes': [],
            'patterns': ['XXX XXX XXXX'],
        }],
    }


def _default_airgram_country() -> dict:
    return {
        'iso2': 'AQ',
        'default_name': 'KoalGram Number',
        'name': 'Номер KoalGram',
        'hidden': False,
        'country_codes': [{
            'country_code': '9',
            'prefixes': [],
            'patterns': ['XXX XX XXX'],
        }],
    }


def format_phone_display(phone: str) -> str:
    """Format stored digits for UI/profile display."""
    digits = canonical_anon_phone(normalize_phone(phone))
    if not digits:
        return ''
    if len(digits) == 11 and digits.startswith('7'):
        return f'+7 {digits[1:4]} {digits[4:7]} {digits[7:11]}'
    if digits.startswith('888'):
        rest = digits[3:]
        if len(rest) <= 1:
            return f'+888 {rest}'
        if len(rest) <= 4:
            return f'+888 {rest[0]} {rest[1:]}'
        if len(rest) >= 8:
            return f'+888 {rest[:4]} {rest[4:8]}'
        return f'+888 {rest[:4]} {rest[4:]}'
    if digits.startswith(TEST_COUNTRY_CODE):
        national = digits[len(TEST_COUNTRY_CODE):]
        if len(national) >= 8:
            return (
                f'+{TEST_COUNTRY_CODE} {national[:3]} '
                f'{national[3:6]} {national[6:8]} {national[8:]}'
            ).strip()
        return f'+{TEST_COUNTRY_CODE} {national}'.strip()
    if digits.startswith('9') and len(digits) in (5, 6, 7, 8, 9, 10):
        if len(digits) == 5:
            return f'+9 {digits[1:4]} {digits[4:]}'
        if len(digits) == 6:
            return f'+9 {digits[1:4]} {digits[4:]}'
        if len(digits) == 7:
            return f'+9 {digits[1:4]} {digits[4:6]} {digits[6:]}'
        if len(digits) == 8:
            return f'+9 {digits[1:4]} {digits[4:6]} {digits[6:]}'
        if len(digits) == 9:
            return f'+9 {digits[1:4]} {digits[4:6]} {digits[6:9]}'
        return f'+9 {digits[1:4]} {digits[4:6]} {digits[6:10]}'
    countries, _ = _countries_payload()
    best: Optional[str] = None
    best_len = -1
    for country in countries:
        for cc in country.get('country_codes') or []:
            code = str(cc.get('country_code') or '')
            if not code or not digits.startswith(code):
                continue
            if len(code) <= best_len:
                continue
            formatted = _format_by_pattern(
                digits, code, list(cc.get('patterns') or []))
            if formatted:
                best = formatted
                best_len = len(code)
    if best:
        return best
    return f'+{digits}'


def is_collectible_phone(phone: str) -> bool:
    digits = canonical_anon_phone(normalize_phone(phone))
    if not digits:
        return False
    if digits.startswith('888'):
        return len(digits) >= 7
    # Legacy ultra-short KoalGram numbers stored as 9XXX (displayed as +9000).
    if digits.startswith('9') and len(digits) in (4, 5):
        return True
    return False


def phone_for_user_field(phone: str) -> str:
    """Phone string for User.phone — digits only; clients format for display."""
    return normalize_phone(phone)


def pack_country_code(cc: dict) -> bytes:
    prefixes = list(cc.get('prefixes') or [])
    patterns = list(cc.get('patterns') or [])
    flags = 0
    if prefixes:
        flags |= 1
    if patterns:
        flags |= 2
    w = tl.Writer()
    w.u32(HELP_COUNTRY_CODE)
    w.i32(flags)
    w.string(str(cc.get('country_code') or ''))
    if flags & 1:
        w.u32(tl.VECTOR)
        w.i32(len(prefixes))
        for item in prefixes:
            w.string(str(item))
    if flags & 2:
        w.u32(tl.VECTOR)
        w.i32(len(patterns))
        for item in patterns:
            w.string(str(item))
    return w.bytes_out()


def pack_country(country: dict) -> bytes:
    flags = 0
    if country.get('hidden'):
        flags |= 1
    localized = country.get('name')
    if localized:
        flags |= 2
    codes = list(country.get('country_codes') or [])
    w = tl.Writer()
    w.u32(HELP_COUNTRY)
    w.i32(flags)
    w.string(str(country.get('iso2') or ''))
    w.string(str(country.get('default_name') or ''))
    if flags & 2:
        w.string(str(localized))
    w.u32(tl.VECTOR)
    w.i32(len(codes))
    for cc in codes:
        w.raw(pack_country_code(cc))
    return w.bytes_out()


def pack_countries_list(req_hash: int) -> bytes:
    countries, cur_hash = _countries_payload()
    if req_hash and int(req_hash) == int(cur_hash):
        w = tl.Writer()
        w.u32(HELP_COUNTRIES_LIST_NOT_MODIFIED)
        return w.bytes_out()
    w = tl.Writer()
    w.u32(HELP_COUNTRIES_LIST)
    w.u32(tl.VECTOR)
    w.i32(len(countries))
    for country in countries:
        w.raw(pack_country(country))
    w.i32(int(cur_hash))
    return w.bytes_out()
