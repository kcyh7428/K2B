#!/usr/bin/env python3
"""Washing Machine embedder: shelf rows -> SQLite + FTS5 index.

Reads all row bullets from ``wiki/context/shelves/<shelf>.md``, computes a
sentence-transformer embedding per row, and upserts into an SQLite database.
Idempotent via ``row_hash = sha256(shelf || "\\0" || row_text)``; rerunning on
the same shelf state performs zero writes.

Schema:
    rows(id INTEGER PRIMARY KEY, shelf TEXT, row_hash TEXT UNIQUE,
         row_text TEXT, embedding BLOB, entities_json TEXT,
         created_at INTEGER, updated_at INTEGER)
    rows_fts  -- FTS5 contentless-index virtual table, content_rowid='id',
                 kept in sync by AI/AD triggers on rows.

Env:
    WASHING_MACHINE_PYTHON  -- interpreter (caller wraps, not read here)
    K2B_VAULT               -- vault root (default: ~/Projects/K2B-Vault)
    K2B_SHELVES_DIR         -- shelves dir (default: <vault>/wiki/context/shelves)
    K2B_INDEX_DB            -- index path (default: <shelves>/index.db)
    WM_EMBED_MODEL          -- model name (default: all-MiniLM-L6-v2)

Plan: plans/2026-04-21_washing-machine-ship-1.md Commit 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import List

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.shelf_rows import Row, parse as parse_row, serialize as serialize_row  # noqa: E402

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
SLUG_IN_VALUE_RE = re.compile(r"^[a-z][a-z0-9_]*_[A-Za-z0-9_\-]+$")

DEFAULT_VAULT = Path.home() / "Projects" / "K2B-Vault"


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


def row_to_text(row: Row) -> str:
    """Canonical on-disk row serialization used as the DB ``row_text`` field.

    Delegates to ``lib.shelf_rows.serialize`` so the stored text exactly
    matches what the shelf writer produces, escapes included. This keeps
    ``row_hash = sha256(shelf || "\\0" || row_text)`` invariant across the
    writer, the indexer, and any future parser (Codex HIGH 2026-04-22).
    """
    return serialize_row(row.date, row.type, row.slug, row.attrs)


def row_to_embedding_text(row: Row) -> str:
    """Build the natural-language-ish text handed to the embedding model.

    Drops the ISO date (pure metadata -- it dilutes the vector with noise)
    and removes the pipe separators + ``key: value`` colons so the model
    sees attribute keys as regular context words (``tel 2830 3709``) rather
    than as an opaque label. Empirically this format bridges synonym pairs
    such as Tel -> phone and Dr. -> doctor in the doctor-phone ship gate,
    where the pipe-heavy form did not (Tel was drowned out by the date,
    pipes, and Chinese name tokens).
    """
    parts: List[str] = [row.type, row.slug]
    for k, v in row.attrs.items():
        parts.append(k)
        parts.append(v)
    return " ".join(parts)


def row_entities(row: Row) -> List[str]:
    """Entity identifiers for the entity-link retrieval signal.

    Sources:
      * the row's slug (always)
      * any ``[[wikilink]]`` tokens in attribute values
      * attribute values that themselves look like a slug (``person_X``,
        ``project_y``, ``meeting_foo``); protects against classifier output
        that drops the wikilink brackets.
    """
    ents = {row.slug}
    for v in row.attrs.values():
        for m in WIKILINK_RE.finditer(v):
            ents.add(m.group(1).strip())
        stripped = v.strip()
        if SLUG_IN_VALUE_RE.match(stripped):
            ents.add(stripped)
    return sorted(ents)


def read_shelf_rows(shelf_path: Path) -> tuple[List[Row], List[tuple[int, str, str]]]:
    """Parse bullet rows under the ``## Rows`` section of a shelf .md file.

    Returns ``(rows, errors)`` where ``errors`` is a list of
    ``(line_number, line_text, error_message)`` tuples for lines that
    look like row content but failed to parse. Callers must check
    ``errors`` and suppress destructive syncs when it is non-empty:
    otherwise a transient bad edit flips every indexed row for the
    affected shelf into a silent delete on the next reindex
    (Codex HIGH 2026-04-22, three passes).

    A missing shelf file returns ``([], [])`` here; ``index_shelf`` is
    responsible for distinguishing "first-time empty shelf" (safe no-op)
    from "shelf file disappeared but DB still has rows" (destructive
    risk, skip writes).

    Every non-empty line inside the ``## Rows`` section that is not a
    sub-header is routed through ``parse_row``. Indented bullets get
    normalised via ``line.strip()``, so a well-formed row tolerates
    accidental leading whitespace. But non-row content under the Rows
    header (``* item``, stray prose, asterisk lists, etc.) fails to
    parse and is recorded as an error -- the earlier "only lines
    starting with exact '- '" filter silently skipped those and let
    the reconcile phase delete the rest of the shelf (Codex HIGH pass
    3 2026-04-22).
    """
    if not shelf_path.exists():
        return [], []
    rows: List[Row] = []
    errors: List[tuple[int, str, str]] = []
    in_rows = False
    for lineno, line in enumerate(
        shelf_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if stripped.startswith("## Rows"):
            in_rows = True
            continue
        if in_rows and stripped.startswith("##") and not stripped.startswith("## Rows"):
            in_rows = False
            continue
        if not in_rows:
            continue
        if not stripped:
            continue
        try:
            rows.append(parse_row(stripped))
        except ValueError as e:
            errors.append((lineno, stripped, str(e)))
    return rows, errors


def hash_row(shelf: str, row_text: str) -> str:
    h = hashlib.sha256()
    h.update(shelf.encode("utf-8"))
    h.update(b"\x00")
    h.update(row_text.encode("utf-8"))
    return h.hexdigest()


def open_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rows (
            id            INTEGER PRIMARY KEY,
            shelf         TEXT NOT NULL,
            row_hash      TEXT UNIQUE NOT NULL,
            row_text      TEXT NOT NULL,
            embedding     BLOB NOT NULL,
            entities_json TEXT NOT NULL,
            created_at    INTEGER NOT NULL,
            updated_at    INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS rows_shelf_idx ON rows(shelf);

        CREATE VIRTUAL TABLE IF NOT EXISTS rows_fts
            USING fts5(row_text, content='rows', content_rowid='id');

        CREATE TRIGGER IF NOT EXISTS rows_ai
            AFTER INSERT ON rows BEGIN
                INSERT INTO rows_fts(rowid, row_text)
                    VALUES (new.id, new.row_text);
            END;

        CREATE TRIGGER IF NOT EXISTS rows_ad
            AFTER DELETE ON rows BEGIN
                INSERT INTO rows_fts(rows_fts, rowid, row_text)
                    VALUES ('delete', old.id, old.row_text);
            END;

        CREATE TRIGGER IF NOT EXISTS rows_au
            AFTER UPDATE ON rows BEGIN
                INSERT INTO rows_fts(rows_fts, rowid, row_text)
                    VALUES ('delete', old.id, old.row_text);
                INSERT INTO rows_fts(rowid, row_text)
                    VALUES (new.id, new.row_text);
            END;
        """
    )
    return conn


def embed_texts(model, texts: List[str]) -> List[bytes]:
    """Batch-encode, normalise for cosine, return float32 blobs."""
    import numpy as np

    if not texts:
        return []
    vecs = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return [np.asarray(v, dtype="float32").tobytes() for v in vecs]


def index_shelf(conn: sqlite3.Connection, model, shelf: str, shelf_path: Path) -> None:
    """Sync the DB to match the on-disk shelf state for ``shelf``.

    New rows get inserted. Rows present in DB but absent from the file get
    deleted. Rows whose ``row_hash`` matches an existing entry are left
    untouched -- this is the zero-delta path for idempotence.

    If any bullet on disk fails to parse, ALL writes for this shelf are
    skipped -- no inserts, no deletes, no updates. The shelf file is
    treated as untrustworthy until the author fixes the malformed bullets
    and reruns the indexer. This prevents two failure modes that the
    obvious looser strategies open up:

      * "skip only inserts" leaves a row edited-to-a-new-hash while the
        malformed bullet sits below it, so the old row stays (delete
        suppressed) and the new row lands (insert allowed). Retrieval
        surfaces both the stale and current version of the same record
        (Codex HIGH pass 2 2026-04-22).
      * "skip only deletes" silently drops previously indexed rows when a
        transient hand-edit loses a bullet (Codex HIGH pass 1 2026-04-22).

    Warnings are printed to stderr so Keith can see which line broke.
    """
    # Missing-file guard: a shelf file that vanishes while the DB still has
    # rows for that shelf would be treated as "authoritative empty" by the
    # reconcile logic and delete everything. Refuse the write and wait for
    # the operator to either restore the file or drop the shelf explicitly
    # (Codex HIGH pass 3 2026-04-22).
    if not shelf_path.exists():
        existing_count = conn.execute(
            "SELECT COUNT(*) FROM rows WHERE shelf = ?", (shelf,)
        ).fetchone()[0]
        if existing_count > 0:
            print(
                f"embed-index: shelf file {shelf_path} missing but "
                f"{existing_count} row(s) indexed for shelf '{shelf}'; "
                f"skipping write to prevent silent deletion. Restore the "
                f"file or delete the shelf's rows manually.",
                file=sys.stderr,
            )
        # First-time / long-empty shelves with no indexed rows are a
        # harmless no-op: nothing to write, nothing to delete.
        return

    parsed, parse_errors = read_shelf_rows(shelf_path)

    if parse_errors:
        print(
            f"embed-index: {len(parse_errors)} malformed row(s) in {shelf_path}; "
            f"skipping all writes for shelf '{shelf}' until the file is fixed",
            file=sys.stderr,
        )
        for lineno, line, err in parse_errors:
            print(f"  line {lineno}: {err}: {line!r}", file=sys.stderr)
        return

    seen: dict[str, Row] = {}
    for row in parsed:
        text = row_to_text(row)
        h = hash_row(shelf, text)
        # Dedup within a single shelf: first occurrence wins.
        if h in seen:
            continue
        seen[h] = row

    existing_hashes = {
        h for (h,) in conn.execute(
            "SELECT row_hash FROM rows WHERE shelf = ?", (shelf,)
        ).fetchall()
    }

    to_insert = [(h, row) for h, row in seen.items() if h not in existing_hashes]
    to_delete = [h for h in existing_hashes if h not in seen]

    if to_insert:
        texts = [row_to_text(r) for _, r in to_insert]
        embedding_texts = [row_to_embedding_text(r) for _, r in to_insert]
        embeddings = embed_texts(model, embedding_texts)
        now = int(time.time())
        payload = []
        for (h, row), row_text, emb in zip(to_insert, texts, embeddings):
            payload.append(
                (
                    shelf,
                    h,
                    row_text,
                    emb,
                    json.dumps(row_entities(row), ensure_ascii=False),
                    now,
                    now,
                )
            )
        conn.executemany(
            "INSERT INTO rows "
            "(shelf, row_hash, row_text, embedding, entities_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            payload,
        )

    if to_delete:
        conn.executemany(
            "DELETE FROM rows WHERE shelf = ? AND row_hash = ?",
            [(shelf, h) for h in to_delete],
        )


def iter_shelves(explicit: str | None) -> List[tuple[str, Path]]:
    root = shelves_dir()
    if explicit:
        return [(explicit, root / f"{explicit}.md")]
    if not root.exists():
        return []
    out: List[tuple[str, Path]] = []
    for p in sorted(root.glob("*.md")):
        if p.name == "index.md":
            continue
        out.append((p.stem, p))
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="embed-index.py")
    ap.add_argument("--shelf", default=None, help="Shelf name (without .md). Omit to index all.")
    ap.add_argument("--db", default=None, help="Override index DB path.")
    args = ap.parse_args(argv)

    db_path = Path(args.db) if args.db else index_db_path()
    conn = open_db(db_path)

    shelves = iter_shelves(args.shelf)
    if not shelves:
        conn.commit()
        conn.close()
        return 0

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"embed-index: sentence-transformers not importable: {e}", file=sys.stderr)
        conn.close()
        return 3

    model = SentenceTransformer(embed_model_name())

    for shelf_name, shelf_path in shelves:
        index_shelf(conn, model, shelf_name, shelf_path)

    conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
