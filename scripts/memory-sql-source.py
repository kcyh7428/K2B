#!/usr/bin/env python3
"""Inspect or stage one native SQL recovery turn, without model extraction."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
import codex_sql_memory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['scan', 'stage'])
    parser.add_argument('--codex-root', required=True, type=Path)
    parser.add_argument('--state-root', required=True, type=Path)
    parser.add_argument('--writer-role', required=True, choices=['home', 'sjm-source-only'])
    parser.add_argument('--since', required=True)
    parser.add_argument('--through', required=True)
    parser.add_argument('--session-id')
    parser.add_argument('--turn-id')
    parser.add_argument('--max-scan-seconds', type=float, default=60)
    args = parser.parse_args()
    kwargs = {name: getattr(args, name) for name in ['codex_root', 'state_root', 'writer_role', 'since', 'through']}
    kwargs['max_scan_seconds'] = args.max_scan_seconds
    try:
        if args.action == 'stage':
            if not args.session_id or not args.turn_id:
                parser.error('stage requires --session-id and --turn-id')
            result = codex_sql_memory.stage(**kwargs, session_id=args.session_id, turn_id=args.turn_id)
        else:
            result = codex_sql_memory.scan(**kwargs)
    except codex_sql_memory.SQLScanBudgetExceeded:
        print(json.dumps({'status': 'needs_attention', 'complete': False, 'diagnostic': 'scan_budget_exceeded'}))
        return 3
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, sqlite3.Error) as exc:
        # No dialogue, native JSON, SQL statement or credentials in diagnostics.
        print(json.dumps({'status': 'failed', 'diagnostic': type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['status'] in {'ok', 'ready'} else 3


if __name__ == '__main__':
    raise SystemExit(main())
