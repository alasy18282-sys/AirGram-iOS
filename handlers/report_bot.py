"""System Report Bot flow."""
from __future__ import annotations

import json
import re
from typing import Optional

import config
from handlers import packers

START_TEXT = (
    "Этот бот помогает бороться с нарушителями. Здесь Вы можете подать жалобу "
    "на нарушителей (с помощью этого вы нам поможете бороться с плохими "
    "пользователями.)\n\n"
    "*Борьба с нарушителями*\n"
    "Так как нам очень сложно бороться с нарушителями, вы можете нам помочь!\n"
    "С помощью команды /report вы можете отправить жалобу на другого "
    "пользователя!\n\n"
    "/report — Отправить жалобу.\n\n"
    "*Рассмотрение жалобы*\n"
    "Жалоба будет рассматриваться в течении 48 часов. Также мы можем "
    "рассмотреть жалобу моментально, если наши модераторы прямо сейчас "
    "*онлайн*, *также что-бы ускорить проверку жалобы желательно отправить "
    "доказательства!*\n\n"
    "С уважением, команда KoalGram Support."
)

REPORT_PROMPT_TEXT = (
    "*Отправление жалобы*. Теперь выберите причину жалобы."
)

SUCCESS_TEXT = (
    "*Жалоба успешно отправлена!* Мы рассмотрим её в течении 48-и часов. "
    "Спасибо за ожидание."
)

REASONS = {
    "Мошенничество": {
        "key": "fraud",
        "label": "Мошенничество",
        "proof_prompt": "Выбран тип жалобы: *Мошенничество*. Теперь выберите нужный вам пункт.",
    },
    "18+": {
        "key": "adult",
        "label": "18+",
        "proof_prompt": "Выбран тип жалобы: *18+*. Теперь выберите нужный вам пункт.",
    },
    "Насилие": {
        "key": "violence",
        "label": "Насилие",
        "proof_prompt": "Выбран тип жалобы: *Насилие*. Теперь выберите нужный вам пункт.",
    },
    "Незаконные товары и услуги": {
        "key": "illegal_goods",
        "label": "Незаконные товары и услуги",
        "proof_prompt": "Выбран тип жалобы: *Незаконные товары и услуги*. Теперь выберите нужный вам пункт.",
    },
    "Нарушение авторских прав": {
        "key": "copyright",
        "label": "Нарушение авторских прав",
        "copyright_prompt": (
            "Выбран тип жалобы: *Нарушение авторских прав*. Для того что-бы "
            "отправить эту жалобу, вам нужно *конкретно объяснить* и "
            "предъявить *все доказательства*, что права принадлежат вам, далее "
            "мы рассмотрим вашу жалобу.\n\n"
            "Теперь отправьте *фото доказательство* и прикрепите к этому текст объяснение."
        ),
    },
    "Спам": {
        "key": "spam",
        "label": "Спам",
        "proof_prompt": "Выбран тип жалобы: *Спам*. Теперь выберите нужный вам пункт.",
    },
}

PROOF_WITH_TEXT = "Теперь отправьте *фото доказательство* и прикрепите к этому текст объяснение."
PROOF_WITHOUT_TEXT = "Теперь отправьте *описание жалобы*."
USERNAME_TEXT = "Теперь отправьте *юзернейм* нарушителя."
UNAVAILABLE_TEXT = "Недоступно"

_REASON_NAMES = {value["key"]: key for key, value in REASONS.items()}
_PROOF_CHOICES = {"Без доказательств", "С доказательствами"}
_USERNAME_RE = re.compile(
    r"(?:https?://)?(?:t\.me/)?@?([A-Za-z][A-Za-z0-9_]{4,31})",
    re.IGNORECASE,
)
_MARKUP_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def is_report_bot(user: dict) -> bool:
    return int(user.get("id") or 0) == int(config.REPORT_BOT_ID)


def _command_name(text: str) -> str:
    first = (text or "").strip().split(None, 1)[0] if (text or "").strip() else ""
    if not first.startswith("/"):
        return ""
    return first.split("@", 1)[0].lower()


def _render_text(text: str) -> tuple[str, list[dict]]:
    out: list[str] = []
    spans: list[dict] = []
    built_len = 0
    i = 0
    while i < len(text):
        if text[i] == '[':
            match = _MARKUP_LINK_RE.match(text, i)
            if match:
                label = match.group(1)
                url = match.group(2)
                start = built_len
                out.append(label)
                built_len += len(label)
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
                start = built_len
                out.append(inner)
                built_len += len(inner)
                spans.append({
                    'type': 'bold',
                    'start': start,
                    'end': start + len(inner),
                })
                i = end + 1
                continue
        out.append(text[i])
        built_len += 1
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


def _reply(text: str, *, reply_markup_json: str = '') -> dict:
    rendered, entities = _render_text(text)
    data = {"text": rendered, "entities": entities}
    if reply_markup_json:
        data["reply_markup_json"] = reply_markup_json
    return data


def _keyboard_json(rows: list[list[str]]) -> str:
    return json.dumps({
        "type": "keyboard",
        "resize": True,
        "rows": rows,
    }, ensure_ascii=False)


def _reason_keyboard() -> str:
    return _keyboard_json([
        ["Мошенничество", "18+"],
        ["Насилие", "Спам"],
        ["Незаконные товары и услуги"],
        ["Нарушение авторских прав"],
    ])


def _proof_keyboard() -> str:
    return _keyboard_json([["Без доказательств", "С доказательствами"]])


def _state_for(storage, user_id: int) -> dict:
    return storage.get_system_bot_state(config.REPORT_BOT_ID, user_id) or {}


def _save_state(storage, user_id: int, state: dict) -> None:
    storage.set_system_bot_state(config.REPORT_BOT_ID, user_id, state)


def _clear_state(storage, user_id: int) -> None:
    storage.clear_system_bot_state(config.REPORT_BOT_ID, user_id)


def _new_state() -> dict:
    return {
        "stage": "choose_reason",
        "reason_key": "",
        "evidence_mode": "",
        "description": "",
        "evidence_photo_id": 0,
    }


def _normalize_username(text: str) -> str:
    value = (text or "").strip()
    match = _USERNAME_RE.search(value)
    if not match:
        return ''
    return match.group(1).strip().lstrip('@').lower()


def _reason_label(state: dict) -> str:
    return _REASON_NAMES.get(state.get("reason_key") or "", "")


def _handle_command(storage, user_id: int, text: str, state: dict) -> Optional[dict]:
    command = _command_name(text)
    if not command:
        return None
    if command == "/start":
        return _reply(START_TEXT)
    if command == "/cancel":
        _clear_state(storage, user_id)
        return _reply("Текущее действие отменено.")
    if command == "/report":
        _save_state(storage, user_id, _new_state())
        return _reply(REPORT_PROMPT_TEXT, reply_markup_json=_reason_keyboard())
    return _reply(UNAVAILABLE_TEXT)


def _proof_choice_reply(reason_text: str) -> dict:
    return _reply(
        f"{reason_text} (Без доказательств, С доказательствами)",
        reply_markup_json=_proof_keyboard(),
    )


def _submit_report(storage, sender: dict, state: dict, username: str) -> dict:
    target = storage.get_user_by_username(username)
    if not target:
        return _reply("Пользователь с таким юзернеймом не найден. Пожалуйста, отправьте корректный *юзернейм* нарушителя.")
    if int(target['id']) == int(sender['id']):
        return _reply("Нельзя отправить жалобу на самого себя. Отправьте *юзернейм* нарушителя.")
    complaint = storage.create_report_complaint(
        reporter_id=int(sender['id']),
        reported_user_id=int(target['id']),
        reported_username=username,
        reason=_reason_label(state),
        evidence_mode=state.get("evidence_mode") or '',
        description=state.get("description") or '',
        evidence_photo_id=int(state.get("evidence_photo_id") or 0),
    )
    try:
        from report_admin_notify import notify_report_admins
        notify_report_admins(storage, complaint)
    except Exception:
        pass
    _clear_state(storage, int(sender['id']))
    return _reply(SUCCESS_TEXT)


def handle_report_bot_message(storage, sender: dict, incoming_msg: dict) -> dict:
    user_id = int(sender["id"])
    text = (incoming_msg.get("text") or "").strip()
    state = _state_for(storage, user_id)

    command_reply = _handle_command(storage, user_id, text, state)
    if command_reply is not None:
        return command_reply

    state = _state_for(storage, user_id)
    stage = state.get("stage") or ""
    media_photo_id = int(incoming_msg.get("media_photo_id") or 0)

    if stage == "choose_reason":
        choice = REASONS.get(text)
        if not choice:
            return _reply(REPORT_PROMPT_TEXT, reply_markup_json=_reason_keyboard())
        state["reason_key"] = choice["key"]
        if choice["key"] == "copyright":
            state["stage"] = "await_copyright_evidence"
            state["evidence_mode"] = "with_evidence"
            _save_state(storage, user_id, state)
            return _reply(choice["copyright_prompt"])
        state["stage"] = "choose_proof_mode"
        _save_state(storage, user_id, state)
        return _proof_choice_reply(choice["proof_prompt"])

    if stage == "choose_proof_mode":
        if text not in _PROOF_CHOICES:
            reason_name = _reason_label(state)
            prompt = REASONS.get(reason_name, {}).get("proof_prompt") if reason_name else None
            if prompt:
                return _proof_choice_reply(prompt)
            return _reply(REPORT_PROMPT_TEXT, reply_markup_json=_reason_keyboard())
        if text == "С доказательствами":
            state["evidence_mode"] = "with_evidence"
            state["stage"] = "await_evidence"
            _save_state(storage, user_id, state)
            return _reply(PROOF_WITH_TEXT)
        state["evidence_mode"] = "without_evidence"
        state["stage"] = "await_description"
        _save_state(storage, user_id, state)
        return _reply(PROOF_WITHOUT_TEXT)

    if stage == "await_evidence":
        if not media_photo_id:
            return _reply(PROOF_WITH_TEXT)
        state["description"] = text
        state["evidence_photo_id"] = media_photo_id
        state["stage"] = "await_username"
        _save_state(storage, user_id, state)
        return _reply(USERNAME_TEXT)

    if stage == "await_copyright_evidence":
        if not media_photo_id:
            return _reply(REASONS["Нарушение авторских прав"]["copyright_prompt"])
        state["description"] = text
        state["evidence_photo_id"] = media_photo_id
        state["stage"] = "await_username"
        _save_state(storage, user_id, state)
        return _reply(USERNAME_TEXT)

    if stage == "await_description":
        if not text:
            return _reply(PROOF_WITHOUT_TEXT)
        state["description"] = text
        state["evidence_photo_id"] = 0
        state["stage"] = "await_username"
        _save_state(storage, user_id, state)
        return _reply(USERNAME_TEXT)

    if stage == "await_username":
        username = _normalize_username(text)
        if not username:
            return _reply(USERNAME_TEXT)
        return _submit_report(storage, sender, state, username)

    return _reply(START_TEXT)
