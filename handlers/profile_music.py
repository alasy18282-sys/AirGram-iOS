"""Profile saved music RPC handlers (corefork.telegram.org/api/profile)."""
import logging

from mtproto import tl
from handlers import packers
from handlers.stickers import pack_document

log = logging.getLogger(__name__)

ACCOUNT_SAVE_MUSIC = 0xb26732a9
ACCOUNT_GET_SAVED_MUSIC_IDS = 0xe09d5faf
ACCOUNT_GET_PRIVACY = 0xdadbc950
ACCOUNT_SET_PRIVACY = 0xc9f81ce8
ACCOUNT_SET_MAIN_PROFILE_TAB = 0x5dee78b0
USERS_GET_SAVED_MUSIC = 0x788d7fe3
USERS_GET_SAVED_MUSIC_BY_ID = 0x7573a4e9

ACCOUNT_SAVED_MUSIC_IDS = 0x998d6636
ACCOUNT_SAVED_MUSIC_IDS_NOT_MODIFIED = 0x4fc81d6e
ACCOUNT_PRIVACY_RULES = 0x50a04e45
USERS_SAVED_MUSIC = 0x34a2f297
USERS_SAVED_MUSIC_NOT_MODIFIED = 0xe3878aa4
_VALID_PROFILE_TABS = {
    0x72c64955,  # profileTabMedia
    0xab339c00,  # profileTabFiles
    0x9f27d26e,  # profileTabMusic
    0xe477092e,  # profileTabVoice
    0xd3656499,  # profileTabLinks
    0xa2c0f695,  # profileTabGifs
    0xb98cd696,  # profileTabPosts
    0x4d4bd46a,  # profileTabGifts
}


def _pack_saved_music_ids(ids) -> bytes:
    w = tl.Writer()
    w.u32(ACCOUNT_SAVED_MUSIC_IDS)
    w.u32(tl.VECTOR)
    w.i32(len(ids))
    for doc_id in ids:
        w.i64(int(doc_id))
    return w.bytes_out()


def _pack_saved_music(documents: list, count: int) -> bytes:
    w = tl.Writer()
    w.u32(USERS_SAVED_MUSIC)
    w.i32(int(count))
    w.u32(tl.VECTOR)
    w.i32(len(documents))
    for doc_bytes in documents:
        w.raw(doc_bytes)
    return w.bytes_out()


def _resolve_documents(storage, doc_ids):
    docs = []
    for doc_id in doc_ids:
        doc = storage.get_document_by_id(int(doc_id))
        if not doc:
            continue
        ss = storage.get_sticker_set_by_id(doc['set_id'])
        docs.append(pack_document(doc, ss))
    return docs


def _push_profile_music_changed(user: dict, storage, push_queue, session) -> None:
    if push_queue is None:
        return
    upd = packers.pack_updates(
        [packers.pack_update_user(int(user['id']))],
        [user],
        user_id=user['id'],
        storage=storage,
    )
    push_queue.push_to_user(
        user['id'], upd, exclude_auth_key_id=session.auth_key_id)


def handle_account_save_music(session, req_msg_id, r, storage, push_queue,
                              registry):
    from handlers.rpc import require_auth, rpc_error
    user, err = require_auth(session, storage)
    if user is None:
        return err

    flags = r.i32()
    unsave = bool(flags & 1)
    has_after = bool(flags & 2)
    doc_id = packers.parse_input_document(r)
    after_id = packers.parse_input_document(r) if has_after else None

    if doc_id is None:
        return rpc_error(400, 'DOCUMENT_INVALID')

    try:
        if unsave:
            storage.unsave_user_music(user['id'], doc_id)
            ok = True
        else:
            ok = storage.save_user_music(
                user['id'], doc_id, after_id=after_id)
    except ValueError as exc:
        return rpc_error(400, str(exc))

    if ok:
        _push_profile_music_changed(user, storage, push_queue, session)

    w = tl.Writer()
    w.u32(tl.BOOL_TRUE if ok else tl.BOOL_FALSE)
    return w.bytes_out()


def handle_account_get_saved_music_ids(session, req_msg_id, r, storage,
                                       push_queue, registry):
    from handlers.rpc import require_auth
    user, err = require_auth(session, storage)
    if user is None:
        return err

    req_hash = r.i64()
    ids = storage.list_user_saved_music_ids(user['id'], cleanup=True)
    h = storage.compute_saved_music_ids_hash(user['id'])

    if req_hash and req_hash == h:
        w = tl.Writer()
        w.u32(ACCOUNT_SAVED_MUSIC_IDS_NOT_MODIFIED)
        return w.bytes_out()

    return _pack_saved_music_ids(ids)


def handle_account_set_main_profile_tab(session, req_msg_id, r, storage,
                                        push_queue, registry):
    from handlers.rpc import require_auth, rpc_error
    user, err = require_auth(session, storage)
    if user is None:
        return err

    tab_cid = r.u32()
    if tab_cid not in _VALID_PROFILE_TABS:
        return rpc_error(400, 'PROFILE_TAB_INVALID')

    storage.set_user_main_profile_tab(user['id'], tab_cid)
    _push_profile_music_changed(user, storage, push_queue, session)

    w = tl.Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_users_get_saved_music(session, req_msg_id, r, storage, push_queue,
                                 registry):
    from handlers.rpc import require_auth, rpc_error
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    target = packers.parse_input_user(r, storage, self_user_id=viewer['id'])
    if target is None:
        return rpc_error(400, 'USER_ID_INVALID')

    from handlers.privacy import (
        INPUT_PRIVACY_KEY_SAVED_MUSIC,
        can_view_privacy,
    )
    if not can_view_privacy(storage, target['id'], viewer['id'],
                            INPUT_PRIVACY_KEY_SAVED_MUSIC):
        return _pack_saved_music([], 0)

    offset = r.i32()
    limit = max(0, min(r.i32(), 100))
    req_hash = r.i64()

    all_ids = storage.list_user_saved_music_ids(target['id'], cleanup=True)
    total = len(all_ids)
    h = storage.compute_saved_music_hash(target['id'])

    if req_hash and req_hash == h:
        w = tl.Writer()
        w.u32(USERS_SAVED_MUSIC_NOT_MODIFIED)
        w.i32(total)
        return w.bytes_out()

    page_ids = all_ids[offset:offset + limit] if limit else []
    docs = _resolve_documents(storage, page_ids)
    return _pack_saved_music(docs, total)


def handle_users_get_saved_music_by_id(session, req_msg_id, r, storage,
                                       push_queue, registry):
    from handlers.rpc import require_auth, rpc_error
    viewer, err = require_auth(session, storage)
    if viewer is None:
        return err

    target = packers.parse_input_user(r, storage, self_user_id=viewer['id'])
    if target is None:
        return rpc_error(400, 'USER_ID_INVALID')

    from handlers.privacy import (
        INPUT_PRIVACY_KEY_SAVED_MUSIC,
        can_view_privacy,
    )
    if not can_view_privacy(storage, target['id'], viewer['id'],
                            INPUT_PRIVACY_KEY_SAVED_MUSIC):
        return _pack_saved_music([], 0)

    assert r.u32() == tl.VECTOR
    count = r.i32()
    requested = []
    for _ in range(count):
        doc_id = packers.parse_input_document(r)
        if doc_id is not None:
            requested.append(doc_id)

    saved = set(storage.list_user_saved_music_ids(target['id'], cleanup=True))
    doc_ids = [d for d in requested if d in saved]
    docs = _resolve_documents(storage, doc_ids)
    return _pack_saved_music(docs, len(doc_ids))


PROFILE_MUSIC_HANDLERS = {
    ACCOUNT_SAVE_MUSIC: handle_account_save_music,
    ACCOUNT_GET_SAVED_MUSIC_IDS: handle_account_get_saved_music_ids,
    ACCOUNT_SET_MAIN_PROFILE_TAB: handle_account_set_main_profile_tab,
    USERS_GET_SAVED_MUSIC: handle_users_get_saved_music,
    USERS_GET_SAVED_MUSIC_BY_ID: handle_users_get_saved_music_by_id,
}
