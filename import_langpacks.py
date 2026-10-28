#!/usr/bin/env python3
"""Import Russian langpack files from the project root for all platforms.

Expected files (already in repo root by default):
  android_ru_v59737292.xml
  tdesktop_ru_v5702288.strings
  ios_ru_v21502454.strings
  ios_en_v21501099.strings  (optional, for iOS English)

Examples:
  python import_langpacks.py
  python import_langpacks.py --android /path/to/android_ru.xml
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from handlers.langpack import (
    ANDROID_RU_PATH,
    IOS_LANGPACK_GLOB,
    TDESKTOP_RU_PATH,
    get_store,
)


def _import_ios(store, pattern: str) -> int:
    count = 0
    for path in sorted(glob.glob(pattern)):
        pack = store.register_ios_pack(path)
        print(
            f'Loaded ios/{pack.lang_code}: {pack.strings_count} strings, '
            f'server version {pack.version}')
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Import Android, Desktop and iOS langpacks into mytelegram-python')
    parser.add_argument('--android', help='Path to android_ru_v*.xml')
    parser.add_argument('--tdesktop', help='Path to tdesktop_ru_v*.strings')
    parser.add_argument('--ios-glob', default=IOS_LANGPACK_GLOB,
                        help='Glob for ios_*_v*.strings (default: project root)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    android_path = os.path.abspath(args.android or ANDROID_RU_PATH)
    tdesktop_path = os.path.abspath(args.tdesktop or TDESKTOP_RU_PATH)
    ios_glob = args.ios_glob
    if not os.path.isabs(ios_glob):
        ios_glob = os.path.join(ROOT, ios_glob)

    missing = []
    if not os.path.isfile(android_path):
        missing.append(android_path)
    if not os.path.isfile(tdesktop_path):
        missing.append(tdesktop_path)
    ios_files = sorted(glob.glob(ios_glob))
    if not ios_files:
        missing.append(ios_glob)

    if missing:
        print('Missing langpack files:', file=sys.stderr)
        for path in missing:
            print(f'  {path}', file=sys.stderr)
        return 1

    if args.dry_run:
        print(f'Would import android: {android_path}')
        print(f'Would import tdesktop: {tdesktop_path}')
        for path in ios_files:
            print(f'Would import ios: {path}')
        return 0

    store = get_store()
    android = store.register_android_pack(android_path)
    print(
        f'Loaded android/ru: {android.strings_count} strings, '
        f'server version {android.version}')
    print(
        f'Loaded android/ru2: {store.get_pack("android", "ru2").strings_count} strings, '
        f'server version {store.get_pack("android", "ru2").version}')

    tdesktop = store.register_tdesktop_pack(tdesktop_path)
    print(
        f'Loaded tdesktop/ru: {tdesktop.strings_count} strings, '
        f'server version {tdesktop.version}')

    ios_count = _import_ios(store, ios_glob)
    print(f'Loaded {ios_count} iOS langpack file(s)')
    print('Restart server.py if it is already running.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
