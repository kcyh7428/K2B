#!/usr/bin/env bash
# verify-skills-parity.sh -- guard Codex skill copies against stale drift.

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

Checks:
  - AGENTS.md and .codex/hooks.json exist
  - every .claude/skills/k2b-*/SKILL.md has .agents/skills/k2b-*/SKILL.md
  - frontmatter name and description match
  - .agents skills do not reference stale Codex skills paths
  - .agents skill files do not reference Claude-only skill paths, project memory, or CLAUDE_PROJECT_DIR
  - .agents skills do not describe MiniMax M2.7 as the live text worker
USAGE
      exit 0
      ;;
    *)
      echo "verify-skills-parity: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

python3 - "$ROOT" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1])
claude_dir = root / ".claude" / "skills"
agents_dir = root / ".agents" / "skills"

errors: list[str] = []
STALE_CODEX_SKILL_RE = re.compile(r"(?i)(?:^|[~./\\`\"']|\s)\.?codex[/\\]skills")
STALE_CLAUDE_SKILL_RE = re.compile(r"(?i)(?:^|[~./\\`\"']|\s)\.?claude[/\\]skills")
STALE_CLAUDE_MEMORY_RE = re.compile(r"(?i)~[/\\]\.claude[/\\]projects[/\\][^\s`\"']*[/\\]memory")
CLAUDE_CODEX_ONLY_PATH_RE = re.compile(r"(?i)(?:^|[~./\\`\"']|\s)(?:\.agents[/\\]skills|\.codex[/\\]hooks\.json)")
MINIMAX_M2_RE = re.compile(r"(?i)(?:minimax(?:[- ]?m2\.7| model m2\.7)|\bm2\.7\b)")
LIVE_WORKER_RE = re.compile(
    r"(?i)(live|primary|current|active|main|default|preferred|chosen|worker|handles text|text worker|text routing|text calls|powered by|uses|utilizes|employs|relies on|routes to|delegates|backup|secondary|standby|reserve|spare|contingency)"
)
MINIMAX_HISTORICAL_TOKENS = (
    "historical",
    "dead",
    "subscription",
    "lapsed",
    "old",
    "removed",
    "legacy",
    "retired",
)
MINIMAX_NEGATION_TOKENS = (
    "no longer",
    "do not",
    "don't",
    "never",
    "not ",
)
SHIP_BRIEF_MARKERS = (
    "Prepare Keith-facing ship brief",
    "What you will notice",
    "What stays the same",
    "What is not included yet",
    "What to do now",
    "Under the hood",
    "Risk / rollback",
)
AGENTS_SESSION_DISCIPLINE_MARKERS = (
    "## Session Discipline",
    "At the END of every K2B desktop session",
    "Plain implementation requests without delivery command wording are not ship authorization",
    "If shipping is blocked",
    "explicitly declines to ship",
)
SHIP_CONTRACT_MARKERS = (
    "Codex Desktop manual ship contract",
    "already authorized",
    "descriptive or architectural context",
    "If the wording is ambiguous",
    "first clause must be complete",
    "list the done checks implied by X",
    "concrete test command, file assertion, or live verification result",
    "cannot produce inspectable evidence",
    "Mixed-mode examples",
)


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"read error: {path}: {exc}")
        return ""


def check_agent_only_text(path: Path, text: str) -> None:
    stale_path_match = STALE_CODEX_SKILL_RE.search(text)
    if stale_path_match:
        errors.append(f"stale Codex skills path in {path}: {stale_path_match.group(0)}")
    stale_claude_match = STALE_CLAUDE_SKILL_RE.search(text)
    if stale_claude_match:
        errors.append(f"stale Claude skills path in {path}: {stale_claude_match.group(0)}")
    stale_claude_memory_match = STALE_CLAUDE_MEMORY_RE.search(text)
    if stale_claude_memory_match:
        errors.append(f"stale Claude project memory path in {path}: {stale_claude_memory_match.group(0)}")
    if "CLAUDE_PROJECT_DIR" in text:
        errors.append(f"Claude-only CLAUDE_PROJECT_DIR reference in {path}")


def check_ship_brief_markers(path: Path, text: str) -> None:
    for marker in SHIP_BRIEF_MARKERS:
        marker_re = re.escape(marker)
        pattern = re.compile(
            rf"(?im)^\s*(?:"
            rf"#{{1,6}}\s+(?:\d+(?:\.\d+)*\s+)?{marker_re}\s*$|"
            rf"\d+\.\s+\*\*{marker_re}\*\*"
            rf")"
        )
        if not pattern.search(text):
            errors.append(f"missing ship-brief marker in {path}: {marker}")


def check_ship_brief_contract(path: Path, text: str) -> None:
    if path.parent.name != "k2b-ship":
        return
    check_ship_brief_markers(path, text)
    for marker in SHIP_CONTRACT_MARKERS:
        if marker not in text:
            errors.append(f"missing Codex ship contract marker in {path}: {marker}")


def check_agents_session_discipline(path: Path, text: str) -> None:
    for marker in AGENTS_SESSION_DISCIPLINE_MARKERS:
        if marker not in text:
            errors.append(f"missing Codex session discipline marker in {path}: {marker}")


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
    if parsed is None:
        errors.append(f"frontmatter parse error in {path}: empty/null frontmatter")
        return {}
    if not isinstance(parsed, dict):
        errors.append(f"frontmatter parse error in {path}: expected mapping, got {type(parsed).__name__}")
        return {}
    return {str(key): value for key, value in parsed.items()}


if not claude_dir.is_dir():
    errors.append(f"missing source skills directory: {claude_dir}")
if not agents_dir.is_dir():
    errors.append(f"missing Codex skills directory: {agents_dir}")
if not (root / "AGENTS.md").is_file():
    errors.append(f"missing Codex instruction surface: {root / 'AGENTS.md'}")
else:
    check_agents_session_discipline(root / "AGENTS.md", read(root / "AGENTS.md"))
if not (root / ".codex" / "hooks.json").is_file():
    errors.append(f"missing Codex hook surface: {root / '.codex' / 'hooks.json'}")
ship_brief_template = root / "plans" / "templates" / "ship-brief.md"
if not ship_brief_template.is_file():
    errors.append(f"missing ship-brief template: {ship_brief_template}")
else:
    check_ship_brief_markers(ship_brief_template, read(ship_brief_template))

for source_skill in sorted(claude_dir.glob("k2b-*/SKILL.md")):
    rel = source_skill.relative_to(claude_dir)
    target_skill = agents_dir / rel
    if not target_skill.exists():
        errors.append(f"missing .agents skill for {rel}")
        continue

    source_text = read(source_skill)
    target_text = read(target_skill)
    claude_codex_only_match = CLAUDE_CODEX_ONLY_PATH_RE.search(source_text)
    if claude_codex_only_match:
        errors.append(f"Codex-only path in Claude skill {source_skill}: {claude_codex_only_match.group(0)}")

    source_fm = frontmatter(source_skill, source_text)
    target_fm = frontmatter(target_skill, target_text)
    for key in ("name", "description"):
        if key not in source_fm or key not in target_fm:
            errors.append(
                f"frontmatter missing in {rel}: {key} "
                f"source_has={key in source_fm} target_has={key in target_fm}"
            )
            continue
        if source_fm[key] != target_fm[key] or type(source_fm[key]) is not type(target_fm[key]):
            errors.append(
                f"frontmatter mismatch in {rel}: {key} "
                f"source={source_fm[key]!r} target={target_fm[key]!r}"
            )
    expected_name = source_skill.parent.name
    for side, skill_path, fm in (
        ("source", source_skill, source_fm),
        ("target", target_skill, target_fm),
    ):
        if "name" in fm and fm["name"] != expected_name:
            errors.append(
                f"frontmatter name does not match directory in {skill_path}: "
                f"expected={expected_name!r} actual={fm['name']!r} side={side}"
            )

    check_agent_only_text(target_skill, target_text)
    check_ship_brief_contract(source_skill, source_text)
    check_ship_brief_contract(target_skill, target_text)

    for lineno, line in enumerate(target_text.splitlines(), start=1):
        if not MINIMAX_M2_RE.search(line):
            continue
        lowered = line.lower()
        if any(token in lowered for token in MINIMAX_HISTORICAL_TOKENS):
            continue
        if any(token in lowered for token in MINIMAX_NEGATION_TOKENS):
            continue
        if LIVE_WORKER_RE.search(line):
            errors.append(f"MiniMax M2.7 live-worker wording in {target_skill}:{lineno}: {line.strip()}")

for target_skill in sorted(agents_dir.glob("k2b-*/SKILL.md")):
    rel = target_skill.relative_to(agents_dir)
    if not (claude_dir / rel).exists():
        errors.append(f"orphan .agents skill without .claude source: {rel}")

for source_file in sorted(claude_dir.rglob("*")):
    if not source_file.is_file() or source_file.name == "SKILL.md":
        continue
    rel = source_file.relative_to(claude_dir)
    if not (agents_dir / rel).exists():
        errors.append(f"missing .agents nested skill file for {rel}")

for target_file in sorted(agents_dir.rglob("*")):
    if not target_file.is_file() or target_file.name == "SKILL.md":
        continue
    rel = target_file.relative_to(agents_dir)
    if not (claude_dir / rel).exists():
        errors.append(f"orphan .agents nested skill file without .claude source: {rel}")
    check_agent_only_text(target_file, read(target_file))

if errors:
    print("verify-skills-parity: FAILED")
    for err in errors:
        print(f"- {err}")
    sys.exit(1)

print("verify-skills-parity: ok")
PY
