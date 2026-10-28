#!/usr/bin/env python3
"""CLI bridge for gift/username rentals WebApp."""
from __future__ import annotations

import json
import os
import sys

# Ensure MTG root is on path when invoked as script
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({'ok': False, 'error': f'invalid json: {exc}'}))
        return 1

    action = str(payload.get('action') or '').strip()
    telegram_id = int(payload.get('telegram_id') or 0)
    data = payload.get('data') if isinstance(payload.get('data'), dict) else {}

    from rentals_webapp.service import handle_action

    try:
        result = handle_action(action, telegram_id, data)
    except Exception as exc:
        result = {'ok': False, 'error': str(exc)}

    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
