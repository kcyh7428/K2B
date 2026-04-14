#!/usr/bin/env python3
"""Defensive parser for NotebookLM "describe each source" JSON output.

Used by the k2b-research `/research videos` skill (Step 6a) to normalize the
raw string NotebookLM returns into a clean array of entries the K2B-as-judge
rubric can reason about. Encapsulates three quirks this pipeline has hit in
production and that Claude should not have to reinvent inline on every run:

1. Citation markers like `[44, 47]`, `[1, 2, 3]`, or `[1-4]` appear inline in
   the raw text. They sometimes land inside string literals (harmless but ugly)
   and sometimes between JSON structure elements, where they break json.loads.
   Stripped everywhere before parsing. The regex handles both comma-separated
   lists and dash ranges (the first-run incident on 2026-04-15 tripped on
   `[1-4]`, `[5-8]` etc because the old regex was comma-only).

2. Long string values like `what_it_covers` sometimes contain literal newlines
   inside the string literal. json.loads rejects unescaped control characters
   inside strings. Normalized to spaces via a character walker that tracks
   whether we are currently inside a string literal (so structural newlines
   between JSON elements are preserved).

3. The `url` field in NBLM output is frequently a synthetic placeholder like
   `https://www.youtube.com/watch?v=ChaseAI_Skills`. NBLM reads transcripts but
   cannot see upload metadata. Each parsed entry is rejoined to the candidate
   list (from yt-search) by matching on the normalized title. Real values for
   `real_url`, `real_title`, `real_channel`, `real_duration`, `real_published`,
   and `video_id` always come from the candidates file, never from NBLM.

Usage:
  parse-nblm.py <nblm-raw-file> <candidates-json-file>

Output (stdout):
  JSON array of normalized entries, one per NBLM entry. Each entry has:
    - NBLM-sourced: what_it_covers, style, level, concrete_examples,
      key_speakers_or_companies (passed through unchanged)
    - Candidate-sourced: real_url, real_title, real_channel, real_duration,
      real_published, video_id (from the matched yt-search result)
    - Meta: identity_resolved (bool), match_method ("title-exact" | "failed")

  Entries where identity resolution failed still appear in the output with
  identity_resolved=false; the caller (Step 6a) is responsible for sorting
  those into `rejects` with reason="identity resolution failed".

Exit codes:
  0 - parse succeeded (with or without resolution failures)
  1 - parse failed (json.loads error after all defensive passes)
  2 - usage error (missing/bad args, files not readable, non-array output)
"""

import json
import re
import sys
from pathlib import Path


CITATION_RE = re.compile(r"\s*\[\d+(?:[-,]\s*\d+)*\]")


def strip_citations(text: str) -> str:
    return CITATION_RE.sub("", text)


def normalize_newlines_in_strings(text: str) -> str:
    """Replace literal \\n and \\r inside JSON string literals with spaces.

    A character walker that tracks whether we are inside a `"..."` literal and
    honors backslash escapes so we do not get confused by escaped quotes. Only
    modifies bytes inside string literals; structural whitespace between JSON
    elements is preserved.
    """
    out = []
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ch in ("\n", "\r"):
            out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


def extract_json_array(text: str) -> str:
    """Return the first top-level JSON array that contains at least one object.

    Walks the entire text from position 0 tracking string-literal state so
    brackets inside `"..."` literals are ignored. When a balanced `[...]` group
    closes at top level, the group is returned only if it contains `{` (i.e.
    looks like a JSON array of objects). Groups without `{` are skipped, which
    handles NBLM responses that wrap the real JSON with prose containing
    incidental `[like this]` bracket structures.

    Nested arrays inside `"key_speakers_or_companies": ["Alice"]` are handled
    correctly by the depth counter since they are only entered after the top-
    level array has been opened.
    """
    in_string = False
    escaped = False
    start = -1
    depth = 0
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            if depth == 0:
                continue
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                if "{" in candidate:
                    return candidate
                start = -1
    raise ValueError("no top-level JSON array with object entries found in NBLM raw output")


def normalize_title(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def build_candidate_index(candidates: list) -> dict:
    return {
        normalize_title(c.get("title", "")): c
        for c in candidates
        if c.get("title")
    }


def normalize_published_to_iso(published: str) -> str:
    """yt-search returns ISO-8601 like '2026-04-01T12:34:56Z'. Take the first
    10 chars if they look like a date, otherwise return 'unknown'."""
    if not published:
        return "unknown"
    m = re.match(r"^\d{4}-\d{2}-\d{2}", published)
    return m.group(0) if m else "unknown"


def rejoin_entry(nblm_entry: dict, cand_index: dict) -> dict:
    nblm_title = nblm_entry.get("title", "") or ""
    cand = cand_index.get(normalize_title(nblm_title))
    if cand:
        merged = dict(nblm_entry)
        merged.update(
            {
                "real_url": cand.get("url", ""),
                "real_title": cand.get("title", ""),
                "real_channel": cand.get("channel", ""),
                "real_duration": cand.get("duration", ""),
                "real_published": normalize_published_to_iso(cand.get("published", "")),
                "video_id": cand.get("video_id", ""),
                "identity_resolved": True,
                "match_method": "title-exact",
            }
        )
        return merged
    merged = dict(nblm_entry)
    merged.update(
        {
            "real_url": nblm_entry.get("url", ""),
            "real_title": nblm_entry.get("title", ""),
            "real_channel": nblm_entry.get("channel", ""),
            "real_duration": nblm_entry.get("duration", ""),
            "real_published": "unknown",
            "video_id": "",
            "identity_resolved": False,
            "match_method": "failed",
        }
    )
    return merged


def parse_nblm(raw: str) -> list:
    """Run all defensive passes and json.loads. Raises json.JSONDecodeError or
    ValueError on final failure."""
    text = strip_citations(raw)
    text = extract_json_array(text)
    text = normalize_newlines_in_strings(text)
    return json.loads(text)


def main():
    if len(sys.argv) != 3:
        print(
            "Usage: parse-nblm.py <nblm-raw-file> <candidates-json-file>",
            file=sys.stderr,
        )
        sys.exit(2)

    raw_path = Path(sys.argv[1])
    cand_path = Path(sys.argv[2])

    if not raw_path.is_file():
        print(f"ERROR: NBLM raw file not found: {raw_path}", file=sys.stderr)
        sys.exit(2)
    if not cand_path.is_file():
        print(f"ERROR: candidates file not found: {cand_path}", file=sys.stderr)
        sys.exit(2)

    try:
        raw = raw_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"ERROR: reading {raw_path}: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        cand_data = json.loads(cand_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: reading/parsing candidates {cand_path}: {e}", file=sys.stderr)
        sys.exit(2)

    candidates = cand_data.get("results", [])
    if not isinstance(candidates, list):
        print(
            f"ERROR: candidates file has no 'results' array (got {type(candidates).__name__})",
            file=sys.stderr,
        )
        sys.exit(2)
    cand_index = build_candidate_index(candidates)

    try:
        nblm_entries = parse_nblm(raw)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"ERROR: NBLM JSON parse failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(nblm_entries, list):
        print(
            f"ERROR: NBLM output was not a JSON array (got {type(nblm_entries).__name__})",
            file=sys.stderr,
        )
        sys.exit(1)

    rejoined = [rejoin_entry(entry, cand_index) for entry in nblm_entries if isinstance(entry, dict)]

    print(json.dumps(rejoined, indent=2))


if __name__ == "__main__":
    main()
