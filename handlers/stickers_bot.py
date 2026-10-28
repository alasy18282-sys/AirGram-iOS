"""System Stickers bot flow."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from typing import Optional

import config
from handlers import packers
from handlers.stickers import (
    build_document_attr_custom_emoji,
    build_document_attr_image_size,
)

START_TEXT = (
    "Этот бот помогает управлять стикерами. Здесь Вы можете создавать новые "
    "наборы стикеров или эмодзи, а также получать статистику использования "
    "Ваших стикеров. Инструкции по созданию стикеров и эмодзи собраны в этом "
    "[документе](https://core.telegram.org/stickers):\n\n"
    "*Эмодзи*\n"
    "/newemojipack — создать новый набор эмодзи\n\n"
    "*Стикеры и маски*\n"
    "/newpack — создать новый набор статичных стикеров в формате PNG / WEBP\n"
    "/newvideo — создать новый набор видеостикеров в формате WEBM\n"
    "/newanimated — создать новый набор анимированных стикеров в формате TGS\n"
    "/newmasks — создать новый набор статичных масок\n\n"
    "/addsticker — добавить стикер в набор\n"
    "/editsticker — редактировать эмодзи или координаты\n"
    "/replacesticker — заменить стикеры в наборе\n"
    "/ordersticker — изменить порядок стикеров в наборе\n"
    "/delsticker — удалить стикер из набора\n"
    "/setpackicon — задать обложку для набора\n"
    "/renamepack — изменить название набора\n"
    "/delpack — удалить набор\n\n"
    "*Статистика*\n"
    "/stats — статистика по стикеру\n"
    "/top — топ стикеров\n"
    "/packstats — статистика по набору стикеров\n"
    "/packtop — топ наборов\n"
    "/topbypack — топ стикеров в наборе\n"
    "/packusagetop — статистика использования Ваших наборов\n\n"
    "/cancel — отменить текущее действие"
)

NEW_PACK_KIND_TEXT = (
    "Создаётся новый набор *пользовательских эмодзи*. Наборы эмодзи бывают "
    "трёх видов: [анимированные](https://core.telegram.org/stickers#animated-emoji), "
    "[статичные](https://core.telegram.org/stickers#static-emoji) и "
    "[видеоэмодзи](https://core.telegram.org/stickers#video-emoji).\n\n"
    "Выберите нужный:"
)

ANIMATED_TITLE_TEXT = (
    "Создаётся новый набор *анимированных эмодзи*. Если возникнут вопросы, в "
    "этом [документе](https://core.telegram.org/stickers#animated-emoji) "
    "собраны нужные инструкции.\n\n"
    "Вы можете отправить команду /adaptive, если хотите, чтобы все эмодзи в "
    "наборе подстраивались под выбранную пользователем тему оформления, как "
    "[здесь](https://t.me/addemoji/EmoticonEmoji). В сообщениях эмодзи будут "
    "того же цвета, что и текст, а при использовании в качестве статуса — "
    "акцентного цвета.\n\n"
    "Пожалуйста, выберите название для нового набора эмодзи."
)

FIRST_TGS_TEXT = (
    "Спасибо! Теперь отправьте мне, пожалуйста, первый анимированный эмодзи — "
    "файл в формате *TGS*, созданный с помощью плагина "
    "[Bodymovin-TG](https://github.com/TelegramMessenger/bodymovin-extension) "
    "для Adobe After Effects.\n\n"
    "Рекомендуем загружать эмодзи через десктопное приложение."
)

AFTER_TGS_TEXT = (
    "Спасибо!\n"
    "Пожалуйста, отправьте мне стандартный эмодзи, который соответствует "
    "созданному Вами.\n"
    "Можно отправить сразу несколько в одном сообщении, однако мы советуем "
    "использовать не больше одного или двух на каждый из эмодзи в наборе."
)

PUBLISH_PROMPT_TEXT = (
    "Пожалуйста, выберите короткое название, которое будет использоваться в "
    "адресе Вашего набора. Я создам ссылку, которой Вы сможете поделиться с "
    "друзьями и подписчиками.\n\n"
    "Например, для этого набора используется короткое название «DuckEmoji»: "
    "[https://telegram.me/addemoji/DuckEmoji](https://telegram.me/addemoji/DuckEmoji)"
)

UNAVAILABLE_TEXT = "Недоступно"

_UNAVAILABLE_COMMANDS = {
    "/newpack",
    "/newvideo",
    "/newanimated",
    "/newmasks",
    "/addsticker",
    "/editsticker",
    "/replacesticker",
    "/ordersticker",
    "/delsticker",
    "/setpackicon",
    "/renamepack",
    "/delpack",
    "/stats",
    "/top",
    "/packstats",
    "/packtop",
    "/topbypack",
    "/packusagetop",
}

_SHORT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,63}$")
_SHORT_NAME_LINK_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/addemoji/([A-Za-z][A-Za-z0-9_.]*)",
    re.IGNORECASE,
)
_MARKUP_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def is_stickers_bot(user: dict) -> bool:
    return int(user.get("id") or 0) == int(config.STICKERS_BOT_ID)


def _command_name(text: str) -> str:
    first = (text or "").strip().split(None, 1)[0] if (text or "").strip() else ""
    if not first.startswith("/"):
        return ""
    return first.split("@", 1)[0].lower()


def _render_text(text: str) -> tuple[str, list[dict]]:
    out: list[str] = []
    spans: list[dict] = []
    i = 0
    while i < len(text):
        if text[i] == '[':
            match = _MARKUP_LINK_RE.match(text, i)
            if match:
                label = match.group(1)
                url = match.group(2)
                start = len(''.join(out))
                out.append(label)
                spans.append({
                    'type': 'text_url',
                    'start': start,
                    'end': start + len(label),
                    'url': url,
                })
                i = match.end()
                continue
        if text[i] == '*':
            end = text.find('*', i + 1)
            if end > i + 1:
                inner = text[i + 1:end]
                start = len(''.join(out))
                out.append(inner)
                spans.append({
                    'type': 'bold',
                    'start': start,
                    'end': start + len(inner),
                })
                i = end + 1
                continue
        out.append(text[i])
        i += 1

    rendered = ''.join(out)
    entities = []
    for span in spans:
        entity = {
            'type': span['type'],
            'offset': packers.utf16_offset(rendered, span['start']),
            'length': packers.utf16_len(rendered[span['start']:span['end']]),
        }
        if span['type'] == 'text_url':
            entity['url'] = span['url']
        entities.append(entity)
    return rendered, packers.merge_entities(entities, packers.detect_text_entities(rendered))


def _reply(text: str) -> dict:
    rendered, entities = _render_text(text)
    return {"text": rendered, "entities": entities}


def _kind_keyboard_json() -> str:
    return json.dumps({
        "type": "keyboard",
        "resize": True,
        "rows": [
            ["Анимированные эмодзи"],
            ["Видео эмодзи"],
            ["Статичные эмодзи"],
        ],
    }, ensure_ascii=False)


def _state_for(storage, user_id: int) -> dict:
    return storage.get_system_bot_state(config.STICKERS_BOT_ID, user_id) or {}


def _save_state(storage, user_id: int, state: dict) -> None:
    storage.set_system_bot_state(config.STICKERS_BOT_ID, user_id, state)


def _clear_state(storage, user_id: int) -> None:
    storage.clear_system_bot_state(config.STICKERS_BOT_ID, user_id)


def _new_state() -> dict:
    return {
        "stage": "choose_kind",
        "adaptive": False,
        "title": "",
        "items": [],
        "pending_doc_id": 0,
    }


def _normalize_pack_short_name(raw: str) -> str:
    text = (raw or "").strip()
    match = _SHORT_NAME_LINK_RE.search(text)
    if match:
        text = match.group(1)
    return text.strip().lstrip("@")


def _draft_items(state: dict) -> list[dict]:
    items = state.get("items") or []
    return [item for item in items if isinstance(item, dict) and item.get("doc_id")]


def _is_tgs_document(doc: Optional[dict]) -> bool:
    if not doc:
        return False
    mime = (doc.get("mime_type") or "").lower()
    path = (doc.get("file_path") or "").lower()
    return "tgsticker" in mime or path.endswith(".tgs")


def _sticker_set_hash(set_id: int, short_name: str, items: list[dict],
                      adaptive: bool) -> int:
    payload = "|".join(
        [str(set_id), short_name, "1" if adaptive else "0"]
        + [f"{item['doc_id']}:{item['emoji']}" for item in items]
    )
    return int(hashlib.md5(payload.encode("utf-8")).hexdigest()[:8], 16)


def _publish_set(storage, state: dict, short_name: str) -> str:
    title = (state.get("title") or "").strip()
    items = _draft_items(state)
    adaptive = bool(state.get("adaptive"))
    set_id = storage.allocate_custom_sticker_set_id()
    access_hash = secrets.randbits(63)
    flags = 128 | 1
    if adaptive:
        flags |= 512

    packs_by_emoji: dict[str, list[int]] = {}
    for idx, item in enumerate(items):
        doc_id = int(item["doc_id"])
        doc = storage.get_sticker_document_by_id(doc_id)
        if not doc:
            continue
        emoji = (item.get("emoji") or "").strip() or "🙂"
        attrs = [
            build_document_attr_image_size(
                int(doc.get("width") or 512),
                int(doc.get("height") or 512),
            ).hex(),
            build_document_attr_custom_emoji(
                emoji,
                set_id,
                access_hash,
                free=True,
                text_color=adaptive,
            ).hex(),
        ]
        updated = dict(doc)
        updated["set_id"] = set_id
        updated["alt"] = emoji
        updated["attributes_json"] = json.dumps(attrs)
        updated["sort_order"] = idx
        storage.upsert_sticker_document(updated)
        packs_by_emoji.setdefault(emoji, []).append(doc_id)

    packs = list(packs_by_emoji.items())
    doc_hash = _sticker_set_hash(set_id, short_name, items, adaptive)
    storage.upsert_sticker_set(
        set_id,
        access_hash,
        short_name,
        title,
        flags,
        len(items),
        doc_hash,
    )
    storage.replace_sticker_packs(set_id, packs)
    return f"https://t.me/addemoji/{short_name}"


def _handle_command(storage, user_id: int, text: str, state: dict) -> Optional[dict]:
    command = _command_name(text)
    if not command:
        return None
    if command == "/start":
        return _reply(START_TEXT)
    if command == "/cancel":
        _clear_state(storage, user_id)
        return _reply("Текущее действие отменено.")
    if command == "/newemojipack":
        _save_state(storage, user_id, _new_state())
        return {
            **_reply(NEW_PACK_KIND_TEXT),
            "reply_markup_json": _kind_keyboard_json(),
        }
    if command == "/adaptive":
        if state.get("stage") != "animated_title":
            return _reply(UNAVAILABLE_TEXT)
        state["adaptive"] = True
        _save_state(storage, user_id, state)
        return _reply(
            "Режим adaptive включён.\n\n" + ANIMATED_TITLE_TEXT
        )
    if command == "/publish":
        if state.get("stage") != "animated_tgs" or not _draft_items(state):
            return _reply(UNAVAILABLE_TEXT)
        state["stage"] = "publish_short_name"
        _save_state(storage, user_id, state)
        return _reply(PUBLISH_PROMPT_TEXT)
    if command in _UNAVAILABLE_COMMANDS or command.startswith("/"):
        return _reply(UNAVAILABLE_TEXT)
    return None


def handle_stickers_bot_message(storage, sender: dict, incoming_msg: dict) -> dict:
    user_id = int(sender["id"])
    text = (incoming_msg.get("text") or "").strip()
    state = _state_for(storage, user_id)

    command_reply = _handle_command(storage, user_id, text, state)
    if command_reply is not None:
        return command_reply

    state = _state_for(storage, user_id)
    stage = state.get("stage") or ""
    media_document_id = int(incoming_msg.get("media_document_id") or 0)

    if stage == "choose_kind":
        lowered = text.casefold()
        if lowered == "анимированные эмодзи".casefold():
            state["stage"] = "animated_title"
            _save_state(storage, user_id, state)
            return _reply(ANIMATED_TITLE_TEXT)
        if lowered in {
            "видео эмодзи".casefold(),
            "статичные эмодзи".casefold(),
        }:
            return _reply(UNAVAILABLE_TEXT)
        return {
            **_reply(NEW_PACK_KIND_TEXT),
            "reply_markup_json": _kind_keyboard_json(),
        }

    if stage == "animated_title":
        title = text.strip()
        if not title:
            return _reply(ANIMATED_TITLE_TEXT)
        if len(title) > 64:
            return _reply("Название слишком длинное. Пожалуйста, выберите более короткое название.")
        state["title"] = title
        state["stage"] = "animated_tgs"
        _save_state(storage, user_id, state)
        return _reply(FIRST_TGS_TEXT)

    if stage == "animated_tgs":
        if not media_document_id:
            if _draft_items(state):
                return _reply(
                    "Чтобы добавить больше анимированных эмодзи, пришлите следующий "
                    "файл в формате .TGS.\n\nКогда набор будет готов к публикации, "
                    "отправьте команду /publish."
                )
            return _reply(FIRST_TGS_TEXT)
        doc = storage.get_sticker_document_by_id(media_document_id)
        if not _is_tgs_document(doc):
            return _reply(FIRST_TGS_TEXT)
        state["pending_doc_id"] = media_document_id
        state["stage"] = "animated_emoticon"
        _save_state(storage, user_id, state)
        return _reply(AFTER_TGS_TEXT)

    if stage == "animated_emoticon":
        emoji = text.strip()
        if not emoji:
            return _reply(AFTER_TGS_TEXT)
        pending_doc_id = int(state.get("pending_doc_id") or 0)
        if not pending_doc_id:
            state["stage"] = "animated_tgs"
            _save_state(storage, user_id, state)
            return _reply(FIRST_TGS_TEXT)
        items = _draft_items(state)
        items.append({"doc_id": pending_doc_id, "emoji": emoji})
        state["items"] = items
        state["pending_doc_id"] = 0
        state["stage"] = "animated_tgs"
        _save_state(storage, user_id, state)
        count = len(items)
        return _reply(
            "Эмодзи добавлен. Количество эмодзи в наборе: "
            f"{count}.\nЧтобы добавить больше анимированных эмодзи, пришлите "
            "следующий файл в формате .TGS.\n\nКогда набор будет готов к "
            "публикации, отправьте команду /publish."
        )

    if stage == "publish_short_name":
        short_name = _normalize_pack_short_name(text)
        if not _SHORT_NAME_RE.fullmatch(short_name):
            return _reply(
                "Короткое название некорректно. Используйте только английские "
                "буквы, цифры и подчёркивания, начиная с буквы."
            )
        if storage.get_sticker_set_by_short_name(short_name):
            return _reply("Такое короткое название уже занято. Пожалуйста, выберите другое.")
        if not _draft_items(state):
            _clear_state(storage, user_id)
            return _reply(UNAVAILABLE_TEXT)
        link = _publish_set(storage, state, short_name)
        _clear_state(storage, user_id)
        return _reply(
            "Набор эмодзи успешно опубликован и доступен по ссылке: "
            f"{link}\n\nПользователи смогут добавить набор по этой ссылке, если "
            "у них установлена достаточно свежая версия Telegram. В настоящий "
            "момент пользовательские эмодзи доступны только с подпиской Telegram Premium."
        )

    return _reply(START_TEXT)
