from __future__ import annotations

import fcntl
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

import automatic_memory  # noqa: E402


def _bundle(
    *,
    host_id: str = "sjm",
    session_id: str = "session-sjm-1",
    completed_at: str = "2026-09-09T10:00:00+08:00",
    cursor: str = "turn:0001",
    key: str = "fact.office",
    kind: str = "fact",
    value: str = "The SJM office is in Macau.",
    event_id: str = "event-user-1",
    quote: str = "The SJM office is in Macau.",
    speaker_source: str = "keith",
    origin: str = "interactive",
    thread_source: str = "user",
    scope: str = "K2B",
) -> dict:
    return {
        "schema_version": 1,
        "scope": scope,
        "review_state": "reviewed",
        "redaction": {
            "status": "redacted",
            "raw_dialogue_included": False,
        },
        "source": {
            "host_id": host_id,
            "session_id": session_id,
            "completed_cursor": cursor,
            "completed_at": completed_at,
            "source_hash": "a" * 64,
            "transcript_hash": "b" * 64,
            "origin": origin,
            "source_kind": "codex_session",
            "thread_source": thread_source,
        },
        "items": [
            {
                "key": key,
                "kind": kind,
                "value": value,
                "speaker_source": speaker_source,
                "evidence_event_id": event_id,
                "evidence_quote": quote,
            }
        ],
    }


def test_offline_outbox_survives_restart_and_drains_after_bounded_retries(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    queued = automatic_memory.enqueue_bundle(
        outbox,
        _bundle(),
        max_attempts=4,
        now="2026-09-09T10:01:00+08:00",
    )

    offline_1 = automatic_memory.record_delivery_attempt(
        outbox,
        queued["delivery_id"],
        outcome="offline",
        error_code="home_unreachable",
        now="2026-09-09T10:02:00+08:00",
    )
    assert offline_1["status"] == "offline"
    assert offline_1["attempt_count"] == 1
    assert offline_1["retry_after_seconds"] == 60

    # A new API call is a process-restart simulation: all state is reloaded
    # from the durable envelope rather than kept in an object.
    reloaded = automatic_memory.list_deliverable(
        outbox, now="2026-09-09T10:03:00+08:00"
    )
    assert [item["delivery_id"] for item in reloaded] == [queued["delivery_id"]]
    assert reloaded[0]["attempt_count"] == 1

    automatic_memory.record_delivery_attempt(
        outbox,
        queued["delivery_id"],
        outcome="offline",
        error_code="home_unreachable",
        now="2026-09-11T10:02:00+08:00",
    )
    delivered = automatic_memory.record_delivery_attempt(
        outbox,
        queued["delivery_id"],
        outcome="accepted",
        now="2026-09-11T10:03:00+08:00",
    )

    assert delivered["status"] == "accepted"
    assert delivered["attempt_count"] == 3
    assert delivered["home_reconciliation"] == "pending"
    assert automatic_memory.list_deliverable(outbox) == []


@pytest.mark.parametrize(
    ("now", "eligible"),
    [
        ("2026-09-09T10:02:59+08:00", False),
        ("2026-09-09T02:03:00+00:00", True),
        ("2026-09-09T10:03:01+08:00", True),
    ],
)
def test_retry_backoff_is_enforced_before_at_and_after_due_time(
    tmp_path: Path, now: str, eligible: bool
) -> None:
    outbox = tmp_path / "sjm-state"
    envelope = automatic_memory.enqueue_bundle(
        outbox, _bundle(), now="2026-09-09T10:00:00+08:00"
    )
    automatic_memory.record_delivery_attempt(
        outbox,
        envelope["delivery_id"],
        outcome="offline",
        error_code="home_unreachable",
        now="2026-09-09T10:02:00+08:00",
    )

    deliverable = automatic_memory.list_deliverable(outbox, now=now)

    assert bool(deliverable) is eligible


def test_pending_delivery_remains_eligible_with_injected_clock(tmp_path: Path) -> None:
    envelope = automatic_memory.enqueue_bundle(
        tmp_path, _bundle(), now="2026-09-09T10:00:00+08:00"
    )

    deliverable = automatic_memory.list_deliverable(
        tmp_path, now="2026-09-01T00:00:00+00:00"
    )

    assert [item["delivery_id"] for item in deliverable] == [
        envelope["delivery_id"]
    ]


@pytest.mark.parametrize(
    "now",
    [True, 7, "", "not-a-timestamp", "2026-09-09T10:03:00"],
)
def test_deliverable_clock_drift_is_a_controlled_validation_error(
    tmp_path: Path, now: object
) -> None:
    automatic_memory.enqueue_bundle(tmp_path, _bundle())

    with pytest.raises(automatic_memory.ValidationError, match="selection timestamp"):
        automatic_memory.list_deliverable(tmp_path, now=now)  # type: ignore[arg-type]


def test_retry_budget_exhaustion_is_explicit_and_not_deliverable(tmp_path: Path) -> None:
    outbox = tmp_path / "sjm-state"
    envelope = automatic_memory.enqueue_bundle(outbox, _bundle(), max_attempts=2)

    automatic_memory.record_delivery_attempt(
        outbox,
        envelope["delivery_id"],
        outcome="failed",
        error_code="malformed_remote_response",
    )
    exhausted = automatic_memory.record_delivery_attempt(
        outbox,
        envelope["delivery_id"],
        outcome="failed",
        error_code="malformed_remote_response",
    )

    assert exhausted["status"] == "exhausted"
    assert exhausted["attempt_count"] == 2
    assert exhausted["last_error"] == "malformed_remote_response"
    assert automatic_memory.list_deliverable(outbox) == []


def test_home_acceptance_replay_after_crash_is_idempotent_and_not_reconciled(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    home = tmp_path / "home-state"
    envelope = automatic_memory.enqueue_bundle(outbox, _bundle())

    first = automatic_memory.accept_home_envelope(home, envelope)
    second = automatic_memory.accept_home_envelope(home, envelope)

    assert first == {
        "delivery_id": envelope["delivery_id"],
        "content_id": envelope["content_id"],
        "status": "accepted_not_reconciled",
        "duplicate": False,
    }
    assert second == {**first, "duplicate": True}
    assert len(list((home / "inbox").glob("*.json"))) == 1
    assert not (home / "memory.json").exists()

    acknowledged = automatic_memory.record_delivery_attempt(
        outbox, envelope["delivery_id"], outcome="accepted"
    )
    assert acknowledged["home_reconciliation"] == "pending"


def test_home_rejects_first_exhausted_acceptance_but_preserves_prior_replay(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-state"
    envelope = automatic_memory.enqueue_bundle(
        source, _bundle(), max_attempts=1
    )
    home = tmp_path / "home-state"
    first = automatic_memory.accept_home_envelope(home, envelope)
    exhausted = automatic_memory.record_delivery_attempt(
        source,
        envelope["delivery_id"],
        outcome="offline",
        error_code="home_unreachable",
        now="2026-09-09T10:03:00+08:00",
    )
    assert exhausted["status"] == "exhausted"

    replay = automatic_memory.accept_home_envelope(home, exhausted)
    assert replay == {**first, "duplicate": True}

    fresh_home = tmp_path / "fresh-home"
    with pytest.raises(automatic_memory.ValidationError, match="exhausted"):
        automatic_memory.accept_home_envelope(fresh_home, exhausted)
    assert not list((fresh_home / "inbox").glob("*.json"))


def test_home_rejects_tampered_envelope_without_acceptance_artifact(tmp_path: Path) -> None:
    outbox = tmp_path / "sjm-state"
    home = tmp_path / "home-state"
    envelope = automatic_memory.enqueue_bundle(outbox, _bundle())
    tampered = deepcopy(envelope)
    tampered["bundle"]["items"][0]["value"] = "Tampered after identity creation"

    with pytest.raises(automatic_memory.ValidationError, match="content identity"):
        automatic_memory.accept_home_envelope(home, tampered)

    assert not list((home / "inbox").glob("*.json"))
    assert not list((home / "acceptance").glob("*.json"))


@pytest.mark.parametrize(
    ("origin", "thread_source"),
    [("worker", "user"), ("interactive", "agent_created_thread"), ("imported", "user")],
)
def test_worker_and_import_origin_bundles_are_rejected(
    tmp_path: Path, origin: str, thread_source: str
) -> None:
    with pytest.raises(automatic_memory.ValidationError, match="source origin"):
        automatic_memory.enqueue_bundle(
            tmp_path,
            _bundle(origin=origin, thread_source=thread_source),
        )


def test_scope_and_redaction_contract_reject_unrelated_or_raw_sources(tmp_path: Path) -> None:
    unrelated = _bundle(scope="Service Motion")
    with pytest.raises(automatic_memory.ValidationError, match="scope"):
        automatic_memory.enqueue_bundle(tmp_path, unrelated)

    raw = _bundle()
    raw["redaction"]["raw_dialogue_included"] = True
    with pytest.raises(automatic_memory.ValidationError, match="redacted"):
        automatic_memory.enqueue_bundle(tmp_path, raw)


def test_older_offline_sjm_fact_cannot_override_newer_home_correction(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    newer_home = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="session-home-new",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:0009",
            value="The SJM office is in Hengqin.",
            event_id="event-home-correction",
            quote="Correction: the SJM office is in Hengqin.",
        )
    )
    older_sjm = automatic_memory.make_envelope(
        _bundle(completed_at="2026-09-09T12:00:00+08:00")
    )

    automatic_memory.apply_reviewed_envelope(memory, newer_home)
    automatic_memory.apply_reviewed_envelope(memory, older_sjm)
    recalled = automatic_memory.recall(memory, "fact.office")

    assert recalled["status"] == "current"
    assert recalled["value"] == "The SJM office is in Hengqin."
    assert recalled["citation"]["host_id"] == "home"
    assert recalled["citation"]["event_id"] == "event-home-correction"
    assert recalled["history"] == [
        {
            "value": "The SJM office is in Macau.",
            "status": "superseded",
            "citation": {
                "host_id": "sjm",
                "session_id": "session-sjm-1",
                "completed_cursor": "turn:0001",
                "completed_at": "2026-09-09T12:00:00+08:00",
                "event_id": "event-user-1",
                "source_hash": "a" * 64,
                "transcript_hash": "b" * 64,
            },
        }
    ]


def test_equal_time_contradictions_remain_unresolved_with_both_citations(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    a = automatic_memory.make_envelope(_bundle(value="Use option A."))
    b = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="session-home-2",
            value="Use option B.",
            event_id="event-user-2",
            quote="Use option B.",
        )
    )

    automatic_memory.apply_reviewed_envelope(memory, b)
    automatic_memory.apply_reviewed_envelope(memory, a)
    recalled = automatic_memory.recall(memory, "fact.office")

    assert recalled["status"] == "unresolved"
    assert recalled["value"] is None
    assert [item["value"] for item in recalled["alternatives"]] == [
        "Use option B.",
        "Use option A.",
    ]
    assert {item["citation"]["host_id"] for item in recalled["alternatives"]} == {
        "home",
        "sjm",
    }


@pytest.mark.parametrize(
    ("kind", "key", "value"),
    [
        ("preference", "preference.writing", "Use plain English."),
        ("commitment", "commitment.follow_up", "Follow up with Amy on Friday."),
        ("decision", "decision.runner", "Use native Codex Automations first."),
    ],
)
def test_preferences_commitments_and_decisions_are_recallable(
    tmp_path: Path, kind: str, key: str, value: str
) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(kind=kind, key=key, value=value, quote=value)
    )

    automatic_memory.apply_reviewed_envelope(memory, envelope)

    recalled = automatic_memory.recall(memory, key)
    assert recalled["status"] == "current"
    assert recalled["kind"] == kind
    assert recalled["value"] == value
    assert recalled["citation"]["event_id"] == "event-user-1"


def test_assistant_suggestion_cannot_be_stored_as_keith_decision(tmp_path: Path) -> None:
    envelope = automatic_memory.make_envelope(
        _bundle(
            kind="decision",
            key="decision.runner",
            value="Use a hidden cron job.",
            quote="I suggest using a hidden cron job.",
            speaker_source="assistant_confirmed",
        ),
        validate=False,
    )

    with pytest.raises(automatic_memory.ValidationError, match="Keith evidence"):
        automatic_memory.apply_reviewed_envelope(
            tmp_path / "memory.json", envelope
        )
    assert not (tmp_path / "memory.json").exists()


def test_reapplying_reviewed_envelope_is_duplicate_free(tmp_path: Path) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(_bundle())

    first = automatic_memory.apply_reviewed_envelope(memory, envelope)
    second = automatic_memory.apply_reviewed_envelope(memory, envelope)

    assert first["applied"] == 1
    assert second == {"applied": 0, "duplicates": 1, "keys": ["fact.office"]}
    state = json.loads(memory.read_text(encoding="utf-8"))
    assert len(state["records"]["fact.office"]["versions"]) == 1


def test_pending_sjm_item_is_recalled_as_provisional_not_home_current(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    memory = tmp_path / "home-state" / "memory.json"
    automatic_memory.enqueue_bundle(outbox, _bundle())

    recalled = automatic_memory.recall(memory, "fact.office", provisional_outbox=outbox)

    assert recalled["status"] == "pending_home"
    assert recalled["value"] == "The SJM office is in Macau."
    assert recalled["citation"]["host_id"] == "sjm"
    assert recalled["home_current"] is None


def test_missing_recall_is_honest(tmp_path: Path) -> None:
    assert automatic_memory.recall(tmp_path / "missing.json", "fact.unknown") == {
        "key": "fact.unknown",
        "status": "missing",
        "value": None,
        "citation": None,
        "history": [],
    }


def test_malformed_durable_state_never_produces_success_or_overwrites_state(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    envelope = automatic_memory.enqueue_bundle(outbox, _bundle())
    state_path = outbox / "outbox" / f"{envelope['delivery_id']}.json"
    state_path.write_text('{"schema_version": 1, "status": []}', encoding="utf-8")

    with pytest.raises(automatic_memory.StateCorruptionError, match="outbox"):
        automatic_memory.record_delivery_attempt(
            outbox, envelope["delivery_id"], outcome="accepted"
        )

    assert json.loads(state_path.read_text(encoding="utf-8"))["status"] == []


def test_memory_apply_is_atomic_when_later_item_is_malformed(tmp_path: Path) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    valid = automatic_memory.make_envelope(_bundle())
    automatic_memory.apply_reviewed_envelope(memory, valid)
    original = memory.read_bytes()

    malformed_bundle = _bundle(
        session_id="session-bad", key="fact.other", value="A valid first item."
    )
    malformed_bundle["items"].append(
        {
            "key": "decision.bad",
            "kind": "decision",
            "value": "Missing evidence fields",
        }
    )
    malformed = automatic_memory.make_envelope(malformed_bundle, validate=False)

    with pytest.raises(automatic_memory.ValidationError, match="item"):
        automatic_memory.apply_reviewed_envelope(memory, malformed)

    assert memory.read_bytes() == original


@pytest.mark.parametrize(
    ("location", "value"),
    [
        ("scope", None),
        ("scope", []),
        ("review_state", {}),
        ("kind", []),
        ("speaker_source", {}),
        ("items", "not-an-array"),
    ],
)
def test_bundle_json_type_drift_is_always_a_controlled_validation_error(
    tmp_path: Path, location: str, value: object
) -> None:
    bundle = _bundle()
    if location in {"kind", "speaker_source"}:
        bundle["items"][0][location] = value
    else:
        bundle[location] = value

    with pytest.raises(automatic_memory.ValidationError):
        automatic_memory.enqueue_bundle(tmp_path, bundle)

    assert not list((tmp_path / "outbox").glob("*.json"))


def test_envelope_status_type_drift_is_rejected_before_home_acceptance(
    tmp_path: Path,
) -> None:
    envelope = automatic_memory.make_envelope(_bundle())
    envelope["status"] = []

    with pytest.raises(automatic_memory.ValidationError, match="status"):
        automatic_memory.accept_home_envelope(tmp_path, envelope)

    assert not list((tmp_path / "inbox").glob("*.json"))


def test_memory_kind_type_drift_is_reported_as_corruption_without_rewrite(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "memory.json"
    raw = {
        "schema_version": 1,
        "records": {"fact.office": {"kind": [], "versions": []}},
    }
    memory.write_text(json.dumps(raw), encoding="utf-8")
    before = memory.read_bytes()

    with pytest.raises(automatic_memory.StateCorruptionError, match="record"):
        automatic_memory.recall(memory, "fact.office")

    assert memory.read_bytes() == before


def test_unknown_thread_origin_fails_closed_before_queue_write(tmp_path: Path) -> None:
    with pytest.raises(automatic_memory.ValidationError, match="source origin"):
        automatic_memory.enqueue_bundle(
            tmp_path,
            _bundle(thread_source="agent-created-thread"),
        )

    assert not list((tmp_path / "outbox").glob("*.json"))


def test_bundle_item_count_is_bounded_before_durable_write(tmp_path: Path) -> None:
    bundle = _bundle()
    bundle["items"] = [
        {
            **bundle["items"][0],
            "key": f"fact.seed_{index:03d}",
            "evidence_event_id": f"event-user-{index:03d}",
        }
        for index in range(101)
    ]

    with pytest.raises(automatic_memory.ValidationError, match="at most 100"):
        automatic_memory.enqueue_bundle(tmp_path, bundle)

    assert not list((tmp_path / "outbox").glob("*.json"))


def test_reviewed_item_text_is_bounded_before_durable_write(tmp_path: Path) -> None:
    bundle = _bundle(value="x" * 65_537)

    with pytest.raises(automatic_memory.ValidationError, match="at most 65536"):
        automatic_memory.enqueue_bundle(tmp_path, bundle)

    assert not list((tmp_path / "outbox").glob("*.json"))


def test_deliverable_batch_is_bounded_and_stably_ordered(tmp_path: Path) -> None:
    automatic_memory.enqueue_bundle(
        tmp_path,
        _bundle(session_id="session-3", cursor="turn:3"),
    )
    automatic_memory.enqueue_bundle(
        tmp_path,
        _bundle(session_id="session-1", cursor="turn:1"),
    )
    automatic_memory.enqueue_bundle(
        tmp_path,
        _bundle(session_id="session-2", cursor="turn:2"),
    )

    batch = automatic_memory.list_deliverable(tmp_path, limit=2)

    assert len(batch) == 2
    assert [row["delivery_id"] for row in batch] == sorted(
        row["delivery_id"] for row in batch
    )


def test_durable_memory_requires_complete_exact_source_citation(tmp_path: Path) -> None:
    memory = tmp_path / "memory.json"
    automatic_memory.apply_reviewed_envelope(
        memory, automatic_memory.make_envelope(_bundle())
    )
    state = json.loads(memory.read_text(encoding="utf-8"))
    del state["records"]["fact.office"]["versions"][0]["citation"]["event_id"]
    memory.write_text(json.dumps(state), encoding="utf-8")
    before = memory.read_bytes()

    with pytest.raises(automatic_memory.StateCorruptionError, match="citation"):
        automatic_memory.recall(memory, "fact.office")

    assert memory.read_bytes() == before


def test_empty_durable_version_list_is_corruption_not_recall_crash(tmp_path: Path) -> None:
    memory = tmp_path / "memory.json"
    raw = {
        "schema_version": 1,
        "records": {"fact.office": {"kind": "fact", "versions": []}},
    }
    memory.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(automatic_memory.StateCorruptionError, match="versions"):
        automatic_memory.recall(memory, "fact.office")


def test_delivery_outcome_json_type_drift_is_controlled(tmp_path: Path) -> None:
    envelope = automatic_memory.enqueue_bundle(tmp_path, _bundle())

    with pytest.raises(automatic_memory.ValidationError, match="outcome"):
        automatic_memory.record_delivery_attempt(
            tmp_path,
            envelope["delivery_id"],
            outcome=[],  # type: ignore[arg-type]
        )


def test_delivery_lookup_rejects_path_shaped_identity_before_filesystem_use(
    tmp_path: Path,
) -> None:
    with pytest.raises(automatic_memory.ValidationError, match="delivery_id has unsupported"):
        automatic_memory.record_delivery_attempt(
            tmp_path,
            "a/../../outside",
            outcome="accepted",
        )


@pytest.mark.parametrize(
    "mutation", ["value", "kind", "evidence_quote", "speaker_source"]
)
def test_durable_memory_recomputes_item_identity_and_keith_evidence(
    tmp_path: Path, mutation: str
) -> None:
    memory = tmp_path / "memory.json"
    automatic_memory.apply_reviewed_envelope(
        memory, automatic_memory.make_envelope(_bundle())
    )

    state = json.loads(memory.read_text(encoding="utf-8"))
    if mutation == "value":
        state["records"]["fact.office"]["versions"][0]["value"] = (
            "INJECTED VALUE never present in the cited source"
        )
    elif mutation == "kind":
        state["records"]["fact.office"]["kind"] = "decision"
    elif mutation == "evidence_quote":
        state["records"]["fact.office"]["versions"][0]["evidence_quote"] = (
            "A quote not bound to the original item identity."
        )
    else:
        state["records"]["fact.office"]["versions"][0]["speaker_source"] = (
            "assistant_confirmed"
        )
    memory.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(automatic_memory.StateCorruptionError):
        automatic_memory.recall(memory, "fact.office")


def test_older_provisional_sjm_value_never_replaces_newer_home_current(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    memory = tmp_path / "home-state" / "memory.json"
    older = automatic_memory.enqueue_bundle(
        outbox,
        _bundle(completed_at="2026-09-09T12:00:00+08:00"),
    )
    automatic_memory.apply_reviewed_envelope(
        memory,
        automatic_memory.make_envelope(
            _bundle(
                host_id="home",
                session_id="session-home-new",
                completed_at="2026-09-11T12:00:00+08:00",
                cursor="turn:0009",
                value="The SJM office is in Hengqin.",
                event_id="event-home-correction",
                quote="Correction: the SJM office is in Hengqin.",
            )
        ),
    )

    pending = automatic_memory.recall(
        memory, "fact.office", provisional_outbox=outbox
    )
    automatic_memory.record_delivery_attempt(
        outbox, older["delivery_id"], outcome="accepted"
    )
    accepted = automatic_memory.recall(
        memory, "fact.office", provisional_outbox=outbox
    )

    for result in (pending, accepted):
        assert result["status"] == "current"
        assert result["value"] == "The SJM office is in Hengqin."
        assert result["citation"]["event_id"] == "event-home-correction"


def test_provisional_recall_deduplicates_item_already_in_home_memory(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.enqueue_bundle(outbox, _bundle())
    automatic_memory.apply_reviewed_envelope(memory, envelope)
    automatic_memory.record_delivery_attempt(
        outbox, envelope["delivery_id"], outcome="accepted"
    )

    recalled = automatic_memory.recall(
        memory, "fact.office", provisional_outbox=outbox
    )

    assert recalled["status"] == "current"
    assert recalled["value"] == "The SJM office is in Macau."
    assert "home_current" not in recalled


def test_newer_provisional_sjm_value_is_pending_home_over_older_home_value(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    memory = tmp_path / "home-state" / "memory.json"
    automatic_memory.apply_reviewed_envelope(
        memory,
        automatic_memory.make_envelope(
            _bundle(
                host_id="home",
                session_id="session-home-old",
                completed_at="2026-09-09T12:00:00+08:00",
                value="Use the old office.",
            )
        ),
    )
    automatic_memory.enqueue_bundle(
        outbox,
        _bundle(
            session_id="session-sjm-new",
            completed_at="2026-09-11T12:00:00+08:00",
            value="Use the new office.",
        ),
    )

    recalled = automatic_memory.recall(
        memory, "fact.office", provisional_outbox=outbox
    )

    assert recalled["status"] == "pending_home"
    assert recalled["value"] == "Use the new office."
    assert recalled["home_current"]["status"] == "current"
    assert recalled["home_current"]["value"] == "Use the old office."


def test_conflicting_valid_provisional_kinds_are_unresolved_with_citations(
    tmp_path: Path,
) -> None:
    outbox = tmp_path / "sjm-state"
    automatic_memory.enqueue_bundle(
        outbox,
        _bundle(
            session_id="session-fact",
            cursor="turn:fact",
            kind="fact",
            value="The office location is undecided.",
            event_id="event-fact",
            quote="The office location is undecided.",
        ),
    )
    automatic_memory.enqueue_bundle(
        outbox,
        _bundle(
            session_id="session-decision",
            cursor="turn:decision",
            kind="decision",
            value="Use the Hengqin office.",
            event_id="event-decision",
            quote="Use the Hengqin office.",
        ),
    )

    recalled = automatic_memory.recall(
        tmp_path / "home-state" / "memory.json",
        "fact.office",
        provisional_outbox=outbox,
    )

    assert recalled["status"] == "unresolved"
    assert recalled["value"] is None
    assert recalled["pending_home"] is True
    assert {alternative["kind"] for alternative in recalled["alternatives"]} == {
        "decision",
        "fact",
    }
    assert {
        alternative["citation"]["event_id"]
        for alternative in recalled["alternatives"]
    } == {"event-decision", "event-fact"}


def test_automatic_memory_lock_times_out_when_actually_held(tmp_path: Path) -> None:
    lock_path = tmp_path / "held.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TimeoutError, match="held.lock"):
            with automatic_memory._lock(lock_path, timeout_seconds=0.01):
                pytest.fail("held lock must not be entered")


@pytest.mark.parametrize(
    ("status", "attempt_count", "last_attempt_at", "last_error", "retry"),
    [
        ("accepted", 0, None, None, None),
        ("pending", 1, "2026-09-09T10:02:00+08:00", None, None),
        ("failed", 0, None, "remote_error", 60),
        ("offline", 1, "2026-09-09T10:02:00+08:00", None, 60),
        ("exhausted", 1, "2026-09-09T10:02:00+08:00", "remote_error", 60),
    ],
)
def test_impossible_delivery_state_combinations_are_rejected(
    tmp_path: Path,
    status: str,
    attempt_count: int,
    last_attempt_at: str | None,
    last_error: str | None,
    retry: int | None,
) -> None:
    envelope = automatic_memory.make_envelope(_bundle(), max_attempts=1)
    envelope.update(
        {
            "status": status,
            "attempt_count": attempt_count,
            "last_attempt_at": last_attempt_at,
            "last_error": last_error,
            "retry_after_seconds": retry,
        }
    )

    with pytest.raises(automatic_memory.ValidationError, match="state is inconsistent"):
        automatic_memory.accept_home_envelope(tmp_path, envelope)

    assert not list((tmp_path / "inbox").glob("*.json"))


def _correction_candidate(target: dict, source: dict, relation: str = "supersedes") -> dict:
    return {
        "relation": relation,
        "target_item_id": target["item_id"],
        "source_item_id": source["item_id"],
        "topic_ref": "wiki/projects/project_atlas.md",
        "field": "deadline",
        "evidence_refs": [target["item_id"], source["item_id"]],
    }


def _reviewed_correction_proposal(
    memory: Path, target: dict, source: dict, relation: str = "supersedes"
) -> dict:
    candidate = _correction_candidate(target, source, relation)
    receipt = automatic_memory.create_review_receipt(
        memory,
        candidate,
        reviewer="codex_conversation",
        model="gpt-5-codex",
        reviewed_at="2026-09-28T10:00:00+00:00",
    )
    return {**candidate, "review_receipt_id": receipt["receipt_id"]}


def test_correction_proposal_validation_and_documented_disposition(tmp_path: Path) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    old_env = automatic_memory.make_envelope(
        _bundle(key="memory.fact.old.aaaa", session_id="s-old", cursor="turn:old")
    )
    new_env = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-new",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:new",
            key="memory.decision.new.bbbb",
            kind="decision",
            value="Use the Hengqin office.",
            event_id="event-new",
            quote="Use the Hengqin office.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, old_env)
    automatic_memory.apply_reviewed_envelope(memory, new_env)
    old = automatic_memory._version(old_env["bundle"], old_env["bundle"]["items"][0])
    new = automatic_memory._version(new_env["bundle"], new_env["bundle"]["items"][0])

    proposal = _reviewed_correction_proposal(memory, old, new)
    v1_bytes = memory.read_bytes()
    result = automatic_memory.apply_corrections(
        memory, [proposal], recovery_dir=tmp_path / "recovery"
    )
    assert {
        key: result[key]
        for key in ("applied", "duplicates", "corrections", "schema_version")
    } == {
        "applied": 1,
        "duplicates": 0,
        "corrections": 1,
        "schema_version": 2,
    }
    # The first correction apply on a v1 file performs a recoverable migration:
    # the exact pre-migration bytes are preserved with their before/after hashes.
    assert result["migration"]["status"] == "migrated"
    assert result["migration"]["before_sha256"] == hashlib.sha256(v1_bytes).hexdigest()
    assert (
        Path(result["migration"]["recovery_path"]).read_bytes() == v1_bytes
    )
    assert automatic_memory.apply_corrections(memory, [proposal]) == {
        "applied": 0,
        "duplicates": 1,
        "corrections": 1,
        "schema_version": 2,
    }

    recalled = automatic_memory.recall(memory, "memory.fact.old.aaaa")
    assert recalled["status"] == "superseded"
    assert recalled["citation"]["event_id"] == "event-user-1"
    assert recalled["original_item_id"] == old["item_id"]
    assert recalled["replacement"] == {
        "key": "memory.decision.new.bbbb",
        "kind": "decision",
        "item_id": new["item_id"],
        "value": "Use the Hengqin office.",
        "citation": new["citation"],
    }
    # The replacement key is independently recallable as current.
    assert automatic_memory.recall(memory, "memory.decision.new.bbbb")["status"] == "current"


def test_correction_apply_on_v1_requires_recoverable_migration(tmp_path: Path) -> None:
    """A v1 file must never be upgraded to v2 without a recovery copy/hashes."""

    memory = tmp_path / "home-state" / "memory.json"
    old_env = automatic_memory.make_envelope(_bundle(key="memory.fact.old.aaaa"))
    new_env = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-new",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:new",
            key="memory.decision.new.bbbb",
            kind="decision",
            value="Use the Hengqin office.",
            event_id="event-new",
            quote="Use the Hengqin office.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, old_env)
    automatic_memory.apply_reviewed_envelope(memory, new_env)
    old = automatic_memory._version(old_env["bundle"], old_env["bundle"]["items"][0])
    new = automatic_memory._version(new_env["bundle"], new_env["bundle"]["items"][0])
    assert json.loads(memory.read_text(encoding="utf-8"))["schema_version"] == 1
    proposal = _reviewed_correction_proposal(memory, old, new)
    before = memory.read_bytes()

    # Read-only proposal/review paths stay v1-compatible.
    proposed = automatic_memory.propose_corrections(
        memory, [_correction_candidate(old, new) | {"review_receipt_id": proposal["review_receipt_id"]}]
    )
    assert proposed["schema_version"] == 1
    assert proposed["proposals"][0]["status"] == "eligible"

    # Without an explicit recovery directory the apply fails closed: no silent
    # v2 write, no recovery copy, byte-identical unchanged file.
    with pytest.raises(automatic_memory.ValidationError, match="recovery_dir"):
        automatic_memory.apply_corrections(memory, [proposal])
    assert memory.read_bytes() == before
    assert json.loads(memory.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not (memory.parent / "recovery").exists()

    # An explicit recoverable migration preserves the exact before bytes and
    # records before/after hashes before the correction lands.
    applied = automatic_memory.apply_corrections(
        memory, [proposal], recovery_dir=memory.parent / "recovery"
    )
    assert applied["applied"] == 1
    assert applied["migration"]["status"] == "migrated"
    before_sha = hashlib.sha256(before).hexdigest()
    assert applied["migration"]["before_sha256"] == before_sha
    recovery = Path(applied["migration"]["recovery_path"])
    assert recovery.read_bytes() == before
    assert automatic_memory.verify_recovery_copy(recovery, before_sha)["matches"] is True
    # The recorded post-migration hash is the correction-free v2 state the
    # recovery copy can restore, not the later corrected file.
    assert (
        applied["migration"]["after_sha256"]
        == automatic_memory._expected_post_migration_sha256(before)
    )
    after = memory.read_bytes()
    assert json.loads(after)["schema_version"] == 2
    assert len(json.loads(after)["corrections"]) == 1


def test_correction_proposal_with_unknown_ids_or_unbound_evidence_fails_closed(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    old_env = automatic_memory.make_envelope(_bundle(key="memory.fact.a.aaaa"))
    new_env = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-new",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:new",
            key="memory.decision.b.bbbb",
            kind="decision",
            value="A decision.",
            event_id="event-new",
            quote="A decision.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, old_env)
    automatic_memory.apply_reviewed_envelope(memory, new_env)
    old = automatic_memory._version(old_env["bundle"], old_env["bundle"]["items"][0])
    new = automatic_memory._version(new_env["bundle"], new_env["bundle"]["items"][0])
    before = memory.read_bytes()

    unknown = _reviewed_correction_proposal(memory, old, new)
    unknown["source_item_id"] = "item:" + "0" * 64
    with pytest.raises(automatic_memory.ValidationError):
        automatic_memory.apply_corrections(memory, [unknown])

    unbound = _reviewed_correction_proposal(memory, old, new)
    unbound["evidence_refs"] = [new["item_id"]]
    with pytest.raises(automatic_memory.ValidationError, match="evidence"):
        automatic_memory.apply_corrections(memory, [unbound])

    flagged_only = _reviewed_correction_proposal(memory, old, new)
    flagged_only["reviewed"] = True
    with pytest.raises(automatic_memory.ValidationError):
        automatic_memory.apply_corrections(memory, [flagged_only])
    assert memory.read_bytes() == before

def test_correction_requires_later_event_and_speaking_authority(tmp_path: Path) -> None:
    """A durable relation may not let older or weaker evidence overrule Keith."""

    memory = tmp_path / "home-state" / "memory.json"

    def apply(bundle: dict) -> dict:
        envelope = automatic_memory.make_envelope(bundle)
        automatic_memory.apply_reviewed_envelope(memory, envelope)
        return automatic_memory._version(envelope["bundle"], envelope["bundle"]["items"][0])

    older_assistant = apply(
        _bundle(
            host_id="home",
            session_id="s-old-assistant",
            completed_at="2026-09-20T10:00:00+08:00",
            cursor="turn:old-assistant",
            key="memory.fact.atlas.aaaa",
            value="An assistant claimed the Atlas deadline is 5 October.",
            event_id="event-old-assistant",
            quote="An assistant claimed the Atlas deadline is 5 October.",
            speaker_source="assistant_confirmed",
        )
    )
    keith_decision = apply(
        _bundle(
            host_id="home",
            session_id="s-keith-decision",
            completed_at="2026-09-22T10:00:00+08:00",
            cursor="turn:keith-decision",
            key="memory.decision.atlas.bbbb",
            kind="decision",
            value="Use the 8 October Atlas deadline.",
            event_id="event-keith-decision",
            quote="Use the 8 October Atlas deadline.",
        )
    )
    later_assistant = apply(
        _bundle(
            host_id="home",
            session_id="s-later-assistant",
            completed_at="2026-09-24T10:00:00+08:00",
            cursor="turn:later-assistant",
            key="memory.fact.atlas.cccc",
            value="The assistant suggested 9 October.",
            event_id="event-later-assistant",
            quote="The assistant suggested 9 October.",
            speaker_source="assistant_confirmed",
        )
    )
    later_keith = apply(
        _bundle(
            host_id="home",
            session_id="s-later-keith",
            completed_at="2026-09-26T10:00:00+08:00",
            cursor="turn:later-keith",
            key="memory.decision.atlas.dddd",
            kind="decision",
            value="Move the Atlas deadline to 10 October.",
            event_id="event-later-keith",
            quote="Move the Atlas deadline to 10 October.",
        )
    )
    before = memory.read_bytes()

    def reviewed(target: dict, source: dict, relation: str = "supersedes") -> dict:
        candidate = _correction_candidate(target, source, relation)
        receipt = automatic_memory.create_review_receipt(
            memory,
            candidate,
            reviewer="codex_conversation",
            model="gpt-5-codex",
            reviewed_at="2026-09-28T10:00:00+00:00",
        )
        return {**candidate, "review_receipt_id": receipt["receipt_id"]}

    # An older assistant claim may not be made to supersede a newer decision.
    with pytest.raises(automatic_memory.ValidationError, match="later"):
        reviewed(keith_decision, older_assistant)
    assert memory.read_bytes() == before

    # A later but weaker speaker may not overrule Keith's decision.
    with pytest.raises(automatic_memory.ValidationError, match="authority"):
        reviewed(keith_decision, later_assistant)
    assert memory.read_bytes() == before

    # Keith's own later correction still applies and can itself be corrected.
    applied = automatic_memory.apply_corrections(
        memory,
        [reviewed(keith_decision, later_keith)],
        recovery_dir=tmp_path / "recovery",
    )
    assert applied["applied"] == 1
    assert automatic_memory.recall(memory, "memory.decision.atlas.bbbb")["status"] == "superseded"
    final_keith = apply(
        _bundle(
            host_id="home",
            session_id="s-final-keith",
            completed_at="2026-09-28T10:00:00+08:00",
            cursor="turn:final-keith",
            key="memory.decision.atlas.eeee",
            kind="decision",
            value="Keep the Atlas deadline at 8 October.",
            event_id="event-final-keith",
            quote="Keep the Atlas deadline at 8 October.",
        )
    )
    applied = automatic_memory.apply_corrections(
        memory,
        [reviewed(later_keith, final_keith)],
        recovery_dir=tmp_path / "recovery",
    )
    assert applied["applied"] == 1
    recall = automatic_memory.recall(memory, "memory.decision.atlas.bbbb")
    assert recall["status"] == "superseded"
    assert recall["replacement"]["item_id"] == final_keith["item_id"]

    # Keith's later retraction of the assistant claim is a legitimate relation.
    applied = automatic_memory.apply_corrections(
        memory,
        [reviewed(older_assistant, final_keith, relation="retracts")],
        recovery_dir=tmp_path / "recovery",
    )
    assert applied["applied"] == 1
    assert automatic_memory.recall(memory, "memory.fact.atlas.aaaa")["status"] == "retracted"


def test_retraction_cancelled_by_later_supersession_resolves_terminal_state(
    tmp_path: Path,
) -> None:
    """A retraction that is itself superseded stops retracting its target.

    ``B`` retracts ``A``; a later accepted ``C`` supersedes ``B``.  Recall of
    ``A`` must follow the whole chain to its terminal state instead of stopping
    at ``A -> B``, while the historical correction records are preserved.
    """

    memory = tmp_path / "home-state" / "memory.json"

    def apply(bundle: dict) -> dict:
        envelope = automatic_memory.make_envelope(bundle)
        automatic_memory.apply_reviewed_envelope(memory, envelope)
        return automatic_memory._version(
            envelope["bundle"], envelope["bundle"]["items"][0]
        )

    original = apply(
        _bundle(
            host_id="home",
            session_id="s-chain-a",
            completed_at="2026-09-20T10:00:00+08:00",
            cursor="turn:chain-a",
            key="memory.fact.atlas.aaaa",
            value="The Atlas deadline is 5 October 2026.",
            event_id="event-chain-a",
            quote="The Atlas deadline is 5 October 2026.",
        )
    )
    retraction = apply(
        _bundle(
            host_id="home",
            session_id="s-chain-b",
            completed_at="2026-09-22T10:00:00+08:00",
            cursor="turn:chain-b",
            key="memory.fact.atlas.bbbb",
            value="Retract the 5 October Atlas deadline claim.",
            event_id="event-chain-b",
            quote="Retract the 5 October Atlas deadline claim.",
        )
    )
    replacement = apply(
        _bundle(
            host_id="home",
            session_id="s-chain-c",
            completed_at="2026-09-26T10:00:00+08:00",
            cursor="turn:chain-c",
            key="memory.decision.atlas.cccc",
            kind="decision",
            value="Keep the Atlas deadline at 8 October 2026.",
            event_id="event-chain-c",
            quote="Keep the Atlas deadline at 8 October 2026.",
        )
    )

    def reviewed(target: dict, source: dict, relation: str) -> dict:
        candidate = _correction_candidate(target, source, relation)
        receipt = automatic_memory.create_review_receipt(
            memory,
            candidate,
            reviewer="codex_conversation",
            model="gpt-5-codex",
            reviewed_at="2026-09-28T10:00:00+00:00",
        )
        return {**candidate, "review_receipt_id": receipt["receipt_id"]}

    retract_proposal = reviewed(original, retraction, "retracts")
    supersede_proposal = reviewed(retraction, replacement, "supersedes")

    applied = automatic_memory.apply_corrections(
        memory, [retract_proposal], recovery_dir=tmp_path / "recovery"
    )
    assert applied["applied"] == 1
    assert (
        automatic_memory.recall(memory, "memory.fact.atlas.aaaa")["status"]
        == "retracted"
    )

    applied = automatic_memory.apply_corrections(
        memory, [supersede_proposal], recovery_dir=tmp_path / "recovery"
    )
    assert applied["applied"] == 1

    recall_a = automatic_memory.recall(memory, "memory.fact.atlas.aaaa")
    assert recall_a["status"] == "current"
    assert recall_a["value"] == "The Atlas deadline is 5 October 2026."
    assert recall_a["citation"]["event_id"] == "event-chain-a"

    recall_b = automatic_memory.recall(memory, "memory.fact.atlas.bbbb")
    assert recall_b["status"] == "superseded"
    assert recall_b["replacement"]["item_id"] == replacement["item_id"]
    assert (
        automatic_memory.recall(memory, "memory.decision.atlas.cccc")["status"]
        == "current"
    )

    # The relation history is retained, and re-applying is idempotent.
    state = json.loads(memory.read_text(encoding="utf-8"))
    assert len(state["corrections"]) == 2
    again = automatic_memory.apply_corrections(memory, [supersede_proposal])
    assert again["applied"] == 0 and again["duplicates"] == 1
    assert (
        automatic_memory.recall(memory, "memory.fact.atlas.aaaa")["status"]
        == "current"
    )
    assert len(json.loads(memory.read_text(encoding="utf-8"))["corrections"]) == 2


def test_forged_inline_review_is_rejected_and_receipt_is_required(tmp_path: Path) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    old_env = automatic_memory.make_envelope(_bundle(key="memory.fact.a.aaaa"))
    new_env = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-new",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:new",
            key="memory.decision.b.bbbb",
            kind="decision",
            value="A decision.",
            event_id="event-new",
            quote="A decision.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, old_env)
    automatic_memory.apply_reviewed_envelope(memory, new_env)
    old = automatic_memory._version(old_env["bundle"], old_env["bundle"]["items"][0])
    new = automatic_memory._version(new_env["bundle"], new_env["bundle"]["items"][0])
    before = memory.read_bytes()

    # A caller-authored inline review object grants no authority.
    inline = _correction_candidate(old, new)
    inline["review"] = {
        "reviewer": "codex_conversation",
        "model": "gpt-5-codex",
        "reviewed_at": "2026-09-28T10:00:00+00:00",
        "candidate_sha256": automatic_memory._sha256_hex(_correction_candidate(old, new)),
        "evidence_sha256": sorted(
            automatic_memory._evidence_sha256(item["item_id"], item)
            for item in (old, new)
        ),
    }
    with pytest.raises(automatic_memory.ValidationError):
        automatic_memory.apply_corrections(memory, [inline])
    assert memory.read_bytes() == before

    # A receipt id with no matching receipt file also fails closed.
    dangling = {**_correction_candidate(old, new), "review_receipt_id": "review:" + "a" * 64}
    with pytest.raises(automatic_memory.ValidationError, match="receipt"):
        automatic_memory.apply_corrections(memory, [dangling])
    assert memory.read_bytes() == before

    # The read-only review path creates a receipt that makes the proposal valid.
    receipt = automatic_memory.create_review_receipt(
        memory,
        _correction_candidate(old, new),
        reviewer="codex_conversation",
        model="gpt-5-codex",
        reviewed_at="2026-09-28T10:00:00+00:00",
    )
    assert receipt["receipt_id"].startswith("review:")
    assert (memory.parent / "review-receipts" / f"{receipt['receipt_id']}.json").is_file()
    applied = automatic_memory.apply_corrections(
        memory,
        [{**_correction_candidate(old, new), "review_receipt_id": receipt["receipt_id"]}],
        recovery_dir=tmp_path / "recovery",
    )
    assert applied["applied"] == 1
    assert memory.read_bytes() != before


def test_review_receipt_tolerates_append_only_capture_but_not_changed_evidence(
    tmp_path: Path,
) -> None:
    """Capture is append-only: an unrelated append must not void a receipt."""

    memory = tmp_path / "home-state" / "memory.json"
    old_env = automatic_memory.make_envelope(_bundle(key="memory.fact.a.aaaa"))
    new_env = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-new",
            completed_at="2026-09-11T12:00:00+08:00",
            cursor="turn:new",
            key="memory.decision.b.bbbb",
            kind="decision",
            value="A decision.",
            event_id="event-new",
            quote="A decision.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, old_env)
    automatic_memory.apply_reviewed_envelope(memory, new_env)
    old = automatic_memory._version(old_env["bundle"], old_env["bundle"]["items"][0])
    new = automatic_memory._version(new_env["bundle"], new_env["bundle"]["items"][0])
    candidate = _correction_candidate(old, new)
    receipt = automatic_memory.create_review_receipt(
        memory,
        candidate,
        reviewer="codex_conversation",
        model="gpt-5-codex",
        reviewed_at="2026-09-28T10:00:00+00:00",
    )
    receipt_path = memory.parent / "review-receipts" / f"{receipt['receipt_id']}.json"
    snapshot_hash = receipt["records_snapshot_sha256"]

    # An unrelated record arrives after the review.  Its evidence hashes do not
    # include the referenced items, so the reviewed candidate is still valid.
    unrelated = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-other",
            completed_at="2026-09-12T12:00:00+08:00",
            cursor="turn:other",
            key="memory.fact.c.cccc",
            value="Another fact.",
            event_id="event-other",
            quote="Another fact.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, unrelated)
    snapshot_after = automatic_memory._records_snapshot_sha256(
        automatic_memory._load_memory(memory)["records"]
    )
    assert snapshot_after != snapshot_hash
    applied = automatic_memory.apply_corrections(
        memory,
        [{**candidate, "review_receipt_id": receipt["receipt_id"]}],
        recovery_dir=tmp_path / "recovery",
    )
    assert applied["applied"] == 1
    # The audit snapshot hash is retained verbatim on the stored receipt.
    stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert stored["records_snapshot_sha256"] == snapshot_hash

    # A receipt whose evidence hashes no longer bind the referenced items still
    # fails closed, even though the snapshot gate is gone.
    forged = deepcopy(receipt)
    forged["evidence_sha256"] = sorted(
        f"{index:064x}" for index in range(1, len(receipt["evidence_sha256"]) + 1)
    )
    forged["receipt_id"] = automatic_memory._sha256_id(
        "review", {key: forged[key] for key in forged if key != "receipt_id"}
    )
    (memory.parent / "review-receipts" / f"{forged['receipt_id']}.json").write_text(
        json.dumps(forged, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(automatic_memory.ValidationError, match="evidence hashes"):
        automatic_memory.validate_correction_proposal(
            automatic_memory._load_memory(memory)["records"],
            {**candidate, "review_receipt_id": forged["receipt_id"]},
            receipt_store=memory.parent / "review-receipts",
        )

    # A mutated candidate also fails: the receipt binds the relation fields.
    mutated = _correction_candidate(old, new)
    mutated["field"] = "delivery_status"
    with pytest.raises(automatic_memory.ValidationError):
        automatic_memory.validate_correction_proposal(
            automatic_memory._load_memory(memory)["records"],
            {**mutated, "review_receipt_id": receipt["receipt_id"]},
            receipt_store=memory.parent / "review-receipts",
        )


def test_migration_recovery_preserves_exact_bytes_and_restores_explicitly(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
    )
    automatic_memory.apply_reviewed_envelope(memory, envelope)
    before_bytes = memory.read_bytes()
    assert json.loads(before_bytes)["schema_version"] == 1

    migrated = automatic_memory.migrate_memory_state_v2(memory, tmp_path / "recovery")
    assert migrated["status"] == "migrated"
    recovery = Path(migrated["recovery_path"])
    # The recovery copy is the exact pre-migration bytes, not a reserialization.
    assert recovery.read_bytes() == before_bytes
    assert hashlib.sha256(recovery.read_bytes()).hexdigest() == migrated["before_sha256"]

    verified = automatic_memory.verify_recovery_copy(recovery, migrated["before_sha256"])
    assert verified["matches"] is True
    assert (
        automatic_memory.verify_recovery_copy(recovery, "f" * 64)["matches"] is False
    )

    # Migration itself never auto-restores: the live file stays v2.
    assert json.loads(memory.read_text(encoding="utf-8"))["schema_version"] == 2

    # An explicit, hash-checked restore reproduces the original bytes.
    target = tmp_path / "restore" / "memory.json"
    restored = automatic_memory.restore_memory_from_recovery(
        recovery, target, expected_sha256=migrated["before_sha256"]
    )
    assert restored["status"] == "restored"
    assert target.read_bytes() == before_bytes
    with pytest.raises(automatic_memory.ValidationError):
        automatic_memory.restore_memory_from_recovery(
            recovery, target, expected_sha256="0" * 64
        )


def test_migration_refuses_stale_or_corrupt_existing_recovery_copy(
    tmp_path: Path,
) -> None:
    """An existing recovery slot is durable evidence, checked before any v2 write."""

    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
    )
    automatic_memory.apply_reviewed_envelope(memory, envelope)
    before_bytes = memory.read_bytes()
    before_sha = hashlib.sha256(before_bytes).hexdigest()
    assert json.loads(before_bytes)["schema_version"] == 1

    # Pre-seed the before_sha-named slot with bytes that do not hash to the live
    # state, standing in for a stale, corrupt or replaced recovery copy.
    recovery_dir = tmp_path / "recovery"
    recovery_dir.mkdir(parents=True)
    recovery = recovery_dir / f"{memory.name}.{before_sha}.v1.json"
    foreign_bytes = before_bytes.replace(b"legacy.aaaa", b"legacy.bbbb")
    assert hashlib.sha256(foreign_bytes).hexdigest() != before_sha
    recovery.write_bytes(foreign_bytes)

    # Migration must fail closed: no v2 write, and the invalid copy is untouched.
    with pytest.raises(automatic_memory.StateCorruptionError, match="recovery copy"):
        automatic_memory.migrate_memory_state_v2(memory, recovery_dir)
    assert memory.read_bytes() == before_bytes
    assert json.loads(memory.read_text(encoding="utf-8"))["schema_version"] == 1
    assert recovery.read_bytes() == foreign_bytes

    # Idempotence with a valid existing copy: an identical slot is not rewritten.
    recovery.write_bytes(before_bytes)
    recovery_mtime = recovery.stat().st_mtime_ns
    migrated = automatic_memory.migrate_memory_state_v2(memory, recovery_dir)
    assert migrated["status"] == "migrated"
    assert migrated["before_sha256"] == before_sha
    assert Path(migrated["recovery_path"]) == recovery
    assert recovery.read_bytes() == before_bytes
    assert recovery.stat().st_mtime_ns == recovery_mtime
    assert json.loads(memory.read_text(encoding="utf-8"))["schema_version"] == 2


def test_restore_over_existing_state_requires_verified_post_migration_hash(
    tmp_path: Path,
) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
    )
    automatic_memory.apply_reviewed_envelope(memory, envelope)
    before_bytes = memory.read_bytes()
    migrated = automatic_memory.migrate_memory_state_v2(memory, tmp_path / "recovery")
    recovery = Path(migrated["recovery_path"])
    assert migrated["after_sha256"] == hashlib.sha256(memory.read_bytes()).hexdigest()

    # Overwriting an existing memory file without the recorded post-migration
    # hash fails closed instead of silently reverting live state.
    with pytest.raises(automatic_memory.ValidationError, match="post-migration"):
        automatic_memory.restore_memory_from_recovery(
            recovery, memory, expected_sha256=migrated["before_sha256"]
        )
    assert memory.read_bytes() != before_bytes
    assert json.loads(memory.read_text(encoding="utf-8"))["schema_version"] == 2

    with pytest.raises(automatic_memory.ValidationError, match="lowercase SHA-256"):
        automatic_memory.restore_memory_from_recovery(
            recovery,
            memory,
            expected_sha256=migrated["before_sha256"],
            expected_target_sha256="not-a-hash",
        )

    # A stale or wrong post-migration hash is refused.
    with pytest.raises(automatic_memory.ValidationError, match="newer live state"):
        automatic_memory.restore_memory_from_recovery(
            recovery,
            memory,
            expected_sha256=migrated["before_sha256"],
            expected_target_sha256="0" * 64,
        )
    assert memory.read_bytes() != before_bytes

    # The exact post-migration hash permits the deliberate restore.
    restored = automatic_memory.restore_memory_from_recovery(
        recovery,
        memory,
        expected_sha256=migrated["before_sha256"],
        expected_target_sha256=migrated["after_sha256"],
    )
    assert restored["status"] == "restored"
    assert memory.read_bytes() == before_bytes


def test_restore_never_clobbers_state_written_after_migration(tmp_path: Path) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
    )
    automatic_memory.apply_reviewed_envelope(memory, envelope)
    migrated = automatic_memory.migrate_memory_state_v2(memory, tmp_path / "recovery")
    recovery = Path(migrated["recovery_path"])

    # A newer record is accepted after the migration.
    newer = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-newer",
            completed_at="2026-09-20T10:00:00+08:00",
            cursor="turn:newer",
            key="memory.decision.newer.bbbb",
            kind="decision",
            value="Newer accepted decision.",
            event_id="event-newer",
            quote="Newer accepted decision.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, newer)
    live_after_append = memory.read_bytes()
    assert hashlib.sha256(live_after_append).hexdigest() != migrated["after_sha256"]

    # The post-migration hash no longer describes live state, so the restore is
    # refused rather than replacing newer accepted records.
    with pytest.raises(automatic_memory.ValidationError, match="newer live state"):
        automatic_memory.restore_memory_from_recovery(
            recovery,
            memory,
            expected_sha256=migrated["before_sha256"],
            expected_target_sha256=migrated["after_sha256"],
        )
    assert memory.read_bytes() == live_after_append
    assert len(automatic_memory.memory_snapshot(memory)["records"]) == 2


def test_restore_refuses_after_hash_not_bound_to_the_recovery_copy(
    tmp_path: Path,
) -> None:
    """Naming the target's current hash must not authorise discarding newer data.

    A caller can read the live target and supply its hash as the "post-migration"
    value.  The restore must instead derive the permitted after-hash from the
    recovery copy itself, so only the unmodified post-migration state can be
    overwritten.
    """

    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
    )
    automatic_memory.apply_reviewed_envelope(memory, envelope)
    migrated = automatic_memory.migrate_memory_state_v2(memory, tmp_path / "recovery")
    recovery = Path(migrated["recovery_path"])

    newer = automatic_memory.make_envelope(
        _bundle(
            host_id="home",
            session_id="s-newer",
            completed_at="2026-09-20T10:00:00+08:00",
            cursor="turn:newer",
            key="memory.decision.newer.bbbb",
            kind="decision",
            value="Newer accepted decision.",
            event_id="event-newer",
            quote="Newer accepted decision.",
        )
    )
    automatic_memory.apply_reviewed_envelope(memory, newer)
    live = memory.read_bytes()

    with pytest.raises(automatic_memory.ValidationError, match="derived from the recovery copy"):
        automatic_memory.restore_memory_from_recovery(
            recovery,
            memory,
            expected_sha256=migrated["before_sha256"],
            expected_target_sha256=hashlib.sha256(live).hexdigest(),
        )
    assert memory.read_bytes() == live
    assert len(automatic_memory.memory_snapshot(memory)["records"]) == 2

    # The genuine, recovery-derived post-migration hash is still accepted while
    # the target is unmodified.
    unmodified = tmp_path / "unmodified" / "memory.json"
    automatic_memory.apply_reviewed_envelope(
        unmodified,
        automatic_memory.make_envelope(
            _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
        ),
    )
    migrated_unmodified = automatic_memory.migrate_memory_state_v2(
        unmodified, tmp_path / "recovery-unmodified"
    )
    restored = automatic_memory.restore_memory_from_recovery(
        Path(migrated_unmodified["recovery_path"]),
        unmodified,
        expected_sha256=migrated_unmodified["before_sha256"],
        expected_target_sha256=migrated_unmodified["after_sha256"],
    )
    assert restored["status"] == "restored"


def test_recall_snapshot_reads_raw_v1_and_v2_file_forms(tmp_path: Path) -> None:
    memory = tmp_path / "home-state" / "memory.json"
    envelope = automatic_memory.make_envelope(
        _bundle(key="memory.fact.legacy.aaaa", session_id="s-legacy", cursor="turn:l")
    )
    automatic_memory.apply_reviewed_envelope(memory, envelope)

    raw_v1 = json.loads(memory.read_text(encoding="utf-8"))
    assert set(raw_v1) == {"schema_version", "records"}
    assert raw_v1["schema_version"] == 1
    recalled = automatic_memory.recall_snapshot(raw_v1, "memory.fact.legacy.aaaa")
    assert recalled["status"] == "current"
    assert recalled["value"] == "The SJM office is in Macau."
    # Reading a published file form must not mutate the caller's object.
    assert set(raw_v1) == {"schema_version", "records"}
    assert (
        automatic_memory.recall_snapshot(raw_v1, "memory.fact.absent.zzzz")["status"]
        == "missing"
    )

    # A migrated v2 file form is accepted through the same entry point.
    automatic_memory.migrate_memory_state_v2(memory, tmp_path / "recovery")
    raw_v2 = json.loads(memory.read_text(encoding="utf-8"))
    assert raw_v2["schema_version"] == 2
    assert automatic_memory.recall_snapshot(raw_v2, "memory.fact.legacy.aaaa")[
        "status"
    ] == "current"

    # A malformed file form still fails closed rather than being coerced.
    with pytest.raises(automatic_memory.StateCorruptionError):
        automatic_memory.recall_snapshot(
            {"schema_version": 1, "records": raw_v1["records"], "extra": []},
            "memory.fact.legacy.aaaa",
        )
