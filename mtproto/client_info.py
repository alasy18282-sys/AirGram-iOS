"""Detect Telegram client platform from initConnection metadata."""
from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from typing import Any, FrozenSet

# tdesktop/Android send initConnection + invokeWithLayer only on the first
# request of a connection.  After a reconnect (or on an auxiliary connection)
# the server sees a brand-new Session with no metadata; packing layer-224
# constructors to a layer-229 client then produces "Response parse failed."
# Cache the metadata per auth key (stable across reconnects) and per user.
# The auth-key cache is also mirrored to SQLite so a server restart does not
# forget the client layer of already-signed-in sessions.
_AUTH_KEY_INFO: dict[int, dict] = {}
_USER_CLIENT: dict[int, dict] = {}
_PERSISTED_LAYER: dict[int, int] = {}
_PERSISTED_CLIENT: dict[int, str] = {}
_LOCK = threading.Lock()

_INFO_FIELDS = (
    'init_api_id', 'init_device_model', 'init_system_version',
    'init_app_version', 'init_system_lang_code', 'init_lang_pack',
    'init_lang_code', 'init_connection_flags', 'client_layer',
)


@lru_cache(maxsize=1)
def fork_ios_api_ids() -> FrozenSet[int]:
    ids: set[int] = set()
    raw = os.environ.get('TELEGRAM_API_ID', '').strip()
    if raw.isdigit():
        ids.add(int(raw))
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            '.telegram_api.env')
    if os.path.isfile(env_path):
        with open(env_path, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if line.startswith('TELEGRAM_API_ID='):
                    val = line.split('=', 1)[1].strip().strip('"').strip("'")
                    if val.isdigit():
                        ids.add(int(val))
    ids.add(36892050)  # intocline / airgram iOS fork default
    return frozenset(ids)


def capture_init_connection(session: Any, *, flags: int, api_id: int,
                            device_model: str, system_version: str,
                            app_version: str, system_lang_code: str,
                            lang_pack: str, lang_code: str) -> None:
    session.init_api_id = int(api_id)
    session.init_device_model = (device_model or '').strip()
    session.init_system_version = (system_version or '').strip()
    session.init_app_version = (app_version or '').strip()
    session.init_system_lang_code = (system_lang_code or '').strip()
    session.init_lang_pack = (lang_pack or '').strip()
    session.init_lang_code = (lang_code or '').strip()
    session.init_connection_flags = int(flags)
    from .log_filter import update_client_from_session
    update_client_from_session(session)
    remember_session(session)


def remember_session(session: Any) -> None:
    """Cache init metadata/layer so recreated sessions keep the client layer."""
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    if not auth_key_id:
        return
    info = {field: getattr(session, field, 0 if field != 'client_layer' else 0)
            for field in _INFO_FIELDS}
    if not info.get('client_layer') and not info.get('init_api_id'):
        return
    with _LOCK:
        _AUTH_KEY_INFO[auth_key_id] = info
        user_id = int(getattr(session, 'user_id', 0) or 0)
        if user_id:
            _USER_CLIENT[user_id] = info


def remember_layer(session: Any, layer: int) -> None:
    session.client_layer = int(layer)
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    if not auth_key_id:
        return
    with _LOCK:
        info = _AUTH_KEY_INFO.get(auth_key_id)
        if info is None:
            info = {field: getattr(session, field, 0)
                    for field in _INFO_FIELDS}
            _AUTH_KEY_INFO[auth_key_id] = info
        info['client_layer'] = int(layer)
        user_id = int(getattr(session, 'user_id', 0) or 0)
        if user_id:
            _USER_CLIENT[user_id] = dict(info)


def inherit_client_info(session: Any) -> None:
    """Fill a fresh Session from the cache of its auth key."""
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    if not auth_key_id:
        return
    with _LOCK:
        info = _AUTH_KEY_INFO.get(auth_key_id)
    if not info:
        return
    for field, value in info.items():
        if field == 'client_layer':
            if not getattr(session, field, 0):
                session.client_layer = int(value or 0)
        elif not getattr(session, field, 0):
            setattr(session, field, value)


def known_auth_key_layer(auth_key_id: int) -> int:
    with _LOCK:
        info = _AUTH_KEY_INFO.get(int(auth_key_id or 0))
    return int((info or {}).get('client_layer') or 0)


def persist_auth_key_info(storage: Any, session: Any) -> None:
    """Store init metadata + TL layer of an auth key in SQLite.

    TDesktop/Android do not repeat initConnection on reconnect, so without a
    durable copy a restarted server has to guess the layer and packs replies
    the client cannot parse ("Response parse failed").
    """
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    if not auth_key_id or storage is None:
        return
    info = {field: getattr(session, field, 0) for field in _INFO_FIELDS}
    if not info.get('client_layer') and not info.get('init_api_id'):
        return
    payload = json.dumps(info, ensure_ascii=False, sort_keys=True)
    keys = {auth_key_id}
    perm_auth_key_id = int(getattr(session, 'perm_auth_key_id', 0) or 0)
    if perm_auth_key_id:
        keys.add(perm_auth_key_id)
    for key in keys:
        with _LOCK:
            if _PERSISTED_CLIENT.get(key) == payload:
                continue
            _PERSISTED_CLIENT[key] = payload
        try:
            storage.set_server_kv(f'client_info:{key}', payload)
        except Exception:
            pass


def hydrate_auth_key_info(storage: Any, session: Any) -> bool:
    """Restore init metadata + TL layer of an auth key from SQLite."""
    auth_key_id = int(getattr(session, 'auth_key_id', 0) or 0)
    if not auth_key_id or storage is None:
        return False
    with _LOCK:
        cached = _AUTH_KEY_INFO.get(auth_key_id)
    if cached and (cached.get('client_layer') or cached.get('init_api_id')):
        return False
    try:
        raw = storage.get_server_kv(f'client_info:{auth_key_id}', '')
    except Exception:
        return False
    if not raw:
        return False
    try:
        info = json.loads(raw)
    except Exception:
        return False
    if not isinstance(info, dict):
        return False
    layer = int(info.get('client_layer') or 0)
    if layer and not (100 <= layer <= 1000):
        info['client_layer'] = 0
    info = {field: info.get(field, 0) for field in _INFO_FIELDS}
    with _LOCK:
        _AUTH_KEY_INFO[auth_key_id] = info
    inherit_client_info(session)
    return True


def remember_user(session: Any, user_id: int) -> None:
    """Associate the cached client info with a signed-in user."""
    if not user_id:
        return
    session.user_id = int(user_id)
    info = {field: getattr(session, field, 0) for field in _INFO_FIELDS}
    with _LOCK:
        _USER_CLIENT[int(user_id)] = info


def known_user_layer(user_id: int) -> int:
    with _LOCK:
        info = _USER_CLIENT.get(int(user_id or 0))
    return int((info or {}).get('client_layer') or 0)


def known_user_client_label(user_id: int) -> str:
    with _LOCK:
        info = _USER_CLIENT.get(int(user_id or 0))
    if not info:
        return ''
    return client_label(type('Info', (), info)())


# Some patched builds (notably several iOS forks) never send
# invokeWithLayer/initConnection: they call auth.sendCode directly on a fresh
# auth key. Packing layer-229 constructors (user#b1b8cc83) to a layer-225/226
# client makes auth.authorization unparseable, so the login spinner runs
# forever. auth.sendCode carries api_id, which is enough to pick a layer.
_API_ID_LAYERS = {
    2040: 229,      # Telegram Desktop
    36892050: 226,  # KoalGram / intocline iOS fork
}
# Official mobile clients (Android 12.7.0 and siblings) are layer 225.
_MOBILE_API_IDS = frozenset({4, 6, 2496, 2899, 5735, 10840, 17349, 21724})
_FALLBACK_MOBILE_LAYER = 225


def infer_layer_from_api_id(api_id: int) -> int:
    api_id = int(api_id or 0)
    if not api_id:
        return 0
    layer = _API_ID_LAYERS.get(api_id)
    if layer:
        return layer
    if api_id in fork_ios_api_ids():
        return 226
    if api_id in _MOBILE_API_IDS:
        return _FALLBACK_MOBILE_LAYER
    return 0


def apply_api_id_layer_hint(session: Any, api_id: int, storage: Any = None) -> int:
    """Pin the client layer from auth.sendCode when the client skipped initConnection."""
    layer = int(getattr(session, 'client_layer', 0) or 0)
    if layer:
        return layer
    layer = infer_layer_from_api_id(api_id)
    if not layer:
        return 0
    session.client_layer = int(layer)
    try:
        remember_layer(session, layer)
    except Exception:
        pass
    if storage is not None:
        try:
            persist_auth_key_info(storage, session)
        except Exception:
            pass
    return layer


def resolve_session_layer(session: Any) -> int:
    """Best known TL layer for a session (0 if the client never told us)."""
    layer = int(getattr(session, 'client_layer', 0) or 0)
    if layer:
        return layer
    layer = known_auth_key_layer(int(getattr(session, 'auth_key_id', 0) or 0))
    if layer:
        session.client_layer = layer
        return layer
    layer = known_user_layer(int(getattr(session, 'user_id', 0) or 0))
    if layer:
        session.client_layer = layer
    return layer


def hydrate_user_layer(storage: Any, user_id: int) -> int:
    """Load the last known client layer of a user from the DB."""
    user_id = int(user_id or 0)
    if not user_id:
        return 0
    layer = known_user_layer(user_id)
    if layer:
        return layer
    try:
        layer = int(storage.get_server_kv(f'client_layer:{user_id}', '') or 0)
    except Exception:
        return 0
    if 100 <= layer <= 1000:
        with _LOCK:
            info = _USER_CLIENT.setdefault(user_id, {})
            info['client_layer'] = layer
        return layer
    return 0


def persist_user_layer(storage: Any, user_id: int, layer: int) -> None:
    user_id = int(user_id or 0)
    layer = int(layer or 0)
    if not user_id or not (100 <= layer <= 1000):
        return
    with _LOCK:
        if _PERSISTED_LAYER.get(user_id) == layer:
            return
        _PERSISTED_LAYER[user_id] = layer
    try:
        storage.set_server_kv(f'client_layer:{user_id}', str(layer))
    except Exception:
        pass


def client_label(session: Any) -> str:
    device = (getattr(session, 'init_device_model', '') or '').lower()
    system = (getattr(session, 'init_system_version', '') or '').lower()
    app = (getattr(session, 'init_app_version', '') or '').lower()
    lang_pack = (getattr(session, 'init_lang_pack', '') or '').lower()
    api_id = int(getattr(session, 'init_api_id', 0) or 0)

    if lang_pack == 'ios':
        return 'iOS'
    if api_id in fork_ios_api_ids():
        return 'iOS'
    if any(token in device for token in ('iphone', 'ipad', 'ipod')):
        return 'iOS'
    if 'ios' in system or 'ipados' in system:
        return 'iOS'
    if 'telegram ios' in app or app.startswith('ios '):
        return 'iOS'

    if lang_pack == 'tdesktop' or 'telegram desktop' in app:
        return 'Desktop'
    if api_id == 2040:
        return 'Desktop'
    if 'android' in system or 'sdk' in system:
        return 'Android'
    if lang_pack == 'android':
        return 'Android'
    if any(token in system for token in ('windows', 'macos', 'linux', 'ubuntu')):
        return 'Desktop'
    return 'Unknown'


def format_client_log(session: Any) -> str:
    label = client_label(session)
    device = getattr(session, 'init_device_model', '') or ''
    app = getattr(session, 'init_app_version', '') or ''
    system = getattr(session, 'init_system_version', '') or ''
    lang_pack = getattr(session, 'init_lang_pack', '') or ''
    api_id = int(getattr(session, 'init_api_id', 0) or 0)
    parts = [f'client={label}']
    if device:
        parts.append(f'device={device}')
    if system:
        parts.append(f'system={system}')
    if app:
        parts.append(f'app={app}')
    if lang_pack:
        parts.append(f'lang_pack={lang_pack}')
    if api_id:
        parts.append(f'api_id={api_id}')
    layer = int(getattr(session, 'client_layer', 0) or 0)
    if layer:
        parts.append(f'layer={layer}')
    return ' '.join(parts)
