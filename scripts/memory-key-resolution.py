#!/usr/bin/env python3
"""Plan or record an interactive Home key disambiguation, never reconcile it."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / 'lib'))
import automatic_memory
import automatic_memory_key_resolution as resolution


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'record', 'restore'])
    parser.add_argument('--state-root', type=Path, required=True)
    parser.add_argument('--writer-role', choices=['home'], required=True)
    parser.add_argument('--delivery-id')
    parser.add_argument('--key')
    parser.add_argument('--reason')
    parser.add_argument('--plan-json', type=Path)
    parser.add_argument('--expected-sha256')
    parser.add_argument('--memory-path', type=Path)
    parser.add_argument('--new-key')
    args = parser.parse_args()
    try:
        if args.action == 'plan':
            if not all([args.delivery_id, args.key, args.reason]):
                parser.error('plan requires delivery-id, key and reason')
            result = resolution.plan(args.state_root, args.delivery_id, args.key, args.reason, memory_path=args.memory_path,
                new_key=args.new_key)
            print(json.dumps({'plan': result, 'review_sha256': hashlib.sha256(
                automatic_memory._canonical_json(result)).hexdigest()}, sort_keys=True))
        elif args.action == 'record':
            if not args.plan_json or not args.expected_sha256:
                parser.error('record requires plan-json and expected-sha256')
            result = resolution.record(args.state_root, json.loads(args.plan_json.read_bytes()),
                expected_sha256=args.expected_sha256, writer_role=args.writer_role, memory_path=args.memory_path)
            print(json.dumps(result, sort_keys=True))
        else:
            if not args.delivery_id:
                parser.error('restore requires delivery-id')
            print(json.dumps(resolution.restore(args.state_root, args.delivery_id,
                writer_role=args.writer_role, memory_path=args.memory_path), sort_keys=True))
    except (OSError, ValueError, RuntimeError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'diagnostic': 'key_resolution_rejected'}))
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
