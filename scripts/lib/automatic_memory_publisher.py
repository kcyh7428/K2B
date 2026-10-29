"""Explicit, gated Home publication. Importing this module performs no work.

Production requires a separately approved machine-local publication authority;
this module never creates that authority or registers a schedule. Python tests
may inject a preflight for synthetic roots, but the CLI exposes no such bypass.
"""
from __future__ import annotations

import fcntl
import getpass
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import automatic_memory
import eod_capture
import automatic_memory_proofs

JSON_REL = automatic_memory_proofs.JSON_REL
NOTE_REL = automatic_memory_proofs.NOTE_REL
INDEX_REL = "wiki/context/index.md"
REPO = Path(__file__).resolve().parents[2]
SMB_STORAGE = {
    "kind": "smb",
    "mount_point": "/Users/keithmbpm2/Projects/K2B-NAS",
    "source": "//cheung@192.168.50.67/Keith-Work",
    "relative_target": "K2B-Vault",
}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _publication_id(value: dict) -> str:
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _safe(vault: Path, relative: str) -> Path:
    path = vault / relative
    if not path.resolve().is_relative_to(vault.resolve()):
        raise PermissionError("publication path escapes configured vault")
    return path


@contextmanager
def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


_SMB_PROBE = """
import os, sys
from pathlib import Path
vault, mount, target = map(Path, sys.argv[1:])
if mount.is_symlink() or target.is_symlink() or mount.resolve() != mount:
    raise SystemExit(2)
if not target.is_dir(): raise SystemExit(3)
if vault.resolve() != target: raise SystemExit(4)
if os.statvfs(target).f_flag & os.ST_RDONLY: raise SystemExit(5)
if not os.access(target, os.W_OK): raise SystemExit(6)
"""


def _smb_readiness(vault: Path, storage: dict) -> dict:
    """Validate this mount and a read-only child probe, with a five-second timeout.

    This adds no deadline to existing publication I/O or rollback. Subprocess
    termination depends on OS scheduling; it is not a universal kernel-I/O bound.
    """
    if not isinstance(storage, dict) or storage != SMB_STORAGE:
        raise PermissionError("SMB storage binding is not authorized")
    print("SMB readiness: five-second subprocess probes only; parent path/policy "
          "reads and publication writes have no new deadline. OS-level stalls "
          "may still require operator recovery.", file=sys.stderr)
    mount = Path(storage["mount_point"])
    target = mount / storage["relative_target"]
    try:
        result = subprocess.run(["/sbin/mount"], capture_output=True, text=True,
                                timeout=5, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PermissionError("current mount identity is unavailable") from exc
    ancestors = []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"(.+) on (/.*) \(([^)]+)\)", line)
        if not match:
            raise PermissionError("current mount identity is malformed")
        source, mounted, flags = match.groups()
        if target.is_relative_to(Path(mounted)):
            ancestors.append((len(Path(mounted).parts), source, mounted,
                              {flag.strip() for flag in flags.split(",")}))
    if not ancestors:
        raise PermissionError("approved SMB mount is absent")
    _, source, mounted, flags = max(ancestors, key=lambda entry: entry[0])
    if (source != storage["source"] or mounted != str(mount)
        or "smbfs" not in flags or "read-only" in flags):
        raise PermissionError("approved SMB mount is absent, replaced or read-only")
    try:
        subprocess.run([sys.executable, "-I", "-c", _SMB_PROBE,
                        str(vault), str(mount), str(target)], cwd="/",
                       capture_output=True, text=True, timeout=5, check=True)
    except subprocess.CalledProcessError as exc:
        reason = {2: "symlink", 3: "missing target", 4: "wrong vault target",
                  5: "read-only target", 6: "target not writable"}.get(
                      exc.returncode, "filesystem probe failure")
        raise PermissionError("approved SMB target is unavailable: " + reason) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise PermissionError("approved SMB target is unavailable") from exc
    return {"storage": "smb-ready", "binding": dict(storage)}


def live_preflight(vault: Path) -> dict:
    """Check Home ownership, policy and live legacy Syncthing or authorized SMB.

    Authorization file fields: schema_version=1, enabled=true, vault_root,
    policy_ledger_sha256 and exclusive_paths=[JSON_REL, NOTE_REL]. Provisioning
    this file is an explicit later activation decision, never a repair side effect.
    Optional storage must equal SMB_STORAGE exactly; absent means legacy mode.
    Operator activation must compare /sbin/mount output to SMB_STORAGE literally.
    Alias resolution and policy reads in this parent, and later writes, retain
    existing I/O behavior. A probe timeout is not a whole-publication deadline.
    If stuck, keep publication disabled, inspect the owning process and pending
    journal, restore the mount privately, and retry through the existing journal
    recovery path. Never discard journals or fabricate a success receipt.
    Credentials are loaded only from this Mac's Syncthing XML and never returned.
    """
    home = Path.home().resolve()
    configured = home / "Projects/K2B-Vault"
    if getpass.getuser() != "keithmbpm2" or home != Path("/Users/keithmbpm2"):
        raise PermissionError("shared publication is allowed only on the Home Mac")
    if vault.resolve() != configured.resolve():
        raise PermissionError("publication requires the configured Home vault")
    authority_path = home / ".local/state/k2b/automatic-memory/publication-authority.json"
    try:
        authority = json.loads(authority_path.read_text())
    except (OSError, ValueError) as exc:
        raise PermissionError("production publication authority is absent or malformed") from exc
    if (not isinstance(authority, dict) or authority.get("schema_version") != 1
        or authority.get("enabled") is not True
        or authority.get("vault_root") != str(configured)
        or authority.get("exclusive_paths") != [JSON_REL, NOTE_REL]):
        raise PermissionError("production publication ownership is not authorized")
    storage_evidence = (_smb_readiness(vault, authority["storage"])
                        if "storage" in authority else None)
    ledger = _safe(vault, "wiki/context/policy-ledger.jsonl").read_bytes()
    # Human-readable policy rules are not interpreted by a permissive matcher.
    # A changed ledger invalidates the explicitly reviewed activation approval.
    try:
        entries = [json.loads(line) for line in ledger.decode().splitlines() if line.strip()]
    except (ValueError, UnicodeError) as exc:
        raise PermissionError("policy ledger is malformed") from exc
    if any(not isinstance(entry, dict) for entry in entries):
        raise PermissionError("policy ledger is malformed")
    digest = _hash(ledger)
    if authority.get("policy_ledger_sha256") != digest:
        raise PermissionError("policy ledger changed; publication needs a fresh policy review")
    if storage_evidence is not None:
        return {"policy_ledger_sha256": digest, **storage_evidence}
    config = ET.parse(home / "Library/Application Support/Syncthing/config.xml").getroot()
    folder = next((f for f in config.findall("folder")
                   if Path(f.get("path", "")).expanduser().resolve() == vault.resolve()), None)
    gui = config.find("gui")
    if folder is None or gui is None or not folder.get("id"):
        raise PermissionError("Syncthing vault folder or local GUI is unavailable")
    address = gui.findtext("address", "")
    # Only the existing local GUI endpoint receives the machine-local API key.
    if not re.fullmatch(r"127\.0\.0\.1:[0-9]+", address):
        raise PermissionError("Syncthing GUI must use a loopback endpoint")
    key = gui.findtext("apikey", "")
    if not key:
        raise PermissionError("Syncthing GUI authorization is unavailable")
    scheme = "https" if gui.get("tls") == "true" else "http"
    request = urllib.request.Request(
        f"{scheme}://{address}/rest/db/status?" + urllib.parse.urlencode({"folder": folder.get("id")}),
        headers={"X-API-Key": key})
    # Loopback health checks must not travel through a configured proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        status = json.load(response)
    if (status.get("state") != "idle" or status.get("error") not in (None, "")
        or any(type(status.get(k)) is not int or status[k] != 0
               for k in ("needFiles", "needBytes", "needDirectories", "errors", "pullErrors"))):
        raise PermissionError("Syncthing vault is not idle and fully synchronized")
    return {"policy_ledger_sha256": digest, "syncthing": "idle"}


def _update_indexes(vault: Path) -> None:
    """Reuse the designated index format/count contract under its own lock."""
    spec = importlib.util.spec_from_file_location("memory_compile_index", REPO / "scripts/compile-index-update.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.VAULT_ROOT = str(vault)
    acquired = False
    try:
        helper.acquire_lock()
        acquired = True
        index = _safe(vault, INDEX_REL)
        text = helper.rewrite_subfolder_index(INDEX_REL)
        row = f"| [[context_automatic-memory-recall]] | Current source-backed memory and unresolved questions | {helper.TODAY} |"
        if "[[context_automatic-memory-recall]]" in text:
            text = re.sub(r"^\| \[\[context_automatic-memory-recall\]\].*$", row, text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + "\n\n## Automatic Memory\n\n| Page | Summary | Updated |\n|------|---------|---------|\n" + row + "\n"
        master = helper.rewrite_master_index()
        eod_capture._atomic_write_text(index, text)
        eod_capture._atomic_write_text(_safe(vault, "wiki/index.md"), master)
    except SystemExit as exc:
        raise OSError(f"designated index writer refused publication (exit {exc.code}); inspect index shape and lock") from exc
    finally:
        if acquired:
            helper.release_lock()


def _append_log(vault: Path, digest: str) -> None:
    log = _safe(vault, "wiki/log.md")
    marker = "automatic-memory snapshot=" + digest
    if marker in log.read_text():
        return
    env = dict(os.environ, K2B_WIKI_LOG=str(log))
    result = subprocess.run(
        [str(REPO / "scripts/wiki-log-append.sh"), "/vault-writer", NOTE_REL, marker],
        env=env, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise OSError("locked wiki log append failed")


def _reconciliation_watermark(state: Path, target: Path, snapshot: dict) -> dict:
    watermark = {}
    target_hash = _hash(str(target.resolve()).encode())
    for path in sorted((state / "inbox").glob("*.json")):
        envelope = automatic_memory.load_home_envelope(state, path.stem)
        import automatic_memory_key_resolution
        effective_envelope, resolution_hash = automatic_memory_key_resolution.effective(state, envelope, memory_path=target)
        receipt_path = state / "reconciliation" / path.name
        try:
            receipt = json.loads(receipt_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("accepted memory has no valid reconciliation receipt") from exc
        if (not isinstance(receipt, dict) or receipt.get("status") != "reconciled"
            or receipt.get("delivery_id") != envelope["delivery_id"]
            or receipt.get("content_id") != envelope["content_id"]
            or receipt.get("memory_target_sha256") != target_hash):
            raise ValueError("reconciliation receipt identity mismatch")
        if receipt.get("key_resolution_sha256") != resolution_hash:
            raise ValueError("reconciliation key resolution binding mismatch")
        for item in effective_envelope["bundle"]["items"]:
            version = automatic_memory._version(effective_envelope["bundle"], item)
            if version not in snapshot["records"].get(item["key"], {}).get("versions", []):
                raise ValueError("reconciliation receipt is not backed by memory state")
        watermark[envelope["delivery_id"]] = _hash(receipt_path.read_bytes())
    return watermark


def publish(memory_state_path: Path, state_root: Path, vault_root: Path,
            writer_role: str, *, preflight=None) -> dict:
    if writer_role != "home":
        raise PermissionError("shared publication requires the Home writer")
    vault, state, target = Path(vault_root).resolve(), Path(state_root), Path(memory_state_path)
    if state.resolve().is_relative_to(vault) or target.resolve().is_relative_to(vault):
        raise PermissionError("publisher receipts and working memory must remain machine-local")
    check = preflight or live_preflight
    # Preflight before even creating the local publication lock.
    evidence = check(vault)
    if (not isinstance(evidence, dict)
        or not (("storage" not in evidence and evidence.get("syncthing") == "idle")
                or (evidence.get("storage") == "smb-ready"
                    and evidence.get("binding") == SMB_STORAGE))
        or not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("policy_ledger_sha256", "")))):
        raise PermissionError("publication preflight is not a healthy, policy-bound result")
    # One operator-authorized Home migration, never general realpath equivalence.
    # Only the production preflight can authorize historical namespace evidence.
    historical_vault = "/Users/keithmbpm2/Projects/K2B-Vault"
    accept_historical = (
        preflight is None and evidence.get("storage") == "smb-ready"
        and evidence.get("binding") == SMB_STORAGE
        and vault == Path(SMB_STORAGE["mount_point"]) / SMB_STORAGE["relative_target"]
        and Path(historical_vault).resolve() == vault)
    paths = {rel: _safe(vault, rel) for rel in
             (JSON_REL, NOTE_REL, INDEX_REL, "wiki/index.md", "wiki/log.md")}
    for rel in (INDEX_REL, "wiki/index.md", "wiki/log.md"):
        if not paths[rel].is_file():
            raise OSError(f"required shared hub is missing: {rel}")
    # Fixed machine-local lock namespace, like the designated index/log writers;
    # TMPDIR must not let two native processes acquire different ownership locks.
    vault_lock = Path("/tmp") / ("k2b-memory-publication-" + _hash(str(vault).encode()) + ".lock")
    with _lock(vault_lock), _lock(state / ".publication.lock"), _lock(state / ".reconciliation.lock"):
        if not target.is_file():
            raise ValueError("reconciled memory state is missing")
        rendered = {}
        eod_capture.publish_shared_recall(state, writer_role="home", memory_path=target,
                                         write_func=lambda name, text: rendered.__setitem__(name, text))
        if set(rendered) != {JSON_REL, NOTE_REL}:
            raise ValueError("publication renderer returned an incomplete artifact pair")
        snapshot = automatic_memory._validate_memory_state(json.loads(rendered[JSON_REL]))
        watermark = _reconciliation_watermark(state, target, snapshot)
        digest = _hash(rendered[JSON_REL].encode())
        source_dates = [version["source_time"][:10] for record in snapshot["records"].values()
                        for version in record["versions"]]
        # A source-derived date keeps replay deterministic across midnight.
        source_date = max(source_dates, default="1970-01-01")
        if rendered[NOTE_REL].count("status: generated\n") != 1:
            raise ValueError("publication renderer frontmatter contract changed")
        rendered[NOTE_REL] = rendered[NOTE_REL].replace(
            "status: generated\n", f'status: generated\ndate: {source_date}\ntype: context\norigin: k2b-extract\nup: "[[index]]"\nsnapshot-sha256: {digest}\n', 1)
        import automatic_memory_proofs
        if automatic_memory_proofs.snapshot_hash(snapshot) != digest:
            raise ValueError('publication proof serialization contract changed')
        proofs = automatic_memory_proofs.collect(state, target, snapshot)
        rendered[NOTE_REL] += automatic_memory_proofs.render(digest, proofs)
        artifact_hashes = {rel: _hash(text.encode()) for rel, text in rendered.items()}
        expected = {"schema_version": 1, "status": "published", "writer_role": "home",
                    "vault_root": str(vault), "snapshot_sha256": digest,
                    "artifact_sha256": artifact_hashes, "reconciliation_watermark": watermark,
                    "policy_ledger_sha256": evidence["policy_ledger_sha256"],
                    "index": INDEX_REL, "log_marker": "automatic-memory snapshot=" + digest}
        if evidence.get("storage") == "smb-ready":
            expected.update(storage="smb-ready", binding=dict(evidence["binding"]))
        # Provenance can grow without changing the semantic snapshot. Each
        # immutable receipt therefore identifies the full publication binding.
        identity = _publication_id(expected)
        receipt_path = state / "publication" / f"{identity}.json"
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text())
            if not isinstance(receipt, dict) or any(receipt.get(k) != v for k, v in expected.items()):
                raise ValueError("publication receipt identity mismatch")
            if (all(paths[rel].is_file() and _hash(paths[rel].read_bytes()) == sha for rel, sha in artifact_hashes.items())
                and "[[context_automatic-memory-recall]]" in paths[INDEX_REL].read_text()
                and expected["log_marker"] in paths["wiki/log.md"].read_text()):
                return {**receipt, "duplicate": True}
        # Only replace our own generated artifacts. Unexpected edits are not
        # an invitation to overwrite a human or another writer's work.
        known = {rel: {sha} for rel, sha in artifact_hashes.items()}
        for folder in ("publication", "publication-pending"):
            for prior_path in (state / folder).glob("*.json"):
                try:
                    prior = json.loads(prior_path.read_text())
                except (OSError, ValueError) as exc:
                    raise ValueError(f"publication receipt is unreadable: {prior_path}") from exc
                if folder == "publication-pending":
                    if not isinstance(prior, dict) or prior.get("status") != "staged":
                        raise ValueError(f"publication pending receipt is malformed: {prior_path}")
                    prior = prior.get("publication")
                if (not isinstance(prior, dict)
                    or not (prior.get("vault_root") == str(vault)
                            or (accept_historical and prior.get("vault_root") == historical_vault))
                    or type(prior.get("schema_version")) is not int or prior["schema_version"] != 1
                    or prior.get("status") != "published" or prior.get("writer_role") != "home"
                    or not isinstance(prior.get("artifact_sha256"), dict)
                    or set(prior["artifact_sha256"]) != set(rendered)
                    or any(not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
                           for sha in prior["artifact_sha256"].values())
                    or prior.get("snapshot_sha256") != prior["artifact_sha256"][JSON_REL]
                    or (("storage" in prior or "binding" in prior)
                        and (prior.get("storage") != "smb-ready" or prior.get("binding") != SMB_STORAGE))):
                    raise ValueError(f"publication receipt is malformed or belongs to another vault: {prior_path}")
                binding = {k: v for k, v in prior.items() if k != "published_at"}
                if _publication_id(binding) != prior_path.stem:
                    raise ValueError(f"publication receipt content binding is invalid: {prior_path}")
                for rel in known:
                    prior_hash = prior.get("artifact_sha256", {}).get(rel)
                    if isinstance(prior_hash, str):
                        known[rel].add(prior_hash)
        before = {}
        for rel in rendered:
            before[rel] = _hash(paths[rel].read_bytes()) if paths[rel].exists() else None
            if before[rel] is not None and before[rel] not in known[rel]:
                raise PermissionError("generated recall was modified by another writer; preserve it")
        # Rendering/staging is complete before either shared artifact changes.
        # Recheck live storage before writing; prior cached health is insufficient.
        if check(vault) != evidence:
            raise PermissionError("publication preflight changed during staging")
        for rel, old_hash in before.items():
            current = _hash(paths[rel].read_bytes()) if paths[rel].exists() else None
            if current != old_hash:
                raise PermissionError("recall was modified during publication staging")
        # A prepared journal is not a success receipt. Retain it across failed
        # runs so even a newer snapshot can recognize our own partial writes.
        pending = state / "publication-pending" / f"{identity}.json"
        staged = {"status": "staged", "publication": expected}
        if not pending.exists():
            eod_capture._atomic_write_json(pending, staged)
        for rel, text in rendered.items():
            eod_capture._atomic_write_text(_safe(vault, rel), text)
        _update_indexes(vault)
        _append_log(vault, digest)
        receipt = {**expected, "published_at": datetime.now(timezone.utc).isoformat()}
        eod_capture._atomic_write_json(receipt_path, receipt)
        # The durable success receipt now retains these recovery hashes; only
        # this completed transaction's redundant pending journal is retired.
        pending.unlink()
        eod_capture._fsync_dir(pending.parent)
        return {**receipt, "duplicate": False}
