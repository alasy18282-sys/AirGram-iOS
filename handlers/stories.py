"""User stories RPC handlers (layer 224)."""
from __future__ import annotations

import json
import logging
import time
from typing import List, Optional

from mtproto import tl
from mtproto.tl import Writer
from db.storage import Storage
from handlers import packers

log = logging.getLogger(__name__)

STORIES_GET_ALL_STORIES = 0xeeb0d625
STORIES_GET_ALL_STORIES_AIRGRAM = 0x637ea878  # KoalGram desktop fork ID
STORIES_SEND_STORY = 0x737fc2ec
STORIES_SEND_STORY_V2 = 0x8f9e6898
STORIES_GET_PEER_STORIES = 0x2c4ada50
STORIES_CAN_SEND_STORY = 0x30eb63f0
STORIES_READ_STORIES = 0xa556dac8
STORIES_GET_PEER_MAX_IDS = 0x78499170
STORIES_DELETE_STORIES = 0xae59db5f
STORIES_INCREMENT_STORY_VIEWS = 0xb2028afb
STORIES_INCREMENT_STORY_VIEWS_LEGACY = 0x4222fa74
STORIES_GET_STORIES_VIEWS = 0x28e16cc8
STORIES_GET_ALL_READ_PEER_STORIES = 0x9b5ae7f9
STORIES_GET_STORY_VIEWS_LIST = 0x7ed23c57
STORIES_GET_ALBUMS = 0x25b3eac7
STORIES_GET_CHATS_TO_SEND = 0xa56a8b60
STORIES_ALBUMS = 0xc3987a3a
STORIES_ALBUMS_NOT_MODIFIED = 0x564edaeb
STORIES_GET_PINNED_STORIES = 0x5821a5dc
STORIES_GET_STORIES_ARCHIVE = 0xb4352016
STORIES_GET_STORIES_BY_ID = 0x5774ca74

STORY_ITEM = 0xedf164f1  # corefork.telegram.org
STORY_ITEM_AIRGRAM_LEGACY = 0x16a4b93c  # older desktop fork constructor
STORIES_STORIES = 0x63c3dd0a
PEER_STORIES = 0x9a35e999
STORIES_PEER_STORIES = 0xcae68768
STORIES_ALL_STORIES = 0x6efc5e81
STORIES_ALL_STORIES_NOT_MODIFIED = 0x1158fe3e
STORIES_STEALTH_MODE = 0x712e27fd
STORIES_CAN_SEND_STORY_COUNT = 0xc387c04e
UPDATE_STORY_ID = 0x1bf335b9
UPDATE_STORY = 0x75b3b798
RECENT_STORY = 0x711d692d
STORY_VIEWS = 0x8d595cd6
STORY_VIEW = 0xb0bdeac5
STORIES_STORY_VIEWS_LIST = 0x59d78fc5
STORIES_STORY_VIEWS = 0xde9eed1d
UPDATE_READ_STORIES = 0xf74e932b
PRIVACY_VALUE_ALLOW_ALL = 0x65427b82
INPUT_PRIVACY_VALUE_ALLOW_ALL = 0xd66e8902


def _require_auth(session, storage):
    from handlers.rpc import require_auth
    return require_auth(session, storage)


def _rpc_error(code: int, message: str) -> bytes:
    from handlers.rpc import rpc_error
    return rpc_error(code, message)


def _use_legacy_story_item(session) -> bool:
    return bool(getattr(session, '_stories_legacy_item', False))


MESSAGE_MEDIA_EMPTY = 0x3ded6320


_PRIVACY_RULE_NO_EXTRA = {
    0x184b35ce,  # inputPrivacyValueAllowAll
    0xd66b66c9,  # inputPrivacyValueDisallowAll
    0x0d09e07b,  # inputPrivacyValueAllowContacts
    0xba52007,   # inputPrivacyValueDisallowContacts
    0x77cdd87f,  # inputPrivacyValueAllowCloseFriends
    0x89d77f3d,  # inputPrivacyValueAllowPremium
    0x73f49b5,   # inputPrivacyValueAllowBots
    0x8b47e85a,  # inputPrivacyValueAllowPremium (alt)
}


def _skip_privacy_rule(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in _PRIVACY_RULE_NO_EXTRA:
        return
    # rules with Vector<InputUser>
    if cid in (0x131cc67f, 0x90110467):  # allowUsers / disallowUsers
        assert r.u32() == tl.VECTOR
        count = r.i32()
        for _ in range(count):
            _skip_input_user(r)
        return
    # rules with Vector<long> (chat IDs)
    if cid in (0x840649cf, 0xe94f0f86):  # allowChatParticipants / disallowChatParticipants
        assert r.u32() == tl.VECTOR
        count = r.i32()
        for _ in range(count):
            r.i64()
        return
    # unknown rule: stop reading (can't safely skip)


def _skip_input_user(r: tl.Reader) -> None:
    cid = r.u32()
    if cid in (0xf7c1b13f, 0xb98886cf):  # inputUserSelf / inputUserEmpty
        return
    if cid in (0xf21158c6, 0x1da448e2):  # inputUser / inputUserFromMessage
        r.i64()  # user_id
        r.i64()  # access_hash
        if cid == 0x1da448e2:
            r.u32()  # peer (InputPeer)
            r.i32()  # msg_id
        return


def skip_input_privacy_rules(r: tl.Reader) -> None:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    for _ in range(count):
        _skip_privacy_rule(r)


def _skip_geo_point(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x2049d70c:  # geoPointEmpty
        return
    # geoPoint: long lat long alt_accuracy(flags.0?)
    r.f64()  # long
    r.f64()  # lat
    r.i64()  # access_hash
    flags = r.i32()
    if flags & 1:
        r.i32()  # accuracy_radius


def _skip_media_area_coordinates(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x3d1ea4e:  # mediaAreaCoordinates
        r.f64()  # x
        r.f64()  # y
        r.f64()  # w
        r.f64()  # h
        r.f64()  # rotation


def _skip_reaction(r: tl.Reader) -> None:
    cid = r.u32()
    if cid == 0x79f5d419:  # reactionEmpty
        return
    if cid == 0x1b2286be:  # reactionEmoji
        r.string()
    elif cid == 0x8935fc73:  # reactionCustomEmoji
        r.i64()


def skip_media_areas(r: tl.Reader) -> None:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    for _ in range(count):
        cid = r.u32()
        if cid == 0xddf36c4f:  # mediaAreaVenue: coords + Venue (geo title addr prov id type)
            _skip_media_area_coordinates(r)
            _skip_geo_point(r)   # geo
            r.string()           # title
            r.string()           # address
            r.string()           # provider
            r.string()           # venue_id
            r.string()           # venue_type
        elif cid == 0xb86cadf5:  # mediaAreaGeoPoint: coords + geo
            _skip_media_area_coordinates(r)
            _skip_geo_point(r)
        elif cid == 0x49a6522f:  # mediaAreaSuggestedReaction: coords + flags + reaction
            _skip_media_area_coordinates(r)
            r.i32()              # flags
            _skip_reaction(r)
        elif cid == 0x770416af:  # mediaAreaChannelPost: coords + channel_id + msg_id
            _skip_media_area_coordinates(r)
            r.i64()              # channel_id
            r.i32()              # msg_id
        elif cid in (0xb282217f, 0xf1392603):  # input* variants: coords + extra
            _skip_media_area_coordinates(r)
            # inputMediaAreaVenue: query_id(long) result_id(string)
            # inputMediaAreaGeoPoint: geo_point InputGeoPoint
            if cid == 0xb282217f:
                r.i64()
                r.string()
            else:
                _skip_geo_point(r)
        # unknown: skip coordinates only (best effort)
        else:
            _skip_media_area_coordinates(r)


def pack_stories_stealth_mode() -> bytes:
    w = Writer()
    w.u32(STORIES_STEALTH_MODE)
    w.i32(0)
    return w.bytes_out()


def pack_recent_story(max_id: int, *, live: bool = False) -> bytes:
    w = Writer()
    w.u32(RECENT_STORY)
    flags = 0
    if live:
        flags |= 1 << 0
    if max_id:
        flags |= 1 << 1
    w.i32(flags)
    if flags & (1 << 1):
        w.i32(int(max_id))
    return w.bytes_out()


def pack_privacy_value_allow_all() -> bytes:
    w = Writer()
    w.u32(PRIVACY_VALUE_ALLOW_ALL)
    return w.bytes_out()


def pack_story_views(owner_id: int, story_id: int, storage: Storage) -> bytes:
    count = storage.get_story_view_count(owner_id, story_id)
    recent = storage.get_story_recent_viewer_ids(owner_id, story_id, limit=5)
    flags = 0
    if recent:
        flags |= 1 << 0
        flags |= 1 << 1
    w = Writer()
    w.u32(STORY_VIEWS)
    w.i32(flags)
    w.i32(int(count))
    if flags & (1 << 0):
        w.u32(tl.VECTOR)
        w.i32(len(recent))
        for uid in recent:
            w.i64(int(uid))
    return w.bytes_out()


def pack_story_view(viewer_id: int, date: int) -> bytes:
    w = Writer()
    w.u32(STORY_VIEW)
    w.i32(0)
    w.i64(int(viewer_id))
    w.i32(int(date))
    return w.bytes_out()


def pack_update_read_stories(owner_id: int, max_id: int) -> bytes:
    w = Writer()
    w.u32(UPDATE_READ_STORIES)
    w.raw(packers.pack_peer_user(owner_id))
    w.i32(int(max_id))
    return w.bytes_out()


def pack_story_media(story: dict, storage: Storage) -> bytes:
    from handlers.media import (
        pack_message_media_photo,
        pack_message_media_from_ref,
    )
    kind = story.get('media_kind') or ''
    if kind == 'photo':
        photo = storage.get_photo_by_id(int(story['photo_id']))
        if photo:
            return pack_message_media_photo(photo, inline_preview=True)
    elif kind == 'document':
        doc = storage.get_document_by_id(int(story['document_id']))
        if doc:
            return pack_message_media_from_ref(
                {'type': 'document', 'id': int(doc['id']), 'doc': doc},
                storage, int(story.get('date') or 0))
    w = Writer()
    w.u32(MESSAGE_MEDIA_EMPTY)
    return w.bytes_out()


def pack_story_item(story: dict, owner_id: int, viewer_id: int,
                    storage: Storage, *,
                    legacy_constructor: bool = False) -> bytes:
    flags = (1 << 7)  # public
    if int(owner_id) == int(viewer_id):
        flags |= 1 << 16  # out
    if story.get('pinned'):
        flags |= 1 << 5
    if story.get('noforwards'):
        flags |= 1 << 10
    caption = story.get('caption') or ''
    entities = []
    try:
        entities = json.loads(story.get('entities_json') or '[]')
    except (TypeError, json.JSONDecodeError):
        entities = []
    if caption:
        flags |= 1 << 0
    if entities:
        flags |= 1 << 1
    flags |= 1 << 2  # privacy
    if int(owner_id) == int(viewer_id):
        flags |= 1 << 3  # views — owner sees viewer list (desktop + Android)

    w = Writer()
    w.u32(STORY_ITEM_AIRGRAM_LEGACY if legacy_constructor else STORY_ITEM)
    w.i32(flags)
    w.i32(int(story['id']))
    w.i32(int(story['date']))
    # from_id (18) / fwd_from (17) omitted — personal stories
    w.i32(int(story['expire_date']))
    if flags & (1 << 0):
        w.string(caption)
    if flags & (1 << 1):
        w.raw(packers.pack_entities(entities, caption))
    w.raw(pack_story_media(story, storage))
    if flags & (1 << 2):
        w.u32(tl.VECTOR)
        w.i32(1)
        w.raw(pack_privacy_value_allow_all())
    if flags & (1 << 3):
        w.raw(pack_story_views(owner_id, int(story['id']), storage))
    return w.bytes_out()


def pack_peer_stories(owner_id: int, viewer_id: int,
                      storage: Storage, *,
                      legacy_story_item: bool = False) -> bytes:
    stories = storage.get_active_stories(owner_id)
    max_read = storage.get_story_read_max_id(viewer_id, owner_id)
    flags = 0
    if max_read:
        flags |= 1 << 0
    w = Writer()
    w.u32(PEER_STORIES)
    w.i32(flags)
    w.raw(packers.pack_peer_user(owner_id))
    if flags & (1 << 0):
        w.i32(max_read)
    w.u32(tl.VECTOR)
    w.i32(len(stories))
    for story in stories:
        w.raw(pack_story_item(
            story, owner_id, viewer_id, storage,
            legacy_constructor=legacy_story_item))
    return w.bytes_out()


def pack_update_story_id(story_id: int, random_id: int) -> bytes:
    w = Writer()
    w.u32(UPDATE_STORY_ID)
    w.i32(int(story_id))
    w.i64(int(random_id))
    return w.bytes_out()


def pack_update_story(owner_id: int, story: dict, viewer_id: int,
                      storage: Storage) -> bytes:
    w = Writer()
    w.u32(UPDATE_STORY)
    w.raw(packers.pack_peer_user(owner_id))
    w.raw(pack_story_item(story, owner_id, viewer_id, storage))
    return w.bytes_out()


def _story_recipient_ids(storage: Storage, owner_id: int) -> List[int]:
    recipients = set()
    for dialog in storage.get_dialogs(owner_id, limit=500):
        peer_id = int(dialog['peer_id'])
        if peer_id > 0 and peer_id != owner_id:
            recipients.add(peer_id)
    for uid in storage.list_users_with_active_stories():
        if uid != owner_id:
            recipients.add(uid)
    return sorted(recipients)


def _push_story_to_recipients(storage: Storage, push_queue, owner_id: int,
                              story: dict, *, exclude_auth_key_id: int = 0):
    if push_queue is None:
        return
    owner = storage.get_user_by_id(owner_id)
    if not owner:
        return
    for recipient_id in _story_recipient_ids(storage, owner_id):
        upd = pack_update_story(owner_id, story, recipient_id, storage)
        push_queue.push_to_user(
            recipient_id, packers.pack_updates(
                [upd], [owner], user_id=recipient_id, storage=storage,
                increment_seq=True),
            exclude_auth_key_id=exclude_auth_key_id)


def _pack_empty_all_stories(state: str, *, flags: int = 0) -> bytes:
    """Empty allStories — no peer payloads (avoids Desktop NaN on story rings)."""
    w = Writer()
    w.u32(STORIES_ALL_STORIES)
    w.i32(flags)
    w.i32(0)  # count
    w.string(state)
    w.u32(tl.VECTOR)
    w.i32(0)  # peer_stories
    w.u32(tl.VECTOR)
    w.i32(0)  # chats
    w.u32(tl.VECTOR)
    w.i32(0)  # users
    w.raw(pack_stories_stealth_mode())
    return w.bytes_out()


def _pack_all_stories_not_modified(state: str, *, flags: int = 0) -> bytes:
    w = Writer()
    w.u32(STORIES_ALL_STORIES_NOT_MODIFIED)
    w.i32(flags)
    w.string(state)
    w.raw(pack_stories_stealth_mode())
    return w.bytes_out()


def _handle_stories_get_all_stories(session, req_msg_id, r, storage,
                                    push_queue, registry, *,
                                    legacy_story_item: bool = False):
    flags = r.i32()
    req_state = ''
    use_next_page = bool(flags & (1 << 1))
    if flags & (1 << 0):
        req_state = r.string()

    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return _pack_empty_all_stories('0:0:0', flags=flags)

    state = storage.get_story_state()
    if use_next_page or (req_state and req_state == state):
        return _pack_all_stories_not_modified(state, flags=flags)

    return _pack_empty_all_stories(state, flags=flags)


def handle_stories_get_all_stories(session, req_msg_id, r, storage,
                                   push_queue, registry):
    return _handle_stories_get_all_stories(
        session, req_msg_id, r, storage, push_queue, registry,
        legacy_story_item=_use_legacy_story_item(session))


def handle_stories_get_all_stories_airgram(session, req_msg_id, r, storage,
                                           push_queue, registry):
    # KoalGram desktop fork historically expects a legacy storyItem constructor.
    setattr(session, '_stories_legacy_item', True)
    return _handle_stories_get_all_stories(
        session, req_msg_id, r, storage, push_queue, registry,
        legacy_story_item=True)


def _resolve_story_peer_id(r: tl.Reader, viewer_id: int) -> Optional[int]:
    peer_id = packers.parse_input_peer(r, self_user_id=viewer_id)
    if peer_id is None:
        return None
    if peer_id < 0:
        return None
    return int(peer_id)


def _push_story_user_updates(storage: Storage, push_queue, owner_id: int,
                             *, exclude_auth_key_id: int = 0):
    """Notify peers that this user has new stories (avatar ring refresh)."""
    if push_queue is None:
        return
    upd = packers.pack_updates(
        [packers.pack_update_user(int(owner_id))],
        [], user_id=0, storage=storage, increment_seq=False)
    for recipient_id in _story_recipient_ids(storage, owner_id):
        push_queue.push_to_user(
            recipient_id, upd, exclude_auth_key_id=exclude_auth_key_id)


def handle_stories_send_story(session, req_msg_id, r, storage, push_queue,
                              registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if peer_id != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')

    from handlers.media import parse_input_media_uploaded, validate_media_ref
    try:
        media = parse_input_media_uploaded(r, storage, viewer['id'])
    except ValueError as exc:
        return _rpc_error(400, str(exc))
    if not validate_media_ref(storage, media):
        return _rpc_error(400, 'MEDIA_INVALID')

    if flags & (1 << 5):
        skip_media_areas(r)
    caption = ''
    entities = []
    if flags & (1 << 0):
        caption = r.string()
    if flags & (1 << 1):
        entities = packers.parse_entities(r)
    skip_input_privacy_rules(r)
    random_id = r.i64()
    period = 86400
    if flags & (1 << 3):
        period = r.i32()
    if flags & (1 << 6):
        packers.skip_input_peer(r)
        r.i32()
    if flags & (1 << 8):
        assert r.u32() == tl.VECTOR
        n = r.i32()
        for _ in range(n):
            r.i32()
    if flags & (1 << 9):
        packers.parse_input_document(r)

    media_kind = media['type']
    photo_id = int(media['id']) if media_kind == 'photo' else None
    document_id = int(media['id']) if media_kind == 'document' else None
    pinned = bool(flags & (1 << 2))

    story = storage.create_story(
        viewer['id'],
        media_kind=media_kind,
        photo_id=photo_id,
        document_id=document_id,
        caption=caption,
        entities=entities,
        period=period,
        pinned=pinned,
    )

    updates = [
        pack_update_story_id(int(story['id']), random_id),
        pack_update_story(int(viewer['id']), story, viewer['id'], storage),
    ]
    result = packers.pack_updates(
        updates, [viewer], user_id=viewer['id'], storage=storage)
    exclude = getattr(session, 'auth_key_id', 0) or 0
    _push_story_to_recipients(
        storage, push_queue, viewer['id'], story,
        exclude_auth_key_id=exclude)
    _push_story_user_updates(
        storage, push_queue, viewer['id'],
        exclude_auth_key_id=exclude)
    return result


def handle_stories_get_peer_stories(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    owner = storage.get_user_by_id(peer_id)
    if not owner:
        return _rpc_error(400, 'PEER_ID_INVALID')

    w = Writer()
    w.u32(STORIES_PEER_STORIES)
    w.raw(pack_peer_stories(
        peer_id, viewer['id'], storage,
        legacy_story_item=_use_legacy_story_item(session)))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(1)
    w.raw(packers.pack_user(
        owner, storage=storage, viewer_id=viewer['id'],
        is_self=int(peer_id) == int(viewer['id'])))
    return w.bytes_out()


def handle_stories_can_send_story(session, req_msg_id, r, storage,
                                  push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if peer_id != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')

    active = len(storage.get_active_stories(viewer['id']))
    remains = max(0, 100 - active)
    w = Writer()
    w.u32(STORIES_CAN_SEND_STORY_COUNT)
    w.i32(remains)
    return w.bytes_out()


def handle_stories_read_stories(session, req_msg_id, r, storage, push_queue,
                                registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    max_id = r.i32()
    if max_id <= 0:
        return _rpc_error(400, 'MAX_ID_INVALID')
    if not storage.get_active_stories(peer_id):
        return _rpc_error(400, 'STORIES_NEVER_CREATED')
    storage.read_stories(viewer['id'], peer_id, max_id)

    w = Writer()
    w.u32(tl.VECTOR)
    w.i32(1)
    w.i32(max_id)
    return w.bytes_out()


def handle_stories_get_peer_max_ids(session, req_msg_id, r, storage,
                                    push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    assert r.u32() == tl.VECTOR
    count = r.i32()
    summaries: List[bytes] = []
    for _ in range(count):
        peer_id = _resolve_story_peer_id(r, viewer['id'])
        max_id = storage.get_story_max_id(peer_id) if peer_id else 0
        if peer_id == int(viewer['id']):
            max_id = 0
        summaries.append(pack_recent_story(max_id))

    w = Writer()
    w.u32(tl.VECTOR)
    w.i32(len(summaries))
    for item in summaries:
        w.raw(item)
    return w.bytes_out()


def handle_stories_delete_stories(session, req_msg_id, r, storage, push_queue,
                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if peer_id != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    assert r.u32() == tl.VECTOR
    count = r.i32()
    ids = [r.i32() for _ in range(count)]
    storage.delete_stories(viewer['id'], ids)
    w = Writer()
    w.u32(tl.VECTOR)
    w.i32(len(ids))
    for sid in ids:
        w.i32(int(sid))
    return w.bytes_out()


def _parse_story_id_vector(r: tl.Reader) -> List[int]:
    assert r.u32() == tl.VECTOR
    count = r.i32()
    return [r.i32() for _ in range(count)]


def _handle_increment_story_views_legacy(session, req_msg_id, r, storage,
                                         push_queue, registry):
    """KoalGram legacy: id:Vector<int> without leading InputPeer."""
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    story_ids = _parse_story_id_vector(r)
    peer_id = None
    if r.remaining() >= 4:
        save = r.pos
        cid = r.u32()
        r.pos = save
        if cid != tl.VECTOR:
            peer_id = packers.parse_input_peer(r, self_user_id=viewer['id'])
    if not peer_id and story_ids:
        peer_id = storage.find_story_owner(story_ids[0])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    storage.record_story_views(peer_id, story_ids, viewer['id'])
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _handle_increment_story_views(session, req_msg_id, r, storage, push_queue,
                                  registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    story_ids = _parse_story_id_vector(r)
    storage.record_story_views(peer_id, story_ids, viewer['id'])
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def _pack_story_views_list(owner_id: int, story_id: int, viewer_id: int,
                           storage: Storage, *, offset: int = 0,
                           limit: int = 50) -> bytes:
    rows, total = storage.list_story_views(
        owner_id, story_id, offset=offset, limit=limit + 1)
    next_offset = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_offset = str(offset + limit)
    users_map = {}
    packed_views = []
    for row in rows:
        uid = int(row['viewer_id'])
        packed_views.append(pack_story_view(uid, int(row['date'])))
        u = storage.get_user_by_id(uid)
        if u:
            users_map[uid] = u
    flags = 0
    if next_offset:
        flags |= 1 << 0
    w = Writer()
    w.u32(STORIES_STORY_VIEWS_LIST)
    w.i32(flags)
    w.i32(total)
    w.i32(total)
    w.i32(0)  # forwards_count
    w.i32(0)  # reactions_count
    w.u32(tl.VECTOR)
    w.i32(len(packed_views))
    for item in packed_views:
        w.raw(item)
    w.u32(tl.VECTOR)
    w.i32(0)  # chats
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage, viewer_id=viewer_id))
    if next_offset:
        w.string(next_offset)
    return w.bytes_out()


def handle_stories_get_story_views_list(session, req_msg_id, r, storage,
                                        push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    flags = r.i32()
    if flags & (1 << 1):
        r.string()  # q
    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if peer_id != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    story_id = r.i32()
    offset_str = r.string()
    limit = r.i32()
    offset = int(offset_str) if offset_str.isdigit() else 0
    lim = min(max(limit, 1), 100)
    if not storage.get_story(peer_id, story_id):
        return _rpc_error(400, 'STORY_ID_INVALID')
    return _pack_story_views_list(
        peer_id, story_id, viewer['id'], storage,
        offset=offset, limit=lim)


def handle_stories_get_stories_views(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    if peer_id != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    story_ids = _parse_story_id_vector(r)

    packed_views = []
    users_map = {}
    for sid in story_ids:
        if not storage.get_story(peer_id, sid):
            continue
        packed_views.append(pack_story_views(peer_id, int(sid), storage))
        for uid in storage.get_story_recent_viewer_ids(peer_id, int(sid)):
            u = storage.get_user_by_id(uid)
            if u:
                users_map[uid] = u

    w = Writer()
    w.u32(STORIES_STORY_VIEWS)
    w.u32(tl.VECTOR)
    w.i32(len(packed_views))
    for item in packed_views:
        w.raw(item)
    users = list(users_map.values())
    w.u32(tl.VECTOR)
    w.i32(len(users))
    for u in users:
        w.raw(packers.pack_user(u, storage=storage, viewer_id=viewer['id']))
    return w.bytes_out()


def handle_stories_get_all_read_peer_stories(session, req_msg_id, r, storage,
                                             push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err

    updates = []
    users_map = {}
    for row in storage.list_story_reads_for_viewer(viewer['id']):
        owner_id = int(row['owner_id'])
        max_id = int(row['max_id'])
        if max_id <= 0:
            continue
        updates.append(pack_update_read_stories(owner_id, max_id))
        owner = storage.get_user_by_id(owner_id)
        if owner:
            users_map[owner_id] = owner
    return packers.pack_updates(
        updates, list(users_map.values()),
        user_id=viewer['id'], storage=storage)


def _pack_stories_list(owner_id: int, viewer_id: int, storage: Storage,
                       stories: List[dict], *,
                       pinned_ids: Optional[List[int]] = None,
                       legacy_story_item: bool = False) -> bytes:
    pinned_ids = pinned_ids or []
    flags = 0
    if pinned_ids:
        flags |= 1 << 0
    w = Writer()
    w.u32(STORIES_STORIES)
    w.i32(flags)
    w.i32(len(stories))
    w.u32(tl.VECTOR)
    w.i32(len(stories))
    for story in stories:
        w.raw(pack_story_item(
            story, owner_id, viewer_id, storage,
            legacy_constructor=legacy_story_item))
    if flags & (1 << 0):
        w.u32(tl.VECTOR)
        w.i32(len(pinned_ids))
        for sid in pinned_ids:
            w.i32(int(sid))
    w.u32(tl.VECTOR)
    w.i32(0)
    owner = storage.get_user_by_id(owner_id)
    w.u32(tl.VECTOR)
    w.i32(1 if owner else 0)
    if owner:
        w.raw(packers.pack_user(
            owner, storage=storage, viewer_id=viewer_id,
            is_self=int(owner_id) == int(viewer_id)))
    return w.bytes_out()


def handle_stories_get_pinned_stories(session, req_msg_id, r, storage,
                                      push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    _offset_id = r.i32()
    _limit = r.i32()
    stories = storage.get_active_stories(peer_id)
    pinned = [int(s['id']) for s in stories if s.get('pinned')]
    return _pack_stories_list(
        peer_id, viewer['id'], storage, stories, pinned_ids=pinned,
        legacy_story_item=_use_legacy_story_item(session))


def handle_stories_get_stories_archive(session, req_msg_id, r, storage,
                                       push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if peer_id != int(viewer['id']):
        return _rpc_error(400, 'PEER_ID_INVALID')
    _offset_id = r.i32()
    _limit = r.i32()
    stories = storage.get_active_stories(peer_id)
    return _pack_stories_list(
        peer_id, viewer['id'], storage, stories,
        legacy_story_item=_use_legacy_story_item(session))


def handle_stories_get_stories_by_id(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    peer_id = _resolve_story_peer_id(r, viewer['id'])
    if not peer_id:
        return _rpc_error(400, 'PEER_ID_INVALID')
    assert r.u32() == tl.VECTOR
    count = r.i32()
    ids = {r.i32() for _ in range(count)}
    stories = [s for s in storage.get_active_stories(peer_id)
               if int(s['id']) in ids]
    return _pack_stories_list(
        peer_id, viewer['id'], storage, stories,
        legacy_story_item=_use_legacy_story_item(session))


def handle_stories_get_albums(session, req_msg_id, r, storage, push_queue,
                              registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    _resolve_story_peer_id(r, viewer['id'])
    _hash = r.i64()
    w = Writer()
    w.u32(STORIES_ALBUMS_NOT_MODIFIED)
    return w.bytes_out()


def handle_stories_get_chats_to_send(session, req_msg_id, r, storage,
                                     push_queue, registry):
    viewer, err = _require_auth(session, storage)
    if viewer is None:
        return err
    w = Writer()
    w.u32(0x64ff9fd5)  # messages.chats
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


STORY_HANDLERS = {
    STORIES_GET_ALL_STORIES: handle_stories_get_all_stories,
    STORIES_GET_ALL_STORIES_AIRGRAM: handle_stories_get_all_stories_airgram,
    STORIES_SEND_STORY: handle_stories_send_story,
    STORIES_SEND_STORY_V2: handle_stories_send_story,
    STORIES_GET_PEER_STORIES: handle_stories_get_peer_stories,
    STORIES_CAN_SEND_STORY: handle_stories_can_send_story,
    STORIES_READ_STORIES: handle_stories_read_stories,
    STORIES_GET_PEER_MAX_IDS: handle_stories_get_peer_max_ids,
    STORIES_DELETE_STORIES: handle_stories_delete_stories,
    STORIES_INCREMENT_STORY_VIEWS: _handle_increment_story_views,
    STORIES_INCREMENT_STORY_VIEWS_LEGACY: _handle_increment_story_views_legacy,
    STORIES_GET_STORIES_VIEWS: handle_stories_get_stories_views,
    STORIES_GET_STORY_VIEWS_LIST: handle_stories_get_story_views_list,
    STORIES_GET_ALL_READ_PEER_STORIES: handle_stories_get_all_read_peer_stories,
    STORIES_GET_ALBUMS: handle_stories_get_albums,
    STORIES_GET_CHATS_TO_SEND: handle_stories_get_chats_to_send,
    STORIES_GET_PINNED_STORIES: handle_stories_get_pinned_stories,
    STORIES_GET_STORIES_ARCHIVE: handle_stories_get_stories_archive,
    STORIES_GET_STORIES_BY_ID: handle_stories_get_stories_by_id,
}
