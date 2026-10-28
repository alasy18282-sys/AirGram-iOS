"""Post gift announcements to @KoalGramNews via Telethon user session.

Bot API cannot render custom emoji in channels (only private/group/supergroup).
We post through a real Telegram account with MessageEntityCustomEmoji instead.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import config

log = logging.getLogger(__name__)

_MTG_ROOT = Path(__file__).resolve().parent
_FALLBACK_ALT = {
    int(config.GIFT_NEWS_HEADER_EMOJI_ID): '✅',
    int(config.GIFT_NEWS_STAR_EMOJI_ID): '⭐️',
}


def _utf16_len(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


class _PostBuilder:
    def __init__(self) -> None:
        self._chunks: list[str] = []
        self.entities = []

    def _offset(self) -> int:
        return _utf16_len(''.join(self._chunks))

    def text(self, value: str) -> '_PostBuilder':
        self._chunks.append(value)
        return self

    def custom_emoji(self, doc_id: int, alt: str) -> '_PostBuilder':
        from telethon.tl.types import MessageEntityCustomEmoji

        alt = alt or _FALLBACK_ALT.get(int(doc_id), '🎁')
        self.entities.append(MessageEntityCustomEmoji(
            offset=self._offset(),
            length=_utf16_len(alt),
            document_id=int(doc_id),
        ))
        self._chunks.append(alt)
        return self

    def text_link(self, label: str, url: str) -> '_PostBuilder':
        from telethon.tl.types import MessageEntityTextUrl

        self.entities.append(MessageEntityTextUrl(
            offset=self._offset(),
            length=_utf16_len(label),
            url=url,
        ))
        self._chunks.append(label)
        return self

    def build(self) -> tuple[str, list]:
        return ''.join(self._chunks), self.entities


async def _fetch_emoji_alts(client, doc_ids: list[int]) -> dict[int, str]:
    from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest

    wanted = [int(x) for x in doc_ids if int(x) > 0]
    if not wanted:
        return {}

    out: dict[int, str] = {}
    try:
        docs = await client(GetCustomEmojiDocumentsRequest(document_id=wanted))
    except Exception as exc:
        log.warning('GetCustomEmojiDocuments failed: %s', exc)
        return out

    for doc in docs or []:
        alt = _FALLBACK_ALT.get(int(doc.id), '🎁')
        for attr in doc.attributes or []:
            if type(attr).__name__ == 'DocumentAttributeCustomEmoji':
                alt = (attr.alt or alt).strip() or alt
                break
        out[int(doc.id)] = alt
    return out


def _gift_emoji_id(gift: dict) -> int:
    return int(gift.get('document_id') or 0)


def _has_craft_models(storage, gift_id: int) -> bool:
    row = storage._conn.execute(
        '''SELECT COUNT(*) AS c FROM star_gift_upgrade_attrs
           WHERE (gift_id=? OR gift_id=0) AND attr_type='model' AND crafted=1''',
        (int(gift_id),),
    ).fetchone()
    return bool(row and int(row['c'] or 0) > 0)


def _upgrade_features(storage, gift: dict) -> tuple[str, str]:
    gid = int(gift['gift_id'])
    has_upgrade = int(gift.get('upgrade_stars') or 0) > 0
    if not has_upgrade:
        has_upgrade = storage.gift_can_upgrade(gid)
    upgrade_line = 'с улучшением' if has_upgrade else 'без улучшения'
    craft_line = 'с крафтом' if _has_craft_models(storage, gid) else 'без крафта'
    return upgrade_line, craft_line


def _stars_footer(builder: _PostBuilder) -> None:
    builder.text('Купить звезды удобно, можно ')
    builder.text_link('в нашем боте', config.GIFT_NEWS_AUTH_BOT_URL)
    builder.text(' (Web-App)\n\n@KoalGramNews x @GiftStar')


async def _build_new_gift_post(storage, gift_id: int, client
                               ) -> Optional[tuple[str, list]]:
    gift = storage.get_star_gift(int(gift_id))
    if not gift:
        return None

    gift_doc_id = _gift_emoji_id(gift)
    if gift_doc_id <= 0:
        return None

    header_id = int(config.GIFT_NEWS_HEADER_EMOJI_ID)
    star_id = int(config.GIFT_NEWS_STAR_EMOJI_ID)
    alts = await _fetch_emoji_alts(client, [header_id, star_id, gift_doc_id])

    stars = int(gift.get('stars') or 0)
    upgrade_line, craft_line = _upgrade_features(storage, gift)

    builder = _PostBuilder()
    builder.custom_emoji(header_id, alts.get(header_id, '✅'))
    builder.text(' Доступен новый подарок!\n\n')
    builder.custom_emoji(gift_doc_id, alts.get(gift_doc_id, '🎁'))
    builder.text(f' {stars} ')
    builder.custom_emoji(star_id, alts.get(star_id, '⭐️'))
    if int(gift.get('limited') or 0):
        total = gift.get('availability_total')
        if total is not None:
            builder.text(f' — {int(total)} шт.')
    builder.text('\n')
    builder.text(f'{upgrade_line} · {craft_line}\n\n')
    _stars_footer(builder)
    return builder.build()


async def _build_upgrade_post(storage, gift_id: int, client
                              ) -> Optional[tuple[str, list]]:
    gift = storage.get_star_gift(int(gift_id))
    if not gift:
        return None

    gift_doc_id = _gift_emoji_id(gift)
    if gift_doc_id <= 0:
        return None

    upgrade_stars = storage.get_gift_upgrade_stars(int(gift_id))
    if upgrade_stars <= 0:
        return None

    header_id = int(config.GIFT_NEWS_HEADER_EMOJI_ID)
    star_id = int(config.GIFT_NEWS_STAR_EMOJI_ID)
    alts = await _fetch_emoji_alts(client, [header_id, star_id, gift_doc_id])

    gift_stars = int(gift.get('stars') or 0)

    builder = _PostBuilder()
    builder.custom_emoji(header_id, alts.get(header_id, '✅'))
    builder.text(' Улучшение подарка доступно!\n\n')
    builder.custom_emoji(gift_doc_id, alts.get(gift_doc_id, '🎁'))
    builder.text(f' {gift_stars} ')
    builder.custom_emoji(star_id, alts.get(star_id, '⭐️'))
    builder.text(' — улучшение за ')
    builder.text(f'{upgrade_stars} ')
    builder.custom_emoji(star_id, alts.get(star_id, '⭐️'))
    builder.text('\n\n')
    _stars_footer(builder)
    return builder.build()


def _session_path() -> Path:
    raw = Path(config.GIFT_NEWS_SESSION)
    if raw.is_absolute():
        return raw
    return (_MTG_ROOT / raw).resolve()


async def announce_new_gift(storage, gift_id: int) -> bool:
    if not config.GIFT_NEWS_NOTIFY_ENABLED:
        return False
    from telethon import TelegramClient
    from frame_verification.telethon_env import (
        prepare_telethon_session,
        telethon_credentials,
    )

    api_id, api_hash = telethon_credentials()
    session_path, cleanup = prepare_telethon_session(_session_path())
    try:
        async with TelegramClient(
                session_path,
                api_id,
                api_hash,
                connection_retries=3,
                retry_delay=1,
                request_retries=2,
        ) as client:
            if not await client.is_user_authorized():
                log.warning('Gift news: Telethon session is not authorized')
                return False
            built = await _build_new_gift_post(storage, int(gift_id), client)
            if not built:
                return False
            text, entities = built
            await client.send_message(
                config.GIFT_NEWS_CHANNEL,
                text,
                formatting_entities=entities,
                link_preview=False,
            )
            log.info('Announced new gift %s to %s', gift_id, config.GIFT_NEWS_CHANNEL)
            return True
    except Exception as exc:
        log.warning('Gift news announce failed: %s', exc)
        return False
    finally:
        if cleanup is not None:
            cleanup()


async def announce_gift_upgrade(storage, gift_id: int) -> bool:
    if not config.GIFT_NEWS_NOTIFY_ENABLED:
        return False
    from telethon import TelegramClient
    from frame_verification.telethon_env import (
        prepare_telethon_session,
        telethon_credentials,
    )

    api_id, api_hash = telethon_credentials()
    session_path, cleanup = prepare_telethon_session(_session_path())
    try:
        async with TelegramClient(
                session_path,
                api_id,
                api_hash,
                connection_retries=3,
                retry_delay=1,
                request_retries=2,
        ) as client:
            if not await client.is_user_authorized():
                log.warning('Gift news: Telethon session is not authorized')
                return False
            built = await _build_upgrade_post(storage, int(gift_id), client)
            if not built:
                return False
            text, entities = built
            await client.send_message(
                config.GIFT_NEWS_CHANNEL,
                text,
                formatting_entities=entities,
                link_preview=False,
            )
            log.info(
                'Announced gift upgrade %s to %s',
                gift_id,
                config.GIFT_NEWS_CHANNEL,
            )
            return True
    except Exception as exc:
        log.warning('Gift news upgrade announce failed: %s', exc)
        return False
    finally:
        if cleanup is not None:
            cleanup()


def try_announce_new_gift(storage, gift_id: int) -> bool:
    """Sync wrapper for scripts without a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(announce_new_gift(storage, int(gift_id)))
    raise RuntimeError('await announce_new_gift() inside async code')


def try_announce_gift_upgrade(storage, gift_id: int) -> bool:
    """Sync wrapper for scripts without a running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(announce_gift_upgrade(storage, int(gift_id)))
    raise RuntimeError('await announce_gift_upgrade() inside async code')

