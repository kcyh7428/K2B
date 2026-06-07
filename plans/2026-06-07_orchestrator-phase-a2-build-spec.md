# Orchestrator Ship 2 -- Phase A2 build spec (strategy half, Stages 9-10)

Date: 2026-06-07
Feature: [[feature_k2b-orchestrator]] Ship 2, Phase A, increment **A2** (strategy half)
Status: DESIGN -- pending Checkpoint-1 adversarial review, then build (main session)
Extends: A1 (research half) -- GATE-PASSED 2026-06-07 (`2e0c679` + `9c0e9c4` + `0bacfbc`)
Boundary: A2 code lives in the **K2B repo** (dispatch/flight/conduct layer). It CALLS already-merged
K2Bi adapters (`write_complete_strategy_spec`) + an already-merged K2Bi module (`invest_backtest.run_backtest`).
**No K2Bi change is required** -- both callees exist + are tested at K2Bi `0ef1631` / main. No K2Bi PR.

## A2 binary MVP (written BEFORE code)

**Named bug: `K2B-CANNOT-RUN-THE-STRATEGY-HALF-AS-ONE-CONVERSATION`.**
Today, even with A1's approved thesis parked, Keith would have to leave the orchestrator chat and hand-drive
`/invest-coach` T10 (strategy spec) + `/invest-backtest` in a separate K2Bi session to get from an approved
thesis to a backtested, approval-ready strategy.

**Pass conditions (all must hold, single binary verdict):**
1. From the already-parked, thesis-APPROVED CDNS chain (`2026-06-07-001`), in ONE K2B conversation, the
   orchestrator dispatches `write_complete_strategy_spec` through the allowlisted K2Bi worker door and a
   **strategy artifact is written** at `K2Bi-Vault/wiki/strategies/strategy_cdns.md` (recorded only after the
   worker reaches `done` AND the file exists; sha-verified before the next dispatch).
2. The orchestrator then dispatches the **backtest** (`run_backtest`) as a second bounded command and a
   **backtest result** is produced (Sharpe / max-DD / win-rate / overfit `look_ahead_check` surfaced).
3. The flight **parks at the strategy-approval gate** (`strategy_approval_gate`) -- NO `/invest-coach`, NO
   session switch, NO capital path (`run_full_ship` is never dispatched), no engine touch.
4. **Negative path holds:** a controlled dispatch with a malformed/under-specified `decision` (e.g. empty
   `how_this_works`) is **refused by the K2Bi strategy writer** (`OrchestratorGateError`, adapter status
   `error`, exit 2) and writes NO strategy file. (Secondary available proof: the backtest sanity gate marks a
   look-ahead-cheat spec `look_ahead_check: suspicious`.)

A2 cannot be marked shipped unless 1-4 all execute live on CDNS.

## The BLOCKER A2 hits first (latent A1 finding #5) -- fix FIRST, with a regression test

`a1_resume_action_locked` re-validates the thesis artifact on every resume via
`_a1_thesis_artifact_payload_valid`, which re-SHAs the WHOLE ticker file (`thesis_path` = `CDNS.md`) against
the recorded `thesis_artifact_sha256`. Thesis + bear-case write the SAME file: bear appended
`## Bear Case (2026-06-07)` AFTER the thesis sha was recorded. So the recorded pre-bear sha
(`642acd0a...`) no longer matches the current file (`cf04f3de...`). The moment A2 calls
`a1_resume_action('2026-06-07-001')`, the oracle sets `thesis_artifact_drift_detected_at` and returns
`thesis_artifact_invalid` -- A2 can never advance past the thesis gate.

**Root cause:** the integrity check's purpose is "detect UNEXPECTED drift between thesis-verify and the next
step." Bear-case is an EXPECTED, recorded modification of the shared file. Once bear has legitimately run, the
verified-thesis baseline must advance to the post-bear content.

**Fix (option a -- re-anchor; chosen over option b "scope to thesis block" because block-scoping couples K2B
to K2Bi's file layout and is brittle):**

- **Forward fix (new flights never drift):** `a1_mark_bear_verdict`, when recording a `PROCEED` verdict,
  re-anchors `thesis_artifact_sha256` to the CURRENT (post-bear) thesis file content (guarded:
  `thesis_artifact_verified=True` + `thesis_path` exists + file non-empty). The protection window
  (thesis-verify -> bear-dispatch, re-checked by the bear preflight) is preserved; only AFTER a legitimate
  recorded bear modification does the baseline advance. (VETO is terminal -> never resumes -> skip re-anchor.)
- **Recovery helper (the existing pre-fix flight `2026-06-07-001`):** new
  `a1_reanchor_thesis_artifact(task_id, artifact_path=None, *, reason)` + CLI `reanchor-thesis-artifact`.
  **Guarded by `bear_done=True`** -- the ONLY legitimate cause of post-thesis drift is the bear append; if
  `bear_done` is False and the thesis drifted, that is real corruption -> operator must use
  `clear-thesis-artifact`, not re-anchor. The helper re-SHAs the current thesis file, updates
  `thesis_artifact_sha256`, and clears `thesis_artifact_drift_detected_at` / `_drift_reason`.

Regression test: a flight with `thesis_written + thesis_artifact_verified + bear_done` whose thesis file was
appended-to after the recorded sha -> `a1_resume_action` returns `thesis_artifact_invalid`; after
`a1_reanchor_thesis_artifact` (or after `a1_mark_bear_verdict` re-anchor on the forward path) it returns the
A2 action, not `thesis_artifact_invalid`.

## Architecture (extend A1, reuse A1.2 machinery)

The A2 chain extends the SAME parent ledger flight (`2026-06-07-001`, `k2b` profile, entity CDNS,
`needs_human`). New bounded K2Bi child dispatches join the chain via `parent_task` + the parent's `flight_id`
so the chain-scoped one-flight lock admits them; A2 children run STRICTLY SEQUENTIALLY (strategy child
terminal before the backtest child is created -- the lock allows at most one live child besides the parent).

### Callees (confirmed at K2Bi main `0ef1631`)

- `invest_orchestrator_adapters.write_complete_strategy_spec(decision: StrategySpecDecision, *, repo_root: Path)`
  -> writes `repo_root/wiki/strategies/strategy_<slug>.md`. NO git ops, NO backtest chaining. For K2Bi,
  `repo_root` is the **K2Bi vault** (`~/Projects/K2Bi-Vault`) -- that is where `wiki/strategies/` lives. The
  param is named `repo_root` (NOT `vault_root`) but the VALUE is the vault root. K2Bi-Vault is not a git repo;
  irrelevant, because `write_complete_strategy_spec` does no git.
- `invest_backtest.run_backtest(slug, *, vault_root: Path, ...)` -> reads
  `vault_root/wiki/strategies/strategy_<slug>.md` (`order.ticker`), pulls live yfinance bars (default
  fetcher), runs a lag-1 SMA(20)/SMA(50) baseline, writes an immutable capture under `vault_root/raw/backtests/`,
  and NEVER touches the strategy file (so the strategy artifact has NO shared-file drift problem). Sanity gate:
  `total_return>500%` OR `max_dd>-2%` OR `win_rate>85%` -> `look_ahead_check: suspicious` (the overfit/look-ahead
  rejector). This is a SEPARATE module from the adapter layer -- dispatched as its own bounded command, exactly
  as A1 dispatches raw `invest_screen --enrich`.

### `StrategySpecDecision` exact shape (frozen dataclass; no inferred schema -- A1 burned 4 attempts on this)

20 fields. The K2B adapter runner builds it via the SAME strict `_dataclass_from_dict` coercion thesis uses,
which requires EVERY field present (it does NOT honor dataclass defaults). So the decision payload MUST carry
all 20 fields (the 5 optionals as explicit `null` / `[]`):

Required (15): `slug`(`^[A-Za-z0-9][A-Za-z0-9_-]*$`), `symbol`, `sigid`, `risk_envelope_pct`(Decimal/str/float),
`order`(dict: ticker, side, qty[positive int], order_type[`MKT`|`LMT`], stop_loss, time_in_force; `limit_price`
required for LMT and MUST be None for MKT), `forward_guidance_metrics`(list[dict]: metric, locked_threshold_text,
guide_source_text, guide_range_text, sits_inside_guide[bool], operator_note?), `forward_guidance_status`
(`pass`|`override`|`waive`), `how_this_works`(non-empty), and non-empty string lists `bucket_rules`,
`entry_rules`, `stop_rules`, `target_rules`, `hold_rules`, `kill_rules`, `accepted_gaps`.
Optional (5, pass explicit null/[]): `forward_guidance_override_reason`, `forward_guidance_waive_reason`,
`regime_filter`(list|null), `date`(str|null), `extra_frontmatter`(dict|null).

`order.ticker` MUST equal the symbol -- the backtest reads `order.ticker` to fetch bars.

## File-by-file changes (all K2B repo)

### 1. `scripts/lib/orchestrator_k2bi_adapter.py` (runner)
- `import datetime as _dt`.
- Extend `_result_to_jsonable` to map `datetime.date`/`datetime.datetime` -> `.isoformat()` (additive; the
  `BacktestResult` carries `date`/`datetime` that would otherwise break `json` serialization in
  `_dump_bounded_output`). Safe for thesis/bear (no date/datetime in their results).
- `_resolve_allowed_repo_root(value)` -- thin sibling of `_resolve_allowed_vault_root` validating
  `repo_root` against `_allowed_vault_roots()` (repo_root == vault for K2Bi), error names `repo_root`.
- `_write_strategy_spec(payload)`: resolve repo_root; `decision = _load_json_value(payload,"decision")`
  (supports `decision_path` carrier); require `decision` dict; symbol-match (payload `symbol` ==
  `decision["symbol"]`); `decision_obj = _dataclass_from_dict(ioa.StrategySpecDecision, decision)`;
  `ioa.write_complete_strategy_spec(decision_obj, repo_root=repo_root)`; return
  `{"status":"ok","result":_result_to_jsonable(result)}`.
- `_run_backtest(payload)`: resolve vault_root; require `slug` (non-empty str); import `invest_backtest`;
  `run_backtest(slug, vault_root=vault_root)`; return ok envelope.
- Register subcommands `write-strategy-spec`, `run-backtest` in `main()`'s subparser loop + dispatch.

### 2. `scripts/lib/orchestrator_profiles.py` (allowlist + preflight)
- `k2bi_allowed_commands()`: add `k2bi-write-strategy-spec` -> `[py, A1_ADAPTER_RUNNER, "write-strategy-spec"]`
  and `k2bi-run-backtest` -> `[py, A1_ADAPTER_RUNNER, "run-backtest"]`.
- `resolve_command()`: for both keys, append `_adapter_payload_args(payload)` (payload_path carrier, same as
  thesis/bear); return None if the carrier is absent/invalid.
- `_preflight_a1_symbol_matches_entity`: add the two keys to the ticker-scoped set (symbol == entity ==
  canonical-registry).
- repo_root/vault_root match: extend `_preflight_a1_vault_root_matches_profile` (or a small sibling) so the
  strategy key validates `repo_root` and the backtest key validates `vault_root` against the profile vault.
- `_preflight_a1_adapter_payload_shape`: for `k2bi-write-strategy-spec` (inline-json carrier only) require
  `decision` dict with `slug` + `symbol`; for `k2bi-run-backtest` require non-empty `slug`. (Path-carried
  payloads pass here and are gated in the runner -- same split as thesis claim_decisions.)
- `preflight_k2bi()`: route the two new keys through vault/repo-root match + payload-shape (NO promoted
  precondition -- that is thesis-specific; chain-validity "thesis approved before strategy" is owned by the
  resume oracle, NOT a child preflight, to avoid re-introducing an over-strict gate like the A1.1 bug).

### 3. `scripts/lib/orchestrator_store.py` (resume flags, helpers, oracle, drift fix)
- Drift fix: `a1_mark_bear_verdict` re-anchor on PROCEED (forward); `a1_reanchor_thesis_artifact` +
  `reanchor-thesis-artifact` CLI (recovery, guarded by `bear_done`).
- New A2 flags on the parent payload: `thesis_approved`, `strategy_spec_written`, `strategy_path`,
  `strategy_dispatch_started_at`, `strategy_artifact_verified`, `strategy_artifact_sha256`,
  `strategy_artifact_verified_at`, `strategy_artifact_drift_detected_at`/`_reason`, `backtest_done`,
  `backtest_artifact_path`, `backtest_look_ahead_check`, `strategy_approved`, `strategy_revision_count`.
- New helpers (mirror the thesis/screen ones; locked, terminal-guarded, artifact-verified-before-flag):
  - `a1_approve_thesis(task_id)` -> sets `thesis_approved=True` (guard: bear_done + verdict PROCEED +
    thesis_artifact_verified). CLI `approve-thesis`.
  - `a1_record_strategy_done(task_id, artifact_path)` -> verify file exists + non-empty -> set
    `strategy_spec_written=True` + `strategy_path` + `strategy_done_at`. CLI `record-strategy-done`.
  - `a1_verify_strategy_artifact(task_id, path=None)` -> sha/mtime verify -> `strategy_artifact_verified` +
    sha. CLI `verify-strategy-artifact`. (`_a1_strategy_artifact_payload_valid` mirrors the thesis one; the
    backtest never writes the strategy file, so no re-anchor needed.)
  - `a1_record_backtest_done(task_id, artifact_path, *, look_ahead_check=None)` -> verify capture exists ->
    `backtest_done=True` + path + look_ahead_check. CLI `record-backtest-done`.
  - `a1_approve_strategy(task_id)` (guard: backtest_done) -> `strategy_approved=True`. CLI `approve-strategy`.
  - `a1_register_strategy_revision(task_id)` -> bounded (3) revise: clears strategy + backtest flags, re-runs
    from `dispatch_strategy`; 4th -> terminal `needs_human` + `terminal_reason=strategy_revision_limit_exceeded`.
    CLI `register-strategy-revision`. Uses a SEPARATE `strategy_revision_count` (does not touch thesis
    `revision_count`).
- `a1_resume_action_locked` extension (AFTER `if not bear_done: return "dispatch_bear_case"`):
  ```
  if not thesis_approved: return "thesis_approval_gate"      # backward-compatible: A1 default unchanged
  if not strategy_spec_written: return "dispatch_strategy"
  if strategy_artifact_drift_detected_at: return "strategy_artifact_invalid"
  if not strategy_artifact_verified: return "verify_strategy_artifact"
  if not _a1_strategy_artifact_payload_valid(payload): set drift; return "strategy_artifact_invalid"
  if not backtest_done: return "dispatch_backtest"
  if not strategy_approved: return "strategy_approval_gate"  # A2 ENDS here (parked)
  return "strategy_approved_await_ship"                       # A3 (ship-to-engine) is the next increment
  ```
  Strategy-revision terminal park handled like the thesis revision-limit park (logically terminal).
- `_payload_is_logically_terminal`: also return True on `terminal_reason=strategy_revision_limit_exceeded`
  (already covered by the generic `terminal_reason` truthiness check -- verify, add test).

### 4. `.claude/skills/k2b-orchestrator/SKILL.md`
- New "A2 chain conductor -- strategy spec + backtest -> strategy gate" section (mirror the A1 section):
  durable model additions, the two new allowlisted dispatches, the exact `StrategySpecDecision` contract, the
  re-anchor recovery note, and the Stage 9-10 procedure (approve-thesis -> dispatch_strategy ->
  record/verify-strategy -> dispatch_backtest -> record-backtest -> park at strategy gate; approve/revise).
- Hard constraints restated: NO `run_full_ship`, NO engine touch, NO capital path in A2.

### 5. `tests/orchestrator/test_orchestrator_a1.py` (or a new `test_orchestrator_a2.py`)
Regression + unit coverage: drift detection + re-anchor (forward + recovery, bear_done guard); A2 oracle
sequence; thesis_approved backward-compat (bear_done w/o thesis_approved still -> `thesis_approval_gate`);
record/verify-strategy + backtest done; strategy revision bound; strategy preflight (symbol/entity/canonical,
repo_root match, payload shape); allowlist resolves both keys with payload carrier; adapter runner builds a
real `StrategySpecDecision` via a realistic fake K2Bi workspace + serializes a `BacktestResult` with
date/datetime; negative path (strategy writer refuses empty `how_this_works`). Target: full orchestrator suite
stays green (269 baseline + A2 additions).

## Hard constraints (non-negotiable)
- A2 has NO capital path. `run_full_ship` is NEVER added to the allowlist or dispatched.
- No engine touch, no commit of K2Bi, no hand-written K2Bi state or hand-set orchestrator flags (every flag is
  set by a locked helper only after the worker reaches `done` AND the artifact exists + is verified).
- `~/Projects/K2Bi` must be a CLEAN git tree on origin/main before each dispatch (worker preflight enforces);
  if dirty, surface to Keith -- do not mutate it. (Strategy/backtest write to K2Bi-VAULT, a separate non-git
  dir, so they do not dirty the repo tree.)
- No K2Bi code change (both callees exist + tested). If one were found broken mid-build, it goes via a K2Bi PR,
  never a direct edit.

## Checkpoint-1 disposition -- Codex, 2026-06-07 (NEEDS-ATTENTION, primary_used=codex, fallback_used=false) -- all 4 ACCEPTED

All 4 are real, material correctness gaps and cheap to close at the plan stage (no code yet). Folded in:

**C1 (HIGH) -- strategy payload could backtest the WRONG ticker.** Runner only checked
`payload.symbol == decision.symbol`, but `run_backtest` reads `order.ticker`, which K2Bi preserves verbatim;
`_validate_strategy_shape` does not enforce cross-field equality. So `strategy_cdns.md` could carry
`order.ticker: SPY` and the backtest would fetch SPY -> false gate evidence. **Fix:** `_write_strategy_spec`
enforces normalized equality `payload.symbol == decision["symbol"] == decision["order"]["ticker"]` (all
upper-trimmed) BEFORE building the dataclass; refuse otherwise. Negative test: mismatched `order.ticker` is
rejected, no file written.

**C2 (HIGH) -- repo_root/vault_root guard too broad -> wrong-vault write.** `_allowed_vault_roots()` includes
`K2B_VAULT_PATH`, and preflight treats `payload_path` contents as opaque, so a path-carried strategy payload
with `repo_root=$K2B_VAULT_PATH` could write the strategy into the K2B vault. **Fix:** add
`_allowed_k2bi_vault_roots()` (K2Bi-only: `K2B_ORCH_ADAPTER_VAULT_ROOT?`, `K2BI_VAULT_PATH?`,
`~/Projects/K2Bi-Vault` -- **excludes** `K2B_VAULT_PATH`) and resolve BOTH strategy `repo_root` and backtest
`vault_root` against it (new `_resolve_allowed_k2bi_root`). The A2 preflight ALSO asserts repo_root/vault_root
== `k2bi_vault()` (defense in depth). Thesis/bear keep their existing `_resolve_allowed_vault_root` +
preflight-match (A1 shipped; not in scope to change). Payload-path test: `repo_root=$K2B_VAULT_PATH` refused.

**C3 (HIGH) -- re-anchor could mask corruption.** `bear_done=True + file exists` does not prove the file is
exactly the bear-written artifact; an unrelated edit landing after bear could be blessed. The bear child
result carries path/verdict but NO post-write sha (changing that needs a K2Bi PR -- out of scope). **Fix
within the no-K2Bi-change constraint:** (a) FORWARD -- `a1_mark_bear_verdict` re-shas SYNCHRONOUSLY at the
moment the conductor records `done` (the conductor's single step right after the worker finishes; no
concurrent writer in the in-session flow -- same threat model as A1.2's deferred post-claim race). (b)
RECOVERY -- `a1_reanchor_thesis_artifact` REQUIRES an explicit `checked_log=True` ack (`--i-checked-the-log`,
mirroring `force-verify-thesis-artifact`) so the operator attests the current file is the legitimate
post-bear thesis+bear artifact, AND is guarded by `bear_done=True`. No ack -> refuse. This converts "bless
whatever is there" into an operator-attested recovery action.

**C4 (MEDIUM) -- nested forward-guidance metric not strict (`"false"` -> True).** `_dataclass_from_dict`
treats `forward_guidance_metrics: list[dict[str, Any]]` values as `Any`, so a string `sits_inside_guide:
"false"` survives, and K2Bi's `bool(m["sits_inside_guide"])` flips it to True. **Fix:** runner-side nested
validation in `_write_strategy_spec` -- each metric must be a dict carrying the required keys (metric,
locked_threshold_text, guide_source_text, guide_range_text, sits_inside_guide) and `sits_inside_guide` MUST be
a real bool (reject str/int). Test: string `sits_inside_guide` rejected.

No second Checkpoint-1 round -- the findings are concrete (not fine-grained style); they are implemented
directly with regression tests.

## Checkpoint-2 disposition -- Codex, 2026-06-07 (6 rounds -> APPROVE)

Pre-commit adversarial review converged over 6 Codex rounds (13 findings, every one a real
correctness/evidence-integrity gap on the dispatch boundary -- not style -- each fixed with a regression
test). Round-6 verdict APPROVE: "No new HIGH-severity correctness blocker is defensible under the stated A2
threat model. Remaining concerns are hand-crafted/direct-helper misuse, fine-grained hardening, or
already-disposed evidence-binding cases, so this loop can terminate."

- **R1 (6):** oracle requires bear `PROCEED` (not just non-VETO); forward re-anchor; `record-strategy-done`
  clears stale downstream flags; `record-backtest-done` rejects unknown `look_ahead_check`; A2 root allowlist
  drops `K2B_VAULT_PATH`; `poll_once` cancels an A2 child whose command != parent oracle stage.
- **R2 (3):** runner binds backtest result symbol to dispatched symbol; `record-backtest-done` binds capture
  to the recorded strategy slug; A2 root allowlist narrowed to EXACTLY the profile vault; backtest invariant
  moved into the oracle + `approve-strategy`, not only the setter.
- **R3 (1):** backtest verdict is ARTIFACT-derived (parse the capture frontmatter), CLI value must match.
- **R4 (1):** `_a1_backtest_payload_valid` re-opens + sha-binds the capture at the gate (reparse fallback for
  pre-sha flights) -- symmetric to the thesis + strategy artifact checks.
- **R5 (1):** `record-strategy-done` binds the strategy artifact to the parent `entity_key` (frontmatter
  ticker == entity).

Net result: the full evidence chain is `entity_key -> strategy (frontmatter ticker + sha, gate-revalidated)
-> backtest (capture strategy_slug + look_ahead_check + sha, gate-revalidated)`. AR7 architect disposition:
the loop terminated on Codex's own APPROVE + "this loop can terminate", not on a forced override.

## Discipline
Checkpoint-1 plan review (this file) BEFORE code. Checkpoint-2 pre-commit review via `/ship`. AR7
architect-disposition terminates the review loop once findings go fine-grained on this no-capital,
single-operator increment -- but every real correctness finding gets a regression test. Then `/ship`:
mark A2 gate-passed, update Shipping Status + Full Scope Tracker (Stages 9-10) + `wiki/concepts/index.md`,
DEVLOG, wiki/log, `/sync`. Next increment after A2 is **A3** (ship-to-engine, Stage 11 -- the capital path).
