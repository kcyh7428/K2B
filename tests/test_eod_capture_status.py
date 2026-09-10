from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import eod_capture  # noqa: E402


@pytest.fixture(autouse=True)
def _codex_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K2B_CODEX_SESSIONS_ROOT", str(tmp_path / ".codex" / "sessions"))
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "home")


def _session(codex_root: Path, run_date: str, name: str) -> Path:
    year, month, day = run_date.split("-")
    path = codex_root / year / month / day / f"rollout-{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "cwd": "/Users/keithmbpm2/Projects/K2B",
                            "timestamp": f"{run_date}T09:00:00+08:00",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {
                            "item": {
                                "type": "message",
                                "role": "user",
                                "content": [{"type": "input_text", "text": f"capture {name}"}],
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _minimal_vault(vault: Path) -> None:
    shelves = vault / "wiki" / "context" / "shelves"
    shelves.mkdir(parents=True)
    (vault / "System" / "memory").mkdir(parents=True)
    (vault / "review").mkdir(parents=True)
    (vault / "wiki" / "log.md").write_text("# Log\n", encoding="utf-8")
    (vault / "System" / "memory" / "self_improve_learnings.md").write_text(
        "# Learnings\n", encoding="utf-8"
    )
    (shelves / "semantic.md").write_text(
        "---\ntags: [context, shelf, semantic]\nrow-count: 0\n---\n\n# Semantic\n\n## Rows\n",
        encoding="utf-8",
    )


def test_discover_session_paths_between_catches_each_missed_day(tmp_path: Path) -> None:
    codex_root = tmp_path / ".codex" / "sessions"
    older = _session(codex_root, "2026-09-05", "older")
    newer = _session(codex_root, "2026-09-07", "newer")
    _session(codex_root, "2026-09-08", "outside")

    discovered = eod_capture.discover_session_paths_between(
        since="2026-09-05", through="2026-09-07", codex_root=codex_root
    )

    assert discovered == {
        "2026-09-05": [older],
        "2026-09-06": [],
        "2026-09-07": [newer],
    }


@pytest.mark.parametrize("project,accepted", [("K2B", True), ("K2Bi", True), ("K2B-other", False), ("Service Motion", False)])
def test_discovery_scopes_codex_worktrees(tmp_path: Path, project: str, accepted: bool) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "worktree")
    source.write_text(source.read_text().replace(
        "/Users/keithmbpm2/Projects/K2B",
        f"/Users/keithmbpm2/.codex/worktrees/f8a5/{project}",
    ))
    found = eod_capture.discover_session_paths(run_date="2026-09-05", codex_root=root)
    assert found == ([source] if accepted else [])


def test_discovery_uses_hkt_day_not_utc_directory(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-04", "midnight")
    source.write_text(source.read_text().replace("2026-09-04T09:00:00+08:00", "2026-09-04T17:00:00Z"))
    assert eod_capture.discover_session_paths(run_date="2026-09-05", codex_root=root) == [source]


def test_discovery_rejects_outside_symlink_before_reading_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    root.mkdir(parents=True)
    outside = _session(tmp_path / "outside", "2026-09-05", "external")
    link = root / "2026" / "09" / "05" / "rollout-linked.jsonl"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    original_scope = eod_capture._is_k2b_scope

    def guarded_scope(path: Path) -> bool:
        assert path != outside.resolve(), "outside symlink target was opened for scope detection"
        return original_scope(path)

    monkeypatch.setattr(eod_capture, "_is_k2b_scope", guarded_scope)

    assert eod_capture.discover_session_paths(
        run_date="2026-09-05", codex_root=root
    ) == []
    assert eod_capture.discover_session_paths_between(
        since="2026-09-05", through="2026-09-05", codex_root=root
    ) == {"2026-09-05": []}


def test_discovery_rejects_date_ranges_over_ten_years(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="10-year safety bound"):
        eod_capture.discover_session_paths_between(
            since="2016-09-01",
            through="2026-09-10",
            codex_root=tmp_path / ".codex" / "sessions",
        )


@pytest.mark.parametrize("role", ["home", "sjm-source-only"])
def test_discover_command_only_writes_local_status(tmp_path, monkeypatch, role):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "queued")
    original = source.read_bytes()
    vault = tmp_path / "vault"
    state = tmp_path / "state" / "capture-status.json"
    monkeypatch.setenv("K2B_CAPTURE_STATUS_FILE", str(state))
    def forbidden(*args, **kwargs):
        pytest.fail("discovery must never invoke extraction or reconciliation")
    monkeypatch.setattr(eod_capture, "run_job_a", forbidden)
    monkeypatch.setattr(eod_capture, "reconcile_extractions", forbidden)
    rc = eod_capture.main(["discover", "--since", "2026-09-05", "--through", "2026-09-06", "--vault", str(vault), "--writer-role", role])
    assert rc == 0
    status = json.loads(state.read_text())
    assert status["counts"]["waiting"] == 1
    assert status["capture_mode"] == "interactive"
    assert status["writer_role"] == role
    assert status["automatic_extraction"] == "disabled"
    assert source.read_bytes() == original
    assert not vault.exists()
    assert state.stat().st_mode & 0o077 == 0


def test_stage_reviewed_binds_manual_extraction_to_unchanged_source(tmp_path, monkeypatch):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "interactive")
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({
        "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "transcript_sha256": hashlib.sha256(eod_capture.strip_transcript(source).encode()).hexdigest(),
        "items": [],
        "reviewed_empty": True,
    }))
    def forbidden(*args, **kwargs):
        pytest.fail("reviewed capture must not invoke provider")
    monkeypatch.setattr(eod_capture, "call_kimi_extractor", forbidden)
    args = ["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--session", str(source), "--reviewed-json", str(reviewed)]
    assert eod_capture.main(args) == 0
    assert len(list((vault / ".staging" / "extractions").glob("*.json"))) == 1
    assert eod_capture.main(args) == 0
    assert len(list((vault / ".staging" / "extractions").glob("*.json"))) == 1
    assert eod_capture.reconcile_extractions(
        vault, run_date="2026-09-05"
    )["reconciled_files"] == 1
    receipt = json.loads(
        next((vault / ".staging" / "reconciled").glob("*.json")).read_text()
    )
    assert receipt["status"] == "reviewed_empty"
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=root,
    )
    assert status["sessions"][0]["status"] == "skipped"
    assert status["sessions"][0]["retryable"] is False
    source.write_text(source.read_text() + "\n")
    assert eod_capture.main(args) == 2


def test_export_and_stage_reviewed_remote_bundle_is_scoped_private_and_provider_free(tmp_path, monkeypatch):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "remote")
    with source.open("a") as handle:
        handle.write(json.dumps({"type": "response_item", "payload": {"item": {
            "type": "function_call_output", "output": "tool-secret-should-not-travel"}}}) + "\n")
        handle.write(json.dumps({"type": "response_item", "payload": {"item": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Decision confirmed for counterpart recall; API_KEY=fixture-secret-value"}]}}}) + "\n")
    bundle_path = tmp_path / "private" / "sjm-bundle.json"
    assert eod_capture.main(["export-source", "--source-host", "sjm", "--session", str(source), "--codex-root", str(root), "--output", str(bundle_path)]) == 0
    bundle = json.loads(bundle_path.read_text())
    assert bundle["source_host"] == "sjm"
    assert bundle["session_path"] == str(source)
    assert bundle["raw_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert bundle["transcript_sha256"] == hashlib.sha256(bundle["transcript"].encode()).hexdigest()
    assert "Decision confirmed for counterpart recall" in bundle["transcript"]
    assert "fixture-secret-value" not in bundle["transcript"]
    assert "tool-secret-should-not-travel" not in bundle["transcript"]
    assert bundle_path.stat().st_mode & 0o077 == 0

    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({
        "raw_source_sha256": bundle["raw_source_sha256"],
        "transcript_sha256": bundle["transcript_sha256"],
        "items": [{
            "kind": "decision", "confidence": "high", "subject": "K2B capture",
            "predicate": "uses", "object": "counterpart recall",
                "scope": "K2B", "canonical_home": "wiki/context/shelves/semantic.md",
            "dedupe_key": "decision:k2b-capture:counterpart-recall",
            "evidence_quote": "Decision confirmed for counterpart recall",
            "speaker_source": "assistant_confirmed",
        }],
    }))
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "home")
    monkeypatch.setattr(eod_capture, "call_kimi_extractor", lambda *_a, **_k: pytest.fail("provider invoked"))
    original_discover = eod_capture.discover_session_paths
    monkeypatch.setattr(
        eod_capture,
        "discover_session_paths",
        lambda **_kwargs: pytest.fail("bundle staging performed live discovery"),
    )
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    args = ["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--source-bundle", str(bundle_path), "--reviewed-json", str(reviewed)]
    assert eod_capture.main(args) == 0
    extraction = json.loads(next((vault / ".staging/extractions").glob("*.json")).read_text())
    assert extraction["source_host"] == "sjm"
    assert extraction["session_path"] == str(source)
    corrected = json.loads(reviewed.read_text())
    corrected["items"][0]["object"] = "counterpart recall after manual correction"
    reviewed.write_text(json.dumps(corrected))
    assert eod_capture.main(args) == 0
    extraction = json.loads(next((vault / ".staging/extractions").glob("*.json")).read_text())
    assert extraction["items"][0]["object"] == "counterpart recall after manual correction"
    assert extraction["run_date"] == "2026-09-05"
    monkeypatch.setattr(eod_capture, "discover_session_paths", original_discover)
    assert eod_capture.reconcile_extractions(vault, run_date="2026-09-05")["reconciled_files"] == 1
    receipt = json.loads(next((vault / ".staging/reconciled").glob("*.json")).read_text())
    assert receipt["source_host"] == "sjm"


def test_stage_reviewed_remote_bundle_rejects_hash_or_date_drift(tmp_path):
    bundle = tmp_path / "bundle.json"
    payload = "[user]\nA sufficiently long source statement"
    base = {
        "bundle_schema_version": 1, "source_host": "sjm", "source_app": "codex_desktop",
        "session_path": "/Users/keithcheung/.codex/sessions/2026/09/05/rollout-test.jsonl",
        "run_date": "2026-09-05", "raw_source_sha256": "a" * 64,
        "transcript": payload, "transcript_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    }
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"raw_source_sha256": "a" * 64, "transcript_sha256": base["transcript_sha256"], "items": []}))
    for mutate in (lambda x: x.update(transcript_sha256="b" * 64), lambda x: x.update(run_date="2026-09-04")):
        candidate = dict(base)
        mutate(candidate)
        bundle.write_text(json.dumps(candidate))
        vault = tmp_path / candidate["run_date"]
        assert eod_capture.main(["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--source-bundle", str(bundle), "--reviewed-json", str(reviewed)]) == 2
        assert not vault.exists()


def test_source_only_host_cannot_reconcile(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "sjm-source-only")
    with pytest.raises(PermissionError, match="home"):
        eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    with pytest.raises(PermissionError, match="home"):
        eod_capture.run_job_a([], vault_path=vault, run_date="2026-09-05")
    assert not vault.exists()


def test_stage_reviewed_command_rejects_non_home_writer(tmp_path, monkeypatch):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "source-only-review")
    payload = eod_capture.strip_transcript(source)
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(
        json.dumps(
            {
                "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "transcript_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "items": [],
            }
        )
    )
    vault = tmp_path / "vault"
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", "sjm-source-only")

    rc = eod_capture.main(
        [
            "stage-reviewed",
            "--date",
            "2026-09-05",
            "--vault",
            str(vault),
            "--session",
            str(source),
            "--codex-root",
            str(root),
            "--reviewed-json",
            str(reviewed),
        ]
    )

    assert rc == 2
    assert not vault.exists()


@pytest.mark.parametrize("role", ["HOME", "home ", "future-role", "sjm-source-only"])
def test_unknown_or_source_only_writer_roles_fail_closed(tmp_path, monkeypatch, role):
    vault = tmp_path / "vault"
    monkeypatch.setenv("K2B_CAPTURE_WRITER_ROLE", role)

    with pytest.raises(PermissionError, match="home"):
        eod_capture.run_job_a([], vault_path=vault, run_date="2026-09-05")
    with pytest.raises(PermissionError, match="home"):
        eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert not vault.exists()


def test_unset_writer_role_only_allows_known_home_account(tmp_path, monkeypatch):
    monkeypatch.delenv("K2B_CAPTURE_WRITER_ROLE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "unknown-account"))
    assert eod_capture._source_only_writer() is True

    monkeypatch.setenv("HOME", str(tmp_path / "keithmbpm2"))
    assert eod_capture._source_only_writer() is False


def test_job_a_skips_source_changed_while_stripping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "changing")
    vault = tmp_path / "vault"
    stale = (
        vault
        / ".staging"
        / "extractions"
        / f"2026-09-05_{eod_capture._safe_session_id(source)}.json"
    )
    stale.parent.mkdir(parents=True)
    stale.write_text('{"stale": true}\n')
    original_strip = eod_capture.strip_transcript

    def mutate_after_read(path: Path) -> str:
        payload = original_strip(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return payload

    monkeypatch.setattr(eod_capture, "strip_transcript", mutate_after_read)

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: pytest.fail("extractor saw a changing source"),
    )

    assert written == []
    skip = json.loads(
        next((vault / ".staging" / "extraction-skips").glob("*.json")).read_text()
    )
    assert skip["reason"] == "source_changed_during_read"
    assert not stale.exists()


def test_job_a_detects_modify_then_restore_during_strip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "restored")
    original_bytes = source.read_bytes()
    original_strip = eod_capture.strip_transcript
    vault = tmp_path / "vault"

    def modify_then_restore(path: Path) -> str:
        payload = original_strip(path)
        before = path.stat()
        path.write_bytes(original_bytes + b"temporary change\n")
        path.write_bytes(original_bytes)
        os.utime(
            path,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        return payload

    monkeypatch.setattr(eod_capture, "strip_transcript", modify_then_restore)

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: pytest.fail("extractor saw a modified source"),
    )

    assert written == []
    assert source.read_bytes() == original_bytes
    skip = json.loads(
        next((vault / ".staging" / "extraction-skips").glob("*.json")).read_text()
    )
    assert skip["reason"] == "source_changed_during_read"


def test_job_a_rejects_source_changed_while_extractor_runs(
    tmp_path: Path,
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "changes-during-extraction")
    vault = tmp_path / "vault"

    def mutate_source(_payload: str, path: Path) -> dict:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return {"items": []}

    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=mutate_source,
    )

    assert written == []
    assert list((vault / ".staging" / "extractions").glob("*.json")) == []
    skip = json.loads(
        next((vault / ".staging" / "extraction-skips").glob("*.json")).read_text()
    )
    assert skip["reason"] == "source_changed_during_extraction"


def test_source_bundle_detects_modify_then_restore_during_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "bundle-restored")
    original_bytes = source.read_bytes()
    original_strip = eod_capture.strip_dialogue_for_transport

    def modify_then_restore(path: Path) -> str:
        transcript = original_strip(path)
        before = path.stat()
        path.write_bytes(original_bytes + b"temporary change\n")
        path.write_bytes(original_bytes)
        os.utime(
            path,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        return transcript

    monkeypatch.setattr(eod_capture, "strip_dialogue_for_transport", modify_then_restore)

    with pytest.raises(ValueError, match="source changed"):
        eod_capture.build_source_bundle(
            source, source_host="home", codex_root=root
        )


def test_transport_redacts_github_tokens_before_and_after_truncation(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "token-redaction")
    oauth = "gho_" + "a" * 30
    fine_grained = "github_pat_" + "b" * 30
    digest = "0123456789abcdef" * 4
    hex_secret = "abcdef0123456789" * 4
    base64_secret = "AbCd0123+EfGh4567/" * 3 + "Z9"
    boundary = "x" * 3989 + " " + fine_grained
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": (
                                        f"oauth={oauth}\nsha256={digest}\n"
                                        f"WEBHOOK_SECRET={hex_secret}\n"
                                        'PASSWORD="correct horse battery staple"\n'
                                        '--client-secret "quoted option secret"\n'
                                        "Cookie: session=short secret; theme=dark\n"
                                        "X-API-Key: short-header-secret\n"
                                        f"opaque={base64_secret}\n{boundary}"
                                    ),
                                }
                            ],
                        }
                    },
                }
            )
            + "\n"
        )

    transport = eod_capture.strip_dialogue_for_transport(source)

    assert oauth not in transport
    assert fine_grained not in transport
    assert "gho_" not in transport
    assert "github_pat_" not in transport
    assert base64_secret not in transport
    assert digest in transport
    assert hex_secret not in transport
    assert "correct horse battery staple" not in transport
    assert "quoted option secret" not in transport
    assert "session=short secret" not in transport
    assert "short-header-secret" not in transport
    assert transport.count("[REDACTED]") >= 8


def test_transport_ignores_partial_jsonl_record(tmp_path: Path) -> None:
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "partial")
    with source.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"response_item","payload":')

    transport = eod_capture.strip_dialogue_for_transport(source)

    assert "capture partial" in transport


def test_capture_status_lock_times_out_instead_of_hanging(tmp_path, monkeypatch):
    status_file = tmp_path / "state" / "capture-status.json"
    monkeypatch.setenv("K2B_CAPTURE_STATUS_LOCK_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(
        eod_capture.fcntl,
        "flock",
        lambda *_args: (_ for _ in ()).throw(BlockingIOError()),
    )
    with pytest.raises(TimeoutError, match="remained busy"):
        with eod_capture._capture_status_lock(status_file):
            pytest.fail("busy lock must not be entered")


def test_capture_status_lock_rejects_nan_timeout(tmp_path, monkeypatch):
    status_file = tmp_path / "state" / "capture-status.json"
    monkeypatch.setenv("K2B_CAPTURE_STATUS_LOCK_TIMEOUT_SECONDS", "nan")
    with eod_capture._capture_status_lock(status_file):
        assert status_file.parent.joinpath("discovery.lock").exists()


@pytest.mark.parametrize("items", [None, "missing", {}, "not-a-list", []])
def test_stage_reviewed_rejects_incomplete_review_before_staging(tmp_path, items):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "incomplete")
    data = {
        "raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "transcript_sha256": hashlib.sha256(eod_capture.strip_transcript(source).encode()).hexdigest(),
    }
    if items != "missing":
        data["items"] = items
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps(data))
    vault = tmp_path / "vault"
    assert eod_capture.main(["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--session", str(source), "--reviewed-json", str(reviewed)]) == 2
    assert not vault.exists()


def test_stage_reviewed_local_rejects_missing_transcript_hash(tmp_path):
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "missing-hash")
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps({"raw_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "items": []}))
    vault = tmp_path / "vault"
    assert eod_capture.main(["stage-reviewed", "--date", "2026-09-05", "--vault", str(vault), "--session", str(source), "--reviewed-json", str(reviewed)]) == 2
    assert not vault.exists()


def test_reconcile_writes_source_receipt_only_after_clean_extraction(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "receipt")
    written = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )

    assert len(written) == 1
    assert list((vault / ".staging").glob("reconciled/*.json")) == []

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    receipts = list((vault / ".staging" / "reconciled").glob("*.json"))
    assert result["reconciled_files"] == 1
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["session_path"] == str(source)
    assert receipt["raw_source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert receipt["status"] == "reconciled"

    replay = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    assert replay["processed_files"] == 1
    assert replay["reconciled_files"] == 1
    assert replay["auto_written"] == 0
    assert replay["deduped"] == 0


def test_malformed_extraction_does_not_write_reconciled_receipt(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    extraction_dir = vault / ".staging" / "extractions"
    extraction_dir.mkdir(parents=True)
    (extraction_dir / "2026-09-05_broken.json").write_text("{broken", encoding="utf-8")

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert result["errors"] == 1
    assert result["reconciled_files"] == 0
    assert list((vault / ".staging").glob("reconciled/*.json")) == []


def test_non_utf8_extraction_is_quarantined_once(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "broken")
    extraction = (
        vault
        / ".staging"
        / "extractions"
        / f"2026-09-05_{eod_capture._safe_session_id(source)}.json"
    )
    extraction.parent.mkdir(parents=True)
    raw_bytes = b"\xffnot-utf8"
    extraction.write_bytes(raw_bytes)

    first = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    second = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert first["errors"] == 1
    assert second["errors"] == 0
    assert not extraction.exists()
    metadata = json.loads(
        (vault / ".staging" / "eod-quarantine" / extraction.name).read_text()
    )
    assert metadata["reason"] == "invalid_utf8_extraction"
    assert metadata["payload_sha256"] == hashlib.sha256(raw_bytes).hexdigest()
    assert Path(metadata["payload_path"]).read_bytes() == raw_bytes
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )
    assert status["sessions"][0]["status"] == "failed"
    assert status["counts"]["failed"] == 1


def test_diagnostic_extraction_artifacts_redact_nested_secrets(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "rollout-secret.jsonl"
    secret = "gho_" + "s" * 30
    quarantine = eod_capture._write_extraction_quarantine(
        vault,
        source,
        run_date="2026-09-05",
        reason="fixture",
        error="fixture",
        extractor_output={"nested": [f"token={secret}"]},
        rejections=[{"rejection_class": "content", "item": {"object": secret}}],
    )
    rejection = eod_capture._write_extraction_rejection(
        vault,
        source,
        run_date="2026-09-05",
        rejection={
            "item_index": 0,
            "rejection_class": "content",
            "error": f"Cookie: session={secret}",
            "item": {"object": secret},
        },
    )

    for artifact in (quarantine, rejection):
        text = artifact.read_text()
        assert secret not in text
        assert "[REDACTED]" in text


def test_all_extraction_writes_hold_global_reconcile_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "shared-lock")
    held: list[Path] = []
    extraction_writes = 0
    original_atomic_write = eod_capture._atomic_write_json

    @contextmanager
    def tracking_lock(path: Path):
        held.append(path)
        try:
            yield
        finally:
            held.remove(path)

    def guarded_atomic_write(path: Path, data: dict) -> None:
        nonlocal extraction_writes
        if path.parent.name == "extractions":
            extraction_writes += 1
            assert eod_capture._reconcile_lock_path(vault) in held
        original_atomic_write(path, data)

    monkeypatch.setattr(eod_capture, "_file_lock", tracking_lock)
    monkeypatch.setattr(eod_capture, "_atomic_write_json", guarded_atomic_write)

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    bundle = eod_capture.build_source_bundle(
        source, source_host="home", codex_root=root
    )
    eod_capture.stage_reviewed_bundle(
        bundle,
        {
            "raw_source_sha256": bundle["raw_source_sha256"],
            "transcript_sha256": bundle["transcript_sha256"],
            "items": [],
            "reviewed_empty": True,
        },
        vault_path=vault,
        run_date="2026-09-05",
    )

    assert extraction_writes == 2


def test_post_move_quarantine_failure_is_per_file_and_recovery_pointer_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "valid-after-corrupt")
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    corrupt = vault / ".staging" / "extractions" / "2026-09-05_000-corrupt.json"
    raw_bytes = b"\xffinvalid"
    corrupt.write_bytes(raw_bytes)
    original_atomic_write = eod_capture._atomic_write_json
    metadata_writes = 0

    def fail_final_metadata(path: Path, data: dict) -> None:
        nonlocal metadata_writes
        if path == vault / ".staging" / "eod-quarantine" / corrupt.name:
            metadata_writes += 1
            if metadata_writes == 2:
                raise OSError("fixture final metadata failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(eod_capture, "_atomic_write_json", fail_final_metadata)

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert result["processed_files"] == 2
    assert result["errors"] == 1
    assert result["reconciled_files"] == 1
    metadata = json.loads(
        (vault / ".staging" / "eod-quarantine" / corrupt.name).read_text()
    )
    assert metadata["payload_move"] == "pending"
    assert Path(metadata["payload_path"]).read_bytes() == raw_bytes
    assert not corrupt.exists()


def test_receipt_and_error_review_failure_does_not_abort_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    root = tmp_path / ".codex" / "sessions"
    source = _session(root, "2026-09-05", "receipt-double-failure")
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=root,
        extract_func=lambda *_args: {"items": []},
    )
    monkeypatch.setattr(
        eod_capture,
        "_write_reconciliation_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt full")),
    )
    monkeypatch.setattr(
        eod_capture,
        "_write_error_review",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("review full")),
    )

    result = eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert result["processed_files"] == 1
    assert result["errors"] == 1
    assert result["reconciled_files"] == 0
    assert (vault / ".staging" / "eod-capture-summary-2026-09-05.json").exists()


def test_error_reviews_with_different_secrets_do_not_overwrite(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    source = tmp_path / "source.json"
    first_secret = "gho_" + "a" * 30
    second_secret = "gho_" + "b" * 30

    eod_capture._write_error_review(
        vault,
        run_date="2026-09-05",
        source_file=source,
        reason="fixture",
        payload={"token": first_secret},
    )
    eod_capture._write_error_review(
        vault,
        run_date="2026-09-05",
        source_file=source,
        reason="fixture",
        payload={"token": second_secret},
    )

    reviews = sorted((vault / "review").glob("eod-error_*.md"))
    assert len(reviews) == 2
    for review in reviews:
        text = review.read_text()
        assert first_secret not in text
        assert second_secret not in text
        assert "[REDACTED]" in text


def test_capture_status_prefers_matching_receipt_over_stale_failure(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "status")

    def fail(_payload: str, _path: Path) -> dict:
        raise RuntimeError("provider offline")

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=fail,
    )
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")

    assert list((vault / ".staging" / "extraction-failures").glob("*.json")) == []

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="home",
    )

    assert status["counts"] == {
        "discovered": 1,
        "waiting": 0,
        "failed": 0,
        "skipped": 0,
        "reconciled": 1,
    }
    assert status["sessions"][0]["status"] == "reconciled"
    assert status["sessions"][0]["retryable"] is False
    assert status["last_reconciled"] == "2026-09-05"
    assert status["run_status"] == "complete"
    assert status["counts_valid"] is True


def test_capture_status_defensively_prefers_matching_failure_over_bare_extraction(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "diagnostic-precedence")
    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda *_args: {"items": []},
    )
    raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    eod_capture._write_extraction_failure(
        vault,
        source,
        run_date="2026-09-05",
        error=OSError("diagnostic cleanup failed"),
        raw_source_sha256=raw_hash,
        codex_root=codex_root,
    )

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )

    assert status["sessions"][0]["status"] == "failed"
    assert status["sessions"][0]["extracted"] is True
    assert status["sessions"][0]["retryable"] is True


def test_capture_status_rejects_receipt_when_present_extraction_changed(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "changed-extraction")
    [extraction] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    changed = json.loads(extraction.read_text(encoding="utf-8"))
    changed["manual_change"] = True
    extraction.write_text(json.dumps(changed), encoding="utf-8")

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )

    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["retryable"] is True
    assert status["last_reconciled"] is None


def test_capture_status_rejects_receipt_when_extraction_is_missing(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "missing-extraction")
    [extraction] = eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
    )
    eod_capture.reconcile_extractions(vault, run_date="2026-09-05")
    extraction.unlink()

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
    )

    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["retryable"] is True
    assert status["last_reconciled"] is None


def test_capture_status_reports_provider_failure_as_retryable(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "retry")

    eod_capture.run_job_a(
        [source],
        vault_path=vault,
        run_date="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    assert status["counts"]["failed"] == 1
    assert status["sessions"][0]["status"] == "failed"
    assert status["sessions"][0]["retryable"] is True
    assert status["reconciliation"] == "disabled_on_source_only_host"


def test_capture_status_hashes_session_as_a_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "streamed-hash")
    expected = hashlib.sha256(source.read_bytes()).hexdigest()

    def reject_read_bytes(_path: Path) -> bytes:
        raise AssertionError("capture status must not load a whole source file")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    assert status["sessions"][0]["raw_source_sha256"] == expected


def test_capture_status_preserves_retryable_evidence_for_unreadable_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "unreadable")
    original_hash = eod_capture._sha256_file

    def flaky_hash(path: Path, **kwargs: object) -> str:
        if path == source:
            raise OSError("transient source rotation")
        return original_hash(path, **kwargs)

    monkeypatch.setattr(eod_capture, "_sha256_file", flaky_hash)
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    session = status["sessions"][0]
    assert session["status"] == "failed"
    assert session["retryable"] is True
    assert session["error"] == "source_unreadable"
    assert session["raw_source_sha256"] is None


def test_capture_status_retries_transient_source_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "transient-read")
    original_hash = eod_capture._sha256_file
    calls = 0

    def flaky_hash(path: Path, **kwargs: object) -> str:
        nonlocal calls
        if path == source:
            calls += 1
            if calls < 3:
                raise OSError("temporary rotation")
        return original_hash(path, **kwargs)

    monkeypatch.setattr(eod_capture, "_sha256_file", flaky_hash)

    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )

    assert calls == 3
    assert status["sessions"][0]["status"] == "waiting"
    assert status["counts"]["failed"] == 0


def test_run_catch_up_can_stage_on_source_only_host_without_shared_hub_write(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    source = _session(codex_root, "2026-09-05", "source-only")
    semantic_before = (vault / "wiki" / "context" / "shelves" / "semantic.md").read_bytes()

    status_file = tmp_path / "local-state" / "capture-status.json"
    staging_before = sorted((vault / ".staging").rglob("*"))
    extractor_calls: list[Path] = []
    result = eod_capture.run_catch_up(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, path: extractor_calls.append(path) or {"items": []},
        reconcile=False,
        status_file=status_file,
        writer_role="sjm-source-only",
    )

    assert result["dates"]["2026-09-05"]["discovered"] == 1
    assert result["dates"]["2026-09-05"]["staged"] == 0
    assert result["dates"]["2026-09-05"]["pending_local"] == 1
    assert result["dates"]["2026-09-05"]["reconciled"] is False
    assert extractor_calls == []
    assert sorted((vault / ".staging").rglob("*")) == staging_before
    assert (vault / "wiki" / "context" / "shelves" / "semantic.md").read_bytes() == semantic_before
    status = eod_capture.capture_status(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        writer_role="sjm-source-only",
    )
    assert status["sessions"][0]["status"] == "waiting"
    assert status["sessions"][0]["raw_source_sha256"] == hashlib.sha256(
        source.read_bytes()
    ).hexdigest()
    persisted = json.loads(status_file.read_text(encoding="utf-8"))
    assert persisted["counts"]["waiting"] == 1
    assert persisted["writer_role"] == "sjm-source-only"
    assert persisted["last_extracted"] is None


def test_source_only_catch_up_rejects_status_path_inside_shared_vault(
    tmp_path: Path,
) -> None:
    vault = tmp_path / "vault"
    with pytest.raises(ValueError, match="outside the shared vault"):
        eod_capture.run_catch_up(
            vault,
            since="2026-09-05",
            through="2026-09-05",
            reconcile=False,
            status_file=vault / ".staging" / "capture-status.json",
            writer_role="sjm-source-only",
        )


def test_catch_up_failure_replaces_stale_status_with_failure_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    status_file = tmp_path / "state" / "capture-status.json"
    status_file.parent.mkdir(parents=True)
    status_file.write_text('{"run_status":"complete","counts_valid":true}\n')
    monkeypatch.setattr(
        eod_capture,
        "discover_session_paths_between",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture discovery failed")),
    )

    with pytest.raises(RuntimeError, match="fixture discovery failed"):
        eod_capture.run_catch_up(
            vault,
            since="2026-09-05",
            through="2026-09-05",
            reconcile=True,
            status_file=status_file,
            writer_role="home",
        )

    persisted = json.loads(status_file.read_text())
    assert persisted["run_status"] == "failed"
    assert persisted["counts_valid"] is False
    assert persisted["counts"] is None
    assert "fixture discovery failed" in persisted["error"]


def test_home_catch_up_does_not_reconcile_empty_dates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    codex_root.mkdir(parents=True)
    reconcile_calls: list[str] = []

    monkeypatch.setattr(
        eod_capture,
        "reconcile_extractions",
        lambda _vault, *, run_date: reconcile_calls.append(run_date) or {},
    )
    result = eod_capture.run_catch_up(
        vault,
        since="2026-09-05",
        through="2026-09-06",
        codex_root=codex_root,
        reconcile=True,
        writer_role="home",
    )

    assert reconcile_calls == []
    assert result["dates"]["2026-09-05"]["reconciled"] is False
    assert result["dates"]["2026-09-06"]["reconciled"] is False


def test_home_catch_up_does_not_report_failed_reconciliation_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    _minimal_vault(vault)
    codex_root = tmp_path / ".codex" / "sessions"
    _session(codex_root, "2026-09-05", "reconcile-error")
    status_file = tmp_path / "state" / "capture-status.json"

    monkeypatch.setattr(
        eod_capture,
        "reconcile_extractions",
        lambda _vault, *, run_date: {
            "errors": 1,
            "candidate_files": 1,
            "reconciled_files": 0,
        },
    )
    result = eod_capture.run_catch_up(
        vault,
        since="2026-09-05",
        through="2026-09-05",
        codex_root=codex_root,
        extract_func=lambda _payload, _path: {"items": []},
        reconcile=True,
        status_file=status_file,
        writer_role="home",
    )

    day = result["dates"]["2026-09-05"]
    assert day["reconciliation_attempted"] is True
    assert day["reconciled"] is False
    assert result["status"]["run_status"] == "failed"
    assert result["status"]["reconciliation_failures"] == [
        {
            "run_date": "2026-09-05",
            "errors": 1,
            "quarantined": 0,
            "reconciled": False,
        }
    ]
    assert json.loads(status_file.read_text())["run_status"] == "failed"


def test_catch_up_main_returns_nonzero_for_reconciliation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        eod_capture,
        "run_catch_up",
        lambda *_args, **_kwargs: {
            "status": {
                "counts": {"failed": 0},
                "run_status": "failed",
            }
        },
    )

    rc = eod_capture.main(
        [
            "catch-up",
            "--since",
            "2026-09-05",
            "--through",
            "2026-09-05",
            "--vault",
            str(tmp_path / "vault"),
        ]
    )

    assert rc == 1


def test_catch_up_main_reports_lock_timeout_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        eod_capture,
        "run_catch_up",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("writer busy")),
    )

    rc = eod_capture.main(
        [
            "catch-up",
            "--since",
            "2026-09-05",
            "--through",
            "2026-09-05",
            "--vault",
            str(tmp_path / "vault"),
        ]
    )

    assert rc == 1
    assert "writer busy" in capsys.readouterr().err
