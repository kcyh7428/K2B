"""Tier detection for adversarial review routing.

classify_tier(repo_root, tier3_config_path) -> (tier: int, reason: str)

Reads git status + numstat from repo_root, applies first-match-wins rules,
returns (tier, reason). See feature_adversarial-review-tiering.md.

Rule order (first match wins):
  1. Tier 0 -- all files under K2B-Vault/, DEVLOG.md, plans/, .claude/plans/
  2. Tier 3 -- any file matches glob in tier3-paths.yml allowlist
  3. Tier 1 -- all files are .md under .claude/skills/, wiki/, CLAUDE.md, README.md
  4. Tier 3 -- >3 files OR >200 LOC (insertions + deletions)
  5. Tier 2 -- default (real code or tests within budget)

Note on K2B-Vault/ in Tier 0: K2B-Vault/ is a sibling directory, not tracked
by this repo. The rule exists for fork/mono-repo portability. In primary K2B
usage it is effectively dead code (vault-only changes never invoke /ship per
k2b-ship SKILL.md "When NOT to Use").
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path


def classify_tier(
    repo_root: str | Path,
    tier3_config_path: str | Path | None = None,
) -> tuple[int, str]:
    """Stub -- raises NotImplementedError until Task 2."""
    raise NotImplementedError("classify_tier stub -- see Task 2")
