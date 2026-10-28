"""Crash game — KoalGram stars only."""
from __future__ import annotations

import math
import random
import time
from typing import Any

import config
from db.storage import Storage

WAITING_SECONDS = 10
PLANE_EMOJI_ID = 5247118020324655014
HISTORY_LIMIT = 12
# Constant growth rate: multiplier = e^(rate * t) until crash target.
# Slightly slower for smoother feel; same climb speed every round.
CRASH_GROWTH_RATE = 0.17
CRASH_MAX_MULT = 10.0
CASHOUT_CLIENT_GRACE = 0.55  # seconds after crash where client_mult still valid
CASHOUT_CLIENT_SLACK = 0.35  # max client_mult lead over server clock


def _open() -> Storage:
    from shop_webapp.service import _open as open_shop
    return open_shop()


def ensure_crash_schema(storage: Storage) -> None:
    storage._conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS crash_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            crash_mult REAL NOT NULL DEFAULT 1.0,
            waiting_started_at INTEGER NOT NULL,
            flying_started_at REAL,
            ended_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS crash_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            telegram_user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            cashout_mult REAL,
            payout INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            username TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_crash_bets_round
            ON crash_bets(round_id);
        CREATE INDEX IF NOT EXISTS idx_crash_bets_user_round
            ON crash_bets(user_id, round_id);
        '''
    )
    cols = {
        row['name']
        for row in storage._conn.execute('PRAGMA table_info(crash_bets)').fetchall()
    }
    if 'display_name' not in cols:
        storage._conn.execute(
            "ALTER TABLE crash_bets ADD COLUMN display_name TEXT NOT NULL DEFAULT ''",
        )
    if 'username' not in cols:
        storage._conn.execute(
            "ALTER TABLE crash_bets ADD COLUMN username TEXT NOT NULL DEFAULT ''",
        )
    storage._conn.commit()


def _sample_crash_multiplier() -> float:
    """Draw a crash point: low values common, max x10 and extremely rare."""
    r = random.random()
    if r < 0.52:
        return round(random.uniform(1.01, 1.50), 2)
    if r < 0.80:
        return round(random.uniform(1.51, 2.20), 2)
    if r < 0.92:
        return round(random.uniform(2.21, 3.50), 2)
    if r < 0.975:
        return round(random.uniform(3.51, 5.50), 2)
    if r < 0.994:
        return round(random.uniform(5.51, 8.00), 2)
    # ~0.6% chance near max
    return round(random.uniform(8.01, 10.00), 2)


def _generate_crash_multiplier(storage: Storage | None = None) -> float:
    """Random crash with anti-repeat against recent history."""
    recent: list[float] = []
    if storage is not None:
        rows = storage._conn.execute(
            '''SELECT crash_mult FROM crash_rounds
               WHERE status='ended' AND ended_at IS NOT NULL
               ORDER BY id DESC LIMIT 10''',
        ).fetchall()
        recent = [round(float(r['crash_mult']), 2) for r in rows]

    candidate = 1.0
    for _ in range(16):
        candidate = _sample_crash_multiplier()
        # Avoid near-duplicates of the last several rounds
        too_close = False
        for idx, prev in enumerate(recent[:6]):
            # Stricter for more recent rounds
            min_gap = 0.35 if idx < 2 else 0.22
            if abs(candidate - prev) < min_gap:
                too_close = True
                break
            # Also avoid same "bucket" repeating thrice in a row
        if len(recent) >= 2 and all(
            abs(candidate - recent[i]) < 0.5 for i in (0, 1)
        ):
            too_close = True
        if not too_close:
            return min(10.0, candidate)
    return min(10.0, candidate)


def _flight_duration(crash_mult: float) -> float:
    """Seconds until crash at constant growth rate."""
    mult = max(1.01, float(crash_mult))
    return math.log(mult) / CRASH_GROWTH_RATE


def _multiplier_at_elapsed(crash_mult: float, elapsed: float) -> float:
    """Smooth climb at fixed rate — not slowed by a high crash target."""
    mult = max(1.0, float(crash_mult))
    if elapsed <= 0:
        return 1.0
    current = math.exp(CRASH_GROWTH_RATE * elapsed)
    return min(mult, current)


def _current_multiplier(round_row: dict[str, Any], now: float) -> float:
    status = round_row.get('status')
    crash_mult = float(round_row.get('crash_mult') or 1.0)
    if status == 'waiting':
        return 1.0
    if status == 'ended':
        return crash_mult
    started = float(round_row.get('flying_started_at') or 0)
    if started <= 0:
        return 1.0
    elapsed = max(0.0, now - started)
    return round(_multiplier_at_elapsed(crash_mult, elapsed), 2)


def _waiting_remaining(round_row: dict[str, Any], now: float) -> int:
    started = int(round_row.get('waiting_started_at') or now)
    return max(0, WAITING_SECONDS - (now - started))


def _flight_seconds_left(round_row: dict[str, Any], now: float) -> int:
    if round_row.get('status') != 'flying':
        return 0
    started = float(round_row.get('flying_started_at') or 0)
    if started <= 0:
        return 0
    duration = _flight_duration(float(round_row.get('crash_mult') or 1.0))
    elapsed = max(0.0, now - started)
    return max(0, int(math.ceil(duration - elapsed)))


def _create_waiting_round(storage: Storage, now: int) -> dict[str, Any]:
    storage._conn.execute(
        '''INSERT INTO crash_rounds
           (status, crash_mult, waiting_started_at)
           VALUES ('waiting', 1.0, ?)''',
        (now,),
    )
    storage._conn.commit()
    row = storage._conn.execute(
        'SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1',
    ).fetchone()
    return dict(row)


def _get_active_round(storage: Storage) -> dict[str, Any] | None:
    row = storage._conn.execute(
        '''SELECT * FROM crash_rounds
           WHERE status IN ('waiting', 'flying')
           ORDER BY id DESC LIMIT 1''',
    ).fetchone()
    return dict(row) if row else None


def _crash_at_time(round_row: dict[str, Any]) -> float:
    started = float(round_row.get('flying_started_at') or 0)
    if started <= 0:
        return 0.0
    return started + _flight_duration(float(round_row.get('crash_mult') or 1.0))


def _settle_expired_active_bets(storage: Storage, now: float) -> None:
    """Mark remaining active bets lost only after cashout grace window."""
    rows = storage._conn.execute(
        "SELECT r.id, r.crash_mult, r.flying_started_at, r.ended_at "
        "FROM crash_rounds r "
        "WHERE r.status='ended' "
        "AND EXISTS ("
        "SELECT 1 FROM crash_bets b "
        "WHERE b.round_id=r.id AND b.status='active'"
        ") "
        "ORDER BY r.id DESC LIMIT 30"
    ).fetchall()
    for row in rows:
        r = dict(row)
        gate = _crash_at_time(r)
        if gate <= 0:
            gate = float(r.get('ended_at') or 0)
        if gate > 0 and now >= gate + CASHOUT_CLIENT_GRACE:
            _settle_lost_bets(storage, int(r['id']))


def _settle_lost_bets(storage: Storage, round_id: int) -> None:
    storage._conn.execute(
        '''UPDATE crash_bets SET status='lost'
           WHERE round_id=? AND status='active' ''',
        (int(round_id),),
    )
    storage._conn.commit()


def _active_bet_count(storage: Storage, round_id: int) -> int:
    row = storage._conn.execute(
        '''SELECT COUNT(*) AS c FROM crash_bets
           WHERE round_id=? AND status='active' ''',
        (int(round_id),),
    ).fetchone()
    return int(row['c'] if row else 0)


def _advance_round(storage: Storage) -> dict[str, Any]:
    ensure_crash_schema(storage)
    now = time.time()
    _settle_expired_active_bets(storage, now)
    round_row = _get_active_round(storage)
    if not round_row:
        return _create_waiting_round(storage, int(now))

    status = round_row['status']
    round_id = int(round_row['id'])

    if status == 'waiting':
        bet_count = _active_bet_count(storage, round_id)
        # Round must not start without at least one bet
        if bet_count <= 0:
            storage._conn.execute(
                '''UPDATE crash_rounds
                   SET waiting_started_at=?
                   WHERE id=? AND status='waiting' ''',
                (int(now), round_id),
            )
            storage._conn.commit()
            row = storage._conn.execute(
                'SELECT * FROM crash_rounds WHERE id=?', (round_id,),
            ).fetchone()
            return dict(row)

        remaining = _waiting_remaining(round_row, now)
        if remaining > 0:
            return round_row
        crash_mult = _generate_crash_multiplier(storage)
        storage._conn.execute(
            '''UPDATE crash_rounds
               SET status='flying', crash_mult=?, flying_started_at=?
               WHERE id=?''',
            (crash_mult, float(now), round_id),
        )
        storage._conn.commit()
        row = storage._conn.execute(
            'SELECT * FROM crash_rounds WHERE id=?', (round_id,),
        ).fetchone()
        return dict(row)

    if status == 'flying':
        current = _current_multiplier(round_row, now)
        crash_mult = float(round_row['crash_mult'])
        if current < crash_mult - 0.001:
            return round_row
        storage._conn.execute(
            '''UPDATE crash_rounds
               SET status='ended', ended_at=?
               WHERE id=?''',
            (int(now), round_id),
        )
        # Settle immediately; in-flight cashouts recover lost→cashed within grace.
        _settle_lost_bets(storage, round_id)
        return _create_waiting_round(storage, int(now))

    return _create_waiting_round(storage, int(now))


def _user_display(
    storage: Storage,
    user_id: int,
    *,
    telegram_user_id: int = 0,
) -> dict[str, Any]:
    user = storage.get_user_by_id(int(user_id)) or {}
    first = (user.get('first_name') or '').strip()
    last = (user.get('last_name') or '').strip()
    full = (first + ' ' + last).strip()
    username = (user.get('username') or '').strip()
    tg_id = int(telegram_user_id or 0)
    if tg_id <= 0:
        tg_row = storage._conn.execute(
            '''SELECT telegram_user_id FROM shop_webapp_sessions
               WHERE user_id=? ORDER BY created_at DESC LIMIT 1''',
            (int(user_id),),
        ).fetchone()
        tg_id = int(tg_row['telegram_user_id']) if tg_row else 0
    if username:
        name = f'@{username}'
    elif full:
        name = full
    else:
        name = f'Игрок {user_id}'
    return {
        'userId': int(user_id),
        'name': name,
        'username': username or None,
        'telegramUserId': tg_id,
    }


def _round_bets_public(storage: Storage, round_id: int) -> list[dict[str, Any]]:
    rows = storage._conn.execute(
        '''SELECT * FROM crash_bets
           WHERE round_id=?
           ORDER BY created_at ASC, id ASC''',
        (int(round_id),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        bet = dict(row)
        stored_name = (bet.get('display_name') or '').strip()
        stored_user = (bet.get('username') or '').strip()
        tg_id = int(bet.get('telegram_user_id') or 0)
        info = _user_display(storage, int(bet['user_id']), telegram_user_id=tg_id)
        if stored_user:
            name = f'@{stored_user}'
        elif stored_name:
            name = stored_name
        else:
            name = info['name']
        out.append({
            'betId': int(bet['id']),
            'userId': int(bet['user_id']),
            'name': name,
            'username': stored_user or info['username'],
            'telegramUserId': tg_id or info['telegramUserId'],
            'amount': int(bet['amount']),
            'status': bet['status'],
            'cashoutMult': float(bet['cashout_mult']) if bet['cashout_mult'] else None,
            'payout': int(bet['payout'] or 0),
        })
    return out


def _history_public(storage: Storage) -> list[dict[str, Any]]:
    rows = storage._conn.execute(
        '''SELECT id, crash_mult, ended_at
           FROM crash_rounds
           WHERE status='ended' AND ended_at IS NOT NULL
           ORDER BY id DESC
           LIMIT ?''',
        (HISTORY_LIMIT,),
    ).fetchall()
    return [
        {
            'roundId': int(r['id']),
            'multiplier': float(r['crash_mult']),
            'endedAt': int(r['ended_at'] or 0),
        }
        for r in reversed(rows)
    ]


def _user_bet_in_round(
    storage: Storage,
    round_id: int,
    user_id: int,
) -> dict[str, Any] | None:
    row = storage._conn.execute(
        '''SELECT * FROM crash_bets
           WHERE round_id=? AND user_id=?
           ORDER BY id DESC LIMIT 1''',
        (int(round_id), int(user_id)),
    ).fetchone()
    return dict(row) if row else None


def get_plane_lottie() -> dict[str, Any]:
    storage = _open()
    ensure_crash_schema(storage)
    try:
        from shop_webapp.emoji_import import ensure_custom_emoji_document
        doc = ensure_custom_emoji_document(storage, PLANE_EMOJI_ID)
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}
    if not doc:
        return {'ok': False, 'error': 'Не удалось загрузить самолёт'}
    sticker = storage.get_sticker_document_by_id(PLANE_EMOJI_ID) or {}
    path = str(sticker.get('file_path') or '')
    if not path.lower().endswith('.tgs'):
        return {'ok': False, 'error': 'Анимация самолёта недоступна'}
    return {'ok': True, 'path': path, 'documentId': PLANE_EMOJI_ID}


def get_state(user_id: int = 0, telegram_user_id: int = 0) -> dict[str, Any]:
    storage = _open()
    round_row = _advance_round(storage)
    now = time.time()
    status = round_row['status']
    round_id = int(round_row['id'])
    crash_mult_val = float(round_row['crash_mult'])
    current_mult = _current_multiplier(round_row, now)
    waiting_left = _waiting_remaining(round_row, now) if status == 'waiting' else 0
    flight_duration = _flight_duration(crash_mult_val) if status == 'flying' else 0

    from shop_webapp.service import _profile
    user_payload = None
    balance = None
    my_bet = None
    if user_id > 0:
        user = storage.get_user_by_id(int(user_id))
        if user:
            user_payload = _profile(storage, user)
            balance = user_payload['stars']
            bet = _user_bet_in_round(storage, round_id, user_id)
            if bet:
                my_bet = {
                    'betId': int(bet['id']),
                    'amount': int(bet['amount']),
                    'status': bet['status'],
                    'cashoutMult': float(bet['cashout_mult']) if bet['cashout_mult'] else None,
                    'payout': int(bet['payout'] or 0),
                }

    has_active_bet = bool(my_bet and my_bet['status'] == 'active')
    can_bet = bool(
        user_id > 0
        and status == 'waiting'
        and not has_active_bet,
    )
    can_cashout = bool(
        user_id > 0
        and status == 'flying'
        and has_active_bet,
    )
    bet_count = _active_bet_count(storage, round_id)
    waiting_for_bets = bool(status == 'waiting' and bet_count <= 0)
    if waiting_for_bets:
        waiting_left = WAITING_SECONDS

    return {
        'ok': True,
        'round': {
            'id': round_id,
            'status': status,
            'crashMultiplier': float(round_row['crash_mult']),
            'currentMultiplier': current_mult,
            'waitingSecondsLeft': waiting_left,
            'waitingTotal': WAITING_SECONDS,
            'flyingStartedAt': float(round_row.get('flying_started_at') or 0),
            'flightSecondsLeft': _flight_seconds_left(round_row, now),
            'flightDuration': round(flight_duration, 2),
            'growthRate': CRASH_GROWTH_RATE,
            'betCount': bet_count,
            'waitingForBets': waiting_for_bets,
        },
        'history': _history_public(storage),
        'bets': _round_bets_public(storage, round_id),
        'myBet': my_bet,
        'canBet': can_bet,
        'canCashout': can_cashout,
        'balance': balance,
        'user': user_payload,
        'planeEmojiId': str(PLANE_EMOJI_ID),
        'serverTime': now,
    }


def place_bet(
    user_id: int,
    amount: int,
    telegram_user_id: int = 0,
) -> dict[str, Any]:
    storage = _open()
    round_row = _advance_round(storage)
    if round_row['status'] != 'waiting':
        return {'ok': False, 'error': 'Ставки принимаются только до начала раунда'}
    try:
        stars = int(amount)
    except (TypeError, ValueError):
        return {'ok': False, 'error': 'Неверная сумма'}
    if stars <= 0:
        return {'ok': False, 'error': 'Укажите сумму ставки'}
    if stars > 10_000:
        return {'ok': False, 'error': 'Максимальная ставка — 10 000 Stars'}

    round_id = int(round_row['id'])
    existing = _user_bet_in_round(storage, round_id, user_id)
    if existing and existing['status'] == 'active':
        return {'ok': False, 'error': 'Вы уже сделали ставку в этом раунде'}

    balance = storage.get_stars_balance(user_id)
    if not config.INFINITE_STARS and balance < stars:
        return {'ok': False, 'error': 'Недостаточно звёзд на балансе'}

    tg_id = int(telegram_user_id or 0)
    if tg_id <= 0:
        row = storage._conn.execute(
            '''SELECT telegram_user_id FROM shop_webapp_sessions
               WHERE user_id=? ORDER BY created_at DESC LIMIT 1''',
            (int(user_id),),
        ).fetchone()
        tg_id = int(row['telegram_user_id']) if row else 0

    info = _user_display(storage, user_id, telegram_user_id=tg_id)
    display_name = info['name']
    username = (info.get('username') or '')[:64]

    new_balance = storage.adjust_stars_balance(
        user_id,
        -stars,
        title='Ставка Crash',
        peer_user_id=config.SERVICE_USER_ID,
        _skip_rating=True,
    )
    now = int(time.time())
    first_bet = _active_bet_count(storage, round_id) <= 0
    storage._conn.execute(
        '''INSERT INTO crash_bets
           (round_id, user_id, telegram_user_id, amount, status, created_at,
            display_name, username)
           VALUES (?,?,?,?, 'active', ?, ?, ?)''',
        (round_id, user_id, tg_id, stars, now, display_name, username),
    )
    # Countdown starts from the first bet of the round
    if first_bet:
        storage._conn.execute(
            '''UPDATE crash_rounds
               SET waiting_started_at=?
               WHERE id=? AND status='waiting' ''',
            (now, round_id),
        )
    storage._conn.commit()

    state = get_state(user_id, telegram_user_id=tg_id)
    state['ok'] = True
    state['placed'] = True
    state['balance'] = new_balance
    return state


def cashout(
    user_id: int,
    telegram_user_id: int = 0,
    client_mult: float | None = None,
) -> dict[str, Any]:
    """Cash out an active bet as fast as possible.

    Does not finalize flying→ended before claiming (that race loses cashouts).
    Honors a short client_mult grace for network latency, then claims the bet
    atomically and credits stars. Returns a lightweight payload (no get_state).
    """
    storage = _open()
    ensure_crash_schema(storage)
    now = time.time()
    _settle_expired_active_bets(storage, now)

    cm = None
    if client_mult is not None:
        try:
            cm = float(client_mult)
        except (TypeError, ValueError):
            cm = None
    if cm is not None:
        cm = max(1.0, min(cm, CRASH_MAX_MULT + 0.5))

    # Prefer an active bet on a flying/ended round (ended = crash just happened).
    row = storage._conn.execute(
        "SELECT b.*, r.status AS round_status, r.crash_mult AS r_crash_mult, "
        "r.flying_started_at AS r_flying_started_at, r.ended_at AS r_ended_at "
        "FROM crash_bets b "
        "JOIN crash_rounds r ON r.id = b.round_id "
        "WHERE b.user_id=? AND b.status='active' "
        "AND r.status IN ('flying', 'ended') "
        "ORDER BY b.id DESC LIMIT 1",
        (int(user_id),),
    ).fetchone()

    recovered_lost = False
    if not row and cm is not None:
        # Rare race: poll settled the bet lost milliseconds ago — recover in grace.
        row = storage._conn.execute(
            "SELECT b.*, r.status AS round_status, r.crash_mult AS r_crash_mult, "
            "r.flying_started_at AS r_flying_started_at, r.ended_at AS r_ended_at "
            "FROM crash_bets b "
            "JOIN crash_rounds r ON r.id = b.round_id "
            "WHERE b.user_id=? AND b.status='lost' AND r.status='ended' "
            "ORDER BY b.id DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
        recovered_lost = row is not None

    if not row:
        # Already cashed (double-tap) — idempotent success.
        cashed = storage._conn.execute(
            "SELECT b.*, r.status AS round_status "
            "FROM crash_bets b JOIN crash_rounds r ON r.id = b.round_id "
            "WHERE b.user_id=? AND b.status='cashed' "
            "ORDER BY b.id DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
        if cashed:
            bal = int(storage.get_stars_balance(user_id))
            return {
                'ok': True,
                'cashedOut': True,
                'payout': int(cashed['payout'] or 0),
                'multiplier': float(cashed['cashout_mult'] or 1),
                'balance': bal,
                'roundId': int(cashed['round_id']),
                'light': True,
            }
        return {'ok': False, 'error': 'У вас нет активной ставки'}

    bet = dict(row)
    round_status = bet['round_status']
    crash_mult = float(bet['r_crash_mult'])
    flying_started = float(bet['r_flying_started_at'] or 0)
    crash_at = (
        flying_started + _flight_duration(crash_mult) if flying_started > 0 else 0.0
    )
    if flying_started > 0 and round_status == 'flying':
        server_mult = float(
            round(_multiplier_at_elapsed(crash_mult, max(0.0, now - flying_started)), 2)
        )
    else:
        server_mult = crash_mult

    crashed_already = round_status == 'ended' or (crash_at > 0 and now >= crash_at)
    # Grace only AFTER the crash moment — not for the entire flight.
    within_grace = bool(
        crashed_already and crash_at and now <= crash_at + CASHOUT_CLIENT_GRACE
    )

    if recovered_lost and not within_grace:
        return {'ok': False, 'error': 'Раунд уже завершён'}
    if crashed_already and not within_grace:
        return {'ok': False, 'error': 'Раунд уже завершён'}

    if cm is not None and not crashed_already and cm + 1e-9 < crash_mult:
        # Live flight: honor client tap, capped by server clock + small slack.
        ceiling = min(crash_mult - 1e-6, server_mult + CASHOUT_CLIENT_SLACK)
        mult = min(max(1.0, cm), ceiling)
    elif cm is not None and within_grace and cm + 1e-9 < crash_mult:
        # Just crashed: allow in-flight cashout request that left before settle.
        mult = min(max(1.0, cm), crash_mult - 1e-6)
    else:
        if crashed_already or server_mult + 1e-9 >= crash_mult:
            return {'ok': False, 'error': 'Раунд уже завершён'}
        mult = max(1.0, server_mult)

    amount = int(bet['amount'])
    payout = max(amount, int(math.floor(amount * mult + 1e-9)))
    bet_id = int(bet['id'])
    round_id = int(bet['round_id'])

    # Claim first — atomic — so concurrent crash settle cannot steal the bet.
    claim_sql_active = (
        "UPDATE crash_bets SET status='cashed', cashout_mult=?, payout=? "
        "WHERE id=? AND status='active'"
    )
    claim_sql_lost = (
        "UPDATE crash_bets SET status='cashed', cashout_mult=?, payout=? "
        "WHERE id=? AND status='lost'"
    )
    claimed = None
    for _attempt in range(4):
        try:
            if recovered_lost:
                claimed = storage._conn.execute(
                    claim_sql_lost, (mult, payout, bet_id),
                )
            else:
                claimed = storage._conn.execute(
                    claim_sql_active, (mult, payout, bet_id),
                )
            storage._conn.commit()
            break
        except Exception as exc:
            msg = str(exc).lower()
            if 'locked' in msg or 'busy' in msg:
                time.sleep(0.04 * (_attempt + 1))
                continue
            raise
    if claimed is None:
        return {'ok': False, 'error': 'Сервер занят, нажмите ещё раз'}
    if claimed.rowcount <= 0:
        fresh = storage._conn.execute(
            'SELECT * FROM crash_bets WHERE id=?', (bet_id,)
        ).fetchone()
        if fresh and fresh['status'] == 'cashed':
            bal = int(storage.get_stars_balance(user_id))
            return {
                'ok': True,
                'cashedOut': True,
                'payout': int(fresh['payout'] or 0),
                'multiplier': float(fresh['cashout_mult'] or mult),
                'balance': bal,
                'roundId': round_id,
                'light': True,
            }
        return {'ok': False, 'error': 'У вас нет активной ставки'}

    new_balance = storage.adjust_stars_balance(
        user_id,
        payout,
        title=f'Crash ×{mult:.2f}',
        peer_user_id=config.SERVICE_USER_ID,
        _skip_rating=True,
    )

    return {
        'ok': True,
        'cashedOut': True,
        'payout': payout,
        'multiplier': mult,
        'balance': int(new_balance),
        'roundId': round_id,
        'light': True,
    }
