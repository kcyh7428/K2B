#!/usr/bin/env python3
"""Collect bounded immutable-source and current-note context for a proposal."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import k2b_context
from raw_source_status import classify

MAX_RAW_BYTES = 48 * 1024
MAX_INDEX_BYTES = 4 * 1024
MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_NOTE_EXCERPT_BYTES = 12 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
INDEXES = ["wiki/index.md"] + ["wiki/" + name + "/index.md" for name in
    ("people", "projects", "work", "concepts", "insights", "reference")]


def bounded(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("context source must be a regular non-symlink file")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("context input exceeds its byte budget; select a bounded source manually")
    return data


def collect(vault: Path, raw_source: str, targets: list[str]) -> dict:
    vault = vault.expanduser().resolve()
    if ".." in Path(raw_source).parts:
        raise ValueError("raw source path may not traverse parents")
    raw = Path(raw_source).expanduser()
    raw = raw if raw.is_absolute() else vault / raw
    if k2b_context._symlinked_ancestor(vault, raw):
        raise ValueError("raw source crosses a symlink or vault boundary")
    try:
        relative = raw.relative_to(vault).as_posix()
    except ValueError as exc:
        raise ValueError("source must be inside the configured vault") from exc
    if not relative.startswith("raw/") or not relative.endswith(".md"):
        raise ValueError("source must be an immutable raw Markdown capture")
    data = bounded(raw, MAX_RAW_BYTES)
    text = data.decode("utf-8")
    status = classify(relative, text)
    if status["classification"] == "technical_record" or status["metadata_state"] == "unreadable":
        raise ValueError("source classification requires separate operator review")
    if status["legacy_disposition"] is not None:
        raise ValueError("source has a preserved skipped/deferred disposition")
    limitations = []
    if not targets:
        match = re.match(r"\A\ufeff?\s*---\r?\n(.*?)\r?\n---(?:\r?\n|$)", text, re.S)
        metadata = yaml.safe_load(match.group(1)) if match else {}
        related = metadata.get("related", []) if isinstance(metadata, dict) else []
        targets = [related] if isinstance(related, str) else related
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets):
            raise ValueError("raw related targets must be note-link strings")
    if len(targets) > 3:
        raise ValueError("select at most three current target notes")
    selected = []
    for target in targets:
        target = target.strip().removeprefix("[[").removesuffix("]]")
        ambiguous = []
        note = k2b_context._resolve_note_link(vault, target, ambiguous=ambiguous)
        if note is None:
            raise ValueError("target is missing, unsafe, protected or ambiguous")
        if note in selected:
            raise ValueError("target notes must be unique")
        selected.append(note)
    notes = []
    for note in selected:
        current = bounded(note, MAX_NOTE_BYTES)
        notes.append({"path": note.relative_to(vault).as_posix(),
            "expected_sha256": hashlib.sha256(current).hexdigest(),
            "excerpt": current[:MAX_NOTE_EXCERPT_BYTES].decode("utf-8", errors="ignore"),
            "truncated": len(current) > MAX_NOTE_EXCERPT_BYTES})
    indexes = []
    for name in INDEXES:
        path = vault / name
        if path.is_symlink() or k2b_context._symlinked_ancestor(vault, path):
            raise ValueError("index context crosses a symlink")
        if not path.is_file():
            limitations.append("index unavailable: " + name)
            continue
        with path.open("rb") as source:
            chunk = source.read(MAX_INDEX_BYTES + 1)
        indexes.append({"path": name, "excerpt": chunk[:MAX_INDEX_BYTES].decode("utf-8", errors="ignore"),
            "truncated": len(chunk) > MAX_INDEX_BYTES})
    packet = {"schema_version": 1, "scope": "interactive_proposal_only",
        "source": {"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "text": text},
        "current_notes": notes, "indexes": indexes, "candidate_only": not notes,
        "limitations": limitations}
    if len(json.dumps(packet, ensure_ascii=False).encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise ValueError("combined context exceeds its byte budget")
    return packet


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--vault", default=os.environ.get("K2B_VAULT_PATH") or
        os.environ.get("K2B_VAULT_ROOT") or str(Path.home() / "Projects/K2B-Vault"))
    args = parser.parse_args(argv)
    try:
        packet = collect(Path(args.vault), args.source, args.targets)
    except (OSError, ValueError, UnicodeError, yaml.YAMLError, RecursionError) as exc:
        print("compile-context: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(packet, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
