"""Personal channel on profile (account.updatePersonalChannel)."""
import logging

from mtproto import tl
from mtproto.tl import Writer
from handlers import packers
from handlers.groups import parse_input_channel, _rpc_error

log = logging.getLogger(__name__)

ACCOUNT_UPDATE_PERSONAL_CHANNEL = 0xd94305e0


def _require_auth(session, storage):
    user_id = storage.get_session_user(session.auth_key_id)
    if not user_id:
        return None, _rpc_error(401, 'AUTH_KEY_UNREGISTERED')
    user = storage.get_user_by_id(user_id)
    if not user:
        return None, _rpc_error(401, 'AUTH_KEY_UNREGISTERED')
    return user, None


def _push_user_refresh(user: dict, storage, push_queue, session,
                       channel: dict | None = None) -> None:
    if push_queue is None:
        return
    chats = [channel] if channel else []
    upd = packers.pack_updates(
        [packers.pack_update_user(int(user['id']))],
        [user],
        user_id=user['id'],
        storage=storage,
        increment_seq=False,
        chats=chats,
    )
    push_queue.push_to_user(
        user['id'], upd, exclude_auth_key_id=session.auth_key_id)


def handle_account_update_personal_channel(session, req_msg_id, r, storage,
                                           push_queue, registry):
    user, err = _require_auth(session, storage)
    if user is None:
        return err

    try:
        parsed = parse_input_channel(r)
    except ValueError:
        return _rpc_error(400, 'CHANNEL_INVALID')

    channel = None
    if parsed is None:
        storage.set_user_personal_channel(user['id'], 0, 0)
    else:
        channel_id, access_hash = parsed
        ch = storage.get_channel_by_access_hash(channel_id, access_hash)
        if not ch:
            return _rpc_error(400, 'CHANNEL_INVALID')
        if int(ch.get('megagroup') or 0):
            return _rpc_error(400, 'CHANNEL_INVALID')
        if not (ch.get('username') or '').strip():
            return _rpc_error(400, 'CHANNEL_PUBLIC_REQUIRED')
        if not storage.is_channel_admin(channel_id, user['id']):
            return _rpc_error(400, 'CHAT_ADMIN_REQUIRED')
        msg_id = storage.resolve_personal_channel_message(
            channel_id, int(ch.get('top_message_id') or 0))
        if not msg_id:
            return _rpc_error(400, 'CHANNEL_INVALID')
        storage.set_user_personal_channel(user['id'], channel_id, msg_id)
        channel = storage.get_channel(channel_id)

    updated = storage.get_user_by_id(user['id'])
    if updated:
        _push_user_refresh(updated, storage, push_queue, session, channel)

    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


PERSONAL_CHANNEL_HANDLERS = {
    ACCOUNT_UPDATE_PERSONAL_CHANNEL: handle_account_update_personal_channel,
}
