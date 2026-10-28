"""Minimal valid TL stubs for common client RPCs (layer 224)."""
from __future__ import annotations

import time
import zlib

from mtproto import tl
from mtproto.tl import Writer

HELP_TERMS_OF_SERVICE_UPDATE_EMPTY = 0xe3309f7f
ACCOUNT_CONTENT_SETTINGS = 0x57e28221
ACCOUNT_THEMES_NOT_MODIFIED = 0xf41eb622
MESSAGES_EMOJI_GROUPS_NOT_MODIFIED = 0x6fb4ad87
ATTACH_MENU_BOTS_NOT_MODIFIED = 0xf1d88a5c
STORIES_ALL_STORIES_NOT_MODIFIED = 0x1158fe3e
STORIES_STEALTH_MODE = 0x712e27fd
PAYMENTS_STARS_REVENUE_ADS_ACCOUNT_URL = 0x394e7f21


def _bool_true() -> bytes:
    w = Writer()
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def pack_stories_stealth_mode() -> bytes:
    w = Writer()
    w.u32(STORIES_STEALTH_MODE)
    w.i32(0)
    return w.bytes_out()


def _pack_stats_graph_empty() -> bytes:
    w = Writer()
    w.u32(0xbedc9822)  # statsGraphError
    w.string('')
    return w.bytes_out()


def _pack_stars_amount(amount: int = 0) -> bytes:
    w = Writer()
    w.u32(0xbbb6b4a3)
    w.i64(amount)
    w.i32(0)
    return w.bytes_out()


def _pack_stars_revenue_status() -> bytes:
    w = Writer()
    w.u32(0xfebe5491)  # starsRevenueStatus
    w.i32(1 << 0)  # withdrawal_enabled
    w.raw(_pack_stars_amount(0))
    w.raw(_pack_stars_amount(0))
    w.raw(_pack_stars_amount(0))
    return w.bytes_out()


def handle_help_get_terms_of_service_update(session, req_msg_id, r, storage,
                                            push_queue, registry):
    w = Writer()
    w.u32(HELP_TERMS_OF_SERVICE_UPDATE_EMPTY)
    w.i32(int(time.time()) + 86400 * 30)
    return w.bytes_out()


def handle_account_get_contact_signup_notification(session, req_msg_id, r,
                                                   storage, push_queue,
                                                   registry):
    w = Writer()
    w.u32(tl.BOOL_FALSE)
    return w.bytes_out()


def handle_account_get_content_settings(session, req_msg_id, r, storage,
                                        push_queue, registry):
    w = Writer()
    w.u32(ACCOUNT_CONTENT_SETTINGS)
    w.i32(0)
    return w.bytes_out()


def handle_account_get_themes(session, req_msg_id, r, storage, push_queue,
                              registry):
    r.string()  # format
    r.i64()  # hash
    w = Writer()
    w.u32(ACCOUNT_THEMES_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_save_draft(session, req_msg_id, r, storage, push_queue,
                               registry):
    return _bool_true()


def handle_messages_get_emoji_groups(session, req_msg_id, r, storage,
                                     push_queue, registry):
    r.i32()  # hash
    w = Writer()
    w.u32(MESSAGES_EMOJI_GROUPS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_emoji_sticker_groups(session, req_msg_id, r, storage,
                                             push_queue, registry):
    r.i32()  # hash
    w = Writer()
    w.u32(MESSAGES_EMOJI_GROUPS_NOT_MODIFIED)
    return w.bytes_out()


def handle_messages_get_attach_menu_bots(session, req_msg_id, r, storage,
                                         push_queue, registry):
    r.i64()  # hash
    w = Writer()
    w.u32(ATTACH_MENU_BOTS_NOT_MODIFIED)
    return w.bytes_out()


def handle_contacts_get_statuses(session, req_msg_id, r, storage, push_queue,
                                 registry):
    w = Writer()
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_stories_get_all_stories(session, req_msg_id, r, storage,
                                   push_queue, registry):
    flags = r.i32()
    if flags & (1 << 0):
        r.string()
    w = Writer()
    w.u32(STORIES_ALL_STORIES_NOT_MODIFIED)
    w.i32(flags & (1 << 0))
    w.string('')
    w.raw(pack_stories_stealth_mode())
    return w.bytes_out()


def handle_payments_get_stars_revenue_stats(session, req_msg_id, r, storage,
                                            push_queue, registry):
    from handlers import packers
    r.i32()  # flags
    packers.skip_input_peer(r)
    w = Writer()
    w.u32(0x6c207376)  # payments.starsRevenueStats
    w.i32(0)
    w.raw(_pack_stats_graph_empty())
    w.raw(_pack_stars_revenue_status())
    w.f64(0.0)
    return w.bytes_out()


def handle_payments_get_stars_revenue_ads_account_url(session, req_msg_id, r,
                                                      storage, push_queue,
                                                      registry):
    from handlers import packers
    packers.skip_input_peer(r)
    w = Writer()
    w.u32(PAYMENTS_STARS_REVENUE_ADS_ACCOUNT_URL)
    w.string('https://example.com/')
    return w.bytes_out()


def _handle_stub_true(session, req_msg_id, r, storage, push_queue, registry):
    return _bool_true()


def _handle_stub_empty_vector(session, req_msg_id, r, storage, push_queue,
                               registry):
    w = Writer()
    w.u32(0x1cb5c415)
    w.i32(0)
    return w.bytes_out()


def _handle_aicompose_get_tones(session, req_msg_id, r, storage, push_queue,
                                registry):
    """aicompose.getTones#abd59201 — AI compose tones are not supported."""
    r.i64()  # hash
    w = Writer()
    w.u32(0xc1f46103)  # aicompose.tonesNotModified
    return w.bytes_out()


def _pack_reactions_notify_settings() -> bytes:
    w = Writer()
    w.u32(0x71e4ea58)  # reactionsNotifySettings (layer 227)
    w.i32(0)
    w.u32(0x97e8bebe)  # notificationSoundDefault
    w.u32(tl.BOOL_TRUE)
    return w.bytes_out()


def handle_account_get_reactions_notify_settings(session, req_msg_id, r, storage,
                                                 push_queue, registry):
    return _pack_reactions_notify_settings()


def handle_messages_get_sponsored_messages(session, req_msg_id, r, storage,
                                           push_queue, registry):
    from handlers import packers
    flags = r.i32()
    packers.skip_input_peer(r)
    if flags & 1:
        r.i32()
    w = Writer()
    w.u32(0x7da2d558)  # messages.sponsoredMessagesEmpty
    return w.bytes_out()


def handle_messages_search_global(session, req_msg_id, r, storage, push_queue,
                                  registry):
    from handlers import packers
    flags = r.i32()
    if flags & (1 << 0):
        r.i32()
    if flags & (1 << 4):
        # layer 228: community:flags.4?InputChannel
        cid = r.u32()
        if cid == 0xf35aec28:  # inputChannel
            r.i64(); r.i64()
    r.string()
    packers.skip_messages_filter(r)
    r.i32(); r.i32(); r.i32()
    packers.skip_input_peer(r)
    r.i32(); r.i32()
    w = Writer()
    w.u32(0x1d73e7ea)  # messages.messages
    w.u32(tl.VECTOR); w.i32(0)
    w.u32(tl.VECTOR); w.i32(0)
    w.u32(tl.VECTOR); w.i32(0)
    w.u32(tl.VECTOR); w.i32(0)
    return w.bytes_out()


def handle_messages_get_saved_reaction_tags(session, req_msg_id, r, storage,
                                            push_queue, registry):
    flags = r.i32()
    if flags & 1:
        from handlers import packers
        packers.skip_input_peer(r)
    req_hash = r.i64()
    if req_hash and int(req_hash) == 0:
        w = Writer()
        w.u32(0x889b59ef)  # messages.savedReactionTagsNotModified
        return w.bytes_out()
    w = Writer()
    w.u32(0x3259950a)  # messages.savedReactionTags
    w.u32(tl.VECTOR)
    w.i32(0)
    w.i64(0)
    return w.bytes_out()


def _pack_saved_dialogs_empty() -> bytes:
    w = Writer()
    w.u32(0xf83ae221)  # messages.savedDialogs
    for _ in range(4):
        w.u32(tl.VECTOR)
        w.i32(0)
    return w.bytes_out()


def _pack_webpage(url: str, req_hash: int = 0) -> bytes:
    page_hash = int(req_hash) or (
        zlib.crc32(url.encode('utf-8')) & 0x7FFFFFFF)
    w = Writer()
    w.u32(tl.WEB_PAGE)
    w.i32(0)
    w.i64(page_hash)
    w.string(url)
    w.string(url)
    w.i32(page_hash)
    return w.bytes_out()


def handle_messages_get_pinned_saved_dialogs(session, req_msg_id, r, storage,
                                             push_queue, registry):
    return _pack_saved_dialogs_empty()


def handle_messages_get_web_page(session, req_msg_id, r, storage, push_queue,
                                 registry):
    url = r.string()
    req_hash = int(r.i32())
    w = Writer()
    w.u32(tl.MESSAGES_WEB_PAGE)
    w.raw(_pack_webpage(url, req_hash))
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_account_get_connected_bots(session, req_msg_id, r, storage,
                                      push_queue, registry):
    w = Writer()
    w.u32(tl.ACCOUNT_CONNECTED_BOTS)
    w.u32(tl.VECTOR)
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_account_get_business_chat_links(session, req_msg_id, r, storage,
                                           push_queue, registry):
    w = Writer()
    w.u32(tl.ACCOUNT_BUSINESS_CHAT_LINKS)
    for _ in range(3):
        w.u32(tl.VECTOR)
        w.i32(0)
    return w.bytes_out()


def handle_help_save_app_log(session, req_msg_id, r, storage, push_queue,
                             registry):
    return _bool_true()


def handle_messages_read_reactions(session, req_msg_id, r, storage, push_queue,
                                   registry):
    from handlers import packers
    flags = r.i32()
    packers.skip_input_peer(r)
    if flags & 1:
        r.i32()
    if flags & 2:
        packers.skip_input_peer(r)
    w = Writer()
    w.u32(0xafb6144a)  # messages.affectedHistory
    w.i32(0)
    w.i32(0)
    w.i32(0)
    return w.bytes_out()


def handle_messages_get_emoji_keywords_difference(session, req_msg_id, r,
                                                  storage, push_queue,
                                                  registry):
    r.string()  # lang_code
    r.i32()  # from_version
    w = Writer()
    w.u32(0x5cc9bd7c)  # emojiKeywordsDifference
    w.string('')
    w.i32(0)
    w.u32(tl.VECTOR)
    w.i32(0)
    return w.bytes_out()


def handle_account_get_chat_themes(session, req_msg_id, r, storage, push_queue,
                                   registry):
    r.i64()  # hash
    w = Writer()
    w.u32(tl.ACCOUNT_THEMES_NOT_MODIFIED)
    return w.bytes_out()


def handle_account_get_default_background_emojis(session, req_msg_id, r,
                                                 storage, push_queue,
                                                 registry):
    r.i64()  # hash
    w = Writer()
    w.u32(tl.EMOJI_LIST_NOT_MODIFIED)
    return w.bytes_out()


def handle_premium_get_my_boosts(session, req_msg_id, r, storage,
                                 push_queue, registry):
    # premium.getMyBoosts#be77b4a has no parameters.
    w = Writer()
    w.u32(0x9ae228e2)  # premium.myBoosts
    w.i32(0)  # flags
    w.u32(tl.VECTOR)
    w.i32(0)  # my_boosts
    w.u32(tl.VECTOR)
    w.i32(0)  # chats
    w.u32(tl.VECTOR)
    w.i32(0)  # users
    return w.bytes_out()


STUB_HANDLERS = {
    0x0be77b4a: handle_premium_get_my_boosts,
    0x2ca51fd1: handle_help_get_terms_of_service_update,
    0x9f07c728: handle_account_get_contact_signup_notification,
    0x8b9b4dae: handle_account_get_content_settings,
    0x7206e458: handle_account_get_themes,
    0x54ae308e: handle_messages_save_draft,
    tl.MESSAGES_SAVE_DRAFT_V228: handle_messages_save_draft,
    0xa423bb51: _handle_stub_true,  # messages.editInlineBotMessage (layer 228)
    0x83557dba: _handle_stub_true,  # messages.editInlineBotMessage (<=227)
    0x6126a43c: handle_messages_search_global,  # layer 228
    0xabd59201: _handle_aicompose_get_tones,  # layer 228+
    0x7488ce5b: handle_messages_get_emoji_groups,
    0x1dd840f5: handle_messages_get_emoji_sticker_groups,
    0x16fcc2cb: handle_messages_get_attach_menu_bots,
    0xd91ffad6: handle_payments_get_stars_revenue_stats,
    0xd1d7efc5: handle_payments_get_stars_revenue_ads_account_url,
    # Unhandled methods seen in logs — profile load helpers
    0x6a3f8d65: _handle_stub_true,
    0x915860ae: _handle_stub_true,
    0x21a548f3: _handle_stub_true,
    0x56da0b3f: _handle_stub_true,
    0x182e6d6f: _handle_stub_true,
    0xe320c158: _handle_stub_true,
    0x472455aa: _handle_stub_true,
    0x7ae43737: _handle_stub_true,
    0x3637e05b: handle_messages_get_saved_reaction_tags,
    0x06dd654c: handle_account_get_reactions_notify_settings,
    0x3d6ce850: handle_messages_get_sponsored_messages,
    0x4bc6589a: handle_messages_search_global,
    0xc4a353ee: handle_contacts_get_statuses,
    tl.MESSAGES_GET_PINNED_SAVED_DIALOGS: handle_messages_get_pinned_saved_dialogs,
    tl.MESSAGES_GET_WEB_PAGE: handle_messages_get_web_page,
    tl.ACCOUNT_GET_CONNECTED_BOTS: handle_account_get_connected_bots,
    tl.ACCOUNT_GET_BUSINESS_CHAT_LINKS: handle_account_get_business_chat_links,
    tl.HELP_SAVE_APP_LOG: handle_help_save_app_log,
    tl.MESSAGES_READ_REACTIONS: handle_messages_read_reactions,
    tl.MESSAGES_GET_EMOJI_KEYWORDS_DIFFERENCE: (
        handle_messages_get_emoji_keywords_difference),
    tl.ACCOUNT_GET_CHAT_THEMES: handle_account_get_chat_themes,
}
