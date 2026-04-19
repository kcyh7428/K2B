#!/usr/bin/env python3
"""Adversarial review tier classifier.

Reads the current working-tree diff and emits one of tier 0, 1, 2, 3 on stdout,
for /ship step 3 routing. See feature_adversarial-review-tiering.

Exit codes:
  0 -- classification succeeded; stdout:
         tier: N
         reason: <text>
  1 -- classifier error (not in a git repo, missing default config,
       malformed tier3-paths.yml, etc.)

The classifier returns an error for missing default config. The caller
(/ship step 3a) treats exit 1 as "fall back to Tier 3" per the feature
spec's fail-safe rule.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from tier_detection import classify_tier  # noqa: E402


def _repo_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        print("ship-detect-tier: not in a git repository", file=sys.stderr)
        sys.exit(1)
    return Path(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=None,
        help=(
            "Path to tier3-paths.yml. Default: <repo>/scripts/tier3-paths.yml. "
            "Missing default = exit 1 (caller falls back to Tier 3)."
        ),
    )
    args = parser.parse_args()

    root = _repo_root()
    config = Path(args.config) if args.config else root / "scripts" / "tier3-paths.yml"

    try:
        tier, reason = classify_tier(repo_root=root, tier3_config_path=config)
    except Exception as exc:
        print(f"ship-detect-tier: classifier error: {exc}", file=sys.stderr)
        return 1

    print(f"tier: {tier}")
    print(f"reason: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
