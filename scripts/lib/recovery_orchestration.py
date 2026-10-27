"""Bounded observation and planning helpers for K2B recovery.

This module is a point-in-time witness and planner. It does not freeze writers,
perform backups, mutate Syncthing, or execute archive transport.
"""

from __future__ import annotations

import fcntl
import ast
import hashlib
import http.client
import io
import ipaddress
import json
import os
import pwd
import re
import selectors
import shlex
import stat
import subprocess
import ssl
import sys
import tempfile
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RULE_VERSION = "ancestors-depth-utf8-v1"
LOCK_SCOPE = "configured_witnesses_role_v1"

REQUIRED_LOCKS = ("note_write", "compile_index", "integration", "wiki_log", "publication")
LOCK_VALUES = ("clear", "busy", "unknown", "not_applicable")

PHYSICAL_ACTORS = {
    "home": ("keithmbpm2", "/Users/keithmbpm2"),
    "sjm": ("keithcheung", "/Users/keithcheung"),
}
APPLICABLE_WITNESSES = {
    "home": frozenset(REQUIRED_LOCKS),
    "sjm": frozenset(("note_write", "compile_index", "wiki_log")),
}

MAX_XML_BYTES = 1024 * 1024
MAX_HTTP_BYTES = 64 * 1024
HTTP_TIMEOUT = 0.75
PROBE_SJM_TIMEOUT = 5.0
PROBE_SJM_MAX_BYTES = 128 * 1024

DEFAULT_VAULT = Path("Projects") / "K2B-Vault"
CONFIG_REL = Path("Library") / "Application Support" / "Syncthing" / "config.xml"
ACTORS = ("home", "sjm")

RULE_STRING = (
    "entries-kind-order:directories-by-component-count-then-utf8;"
    "files-by-utf8;entry-encoding:utf8-bytes-plus-NUL;"
    "no-root-dot-entry;directories-include-all-ancestors-below-root"
)

_TARGET_RE = re.compile(r"[A-Za-z0-9._-]{1,64}@(?:[0-9]{1,3}\.){3}[0-9]{1,3}\Z")
_SAFE_USERNAME_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}\Z")
_RUN_ID_RE = re.compile(r"manual-[A-Za-z0-9-]{1,73}\Z")
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
_OCTAL_MODE_RE = re.compile(r"[0-7]{1,4}\Z")

STAT_FORMAT = "%f|%u|%g|%a|%d|%i|%h|%s"

REMOTE_TOOLS_SNIPPET = (
    "import os,stat,sys\n"
    "paths=['/bin/hostname','/usr/bin/sha256sum','/usr/bin/stat','/usr/bin/python3','/usr/syno/bin/synoacltool']\n"
    "status=[]\n"
    "for p in paths:\n"
    "    try:\n"
    "        st=os.stat(p)\n"
    "    except FileNotFoundError:\n"
    "        status.append('missing_tool:'+p)\n"
    "        continue\n"
    "    except OSError:\n"
    "        status.append('permission_unavailable:'+p)\n"
    "        continue\n"
    "    if stat.S_ISREG(st.st_mode) and os.access(p,os.X_OK):\n"
    "        status.append('present:'+p)\n"
    "    else:\n"
    "        status.append('permission_unavailable:'+p)\n"
    "sys.stdout.write('\\n'.join(status)+'\\n')\n"
)

REMOTE_TOOLS_REQUIRED = (
    "/bin/hostname",
    "/usr/bin/sha256sum",
    "/usr/bin/stat",
    "/usr/bin/python3",
    "/usr/syno/bin/synoacltool",
)

_TMP_ALIASES = (Path("/tmp"), Path("/private/tmp"))


class OrchestrationError(Exception):
    """Sanitized orchestration failure carrying only a static reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fail(reason: str) -> "OrchestrationError":
    return OrchestrationError(reason)


def _home(home: Path | None) -> Path:
    if home is not None:
        return Path(home)
    return Path(os.path.expanduser("~"))


def _verify_physical_actor(actor: str) -> Path:
    try:
        entry = pwd.getpwuid(os.geteuid())
    except (KeyError, OSError):
        raise _fail("physical_actor_unavailable")
    expected = PHYSICAL_ACTORS[actor]
    if entry.pw_name != expected[0] or entry.pw_dir != expected[1]:
        raise _fail("physical_actor_mismatch")
    return Path(entry.pw_dir)


def _reject_symlink_ancestors(path: Path, *, allow_missing_leaf: bool = True) -> None:
    """Raise when any ancestor is a symlink or otherwise not a directory."""
    path = Path(path)
    parts: list[str] = []
    current = path
    while True:
        parts.append(current.name)
        parent = current.parent
        if parent == current:
            break
        current = parent
    parts.reverse()
    walk = Path(os.sep)
    for name in parts:
        if not name:
            continue
        walk = walk / name
        try:
            st = os.lstat(walk)
        except FileNotFoundError:
            if walk == path and not allow_missing_leaf:
                raise _fail("path_unavailable")
            return
        except OSError:
            raise _fail("path_unavailable")
        if stat.S_ISLNK(st.st_mode):
            if walk == path and allow_missing_leaf:
                return
            raise _fail("path_symlink_ancestor")
        if not stat.S_ISDIR(st.st_mode) and walk != path:
            raise _fail("path_not_directory")


def _tmp_real_parent() -> Path:
    """Resolve /tmp or /private/tmp to the real OS temporary parent without
    following an untrusted final-component symlink."""
    real = Path(os.path.realpath("/tmp"))
    return real


def _resolve_tmp_alias(value: str) -> str:
    p = Path(value)
    parent = p.parent
    for alias in _TMP_ALIASES:
        if parent == alias or parent == alias.resolve():
            real_parent = _tmp_real_parent()
            return str(real_parent / p.name)
    return str(p)


def _read_env_default(name: str, fallback: str) -> str:
    return os.environ.get(name) or fallback


def _publication_lock_path(vault: Path) -> Path:
    digest = hashlib.sha256(str(Path(vault).resolve()).encode()).hexdigest()
    return Path("/tmp").resolve() / ("k2b-memory-publication-" + digest + ".lock")


def default_lock_specs(home: Path, *, vault: Path, state_root: Path | None = None) -> list[dict[str, Any]]:
    """Derive the standard per-host lock witnesses from the publisher excerpt."""
    state = Path(state_root) if state_root is not None else Path(home) / ".local" / "state" / "k2b"
    note = _resolve_tmp_alias(_read_env_default("K2B_NOTE_WRITE_LOCK", "/tmp/k2b-note-write.lock.d"))
    compile_index = _resolve_tmp_alias(_read_env_default("K2B_COMPILE_INDEX_LOCK", "/tmp/k2b-compile-index.lock.d"))
    wiki = _resolve_tmp_alias(_read_env_default("K2B_WIKI_LOG_LOCK", "/tmp/k2b-wiki-log.lock"))
    integration = state / "digestion" / ".integration.lock"
    publication = _publication_lock_path(vault)
    return [
        {"name": "note_write", "kind": "mkdir", "path": str(note)},
        {"name": "compile_index", "kind": "mkdir", "path": str(compile_index)},
        {"name": "integration", "kind": "flock", "path": str(integration)},
        {"name": "wiki_log", "kind": "flock", "path": str(wiki), "fallback_kind": "mkdir", "fallback_path": str(wiki) + ".d"},
        {"name": "publication", "kind": "flock", "path": str(publication)},
    ]


def _normalize_lock_specs(specs: list[dict[str, Any]] | None, home: Path, vault: Path) -> list[dict[str, Any]]:
    if specs is None:
        specs = default_lock_specs(home, vault=vault)
    if not isinstance(specs, list):
        raise _fail("lock_specs_invalid")
    seen: dict[str, dict[str, Any]] = {}
    for spec in specs:
        if not isinstance(spec, dict):
            raise _fail("lock_spec_invalid")
        name = spec.get("name")
        kind = spec.get("kind")
        path = spec.get("path")
        if not isinstance(name, str) or name not in REQUIRED_LOCKS:
            raise _fail("lock_spec_name_invalid")
        if name in seen:
            raise _fail("lock_spec_duplicate")
        if kind not in ("mkdir", "flock"):
            raise _fail("lock_spec_kind_invalid")
        if (not isinstance(path, str) or not path or not Path(path).is_absolute()
                or ".." in Path(path).parts or any(ord(ch) < 32 for ch in path)):
            raise _fail("lock_spec_path_invalid")
        entry: dict[str, Any] = {"name": name, "kind": kind, "path": path}
        fk = spec.get("fallback_kind")
        if fk is not None:
            if fk != "mkdir":
                raise _fail("lock_spec_fallback_invalid")
            fp = spec.get("fallback_path")
            if (not isinstance(fp, str) or not fp or not Path(fp).is_absolute()
                    or ".." in Path(fp).parts or any(ord(ch) < 32 for ch in fp)):
                raise _fail("lock_spec_fallback_invalid")
            entry["fallback_kind"] = "mkdir"
            entry["fallback_path"] = fp
        seen[name] = entry
    missing = [n for n in REQUIRED_LOCKS if n not in seen]
    if missing:
        raise _fail("lock_specs_incomplete")
    return [seen[n] for n in REQUIRED_LOCKS]


def _probe_mkdir(path: Path) -> str:
    try:
        _reject_symlink_ancestors(path, allow_missing_leaf=True)
    except OrchestrationError:
        return "unknown"
    if not path.parent.is_dir():
        return "unknown"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "clear"
    except OSError:
        return "unknown"
    if stat.S_ISLNK(st.st_mode):
        return "unknown"
    if stat.S_ISDIR(st.st_mode):
        return "busy"
    return "unknown"


def _probe_flock(path: Path) -> str:
    try:
        _reject_symlink_ancestors(path, allow_missing_leaf=True)
    except OrchestrationError:
        return "unknown"
    if not path.parent.is_dir():
        return "unknown"
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "clear"
    except OSError:
        return "unknown"
    if stat.S_ISLNK(st.st_mode):
        return "unknown"
    if not stat.S_ISREG(st.st_mode):
        return "unknown"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return "unknown"
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "busy"
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            return "unknown"
        return "clear"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _probe_lock(spec: dict[str, Any]) -> str:
    kind = spec["kind"]
    path = Path(spec["path"])
    if kind == "mkdir":
        return _probe_mkdir(path)
    primary = _probe_flock(path)
    if spec.get("fallback_kind") == "mkdir":
        fallback = _probe_mkdir(Path(spec["fallback_path"]))
        if fallback == "busy":
            return "busy"
        if fallback == "unknown":
            return "unknown"
    return primary


def _probe_locks(specs: list[dict[str, Any]], actor: str = "home") -> dict[str, str]:
    applicable = APPLICABLE_WITNESSES[actor]
    result: dict[str, str] = {}
    for spec in specs:
        name = spec["name"]
        try:
            raw = _probe_lock(spec)
        except OrchestrationError:
            raw = "unknown"
        if name in applicable:
            result[name] = raw
            continue
        if raw == "busy":
            result[name] = "busy"
        elif raw == "clear":
            result[name] = "not_applicable"
        else:
            result[name] = _home_only_unknown_to_not_applicable(spec)
    return result


def _home_only_unknown_to_not_applicable(spec: dict[str, Any]) -> str:
    path = Path(spec["path"])
    try:
        _reject_symlink_ancestors(path, allow_missing_leaf=True)
    except OrchestrationError:
        return "unknown"
    try:
        os.lstat(path.parent)
    except FileNotFoundError:
        return "not_applicable"
    except OSError:
        return "unknown"
    return "unknown"


def _reject_dtd(data: bytes) -> str:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _fail("config_xml_unsafe")
    upper = text.upper()
    if "\x00" in text or "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise _fail("config_xml_unsafe")
    declaration = re.match(r"\s*<\?xml\b(.*?)\?>", text, re.DOTALL)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]",
                             declaration.group(1), re.IGNORECASE)
        if encoding and encoding.group(1).lower() not in ("utf-8", "utf8"):
            raise _fail("config_xml_unsafe")
    return text


def _read_config(path: Path) -> bytes:
    try:
        _reject_symlink_ancestors(path, allow_missing_leaf=False)
    except OrchestrationError:
        raise _fail("config_path_unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise _fail("config_unavailable")
    try:
        pre = os.fstat(fd)
        if not stat.S_ISREG(pre.st_mode):
            raise _fail("config_unavailable")
        if pre.st_size > MAX_XML_BYTES:
            raise _fail("config_xml_too_large")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_XML_BYTES:
            chunk = os.read(fd, min(65536, MAX_XML_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > MAX_XML_BYTES:
            raise _fail("config_xml_too_large")
        post = os.fstat(fd)
        named = os.lstat(path)
        signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if signature(pre) != signature(post) or signature(post) != signature(named):
            raise _fail("config_unavailable")
        return b"".join(chunks)
    except OSError:
        raise _fail("config_unavailable")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _select_folder(root: ET.Element, vault: Path) -> ET.Element | None:
    target = vault.resolve()
    for folder in root.findall("folder"):
        raw = folder.get("path", "")
        if not raw:
            continue
        try:
            resolved = Path(raw).expanduser().resolve()
        except OSError:
            continue
        if resolved == target:
            return folder
    return None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _fail("json_duplicate_key")
        result[key] = value
    return result


def _loads_strict(raw: bytes, reason: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _fail(reason)
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except OrchestrationError:
        raise _fail(reason)
    except ValueError:
        raise _fail(reason)


def _fetch_status(scheme: str, address: str, folder_id: str, key: str) -> dict[str, Any]:
    host, _, port = address.partition(":")
    if (host != "127.0.0.1" or not port.isascii() or not port.isdigit()
            or len(port) > 5 or not 0 < int(port) <= 65535):
        raise _fail("gui_endpoint_refused")
    ipaddress.ip_address(host)
    query = urllib.parse.urlencode({"folder": folder_id})
    path = "/rest/db/status?" + query
    if scheme == "https":
        conn = http.client.HTTPSConnection(host, int(port), timeout=HTTP_TIMEOUT, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(host, int(port), timeout=HTTP_TIMEOUT)
    try:
        conn.request("GET", path, headers={"X-API-Key": key})
        resp = conn.getresponse()
        if resp.status != 200:
            raise _fail("gui_status_failed")
        body = resp.read(MAX_HTTP_BYTES + 1)
    except OrchestrationError:
        raise
    except (OSError, http.client.HTTPException):
        raise _fail("gui_unreachable")
    finally:
        try:
            conn.close()
        except OSError:
            pass
    if len(body) > MAX_HTTP_BYTES:
        raise _fail("gui_response_too_large")
    data = _loads_strict(body, "gui_response_malformed")
    if not isinstance(data, dict):
        raise _fail("gui_response_malformed")
    return data


def _integer_zero(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail("gui_counter_invalid")
    if value < 0:
        raise _fail("gui_counter_invalid")
    return value


def _project_status(status: dict[str, Any]) -> dict[str, Any]:
    state = status.get("state")
    if not isinstance(state, str):
        raise _fail("gui_state_missing")
    if "error" not in status:
        raise _fail("gui_error_missing")
    raw_error = status["error"]
    if not (raw_error is None or isinstance(raw_error, str)):
        raise _fail("gui_error_malformed")
    counters = {}
    for key in ("needFiles", "needBytes", "needDirectories", "errors", "pullErrors"):
        if key not in status:
            raise _fail("gui_counter_missing")
        counters[key] = _integer_zero(status[key])
    return {
        "state": state,
        "error": raw_error,
        "needFiles": counters["needFiles"],
        "needBytes": counters["needBytes"],
        "needDirectories": counters["needDirectories"],
        "errors": counters["errors"],
        "pullErrors": counters["pullErrors"],
    }


def local_status(
    actor: str,
    *,
    home: Path | None = None,
    config_path: Path | None = None,
    vault: Path | None = None,
    lock_specs: list[dict[str, Any]] | None = None,
    now_ns: int | None = None,
) -> dict[str, Any]:
    if actor not in ACTORS:
        raise _fail("actor_invalid")
    home_path = _verify_physical_actor(actor) if home is None else _home(home)
    vault_path = Path(vault) if vault is not None else home_path / DEFAULT_VAULT
    cfg = Path(config_path) if config_path is not None else home_path / CONFIG_REL
    if now_ns is not None and (isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0):
        raise _fail("now_invalid")
    specs = _normalize_lock_specs(lock_specs, home_path, vault_path)
    data = _read_config(cfg)
    text = _reject_dtd(data)
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        raise _fail("config_xml_malformed")
    folder = _select_folder(root, vault_path)
    gui = root.find("gui")
    if folder is None or gui is None or not folder.get("id"):
        raise _fail("syncthing_vault_unavailable")
    address = gui.findtext("address", "") or ""
    if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
        raise _fail("gui_endpoint_refused")
    key = gui.findtext("apikey", "") or ""
    if not key:
        raise _fail("gui_key_unavailable")
    scheme = "https" if gui.get("tls") == "true" else "http"
    if now_ns is None:
        now_ns = time.time_ns()
    status = _fetch_status(scheme, address, folder.get("id"), key)
    projected = _project_status(status)
    locks = _probe_locks(specs, actor)
    return {
        "schema_version": SCHEMA_VERSION,
        "actor": actor,
        "observed_at_ns": now_ns,
        "state": projected["state"],
        "need_files": projected["needFiles"],
        "need_bytes": projected["needBytes"],
        "need_directories": projected["needDirectories"],
        "errors": projected["errors"],
        "pull_errors": projected["pullErrors"],
        "error_present": not (projected["error"] is None or projected["error"] == ""),
        "locks": locks,
        "lock_scope": LOCK_SCOPE,
        "witness_specs": specs,
    }


_ALLOWED_OBS_KEYS = {
    "schema_version", "actor", "observed_at_ns", "state",
    "need_files", "need_bytes", "need_directories", "errors", "pull_errors",
    "error_present", "locks", "lock_scope", "witness_specs",
}

_COUNTER_KEYS = ("need_files", "need_bytes", "need_directories", "errors", "pull_errors")


def validate_observation(
    obs: dict[str, Any],
    *,
    actor: str,
    now_ns: int,
    max_age_ns: int = 2_000_000_000,
    expected_witness_specs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if actor not in ACTORS:
        raise _fail("actor_invalid")
    if isinstance(now_ns, bool) or not isinstance(now_ns, int) or now_ns <= 0:
        raise _fail("now_invalid")
    if isinstance(max_age_ns, bool) or not isinstance(max_age_ns, int) or max_age_ns < 0:
        raise _fail("max_age_invalid")
    if not isinstance(obs, dict):
        raise _fail("observation_invalid")
    if set(obs.keys()) != _ALLOWED_OBS_KEYS:
        raise _fail("observation_fields_invalid")
    if obs.get("schema_version") != SCHEMA_VERSION or isinstance(obs.get("schema_version"), bool):
        raise _fail("observation_schema_invalid")
    if obs.get("actor") != actor:
        raise _fail("observation_actor_invalid")
    ts = obs.get("observed_at_ns")
    if isinstance(ts, bool) or not isinstance(ts, int) or ts <= 0:
        raise _fail("observation_timestamp_invalid")
    age = now_ns - ts
    if age > max_age_ns:
        raise _fail("observation_stale")
    if age < -500_000_000:
        raise _fail("observation_future")
    if obs.get("state") != "idle":
        raise _fail("observation_state_not_idle")
    if obs.get("error_present") is not False:
        raise _fail("observation_error_present")
    for key in _COUNTER_KEYS:
        value = obs.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value != 0:
            raise _fail("observation_counter_invalid")
    locks = obs.get("locks")
    supplied_specs = obs.get("witness_specs")
    if not isinstance(supplied_specs, list):
        raise _fail("observation_witness_specs_invalid")
    normalized = _normalize_lock_specs(supplied_specs, Path("/"), Path("/"))
    if normalized != supplied_specs:
        raise _fail("observation_witness_specs_invalid")
    if expected_witness_specs is not None:
        expected = _normalize_lock_specs(expected_witness_specs, Path("/"), Path("/"))
        if normalized != expected:
            raise _fail("observation_witness_binding_mismatch")
    if not isinstance(locks, dict):
        raise _fail("observation_locks_invalid")
    if set(locks.keys()) != set(REQUIRED_LOCKS):
        raise _fail("observation_locks_incomplete")
    applicable = APPLICABLE_WITNESSES[actor]
    for name in REQUIRED_LOCKS:
        if name in applicable:
            if locks.get(name) != "clear":
                raise _fail("observation_lock_not_clear")
        else:
            if locks.get(name) != "not_applicable":
                raise _fail("observation_lock_not_applicable")
    if obs.get("lock_scope") != LOCK_SCOPE:
        raise _fail("observation_lock_scope_invalid")
    return dict(obs)


_SSH_ALIAS = "sjm-ai"
_REMOTE_INTERPRETER_ARGV = ["python3", "-"]


def _module_source() -> str:
    try:
        return Path(__file__).read_text(encoding="utf-8")
    except OSError:
        raise _fail("module_source_unavailable")


def build_probe_sjm_argv() -> list[str]:
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=1",
        _SSH_ALIAS,
        shlex.join(_REMOTE_INTERPRETER_ARGV),
    ]


def build_probe_sjm_stdin() -> bytes:
    source = _module_source()
    definitions = set("OrchestrationError _fail _home _verify_physical_actor _reject_symlink_ancestors "
        "_tmp_real_parent _resolve_tmp_alias _read_env_default _publication_lock_path "
        "default_lock_specs _normalize_lock_specs _probe_mkdir _probe_flock _probe_lock "
        "_probe_locks _home_only_unknown_to_not_applicable _reject_dtd _read_config _select_folder "
        "_reject_duplicate_keys _loads_strict _fetch_status _integer_zero _project_status local_status".split())
    constants = set("SCHEMA_VERSION LOCK_SCOPE REQUIRED_LOCKS LOCK_VALUES PHYSICAL_ACTORS "
        "APPLICABLE_WITNESSES MAX_XML_BYTES MAX_HTTP_BYTES HTTP_TIMEOUT DEFAULT_VAULT CONFIG_REL "
        "ACTORS _TMP_ALIASES".split())
    selected = ast.parse("from __future__ import annotations\n"
        "import fcntl, hashlib, http.client, ipaddress, json, os, pwd, re, stat, ssl, time, urllib.parse\n"
        "import xml.etree.ElementTree as ET\n"
        "from pathlib import Path\nfrom typing import Any\n").body
    found = set()
    try:
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in definitions:
                selected.append(node)
                found.add(node.name)
            elif isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in constants:
                    selected.append(node)
                    found.add(target.id)
        if found != definitions | constants:
            raise _fail("observation_source_definition_missing")
        source = ast.unparse(ast.Module(body=selected, type_ignores=[])) + "\n"
        compile(source, "<recovery-observation>", "exec")
    except (SyntaxError, ValueError):
        raise _fail("observation_source_invalid")
    call = "\nprint(json.dumps(local_status('sjm')))\n"
    return (source + call).encode("utf-8")


def _run_probe(argv: list[str], payload: bytes, timeout: float) -> subprocess.CompletedProcess:
    """Run argv with a bounded streaming capture on stdout and stderr."""
    if not isinstance(payload, (bytes, bytearray)):
        raise _fail("probe_payload_invalid")
    if len(payload) > PROBE_SJM_MAX_BYTES:
        raise _fail("probe_payload_too_large")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise _fail("timeout_invalid")
    cap = PROBE_SJM_MAX_BYTES + 1
    deadline = time.monotonic() + float(timeout)
    stdin_file = tempfile.TemporaryFile()
    proc = None
    selector = selectors.DefaultSelector()
    out_buf = bytearray()
    err_buf = bytearray()
    try:
        stdin_file.write(payload)
        stdin_file.flush()
        stdin_file.seek(0)
        try:
            proc = subprocess.Popen(
                argv,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
            )
        except OSError:
            raise _fail("probe_unreachable")
        assert proc.stdout is not None and proc.stderr is not None
        os.set_blocking(proc.stdout.fileno(), False)
        os.set_blocking(proc.stderr.fileno(), False)
        selector.register(proc.stdout, selectors.EVENT_READ, "out")
        selector.register(proc.stderr, selectors.EVENT_READ, "err")
        open_streams = 2
        while open_streams > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _fail("probe_timeout")
            events = selector.select(timeout=min(remaining, 0.25))
            if not events:
                if proc.poll() is not None and not selector.get_map():
                    break
                continue
            for key, _mask in events:
                stream = key.fileobj
                tag = key.data
                try:
                    buf = out_buf if tag == "out" else err_buf
                    chunk = os.read(stream.fileno(), min(65536, cap - len(buf)))
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                buf = out_buf if tag == "out" else err_buf
                if chunk:
                    buf.extend(chunk)
                    if len(buf) > PROBE_SJM_MAX_BYTES:
                        raise _fail("probe_output_too_large")
                else:
                    selector.unregister(stream)
                    open_streams -= 1
        try:
            returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise _fail("probe_timeout")
        return subprocess.CompletedProcess(argv, returncode, bytes(out_buf), bytes(err_buf))
    except OrchestrationError:
        raise
    finally:
        try:
            selector.close()
        except OSError:
            pass
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        try:
            stdin_file.close()
        except OSError:
            pass


def probe_sjm(*, timeout: float = PROBE_SJM_TIMEOUT,
              expected_payload_sha256: str | None = None,
              expected_witness_specs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise _fail("timeout_invalid")
    argv = build_probe_sjm_argv()
    payload = build_probe_sjm_stdin()
    digest = hashlib.sha256(payload).hexdigest()
    if expected_payload_sha256 is not None and digest != expected_payload_sha256:
        raise _fail("probe_payload_digest_mismatch")
    if len(payload) > PROBE_SJM_MAX_BYTES:
        raise _fail("probe_payload_too_large")
    proc = _run_probe(argv, payload, float(timeout))
    if len(proc.stdout) > PROBE_SJM_MAX_BYTES:
        raise _fail("probe_output_too_large")
    if proc.returncode != 0:
        raise _fail("probe_failed")
    received_ns = time.time_ns()
    wrapped = _loads_strict(proc.stdout, "probe_output_malformed")
    if not isinstance(wrapped, dict):
        raise _fail("probe_output_malformed")
    observation = validate_observation(wrapped, actor="sjm", now_ns=received_ns,
                                       expected_witness_specs=expected_witness_specs)
    return {"observation": observation, "received_at_ns": received_ns,
            "payload_sha256": digest}


def _load_manifest_module():
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import importlib
    return importlib.import_module("k2b_recovery")


def _utf8_key(text: str) -> bytes:
    return text.encode("utf-8")


def _dir_sort_key(path: str) -> tuple[int, bytes]:
    return (len([p for p in path.split("/") if p]), _utf8_key(path))


def _ancestors(rel_file: str) -> list[str]:
    parts = rel_file.split("/")[:-1]
    out: list[str] = []
    acc: list[str] = []
    for part in parts:
        acc.append(part)
        out.append("/".join(acc))
    return out


def _check_segment(segment: str) -> None:
    if segment in ("", ".", "..") or segment.startswith("-"):
        raise _fail("archive_path_unsafe")
    for ch in segment:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise _fail("archive_path_control")


def _validate_rel_path(rel: Any) -> str:
    if not isinstance(rel, str) or not rel:
        raise _fail("plan_invalid")
    if rel.startswith("/"):
        raise _fail("archive_path_unsafe")
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        raise _fail("archive_path_unsafe")
    if unicodedata.normalize("NFC", rel) != rel:
        raise _fail("archive_path_unsafe")
    for segment in rel.split("/"):
        _check_segment(segment)
    return rel


def _capture_directories(root: Path, dirs: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for rel in dirs:
        _validate_rel_path(rel)
        current = root
        for segment in rel.split("/"):
            current = current / segment
            try:
                st = os.lstat(current)
            except OSError:
                raise _fail("archive_directory_missing")
            if stat.S_ISLNK(st.st_mode):
                raise _fail("archive_symlink_rejected")
            if not stat.S_ISDIR(st.st_mode):
                raise _fail("archive_directory_invalid")
        st = os.lstat(current)
        result.append({"path": rel, "mode": stat.S_IMODE(st.st_mode), "mtime_ns": st.st_mtime_ns})
    return result


def _validate_file_entry(root: Path, rel: str) -> None:
    _validate_rel_path(rel)
    current = root
    st = None
    for segment in rel.split("/"):
        current = current / segment
        try:
            st = os.lstat(current)
        except OSError:
            raise _fail("archive_file_missing")
        if stat.S_ISLNK(st.st_mode):
            raise _fail("archive_symlink_rejected")
    if st is None or not stat.S_ISREG(st.st_mode):
        raise _fail("archive_file_invalid")
    if st.st_nlink != 1:
        raise _fail("archive_hardlink_rejected")


def _entries_bytes(directories: list[str], files: list[str]) -> bytes:
    out = io.BytesIO()
    for rel in directories:
        out.write(rel.encode("utf-8"))
        out.write(b"\x00")
    for rel in files:
        out.write(rel.encode("utf-8"))
        out.write(b"\x00")
    return out.getvalue()


def _rule_digest() -> str:
    return hashlib.sha256(RULE_STRING.encode("utf-8")).hexdigest()


def plan_digest(plan: dict[str, Any]) -> str:
    """Bind all plan fields except the digest itself using canonical JSON."""
    try:
        bound = {key: value for key, value in plan.items() if key != "plan_sha256"}
        data = json.dumps(bound, sort_keys=True, ensure_ascii=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError):
        raise _fail("plan_invalid")
    return hashlib.sha256(data).hexdigest()


def archive_plan(root: Path, approved_paths: list[str]) -> dict[str, Any]:
    root = Path(root)
    if not root.is_absolute():
        raise _fail("archive_root_not_absolute")
    if not isinstance(approved_paths, list) or not all(isinstance(p, str) for p in approved_paths):
        raise _fail("archive_approved_invalid")
    if not 1 <= len(approved_paths) <= 2000:
        raise _fail("archive_selection_count_invalid")
    if len(set(approved_paths)) != len(approved_paths):
        raise _fail("archive_duplicate_file")
    _reject_symlink_ancestors(root, allow_missing_leaf=False)
    for relative in approved_paths:
        _validate_rel_path(relative)
        _validate_file_entry(root, relative)
    manifest_mod = _load_manifest_module()
    try:
        manifest = manifest_mod.manifest(root, approved_paths)
    except Exception:
        raise _fail("archive_manifest_failed")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise _fail("archive_manifest_failed")
    raw_files: list[str] = []
    total_bytes = 0
    for entry in manifest["files"]:
        if isinstance(entry, dict):
            rel = entry.get("path")
            size = entry.get("size")
            if isinstance(size, int) and not isinstance(size, bool) and size >= 0:
                total_bytes += size
            else:
                raise _fail("archive_manifest_failed")
        else:
            raise _fail("archive_manifest_failed")
        if not isinstance(rel, str) or not rel:
            raise _fail("archive_manifest_failed")
        raw_files.append(rel)
    if len(set(raw_files)) != len(raw_files):
        raise _fail("archive_duplicate_file")
    for rel in raw_files:
        _validate_file_entry(root, rel)
    seen_dirs: set[str] = set()
    for rel in raw_files:
        for ancestor in _ancestors(rel):
            seen_dirs.add(ancestor)
    dirs_sorted = sorted(seen_dirs, key=_dir_sort_key)
    dirs_captured = _capture_directories(root, dirs_sorted)
    files_sorted = sorted(raw_files, key=_utf8_key)
    root_st = os.lstat(root)
    if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        raise _fail("archive_root_invalid")
    entries = _entries_bytes(dirs_sorted, files_sorted)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "files": files_sorted,
        "file_metadata": sorted(manifest["files"], key=lambda item: _utf8_key(item["path"])),
        "directories": dirs_captured,
        "root_mode": stat.S_IMODE(root_st.st_mode),
        "entries": dirs_sorted + files_sorted,
        "entries_nul_sha256": hashlib.sha256(entries).hexdigest(),
        "rule_sha256": _rule_digest(),
        "total_bytes": total_bytes,
    }
    plan["plan_sha256"] = plan_digest(plan)
    entries_nul(plan)
    return plan


def entries_nul(plan: dict[str, Any]) -> bytes:
    if not isinstance(plan, dict):
        raise _fail("plan_invalid")
    if plan.get("plan_sha256") != plan_digest(plan):
        raise _fail("plan_metadata_digest_invalid")
    files = plan.get("files")
    directories = plan.get("directories")
    entries = plan.get("entries")
    if not isinstance(files, list) or not isinstance(directories, list) or not isinstance(entries, list):
        raise _fail("plan_invalid")
    if plan.get("schema_version") != SCHEMA_VERSION or isinstance(plan.get("schema_version"), bool):
        raise _fail("plan_schema_invalid")
    if plan.get("rule_version") != RULE_VERSION:
        raise _fail("plan_rule_version_invalid")
    if plan.get("rule_sha256") != _rule_digest():
        raise _fail("plan_rule_invalid")
    metadata = plan.get("file_metadata")
    if not isinstance(metadata, list) or len(metadata) != len(files):
        raise _fail("plan_file_metadata_invalid")
    if [item.get("path") if isinstance(item, dict) else None for item in metadata] != files:
        raise _fail("plan_file_metadata_invalid")
    for item in metadata:
        if set(item) != {"path", "size", "mode", "mtime_ns", "sha256"}:
            raise _fail("plan_file_metadata_invalid")
        for key in ("size", "mode", "mtime_ns"):
            if isinstance(item[key], bool) or not isinstance(item[key], int):
                raise _fail("plan_file_metadata_invalid")
        if item["size"] < 0 or not 0 <= item["mode"] <= 0o7777:
            raise _fail("plan_file_metadata_invalid")
        if not isinstance(item["sha256"], str) or not _SHA256_HEX_RE.fullmatch(item["sha256"]):
            raise _fail("plan_file_metadata_invalid")
    file_set: set[str] = set()
    files_checked: list[str] = []
    for item in files:
        rel = _validate_rel_path(item)
        if rel in file_set:
            raise _fail("plan_duplicate_file")
        file_set.add(rel)
        files_checked.append(rel)
    dir_paths: list[str] = []
    for item in directories:
        if not isinstance(item, dict):
            raise _fail("plan_invalid")
        path = item.get("path")
        rel = _validate_rel_path(path)
        mode = item.get("mode")
        if isinstance(mode, bool) or not isinstance(mode, int) or mode < 0 or mode > 0o7777:
            raise _fail("plan_directory_mode_invalid")
        mtime = item.get("mtime_ns")
        if isinstance(mtime, bool) or not isinstance(mtime, int):
            raise _fail("plan_directory_mtime_invalid")
        dir_paths.append(rel)
    derived: set[str] = set()
    for rel in files_checked:
        for ancestor in _ancestors(rel):
            derived.add(ancestor)
    if set(dir_paths) != derived:
        raise _fail("plan_ancestors_invalid")
    if len(set(dir_paths)) != len(dir_paths):
        raise _fail("plan_duplicate_directory")
    dir_set = set(dir_paths)
    for rel in files_checked:
        if rel in dir_set:
            raise _fail("plan_duplicate_directory")
    lower_files = {rel.lower() for rel in files_checked}
    lower_dirs = {rel.lower() for rel in dir_paths}
    if len(lower_files) != len(file_set):
        raise _fail("plan_case_collision")
    if len(lower_dirs) != len(dir_set):
        raise _fail("plan_case_collision")
    if lower_files & lower_dirs:
        raise _fail("plan_case_collision")
    expected_dirs = sorted(dir_paths, key=_dir_sort_key)
    if expected_dirs != dir_paths:
        raise _fail("plan_order_invalid")
    expected_files = sorted(files_checked, key=_utf8_key)
    if expected_files != files:
        raise _fail("plan_order_invalid")
    if expected_dirs + expected_files != entries:
        raise _fail("plan_order_invalid")
    data = _entries_bytes(dir_paths, files)
    if plan.get("entries_nul_sha256") != hashlib.sha256(data).hexdigest():
        raise _fail("plan_digest_invalid")
    return data


def validate_source_plan(root: Path, plan: dict[str, Any]) -> None:
    """Rehash approved source members; the operator must retain the quiet window."""
    entries_nul(plan)
    current = archive_plan(root, plan["files"])
    for field in ("file_metadata", "directories", "root_mode", "total_bytes",
                  "entries_nul_sha256", "rule_sha256", "rule_version"):
        if current[field] != plan[field]:
            raise _fail("source_plan_changed")


def validate_tar_create_paths(root: Path, list_path: Path, archive_path: Path) -> None:
    for candidate in (root, list_path, archive_path):
        if not Path(candidate).is_absolute():
            raise _fail("tar_path_not_absolute")
        _reject_symlink_ancestors(Path(candidate), allow_missing_leaf=True)
        try:
            item = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            raise _fail("tar_path_unavailable")
        if stat.S_ISLNK(item.st_mode):
            raise _fail("tar_path_symlink")
        if Path(candidate) == Path(archive_path):
            raise _fail("tar_archive_exists")
    if _within(Path(root), Path(list_path)) or _within(Path(root), Path(archive_path)) or _within(Path(list_path), Path(archive_path)):
        raise _fail("tar_paths_overlap")


def _tar_create_tokens(root: Path, list_name: str, output_name: str) -> list[str]:
    return [
        "/usr/bin/tar",
        "--format=pax",
        "--no-acls",
        "--no-xattrs",
        "--no-fflags",
        "--no-recursion",
        "--null",
        "-cpf",
        output_name,
        "-C",
        str(Path(root)),
        "-T",
        list_name,
    ]


def tar_create_argv(root: Path, list_path: Path, archive_path: Path) -> list[str]:
    validate_tar_create_paths(root, list_path, archive_path)
    return _tar_create_tokens(root, str(Path(list_path)), str(Path(archive_path)))


def tar_create_stream_argv(root: Path, list_descriptor: int) -> list[str]:
    """Caller must bind the inherited list FD and a private output FD."""
    if not Path(root).is_absolute():
        raise _fail("tar_path_not_absolute")
    _reject_symlink_ancestors(Path(root), allow_missing_leaf=False)
    if isinstance(list_descriptor, bool) or not isinstance(list_descriptor, int) or list_descriptor < 3:
        raise _fail("tar_list_descriptor_invalid")
    try:
        item = os.fstat(list_descriptor)
    except OSError:
        raise _fail("tar_list_descriptor_invalid")
    if not stat.S_ISREG(item.st_mode):
        raise _fail("tar_list_descriptor_invalid")
    return _tar_create_tokens(root, "/dev/fd/" + str(list_descriptor), "-")


def _within(a: Path, b: Path) -> bool:
    a = a.resolve()
    b = b.resolve()
    if a == b:
        return True
    return b in a.parents or a in b.parents


def tar_extract_argv(archive: Path, destination: Path) -> list[str]:
    for candidate in (archive, destination):
        if not Path(candidate).is_absolute():
            raise _fail("tar_path_not_absolute")
        _reject_symlink_ancestors(Path(candidate), allow_missing_leaf=True)
        try:
            item = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            raise _fail("tar_path_unavailable")
        if stat.S_ISLNK(item.st_mode):
            raise _fail("tar_path_symlink")
        if Path(candidate) == Path(destination):
            if not stat.S_ISDIR(item.st_mode):
                raise _fail("tar_destination_invalid")
            try:
                with os.scandir(destination) as entries:
                    if next(entries, None) is not None:
                        raise _fail("tar_destination_not_empty")
            except OSError:
                raise _fail("tar_destination_unavailable")
    if _within(Path(archive), Path(destination)):
        raise _fail("tar_paths_overlap")
    return [
        "/usr/bin/tar",
        "-xpf",
        str(Path(archive)),
        "-C",
        str(Path(destination)),
        "--no-same-owner",
        "--no-acls",
        "--no-xattrs",
        "--no-mac-metadata",
        "--no-fflags",
    ]


_NAS_ACTIONS = (
    "identity",
    "tools",
    "stat_parent",
    "acl_parent",
    "hash_archive",
    "stat_archive",
)

_NAS_ARCHIVE_ACTIONS = ("hash_archive", "stat_archive")


def _validate_target(target: str) -> str:
    if not isinstance(target, str) or not _TARGET_RE.fullmatch(target):
        raise _fail("nas_target_invalid")
    user, _, host = target.partition("@")
    if not _SAFE_USERNAME_RE.fullmatch(user):
        raise _fail("nas_target_invalid")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise _fail("nas_target_invalid")
    if ip.version != 4:
        raise _fail("nas_target_invalid")
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        raise _fail("nas_target_invalid")
    if ip.is_private:
        return target
    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return target
    raise _fail("nas_target_invalid")


def _validate_parent(parent: str) -> str:
    if not isinstance(parent, str):
        raise _fail("nas_parent_invalid")
    m = re.fullmatch(r"/volume1/homes/([A-Za-z0-9_.-]{1,64})\Z", parent)
    if not m:
        raise _fail("nas_parent_invalid")
    username = m.group(1)
    if username in (".", "..") or (username.endswith(".") and username.strip(".") == ""):
        raise _fail("nas_parent_invalid")
    return parent


def _validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise _fail("nas_run_id_invalid")


def _archive_path(parent: str, run_id: str) -> str:
    return parent + "/K2B-Recovery/" + run_id + "/knowledge.tar.gpg"


def nas_command(target: str, parent: str, action: str, *, run_id: str | None = None) -> list[str]:
    _validate_target(target)
    _validate_parent(parent)
    if parent.rsplit("/", 1)[1] != target.split("@", 1)[0]:
        raise _fail("nas_parent_identity_mismatch")
    if action not in _NAS_ACTIONS:
        raise _fail("nas_action_invalid")
    if action in _NAS_ARCHIVE_ACTIONS:
        if run_id is None:
            raise _fail("nas_run_id_required")
        _validate_run_id(run_id)
    elif run_id is not None:
        raise _fail("nas_run_id_unexpected")
    if action == "identity":
        remote = ["/bin/hostname"]
    elif action == "tools":
        remote = ["/usr/bin/python3", "-c", REMOTE_TOOLS_SNIPPET]
    elif action == "stat_parent":
        remote = ["/usr/bin/stat", "-c", STAT_FORMAT, parent]
    elif action == "acl_parent":
        remote = ["/usr/syno/bin/synoacltool", "-get", parent]
    elif action == "hash_archive":
        remote = ["/usr/bin/sha256sum", "--", _archive_path(parent, run_id or "")]
    else:
        remote = ["/usr/bin/stat", "-c", STAT_FORMAT, _archive_path(parent, run_id or "")]
    return [
        "ssh",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=1",
        target,
        shlex.join(remote),
    ]


def parse_tools_output(text: str) -> dict[str, str]:
    if not isinstance(text, str):
        raise _fail("nas_tools_output_invalid")
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" not in line:
            raise _fail("nas_tools_output_invalid")
        status, _, path = line.partition(":")
        if status not in ("present", "missing_tool", "permission_unavailable"):
            raise _fail("nas_tools_output_invalid")
        if path not in REMOTE_TOOLS_REQUIRED:
            raise _fail("nas_tools_output_invalid")
        if path in result:
            raise _fail("nas_tools_output_invalid")
        result[path] = status
    if set(result.keys()) != set(REMOTE_TOOLS_REQUIRED):
        raise _fail("nas_tools_output_invalid")
    return result


def parse_stat_output(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise _fail("nas_stat_output_invalid")
    line = text.strip()
    if "\n" in line or "|" not in line:
        raise _fail("nas_stat_output_invalid")
    parts = line.split("|")
    if len(parts) != 8:
        raise _fail("nas_stat_output_invalid")
    type_bits, uid, gid, mode, dev, ino, links, size = parts
    if not re.fullmatch(r"[0-9a-fA-F]{1,8}", type_bits):
        raise _fail("nas_stat_output_invalid")
    numeric_mode = int(type_bits, 16)
    if stat.S_ISREG(numeric_mode):
        ftype = "regular file"
    elif stat.S_ISDIR(numeric_mode):
        ftype = "directory"
    else:
        raise _fail("nas_stat_output_invalid")
    if not _OCTAL_MODE_RE.fullmatch(mode):
        raise _fail("nas_stat_output_invalid")
    def _num(value: str) -> int:
        if not value or len(value) > 20 or not value.isascii() or not value.isdigit():
            raise _fail("nas_stat_output_invalid")
        return int(value)
    uid_v = _num(uid)
    gid_v = _num(gid)
    dev_v = _num(dev)
    ino_v = _num(ino)
    links_v = _num(links)
    size_v = _num(size)
    return {
        "type": ftype,
        "uid": uid_v,
        "gid": gid_v,
        "mode": mode,
        "device": dev_v,
        "inode": ino_v,
        "links": links_v,
        "size": size_v,
    }


def parse_hash_output(text: str, expected_path: str) -> str:
    if not isinstance(text, str):
        raise _fail("nas_hash_output_invalid")
    if not isinstance(expected_path, str) or not expected_path.startswith("/"):
        raise _fail("nas_hash_output_invalid")
    stripped = text[:-1] if text.endswith("\n") else text
    if not stripped or "\n" in stripped or "\r" in stripped:
        raise _fail("nas_hash_output_invalid")
    m = re.fullmatch(r"([0-9a-f]{64}) [ *](.*)", stripped, re.DOTALL)
    if not m:
        raise _fail("nas_hash_output_invalid")
    digest, path = m.group(1), m.group(2)
    if path != expected_path:
        raise _fail("nas_hash_output_invalid")
    return digest
