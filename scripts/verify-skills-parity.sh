#!/usr/bin/env bash
# verify-skills-parity.sh -- compatibility name for the live Codex skill guard.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'USAGE'
Usage: scripts/verify-skills-parity.sh [--root PATH]

Checks the sole live K2B instruction and skill surfaces:
  - AGENTS.md, .codex/hooks.json, and .agents/skills exist
  - retired CLAUDE.md and .claude project state are absent
  - every K2B skill has valid frontmatter whose name matches its directory
  - live skills do not reference retired Claude or stale Codex skill paths
  - live skills do not describe retired MiniMax or Kimi K2.6 as a live worker
  - the live ship skill and ship-brief template retain their safety contracts
USAGE
      exit 0
      ;;
    *)
      echo "verify-skills-parity: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

PYTHON_BIN="${K2B_PYTHON:-python3}"
if ! "$PYTHON_BIN" -c 'import yaml' >/dev/null 2>&1; then
  PROJECT_PYTHON="$HOME/Projects/K2B/venv/washing-machine/bin/python"
  if [ -x "$PROJECT_PYTHON" ] && "$PROJECT_PYTHON" -c 'import yaml' >/dev/null 2>&1; then
    PYTHON_BIN="$PROJECT_PYTHON"
  else
    echo "verify-skills-parity: PyYAML is required; install requirements-dev.txt" >&2
    exit 2
  fi
fi

"$PYTHON_BIN" - "$ROOT" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
agents_dir = root / ".agents" / "skills"
errors: list[str] = []

STALE_CODEX_SKILL_RE = re.compile(r"(?i)(?:^|[~./\\`\"']|\s)\.?codex[/\\]skills")
STALE_CLAUDE_SKILL_RE = re.compile(r"(?i)(?:^|[~./\\`\"']|\s)\.?claude[/\\]skills")
STALE_CLAUDE_MEMORY_RE = re.compile(r"(?i)~[/\\]\.claude[/\\]projects[/\\][^\s`\"']*[/\\]memory")
MINIMAX_M2_RE = re.compile(r"(?i)(?:minimax(?:[- ]?m2\.7| model m2\.7)|\bm2\.7\b)")
KIMI_K2_6_RE = re.compile(r"(?i)\bkimi(?:\s+k2)?\.?6\b|\bk2\.6\b")
LIVE_WORKER_RE = re.compile(
    r"(?i)(live|primary|current|active|main|default|preferred|chosen|worker|"
    r"handles text|text worker|text routing|text calls|powered by|uses|utilizes|"
    r"employs|relies on|routes to|delegates|backup|secondary|standby|fallback)"
)
NEGATION_TOKENS = (
    "historical", "dead", "disabled", "removed", "legacy", "retired",
    "previous", "no longer", "do not", "don't", "never", "not ",
)
SHIP_BRIEF_MARKERS = (
    "What you will notice",
    "What stays the same",
    "What is not included yet",
    "What to do now",
    "Under the hood",
    "Risk / rollback",
)
AGENTS_MARKERS = (
    "## Review and delivery",
    "Plain implementation requests do not authorize",
    "Same-family fallback does not count",
    "Commit/push and activation are distinct states",
)
SHIP_MARKERS = (
    "Plain implementation wording is not delivery authority",
    "explicit delivery wording",
    "same-family review presented as independent",
    "Activation on each Mac is a separate state",
)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"read error: {path}: {exc}")
        return ""


def frontmatter(path: Path, text: str) -> dict[str, object]:
    if not text.startswith("---\n"):
        errors.append(f"frontmatter parse error in {path}: missing opening delimiter")
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        errors.append(f"frontmatter parse error in {path}: missing closing delimiter")
        return {}
    try:
        parsed = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        errors.append(f"frontmatter parse error in {path}: {exc}")
        return {}
    if not isinstance(parsed, dict):
        errors.append(f"frontmatter parse error in {path}: expected mapping")
        return {}
    return {str(key): value for key, value in parsed.items()}


def require_markers(path: Path, text: str, markers: tuple[str, ...]) -> None:
    for marker in markers:
        if marker not in text:
            errors.append(f"missing required marker in {path}: {marker}")


agents_file = root / "AGENTS.md"
hooks_file = root / ".codex" / "hooks.json"
brief_file = root / "plans" / "templates" / "ship-brief.md"

for required in (agents_file, hooks_file, brief_file):
    if not required.is_file():
        errors.append(f"missing live K2B surface: {required}")
if not agents_dir.is_dir():
    errors.append(f"missing live skills directory: {agents_dir}")
if (root / "CLAUDE.md").exists():
    errors.append(f"retired Claude instruction file present: {root / 'CLAUDE.md'}")
if (root / ".claude").exists():
    errors.append(f"retired Claude project tree present: {root / '.claude'}")

if agents_file.is_file():
    require_markers(agents_file, read(agents_file), AGENTS_MARKERS)
if brief_file.is_file():
    require_markers(brief_file, read(brief_file), SHIP_BRIEF_MARKERS)

if agents_dir.is_dir():
    skill_files = sorted(agents_dir.glob("k2b-*/SKILL.md"))
    if not skill_files:
        errors.append(f"no live K2B skills found under: {agents_dir}")
    for skill_file in skill_files:
        text = read(skill_file)
        fm = frontmatter(skill_file, text)
        expected_name = skill_file.parent.name
        if fm.get("name") != expected_name:
            errors.append(
                f"frontmatter name mismatch in {skill_file}: "
                f"expected={expected_name!r} actual={fm.get('name')!r}"
            )
        if not isinstance(fm.get("description"), str) or not fm.get("description", "").strip():
            errors.append(f"frontmatter description missing in {skill_file}")

    for path in sorted(p for p in agents_dir.rglob("*") if p.is_file()):
        text = read(path)
        for regex, label in (
            (STALE_CODEX_SKILL_RE, "stale Codex skills path"),
            (STALE_CLAUDE_SKILL_RE, "retired Claude skills path"),
            (STALE_CLAUDE_MEMORY_RE, "retired Claude memory path"),
        ):
            match = regex.search(text)
            if match:
                errors.append(f"{label} in {path}: {match.group(0)}")
        if "CLAUDE_PROJECT_DIR" in text:
            errors.append(f"retired CLAUDE_PROJECT_DIR reference in {path}")
        for lineno, line in enumerate(text.splitlines(), start=1):
            lowered = line.lower()
            if any(token in lowered for token in NEGATION_TOKENS):
                continue
            if LIVE_WORKER_RE.search(line) and (MINIMAX_M2_RE.search(line) or KIMI_K2_6_RE.search(line)):
                errors.append(f"retired live-worker wording in {path}:{lineno}: {line.strip()}")

ship_skill = agents_dir / "k2b-ship" / "SKILL.md"
if not ship_skill.is_file():
    errors.append(f"missing live ship skill: {ship_skill}")
else:
    require_markers(ship_skill, read(ship_skill), SHIP_MARKERS)

if errors:
    print("verify-skills-parity: FAILED")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

print("verify-skills-parity: ok")
PY
