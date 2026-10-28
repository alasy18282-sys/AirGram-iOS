"""Fixed test phone numbers (+999) for QA login."""
from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Optional

from db.storage import normalize_phone

if TYPE_CHECKING:
    from db.storage import Storage

TEST_COUNTRY_CODE = '999'
TEST_PHONE = '99944444244'
TEST_CODE = '91209'
TEST_COUNTRY = {
    'iso2': 'T9',
    'default_name': 'Test Numbers',
    'name': 'Test Numbers',
    'hidden': False,
    'country_codes': [{
        'country_code': TEST_COUNTRY_CODE,
        'prefixes': [],
        'patterns': ['XXX XXX XX XX'],
    }],
}


def is_test_country_phone(phone: str) -> bool:
    digits = normalize_phone(phone)
    return bool(digits.startswith(TEST_COUNTRY_CODE))


def is_allowed_test_phone(phone: str) -> bool:
    return normalize_phone(phone) == TEST_PHONE


def test_phone_disabled(phone: str) -> bool:
    """+999 numbers other than the single allowed test phone."""
    if not is_test_country_phone(phone):
        return False
    return not is_allowed_test_phone(phone)


def create_test_phone_code(storage: Storage, phone: str) -> tuple[str, str]:
    phone = normalize_phone(phone)
    code_hash = secrets.token_hex(16)
    expires = int(time.time()) + 86400 * 365
    storage._conn.execute('DELETE FROM phone_codes WHERE phone=?', (phone,))
    storage._conn.execute(
        'INSERT INTO phone_codes VALUES (?,?,?,?,0)',
        (phone, TEST_CODE, code_hash, expires),
    )
    storage._conn.commit()
    return TEST_CODE, code_hash


def test_sign_in_rejected(phone: str, code: str) -> bool:
    if test_phone_disabled(phone):
        return True
    if is_allowed_test_phone(phone) and code != TEST_CODE:
        return True
    return False
