"""Localization packs for Telegram Desktop and Android clients."""
from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from mtproto import tl

log = logging.getLogger(__name__)

LANGPACK_GET_LANGUAGES = 0x42c6978f
LANGPACK_GET_LANGUAGE = 0x6a596502
LANGPACK_GET_DIFFERENCE = 0xcd984aa5
LANGPACK_GET_LANG_PACK = 0xf2f2330a
LANG_PACK_STRING_PLURALIZED = 0x6c47ac9f
LANG_PACK_DIFFERENCE = 0xf385c1f6

_ROOT = os.path.dirname(os.path.dirname(__file__))
TDESKTOP_RU_PATH = os.path.join(_ROOT, 'tdesktop_ru_v5702288.strings')
ANDROID_RU_PATH = os.path.join(_ROOT, 'android_ru_v59737292.xml')
IOS_LANGPACK_GLOB = os.path.join(_ROOT, 'ios_*_v*.strings')
IOS_LANGPACK_NAME_RE = re.compile(r'^ios_([a-z0-9]+)_v(\d+)\.strings$', re.I)

_PLURAL_SUFFIXES = {
    'zero': 0,
    'one': 1,
    'two': 2,
    'few': 3,
    'many': 4,
    'other': 5,
}

_LANG_META = {
    'en': {
        'name': 'English',
        'native_name': 'English',
        'plural_code': 'en',
        'official': True,
    },
    'ru': {
        'name': 'Russian',
        'native_name': 'Русский',
        'plural_code': 'ru',
        'official': True,
    },
    'ru2': {
        'name': 'Russian 2',
        'native_name': 'Русский 2',
        'plural_code': 'ru',
        'base_lang_code': 'ru',
        'official': True,
        'android_only': True,
    },
}

_PREMIUM_BUTTON_TEXT = 'Опробовать Premium'

_LANG_OVERRIDES = {
  # Android
  'SubscribeToPremium': _PREMIUM_BUTTON_TEXT,
  'SubscribeToPremiumPerYear': _PREMIUM_BUTTON_TEXT,
  'SubscribeToPremiumPerCustom': _PREMIUM_BUTTON_TEXT,
  'SubscribeToPremiumNoPrice': _PREMIUM_BUTTON_TEXT,
  # Desktop
  'lng_premium_subscribe_button': _PREMIUM_BUTTON_TEXT,
  'lng_premium_summary_button': _PREMIUM_BUTTON_TEXT,
  'lng_premium_free': _PREMIUM_BUTTON_TEXT,
}

_ANDROID_RU_CODES = frozenset({'ru', 'ru2'})
_DIFF_CACHE: Dict[Tuple[str, str, int], bytes] = {}


def _reset_difference_cache() -> None:
    _DIFF_CACHE.clear()


def _normalize_lang_pack(lang_pack: str) -> str:
    return (lang_pack or '').strip().lower()


def _resolve_lang_pack(lang_pack: str) -> str:
    """Android sends an empty lang_pack; treat that as the android pack."""
    lang_pack = _normalize_lang_pack(lang_pack)
    if lang_pack in ('', 'android'):
        return 'android'
    if lang_pack == 'ios':
        return 'ios'
    return lang_pack


@dataclass
class LangPackEntry:
    key: str
    value: str = ''
    plural: Dict[str, str] = field(default_factory=dict)


@dataclass
class LangPackData:
    lang_pack: str
    lang_code: str
    version: int
    entries: Dict[str, LangPackEntry]
    path: str = ''
    base_version: int = 0

    @property
    def strings_count(self) -> int:
        return len(self.entries)


def _local_langpack_version(base_version: int) -> int:
    """Match the upstream pack version so bundled client strings stay valid."""
    return int(base_version)


def _version_from_path(path: str) -> int:
    match = re.search(r'v(\d+)', os.path.basename(path))
    return int(match.group(1)) if match else 1


def _unescape_tdesktop_value(value: str) -> str:
    return (
        value
        .replace('\\n', '\n')
        .replace('\\t', '\t')
        .replace('\\"', '"')
        .replace('\\\\', '\\')
    )


def _parse_tdesktop_strings(path: str) -> Dict[str, LangPackEntry]:
    entries: Dict[str, LangPackEntry] = {}
    pattern = re.compile(r'^"([^"]+)"\s*=\s*"(.*)"\s*;\s*$')
    with open(path, encoding='utf-8') as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith('//') or line.startswith('/*'):
                continue
            match = pattern.match(line)
            if not match:
                continue
            raw_key, raw_value = match.group(1), match.group(2)
            value = _unescape_tdesktop_value(raw_value)
            if '#' in raw_key:
                base_key, plural_key = raw_key.split('#', 1)
                entry = entries.setdefault(base_key, LangPackEntry(key=base_key))
                entry.plural[plural_key] = value
            else:
                entries[raw_key] = LangPackEntry(key=raw_key, value=value)
    return entries


def _parse_android_xml(path: str) -> Dict[str, LangPackEntry]:
    entries: Dict[str, LangPackEntry] = {}
    tree = ET.parse(path)
    for node in tree.getroot().findall('string'):
        raw_key = node.attrib.get('name', '')
        if not raw_key:
            continue
        value = ''.join(node.itertext())
        plural_key = None
        base_key = raw_key
        for suffix in _PLURAL_SUFFIXES:
            token = f'_{suffix}'
            if raw_key.endswith(token):
                base_key = raw_key[:-len(token)]
                plural_key = suffix
                break
        if plural_key:
            entry = entries.setdefault(base_key, LangPackEntry(key=base_key))
            entry.plural[plural_key] = value
        else:
            entries[raw_key] = LangPackEntry(key=raw_key, value=value)
    return entries


def _apply_lang_overrides(entries: Dict[str, LangPackEntry]) -> None:
    for key, value in _LANG_OVERRIDES.items():
        entries[key] = LangPackEntry(key=key, value=value)


class LangPackStore:
    def __init__(self) -> None:
        self._packs: Dict[Tuple[str, str], LangPackData] = {}
        self._load_defaults()

    def _load_defaults(self) -> None:
        if os.path.isfile(TDESKTOP_RU_PATH):
            tdesktop_entries = _parse_tdesktop_strings(TDESKTOP_RU_PATH)
            _apply_lang_overrides(tdesktop_entries)
            tdesktop_base = _version_from_path(TDESKTOP_RU_PATH)
            self._packs[('tdesktop', 'ru')] = LangPackData(
                lang_pack='tdesktop',
                lang_code='ru',
                # Bump beyond the source bundle version so clients that got
                # the old overrides-only response will request translations
                # again and receive the complete pack.
                version=_local_langpack_version(tdesktop_base) + 1,
                entries=tdesktop_entries,
                path=TDESKTOP_RU_PATH,
                base_version=tdesktop_base,
            )
            log.info(
                'Loaded tdesktop ru langpack: %d strings (v%d)',
                self._packs[('tdesktop', 'ru')].strings_count,
                self._packs[('tdesktop', 'ru')].version,
            )
        else:
            log.warning('Desktop Russian langpack not found: %s', TDESKTOP_RU_PATH)

        if os.path.isfile(ANDROID_RU_PATH):
            android_entries = _parse_android_xml(ANDROID_RU_PATH)
            _apply_lang_overrides(android_entries)
            base_version = _version_from_path(ANDROID_RU_PATH)
            # The patched APK only bundles a small KoalGram-specific Russian
            # override resource, not Telegram's complete Russian table.
            # Advertise a newer server pack so existing Russian installs
            # request the complete translation from langpack.getDifference.
            local_version = _local_langpack_version(base_version) + 1
            self._packs[('android', 'ru')] = LangPackData(
                lang_pack='android',
                lang_code='ru',
                version=local_version,
                entries=android_entries,
                path=ANDROID_RU_PATH,
                base_version=base_version,
            )
            self._packs[('android', 'ru2')] = LangPackData(
                lang_pack='android',
                lang_code='ru2',
                version=local_version + 1,
                entries=android_entries,
                path=ANDROID_RU_PATH,
                base_version=base_version,
            )
            log.info(
                'Loaded android ru langpack: %d strings (v%d)',
                self._packs[('android', 'ru')].strings_count,
                self._packs[('android', 'ru')].version,
            )
            log.info(
                'Loaded android ru2 langpack: %d strings (v%d)',
                self._packs[('android', 'ru2')].strings_count,
                self._packs[('android', 'ru2')].version,
            )
        else:
            log.warning('Android Russian langpack not found: %s', ANDROID_RU_PATH)

        self._load_ios_packs()

    def _load_ios_packs(self) -> None:
        import glob
        for path in sorted(glob.glob(IOS_LANGPACK_GLOB)):
            match = IOS_LANGPACK_NAME_RE.match(os.path.basename(path))
            if not match:
                log.warning('Skipping unrecognized iOS langpack file: %s', path)
                continue
            lang_code = match.group(1).lower()
            entries = _parse_tdesktop_strings(path)
            _apply_lang_overrides(entries)
            base_version = int(match.group(2))
            local_version = _local_langpack_version(base_version)
            self._packs[('ios', lang_code)] = LangPackData(
                lang_pack='ios',
                lang_code=lang_code,
                version=local_version,
                entries=entries,
                path=path,
                base_version=base_version,
            )
            log.info(
                'Loaded ios %s langpack: %d strings (v%d)',
                lang_code,
                self._packs[('ios', lang_code)].strings_count,
                local_version,
            )

    def register_android_pack(self, path: str) -> LangPackData:
        """Load or reload android_ru_v*.xml into the in-memory store."""
        path = os.path.abspath(path)
        base_version = _version_from_path(path)
        local_version = _local_langpack_version(base_version) + 1
        entries = _parse_android_xml(path)
        _apply_lang_overrides(entries)
        ru_pack = LangPackData(
            lang_pack='android',
            lang_code='ru',
            version=local_version,
            entries=entries,
            path=path,
            base_version=base_version,
        )
        ru2_pack = LangPackData(
            lang_pack='android',
            lang_code='ru2',
            version=local_version + 1,
            entries=entries,
            path=path,
            base_version=base_version,
        )
        self._packs[('android', 'ru')] = ru_pack
        self._packs[('android', 'ru2')] = ru2_pack
        _reset_difference_cache()
        return ru_pack

    def register_tdesktop_pack(self, path: str) -> LangPackData:
        """Load or reload tdesktop_ru_v*.strings into the in-memory store."""
        path = os.path.abspath(path)
        base_version = _version_from_path(path)
        entries = _parse_tdesktop_strings(path)
        _apply_lang_overrides(entries)
        pack = LangPackData(
            lang_pack='tdesktop',
            lang_code='ru',
            version=_local_langpack_version(base_version) + 1,
            entries=entries,
            path=path,
            base_version=base_version,
        )
        self._packs[('tdesktop', 'ru')] = pack
        _reset_difference_cache()
        return pack

    def register_ios_pack(self, path: str) -> LangPackData:
        """Load or reload one ios_*_v*.strings file into the in-memory store."""
        path = os.path.abspath(path)
        match = IOS_LANGPACK_NAME_RE.match(os.path.basename(path))
        if not match:
            raise ValueError(
                f'expected ios_<lang>_v<version>.strings, got {os.path.basename(path)}')
        lang_code = match.group(1).lower()
        entries = _parse_tdesktop_strings(path)
        _apply_lang_overrides(entries)
        base_version = int(match.group(2))
        local_version = _local_langpack_version(base_version)
        pack = LangPackData(
            lang_pack='ios',
            lang_code=lang_code,
            version=local_version,
            entries=entries,
            path=path,
            base_version=base_version,
        )
        self._packs[('ios', lang_code)] = pack
        _reset_difference_cache()
        return pack

    def get_pack(self, lang_pack: str, lang_code: str) -> Optional[LangPackData]:
        lang_code = (lang_code or '').strip().lower()
        resolved_pack = _resolve_lang_pack(lang_pack)
        if resolved_pack == 'ios':
            return self._packs.get(('ios', lang_code))
        if resolved_pack == 'android' and lang_code in _ANDROID_RU_CODES:
            return self._packs.get(('android', lang_code))
        if resolved_pack == 'tdesktop' and lang_code == 'ru':
            return self._packs.get(('tdesktop', 'ru'))
        if resolved_pack == 'android' and lang_code == 'ru':
            return self._packs.get(('android', 'ru'))
        return None

    def lookup_string(self, lang_pack: str, lang_code: str, key: str) -> str:
        pack = self.get_pack(lang_pack, lang_code)
        if pack:
            entry = pack.entries.get(key)
            if entry:
                if entry.plural:
                    return (
                        entry.plural.get('other')
                        or entry.plural.get('many')
                        or entry.plural.get('few')
                        or entry.plural.get('one')
                        or next(iter(entry.plural.values()), '')
                    )
                return entry.value
        if not lang_pack and lang_code in _ANDROID_RU_CODES:
            for code in (lang_code, 'ru2', 'ru'):
                pack = self._packs.get(('android', code))
                if not pack:
                    continue
                entry = pack.entries.get(key)
                if entry:
                    if entry.plural:
                        return (
                            entry.plural.get('other')
                            or entry.plural.get('many')
                            or entry.plural.get('few')
                            or entry.plural.get('one')
                            or next(iter(entry.plural.values()), '')
                        )
                    return entry.value
        if not lang_pack and lang_code == 'ru':
            for fallback_pack in ('ios', 'android', 'tdesktop'):
                pack = self._packs.get((fallback_pack, 'ru'))
                if not pack:
                    continue
                entry = pack.entries.get(key)
                if entry:
                    if entry.plural:
                        return (
                            entry.plural.get('other')
                            or entry.plural.get('many')
                            or entry.plural.get('few')
                            or entry.plural.get('one')
                            or next(iter(entry.plural.values()), '')
                        )
                    return entry.value
        if lang_pack == 'ios' or _resolve_lang_pack(lang_pack) == 'ios':
            for code in (lang_code, 'en', 'ru'):
                pack = self._packs.get(('ios', code))
                if not pack:
                    continue
                entry = pack.entries.get(key)
                if entry:
                    if entry.plural:
                        return (
                            entry.plural.get('other')
                            or entry.plural.get('many')
                            or entry.plural.get('few')
                            or entry.plural.get('one')
                            or next(iter(entry.plural.values()), '')
                        )
                    return entry.value
        if key in _BUSINESS_LANG_STRINGS:
            return _BUSINESS_LANG_STRINGS[key]
        readable = re.sub(r'([a-z])([A-Z])', r'\1 \2', key)
        return readable.replace('_', ' ')

    def languages_for_pack(self, lang_pack: str) -> List[dict]:
        resolved_pack = _resolve_lang_pack(lang_pack)
        languages = [_language_meta('en', resolved_pack)]
        if resolved_pack == 'tdesktop':
            pack = self._packs.get((resolved_pack, 'ru'))
            if pack:
                languages.append(_language_meta('ru', resolved_pack, pack))
        elif resolved_pack == 'android':
            pack = self._packs.get((resolved_pack, 'ru'))
            if pack:
                languages.append(_language_meta('ru', resolved_pack, pack))
        elif resolved_pack == 'ios':
            loaded = sorted({
                code for (pack_name, code) in self._packs if pack_name == 'ios'
            })
            for lang_code in loaded:
                if lang_code == 'en':
                    continue
                pack = self._packs.get(('ios', lang_code))
                if pack:
                    languages.append(_language_meta(lang_code, resolved_pack, pack))
            en_pack = self._packs.get(('ios', 'en'))
            if en_pack:
                languages[0] = _language_meta('en', resolved_pack, en_pack)
        return languages

    def all_languages(self) -> List[dict]:
        seen: Dict[str, dict] = {}
        for lang in self.languages_for_pack('ios'):
            seen[lang['lang_code']] = lang
        for lang in self.languages_for_pack('android'):
            if lang['lang_code'] not in seen:
                seen[lang['lang_code']] = lang
        for lang in self.languages_for_pack('tdesktop'):
            if lang['lang_code'] not in seen:
                seen[lang['lang_code']] = lang
        return list(seen.values())

    def difference(self, lang_pack: str, lang_code: str,
                   from_version: int) -> Tuple[str, int, int, List[LangPackEntry]]:
        lang_code = (lang_code or 'en').strip().lower()
        pack = self.get_pack(lang_pack, lang_code)
        if not pack:
            return lang_code, int(from_version), int(from_version), []
        from_v = int(from_version)
        if from_v >= pack.version:
            return pack.lang_code, from_v, pack.version, []
        if _resolve_lang_pack(lang_pack) in ('tdesktop', 'android'):
            # Both desktop and our Android APK need full tables. Android's
            # values-ru resource is deliberately minimal, so just returning
            # Premium overrides leaves most of the app in English.
            changed = list(pack.entries.values())
        else:
            # Android/iOS clients bundle their base translations locally;
            # only server-specific Premium label overrides need patching.
            changed = [
                pack.entries[key]
                for key in _LANG_OVERRIDES
                if key in pack.entries
            ]
        return pack.lang_code, from_v, pack.version, changed


def _language_meta(lang_code: str, lang_pack: str,
                   pack: Optional[LangPackData] = None) -> dict:
    meta = _LANG_META[lang_code]
    strings_count = pack.strings_count if pack else 0
    translated_count = strings_count
    return {
        'name': meta['name'],
        'native_name': meta['native_name'],
        'lang_code': lang_code,
        'plural_code': meta['plural_code'],
        'strings_count': strings_count,
        'translated_count': translated_count,
        'official': meta.get('official', False),
        'rtl': meta.get('rtl', False),
        'beta': meta.get('beta', False),
        'base_lang_code': meta.get('base_lang_code'),
        'translations_url': '',
    }


def _resolve_language_meta(lang_code: str, lang_pack: str,
                           pack: Optional[LangPackData]) -> dict:
    lang_code = (lang_code or 'en').strip().lower()
    if pack:
        return _language_meta(pack.lang_code, lang_pack, pack)
    if lang_code in _LANG_META:
        return _language_meta(lang_code, lang_pack)
    return _language_meta('en', lang_pack)


_BUSINESS_LANG_STRINGS = {
    'ContinueOnThisLanguage': 'Продолжить на этом языке',
    'BusinessLocation': 'Location',
    'BusinessHours': 'Opening hours',
    'BusinessIntro': 'Intro',
    'BusinessGreeting': 'Greeting message',
    'BusinessAway': 'Away message',
    'BusinessQuickReplies': 'Quick replies',
    'BusinessChatLinks': 'Chat links',
    'BusinessBots': 'Chatbots',
    'BusinessProfile': 'Business profile',
    'BusinessLinks': 'Links',
    'SetLocation': 'Set location',
    'SetHours': 'Set opening hours',
    'SetAwayMessage': 'Set away message',
    'SetGreetingMessage': 'Set greeting message',
    'SetIntro': 'Set intro',
    'BusinessTimezone': 'Time zone',
    'BusinessOpenNow': 'Open now',
    'BusinessClosed': 'Closed',
}


_STORE = LangPackStore()


def get_store() -> LangPackStore:
    return _STORE


def _pack_langpack_language(meta: dict) -> bytes:
    w = tl.Writer()
    w.u32(tl.LANG_PACK_LANGUAGE)
    flags = 0
    if meta.get('official'):
        flags |= 1
    if meta.get('base_lang_code'):
        flags |= 2
    if meta.get('rtl'):
        flags |= 4
    if meta.get('beta'):
        flags |= 8
    w.i32(flags)
    w.string(meta['name'])
    w.string(meta['native_name'])
    w.string(meta['lang_code'])
    if meta.get('base_lang_code'):
        w.string(meta['base_lang_code'])
    w.string(meta['plural_code'])
    w.i32(int(meta['strings_count']))
    w.i32(int(meta['translated_count']))
    w.string(meta.get('translations_url', ''))
    return w.bytes_out()


def _pack_langpack_string(entry: LangPackEntry, lang_code: str) -> bytes:
    if entry.plural:
        w = tl.Writer()
        w.u32(LANG_PACK_STRING_PLURALIZED)
        flags = 0
        for suffix in ('zero', 'one', 'two', 'few', 'many'):
            if suffix in entry.plural:
                flags |= 1 << _PLURAL_SUFFIXES[suffix]
        other_value = (
            entry.plural.get('other')
            or entry.plural.get('many')
            or entry.plural.get('few')
            or entry.plural.get('one')
            or entry.value
            or next(iter(entry.plural.values()), '')
        )
        w.i32(flags)
        w.string(entry.key)
        for suffix in ('zero', 'one', 'two', 'few', 'many'):
            if suffix in entry.plural:
                w.string(entry.plural[suffix])
        w.string(other_value)
        return w.bytes_out()

    w = tl.Writer()
    w.u32(tl.LANG_PACK_STRING)
    w.string(entry.key)
    w.string(entry.value)
    return w.bytes_out()


def _pack_langpack_string_value(lang_pack: str, lang_code: str, key: str) -> bytes:
    store = get_store()
    pack = store.get_pack(lang_pack, lang_code)
    if pack:
        entry = pack.entries.get(key)
        if entry:
            return _pack_langpack_string(entry, lang_code)
    value = store.lookup_string(lang_pack, lang_code, key)
    w = tl.Writer()
    w.u32(tl.LANG_PACK_STRING)
    w.string(key)
    w.string(value)
    return w.bytes_out()


def _pack_langpack_difference(lang_pack: str, lang_code: str,
                            from_version: int) -> bytes:
    store = get_store()
    lang_code, from_v, version, entries = store.difference(
        lang_pack, lang_code, from_version)
    w = tl.Writer()
    w.u32(LANG_PACK_DIFFERENCE)
    w.string(lang_code)
    w.i32(int(from_v))
    w.i32(int(version))
    w.u32(tl.VECTOR)
    w.i32(len(entries))
    for entry in entries:
        w.raw(_pack_langpack_string(entry, lang_code))
    return w.bytes_out()


def parse_lang_pack_body(r: tl.Reader) -> Tuple[str, str]:
    """Support both legacy (lang_code only) and modern (lang_pack, lang_code)."""
    first = r.string()
    if r.remaining() >= 4:
        lang_pack = first
        lang_code = r.string()
    else:
        lang_pack = ''
        lang_code = first
    return lang_pack, lang_code


def parse_get_strings_body(r: tl.Reader) -> Tuple[str, str, List[str]]:
    first = r.string()
    lang_pack = ''
    if r.remaining() >= 4:
        peek = r.u32()
        r.pos -= 4
        if peek != tl.VECTOR:
            lang_pack = first
            lang_code = r.string()
        else:
            lang_code = first
    else:
        lang_code = first
    assert r.u32() == tl.VECTOR
    keys = [r.string() for _ in range(r.i32())]
    return lang_pack, lang_code, keys


def pack_get_strings_response(lang_pack: str, lang_code: str,
                              keys: List[str]) -> bytes:
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(keys))
    for key in keys:
        w.raw(_pack_langpack_string_value(lang_pack, lang_code, key))
    return w.bytes_out()


def pack_get_languages_response(lang_pack: str) -> bytes:
    languages = get_store().languages_for_pack(lang_pack)
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(languages))
    for meta in languages:
        w.raw(_pack_langpack_language(meta))
    return w.bytes_out()


def pack_all_languages_response() -> bytes:
    languages = get_store().all_languages()
    w = tl.Writer()
    w.u32(tl.VECTOR)
    w.i32(len(languages))
    for meta in languages:
        w.raw(_pack_langpack_language(meta))
    return w.bytes_out()


def pack_get_language_response(lang_pack: str, lang_code: str) -> bytes:
    store = get_store()
    pack = store.get_pack(lang_pack, lang_code)
    return _pack_langpack_language(
        _resolve_language_meta(lang_code, lang_pack, pack))



def pack_get_difference_response(lang_pack: str, lang_code: str,
                                 from_version: int) -> bytes:
    cache_key = (
        _normalize_lang_pack(lang_pack),
        (lang_code or 'en').strip().lower(),
        int(from_version),
    )
    # Full initial langpack responses are megabytes — do not cache them.
    if int(from_version) != 0:
        cached = _DIFF_CACHE.get(cache_key)
        if cached is not None:
            return cached
    result = _pack_langpack_difference(lang_pack, lang_code, from_version)
    if int(from_version) != 0 and cache_key[1] in (*_ANDROID_RU_CODES, 'en', 'ru'):
        _DIFF_CACHE[cache_key] = result
    return result


get_store()
