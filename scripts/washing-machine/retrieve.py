#!/usr/bin/env python3
"""Washing Machine hybrid retriever.

Reads the SQLite index built by ``embed-index.py`` and returns rows ranked by
reciprocal rank fusion across three signals:

    * cosine similarity on the sentence-transformer embedding
    * FTS5 BM25 on the row text
    * entity-link (intersection of query entities and row entities)

Weights default to ``alpha=0.5 beta=0.3 gamma=0.2`` and are env-overridable.

CLI::

    retrieve.py "query text" [--shelf semantic] [--k 10]

Emits a JSON array to stdout. Missing index, empty shelf, or no-signal query
return ``[]`` -- never an exception.

Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 2.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.shelf_rows import parse as parse_row  # noqa: E402

DEFAULT_VAULT = Path.home() / "Projects" / "K2B-Vault"

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
SLUG_RE = re.compile(r"\b[a-z][a-z0-9_]*_[A-Za-z0-9_\-]+\b")
NON_ALPHANUMERIC_RE = re.compile(r"[^0-9A-Za-z\u00C0-\uFFFF ]+", re.UNICODE)


def shelves_dir() -> Path:
    override = os.environ.get("K2B_SHELVES_DIR")
    if override:
        return Path(override)
    vault = Path(os.environ.get("K2B_VAULT", str(DEFAULT_VAULT)))
    return vault / "wiki" / "context" / "shelves"


def index_db_path() -> Path:
    override = os.environ.get("K2B_INDEX_DB")
    if override:
        return Path(override)
    return shelves_dir() / "index.db"


def embed_model_name() -> str:
    return os.environ.get("WM_EMBED_MODEL", "all-MiniLM-L6-v2")


def _fenv(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _ienv(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


ALPHA = _fenv("WM_RRF_ALPHA", 0.5)
BETA = _fenv("WM_RRF_BETA", 0.3)
GAMMA = _fenv("WM_RRF_GAMMA", 0.2)
RRF_K = _ienv("WM_RRF_K", 60)
COSINE_THRESHOLD = _fenv("WM_COSINE_THRESHOLD", 0.17)


def query_entities(query: str) -> Set[str]:
    ents: Set[str] = set()
    for m in WIKILINK_RE.finditer(query):
        ents.add(m.group(1).strip())
    for m in SLUG_RE.finditer(query):
        ents.add(m.group(0))
    return ents


def fts_tokens(query: str) -> List[str]:
    """Strip FTS operators, split on non-alphanumeric, drop empties."""
    cleaned = NON_ALPHANUMERIC_RE.sub(" ", query)
    return [t for t in cleaned.split() if t]


def bm25_ranks(conn: sqlite3.Connection, shelf: str, query: str) -> List[int]:
    tokens = fts_tokens(query)
    if not tokens:
        return []
    # OR semantics across tokens; quote each token to insulate FTS5 from
    # anything the tokeniser might consider an operator at match time.
    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    try:
        rows = conn.execute(
            "SELECT rows.id "
            "FROM rows_fts "
            "JOIN rows ON rows.id = rows_fts.rowid "
            "WHERE rows_fts MATCH ? AND rows.shelf = ? "
            "ORDER BY bm25(rows_fts) ASC",
            (fts_query, shelf),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [rid for (rid,) in rows]


def cosine_ranks(
    conn: sqlite3.Connection, model, shelf: str, query: str
) -> Tuple[List[int], Dict[int, float]]:
    """Return (row ids in descending cosine order, id -> cosine score).

    Corrupt or mismatched embedding blobs (wrong byte-length, shape mismatch
    vs. the query vector, schema drift from a prior model) are skipped with
    a stderr warning rather than aborting the whole query. Keeps the
    "never raise" contract the retriever advertises to callers even when
    the DB has bad state (Codex MEDIUM 2026-04-22).
    """
    import numpy as np

    rows = conn.execute(
        "SELECT id, embedding FROM rows WHERE shelf = ?", (shelf,)
    ).fetchall()
    if not rows:
        return [], {}

    qv = np.asarray(
        model.encode(query, normalize_embeddings=True, show_progress_bar=False),
        dtype="float32",
    )
    scored: List[Tuple[int, float]] = []
    for rid, blob in rows:
        try:
            v = np.frombuffer(blob, dtype="float32")
        except (TypeError, ValueError) as e:
            print(f"retrieve: skip row {rid}: decode failed: {e}", file=sys.stderr)
            continue
        if v.shape != qv.shape:
            print(
                f"retrieve: skip row {rid}: shape mismatch "
                f"(row={v.shape}, query={qv.shape})",
                file=sys.stderr,
            )
            continue
        scored.append((rid, float(np.dot(qv, v))))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [rid for rid, _ in scored], {rid: s for rid, s in scored}


def entity_ranks(
    conn: sqlite3.Connection, shelf: str, query_ents: Set[str]
) -> List[int]:
    if not query_ents:
        return []
    rows = conn.execute(
        "SELECT id, entities_json FROM rows WHERE shelf = ?", (shelf,)
    ).fetchall()
    overlaps: List[Tuple[int, int]] = []
    for rid, ents_json in rows:
        try:
            ents = set(json.loads(ents_json))
        except (TypeError, ValueError):
            ents = set()
        overlap = len(ents & query_ents)
        if overlap > 0:
            overlaps.append((rid, overlap))
    overlaps.sort(key=lambda x: x[1], reverse=True)
    return [rid for rid, _ in overlaps]


def rrf_fuse(
    rankings: Iterable[Tuple[List[int], float]], k: int = RRF_K
) -> Dict[int, float]:
    """Sum ``w / (k + rank + 1)`` contributions across weighted rankings."""
    scores: Dict[int, float] = {}
    for ranking, weight in rankings:
        for rank, rid in enumerate(ranking):
            scores[rid] = scores.get(rid, 0.0) + weight / (k + rank + 1)
    return scores


def slug_from_row_text(row_text: str) -> str:
    """Extract the slug field via the canonical shelf-row parser.

    Uses ``lib.shelf_rows.parse`` so escaped pipes in values (``\\|``) do
    not corrupt the field boundary -- a naive ``split(" | ")`` would split
    inside an attribute value and return the wrong field (Codex HIGH
    2026-04-22).
    """
    try:
        return parse_row(row_text).slug
    except ValueError:
        return ""


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="retrieve.py")
    ap.add_argument("query")
    ap.add_argument("--shelf", default="semantic")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else index_db_path()
    if not db_path.exists():
        print("[]")
        return 0

    conn = sqlite3.connect(str(db_path))

    # Cheap shelf-presence check: empty -> [] without loading the embed model.
    present = conn.execute(
        "SELECT 1 FROM rows WHERE shelf = ? LIMIT 1", (args.shelf,)
    ).fetchone()
    if not present:
        print("[]")
        conn.close()
        return 0

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"retrieve: sentence-transformers not importable: {e}", file=sys.stderr)
        conn.close()
        return 3

    model = SentenceTransformer(embed_model_name())

    cos_order, cos_scores = cosine_ranks(conn, model, args.shelf, args.query)
    bm25_order = bm25_ranks(conn, args.shelf, args.query)
    qents = query_entities(args.query)
    ent_order = entity_ranks(conn, args.shelf, qents)

    # Inclusion set: a row is eligible iff it clears at least one signal.
    #   * BM25 or entity hit: always eligible
    #   * cosine alone: only if the row's cosine clears COSINE_THRESHOLD
    eligible: Set[int] = set(bm25_order) | set(ent_order)
    for rid in cos_order:
        if cos_scores.get(rid, 0.0) >= COSINE_THRESHOLD:
            eligible.add(rid)

    if not eligible:
        print("[]")
        conn.close()
        return 0

    cos_filtered = [rid for rid in cos_order if rid in eligible]
    bm25_filtered = [rid for rid in bm25_order if rid in eligible]
    ent_filtered = [rid for rid in ent_order if rid in eligible]

    scores = rrf_fuse(
        [(cos_filtered, ALPHA), (bm25_filtered, BETA), (ent_filtered, GAMMA)]
    )

    ordered = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[: args.k]

    results = []
    for rid, score in ordered:
        row = conn.execute(
            "SELECT shelf, row_text, entities_json FROM rows WHERE id = ?",
            (rid,),
        ).fetchone()
        if row is None:
            continue
        shelf, row_text, ents_json = row
        try:
            ents = json.loads(ents_json)
        except (TypeError, ValueError):
            ents = []
        results.append(
            {
                "id": rid,
                "shelf": shelf,
                "slug": slug_from_row_text(row_text),
                "row_text": row_text,
                "entities": ents,
                "score": round(score, 6),
                "cosine": round(cos_scores.get(rid, 0.0), 6),
            }
        )

    print(json.dumps(results, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
