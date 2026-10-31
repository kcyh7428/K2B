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
import contextlib
import errno
import fcntl
import hashlib
import importlib.util
import json
import io
import os
import pwd
import re
import stat
import subprocess
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
    if (folded_joined == "wiki/reference/minnie-family-memory"
            or folded_joined.startswith("wiki/reference/minnie-family-memory/")):
        raise WriteError("Mini-owned generated memory is not an ordinary note target")
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


def _native_path_identity(path, expected_stat):
    """Return Darwin's lookup identity, or request the names scan.

    The descriptor checks this lookup only. It does not pin a later write;
    the existing writer locks, expected bytes and atomic replacement remain.
    """
    if sys.platform != "darwin":
        return None
    getpath = getattr(fcntl, "F_GETPATH", None)
    if (getpath is None or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_NONBLOCK")):
        return None
    if not (stat.S_ISDIR(expected_stat.st_mode) or stat.S_ISREG(expected_stat.st_mode)):
        return None
    # F_GETPATH is exported by Python and defined by the installed Apple
    # SDK. Its path buffer uses the native limit; fcntl accepts at most 1024.
    try:
        size = os.pathconf("/", "PC_PATH_MAX")
    except (ValueError, NotImplementedError):
        return None
    if not isinstance(size, int) or not 0 < size <= 1024:
        return None
    unsupported = (errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS,
                   errno.EINVAL, errno.ENOTTY)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if stat.S_ISDIR(expected_stat.st_mode):
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ENOENT or exc.errno in unsupported:
            return None
        raise
    try:
        opened = os.fstat(fd)
        if (stat.S_IFMT(opened.st_mode), opened.st_dev, opened.st_ino) != (
                stat.S_IFMT(expected_stat.st_mode), expected_stat.st_dev, expected_stat.st_ino):
            raise WriteError("note path changed during native validation: " + path)
        try:
            raw = fcntl.fcntl(fd, getpath, bytes(size))
        except OSError as exc:
            if exc.errno in unsupported:
                return None
            raise
        if not isinstance(raw, bytes) or len(raw) != size:
            return None
        nul = raw.find(b"\x00")
        if nul <= 0 or raw[nul + 1:].strip(b"\x00"):
            return None
        got = os.fsdecode(raw[:nul])
        if not os.path.isabs(got):
            return None
        if os.path.normpath(got) != got:
            return None
        return got
    finally:
        os.close(fd)

def check_target_chain(vault_real, rel):
    """Reject symlinks and non-regular files along the target chain."""
    parts = rel.split("/")
    cur = vault_real
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        target = os.path.join(cur, part)
        try:
            st = os.lstat(target)
        except FileNotFoundError:
            st = None
        if st is not None:
            if stat.S_ISLNK(st.st_mode):
                raise WriteError("refusing symlink in note path: " + target)
            if last and not stat.S_ISREG(st.st_mode):
                raise WriteError("note target is not a regular file: " + target)
            if not last and not stat.S_ISDIR(st.st_mode):
                raise WriteError("note path crosses a non-directory: " + target)
            resolved = _native_path_identity(target, st)
            if resolved == target:
                cur = target
                continue
        exact = False
        alias = False
        with os.scandir(cur) as entries:
            for entry in entries:
                if entry.name == part:
                    exact = True
                    break
                if entry.name.casefold() == part.casefold():
                    alias = True
        if not exact and alias:
            raise WriteError("note path must use the canonical on-disk case: " + part)
        cur = target
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            return  # missing tail is fine
        if stat.S_ISLNK(st.st_mode):
            raise WriteError("refusing symlink in note path: " + cur)
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
    and its global semantics are unchanged. Child classification uses
    non-following DirEntry predicates; the legacy index-existence ownership
    probe is retained. Individual metadata errors retain false-predicate
    behavior, while directory listing failures propagate."""
    try:
        root_mode = os.lstat(dir_abs).st_mode
    except (OSError, ValueError):
        return 0
    if not stat.S_ISDIR(root_mode):
        return 0
    n = 0
    subdirs = []
    with os.scandir(dir_abs) as entries:
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
            except OSError:
                pass
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                # A nested folder without its own index is owned by this index.
                if entry.name.casefold() != "shipped" and not os.path.exists(os.path.join(entry.path, "index.md")):
                    subdirs.append(entry.path)
                continue
            if not entry.name.endswith(".md") or entry.name == "index.md":
                continue
            if ".sync-conflict-" in entry.name:
                continue
            try:
                if entry.is_file(follow_symlinks=False):
                    n += 1
            except OSError:
                continue
    for path in subdirs:
        n += count_pages_scoped(path)
    return n


def update_master_index_text(text, index_rel, new_count, compile_mod):
    """Validate ownership without counting; update only that row and total.

    Unrelated counts remain their existing index snapshot. No filesystem
    access or missing-row creation is part of ordinary-save bookkeeping.
    """
    parts = index_rel.split("/")
    if (not index_rel.startswith("wiki/") or index_rel == "wiki/index.md"
            or not index_rel.endswith("/index.md") or "\\" in index_rel
            or any(part in ("", ".", "..") for part in parts)):
        raise ShapeError("invalid owning index: " + index_rel)
    owner_href = index_rel[len("wiki/"):]
    if new_count is not None and (type(new_count) is not int or new_count < 0):
        raise ShapeError("new count must be a nonnegative integer")

    lines = text.split("\n")
    headers = [i for i, line in enumerate(lines)
               if line.strip() == compile_mod.MASTER_TABLE_HEADER]
    if len(headers) != 1:
        raise ShapeError("master index must contain exactly one table header")
    start = headers[0] + 2
    if start > len(lines) or not is_separator_row(lines[start - 1], 3):
        raise ShapeError("master index table separator is malformed")
    rows = []
    hrefs = set()
    for i in range(start, len(lines)):
        if not lines[i].strip().startswith("|"):
            break
        match = compile_mod.MASTER_ROW_RE.fullmatch(lines[i])
        if match is None:
            raise ShapeError("malformed master index row")
        href = match.group(2)
        if (href.startswith("/") or "\\" in href
                or not href.endswith("/index.md")
                or any(part in ("", ".", "..") for part in href.split("/"))
                or any(ord(char) < 32 or ord(char) == 127 for char in href)):
            raise ShapeError("unsafe or noncanonical master href: " + href)
        if href in hrefs:
            raise ShapeError("duplicate master row href: " + href)
        hrefs.add(href)
        try:
            count = int(match.group(4))
        except ValueError:
            raise ShapeError("master count cannot be parsed")
        rows.append((i, match, count))
    if not rows or owner_href not in hrefs:
        raise ShapeError("no master row for owning index: " + owner_href)

    total_lines = [line for line in lines
                   if line.lstrip().startswith("**Total wiki pages:")]
    if len(total_lines) != 1 or any(
            compile_mod.MASTER_TOTAL_RE.fullmatch(line) is None
            for line in total_lines):
        raise ShapeError("master total line is missing, malformed or duplicated")
    if new_count is None:
        return text

    total = 0
    for i, match, count in rows:
        if match.group(2) == owner_href:
            count = new_count
            a, z = match.span(4)
            lines[i] = lines[i][:a] + str(count) + lines[i][z:]
        total += count
    out = "\n".join(lines)
    match = compile_mod.MASTER_TOTAL_RE.search(out)
    a, z = match.span(2)
    out = out[:a] + str(total) + out[z:]
    return out


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
    text, note_stem, summary, status, section, folder_dir_abs, compile_mod,
    page_count=None,
):
    """Apply the note row update plus date/count header line.

    Only the owning table's row changes; unrelated cells, rows, tables and
    sections are preserved byte-for-byte. None validates without a count;
    mutation receives the caller's actual affected-scope count.
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

    def header_sub(match):
        return (
            match.group(1)
            + today
            + match.group(3)
            + (match.group(4) if page_count is None else str(page_count))
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
        update_master_index_text(
            _read_index_bytes(master_abs, "master index").decode("utf-8"), index_rel, None, compile_mod
        )
    except UnicodeDecodeError:
        raise ShapeError("master index is not valid UTF-8")
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
        scope_count = count_pages_scoped(os.path.dirname(folder_index_abs))
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
            page_count=scope_count,
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

        fresh_master_bytes = _read_index_bytes(master_abs, "master index")
        backups["master_index_before"] = durable_private_copy(
            "before", fresh_master_bytes
        )
        new_master_text = update_master_index_text(
            fresh_master_bytes.decode("utf-8"), index_rel, scope_count, compile_mod
        )
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


# Private waiting edits use the existing recovery/receipt store. They never
# modify a downloaded mirror, and delivery uses the ordinary writer unchanged.
PENDING_SCAN_LIMIT = 1000
PENDING_RECORD_BYTES = 16384
# Existing partial receipts may quote a bounded index diagnostic. JSON escaping
# can expand each byte sixfold; their bound is separate from waiting metadata.
NORMAL_RECEIPT_BYTES = 6 * MAX_INDEX_BYTES + 2 * PENDING_RECORD_BYTES
PENDING_KEYS = {
    "schema", "operation", "user", "path", "note_sha256", "summary",
    "status", "section", "source_ref", "expected_sha256", "content",
    "created_utc", "result", "writer_receipt", "writer_receipt_sha256",
    "record_sha256", "time_utc",
}
PENDING_ID_FIELDS = (
    "user", "path", "note_sha256", "summary", "status", "section",
    "source_ref", "expected_sha256",
)


def _pending_digest(value):
    return sha256_hex(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _pending_exclude_shared(path):
    forbidden = [
        "/Users/keithmbpm2/Projects/K2B-Vault",
        "/Users/keithmbpm2/Projects/K2B-NAS",
        "/Users/keithmbpm2/Projects/K2B-Vault-Offline",
        "/Users/keithcheung/Projects/K2B-Vault",
        "/Users/keithcheung/Projects/K2B-Vault-Offline",
        "/Volumes",
    ]
    forbidden += [os.environ[k] for k in ("K2B_VAULT_PATH", "K2B_VAULT_ROOT")
                  if os.environ.get(k)]
    for root in forbidden:
        root = os.path.abspath(root).casefold().rstrip("/")
        if path.casefold() == root or path.casefold().startswith(root + "/"):
            raise WriteError("pending storage/content must stay outside Vault and mirror paths")


def _pending_local_path(path, directory=False, create=False):
    if not os.path.isabs(path):
        raise WriteError("pending local paths must be absolute")
    path = os.path.abspath(path)
    _pending_exclude_shared(path)
    # macOS's two system aliases are local OS paths, not user-selected links.
    if sys.platform == "darwin":
        for alias, target in (("/tmp", "/private/tmp"), ("/var", "/private/var")):
            if (path.startswith(alias + "/") and os.path.islink(alias)
                    and os.readlink(alias) in (target, target.lstrip("/"))):
                path = target + path[len(alias):]
    current = os.sep
    for part in path.split(os.sep)[1:]:
        current = os.path.join(current, part)
        if create and not os.path.lexists(current):
            os.mkdir(current, 0o700)
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise WriteError("pending local path must not traverse symlinks")
        if current != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise WriteError("pending local directory is not a directory")
        elif not stat.S_ISREG(info.st_mode):
            raise WriteError("pending content/receipt is not a regular file")
    return path


def _pending_read(path, limit, private=True):
    path = _pending_local_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_size > limit
                or before.st_uid != os.geteuid()
                or private and stat.S_IMODE(before.st_mode) != 0o600):
            raise WriteError("invalid private pending file identity, size or permissions")
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after, named = os.fstat(fd), os.lstat(path)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if len(data) > limit or identity(before) != identity(after) or identity(after) != identity(named):
            raise WriteError("pending file changed while reading")
        return data
    finally:
        os.close(fd)


def _pending_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise WriteError("duplicate pending JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(data, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise WriteError("invalid pending JSON: " + str(exc))
    if not isinstance(value, dict):
        raise WriteError("pending JSON must be an object")
    return value


def _pending_state(create=False):
    root = os.path.abspath(state_root())
    _pending_exclude_shared(root)
    if not os.path.lexists(root) and not create:
        return None
    root = _pending_local_path(root, directory=True, create=create)
    if os.lstat(root).st_uid != os.geteuid():
        raise WriteError("pending state must belong to the current operator")
    return root


@contextlib.contextmanager
def _pending_lock(create=False):
    root = _pending_state(create)
    if root is None:
        yield None
        return
    lock = os.path.join(root, "note-pending.lock.d")
    acquire_lock(lock)
    try:
        yield root
    finally:
        release_lock(lock)


def _pending_record_path(operation):
    if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{64}", operation):
        raise WriteError("pending operation must be its exact 64-character ID")
    return os.path.join(receipts_root(), "pending-" + operation + ".json")


def _pending_seal(record):
    return _pending_digest({k: v for k, v in record.items()
                            if k not in ("record_sha256", "time_utc")})


def _pending_save(record):
    _pending_local_path(receipts_root(), directory=True, create=True)
    record = dict(record)
    record["record_sha256"] = _pending_seal(record)
    sized = dict(record, time_utc=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"))
    if len((json.dumps(sized, indent=2, sort_keys=True) + "\n").encode()) > PENDING_RECORD_BYTES:
        raise WriteError("pending metadata exceeds its retained-record bound")
    write_receipt_file("pending-" + record["operation"], record)


def _pending_load(operation):
    record = _pending_json(_pending_read(_pending_record_path(operation), PENDING_RECORD_BYTES))
    if set(record) != PENDING_KEYS or type(record["schema"]) is not int or record["schema"] != 1:
        raise WriteError("invalid pending record fields or schema")
    if record["operation"] != operation or record["user"] != validate_operator():
        raise WriteError("pending record operator/operation mismatch")
    if record["record_sha256"] != _pending_seal(record):
        raise WriteError("pending record integrity mismatch")
    if not isinstance(record["path"], str) or validate_note_rel(record["path"]) != record["path"]:
        raise WriteError("invalid pending note path")
    for key, maximum, optional in (("summary", MAX_SUMMARY_CHARS, False),
                                   ("source_ref", MAX_SOURCE_REF_CHARS, False),
                                   ("status", MAX_STATUS_CHARS, True),
                                   ("section", MAX_STATUS_CHARS, True)):
        if not isinstance(record[key], str) and not (optional and record[key] is None):
            raise WriteError("invalid pending field type: " + key)
        if validate_line_field(key, record[key], maximum, allow_empty=optional) != record[key]:
            raise WriteError("noncanonical pending field: " + key)
    validate_expected(record["expected_sha256"])
    digest = record["note_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise WriteError("invalid pending content hash")
    if operation != _pending_digest({k: record[k] for k in PENDING_ID_FIELDS}):
        raise WriteError("pending identity mismatch")
    for key, fmt in (("created_utc", "%Y-%m-%dT%H:%M:%SZ"), ("time_utc", "%Y%m%dT%H%M%S.%fZ")):
        if not isinstance(record[key], str):
            raise WriteError("invalid pending UTC time")
        parsed = datetime.strptime(record[key], fmt)
        if parsed.strftime(fmt) != record[key]:
            raise WriteError("noncanonical pending UTC time")
    expected_content = "note-write-backups/after/" + digest + ".md"
    alternate_content = "note-write-backups/before/" + digest + ".md"
    if record["content"] not in (expected_content, alternate_content):
        raise WriteError("invalid pending content path")
    data = _pending_read(os.path.join(state_root(), record["content"]), MAX_NOTE_BYTES)
    if sha256_hex(data) != digest:
        raise WriteError("pending content hash mismatch")
    if not data.strip() or b"\0" in data:
        raise WriteError("invalid pending note content")
    validate_frontmatter(data.decode("utf-8"))
    if record["result"] not in ("waiting-local", "delivered"):
        raise WriteError("invalid pending delivery state")
    if record["result"] == "waiting-local":
        if record["writer_receipt"] is not None or record["writer_receipt_sha256"] is not None:
            raise WriteError("waiting edit must not claim a writer receipt")
    else:
        normal = operation_key(record["path"], data, record["summary"], record["status"], record["source_ref"], record["section"])
        if record["writer_receipt"] != "note-write-receipts/" + normal + ".json":
            raise WriteError("invalid delivered receipt path")
        if not isinstance(record["writer_receipt_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", record["writer_receipt_sha256"]):
            raise WriteError("invalid delivered receipt hash")
    return record, data


def _pending_online():
    # This preflight is specific to pending delivery, not a general mount lease.
    actor = validate_operator()
    if actor == "keithmbpm2":
        mount, source, logical = (
            "/Users/keithmbpm2/Projects/K2B-NAS", "//cheung@192.168.50.67/Keith-Work",
            "/Users/keithmbpm2/Projects/K2B-Vault")
    else:
        mount, source, logical = ("/Volumes/Keith-Work", "//cheung@100.75.5.21/Keith-Work",
                                  "/Users/keithcheung/Projects/K2B-Vault")
    lines = subprocess.check_output(["/sbin/mount"], text=True).splitlines()
    matches = [line for line in lines if line.startswith(source + " on " + mount + " (smbfs,")]
    if len(matches) != 1 or "read-only" in matches[0]:
        raise WriteError("pending delivery requires the approved writable SMB mount")
    root = mount + "/K2B-Vault"
    if (not os.path.islink(logical) or os.path.realpath(logical) != root
            or vault_root() != root or os.path.realpath(compile_lock_dir()) != root + "/.k2b-compile-index.lock.d"):
        raise WriteError("pending delivery NAS alias/root/lock binding mismatch")
    helper = load_compile_index()
    if str(helper.LOCK_DIR) != root + "/.k2b-compile-index.lock.d":
        raise WriteError("pending delivery imported lock binding mismatch")


def _pending_normal_receipt(record, data):
    operation = operation_key(record["path"], data, record["summary"], record["status"], record["source_ref"], record["section"])
    path = os.path.join(receipts_root(), operation + ".json")
    if not os.path.lexists(path):
        return None
    raw = _pending_read(path, NORMAL_RECEIPT_BYTES)
    normal = _pending_json(raw)
    for key in PENDING_ID_FIELDS:
        if normal.get(key) != record[key]:
            raise WriteError("pending normal-writer receipt mismatch: " + key)
    if normal.get("operation") != operation:
        raise WriteError("pending normal-writer operation mismatch")
    if normal.get("result") not in ("saved-local", "already-present"):
        return None
    return "note-write-receipts/" + operation + ".json", sha256_hex(raw)


def _stage_pending(args):
    actor = validate_operator()
    record = {"schema": 1, "user": actor, "path": validate_note_rel(args.path),
              "summary": validate_line_field("summary", args.summary, MAX_SUMMARY_CHARS),
              "status": validate_line_field("status", args.status, MAX_STATUS_CHARS, allow_empty=True),
              "section": validate_line_field("section", args.section, MAX_STATUS_CHARS, allow_empty=True),
              "source_ref": validate_line_field("source_ref", args.source_ref, MAX_SOURCE_REF_CHARS),
              "expected_sha256": validate_expected(args.expected_sha256)}
    # Only local draft/state paths are examined; no Vault existence check occurs.
    with _pending_lock(create=True):
        data = _pending_read(args.content_file, MAX_NOTE_BYTES, private=False)
        if not data.strip() or b"\0" in data:
            raise WriteError("invalid empty or NUL-containing pending note")
        validate_frontmatter(data.decode("utf-8"))
        record["note_sha256"] = sha256_hex(data)
        operation = _pending_digest({k: record[k] for k in PENDING_ID_FIELDS})
        if os.path.lexists(_pending_record_path(operation)):
            previous, _ = _pending_load(operation)
            print(json.dumps({"result": previous["result"], "operation": operation, "path": record["path"]}))
            return 0
        _pending_local_path(os.path.join(backups_root(), "before"), directory=True, create=True)
        _pending_local_path(os.path.join(backups_root(), "after"), directory=True, create=True)
        content = durable_private_copy("after", data)
        if _pending_read(os.path.join(state_root(), content), MAX_NOTE_BYTES) != data:
            raise WriteError("pending recovery copy mismatch")
        record.update(operation=operation, content=content, created_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      result="waiting-local", writer_receipt=None, writer_receipt_sha256=None)
        _pending_save(record)
        print(json.dumps({"result": "waiting-local", "operation": operation, "path": record["path"],
                          "note": "kept privately on this Mac; not saved to NAS"}))
    return 0


def _list_pending(args):
    validate_operator()
    if not 1 <= args.limit <= 100:
        raise WriteError("pending limit must be between 1 and 100")
    rows = []
    with _pending_lock() as root:
        directory = os.path.join(root, "note-write-receipts") if root else None
        if directory and os.path.lexists(directory):
            _pending_local_path(directory, directory=True)
            names = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.name.startswith("pending-"):
                        names.append(entry.name)
                        if len(names) > PENDING_SCAN_LIMIT:
                            raise WriteError("pending inspection bound exceeded; preserve state")
            for name in names:
                match = re.fullmatch(r"pending-([a-f0-9]{64})\.json", name)
                if not match:
                    raise WriteError("invalid pending receipt filename")
                record, _ = _pending_load(match.group(1))
                if record["result"] == "waiting-local":
                    rows.append({k: record[k] for k in ("operation", "path", "created_utc", "summary")})
    rows.sort(key=lambda row: (row["created_utc"], row["operation"]))
    print(json.dumps({"result": "pending", "edits": rows[:args.limit], "more": len(rows) > args.limit}))
    return 0


def _resume_pending(args):
    validate_operator()
    with _pending_lock() as root:
        if root is None:
            raise WriteError("pending operation not found")
        record, data = _pending_load(args.operation)
        _pending_online()
        completed = _pending_normal_receipt(record, data)
        if record["result"] == "delivered":
            if completed != (record["writer_receipt"], record["writer_receipt_sha256"]):
                raise WriteError("delivered pending receipt binding changed")
        else:
            if completed is None:
                command = argparse.Namespace(**{k: record[k] for k in ("path", "summary", "status", "section", "source_ref", "expected_sha256")})
                command.content_file = os.path.join(state_root(), record["content"])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = cmd_write(command)
                if code:
                    print(json.dumps({"result": "pending-repair" if code == 3 else "waiting-local",
                                      "operation": record["operation"], "writer_output": output.getvalue()}))
                    return code
                completed = _pending_normal_receipt(record, data)
                if completed is None:
                    raise WriteError("normal writer completion is unverified; pending edit retained")
            record.update(result="delivered", writer_receipt=completed[0], writer_receipt_sha256=completed[1])
            _pending_save(record)
        print(json.dumps({"result": "delivered", "operation": record["operation"], "path": record["path"],
                          "receipt": record["writer_receipt"], "receipt_sha256": record["writer_receipt_sha256"],
                          "note": "saved through the NAS writer; counterpart readback separate"}))
    return 0


def cmd_pending_operation(args):
    try:
        return {"stage": _stage_pending, "pending": _list_pending, "resume": _resume_pending}[args.command](args)
    except (ConflictError, TimeoutError, BackupError, WriteError, OSError,
            ValueError, TypeError, UnicodeError, KeyError) as exc:
        code = (2 if isinstance(exc, ConflictError) else
                4 if isinstance(exc, TimeoutError) else
                5 if isinstance(exc, BackupError) else 1)
        sys.stderr.write("vault-note-write: " + str(exc) + "\n")
        return code


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
    stage = sub.add_parser("stage", help="keep one ordinary edit privately, waiting for NAS")
    for name in ("path", "content-file", "expected-sha256", "summary", "source-ref"):
        stage.add_argument("--" + name, required=True)
    stage.add_argument("--status", default=None)
    stage.add_argument("--section", default=None)
    stage.set_defaults(func=cmd_pending_operation)
    pending = sub.add_parser("pending", help="list bounded waiting-edit metadata")
    pending.add_argument("--limit", type=int, default=20)
    pending.set_defaults(func=cmd_pending_operation)
    resume = sub.add_parser("resume", help="deliver one waiting edit through the NAS writer")
    resume.add_argument("--operation", required=True)
    resume.set_defaults(func=cmd_pending_operation)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
