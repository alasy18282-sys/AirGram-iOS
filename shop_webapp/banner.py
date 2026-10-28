"""Chat list banner (help.promoData custom_pending_suggestion)."""
from __future__ import annotations

import time
from typing import Any

from mtproto import tl
from mtproto.tl import Writer
from handlers.packers import pack_entities, utf16_len

PENDING_SUGGESTION = 0xE7E82E12
TEXT_WITH_ENTITIES = 0x751F3146
HELP_PROMO_DATA = 0x8A4D87A
HELP_PROMO_DATA_EMPTY = 0x98F6AC75


def ensure_banner_table(storage) -> None:
    storage._conn.execute('''
        CREATE TABLE IF NOT EXISTS chat_list_banner (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER NOT NULL DEFAULT 0,
            emoji TEXT NOT NULL DEFAULT '',
            emoji_id TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            suggestion_id TEXT NOT NULL DEFAULT 'airgram_banner',
            updated_at INTEGER NOT NULL DEFAULT 0
        )
    ''')
    storage._conn.execute(
        'INSERT OR IGNORE INTO chat_list_banner (id) VALUES (1)',
    )
    storage._conn.commit()


def _row_to_banner(row) -> dict[str, Any]:
    if not row:
        return {'enabled': False}
    return {
        'enabled': bool(row['enabled']),
        'emoji': row['emoji'] or '',
        'emojiId': row['emoji_id'] or '',
        'title': row['title'] or '',
        'description': row['description'] or '',
        'url': row['url'] or '',
        'suggestionId': row['suggestion_id'] or 'airgram_banner',
        'updatedAt': int(row['updated_at'] or 0),
    }


def get_banner(storage) -> dict[str, Any]:
    ensure_banner_table(storage)
    row = storage._conn.execute(
        'SELECT * FROM chat_list_banner WHERE id=1',
    ).fetchone()
    return {'ok': True, 'banner': _row_to_banner(row)}


def set_banner(
    storage,
    *,
    enabled: bool,
    emoji: str = '',
    emoji_id: str = '',
    title: str = '',
    description: str = '',
    url: str = '',
    suggestion_id: str = 'airgram_banner',
) -> dict[str, Any]:
    ensure_banner_table(storage)
    emoji = (emoji or '').strip()
    emoji_id = str(emoji_id or '').strip()
    import_error = ''
    if emoji_id:
        try:
            from shop_webapp.emoji_import import ensure_custom_emoji_document
            imported = ensure_custom_emoji_document(storage, int(emoji_id))
            if imported and not emoji:
                emoji = (imported.get('alt') or '').strip() or '⭐'
            elif imported is None:
                import_error = 'emoji_import_failed'
                if not emoji:
                    emoji = '⭐'
        except Exception as exc:
            import_error = str(exc)
            if not emoji:
                emoji = '⭐'
    storage._conn.execute(
        '''UPDATE chat_list_banner SET enabled=?, emoji=?, emoji_id=?, title=?,
           description=?, url=?, suggestion_id=?, updated_at=?
           WHERE id=1''',
        (
            1 if enabled else 0,
            emoji,
            emoji_id,
            (title or '').strip(),
            (description or '').strip(),
            (url or '').strip(),
            (suggestion_id or 'airgram_banner').strip() or 'airgram_banner',
            int(time.time()),
        ),
    )
    storage._conn.commit()
    try:
        from shop_webapp.banner_push import enqueue_banner_refresh
        pushed = enqueue_banner_refresh(storage)
    except Exception:
        pushed = 0
    result = get_banner(storage)
    if import_error:
        result['emojiImportWarning'] = import_error
    result['clientsNotified'] = pushed
    return result


def _pack_text_with_entities(text: str, entities: list[dict] | None = None) -> bytes:
    w = Writer()
    w.u32(TEXT_WITH_ENTITIES)
    w.string(text or '')
    w.raw(pack_entities(entities or [], text or ''))
    return w.bytes_out()


def _title_payload(emoji: str, emoji_id: str, title: str) -> tuple[str, list[dict]]:
    emoji = (emoji or '').strip()
    title = (title or '').strip()
    emoji_id = str(emoji_id or '').strip()
    if emoji_id:
        if not emoji:
            emoji = '⭐'
        spacer = ' ' if title else ''
        text = f'{emoji}{spacer}{title}'
        entities = [{
            'type': 'custom_emoji',
            'offset': 0,
            'length': utf16_len(emoji),
            'document_id': int(emoji_id),
        }]
        return text, entities
    if emoji and title:
        return f'{emoji} {title}', []
    if emoji:
        return emoji, []
    return title, []


def pack_promo_data(banner: dict[str, Any]) -> bytes:
    suggestion_id = (
        banner.get('suggestionId')
        or banner.get('suggestion_id')
        or 'airgram_banner'
    )
    title_text, title_entities = _title_payload(
        banner.get('emoji') or '',
        banner.get('emojiId') or banner.get('emoji_id') or '',
        banner.get('title') or '',
    )
    expires = int(time.time()) + 3600
    w = Writer()
    w.u32(HELP_PROMO_DATA)
    w.i32(1 << 4)  # custom_pending_suggestion
    w.i32(expires)
    w.u32(tl.VECTOR)
    w.i32(0)  # pending_suggestions — empty, no dismiss X
    w.u32(tl.VECTOR)
    w.i32(0)  # dismissed_suggestions
    w.u32(PENDING_SUGGESTION)
    w.string(suggestion_id)
    w.raw(_pack_text_with_entities(title_text, title_entities))
    w.raw(_pack_text_with_entities(banner.get('description') or ''))
    w.string(banner.get('url') or '')
    w.u32(tl.VECTOR)
    w.i32(0)  # chats
    w.u32(tl.VECTOR)
    w.i32(0)  # users
    return w.bytes_out()


def pack_promo_data_empty() -> bytes:
    w = Writer()
    w.u32(HELP_PROMO_DATA_EMPTY)
    w.i32(int(time.time()) + 3600)
    return w.bytes_out()


def promo_data_response(storage) -> bytes:
    data = get_banner(storage)
    banner = data.get('banner') or {}
    if not banner.get('enabled'):
        return pack_promo_data_empty()
    if not (banner.get('title') or banner.get('description') or banner.get('url')):
        return pack_promo_data_empty()
    return pack_promo_data(banner)
