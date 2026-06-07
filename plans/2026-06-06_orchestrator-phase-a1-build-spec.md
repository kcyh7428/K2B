# Orchestrator Ship 2 — Phase A1 build spec (handoff #2)

Date: 2026-06-06
Feature: [[feature_k2b-orchestrator]] Ship 2, Phase A, increment **A1** (research half)
Status: DESIGN — pending Checkpoint-1 adversarial review, then Codex job
Builder: Codex (K2B repo) · Reviewer: Kimi (non-Codex, AR7) · Ship: K2B `/ship`
Depends on: K2Bi enabling-layer adapters — **MERGED** `kcstudio/K2Bi#10` → main `0ef1631` (2026-06-06)

## Why A1 first

Phase A splits A1 (research half) → A2 (strategy half) → A3 (ship). **A1 carries NO capital path** — it ends parked at the thesis-approval gate, before any strategy or engine interaction exists. So A1 is the safe, bounded first build: the token-replay and rollback-recovery carry-forwards (which belong to the A3 ship step) do not apply here. Build the low-risk half first, prove the one-conversation chain, then escalate.

## Architecture (settled post-adapter-merge — supersedes the Checkpoint-1 spec §3)

The original §3 ("conduct inline + call raw K2Bi helpers + code-locked backstop") was invalidated by the 2026-06-04 review and replaced by the merged adapter layer. The settled model:

**The orchestrator-agent (K2B, one chat) gathers the human's decisions inline, packages them into the adapters' typed inputs, and dispatches the merged K2Bi adapters + the existing unattended modules.** Safety is enforced *inside* the K2Bi adapters (in K2Bi code) — the orchestrator never re-implements a safety rule and never hand-writes K2Bi state.

Merged adapter signatures this build dispatches:
- `verify_and_generate_thesis(thesis_input, vault_root, *, claim_decisions, operator_override_reason, calx_override_acknowledged, vendor_warning_acknowledged, vendor_provenance, ...) -> ThesisGateResult` — enforces T7 and writes the thesis. The orchestrator supplies the `claim_decisions` it captured from Keith.
- (A2/A3 only: `write_complete_strategy_spec`, `run_full_ship` — NOT used in A1.)

## A1 stage map (reproduces the contract, Stages 3-8)

Legend: 🟢 Keith · ⚙️ dispatch unattended · ✋ inline conduct (agent + Keith) · ⏸ park.

| Contract stage | Mechanism | A1 detail |
|---|---|---|
| 3 Promote | 🟢✋ | "promote CDNS" → orchestrator advances the watchlist entry to `promoted`. **OPEN-1: confirm the non-interactive promote entrypoint** (`invest_screen --manual-promote`?). Stage-3 is the permanent human gate. |
| 4 Screen | ⚙️ | dispatch `invest_screen --enrich CDNS` (existing, already allowlisted in Ship-1a). Surface score+band; ask "run the chain?" |
| 5-7 Thesis | ✋⚙️ | orchestrator gathers sources inline (Chat-1 booster discipline: every claim fetched/repaired). **T7 inline:** surface each load-bearing claim; Keith marks verified/refused/override; capture vendor provenance + override framing. Package into `claim_decisions` + the override-ack flags; dispatch **`verify_and_generate_thesis(...)`** — the adapter enforces T7 and writes the ticker file (refuses if a claim is unverified without a framed override). |
| 7 Bear-case | ⚙️ | dispatch `run_bear_case` → PROCEED/VETO written to the ticker file. |
| 8 Park at thesis gate | ⏸🚦 | surface "thesis ready, bear PROCEED X%, N claims you verified." Keith → "approve" / "revise". VETO hard-stops (surface why). **A1 ends here** — no strategy, no engine. |

## Flight / park model (reuse Ship-1a)

- One chain flight per ticker, `entity_key=<TICKER>`, `k2b` profile, created parked.
- A1 park states: the promote gate and the thesis-approval gate map to `needs_human`; `payload.stage` records position so a fresh session resumes. The screen + thesis-compute + bear-case sub-dispatches are bounded worker tasks through the allowlisted door + preflight (the LRCX-smoke pattern).
- `/portfolio` surfaces the chain flight. No new daemon (in-session driver).

## Allowlist + preflight (security item)

Add to the K2Bi command allowlist for A1: the `verify_and_generate_thesis` dispatch and `run_bear_case`. `invest_screen --enrich` is already allowlisted (Ship 1a). Each dispatch keeps the Ship-1a preflight (clean K2Bi git tree, workspace lock). **No capital-path command in A1** → no special capital preflight needed yet (that arrives in A3).

## A1 MVP (binary)

Named bug: K2B-CANNOT-RUN-THE-RESEARCH-CHAIN-AS-ONE-CONVERSATION. **Pass:** from a single K2B conversation, "promote CDNS" reaches a **written, T7-verified thesis + a bear-verdict parked at the approval gate**, with no separate `/invest-coach` session and no manual stage-driving — and the T7 enforcement is real (the adapter refuses to write a thesis if a claim is left unverified without a framed override; prove it with a negative path).

## Boundary

A1 orchestrator code lives in the **K2B repo** (the dispatch/flight/conduct layer). It CALLS the merged K2Bi adapters (no K2Bi changes in A1). Builder Codex, Kimi review, ship through K2B `/ship`.

## Open items for A1 Checkpoint-1 (close before the Codex job)

1. **Promote entrypoint** — the non-interactive path to write a `promoted` watchlist entry from a theme candidate.
2. **ThesisInput population** — exactly what the orchestrator must assemble (claims, sources, scores) before `verify_and_generate_thesis`, and how much source-gathering it does inline vs dispatching `/research`.
3. **`claim_decisions` schema** — map the inline T7 conversation to the adapter's `ThesisClaimDecision` list + the override-ack flags (read the merged adapter's types).
4. **Resume payload** — minimum `payload.stage` to resume the A1 chain mid-flight (promote-done / screened / thesis-parked).

## Not in A1 (later increments)

A2: strategy-spec via `write_complete_strategy_spec` + backtest, park at strategy gate. A3: the ship step via `run_full_ship` — the capital path, where token-replay protection + rollback-recovery (the PR #10 carry-forwards) get designed and the heaviest review applies.

---

## Checkpoint-1 review disposition — Kimi (Codex fell back), 2026-06-06 — all 5 ACCEPTED

Review verdict NEEDS-ATTENTION (`primary_used=codex, fallback_used=true` — Codex was unavailable, Kimi reviewed; valid for a plan review since no code exists yet). All 5 findings are real gaps in THIS spec — closed below. No code was written; cheap to fix now.

### Closure #1 (was OPEN-3, HIGH) — record the exact adapter contract (no inferred schema)

The orchestrator's inline T7 conduct MUST emit exactly this per-claim shape at `0ef1631` — **no defaulting/coercing a missing field to satisfy the adapter (coercion = weakening the gate). A field you cannot fill is an `advisory`/`refused` claim, not a defaulted one.**

`ThesisClaimDecision` (frozen dataclass):
```
claim_id: str
claim_text: str
claim_load_bearing: bool
source_url: str | None
source_excerpt: str          # required; _validate_load_bearing_claim_evidence enforces non-empty for load-bearing claims
curated_framing: str
operator_mark: str           # MUST be one of ALLOWED_OPERATOR_MARKS = {verified, refused, override, advisory}
operator_note: str | None
source_vendor: str
spot_check_vendor: str | None = None
```
Function-level kwargs on `verify_and_generate_thesis(thesis_input, vault_root, *, claim_decisions, ...)`:
```
operator_override_reason: str | None        # required framing when any load-bearing claim is refused/override
calx_override_acknowledged: bool = False
vendor_warning_acknowledged: bool = False   # REQUIRED true if vendor_provenance is supplied (line 900-901)
vendor_provenance: dict[str, Any] | None
refresh: bool = False                        # overwrite an existing thesis (see Closure #5)
```
Enforcement the adapter already does (orchestrator must NOT duplicate, only feed): refuses on refused-without-framed-override; requires excerpts on load-bearing claims; vendor-must-differ; CALX override framing. Returns `ThesisGateResult(thesis_result, audit)` or raises `OrchestratorGateError`.

### Closure #2 (HIGH) — VETO is a terminal flight state, excluded from resume
On bear-case `VETO`, the chain flight transitions to a **terminal** state (e.g. `terminal_bear_veto`), NOT `needs_human`. `/portfolio` shows it as rejected, not as an attention sink. Resume logic: if `bear_verdict=VETO`, surface terminal rejection only — never offer approve/revise, never re-dispatch bear-case, never advance to A2. A VETOed ticker cannot proceed without an explicit fresh chain.

### Closure #3 (MEDIUM) — per-subtask completion flags, not a single stage enum
Replace `payload.stage` with per-subtask flags: `promote_done`, `screen_done`, `thesis_written`, `bear_done`. Resume checks the flags (handles partial success: thesis written but bear-case interrupted → resume re-dispatches only bear-case). **Idempotency:** re-dispatching `verify_and_generate_thesis` with identical `claim_decisions` must overwrite (refresh=True), not append/duplicate.

### Closure #4 (MEDIUM) — orchestrator enforces the `promoted` precondition (chain validity, not adapter's job)
Before the thesis dispatch, the **orchestrator** preflights: assert the watchlist entry status is `promoted`; fail closed with a human-readable error if not. This is chain-validity (orchestrator-owned), distinct from capital safety (adapter-owned). Prevents a stale/hand-constructed flight from running thesis on an un-gated ticker.

### Closure #5 (MEDIUM) — bounded revise loop + overwrite semantics
"Revise" at the thesis gate: **max 3 revisions** then terminal `needs_human` escalation. A revision **re-runs Stage 5-7 with a fresh source fetch** (no stale `vendor_provenance`). `verify_and_generate_thesis` is dispatched with `refresh=True` (overwrite, not append — confirmed: the adapter exposes a `refresh` kwarg). Previous `claim_decisions` are re-surfaced for amendment, not silently discarded.

### Open items status
- OPEN-1 (promote entrypoint): still confirm at build (Codex reads K2Bi); but Closure #4's preflight is the safety net regardless of the entrypoint.
- OPEN-2 (ThesisInput population): Codex reads `invest_thesis.ThesisInput`; the orchestrator assembles claims+sources+scores + the `claim_decisions` of Closure #1.
- OPEN-3: CLOSED (#1). OPEN-4: CLOSED (#3).

**A1 spec is now build-ready.** Next: the A1 Codex job (build to these closures), Kimi pre-commit review (Checkpoint-2), through K2B `/ship`.

---

## A1 build — final disposition ruling (K2B architect, 2026-06-07, after 19 review rounds)

Latest Kimi (real: `primary_used=minimax, fallback_used=false, no_fallback=true`) NEEDS-ATTENTION, 10 findings, on a **fully-green (484 tests), no-capital-path** build at **round 19**. This is the AR7 loop-termination point: reviewer satisfaction is not the goal, and the loop has drifted into fine-grained hardening. Lens: A1 has no capital path; the worker is **single-shot, non-persistent**; single-operator/single-Mac; K2Bi adapters are **trusted/merged**.

**ONE final bounded patch (real interface/state correctness, cheap):**
- #1 [CRITICAL] adapter stdout double-emit → genuine bug (two JSON envelopes break the worker parser). Fix: exactly one `print(encoded_output)`; original error JSON wins over cleanup error (cleanup detail → stderr).
- #8 [MEDIUM] SKILL.md: state `--i-checked-the-log` is **mandatory** for `force-verify-thesis-artifact` + include the exact failure text. Trivial doc fix.
- #6 [HIGH] IF contained: clear `screen_approved_by_operator` on zombie reclaim (or re-verify the screen artifact on resume-to-thesis). Bounded reclaim-correctness fix; if it balloons, defer.

**Disposition-only — reject/defer (non-material / fails-safe / out-of-threat-model):**
- #2 [CRITICAL→n/a] module-snapshot corruption → only bites **if the worker process is reused; it is single-shot non-persistent**. Document the non-reuse invariant; not a fix.
- #3 [HIGH] resume drift TOCTOU → fails SAFE (false-drift is recoverable via `clear-thesis-artifact`); microsecond window, single-operator. Defer.
- #4 [HIGH] terminal-TTL precedence → the test passes; conservative-earliest-expiry is safe; deferring expiry via a future `terminal_reason_at` is not a real use case. Defer (comment at most).
- #5 [HIGH] OOM on giant adapter result → K2Bi adapter is trusted/merged/tested, not a malicious-input threat model. Defer.
- #7 [MEDIUM] registry stat/read race → Kimi-admitted low-sev single-user; fail-safe error. Defer.
- #9 [MEDIUM] argparse-no-JSON → only fires if the worker omits a required arg (a separate worker bug). Defer.
- #10 [MEDIUM] bear_input preflight shape → fail-cheap-vs-fail-expensive optimization; already fails safe (task `failed` not silently wrong). Defer.

**HARD STOP.** Apply the bounded patch, hand back to the K2B ship manager. `/ship` runs the final Checkpoint-2 pass and ships **with this disposition attached** regardless of the next verdict. No round 21. 19 adversarial rounds on a no-capital build is the gate more than satisfied; the terminal authority is the architect disposition, not reviewer zero-findings.

---

## A1 live-MVP run #1 (2026-06-07) -- FOUND A REAL BUG (preflight status gate)

First live run on CDNS. The probe did its job: it surfaced a logic bug the fakes-based unit tests (87 green) missed.

**Bug:** `assert_a1_promoted_precondition` (`scripts/lib/orchestrator_profiles.py:425-441`) requires watchlist `status == "promoted"` "before thesis dispatch." But the A1 chain runs **screen (Stage 4) BEFORE thesis (Stage 5-7)**, and K2Bi's `invest_screen --enrich` advances the watchlist `promoted -> screened`. So after a real screen the status is `screened`, and the thesis preflight rejects it (`must be status 'promoted'; got 'screened'`). The chain can NEVER reach thesis through the normal promote->screen->thesis path. The unit tests seeded `status: promoted` directly and never exercised the screen->screened->thesis transition, so they passed over the broken gate.

**Secondary (not a bug):** K2Bi's `--manual-promote` fuses promote + screen (CDNS -> `screened`, quick_score 64, band C in one step). Valid watchlist entry. Means the orchestrator's separate Stage-4 screen dispatch is also redundant for a manually-promoted ticker (the K2Bi enrich would reject an already-`screened` ticker since it requires `promoted`).

**Fix (small, K2B-side):** `assert_a1_promoted_precondition` must accept the post-screen status -- require `status in {promoted, screened}` (thesis legitimately follows screen). Add a test that drives promote -> screen(->screened) -> thesis-preflight-passes. Optionally have the chain skip a redundant Stage-4 dispatch when the ticker is already `screened`.

**State:** CDNS is legitimately `promoted`+`screened` (64, band C) on the watchlist. Live MVP NOT passed -- blocked on this fix. pending-action stays open. A1.1 patch needed before re-running the probe.
