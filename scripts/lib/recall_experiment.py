"""Provider-free, process-local retrieval comparisons, never a live index.

Descriptions are optional discovery hints bound to original note hashes. The
context manager replaces only the ranking boundary for an offline experiment,
then restores it. Packets still contain only exact original source passages.
Validate experimental packets inside the same context, against the same aids.
No assistant startup, capture job or normal CLI imports this module.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path

import k2b_context


def load_descriptions(payload: dict, vault: Path) -> dict[str, dict]:
    """Validate bounded hint structure and paths; bind bytes when ranked."""

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported description schema")
    rows = payload.get("sources")
    if not isinstance(rows, list) or len(rows) > 512:
        raise ValueError("description source bound exceeded")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "source_sha256", "summary", "questions"}:
            raise ValueError("invalid description fields")
        rel = row["path"]
        if not isinstance(rel, str):
            raise ValueError("invalid description path")
        try:
            path = k2b_context._bind_local_path(vault, rel, "description")
        except k2b_context.InvalidInput as exc:
            raise ValueError("unsafe description path") from exc
        if (not k2b_context._within_note_roots(rel) or path.name == "index.md"
                or k2b_context._is_excluded(rel) or not path.is_file()):
            raise ValueError("description must bind an ordinary source")
        if rel in result or not isinstance(row["source_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", row["source_sha256"]):
            raise ValueError("duplicate description or invalid source hash")
        if not isinstance(row["summary"], str) or not 1 <= len(row["summary"]) <= 1000:
            raise ValueError("description summary bound exceeded")
        questions = row["questions"]
        if (not isinstance(questions, list) or len(questions) > 8
                or any(not isinstance(q, str) or not 1 <= len(q) <= 400 for q in questions)):
            raise ValueError("description question bound exceeded")
        result[rel] = dict(row)
    return result


def _tokens(text: str) -> str:
    words = []
    for word in re.findall(r"[\u3400-\u9fff]+|[^\W_]+", text.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            words.extend(word[i:i+2] for i in range(max(1, len(word)-1)))
        else:
            words.append(word)
    return " ".join(words)


def _bm25(documents: list[dict], terms: list[str]) -> list[Path]:
    """SQLite FTS5 BM25 with weighted identity and bilingual pre-tokenization."""

    if not documents or not terms:
        return []
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE search USING fts5(identity, body, description)")
        connection.executemany("INSERT INTO search VALUES (?, ?, ?)", [
            (_tokens(" ".join([d["stem"], d["title"], d["aliases"]])),
             _tokens(d["text"]), _tokens(d.get("description", ""))) for d in documents
        ])
        query = " OR ".join('"' + _tokens(t).replace('"', '""') + '"' for t in terms if _tokens(t))
        if not query:
            return []
        rows = connection.execute(
            "SELECT rowid FROM search WHERE search MATCH ? ORDER BY bm25(search, 5.0, 1.0, 1.0), rowid", (query,)
        ).fetchall()
        ranked = [documents[row[0]-1]["path"] for row in rows]
        # Index-only aliases still discover sources, but index prose is never evidence.
        ranked.extend(d["path"] for d in documents if d["indexed"] and d["path"] not in ranked)
        return ranked
    finally:
        connection.close()


@contextmanager
def ranking_variant(context, vault: Path, *, method: str = "lexical", descriptions: dict | None = None):
    """Temporarily select an offline ranker without changing live configuration."""

    if method not in {"lexical", "bm25"}:
        raise ValueError("unsupported ranking method")
    original_rank = context._rank_note_documents
    original_candidates = context._candidate_note_files
    aids = descriptions or {}
    aid_matches: set[Path] = set()

    def rank(documents, terms):
        enriched = []
        aid_matches.clear()
        for document in documents:
            doc = dict(document)
            rel = doc["path"].relative_to(vault).as_posix()
            aid = aids.get(rel)
            if aid:
                # The sidecar binds original file bytes, while evidence quotes
                # use the normal reader's newline-normalized text convention.
                digest = context._sha256_file_bounded(doc["path"], context.MAX_SOURCE_FILE_BYTES)
                if digest != aid["source_sha256"]:
                    raise ValueError(f"stale description: {rel}")
                doc["description"] = aid["summary"] + " " + " ".join(aid["questions"])
                if context._matches(doc["description"], terms):
                    aid_matches.add(doc["path"])
            enriched.append(doc)
        if method == "bm25":
            return _bm25(enriched, terms)
        lexical = [{**d, "text": d["text"] + " " + d.get("description", "")} for d in enriched]
        return original_rank(lexical, terms)

    def candidates(*args, **kwargs):
        result = list(original_candidates(*args, **kwargs))
        result[-1] = result[-1] | aid_matches
        return tuple(result)

    context._rank_note_documents = rank
    context._candidate_note_files = candidates
    try:
        yield
    finally:
        context._rank_note_documents = original_rank
        context._candidate_note_files = original_candidates


def compare(baseline, repaired, vault: Path, cases: dict, *, descriptions: dict,
            packet_sink=None) -> dict:
    """Compare fixed inputs. Passage checks are diagnostics, not answer grades."""

    if not isinstance(cases, dict):
        raise ValueError("comparison cases must be grouped")
    case_ids = set()
    for rows in cases.values():
        if not isinstance(rows, list) or len(rows) > 200:
            raise ValueError("case count bound exceeded")
        for row in rows:
            if not isinstance(row, (list, tuple)) or not 3 <= len(row) <= 4:
                raise ValueError("invalid case row")
            case_id = row[0]
            if (not isinstance(case_id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", case_id)
                    or case_id in case_ids):
                raise ValueError("unsafe or duplicate case identifier")
            case_ids.add(case_id)
    results = []
    variants = [
        ("baseline", baseline, None, {}),
        ("repaired", repaired, "lexical", {}),
        ("bm25", repaired, "bm25", {}),
        ("repaired_summaries", repaired, "lexical", descriptions),
        ("bm25_summaries", repaired, "bm25", descriptions),
    ]
    for name, context, method, aids in variants:
        manager = ranking_variant(context, vault, method=method, descriptions=aids) if method else nullcontext()
        with manager:
            for group, rows in cases.items():
                for row in rows:
                    case, query, wanted, *tail = row
                    selected = tail[0] if group == "controls" and tail else None
                    markers = tail[0] if group == "heldout" and tail else None
                    started = time.monotonic()
                    packet = context.gather(query, vault, topic_note=selected, now="2026-10-25T12:00:00+08:00")
                    elapsed = time.monotonic() - started
                    paths = [s["relative_path"] for s in packet["sources"]]
                    expected_ids = {s["source_id"] for s in packet["sources"] if s["relative_path"] == wanted}
                    quotes = "\n".join(e["quote"] for e in packet["evidence"] if e["source_id"] in expected_ids).casefold()
                    # Empty semantic draft still verifies all source/locator/hash
                    # bindings. It does not claim a model answered the question.
                    draft = {"schema_version": 1, "packet_id": packet["packet_id"],
                             "topic_ref": (packet["topic"] or {}).get("note_ref"),
                             **{section: [] for section in ["goal", "state", "decisions", "commitments", "next_action", "open_questions", "correction_proposals"]}}
                    try:
                        context.validate_draft(packet, draft, vault_root=vault)
                        binding_valid = True
                    except context.ContextError:
                        binding_valid = False
                    result = {"variant": name, "group": group, "case": case,
                              "query": query, "expected_source": wanted,
                              "source_hit": wanted in paths if wanted else not packet["evidence"],
                              "reciprocal_rank": 1 / (paths.index(wanted) + 1) if wanted in paths else 0,
                              "passage_check": all(m.casefold() in quotes for m in markers) if markers else None,
                              "binding_valid": binding_valid, "packet_bytes": context._packet_bytes(packet),
                              "evidence_count": len(packet["evidence"]), "gather_seconds": elapsed,
                              "sources": paths, "status": packet["status"], "reasons": packet["reasons"]}
                    results.append(result)
                    if packet_sink:
                        packet_sink(name, case, packet)
    return {"schema_version": 1, "results": results,
            "limitations": ["Source inclusion and literal passage markers are not answer-accuracy scores.",
                            "Cold BM25 timings include building the temporary in-memory index.",
                            "Uniform extractive descriptions test the mechanism, not semantic paraphrase quality.",
                            "No capture, provider call, background index or live configuration change."]}
