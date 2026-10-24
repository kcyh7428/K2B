"""Exercise the existing save-to-fresh-recall contract through real CLIs."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from test_vault_note_write import vault_env, base_write_cmd  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_decision_saved_locally_is_source_cited_in_separate_reader(vault_env, tmp_path):
    # A simulated Kimi Work save uses exactly the same writer as Codex.
    # Assistant UI execution is recorded separately, never inferred here.
    env = vault_env["env"]
    note = "wiki/work/work_running-fixture.md"
    content = '''---
tags: [work, running-fixture]
date: 2026-10-24
type: work
origin: keith
up: "[[MOC_SJM-Work]]"
---
# Running fixture

## Earlier decision
On 23 October the fictional route color was amber.

## Current decision
On 24 October Keith changed the fictional route color to violet.
Source: fictional Kimi Work parity conversation, decision 2.
'''
    command = base_write_cmd(tmp_path, vault_env, note, content, "missing",
                             "Fictional running decision", "fixture:kimi-work:decision-2")
    saved = subprocess.run(command, env=env, text=True, capture_output=True, timeout=15)
    assert saved.returncode == 0, saved.stderr + saved.stdout
    receipt = json.loads(saved.stdout)
    assert receipt["result"] == "saved-local", receipt
    assert "synchronization unverified" in receipt["note"]
    assert (vault_env["vault"] / note).read_text() == content
    assert "work_running-fixture" in (vault_env["vault"] / "wiki/work/index.md").read_text()
    packet_file = tmp_path / "private-packet.json"
    read = subprocess.run([
        sys.executable, str(ROOT / "scripts/k2b-context.py"), "gather",
        "--query", "continue running fixture", "--topic-note", note,
        "--vault", str(vault_env["vault"]), "--no-provisional-memory",
        "--output", str(packet_file),
    ], env=env, text=True, capture_output=True, timeout=15)
    assert read.returncode == 0, read.stderr + read.stdout
    packet = json.loads(packet_file.read_text())
    assert any("violet" in e["quote"] for e in packet["evidence"])
    source = next(s for s in packet["sources"] if s.get("relative_path") == note)
    assert source["content_hash"] == hashlib.sha256(content.encode()).hexdigest()
    assert not any("Treasury candidate" in e["quote"] for e in packet["evidence"])
    assert any(e.get("locator") for e in packet["evidence"] if "violet" in e["quote"])
