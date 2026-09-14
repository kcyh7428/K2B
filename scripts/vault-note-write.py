#!/usr/bin/env python3
# scripts/vault-note-write.py
# Simple local ordinary-note writer for the two-Mac K2B vault.
#
# Part of the approved simple Markdown/Syncthing note-saving path
# (.kimi/ship-card.md, 2026-09-14). Ordinary note saves complete locally on
# either Mac without Home availability. There is no event database, revision
# transport, automatic merge, distributed lock, or deletion here.
#
# Usage:
#   vault-note-write.py write --path <vault-rel.md> --content-file <abs>
#       --expected-sha256 <sha256|missing> --summary '<one line>'
#       --source-ref '<citation id>' [--status '<one line'>]
#       [--section '<existing heading'>]
#   vault-note-write.py conflicts
#
# Write runs only for the approved operators (keithmbpm2, keithcheung),
# resolved from the effective UID via the OS account database (never from
# spoofable environment variables); conflicts stays read-only for any user.
#
# An existing note row in the folder index uniquely determines the owning
# table. For a NEW note in an index with several eligible tables, name the
# owning heading with --section; without it the save fails before mutation.
# Verified owning schemas: Page|Status|Summary|Updated (work, projects),
# Page|Summary|Updated (insights, reference), Page|Role/Context|Updated
# (people sections), Page|Ship / Phase|Priority|Effort|Updated and
# Page|Priority|Effort|Impact|Updated (concepts). The Shipped schema is
# never an ordinary save destination.
#
# write exit codes:
#   0  saved locally (or identical replay; synchronization unverified)
#   1  validation failure (path, content, frontmatter, shape, identity)
#   2  real Syncthing conflict copy present, or expected hash is stale
#   3  partial: note saved but a later step failed (retry same bytes to finish)
#   4  lock timeout
#   5  backup/receipt failure before mutation (all files untouched)
#
# conflicts exit code is always 0; read failures and scan deadline overruns
# print an "unknown" summary instead of a false all-clear. It never contacts
# a host, provider or the Syncthing API, and never writes anything.
#
# Format rule: no em dashes anywhere. Use "--".

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import re
import stat
import sys
import tempfile
import time
from datetime import date, datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOWED_ROOTS = (
    "wiki/work",
    "wiki/people",
    "wiki/projects",
    "wiki/concepts",
    "wiki/insights",
    "wiki/reference",
)
ALLOWED_USERS = ("keithmbpm2", "keithcheung")
REQUIRED_FRONTMATTER_FIELDS = ("tags", "date", "type", "origin", "up")

MAX_NOTE_BYTES = 2 * 1024 * 1024
MAX_INDEX_BYTES = 5 * 1024 * 1024
MAX_SUMMARY_CHARS = 500
MAX_STATUS_CHARS = 100
MAX_SOURCE_REF_CHARS = 300
CONFLICT_REPORT_LIMIT = 10
CONFLICT_WALK_LIMIT = 20000
CONFLICT_DEADLINE_SECONDS = 2.0

LOCK_MAX_TRIES = 200  # 200 * 0.05s = 10s, same budget as compile-index-update
LOCK_SLEEP = 0.05


class WriteError(Exception):
    """Validation failure (exit 1)."""


class ConflictError(Exception):
    """Conflict copy present or stale expected hash (exit 2)."""


class ShapeError(WriteError):
    """Missing or malformed folder index (exit 1)."""


class BackupError(Exception):
    """Durable private copy or receipt failure (exit 5)."""


# --- environment-derived paths ---------------------------------------------


def vault_root():
    root = os.environ.get("K2B_VAULT_PATH") or os.environ.get("K2B_VAULT_ROOT")
    if not root:
        root = os.path.join(os.path.expanduser("~"), "Projects", "K2B-Vault")
    return os.path.realpath(os.path.abspath(root))


def state_root():
    root = os.environ.get("K2B_LOCAL_STATE")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".local", "state", "k2b")
    return os.path.abspath(root)


def writer_lock_dir():
    return os.environ.get("K2B_NOTE_WRITE_LOCK", "/tmp/k2b-note-write.lock.d")


def compile_lock_dir():
    return os.environ.get(
        "K2B_COMPILE_INDEX_LOCK", "/tmp/k2b-compile-index.lock.d"
    )


def backups_root():
    return os.path.join(state_root(), "note-write-backups")


def receipts_root():
    return os.path.join(state_root(), "note-write-receipts")


def load_compile_index():
    """Import scripts/compile-index-update.py for its designated helpers.

    Imported lazily so environment overrides are read at call time. Its
    module-level VAULT_ROOT resolves from K2B_VAULT_ROOT, so mirror the
    writer's resolved vault there for the duration of the import. This also
    overrides any stale inherited K2B_VAULT_ROOT when K2B_VAULT_PATH is the
    active override.
    """
    previous = os.environ.get("K2B_VAULT_ROOT")
    os.environ["K2B_VAULT_ROOT"] = vault_root()
    path = os.path.join(REPO_ROOT, "scripts", "compile-index-update.py")
    spec = importlib.util.spec_from_file_location(
        "k2b_compile_index_update", path
    )
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    finally:
        if previous is None:
            os.environ.pop("K2B_VAULT_ROOT", None)
        else:
            os.environ["K2B_VAULT_ROOT"] = previous
    return mod


# --- locks ------------------------------------------------------------------


def acquire_lock(path):
    tries = 0
    while True:
        try:
            os.mkdir(path)
            return
        except FileExistsError:
            tries += 1
            if tries > LOCK_MAX_TRIES:
                raise TimeoutError("could not acquire lock " + path)
        except OSError as exc:
            raise WriteError("lock unavailable: " + path + ": " + str(exc))
        time.sleep(LOCK_SLEEP)


def release_lock(path):
    try:
        os.rmdir(path)
    except OSError:
        pass


# --- durable private copies ---------------------------------------------------


def durable_private_copy(subdir, data):
    """Write content-addressed bytes under state backups; return rel key.

    Dedup is global across before/ and after/: if these exact bytes are
    already preserved in either role, that existing key is returned and no
    second copy is made. The returned key always names a file that exists.
    """
    digest = hashlib.sha256(data).hexdigest()
    name = digest + ".md"
    for role in (subdir, "after" if subdir == "before" else "before"):
        candidate = os.path.join(backups_root(), role, name)
        if os.path.exists(candidate):
            return os.path.join("note-write-backups", role, name)
    directory = os.path.join(backups_root(), subdir)
    final = os.path.join(directory, name)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise BackupError("cannot create backup dir " + directory + ": " + str(exc))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cpy-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise BackupError("cannot write backup " + final + ": " + str(exc))
    if not os.path.exists(final):
        raise BackupError("backup missing after write: " + final)
    return os.path.join("note-write-backups", subdir, name)


def atomic_replace(path, data, mode):
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".vnw-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def recheck_bytes(path, planned_bytes, label):
    """Confirm disk still holds the bytes a plan was computed from."""
    current = _read_bytes(path)
    if current != planned_bytes:
        raise ConflictError(
            label + " changed while saving; external edit detected: " + path
        )
    return current


# --- validation ---------------------------------------------------------------


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def current_user_name():
    """Resolve the effective UID through the OS, never through the
    environment (LOGNAME/USER are spoofable)."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        raise WriteError(
            "cannot resolve effective uid %d to a user name" % os.geteuid()
        )


def validate_operator():
    user = current_user_name()
    if user not in ALLOWED_USERS:
        raise WriteError(
            "current user %r is not an approved K2B operator" % user
        )
    return user


def validate_note_rel(rel):
    if not rel or rel.startswith("/"):
        raise WriteError("note path must be a vault-relative path")
    parts = rel.split("/")
    folded = [p.casefold() for p in parts]
    for part in parts:
        if part in ("", ".", ".."):
            raise WriteError("note path must not contain empty, '.' or '..' parts")
        if part.startswith("."):
            raise WriteError("note path must not contain hidden parts: " + part)
    joined = "/".join(parts)
    folded_joined = "/".join(folded)
    # macOS volumes are case-insensitive by default: validate the
    # case-folded path family so aliases cannot bypass protected
    # components or land outside the ordinary roots.
    allowed = any(
        folded_joined == root or folded_joined.startswith(root + "/")
        for root in ALLOWED_ROOTS
    )
    if not allowed:
        raise WriteError(
            "note path is outside the ordinary note roots: " + joined
        )
    if "shipped" in folded:
        raise WriteError("Shipped/ is not an ordinary note target")
    base = parts[-1]
    if base.casefold() == "index.md":
        raise WriteError("index.md is not an ordinary note target")
    if not base.endswith(".md"):
        raise WriteError("ordinary notes must be Markdown (.md)")
    return joined


def check_target_chain(vault_real, rel):
    """Reject symlinks and non-regular files along the target chain."""
    parts = rel.split("/")
    cur = vault_real
    for i, part in enumerate(parts):
        names = os.listdir(cur)
        if part not in names and any(n.casefold() == part.casefold() for n in names):
            raise WriteError("note path must use the canonical on-disk case: " + part)
        cur = os.path.join(cur, part)
        if not os.path.lexists(cur):
            return  # missing tail is fine
        st = os.lstat(cur)
        if stat.S_ISLNK(st.st_mode):
            raise WriteError("refusing symlink in note path: " + cur)
        last = i == len(parts) - 1
        if last and not stat.S_ISREG(st.st_mode):
            raise WriteError("note target is not a regular file: " + cur)
        if not last and not stat.S_ISDIR(st.st_mode):
            raise WriteError("note path crosses a non-directory: " + cur)


def require_regular_not_symlink(path, label):
    st = os.lstat(path)
    if stat.S_ISLNK(st.st_mode):
        raise ShapeError(label + " must not be a symlink: " + path)
    if not stat.S_ISREG(st.st_mode):
        raise ShapeError(label + " must be a regular file: " + path)


def validate_state_location(vault_real):
    state = state_root()
    state_real = os.path.realpath(state)
    if state_real == vault_real or state_real.startswith(vault_real + os.sep):
        raise WriteError("local state must not resolve inside the vault: " + state)
    if os.path.lexists(state) and not os.path.isdir(state):
        raise WriteError("local state path is not a directory: " + state)


def read_and_validate_content(content_file, vault_real):
    if not os.path.isabs(content_file):
        raise WriteError("--content-file must be an absolute path")
    if os.path.islink(content_file):
        raise WriteError("--content-file must not be a symlink")
    real = os.path.realpath(content_file)
    if real == vault_real or real.startswith(vault_real + os.sep):
        raise WriteError("--content-file must live outside the vault")
    if os.path.lexists(real) and stat.S_ISLNK(os.lstat(real).st_mode):
        raise WriteError("--content-file must not be a symlink")
    if not os.path.isfile(real):
        raise WriteError("--content-file is not a regular file: " + content_file)
    size = os.path.getsize(real)
    if size > MAX_NOTE_BYTES:
        raise WriteError(
            "note content exceeds %d bytes" % MAX_NOTE_BYTES
        )
    with open(real, "rb") as f:
        data = f.read()
    if not data.strip():
        raise WriteError("note content is empty")
    if b"\x00" in data:
        raise WriteError("note content contains NUL bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise WriteError("note content is not valid UTF-8")
    validate_frontmatter(text)
    return data


def validate_frontmatter(text):
    """Require closed YAML frontmatter with the ordinary-note core fields."""
    try:
        import yaml
    except ImportError:
        raise WriteError(
            "PyYAML is required for 'write'; run with a python that has "
            "PyYAML (for example the project virtualenv interpreter when "
            "present) -- nothing was installed automatically"
        )
    match = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    if not match:
        raise WriteError(
            "note must start with a closed YAML frontmatter block (--- ... ---)"
        )
    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        raise WriteError("frontmatter is not valid YAML")
    if not isinstance(frontmatter, dict):
        raise WriteError("frontmatter must be a YAML mapping")
    for field in REQUIRED_FRONTMATTER_FIELDS:
        if field not in frontmatter or frontmatter[field] in (None, ""):
            raise WriteError(
                "frontmatter missing required ordinary-note field: " + field
            )
    if not match.group(2).strip():
        raise WriteError("note body must not be empty")


def validate_expected(value):
    if value == "missing":
        return value
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise WriteError(
            "--expected-sha256 must be a sha256 hex digest or 'missing'"
        )
    return value


def validate_line_field(name, value, max_chars, allow_empty=False):
    if value is None:
        if allow_empty:
            return None
        raise WriteError(name + " is required")
    if "\n" in value or "\r" in value:
        raise WriteError(name + " must be a single line")
    if "|" in value:
        raise WriteError(name + " must not contain table delimiters (|)")
    value = value.strip()
    if not value and not allow_empty:
        raise WriteError(name + " must not be empty")
    if len(value) > max_chars:
        raise WriteError(name + " exceeds %d characters" % max_chars)
    return value


# --- conflict copies -----------------------------------------------------------


def conflict_copies(directory, stem):
    prefix = stem.casefold() + ".sync-conflict-"
    try:
        names = os.listdir(directory)
    except OSError as exc:
        # A failed scan must never read as "no conflicts"; treat unreadable,
        # missing, or racing directories as an explicit pre-mutation error.
        raise WriteError(
            "cannot scan %s for conflict copies: %s" % (directory, exc)
        )
    return sorted(
        n for n in names if n.casefold().startswith(prefix) and n.casefold().endswith(".md")
    )


# --- folder index table handling -------------------------------------------------

# Verified live folder-index owning schemas. Keys are normalized header
# cells (whitespace-collapsed, case-folded). "shipped" is recognized but is
# never an ordinary save destination.
SCHEMA_KINDS = {
    ("page", "status", "summary", "updated"): "status",  # work, projects
    ("page", "summary", "updated"): "summary",  # insights, reference
    ("page", "role/context", "updated"): "role",  # people sections
    ("page", "ship / phase", "priority", "effort", "updated"): "ship",
    ("page", "priority", "effort", "impact", "updated"): "backlog",
    ("page", "shipped", "notes"): "shipped",
}
# Which column carries the one-line summary (and receives --summary).
SUMMARY_COLUMN = {"status": 2, "summary": 1, "role": 1}
# Which column carries --status.
STATUS_COLUMN = {"status": 1}


def count_pages_scoped(dir_abs):
    """Designated compile count_pages semantics, minus .sync-conflict-*.md
    artifacts. Scoped to this writer: compile-index-update.py is not edited
    and its global semantics are unchanged."""
    if not os.path.isdir(dir_abs):
        return 0
    n = 0
    for entry in os.listdir(dir_abs):
        path = os.path.join(dir_abs, entry)
        if os.path.islink(path):
            continue
        if os.path.isdir(path):
            # A nested folder without its own index is owned by this index.
            if entry.casefold() != "shipped" and not os.path.exists(os.path.join(path, "index.md")):
                n += count_pages_scoped(path)
            continue
        if not entry.endswith(".md") or entry == "index.md":
            continue
        if ".sync-conflict-" in entry:
            continue
        if os.path.isfile(os.path.join(dir_abs, entry)):
            n += 1
    return n


def rewrite_master_index_scoped(compile_mod):
    """Run the designated master rewrite with conflict artifacts excluded
    from counts, restoring the helper immediately afterwards."""
    original = compile_mod.count_pages
    compile_mod.count_pages = count_pages_scoped
    try:
        return compile_mod.rewrite_master_index()
    finally:
        compile_mod.count_pages = original


def split_row(line):
    # Markdown escapes inside wikilinks are cell content, not delimiters.
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def page_target(cell):
    """Compare link destinations without changing the displayed alias."""
    if cell.startswith("[[") and cell.endswith("]]"):
        return "[[" + cell[2:-2].split("\\|", 1)[0] + "]]"
    return cell


def is_separator_row(line, width):
    if not line.strip().startswith("|"):
        return False
    cells = split_row(line)
    if len(cells) != width:
        return False
    return all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells)


def schema_kind(header_cells):
    norm = tuple(" ".join(c.split()).casefold() for c in header_cells)
    return SCHEMA_KINDS.get(norm)


def parse_tables(lines):
    """Parse all recognized-schema tables, tracking the nearest preceding
    heading. Unrelated tables and sections are left untouched."""
    tables = []
    heading = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        hm = re.match(r"^#{1,6}\s+(.*?)\s*$", line)
        if hm:
            heading = re.sub(r"\s*#+\s*$", "", hm.group(1)).strip()
            idx += 1
            continue
        if line.strip().startswith("|"):
            cells = split_row(line)
            kind = schema_kind(cells)
            if kind is None:
                idx += 1
                continue
            width = len(cells)
            if idx + 1 >= len(lines) or not is_separator_row(
                lines[idx + 1], width
            ):
                raise ShapeError("folder index table separator row is malformed")
            first = idx + 2
            end = first
            rows = []
            pages = {}
            while end < len(lines) and lines[end].strip().startswith("|"):
                row_cells = split_row(lines[end])
                if len(row_cells) != width:
                    raise ShapeError(
                        "folder index row has wrong column count: "
                        + lines[end].strip()
                    )
                rows.append(end)
                pages[end] = page_target(row_cells[0])
                end += 1
            tables.append(
                {
                    "kind": kind,
                    "width": width,
                    "end": end,
                    "rows": rows,
                    "pages": pages,
                    "heading": heading,
                }
            )
        idx += 1
    return tables


def select_owner_table(tables, target, section):
    """Pick the one table that actually owns the note.

    An existing note row uniquely determines the owner. A new note uses
    --section to name the heading above the owning table; without it, a
    single eligible table is unambiguous and multiple eligible tables fail
    before any mutation.
    """
    eligible = [t for t in tables if t["kind"] != "shipped"]
    if any(target in t["pages"].values() for t in tables if t["kind"] == "shipped"):
        raise ShapeError("note belongs to the Shipped table")
    owners = [t for t in eligible if target in t["pages"].values()]
    if len(owners) > 1:
        raise ShapeError(
            "note row appears in multiple index tables; refusing ambiguous update"
        )
    if owners:
        return owners[0]
    if section is not None:
        want = " ".join(section.split()).casefold()
        matches = [t for t in eligible if " ".join((t["heading"] or "").split()).casefold() == want]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ShapeError("--section matches multiple tables; refusing ambiguous save")
        raise ShapeError(
            "--section %r does not name a heading above an eligible table"
            % section
        )
    if len(eligible) == 1:
        return eligible[0]
    if not eligible:
        raise ShapeError(
            "no eligible Page table in folder index "
            "(the Shipped table is not an ordinary save destination)"
        )
    raise ShapeError(
        "multiple eligible Page tables; supply --section naming the owning heading"
    )


def update_folder_index_text(
    text, note_stem, summary, status, section, folder_dir_abs, compile_mod
):
    """Apply the note row update plus date/count header line.

    Only the owning table's row changes; unrelated cells, rows, tables and
    sections are preserved byte-for-byte.
    """
    lines = text.split("\n")
    tables = parse_tables(lines)
    if not tables:
        raise ShapeError("unrecognized folder index table header (no known Page schema)")
    target = "[[%s]]" % note_stem
    owner = select_owner_table(tables, target, section)
    if status is not None and owner["kind"] not in STATUS_COLUMN:
        raise ShapeError(
            "--status is only supported for Page | Status | Summary | Updated tables"
        )
    today = date.today().isoformat()
    width = owner["width"]
    kind = owner["kind"]
    summary_col = SUMMARY_COLUMN.get(kind)

    replaced = False
    for ri in owner["rows"]:
        cells = split_row(lines[ri])
        if page_target(cells[0]) != target:
            continue
        new_cells = list(cells)
        if summary_col is not None:
            new_cells[summary_col] = summary
        if kind in STATUS_COLUMN:
            new_cells[STATUS_COLUMN[kind]] = (
                status if status is not None else cells[STATUS_COLUMN[kind]]
            )
        new_cells[width - 1] = today
        lines[ri] = "| " + " | ".join(new_cells) + " |"
        replaced = True
    if not replaced:
        new_cells = ["-"] * width
        new_cells[0] = target
        if summary_col is not None:
            new_cells[summary_col] = summary
        if kind in STATUS_COLUMN:
            new_cells[STATUS_COLUMN[kind]] = (
                status if status is not None else "-"
            )
        new_cells[width - 1] = today
        lines.insert(owner["end"], "| " + " | ".join(new_cells) + " |")

    updated = "\n".join(lines)

    new_count = count_pages_scoped(folder_dir_abs)

    def header_sub(match):
        return (
            match.group(1)
            + today
            + match.group(3)
            + str(new_count)
            + match.group(5)
        )

    updated, n = compile_mod.SUBFOLDER_LINE_RE.subn(
        header_sub, updated, count=1
    )
    if n != 1:
        raise ShapeError("folder index header line (Last updated/Entries) missing")
    return updated


# --- receipts ---------------------------------------------------------------------


def operation_key(rel, data, summary, status, source_ref, section):
    payload = json.dumps(
        [rel, sha256_hex(data), summary, status, source_ref, section],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def read_existing_receipt(op_key):
    """Return the persisted receipt payload for this operation, or None.

    A missing or unreadable receipt means the operation's history is
    unknown; callers treat that as "no matching unfinished operation".
    """
    path = os.path.join(receipts_root(), op_key + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_receipt_file(op_key, payload):
    directory = receipts_root()
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise BackupError("cannot create receipt dir " + directory + ": " + str(exc))
    final = os.path.join(directory, op_key + ".json")
    receipt = dict(payload)
    receipt["time_utc"] = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S.%fZ"
    )
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".rcpt-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise BackupError("cannot write receipt " + final + ": " + str(exc))
    if not os.path.exists(final):
        raise BackupError("receipt missing after write: " + final)
    return os.path.join("note-write-receipts", op_key + ".json")


# --- write command ---------------------------------------------------------------


def cmd_write(args):
    try:
        return _cmd_write(args)
    except WriteError as exc:
        sys.stderr.write("vault-note-write: " + str(exc) + "\n")
        return 1
    except ConflictError as exc:
        sys.stderr.write("vault-note-write: " + str(exc) + "\n")
        return 2
    except TimeoutError as exc:
        sys.stderr.write("vault-note-write: " + str(exc) + "\n")
        return 4
    except BackupError as exc:
        sys.stderr.write("vault-note-write: " + str(exc) + "\n")
        return 5
    except (OSError, UnicodeError) as exc:
        sys.stderr.write("vault-note-write: input unavailable: " + str(exc) + "\n")
        return 1


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def _read_index_bytes(path, label):
    if os.path.getsize(path) > MAX_INDEX_BYTES:
        raise ShapeError(label + " exceeds %d bytes" % MAX_INDEX_BYTES)
    return _read_bytes(path)


def _cmd_write(args):
    rel = validate_note_rel(args.path)
    summary = validate_line_field(
        "--summary", args.summary, MAX_SUMMARY_CHARS
    )
    status = validate_line_field(
        "--status", args.status, MAX_STATUS_CHARS, allow_empty=True
    )
    section = validate_line_field(
        "--section", args.section, MAX_STATUS_CHARS, allow_empty=True
    )
    source_ref = validate_line_field(
        "--source-ref", args.source_ref, MAX_SOURCE_REF_CHARS
    )
    expected = validate_expected(args.expected_sha256)
    user = validate_operator()

    vault_real = vault_root()
    if not os.path.isdir(vault_real):
        raise WriteError("vault root not found: " + vault_real)
    validate_state_location(vault_real)
    check_target_chain(vault_real, rel)
    canonical_rel = os.path.relpath(
        os.path.realpath(os.path.join(vault_real, rel)), vault_real
    )
    if canonical_rel != rel:
        raise WriteError(
            "note path must use the canonical on-disk case: " + canonical_rel
        )
    data = read_and_validate_content(args.content_file, vault_real)

    target_abs = os.path.join(vault_real, rel)
    target_dir = os.path.dirname(target_abs)
    note_stem = os.path.splitext(os.path.basename(rel))[0]

    op_key = operation_key(rel, data, summary, status, source_ref, section)
    base_receipt = {
        "operation": op_key,
        "user": user,
        "path": rel,
        "note_sha256": sha256_hex(data),
        "summary": summary,
        "status": status,
        "section": section,
        "source_ref": source_ref,
        "expected_sha256": expected,
        "synchronized": "unverified",
        "backups": {},
    }

    lock_path = writer_lock_dir()
    acquire_lock(lock_path)
    try:
        index_lock = compile_lock_dir()
        acquire_lock(index_lock)
        try:
            return _write_locked(
                args,
                rel,
                summary,
                status,
                section,
                expected,
                vault_real,
                data,
                target_abs,
                target_dir,
                note_stem,
                op_key,
                base_receipt,
            )
        finally:
            release_lock(index_lock)
    finally:
        release_lock(lock_path)


def _write_locked(
    args,
    rel,
    summary,
    status,
    section,
    expected,
    vault_real,
    data,
    target_abs,
    target_dir,
    note_stem,
    op_key,
    base_receipt,
):
    """All validation, durable copies and mutations, both locks held.

    Nothing is modified until validation and planning finish and the
    private before/after copies plus the intent receipt are durable. Any
    failure after the note replacement is reported as partial with the
    saved path and the failed step. A completed identical replay is a pure
    no-op: no note, index, backup or receipt rewrite.
    """
    compile_mod = load_compile_index()

    # --- validate and plan (no mutation) ---
    index_rel = compile_mod.resolve_subfolder_index(rel)
    if index_rel is None:
        raise ShapeError("no containing folder index.md found for " + rel)
    folder_index_abs = os.path.join(vault_real, index_rel)
    note_stem = os.path.splitext(os.path.relpath(target_abs, os.path.dirname(folder_index_abs)))[0]
    require_regular_not_symlink(folder_index_abs, "folder index")
    master_abs = os.path.join(vault_real, "wiki", "index.md")
    if not os.path.isfile(master_abs):
        raise ShapeError("master index missing: wiki/index.md")
    require_regular_not_symlink(master_abs, "master index")

    folder_index_bytes = _read_index_bytes(folder_index_abs, "folder index")
    try:
        folder_text = folder_index_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise ShapeError("folder index is not valid UTF-8: " + index_rel)
    update_folder_index_text(
        folder_text,
        note_stem,
        summary,
        status,
        section,
        os.path.dirname(folder_index_abs),
        compile_mod,
    )
    try:
        rewrite_master_index_scoped(compile_mod)
    except SystemExit as exc:
        raise ShapeError(
            "master index shape validation failed (exit %s)" % exc.code
        )

    def require_no_conflicts():
        conflicts = []
        for directory, stem in ((target_dir, os.path.splitext(os.path.basename(rel))[0]),
                                (os.path.dirname(folder_index_abs), "index"),
                                (os.path.dirname(master_abs), "index")):
            conflicts.extend(os.path.join(directory, name) for name in conflict_copies(directory, stem))
        if conflicts:
            raise ConflictError("Syncthing conflict copy present; preserve both versions: " + ", ".join(conflicts[:CONFLICT_REPORT_LIMIT]))
    require_no_conflicts()

    existing_receipt = read_existing_receipt(op_key)
    matching = isinstance(existing_receipt, dict) and all(
        existing_receipt.get(key) == base_receipt[key]
        for key in ("operation", "path", "note_sha256", "summary", "status", "section", "source_ref")
    ) and existing_receipt.get("result") in ("in-progress", "partial", "saved-local", "already-present")
    if not matching:
        existing_receipt = None

    if os.path.lexists(target_abs):
        current = _read_bytes(target_abs)
        recorded_replay = bool(existing_receipt) and current == data and existing_receipt.get("expected_sha256") == expected
        if expected == "missing" and not recorded_replay:
            if existing_receipt and current != data:
                raise ConflictError("note changed after the recorded save; preserve the external edit: " + rel)
            raise WriteError(
                "--expected-sha256 missing but note exists: " + rel
            )
        if sha256_hex(current) != expected and not recorded_replay:
            raise ConflictError(
                "stale expected hash for " + rel + "; target changed"
            )
    else:
        if expected != "missing":
            raise ConflictError(
                "stale expected hash for "
                + rel
                + "; note does not exist"
            )
        current = None

    replay = current is not None and current == data
    completed_results = ("saved-local", "already-present")
    unfinished = bool(existing_receipt) and existing_receipt.get(
        "result"
    ) not in completed_results
    if replay and existing_receipt and not unfinished:
        # Completed identical operation with a matching durable receipt:
        # never rewrite note/index dates, backups or the receipt, on any
        # calendar day. Only a matching unfinished operation repairs.
        print(
            json.dumps(
                {
                    "result": "already-present",
                    "path": rel,
                    "folder_index": index_rel,
                    "note": "saved locally; synchronization unverified",
                    "receipt": os.path.join(
                        "note-write-receipts", op_key + ".json"
                    ),
                }
            )
        )
        return 0

    # --- persist before/after copies and intent receipt before mutation ---
    backups = {}
    if existing_receipt:
        # Retry of an unfinished operation: keep the original backup
        # references instead of replacing them with an empty replay map.
        prior = existing_receipt.get("backups")
        if isinstance(prior, dict):
            backups.update(prior)
    if not replay:
        if current is not None:
            backups["note_before"] = durable_private_copy("before", current)
    if "note_after" not in backups:
        backups["note_after"] = durable_private_copy("after", data)
    intent = dict(base_receipt)
    intent.update(
        {
            "result": "in-progress",
            "backups": dict(backups),
            "receipt": os.path.join("note-write-receipts", op_key + ".json"),
        }
    )
    write_receipt_file(op_key, intent)

    # --- mutate note (failure here leaves the vault untouched) ---
    if not replay:
        require_no_conflicts()
        check_target_chain(vault_real, rel)
        mode = 0o644
        if current is not None:
            mode = stat.S_IMODE(os.lstat(target_abs).st_mode)
            recheck_bytes(target_abs, current, "note target")
        elif os.path.lexists(target_abs):
            # A new note must not overwrite a file that appeared after
            # the intent receipt was durable.
            raise ConflictError(
                "note target appeared after intent receipt; refusing to "
                "overwrite: " + rel
            )
        try:
            atomic_replace(target_abs, data, mode)
        except OSError as exc:
            raise WriteError("cannot write note " + rel + ": " + str(exc))

    # --- mutate indexes; failures here are partial (note already saved) ---
    try:
        fresh_folder_bytes = _read_index_bytes(folder_index_abs, "folder index")
        backups["folder_index_before"] = durable_private_copy(
            "before", fresh_folder_bytes
        )
        fresh_folder_text = fresh_folder_bytes.decode("utf-8")
        new_folder_text = update_folder_index_text(
            fresh_folder_text,
            note_stem,
            summary,
            status,
            section,
            os.path.dirname(folder_index_abs),
            compile_mod,
        )
        backups["folder_index_after"] = durable_private_copy(
            "after", new_folder_text.encode("utf-8")
        )
        recheck_bytes(
            folder_index_abs, fresh_folder_bytes, "folder index"
        )
        require_no_conflicts()
        require_regular_not_symlink(folder_index_abs, "folder index")
        atomic_replace(
            folder_index_abs,
            new_folder_text.encode("utf-8"),
            stat.S_IMODE(os.lstat(folder_index_abs).st_mode),
        )

        fresh_master_bytes = _read_bytes(master_abs)
        backups["master_index_before"] = durable_private_copy(
            "before", fresh_master_bytes
        )
        new_master_text = rewrite_master_index_scoped(compile_mod)
        backups["master_index_after"] = durable_private_copy(
            "after", new_master_text.encode("utf-8")
        )
        recheck_bytes(master_abs, fresh_master_bytes, "master index")
        require_no_conflicts()
        require_regular_not_symlink(master_abs, "master index")
        atomic_replace(
            master_abs,
            new_master_text.encode("utf-8"),
            stat.S_IMODE(os.lstat(master_abs).st_mode),
        )
    except (OSError, UnicodeDecodeError, SystemExit, ShapeError,
            WriteError, BackupError, ConflictError) as exc:
        if isinstance(exc, SystemExit):
            detail = "index helper exited with code %s" % (exc.code,)
        else:
            detail = str(exc)
        partial = dict(base_receipt)
        partial.update(
            {
                "result": "partial",
                "saved": rel,
                "failed_step": "index",
                "detail": detail,
                "note": "note saved locally; index step failed; retry the same content to repair",
                "backups": dict(backups),
            }
        )
        if isinstance(exc, ConflictError):
            partial["failed_step"] = "conflict"
            partial["note"] = "note saved locally; stop and resolve the reported conflict manually before retrying"
        try:
            write_receipt_file(op_key, partial)
        except (BackupError, OSError):
            pass
        print(json.dumps({k: partial[k] for k in (
            "result", "saved", "failed_step", "detail", "note"
        )}))
        return 2 if isinstance(exc, ConflictError) else 3

    final = dict(base_receipt)
    final.update(
        {
            "result": "already-present" if replay else "saved-local",
            "folder_index": index_rel,
            "backups": dict(backups),
        }
    )
    try:
        write_receipt_file(op_key, final)
    except (BackupError, OSError) as exc:
        partial = dict(base_receipt)
        partial.update(
            {
                "result": "partial",
                "saved": rel,
                "failed_step": "receipt",
                "detail": str(exc),
                "note": "note and indexes saved locally; receipt finalization failed; retry the same content to repair",
                "backups": dict(backups),
            }
        )
        try:
            write_receipt_file(op_key, partial)
        except (BackupError, OSError):
            pass
        print(json.dumps({k: partial[k] for k in (
            "result", "saved", "failed_step", "detail", "note"
        )}))
        return 3
    result = {
        "result": "already-present" if replay else "saved-local",
        "path": rel,
        "folder_index": index_rel,
        "note": "saved locally; synchronization unverified",
        "receipt": os.path.join("note-write-receipts", op_key + ".json"),
    }
    print(json.dumps(result))
    return 0


def rel_dirname(rel):
    parent = os.path.dirname(rel)
    return parent if parent else "."


# --- conflicts command ------------------------------------------------------------


def cmd_conflicts(_args):
    root = vault_root()
    if not os.path.isdir(root):
        print("VAULT CONFLICTS: unknown (vault not readable: %s)" % root)
        return 0
    found = []
    errors = []
    walked = 0
    truncated = False

    def onerror(exc):
        errors.append(exc)

    deadline = time.monotonic() + CONFLICT_DEADLINE_SECONDS
    try:
        for dirpath, dirnames, filenames in os.walk(root, onerror=onerror):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            if time.monotonic() > deadline:
                truncated = "time budget exceeded"
                break
            for name in filenames:
                walked += 1
                if walked > CONFLICT_WALK_LIMIT:
                    truncated = "walk limit reached"
                    break
                if ".sync-conflict-" in name and name.endswith(".md"):
                    found.append(
                        os.path.relpath(os.path.join(dirpath, name), root)
                    )
                    if len(found) >= CONFLICT_REPORT_LIMIT:
                        truncated = "report limit reached"
                        break
            if truncated:
                break
    except OSError as exc:
        errors.append(exc)

    if errors:
        first = errors[0]
        target = getattr(first, "filename", None) or str(first)
        print("VAULT CONFLICTS: unknown (unreadable path: %s)" % target)
        return 0
    if truncated and not found:
        print("VAULT CONFLICTS: unknown (%s)" % truncated)
        return 0
    for rel in found:
        print("VAULT CONFLICT: " + rel.replace(os.sep, "/"))
    if truncated:
        print(
            "VAULT CONFLICTS: additional entries not scanned (%s)"
            % truncated
        )
    return 0


# --- entrypoint --------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="vault-note-write.py",
        description="Simple local ordinary-note writer for the K2B vault.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write", help="save one ordinary note locally")
    write.add_argument("--path", required=True)
    write.add_argument("--content-file", required=True)
    write.add_argument("--expected-sha256", required=True)
    write.add_argument("--summary", required=True)
    write.add_argument("--source-ref", required=True)
    write.add_argument("--status", default=None)
    write.add_argument(
        "--section",
        default=None,
        help="heading above the owning table, required when a new note "
        "could belong to several eligible tables",
    )
    conflicts = sub.add_parser(
        "conflicts",
        help="read-only bounded report of Syncthing conflict copies",
    )
    conflicts.set_defaults(func=cmd_conflicts)
    write.set_defaults(func=cmd_write)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
