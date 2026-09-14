#!/usr/bin/env python3
# tests/test_vault_note_write.py
# Focused tests for scripts/vault-note-write.py (simple two-Mac ordinary
# note writer). Every test runs against a synthetic vault under tmp_path via
# environment overrides; no real vault, state, provider, SSH or Syncthing
# surface is touched.

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "vault-note-write.py")

MAX_NOTE_BYTES = 2 * 1024 * 1024

FOLDER_INDEX_4COL = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki Work Index
Fixture work index.

Last updated: 2026-09-01 | Entries: 2

| Page | Status | Summary | Updated |
|------|--------|---------|---------|
| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery: vendor recovery through 2027 | 2026-09-01 |
| [[work_sjm-ai-implementation]] | active | SJM AI implementation hub | 2026-09-01 |
"""

FOLDER_INDEX_3COL = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki People Index
Fixture people index.

Last updated: 2026-09-01 | Entries: 1

| Page | Summary | Updated |
|---------|---------|---------|
| [[person_Amy-Lin]] | SVP Treasury candidate | 2026-07-15 |
"""

MASTER_INDEX = """---
tags: [index, wiki, master]
date: 2026-04-15
type: index
origin: k2b-generate
---
# K2B Wiki -- Master Index
Fixture master index.

Last updated: 2026-09-01

## Subfolders

| Folder | Purpose | Entries |
|--------|---------|---------|
| [people/](people/index.md) | Person pages | 1 |
| [work/](work/index.md) | Work pages | 2 |

**Total wiki pages: 3**
"""

NOTE_KINGDEE_V1 = """---
tags: [work, sjm]
date: 2026-09-01
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

Vendor recovery running through 2027.
"""

NOTE_KINGDEE_V2 = """---
tags: [work, sjm]
date: 2026-09-14
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Kingdee HRIS Recovery

Vendor recovery running through 2027. January 2027 payroll readiness confirmed.
"""

NOTE_NEW = """---
tags: [work, sjm]
date: 2026-09-14
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Call Centre Consolidation

Consolidate the call centre off Macau.
"""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


@pytest.fixture()
def vault_env(tmp_path, monkeypatch):
    """Synthetic vault + local state + lock dirs, all under tmp_path."""
    vault = tmp_path / "vault"
    state = tmp_path / "state"
    lock_root = tmp_path / "locks"
    for d in (
        vault / "wiki" / "work",
        vault / "wiki" / "people",
        state,
        lock_root,
    ):
        d.mkdir(parents=True, exist_ok=True)
    (vault / "wiki" / "work" / "index.md").write_text(FOLDER_INDEX_4COL, encoding="utf-8")
    (vault / "wiki" / "people" / "index.md").write_text(FOLDER_INDEX_3COL, encoding="utf-8")
    (vault / "wiki" / "people" / "person_Amy-Lin.md").write_text(
        "---\ntags: [person]\ndate: 2026-07-15\ntype: person\norigin: keith\n"
        'up: "[[MOC_SJM-Work]]"\n---\n# Amy Lin\n\nTreasury candidate.\n',
        encoding="utf-8",
    )
    (vault / "wiki" / "index.md").write_text(MASTER_INDEX, encoding="utf-8")
    (vault / "wiki" / "work" / "work_kingdee-hris-recovery.md").write_text(
        NOTE_KINGDEE_V1, encoding="utf-8"
    )
    (vault / "wiki" / "work" / "work_sjm-ai-implementation.md").write_text(
        "---\ntags: [work]\ndate: 2026-09-01\ntype: work\norigin: keith\n"
        'up: "[[MOC_SJM-Work]]"\n---\n# SJM AI Implementation\n\nHub.\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2B_VAULT_ROOT", str(vault))
    monkeypatch.setenv("K2B_LOCAL_STATE", str(state))
    monkeypatch.setenv("K2B_NOTE_WRITE_LOCK", str(lock_root / "note-write.lock.d"))
    monkeypatch.setenv("K2B_COMPILE_INDEX_LOCK", str(lock_root / "compile-index.lock.d"))
    return {
        "vault": vault,
        "state": state,
        "lock_root": lock_root,
        "env": dict(os.environ),
    }


def write_args(env, *extra):
    return [sys.executable, SCRIPT, *extra]


def run_cli(args, env, **kwargs):
    return subprocess.run(
        args,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        **kwargs,
    )


def draft(tmp_path, cli_env, content: str):
    """Private draft outside the vault."""
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir(exist_ok=True)
    f = draft_dir / ("draft-%d.md" % time.time_ns())
    f.write_text(content, encoding="utf-8")
    return f


def base_write_cmd(tmp_path, cli_env, note_rel, content, expected, summary, source_ref):
    content_file = draft(tmp_path, cli_env, content)
    cmd = write_args(
        cli_env,
        "write",
        "--path", note_rel,
        "--content-file", str(content_file),
        "--expected-sha256", expected,
        "--summary", summary,
        "--source-ref", source_ref,
    )
    return cmd


def kingdee_expected(env):
    target = env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    return sha256_bytes(target.read_bytes())


def receipts(env):
    rdir = env["state"] / "note-write-receipts"
    if not rdir.is_dir():
        return []
    return sorted(rdir.glob("*.json"))


def result_of(proc):
    return json.loads(proc.stdout.decode("utf-8"))


# --- identity / offline save ---------------------------------------------

def load_module():
    spec = importlib.util.spec_from_file_location(
        "vault_note_write_under_test", SCRIPT
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("user", ["keithmbpm2", "keithcheung"])
def test_identity_fixtures_save_same_note_offline(vault_env, tmp_path, monkeypatch, user, capsys):
    mod = load_module()
    monkeypatch.setattr(mod, "current_user_name", lambda: user)
    content_file = draft(tmp_path, vault_env, NOTE_KINGDEE_V2)
    rc = mod.main(
        [
            "write",
            "--path", "wiki/work/work_kingdee-hris-recovery.md",
            "--content-file", str(content_file),
            "--expected-sha256", kingdee_expected(vault_env),
            "--summary", "Kingdee HRIS recovery: January 2027 payroll readiness confirmed",
            "--source-ref", "codex-session-2026-09-14-kingdee",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "saved-local"
    assert "synchronization unverified" in out["note"]
    assert "synchronized" not in out["result"]
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V2
    assert receipts(vault_env), "expected a local receipt"


# --- recovery copies ------------------------------------------------------

def test_before_after_copies_recover_mistaken_overwrite(vault_env, tmp_path):
    cli_env = dict(os.environ)
    original = (vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md").read_bytes()
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(original),
        "updated summary", "src-1",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    backups = vault_env["state"] / "note-write-backups"
    before = list(backups.glob("before/*.md"))
    after = list(backups.glob("after/*.md"))
    assert before and after
    assert any(p.read_bytes() == original for p in before), "before copy must hold pre-write bytes"
    assert any(p.read_bytes() == NOTE_KINGDEE_V2.encode() for p in after)


# --- refusal cases leave target unchanged ---------------------------------

@pytest.mark.parametrize(
    "note_rel",
    [
        "wiki/work/work_kingdee-hris-recovery.md/../escape.md",
        "wiki/work/../context/policy-ledger.jsonl.md",
        "wiki/work/.hidden/note.md",
        "wiki/work/Shipped/note.md",
        "wiki/work/index.md",
        "wiki/context/context_note.md",
        "System/memory/active_rules.md",
        "wiki/work/note.txt",
    ],
)
def test_disallowed_paths_refused(vault_env, tmp_path, note_rel):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, note_rel, NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (note_rel, proc.stdout, proc.stderr)
    if note_rel == "wiki/work/index.md":
        assert (vault_env["vault"] / note_rel).read_text(encoding="utf-8") == FOLDER_INDEX_4COL
    else:
        assert not (vault_env["vault"] / note_rel).exists()


def test_stale_expected_hash_leaves_target_unchanged(vault_env, tmp_path):
    cli_env = dict(os.environ)
    stale = "0" * 64
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, stale, "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_expected_missing_but_note_exists_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, "missing", "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_symlink_target_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    link = vault_env["vault"] / "wiki" / "work" / "work_link.md"
    link.symlink_to(vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_link.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert link.is_symlink()


def test_fifo_target_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    fifo = vault_env["vault"] / "wiki" / "work" / "work_fifo.md"
    os.mkfifo(fifo)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_fifo.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert stat.S_ISFIFO(fifo.stat().st_mode)


@pytest.mark.parametrize(
    "bad_content",
    [
        "",  # empty
        "\x00binary",  # NUL
        "no markdown structure at all",  # not markdown-looking
    ],
)
def test_malformed_content_refused(vault_env, tmp_path, bad_content):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", bad_content, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_oversized_content_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    big = "# Big\n\n" + ("x" * MAX_NOTE_BYTES)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_big.md", big, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_big.md").exists()


def test_invalid_utf8_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    draft_dir = tmp_path / "drafts"
    draft_dir.mkdir(exist_ok=True)
    f = draft_dir / "bad.md"
    f.write_bytes(b"# Note\n\n\xff\xfe invalid")
    cmd = write_args(
        cli_env, "write", "--path", "wiki/work/work_bad.md",
        "--content-file", str(f), "--expected-sha256", "missing",
        "--summary", "sum", "--source-ref", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_bad.md").exists()


def test_content_file_inside_vault_refused(vault_env):
    cli_env = dict(os.environ)
    inside = vault_env["vault"] / "wiki" / "work" / "draft-inside.md"
    inside.write_text(NOTE_NEW, encoding="utf-8")
    cmd = write_args(
        cli_env, "write", "--path", "wiki/work/work_inside.md",
        "--content-file", str(inside), "--expected-sha256", "missing",
        "--summary", "sum", "--source-ref", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_inside.md").exists()


def test_summary_cannot_inject_table_delimiter(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env),
        "summary | forged row", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_multiline_summary_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env),
        "line1\nline2", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1


# --- conflict copies ------------------------------------------------------

def make_conflict(vault_env, folder, stem):
    conflict = vault_env["vault"] / folder / ("%s.sync-conflict-20260914-120000.md" % stem)
    conflict.write_text("# conflict version\n", encoding="utf-8")
    return conflict


def test_conflict_copy_refused_and_both_versions_preserved(vault_env, tmp_path):
    cli_env = dict(os.environ)
    conflict = make_conflict(vault_env, "wiki/work", "work_kingdee-hris-recovery")
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    before_bytes = target.read_bytes()
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(before_bytes), "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert target.read_bytes() == before_bytes
    assert conflict.read_text(encoding="utf-8") == "# conflict version\n"


def test_unrelated_conflict_does_not_block_different_note(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_sjm-ai-implementation")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- lock exclusion -------------------------------------------------------

def test_writer_lock_exclusion(vault_env, tmp_path):
    cli_env = dict(os.environ)
    lock = vault_env["lock_root"] / "note-write.lock.d"
    lock.mkdir()
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 4
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- idempotent replay ----------------------------------------------------

def test_idempotent_replay_creates_no_duplicate_rows_or_copies(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env), "new summary", "src",
    )
    proc1 = run_cli(cmd, cli_env)
    assert proc1.returncode == 0, proc1.stderr.decode()
    backups_after_first = sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    )
    index_after_first = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    receipts_after_first = len(receipts(vault_env))

    expected_now = kingdee_expected(vault_env)
    cmd2 = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, expected_now, "new summary", "src",
    )
    proc2 = run_cli(cmd2, cli_env)
    assert proc2.returncode == 0, proc2.stderr.decode()
    out = result_of(proc2)
    assert out["result"] == "already-present"
    backups_after_second = sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    )
    assert backups_after_second == backups_after_first, "replay must not add history copies"
    index_after_second = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    assert index_after_second.count("[[work_kingdee-hris-recovery]]") == 1
    assert index_after_second == index_after_first
    assert len(receipts(vault_env)) == receipts_after_first, "replay must not duplicate receipts"
    last_receipt = json.loads(receipts(vault_env)[-1].read_text(encoding="utf-8"))
    assert last_receipt["result"] == "saved-local", \
        "completed replay is a no-op and must not rewrite the finished receipt"
    assert last_receipt["synchronized"] == "unverified"


# --- new note, index integration ------------------------------------------

def test_new_note_updates_4col_index_counts_and_master(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_call-centre-consolidation.md",
        NOTE_NEW, "missing",
        "Consolidate the call centre off Macau", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    assert "[[work_call-centre-consolidation]]" in index
    assert "Consolidate the call centre off Macau" in index
    assert "| Entries: 3" in index
    master = (vault_env["vault"] / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "**Total wiki pages: 4**" in master


def test_existing_note_row_updated_and_other_rows_retained(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env),
        "Kingdee HRIS recovery: payroll readiness confirmed", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    kingdee_rows = [l for l in index.splitlines() if "work_kingdee-hris-recovery" in l]
    assert len(kingdee_rows) == 1
    assert "Kingdee HRIS recovery: payroll readiness confirmed" in kingdee_rows[0]
    assert "[[work_sjm-ai-implementation]] | active | SJM AI implementation hub" in index
    assert "| Entries: 2" in index


def test_3col_index_gets_row_without_status(vault_env, tmp_path):
    cli_env = dict(os.environ)
    note = """---
tags: [person]
date: 2026-09-14
type: person
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Person Bob Jones

SJM finance stakeholder.
"""
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/people/person_Bob-Jones.md",
        note, "missing", "SJM finance stakeholder", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "people" / "index.md").read_text(encoding="utf-8")
    rows = [l for l in index.splitlines() if "person_Bob-Jones" in l]
    assert len(rows) == 1
    assert rows[0].startswith("| [[person_Bob-Jones]] | SJM finance stakeholder |")
    assert rows[0].count("|") == 4  # 3-column shape


def test_4col_status_column_updated_when_supplied(vault_env, tmp_path):
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_KINGDEE_V2)
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/work/work_kingdee-hris-recovery.md",
        "--content-file", str(content_file),
        "--expected-sha256", kingdee_expected(vault_env),
        "--summary", "Kingdee recovery status changed", "--source-ref", "src",
        "--status", "simmering",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki" / "work" / "index.md").read_text(encoding="utf-8")
    rows = [l for l in index.splitlines() if "work_kingdee-hris-recovery" in l]
    assert len(rows) == 1
    assert "| simmering |" in rows[0]


def test_malformed_index_shape_fails_explicitly_without_note_write(vault_env, tmp_path):
    cli_env = dict(os.environ)
    bad_index = vault_env["vault"] / "wiki" / "work" / "index.md"
    bad_index.write_text("# no table here\n", encoding="utf-8")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_missing_index_blocks_save_explicitly(vault_env, tmp_path):
    cli_env = dict(os.environ)
    (vault_env["vault"] / "wiki" / "work" / "index.md").unlink()
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_master_index_conflict_copy_refused_before_note_write(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki", "index")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 2
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- partial save + retry repair ------------------------------------------

def test_index_write_failure_is_partial_and_retry_repairs(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["saved"] == "wiki/work/work_new-note.md"
    assert out["failed_step"] == "index"
    work_dir = vault_env["vault"] / "wiki/work"
    assert (work_dir / "work_new-note.md").read_text(encoding="utf-8") == NOTE_NEW

    # Retry the exact original command now that the fault is gone: the
    # note bytes equal this operation's saved-after hash and its receipt
    # is unfinished, so the indexes repair.
    mod.atomic_replace = real_replace
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "sum", "src",
    )
    assert rc == 0
    assert "[[work_new-note]]" in (work_dir / "index.md").read_text(encoding="utf-8")


def test_backup_failure_blocks_write(vault_env, tmp_path):
    cli_env = dict(os.environ)
    backups = vault_env["state"] / "note-write-backups"
    backups.write_text("i am a file, not a dir", encoding="utf-8")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 5, (proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- conflicts subcommand (SessionStart support) --------------------------

def test_conflicts_subcommand_lists_bounded_paths(vault_env):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_kingdee-hris-recovery")
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    out = proc.stdout.decode("utf-8")
    assert "work_kingdee-hris-recovery.sync-conflict-" in out


def test_conflicts_subcommand_silent_when_none(vault_env):
    cli_env = dict(os.environ)
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    assert proc.stdout.decode("utf-8").strip() == ""


def test_conflicts_subcommand_unknown_on_missing_vault(vault_env, monkeypatch):
    monkeypatch.setenv("K2B_VAULT_PATH", "/nonexistent-vault-path-for-test")
    cli_env = dict(os.environ)
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    out = proc.stdout.decode("utf-8")
    assert "unknown" in out


def test_conflicts_subcommand_performs_no_writes(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_kingdee-hris-recovery")
    before = sorted(str(p.relative_to(vault_env["vault"])) for p in vault_env["vault"].rglob("*"))
    proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
    assert proc.returncode == 0
    after = sorted(str(p.relative_to(vault_env["vault"])) for p in vault_env["vault"].rglob("*"))
    assert before == after, "conflicts must not create backup/state files in the vault"


# --- receipt hygiene ------------------------------------------------------

def test_private_state_files_are_0600(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    for f in receipts(vault_env):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    backups = vault_env["state"] / "note-write-backups"
    for f in list(backups.glob("before/*")) + list(backups.glob("after/*")):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600


# --- correction gaps: identity, symlinks, state confinement -----------------

def test_unapproved_identity_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    monkeypatch.setattr(mod, "current_user_name", lambda: "mallory")
    content_file = draft(tmp_path, vault_env, NOTE_KINGDEE_V2)
    rc = mod.main(
        [
            "write",
            "--path", "wiki/work/work_kingdee-hris-recovery.md",
            "--content-file", str(content_file),
            "--expected-sha256", kingdee_expected(vault_env),
            "--summary", "s", "--source-ref", "src",
        ]
    )
    assert rc == 1
    target = vault_env["vault"] / "wiki" / "work" / "work_kingdee-hris-recovery.md"
    assert target.read_text(encoding="utf-8") == NOTE_KINGDEE_V1


def test_conflicts_subcommand_allowed_for_any_identity(vault_env, monkeypatch, capsys):
    mod = load_module()
    monkeypatch.setattr(mod, "current_user_name", lambda: "mallory")
    rc = mod.main(["conflicts"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == ""


def test_draft_symlink_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    real = draft(tmp_path, vault_env, NOTE_NEW)
    link = tmp_path / "drafts" / "linked-draft.md"
    link.symlink_to(real)
    cmd = write_args(
        cli_env, "write", "--path", "wiki/work/work_new-note.md",
        "--content-file", str(link), "--expected-sha256", "missing",
        "--summary", "sum", "--source-ref", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_folder_index_symlink_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    index = vault_env["vault"] / "wiki" / "work" / "index.md"
    real = index.read_bytes()
    index.unlink()
    index.symlink_to(tmp_path / "elsewhere-index.md")
    (tmp_path / "elsewhere-index.md").write_bytes(real)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_master_index_symlink_refused(vault_env, tmp_path):
    cli_env = dict(os.environ)
    master = vault_env["vault"] / "wiki" / "index.md"
    real = master.read_bytes()
    master.unlink()
    master.symlink_to(tmp_path / "elsewhere-master.md")
    (tmp_path / "elsewhere-master.md").write_bytes(real)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


def test_local_state_inside_vault_refused(vault_env, tmp_path, monkeypatch):
    monkeypatch.setenv("K2B_LOCAL_STATE", str(vault_env["vault"] / "state-inside"))
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()
    assert not (vault_env["vault"] / "state-inside").exists()


# --- correction gaps: frontmatter validation --------------------------------

FRONTMATTER_UNCLOSED = "---\ntags: [work]\ndate: 2026-09-14\n# never closed\n\nbody\n"
FRONTMATTER_YAML_LIST = "---\n- a\n- b\n---\n# Body\n\ntext\n"
FRONTMATTER_MISSING_UP = (
    "---\ntags: [work]\ndate: 2026-09-14\ntype: work\norigin: keith\n"
    "---\n# Body\n\ntext\n"
)
FRONTMATTER_EMPTY_BODY = (
    "---\ntags: [work]\ndate: 2026-09-14\ntype: work\norigin: keith\n"
    'up: "[[MOC_SJM-Work]]"\n---\n'


)


@pytest.mark.parametrize(
    "bad",
    [
        FRONTMATTER_UNCLOSED,
        FRONTMATTER_YAML_LIST,
        FRONTMATTER_MISSING_UP,
        FRONTMATTER_EMPTY_BODY,
    ],
)
def test_invalid_frontmatter_refused(vault_env, tmp_path, bad):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", bad, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1
    assert not (vault_env["vault"] / "wiki" / "work" / "work_new-note.md").exists()


# --- correction gaps: backup keys and receipts -------------------------------

def _receipt_payload(receipt_path):
    return json.loads(receipt_path.read_text(encoding="utf-8"))


def test_receipt_backup_keys_exist_on_disk(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    receipt = _receipt_payload(receipts(vault_env)[-1])
    keys = [v for v in receipt.get("backups", {}).values() if v]
    assert keys, "receipt must record backup keys"
    for key in keys:
        assert (vault_env["state"] / key).exists(), "backup key missing: " + key


def test_partial_updates_same_receipt_and_retry_completes_it(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    capsys.readouterr()
    assert len(receipts(vault_env)) == 1, "partial must update the same operation receipt"
    receipt = _receipt_payload(receipts(vault_env)[0])
    assert receipt["result"] == "partial"
    assert receipt["saved"] == "wiki/work/work_new-note.md"

    mod.atomic_replace = real_replace
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "sum", "src",
    )
    assert rc == 0
    assert len(receipts(vault_env)) == 1, "retry must finish the same operation receipt"
    receipt = _receipt_payload(receipts(vault_env)[0])
    assert receipt["result"] in ("saved-local", "already-present")
    assert "[[work_new-note]]" in (work_dir / "index.md").read_text(encoding="utf-8")


# --- correction gaps: conflicts read-failure honesty --------------------------

def test_conflicts_unknown_on_unreadable_dir(vault_env):
    cli_env = dict(os.environ)
    secret = vault_env["vault"] / "wiki" / "work" / "restricted"
    secret.mkdir()
    (secret / "note.md").write_text("x", encoding="utf-8")
    secret.chmod(0)
    try:
        proc = run_cli(write_args(cli_env, "conflicts"), cli_env)
        assert proc.returncode == 0
        assert "unknown" in proc.stdout.decode("utf-8")
    finally:
        secret.chmod(0o700)


def test_conflicts_deadline_reports_unknown(vault_env, monkeypatch, capsys):
    mod = load_module()
    ticks = iter(range(0, 10**12, 10**9))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    rc = mod.main(["conflicts"])
    assert rc == 0
    assert "unknown" in capsys.readouterr().out


# --- correction pass: ten findings, regression-first -------------------------

import datetime as _dt


def _today():
    return _dt.date.today().isoformat()


NOTE_CONCEPT_V2 = """---
tags: [concept]
date: 2026-09-14
type: concept
origin: keith
up: "[[MOC_K2B-System]]"
---
# Agent Routing

Route tasks between agents. January rollout confirmed.
"""

NOTE_CONCEPT_NEW = """---
tags: [concept]
date: 2026-09-14
type: concept
origin: keith
up: "[[MOC_K2B-System]]"
---
# New Concept

Fresh concept note.
"""

NOTE_PERSON_CINDY_V2 = """---
tags: [person]
date: 2026-09-14
type: person
origin: keith
up: "[[MOC_TalentSignals]]"
---
# Cindy Zhao

Advisor v2.
"""

_PERSON_NOTE = """---
tags: [person]
date: 2026-06-01
type: person
origin: keith
up: "[[MOC_SJM-Work]]"
---
# {name}

Fixture person.
"""

PEOPLE_INDEX_MULTI = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki People Index

Last updated: 2026-09-01 | Entries: 4

## SJM Resorts

| Page | Role/Context | Updated |
|------|--------------|---------|
| [[person_Amy-Lin]] | SVP Treasury candidate | 2026-07-15 |
| [[person_Gerard-Walker]] | CIO, group technology | 2026-06-01 |

## TalentSignals / Agency at Scale

| Page | Role/Context | Updated |
|------|--------------|---------|
| [[person_Cindy-Zhao]] | TalentSignals advisor | 2026-08-15 |

## Other

| Page | Role/Context | Updated |
|------|--------------|---------|
| [[person_Random-Dev]] | External engineer | 2026-05-01 |
"""

CONCEPTS_INDEX = """---
tags: [index, wiki]
date: 2026-04-08
type: index
origin: k2b-generate
---
# Wiki Concepts Index

Last updated: 2026-09-01 | Entries: 5

## In Progress

| Page | Ship / Phase | Priority | Effort | Updated |
|------|--------------|----------|--------|---------|
| [[concept_agent-routing]] | Phase 2 rollout | high | 5d | 2026-09-01 |

## Next Up

| Page | Ship / Phase | Priority | Effort | Updated |
|------|--------------|----------|--------|---------|
| [[concept_vector-memory]] | Not started | medium | 3d | 2026-08-20 |

## Backlog

| Page | Priority | Effort | Impact | Updated |
|------|----------|--------|--------|---------|
| [[concept_old-idea]] | low | 1d | small | 2026-08-01 |

## Architecture Patterns

| Page | Summary | Updated |
|------|---------|---------|
| [[concept_pattern-cache]] | Cache invalidation pattern | 2026-07-01 |

## Shipped

| Page | Shipped | Notes |
|------|---------|-------|
| [[concept_done-thing]] | 2026-06-01 | Retired after rollout |
"""

CONCEPT_STEMS = (
    "concept_agent-routing",
    "concept_vector-memory",
    "concept_old-idea",
    "concept_pattern-cache",
    "concept_done-thing",
)


def _person_note(name):
    return _PERSON_NOTE.format(name=name)


def install_people_multi(env):
    vault = env["vault"]
    (vault / "wiki/people/index.md").write_text(PEOPLE_INDEX_MULTI, encoding="utf-8")
    for stem, name in (
        ("person_Gerard-Walker", "Gerard Walker"),
        ("person_Cindy-Zhao", "Cindy Zhao"),
        ("person_Random-Dev", "Random Dev"),
    ):
        (vault / "wiki/people" / (stem + ".md")).write_text(
            _person_note(name), encoding="utf-8"
        )


def install_concepts(env):
    vault = env["vault"]
    folder = vault / "wiki/concepts"
    folder.mkdir(exist_ok=True)
    (folder / "index.md").write_text(CONCEPTS_INDEX, encoding="utf-8")
    for stem in CONCEPT_STEMS:
        (folder / (stem + ".md")).write_text(NOTE_CONCEPT_NEW, encoding="utf-8")


def inproc_write(mod, tmp_path, env, note_rel, content, expected, summary, src="src",
                 extra=None):
    assert mod.vault_root() == str(env["vault"].resolve()), "test vault override lost"
    assert mod.state_root() == str(env["state"]), "test state override lost"
    content_file = draft(tmp_path, env, content)
    argv = [
        "write", "--path", note_rel,
        "--content-file", str(content_file),
        "--expected-sha256", expected,
        "--summary", summary,
        "--source-ref", src,
    ]
    if extra:
        argv.extend(extra)
    return mod.main(argv)


def inject_first_index_replace_failure(mod):
    """Make the first index.md atomic_replace raise, producing a real
    partial save (folder index replace precedes the master replace).
    Returns the real implementation so the caller can restore it; uses
    direct attribute restore instead of monkeypatch.undo(), which would
    also undo the vault_env fixture's environment overrides."""
    real = mod.atomic_replace
    state = {"hit": False}

    def fake(path, data, mode):
        if os.path.basename(path) == "index.md" and not state["hit"]:
            state["hit"] = True
            raise OSError("injected folder index write failure")
        return real(path, data, mode)

    mod.atomic_replace = fake
    return real


@pytest.mark.parametrize("existing", [False, True])
def test_direct_exact_original_retry(vault_env, tmp_path, capsys, existing):
    mod = load_module()
    rel = "wiki/work/work_kingdee-hris-recovery.md" if existing else "wiki/work/work_new-note.md"
    target = vault_env["vault"] / rel
    expected = sha256_bytes(target.read_bytes()) if existing else "missing"
    real = inject_first_index_replace_failure(mod)
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, expected, "sum") == 3
    capsys.readouterr()
    mod.atomic_replace = real
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, expected, "sum") == 0
    assert target.read_text() == NOTE_NEW
    assert _receipt_payload(receipts(vault_env)[0])["result"] in ("saved-local", "already-present")


def test_direct_escaped_concept_table_and_phase_preserved(vault_env, tmp_path):
    install_concepts(vault_env)
    index = vault_env["vault"] / "wiki/concepts/index.md"
    index.write_text(index.read_text().replace("[[concept_done-thing]]", "[[Shipped/concept_done-thing\\|Done]]"))
    target = vault_env["vault"] / "wiki/concepts/concept_agent-routing.md"
    before = index.read_text()
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/concepts/concept_agent-routing.md", NOTE_CONCEPT_V2, sha256_bytes(target.read_bytes()), "New summary", "src"), vault_env["env"])
    assert proc.returncode == 0, proc.stderr.decode()
    original_phase = next(line for line in before.splitlines() if "[[concept_agent-routing]]" in line).split("|")[2].strip()
    assert "| [[concept_agent-routing]] | " + original_phase + " |" in index.read_text()
    assert "[[Shipped/concept_done-thing\\|Done]]" in index.read_text()


@pytest.mark.parametrize("kind", ["note", "folder", "master"])
def test_direct_conflict_arriving_during_intent_blocks_note(vault_env, tmp_path, monkeypatch, kind):
    mod = load_module()
    real = mod.write_receipt_file
    directory = vault_env["vault"] / ("wiki" if kind == "master" else "wiki/work")
    stem = "work_new-note" if kind == "note" else "index"
    def late_conflict(key, payload):
        result = real(key, payload)
        (directory / (stem + ".sync-conflict-late.md")).write_text("other version")
        return result
    monkeypatch.setattr(mod, "write_receipt_file", late_conflict)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum") == 2
    assert not (vault_env["vault"] / "wiki/work/work_new-note.md").exists()


def test_direct_metadata_only_save_has_real_receipt(vault_env, tmp_path, capsys):
    mod = load_module()
    rel = "wiki/work/work_kingdee-hris-recovery.md"
    target = vault_env["vault"] / rel
    assert inproc_write(mod, tmp_path, vault_env, rel, target.read_text(), sha256_bytes(target.read_bytes()), "Revised summary") == 0
    result = json.loads(capsys.readouterr().out)
    assert (vault_env["state"] / result["receipt"]).is_file()
    assert "Revised summary" in (target.parent / "index.md").read_text()


def test_direct_effective_identity(vault_env, monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod.os, "getuid", lambda: 1)
    monkeypatch.setattr(mod.os, "geteuid", lambda: 2)
    from types import SimpleNamespace
    monkeypatch.setattr(mod.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_name="keithcheung" if uid == 2 else "unauthorized"))
    assert mod.validate_operator() == "keithcheung"


def test_direct_import_preserves_path_environment(vault_env, monkeypatch):
    mod = load_module()
    monkeypatch.setenv("K2B_VAULT_ROOT", "/unused-sentinel")
    mod.load_compile_index()
    assert os.environ["K2B_VAULT_ROOT"] == "/unused-sentinel"


def test_direct_nested_note_uses_exact_relative_index_row(vault_env, tmp_path):
    folder = vault_env["vault"] / "wiki/work/nested"
    folder.mkdir()
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/work/nested/note.md", NOTE_NEW, "missing", "Nested note", "src"), vault_env["env"])
    assert proc.returncode == 0, proc.stderr.decode()
    assert "[[nested/note]]" in (folder.parent / "index.md").read_text()
    assert "[[note]]" not in (folder.parent / "index.md").read_text()
    assert "| Entries: 3" in (folder.parent / "index.md").read_text()


def test_residual_alias_row_updated_once(vault_env, tmp_path):
    index = vault_env["vault"] / "wiki/work/index.md"
    index.write_text(index.read_text().replace("[[work_kingdee-hris-recovery]]", "[[work_kingdee-hris-recovery\\|Kingdee]]"))
    target = index.parent / "work_kingdee-hris-recovery.md"
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/work/work_kingdee-hris-recovery.md", NOTE_NEW, sha256_bytes(target.read_bytes()), "Updated alias", "src"), vault_env["env"])
    assert proc.returncode == 0, proc.stderr.decode()
    assert index.read_text().count("work_kingdee-hris-recovery") == 1
    assert "[[work_kingdee-hris-recovery\\|Kingdee]]" in index.read_text()
    assert "Updated alias" in index.read_text()


def test_residual_original_retry_external_change(vault_env, tmp_path, capsys):
    mod = load_module()
    real = inject_first_index_replace_failure(mod)
    rel = "wiki/work/work_new-note.md"
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, "missing", "sum") == 3
    mod.atomic_replace = real
    (vault_env["vault"] / rel).write_text("External change")
    capsys.readouterr()
    assert inproc_write(mod, tmp_path, vault_env, rel, NOTE_NEW, "missing", "sum") == 2
    assert "changed" in capsys.readouterr().err
    assert (vault_env["vault"] / rel).read_text() == "External change"


@pytest.mark.parametrize("after_folder", [False, True])
def test_residual_conflict_after_note_reports_manual_resolution(vault_env, tmp_path, monkeypatch, capsys, after_folder):
    mod = load_module()
    real = mod.atomic_replace
    def replace_and_conflict(path, data, mode):
        result = real(path, data, mode)
        trigger = "wiki/work/index.md" if after_folder else "wiki/work/work_new-note.md"
        if str(path).endswith(trigger):
            (vault_env["vault"] / "wiki/index.sync-conflict-late.md").write_text("Other version")
        return result
    monkeypatch.setattr(mod, "atomic_replace", replace_and_conflict)
    assert inproc_write(mod, tmp_path, vault_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum") == 2
    out = json.loads(capsys.readouterr().out)
    assert out["failed_step"] == "conflict"
    assert "resolve" in out["note"]
    receipt = _receipt_payload(receipts(vault_env)[0])
    assert receipt["failed_step"] == "conflict"
    assert (vault_env["vault"] / "wiki/work/work_new-note.md").read_text() == NOTE_NEW


def test_direct_duplicate_section_is_ambiguous(vault_env, tmp_path):
    install_people_multi(vault_env)
    index = vault_env["vault"] / "wiki/people/index.md"
    index.write_text(index.read_text().replace("## Other", "## SJM Resorts"))
    proc = run_cli(base_write_cmd(tmp_path, vault_env["env"], "wiki/people/person_New.md", _person_note("New"), "missing", "Role", "src") + ["--section", "SJM Resorts"], vault_env["env"])
    assert proc.returncode == 1
    assert not (index.parent / "person_New.md").exists()


# --- finding 1: case-insensitive macOS aliases --------------------------------

@pytest.mark.parametrize(
    "alias",
    [
        "wiki/work/INDEX.md",
        "wiki/work/Index.md",
        "wiki/work/SHIPPED/note.md",
        "wiki/work/shipped/note.md",
        "wiki/work/ShIpPeD/note.md",
        "WIKI/WORK/note.md",
        "Wiki/Work/note.md",
    ],
)
def test_case_insensitive_aliases_refused(vault_env, tmp_path, alias):
    if "shipped" in alias.casefold():
        # Pre-create the aliased directory so a case-blind check would write.
        (vault_env["vault"] / os.path.dirname(alias)).mkdir(
            parents=True, exist_ok=True
        )
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, alias, NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (alias, proc.stdout, proc.stderr)
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL, "index must be untouched for alias " + alias
    assert not (vault_env["vault"] / "wiki/work/note.md").exists()


# --- finding 2: owning-table selection over verified schemas ------------------

def test_ship_schema_existing_row_updates_only_owning_table(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/concepts/concept_agent-routing.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/concepts/concept_agent-routing.md",
        NOTE_CONCEPT_V2, sha256_bytes(target.read_bytes()),
        "Agent routing: phase 2 live", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/concepts/index.md").read_text(encoding="utf-8")
    today = _today()
    row = [l for l in index.splitlines() if "concept_agent-routing" in l]
    assert row == [
        "| [[concept_agent-routing]] | Phase 2 rollout | high | 5d | %s |"
        % today
    ], "unrelated Ship/Phase, Priority and Effort metadata is preserved"
    # Unrelated tables and sections are byte-preserved.
    assert "| [[concept_vector-memory]] | Not started | medium | 3d | 2026-08-20 |" in index
    assert "| [[concept_old-idea]] | low | 1d | small | 2026-08-01 |" in index
    assert "| [[concept_pattern-cache]] | Cache invalidation pattern | 2026-07-01 |" in index
    assert "| [[concept_done-thing]] | 2026-06-01 | Retired after rollout |" in index
    for heading in ("## In Progress", "## Next Up", "## Backlog",
                    "## Architecture Patterns", "## Shipped"):
        assert heading in index


def test_new_note_multi_table_index_requires_section(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/concepts/concept_new-thing.md", NOTE_CONCEPT_NEW, "missing",
        "Fresh concept", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki/concepts/concept_new-thing.md").exists()


def test_new_note_with_section_selects_owning_table(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_CONCEPT_NEW)
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/concepts/concept_new-thing.md",
        "--content-file", str(content_file),
        "--expected-sha256", "missing",
        "--summary", "Fresh concept",
        "--source-ref", "src",
        "--section", "Next Up",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/concepts/index.md").read_text(encoding="utf-8")
    today = _today()
    assert "| [[concept_new-thing]] | - | - | - | %s |" % today in index
    # Row landed under Next Up, nowhere else.
    sections = index.split("## ")
    next_up = [s for s in sections if s.startswith("Next Up")][0]
    in_progress = [s for s in sections if s.startswith("In Progress")][0]
    backlog = [s for s in sections if s.startswith("Backlog")][0]
    assert "concept_new-thing" in next_up
    assert "concept_new-thing" not in in_progress
    assert "concept_new-thing" not in backlog
    assert "## Shipped" in index
    assert "| [[concept_done-thing]] | 2026-06-01 | Retired after rollout |" in index


@pytest.mark.parametrize("section", ["Shipped", "No Such Section"])
def test_new_note_unknown_or_shipped_section_refused(vault_env, tmp_path, section):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_CONCEPT_NEW)
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/concepts/concept_new-thing.md",
        "--content-file", str(content_file),
        "--expected-sha256", "missing",
        "--summary", "Fresh concept",
        "--source-ref", "src",
        "--section", section,
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (section, proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki/concepts/concept_new-thing.md").exists()


def test_existing_row_in_shipped_table_is_not_saved(vault_env, tmp_path):
    install_concepts(vault_env)
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/concepts/concept_done-thing.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/concepts/concept_done-thing.md",
        NOTE_CONCEPT_V2, sha256_bytes(target.read_bytes()),
        "attempted shipped update", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert target.read_text(encoding="utf-8") == NOTE_CONCEPT_NEW


def test_people_multi_section_existing_row_updates_only_owning_section(vault_env, tmp_path):
    install_people_multi(vault_env)
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/people/person_Cindy-Zhao.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/people/person_Cindy-Zhao.md",
        NOTE_PERSON_CINDY_V2, sha256_bytes(target.read_bytes()),
        "TalentSignals advisor, expanded scope", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/people/index.md").read_text(encoding="utf-8")
    today = _today()
    assert (
        "| [[person_Cindy-Zhao]] | TalentSignals advisor, expanded scope | %s |" % today
        in index
    )
    # Other sections byte-preserved.
    assert "| [[person_Amy-Lin]] | SVP Treasury candidate | 2026-07-15 |" in index
    assert "| [[person_Gerard-Walker]] | CIO, group technology | 2026-06-01 |" in index
    assert "| [[person_Random-Dev]] | External engineer | 2026-05-01 |" in index
    assert index.count("## ") == 3  # three section headings


def test_people_new_note_requires_section_and_section_selects(vault_env, tmp_path):
    install_people_multi(vault_env)
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/people/person_New-Person.md", _person_note("New Person"), "missing",
        "New joiner", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)

    content_file = draft(tmp_path, cli_env, _person_note("New Person"))
    cmd2 = write_args(
        cli_env, "write",
        "--path", "wiki/people/person_New-Person.md",
        "--content-file", str(content_file),
        "--expected-sha256", "missing",
        "--summary", "New joiner",
        "--source-ref", "src",
        "--section", "SJM Resorts",
    )
    proc2 = run_cli(cmd2, cli_env)
    assert proc2.returncode == 0, proc2.stderr.decode()
    index = (vault_env["vault"] / "wiki/people/index.md").read_text(encoding="utf-8")
    today = _today()
    assert "| [[person_New-Person]] | New joiner | %s |" % today in index
    sjm = [s for s in index.split("## ") if s.startswith("SJM Resorts")][0]
    talent = [s for s in index.split("## ") if s.startswith("TalentSignals")][0]
    assert "person_New-Person" in sjm
    assert "person_New-Person" not in talent


def test_status_rejected_for_non_status_schema(vault_env, tmp_path):
    install_people_multi(vault_env)
    cli_env = dict(os.environ)
    content_file = draft(tmp_path, cli_env, NOTE_PERSON_CINDY_V2)
    target = vault_env["vault"] / "wiki/people/person_Cindy-Zhao.md"
    before = target.read_bytes()
    cmd = write_args(
        cli_env, "write",
        "--path", "wiki/people/person_Cindy-Zhao.md",
        "--content-file", str(content_file),
        "--expected-sha256", sha256_bytes(before),
        "--summary", "s", "--source-ref", "src",
        "--status", "active",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert target.read_bytes() == before
    assert (vault_env["vault"] / "wiki/people/index.md").read_text(
        encoding="utf-8"
    ) == PEOPLE_INDEX_MULTI


def test_first_compatible_table_is_not_owner_when_row_elsewhere(vault_env, tmp_path):
    # Two 4-column-compatible tables; the row lives in the second one.
    vault_env["vault"].joinpath("wiki/work/index.md").write_text(
        FOLDER_INDEX_4COL.replace(
            "| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery: vendor recovery through 2027 | 2026-09-01 |\n"
            "| [[work_sjm-ai-implementation]] | active | SJM AI implementation hub | 2026-09-01 |",
            "| [[work_sjm-ai-implementation]] | active | SJM AI implementation hub | 2026-09-01 |\n\n"
            "## On Hold\n\n"
            "| Page | Status | Summary | Updated |\n"
            "|------|--------|---------|---------|\n"
            "| [[work_kingdee-hris-recovery]] | active | Kingdee HRIS recovery: vendor recovery through 2027 | 2026-09-01 |",
        ),
        encoding="utf-8",
    )
    cli_env = dict(os.environ)
    target = vault_env["vault"] / "wiki/work/work_kingdee-hris-recovery.md"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(target.read_bytes()),
        "updated summary", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    hold = [s for s in index.split("## ") if s.startswith("On Hold")][0]
    main_rows = index.split("## ")[0]
    assert "updated summary" in hold
    assert "work_kingdee-hris-recovery" not in main_rows


# --- finding 4: backups hold fresh bytes; expected-absence recheck ------------

def test_folder_index_backup_holds_fresh_bytes_not_stale_plan(vault_env, tmp_path, monkeypatch):
    mod = load_module()
    index_path = vault_env["vault"] / "wiki/work/index.md"
    original = index_path.read_bytes()
    external = original.replace(b"Entries: 2", b"Entries: 9")
    # A concurrent edit lands after planning read the index but before the
    # mutation phase re-reads it.
    real_receipt = mod.write_receipt_file

    def fake_receipt(op_key, payload):
        if payload.get("result") == "in-progress":
            index_path.write_bytes(external)
        return real_receipt(op_key, payload)

    monkeypatch.setattr(mod, "write_receipt_file", fake_receipt)
    target = vault_env["vault"] / "wiki/work/work_kingdee-hris-recovery.md"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, sha256_bytes(target.read_bytes()),
        "fresh backup check", "src",
    )
    assert rc == 0
    receipt = _receipt_payload(receipts(vault_env)[-1])
    key = receipt["backups"]["folder_index_before"]
    assert (vault_env["state"] / key).read_bytes() == external, \
        "before copy must hold the fresh bytes being replaced, not the planning bytes"
    index_after = index_path.read_text(encoding="utf-8")
    assert "fresh backup check" in index_after
    assert "[[work_kingdee-hris-recovery]]" in index_after


def test_new_note_appearing_after_intent_is_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    target = vault_env["vault"] / "wiki/work/work_racer.md"
    real_receipt = mod.write_receipt_file

    def fake_receipt(op_key, payload):
        if payload.get("result") == "in-progress":
            target.write_text(NOTE_NEW, encoding="utf-8")
        return real_receipt(op_key, payload)

    monkeypatch.setattr(mod, "write_receipt_file", fake_receipt)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_racer.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 2, capsys.readouterr()
    assert target.read_text(encoding="utf-8") == NOTE_NEW, "appeared file must be preserved"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL


# --- finding 5: post-note-mutation failure boundary ---------------------------

def test_partial_on_folder_index_decode_failure(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    calls = {"n": 0}
    real_read = mod._read_index_bytes

    def fake_read(path, label):
        calls["n"] += 1
        if calls["n"] == 2:
            return b"\xff\xfe not utf-8"
        return real_read(path, label)

    monkeypatch.setattr(mod, "_read_index_bytes", fake_read)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["saved"] == "wiki/work/work_new-note.md"
    assert out["failed_step"] == "index"
    assert (vault_env["vault"] / "wiki/work/work_new-note.md").read_text(
        encoding="utf-8"
    ) == NOTE_NEW
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL


def test_partial_on_master_helper_systemexit(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_load = mod.load_compile_index

    class FlakyModule:
        def __init__(self, wrapped):
            self._w = wrapped
            self.calls = 0

        def __getattr__(self, name):
            return getattr(self._w, name)

        def rewrite_master_index(self):
            self.calls += 1
            if self.calls == 2:
                raise SystemExit(3)
            return self._w.rewrite_master_index()

    flaky = FlakyModule(real_load())
    monkeypatch.setattr(mod, "load_compile_index", lambda: flaky)
    master_before = (vault_env["vault"] / "wiki/index.md").read_bytes()
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["failed_step"] == "index"
    assert "3" in out["detail"]
    assert (vault_env["vault"] / "wiki/index.md").read_bytes() == master_before


def test_partial_on_after_copy_failure(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    calls = {"n": 0}
    real_copy = mod.durable_private_copy

    def fake_copy(subdir, data):
        calls["n"] += 1
        # New note copies: note_after(1), folder_before(2), folder_after(3).
        if calls["n"] == 3:
            raise mod.BackupError("injected after-copy failure")
        return real_copy(subdir, data)

    monkeypatch.setattr(mod, "durable_private_copy", fake_copy)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert (vault_env["vault"] / "wiki/work/work_new-note.md").read_text(
        encoding="utf-8"
    ) == NOTE_NEW, "note save is not rolled back"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL, "index not replaced when its after-copy failed"


def test_partial_on_final_receipt_failure(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_receipt = mod.write_receipt_file

    def fake_receipt(op_key, payload):
        if payload.get("result") == "saved-local":
            raise mod.BackupError("injected final receipt failure")
        return real_receipt(op_key, payload)

    monkeypatch.setattr(mod, "write_receipt_file", fake_receipt)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "partial"
    assert out["failed_step"] == "receipt"
    assert out["saved"] == "wiki/work/work_new-note.md"
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "partial"
    assert receipt["failed_step"] == "receipt"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) != FOLDER_INDEX_4COL, "indexes were mutated even though the final receipt failed"


# --- finding 6: exact-command retry repair boundaries -------------------------

def test_interrupted_intent_receipt_retry_completes(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = mod.atomic_replace
    calls = {"n": 0}

    def fake_replace(path, data, mode):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("injected note write failure")
        return real_replace(path, data, mode)

    monkeypatch.setattr(mod, "atomic_replace", fake_replace)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_late.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 1, "note-write failure is pre-mutation and distinct from partial"
    assert not (vault_env["vault"] / "wiki/work/work_late.md").exists()
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "in-progress"

    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_late.md", NOTE_NEW, "missing", "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    assert (vault_env["vault"] / "wiki/work/work_late.md").read_text(
        encoding="utf-8"
    ) == NOTE_NEW
    assert "[[work_late]]" in (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    )
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "saved-local"


def test_partial_then_external_change_refused(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    capsys.readouterr()
    saved_hash = sha256_bytes((work_dir / "work_new-note.md").read_bytes())

    # External edit lands before the retry; the saved-after hash is stale.
    (work_dir / "work_new-note.md").write_text(NOTE_KINGDEE_V1, encoding="utf-8")
    cli_env = dict(os.environ)
    cmd3 = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, saved_hash, "sum", "src")
    proc3 = run_cli(cmd3, cli_env)
    assert proc3.returncode == 2, (proc3.stdout, proc3.stderr)
    assert (work_dir / "work_new-note.md").read_text(encoding="utf-8") == NOTE_KINGDEE_V1
    receipt = _receipt_payload(receipts(vault_env)[-1])
    assert receipt["result"] == "partial"


def test_retry_preserves_original_backup_references(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 3
    capsys.readouterr()
    partial_receipt = _receipt_payload(receipts(vault_env)[-1])
    assert partial_receipt["backups"].get("note_after"), \
        "partial receipt must keep the operation's durable copies"
    note_after_key = partial_receipt["backups"]["note_after"]
    assert (vault_env["state"] / note_after_key).exists()

    mod.atomic_replace = real_replace
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "sum", "src",
    )
    assert rc == 0
    final_receipt = _receipt_payload(receipts(vault_env)[-1])
    assert final_receipt["backups"]["note_after"] == note_after_key, \
        "retry must preserve original backup references, not an empty replay map"


def test_different_operation_same_note_after_partial(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()
    real_replace = inject_first_index_replace_failure(mod)
    work_dir = vault_env["vault"] / "wiki/work"
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "first summary", "src",
    )
    assert rc == 3
    capsys.readouterr()
    receipt_files = receipts(vault_env)
    assert len(receipt_files) == 1
    first_key = receipt_files[0].name
    first_receipt = _receipt_payload(receipt_files[0])
    assert first_receipt["result"] == "partial"

    mod.atomic_replace = real_replace
    # Same bytes with different index metadata is a new operation. It must
    # update the index and issue its own real receipt, not skip the change.
    expected = sha256_bytes((work_dir / "work_new-note.md").read_bytes())
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "second summary", "src",
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert len(receipts(vault_env)) == 2
    assert (vault_env["state"] / out["receipt"]).is_file()
    assert "second summary" in (work_dir / "index.md").read_text()
    first_receipt = _receipt_payload(vault_env["state"] / "note-write-receipts" / first_key)
    assert first_receipt["result"] == "partial"

    # The unfinished original operation still repairs on its exact retry.
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected, "first summary", "src",
    )
    assert rc == 0
    index = (work_dir / "index.md").read_text(encoding="utf-8")
    assert "first summary" in index
    first_receipt = _receipt_payload(vault_env["state"] / "note-write-receipts" / first_key)
    assert first_receipt["result"] in ("saved-local", "already-present")


# --- finding 7: counts exclude unrelated conflict artifacts -------------------

def test_counts_exclude_unrelated_sync_conflict_artifacts(vault_env, tmp_path):
    cli_env = dict(os.environ)
    make_conflict(vault_env, "wiki/work", "work_sjm-ai-implementation")
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_unrelated.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    index = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    assert "| Entries: 3" in index, "folder count must exclude the unrelated .sync-conflict artifact"
    master = (vault_env["vault"] / "wiki/index.md").read_text(encoding="utf-8")
    assert "| [work/](work/index.md) | Work pages | 3 |" in master
    assert "**Total wiki pages: 4**" in master


# --- finding 8: effective UID resolution --------------------------------------

def test_env_user_spoofing_does_not_change_identity(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cli_env["LOGNAME"] = "root"
    cli_env["USER"] = "mallory"
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/work_kingdee-hris-recovery.md",
        NOTE_KINGDEE_V2, kingdee_expected(vault_env), "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, (
        "env LOGNAME/USER must not override the OS identity: " + proc.stderr.decode()
    )


# --- finding 9: conflict-list listdir failures are errors ----------------------

def test_missing_target_dir_conflict_scan_is_error(vault_env, tmp_path):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(
        tmp_path, cli_env,
        "wiki/work/nested/note.md", NOTE_NEW, "missing", "sum", "src",
    )
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert not (vault_env["vault"] / "wiki/work/nested").exists()
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL


def test_conflict_listdir_failure_is_error_not_empty_list(vault_env, tmp_path, monkeypatch, capsys):
    mod = load_module()

    def fake_listdir(directory):
        raise OSError("injected listdir failure")

    monkeypatch.setattr(mod.os, "listdir", fake_listdir)
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "injected listdir failure" in err
    assert not (vault_env["vault"] / "wiki/work/work_new-note.md").exists()
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == FOLDER_INDEX_4COL
    assert (vault_env["vault"] / "wiki/index.md").read_bytes() == MASTER_INDEX.encode()


# --- finding 10: completed replay is a true no-op across calendar days ---------

def test_completed_replay_on_later_day_is_noop(vault_env, tmp_path, monkeypatch, capsys):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index_after_first = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    receipt_path = receipts(vault_env)[-1]
    receipt_before = receipt_path.read_bytes()
    backups_before = sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    )

    mod = load_module()

    class FutureDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2027, 1, 2)

    monkeypatch.setattr(mod, "date", FutureDate)
    expected_now = sha256_bytes(
        (vault_env["vault"] / "wiki/work/work_new-note.md").read_bytes()
    )
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected_now, "sum", "src",
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == index_after_first, "completed replay must not rewrite index dates"
    assert receipt_path.read_bytes() == receipt_before, "completed replay must not rewrite the receipt"
    assert sorted(
        p.name for p in (vault_env["state"] / "note-write-backups").glob("*/*.md")
    ) == backups_before, "completed replay must not add backups"


def test_replay_without_receipt_records_current_save(vault_env, tmp_path, monkeypatch, capsys):
    cli_env = dict(os.environ)
    cmd = base_write_cmd(tmp_path, cli_env, "wiki/work/work_new-note.md", NOTE_NEW, "missing", "sum", "src")
    proc = run_cli(cmd, cli_env)
    assert proc.returncode == 0, proc.stderr.decode()
    index_after_first = (vault_env["vault"] / "wiki/work/index.md").read_text(encoding="utf-8")
    for r in receipts(vault_env):
        r.unlink()

    mod = load_module()
    expected_now = sha256_bytes(
        (vault_env["vault"] / "wiki/work/work_new-note.md").read_bytes()
    )
    rc = inproc_write(
        mod, tmp_path, vault_env,
        "wiki/work/work_new-note.md", NOTE_NEW, expected_now, "sum", "src",
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already-present"
    assert (vault_env["vault"] / "wiki/work/index.md").read_text(
        encoding="utf-8"
    ) == index_after_first
    assert len(receipts(vault_env)) == 1
    receipt = _receipt_payload(vault_env["state"] / out["receipt"])
    assert receipt["result"] == "already-present"
    assert (vault_env["state"] / receipt["backups"]["note_after"]).is_file()
