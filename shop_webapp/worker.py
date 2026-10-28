#!/usr/bin/env python3
"""Persistent shop worker — one Storage connection, JSON lines on stdin/stdout."""
from __future__ import annotations

import json
import os
import sys
import traceback

from shop_webapp import bridge
from shop_webapp.service import _ensure_tables, _open


def _warmup() -> None:
    storage = _open()
    _ensure_tables(storage)
    from shop_webapp.cases import ensure_cases_schema
    from shop_webapp.crash import ensure_crash_schema
    ensure_cases_schema(storage)
    ensure_crash_schema(storage)


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + '\n')
    sys.stdout.flush()


def main() -> None:
    _warmup()
    _write({'ready': True})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get('id')
            command = req.get('command')
            argv = req.get('args') or []
            if not command:
                raise ValueError('missing command')
            result = bridge.dispatch(command, argv)
            _write({'id': req_id, 'ok': True, 'result': result})
        except Exception as exc:
            payload = {
                'id': req_id,
                'ok': False,
                'error': str(exc),
            }
            if os.environ.get('SHOP_DEBUG') == '1':
                payload['trace'] = traceback.format_exc()
            _write(payload)


if __name__ == '__main__':
    main()
