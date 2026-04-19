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


def _run_git(*args: str, cwd: Path) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=str(cwd), text=True, errors="replace"
    )


def gather_tree_state(repo_root: str | Path) -> dict:
    """Return the current working-tree state for classification.

    Uses `git status --porcelain -z` to correctly handle paths with spaces,
    renames, and unusual characters. Merges staged and unstaged numstat so
    classification matches exactly what the review would see.

    Returns:
      - files: list[str] -- paths with any working-tree change
      - statuses: dict[str, str] -- path -> "A"/"M"/"D"/"R"/"?"
      - total_loc: int -- insertions + deletions across tracked diffs, plus
        untracked-file line counts (binary files count as 0)
    """
    root = Path(repo_root)
    porcelain = _run_git("status", "--porcelain", "-z", cwd=root)

    files: list[str] = []
    statuses: dict[str, str] = {}

    # Porcelain collapses untracked directories to their name (e.g. `.claude/`)
    # in default mode. `git status -uall` would expand them but is banned by
    # CLAUDE.md (memory issues on large repos). Instead we parse porcelain for
    # tracked changes and use `git ls-files --others --exclude-standard -z`
    # to enumerate every untracked FILE individually below.
    records = porcelain.split("\x00")
    i = 0
    while i < len(records):
        rec = records[i]
        if not rec:
            i += 1
            continue
        if len(rec) < 3:
            i += 1
            continue
        idx, wt = rec[0], rec[1]
        path = rec[3:]  # skip "XY "
        if idx == "R" or wt == "R":
            # Current record holds the NEW path; next record is the OLD path.
            status = "R"
            i += 1  # consume the old-path token that follows
        elif idx == "?" and wt == "?":
            # Skip untracked records from porcelain -- they collapse
            # directories. Re-emit individual files via ls-files below.
            i += 1
            continue
        elif "A" in (idx, wt):
            status = "A"
        elif "D" in (idx, wt):
            status = "D"
        elif "M" in (idx, wt):
            status = "M"
        else:
            status = "?"
        files.append(path)
        statuses[path] = status
        i += 1

    # Enumerate untracked files individually (porcelain collapsed them above).
    try:
        untracked_z = _run_git(
            "ls-files", "--others", "--exclude-standard", "-z", cwd=root
        )
    except subprocess.CalledProcessError:
        untracked_z = ""
    for path in untracked_z.split("\x00"):
        if not path:
            continue
        files.append(path)
        statuses[path] = "A"

    # LOC: combine unstaged + staged numstat. Untracked files are not in
    # either; count them separately by reading line count.
    total_loc = 0
    tracked_loc_seen: set[str] = set()
    for diff_args in (("diff", "--numstat"), ("diff", "--cached", "--numstat")):
        try:
            numstat = _run_git(*diff_args, cwd=root)
        except subprocess.CalledProcessError:
            continue
        for line in numstat.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            ins, dels, path = parts
            if ins == "-" and dels == "-":
                tracked_loc_seen.add(path)  # binary, 0 LOC
                continue
            try:
                total_loc += int(ins) + int(dels)
                tracked_loc_seen.add(path)
            except ValueError:
                continue

    for path, status in statuses.items():
        if status == "A" and path not in tracked_loc_seen:
            full_path = root / path
            if full_path.is_file():
                try:
                    with full_path.open("rb") as f:
                        total_loc += sum(1 for _ in f)
                except OSError:
                    pass

    return {"files": files, "statuses": statuses, "total_loc": total_loc}


TIER_0_PREFIXES = (
    "K2B-Vault/",   # fork/mono-repo portability (dead code in primary K2B)
    "plans/",
    ".claude/plans/",
)
TIER_0_EXACT = ("DEVLOG.md",)


def _is_tier_0_path(path: str) -> bool:
    if path in TIER_0_EXACT:
        return True
    return any(path.startswith(p) for p in TIER_0_PREFIXES)


def classify_tier(
    repo_root: str | Path,
    tier3_config_path: str | Path | None = None,
) -> tuple[int, str]:
    state = gather_tree_state(repo_root)
    files = state["files"]

    if not files:
        return 2, "no changes (classifier should not run here -- /ship step 1 handles this)"

    # Rule 1: Tier 0 -- all files vault/devlog/plans only
    if all(_is_tier_0_path(f) for f in files):
        return 0, f"tier-0: {len(files)} file(s), all vault/devlog/plans"

    # All other rules not yet implemented -- fall through to Tier 2 default
    return 2, "tier-2: default (rules 2-5 pending)"
