import sqlite3
import unittest
from unittest import mock

from shop_webapp import service
from shop_webapp.cases import ensure_cases_schema


class _TestStorage:
    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row


class TopDonorTotalsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.storage = _TestStorage()
        service._ensure_tables(self.storage)
        ensure_cases_schema(self.storage)
        self.storage._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bot_phone_assignments (
                telegram_user_id INTEGER PRIMARY KEY,
                phone TEXT UNIQUE NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telegram_phones (
                telegram_user_id INTEGER NOT NULL,
                phone TEXT NOT NULL,
                created_at INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        self.storage._conn.execute(
            """
            CREATE TABLE frame_verification_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_payment_id TEXT UNIQUE NOT NULL,
                buyer_telegram_id INTEGER,
                stars_amount INTEGER NOT NULL,
                status TEXT NOT NULL,
                refunded_at INTEGER
            )
            """
        )
        service._TOP_DONORS_CACHE = None
        service._TOP_DONORS_CACHE_AT = 0.0

    def tearDown(self) -> None:
        self.storage._conn.close()
        service._TOP_DONORS_CACHE = None
        service._TOP_DONORS_CACHE_AT = 0.0

    def test_sums_all_real_payments_and_excludes_airgram_cases(self) -> None:
        payer = 1001
        other_payer = 2002
        conn = self.storage._conn

        conn.execute(
            """INSERT INTO shop_star_orders
               (nonce, user_id, target_user_id, stars, xtr, status, payment_id,
                created_at, buyer_telegram_id)
               VALUES ('star', 1, 1, 100000, 10, 'paid', 'stx-star', 1, ?)""",
            (payer,),
        )
        conn.execute(
            """INSERT INTO shop_anon_orders
               (nonce, user_id, target_user_id, kind, digits, xtr, status,
                payment_id, created_at, buyer_telegram_id)
               VALUES ('anon', 1, 1, 'random_8', '', 20, 'paid', 'stx-anon',
                       2, ?)""",
            (payer,),
        )
        conn.execute(
            """INSERT INTO shop_username_orders
               (nonce, user_id, target_user_id, username, xtr, status,
                payment_id, created_at, buyer_telegram_id)
               VALUES ('username', 1, 1, 'sample', 30, 'paid',
                       'stx-username', 3, ?)""",
            (payer,),
        )
        conn.execute(
            """INSERT INTO shop_premium_orders
               (nonce, user_id, target_user_id, recipient_username, months,
                days, xtr, status, payment_id, created_at, buyer_telegram_id)
               VALUES ('premium', 1, 1, 'sample', 1, 30, 40, 'paid',
                       'stx-premium', 4, ?)""",
            (payer,),
        )
        conn.execute(
            """INSERT INTO shop_case_orders
               (nonce, user_id, case_id, count, xtr, airgram, currency, status,
                payment_id, created_at, buyer_telegram_id)
               VALUES ('xtr-case', 1, 'case', 1, 50, 0, 'xtr', 'paid',
                       'stx-case', 5, ?)""",
            (payer,),
        )
        conn.execute(
            """INSERT INTO frame_verification_orders
               (telegram_payment_id, buyer_telegram_id, stars_amount, status,
                refunded_at)
               VALUES ('stx-frame', ?, 60, 'completed', NULL)""",
            (payer,),
        )

        for nonce, telegram_id in (
            ("payer-airgram-case", payer),
            ("other-airgram-case", other_payer),
        ):
            conn.execute(
                """INSERT INTO shop_case_orders
                   (nonce, user_id, case_id, count, xtr, airgram, currency,
                    status, payment_id, created_at, buyer_telegram_id)
                   VALUES (?, 1, 'case', 1, 0, 130000, 'airgram', 'paid',
                           'airgram_balance:test', 6, ?)""",
                (nonce, telegram_id),
            )

        conn.execute(
            """INSERT INTO shop_star_orders
               (nonce, user_id, target_user_id, stars, xtr, status, payment_id,
                created_at, buyer_telegram_id)
               VALUES ('refunded', 1, 1, 700000, 70, 'refunded',
                       'stx-refunded', 7, ?)""",
            (payer,),
        )
        conn.execute(
            """INSERT INTO frame_verification_orders
               (telegram_payment_id, buyer_telegram_id, stars_amount, status,
                refunded_at)
               VALUES ('stx-refunded-frame', ?, 80, 'completed', 8)""",
            (payer,),
        )
        conn.commit()

        totals = service._collect_top_donor_totals_from_db(self.storage)
        self.assertEqual({payer: 210}, totals)

    def test_net_donations_subtract_refunds(self) -> None:
        txs = [
            {
                'amount': 100,
                'source': {
                    'type': 'user',
                    'user': {'id': 42},
                },
            },
            {
                'amount': 100,
                'receiver': {
                    'type': 'user',
                    'user': {'id': 42},
                },
            },
            {
                'amount': 50,
                'source': {
                    'type': 'user',
                    'user': {'id': 42},
                },
            },
            {
                'amount': 10,
                'source': {
                    'type': 'user',
                    'user': {'id': 99},
                },
            },
        ]
        totals = service._net_donations_from_star_transactions(txs)
        self.assertEqual({42: 50, 99: 10}, totals)

    def test_messenger_rank_includes_zero_donors(self) -> None:
        payer = 1001
        other = 2002
        zero = 3003
        conn = self.storage._conn
        for tid, phone in (
            (payer, '+79000000001'),
            (other, '+79000000002'),
            (zero, '+79000000003'),
        ):
            conn.execute(
                """INSERT INTO bot_phone_assignments
                   (telegram_user_id, phone, created_at)
                   VALUES (?, ?, 1)""",
                (tid, phone),
            )
        conn.commit()

        totals = {payer: 100, other: 50}
        ranked = service._rank_messenger_users(self.storage, totals)
        self.assertEqual(
            [(1, payer, 100), (2, other, 50), (3, zero, 0)],
            ranked,
        )

    def test_collect_uses_api_when_available(self) -> None:
        auth_txs = [
            {
                'amount': 30,
                'source': {'type': 'user', 'user': {'id': 11}},
            },
        ]
        frame_txs = [
            {
                'amount': 20,
                'source': {'type': 'user', 'user': {'id': 11}},
            },
            {
                'amount': 5,
                'source': {'type': 'user', 'user': {'id': 22}},
            },
        ]

        def fake_fetch(token: str):
            if token == 'auth-token':
                return auth_txs
            if token == 'frame-token':
                return frame_txs
            return []

        with mock.patch.object(service, '_shop_bot_token', return_value='auth-token'), \
             mock.patch.object(service, '_frame_bot_token', return_value='frame-token'), \
             mock.patch.object(service, '_fetch_bot_star_transactions', side_effect=fake_fetch):
            totals = service._collect_top_donor_totals(self.storage)

        self.assertEqual({11: 50, 22: 5}, totals)


if __name__ == "__main__":
    unittest.main()
