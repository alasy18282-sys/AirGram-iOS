"""Polls — corefork.telegram.org/api/poll (layer 227)."""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import List, Optional

import config
from db.storage import Storage
from handlers import packers
from mtproto import tl

log = logging.getLogger(__name__)

# Layer 223/core constructors
POLL = 0x58747131
POLL_ANSWER = 0xFF16E2CA
POLL_RESULTS = 0x7ADF2420
POLL_ANSWER_VOTERS = 0x3B6DDAD2
MESSAGE_MEDIA_POLL = 0x4BD6E798
INPUT_MEDIA_POLL = 0xF94E5F1
UPDATE_MESSAGE_POLL = 0xACA1657B

# Layer 224+ constructors (Telegram Desktop)
POLL_ALT = 0xB8425BE9
POLL_ANSWER_ALT = 0x4B7D786A
POLL_RESULTS_ALT = 0xBA7BB15E
POLL_ANSWER_VOTERS_ALT = 0x3645230A
MESSAGE_MEDIA_POLL_ALT = 0x773F4E66
INPUT_MEDIA_POLL_ALT = 0x883A4108
UPDATE_MESSAGE_POLL_ALT = 0xD64C522B

# Layer 227 poll (subscribers_only, countries_iso2)
POLL_L227 = 0x966E2DBF

TEXT_WITH_ENTITIES = 0x751F3146
MESSAGES_SEND_VOTE = 0x10EA6184
MESSAGES_GET_POLL_RESULTS = 0x73BB643B
MESSAGES_GET_POLL_RESULTS_ALT = 0xEDA3E33B
MESSAGES_EDIT_MESSAGE = 0x51E842E1
MESSAGES_EDIT_MESSAGE_LEGACY = 0x48F71778
MESSAGES_GET_POLL_VOTES = 0xB86E380E

MESSAGE_PEER_VOTE = 0xB6CC2D5C
MESSAGE_PEER_VOTE_MULTIPLE = 0x4628F6E6
MESSAGES_VOTES_LIST = 0x4899484E

_ALL_POLL_CTORS = frozenset({POLL, POLL_ALT, POLL_L227})


def _rpc_error(code: int, message: str) -> bytes:
    w = tl.Writer()
    w.u32(tl.RPC_ERROR)
    w.i32(code)
    w.string(message)
    return w.bytes_out()


def _poll_schema(cid: int) -> str:
    if cid == POLL_L227:
        return 'l227'
    if cid == POLL_ALT:
        return 'alt'
    return 'core'


def _effective_schema(poll_data: dict) -> str:
    schema = (
        poll_data.get('schema')
        or poll_data.get('poll', {}).get('schema')
        or 'core'
    )
    if schema == 'core' and config.LAYER >= 227:
        return 'l227'
    return schema


def _modern_schema(schema: str) -> bool:
    return schema in ('alt', 'l227')


def _pack_text_with_entities(text: str,
                             entities: list[dict] | None = None) -> bytes:
    w = tl.Writer()
    w.u32(TEXT_WITH_ENTITIES)
    w.string(text or '')
    w.raw(packers.pack_entities(entities or [], text or ''))
    return w.bytes_out()


def _parse_text_with_entities(r: tl.Reader) -> dict:
    """Parse TextWithEntities, tolerating legacy plain string payloads."""
    cid = r.u32()
    if cid == TEXT_WITH_ENTITIES:
        text = r.string()
        entities = packers.parse_entities(r)
        return {'text': text, 'entities': entities}
    # Legacy clients may encode plain string instead of TextWithEntities.
    r.pos -= 4
    return {'text': r.string(), 'entities': []}


def _skip_input_media(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (INPUT_MEDIA_POLL, INPUT_MEDIA_POLL_ALT):
        parse_input_media_poll_body(r, cid)
        return
    if cid in (0x37c9330, 0x7d8375da, 0x1e287d04):
        r.i32()
        parse_input_file_skip(r)
        return
    if cid in (0xa8763ab5, 0xe3af4434, 0xb3ba0635):
        r.i32()
        if cid == 0xa8763ab5:
            packers.parse_input_document(r)
        else:
            packers.skip_input_photo(r)
        return
    log.warning('skip_input_media: unknown ctor %#010x', cid)


def parse_input_file_skip(r: tl.Reader) -> None:
    from handlers.media import parse_input_file
    parse_input_file(r)


def parse_poll_answer(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid == POLL_ANSWER_ALT:
        flags = r.i32()
    else:
        if cid != POLL_ANSWER:
            raise ValueError(f'unsupported PollAnswer {cid:#010x}')
        flags = 0
    text_data = _parse_text_with_entities(r)
    option = r.tl_bytes().hex()
    if flags & 1:
        _skip_message_media(r)
    if flags & 2:
        packers.skip_input_peer(r)
        r.i32()
    return {
        'text': text_data['text'],
        'entities': text_data.get('entities') or [],
        'option': option,
    }


def _skip_message_media(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == MESSAGE_MEDIA_POLL:
        _skip_poll(r)
        _skip_poll_results(r)
    elif cid == MESSAGE_MEDIA_POLL_ALT:
        flags = r.i32()
        _skip_poll(r)
        _skip_poll_results(r)
        if flags & 1:
            _skip_message_media(r)


def _parse_poll_flags(flags: int) -> dict:
    return {
        'closed': bool(flags & 1),
        'public_voters': bool(flags & 2),
        'multiple_choice': bool(flags & 4),
        'quiz': bool(flags & 8),
        'open_answers': bool(flags & 64),
        'revoting_disabled': bool(flags & 128),
        'shuffle_answers': bool(flags & 256),
        'hide_results_until_close': bool(flags & 512),
        'creator': bool(flags & 1024),
        'subscribers_only': bool(flags & 2048),
    }


def _parse_poll_tail(r: tl.Reader, flags: int,
                     *, read_countries: bool) -> tuple:
    close_period = r.i32() if flags & 16 else 0
    close_date = r.i32() if flags & 32 else 0
    countries_iso2: list[str] = []
    if read_countries and flags & (1 << 12):
        assert r.u32() == tl.VECTOR
        cnt = r.i32()
        countries_iso2 = [r.string() for _ in range(cnt)]
    return close_period, close_date, countries_iso2


def _skip_poll(r: tl.Reader) -> None:
    cid = r.u32()
    if cid not in _ALL_POLL_CTORS:
        raise ValueError(f'unsupported Poll {cid:#010x}')
    r.i64()
    flags = r.i32()
    _parse_text_with_entities(r)
    if r.u32() != tl.VECTOR:
        raise ValueError('POLL_ANSWERS_VECTOR_INVALID')
    n = r.i32()
    for _ in range(n):
        parse_poll_answer(r)
    read_countries = cid in (POLL, POLL_L227)
    _parse_poll_tail(r, flags, read_countries=read_countries)


def _skip_poll_results(r: tl.Reader) -> None:
    cid = r.u32()
    assert cid in (POLL_RESULTS, POLL_RESULTS_ALT)
    flags = r.i32()
    if flags & (1 << 1):
        assert r.u32() == tl.VECTOR
        n = r.i32()
        for _ in range(n):
            av_cid = r.u32()
            assert av_cid in (POLL_ANSWER_VOTERS, POLL_ANSWER_VOTERS_ALT)
            av_flags = r.i32()
            r.tl_bytes()
            if av_cid == POLL_ANSWER_VOTERS_ALT:
                if av_flags & 4:
                    r.i32()
                    assert r.u32() == tl.VECTOR
                    for _ in range(r.i32()):
                        packers.skip_input_peer(r)
            else:
                r.i32()
    if flags & (1 << 2):
        r.i32()
    if flags & (1 << 3):
        assert r.u32() == tl.VECTOR
        cnt = r.i32()
        for _ in range(cnt):
            packers.skip_input_peer(r)
    if flags & (1 << 4):
        r.string()
        packers.parse_entities(r)
    if flags & (1 << 5):
        _skip_message_media(r)


def parse_poll(r: tl.Reader) -> dict:
    cid = r.u32()
    if cid not in _ALL_POLL_CTORS:
        raise ValueError(f'unsupported Poll {cid:#010x}')
    schema = _poll_schema(cid)
    poll_id = r.i64()
    flags = r.i32()
    flag_data = _parse_poll_flags(flags)
    question_data = _parse_text_with_entities(r)
    if r.u32() != tl.VECTOR:
        raise ValueError('POLL_ANSWERS_VECTOR_INVALID')
    n = r.i32()
    answers = [parse_poll_answer(r) for _ in range(n)]
    close_period, close_date, countries_iso2 = _parse_poll_tail(
        r, flags, read_countries=cid in (POLL, POLL_L227))
    poll_hash = int(r.i64()) if cid == POLL_L227 else 0
    return {
        'poll_id': poll_id,
        'schema': schema,
        **flag_data,
        'question': question_data['text'],
        'question_entities': question_data.get('entities') or [],
        'answers': answers,
        'close_period': close_period,
        'close_date': close_date,
        'countries_iso2': countries_iso2,
        'hash': poll_hash,
    }


def parse_input_media_poll_body(r: tl.Reader,
                                ctor: int = INPUT_MEDIA_POLL) -> dict:
    flags = r.i32()
    poll = parse_poll(r)
    correct_answers: list[str] = []
    solution = ''
    solution_entities: list[dict] = []
    if flags & 1:
        if r.u32() != tl.VECTOR:
            raise ValueError('POLL_CORRECT_ANSWERS_VECTOR_INVALID')
        n = r.i32()
        prefer_indices = ctor == INPUT_MEDIA_POLL_ALT
        correct_answers = _parse_correct_answers(r, poll, n, prefer_indices)
    if flags & 8:
        _skip_input_media(r)
    if flags & 2:
        solution = r.string()
        solution_entities = packers.parse_entities(r)
    if flags & 4:
        _skip_input_media(r)
    return {
        'type': 'poll',
        'poll': poll,
        'correct_answers': correct_answers,
        'solution': solution,
        'solution_entities': solution_entities,
    }


def _parse_correct_answers(r: tl.Reader, poll: dict, count: int,
                           prefer_indices: bool) -> list[str]:
    if count <= 0:
        return []
    start = r.pos
    readers = (
        (_read_correct_answer_indices, _read_correct_answer_bytes)
        if prefer_indices else
        (_read_correct_answer_bytes, _read_correct_answer_indices)
    )
    last_error: Exception | None = None
    for reader in readers:
        r.pos = start
        try:
            return reader(r, poll, count)
        except (ValueError, AssertionError, IndexError) as exc:
            last_error = exc
    r.pos = start
    if last_error:
        raise last_error
    return []


def _read_correct_answer_indices(r: tl.Reader, poll: dict,
                                 count: int) -> list[str]:
    answers = poll.get('answers') or []
    indices = [r.i32() for _ in range(count)]
    if any(idx < 0 or idx >= len(answers) for idx in indices):
        raise ValueError('correct_answers indices invalid')
    return [answers[idx]['option'] for idx in indices]


def _read_correct_answer_bytes(r: tl.Reader, poll: dict,
                               count: int) -> list[str]:
    valid = {
        str(ans.get('option') or '').lower(): str(ans.get('option') or '')
        for ans in (poll.get('answers') or [])
    }
    values = [r.tl_bytes().hex() for _ in range(count)]
    if valid and any(v.lower() not in valid for v in values):
        raise ValueError('correct_answers bytes invalid')
    return [valid.get(v.lower(), v) for v in values]


def build_poll_json(media: dict) -> str:
    poll = dict(media['poll'])
    if not int(poll.get('poll_id') or 0):
        poll['poll_id'] = secrets.randbits(63)
    if not poll.get('schema') or poll.get('schema') == 'core':
        poll['schema'] = 'l227' if config.LAYER >= 227 else poll.get('schema', 'core')
    poll['creator'] = True
    if poll.get('quiz') and poll.get('close_period') and not poll.get('close_date'):
        poll['close_date'] = int(time.time()) + int(poll['close_period'])
    payload = {
        'poll_id': int(poll.get('poll_id') or 0),
        'poll': poll,
        'correct_answers': media.get('correct_answers') or [],
        'solution': media.get('solution') or '',
        'solution_entities': media.get('solution_entities') or [],
        'schema': poll.get('schema'),
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_poll_json(raw) -> Optional[dict]:
    if not raw:
        return None
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
    if not isinstance(data, dict) or data.get('kind') == 'dice':
        return None
    return data


def _pack_poll_answer(ans: dict, schema: str = 'core') -> bytes:
    w = tl.Writer()
    if _modern_schema(schema):
        w.u32(POLL_ANSWER_ALT)
        w.i32(0)
    else:
        w.u32(POLL_ANSWER)
    w.raw(_pack_text_with_entities(
        ans.get('text') or '', ans.get('entities') or []))
    w.tl_bytes(bytes.fromhex(ans['option']))
    return w.bytes_out()


def pack_poll(poll: dict) -> bytes:
    flags = 0
    if poll.get('closed'):
        flags |= 1
    if poll.get('public_voters'):
        flags |= 2
    if poll.get('multiple_choice'):
        flags |= 4
    if poll.get('quiz'):
        flags |= 8
    if poll.get('open_answers'):
        flags |= 64
    if poll.get('revoting_disabled'):
        flags |= 128
    if poll.get('shuffle_answers'):
        flags |= 256
    if poll.get('hide_results_until_close'):
        flags |= 512
    if poll.get('creator'):
        flags |= 1024
    if poll.get('subscribers_only'):
        flags |= 2048
    close_period = int(poll.get('close_period') or 0)
    close_date = int(poll.get('close_date') or 0)
    if close_period:
        flags |= 16
    if close_date:
        flags |= 32
    countries = poll.get('countries_iso2') or []
    if countries:
        flags |= 1 << 12
    schema = poll.get('schema') or ('l227' if config.LAYER >= 227 else 'core')
    w = tl.Writer()
    if schema == 'l227':
        w.u32(POLL_L227)
    elif schema == 'alt':
        w.u32(POLL_ALT)
    else:
        w.u32(POLL)
    w.i64(int(poll.get('poll_id') or 0))
    w.i32(flags)
    w.raw(_pack_text_with_entities(
        poll.get('question') or '', poll.get('question_entities') or []))
    answers = poll.get('answers') or []
    w.u32(tl.VECTOR)
    w.i32(len(answers))
    for ans in answers:
        w.raw(_pack_poll_answer(ans, schema))
    if flags & 16:
        w.i32(close_period)
    if flags & 32:
        w.i32(close_date)
    if flags & (1 << 12):
        w.u32(tl.VECTOR)
        w.i32(len(countries))
        for iso in countries:
            w.string(iso)
    if schema == 'l227':
        w.i64(int(poll.get('hash') or 0))
    return w.bytes_out()


def pack_poll_results(storage: Storage, peer_id: int, msg_id: int,
                      poll_data: dict, viewer_id: Optional[int] = None) -> bytes:
    poll = poll_data['poll']
    votes = storage.list_poll_votes(peer_id, msg_id)
    user_vote = None
    if viewer_id is not None:
        user_vote = storage.get_user_poll_vote(peer_id, msg_id, viewer_id)

    counts: dict[str, int] = {}
    for v in votes:
        for opt in v['options']:
            key = opt.lower()
            counts[key] = counts.get(key, 0) + 1
    total_voters = len(votes)
    correct_set = {
        o.lower() for o in (poll_data.get('correct_answers') or [])
    }
    solution = poll_data.get('solution') or ''
    solution_entities = poll_data.get('solution_entities') or []

    schema = _effective_schema(poll_data)
    modern = _modern_schema(schema)

    flags = 1 << 1  # always include per-option results
    if total_voters > 0:
        flags |= 1 << 2
    if poll.get('public_voters') and total_voters > 0:
        flags |= 1 << 3
    if solution:
        flags |= 1 << 4

    w = tl.Writer()
    w.u32(POLL_RESULTS_ALT if modern else POLL_RESULTS)
    w.i32(flags)
    if flags & (1 << 1):
        w.u32(tl.VECTOR)
        w.i32(len(poll.get('answers') or []))
        for ans in poll.get('answers') or []:
            opt = ans['option']
            opt_key = opt.lower()
            opt_bytes = bytes.fromhex(opt)
            af = 0
            if user_vote and opt_key in {o.lower() for o in user_vote}:
                af |= 1
            if poll.get('quiz') and opt_key in correct_set:
                af |= 2
            if modern:
                af |= 4
            vw = tl.Writer()
            vw.u32(
                POLL_ANSWER_VOTERS_ALT if modern else POLL_ANSWER_VOTERS)
            vw.i32(af)
            vw.tl_bytes(opt_bytes)
            vw.i32(int(counts.get(opt_key, 0)))
            if modern:
                vw.u32(tl.VECTOR)
                vw.i32(0)
            w.raw(vw.bytes_out())
    if flags & (1 << 2):
        w.i32(total_voters)
    if flags & (1 << 3):
        w.u32(tl.VECTOR)
        w.i32(0)
    if flags & (1 << 4):
        w.string(solution)
        w.raw(packers.pack_entities(solution_entities, solution))
    return w.bytes_out()


def pack_message_media_poll(storage: Storage, poll_data: dict,
                            peer_id: int, msg_id: int,
                            viewer_id: Optional[int] = None) -> bytes:
    schema = _effective_schema(poll_data)
    w = tl.Writer()
    if _modern_schema(schema):
        w.u32(MESSAGE_MEDIA_POLL_ALT)
        w.i32(0)
    else:
        w.u32(MESSAGE_MEDIA_POLL)
    w.raw(pack_poll(poll_data['poll']))
    w.raw(pack_poll_results(
        storage, peer_id, msg_id, poll_data, viewer_id=viewer_id))
    return w.bytes_out()


def pack_update_message_poll(poll_data: dict, storage: Storage,
                             peer_id: int, msg_id: int,
                             viewer_id: Optional[int] = None,
                             include_poll: bool = False) -> bytes:
    schema = _effective_schema(poll_data)
    poll_id = int(
        poll_data.get('poll_id')
        or poll_data['poll'].get('poll_id')
        or 0)
    w = tl.Writer()
    if _modern_schema(schema):
        upd_flags = 1 << 1
        if include_poll:
            upd_flags |= 1
        w.u32(UPDATE_MESSAGE_POLL_ALT)
        w.i32(upd_flags)
        w.raw(packers.pack_peer(peer_id))
        w.i32(int(msg_id))
        w.i64(poll_id)
        if include_poll:
            w.raw(pack_poll(poll_data['poll']))
    else:
        upd_flags = 1 if include_poll else 0
        w.u32(UPDATE_MESSAGE_POLL)
        w.i32(upd_flags)
        w.i64(poll_id)
        if include_poll:
            w.raw(pack_poll(poll_data['poll']))
    w.raw(pack_poll_results(
        storage, peer_id, msg_id, poll_data, viewer_id=viewer_id))
    return w.bytes_out()


def _collect_users(storage: Storage, user_ids: List[int]) -> List[dict]:
    users = []
    seen = set()
    for uid in user_ids:
        if uid in seen:
            continue
        u = storage.get_user_by_id(uid)
        if u:
            users.append(u)
            seen.add(uid)
    return users


def _find_poll_message(storage: Storage, viewer_id: int, peer_id: int,
                       msg_id: int):
    if peer_id >= config.CHANNEL_ID_MIN:
        storage.ensure_channel_messages_for_member(peer_id, viewer_id)
    msg = storage.get_message(viewer_id, msg_id)
    if not msg or int(msg.get('peer_id') or 0) != int(peer_id):
        return None
    return msg


def _push_poll_update(storage, push_queue, session, peer_id, msg_id,
                      poll_data, viewer_ids: List[int]):
    if push_queue is None:
        return
    for uid in viewer_ids:
        if uid <= 0:
            continue
        member_upd = pack_update_message_poll(
            poll_data, storage, peer_id, msg_id, viewer_id=uid)
        push_queue.push_to_user(
            uid, packers.pack_updates(
                [member_upd], _collect_users(storage, [uid]),
                user_id=uid, storage=storage, increment_seq=False),
            exclude_auth_key_id=session.auth_key_id)


def handle_messages_send_vote(session, req_msg_id, r, storage, push_queue,
                              registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    if storage.is_user_frozen(viewer['id']):
        return _rpc_error(420, 'FROZEN_METHOD_INVALID')

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return _rpc_error(400, 'PEER_ID_INVALID')
    msg_id = r.i32()
    assert r.u32() == tl.VECTOR
    n = r.i32()
    options = [r.tl_bytes().hex() for _ in range(n)]

    if peer_id >= config.CHANNEL_ID_MIN:
        if not storage.is_channel_member(peer_id, viewer['id']):
            return _rpc_error(400, 'CHANNEL_PRIVATE')
    elif peer_id != viewer['id']:
        if not storage.get_user_by_id(peer_id):
            return _rpc_error(400, 'PEER_ID_INVALID')

    msg = _find_poll_message(storage, viewer['id'], peer_id, msg_id)
    if not msg:
        return _rpc_error(400, 'MSG_ID_INVALID')

    poll_data = parse_poll_json(msg.get('media_poll_json'))
    if not poll_data:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    try:
        storage.cast_poll_vote(
            peer_id, msg_id, viewer['id'], options, poll_data['poll'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))

    upd = pack_update_message_poll(
        poll_data, storage, peer_id, msg_id, viewer_id=viewer['id'])
    users = _collect_users(storage, [viewer['id']])

    if peer_id >= config.CHANNEL_ID_MIN and push_queue is not None:
        member_ids = storage.list_channel_member_ids(peer_id)
        _push_poll_update(
            storage, push_queue, session, peer_id, msg_id, poll_data,
            [uid for uid in member_ids if uid != viewer['id']])

    return packers.pack_updates(
        [upd], users, user_id=viewer['id'], storage=storage,
        increment_seq=False)


def handle_messages_get_poll_results(session, req_msg_id, r, storage,
                                     push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return _rpc_error(400, 'PEER_ID_INVALID')
    msg_id = r.i32()
    if r.remaining() >= 8:
        r.i64()

    msg = _find_poll_message(storage, viewer['id'], peer_id, msg_id)
    if not msg:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    poll_data = parse_poll_json(msg.get('media_poll_json'))
    if not poll_data:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    return packers.pack_updates(
        [pack_update_message_poll(
            poll_data, storage, peer_id, msg_id,
            viewer_id=viewer['id'], include_poll=True)],
        [], user_id=viewer['id'], storage=storage,
        increment_seq=False)


def _skip_edit_message_tail(r: tl.Reader, flags: int) -> None:
    if flags & (1 << 15):
        r.i32()
    if flags & (1 << 18):
        r.i32()
    if flags & (1 << 17):
        r.i32()
    if flags & (1 << 23):  # layer 228: rich_message:InputRichMessage
        try:
            packers._skip_input_rich_message(r)
        except (ValueError, AssertionError):
            pass


def handle_messages_edit_message(session, req_msg_id, r, storage, push_queue,
                                 registry):
    from handlers.rpc import require_auth
    from handlers import media as media_handlers

    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err
    if storage.is_user_frozen(viewer['id']):
        return _rpc_error(420, 'FROZEN_METHOD_INVALID')

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return _rpc_error(400, 'PEER_ID_INVALID')
    msg_id = r.i32()

    new_text = None
    if flags & (1 << 11):
        new_text = r.string()

    poll_media = None
    if flags & (1 << 14):
        try:
            poll_media = media_handlers.parse_input_media_uploaded(
                r, storage, viewer['id'])
        except (ValueError, AssertionError):
            return _rpc_error(400, 'MEDIA_INVALID')

    if flags & (1 << 2):
        packers.skip_reply_markup(r)
    if flags & (1 << 3):
        packers.parse_entities(r)
    _skip_edit_message_tail(r, flags)

    msg = _find_poll_message(storage, viewer['id'], peer_id, msg_id)
    if not msg:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    poll_data = parse_poll_json(msg.get('media_poll_json'))
    if not poll_data and not (poll_media and poll_media.get('type') == 'poll'):
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    if poll_media and poll_media.get('type') == 'poll':
        new_json = build_poll_json(poll_media)
        poll_data = json.loads(new_json)
        old = parse_poll_json(msg.get('media_poll_json'))
        if old:
            poll_data['poll']['poll_id'] = (
                old['poll'].get('poll_id') or poll_data['poll'].get('poll_id'))
            if old.get('correct_answers'):
                poll_data['correct_answers'] = old['correct_answers']
            if old.get('solution'):
                poll_data['solution'] = old['solution']
                poll_data['solution_entities'] = old.get('solution_entities') or []
    elif poll_data and new_text is None:
        return _rpc_error(400, 'MESSAGE_NOT_MODIFIED')

    poll_json = json.dumps(poll_data, ensure_ascii=False)
    if peer_id >= config.CHANNEL_ID_MIN:
        storage.update_channel_poll_message(
            peer_id, msg_id, media_poll_json=poll_json, text=new_text)
        updated = storage.get_message(viewer['id'], msg_id)
    else:
        updated = storage.update_message_poll(
            viewer['id'], peer_id, msg_id,
            media_poll_json=poll_json, text=new_text)
        if updated and peer_id != viewer['id']:
            other_id = int(peer_id)
            storage.update_message_poll(
                other_id, peer_id, msg_id,
                media_poll_json=poll_json, text=new_text)
        elif updated and peer_id == viewer['id']:
            pass
    if not updated:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    pts = storage.increment_pts(viewer['id'])
    users = _collect_users(storage, [viewer['id'], int(msg.get('from_id') or 0)])
    edit_upd = packers.pack_update_edit_message(updated, pts, storage)

    notify_ids = [viewer['id']]
    if peer_id >= config.CHANNEL_ID_MIN:
        notify_ids = storage.list_channel_member_ids(peer_id)
    else:
        from_id = int(msg.get('from_id') or 0)
        if from_id and from_id != viewer['id']:
            notify_ids.append(from_id)
        if peer_id and peer_id != viewer['id']:
            notify_ids.append(peer_id)

    if push_queue is not None:
        for uid in set(notify_ids):
            if uid == viewer['id']:
                continue
            _push_poll_update(
                storage, push_queue, session, peer_id, msg_id, poll_data,
                [uid])

    return packers.pack_updates(
        [edit_upd, pack_update_message_poll(
            poll_data, storage, peer_id, msg_id,
            viewer_id=viewer['id'], include_poll=True)],
        users, user_id=viewer['id'], storage=storage, increment_seq=False)


def _pack_message_peer_vote(user_id: int, options: list[str], voted_at: int,
                            multiple: bool) -> bytes:
    w = tl.Writer()
    if multiple and len(options) > 1:
        w.u32(MESSAGE_PEER_VOTE_MULTIPLE)
        w.raw(packers.pack_peer(user_id))
        w.u32(tl.VECTOR)
        w.i32(len(options))
        for opt in options:
            w.tl_bytes(bytes.fromhex(opt))
    else:
        w.u32(MESSAGE_PEER_VOTE)
        w.raw(packers.pack_peer(user_id))
        opt = options[0] if options else b'\x00'
        if isinstance(opt, str):
            opt = bytes.fromhex(opt)
        w.tl_bytes(opt)
    w.i32(int(voted_at))
    return w.bytes_out()


def handle_messages_get_poll_votes(session, req_msg_id, r, storage,
                                   push_queue, registry):
    from handlers.rpc import require_auth
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if peer_id is None:
        return _rpc_error(400, 'PEER_ID_INVALID')
    msg_id = r.i32()
    filter_option = None
    if flags & 1:
        filter_option = r.tl_bytes().hex()
    if flags & 2:
        r.string()  # offset
    limit = r.i32()

    msg = _find_poll_message(storage, viewer['id'], peer_id, msg_id)
    if not msg:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')

    poll_data = parse_poll_json(msg.get('media_poll_json'))
    if not poll_data:
        return _rpc_error(400, 'MESSAGE_ID_INVALID')
    poll = poll_data['poll']
    if not poll.get('public_voters'):
        return _rpc_error(400, 'MESSAGE_POLL_CLOSED')

    votes = storage.list_poll_votes(peer_id, msg_id)
    if filter_option:
        key = filter_option.lower()
        votes = [v for v in votes if key in {o.lower() for o in v['options']}]

    votes = votes[:max(1, min(int(limit or 100), 100))]
    user_ids = [int(v['user_id']) for v in votes]
    users = _collect_users(storage, user_ids)

    w = tl.Writer()
    w.u32(MESSAGES_VOTES_LIST)
    w.i32(0)
    w.i32(len(votes))
    w.u32(tl.VECTOR)
    w.i32(len(votes))
    multiple = bool(poll.get('multiple_choice'))
    for v in votes:
        w.raw(_pack_message_peer_vote(
            int(v['user_id']), v['options'], int(time.time()), multiple))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage))
    return w.bytes_out()


POLL_HANDLERS = {
    MESSAGES_SEND_VOTE: handle_messages_send_vote,
    MESSAGES_GET_POLL_RESULTS: handle_messages_get_poll_results,
    MESSAGES_GET_POLL_RESULTS_ALT: handle_messages_get_poll_results,
    MESSAGES_EDIT_MESSAGE: handle_messages_edit_message,
    MESSAGES_EDIT_MESSAGE_LEGACY: handle_messages_edit_message,
    tl.MESSAGES_EDIT_MESSAGE_V228: handle_messages_edit_message,
    MESSAGES_GET_POLL_VOTES: handle_messages_get_poll_votes,
}
