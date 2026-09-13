from __future__ import annotations

import fcntl
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
