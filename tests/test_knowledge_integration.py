from __future__ import annotations

import hashlib
import json
import stat
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/lib"))
import automatic_memory as memory
import knowledge_integration as ki
from test_automatic_memory import _bundle
from test_vault_note_write import base_write_cmd, run_cli, vault_env as original_vault_env

WORK = "wiki/work/work_kingdee-hris-recovery.md"
PERSON = "wiki/people/person_Amy-Lin.md"


class PrivateTestEnvironment(dict):
    def __repr__(self):
        return "<synthetic vault fixture; process environment omitted>"


@pytest.fixture
def vault_env(original_vault_env):
    return PrivateTestEnvironment(original_vault_env)


def sha(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def snapshot(value="Synthetic review remains a proposal."):
    records = {}
    for key, text in (("fact.a", value), ("fact.b", "A second independent assertion.")):
        bundle = memory.validate_bundle(_bundle(key=key, value=text, quote=text))
        version = memory._version(bundle, bundle["items"][0])
        records[key] = {"kind": "fact", "versions": [version]}
    data = {"schema_version": 1, "records": records}
    memory._validate_memory_state(data)
    return data


def source(data, key="fact.a"):
    version = data["records"][key]["versions"][0]
    return {"kind": "memory_item", "item_id": version["item_id"],
        "source_identity": version["source_identity"],
        "evidence_sha256": memory._evidence_sha256(version["item_id"], version),
        "speaker": version["speaker_source"], "evidence_quote": version["evidence_quote"],
        "value": version["value"]}


def proposal(env, data=None, paths=(WORK,), *, leaf=False):
    data = data or snapshot()
    targets = []
    for rel in (() if leaf else paths):
        before = (env["vault"] / rel).read_text()
        after = before + "\n## Updates\n\n### 2026-10-26\n\nReported synthetic assertion; event completion remains unknown.\n"
        targets.append({"path": rel, "expected_sha256": sha(before),
            "replacement_content": after, "replacement_sha256": sha(after),
            "current_summary_change": "Report a synthetic assertion without upgrading its status",
            "dated_history": "2026-10-26: retain original statement and attribution",
            "relationships": []})
    return {"schema_version": 1, "assertion_status": "reported_assertion",
        "sources": [source(data)], "targets": targets,
        "disposition": "retained_leaf" if leaf else "integrated",
        "reason": "Retain source evidence" if leaf else "Integrate reviewed source evidence",
        "semantic_review": {"supervisor": "codex", "source_reference": "synthetic-conversation",
            "statement": "The active supervisor read the exact source; completion is unknown."}}


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_mode, sha(p.read_bytes()))
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setattr(ki, "_physical_operator", lambda: "keithmbpm2")


def validate(env, data, item):
    return ki.validate_proposal(item, vault_root=env["vault"], snapshot=data)


def save_targets(tmp_path, env, data, item, limit=None):
    checked = validate(env, data, item)
    outputs = []
    for target in item["targets"][:limit]:
        command = base_write_cmd(tmp_path, env, target["path"], target["replacement_content"],
            target["expected_sha256"], ki._summary_for_target(target), checked["writer_source_ref"])
        result = run_cli(command, env["env"])
        assert result.returncode == 0, result.stderr.decode()
        outputs.append(json.loads(result.stdout))
    return outputs


def record(env, data, item, outputs):
    return ki.record_integration(item, outputs, state_root=env["state"] / "digestion",
        vault_root=env["vault"], snapshot=data)


def test_validate_is_read_only_and_keeps_existing_uplinks(vault_env):
    data, env = snapshot(), vault_env
    before = tree(env["vault"]), tree(env["state"])
    result = validate(env, data, proposal(env, data))
    assert result["operation_id"].startswith("operation:")
    assert result["semantic_review"]["verified_by_validator"] is False
    assert before == (tree(env["vault"]), tree(env["state"]))


def test_leaf_replay_is_idempotent_and_covers_only_one_sibling(vault_env, home):
    env, data = vault_env, snapshot()
    assert source(data)["source_identity"] == source(data, "fact.b")["source_identity"]
    item = proposal(env, data, leaf=True)
    before = tree(env["vault"])
    result = record(env, data, item, [])
    path = Path(result["receipt_path"])
    receipt_bytes = path.read_bytes()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert record(env, data, item, [])["status"] == "already-recorded"
    assert path.read_bytes() == receipt_bytes
    assert before == tree(env["vault"])
    rows = {row["record_key"]: row for row in ki.build_queue(data, [json.loads(receipt_bytes)])}
    assert rows["fact.a"]["state"] == "retained_leaf"
    assert rows["fact.a"]["reason"] == item["reason"]
    assert rows["fact.b"]["state"] == "needs_review"
    changed = ki.build_queue(snapshot("A changed source version."), [json.loads(receipt_bytes)])
    assert all(row["state"] == "needs_review" for row in changed)


@pytest.mark.parametrize("field,value", [
    ("item_id", "invented"), ("source_identity", "invented"),
    ("evidence_sha256", "a" * 64), ("speaker", "assistant_confirmed"),
    ("evidence_quote", "Invented quote"), ("value", "Invented value"),
])
def test_source_binding_cannot_be_forged(vault_env, field, value):
    data = snapshot()
    item = proposal(vault_env, data)
    item["sources"][0][field] = value
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


def test_typed_completion_upgrade_rejected_but_real_quoted_words_allowed(vault_env):
    data = snapshot("Keith reported that the draft was approved, with sending unverified.")
    item = proposal(vault_env, data)
    validate(vault_env, data, item)
    item["assertion_status"] = "completed"
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)
    item["assertion_status"] = "reported_assertion"
    item["event_state"] = "completed"
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


@pytest.mark.parametrize("path", ["../escape.md", "wiki/context/control.md", "wiki/work/index.md", "wiki/work/new.md"])
def test_unsafe_or_missing_target_rejected(vault_env, path):
    data = snapshot()
    item = proposal(vault_env, data)
    item["targets"][0]["path"] = path
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


def test_changed_source_and_stale_note_hash_refused(vault_env):
    data = snapshot()
    item = proposal(vault_env, data)
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, snapshot("Changed source"), item)
    (vault_env["vault"] / WORK).write_text("Unexpected target edit")
    with pytest.raises(ki.IntegrationConflict):
        validate(vault_env, data, item)


@pytest.mark.parametrize("suffix", ["[[missing_note]]", "[bad](../escape.md)", "[bad](javascript:run)"])
def test_new_missing_or_unsafe_link_rejected(vault_env, suffix):
    data = snapshot()
    item = proposal(vault_env, data)
    target = item["targets"][0]
    target["replacement_content"] += suffix
    target["replacement_sha256"] = sha(target["replacement_content"])
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


def test_ambiguous_link_rejected(vault_env):
    for folder in ("wiki/work", "wiki/people"):
        (vault_env["vault"] / folder / "duplicate.md").write_text("Synthetic note")
    data = snapshot()
    item = proposal(vault_env, data)
    target = item["targets"][0]
    target["replacement_content"] += "\n[[duplicate]]\n"
    target["replacement_sha256"] = sha(target["replacement_content"])
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


@pytest.mark.parametrize("obstacle", ["symlink", "conflict"])
def test_target_symlink_or_real_conflict_refused(vault_env, obstacle):
    data = snapshot()
    item = proposal(vault_env, data)
    path = vault_env["vault"] / WORK
    if obstacle == "symlink":
        copy = vault_env["state"] / "outside.md"
        copy.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(copy)
    else:
        path.with_name(path.stem + ".sync-conflict-20261026-000000-device.md").write_text("Preserve me")
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


def test_real_two_note_save_records_once(tmp_path, vault_env, home):
    env, data = vault_env, snapshot()
    item = proposal(env, data, paths=(WORK, PERSON))
    outputs = save_targets(tmp_path, env, data, item)
    result = record(env, data, item, outputs)
    path = Path(result["receipt_path"])
    payload, before = path.read_bytes(), tree(env["vault"])
    receipt = json.loads(payload)
    assert len(receipt["targets"]) == 2
    assert all(row["writer_receipt_id"].startswith("note-write-receipts/") for row in receipt["targets"])
    assert record(env, data, item, outputs)["status"] == "already-recorded"
    assert record(env, data, item, [])["status"] == "already-recorded"
    assert path.read_bytes() == payload
    assert tree(env["vault"]) == before
    assert [row["state"] for row in ki.build_queue(data, [receipt])].count("integrated") == 1
    for mutate in ("reason", "operation_id", "source_ref"):
        forged = deepcopy(receipt)
        forged[mutate] = "forged"
        assert all(row["state"] == "needs_review" for row in ki.build_queue(data, [forged]))


def test_partial_multi_note_save_cannot_get_completion(tmp_path, vault_env, home):
    env, data = vault_env, snapshot()
    item = proposal(env, data, paths=(WORK, PERSON))
    outputs = save_targets(tmp_path, env, data, item, limit=1)
    with pytest.raises(ki.IntegrationError):
        record(env, data, item, outputs)
    assert not list((env["state"] / "digestion").rglob("operation:*.json"))
    assert (env["vault"] / WORK).read_text() == item["targets"][0]["replacement_content"]
    assert sha((env["vault"] / PERSON).read_bytes()) == item["targets"][1]["expected_sha256"]
    second = deepcopy(item)
    second["targets"] = item["targets"][1:]
    target = item["targets"][1]
    checked = validate(env, data, item)
    command = base_write_cmd(tmp_path, env, PERSON, target["replacement_content"],
        target["expected_sha256"], ki._summary_for_target(target), checked["writer_source_ref"])
    saved = run_cli(command, env["env"])
    assert saved.returncode == 0, saved.stderr.decode()
    outputs.append(json.loads(saved.stdout))
    assert record(env, data, item, outputs)["status"] == "recorded"


@pytest.mark.parametrize("damage", ["caller-only", "duplicate", "source_ref", "operation", "before", "after", "drift"])
def test_fake_or_corrupt_save_evidence_cannot_complete(tmp_path, vault_env, home, damage):
    env, data = vault_env, snapshot()
    item = proposal(env, data)
    outputs = save_targets(tmp_path, env, data, item)
    receipt_path = env["state"] / outputs[0]["receipt"]
    durable = json.loads(receipt_path.read_text())
    if damage == "caller-only":
        receipt_path.unlink()
    elif damage == "duplicate":
        outputs *= 2
    elif damage in {"source_ref", "operation"}:
        durable[damage] = "wrong"
        receipt_path.write_text(json.dumps(durable))
    elif damage in {"before", "after"}:
        (env["state"] / durable["backups"]["note_" + damage]).write_text("Corrupt recovery copy")
    else:
        (env["vault"] / WORK).write_text("Concurrent different content")
    with pytest.raises(ki.IntegrationError):
        record(env, data, item, outputs)
    assert not list((env["state"] / "digestion").rglob("operation:*.json"))


def test_tampered_existing_integration_receipt_refused(vault_env, home):
    data = snapshot()
    item = proposal(vault_env, data, leaf=True)
    result = record(vault_env, data, item, [])
    path = Path(result["receipt_path"])
    payload = json.loads(path.read_text())
    payload["reason"] = "Tampered"
    path.write_text(json.dumps(payload))
    with pytest.raises(ki.IntegrationError):
        record(vault_env, data, item, [])


def test_sjm_cannot_record_under_any_override(vault_env, monkeypatch):
    data = snapshot()
    item = proposal(vault_env, data, leaf=True)
    monkeypatch.setattr(ki, "_physical_operator", lambda: "keithcheung")
    with pytest.raises(ki.IntegrationError, match="Home-only"):
        record(vault_env, data, item, [])
    assert not (vault_env["state"] / "digestion").exists()


@pytest.mark.parametrize("location", ["vault", "native", "native-parent", "symlink"])
def test_output_state_location_is_fenced(vault_env, home, monkeypatch, tmp_path, location):
    native = tmp_path / "private/native"
    monkeypatch.setattr(ki, "_native_memory_root", lambda: native)
    root = {"vault": vault_env["vault"] / "bad", "native": native / "bad",
        "native-parent": native.parent, "symlink": tmp_path / "linked"}[location]
    if location == "symlink":
        root.symlink_to(vault_env["state"], target_is_directory=True)
    with pytest.raises(ki.IntegrationError):
        ki.record_integration(proposal(vault_env, leaf=True), [], state_root=root,
            vault_root=vault_env["vault"], snapshot=snapshot())


@pytest.mark.parametrize("compiled", ["false", "skipped", "deferred"])
def test_raw_capture_validation_immutable_and_legacy_aware(vault_env, compiled):
    env, data = vault_env, snapshot()
    raw = env["vault"] / "raw/research/source.md"
    raw.parent.mkdir(parents=True)
    payload = f"---\ntype: research\ncompiled: {compiled}\n---\nExact synthetic quotation.\n"
    raw.write_text(payload)
    item = proposal(env, data, leaf=True)
    item["sources"] = [{"kind": "raw_capture", "raw_path": "raw/research/source.md",
        "raw_sha256": sha(payload), "classification": "human_capture",
        "speaker": "keith", "evidence_quote": "Exact synthetic quotation."}]
    before = raw.read_bytes()
    if compiled == "false":
        validate(env, data, item)
        item["sources"][0]["evidence_quote"] = "Invented quotation"
        with pytest.raises(ki.IntegrationError):
            validate(env, data, item)
        item["sources"][0]["raw_sha256"] = "0" * 64
        with pytest.raises(ki.IntegrationError):
            validate(env, data, item)
    else:
        with pytest.raises(ki.IntegrationError):
            validate(env, data, item)
    assert raw.read_bytes() == before


def test_queue_cli_defaults_to_real_private_receipts(tmp_path, vault_env, home, capsys):
    env, data = vault_env, snapshot()
    item = proposal(env, data, leaf=True)
    result = record(env, data, item, [])
    published = env["vault"] / "System/memory/automatic-memory-current.json"
    published.parent.mkdir(parents=True)
    published.write_text(json.dumps(data))
    before = tree(env["vault"]), tree(env["state"])
    args = ["queue", "--vault-root", str(env["vault"]), "--state-root", str(env["state"] / "digestion")]
    assert ki.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["receipt_health"]["loaded"] == 1
    assert output["queue"][0]["state"] in {"retained_leaf", "needs_review"}
    assert sum(row["state"] == "retained_leaf" for row in output["queue"]) == 1
    assert before == (tree(env["vault"]), tree(env["state"]))
    Path(result["receipt_path"]).with_name("malformed.json").write_text("{")
    assert ki.main(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "partial"
    assert output["receipt_health"]["rejected"] == 1


def test_queue_and_validate_do_not_create_state(tmp_path, vault_env, capsys):
    env, data = vault_env, snapshot()
    snap, prop, absent = tmp_path / "snapshot.json", tmp_path / "proposal.json", tmp_path / "absent"
    snap.write_text(json.dumps(data))
    prop.write_text(json.dumps(proposal(env, data)))
    before = tree(env["vault"]), tree(env["state"])
    assert ki.main(["queue", "--snapshot", str(snap), "--state-root", str(absent)]) == 0
    assert not absent.exists()
    capsys.readouterr()
    assert ki.main(["validate", "--snapshot", str(snap), "--proposal", str(prop),
        "--vault-root", str(env["vault"])]) == 0
    assert before == (tree(env["vault"]), tree(env["state"]))


def test_deep_json_cli_has_concise_failure(tmp_path, capsys):
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 2000 + "0" + "]" * 2000)
    assert ki.main(["queue", "--snapshot", str(deep)]) != 0
    result = capsys.readouterr()
    assert "Traceback" not in result.err
    assert "not readable JSON" in result.err


def test_sjm_queue_does_not_claim_home_ledger_coverage(tmp_path, monkeypatch, capsys):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(snapshot()))
    monkeypatch.setattr(ki, "_physical_operator", lambda: "keithcheung")
    assert ki.main(["queue", "--snapshot", str(path), "--state-root", str(tmp_path / "absent")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "partial"
    assert result["scope"]["receipt_authority"] == "unverified_on_this_host"
    assert result["scope"]["global_completeness"] == "unmeasured"
    assert any("not global pending counts" in line for line in result["limitations"])


def test_writer_summary_mismatch_is_refused(tmp_path, vault_env, home):
    env, data = vault_env, snapshot()
    item = proposal(env, data)
    checked = validate(env, data, item)
    target = item["targets"][0]
    command = base_write_cmd(tmp_path, env, WORK, target["replacement_content"],
        target["expected_sha256"], "Unreviewed summary", checked["writer_source_ref"])
    saved = run_cli(command, env["env"])
    assert saved.returncode == 0
    with pytest.raises(ki.IntegrationError, match="summary disagrees"):
        record(env, data, item, [json.loads(saved.stdout)])


def test_noop_save_and_single_stdout_file(tmp_path, vault_env, home):
    env, data = vault_env, snapshot()
    item = proposal(env, data)
    target = item["targets"][0]
    target["replacement_content"] = (env["vault"] / WORK).read_text()
    target["replacement_sha256"] = target["expected_sha256"]
    outputs = save_targets(tmp_path, env, data, item)
    file = tmp_path / "writer.json"
    file.write_text(json.dumps(outputs[0]))
    assert ki._load_receipts_list(file) == outputs
    assert record(env, data, item, outputs)["status"] == "recorded"
    (env["state"] / outputs[0]["receipt"]).unlink()
    with pytest.raises(ki.IntegrationError):
        record(env, data, item, outputs)


@pytest.mark.parametrize("addition", [
    "\n![[Assets/images/example.png]]\n",
    "\n![chart](Assets/images/example.png)\n",
    "\n```md\n[[invented-example]]\n[x](../escape.md)\n```\n",
    "\nThe literal syntax is `[[invented-example]]`.\n",
])
def test_assets_and_literal_code_links(tmp_path, vault_env, addition):
    env, data = vault_env, snapshot()
    asset = env["vault"] / "Assets/images/example.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"synthetic image fixture")
    item = proposal(env, data)
    item["targets"][0]["replacement_content"] += addition
    item["targets"][0]["replacement_sha256"] = sha(item["targets"][0]["replacement_content"])
    validate(env, data, item)


def test_three_targets_boundary(vault_env):
    env, data = vault_env, snapshot()
    third = "wiki/work/work_sjm-ai-implementation.md"
    item = proposal(env, data, paths=(WORK, PERSON, third))
    assert len(validate(env, data, item)["targets"]) == 3
    item["targets"].append(deepcopy(item["targets"][0]))
    with pytest.raises(ki.IntegrationError):
        validate(env, data, item)


def test_overlap_guard_and_order_independent_queue(vault_env, home, tmp_path, capsys):
    env, data = vault_env, snapshot()
    item = proposal(env, data, leaf=True)
    result = record(env, data, item, [])
    first = json.loads(Path(result["receipt_path"]).read_text())
    second = deepcopy(first)
    second["reason"] = "A different proposed disposition of identical evidence"
    second["operation_id"] = ki._operation_id(sources=second["sources"],
        disposition=second["disposition"], reason=second["reason"], targets=[])
    second["source_ref"] = "k2b-digest:" + second["operation_id"]
    forward = ki.build_queue(data, [first, second])
    assert forward == ki.build_queue(data, [second, first])
    row = next(row for row in forward if row["record_key"] == "fact.a")
    assert row["state"] == "blocked" and "overlapping" in row["reason"]
    item["reason"] = second["reason"]
    with pytest.raises(ki.IntegrationError, match="already covered"):
        record(env, data, item, [])
    snap, prop = tmp_path / "snapshot.json", tmp_path / "proposal.json"
    snap.write_text(json.dumps(data)); prop.write_text(json.dumps(item))
    before = tree(env["vault"]), tree(env["state"])
    assert ki.main(["validate", "--proposal", str(prop), "--snapshot", str(snap),
        "--vault-root", str(env["vault"]), "--state-root", str(env["state"] / "digestion")]) == 1
    assert "already covered" in capsys.readouterr().err
    assert before == (tree(env["vault"]), tree(env["state"]))


@pytest.mark.parametrize("addition", [
    '\n[example][ref]\n[ref]: javascript:alert(1)\n',
    '\n[example][ref]\n[ref]: wiki/work/invented.md\n',
    '\n<a href="javascript:alert(1)">unsafe</a>\n',
    '\n<a href="wiki/work/invented.md">missing</a>\n',
])
def test_reference_and_html_link_targets_are_validated(vault_env, addition):
    data = snapshot()
    item = proposal(vault_env, data)
    target = item["targets"][0]
    target["replacement_content"] += addition
    target["replacement_sha256"] = sha(target["replacement_content"])
    with pytest.raises(ki.IntegrationError):
        validate(vault_env, data, item)


def test_footnotes_and_case_insensitive_existing_links(vault_env):
    data = snapshot()
    item = proposal(vault_env, data)
    target = item["targets"][0]
    target["replacement_content"] += '\nA supported assertion[^1].\n\n[^1]: Explanation text.\n\n[[PERSON_AMY-LIN]]\n'
    target["replacement_sha256"] = sha(target["replacement_content"])
    validate(vault_env, data, item)
