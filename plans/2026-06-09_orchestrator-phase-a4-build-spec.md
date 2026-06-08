# Orchestrator Phase A4 build spec -- operator-approved limits-apply (K2B wiring)

Status: Checkpoint-1 CLEARED (Codex NEEDS-ATTENTION, 4 findings ALL accepted + folded below) ->
Codex build -> Kimi Checkpoint-2 -> /ship.
Author: K2B (Opus). Increment: Ship-2 / A4. Feature: [[feature_k2b-orchestrator]].

## 0. Checkpoint-1 dispositions (Codex plan review `2026-06-08T16-08-51Z_7cd86e`, NEEDS-ATTENTION)

All 4 findings ACCEPTED and folded into the body below before any code.

- **F1 (High) -- worker capital-timeout.** `apply_approved_limits` mutates + commits with
  exception-driven rollback AFTER mutation, but the worker gives the 1200s ship timeout ONLY to
  `k2bi-run-full-ship` (`SHIP_COMMAND_KEY`); everything else gets 540s. A 540s SIGKILL mid-apply
  bypasses K2Bi's rollback -> partial config.yaml/proposal. **Fix:** make the worker's capital set
  `{k2bi-run-full-ship, k2bi-apply-limits}` share `K2B_ORCH_SHIP_CMD_TIMEOUT` (new build item 4.0),
  with a timeout test like A3.
- **F2 (High) -- preflight tree rule too loose.** K2Bi `apply_approved_limits` refuses ANY
  pre-existing staged OR unstaged change to {proposal, config} (`_assert_no_staged_target_changes`
  + `_assert_no_unstaged_target_changes`). The draft `_git_tree_clean_except_targets` allowed
  ` M`/`A `/`MM` on targets, so K2B would mint a dual-sha token over a dirty config + burn an
  attempt K2Bi must reject. **Fix:** A4 tree rule = config.yaml CLEAN (no staged/unstaged change),
  proposal untracked `??` or clean (never staged/modified), nothing else dirty. Mirror K2Bi's two
  asserts so we fail BEFORE minting (4.1).
- **F3 (High) -- inspector too weak + too narrow.** `propose_limits` supports position_size /
  trade_risk / leverage / market_hours / whitelist; containment on `instrument_whitelist.symbols`
  can miss extra unauthorized symbols. **Fix:** SCOPE A4 to `instrument_whitelist` only (reject other
  rules at record + preflight) and the inspector asserts the committed config's
  `instrument_whitelist.symbols` EXACTLY equals the proposal's expected `after` (normalized
  set-equality, no extras), else NOT `committed` (4.3 inspector + scope guard).
- **F4 (Medium) -- preflight can't read payload_path fields.** `preflight_k2bi` parses ONLY the task
  ROW payload; it never dereferences `payload_path`. **Fix:** the apply child's DB row payload MUST
  carry `proposal_path`, `approval`, `required_primary` inline (for preflight) alongside
  `payload_path` (the adapter fd-guard file) -- exactly as the A3 ship child's row carries
  `strategy_path` + `approval` (4.2/4.4).

## 1. What A4 is

After Keith's explicit approval, the orchestrator applies an operator-approved **limits
proposal** (e.g. a whitelist add) the same way A3 ships a strategy: **approve -> bound token ->
a K2Bi adapter owns the commit + rollback -> verify**. It removes the manual `/invest-ship
--approve-limits <file>` session-switch. The **approval stays human** (Keith reads + approves the
exact proposal); only the **execution** (patch config.yaml + commit, with rollback) is
orchestrator-driven.

The K2Bi half is ALREADY SHIPPED (`apply_approved_limits`, K2Bi `d424b62`). A4 is **K2B
orchestrator wiring only**: a new allowlisted command key, a capital-style preflight, a runner
subcommand, A4 store helpers + resume ladder + a human gate + an independent inspector, the
poll-once stage guard entry, and the A4 conductor in the SKILL.

A4 is a **standalone flight** (its own entity = the proposal slug, its own lifecycle), spawnable
by the A3 ship-gate whitelist branch as an inline sub-step, then the A3 ship resumes.

## 2. The REAL shipped K2Bi interface (match EXACTLY -- the A1/A2/A3 landmine)

From `~/Projects/K2Bi/scripts/lib/invest_orchestrator_adapters.py`:

```python
apply_approved_limits(
    proposal_path: Path,
    *,
    approval: LimitsApproval,
    review_runner: ReviewRunner | None = None,
    approve_handler: ApproveLimitsFunc = iss.handle_approve_limits,
    git_runner: GitRunner | None = None,
    config_path: Path | None = None,      # defaults to repo_root/execution/validators/config.yaml
    now_utc: _dt.datetime | None = None,  # <-- NOTE: now_utc, NOT `now` (handoff prose said `now=`)
    required_primary: str = "minimax",
) -> LimitsApplyResult

@dataclass(frozen=True)
class LimitsApproval:
    final_approval_token: str
    approved_by: str
    approved_at: str
    apply_lease_id: str        # <-- apply_lease_id, NOT ship_lease_id
```

Token (LOCKED, validated by K2Bi `_validate_limits_approval`):
```
APPROVE_LIMITS:<slug>:<proposal_sha256>:<config_sha256>:<approved_at>:<apply_lease_id>
```
- colon-delimited; binds BOTH the proposal sha AND the on-disk `config.yaml` sha.
- `<slug>` = K2Bi `iss._derive_limits_slug(proposal_path)`: regex
  `^\d{4}-\d{2}-\d{2}_limits-proposal_(.+)$` on the filename stem, else the bare stem. K2B MUST
  derive the SAME slug.
- `approved_at` = ISO-8601 UTC with explicit `+00:00`; K2Bi enforces it is within **300s** clock
  skew of apply-time (`_require_recent_utc_timestamp`, max_clock_skew_s=300). => mint the token
  immediately before dispatch; dispatch promptly. A stale token fails SAFE (rollback).
- `apply_lease_id` must match K2Bi `_SHIP_LEASE_RE = ^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$`.

K2Bi `apply_approved_limits` behavior (do NOT reimplement -- wrap): per-slug flock; refuses stale
rollback marker (`.k2bi-orchestrator/rollback/limits_<slug>.json`); validates the token; clean-tree
preflight allowing ONLY {proposal, config.yaml} (with `--untracked-files=no`, so a brand-new
untracked proposal is invisible/allowed) AND refusing pre-existing staged OR unstaged changes to
those two targets; plan-reviews the proposal; calls `handle_approve_limits` (applies the `## YAML
Patch` + flips proposal `proposed->approved`); verifies BOTH files' post-handler bytes; diff-reviews
both; `git add` + `git commit --only` proposal + config.yaml. ANY failed gate -> restore both
files' bytes + raise `OrchestratorGateError(rollback_result=...)`. It COMMITS to the K2Bi repo.

`LimitsApplyResult(slug, commit_message, commit_hints: iss.LimitsCommitHints, plan_review,
diff_review, events)`. `LimitsCommitHints` has `.slug .rule .change_type .transition .approved_at
.parent_commit_sha .config_path .file ...`.

Proposal shape (K2Bi `review/strategy-approvals/2026-05-04_limits-proposal_instrument_whitelist-add-G.md`):
frontmatter `type: limits-proposal`, `status: proposed`, `applies-to: execution/validators/config.yaml`;
body has `## Change` (rule/change_type/ticker/field/before/after) + `## YAML Patch` (before/after
fenced blocks). `REQUIRED_LIMITS_FIELDS = {type, status, applies-to}`.

## 3. The propose step is conductor-driven, NOT a new dispatch

The proposal markdown is generated by the EXISTING K2Bi read-only generator
`scripts/lib/propose_limits.py` (CLI `python3 -m scripts.lib.propose_limits write --text "<ask>"
--rationale "..."`). It NEVER opens config.yaml in write mode (its hard rule). The A4 conductor
runs it directly (cwd=K2Bi), exactly as Chat-1/Chat-2 do their own in-session work; the orchestrator
is the durable ledger. **A4 adds NO `k2bi-propose-limits` command key.** Only the dangerous
APPLY (commit) goes through the capital wiring below.

The 4th human gate = Keith reads the generated proposal's actual YAML patch and approves THAT exact
proposal (its sha is then bound into the token).

## 4. Build items (mirror A3 verbatim; prefix `a4_` / `limits_`)

### 4.0 `orchestrator_worker.py` (F1 -- capital timeout)
The worker today gives the 1200s ship timeout to a single key: `SHIP_COMMAND_KEY =
"k2bi-run-full-ship"`, checked `if command_key == SHIP_COMMAND_KEY` (lines 13-15, 194-200).
- Replace the single constant with a set: `CAPITAL_COMMAND_KEYS = frozenset({"k2bi-run-full-ship",
  "k2bi-apply-limits"})` (keep `SHIP_COMMAND_KEY` as an alias if other code references it, or update
  references). Change the timeout branch to `if command_key in CAPITAL_COMMAND_KEYS:` -> use
  `K2B_ORCH_SHIP_CMD_TIMEOUT` (1200s default); else `K2B_ORCH_CMD_TIMEOUT` (540s).
- Rationale: `apply_approved_limits` runs plan review + `handle_approve_limits` mutation + diff
  review + commit; a 540s SIGKILL mid-mutation bypasses K2Bi's exception-driven rollback and can
  leave config.yaml/proposal partial. The independent inspector would then classify
  `partial_approved_uncommitted` (correct) but the point is to not induce it via timeout.
- Test: assert `k2bi-apply-limits` resolves to the 1200s timeout (mirror the A3 timeout test).

### 4.1 `orchestrator_profiles.py`
- **Command key.** Add to `k2bi_allowed_commands()`:
  ```python
  "k2bi-apply-limits": ["python3", str(A1_ADAPTER_RUNNER), "apply-limits"],
  ```
- **resolve_command.** Add `"k2bi-apply-limits"` to the set that appends `_adapter_payload_args(...)`
  (the same `{thesis, bear, write-strategy, backtest, author, full-ship}` block). No symbol arg (it
  carries `payload_path`/`payload_json` like the adapter commands).
- **Slug + token + tree helpers** (new, mirror the A3 ones):
  - `_limits_slug_from_proposal_path(path) -> str | None`: regex
    `^\d{4}-\d{2}-\d{2}_limits-proposal_(.+)$` on `path.stem`, else `path.stem`. MUST equal K2Bi
    `_derive_limits_slug`.
  - `_limits_token(slug, proposal_sha, config_sha, approved_at, lease) -> str` =
    `f"APPROVE_LIMITS:{slug}:{proposal_sha}:{config_sha}:{approved_at}:{lease}"`.
  - `_proposal_path_under_repo(path_value) -> (Path|None, reason)`: mirror `_strategy_path_under_repo`
    but require the file under `<k2bi_repo>/review/strategy-approvals/` and name matching
    `*_limits-proposal_*.md` (or at least `<...>.md` with a derivable slug). Reject path traversal.
  - **`_git_tree_clean_for_limits_apply(repo, proposal: Path, config: Path) -> (bool, reason)`**
    (F2 -- TIGHTER than A3; mirror the K2Bi gate so we fail BEFORE minting/burning an attempt):
    1. config.yaml: NO staged change (`git diff --cached --quiet -- <config_rel>` rc==0) AND NO
       unstaged change (`git diff --quiet -- <config_rel>` rc==0). (mirrors K2Bi
       `_assert_no_staged_target_changes` + `_assert_no_unstaged_target_changes`.)
    2. proposal: may be **untracked `??`** (just authored) OR fully clean; must NOT be staged or
       tracked-modified (no `A `/` M`/`M `/`MM`/`AM` on the proposal).
    3. whole tree: `git status --short --untracked-files=all` may contain ONLY the proposal-as-`??`
       line; ANY other dirty/untracked/unmerged line (including config) -> refuse. Reuse
       `_status_line_paths`; the ONLY allowed status code is `??` and ONLY for the proposal rel-path.
    Do NOT reuse the permissive `_status_line_is_allowed_a3_target` (it allows ` M`/`A ` which K2Bi
    rejects). Leave A3's `_git_tree_clean_except_target` untouched.
- **`_preflight_a4_limits(task, payload) -> (bool, reason)`** (mirror `_preflight_a3_capital`).
  `payload` is the task ROW payload (`_task_payload(task)`), which per F4 carries `proposal_path` +
  `approval` inline:
  1. kill-switch READ-ONLY: refuse if `<k2bi_vault>/System/.killed` exists.
  2. validators config present + non-empty: `<k2bi_repo>/execution/validators/config.yaml`.
  3. proposal: `_proposal_path_under_repo(payload["proposal_path"])`; file exists; frontmatter
     `type == limits-proposal`, `status == proposed`, `applies-to ==
     execution/validators/config.yaml`. Derive slug.
  4. **SCOPE GUARD (F3):** parse the proposal `## Change` block; refuse unless `rule ==
     instrument_whitelist` and `change_type == add`. A4 this increment handles ONLY whitelist adds;
     any other rule/change_type -> `(False, "A4 supports only instrument_whitelist/add this increment;
     route others to the manual /invest-ship --approve-limits path")`.
  5. approval object present with non-empty `final_approval_token, approved_by, approved_at,
     apply_lease_id` (mirror `_approval_payload`, but `apply_lease_id` key).
  6. `approved_at` ISO-8601 with explicit UTC offset == 0; `apply_lease_id` matches `SHIP_LEASE_RE`.
  7. token binds CURRENT shas: `current_proposal_sha = _sha256_file(proposal)`, `current_config_sha
     = _sha256_file(config)`; `expected = _limits_token(slug, current_proposal_sha,
     current_config_sha, approved_at, apply_lease_id)`; refuse if mismatch.
  8. **NO ticker-whitelist precheck, NO entity==ticker check** (the entity is a proposal slug, not a
     ticker; whitelisting is the very thing A4 enables).
  9. `_git_tree_clean_for_limits_apply(repo, proposal, config)` (the F2-tight rule).
- **`preflight_k2bi` dispatch.** Route `k2bi-apply-limits` -> `_preflight_a4_limits(task, payload)`
  and `return` it (like A3's `if command_key == "k2bi-run-full-ship": return _preflight_a3_capital(...)`).
  `k2bi-apply-limits` is NOT in `_preflight_a1_symbol_matches_entity`'s ticker set, so that check
  passes through untouched (no symbol). Confirm it is allowlisted (resolve_command non-None) first,
  as today.

### 4.2 `orchestrator_k2bi_adapter.py`
- **Resolver** `_resolve_allowed_limits_proposal_path(path_value) -> Path` (mirror
  `_resolve_allowed_strategy_repo_path`): realpath; must be under `_allowed_k2bi_repo_roots()[0] /
  "review" / "strategy-approvals"`; name `*.md` with a derivable limits slug.
- **Approval builder** `_limits_approval_from_payload(raw) -> ioa.LimitsApproval` (mirror
  `_approval_from_payload`): require non-empty `final_approval_token, approved_by, approved_at,
  apply_lease_id`; return `ioa.LimitsApproval(...)`.
- **Runner** `_apply_approved_limits(payload) -> {"status":"ok","result": ...}`:
  ```python
  proposal_path = _resolve_allowed_limits_proposal_path(payload.get("proposal_path"))
  approval = _limits_approval_from_payload(payload.get("approval"))
  required_primary = str(payload.get("required_primary", "minimax")).strip() or "minimax"
  from scripts.lib import invest_orchestrator_adapters as ioa
  result = ioa.apply_approved_limits(
      proposal_path, approval=approval, required_primary=required_primary)
  return {"status": "ok", "result": _result_to_jsonable(result)}
  ```
  Pass `config_path=None` -> the K2Bi adapter resolves `repo_root/execution/validators/config.yaml`
  itself (repo_root derived from proposal_path). Do NOT pass `now_utc`. K2B builds only the typed
  approval + validates root containment; K2Bi owns reviews/handler/commit/rollback. The
  `OrchestratorGateError(rollback_result=...)` surfaces as adapter status `error` via the existing
  `_adapter_error_envelope` (ValueError-subclass -> validation/exit 2).
- **main().** Add `"apply-limits"` to the subparser-name loop and the dispatch chain
  (`elif args.cmd == "apply-limits": output = _apply_approved_limits(payload)`).

### 4.3 `orchestrator_store.py`
- **Terminal status.** Add `"terminal_limits_applied"` to `TERMINAL_STATUSES` and the
  human-terminal set used for the SQL terminal clause (mirror exactly how `terminal_shipped` was
  added at lines ~48-62). Constant `A4_MAX_APPLY_ATTEMPTS = 3`.
- **Resume oracle.** Extend `a1_resume_action_locked` with a **limits branch at the top** (right
  after the terminal-status short-circuits, before the ticker-chain flags). A limits flight carries
  `payload["chain_kind"] == "limits"`:
  ```python
  if status == "terminal_limits_applied": return "terminal_limits_applied"
  ...
  if payload.get("chain_kind") == "limits":
      return _a4_limits_resume_from_payload(status, payload)   # ladder below; never touches ticker flags
  ```
  `_a4_limits_resume_from_payload` ladder (mirror the A3 ship ladder):
  - attempt-limit / terminal_reason -> `needs_human_terminal`
  - `limits_verified` (or status terminal_limits_applied) -> `terminal_limits_applied`
  - `limits_partial_detected_at` -> `limits_partial`
  - `limits_rolled_back_at` -> `limits_rolled_back`
  - not `limits_proposal_recorded` -> `author_limits_proposal`
  - not `limits_authorized` -> `await_limits_approval`
  - not `limits_dispatch_started_at` -> `dispatch_limits`
  - else -> `verify_limits`
  The ticker-chain code path is untouched for non-limits flights (backward-compatible; every existing
  A1/A2/A3 flight + test lacks `chain_kind`).
- **Helpers** (mirror the A3 set; reuse `_a3_git_repo_root_for_path`, `_a3_git_head`,
  `_a3_git_status_for_path`, `_a3_git_path_tracked_at_head`, `_sha256_file`, `_parse_md_frontmatter`,
  `now_iso` -- they are generic):
  - `a4_record_limits_proposal(task_id, proposal_path) -> (bool, reason)`: file exists + non-empty;
    frontmatter type/status/applies-to valid (status==proposed); under repo
    `review/strategy-approvals/`; derive slug. **F3 scope guard:** parse `## Change`; refuse unless
    `rule == instrument_whitelist` and `change_type == add`. Record `limits_proposal_recorded=True`,
    `limits_proposal_path`, `limits_proposal_sha256`, `limits_proposal_slug`,
    `limits_change_rule`, `limits_change_type`, and the expected `after` symbol list
    (`limits_expected_after_symbols`, normalized) so the inspector can assert exact equality without
    re-reading the proposal if it later changes. (No A2-style prior-sha bind -- a freshly authored
    proposal has no predecessor; the token binds it at mark-dispatch.) A re-author resets downstream
    apply/recovery markers but keeps `limits_attempt_count`.
  - `a4_authorize_limits(task_id) -> (bool, reason)` (mirror `a1_authorize_ship`): require resume
    action `await_limits_approval`; set `limits_authorized=True`, `limits_authorized_at`.
  - `a4_mark_limits_dispatch_started(task_id) -> (bool, dict|reason)` (mirror
    `a1_mark_ship_dispatch_started`): require resume action `dispatch_limits`; bound by
    `A4_MAX_APPLY_ATTEMPTS`; read the recorded proposal path; `current_proposal_sha=_sha256_file(p)`
    MUST equal `limits_proposal_sha256` (else refuse -- proposal changed since record);
    resolve `<k2bi_repo>/execution/validators/config.yaml`, `current_config_sha=_sha256_file(cfg)`;
    record git HEAD-before (`_a3_git_repo_root_for_path` + `_a3_git_head` on the proposal path);
    mint `approved_at=datetime.now(utc).isoformat()`, `apply_lease_id=f"{slug}-limits-a1-{...Z}"`,
    `token=_limits_token(...)`. Persist `limits_attempt_count+1`, `limits_dispatch_started_at`,
    `limits_dispatch_proposal_sha256`, `limits_dispatch_config_sha256`, `limits_repo_head_before`,
    `limits_apply_lease_id`, `limits_approved_at`, `limits_approval_token`; clear stale recovery
    markers. Return `{lease_id, apply_lease_id, proposal_sha, config_sha, approved_at,
    approval_token, proposal_path, config_path}` for the conductor to build the child payload.
  - `_a4_inspect_limits_state_from_payload(task_id, payload) -> dict` + `a4_inspect_limits_state`
    (mirror `a1_inspect_ship_state`). Classify from git + proposal-status + config-content +
    rollback marker `<repo>/.k2bi-orchestrator/rollback/limits_<slug>.json`:
    - marker exists -> `incomplete_rollback_marker`.
    - proposal `status: approved` AND proposal tracked-at-HEAD AND config tracked-at-HEAD AND HEAD
      advanced AND **config.yaml's `instrument_whitelist.symbols` EXACTLY equals the proposal's
      expected `after` list** (F3: normalized set-equality -- same members, no extras, no missing;
      NOT containment, so a commit that also added an unauthorized symbol fails) AND no dirty target
      lines -> `committed`. If approved but not committed/landed/exact -> `partial_approved_uncommitted`.
    - proposal `status: proposed` and (sha unchanged from recorded, or untracked) and config
      unchanged -> `clean_rollback`.
    - else `unknown` with a reason.
    Expected `after` = the proposal `## Change` block's `after` list (the authoritative structured
    field) cross-checked against the rule==`instrument_whitelist`/change_type==`add` scope; read the
    committed config via `yaml.safe_load` and compare `instrument_whitelist.symbols` as a normalized
    set (uppercased, stripped). Fail CLOSED (NOT `committed`) if config is unreadable, the rule is not
    `instrument_whitelist`, or the post-list is not an exact match. (This is the "the YAML actually
    changed -- and ONLY as approved" gate the handoff requires, hardened per F3.)
  - `a4_verify_limits(task_id) -> (bool, reason)` (mirror `a1_verify_ship`): inspect; only
    `committed` sets status `terminal_limits_applied` + `limits_verified=True`,
    `limits_verified_at`, `limits_commit_sha=head`. `partial_approved_uncommitted` ->
    `needs_human` + `limits_partial_detected_at`. Else stay, record verify-failed reason.
  - `a4_record_limits_failed(task_id, *, reason)` (mirror `a1_record_ship_failed`): inspect; set
    `limits_rollback_clean = state==clean_rollback`; on clean_rollback set `limits_rolled_back_at`;
    on partial set `limits_partial_detected_at`; park `needs_human`.
  - `a4_retry_limits_after_rollback(task_id)` (mirror `a1_retry_ship_after_rollback`): allow a bounded
    retry only when live inspect == `clean_rollback` and `limits_rolled_back_at` recorded; clear
    dispatch/recovery markers; keep `limits_attempt_count`.
  - `_a4_terminalize_attempt_limit_locked(conn, task_id, payload)` (mirror the A3 version): park
    `needs_human` with `terminal_reason="limits_apply_attempt_limit_exceeded"`.
- **poll-once stage guard.** Add `"k2bi-apply-limits": "verify_limits"` to the `stage_expected`
  dict. The apply child is created AFTER `mark-limits-dispatch-started` (which advances the oracle to
  `verify_limits`), exactly like the ship child runs during `verify_ship`. The oracle already
  branches on `chain_kind`, so `a1_resume_action_locked(parent)` returns `verify_limits` for a
  limits parent -- no separate oracle dispatch needed in the guard.
- **CLI subparsers + dispatch** (mirror the A3 block at lines ~3301-3343 / ~3636-3692):
  - `record-limits-proposal <id> <path>`
  - `authorize-limits <id>`
  - `mark-limits-dispatch-started <id>` (prints the returned JSON)
  - `verify-limits <id>`
  - `record-limits-failed <id> --reason R`
  - `retry-limits <id>`
  - `inspect-limits-state <id>` (prints JSON)

### 4.4 k2b-orchestrator SKILL.md -- the A4 conductor + A3 gate wiring
- New section "A4 limits conductor -- approve -> author proposal -> apply -> verify". Durable model:
  standalone flight, `profile=k2b`, `entity=<proposal-slug>`, `status=needs_human`, payload
  `chain_kind=limits`; apply child `profile=k2bi`, `command_key=k2bi-apply-limits`,
  `--parent-task`/`--flight`/`--entity <proposal-slug>`, `payload_path` carrying `proposal_path`,
  `approval`, `required_primary`. Procedure:
  1. **Author the proposal.** Run K2Bi `python3 -m scripts.lib.propose_limits write --text
     "<Keith's ask>" --rationale "<why>"` (cwd=`~/Projects/K2Bi`) -> proposal path. Create the A4
     flight (entity = derived slug). `record-limits-proposal <id> <path>`.
  2. **Human gate (the 4th gate analog).** Surface the proposal's actual `## YAML Patch` (the exact
     config change Keith is approving). On his explicit "approve adding <X>", run
     `authorize-limits <id>`. (Veto = stop; nothing committed.)
  3. **Dispatch apply.** `mark-limits-dispatch-started <id>` -> use its JSON to build the
     `k2bi-apply-limits` child. **F4: the child's DB ROW `--payload` JSON MUST carry `proposal_path`,
     `approval={final_approval_token, approved_by, approved_at, apply_lease_id}`, and
     `required_primary` INLINE** (so `preflight_k2bi` -> `_preflight_a4_limits` can run the dual-sha
     token-bind check -- preflight never dereferences `payload_path`) **PLUS `payload_path`** pointing
     to a file with the SAME `{proposal_path, approval, required_primary}` (the adapter fd-guard reads
     the file via `_load_payload`). This mirrors the A3 ship child exactly (row carries
     `strategy_path` + `approval`; file carries the adapter copy). Set `K2B_ORCH_ADAPTER_PAYLOAD_DIR`,
     `poll-once`. Dispatch PROMPTLY (300s token skew window).
  4. **Verify.** After the child reaches `done`, `verify-limits <id>` -> only `committed` (config
     patched + both committed + the whitelist actually changed) sets `terminal_limits_applied`. On
     child error / verification refusal: `record-limits-failed`, surface `inspect-limits-state`, do
     NOT re-fire. `retry-limits` only when a live `inspect-limits-state` returns `clean_rollback`.
  Constraints: kill-switch + validators are READ-ONLY from K2B; the K2Bi adapter owns commit +
  rollback; the orchestrator never hand-commits and never edits config.yaml.
- **A3 ship-gate wiring.** In the A3 conductor's Stage-11 whitelist precheck (where it today routes a
  non-whitelisted ticker to `/invest-propose-limits`): add the inline offer "approve adding <TICKER>
  to the engine whitelist now?" On Keith's yes, run the A4 conductor (entity = the whitelist-add
  proposal slug) to `terminal_limits_applied`, THEN resume the A3 ship (the ticker is now
  whitelisted; `retry-ship` if the A3 flight was parked at `ship_rolled_back`). This closes the
  session-switch complaint.

### 4.5 Tests -- `tests/orchestrator/test_orchestrator_a4.py`
Mirror `test_orchestrator_a4`/A3 structure (fixtures: temp env, store, a real git repo,
proposal-file + config.yaml builders). Cover, at minimum:
- **Oracle:** limits ladder transitions (author_limits_proposal -> await_limits_approval ->
  dispatch_limits -> verify_limits -> terminal_limits_applied); `chain_kind != limits` is
  unaffected (a ticker flight still returns the A3 ladder).
- **Gates:** `authorize-limits` refuses unless `await_limits_approval`; `mark-limits-dispatch-started`
  refuses unless `dispatch_limits`, refuses when proposal sha drifted since record, mints a token
  binding BOTH current shas + a lease matching `SHIP_LEASE_RE`; attempt-limit terminalization at 3.
- **Token:** the minted token equals `APPROVE_LIMITS:<slug>:<p_sha>:<c_sha>:<approved_at>:<lease>`
  and the K2B preflight token-bind check accepts it / rejects a wrong-proposal-sha + wrong-config-sha.
- **Inspector:** committed (incl. the config-content "whitelist actually changed" assertion),
  clean_rollback, partial_approved_uncommitted, incomplete_rollback_marker, unknown.
- **verify-limits:** terminalizes only on committed; partial -> needs_human; clean_rollback stays.
- **Profiles preflight:** `_preflight_a4_limits` refuses kill-switch, missing validators, wrong
  proposal dir, status!=proposed, bad/stale token, dirty tree-outside-targets; passes the happy path
  (untracked proposal `??` + clean config).
- **Adapter runner:** `_apply_approved_limits` resolves the proposal under the repo, builds
  `LimitsApproval`, calls a faked `apply_approved_limits`, serializes ok; surfaces
  `OrchestratorGateError(rollback_result=...)` as status error exit 2; rejects a proposal path
  outside `review/strategy-approvals/`.
- **poll-once stage guard:** an out-of-order `k2bi-apply-limits` child (parent not at
  `verify_limits`) is cancelled; an in-order one is admitted.
- **Worker timeout (F1):** `k2bi-apply-limits` resolves to the 1200s `K2B_ORCH_SHIP_CMD_TIMEOUT`
  capital path, not 540s (mirror the A3 timeout test).
- **Preflight tree (F2):** a modified/staged config.yaml is refused even with a valid token; an
  untracked proposal + clean config passes; an unrelated dirty tracked file is refused.
- **Inspector exact-equality (F3):** a commit that adds the approved symbol AND an extra symbol is
  NOT `committed`; an exact match is `committed`; a non-`instrument_whitelist` proposal fails closed.
- **Real-K2Bi shape guard** (mirror `TestRealK2BiShipShape`): assert the live
  `invest_orchestrator_adapters.apply_approved_limits` signature + `LimitsApproval` field names match
  what the runner builds (catches any future K2Bi drift -- the A1/A2/A3 landmine).

## 5. Non-negotiable constraints
1. Kill-switch (`<k2bi_vault>/System/.killed`) + validators (`config.yaml`) are **READ-ONLY** from
   K2B. A4 NEVER writes/clears them. The whitelist add lands ONLY through the K2Bi adapter's
   `handle_approve_limits` + commit.
2. The K2Bi adapter owns the commit + rollback. The orchestrator never hand-commits.
3. The token MUST bind the EXACT proposal Keith reviewed AND the exact on-disk config (dual-sha).
   The blast radius of a wrong validator-apply > a strategy ship, so the binding is non-negotiable.
4. No VPS sync (the engine never calls this adapter -- it reads config.yaml after the repo syncs by
   the normal path).
5. Do NOT weaken `handle_approve_limits`, the validators, or `apply_approved_limits` -- wrap them.

## 6. Discipline
Codex Checkpoint-1 plan review of THIS spec (MANDATORY -- it changes the validator-landing safety
model) -> fold findings -> Codex builds on a branch (agent-handoff, driven, no copy-paste) -> Kimi
Checkpoint-2 cross-model `--no-fallback` (Codex built it) -> self-disposition material vs
fine-grained, AR7-terminate -> `/ship`. Then the live MVP = the CDNS positive send (Section: see
feature_k2b-orchestrator `pending-action`).
