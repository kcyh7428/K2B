#!/usr/bin/env python3
"""Tests for the Chat-2 agent-native path: waiting_for_agent_theme parked state +
verify-theme gate (>=5 supported candidates, >=1 2nd/3rd-order, citation ledger
>=60% supported). No Kimi anywhere in this path."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def temp_env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    db = tmp_path / "orch.sqlite"
    monkeypatch.setenv("K2B_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2BI_VAULT_PATH", str(vault))
    monkeypatch.setenv("K2B_ORCH_DB", str(db))
    monkeypatch.setenv("K2B_ORCH_SKIP_PROVIDER_PING", "1")
    ws = tmp_path / "k2bi"
    ws.mkdir()
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(ws))
    for k in [k for k in sys.modules if "orchestrator" in k]:
        del sys.modules[k]


@pytest.fixture
def store(temp_env, tmp_path):
    from scripts.lib import orchestrator_store as store
    db = tmp_path / "orch.sqlite"
    vault = tmp_path / "vault"
    store.DB_PATH = str(db)
    store.RESULTS_DIR = str(vault / "raw" / "orchestrator-results")
    store.BOARD_PATH = str(vault / "System" / "orchestrator" / "board.md")
    store.K2B_VAULT = str(vault)
    store.init_db(store.connect())
    os.makedirs(store.RESULTS_DIR, exist_ok=True)
    return store


def _row(symbol, order, status, *, url="https://example.com/x", note="backs the claim",
         checked="2026-05-31T00:00:00Z"):
    return {"symbol": symbol, "order": order, "claim": f"{symbol} claim",
            "url": url, "support_note": note, "checked_at": checked, "status": status}


_ARK = {"people_culture": 7, "rd_execution": 7, "moat": 7,
        "product_leadership": 7, "thesis_risk": 6, "valuation": 6}


def _theme_text(ledger, *, candidate_count=5, with_ledger=True, close=True,
                ark_syms=None, body_syms=None):
    """Build theme markdown. By default candidate_ark_scores + the body candidate
    table are derived from the SUPPORTED ledger symbols, so the displayed-set
    cross-check passes; pass ark_syms/body_syms to test inconsistency."""
    supported = [str(r["symbol"]).upper() for r in ledger
                 if r.get("status") in ("cite-ok", "repaired")]
    ark_syms = supported if ark_syms is None else ark_syms
    body_syms = supported if body_syms is None else body_syms
    fm = {"type": "macro-theme", "candidate-count": candidate_count}
    if with_ledger:
        fm["citation_ledger"] = ledger
    fm["candidate_ark_scores"] = {s: dict(_ARK) for s in ark_syms}
    text = "---\n" + yaml.safe_dump(fm, sort_keys=False)
    if not close:
        return text + "\n# missing close fence\n"
    text += "---\n\n## Candidate tickers\n\n| Symbol | Order | Why |\n|---|---|---|\n"
    for s in body_syms:
        text += f"| {s} | 2nd | reasoning |\n"
    return text


def _write_theme(path, ledger, *, candidate_count=5, with_ledger=True, close=True,
                 ark_syms=None, body_syms=None):
    path.write_text(_theme_text(ledger, candidate_count=candidate_count,
                                with_ledger=with_ledger, close=close,
                                ark_syms=ark_syms, body_syms=body_syms))
    return str(path)


def _five_good():
    # 5 supported, >=1 2nd/3rd, all fields present, ratio 100%
    return [
        _row("AAA", "1st", "cite-ok"),
        _row("BBB", "1st", "repaired"),
        _row("CCC", "2nd", "cite-ok"),
        _row("DDD", "2nd", "cite-ok"),
        _row("EEE", "3rd", "cite-ok"),
    ]


def _add_parked(store, entity="ai-supply-chain"):
    return store.add_task(
        assignee_profile="k2bi", command_key="k2bi-narrative", success_criteria="ok",
        permissions="analyst-command", entity_key=entity,
        payload={"narrative": "x" * 60}, status="waiting_for_agent_theme",
    )


def _write_vault_theme(tmp_path, ledger, *, slug="ai-supply-chain", candidate_count=5, index=True):
    """F2: a real, indexed vault theme under K2BI_VAULT/wiki/macro-themes/."""
    macro = tmp_path / "vault" / "wiki" / "macro-themes"
    macro.mkdir(parents=True, exist_ok=True)
    theme = macro / f"theme_{slug}.md"
    theme.write_text(_theme_text(ledger, candidate_count=candidate_count))
    idx = macro / "index.md"
    if index:
        with open(idx, "a") as f:
            f.write(f"| [[theme_{slug}|t]] | 2026-05-31 | {candidate_count} | candidates-pending-review |\n")
    elif not idx.exists():
        idx.write_text("# Raw macro-themes index\n")
    return str(theme)


# ---- state-machine correctness ------------------------------------------------

class TestParkedStateMachine:
    def test_state_classification(self, store):
        assert "waiting_for_agent_theme" in store.ALL_STATUSES
        assert "waiting_for_agent_theme" in store.VALID_INITIAL_STATUSES
        assert "waiting_for_agent_theme" not in store.TERMINAL_STATUSES

    def test_add_creates_parked_flight(self, store):
        tid = _add_parked(store)
        assert store.get_task(tid)["status"] == "waiting_for_agent_theme"

    def test_poll_once_does_not_dispatch_parked(self, store):
        tid = _add_parked(store)
        store.poll_once()
        # Still parked -- never dispatched, never blocked.
        assert store.get_task(tid)["status"] == "waiting_for_agent_theme"

    def test_one_flight_lock_holds_for_parked(self, store):
        _add_parked(store, entity="dup-topic")
        with pytest.raises(store.FlightLockError):
            _add_parked(store, entity="Dup-Topic")  # case-insensitive lock


# ---- the gate -----------------------------------------------------------------

class TestVerifyThemeGate:
    def test_pass(self, store, tmp_path):
        p = _write_theme(tmp_path / "theme_ok.md", _five_good())
        ok, reason = store._verify_theme_gate(p)
        assert ok, reason

    def test_fail_fewer_than_5_supported(self, store, tmp_path):
        ledger = _five_good()[:4] + [_row("EEE", "2nd", "unverified")]
        p = _write_theme(tmp_path / "theme_u5.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "fewer than 5" in reason

    def test_fail_no_2nd_3rd_order(self, store, tmp_path):
        ledger = [_row(s, "1st", "cite-ok") for s in ("AAA", "BBB", "CCC", "DDD", "EEE")]
        p = _write_theme(tmp_path / "theme_no23.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "2nd/3rd" in reason

    def test_fail_ratio_below_60(self, store, tmp_path):
        ledger = _five_good() + [_row(f"U{i}", "1st", "unverified") for i in range(5)]
        p = _write_theme(tmp_path / "theme_ratio.md", ledger, candidate_count=5)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "ratio" in reason  # 5/10 = 50%

    def test_fail_supported_row_missing_url(self, store, tmp_path):
        ledger = _five_good()
        ledger[2]["url"] = ""
        p = _write_theme(tmp_path / "theme_nourl.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "url" in reason

    def test_fail_no_ledger(self, store, tmp_path):
        p = _write_theme(tmp_path / "theme_noledger.md", [], with_ledger=False)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "citation_ledger" in reason

    def test_fail_candidate_count_lt5(self, store, tmp_path):
        p = _write_theme(tmp_path / "theme_cc.md", _five_good(), candidate_count=3)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "candidate-count" in reason

    def test_fail_malformed_frontmatter(self, store, tmp_path):
        p = _write_theme(tmp_path / "theme_bad.md", _five_good(), close=False)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "frontmatter" in reason

    def test_fail_invalid_status_token(self, store, tmp_path):
        ledger = _five_good()
        ledger[0]["status"] = "totally-made-up"
        p = _write_theme(tmp_path / "theme_badstatus.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "status" in reason


# ---- end-to-end transition ----------------------------------------------------

class TestVerifyThemeComplete:
    def test_pass_transitions_parked_to_done(self, store, tmp_path):
        tid = _add_parked(store)
        p = _write_vault_theme(tmp_path, _five_good())
        ok, reason = store.verify_theme_complete(tid, p)
        assert ok, reason
        t = store.get_task(tid)
        assert t["status"] == "done"
        assert t["result_url"] == os.path.realpath(p)

    def test_gate_fail_leaves_flight_parked(self, store, tmp_path):
        tid = _add_parked(store)
        p = _write_vault_theme(tmp_path, _five_good()[:4])
        ok, reason = store.verify_theme_complete(tid, p)
        assert not ok
        assert store.get_task(tid)["status"] == "waiting_for_agent_theme"

    def test_rejects_non_parked_task(self, store, tmp_path):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="k2bi-narrative", success_criteria="ok",
            permissions="analyst-command", entity_key="ready-topic",
            payload={"narrative": "x" * 60}, status="ready",
        )
        p = _write_vault_theme(tmp_path, _five_good())
        ok, reason = store.verify_theme_complete(tid, p)
        assert not ok and "waiting_for_agent_theme" in reason
        assert store.get_task(tid)["status"] == "ready"


# ---- F2: durable, indexed vault artifact required --------------------------

class TestVerifyThemePathValidation:
    def test_rejects_path_outside_macro_themes(self, store, tmp_path):
        tid = _add_parked(store)
        p = _write_theme(tmp_path / "theme_tmp.md", _five_good())  # a bare temp file
        ok, reason = store.verify_theme_complete(tid, p)
        assert not ok and "must live under" in reason
        assert store.get_task(tid)["status"] == "waiting_for_agent_theme"

    def test_rejects_theme_not_in_index(self, store, tmp_path):
        tid = _add_parked(store)
        p = _write_vault_theme(tmp_path, _five_good(), index=False)
        ok, reason = store.verify_theme_complete(tid, p)
        assert not ok and "not referenced in macro-themes/index.md" in reason
        assert store.get_task(tid)["status"] == "waiting_for_agent_theme"


# ---- F3/F4: candidate-set integrity + citation realness --------------------

class TestGateIntegrityF3F4:
    def test_fail_duplicate_symbol(self, store, tmp_path):
        ledger = _five_good()
        ledger[1]["symbol"] = "AAA"  # dup of row 0
        p = _write_theme(tmp_path / "t.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "duplicate" in reason

    def test_fail_candidate_count_mismatch(self, store, tmp_path):
        # 5 distinct supported but candidate-count says 7
        p = _write_theme(tmp_path / "t.md", _five_good(), candidate_count=7)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "!= distinct supported" in reason

    def test_fail_non_http_url(self, store, tmp_path):
        ledger = _five_good()
        ledger[2]["url"] = "not-a-real-url"
        p = _write_theme(tmp_path / "t.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "http" in reason

    def test_fail_non_iso_checked_at(self, store, tmp_path):
        ledger = _five_good()
        ledger[3]["checked_at"] = "yesterday"
        p = _write_theme(tmp_path / "t.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "ISO-8601" in reason


# ---- F5: the locked macro-theme write helper -------------------------------

class TestMacroThemeWriteHelper:
    def _run(self, cf, slug_base):
        return subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "macro-theme-write.py"), str(cf), slug_base],
            cwd=str(REPO_ROOT), env=dict(os.environ), capture_output=True, text=True,
        )

    def test_writes_theme_indexes_and_autoversions(self, store, tmp_path, monkeypatch):
        macro = tmp_path / "vault" / "wiki" / "macro-themes"
        macro.mkdir(parents=True, exist_ok=True)
        (macro / "index.md").write_text("# index\n")
        cf = tmp_path / "content.md"
        cf.write_text(_theme_text(_five_good()))  # gate-valid content
        r1 = self._run(cf, "ai supply chain trend")
        assert r1.returncode == 0, r1.stderr
        p1 = r1.stdout.strip()
        assert p1.endswith("theme_ai-supply-chain-trend.md")
        assert os.path.exists(p1)
        assert "theme_ai-supply-chain-trend" in (macro / "index.md").read_text()
        # second write, same slug-base -> auto-version _2 (never overwrites)
        r2 = self._run(cf, "ai supply chain trend")
        assert r2.returncode == 0, r2.stderr
        assert r2.stdout.strip().endswith("theme_ai-supply-chain-trend_2.md")

    # Codex Checkpoint-2 r4 (HIGH): the helper gates BEFORE publishing. A theme
    # that fails the gate must never become a visible/indexed/promotable artifact.
    def _assert_rejected_unpublished(self, macro, r):
        assert r.returncode == 4, (r.returncode, r.stdout, r.stderr)
        assert "REJECTED" in r.stderr
        # nothing published: no theme_*.md and no leftover temp file
        assert list(macro.glob("theme_*.md")) == []
        assert list(macro.glob(".tmp_theme_*.md")) == []
        # index untouched (still the seed line only)
        assert (macro / "index.md").read_text() == "# index\n"

    def test_rejects_extra_body_ticker_not_published_not_indexed(self, store, tmp_path):
        macro = tmp_path / "vault" / "wiki" / "macro-themes"
        macro.mkdir(parents=True, exist_ok=True)
        (macro / "index.md").write_text("# index\n")
        supported = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        cf = tmp_path / "bad.md"
        cf.write_text(_theme_text(_five_good(), body_syms=supported + ["ZZZ"]))
        self._assert_rejected_unpublished(macro, self._run(cf, "ai supply chain trend"))

    def test_rejects_no_body_table_not_published(self, store, tmp_path):
        macro = tmp_path / "vault" / "wiki" / "macro-themes"
        macro.mkdir(parents=True, exist_ok=True)
        (macro / "index.md").write_text("# index\n")
        cf = tmp_path / "bad.md"
        cf.write_text(_theme_text(_five_good(), body_syms=[]))  # ledger clean, no display
        self._assert_rejected_unpublished(macro, self._run(cf, "ai supply chain trend"))

    def test_rejects_ark_mismatch_not_published(self, store, tmp_path):
        macro = tmp_path / "vault" / "wiki" / "macro-themes"
        macro.mkdir(parents=True, exist_ok=True)
        (macro / "index.md").write_text("# index\n")
        cf = tmp_path / "bad.md"
        cf.write_text(_theme_text(_five_good(), ark_syms=["AAA", "BBB", "CCC", "DDD"]))  # 4 != 5
        self._assert_rejected_unpublished(macro, self._run(cf, "ai supply chain trend"))


# ---- complete must refuse the parked state (gate cannot be bypassed) ----------

class TestCompleteRefusesParked:
    def test_complete_cli_refuses_waiting_for_agent_theme(self, store, tmp_path):
        tid = _add_parked(store)
        env = dict(os.environ)  # temp_env already set K2B_ORCH_DB etc.
        r = subprocess.run(
            [sys.executable, "-m", "scripts.lib.orchestrator_store", "complete", tid],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        )
        assert r.returncode != 0
        assert "verify-theme" in (r.stderr + r.stdout)
        # still parked -- not bypassed to done
        assert store.get_task(tid)["status"] == "waiting_for_agent_theme"


# ---- F1 (r2): the retired Kimi dispatch is unreachable by default -----------
# (this file's temp_env does NOT set K2B_ORCH_ALLOW_LEGACY_NARRATIVE)

class TestRetiredKimiDispatch:
    def test_resolve_command_none_by_default(self, store):
        from scripts.lib import orchestrator_profiles as profiles
        assert profiles.resolve_command("k2bi", "k2bi-narrative", {"narrative": "x"}) is None

    def test_resolve_command_argv_only_with_override(self, store, monkeypatch):
        from scripts.lib import orchestrator_profiles as profiles
        monkeypatch.setenv("K2B_ORCH_ALLOW_LEGACY_NARRATIVE", "1")
        argv = profiles.resolve_command("k2bi", "k2bi-narrative", {"narrative": "seed"})
        assert argv and argv[-1] == "--narrative=seed"

    def test_preflight_blocks_retired_by_default(self, store):
        from scripts.lib import orchestrator_profiles as profiles
        tid = store.add_task(
            assignee_profile="k2bi", command_key="k2bi-narrative", success_criteria="ok",
            permissions="analyst-command", entity_key="legacy-topic",
            payload={"narrative": "x" * 60}, status="ready",
        )
        ok, reason = profiles.preflight_k2bi(store.get_task(tid))
        assert not ok and "RETIRED" in reason

    def test_ready_k2bi_narrative_not_dispatched(self, store):
        tid = store.add_task(
            assignee_profile="k2bi", command_key="k2bi-narrative", success_criteria="ok",
            permissions="analyst-command", entity_key="legacy-poll",
            payload={"narrative": "x" * 60}, status="ready",
        )
        store.poll_once()
        st = store.get_task(tid)["status"]
        assert st != "running"  # never reaches the Kimi pipeline
        assert st == "blocked"


# ---- F2 (r2): index membership must be exact, not substring -----------------

class TestIndexExactMatch:
    def test_prefix_substring_does_not_count_as_indexed(self, store, tmp_path):
        macro = tmp_path / "vault" / "wiki" / "macro-themes"
        macro.mkdir(parents=True, exist_ok=True)
        # index references theme_ai-compute-demand; our theme is theme_ai.md
        (macro / "index.md").write_text(
            "| [[theme_ai-compute-demand|x]] | 2026-05-31 | 6 | candidates-pending-review |\n"
        )
        theme = macro / "theme_ai.md"
        fm = {"type": "macro-theme", "candidate-count": 5, "citation_ledger": _five_good()}
        theme.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# t\n")
        tid = _add_parked(store, entity="ai-prefix")
        ok, reason = store.verify_theme_complete(tid, str(theme))
        assert not ok and "not referenced" in reason


# ---- F4 (r2): URL must be structurally valid http(s) ------------------------

class TestUrlStrict:
    def test_scheme_only_no_netloc_fails(self, store, tmp_path):
        ledger = _five_good()
        ledger[0]["url"] = "https://"
        p = _write_theme(tmp_path / "t.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "url" in reason

    def test_url_with_whitespace_fails(self, store, tmp_path):
        ledger = _five_good()
        ledger[1]["url"] = "https://example.com/a b"
        p = _write_theme(tmp_path / "t.md", ledger)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "url" in reason


# ---- r3 finding: displayed candidate set must equal the validated ledger ----

class TestDisplayedSetCrossCheck:
    def test_pass_with_consistent_ark_and_body(self, store, tmp_path):
        # sanity: the default helper builds ark + body from the ledger -> passes
        p = _write_theme(tmp_path / "t.md", _five_good())
        ok, reason = store._verify_theme_gate(p)
        assert ok, reason

    def test_fail_no_body_candidate_table(self, store, tmp_path):
        p = _write_theme(tmp_path / "t.md", _five_good(), body_syms=[])
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "body candidate-table symbols" in reason

    def test_fail_extra_unsupported_body_symbol(self, store, tmp_path):
        good = _five_good()
        body = [str(r["symbol"]).upper() for r in good] + ["ZZZ"]  # ZZZ not in ledger
        p = _write_theme(tmp_path / "t.md", good, body_syms=body)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "body candidate-table symbols" in reason

    def test_fail_ark_scores_mismatch(self, store, tmp_path):
        good = _five_good()
        ark = [str(r["symbol"]).upper() for r in good][:4]  # only 4 of the 5 scored
        p = _write_theme(tmp_path / "t.md", good, ark_syms=ark)
        ok, reason = store._verify_theme_gate(p)
        assert not ok and "candidate_ark_scores symbols" in reason
