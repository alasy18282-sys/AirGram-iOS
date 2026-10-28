#!/usr/bin/env python3
"""Refresh collectible gift values from Telegram's live resale market."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo
from babel.numbers import get_currency_precision

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from db.storage import Storage
from frame_verification.telethon_env import (
    prepare_telethon_session,
    telethon_credentials,
)

try:
    from telethon import TelegramClient
    from telethon.tl.functions.payments import (
        GetResaleStarGiftsRequest,
        GetUniqueStarGiftValueInfoRequest,
    )
except ImportError as exc:  # pragma: no cover - deployment dependency check
    raise SystemExit('Install telethon to refresh gift values') from exc

log = logging.getLogger('star_gift_values')
MOSCOW = ZoneInfo('Europe/Moscow')


def _usd_base_rates() -> dict[str, float]:
    with urlopen('https://open.er-api.com/v6/latest/USD', timeout=15) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if payload.get('result') != 'success':
        raise RuntimeError('currency conversion rates unavailable for USD')
    rates = {str(code).upper(): float(rate)
             for code, rate in (payload.get('rates') or {}).items()
             if float(rate) > 0}
    rates['USD'] = 1.0
    return rates


def _usd_rate(currency: str) -> float:
    currency = (currency or '').upper()
    if currency == 'USD':
        return 1.0
    url = f'https://open.er-api.com/v6/latest/{currency}'
    with urlopen(url, timeout=15) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if payload.get('result') != 'success':
        raise RuntimeError(f'currency conversion unavailable for {currency}')
    rate = (payload.get('rates') or {}).get('USD')
    if not rate or float(rate) <= 0:
        raise RuntimeError(f'USD conversion rate missing for {currency}')
    return float(rate)


def _as_optional_int(obj, name: str):
    value = getattr(obj, name, None)
    if isinstance(value, datetime):
        return int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp())
    return int(value) if value is not None else None


async def refresh_star_gift_values(session: str = 'mysession') -> tuple[int, int]:
    storage = Storage.open_tool()
    cleanup_session = None
    updated = failed = 0
    try:
        try:
            exchange_rates = await asyncio.to_thread(_usd_base_rates)
            storage.upsert_currency_exchange_rates(exchange_rates)
            log.info('Updated fiat exchange rates for %d currencies',
                     len(exchange_rates))
        except Exception:
            log.exception('Could not refresh fiat exchange rates; using cached rates')

        # Values are profile data, not shop data: collectible gifts hidden
        # from the purchase catalog still need to stay in sync with Telegram.
        # Basic gifts have no collectible title and Telegram's resale API
        # cannot provide a market value for them; the UI uses the marked
        # Stars-based estimate for those gifts instead.
        cached_at = {
            int(row['gift_id']): int(row['updated_at'] or 0)
            for row in storage._conn.execute(
                'SELECT gift_id, updated_at FROM star_gift_value_cache'
            ).fetchall()
        }
        refresh_before = int(time.time()) - 24 * 60 * 60
        gifts = [g for g in storage.list_star_gifts()
                 if g.get('title')
                 and cached_at.get(int(g['gift_id']), 0) < refresh_before]
        if not gifts:
            return 0, 0

        session_path, cleanup_session = prepare_telethon_session(session)
        api_id, api_hash = telethon_credentials()
        async with TelegramClient(
                session_path, api_id, api_hash,
                connection_retries=4, retry_delay=3, request_retries=3) as client:
            for gift in gifts:
                gift_id = int(gift['gift_id'])
                try:
                    resale = await client(GetResaleStarGiftsRequest(
                        gift_id=gift_id, offset='', limit=1,
                        sort_by_price=True))
                    listings = list(getattr(resale, 'gifts', None) or [])
                    if not listings:
                        log.info('No Telegram resale value yet for %s (%s)',
                                 gift.get('title') or gift_id, gift_id)
                        continue
                    source = listings[0]
                    info = await client(GetUniqueStarGiftValueInfoRequest(
                        slug=str(source.slug)))
                    currency = str(getattr(info, 'currency', '') or '').upper()
                    if not currency:
                        raise RuntimeError('Telegram returned no value currency')

                    average_price = _as_optional_int(info, 'average_price')
                    amount = (average_price if average_price is not None
                              else int(info.value))
                    source_rate = (exchange_rates.get(currency)
                                   if 'exchange_rates' in locals() else None)
                    if not source_rate:
                        source_rate = 1.0 / await asyncio.to_thread(
                            _usd_rate, currency)
                    source_precision = int(get_currency_precision(currency))
                    usd_cents = int(round(
                        amount / (10 ** source_precision)
                        / source_rate * 100))
                    payload = {
                        'gift_id': gift_id,
                        'currency': currency,
                        'value': amount,
                        'value_usd_amount': usd_cents,
                        'value_is_average': int(
                            average_price is not None
                            or bool(getattr(info, 'value_is_average', False))),
                        'initial_sale_date': _as_optional_int(
                            info, 'initial_sale_date') or 0,
                        'initial_sale_stars': int(
                            getattr(info, 'initial_sale_stars', 0) or 0),
                        'initial_sale_price': int(
                            getattr(info, 'initial_sale_price', 0) or 0),
                        'last_sale_date': _as_optional_int(info, 'last_sale_date'),
                        'last_sale_price': _as_optional_int(info, 'last_sale_price'),
                        'last_sale_on_fragment': int(bool(getattr(
                            info, 'last_sale_on_fragment', False))),
                        'floor_price': _as_optional_int(info, 'floor_price'),
                        'average_price': average_price,
                        'listed_count': _as_optional_int(info, 'listed_count'),
                        'fragment_listed_count': _as_optional_int(
                            info, 'fragment_listed_count'),
                        'fragment_listed_url': getattr(
                            info, 'fragment_listed_url', None),
                        'source_slug': str(source.slug),
                        'updated_at': int(time.time()),
                    }
                    storage.upsert_star_gift_value(payload)
                    updated += 1
                    log.info('Updated Telegram value for %s (%s): %s %s',
                             gift.get('title') or gift_id, gift_id,
                             amount, currency)
                except Exception:
                    failed += 1
                    log.exception('Could not refresh Telegram gift value for %s',
                                  gift.get('title') or gift_id)
        return updated, failed
    finally:
        if cleanup_session:
            cleanup_session()
        try:
            storage._conn._conn.close()
        except Exception:
            pass


async def daily_refresh_loop() -> None:
    """Refresh now, then daily at 04:15 Moscow time."""
    while True:
        try:
            updated, failed = await refresh_star_gift_values()
            log.info('Gift value refresh complete: updated=%d failed=%d',
                     updated, failed)
        except Exception:
            log.exception('Gift value refresh failed')

        now = datetime.now(MOSCOW)
        next_run = datetime.combine(
            now.date(), datetime_time(hour=4, minute=15), tzinfo=MOSCOW)
        if next_run <= now:
            next_run += timedelta(days=1)
        await asyncio.sleep(max(1.0, (next_run - now).total_seconds()))


async def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', default='mysession')
    args = parser.parse_args()
    updated, failed = await refresh_star_gift_values(args.session)
    print(f'Updated {updated} gift values; failed {failed}.')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
