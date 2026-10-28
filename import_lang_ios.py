#!/usr/bin/env python3
"""Import iOS .strings langpack into mytelegram-python.

Copies the file into the project root as ios_<lang>_v<version>.strings
and loads it into the in-memory langpack store.

Examples:
  python import_lang_ios.py C:\\Users\\imfas\\Downloads\\ios_en_v21501099.strings
  python import_lang_ios.py ios_ru_v21502454.strings --lang ru
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from handlers.langpack import IOS_LANGPACK_NAME_RE, get_store

DEFAULT_NAME_RE = re.compile(r'^ios_([a-z0-9]+)_v(\d+)\.strings$', re.I)


def _target_name(src_name: str, lang: str | None, version: int | None) -> str:
    match = DEFAULT_NAME_RE.match(src_name)
    if match:
        return src_name
    if not lang:
        raise ValueError(
            f'filename must look like ios_en_v21501099.strings, got {src_name!r}; '
            'use --lang and optionally --version')
    ver = version if version is not None else 1
    return f'ios_{lang.lower()}_v{ver}.strings'


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Import iOS .strings langpack file into mytelegram-python')
    parser.add_argument('file', help='Path to ios_XX_vNNNN.strings file')
    parser.add_argument('--lang', help='Language code if filename is non-standard')
    parser.add_argument('--version', type=int,
                        help='Pack version if filename is non-standard')
    parser.add_argument('--dry-run', action='store_true',
                        help='Validate only, do not copy or load')
    args = parser.parse_args()

    src = os.path.abspath(args.file)
    if not os.path.isfile(src):
        print(f'Error: file not found: {src}', file=sys.stderr)
        return 1

    try:
        dest_name = _target_name(os.path.basename(src), args.lang, args.version)
    except ValueError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1

    if not IOS_LANGPACK_NAME_RE.match(dest_name):
        print(f'Error: invalid target name: {dest_name}', file=sys.stderr)
        return 1

    dest = os.path.join(ROOT, dest_name)
    match = IOS_LANGPACK_NAME_RE.match(dest_name)
    lang_code = match.group(1).lower()
    base_version = int(match.group(2))

    if args.dry_run:
        print(f'Would import {src}')
        print(f'  -> {dest}')
        print(f'  lang={lang_code} base_version={base_version}')
        return 0

    if os.path.abspath(src) != dest:
        shutil.copy2(src, dest)
        print(f'Copied to {dest}')
    else:
        print(f'Using existing file {dest}')

    store = get_store()
    pack = store.register_ios_pack(dest)
    print(
        f'Loaded ios/{lang_code}: {pack.strings_count} strings, '
        f'server version {pack.version} (base v{base_version})')
    print('Restart server.py if it is already running.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
