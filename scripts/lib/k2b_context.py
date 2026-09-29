"""Bounded, source-linked evidence gathering and resume-brief validation.

The module never calls a provider, never writes the vault and never launches a
second agent.  It gathers a bounded, deterministic set of source-linked
evidence from the local vault and published automatic memory, then validates a
model-produced draft against that packet and renders a resume brief.  The
current conversational Codex model supplies the semantic draft; code supplies
bounded retrieval, source binding, repeatable state rules and rendering.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import automatic_memory


PACKET_SCHEMA_VERSION = 1
DRAFT_SCHEMA_VERSION = 1
BRIEF_SCHEMA_VERSION = 1

MAX_SOURCES = 20
MAX_EVIDENCE = 80
MAX_PACKET_BYTES = 48_000
MAX_SOURCE_FILE_BYTES = 2 * 1024 * 1024
MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
GATHER_DEADLINE_SECONDS = 5.0
FRESHNESS_THRESHOLD_SECONDS = 6 * 3600
MAX_SPANS_PER_SOURCE = 12
MAX_TEXT_CHARS = 20_000
# A selected passage normally keeps its whole block.  A single oversized line
# or paragraph (for example a one-line 25K raw capture) is instead reduced to a
# bounded exact window that still contains the matched term, so the stored
# quote never truncates a match away from the end of the block.  The window is
# centered on the first match and clipped to ``MAX_TEXT_CHARS``; the locator
# records both the line range and the exact byte slice, and validation rebuilds
# the identical window from source + locator.
MATCH_WINDOW_CHARS = 8_000
MAX_BACKLOG_RECEIPTS = 50
MAX_COVERAGE_RECEIPT_BYTES = 256 * 1024
MAX_SECTION_ITEMS = 40
# Bounds on the packet's own ``terms`` metadata.  Query-derived terms can never
# exceed the 400-char query, so ``MAX_TERM_CHARS`` matches that limit while
# ``MAX_TERMS`` leaves room for the worst case (a query of CJK bigrams).
MAX_TERMS = 512
MAX_TERM_CHARS = 400
# Model-proposed follow-up terms are a bounded hint, never free metadata: at
# most this many new terms are admitted and each is clipped to this length, so
# an oversized term cannot inflate the packet before evidence trimming runs.
MAX_FOLLOWUP_TERMS = 8
MAX_FOLLOWUP_TERM_CHARS = 80
# A broad (non-topic) query reserves this many evidence spans for its ranked
# ordinary notes.  Without the floor a high-volume published-memory snapshot
# can consume every span before any note is emitted, so a strongly relevant
# note would never even reach the packet.  The reservation only limits memory
# when memory would otherwise take the whole budget, and the leading note's
# retained spans are additionally kept through the byte trim.
BROAD_NOTE_EVIDENCE_RESERVE = 8
# Obvious English conversational filler carries no retrieval signal but does
# carry substring matches: in a spoken continuation query such as "where were
# we" the term ``we`` matches the unrelated stem ``weave``, while ``where`` and
# ``were`` match incidental prose.  Filtering is deliberately narrow so it can
# never hide a claim: only closed-class grammatical words that carry no
# reference of their own and non-lexical backchannel are dropped.  Anything that
# can change the meaning of a project claim stays searchable -- negation
# (``no``, ``not``, ``nor``), completion, status and time words (``done``,
# ``now``, ``still``, ``again``), predicate state particles (``up``, ``out``,
# ``on``, ``over``), modal and speculative hedges (``maybe``, ``might``,
# ``could``, ``perhaps``, ``probably``), degree and evaluative adverbs
# (``very``, ``too``, ``really``, ``just``), the lexical verb ``do`` and its
# forms, quantifiers and deictic place/time adverbs.  A status query such as
# "not done" therefore still yields its distinguishing terms.  CJK terms are
# character n-grams, never whole English words, so they are never affected.
CONVERSATIONAL_FILLER = frozenset(
    {
        # Articles and demonstratives.
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        # Personal and possessive pronouns.
        "i",
        "me",
        "my",
        "we",
        "us",
        "our",
        "you",
        "your",
        "yours",
        "he",
        "him",
        "his",
        "she",
        "her",
        "it",
        "its",
        "they",
        "them",
        "their",
        # Interrogatives and relative pronouns.
        "how",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "whose",
        "why",
        # Prepositions, particles and coordinating/subordinating conjunctions.
        # Predicate state particles are deliberately absent: "is the dashboard
        # up", "is the service out", "is the sync on" and "is the migration
        # over" carry their state in the particle, so dropping it leaves only
        # the entity word and can rank an older note that shares it above the
        # note recording the current state.  ``down`` and ``off`` were never
        # filtered, so every state word now behaves the same way.
        "about",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "to",
        "with",
        "and",
        "but",
        "if",
        "or",
        "than",
        # Copulas and the be/have auxiliaries.
        "am",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "having",
        # Non-lexical backchannel.
        "uh",
        "um",
        "oh",
        "yeah",
    }
)
MAX_LINK_HOPS = 4
MAX_MEMORY_FILE_BYTES = 8 * 1024 * 1024
MAX_PROVISIONAL_ENVELOPES = 200
MAX_PROVISIONAL_BYTES = 8 * 1024 * 1024
# Upper bound on previously unmatched items pulled in transitively to explain
# an accepted correction, so a long or malformed relation chain stays bounded.
MAX_CORRECTION_EXPANSION = 32

ALLOWED_NOTE_ROOTS = (
    "wiki/work",
    "wiki/people",
    "wiki/projects",
    "wiki/concepts",
    "wiki/insights",
    "wiki/reference",
)
# Discovery/ranking aids only: an index row narrows which note is the right
# candidate.  Index text is never emitted as evidence.
DISCOVERY_INDEX_RELS = (
    "wiki/work/index.md",
    "wiki/people/index.md",
    "wiki/projects/index.md",
)
# Bounded allowlist of human-capture raw subtrees considered for retrieval.
# Technical/audit raw trees (backtests, orchestrator-results, investment
# runs) are deliberately absent so they are never scanned.
RAW_CAPTURE_ROOTS = (
    "raw/meetings",
    "raw/tldrs",
    "raw/tldr",
    "raw/sessions",
    "raw/session",
    "raw/research",
    "raw/daily",
    "raw/youtube",
)
RAW_TECHNICAL_PATH_PARTS = {
    "audit",
    "audits",
    "backtests",
    "exports",
    "k2b-investment",
    "logs",
    "orchestrator-results",
    "results",
}
SEMANTIC_SHELF_REL = "wiki/context/shelves/semantic.md"
PUBLISHED_MEMORY_REL = "System/memory/automatic-memory-current.json"

PACKET_TOP_KEYS = {
    "schema_version",
    "kind",
    "query",
    "terms",
    "topic",
    "generated_at",
    "limits",
    "sources",
    "evidence",
    "corrections",
    "partial",
    "reasons",
    "coverage",
    "freshness",
    "status",
    "packet_id",
}
LIMIT_KEYS = {"max_sources", "max_evidence", "max_bytes"}
SOURCE_BASE_KEYS = {
    "source_id",
    "source_type",
    "relative_path",
    "content_hash",
    "source_date",
    "date_quality",
    "speaker",
    "authority_category",
    "vault_relative",
    "pending_home",
    "local_only",
}
SOURCE_OPTIONAL_KEYS = {"snapshot_hash"}
SOURCE_TYPES = {
    "vault_note",
    "raw_capture",
    "semantic_shelf",
    "published_memory",
    "provisional_memory",
}
NOTE_EVIDENCE_KEYS = {
    "evidence_id",
    "source_id",
    "kind",
    "quote",
    "locator",
    "source_time",
    "source_date",
    "date_quality",
    "speaker",
    "authority",
    "disposition",
    "durable",
}
MEMORY_EVIDENCE_KEYS = {
    "evidence_id",
    "source_id",
    "kind",
    "key",
    "memory_kind",
    "item_id",
    "quote",
    "value",
    "speaker_source",
    "source_time",
    "source_date",
    "date_quality",
    "citation",
    "authority",
    "locator",
    "disposition",
    "durable",
    "pending_home",
}
MEMORY_EVIDENCE_OPTIONAL_KEYS = {"replacement_item_id"}
PACKET_CORRECTION_KEYS = {
    "correction_id",
    "relation",
    "target_item_id",
    "source_item_id",
    "topic_ref",
    "field",
    "status",
}
COVERAGE_KEYS = {
    "sources",
    "evidence",
    "scanned_bytes",
    "provisional_state_hash",
    "provisional_delivery",
    "coverage_receipt",
}
FRESHNESS_KEYS = {
    "observed_at",
    "snapshot_hash",
    "published_memory",
    "cross_host_sync",
    "provisional_included",
    "coverage_receipt",
    "candidate_inventory",
}
EVIDENCE_DISPOSITIONS = {
    "current",
    "superseded",
    "retracted",
    "unresolved",
    "pending_home",
}
# A note locator always carries the 1-based line range.  A windowed span for an
# oversized block adds the exact byte slice that was quoted so validation can
# rebuild the identical quote instead of trusting a truncated line range.
LOCATOR_RE = re.compile(r"^line=(\d+)-(\d+)(?:;bytes=(\d+)-(\d+))?$")
# A passage in one file shares its file's single frontmatter day, and line order
# is not event order.  The only within-file order evidence accepted is a date
# the passage stamps at its own start (after leading markdown/whitespace).  A
# date buried mid-sentence is deliberately ignored: an ISO deadline such as
# "2026-10-08" would otherwise be misread as the passage's event date.
PASSAGE_DATE_RE = re.compile(r"^\W*?(\d{4}-\d{2}-\d{2})(?!\d)")
# A brief citation target may carry a ``#Lnn`` line fragment, mirroring the
# line-range locator.  It is only a portable marker; rendering converts it to
# the local ``:line`` form the Codex desktop opens.
LINK_FRAGMENT_RE = re.compile(r"^L(\d+)(?:-L(\d+))?$")
ITEM_ID_RE = re.compile(r"^item:[0-9a-f]{64}$")
EVIDENCE_ID_RE = re.compile(r"^ev:[0-9a-f]{64}$")
SOURCE_ID_RE = re.compile(r"^src:[0-9a-f]{64}$")
CITATION_KEYS = {
    "host_id",
    "session_id",
    "completed_cursor",
    "completed_at",
    "event_id",
    "source_hash",
    "transcript_hash",
}

EXCLUDED_PATH_PARTS = {
    ".git",
    ".stversions",
    ".trash",
    ".obsidian",
    "node_modules",
    "archive",
    "archives",
    "migration-exports",
}

ITEM_STATUSES = {"supported", "provisional", "unresolved", "inference"}
ITEM_ORIGINS = {"source", "recommendation"}
CORRECTION_RELATIONS = {"supersedes", "retracts"}

BRIEF_KEYS = {
    "schema_version",
    "kind",
    "brief_id",
    "packet_id",
    "topic_ref",
    "generated_at",
    "sections",
    "corrections",
    "sources",
    "citations",
    "freshness",
    "warnings",
    "partial",
}
BRIEF_SECTION_NAMES = (
    "goal",
    "state",
    "decisions",
    "commitments",
    "next_action",
    "open_questions",
)
BRIEF_CITATION_KEYS = {
    "evidence_id",
    "kind",
    "label",
    "target",
    "provenance_id",
    "limitation",
}

HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FRONTMATTER_DATE_RE = re.compile(r"^date:\s*(.+?)\s*$", re.MULTILINE)
LINK_RE = re.compile(r"\[\[([^\]|#]+)")
LINE_SPLIT_RE = re.compile(r"\n\s*\n")
MD_LINK_TARGET_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)")
RAW_PATH_REF_RE = re.compile(r"(raw/[A-Za-z0-9._/-]+\.md)")
FRONTMATTER_ALIAS_RE = re.compile(r"(?im)^\s*(?:aliases|alias)\s*:\s*(.*?)\s*$")
SYNTHETIC_FRONTMATTER_RE = re.compile(
    r"(?im)^\s*(?:synthetic|synthetic_marker)\s*:\s*(?:true|yes|1)\s*$"
)
SYNTHETIC_TEXT_RE = re.compile(r"\[(?:k2b[-_ ]?)?synthetic[^\]]*\]", re.IGNORECASE)
SYNTHETIC_TOKEN_RE = re.compile(r"(?i)\bk2b[-_ ]?synthetic\b")


class ContextError(Exception):
    """Base class carrying a stable machine reason code."""

    exit_code = 2
    reason = "invalid_input"

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason


class InvalidInput(ContextError):
    exit_code = 2


class UnavailableState(ContextError):
    exit_code = 3


class StalePacket(ContextError):
    exit_code = 2
    reason = "stale_packet"


def _sha256_hex(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInput("invalid_timestamp", f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidInput("invalid_timestamp", f"{label} must include a timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _source_instant(value: object) -> datetime | None:
    """Return one aware instant for ordering, or ``None`` when unusable.

    Geometry must not be inferred from the serialized offset text.  A later
    correction recorded as ``2026-09-21T01:00:00+09:00`` is earlier than
    ``2026-09-20T23:00:00-10:00`` even though the former sorts higher as a
    string.  Invalid, missing or timezone-naive values fail closed.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _require_text(value: object, label: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput("invalid_schema", f"{label} must be a non-empty string")
    text = value.strip()
    if len(text) > max_chars:
        raise InvalidInput("invalid_schema", f"{label} is too long")
    return text


def _exact_keys(value: object, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise InvalidInput("invalid_schema", f"{label} must be an object")
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise InvalidInput(
            "invalid_schema", f"{label} fields invalid; missing={missing}, extra={extra}"
        )
    return value


def _query_terms(query: str) -> list[str]:
    """Deterministic terms for English words and Chinese character bigrams.

    Obvious English conversational filler is dropped so a broad spoken query
    (``where were we``) does not contribute substrings such as ``we`` that
    falsely match unrelated words.  CJK character terms are never filtered.
    """

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        lowered = term.casefold()
        if len(lowered) < 2 or lowered in seen:
            return
        if lowered in CONVERSATIONAL_FILLER:
            return
        seen.add(lowered)
        terms.append(lowered)

    word = []
    cjk_run = []

    def flush_word() -> None:
        if word:
            add("".join(word))
            word.clear()

    def flush_cjk() -> None:
        if len(cjk_run) == 1:
            add(cjk_run[0])
        else:
            for index in range(len(cjk_run) - 1):
                add(cjk_run[index] + cjk_run[index + 1])
        cjk_run.clear()

    for char in query:
        if CJK_RE.match(char):
            flush_word()
            cjk_run.append(char)
        elif char.isalnum() or char in "_.":
            flush_cjk()
            word.append(char)
        else:
            flush_word()
            flush_cjk()
    flush_word()
    flush_cjk()
    return terms


def _matches(text: str, terms: list[str]) -> bool:
    if not terms:
        return False
    lowered = text.casefold()
    return any(term in lowered for term in terms)


def _bounded_followup_terms(
    extra_terms: list | None, existing: list[str]
) -> tuple[list[str], bool]:
    """Admit at most :data:`MAX_FOLLOWUP_TERMS` bounded model-proposed terms.

    A follow-up term is optionally suggested by the model for one bounded
    retrieval.  It must not become unbounded packet metadata, so only a fixed
    number of new terms enter and each is clipped to
    :data:`MAX_FOLLOWUP_TERM_CHARS`.  ``True`` marks that a term was clipped or
    dropped so the packet reports itself partial.
    """

    if not extra_terms:
        return [], False
    seen = set(existing)
    admitted: list[str] = []
    bounded = False
    for term in extra_terms:
        if not isinstance(term, str):
            raise InvalidInput("invalid_input", "follow-up terms must be strings")
        text = term.strip().casefold()
        if not text:
            continue
        clipped = text[:MAX_FOLLOWUP_TERM_CHARS]
        if clipped != text:
            bounded = True
        if len(admitted) >= MAX_FOLLOWUP_TERMS:
            bounded = True
            break
        if clipped in seen:
            continue
        seen.add(clipped)
        admitted.append(clipped)
    return admitted, bounded


def _topic_scope_terms(topic_ref: str, query_terms: list[str]) -> list[str]:
    """Entity terms that bind memory to an explicitly selected topic note.

    The topic note reference, not its body, anchors the scope: a pointer to
    ``wiki/projects/project_atlas.md`` yields ``atlas``.  Generic type or
    prefix words (``project``, ``work``, ``people``, ...) are dropped because
    OR-matching them would pull every sibling note into scope.  When a query
    word also names the topic entity (``continue Atlas``) only the anchored
    entity term is used, so a generic verb cannot drag in unrelated memory such
    as ``Continue Bob hiring``.  With no overlap the note's own entity terms
    still scope the search.  A filename made only of generic words (for example
    ``project.md``) yields no scope terms; that is intentional, and the caller
    then admits only the accepted corrections recorded against the exact
    ``topic_ref`` plus the note body itself instead of widening to every
    matching sibling.
    """

    stem = Path(topic_ref).stem
    spaced = re.sub(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", " ", stem)
    tokens = [term for term in _query_terms(spaced) if not _is_generic_topic_term(term)]
    anchored = [term for term in query_terms if term in tokens]
    return anchored or tokens


# Type/prefix words that name a note's folder or kind rather than a topic
# entity.  They are excluded from topic-scoped memory because retrieval ORs the
# scope terms: keeping ``project`` would match every project note key.
GENERIC_TOPIC_TERMS = frozenset(
    {
        "wiki",
        "work",
        "people",
        "person",
        "project",
        "projects",
        "concept",
        "concepts",
        "insight",
        "insights",
        "reference",
        "note",
        "notes",
    }
)


def _is_generic_topic_term(term: str) -> bool:
    return term in GENERIC_TOPIC_TERMS


def _is_excluded(rel: str) -> bool:
    parts = [part.casefold() for part in rel.split("/")]
    if any(part in EXCLUDED_PATH_PARTS for part in parts):
        return True
    return any(".sync-conflict-" in part for part in parts)


def _safe_within(vault: Path, path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(vault.resolve())


def _symlinked_ancestor(base: Path, candidate: Path) -> bool:
    """True when any directory component of ``candidate`` below ``base`` is a symlink.

    A final-file symlink check is insufficient: ``raw/link/secret.md`` with a
    symlinked ``link`` directory resolves inside the vault yet leaks excluded
    content.  Any symlinked parent fails closed.
    """

    try:
        rel = candidate.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in rel.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _resolved_relative(base: Path, path: Path) -> str | None:
    """Resolved, base-relative POSIX path, or ``None`` when it escapes the root."""

    try:
        resolved = path.resolve()
        return resolved.relative_to(Path(base).resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _bind_local_path(base: Path, rel: object, label: str) -> Path:
    """Resolve a vault-relative path, rejecting escapes and symlink parents.

    A caller-rebuilt packet can carry an injected ``relative_path``; every
    read of note/memory evidence therefore re-derives the path under the real
    vault root instead of trusting the packet.  Absolute paths, ``..``
    segments, symlinked parents and any resolution that leaves the root fail
    closed before a byte is read.
    """

    if not isinstance(rel, str) or not rel.strip():
        raise InvalidInput("evidence_binding", f"{label} has no relative path")
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise InvalidInput("evidence_binding", f"{label} path escapes its root")
    base_resolved = Path(base).expanduser().resolve()
    for part in candidate.parts[:-1]:
        if (base_resolved / part).is_symlink():
            raise InvalidInput("evidence_binding", f"{label} path has a symlink parent")
    path = base_resolved / candidate
    if path.is_symlink():
        raise InvalidInput("evidence_binding", f"{label} path is a symlink")
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise InvalidInput("evidence_binding", f"{label} path is not resolvable") from exc
    if resolved != base_resolved and not resolved.is_relative_to(base_resolved):
        raise InvalidInput("evidence_binding", f"{label} path escapes its root")
    return resolved


def _read_bounded(path: Path, limit: int) -> str | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
    except OSError:
        return None
    if size > limit:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_bytes_bounded(path: Path, limit: int) -> bytes | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
    except OSError:
        return None
    if size > limit:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _sha256_file_bounded(path: Path, limit: int) -> str | None:
    """Streaming content hash that never reads an unbounded file into memory.

    Returns ``None`` when the path is missing, a symlink, unreadable, or larger
    than ``limit`` so freshness callers fail closed instead of hashing a
    truncated or arbitrarily large file.
    """

    try:
        if path.is_symlink() or not path.is_file():
            return None
    except OSError:
        return None
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    return None
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _file_size(path: Path) -> int | None:
    """Raw byte size of a regular file, or ``None`` when it cannot be read."""

    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.stat().st_size
    except OSError:
        return None


def _bounded_file_size(path: Path, limit: int) -> int | None:
    """Byte size of a supported file, or ``None`` when it cannot be bound.

    Missing, symlinked, unreadable or oversized paths return ``None`` so a
    caller records a conservative placeholder rather than hashing them.
    """

    size = _file_size(path)
    if size is None or size > limit:
        return None
    return size


def _within_note_roots(rel: str) -> bool:
    normalized = rel.replace("\\", "/").lstrip("./")
    return any(
        normalized == root or normalized.startswith(root + "/")
        for root in ALLOWED_NOTE_ROOTS
    )


def _within_raw_roots(rel: str) -> bool:
    normalized = rel.replace("\\", "/").lstrip("./")
    return any(
        normalized == root or normalized.startswith(root + "/")
        for root in RAW_CAPTURE_ROOTS
    )


def _frontmatter_body(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    return text[3:end]


def _note_aliases(text: str) -> set[str]:
    """Declared aliases from frontmatter, casefolded and bounded."""

    body = _frontmatter_body(text)
    if body is None:
        return set()
    aliases: set[str] = set()
    lines = body.splitlines()
    for index, line in enumerate(lines):
        match = FRONTMATTER_ALIAS_RE.match(line)
        if match is None:
            continue
        value = match.group(1).strip()
        items: list[str] = []
        if value.startswith("[") and value.endswith("]"):
            items = value[1:-1].split(",")
        elif value:
            items = [value]
        else:
            for following in lines[index + 1 :]:
                stripped = following.strip()
                if stripped.startswith("-"):
                    items.append(stripped[1:].strip())
                elif stripped and not stripped.startswith("#"):
                    break
        for item in items:
            cleaned = item.strip().strip('"').strip("'").casefold()
            if cleaned:
                aliases.add(cleaned)
    return aliases


def _is_synthetic_content(text: str) -> bool:
    body = _frontmatter_body(text)
    if body is not None and SYNTHETIC_FRONTMATTER_RE.search(body):
        return True
    return (
        SYNTHETIC_TEXT_RE.search(text) is not None
        or SYNTHETIC_TOKEN_RE.search(text) is not None
    )


def _is_technical_record(rel: str) -> bool:
    parts = [part.casefold() for part in rel.replace("\\", "/").split("/")]
    if any(part in RAW_TECHNICAL_PATH_PARTS for part in parts):
        return True
    return "audit" in parts[-1]


def _frontmatter_date(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    match = FRONTMATTER_DATE_RE.search(text[3:end])
    if match is None:
        return None
    raw = match.group(1).strip().strip('"').strip("'")
    try:
        return datetime.fromisoformat(raw).date().isoformat()
    except ValueError:
        return None


def _first_match_index(text: str, terms: list[str]) -> int | None:
    """Character offset of the earliest admitted term, or ``None``."""

    lowered = text.casefold()
    best: int | None = None
    for term in terms:
        position = lowered.find(term)
        if position != -1 and (best is None or position < best):
            best = position
    return best


def _window_bounds_at(block: str, match_index: int, terms: list[str]) -> tuple[int, int]:
    """Inclusive character bounds of the bounded window anchored on one match.

    The window is centered on ``match_index`` and capped at
    :data:`MATCH_WINDOW_CHARS`; the start is capped so the finished quote stays
    within the packet-wide :data:`MAX_TEXT_CHARS`.  The anchored term always
    stays inside the window.  The full block is the degenerate case
    (``0, len(block) - 1``).
    """

    length = len(block)
    if length <= MATCH_WINDOW_CHARS:
        return 0, max(0, length - 1)
    lowered = block.casefold()
    term_end = match_index + 1
    for term in terms:
        if lowered.startswith(term, match_index):
            term_end = max(term_end, match_index + len(term))
    # Place a standard width of context before the match, then as much after it
    # as the term and the standard width need.  The whole window is then slid
    # back if needed so it never exceeds ``MAX_TEXT_CHARS`` and never starts
    # past the match -- keeping the matched term inside every emitted quote.
    start = max(0, match_index - MATCH_WINDOW_CHARS // 2)
    start = min(start, match_index)
    end = max(match_index + MATCH_WINDOW_CHARS - 1, term_end - 1)
    if end - start + 1 > MAX_TEXT_CHARS:
        end = start + MAX_TEXT_CHARS - 1
    end = min(end, length - 1)
    if end - start + 1 > MAX_TEXT_CHARS:
        start = max(0, end - MAX_TEXT_CHARS + 1)
    return start, end


def _char_to_byte(block: str, char_index: int) -> int:
    """UTF-8 byte offset of ``char_index`` within ``block``."""

    return len(block[:char_index].encode("utf-8"))


def _window_quote(block: str, start: int, end: int) -> tuple[str, int | None, int | None]:
    """The exact stored quote for one character-bounded window of a block."""

    return (
        block[start : end + 1].strip(),
        _char_to_byte(block, start),
        _char_to_byte(block, end + 1) - 1,
    )


def _window_gap_has_match(
    lowered: str, terms: list[str], bounds: list[tuple[int, int]]
) -> bool:
    """True when a term occurs between two retained windows of one block."""

    for (_, prev_end), (next_start, _) in zip(bounds, bounds[1:]):
        if next_start <= prev_end + 1:
            continue
        for term in terms:
            found = lowered.find(term, prev_end + 1)
            if found != -1 and found <= next_start - 1:
                return True
    return False


def _block_windows(
    block: str, terms: list[str]
) -> tuple[list[tuple[str, int | None, int | None]], bool]:
    """Return the bounded windows a block contributes, plus an omission flag.

    A block that fits keeps one whole-block quote (``None`` byte bounds); an
    oversized block without a match keeps its degenerate head window.  An
    oversized block with matches normally keeps one window around its earliest
    match, but a single 30 KB line can carry both an initial claim and a later
    correction: then the earliest and the newest match each keep their own
    bounded window so a late correction is not silently dropped.  ``omitted``
    is True when a matching passage fell between the retained windows, so the
    caller can report a partial span list instead of presenting the kept
    windows as complete.
    """

    length = len(block)
    if length <= MATCH_WINDOW_CHARS:
        return [(block.strip()[:MAX_TEXT_CHARS], None, None)], False
    first = _first_match_index(block, terms)
    if first is None:
        return [_window_quote(block, 0, MATCH_WINDOW_CHARS - 1)], False
    lowered = block.casefold()
    last = first
    for term in terms:
        found = lowered.rfind(term)
        if found > last:
            last = found
    anchors = [first] if last == first else [first, last]
    windows: list[tuple[str, int | None, int | None]] = []
    bounds: list[tuple[int, int]] = []
    for anchor in anchors:
        start, end = _window_bounds_at(block, anchor, terms)
        # Keep the late window only when it adds coverage: a match already
        # inside a retained window does not need a second, nearly identical
        # quote.
        if any(bound_start <= anchor <= bound_end for bound_start, bound_end in bounds):
            continue
        bounds.append((start, end))
        windows.append(_window_quote(block, start, end))
    omitted = _window_gap_has_match(lowered, terms, sorted(bounds))
    return windows, omitted


def _block_quote(
    block: str, terms: list[str]
) -> tuple[str, int | None, int | None]:
    """Return the first stored quote for a block plus its UTF-8 byte bounds.

    A block within :data:`MATCH_WINDOW_CHARS` keeps its whole text, clipped only
    by the packet-wide :data:`MAX_TEXT_CHARS`; ``(None, None)`` marks the whole
    block.  An oversized block keeps a bounded exact window (``byte_start``,
    ``byte_end``) that still contains its earliest match.  This is the first of
    :func:`_block_windows`; a late correction on the same oversized line keeps
    its own additional window there.
    """

    return _block_windows(block, terms)[0][0]


def _rebuild_window(block: str, byte_start: int, byte_end: int) -> str:
    """Rebuild the exact windowed quote from source block + byte bounds."""

    raw = block.encode("utf-8")
    return raw[byte_start : byte_end + 1].decode("utf-8", errors="replace").strip()


def _span_locator(
    start_line: int,
    end_line: int,
    byte_start: int | None,
    byte_end: int | None,
) -> str:
    """Portable line (and, for a windowed block, byte) locator for a span."""

    locator = f"line={start_line}-{end_line}"
    if byte_start is not None and byte_end is not None:
        locator += f";bytes={byte_start}-{byte_end}"
    return locator


def _spans(
    text: str, terms: list[str], *, take_head: bool, prefer_body: bool = False
) -> tuple[list[tuple[str, int, int, int | None, int | None]], bool]:
    """Return bounded (quote, start_line, end_line, byte_start, byte_end) spans.

    A selected source keeps matching passages plus as much bounded head
    context as the remaining span budget allows.  Scanning continues past
    introductory paragraphs instead of letting twelve non-matching intros
    hide a later match; matches are selected first, then head paragraphs fill
    the remaining slots in document order.  A source with more matches than
    the per-source cap never keeps only its earliest passages: the oldest and
    the newest matches are both retained so a late correction can still
    surface, and the dropped middle is reported through the returned flag
    instead of vanishing silently.  Discovery sources (``take_head=False``)
    skip the head fill and keep match-only selection.
    ``byte_start``/``byte_end`` are the inclusive match-window bounds for an
    oversized block (``None`` for the whole block); they travel into the
    locator so validation can rebuild the identical quote.  A single oversized
    line that carries both an early claim and a later correction contributes
    one window per end, and any match elided between those windows also sets
    the truncation flag.

    ``prefer_body`` reorders the retained spans -- never their quotes,
    locators or byte bounds -- so the explicitly selected topic note leads with
    a substantive passage.  Its reserved evidence slot would otherwise be spent
    on a heading that merely repeats a query word.  A matching non-heading
    passage wins first, then any non-heading passage, and a heading is kept
    only when neither the retained spans nor the bounded scan saw an eligible
    body passage.  Because the per-source cap fills with matches before the
    body preference runs, an all-heading selection would otherwise stay
    heading-only even though a body exists; one interior heading is exchanged
    for the earliest body passage so the anchor is substantive, and a matching
    passage dropped by that exchange still marks the list partial.
    """

    lines = text.splitlines()
    matches: list[tuple[str, int, int, int | None, int | None]] = []
    head_spans: list[tuple[str, int, int, int | None, int | None]] = []
    # For the explicitly selected topic note only: the earliest matching
    # non-heading passage and the earliest non-heading passage of any kind.
    # One reference each keeps this bounded even when hundreds of matching
    # headings would saturate the per-source cap before any body is seen.
    body_match: tuple[str, int, int, int | None, int | None] | None = None
    body_any: tuple[str, int, int, int | None, int | None] | None = None
    truncated = False
    start = 0
    buffer: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal start, buffer, truncated, body_match, body_any
        if not buffer:
            return
        block = "\n".join(buffer)
        windows, omitted = _block_windows(block, terms)
        if omitted:
            truncated = True
        for quote, byte_start, byte_end in windows:
            is_frontmatter = quote.startswith("---") and quote.rstrip().endswith("---")
            if not quote or is_frontmatter:
                continue
            span = (quote, start + 1, end_line, byte_start, byte_end)
            if take_head and len(head_spans) < MAX_SPANS_PER_SOURCE:
                head_spans.append(span)
            if prefer_body and not _is_heading_span(quote):
                if body_any is None:
                    body_any = span
                if body_match is None and _matches(quote, terms):
                    body_match = span
            if _matches(quote, terms):
                matches.append(span)

    for index, line in enumerate(lines):
        if line.strip() == "":
            flush(index)
            start = index + 1
            buffer = []
        else:
            if not buffer:
                start = index
            buffer.append(line)
    flush(len(lines))
    if len(matches) > MAX_SPANS_PER_SOURCE:
        # Keep both ends of the document: the earliest matches preserve the
        # original claim and the newest ones carry a possible correction.  The
        # elided middle is reported so the packet is marked partial.
        truncated = True
        head_keep = MAX_SPANS_PER_SOURCE // 2
        tail_keep = MAX_SPANS_PER_SOURCE - head_keep
        selected = matches[:head_keep] + matches[-tail_keep:]
    elif take_head:
        selected = list(matches)
    else:
        return list(matches), truncated
    if head_spans and len(selected) < MAX_SPANS_PER_SOURCE:
        first_head = head_spans[0]
        if first_head not in selected:
            selected.append(first_head)
    seen = set(selected)
    for span in head_spans:
        if len(selected) >= MAX_SPANS_PER_SOURCE:
            break
        if span in seen:
            continue
        selected.append(span)
        seen.add(span)
    ordered = sorted(selected, key=lambda span: span[1])[:MAX_SPANS_PER_SOURCE]
    if prefer_body:
        ordered, body_dropped_match = _prefer_body_spans(
            ordered, terms, candidate=body_match or body_any
        )
        if body_dropped_match:
            truncated = True
    return ordered, truncated


def _is_heading_span(quote: str) -> bool:
    """True when every non-empty line of a span is a Markdown heading.

    A block that opens with a heading but carries body prose is substantive
    content, so it stays eligible as a body passage rather than being dismissed
    as a bare heading.
    """

    lines = [line.strip() for line in quote.splitlines() if line.strip()]
    return bool(lines) and all(
        re.match(r"^#{1,6}(?:\s|$)", line) for line in lines
    )


def _prefer_body_spans(
    spans: list[tuple[str, int, int, int | None, int | None]],
    terms: list[str],
    *,
    candidate: tuple[str, int, int, int | None, int | None] | None = None,
) -> tuple[list[tuple[str, int, int, int | None, int | None]], bool]:
    """Lead with a substantive body passage, admitting one the cap hid.

    Tiers, stably sorted so document order survives inside each: a matching
    non-heading passage, then any non-heading passage, then a heading.  The
    sort only changes order; each span keeps its original locator, byte bounds
    and quote, so the derived hash and citation are unchanged.

    When every retained span is a heading but the bounded scan saw an eligible
    body passage (``candidate``), one interior heading is exchanged for it so
    a matching-heading-saturated selection still leads with real prose.  A
    heading at either retained end is never moved, so the earliest retained
    span and the newest one -- including a late correction -- survive.  The
    returned flag is True when the exchange dropped a matching passage, so the
    caller keeps the packet honestly partial rather than presenting the capped
    list as complete.
    """

    if candidate is not None and not any(
        not _is_heading_span(span[0]) for span in spans
    ):
        spans, elided = _exchange_for_body(spans, candidate, terms)
    else:
        elided = False

    def tier(span: tuple[str, int, int, int | None, int | None]) -> int:
        quote = span[0]
        if _is_heading_span(quote):
            return 2
        return 0 if _matches(quote, terms) else 1

    return sorted(spans, key=tier), elided


def _exchange_for_body(
    spans: list[tuple[str, int, int, int | None, int | None]],
    candidate: tuple[str, int, int, int | None, int | None],
    terms: list[str],
) -> tuple[list[tuple[str, int, int, int | None, int | None]], bool]:
    """Swap one interior heading for ``candidate`` when the cap is saturated.

    Returns the (possibly unchanged) spans plus whether a matching passage was
    dropped.  Under the cap the body simply joins the list; at the cap only a
    heading strictly between the retained ends is exchanged, so the oldest
    retained span and the newest one always survive.  A non-matching heading
    is spent before a matching one, so a query word in a bare ``#`` line never
    costs a real passage.
    """

    if candidate in spans:
        return spans, False
    if len(spans) < MAX_SPANS_PER_SOURCE:
        return sorted([*spans, candidate], key=lambda span: span[1]), False
    interior = [
        index
        for index in range(1, len(spans) - 1)
        if _is_heading_span(spans[index][0])
    ]
    if not interior:
        # Both retained ends are the only headings; keep the heading fallback.
        return spans, False
    non_matching = [
        index for index in interior if not _matches(spans[index][0], terms)
    ]
    pool = non_matching or interior
    victim = pool[len(pool) // 2]
    elided = _matches(spans[victim][0], terms)
    kept = [span for index, span in enumerate(spans) if index != victim]
    kept.append(candidate)
    return sorted(kept, key=lambda span: span[1]), elided


def _resolve_note_link(vault: Path, target: str) -> Path | None:
    """Map a wikilink/markdown-link target to an ordinary note, or ``None``."""

    name = target.split("#", 1)[0].split("|", 1)[0].strip()
    if not name or name.startswith(("http://", "https://", "mailto:")):
        return None
    parts = Path(name).parts
    if ".." in parts:
        return None
    candidates: list[Path] = []
    if name.endswith(".md"):
        candidates.append(vault / name.lstrip("/"))
    else:
        for root in ALLOWED_NOTE_ROOTS:
            candidates.append(vault / root / f"{name}.md")
    for candidate in candidates:
        try:
            rel = candidate.relative_to(vault).as_posix()
        except ValueError:
            continue
        if not _within_note_roots(rel) or _is_excluded(rel) or candidate.is_symlink():
            continue
        if candidate.is_file() and _safe_within(vault, candidate):
            return candidate
    return None


def _index_candidates(
    vault: Path, terms: list[str], deadline: float
) -> tuple[list[Path], int, bool]:
    """Discover notes via their folder ``index.md`` rows (ranking aid only).

    A matching index row names a candidate note; the index text itself is
    never returned as evidence.  Returns the discovered notes in row order,
    the bytes read and whether the deadline stopped the scan.
    """

    discovered: list[Path] = []
    seen: set[Path] = set()
    bytes_read = 0
    truncated = False
    for rel in DISCOVERY_INDEX_RELS:
        index_path = vault / rel
        if not index_path.is_file() or index_path.is_symlink():
            continue
        if time.monotonic() >= deadline:
            truncated = True
            break
        text = _read_bounded(index_path, MAX_SOURCE_FILE_BYTES)
        if text is None:
            continue
        bytes_read += len(text.encode("utf-8"))
        for line in text.splitlines():
            if not _matches(line, terms):
                continue
            targets = LINK_RE.findall(line) + MD_LINK_TARGET_RE.findall(line)
            for target in targets:
                path = _resolve_note_link(vault, target)
                if path is None or path in seen:
                    continue
                seen.add(path)
                discovered.append(path)
    return discovered, bytes_read, truncated


def _candidate_note_files(
    vault: Path,
    terms: list[str],
    deadline: float,
    max_candidate_bytes: int = MAX_CANDIDATE_BYTES,
    *,
    start_bytes: int = 0,
    scanned_paths: set[Path] | None = None,
    use_index: bool = True,
) -> tuple[list[Path], bool, bool, int, set[Path], set[Path]]:
    """Rank ordinary notes: name/alias exact, then index discovery, then keyword.

    Returns the ranked paths plus whether the cumulative candidate-content
    budget or the five-second deadline stopped the scan, whether any oversized
    file was skipped, the cumulative candidate bytes/photograph of scanned
    paths, and the set of discovery-ranked paths (name/alias/index) that should
    contribute a head span even when their body does not repeat the query.
    """

    scanned_bytes = start_bytes
    scanned_paths = set(scanned_paths or ())
    discovery_head: set[Path] = set()
    # Exact stem matches are ranked by how many distinct query terms the name
    # carries, so a specific multi-term entity outranks a note that merely
    # shares one broad word.
    exact: list[tuple[int, Path]] = []
    alias: list[Path] = []
    indexed: list[Path] = []
    keyword: list[tuple[int, Path]] = []
    seen: set[Path] = set()
    truncated = False
    oversized = False

    if use_index:
        found, index_bytes, index_truncated = _index_candidates(vault, terms, deadline)
        if index_truncated or scanned_bytes + index_bytes > max_candidate_bytes:
            truncated = True
        else:
            scanned_bytes += index_bytes
            for path in found:
                if path not in seen:
                    seen.add(path)
                    indexed.append(path)
                    discovery_head.add(path)

    for root in ALLOWED_NOTE_ROOTS:
        directory = vault / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if time.monotonic() >= deadline:
                truncated = True
                break
            rel = path.relative_to(vault).as_posix()
            if _is_excluded(rel) or path.is_symlink() or path.name == "index.md":
                continue
            if not _safe_within(vault, path):
                continue
            stem = path.stem.casefold()
            stem_hits = sum(1 for term in terms if term in stem)
            if stem_hits:
                if path not in seen:
                    seen.add(path)
                    exact.append((-stem_hits, path))
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_SOURCE_FILE_BYTES:
                oversized = True
                continue
            if scanned_bytes + size > max_candidate_bytes:
                truncated = True
                break
            text = _read_bounded(path, MAX_SOURCE_FILE_BYTES)
            if text is None:
                continue
            scanned_bytes += len(text.encode("utf-8"))
            scanned_paths.add(path)
            aliases = _note_aliases(text)
            if any(term in name for name in aliases for term in terms):
                if path not in seen:
                    seen.add(path)
                    alias.append(path)
                    discovery_head.add(path)
                continue
            hits = sum(1 for term in terms if term in text.casefold())
            if hits and path not in seen:
                keyword.append((hits, path))
    ranked = [path for _, path in sorted(exact)] + alias + indexed + [
        item[1] for item in sorted(keyword, key=lambda row: (-row[0], str(row[1])))
    ]
    return ranked, truncated, oversized, scanned_bytes, scanned_paths, discovery_head


def _candidate_raw_files(
    vault: Path,
    terms: list[str],
    deadline: float,
    max_candidate_bytes: int,
    *,
    start_bytes: int,
    scanned_paths: set[Path],
) -> tuple[list[Path], bool, bool, int, set[Path]]:
    """Lexically rank bounded raw captures, sharing the caller's budget."""

    scanned_bytes = start_bytes
    scanned_paths = set(scanned_paths)
    exact: list[Path] = []
    keyword: list[tuple[int, Path]] = []
    truncated = False
    oversized = False
    for root in RAW_CAPTURE_ROOTS:
        directory = vault / root
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.md")):
            if time.monotonic() >= deadline:
                truncated = True
                break
            rel = path.relative_to(vault).as_posix()
            if _is_excluded(rel) or path.is_symlink() or path.name == "index.md":
                continue
            if not _safe_within(vault, path) or _is_synthetic_path(path):
                continue
            # A symlinked parent or a resolved path under an excluded segment
            # (for example ``.stversions``) is not a permitted raw capture even
            # though the literal relative path looks clean.
            resolved_rel = _resolved_relative(vault, path)
            if (
                resolved_rel is None
                or _symlinked_ancestor(vault, path)
                or not _within_raw_roots(resolved_rel)
                or _is_excluded(resolved_rel)
                or _is_technical_record(resolved_rel)
            ):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_SOURCE_FILE_BYTES:
                oversized = True
                continue
            if scanned_bytes + size > max_candidate_bytes:
                truncated = True
                break
            text = _read_bounded(path, MAX_SOURCE_FILE_BYTES)
            if text is None:
                continue
            scanned_bytes += len(text.encode("utf-8"))
            scanned_paths.add(path)
            if _is_synthetic_content(text):
                continue
            if any(term in path.stem.casefold() for term in terms):
                exact.append(path)
                continue
            hits = sum(1 for term in terms if term in text.casefold())
            if hits:
                keyword.append((hits, path))
        if truncated:
            break
    ranked = exact + [
        item[1] for item in sorted(keyword, key=lambda row: (-row[0], str(row[1])))
    ]
    return ranked, truncated, oversized, scanned_bytes, scanned_paths


def _candidate_inventory_digest(
    vault: Path,
    note_paths: list[Path],
    raw_paths: list[Path],
    *,
    max_hash_bytes: int,
) -> tuple[str, bool]:
    """Bind one bounded candidate-discovery result to a content digest.

    Every ranked candidate is bound by a bounded content fingerprint
    (``[path, size, sha256]``), not only by its path.  A merely path-bound
    digest cannot see a name-matched note that keeps its place in the ranked
    set but only later gains relevant text, because such a note is not one of
    the packet's retained sources.  Content-binding catches it.

    Hashing respects the caller's aggregate byte bound: once the cumulative
    candidate content would exceed ``max_hash_bytes`` the remaining candidates
    are recorded path-only and ``complete`` is returned ``False``, so a large
    vault cannot force unbounded hashing.  The result is ``(digest, complete)``.
    """

    hashed_bytes = 0
    complete = True

    def entries(paths: list[Path]) -> list:
        nonlocal hashed_bytes, complete
        values: set[str] = set()
        for path in paths:
            try:
                values.add(path.relative_to(vault).as_posix())
            except ValueError:
                continue
        rows: list = []
        for rel in sorted(values):
            size = _bounded_file_size(vault / rel, MAX_SOURCE_FILE_BYTES)
            if size is None:
                raw_size = _file_size(vault / rel)
                if raw_size is not None and raw_size > MAX_SOURCE_FILE_BYTES:
                    # Over the per-file evidence bound, so this candidate can
                    # never become a source; a size-only row still moves if it
                    # later shrinks back under the bound.
                    rows.append([rel, raw_size, None])
                    continue
                complete = False
                rows.append([rel, None, None])
                continue
            if hashed_bytes + size > max_hash_bytes:
                complete = False
                rows.append([rel, size, None])
                continue
            content_hash = _sha256_file_bounded(vault / rel, MAX_SOURCE_FILE_BYTES)
            if content_hash is None:
                complete = False
                rows.append([rel, size, None])
                continue
            hashed_bytes += size
            rows.append([rel, size, content_hash])
        return rows

    digest = _sha256_hex({"notes": entries(note_paths), "raw": entries(raw_paths)})
    return digest, complete


def _candidate_inventory_record(
    vault: Path,
    note_paths: list[Path],
    raw_paths: list[Path],
    *,
    complete: bool,
    max_candidate_bytes: int,
    scope: str = "broad",
) -> dict:
    """A tiny, recheckable summary of a query's bounded candidate inventory.

    The digest content-binds every file the bounded lexical discovery ranked,
    so a relevant note or raw capture that arrives after gather -- or an
    existing, name-matched one that becomes relevant -- changes it even though
    no retained source changed.  ``complete`` is false when the scan stopped
    early or the aggregate hashing bound was reached; the packet reports that
    limit, and an incomplete inventory is not a complete statement to recheck
    against.  ``scope`` records whether the inventory came from a broad ranked
    discovery or from an explicitly chosen topic note's bounded linked scope.
    """

    digest, digest_complete = _candidate_inventory_digest(
        vault, note_paths, raw_paths, max_hash_bytes=max_candidate_bytes
    )
    return {
        "scope": scope,
        "digest": digest,
        "count": len(note_paths) + len(raw_paths),
        "complete": bool(complete) and digest_complete,
        "max_candidate_bytes": int(max_candidate_bytes),
    }


def _resolve_raw_link(vault: Path, target: str) -> Path | None:
    name = target.split("#", 1)[0].split("|", 1)[0].strip().strip("<>")
    if not name or name.startswith(("http://", "https://")):
        return None
    if ".." in Path(name).parts:
        return None
    candidates: list[Path] = []
    if name.endswith(".md"):
        candidates.append(vault / name.lstrip("/"))
    else:
        for root in RAW_CAPTURE_ROOTS:
            candidates.append(vault / root / f"{name}.md")
    for candidate in candidates:
        try:
            rel = candidate.relative_to(vault).as_posix()
        except ValueError:
            continue
        if not _within_raw_roots(rel) or _is_excluded(rel) or candidate.is_symlink():
            continue
        if _symlinked_ancestor(vault, candidate):
            continue
        if candidate.is_file() and _safe_within(vault, candidate):
            resolved_rel = _resolved_relative(vault, candidate)
            if (
                resolved_rel is None
                or not _within_raw_roots(resolved_rel)
                or _is_excluded(resolved_rel)
                or _is_technical_record(resolved_rel)
            ):
                continue
            return candidate.resolve()
    return None


def _linked_raw_captures(vault: Path, source: Path) -> list[Path]:
    """Raw captures a chosen topic note links or names explicitly."""

    text = _read_bounded(source, MAX_SOURCE_FILE_BYTES)
    if text is None:
        return []
    names = set(RAW_PATH_REF_RE.findall(text))
    names.update(MD_LINK_TARGET_RE.findall(text))
    names.update(LINK_RE.findall(text))
    found: list[Path] = []
    seen: set[Path] = set()
    for name in sorted(names):
        path = _resolve_raw_link(vault, name)
        if path is None or path in seen:
            continue
        seen.add(path)
        found.append(path)
    return found


def _explicit_raw_captures(vault: Path, rels: list[str]) -> list[Path]:
    """Resolve caller-selected raw captures, failing closed on any escape."""

    found: list[Path] = []
    for rel in rels:
        normalized = str(rel).replace("\\", "/").lstrip("./")
        candidate = vault / normalized
        if (
            not normalized
            or ".." in Path(normalized).parts
            or not _within_raw_roots(normalized)
        ):
            raise InvalidInput(
                "raw_capture_invalid", f"raw capture is not under a permitted raw root: {rel}"
            )
        if (
            _symlinked_ancestor(vault, candidate)
            or candidate.is_symlink()
            or not candidate.is_file()
        ):
            raise InvalidInput("raw_capture_missing", f"raw capture is unavailable: {rel}")
        # Re-apply the raw-root/exclusion rules to the resolved path: a symlink
        # whose target lives under ``.stversions`` (or any excluded segment),
        # or that escapes the vault, is rejected even when the literal relative
        # path looked permitted.
        resolved_rel = _resolved_relative(vault, candidate)
        if resolved_rel is None:
            raise InvalidInput(
                "raw_capture_invalid", f"raw capture escapes the vault: {rel}"
            )
        if (
            not _within_raw_roots(resolved_rel)
            or _is_excluded(resolved_rel)
            or _is_technical_record(resolved_rel)
            or _is_synthetic_path(candidate)
        ):
            raise InvalidInput(
                "raw_capture_invalid",
                f"raw capture resolves outside a permitted raw scope: {rel}",
            )
        resolved = candidate.resolve()
        if resolved not in found:
            found.append(resolved)
    return found


def _is_synthetic_path(path: Path) -> bool:
    return "synthetic" in path.name.casefold()


def _linked_notes(vault: Path, source: Path, limit: int) -> list[Path]:
    text = _read_bounded(source, MAX_SOURCE_FILE_BYTES)
    if text is None:
        return []
    names = {match.strip() for match in LINK_RE.findall(text)}
    found: list[Path] = []
    for name in sorted(names):
        if not name or "/" in name or ".." in name:
            continue
        for root in ALLOWED_NOTE_ROOTS:
            candidate = vault / root / f"{name}.md"
            rel = candidate.relative_to(vault).as_posix()
            if candidate.is_file() and not _is_excluded(rel) and _safe_within(vault, candidate):
                found.append(candidate)
                break
        if len(found) >= limit:
            break
    return found


def _parse_aware(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _freshness_stale(observed_at: object) -> bool:
    when = _parse_aware(observed_at)
    if when is None:
        return True
    age = (datetime.now(timezone.utc) - when).total_seconds()
    return age > FRESHNESS_THRESHOLD_SECONDS


def _validated_backlog_aggregate(data: object) -> dict | None:
    """Return one truthful turn-level backlog observation, or ``None``.

    ``capture-status.json`` counts session files, not completed turns.  The
    native ``memory-backlog`` command persists turn-level aggregates beside
    its run receipts; this validator accepts only a complete, internally
    consistent aggregate so an incomplete scan is reported as unknown rather
    than fabricated zero pending work.
    """

    if not isinstance(data, dict):
        return None
    checked_at = data.get("checked_at")
    if _parse_aware(checked_at) is None:
        return None
    if data.get("complete") is not True:
        return None
    if data.get("status") not in {"ok", "needs_attention"}:
        return None
    pending = data.get("pending_turns")
    eligible = data.get("eligible_turns")
    extracted = data.get("extracted_turns")
    if type(pending) is not int or pending < 0:
        return None
    if type(eligible) is not int or eligible < 0:
        return None
    if type(extracted) is not int or extracted < 0 or extracted > eligible:
        return None
    if extracted + pending > eligible:
        return None
    oldest = data.get("oldest_pending_at")
    if pending == 0:
        if oldest is not None:
            return None
        oldest_pending = None
    else:
        if _parse_aware(oldest) is None:
            return None
        oldest_pending = oldest
    return {
        "checked_at": checked_at,
        "pending_turns": pending,
        "oldest_pending_at": oldest_pending,
        "eligible_turns": eligible,
        "extracted_turns": extracted,
        "window": {
            "since": data.get("since") if isinstance(data.get("since"), str) else None,
            "through": data.get("through") if isinstance(data.get("through"), str) else None,
        },
        "writer_role": data.get("writer_role")
        if data.get("writer_role") in {"home", "sjm-source-only"}
        else None,
    }


def _latest_backlog_aggregate(state_root: Path | None) -> dict | None:
    """Read the newest bounded, validated turn-level backlog aggregate."""

    if state_root is None:
        return None
    try:
        if state_root.is_symlink() or not state_root.is_dir():
            return None
    except OSError:
        return None
    candidates = sorted(
        state_root.glob("backlog-aggregate-*.json"), reverse=True
    )[:MAX_BACKLOG_RECEIPTS]
    latest: dict | None = None
    latest_at: datetime | None = None
    for candidate in candidates:
        raw = _read_bytes_bounded(candidate, MAX_COVERAGE_RECEIPT_BYTES)
        if raw is None:
            continue
        try:
            parsed = _validated_backlog_aggregate(
                json.loads(raw.decode("utf-8", errors="replace"))
            )
        except ValueError:
            continue
        if parsed is None:
            continue
        observed = _parse_aware(parsed["checked_at"])
        if observed is None:
            continue
        if latest_at is None or observed > latest_at:
            latest = parsed
            latest_at = observed
    return latest


def _coverage_receipt(path: Path | None, *, state_root: Path | None = None) -> dict:
    """Read bounded coverage evidence without scanning session history.

    The session inventory supplies only its own session-file counts.  Pending
    turns and the oldest pending source time come solely from a validated
    turn-level backlog aggregate; otherwise they remain unknown with an
    explicit reason.
    """

    result = {
        "available": False,
        "observed_at": None,
        "observed_stale": True,
        "capture_observed_at": None,
        "pending_turns": None,
        "oldest_pending_at": None,
        "pending_observed_at": None,
        "pending_stale": None,
        "failed": None,
        "label": "global legacy local inventory",
        "window": None,
        "stale": True,
    }
    capture_observed: str | None = None
    if path is not None and path.is_file() and not path.is_symlink():
        raw = _read_bytes_bounded(path, MAX_COVERAGE_RECEIPT_BYTES)
        if raw is not None:
            try:
                data = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                data = None
            if isinstance(data, dict):
                counts = data.get("counts")
                counts = counts if isinstance(counts, dict) else {}
                observed = data.get("checked_at") or data.get("last_discovered")
                capture_observed = observed if isinstance(observed, str) else None
                result.update(
                    {
                        "available": True,
                        "capture_observed_at": capture_observed,
                        "observed_at": capture_observed,
                        "session_waiting": counts.get("waiting")
                        if type(counts.get("waiting")) is int
                        else None,
                        "failed": counts.get("failed")
                        if type(counts.get("failed")) is int
                        else None,
                        "pending_reason": (
                            "capture_status_waiting_is_session_count_not_pending_turns"
                        ),
                        "label": "global legacy local inventory",
                        "window": data.get("window")
                        if isinstance(data.get("window"), dict)
                        else None,
                    }
                )

    backlog = _latest_backlog_aggregate(state_root)
    if backlog is not None:
        result.update(
            {
                "available": True,
                "pending_observed_at": backlog["checked_at"],
                "pending_turns": backlog["pending_turns"],
                "oldest_pending_at": backlog["oldest_pending_at"],
                "pending_source": "memory_backlog_aggregate",
                "pending_reason": None,
                "window": backlog["window"],
            }
        )
        capture_when = _parse_aware(capture_observed)
        pending_when = _parse_aware(backlog["checked_at"])
        if (
            pending_when is not None
            and (capture_when is None or pending_when > capture_when)
        ):
            result["observed_at"] = backlog["checked_at"]
    elif state_root is not None:
        # A caller that supplied a state root asked for turn-level evidence;
        # say why it is not being reported instead of leaving a silent null.
        result["pending_reason"] = "turn_level_backlog_unavailable"

    # The receipt mixes two independent bounded observations.  A fresh
    # session-file inventory must not make an older turn-level pending count
    # look fresh: the pending number carries its own observation time, so a
    # reported zero is never presented as a current catch-up claim.  The
    # overall receipt is stale when either contributing observation is.
    pending_observed = result["pending_observed_at"]
    result["pending_stale"] = (
        _freshness_stale(pending_observed) if pending_observed else None
    )
    result["observed_stale"] = _freshness_stale(result["observed_at"])
    result["stale"] = bool(result["observed_stale"] or result["pending_stale"])
    return result


def _render_memory_evidence(
    state: dict,
    snapshot_hash: str,
    source_id: str,
    terms: list[str],
    *,
    max_evidence: int = MAX_EVIDENCE,
    require_key_or_value: bool = False,
    topic_terms: list[str] | None = None,
    topic_ref: str | None = None,
) -> tuple[list[dict], list[dict], set[str]]:
    evidence: list[dict] = []
    corrections: list[dict] = []
    touched_keys: set[str] = set()
    emitted_items: set[str] = set()
    index = automatic_memory._item_index(state["records"])
    # With an explicit topic note, a record only enters the packet if it is
    # bound to that topic: its key/value names a topic entity term, or an
    # accepted correction recorded against the same ``topic_ref`` references
    # it.  This stops a generic query word ("continue") from dragging in
    # unrelated memory, while the correction walk below still carries a
    # relevant cross-key replacement even when its own key does not match.
    topic_item_ids: set[str] = set()
    if topic_ref is not None:
        for correction in state.get("corrections", []):
            if correction["topic_ref"] == topic_ref:
                topic_item_ids.add(correction["target_item_id"])
                topic_item_ids.add(correction["source_item_id"])
    for key in sorted(state["records"]):
        record = state["records"][key]
        key_value_text = " ".join(
            [key] + [version["value"] for version in record["versions"]]
        )
        if topic_terms:
            record_ids = {version["item_id"] for version in record["versions"]}
            if not _matches(key_value_text, topic_terms) and not (
                record_ids & topic_item_ids
            ):
                continue
        else:
            if require_key_or_value:
                # An explicit topic note narrows memory scope: a match that
                # exists only in an incidental evidence quote must not widen
                # the search.
                haystack = key_value_text
            else:
                haystack = " ".join(
                    [key_value_text]
                    + [version["evidence_quote"] for version in record["versions"]]
                )
            if not _matches(haystack, terms):
                continue
        touched_keys.add(key)
        dispositions = automatic_memory.item_dispositions(state, key)
        ordered = sorted(
            record["versions"],
            key=lambda row: (row["source_time"], row["source_identity"], row["item_id"]),
        )
        for version in ordered:
            item = dispositions.get(
                version["item_id"],
                {"disposition": "current", "replacement_item_id": None},
            )
            evidence.append(
                _memory_span(
                    source_id=source_id,
                    snapshot_hash=snapshot_hash,
                    key=key,
                    kind=record["kind"],
                    version=version,
                    disposition=item["disposition"],
                    replacement_item=item["replacement_item_id"],
                    durable=True,
                )
            )
            emitted_items.add(version["item_id"])
    # A replacement or competing alternative may live under a key that did not
    # match the query terms, and a supersede chain may be several hops long.
    # Walk the accepted relations transitively (bounded and cycle-safe) so a
    # superseded A->B->C chain, a retraction at a later hop, or a deeper
    # unresolved branch never renders without its current or alternative
    # evidence.
    corrections_by_target: dict[str, list[dict]] = {}
    for correction in state.get("corrections", []):
        corrections_by_target.setdefault(correction["target_item_id"], []).append(
            correction
        )
    protected_ids = {
        item_id
        for correction in state.get("corrections", [])
        for item_id in (correction["target_item_id"], correction["source_item_id"])
    }

    def evict_for_capacity() -> bool:
        """Make room for a correction partner without orphaning that partner.

        Matched key/value spans can consume the whole evidence bound before a
        nonmatching successor is reached.  Drop the lowest-priority span that
        is not an endpoint of an accepted relation, so the correction pair
        still fits as a unit.  If every retained span is protected, the caller
        leaves the relation incomplete and the final packet prunes it with an
        explicit partial reason.
        """

        candidates = [
            index
            for index, span in enumerate(evidence)
            if span.get("item_id") not in protected_ids
        ]
        if not candidates:
            return False
        victim = min(
            candidates,
            key=lambda index: (_evidence_trim_priority(evidence[index]), -index),
        )
        evidence.pop(victim)
        return True

    def emit_unmatched(item_id: str) -> bool:
        if item_id in emitted_items or item_id not in index:
            return False
        key, kind, version = index[item_id]
        disposition = automatic_memory.item_dispositions(state, key).get(
            item_id, {"disposition": "current", "replacement_item_id": None}
        )
        evidence.append(
            _memory_span(
                source_id=source_id,
                snapshot_hash=snapshot_hash,
                key=key,
                kind=kind,
                version=version,
                disposition=disposition["disposition"],
                replacement_item=disposition["replacement_item_id"],
                durable=True,
            )
        )
        emitted_items.add(item_id)
        return True

    pending: deque[str] = deque()
    for span in evidence:
        item_id = span.get("item_id")
        if item_id is not None:
            pending.append(item_id)
        replacement = span.get("replacement_item_id")
        if replacement is not None:
            pending.append(replacement)
    expanded = 0
    blocked = False
    while pending and expanded < MAX_CORRECTION_EXPANSION:
        item_id = pending.popleft()
        for correction in corrections_by_target.get(item_id, []):
            successor = correction["source_item_id"]
            if successor in emitted_items:
                continue
            if len(evidence) >= max_evidence and not evict_for_capacity():
                blocked = True
                break
            if emit_unmatched(successor):
                expanded += 1
                pending.append(successor)
        if blocked:
            break
    final_item_ids = {
        span["item_id"] for span in evidence if span.get("item_id") is not None
    }
    for correction in state.get("corrections", []):
        if (
            correction["target_item_id"] in final_item_ids
            or correction["source_item_id"] in final_item_ids
        ):
            corrections.append(
                {
                    "correction_id": correction["correction_id"],
                    "relation": correction["relation"],
                    "target_item_id": correction["target_item_id"],
                    "source_item_id": correction["source_item_id"],
                    "topic_ref": correction["topic_ref"],
                    "field": correction["field"],
                    "status": "accepted",
                }
            )
    return evidence, corrections, touched_keys


def _memory_span(
    *,
    source_id: str,
    snapshot_hash: str,
    key: str,
    kind: str,
    version: dict,
    disposition: str,
    replacement_item: str | None,
    durable: bool,
) -> dict:
    item_id = version["item_id"]
    evidence_id = "ev:" + _sha256_hex(
        {"type": "memory", "item_id": item_id, "snapshot": snapshot_hash}
    )
    span = {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "kind": "memory",
        "key": key,
        "memory_kind": kind,
        "item_id": item_id,
        "quote": version["evidence_quote"],
        "value": version["value"],
        "speaker_source": version["speaker_source"],
        "source_time": version["source_time"],
        "source_date": version["source_time"][:10],
        "date_quality": "recorded",
        "citation": version["citation"],
        "authority": "keith_statement"
        if version["speaker_source"] == "keith"
        else "assistant_claim",
        "locator": f"key={key}#item={item_id}",
        "disposition": disposition,
        "durable": durable,
        "pending_home": not durable,
    }
    if replacement_item is not None:
        span["replacement_item_id"] = replacement_item
    return span


def _read_provisional_envelopes(
    provisional_root: Path | None,
    *,
    limit: int = MAX_PROVISIONAL_ENVELOPES,
    max_bytes: int = MAX_PROVISIONAL_BYTES,
) -> dict:
    """Bounded, read-only outbox scan shared by the provisional readers."""

    result = {"envelopes": [], "failed": 0, "truncated": False}
    if provisional_root is None or not provisional_root.is_dir():
        return result
    outbox = provisional_root / "outbox"
    if not outbox.is_dir():
        return result
    total = 0
    for index, path in enumerate(sorted(outbox.glob("*.json"))):
        if index >= limit:
            result["truncated"] = True
            break
        raw = _read_bytes_bounded(path, MAX_SOURCE_FILE_BYTES)
        if raw is None:
            result["failed"] += 1
            continue
        if total + len(raw) > max_bytes:
            result["truncated"] = True
            break
        total += len(raw)
        try:
            envelope = automatic_memory._validate_envelope(
                json.loads(raw.decode("utf-8", errors="replace")), durable=True
            )
        except (ValueError, automatic_memory.StateCorruptionError):
            result["failed"] += 1
            continue
        result["envelopes"].append(envelope)
    return result


def _provisional_hash_from_scan(scanned: dict) -> str:
    """Bind provisional freshness to identity, delivery state and counts.

    Key lists alone do not change when a pending delivery becomes offline or
    failed, so a packet could otherwise keep stale delivery/coverage counts.
    Include every validated envelope (including exhausted work, which is
    excluded from evidence but still changes coverage) and the bounded scan
    failure/truncation counts.
    """

    envelopes = []
    for envelope in scanned["envelopes"]:
        envelopes.append(
            {
                "delivery_id": envelope["delivery_id"],
                "content_id": envelope["content_id"],
                "status": envelope["status"],
                "attempt_count": envelope["attempt_count"],
                "max_attempts": envelope["max_attempts"],
                "last_attempt_at": envelope["last_attempt_at"],
                "last_error": envelope["last_error"],
                "retry_after_seconds": envelope["retry_after_seconds"],
                "item_keys": sorted(item["key"] for item in envelope["bundle"]["items"]),
            }
        )
    return _sha256_hex(
        {
            "failed": scanned["failed"],
            "truncated": scanned["truncated"],
            "envelopes": envelopes,
        }
    )


def _provisional_state_hash(provisional_root: Path | None) -> str | None:
    if provisional_root is None or not provisional_root.is_dir():
        return None
    return _provisional_hash_from_scan(_read_provisional_envelopes(provisional_root))


def _provisional_delivery_counts_from_scan(
    provisional_root: Path | None, scanned: dict
) -> dict | None:
    """Derive delivery counts from one already-completed bounded outbox scan."""

    if provisional_root is None or not provisional_root.is_dir():
        return None
    outbox = provisional_root / "outbox"
    if not outbox.is_dir():
        return {
            "pending": 0,
            "offline": 0,
            "failed": 0,
            "exhausted": 0,
            "accepted": 0,
        }
    counts = {
        "pending": 0,
        "offline": 0,
        "failed": scanned["failed"],
        "exhausted": 0,
        "accepted": 0,
    }
    for envelope in scanned["envelopes"]:
        counts[envelope["status"]] = counts.get(envelope["status"], 0) + 1
    counts["truncated"] = scanned["truncated"]
    return counts


def _provisional_delivery_counts(provisional_root: Path | None) -> dict | None:
    """Read-only outbox delivery counts; exhausted work is never called published."""

    if provisional_root is None or not provisional_root.is_dir():
        return None
    return _provisional_delivery_counts_from_scan(
        provisional_root, _read_provisional_envelopes(provisional_root)
    )


def _empty_home_state() -> dict:
    return {"schema_version": 1, "records": {}, "corrections": []}


def _emit_note_sources(
    vault: Path,
    paths: list[Path],
    terms: list[str],
    *,
    sources: list[dict],
    evidence: list[dict],
    reasons: list[str],
    scanned_bytes: int,
    scanned_paths: set[Path],
    head_all: set[Path],
    head_first: set[Path],
    anchor_path: Path | None = None,
    max_sources: int,
    max_evidence: int,
    max_candidate_bytes: int,
    deadline: float,
) -> int:
    """Bind note/raw paths to sources and evidence, sharing one byte budget.

    A ranked candidate only becomes a source when it keeps at least one
    evidence span, so a name-only match cannot crowd out a relevant one.
    """

    for path in paths:
        if len(sources) >= max_sources or time.monotonic() >= deadline:
            reasons.append("source_limit" if len(sources) >= max_sources else "time_budget")
            break
        rel = path.relative_to(vault).as_posix()
        text = _read_bounded(path, MAX_SOURCE_FILE_BYTES)
        if text is None:
            reasons.append("oversized_or_unreadable_source")
            continue
        current_bytes = len(text.encode("utf-8"))
        # Charge the single cumulative candidate-content budget.  Files already
        # counted during ranking are not charged twice; newly read exact/topic
        # matches and explicit selections are.
        if path not in scanned_paths:
            if scanned_bytes + current_bytes > max_candidate_bytes:
                reasons.append("candidate_byte_limit")
                break
            scanned_bytes += current_bytes
            scanned_paths.add(path)
        # Compute spans before binding the source so a matched-only-in-name
        # candidate that keeps no span never consumes a source slot.
        spans, spans_truncated = _spans(
            text,
            terms,
            take_head=path in head_all,
            prefer_body=path == anchor_path,
        )
        if not spans and path in head_first:
            # A discovery-ranked note always contributes at least its head so
            # a matched entity is visible even when its body uses other words.
            head_only, _ = _spans(text, terms, take_head=True)
            spans = head_only[:1]
        if spans_truncated:
            # A long source dropped some matching passages between the retained
            # oldest and newest ones; the packet says so instead of implying
            # that its span list is complete.
            reasons.append("source_span_limit")
        if not spans:
            reasons.append("no_retained_spans")
            continue
        if len(evidence) >= max_evidence:
            reasons.append("evidence_limit")
            break
        content_hash = _sha256_bytes(text.encode("utf-8"))
        source_id = "src:" + _sha256_hex({"rel": rel, "content_hash": content_hash})
        source_date = _frontmatter_date(text)
        source_type = "raw_capture" if _within_raw_roots(rel) else "vault_note"
        sources.append(
            {
                "source_id": source_id,
                "source_type": source_type,
                "relative_path": rel,
                "content_hash": content_hash,
                "source_date": source_date,
                "date_quality": "frontmatter" if source_date else "unknown",
                "speaker": None,
                "authority_category": source_type,
                "vault_relative": True,
                "pending_home": False,
                "local_only": False,
            }
        )
        for quote, start_line, end_line, byte_start, byte_end in spans:
            if len(evidence) >= max_evidence:
                reasons.append("evidence_limit")
                break
            locator = _span_locator(start_line, end_line, byte_start, byte_end)
            evidence.append(
                {
                    "evidence_id": "ev:"
                    + _sha256_hex(
                        {"source_id": source_id, "locator": locator, "quote": quote}
                    ),
                    "source_id": source_id,
                    "kind": "note",
                    "quote": quote,
                    "locator": locator,
                    "source_time": None,
                    "source_date": source_date,
                    "date_quality": "frontmatter" if source_date else "unknown",
                    "speaker": None,
                    "authority": source_type,
                    "disposition": "current",
                    "durable": True,
                }
            )
    return scanned_bytes


def gather(
    query: str,
    vault_root: Path,
    *,
    topic_note: str | None = None,
    raw_sources: list[str] | None = None,
    published_memory: Path | None = None,
    semantic_shelf: Path | None = None,
    provisional_root: Path | None = None,
    coverage_receipt: Path | None = None,
    coverage_state_root: Path | None = None,
    extra_terms: list[str] | None = None,
    now: str | None = None,
    max_sources: int = MAX_SOURCES,
    max_evidence: int = MAX_EVIDENCE,
    max_bytes: int = MAX_PACKET_BYTES,
    max_candidate_bytes: int = MAX_CANDIDATE_BYTES,
    deadline_seconds: float = GATHER_DEADLINE_SECONDS,
) -> dict:
    """Return a bounded, source-linked evidence packet.  Never writes state."""

    vault = Path(vault_root).expanduser().resolve()
    if not vault.is_dir():
        raise UnavailableState("vault_unavailable", f"vault is not a directory: {vault}")
    query = _require_text(query, "query", max_chars=400)
    generated_at = _iso(now, "gather now") if now else _now_iso()
    deadline = time.monotonic() + max(0.1, deadline_seconds)
    terms = _query_terms(query)
    followups, followups_bounded = _bounded_followup_terms(extra_terms, terms)
    if followups:
        terms = list(dict.fromkeys(terms + followups))

    reasons: list[str] = []
    if followups_bounded:
        reasons.append("extra_terms_bounded")
    sources: list[dict] = []
    evidence: list[dict] = []
    corrections: list[dict] = []
    # Only a broad (non-topic) query performs ranked candidate discovery, so
    # only that branch carries a candidate inventory for validation to recheck.
    candidate_inventory: dict | None = None
    scanned_bytes = 0
    scanned_paths: set[Path] = set()

    explicit_topic = bool(topic_note)
    # With an explicit topic note, memory is bound to that topic's entity
    # terms (or to an accepted correction recorded against the same note).
    topic_terms = _topic_scope_terms(topic_note, terms) if topic_note else []
    # A caller-selected raw capture is resolved and validated once, up front.
    # That makes an invalid or symlinked selection fail closed whether or not a
    # topic note is supplied, and lets both the scoped and the broad branch
    # honour the explicit source instead of silently dropping it.
    explicit_raw: list[Path] = (
        _explicit_raw_captures(vault, raw_sources) if raw_sources else []
    )

    # Read and render the published key/value store up front so its exact
    # bounded headroom can be reserved: a broad query must not spend every
    # source slot and evidence span on notes and raw captures before the memory
    # snapshot is considered.  The overall 20-source/80-span bounds are
    # unchanged; notes simply cannot consume the slots memory will need.
    memory_path = published_memory or (vault / PUBLISHED_MEMORY_REL)
    memory_state = _empty_home_state()
    snapshot_hash: str | None = None
    # Distinguish four states so a coverage gap is never silent and a healthy
    # unsupported-but-present snapshot is not reported as corrupt.  ``absent``
    # is a real gap on Home where the snapshot should exist; on SJM it means
    # Home publication may be unavailable.  ``present`` carries the snapshot
    # hash; ``unsupported`` and ``unreadable`` mean the file exists but could
    # not be validated, which is different from simply missing.
    published_memory_state: dict = {"state": "absent", "snapshot_hash": None}
    if memory_path.is_file() and not memory_path.is_symlink():
        raw = _read_bytes_bounded(memory_path, MAX_MEMORY_FILE_BYTES)
        if raw is None:
            reasons.append("published_memory_oversized")
            published_memory_state = {"state": "unsupported", "snapshot_hash": None}
        else:
            snapshot_hash = _sha256_bytes(raw)
            published_memory_state = {
                "state": "present",
                "snapshot_hash": snapshot_hash,
            }
            try:
                memory_state = automatic_memory._validate_memory_state(
                    json.loads(raw.decode("utf-8", errors="replace"))
                )
            except (ValueError, automatic_memory.StateCorruptionError) as exc:
                raise UnavailableState(
                    "memory_state_corrupt", "published automatic memory is unreadable"
                ) from exc
    else:
        reasons.append("published_memory_absent")
    mem_source_id: str | None = None
    mem_evidence: list[dict] = []
    mem_corrections: list[dict] = []
    if snapshot_hash and terms:
        mem_source_id = "src:" + _sha256_hex(
            {"rel": PUBLISHED_MEMORY_REL, "snapshot": snapshot_hash}
        )
        mem_evidence, mem_corrections, _ = _render_memory_evidence(
            memory_state,
            snapshot_hash,
            mem_source_id,
            terms,
            max_evidence=max_evidence,
            require_key_or_value=explicit_topic,
            topic_terms=topic_terms,
            topic_ref=topic_note,
        )
    reserved_sources = 1 if (mem_evidence and max_sources > 1) else 0
    # An explicitly selected topic note is the caller's scope anchor, so keep
    # one evidence slot in reserve for it: a memory-heavy snapshot must not
    # consume every span before the note is even emitted.
    anchor_reserve = 1 if (topic_note and max_evidence > 0) else 0
    # A broad query has no caller-selected anchor, so reserve a bounded floor
    # for the ranked ordinary notes instead: a high-volume memory snapshot must
    # not consume every span before a strongly relevant note is emitted.  The
    # reservation only binds when memory would otherwise take the whole budget,
    # and never claims more than half the span budget so a small caller bound
    # cannot suppress the memory snapshot wholesale.
    broad_note_reserve = (
        min(BROAD_NOTE_EVIDENCE_RESERVE, max_evidence // 2)
        if (not topic_note and terms and max_evidence > 0)
        else 0
    )
    note_reserve = max(anchor_reserve, broad_note_reserve)
    reserved_evidence = min(len(mem_evidence), max(0, max_evidence - note_reserve))
    note_max_sources = max_sources - reserved_sources
    note_max_evidence = max_evidence - reserved_evidence

    if topic_note:
        note_path = (vault / topic_note).resolve()
        if not _safe_within(vault, note_path):
            raise InvalidInput("topic_note_escapes_vault", "topic note escapes the vault")
        rel_note = note_path.relative_to(vault).as_posix()
        if not _within_note_roots(rel_note):
            raise InvalidInput(
                "topic_note_invalid",
                "topic note must be an ordinary note under a permitted note root",
            )
        if note_path.suffix != ".md" or note_path.name == "index.md":
            raise InvalidInput("topic_note_invalid", "topic note must be an ordinary note")
        if note_path.is_symlink() or not note_path.is_file() or _is_excluded(rel_note):
            raise InvalidInput("topic_note_missing", f"topic note is unavailable: {topic_note}")
        note_paths = [note_path, *_linked_notes(vault, note_path, MAX_LINK_HOPS)]
        # An explicit chosen note constrains raw scope: only directly linked
        # captures and explicitly selected ones may widen it.  An explicit
        # ``--raw-capture`` adds to the linked captures instead of replacing
        # them, so the note's own linked decision is never silently dropped.
        linked_raw = _linked_raw_captures(vault, note_path)
        # Explicit selections keep priority over transitively linked ones;
        # duplicate paths collapse so a capture is emitted at most once.
        raw_selected: list[Path] = []
        raw_seen: set[Path] = set()
        for path in [*explicit_raw, *linked_raw]:
            if path in raw_seen:
                continue
            raw_seen.add(path)
            raw_selected.append(path)
        # The chosen note's own bounded scope (the note, its linked notes and
        # its linked captures) is the candidate inventory for a topic query.
        # Explicitly selected captures separately become retained sources.
        candidate_inventory = _candidate_inventory_record(
            vault,
            note_paths,
            linked_raw,
            complete=True,
            max_candidate_bytes=max_candidate_bytes,
            scope="topic",
        )
        if not candidate_inventory["complete"]:
            reasons.append("candidate_inventory_content_limit")
        head_all = {note_path} | set(raw_selected)
        scanned_bytes = _emit_note_sources(
            vault,
            note_paths,
            terms,
            sources=sources,
            evidence=evidence,
            reasons=reasons,
            scanned_bytes=scanned_bytes,
            scanned_paths=scanned_paths,
            head_all=head_all,
            head_first=head_all,
            anchor_path=note_path,
            max_sources=note_max_sources,
            max_evidence=note_max_evidence,
            max_candidate_bytes=max_candidate_bytes,
            deadline=deadline,
        )
        scanned_bytes = _emit_note_sources(
            vault,
            raw_selected,
            terms,
            sources=sources,
            evidence=evidence,
            reasons=reasons,
            scanned_bytes=scanned_bytes,
            scanned_paths=scanned_paths,
            head_all=head_all,
            head_first=head_all,
            max_sources=note_max_sources,
            max_evidence=note_max_evidence,
            max_candidate_bytes=max_candidate_bytes,
            deadline=deadline,
        )
    elif terms:
        (
            note_paths,
            truncated,
            oversized,
            scanned_bytes,
            scanned_paths,
            discovery_head,
        ) = _candidate_note_files(
            vault, terms, deadline, max_candidate_bytes
        )
        if truncated:
            reasons.append("candidate_byte_limit")
        if oversized:
            reasons.append("oversized_or_unreadable_source")
        scanned_bytes = _emit_note_sources(
            vault,
            note_paths,
            terms,
            sources=sources,
            evidence=evidence,
            reasons=reasons,
            scanned_bytes=scanned_bytes,
            scanned_paths=scanned_paths,
            head_all=set(),
            head_first=discovery_head,
            max_sources=note_max_sources,
            max_evidence=note_max_evidence,
            max_candidate_bytes=max_candidate_bytes,
            deadline=deadline,
        )
        raw_paths, raw_truncated, raw_oversized, scanned_bytes, scanned_paths = (
            _candidate_raw_files(
                vault,
                terms,
                deadline,
                max_candidate_bytes,
                start_bytes=scanned_bytes,
                scanned_paths=scanned_paths,
            )
        )
        candidate_inventory = _candidate_inventory_record(
            vault,
            note_paths,
            raw_paths,
            complete=not (truncated or raw_truncated),
            max_candidate_bytes=max_candidate_bytes,
        )
        if not candidate_inventory["complete"] and not (truncated or raw_truncated):
            # The ranked set was complete, but content-binding it hit the
            # aggregate hashing bound, so the recorded digest is not a complete
            # statement to recheck against.
            reasons.append("candidate_inventory_content_limit")
        if raw_truncated:
            reasons.append("candidate_byte_limit")
        if raw_oversized:
            reasons.append("oversized_or_unreadable_source")
        # An explicit ``--raw-capture`` keeps priority over the ranked
        # discovery set and is never silently dropped: it is emitted first and
        # de-duplicated by resolved path.  Its head is retained so an
        # explicitly chosen capture still surfaces when it does not match the
        # query terms.
        explicit_resolved = {path.resolve() for path in explicit_raw}
        ranked_raw = [
            path for path in raw_paths if path.resolve() not in explicit_resolved
        ]
        raw_paths = [*explicit_raw, *ranked_raw]
        raw_head = set(explicit_raw)
        scanned_bytes = _emit_note_sources(
            vault,
            raw_paths,
            terms,
            sources=sources,
            evidence=evidence,
            reasons=reasons,
            scanned_bytes=scanned_bytes,
            scanned_paths=scanned_paths,
            head_all=raw_head,
            head_first=raw_head,
            max_sources=note_max_sources,
            max_evidence=note_max_evidence,
            max_candidate_bytes=max_candidate_bytes,
            deadline=deadline,
        )
    else:
        if explicit_raw:
            # An explicit selection with neither a topic note nor a usable
            # query term cannot be bound.  Reject the combination clearly
            # instead of validating the path and then dropping it silently.
            raise InvalidInput(
                "raw_capture_unbound",
                "an explicit raw capture needs a topic note or a usable query term",
            )
        reasons.append("no_query_terms")

    # Semantic shelf: bounded discovery aid, not authoritative evidence.  An
    # explicit topic note is the chosen scope, so a merely term-matching shelf
    # must not widen it.
    shelf = semantic_shelf or (vault / SEMANTIC_SHELF_REL)
    if (
        not explicit_topic
        and shelf.is_file()
        and len(sources) < note_max_sources
        and len(evidence) < note_max_evidence
    ):
        text = _read_bounded(shelf, MAX_SOURCE_FILE_BYTES)
        if text is not None:
            rel = SEMANTIC_SHELF_REL
            content_hash = _sha256_bytes(text.encode("utf-8"))
            source_id = "src:" + _sha256_hex({"rel": rel, "content_hash": content_hash})
            spans, spans_truncated = _spans(text, terms, take_head=False)
            if spans_truncated:
                reasons.append("source_span_limit")
            if spans:
                sources.append(
                    {
                        "source_id": source_id,
                        "source_type": "semantic_shelf",
                        "relative_path": rel,
                        "content_hash": content_hash,
                        "source_date": None,
                        "date_quality": "unknown",
                        "speaker": None,
                        "authority_category": "semantic_shelf",
                        "vault_relative": True,
                        "pending_home": False,
                        "local_only": False,
                    }
                )
                for quote, start_line, end_line, byte_start, byte_end in spans:
                    if len(evidence) >= note_max_evidence:
                        reasons.append("evidence_limit")
                        break
                    locator = _span_locator(start_line, end_line, byte_start, byte_end)
                    evidence.append(
                        {
                            "evidence_id": "ev:"
                            + _sha256_hex(
                                {
                                    "source_id": source_id,
                                    "locator": locator,
                                    "quote": quote,
                                }
                            ),
                            "source_id": source_id,
                            "kind": "note",
                            "quote": quote,
                            "locator": locator,
                            "source_time": None,
                            "source_date": None,
                            "date_quality": "unknown",
                            "speaker": None,
                            "authority": "semantic_shelf",
                            "disposition": "current",
                            "durable": True,
                        }
                    )

    # Append the published memory already read above.  Its headroom was
    # reserved, so this is a bounded, deterministic inclusion rather than a
    # late drop after the note/raw material has consumed every slot.
    if mem_evidence and mem_source_id is not None:
        if len(sources) < max_sources:
            sources.append(
                {
                    "source_id": mem_source_id,
                    "source_type": "published_memory",
                    "relative_path": PUBLISHED_MEMORY_REL,
                    "content_hash": snapshot_hash,
                    "snapshot_hash": snapshot_hash,
                    "source_date": max(
                        (
                            version["source_time"][:10]
                            for record in memory_state["records"].values()
                            for version in record["versions"]
                        ),
                        default=None,
                    ),
                    "date_quality": "derived",
                    "speaker": None,
                    "authority_category": "published_memory",
                    "vault_relative": True,
                    "pending_home": False,
                    "local_only": False,
                }
            )
            for span in mem_evidence:
                if len(evidence) >= max_evidence:
                    reasons.append("evidence_limit")
                    break
                evidence.append(span)
            corrections.extend(mem_corrections)
        else:
            # Published memory could not be included; say so rather than
            # silently dropping the key/value store.
            reasons.append("source_limit")

    # Any validated local provisional outbox.  One bounded scan supplies the
    # freshness hash and the delivery counts so the packet cannot pair values
    # observed from two different outbox states.
    provisional_scanned = _read_provisional_envelopes(provisional_root)
    provisional_hash = (
        _provisional_hash_from_scan(provisional_scanned)
        if provisional_root is not None and provisional_root.is_dir()
        else None
    )
    provisional_delivery = _provisional_delivery_counts_from_scan(
        provisional_root, provisional_scanned
    )
    if provisional_delivery and provisional_delivery.get("exhausted"):
        reasons.append("provisional_delivery_exhausted")
    if provisional_delivery and provisional_delivery.get("truncated"):
        reasons.append("provisional_scan_truncated")
    if provisional_delivery and provisional_delivery.get("failed"):
        # A malformed or unreadable outbox entry was skipped, so local coverage
        # is incomplete.  Surface it instead of returning an "ok"/"no_evidence"
        # packet that silently hides skipped provisional memory.
        reasons.append("provisional_scan_failed")

    if provisional_root is not None and terms:
        (
            provisional_spans,
            provisional_corrections,
            provisional_truncated,
        ) = _provisional_memory_evidence(
            provisional_root,
            terms,
            max_evidence - len(evidence),
            require_key_or_value=explicit_topic,
            topic_terms=topic_terms,
            scanned=provisional_scanned,
        )
        if provisional_truncated:
            # A matching provisional correction existed but did not fit the
            # evidence budget.  Say so rather than presenting the durable side
            # as a complete answer.
            reasons.append("provisional_evidence_limit")
        if provisional_spans:
            if len(sources) < max_sources:
                sources.append(
                    {
                        "source_id": provisional_spans[0]["source_id"],
                        "source_type": "provisional_memory",
                        "relative_path": None,
                        "content_hash": provisional_hash,
                        "source_date": None,
                        "date_quality": "unknown",
                        "speaker": None,
                        "authority_category": "local_provisional",
                        "vault_relative": False,
                        "pending_home": True,
                        "local_only": True,
                    }
                )
                evidence.extend(provisional_spans)
                corrections.extend(provisional_corrections)
            else:
                reasons.append("source_limit")

    # Deduplicate by item id, preferring durable evidence over provisional.
    evidence = _dedupe_evidence(evidence)
    corrections = _dedupe_corrections(corrections)
    # The leading ranked ordinary note of a broad query keeps a bounded set of
    # its spans through the byte trim, so a memory-rich packet still
    # source-links the best lexical candidate instead of dropping every note.
    retained_lead_ids = _lead_note_evidence_ids(sources, evidence, broad_note_reserve)

    coverage_value = _coverage_receipt(
        coverage_receipt, state_root=coverage_state_root or provisional_root
    )
    partial = bool(reasons)
    packet = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "kind": "k2b_context_packet",
        "query": query,
        "terms": terms,
        "topic": {"note_ref": topic_note} if topic_note else None,
        "generated_at": generated_at,
        "limits": {
            "max_sources": max_sources,
            "max_evidence": max_evidence,
            "max_bytes": max_bytes,
        },
        "sources": sources,
        "evidence": evidence,
        "corrections": corrections,
        "partial": partial,
        "reasons": sorted(set(reasons)),
        "coverage": {
            "sources": len(sources),
            "evidence": len(evidence),
            "scanned_bytes": scanned_bytes,
            "provisional_state_hash": provisional_hash,
            "provisional_delivery": provisional_delivery,
            "coverage_receipt": coverage_value,
        },
        "freshness": {
            "observed_at": generated_at,
            "snapshot_hash": snapshot_hash,
            "published_memory": published_memory_state,
            "cross_host_sync": "unverified",
            "provisional_included": any(not span.get("durable", True) for span in evidence),
            "coverage_receipt": coverage_value,
            "candidate_inventory": candidate_inventory,
        },
    }
    # Size the trim against the derived fields that will be added below.  The
    # placeholders use the longest possible status and a fixed-width packet id,
    # so sealing the packet after trimming cannot push it back over max_bytes.
    packet["status"] = "no_evidence"
    packet["packet_id"] = "packet:" + "0" * 64
    packet = _trim_packet(packet, max_bytes, retained_evidence_ids=retained_lead_ids)
    packet["status"] = (
        "partial"
        if packet["partial"]
        else "ok"
        if packet["evidence"]
        else "no_evidence"
    )
    packet["packet_id"] = "packet:" + _sha256_hex(_packet_manifest(packet))
    if _packet_bytes(packet) > max_bytes:
        raise InvalidInput(
            "packet_byte_limit_irreducible",
            "sealed packet exceeds max_bytes after trimming",
        )
    return packet


def _provisional_memory_evidence(
    provisional_root: Path,
    terms: list[str],
    budget: int,
    *,
    require_key_or_value: bool = False,
    topic_terms: list[str] | None = None,
    scanned: dict | None = None,
) -> tuple[list[dict], list[dict], bool]:
    """Matching local outbox spans, bounded by ``budget``.

    The scan always completes within the already-computed bounded envelope
    read so a match that does not fit the evidence budget is reported as
    ``truncated`` instead of vanishing.  A provisional correction of a durable
    claim must never be omitted while the older claim is presented as complete.
    """

    source_id = "src:" + _sha256_hex(
        {"rel": "provisional", "root": str(provisional_root.resolve())}
    )
    spans: list[dict] = []
    corrections: list[dict] = []
    truncated = False
    envelopes = (
        scanned["envelopes"]
        if scanned is not None
        else _read_provisional_envelopes(provisional_root)["envelopes"]
    )
    for envelope in envelopes:
        if envelope["status"] == "exhausted":
            continue
        bundle = envelope["bundle"]
        for item in bundle["items"]:
            if topic_terms:
                haystack = f"{item['key']} {item['value']}"
                if not _matches(haystack, topic_terms):
                    continue
            else:
                haystack = f"{item['key']} {item['value']}"
                if not require_key_or_value:
                    haystack += f" {item['evidence_quote']}"
                if not _matches(haystack, terms):
                    continue
            if len(spans) >= budget:
                truncated = True
                continue
            version = automatic_memory._version(bundle, item)
            span = _memory_span(
                source_id=source_id,
                snapshot_hash=envelope["content_id"],
                key=item["key"],
                kind=item["kind"],
                version=version,
                disposition="pending_home",
                replacement_item=None,
                durable=False,
            )
            span["pending_home"] = True
            spans.append(span)
    return spans, corrections, truncated


def _dedupe_evidence(evidence: list[dict]) -> list[dict]:
    by_item: dict[str, dict] = {}
    ordered: list[dict] = []
    for span in evidence:
        item_id = span.get("item_id")
        if item_id is None:
            ordered.append(span)
            continue
        existing = by_item.get(item_id)
        if existing is None:
            by_item[item_id] = span
            ordered.append(span)
            continue
        if existing.get("pending_home") and not span.get("pending_home"):
            index = ordered.index(existing)
            ordered[index] = span
            by_item[item_id] = span
    return ordered


def _dedupe_corrections(corrections: list[dict]) -> list[dict]:
    seen: set[str] = set()
    ordered: list[dict] = []
    for correction in corrections:
        if correction["correction_id"] in seen:
            continue
        seen.add(correction["correction_id"])
        ordered.append(correction)
    return ordered


def _packet_manifest(packet: dict) -> dict:
    """Bind the packet id to every substantive field, not just the manifest.

    Excludes only ``packet_id`` (what is being derived) and ``status`` (a
    derived summary).  Quotes, locators, dispositions, item metadata, coverage
    and freshness are therefore all inside the id, so a mutated packet either
    fails the id check or is rechecked against its real source below.
    """

    return {
        key: packet[key]
        for key in packet
        if key not in {"packet_id", "status"}
    }


def _packet_bytes(packet: dict) -> int:
    return len(json.dumps(packet, ensure_ascii=False).encode("utf-8"))


def _evidence_trim_priority(
    span: dict, *, anchor: bool = False, retained: bool = False
) -> int:
    """Lower is discarded first; curated memory outranks broad prose.

    ``anchor`` marks the explicitly selected topic note, ``retained`` the
    automatically retained leading note of a broad query.  The explicit
    caller choice outranks everything; the automatic best guess still outranks
    the memory snapshot it must survive, but yields to an accepted
    correction pair so curation is never traded away for a lexical guess.
    """

    if anchor:
        # The explicitly selected topic note.  A note the caller named must
        # stay source-linked whenever the byte bound can hold it, so it
        # outranks everything else.  An accepted correction pair is still
        # dropped as one unit -- both halves together -- just before, and in
        # preference to, sacrificing the anchor.
        return 6
    if retained:
        # The leading ranked ordinary note of a broad query.  No caller chose
        # it, so it yields to an accepted correction pair, but it outranks the
        # curated memory snapshot: a memory-rich packet must not evict every
        # ordinary note span while memory keeps its own.
        return 4
    if span.get("kind") == "memory":
        if not span.get("durable", True):
            # A validated but unpublished local provisional item.
            return 2
        if span.get("replacement_item_id") or span.get("disposition") in {
            "superseded",
            "retracted",
        }:
            # One half of a contradiction/correction pair: kept as long as the
            # byte bound allows, always dropped together with its partner, and
            # never left standing unqualified to make room for the anchor.
            return 5
        return 3
    if span.get("authority") == "semantic_shelf":
        return 0
    return 1


def _anchor_evidence_ids(packet: dict) -> set[int]:
    """``id()``s of the explicitly selected topic note's one reserved span.

    ``--topic-note`` names the caller's chosen scope anchor, recorded under
    ``topic.note_ref``.  Normalising that reference the same way as the emitted
    source's vault-relative posix path keeps the note's first real span out of
    the ordinary-prose trim, so a memory-rich packet can stay ``partial`` and
    honest while still source-linking the note the caller explicitly asked for.
    The span is reserved, not absolute: ``_trim_packet`` drops every other span
    first and then fails explicitly rather than returning a note-less packet, so
    a bound that cannot hold the anchor never implies the topic was covered.
    """

    topic = packet.get("topic")
    if not isinstance(topic, dict):
        return set()
    note_ref = topic.get("note_ref")
    if not isinstance(note_ref, str) or not note_ref:
        return set()
    wanted = posixpath.normpath(note_ref)
    anchor_sources = {
        source.get("source_id")
        for source in packet.get("sources", [])
        if source.get("relative_path") == wanted
    }
    if not anchor_sources:
        return set()
    for span in packet.get("evidence", []):
        if span.get("source_id") in anchor_sources:
            return {id(span)}
    return set()


def _lead_note_evidence_ids(
    sources: list[dict], evidence: list[dict], limit: int
) -> frozenset[str]:
    """Evidence ids of the highest-ranked ordinary note's retained spans.

    A broad query emits its ranked ordinary notes before the appended memory
    snapshot, so the first vault-note/raw-capture source is the best lexical
    candidate.  ``_trim_packet`` keeps a bounded number of that source's spans
    through the byte trim, so a memory-rich packet still source-links the most
    relevant note instead of dropping every ordinary note span.  The limit is a
    bound, not a promise: a note with fewer matches contributes only what it
    has, and memory still fills the rest of the packet.
    """

    if limit <= 0 or not sources or not evidence:
        return frozenset()
    lead_source_id = next(
        (
            source["source_id"]
            for source in sources
            if source.get("source_type") in {"vault_note", "raw_capture"}
        ),
        None,
    )
    if lead_source_id is None:
        return frozenset()
    ids: list[str] = []
    for span in evidence:
        if span.get("source_id") != lead_source_id:
            continue
        evidence_id = span.get("evidence_id")
        if not isinstance(evidence_id, str):
            continue
        ids.append(evidence_id)
        if len(ids) >= limit:
            break
    return frozenset(ids)


def _correction_item_pairs(packet: dict) -> dict[str, set[str]]:
    """Map each item id to the item ids joined to it by an accepted relation.

    A ``supersedes`` relation lets the target point at its replacement through
    ``replacement_item_id``, but a ``retracts`` relation has no replacement
    value, so trimming must pair target and source through the correction
    relation itself.  Without this, a retraction source (ordinary current
    memory) is dropped before the higher-priority retracted target, leaving a
    packet that asserts a retraction whose source evidence is gone.
    """

    pairs: dict[str, set[str]] = {}
    for correction in packet.get("corrections", []):
        target = correction.get("target_item_id")
        source = correction.get("source_item_id")
        if not isinstance(target, str) or not isinstance(source, str):
            continue
        pairs.setdefault(target, set()).add(source)
        pairs.setdefault(source, set()).add(target)
    return pairs


def _correction_partner_ids(
    span: dict, evidence: list[dict], pairs: dict[str, set[str]]
) -> set[int]:
    """Return the ``id()``s of the spans paired with ``span`` (possibly empty)."""

    if span.get("kind") != "memory":
        return set()
    item_id = span.get("item_id")
    replacement_of = span.get("replacement_item_id")
    if item_id is None and replacement_of is None:
        return set()
    related: set[str] = set()
    if replacement_of is not None:
        related.add(replacement_of)
    if item_id is not None:
        related |= pairs.get(item_id, set())
    partners: set[int] = set()
    for other in evidence:
        if other is span or other.get("kind") != "memory":
            continue
        other_id = other.get("item_id")
        if replacement_of is not None and other_id == replacement_of:
            partners.add(id(other))
        elif item_id is not None and other.get("replacement_item_id") == item_id:
            partners.add(id(other))
        elif other_id is not None and other_id in related:
            partners.add(id(other))
    return partners


def _prune_orphan_packet_sources(packet: dict) -> None:
    """Drop metadata-only sources and keep coverage counts coherent."""

    kept_source_ids = {span["source_id"] for span in packet["evidence"]}
    packet["sources"] = [
        source
        for source in packet["sources"]
        if source["source_id"] in kept_source_ids
    ]
    packet["coverage"]["sources"] = len(packet["sources"])
    packet["coverage"]["evidence"] = len(packet["evidence"])


def _correction_edges(packet: dict) -> dict[str, set[str]]:
    """Return every item-id relationship that must be retained as a unit."""

    edges: dict[str, set[str]] = {}

    def add(left: object, right: object) -> None:
        if not isinstance(left, str) or not isinstance(right, str):
            return
        edges.setdefault(left, set()).add(right)
        edges.setdefault(right, set()).add(left)

    for span in packet.get("evidence", []):
        if span.get("kind") != "memory":
            continue
        item_id = span.get("item_id")
        replacement = span.get("replacement_item_id")
        if isinstance(item_id, str) and isinstance(replacement, str):
            add(item_id, replacement)
    for correction in packet.get("corrections", []):
        add(correction.get("target_item_id"), correction.get("source_item_id"))
    return edges


def _trim_order_priorities(
    packet: dict,
    *,
    anchor_span_ids: set[int],
    retained_evidence_ids: frozenset[str],
) -> list[int]:
    """Trim priority per span, ranking a correction unit by its stickiest member.

    A correction relation is dropped as one unit, so its ordering must be the
    component's, not one half's.  A superseded target carries the reserved pair
    priority while its replacement renders as ordinary ``current`` memory;
    without this the unit would be evicted at the replacement's lower priority
    and a merely lexical broad-query note could outlive the curation.
    """

    priorities = [
        _evidence_trim_priority(
            span,
            anchor=id(span) in anchor_span_ids,
            retained=span.get("evidence_id") in retained_evidence_ids,
        )
        for span in packet["evidence"]
    ]
    edges = _correction_edges(packet)
    if not edges:
        return priorities
    by_item: dict[str, list[int]] = {}
    for index, span in enumerate(packet["evidence"]):
        if span.get("kind") != "memory":
            continue
        item_id = span.get("item_id")
        if isinstance(item_id, str):
            by_item.setdefault(item_id, []).append(index)
    if not by_item:
        return priorities
    component = {
        item_id: max(priorities[index] for index in indexes)
        for item_id, indexes in by_item.items()
    }
    # ``_correction_edges`` is symmetric, so one pass per change propagates the
    # component maximum in both directions until every member agrees.
    changed = True
    while changed:
        changed = False
        for left, neighbours in edges.items():
            if left not in component:
                continue
            value = component[left]
            for right in neighbours:
                if right in component and component[right] < value:
                    component[right] = value
                    changed = True
    for item_id, indexes in by_item.items():
        for index in indexes:
            priorities[index] = max(priorities[index], component[item_id])
    return priorities


def _prune_incomplete_correction_units(packet: dict) -> bool:
    """Resolve one-sided correction relations without losing current evidence.

    A correction relates an obsolete target to the current assertion that
    replaces or retracts it.  When both endpoints are present the relation is
    kept as a unit.  When only one side is present the relation cannot be
    stated, so it is dropped from ``corrections`` and resolved by side:

    * a lone current side (the successor of a supersede or the source of a
      retraction) is retained as ordinary current evidence -- dropping a
      useful current assertion is never acceptable;
    * a lone obsolete/retracted side is removed so it is never presented as
      current, and the packet is flagged ``partial`` with
      ``correction_pair_incomplete``.

    Connected components cover chains: a component is complete only when every
    endpoint it touches is present.  Dropping a lone current side's relation
    metadata is not a coverage loss, so it does not mark the packet partial.
    """

    edges = _correction_edges(packet)
    if not edges:
        return False
    disposition = {
        span["item_id"]: span.get("disposition")
        for span in packet.get("evidence", [])
        if span.get("kind") == "memory" and isinstance(span.get("item_id"), str)
    }
    present = set(disposition)
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, neighbours in edges.items():
        find(left)
        for right in neighbours:
            union(left, right)
    components: dict[str, set[str]] = {}
    for node in parent:
        components.setdefault(find(node), set()).add(node)
    incomplete_roots = {
        root for root, nodes in components.items() if not nodes <= present
    }
    if not incomplete_roots:
        return False

    dropped_ids: set[str] = set()
    uncovered = False
    for root in incomplete_roots:
        present_members = components[root] & present
        if not present_members:
            # The relation's evidence is entirely out of view, so the metadata
            # states a relationship the packet cannot show.
            uncovered = True
            continue
        # Keep only the current side; an obsolete/retracted/unresolved side
        # must never be left standing as if it were current.
        dropped_ids |= {
            item_id
            for item_id in present_members
            if disposition.get(item_id) != "current"
        }
    kept_corrections = [
        correction
        for correction in packet.get("corrections", [])
        if find(correction["target_item_id"]) not in incomplete_roots
        and find(correction["source_item_id"]) not in incomplete_roots
    ]
    evidence_changed = bool(dropped_ids)
    corrections_changed = len(kept_corrections) != len(packet.get("corrections", []))
    if not evidence_changed and not corrections_changed:
        return False
    if dropped_ids:
        packet["evidence"] = [
            span
            for span in packet["evidence"]
            if span.get("item_id") not in dropped_ids
        ]
    packet["corrections"] = kept_corrections
    if evidence_changed or uncovered:
        packet["partial"] = True
        reasons = packet.setdefault("reasons", [])
        if "correction_pair_incomplete" not in reasons:
            reasons.append("correction_pair_incomplete")
            reasons.sort()
    _prune_orphan_packet_sources(packet)
    return True


def _trim_packet(
    packet: dict,
    max_bytes: int,
    *,
    retained_evidence_ids: frozenset[str] = frozenset(),
) -> dict:
    """Drop evidence until the packet fits ``max_bytes``.

    ``retained_evidence_ids`` names the spans of a broad query's leading
    ordinary note.  They trim just above the memory snapshot -- but below an
    accepted correction pair -- so a memory-rich packet keeps the best lexical
    candidate source-linked while memory still fills whatever remains.  Unlike
    the explicit topic anchor, an unfittable retained span is not fatal: it is
    an automatic best guess, so the trim simply drops it and the packet stays
    honestly ``partial``.
    """

    _prune_incomplete_correction_units(packet)
    if _packet_bytes(packet) <= max_bytes:
        return packet
    packet["partial"] = True
    packet.setdefault("reasons", [])
    if "packet_byte_limit" not in packet["reasons"]:
        packet["reasons"].append("packet_byte_limit")
        packet["reasons"].sort()
    # Capture the caller's explicit anchor before any span is evicted so the
    # post-trim check can tell "the note never emitted a span" from "the bound
    # forced the note out".
    anchor_ids = _anchor_evidence_ids(packet)
    while _packet_bytes(packet) > max_bytes:
        if not packet["evidence"]:
            # There is nothing left to evict.  Orphaned correction metadata was
            # already removed; any remaining bytes are irreducible structure.
            _prune_orphan_packet_sources(packet)
            if _packet_bytes(packet) <= max_bytes:
                break
            if _prune_incomplete_correction_units(packet):
                continue
            raise InvalidInput(
                "packet_byte_limit_irreducible",
                (
                    "packet metadata exceeds max_bytes even after evidence and "
                    "correction trimming"
                ),
            )
        # Drop the lowest-priority span, breaking ties from the tail so the
        # best-ranked evidence survives.  A correction pair is dropped as a
        # unit: never leave one side of a supersede/retract lopsided.
        pairs = _correction_item_pairs(packet)
        anchor_span_ids = _anchor_evidence_ids(packet)
        priorities = _trim_order_priorities(
            packet,
            anchor_span_ids=anchor_span_ids,
            retained_evidence_ids=retained_evidence_ids,
        )
        order = sorted(
            range(len(packet["evidence"])),
            key=lambda index: (priorities[index], -index),
        )
        victim = packet["evidence"][order[0]]
        drop_ids = {id(victim)} | _correction_partner_ids(
            victim, packet["evidence"], pairs
        )
        packet["evidence"] = [
            span for span in packet["evidence"] if id(span) not in drop_ids
        ]
        _prune_orphan_packet_sources(packet)
        _prune_incomplete_correction_units(packet)
    _prune_orphan_packet_sources(packet)
    if anchor_ids and not _anchor_evidence_ids(packet):
        # Every lower-priority span was already gone, so this bound cannot hold
        # even a minimal packet that keeps the selected note source-linked.
        # Failing closed beats a "valid" partial packet with no note span.
        raise InvalidInput(
            "topic_anchor_unfittable",
            (
                "byte bound cannot retain a span of the explicitly selected "
                "topic note"
            ),
        )
    return packet


# --- Draft validation ---------------------------------------------------------


def _validate_packet(packet: object) -> dict:
    """Strict, bounded packet-schema validation before any source use."""

    if not isinstance(packet, dict):
        raise InvalidInput("invalid_packet", "packet must be an object")
    if set(packet) != PACKET_TOP_KEYS:
        missing = sorted(PACKET_TOP_KEYS - set(packet))
        extra = sorted(set(packet) - PACKET_TOP_KEYS)
        raise InvalidInput(
            "invalid_packet", f"packet fields invalid; missing={missing}, extra={extra}"
        )
    if packet.get("schema_version") != PACKET_SCHEMA_VERSION or packet.get("kind") != "k2b_context_packet":
        raise InvalidInput("invalid_packet", "packet schema_version or kind is unsupported")
    if not isinstance(packet["query"], str) or not packet["query"].strip():
        raise InvalidInput("invalid_packet", "packet query must be a non-empty string")
    if (
        not isinstance(packet["terms"], list)
        or len(packet["terms"]) > MAX_TERMS
        or not all(
            isinstance(term, str) and 0 < len(term) <= MAX_TERM_CHARS
            for term in packet["terms"]
        )
    ):
        raise InvalidInput("invalid_packet", "packet terms must be a bounded string array")
    if not isinstance(packet["sources"], list) or not isinstance(packet["evidence"], list):
        raise InvalidInput("invalid_packet", "packet sources/evidence must be arrays")
    if not isinstance(packet["corrections"], list):
        raise InvalidInput("invalid_packet", "packet corrections must be an array")
    if not isinstance(packet["reasons"], list):
        raise InvalidInput("invalid_packet", "packet reasons must be an array")
    if not isinstance(packet["partial"], bool):
        raise InvalidInput("invalid_packet", "packet partial must be a boolean")
    _exact_keys(packet["coverage"], COVERAGE_KEYS, "packet coverage")
    _exact_keys(packet["freshness"], FRESHNESS_KEYS, "packet freshness")
    _validate_published_memory_state(packet["freshness"]["published_memory"])
    _validate_candidate_inventory(packet["freshness"]["candidate_inventory"])
    _exact_keys(packet["limits"], LIMIT_KEYS, "packet limits")
    topic = packet["topic"]
    if topic is not None:
        _exact_keys(topic, {"note_ref"}, "packet topic")
    for index, source in enumerate(packet["sources"]):
        _validate_packet_source(source, f"packet source {index}")
    source_ids = {source["source_id"] for source in packet["sources"]}
    for index, span in enumerate(packet["evidence"]):
        _validate_packet_evidence(span, f"packet evidence {index}")
        if span["source_id"] not in source_ids:
            raise InvalidInput("invalid_packet", "packet evidence references an unknown source")
    for index, correction in enumerate(packet["corrections"]):
        _exact_keys(correction, PACKET_CORRECTION_KEYS, f"packet correction {index}")
    if packet.get("packet_id") != "packet:" + _sha256_hex(_packet_manifest(packet)):
        raise InvalidInput("invalid_packet", "packet_id does not bind its own content")
    return packet


def _validate_candidate_inventory(inventory: object) -> None:
    """Validate the recorded broad-query candidate inventory summary."""

    if inventory is None:
        return
    if not isinstance(inventory, dict) or set(inventory) != {
        "scope",
        "digest",
        "count",
        "complete",
        "max_candidate_bytes",
    }:
        raise InvalidInput("invalid_packet", "packet candidate inventory is invalid")
    if inventory["scope"] not in {"broad", "topic"}:
        raise InvalidInput(
            "invalid_packet", "packet candidate inventory scope is unsupported"
        )
    if (
        not isinstance(inventory["digest"], str)
        or HEX_64_RE.fullmatch(inventory["digest"]) is None
    ):
        raise InvalidInput("invalid_packet", "packet candidate inventory digest is invalid")
    if type(inventory["count"]) is not int or inventory["count"] < 0:
        raise InvalidInput("invalid_packet", "packet candidate inventory count is invalid")
    if not isinstance(inventory["complete"], bool):
        raise InvalidInput("invalid_packet", "packet candidate inventory complete is invalid")
    if (
        type(inventory["max_candidate_bytes"]) is not int
        or inventory["max_candidate_bytes"] <= 0
    ):
        raise InvalidInput(
            "invalid_packet", "packet candidate inventory bound is invalid"
        )


def _validate_published_memory_state(state: object) -> None:
    """Validate the recorded published-snapshot coverage state."""

    if not isinstance(state, dict) or set(state) != {"state", "snapshot_hash"}:
        raise InvalidInput("invalid_packet", "packet published_memory state is invalid")
    if state["state"] not in {"present", "absent", "unsupported"}:
        raise InvalidInput("invalid_packet", "packet published_memory state is unsupported")
    digest = state["snapshot_hash"]
    if digest is not None and (
        not isinstance(digest, str) or HEX_64_RE.fullmatch(digest) is None
    ):
        raise InvalidInput("invalid_packet", "packet published_memory hash is invalid")
    if state["state"] == "present" and digest is None:
        raise InvalidInput("invalid_packet", "packet published_memory present without a hash")
    if state["state"] != "present" and digest is not None:
        raise InvalidInput("invalid_packet", "packet published_memory gap carries a hash")


def _validate_packet_source(source: object, label: str) -> None:
    if not isinstance(source, dict):
        raise InvalidInput("invalid_packet", f"{label} must be an object")
    keys = set(source)
    if not SOURCE_BASE_KEYS <= keys <= (SOURCE_BASE_KEYS | SOURCE_OPTIONAL_KEYS):
        raise InvalidInput("invalid_packet", f"{label} fields are invalid")
    if not isinstance(source["source_id"], str) or SOURCE_ID_RE.fullmatch(source["source_id"]) is None:
        raise InvalidInput("invalid_packet", f"{label} source_id is invalid")
    if source["source_type"] not in SOURCE_TYPES:
        raise InvalidInput("invalid_packet", f"{label} source_type is unsupported")
    content_hash = source["content_hash"]
    if not isinstance(content_hash, str) or HEX_64_RE.fullmatch(content_hash) is None:
        raise InvalidInput("invalid_packet", f"{label} content_hash must be a SHA-256")
    if "snapshot_hash" in source and (
        not isinstance(source["snapshot_hash"], str)
        or HEX_64_RE.fullmatch(source["snapshot_hash"]) is None
    ):
        raise InvalidInput("invalid_packet", f"{label} snapshot_hash must be a SHA-256")
    for flag in ("vault_relative", "pending_home", "local_only"):
        if not isinstance(source[flag], bool):
            raise InvalidInput("invalid_packet", f"{label} {flag} must be a boolean")


def _validate_packet_evidence(span: object, label: str) -> None:
    if not isinstance(span, dict):
        raise InvalidInput("invalid_packet", f"{label} must be an object")
    kind = span.get("kind")
    if kind == "note":
        if set(span) != NOTE_EVIDENCE_KEYS:
            raise InvalidInput("invalid_packet", f"{label} note fields are invalid")
    elif kind == "memory":
        keys = set(span)
        if not MEMORY_EVIDENCE_KEYS <= keys <= (
            MEMORY_EVIDENCE_KEYS | MEMORY_EVIDENCE_OPTIONAL_KEYS
        ):
            raise InvalidInput("invalid_packet", f"{label} memory fields are invalid")
        if not isinstance(span["item_id"], str) or ITEM_ID_RE.fullmatch(span["item_id"]) is None:
            raise InvalidInput("invalid_packet", f"{label} item_id is invalid")
        _exact_keys(span["citation"], CITATION_KEYS, f"{label} citation")
    else:
        raise InvalidInput("invalid_packet", f"{label} kind is unsupported")
    if not isinstance(span["evidence_id"], str) or EVIDENCE_ID_RE.fullmatch(span["evidence_id"]) is None:
        raise InvalidInput("invalid_packet", f"{label} evidence_id is invalid")
    if not isinstance(span["source_id"], str) or SOURCE_ID_RE.fullmatch(span["source_id"]) is None:
        raise InvalidInput("invalid_packet", f"{label} source_id is invalid")
    if not isinstance(span["quote"], str) or not span["quote"]:
        raise InvalidInput("invalid_packet", f"{label} quote must be a non-empty string")
    if kind == "note":
        locator_match = (
            LOCATOR_RE.fullmatch(span["locator"])
            if isinstance(span["locator"], str)
            else None
        )
        if locator_match is None:
            raise InvalidInput("invalid_packet", f"{label} locator is invalid")
        has_start = locator_match.group(3) is not None
        has_end = locator_match.group(4) is not None
        if has_start != has_end:
            raise InvalidInput("invalid_packet", f"{label} window locator is incomplete")
        if has_start:
            byte_start = int(locator_match.group(3))
            byte_end = int(locator_match.group(4))
            if not 0 <= byte_start <= byte_end:
                raise InvalidInput(
                    "invalid_packet", f"{label} window locator is out of range"
                )
    if span["disposition"] not in EVIDENCE_DISPOSITIONS:
        raise InvalidInput("invalid_packet", f"{label} disposition is unsupported")
    if not isinstance(span["durable"], bool):
        raise InvalidInput("invalid_packet", f"{label} durable must be a boolean")


def _check_packet_freshness(
    packet: dict,
    vault_root: Path | None,
    *,
    published_memory: Path | None = None,
    provisional_root: Path | None = None,
) -> None:
    """Fail closed if any packet-bound source changed since it was gathered.

    The check binds the same sources and bounds gather used: an explicitly
    selected published memory snapshot is re-hashed through its own path, the
    local provisional outbox is compared through its recorded state hash, and
    vault notes/raw captures use the gather text representation.  A packet
    whose provisional outbox changed (a newer correction arrived) is stale even
    when every cited item is still individually valid.
    """

    _check_provisional_state_freshness(packet, provisional_root)
    _check_published_snapshot_freshness(packet, vault_root, published_memory)
    if vault_root is None and published_memory is None:
        return
    vault = Path(vault_root).expanduser() if vault_root is not None else None
    _check_candidate_inventory_freshness(packet, vault)
    for source in packet["sources"]:
        if source.get("local_only"):
            # Bound by the provisional state hash above, not a vault path.
            continue
        if source.get("source_type") == "provisional_memory":
            continue
        if source.get("source_type") == "published_memory" and published_memory is not None:
            # An explicitly selected snapshot is not the vault default, so
            # re-hash the file gather actually read.
            path = Path(published_memory)
            if not path.is_file() or path.is_symlink():
                raise StalePacket("stale_packet", f"memory snapshot is unavailable: {path}")
            current = _read_bytes_bounded(path, MAX_MEMORY_FILE_BYTES)
            current_hash = None if current is None else _sha256_bytes(current)
        else:
            if vault is None or not source.get("vault_relative"):
                continue
            rel = source.get("relative_path")
            if not rel:
                continue
            path = _bind_local_path(vault, rel, "packet source")
            if not path.is_file() or path.is_symlink():
                raise StalePacket("stale_packet", f"source changed or is unavailable: {rel}")
            # Re-hash each source with the same representation and per-type
            # bound that gather used to bind it, so an oversized-but-supported
            # published memory snapshot or a CRLF note is not falsely stale.
            current_hash = _freshness_content_hash(source, path)
        if current_hash is None:
            raise StalePacket(
                "stale_packet", f"source changed or is unreadable: {source.get('relative_path')}"
            )
        if current_hash != source.get("content_hash"):
                raise StalePacket(
                    "stale_packet", f"source changed: {source.get('relative_path')}"
                )


def _check_candidate_inventory_freshness(packet: dict, vault: Path | None) -> None:
    """Recheck the bounded candidate inventory a query was built from.

    Re-hashing the retained sources cannot notice a relevant note or raw
    capture that matches the query but arrived after gather, because it is not
    in ``packet['sources']`` at all.  Gather records a digest of its bounded
    candidate inventory -- a ranked discovery for a broad query, or a chosen
    note's linked scope for a topic query -- and validation re-derives the same
    bounded inventory and fails closed when it changed.  A broad scan that was
    itself cut short is not a complete statement, so it is skipped -- the
    packet already reports that limit.
    """

    inventory = packet["freshness"].get("candidate_inventory")
    if not inventory:
        return
    if vault is None:
        raise StalePacket(
            "stale_packet",
            "candidate inventory cannot be rechecked without the vault root",
        )
    vault = Path(vault).expanduser().resolve()
    if inventory["scope"] == "topic":
        _check_topic_inventory_freshness(packet, vault, inventory)
        return
    if not inventory["complete"]:
        return
    deadline = time.monotonic() + GATHER_DEADLINE_SECONDS
    max_candidate_bytes = inventory["max_candidate_bytes"]
    (
        note_paths,
        note_truncated,
        _,
        scanned_bytes,
        scanned_paths,
        _,
    ) = _candidate_note_files(
        vault, packet["terms"], deadline, max_candidate_bytes
    )
    raw_paths, raw_truncated, _, _, _ = _candidate_raw_files(
        vault,
        packet["terms"],
        deadline,
        max_candidate_bytes,
        start_bytes=scanned_bytes,
        scanned_paths=scanned_paths,
    )
    if note_truncated or raw_truncated:
        raise StalePacket(
            "stale_packet",
            "candidate inventory changed: the bounded recheck could not complete",
        )
    digest, _ = _candidate_inventory_digest(
        vault, note_paths, raw_paths, max_hash_bytes=max_candidate_bytes
    )
    if digest != inventory["digest"]:
        raise StalePacket(
            "stale_packet",
            "candidate inventory changed: relevant evidence arrived after gather",
        )


def _check_topic_inventory_freshness(
    packet: dict, vault: Path, inventory: dict
) -> None:
    """Recheck an explicitly chosen topic note's bounded linked scope.

    A linked note that was irrelevant at gather time and later becomes relevant
    is in no recorded source, so the scope is re-derived and each candidate is
    content-bound.  The scope is small (the note plus at most ``MAX_LINK_HOPS``
    linked notes and its linked captures), so this stays bounded.
    """

    topic = packet.get("topic") or {}
    rel = topic.get("note_ref")
    if not isinstance(rel, str) or not rel:
        raise StalePacket(
            "stale_packet", "topic candidate inventory has no bound topic note"
        )
    note_path = _bind_local_path(vault, rel, "topic note")
    note_paths = [note_path, *_linked_notes(vault, note_path, MAX_LINK_HOPS)]
    raw_paths = _linked_raw_captures(vault, note_path)
    digest, _ = _candidate_inventory_digest(
        vault, note_paths, raw_paths, max_hash_bytes=inventory["max_candidate_bytes"]
    )
    if digest != inventory["digest"]:
        raise StalePacket(
            "stale_packet",
            "candidate inventory changed: relevant evidence arrived after gather",
        )


def _check_published_snapshot_freshness(
    packet: dict,
    vault_root: Path | None,
    published_memory: Path | None,
) -> None:
    """Re-hash the recorded memory snapshot even when no span was retained.

    A query that does not match the current key/value store produces no
    ``published_memory`` source entry, but the packet still records the
    snapshot hash it observed.  If a relevant item is published between gather
    and validate, that hash changes and the packet must be rebuilt rather than
    silently omitting the newly available evidence.
    """

    freshness = packet.get("freshness") or {}
    recorded = freshness.get("snapshot_hash")
    state = freshness.get("published_memory") or {"state": "absent"}
    state_kind = state.get("state")
    recorded_state_hash = state.get("snapshot_hash")
    path: Path | None
    if published_memory is not None:
        path = Path(published_memory)
    elif vault_root is not None:
        path = Path(vault_root) / PUBLISHED_MEMORY_REL
    else:
        path = None
    if state_kind == "unsupported":
        # The snapshot existed at gather time but exceeded the supported bound;
        # a staleness recheck cannot re-read it either, so the recorded gap
        # stands rather than raising a false stale error.
        return
    if path is None:
        if state_kind == "absent" and recorded is None:
            return
        raise StalePacket(
            "stale_packet",
            "recorded memory snapshot cannot be verified without a published memory path",
        )
    if not path.is_file() or path.is_symlink():
        if state_kind == "absent":
            return
        raise StalePacket("stale_packet", "memory snapshot is unavailable")
    raw = _read_bytes_bounded(path, MAX_MEMORY_FILE_BYTES)
    if raw is None:
        if state_kind == "absent":
            return
        raise StalePacket("stale_packet", "memory snapshot is unreadable")
    if state_kind == "absent":
        # A snapshot that appeared since gather must invalidate the packet.
        raise StalePacket("stale_packet", "memory snapshot appeared since the packet was built")
    current = _sha256_bytes(raw)
    expected = recorded_state_hash or recorded
    if current != expected:
        raise StalePacket("stale_packet", "memory snapshot changed since the packet was built")


def _check_provisional_state_freshness(
    packet: dict, provisional_root: Path | None
) -> None:
    """Reject a packet whose local provisional outbox changed since gather.

    The outbox state hash is computed with the identical reader and bounds
    gather used, so a new local envelope (for example a newer correction that
    was not part of the packet) invalidates the packet instead of letting a
    draft silently omit it.
    """

    recorded = (packet.get("coverage") or {}).get("provisional_state_hash")
    if provisional_root is None:
        if recorded is not None:
            raise StalePacket(
                "stale_packet",
                "local provisional outbox cannot be verified without its root",
            )
        return
    current = _provisional_state_hash(provisional_root)
    if recorded != current:
        raise StalePacket(
            "stale_packet", "local provisional outbox changed since the packet was built"
        )


def _freshness_content_hash(source: dict, path: Path) -> str | None:
    """Re-derive a packet source's gather-time hash with matching semantics.

    Gather hashes the raw bytes of a published memory snapshot (bounded at
    :data:`MAX_MEMORY_FILE_BYTES`) but the normalized UTF-8 text of note/raw
    sources (bounded at :data:`MAX_SOURCE_FILE_BYTES`).  Freshness must use the
    identical representation and bound or an unchanged source is rejected as
    stale.  ``None`` means missing, unreadable or oversized and fails closed.
    """

    if source.get("source_type") == "published_memory":
        raw = _read_bytes_bounded(path, MAX_MEMORY_FILE_BYTES)
        return None if raw is None else _sha256_bytes(raw)
    text = _read_bounded(path, MAX_SOURCE_FILE_BYTES)
    return None if text is None else _sha256_bytes(text.encode("utf-8"))


def _verify_evidence_binding(
    packet: dict,
    vault_root: Path | None,
    *,
    published_memory: Path | None = None,
    provisional_root: Path | None = None,
) -> None:
    """Recheck every evidence span against its actual source, not its id.

    A packet id binds content, but the packet may have been rebuilt by a
    caller.  This step re-derives each quote from the named vault file and each
    memory span from the validated published/provisional snapshot, so altered
    or invented citations fail closed even when the source file is unchanged.
    """

    sources = {source["source_id"]: source for source in packet["sources"]}
    memory_state: dict | None = None
    memory_loaded = False
    provisional_items: dict[str, tuple[dict, dict, dict]] | None = None
    for source in packet["sources"]:
        _verify_source_identity(source, provisional_root)
    for span in packet["evidence"]:
        source = sources[span["source_id"]]
        if span["kind"] == "note":
            _verify_note_span(span, source, vault_root)
            continue
        if span["durable"] and source["source_type"] in {"published_memory"}:
            if not memory_loaded:
                memory_state = _load_packet_memory(
                    packet, source, vault_root, published_memory
                )
                memory_loaded = True
            _verify_memory_span(span, source, memory_state)
        else:
            if provisional_items is None:
                provisional_items = _load_provisional_items(provisional_root, packet)
            _verify_provisional_span(
                span, provisional_items, provisional_root=provisional_root
            )


def _verify_source_identity(source: dict, provisional_root: Path | None) -> None:
    """Recompute a source id from its real path/hash instead of trusting it."""

    source_type = source["source_type"]
    if source_type in {"vault_note", "raw_capture", "semantic_shelf"}:
        expected = "src:" + _sha256_hex(
            {"rel": source.get("relative_path"), "content_hash": source.get("content_hash")}
        )
    elif source_type == "published_memory":
        expected = "src:" + _sha256_hex(
            {"rel": source.get("relative_path"), "snapshot": source.get("content_hash")}
        )
    elif source_type == "provisional_memory":
        if provisional_root is None:
            raise InvalidInput(
                "evidence_binding", "provisional source cannot be verified without its outbox"
            )
        expected = "src:" + _sha256_hex(
            {"rel": "provisional", "root": str(Path(provisional_root).resolve())}
        )
    else:
        raise InvalidInput("evidence_binding", "packet source type is unsupported")
    if source["source_id"] != expected:
        raise InvalidInput("evidence_binding", "packet source id does not bind its content")


def _load_packet_memory(
    packet: dict,
    source: dict,
    vault_root: Path | None,
    published_memory: Path | None,
) -> dict:
    if published_memory is not None:
        path = Path(published_memory)
    elif vault_root is not None and source.get("relative_path"):
        path = _bind_local_path(
            Path(vault_root), source["relative_path"], "memory snapshot source"
        )
    else:
        raise InvalidInput(
            "evidence_binding", "memory evidence cannot be verified without its snapshot"
        )
    raw = _read_bytes_bounded(path, MAX_MEMORY_FILE_BYTES)
    if raw is None:
        raise InvalidInput(
            "evidence_binding", "memory snapshot for evidence is unavailable"
        )
    if _sha256_bytes(raw) != source["content_hash"]:
        raise StalePacket("stale_packet", "memory snapshot changed since the packet was built")
    try:
        state = automatic_memory._validate_memory_state(
            json.loads(raw.decode("utf-8", errors="replace"))
        )
    except (ValueError, automatic_memory.StateCorruptionError) as exc:
        raise UnavailableState(
            "memory_state_corrupt", "published automatic memory is unreadable"
        ) from exc
    durable_ids = {correction["correction_id"] for correction in state["corrections"]}
    for correction in packet["corrections"]:
        if correction["correction_id"] not in durable_ids:
            raise InvalidInput(
                "evidence_binding", "packet correction is not present in the snapshot"
            )
    return state


def _verify_note_span(span: dict, source: dict, vault_root: Path | None) -> None:
    if vault_root is None:
        raise InvalidInput(
            "evidence_binding", "note evidence cannot be verified without the vault root"
        )
    rel = source.get("relative_path")
    if source["source_type"] not in {"vault_note", "raw_capture", "semantic_shelf"} or not rel:
        raise InvalidInput("evidence_binding", "note evidence has no vault-bound source")
    path = _bind_local_path(Path(vault_root), rel, "note evidence source")
    text = _read_bounded(path, MAX_SOURCE_FILE_BYTES)
    if text is None:
        raise InvalidInput("evidence_binding", f"note source is unavailable: {rel}")
    if _sha256_bytes(text.encode("utf-8")) != source["content_hash"]:
        raise StalePacket("stale_packet", f"source changed: {rel}")
    match = LOCATOR_RE.fullmatch(span["locator"])
    if match is None:
        raise InvalidInput("evidence_binding", "note evidence locator is malformed")
    start, end = int(match.group(1)), int(match.group(2))
    byte_start = int(match.group(3)) if match.group(3) is not None else None
    byte_end = int(match.group(4)) if match.group(4) is not None else None
    if (byte_start is None) != (byte_end is None):
        raise InvalidInput("evidence_binding", "note evidence window is malformed")
    if byte_start is not None and byte_end is not None and not (
        0 <= byte_start <= byte_end
    ):
        raise InvalidInput("evidence_binding", "note evidence window is out of range")
    lines = text.splitlines()
    if start < 1 or end < start or end > len(lines):
        raise InvalidInput("evidence_binding", "note evidence locator is out of range")
    block = "\n".join(lines[start - 1 : end])
    if byte_start is None or byte_end is None:
        expected_quote = block.strip()[:MAX_TEXT_CHARS]
    else:
        block_bytes = block.encode("utf-8")
        if byte_start > byte_end or byte_end >= len(block_bytes):
            raise InvalidInput("evidence_binding", "note evidence window exceeds its block")
        expected_quote = _rebuild_window(block, byte_start, byte_end)
    if len(expected_quote) > MAX_TEXT_CHARS:
        raise InvalidInput("evidence_binding", "note evidence quote exceeds the text bound")
    if expected_quote != span["quote"]:
        raise InvalidInput("evidence_binding", "note evidence quote does not match its source")
    expected_id = "ev:" + _sha256_hex(
        {"source_id": span["source_id"], "locator": span["locator"], "quote": span["quote"]}
    )
    if span["evidence_id"] != expected_id:
        raise InvalidInput("evidence_binding", "note evidence id does not bind its content")
    if span["authority"] != source["authority_category"]:
        raise InvalidInput("evidence_binding", "note evidence authority does not match its source")
    if span["speaker"] != source["speaker"]:
        raise InvalidInput("evidence_binding", "note evidence speaker does not match its source")
    if span["source_time"] is not None:
        raise InvalidInput("evidence_binding", "note evidence must not invent an event time")
    if span["durable"] is not True:
        raise InvalidInput("evidence_binding", "note evidence durability does not match its source")
    if span["disposition"] != "current":
        raise InvalidInput("evidence_binding", "note evidence disposition is unsupported")
    if (
        span["source_date"] != source["source_date"]
        or span["date_quality"] != source["date_quality"]
    ):
        raise InvalidInput("evidence_binding", "note evidence date does not match its source")


def _verify_memory_span(span: dict, source: dict, state: dict) -> None:
    index = automatic_memory._item_index(state["records"])
    entry = index.get(span["item_id"])
    if entry is None or entry[0] != span["key"]:
        raise InvalidInput("evidence_binding", "memory evidence item is not in the snapshot")
    key, kind, version = entry
    if (
        version["value"] != span["value"]
        or version["evidence_quote"] != span["quote"]
        or version["citation"] != span["citation"]
        or version["speaker_source"] != span["speaker_source"]
        or version["source_time"] != span["source_time"]
        or kind != span["memory_kind"]
    ):
        raise InvalidInput("evidence_binding", "memory evidence does not match its snapshot")
    recomputed = automatic_memory._item_identity(
        source_identity=version["source_identity"],
        key=key,
        kind=kind,
        value=version["value"],
        evidence_quote=version["evidence_quote"],
        speaker_source=version["speaker_source"],
    )
    if recomputed != span["item_id"]:
        raise InvalidInput("evidence_binding", "memory evidence identity does not recompute")
    disposition = automatic_memory.item_dispositions(state, key)[span["item_id"]]
    if disposition["disposition"] != span["disposition"]:
        raise InvalidInput("evidence_binding", "memory evidence disposition does not match its snapshot")
    if disposition["replacement_item_id"] != span.get("replacement_item_id"):
        raise InvalidInput("evidence_binding", "memory evidence replacement does not match its snapshot")
    expected_authority = (
        "keith_statement" if version["speaker_source"] == "keith" else "assistant_claim"
    )
    if span["authority"] != expected_authority:
        raise InvalidInput("evidence_binding", "memory evidence authority does not match its speaker")
    if span["durable"] is not True or span["pending_home"] is not False:
        raise InvalidInput("evidence_binding", "memory evidence durability does not match its snapshot")
    if span["source_date"] != span["source_time"][:10] or span["date_quality"] != "recorded":
        raise InvalidInput("evidence_binding", "memory evidence date does not match its source time")
    expected_source_id = "src:" + _sha256_hex(
        {"rel": source.get("relative_path"), "snapshot": source["content_hash"]}
    )
    if span["source_id"] != expected_source_id:
        raise InvalidInput("evidence_binding", "memory evidence source id does not bind its snapshot")
    expected_id = "ev:" + _sha256_hex(
        {"type": "memory", "item_id": span["item_id"], "snapshot": source["content_hash"]}
    )
    if span["evidence_id"] != expected_id:
        raise InvalidInput("evidence_binding", "memory evidence id does not bind its content")


def _load_provisional_items(
    provisional_root: Path | None, packet: dict
) -> dict[str, tuple[dict, dict, dict]]:
    if provisional_root is None:
        raise InvalidInput(
            "evidence_binding", "provisional evidence cannot be verified without its outbox"
        )
    items: dict[str, tuple[dict, dict, dict]] = {}
    for envelope in _read_provisional_envelopes(provisional_root)["envelopes"]:
        bundle = envelope["bundle"]
        for item in bundle["items"]:
            version = automatic_memory._version(bundle, item)
            items[version["item_id"]] = (version, envelope, item)
    return items


def _verify_provisional_span(
    span: dict,
    items: dict[str, tuple[dict, dict, dict]],
    *,
    provisional_root: Path | None,
) -> None:
    """Recheck every semantic field of a provisional span against the outbox.

    A caller can rebuild a packet and recompute ``packet_id``; the item id
    alone is therefore not enough.  Authority, key/kind, event time,
    disposition, source id and pending_home are all re-derived from the
    validated local outbox and must match exactly.
    """

    entry = items.get(span["item_id"])
    if entry is None:
        raise InvalidInput("evidence_binding", "provisional evidence item is not in the outbox")
    version, envelope, item = entry
    if (
        version["value"] != span["value"]
        or version["evidence_quote"] != span["quote"]
        or version["citation"] != span["citation"]
        or version["speaker_source"] != span["speaker_source"]
        or version["source_time"] != span["source_time"]
        or item["key"] != span["key"]
        or item["kind"] != span["memory_kind"]
    ):
        raise InvalidInput("evidence_binding", "provisional evidence does not match its outbox")
    expected_authority = (
        "keith_statement" if version["speaker_source"] == "keith" else "assistant_claim"
    )
    if span["authority"] != expected_authority:
        raise InvalidInput("evidence_binding", "provisional evidence authority does not match its outbox")
    if span["source_date"] != span["source_time"][:10] or span["date_quality"] != "recorded":
        raise InvalidInput("evidence_binding", "provisional evidence date does not match its source time")
    if span["disposition"] != "pending_home":
        raise InvalidInput("evidence_binding", "provisional evidence disposition is unsupported")
    if span["durable"] is not False or span["pending_home"] is not True:
        raise InvalidInput("evidence_binding", "provisional evidence delivery state does not match its outbox")
    if provisional_root is None:
        raise InvalidInput(
            "evidence_binding", "provisional evidence cannot be verified without its outbox"
        )
    expected_source_id = "src:" + _sha256_hex(
        {"rel": "provisional", "root": str(Path(provisional_root).resolve())}
    )
    if span["source_id"] != expected_source_id:
        raise InvalidInput("evidence_binding", "provisional evidence source id does not bind its outbox")
    expected_id = "ev:" + _sha256_hex(
        {"type": "memory", "item_id": span["item_id"], "snapshot": envelope["content_id"]}
    )
    if span["evidence_id"] != expected_id:
        raise InvalidInput("evidence_binding", "provisional evidence id does not bind its content")


def _validate_item(raw: object, label: str, *, commitment: bool = False) -> dict:
    expected = {"text", "evidence_ids", "status", "origin"}
    if commitment:
        expected |= {"owner", "due_at", "completion_state"}
    value = _exact_keys(raw, expected, label)
    text = _require_text(value["text"], f"{label} text")
    evidence_ids = value["evidence_ids"]
    if not isinstance(evidence_ids, list) or len(evidence_ids) > 16:
        raise InvalidInput("invalid_schema", f"{label} evidence_ids must be a bounded array")
    for evidence_id in evidence_ids:
        if not isinstance(evidence_id, str) or SAFE_ID_RE.fullmatch(evidence_id) is None:
            raise InvalidInput("invalid_schema", f"{label} evidence_ids must be opaque ids")
    if not isinstance(value["status"], str) or value["status"] not in ITEM_STATUSES:
        raise InvalidInput("invalid_schema", f"{label} status is unsupported")
    if not isinstance(value["origin"], str) or value["origin"] not in ITEM_ORIGINS:
        raise InvalidInput("invalid_schema", f"{label} origin is unsupported")
    if not evidence_ids and value["origin"] == "source":
        raise InvalidInput("invalid_schema", f"{label} source items require evidence_ids")
    item = {
        "text": text,
        "evidence_ids": list(evidence_ids),
        "status": value["status"],
        "origin": value["origin"],
    }
    if commitment:
        owner = value["owner"]
        due_at = value["due_at"]
        completion = value["completion_state"]
        if owner is not None and (not isinstance(owner, str) or not owner.strip()):
            raise InvalidInput("invalid_schema", f"{label} owner must be null or a string")
        if due_at is not None and not isinstance(due_at, str):
            raise InvalidInput("invalid_schema", f"{label} due_at must be null or a string")
        if completion is not None and (
            not isinstance(completion, str)
            or completion
            not in {"completed", "not_observed", "unknown", "blocked", "proposed", "retracted"}
        ):
            raise InvalidInput("invalid_schema", f"{label} completion_state is unsupported")
        item.update(
            {
                "owner": owner.strip() if isinstance(owner, str) else None,
                "due_at": due_at,
                "completion_state": completion,
            }
        )
    return item


def _validate_section(
    draft: dict, name: str, *, commitment: bool = False, maximum: int = MAX_SECTION_ITEMS
) -> list[dict]:
    raw = draft.get(name)
    if not isinstance(raw, list) or len(raw) > maximum:
        raise InvalidInput("invalid_schema", f"draft section {name} must be a bounded array")
    return [_validate_item(item, f"{name} item", commitment=commitment) for item in raw]


def _span_identity(span: dict) -> str | None:
    """The identity of an evidence span: its memory item, else its evidence id."""

    return span.get("item_id") or span.get("evidence_id")


def _proposal_target_key(proposal: dict) -> str | None:
    """The identity a proposal corrects: the memory item, else the evidence span."""

    return proposal.get("target_item_id") or proposal.get("target_evidence_id")


def _proposal_source_key(proposal: dict) -> str | None:
    """The identity a proposal corrects with: the memory item, else the span."""

    return proposal.get("source_item_id") or proposal.get("source_evidence_id")


def _is_source_bound_note_pair(proposal: dict) -> bool:
    """True when both endpoints are current, source-bound ordinary-note spans."""

    target = proposal["target_span"]
    source = proposal["source_span"]
    return (
        target.get("kind") == "note"
        and source.get("kind") == "note"
        and target.get("disposition") == "current"
        and source.get("disposition") == "current"
    )


def _note_span_day(span: dict) -> int | None:
    """The frontmatter day of a note span as an ordinal, or ``None``."""

    if span.get("date_quality") != "frontmatter":
        return None
    raw = span.get("source_date")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).date().toordinal()
    except ValueError:
        return None


def _note_span_passage_day(span: dict) -> int | None:
    """Ordinal of the ISO date a quoted passage stamps at its own start.

    Two passages in the same file share one frontmatter day, and a file's line
    order is not event order (newest-first notes and inserted edits reverse
    it).  The only within-file order evidence accepted is a date that leads the
    passage itself, as a dated daily-note entry does; a date buried inside the
    sentence (for example an ISO deadline) is ignored, so a passage without a
    leading date stays unorderable.
    """

    quote = span.get("quote")
    if not isinstance(quote, str):
        return None
    match = PASSAGE_DATE_RE.match(quote)
    if match is None:
        return None
    try:
        return datetime.fromisoformat(match.group(1)).date().toordinal()
    except ValueError:
        return None


def _note_correction_is_later(target: dict, source: dict) -> bool:
    """True when a source-bound note passage is strictly later than its target.

    Ordinary notes carry at most a frontmatter day, and file modification or
    arrival order is not an event date.  Passages in different files need a
    strictly later frontmatter day.  Two passages in the same file are ordered
    only by a date the passage itself states -- never by line position, which
    newest-first notes and inserted edits reverse.  Missing, ambiguous or equal
    dates stay unresolved rather than guessing an order.
    """

    if target.get("source_id") == source.get("source_id"):
        target_day = _note_span_passage_day(target)
        source_day = _note_span_passage_day(source)
        if target_day is None or source_day is None:
            return False
        return source_day > target_day
    target_day = _note_span_day(target)
    source_day = _note_span_day(source)
    if target_day is None or source_day is None:
        return False
    return source_day > target_day


def _validate_correction_proposals(packet: dict, raw: object) -> list[dict]:
    """Validate correction proposals and resolve uniquely grounded ones.

    A durable link is used when it already exists.  Otherwise an explicit,
    uniquely grounded cross-key proposal resolves the read view without writing
    shared memory.  Ambiguous, unsupported or reverse-chronology proposals stay
    ``unresolved`` so the target can never be presented as a current fact.
    An explicit correction between two source-bound ordinary-note passages
    resolves the read view the same way: the passages keep their own source
    IDs and hashes, and nothing is written back to memory.
    """

    if not isinstance(raw, list) or len(raw) > 20:
        raise InvalidInput("invalid_schema", "correction_proposals must be a bounded array")
    evidence_ids = {span["evidence_id"] for span in packet["evidence"]}
    accepted = {
        (correction["target_item_id"], correction["source_item_id"], correction["relation"])
        for correction in packet["corrections"]
    }
    evidence_by_id = {span["evidence_id"]: span for span in packet["evidence"]}
    parsed: list[dict] = []
    for proposal in raw:
        value = _exact_keys(
            proposal,
            {"target_evidence_id", "source_evidence_id", "relation", "rationale"},
            "correction proposal",
        )
        target = value["target_evidence_id"]
        source = value["source_evidence_id"]
        if target not in evidence_ids or source not in evidence_ids:
            raise InvalidInput(
                "invalid_schema", "correction proposal references evidence outside the packet"
            )
        relation = value["relation"]
        if not isinstance(relation, str) or relation not in CORRECTION_RELATIONS:
            raise InvalidInput("invalid_schema", "correction proposal relation is unsupported")
        rationale = _require_text(value["rationale"], "correction proposal rationale", max_chars=600)
        target_span = evidence_by_id[target]
        source_span = evidence_by_id[source]
        target_item = target_span.get("item_id")
        source_item = source_span.get("item_id")
        parsed.append(
            {
                "target_evidence_id": target,
                "source_evidence_id": source,
                "relation": relation,
                "rationale": rationale,
                "target_item_id": target_item,
                "source_item_id": source_item,
                "target_span": target_span,
                "source_span": source_span,
                "status": "proposed",
                "reason": None,
            }
        )

    # Competing proposals for one target stay unresolved rather than picking a
    # winner by order or wording.
    by_target: dict[str, list[dict]] = {}
    for item in parsed:
        target_key = _proposal_target_key(item)
        if target_key is not None:
            by_target.setdefault(target_key, []).append(item)
    for group in by_target.values():
        signatures = {(_proposal_source_key(row), row["relation"]) for row in group}
        if len(group) > 1 and len(signatures) > 1:
            for row in group:
                row["status"] = "unresolved"
                row["reason"] = "competing_proposals"

    for row in parsed:
        if row["status"] == "unresolved":
            continue
        target_item = row["target_item_id"]
        source_item = row["source_item_id"]
        if target_item is None or source_item is None:
            if _is_source_bound_note_pair(row):
                if row["target_evidence_id"] == row["source_evidence_id"]:
                    row["status"] = "unresolved"
                    row["reason"] = "self_reference"
                    continue
                if not _note_correction_is_later(
                    row["target_span"], row["source_span"]
                ):
                    # Ordinary notes carry at most a frontmatter day and file
                    # arrival order is not an event date, so an unorderable
                    # note pair stays unresolved instead of guessing.
                    row["status"] = "unresolved"
                    row["reason"] = "chronology_unresolved"
                    continue
                # Read-view only: no durable relation and no memory write.
                row["status"] = "resolved"
                row["reason"] = "source_bound_note_read_view"
                continue
            row["status"] = "unresolved"
            row["reason"] = "unsupported_evidence_type"
            continue
        if (target_item, source_item, row["relation"]) in accepted:
            row["status"] = "accepted"
            continue
        if target_item == source_item:
            row["status"] = "unresolved"
            row["reason"] = "self_reference"
            continue
        target_span = row["target_span"]
        source_span = row["source_span"]
        if target_span["disposition"] in {"superseded", "retracted"}:
            row["status"] = "unresolved"
            row["reason"] = "target_already_disposed"
            continue
        source_is_pending_home = bool(
            source_span.get("pending_home") or not source_span.get("durable", True)
        )
        if source_span["disposition"] not in {"current", "pending_home"}:
            row["status"] = "unresolved"
            row["reason"] = "source_not_current"
            continue
        if (
            target_span["authority"] == "keith_statement"
            and source_span["authority"] != "keith_statement"
        ):
            # A later assistant suggestion never overrules Keith's decision.
            row["status"] = "unresolved"
            row["reason"] = "assistant_cannot_override_keith"
            continue
        target_instant = _source_instant(target_span.get("source_time"))
        source_instant = _source_instant(source_span.get("source_time"))
        if (
            target_instant is None
            or source_instant is None
            or source_instant <= target_instant
        ):
            # Chronology must be established; file arrival order is not evidence.
            row["status"] = "unresolved"
            row["reason"] = "chronology_unresolved"
            continue
        if source_is_pending_home:
            # A validated local outbox item is newer evidence, but it is not a
            # durable Home correction.  Resolve it only as a provisional read
            # view; the corrected status is carried in the brief and never
            # written back to shared memory.
            row["status"] = "provisional"
            row["reason"] = "pending_home_source"
        else:
            row["status"] = "resolved"

    return [
        {
            "target_evidence_id": row["target_evidence_id"],
            "source_evidence_id": row["source_evidence_id"],
            "relation": row["relation"],
            "rationale": row["rationale"],
            "status": row["status"],
            "reason": row["reason"],
            "target_item_id": row["target_item_id"],
            "source_item_id": row["source_item_id"],
        }
        for row in parsed
    ]


def _effective_dispositions(packet: dict, proposals: list[dict]) -> dict[str, str]:
    """Overlay query-time corrections on the packet's gathered dispositions."""

    effective = {
        span["evidence_id"]: span["disposition"] for span in packet["evidence"]
    }
    for proposal in proposals:
        if proposal["status"] in {"resolved", "provisional"}:
            disposition = "superseded" if proposal["relation"] == "supersedes" else "retracted"
        elif proposal["status"] == "unresolved":
            disposition = "unresolved"
        else:
            continue
        # A durable correction names a memory item; a read-view correction
        # between two ordinary-note passages names the corrected span itself.
        target_item = proposal.get("target_item_id")
        target_evidence = proposal.get("target_evidence_id")
        for span in packet["evidence"]:
            if span["evidence_id"] == target_evidence or (
                target_item is not None and span.get("item_id") == target_item
            ):
                effective[span["evidence_id"]] = disposition
    return effective


def _validate_overlays(raw: object) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > 20:
        raise InvalidInput("invalid_schema", "overlays must be a bounded array")
    overlays: list[dict] = []
    for overlay in raw:
        value = _exact_keys(overlay, {"text", "conversation_ref"}, "overlay")
        overlays.append(
            {
                "text": _require_text(value["text"], "overlay text", max_chars=2000),
                "conversation_ref": _require_text(
                    value["conversation_ref"], "overlay conversation_ref", max_chars=200
                ),
            }
        )
    return overlays


def _citation_target_for_note(rel: str, locator: str) -> str | None:
    match = LOCATOR_RE.fullmatch(locator)
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if start == end:
        return f"{rel}#L{start}"
    return f"{rel}#L{start}-L{end}"


def _build_citations(packet: dict) -> dict[str, dict]:
    """Derive programmatically rendered citations from the verified packet.

    The model never supplies links: every citation is built here from the
    already-verified spans.  Note spans link to their current local passage;
    published memory links to the published recall surface; provisional memory
    has no published surface locally, so it carries only its provenance id and
    an explicit limitation.
    """

    sources = {source["source_id"]: source for source in packet["sources"]}
    citations: dict[str, dict] = {}
    for span in packet["evidence"]:
        source = sources[span["source_id"]]
        if span["kind"] == "note":
            rel = source.get("relative_path")
            target = _citation_target_for_note(rel, span["locator"]) if rel else None
            line_range = span["locator"].split("=", 1)[-1].split(";", 1)[0]
            citations[span["evidence_id"]] = {
                "evidence_id": span["evidence_id"],
                "kind": "note",
                "label": f"{rel}:{line_range}" if rel else "(unknown note)",
                "target": target,
                "provenance_id": f"{source['source_id']}@{str(source['content_hash'])[:12]}",
                "limitation": None,
            }
            continue
        key = span["key"]
        citation = span.get("citation") or {}
        provenance = citation.get("event_id") or citation.get("session_id") or span["item_id"]
        if span.get("pending_home") or not span.get("durable"):
            citations[span["evidence_id"]] = {
                "evidence_id": span["evidence_id"],
                "kind": "memory",
                "label": f"provisional memory {key}",
                "target": None,
                "provenance_id": span["item_id"],
                "limitation": (
                    "machine-local provisional memory; not published, so no local "
                    "published recall surface exists"
                ),
            }
            continue
        citations[span["evidence_id"]] = {
            "evidence_id": span["evidence_id"],
            "kind": "memory",
            "label": f"published memory {key}",
            "target": source.get("relative_path"),
            "provenance_id": f"item={span['item_id']} event={provenance}",
            "limitation": (
                "original capture transcript is not stored locally; link is the "
                "published recall surface"
            ),
        }
    return citations


def _safe_citation_target(target: object) -> bool:
    """A citation target must be a current, vault-relative local path."""

    if not isinstance(target, str) or not target:
        return False
    if "://" in target or target.startswith("//") or target.startswith("~"):
        return False
    if "\\" in target or target.startswith("/"):
        return False
    path_part = target.split("#", 1)[0]
    if not path_part:
        return False
    candidate = Path(path_part)
    if candidate.is_absolute() or ".." in candidate.parts:
        return False
    return (
        _within_note_roots(path_part)
        or _within_raw_roots(path_part)
        or path_part == PUBLISHED_MEMORY_REL
    )


def _validate_brief_citation(key: object, citation: object) -> None:
    value = _exact_keys(citation, BRIEF_CITATION_KEYS, "brief citation")
    if value["evidence_id"] != key:
        raise InvalidInput("invalid_brief", "brief citation key does not match its evidence id")
    if not isinstance(value["evidence_id"], str) or EVIDENCE_ID_RE.fullmatch(value["evidence_id"]) is None:
        raise InvalidInput("invalid_brief", "brief citation evidence_id is invalid")
    if value["kind"] not in {"note", "memory"}:
        raise InvalidInput("invalid_brief", "brief citation kind is unsupported")
    if not isinstance(value["label"], str) or not value["label"].strip():
        raise InvalidInput("invalid_brief", "brief citation label must be a non-empty string")
    if not isinstance(value["provenance_id"], str) or not value["provenance_id"].strip():
        raise InvalidInput("invalid_brief", "brief citation provenance_id must be a non-empty string")
    limitation = value["limitation"]
    if limitation is not None and (not isinstance(limitation, str) or not limitation.strip()):
        raise InvalidInput("invalid_brief", "brief citation limitation must be null or a string")
    target = value["target"]
    if target is not None and not _safe_citation_target(target):
        raise InvalidInput("invalid_brief", "brief citation target must be a local vault path")
    if value["kind"] == "note" and target is None:
        raise InvalidInput("invalid_brief", "note citations require a local passage link")


def _validate_brief_for_render(brief: object) -> dict:
    """Reject a tampered or unsupported brief before printing any link."""

    if not isinstance(brief, dict) or brief.get("kind") != "k2b_resume_brief":
        raise InvalidInput("invalid_brief", "brief must be a rendered resume brief object")
    if set(brief) != BRIEF_KEYS:
        raise InvalidInput("invalid_brief", "brief fields are invalid")
    if brief.get("schema_version") != BRIEF_SCHEMA_VERSION:
        raise InvalidInput("invalid_brief", "brief schema_version is unsupported")
    expected_id = "brief:" + _sha256_hex(
        {key: value for key, value in brief.items() if key != "brief_id"}
    )
    if brief.get("brief_id") != expected_id:
        raise InvalidInput("invalid_brief", "brief identity does not bind its content")
    sections = brief["sections"]
    if not isinstance(sections, dict) or set(sections) != set(BRIEF_SECTION_NAMES):
        raise InvalidInput("invalid_brief", "brief sections are invalid")
    for name in BRIEF_SECTION_NAMES:
        if not isinstance(sections[name], list):
            raise InvalidInput("invalid_brief", f"brief section {name} must be an array")
    if not isinstance(brief["citations"], dict):
        raise InvalidInput("invalid_brief", "brief citations must be an object")
    for key, citation in brief["citations"].items():
        _validate_brief_citation(key, citation)
    if not isinstance(brief["sources"], list) or not isinstance(brief["warnings"], list):
        raise InvalidInput("invalid_brief", "brief sources/warnings must be arrays")
    return brief


def _unresolved_conflict_item(packet: dict, proposals: list[dict], missing_ids: set[str]) -> dict:
    """Programmatically represent an omitted unresolved conflict.

    Both alternatives and every cited source are carried into the brief so a
    material conflict can never be silently reduced to one side.
    """

    spans = {span["evidence_id"]: span for span in packet["evidence"]}
    # A correction target is a memory item when there is one, and otherwise the
    # corrected evidence span itself, so a note-based conflict is represented
    # with its alternative instead of collapsing to an empty identity.
    target_keys = {
        _span_identity(spans[evidence_id])
        for evidence_id in missing_ids
        if evidence_id in spans
    }
    alternative_ids: list[str] = []
    for proposal in proposals:
        if _proposal_target_key(proposal) in target_keys:
            source_id = proposal.get("source_evidence_id")
            if source_id and source_id not in missing_ids:
                alternative_ids.append(source_id)
    ordered_ids = list(dict.fromkeys(list(missing_ids) + alternative_ids))

    def describe(span: dict) -> str:
        raw = span.get("value") or span.get("quote") or ""
        return " ".join(str(raw).split())[:200]

    parts = []
    for evidence_id in ordered_ids:
        span = spans.get(evidence_id)
        if span is None:
            continue
        role = "unresolved" if evidence_id in missing_ids else "alternative"
        parts.append(f"{role}: {describe(span)}")
    text = "Unresolved conflict (no explicit resolution): " + "; ".join(parts)
    return {
        "text": text[:MAX_TEXT_CHARS],
        "evidence_ids": ordered_ids,
        "status": "unresolved",
        "origin": "source",
    }


def validate_draft(
    packet: object,
    draft: object,
    *,
    vault_root: Path | None = None,
    published_memory: Path | None = None,
    provisional_root: Path | None = None,
    now: str | None = None,
) -> dict:
    """Validate a model draft against its packet and render a resume brief."""

    packet = _validate_packet(packet)
    _check_packet_freshness(
        packet,
        vault_root,
        published_memory=published_memory,
        provisional_root=provisional_root,
    )
    _verify_evidence_binding(
        packet,
        vault_root,
        published_memory=published_memory,
        provisional_root=provisional_root,
    )
    value = _exact_keys(
        draft,
        {
            "schema_version",
            "packet_id",
            "topic_ref",
            "goal",
            "state",
            "decisions",
            "commitments",
            "next_action",
            "open_questions",
            "correction_proposals",
        }
        | ({"overlays"} if isinstance(draft, dict) and "overlays" in draft else set()),
        "draft",
    )
    if value["schema_version"] != DRAFT_SCHEMA_VERSION:
        raise InvalidInput("invalid_schema", "draft schema_version is unsupported")
    if value["packet_id"] != packet["packet_id"]:
        raise InvalidInput("packet_mismatch", "draft was produced for a different packet")
    topic_ref = value["topic_ref"]
    packet_topic = (packet.get("topic") or {}).get("note_ref")
    if topic_ref != packet_topic:
        raise InvalidInput("packet_mismatch", "draft topic_ref does not match the packet topic")
    goal = _validate_section(value, "goal")
    state = _validate_section(value, "state")
    decisions = _validate_section(value, "decisions")
    commitments = _validate_section(value, "commitments", commitment=True)
    next_action = _validate_section(value, "next_action", maximum=1)
    open_questions = _validate_section(value, "open_questions")
    overlays = _validate_overlays(value.get("overlays"))
    proposals = _validate_correction_proposals(packet, value["correction_proposals"])
    effective = _effective_dispositions(packet, proposals)

    evidence_by_id = {span["evidence_id"]: span for span in packet["evidence"]}
    sections = {
        "goal": goal,
        "state": state,
        "decisions": decisions,
        "commitments": commitments,
        "next_action": next_action,
        "open_questions": open_questions,
    }
    for name, items in sections.items():
        for item in items:
            for evidence_id in item["evidence_ids"]:
                if evidence_id not in evidence_by_id:
                    raise InvalidInput(
                        "unknown_evidence",
                        f"{name} item references evidence outside the packet: {evidence_id}",
                    )
            if item["origin"] != "source" or item["status"] != "supported":
                continue
            spans = [
                evidence_by_id[evidence_id] for evidence_id in item["evidence_ids"]
            ]
            if spans and all(
                span.get("kind") == "memory"
                and (span.get("pending_home") or not span.get("durable", True))
                for span in spans
            ):
                # Evidence that exists only in the machine-local outbox has not
                # been published or reconciled.  It can inform the answer, but
                # an item backed solely by it must carry the provisional status
                # so the brief labels it instead of presenting it as supported.
                raise InvalidInput(
                    "provisional_evidence_used_as_supported",
                    (
                        f"{name} item presents machine-local provisional evidence "
                        "as a supported fact"
                    ),
                )
            for evidence_id in item["evidence_ids"]:
                disposition = effective.get(evidence_id)
                if disposition in {"superseded", "retracted"}:
                    raise InvalidInput(
                        "superseded_evidence_used_as_current",
                        f"{name} item presents a {disposition} evidence span as current",
                    )
                if disposition == "unresolved":
                    raise InvalidInput(
                        "unresolved_evidence_used_as_current",
                        f"{name} item presents an unresolved evidence span as current",
                    )
    for overlay in overlays:
        sections["state"].append(
            {
                "text": overlay["text"],
                "evidence_ids": [],
                "status": "provisional",
                "origin": "source",
                "conversation_ref": overlay["conversation_ref"],
                "durable": False,
            }
        )

    warnings: list[str] = []
    if packet["partial"]:
        warnings.append("packet_partial")
    coverage = packet["freshness"].get("coverage_receipt", {})
    if not coverage.get("available"):
        warnings.append("coverage_receipt_unavailable")
    elif coverage.get("stale"):
        warnings.append("coverage_stale")
    if packet["freshness"].get("provisional_included"):
        warnings.append("provisional_included")
    if any(proposal["status"] == "provisional" for proposal in proposals):
        warnings.append("provisional_correction_pending_home")
    unresolved_ids = {
        span["evidence_id"]
        for span in packet["evidence"]
        if effective.get(span["evidence_id"]) == "unresolved"
    }
    if unresolved_ids:
        referenced = {
            evidence_id
            for items in sections.values()
            for item in items
            for evidence_id in item["evidence_ids"]
        }
        missing = unresolved_ids - referenced
        if missing:
            # A material conflict may not be silently reduced to one side: the
            # validator injects an explicit unresolved item carrying both
            # alternatives and all of their sources rather than only warning.
            sections["open_questions"].append(
                _unresolved_conflict_item(packet, proposals, missing)
            )
            warnings.append("unresolved_conflict_included")
    if not packet["evidence"]:
        warnings.append("no_evidence")

    citations = _build_citations(packet)
    brief = {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "kind": "k2b_resume_brief",
        "packet_id": packet["packet_id"],
        "topic_ref": topic_ref,
        "generated_at": _iso(now, "validate now") if now else _now_iso(),
        "sections": sections,
        "corrections": proposals,
        "sources": [_render_source(source, packet) for source in packet["sources"]],
        "citations": citations,
        "freshness": {
            "observed_at": packet["freshness"]["observed_at"],
            "snapshot_hash": packet["freshness"].get("snapshot_hash"),
            "published_memory": packet["freshness"].get("published_memory"),
            "cross_host_sync": "unverified",
            "partial": packet["partial"],
            "reasons": packet["reasons"],
            "provisional_included": packet["freshness"].get("provisional_included", False),
            "coverage_receipt": coverage,
        },
        "warnings": warnings,
        "partial": bool(packet["partial"] or warnings),
    }
    brief["brief_id"] = "brief:" + _sha256_hex(brief)
    return brief


def _render_source(source: dict, packet: dict) -> dict:
    if source.get("local_only"):
        label = "machine-local provisional outbox (not published)"
    else:
        label = source.get("relative_path") or "(unknown source)"
    target = None
    rel = source.get("relative_path")
    if not source.get("local_only") and isinstance(rel, str) and (
        _within_note_roots(rel) or _within_raw_roots(rel) or rel == PUBLISHED_MEMORY_REL
    ):
        target = rel
    return {
        "source_id": source["source_id"],
        "type": source["source_type"],
        "label": label,
        "relative_path": source.get("relative_path"),
        "target": target,
        "hash_short": str(source.get("content_hash"))[:12],
        "source_date": source.get("source_date"),
        "date_quality": source.get("date_quality"),
        "pending_home": bool(source.get("pending_home")),
        "evidence_ids": sorted(
            span["evidence_id"]
            for span in packet["evidence"]
            if span["source_id"] == source["source_id"]
        ),
    }


# --- Rendering ----------------------------------------------------------------


def _local_markdown_target(target: object, vault_root: Path | None) -> str | None:
    """Resolve a verified vault-relative target for local Markdown rendering.

    Stored briefs keep portable vault-relative targets; the machine-readable
    JSON output is never rewritten.  When a render is given this Mac's vault
    root, a verified relative note span is resolved against that root and
    emitted as an absolute local path with an optional ``:line`` target the
    Codex desktop can open.  Anything that cannot be resolved inside the vault
    -- a symlinked parent, an escaping resolution, a missing file, an
    unrecognized fragment -- keeps its safe relative form instead of becoming
    an unsafe absolute link.  No source-host path is ever synthesized.
    """

    if not isinstance(target, str) or not target:
        return None
    if vault_root is None:
        return target
    rel, _, fragment = target.partition("#")
    line: str | None = None
    if fragment:
        match = LINK_FRAGMENT_RE.fullmatch(fragment)
        if match is None:
            return target
        line = match.group(1)
    try:
        path = _bind_local_path(Path(vault_root), rel, "citation link")
    except InvalidInput:
        return target
    if path.is_symlink() or not path.is_file():
        return target
    link = path.as_posix()
    if line is not None:
        link = f"{link}:{line}"
    return link


def render_brief(
    brief: object, *, fmt: str = "markdown", vault_root: Path | None = None
) -> str:
    brief = _validate_brief_for_render(brief)
    if fmt == "json":
        return json.dumps(brief, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if fmt != "markdown":
        raise InvalidInput("invalid_format", "format must be markdown or json")
    # The machine-readable brief keeps vault-relative targets so the artifact
    # stays portable.  Rendering for the local Codex desktop chat resolves each
    # verified note/memory target against this Mac's vault root and emits an
    # absolute local file link with an optional ``:line`` target.
    lines: list[str] = []
    labels = {
        "goal": "Goal",
        "state": "State",
        "decisions": "Decisions",
        "commitments": "Commitments",
        "next_action": "Next",
        "open_questions": "Open questions",
    }
    citations = {
        key: {
            **citation,
            "target": _local_markdown_target(citation.get("target"), vault_root),
        }
        for key, citation in brief["citations"].items()
    }
    for name in ("goal", "state", "decisions", "commitments", "next_action", "open_questions"):
        items = brief["sections"].get(name, [])
        if not items:
            continue
        for item in items:
            suffix = _item_suffix(item, citations)
            lines.append(f"**{labels[name]}:** {item['text']}{suffix}")
    if brief.get("corrections"):
        for correction in brief["corrections"]:
            line = (
                f"**Correction ({correction['status']}):** {correction['relation']} "
                f"{correction['target_evidence_id']} -> {correction['source_evidence_id']}"
            )
            if correction.get("reason") == "source_bound_note_read_view":
                # A read-view resolution between two note passages is not a
                # durable memory repair and must not be read as one.
                line += " (read view only; not written to shared memory)"
            lines.append(line)
    if brief.get("sources"):
        rendered_sources = []
        for source in brief["sources"]:
            link = _local_markdown_target(source.get("target"), vault_root)
            if link:
                rendered_sources.append(f"[{source['label']}]({link})[{source['hash_short']}]")
            else:
                rendered_sources.append(f"{source['label']}[{source['hash_short']}]")
        rendered = ", ".join(rendered_sources)
        lines.append(f"**Sources:** {rendered}")
    freshness = brief["freshness"]
    coverage = freshness.get("coverage_receipt", {})
    coverage_bits = [f"cross-host sync: {freshness['cross_host_sync']}"]
    published_memory = freshness.get("published_memory") or {"state": "unknown"}
    published_state = published_memory.get("state")
    if published_state == "present":
        coverage_bits.append("published memory snapshot: present")
    elif published_state == "unsupported":
        coverage_bits.append("published memory snapshot: present but unsupported")
    elif published_state == "absent":
        coverage_bits.append(
            "published memory snapshot: absent (coverage gap; Home publication "
            "may be unavailable)"
        )
    if not coverage.get("available"):
        coverage_bits.append("local coverage: unknown (no recent receipt)")
    else:
        observed_bit = f"local coverage observed at {coverage.get('observed_at')}"
        if coverage.get("observed_stale", coverage.get("stale")):
            observed_bit += " (stale)"
        coverage_bits.append(observed_bit)
        global_pending = coverage.get("pending_turns")
        if global_pending is not None:
            pending_bit = (
                f"global pending turns (not this topic): {global_pending}"
            )
            oldest = coverage.get("oldest_pending_at")
            if oldest:
                pending_bit += f", oldest {oldest}"
            pending_observed = coverage.get("pending_observed_at")
            if pending_observed:
                pending_bit += f", observed at {pending_observed}"
            if coverage.get("pending_stale"):
                pending_bit += " (stale turn-level observation)"
            coverage_bits.append(
                pending_bit
            )
        else:
            reason = coverage.get("pending_reason") or "turn-level evidence unavailable"
            coverage_bits.append(f"global pending turns: unknown ({reason})")
        session_waiting = coverage.get("session_waiting")
        if session_waiting is not None:
            coverage_bits.append(
                f"legacy session-file waiting count: {session_waiting}"
            )
    if freshness.get("provisional_included"):
        coverage_bits.append("includes machine-local provisional memory")
    if freshness.get("partial"):
        coverage_bits.append("partial: " + ",".join(freshness.get("reasons") or ["unknown"]))
    lines.append("**Coverage:** " + "; ".join(coverage_bits))
    if brief.get("warnings"):
        lines.append("**Warnings:** " + ", ".join(brief["warnings"]))
    return "\n".join(lines) + "\n"


def _citation_ref(citation: dict) -> str:
    label = citation["label"]
    target = citation.get("target")
    provenance = citation["provenance_id"]
    ref = f"[{label}]({target})" if target else label
    ref += f" (provenance: {provenance})"
    if citation.get("limitation"):
        ref += f" — {citation['limitation']}"
    return ref


def _item_suffix(item: dict, citations: dict[str, dict]) -> str:
    bits = []
    if item["status"] != "supported":
        bits.append(item["status"])
    if item.get("origin") == "recommendation":
        bits.append("recommendation")
    if item.get("owner"):
        bits.append(f"owner: {item['owner']}")
    if item.get("due_at"):
        bits.append(f"due: {item['due_at']}")
    if item.get("completion_state"):
        bits.append(f"completion: {item['completion_state']}")
    if item.get("conversation_ref"):
        bits.append(f"conversation: {item['conversation_ref']}")
    if item.get("evidence_ids"):
        refs = []
        for evidence_id in item["evidence_ids"]:
            citation = citations.get(evidence_id)
            refs.append(_citation_ref(citation) if citation else evidence_id)
        bits.append("sources: " + "; ".join(refs))
    return (" [" + "; ".join(bits) + "]") if bits else ""
