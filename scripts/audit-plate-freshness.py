#!/usr/bin/env python3
"""Audit source notes that feed /plate for stale shipped/current drift."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def vault_path(env_name: str, default_suffix: str) -> Path:
    return Path(os.environ.get(env_name, str(Path.home() / "Projects" / default_suffix))).expanduser()


def empty_env_paths(*env_names: str) -> list[str]:
    return [env_name for env_name in env_names if env_name in os.environ and os.environ[env_name] == ""]


def split_markdown_row(line: str) -> list[str]:
    cells: list[str] = []
    buf: list[str] = []
    wiki_depth = 0
    square_depth = 0
    paren_depth = 0
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            i += 1
            continue
        pair = line[i : i + 2]
        if pair == "[[":
            wiki_depth += 1
            buf.append(pair)
            i += 2
            continue
        if pair == "]]":
            wiki_depth = max(0, wiki_depth - 1)
            buf.append(pair)
            i += 2
            continue

        if wiki_depth == 0:
            if ch == "[":
                square_depth += 1
            elif ch == "]" and square_depth:
                square_depth -= 1
            elif ch == "(" and square_depth == 0 and buf and buf[-1] == "]":
                paren_depth += 1
            elif ch == ")" and paren_depth:
                paren_depth -= 1

        if ch == "|" and wiki_depth == 0 and square_depth == 0 and paren_depth == 0:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf).strip())
    if cells and cells[0] == "":
        cells = cells[1:]
    if cells and cells[-1] == "":
        cells = cells[:-1]
    return cells


def wikilink_feature_slug(cell: str) -> str:
    match = re.search(r"\[\[([^]]+)\]\]", cell)
    if not match:
        return cell.strip()

    raw = match.group(1)
    buf: list[str] = []
    target = raw
    alias = ""
    escape = False
    for index, ch in enumerate(raw):
        if escape:
            buf.append(ch)
            escape = False
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            continue
        if ch == "|":
            target = "".join(buf).strip()
            alias = raw[index + 1 :].replace("\\|", "|").strip()
            break
        buf.append(ch)
    target_slug = target.rsplit("/", 1)[-1].strip()
    if re.fullmatch(r"feature_[a-z0-9-]+", alias):
        return alias
    if re.fullmatch(r"feature_[a-z0-9-]+", target_slug):
        return target_slug
    return ""


def shipped_features(concepts_index: Path) -> set[str]:
    shipped: set[str] = set()
    in_shipped = False
    for line in concepts_index.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("## Shipped"):
            in_shipped = True
            continue
        if in_shipped and line.startswith("## "):
            break
        if not in_shipped or not line.startswith("|"):
            continue
        parts = split_markdown_row(line)
        first_cell = parts[0] if parts else ""
        slug = wikilink_feature_slug(first_cell)
        if re.fullmatch(r"feature_[a-z0-9-]+", slug):
            shipped.add(slug)
    return shipped


def stale_keyword_pattern(feature: str) -> re.Pattern[str]:
    escaped = re.escape(feature)
    return re.compile(
        rf"(current|next|pending|next up|active next|follow-up)[^.\n|]{{0,160}}{escaped}|"
        rf"{escaped}[^.\n|]{{0,160}}(current|next|pending|next up|active next)",
        re.IGNORECASE,
    )


def stale_a5_alias_patterns() -> list[re.Pattern[str]]:
    # Temporary compatibility for the A5 wording that caused the 2026-06-21
    # stale plate incident. Generic exact-slug matching catches future cases
    # where current/next prose names the shipped feature slug directly.
    return [
        re.compile(pattern, re.IGNORECASE)
        for pattern in [
            r"A5 DEPLOY-GATE NEXT",
            r"Active next work is K2B-side A5 deploy-to-engine gate",
            r"active orchestrator follow-up .* A5 deploy-to-engine gate",
            r"CURRENT -- K2B A5 deploy-to-engine gate",
            r"K2B A5 deploy-to-engine gate: promoted to Next Up",
        ]
    ]


def relevant_lines(path: Path, lines: list[str]) -> list[tuple[int, str]]:
    # These are active-summary boundaries for the two source notes that feed
    # /plate. Historical update sections below these headings are intentionally
    # ignored so old "pending/current" prose does not block today's ship.
    relevant: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        if path.name == "index.md" and line.startswith("> **Archived predecessors.**"):
            break
        if path.name == "feature_k2b-orchestrator.md" and line.startswith("## Why v2 is mostly orchestration"):
            break
        relevant.append((line_number, line))
    return relevant


def stale_findings(k2b_vault: Path, k2bi_vault: Path, include_k2bi_planning: bool) -> list[str]:
    shipped = shipped_features(k2b_vault / "wiki" / "concepts" / "index.md")
    if not shipped:
        return []

    tracked_paths = [
        (k2b_vault / "wiki" / "concepts" / "feature_k2b-orchestrator.md", "orchestrator tracker"),
    ]
    if include_k2bi_planning:
        tracked_paths.append((k2bi_vault / "wiki" / "planning" / "index.md", "K2Bi Resume Card"))

    generic_patterns = {feature: stale_keyword_pattern(feature) for feature in shipped}
    a5_shipped = "feature_orchestrator-deploy-gate" in shipped
    a5_patterns = stale_a5_alias_patterns() if a5_shipped else []

    findings: list[str] = []
    for path, label in tracked_paths:
        if not path.exists():
            findings.append(f"{path}: {label} expected source missing before scan")
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            findings.append(f"{path}: {label} expected source missing before scan")
            continue
        for line_number, line in relevant_lines(path, lines):
            for feature, pattern in generic_patterns.items():
                if pattern.search(line):
                    findings.append(
                        f"{path}:{line_number}: {label} still names shipped {feature} as current/next work"
                    )
            if a5_patterns and any(pattern.search(line) for pattern in a5_patterns):
                findings.append(f"{path}:{line_number}: {label} still names shipped A5 as current/next work")
    return findings


ALLOW_MISSING_K2BI_ENV = "K2B_PLATE_ALLOW_MISSING_K2BI_VAULT"


def missing_k2bi_planning_allowed() -> bool:
    if "K2BI_VAULT_PATH" in os.environ:
        return False
    # The Mac Mini K2B runtime does not host K2Bi-Vault. Missing K2Bi planning
    # is allowed only when the caller explicitly opts into that incomplete
    # scan; local MacBook and explicit override paths fail closed.
    return os.environ.get(ALLOW_MISSING_K2BI_ENV) == "1"


def main() -> int:
    empty_paths = empty_env_paths("K2B_VAULT_PATH", "K2BI_VAULT_PATH")
    if empty_paths:
        print("plate-freshness audit failed: required source file missing")
        for env_name in empty_paths:
            print(f"- {env_name} is set but empty")
        return 2

    k2b_vault = vault_path("K2B_VAULT_PATH", "K2B-Vault")
    k2bi_vault = vault_path("K2BI_VAULT_PATH", "K2Bi-Vault")
    k2bi_planning = k2bi_vault / "wiki" / "planning" / "index.md"
    required_files = [
        k2b_vault / "wiki" / "concepts" / "index.md",
        k2b_vault / "wiki" / "concepts" / "feature_k2b-orchestrator.md",
    ]
    optional_missing: list[Path] = []
    if not k2bi_planning.exists():
        if missing_k2bi_planning_allowed():
            optional_missing.append(k2bi_planning)
        else:
            required_files.append(k2bi_planning)
    missing = [path for path in required_files if not path.exists()]
    if missing:
        print("plate-freshness audit failed: required source file missing")
        for path in missing:
            print(f"- {path}")
        return 2
    for path in optional_missing:
        print(
            "plate-freshness audit warning: optional source file missing; "
            f"K2Bi Resume Card stale check skipped: {path}"
        )
    shipped = shipped_features(k2b_vault / "wiki" / "concepts" / "index.md")
    if not shipped:
        print("plate-freshness audit failed: no shipped feature rows found in concepts index")
        return 2
    findings = stale_findings(k2b_vault, k2bi_vault, include_k2bi_planning=not optional_missing)
    if findings:
        print("plate-freshness audit failed:")
        for finding in findings:
            print(f"- {finding}")
        return 1

    print("plate-freshness audit passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
