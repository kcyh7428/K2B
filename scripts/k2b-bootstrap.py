#!/usr/bin/env python3
"""Print current K2B startup context without contacting providers or other Macs."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
from k2b_bootstrap import build_bootstrap, render_bootstrap


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path(os.environ.get("K2B_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))))
    parser.add_argument("--vault", type=Path, default=Path(os.environ.get("K2B_VAULT_PATH", str(Path.home() / "Projects/K2B-Vault"))))
    parser.add_argument("--state-root", type=Path, default=Path(os.environ.get("K2B_STATE_ROOT", str(Path.home() / ".local/state/k2b"))))
    parser.add_argument("--host", choices=["home", "sjm", "unknown"], help="diagnostic role only, does not grant writer authority")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()
    packet = build_bootstrap(args.project, args.vault, args.state_root, args.host)
    print(json.dumps(packet, ensure_ascii=False, indent=2) if args.format == "json" else render_bootstrap(packet), end="\n" if args.format == "json" else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
