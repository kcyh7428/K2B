# Orchestrator Ship 2 — Phase A — Checkpoint-1 design spec

Date: 2026-06-04
Feature: [[feature_k2b-orchestrator]] Ship 2, Phase A
Status: DESIGN (Checkpoint-1) — no code until this passes adversarial review
Author: K2B (Scope B / architecture)

## 0. What this resolves

Phase A's note names one open design question: *"`/invest-coach` is interactive multi-turn; the orchestrator's dispatch model runs bounded non-interactive commands. How do you drive the chain as parked flights without losing the single-conversation feel? Resolve before any Phase A code."* This spec answers it.

## 1. Goal (the binding target)

Reproduce the acceptance contract in `feature_k2b-orchestrator.md` (the Option-A table, Keith confirmed 2026-06-02) **turn-for-turn**: take a promoted ticker through screen → thesis → bear-case → strategy spec → backtest → ship-to-engine as **one continuous K2B conversation**, parking at the human gates, never making Keith hand-drive a separate `/invest-coach` session. Out of scope: Stage 15 retro (= Phase B), Stages 0-2 (= Ship 1, shipped).

**Hard precondition (do not start coding):** Phase A trails K2Bi's manual second-strategy proof. Today K2Bi has NOT cleanly traded+journaled its 2nd strategy (PM checkpoint still lists it as the open gate; no recent `trade_closed` events). This spec may be written + reviewed now; **A-code starts only after that gate clears.**

## 2. The decisive finding (from the 2026-06-04 K2Bi control-surface audit)

The chain is NOT uniformly dispatchable. It splits cleanly:

**Unattended — dispatch as bounded flights (the Ship-1a pattern):**

| Stage | Entrypoint (non-interactive) | Reads | Writes |
|---|---|---|---|
| 4 screen | `python3 -m scripts.lib.invest_screen --enrich <SYM>` | watchlist/<SYM>.md (status `promoted`) | watchlist/<SYM>.md (status `screened` + scores) |
| 5-6 thesis-compute | `scripts.lib.invest_thesis.generate_thesis(ThesisInput, vault_root)` | populated `ThesisInput` (claims+sources+scores) | wiki/tickers/<SYM>.md (thesis_score, bull/bear) |
| 7 bear-case | `scripts.lib.invest_bear_case.run_bear_case(sym, bear_input, vault_root)` | ticker file w/ thesis_score | ticker file (bear_verdict PROCEED/VETO) |
| 10 backtest | `scripts.lib.invest_backtest.run_backtest(slug, vault_root)` | strategy_<slug>.md (order.ticker) | raw/backtests/<date>_<slug>_backtest.md |
| 11 ship | `python3 -m scripts.lib.invest_ship_strategy approve-strategy <path>` | strategy + ticker + backtest (all gates) | strategy_<slug>.md (status `approved` + sha); **code-locked validation** |

**Irreducibly interactive — K2Bi safety gates (cannot be a blind tap):**

- **T7 claim verification** (before the thesis is written): operator marks each load-bearing claim `verified / refused / override`. `generate_thesis()` raises if a claim is refused without override. This is the named defense against the CALX unsourced-LLM-claim failure (L-2026-04-30-001).
- **T10 strategy bucket-rules** (Stage 9): operator confirms each entry/exit rule. **No standalone module exists** — strategy-spec authoring lives only inside the coach; the writer is `scripts.lib.invest_coach.build_canonical_strategy_frontmatter()`.
- **T11 forward-guidance check** (Stage 9): operator pastes management guidance; the orchestrator flags any rule contradicting it; override needs a reason ≥20 chars.

**Key reconciliation with the contract:** the contract ALREADY bakes these in — Stage 5-7 reads *"thesis ready … 3 claims need your eyes"* (= T7), and Stage 9 reads *"drafts strategy_cdns.md … approve strategy"* (= T10+T11 drafting then approval). So the contract's "~5 taps" already means "park, surface what needs your eyes, you decide." No collision; the safety gates ARE the contract's gates.

## 3. Architecture decision — agent-native, one conversation (Direction 1)

The contract specifies one conversation → this is settled, not optional. The orchestrator-agent (K2B, in a single chat) does BOTH:

1. **Dispatches** the unattended stages (4, 5-6 compute, 7, 10, 11) as bounded parked flights through **Ship-1a's allowlisted K2Bi door + preflight** (clean git tree, workspace lock, etc.). Reuses the existing task-board + dispatcher + worker harness — no new control plane.
2. **Conducts** the safety gates (T7, T10, T11) and the contract's human gates **inline** with Keith, by **reading K2Bi's live coach procedure** at runtime (NOT a frozen copy) and feeding Keith's decisions into **K2Bi's own helper functions**. The orchestrator never hand-writes a K2Bi file and never re-implements a safety rule — K2Bi code does every computation + write.

This is the exact agent-native pattern Ship 1 (Chat-1/Chat-2) already uses: the orchestrator is the pause/lock/return ledger; the K2B agent does the in-session reasoning. Phase A just extends it across the analyst chain.

**Why this is safe despite conducting K2Bi's gates inline:**
- All writes go through K2Bi's validated helpers (`generate_thesis`, `run_bear_case`, `build_canonical_strategy_frontmatter`, the backtest/ship modules). The orchestrator passes human decisions IN; it does not author K2Bi state.
- The safety LOGIC stays single-homed in K2Bi (read live, not copied) — no second copy to rot (Memory-Layer-Ownership compliant).
- **`/invest-ship --approve-strategy` is the code-locked backstop.** Before anything reaches the engine it re-validates in K2Bi's own code: bear_verdict PROCEED + fresh, backtest sane, `## How This Works` present+non-empty, forward-guidance reconciled. Inline conduct cannot bypass it.

**Rejected alternative (Direction 2 — handoff):** park at the strategy step, send Keith to a real `/invest-coach --resume` session, resume on `status: approved`. Cleaner project boundary, but it **violates the contract** (Keith leaves the chat). Off the table by Keith's decision 2026-06-04.

## 4. Stage-by-stage execution map (reproduces the contract table)

Legend: 🟢 Keith · ⚙️ dispatch (unattended flight) · ✋ inline conduct (agent + Keith) · ⏸ park.

| Contract stage | Mechanism | Detail |
|---|---|---|
| 3 Promote | 🟢 ✋ | "promote CDNS" → agent advances the watchlist entry to `promoted` (Stage-3 = the permanent human gate). **OPEN ITEM: confirm the promote entrypoint** — audit saw `/screen <SYM> --manual-promote`; verify the non-interactive path to write a `promoted` watchlist entry from a theme candidate. |
| 4 Screen | ⚙️ | dispatch `invest_screen --enrich CDNS` → flight `screen:CDNS`. Surface score + band; ask "run the full chain?" |
| 5-7 Research→thesis→bear | ✋⚙️⏸ | agent gathers sources + drafts the Ahern 4-phase claims (inline, reusing the Chat-1 booster discipline: every claim fetched/repaired). **T7 inline:** surface claims, Keith marks verified/refused/override. Agent calls `generate_thesis(ThesisInput)` with the verification results → ticker file. Then ⚙️ dispatch `run_bear_case` → PROCEED/VETO. ⏸ park at thesis gate; surface "thesis ready, bear PROCEED X%, N claims you verified." |
| 8 Approve thesis | 🚦🟢 | Keith "approve" / "revise". On revise: re-conduct the relevant T6/T7 step. VETO from bear-case hard-stops the chain (surface why). |
| 9 Strategy spec | ✋⏸ | **T10 inline:** agent drafts entry/stop/targets/hold/kill + the "How This Works (plain English)" section, surfaces each bucket rule for Keith's confirm. **T11 inline:** agent asks Keith to paste current management guidance; flags any rule contradicting it; override needs reason ≥20 chars. Agent calls `build_canonical_strategy_frontmatter()` → writes strategy_<slug>.md (status `proposed`). ⏸ park at strategy gate. |
| 9-gate Approve strategy | 🚦🟢 | "approve strategy". |
| 10 Backtest | ⚙️ | dispatch `run_backtest(slug)` → surface Sharpe / maxDD / win / overfit-flag. (Backtest is partial in K2Bi — no walk-forward yet; surface that caveat honestly.) |
| 11 Ship | 🚦🟢⚙️ | "ship it" → dispatch `invest_ship_strategy approve-strategy <path>` → code-locked gate re-validates everything → commit → engine loads next tick. **Highest-risk dispatch** (capital-path); heaviest preflight + review; ships last. |
| 12-15 | 🔒 | engine trades + journals autonomously (K2Bi). Stage 15 retro = Phase B (separate). |

## 5. Flight / park model (reuse Ship-1a, minimal additions)

- **One flight per ticker-chain**, `entity_key = <TICKER>`, `k2b` profile, created parked. The flight is the durable ledger of "where is CDNS in the chain" so `/portfolio` shows it and it survives across sessions.
- Sub-dispatches (screen, bear-case, backtest, ship) are bounded worker tasks under that chain, each through the allowlisted door + preflight, exactly like Ship 1a's LRCX smoke.
- **Park states:** reuse `needs_human` for the 4 human gates; add a `payload.stage` field recording the current contract stage so a fresh session resumes mid-chain (Keith's reply in conversation is the resume trigger; the flight records position).
- **No new daemon** — consistent with the no-always-on descope. The agent drives in-session; the board is the lock + position ledger.

## 6. Non-negotiable constraints (carried from the feature note + audit)

1. Engine owns state; orchestrator reads engine-published vault snapshots only. No direct `ib_async`.
2. Validators are read-only from Claude. No mid-flight validator edits.
3. Kill-switch (`.killed`) is operator-only.
4. `/invest-ship --approve-strategy` IS the gate; orchestrator dispatches it, never re-gates separately.
5. Strategy specs authored via K2Bi's coach helper, never hand-written by the orchestrator.
6. Every K2Bi dispatch goes through Ship-1a's preflight (clean K2Bi git tree, workspace lock). **Allowlist expansion needed** (security-review item): add `invest_thesis`, `invest_bear_case`, `invest_backtest`, `invest_ship_strategy approve-strategy` to the K2Bi command allowlist. The ship command is capital-path — it must require the strictest preflight + a written gate.

## 7. Suggested build increments (so the risky step ships last)

Phase A is large; split it, matching K2Bi's "one item at a time" + capital-path-last discipline:

- **A1 — research half (no capital risk):** promote(3) → screen(4) → thesis+T7(5-7) → bear-case → park at thesis gate(8). MVP: "promote CDNS → a verified thesis + bear-verdict parked for approval, one conversation."
- **A2 — strategy half (no capital risk):** approve-thesis → strategy-spec+T10/T11(9) → backtest(10) → park at ship gate. MVP: "approved thesis → a complete, backtested strategy spec parked for ship approval."
- **A3 — the ship step (capital-path, heaviest review):** "ship it" → dispatch `/invest-ship` → engine loads. MVP: "approve → strategy reaches the engine via the code-locked gate." Ships only behind a written PM gate + full adversarial review.

Each increment is its own binary MVP + its own Checkpoint-2 review.

## 8. Overall MVP (unchanged from the feature note)

Named bug: K2B-CANNOT-RUN-THE-ANALYST-CHAIN-AS-ONE-CONVERSATION. Pass: from a single K2B conversation, "promote CDNS" reaches "strategy loaded by the engine" parking at exactly the 4 gates, matching the acceptance table turn-for-turn, with no separate `/invest-coach` session and no manual stage-driving.

## 9. Open items to resolve before A1 code (Checkpoint-1 must close these)

1. **Promote entrypoint (Stage 3):** confirm the non-interactive path that writes a `promoted` watchlist entry from a theme candidate (`--manual-promote`?).
2. **Thesis source-gathering (T5/T6):** how much does the agent gather inline vs dispatch `/research`? Define the `ThesisInput` the agent must populate before calling `generate_thesis`.
3. **Allowlist + preflight expansion:** exact command keys + the capital-path preflight for the ship dispatch (security review).
4. **Cross-session resume payload:** the minimum `payload.stage` schema to resume mid-chain.
5. **Revise loops:** on "revise thesis" / "recalibrate strategy", which K2Bi turn re-runs and what state resets.

## 10. What Phase A does NOT do

Stage 15 retro (Phase B), walk-forward/overfit backtest (K2Bi item 4.2), the fencing-token race (orthogonal), any always-on/daemon mode, any new trading methodology (K2Bi owns that).

---

## Checkpoint-1 review — Codex, 2026-06-04 — NEEDS-ATTENTION

Codex read the actual K2Bi modules. 5 findings. Disposition: **ALL ACCEPTED** — each is grounded in real code and invalidates a load-bearing assumption of the §3 architecture.

**The three HIGHs share one root cause:** the safety + completeness logic I assumed lived in dispatchable K2Bi *helpers* actually lives in K2Bi's *interactive coach/ship SKILL bodies*. So conducting it inline from K2B is either unsafe (drifts from the discipline) or crosses the boundary (hand-writes K2Bi state).

1. **HIGH (§3-4/6) — inline T7 does not preserve K2Bi's safety discipline.** `generate_thesis()` only validates the supplied verification *statuses* + note lengths; it cannot prove Keith clicked sources, saw side-by-side framing, respected vendor-must-differ, or saw the CALX override warning — those live in the coach SKILL. "Code-locked backstop" does NOT cover T7. Fix: keep T7 in real `/invest-coach`, OR build a typed K2Bi gate adapter that records source excerpts + marks + provenance + override framing before calling `generate_thesis()`.
2. **HIGH (§3/4/7 A3) — `invest_ship_strategy approve-strategy` is NOT the full `/invest-ship`.** The helper only flips the file to `status: approved`; the full ship (plan review, Keith's final approve/reject/defer, staging, commit, engine-load) lives in the invest-ship SKILL. Dispatching the helper inline bypasses review/approval OR leaves an approved dirty file that never commits/loads. Fix: A3 dispatches a K2Bi wrapper for the FULL ship workflow with explicit review + final-approval tokens, OR stops at a handoff.
3. **HIGH (§3-4/6) — the strategy-spec boundary is false.** `build_canonical_strategy_frontmatter()` returns frontmatter only, not a complete file; the body (`## How This Works`, bucket rules, accepted gaps) still has to be authored = K2Bi state. So "orchestrator never hand-writes K2Bi state" breaks here. Fix: a K2Bi helper that writes the COMPLETE proposed spec atomically from confirmed decisions, OR do it in `/invest-coach`.
4. **MEDIUM (§5/9) — cross-session resume is under-specified + unsafe.** Current `needs_human -> return` is untyped, replaces payload with `return_text`, doesn't merge `payload.stage` or preserve gate artifacts or validate the reply belongs to the gate; the 7-day TTL can silently cancel a capital-chain gate. Fix: typed gate payloads (artifact paths/shas, decision schema, merge semantics, stale-state checks, TTL/stale-watchlist recovery).
5. **MEDIUM (§4/9) — revise + VETO loops not implementable as written.** No definition of which artifacts are invalidated on revise-thesis / recalibrate-strategy / bear VETO, nor how `/portfolio` reflects VETO vs revision. Fix: deterministic state resets specified before A1.

### Revised path (folds the findings into the sequencing gate)

The findings converge on one conclusion: **Phase A's "one conversation" needs a K2Bi-side enabling layer first** — typed, dispatchable gate adapters that move the T7 / strategy-spec-write / full-ship safety+completeness logic OUT of the interactive coach SKILL and INTO code the orchestrator can call safely (via PR, architect-only). Without it, the safety-critical stages must hand off to the real coach (breaking "one conversation").

This reinforces the existing sequencing gate rather than fighting it: Phase A already trails K2Bi's manual 2nd-strategy proof. The natural order is now explicit —
1. K2Bi proves the chain by hand (current gate), and in doing so
2. K2Bi grows the typed gate-adapters (the enabling layer), then
3. the orchestrator wires the now-safely-dispatchable chain.

**New Checkpoint-1 open item (blocks A1):** spec the K2Bi enabling-layer adapters (thesis-gate adapter, strategy-spec writer, full-ship wrapper) as K2Bi PRs, OR Keith accepts a hybrid handoff for the safety-critical stages. This is a contract-level decision (it bends "one conversation") and is Keith's call.

---

## Adapter build — Round 6 disposition ruling (K2B architect, 2026-06-04)

Codex (K2Bi worker) surfaced that the Kimi review loop has drifted from "expose the gates as callable" into distributed-lock-manager + hostile-filesystem hardening. **Architect ruling: endorse Codex's disposition.** This is AR7 + deployment-bounding — the review is now hardening for threat models that do not fit K2Bi's actual deployment (single operator, single Mac, paper trading).

**Fix now (in scope, small, adapter-local):** rollback order/index cleanup; review-log-path symlink/traversal validation; trailer sanitization for `hints.trailers`; review `files`/`plan` arg validation; named repo-root timeout constant.

**Reject as out of scope (this adapter pass):**
- Server-side approval nonce / consumed-token store → belongs in the **orchestrator protocol (handoff #2)**, not the K2Bi adapter. **DEFERRED, not dropped** (see below).
- Distributed lock durability / stale-marker TTL / heartbeat → operational distributed-systems design; same-checkout locking + external `ship_lease_id` already cover the real deployment.
- Stat-to-write race / `O_NOFOLLOW` atomic-writer redesign → theoretical for a single-user Mac; the symlink/traversal validation already covers realistic input hardening.

**Loop-termination rule:** after the small fixes, run ONE more Kimi review. **If the only remaining NEEDS-ATTENTION findings are the rejected out-of-scope items, STOP** — that is the PM/architect disposition gate, not a signal to keep coding. Raise the PR with the Round-6 disposition attached. A reviewer that is technically-correct-but-scope-bounded is grounds for rejection.

**DEFERRED to handoff #2 (orchestrator wiring) — open item, do not lose:** approval-token **replay protection**. The ship adapter (capital path) takes explicit approval tokens; the issuance + single-use consumption protocol for those tokens is an orchestrator-protocol concern and MUST be designed in the handoff #2 spec. Tracked here so it survives.

---

## Adapter build — Round 7 disposition ruling (K2B architect, 2026-06-06)

Final Kimi review (real: `primary_used=minimax`, `fallback_used=false`) returned NEEDS-ATTENTION with 8 findings. Codex correctly stopped at the gate (PR condition not met).

**Reframe (bounds the chase):** this adapter is UPSTREAM of the engine's own code-locked gate — the engine loads only `status: approved` + traceable `approved_commit_sha`. A half-shipped/uncommitted/malformed strategy cannot reach the engine. So every finding here is correctness/recoverability/robustness, NOT "a bad trade gets through." That is the disposition lens.

**ONE final bounded patch (cheap + genuinely improves correctness on the capital path):**
- #3 [HIGH] approval-token timezone: reject naive `approved_at` (require tz-aware / UTC offset). One-liner; it's the malformed-token guard, not the deferred nonce store.
- #4 [HIGH] apply the EXISTING `_sha256_file_descriptor` helper at the `write_complete_strategy_spec` verify site (round-3 fix was applied to restore but not the write path). Consistency fix, not the O_NOFOLLOW redesign.
- #1 [CRITICAL→recoverability] persistent rollback marker + refuse-on-incomplete-rollback + structured result. Not capital-critical (engine loader backstops it) but good capital-path hygiene; Codex scopes it bounded.
- #2 [narrow] move lock dir out of `.git` (+ `.gitignore`) and add resolved-path traversal validation. NOT the distributed-lock redesign (still rejected); just the cheap local fixes.
- #8 fold in ONLY if it is the trivial `encoding='utf-8', errors='replace'` one-liner.

**Disposition-only (rejected — non-material, fails safe, or moot):**
- #5 [HIGH] broad stderr check → fails SAFE (false-fail blocks a ship, never leaks capital). Reject; revisit with a STRICT flag only if it becomes a real nuisance.
- #6 [HIGH] `git commit --only` edge cases → Codex CONFIRM the Ship-1a clean-tree preflight already excludes unmerged/merge-in-progress/submodule before dispatch. If yes → moot, disposition-only. If a gap exists → one cheap `git status` assertion, nothing broader.
- #7 [MEDIUM] mutable events in frozen dataclass → cleanliness, not risk. Reject.

**HARD termination (no more loops):** apply the bounded patch, run ONE more Kimi review, then **raise the PR regardless of the verdict**, with this disposition attached. If Kimi still NEEDS-ATTENTIONs on the disposition-only/non-material items, that is a reviewer-scope-bounded result, NOT a signal to keep coding — the terminal gate is Keith's K2Bi-PM-hat review of the PR, not reviewer satisfaction.

---

## PR #10 merge disposition (K2B architect, 2026-06-06)

PR `kcstudio/K2Bi#10` "Add orchestrator safety gate adapters" — MERGEABLE, **purely additive (+2923 / −0)**: a new `scripts/lib/invest_orchestrator_adapters.py` + tests + the 7 round-response logs + a `.gitignore` entry. **No edits to the coach/ship/thesis modules** → interactive paths + existing safety are untouched by construction. 1808 tests pass (incl. the refuse-closed safety tests). Round 7 fixes confirmed present (tz-aware required @1090, sha-fd at write verify @307, rollback marker, lock relocation).

**Recommendation: SAFE TO MERGE.** Decisive reasons:
1. **The adapter is INERT on merge** — nothing calls it in production until handoff #2 wires the orchestrator. Merging adds dormant capability; it changes no live path and cannot touch trading until #2 ships through its own gates.
2. **The engine's `approved_commit_sha` gate is the terminal capital backstop** downstream of every adapter path. No residual finding lets a bad strategy reach the engine.

Final Kimi (job 2459b9) 5 findings — disposition:
- #4 token cryptographic operator-binding → the replay concern, **already deferred to handoff #2 by ruling** (Kimi agrees it's "architecturally acceptable per PM ruling"). Carry forward.
- #5 broad stderr check → fails-SAFE (false-fail blocks a ship, never leaks capital). Rejected; optional STRICT flag later.
- #2 symlink/hardlink TOCTOU on file identity → requires a **local attacker on Keith's Mac**, out of threat model. Rejected.
- #1 rollback marker can block future ships if cleanup fails + #3 restore proceeds with possibly-dirty index → **fail-CLOSED liveness warts, not capital risk** (a blocked ship is recoverable by clearing the marker; nothing bad ships). NOTE: #1 was introduced by the Round-7 rollback-marker fix itself. Non-blocking for merge; **belongs in handoff #2's recovery-model design**, not a one-off patch (which would just reopen the loop).

### Carried forward to handoff #2 (orchestrator wiring) — do not lose
1. Approval-token **replay protection** + cryptographic operator-binding (#4) — design the issuance/single-use-consumption protocol here.
2. Rollback-marker **recoverability / auto-remediation** (#1, #3) — fold into the coherent cross-session resume + recovery model, not a patch.
3. (Minor) configurable stderr strictness (#5) — only if it proves a real nuisance in use.
