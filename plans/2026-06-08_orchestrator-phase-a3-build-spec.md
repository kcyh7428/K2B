# Orchestrator Ship 2 -- Phase A3 build spec (ship-to-engine, Stage 11 -- THE CAPITAL PATH)

Date: 2026-06-08
Feature: [[feature_k2b-orchestrator]] Ship 2, Phase A, increment **A3** (ship-to-engine) -- the LAST of Phase A.
Status: BUILD-READY -- Checkpoint-1 plan review PASSED-WITH-FIXES (Codex, 2026-06-08, 3 findings all folded in below). Next: Codex builder (`codex/orchestrator-a3` branch), then **Kimi-backed** Checkpoint-2 (non-Codex, since Codex builds), then `/ship`, then live MVP.
Builder: Codex (K2B repo) · Reviewer: Kimi-backed (`scripts/review.sh --primary minimax --no-fallback`, AR7) · Ship: K2B `/ship`.
Extends: A1 (research half, Stages 3-8) + A2 (strategy half, Stages 9-10), both GATE-PASSED 2026-06-07 (`0bacfbc` + `0ba8c09`).
Boundary: A3 code lives in the **K2B repo** (the dispatch/flight/conduct layer). It DISPATCHES the already-merged K2Bi adapter `invest_orchestrator_adapters.run_full_ship` (which OWNS its own git commit + rollback). **No K2Bi code change is required** -- `run_full_ship`, `FullShipApproval`, `handle_approve_strategy`, `scan_backtests_for_slug`, `scan_bear_case_for_ticker` all exist + are tested at K2Bi main. No K2Bi PR (unless a callee is found broken mid-build -> then a PR, never a direct edit).

This is the CAPITAL PATH -- the dangerous one. It commits a strategy to the engine repo. The heaviest review applies.

---

## A3 binary MVP (written BEFORE code -- named-bug shape)

**Named bug: `K2B-CANNOT-SHIP-THE-STRATEGY-TO-THE-ENGINE-AS-ONE-CONVERSATION`.**
Today, even with A2's approved strategy parked, Keith would have to leave the orchestrator chat and hand-drive `/invest-ship --approve-strategy` in a separate K2Bi session to get the approved strategy committed so the engine picks it up.

**Pass conditions (all must hold; single binary verdict):**
1. From the already-parked, strategy-APPROVED CDNS chain (`2026-06-07-001`, resume action `strategy_approved_await_ship`), in ONE K2B conversation, Keith says "ship it" (the 4th human gate), and the orchestrator dispatches the K2Bi-owned `run_full_ship` through the allowlisted worker door.
2. `run_full_ship` commits `strategy_cdns.md` (status `proposed` -> `approved`) to the **K2Bi git repo** (`~/Projects/K2Bi/wiki/strategies/strategy_cdns.md`), with its internal plan+diff reviews passing (primary APPROVE) and the real ship gate (`handle_approve_strategy`: bear fresh + backtest sane + forward-guidance) passing. The engine loads it on the next `/sync` + tick.
3. The flight reaches the new terminal state `terminal_shipped`, with **NO manual `/invest-ship`** and **NO hand-committing** -- the merged adapter owns the commit + rollback; the orchestrator only dispatches, records the dispatch, and verifies the commit landed before going terminal.
4. **Negative path holds (prove a clean refusal + rollback, NO partial ship):** a ship attempt with a **stale strategy sha** (or a bad token / a dirty K2Bi tree / an engaged kill-switch) is REFUSED -- `run_full_ship` raises `OrchestratorGateError`, `_rollback_ship_failure` restores the original bytes + clears the index, the adapter returns status `error` carrying the `rollback_result`, and the repo HEAD is unchanged (no commit, no partial ship). The orchestrator surfaces the rollback and stays parked -- it does NOT re-fire.

A3 cannot be marked shipped unless 1-4 all execute live on CDNS.

---

## The BLOCKER A3 hits first (the #1 Checkpoint-1 design question) -- RESOLVED

**The collision.** `run_full_ship` does `git rev-parse --show-toplevel` + `git add` + `git commit` on the strategy file's repo, and the K2Bi engine (`execution/strategies/loader.py::load_approved`) only loads `wiki/strategies/*.md` with `status: approved` from the **git-tracked K2Bi REPO** (`~/Projects/K2Bi/wiki/strategies/` -- contains `strategy_g-*`, `strategy_spy-*`; deployed to the VPS via `/sync`). But A2 wrote `strategy_cdns.md` to the **K2Bi VAULT** (`~/Projects/K2Bi-Vault/wiki/strategies/`, sha `86fdaf96…`), which is **not a git repo** (`git rev-parse` fails there). So `run_full_ship` cannot ship the A2 strategy from where it sits.

**Why two folders (Q30 split, K2Bi's existing design).** The git REPO is the system of record: `/invest-ship` writes approved strategies there + commits (where git hooks fire, `approved_commit_sha` resolves), and `/sync` deploys the committed code folder to the VPS engine. The VAULT (Syncthing) is the working scratchpad: thesis, bear-case, backtest captures live there -- NOT git, no commit, no "approved" proof. A2's `write_complete_strategy_spec(repo_root=<VAULT>)` + `run_backtest(vault_root=<VAULT>)` both operate on the vault (the backtest reads the strategy from `vault/wiki/strategies/` and writes the capture to `vault/raw/backtests/`). That was correct for A2's backtest, but it is NOT where `run_full_ship` ships from.

**RESOLUTION (Keith-confirmed 2026-06-08).**
- **Q1 -- the ship-write path is sanctioned.** A strategy-ship is a K2Bi-OWNED runtime data operation the engine is designed to consume (exactly `/invest-ship` today), authorized by the 4th human gate -- distinct from K2Bi CODE/feature changes, which still go via a GitHub PR. The merged `run_full_ship` adapter owns its own commit + rollback; the orchestrator DISPATCHES it and never hand-commits. This is the A1/A2 "adapter-owns-its-write" pattern extended to the capital path.
- **Q2 -- re-author into the repo + sha-bind (evidence stays in the vault).** A3 dispatches `write_complete_strategy_spec(repo_root=~/Projects/K2Bi)` to author the **proposed** strategy into the REPO, then ASSERTS the repo file's sha256 == the A2-backtested vault file's sha (`strategy_artifact_sha256` = `86fdaf96…`) as the evidence binding -- shipping exactly what was backtested + approved; fail closed on any mismatch. The bear/backtest evidence stays in the VAULT, where `run_full_ship`'s `handle_approve_strategy` reads it via `vault_root` (`scan_bear_case_for_ticker` + `scan_backtests_for_slug` both take `vault_root`).

**Live-flight wrinkle (the parked `2026-06-07-001` predates A3, so the `decision` is NOT in its payload).** For this flight the A3 conductor reconstructs the `decision` from the approved thesis + the existing vault `strategy_cdns.md` (which carries every rule/order/forward-guidance field), re-authors into the repo, and the **sha-equality assertion (repo file == vault A2 sha) is the safety net**: a faithful reconstruction reproduces `86fdaf96…` byte-for-byte; a drifted one does not and is refused. Go-forward: A3 stores the `decision` it authored with in the parent payload (`ship_decision`) so a re-ship/revision never reconstructs.

---

## Architecture (extend the A1/A2 chain; reuse A1/A2 machinery -- do not re-invent)

The A3 chain extends the SAME parent ledger flight (`2026-06-07-001`, `k2b` profile, entity CDNS, `needs_human`). New bounded K2Bi child dispatches join the chain via `parent_task` + the parent's `flight_id` so the chain-scoped one-flight lock admits them; A3 children run STRICTLY SEQUENTIALLY (the author child terminal before the ship child is created -- the lock allows at most one live child besides the parent).

A3 adds TWO new bounded dispatches (mirroring A2's strategy + backtest pair), both through the `payload_path` carrier + fd-guard:
1. **`k2bi-author-strategy-to-repo`** -> the K2B runner calls `write_complete_strategy_spec(decision, repo_root=<K2Bi REPO>)`. This is the SAME merged adapter A2 uses, pointed at the REPO (not the vault). A NEW **K2Bi-REPO-only** root resolver gates it (the A2 vault-only C2 guard is untouched).
2. **`k2bi-run-full-ship`** -> the K2B runner calls `run_full_ship(strategy_path=<repo strategy>, approval=<FullShipApproval>, vault_root=<K2Bi VAULT>, required_primary=...)`. The adapter OWNS the plan+diff reviews, the real ship gate, the commit, and the rollback. NEW **capital preflight** gates the dispatch.

### Callees (confirmed at K2Bi main, READ EXACTLY -- the A1/A2 landmine was stub-shape mismatch)

`run_full_ship(strategy_path, *, approval: FullShipApproval, review_runner=None, approve_handler=iss.handle_approve_strategy, git_runner=None, vault_root=None, now=None, today=None, required_primary="minimax") -> FullShipResult`. It, in order:
- (a) takes the `_strategy_ship_lock` flock (per-slug, in `<repo>/.k2bi-orchestrator/locks/`);
- (b) `_refuse_if_incomplete_rollback` -- refuses if a rollback marker exists at `<repo>/.k2bi-orchestrator/rollback/<slug>.json`;
- (c) reads `original = strategy_path.read_bytes()`, `original_sha = _sha256_bytes(original)`, then `_validate_full_ship_approval(approval, slug, original_sha)` -- **token gate, BEFORE any review** (a bad/stale-sha token fails here, fast);
- (d) `_assert_ship_clean_preflight(git_runner, repo_root, rel_path)` -- refuses unless the only working-tree change is the target strategy file (`git status --porcelain=v1 --untracked-files=no`, allows ` M`/`M `/`MM`/`A `/`AM` on `rel_path` only);
- (e) **plan review** (`run_review_with_script`, kind=plan) -> `_require_review_approved(plan_review, ..., required_primary)` (verdict APPROVE + `primary_used==required_primary` + `fallback_used is False`);
- (f) `approve_handler = handle_approve_strategy(strategy_path, vault_root=vault_root, now=now, today=today)` -- THE REAL SHIP GATE: `_require_status(fm,"proposed")`, `_validate_strategy_shape`, forward-guidance (`extract/validate_forward_guidance_check`), bear-case freshness (`scan_bear_case_for_ticker(order.ticker, vault_root=resolve_vault_root(vault_root))`), backtest sanity (`scan_backtests_for_slug(slug, vault_root=...)`), then atomically rewrites the file `proposed -> approved` + `approved_at` + `approved_commit_sha` (= parent sha);
- (g) **diff review** -> `_require_review_approved(diff_review, ...)`;
- (h) `git add rel_path` + `git commit --only rel_path -m <message>`.
ANY raised exception in (e)-(h) triggers `_rollback_ship_failure(...)` (restores original bytes, clears the index, writes/clears a rollback marker) and re-raises `OrchestratorGateError(rollback_result=...)`. This COMMITS to a real git repo.

`FullShipApproval(final_approval_token, approved_by, approved_at, ship_lease_id)` (frozen dataclass). `_validate_full_ship_approval` enforces:
- `approved_at` parses as ISO-8601 datetime **with an explicit timezone offset that equals UTC** (`+00:00`);
- `ship_lease_id` matches `^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$`;
- `final_approval_token` **==** `f"APPROVE_STRATEGY:{slug}:{strategy_sha256}:{approved_at}:{ship_lease_id}"` where `strategy_sha256` is the sha of the strategy file bytes at ship time (`original_sha`). **The token binding (slug + sha + approved_at + lease) IS the adapter's built-in replay/drift guard.**
- `approved_by` non-empty.

`FullShipResult(slug, commit_message, commit_hints: iss.StrategyCommitHints, plan_review: ReviewGateResult, diff_review: ReviewGateResult, events: list[dict])`. `RollbackResult(strategy_path, original_sha256, marker_path, index_restored, working_tree_restored, marker_cleared, events)`.

`handle_approve_strategy(path, *, parent_sha=None, now=None, vault_root=None, today=None) -> StrategyCommitHints`. It reads bear/backtest evidence from `resolve_vault_root(vault_root)` (override > `K2BI_VAULT_ROOT` env > `~/Projects/K2Bi-Vault`), and requires the strategy file at `path` to be `status: proposed`. `scan_bear_case_for_ticker` enforces `bear_verdict: PROCEED` + `bear-last-verified` within `ibc.FRESH_DAYS` (NOT future-dated) + the full bear schema + `symbol == ticker`. `scan_backtests_for_slug` globs `vault/raw/backtests/*_<slug>_backtest.md`, picks the most-recent capture, requires `look_ahead_check: passed` (or `suspicious` + a binding `## Backtest Override` section in the strategy body).

---

## File-by-file changes (all K2B repo)

### 1. `scripts/lib/orchestrator_k2bi_adapter.py` (runner)

- **`_allowed_k2bi_repo_roots()`** (NEW) -- resolves ONLY to the K2Bi REPO: `K2B_ORCH_K2BI_WORKSPACE` env if set, else `~/Projects/K2Bi`. Resolves to EXACTLY ONE root (mirror the single-root discipline of `_allowed_k2bi_vault_roots`). Deliberately EXCLUDES the vault, `K2B_VAULT_PATH`, and the generic override -- the repo is the only legal author/ship target.
- **`_resolve_allowed_k2bi_repo(path_value, field_name)`** (NEW) -- realpath + match against `_allowed_k2bi_repo_roots()`; error names `field_name`. Used by both A3 subcommands.
- **`_author_strategy_to_repo(payload)`** (NEW subcommand `author-strategy-to-repo`): `repo_root = _resolve_allowed_k2bi_repo(payload.get("repo_root"), "repo_root")`; `decision = _load_json_value(payload,"decision")`; reuse `_validate_strategy_decision_payload(decision, payload_symbol)` (the C1 symbol==order.ticker + C4 nested-bool gates); `decision_obj = _dataclass_from_dict(ioa.StrategySpecDecision, decision)`; `ioa.write_complete_strategy_spec(decision_obj, repo_root=repo_root)`; return ok + `_result_to_jsonable(result)` (path + content_sha256). Identical to `_write_strategy_spec` except the root resolver (REPO not VAULT).
- **`_run_full_ship(payload)`** (NEW subcommand `run-full-ship`):
  - `strategy_path` -- resolve from payload (`strategy_path`); assert it is `<K2Bi REPO>/wiki/strategies/strategy_<slug>.md` (contain it under `_allowed_k2bi_repo_roots()[0]/wiki/strategies` via realpath + `commonpath`; reject symlinks/traversal -- the runner's existing containment idiom).
  - `vault_root = _resolve_allowed_k2bi_root(payload.get("vault_root"), "vault_root")` (the K2Bi VAULT -- where bear/backtest evidence lives).
  - `payload_symbol` non-empty + uppercased (entity binding belt-and-braces; the path slug is authoritative for `run_full_ship`).
  - Build `ioa.FullShipApproval(final_approval_token=..., approved_by=..., approved_at=..., ship_lease_id=...)` from `payload["approval"]` (a nested dict; require all 4 string fields present, non-empty).
  - `required_primary = str(payload.get("required_primary","minimax"))`.
  - `result = ioa.run_full_ship(Path(strategy_path), approval=approval, vault_root=vault_root, required_primary=required_primary)`.
  - return `{"status":"ok","result": _result_to_jsonable(result)}`.
- **Surface the rollback on failure.** Extend `_adapter_error_envelope(exc)`: if `getattr(exc, "rollback_result", None)` is set (an `OrchestratorGateError` from `run_full_ship`), include `"rollback_result": _result_to_jsonable(exc.rollback_result)` in the envelope so the worker's stdout error JSON carries the rollback details (index_restored / working_tree_restored / marker_cleared / events) for the conductor to surface. A failed ship gate stays `category=validation, exit_code=2, retryable=false` (it is a `ValueError` subclass) -- NOT retryable.
- Register `author-strategy-to-repo` + `run-full-ship` in `main()`'s subparser loop + dispatch (same `--workspace`/`--payload-json`/`--payload-path` shape).
- `_result_to_jsonable` already maps datetime -> isoformat (added in A2); `FullShipResult`/`RollbackResult`/`StrategyCommitHints`/`ReviewGateResult` are dataclasses -> serialize fine.

### 2. `scripts/lib/orchestrator_profiles.py` (allowlist + capital preflight)

- `k2bi_allowed_commands()`: add `k2bi-author-strategy-to-repo` -> `[py, A1_ADAPTER_RUNNER, "author-strategy-to-repo"]` and `k2bi-run-full-ship` -> `[py, A1_ADAPTER_RUNNER, "run-full-ship"]`.
- `resolve_command()`: for both keys, append `_adapter_payload_args(payload)` (payload_path carrier) -- return None if the carrier is absent/invalid.
- `k2bi_repo()` (NEW) -- returns `k2bi_workspace()` (`~/Projects/K2Bi`); the canonical repo root for A3.
- `_preflight_a1_symbol_matches_entity`: add both keys to the ticker-scoped set (symbol == entity == canonical-registry). (For `k2bi-run-full-ship` the payload carries `symbol`; the strategy path slug must also match -- see capital preflight.)
- **A3 root routing.** Do NOT route the two A3 keys through `_preflight_a1_vault_root_matches_profile` (its `repo_root`-against-VAULT check would reject the author command's `repo_root=<REPO>`). Instead:
  - `k2bi-author-strategy-to-repo`: assert `repo_root == k2bi_repo()` (new repo-match) + `_preflight_a1_adapter_payload_shape` (decision shape) + the STANDARD clean-tree git check (the write happens in the worker, after preflight, so the tree is still clean at preflight time).
  - `k2bi-run-full-ship`: the NEW **capital preflight** (below) -- and a relaxed tree check that allows EXACTLY the target strategy file (the author step just made the tree dirty with it).
- **`_preflight_a3_capital(task, payload)`** (NEW -- "that arrives in A3"). Before a ship dispatch, assert ALL of:
  1. **Kill-switch absent (READ ONLY, NEVER write).** `<K2Bi VAULT>/System/.killed` must NOT exist. If present -> `(False, "engine kill-switch engaged (System/.killed present); ship refused")`.
  2. **Validators config present.** `<K2Bi REPO>/execution/validators/config.yaml` exists + non-empty. If missing -> refuse (never ship while the hard-rule safety layer is absent).
  3. **Approval token well-formed + binds the CURRENT repo strategy sha.** Read `payload["approval"]` (token, approved_by, approved_at, ship_lease_id). `approved_at` parses ISO-8601 with UTC offset; `ship_lease_id` matches `^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$`; sha the CURRENT repo strategy file; require `token == f"APPROVE_STRATEGY:{slug}:{sha}:{approved_at}:{ship_lease_id}"` (slug from the strategy path basename). A token built against a since-drifted file FAILS here, at the preflight layer -- the orchestrator-side replay/drift guard, ahead of the adapter's identical check.
  4. **Strategy path under the K2Bi REPO + frontmatter ticker == entity.** The resolved `strategy_path` is `<REPO>/wiki/strategies/strategy_<slug>.md`, exists, status `proposed`; its `order.ticker` (or top-level `ticker`) == `entity_key`.
  5. **K2Bi tree clean EXCEPT the target strategy file.** `git -C <REPO> status --short` shows nothing, OR only `wiki/strategies/strategy_<slug>.md` (as `??` / ` M` / `M ` / `A ` / `AM`). Any OTHER dirty path -> refuse (surface to Keith; do not mutate the tree). This mirrors `run_full_ship`'s internal `_assert_ship_clean_preflight` but tolerates the just-authored untracked target (`??`, which `--untracked-files=no` hides from the adapter but `--short` shows to us).
- `preflight_k2bi()`: route the two new keys. The author key -> repo-root match + payload-shape + standard clean-tree. The ship key -> `_preflight_a3_capital`. NO promoted/thesis precondition (those are A1-specific); chain-validity ("strategy approved before ship") is owned by the resume oracle + the `poll_once` stage-match guard (see below), NOT a child preflight.

### 3. `scripts/lib/orchestrator_store.py` (resume flags, helpers, oracle, terminal state)

**New terminal status (Checkpoint-1 F3 -- derive ALL terminal SQL, do not hard-code).** Add `terminal_shipped` to `TERMINAL_STATUSES` + `ALL_STATUSES` (mirror `terminal_bear_veto`). A shipped chain is terminal (the ticker is now an engine position; a fresh chain would be a new flight). **BUT adding the constant is NOT enough** -- several call sites hard-code the OLD terminal set `('done','failed','cancelled','terminal_bear_veto')` in raw SQL: `add_task`'s entity-lock + exemption queries (lines ~312, ~326) and `poll_once`'s parent-liveness check (line ~2393). Left as-is, a `terminal_shipped` chain would be treated as LIVE -- still holding the entity lock + acceptable as a live parent for queued children. **Fix: derive the terminal IN-clause from `TERMINAL_STATUSES`** (a small helper returning `(placeholders, params)` from `sorted(TERMINAL_STATUSES)`, used at every hard-coded site) so any future terminal status propagates automatically. Also: `a1_resume_action_locked`'s top branch must return `terminal_shipped` AS-IS for `status == "terminal_shipped"` (the generic `f"terminal_{status}"` fallthrough would wrongly yield `"terminal_terminal_shipped"` -- handle it explicitly like `terminal_bear_veto`). Tests: a shipped parent releases the entity lock AND rejects/cancels a queued child dispatch.

**New A3 flags on the parent payload:** `ship_authorized`, `ship_authorized_at`, `ship_decision` (the decision dict used to author the repo strategy -- go-forward; absent for the live flight), `ship_repo_authored`, `ship_strategy_repo_path`, `ship_strategy_repo_sha256`, `ship_dispatch_started_at`, `ship_lease_id`, `ship_approved_at` (the token's approved_at), `ship_attempt_count`, `ship_commit_sha`, `ship_verified`, `ship_verified_at`, `ship_rolled_back_at`, `ship_rollback_reason`, `ship_rollback_clean` (bool: rollback restored working tree + cleared marker), `terminal_reason` (`ship_attempt_limit_exceeded`).

**New helpers (mirror the A2 ones; locked, terminal-guarded, artifact/commit-verified before flag):**
- `a1_authorize_ship(task_id)` -- the 4th human gate. Guard: at the ship gate -- `thesis_approved + strategy_approved + _a1_backtest_payload_valid` all hold AND resume action is `strategy_approved_await_ship`. Sets `ship_authorized=True` + `ship_authorized_at`. CLI `approve-ship`. (Surfacing-time kill-switch + bear-freshness are checked by the conductor + the capital preflight; this helper records intent.)
- `a1_record_ship_repo_authored(task_id, repo_path)` -- after the author-to-repo worker `done` + the repo file exists: verify the file is non-empty, frontmatter ticker == `entity_key`, status `proposed`, **and `sha256(repo file) == payload["strategy_artifact_sha256"]` (the A2 vault sha -- the evidence binding).** Refuse on any mismatch (the re-authored bytes drifted from what was backtested+approved -> fail closed). Sets `ship_repo_authored=True` + `ship_strategy_repo_path` + `ship_strategy_repo_sha256`. Guard: `ship_authorized`. CLI `record-ship-repo-authored`.
- `a1_mark_ship_dispatch_started(task_id)` -> records `ship_dispatch_started_at`, mints a fresh `ship_lease_id` (`f"{entity_lower}-ship-a{attempt}-{compact_ts}"`, regex-valid), increments `ship_attempt_count`, RETURNS `(lease_id, repo_sha, approved_at)` so the conductor builds the token deterministically. Guard: `ship_authorized + ship_repo_authored` + not `ship_verified` + `ship_attempt_count < A3_MAX_SHIP_ATTEMPTS` (else terminalize `needs_human` + `terminal_reason=ship_attempt_limit_exceeded`). **This records the dispatch BEFORE firing -- the replay-guard anchor.** CLI `mark-ship-dispatch-started`.
- **`a1_inspect_ship_state(task_id)`** (NEW -- Checkpoint-1 F1+F2; the INDEPENDENT source of truth, not the worker's stdout). Reads the K2Bi REPO directly (read-only git + the strategy file + the rollback marker) and classifies the true post-ship state into exactly one of:
  - `committed` -- HEAD touches `wiki/strategies/strategy_<slug>.md` (via `git -C <REPO> log -1 --format=%H -- <rel>` == HEAD that includes the file) AND the working-tree file is `status: approved` + has `approved_commit_sha`.
  - `clean_rollback` -- file is `status: proposed`, `sha256 == ship_strategy_repo_sha256` (the bound proposed bytes), NO rollback marker at `<REPO>/.k2bi-orchestrator/rollback/<slug>.json`, index clean for the file.
  - `partial_approved_uncommitted` -- file is `status: approved` but HEAD does NOT include the commit (the F1 SIGKILL-mid-ship case: approved on disk, never committed, rollback never ran). **This is the dangerous partial; it must NEVER be classified shippable.**
  - `incomplete_rollback_marker` -- a rollback marker exists (the adapter's own backstop; `run_full_ship` will refuse the next dispatch).
  - `unknown` -- anything else (e.g. wrong sha, file missing) -> operator recovery.
  This classifier is **why F2 is closed without a K2Bi change**: `handle_approve_strategy` raises `iss.ValidationError` (not `OrchestratorGateError`) for bear/backtest/forward-guidance refusals, so `run_full_ship` re-raises WITHOUT attaching `rollback_result` even though the rollback DID restore the file. K2B therefore must NOT depend on the worker's `rollback_result`; it inspects the repo itself. The worker's `rollback_result` (when present, i.e. for `OrchestratorGateError` paths) is recorded as supplementary evidence only.
- `a1_verify_ship(task_id)` -> after the ship worker reaches `done` with status ok: call `a1_inspect_ship_state`. ONLY `committed` -> set `ship_commit_sha` + `ship_verified=True` + `ship_verified_at` + transition the parent to **`terminal_shipped`**. `partial_approved_uncommitted` -> set `ship_partial_detected_at` + park (NOT terminal; surfaced for recovery). Anything else -> refuse, stay parked. A worker that reported ok but did not actually commit (or left an approved-uncommitted partial) NEVER goes terminal. CLI `verify-ship`.
- `a1_record_ship_failed(task_id, *, reason)` -> when the ship worker returns status error (a refused gate OR a timeout/kill): call `a1_inspect_ship_state` to determine the true state and set `ship_rolled_back_at` + `ship_rollback_reason=reason` + `ship_rollback_clean=(state=="clean_rollback")` + `ship_partial_detected_at` if `partial_approved_uncommitted`; do NOT set `ship_verified`; keep parked `needs_human`. The `clean` flag is derived from the INDEPENDENT inspection, not from the (possibly-absent) `rollback_result`. CLI `record-ship-failed` (`--reason`).
- `a1_recover_partial_ship(task_id)` -> recovery from `partial_approved_uncommitted` (F1): re-author the PROPOSED strategy over the approved-uncommitted file (the sha-bound bytes reset it to `proposed`); after `record-ship-repo-authored` re-confirms `sha == ship_strategy_repo_sha256`, the inspector returns `clean_rollback` and `retry-ship` is unlocked. This is the only sanctioned path out of a partial -- never a hand-edit, never a force-commit. CLI: routed through `record-ship-repo-authored` (re-author) + the inspector gate; no separate primitive needed.
- `a1_retry_ship_after_rollback(task_id)` -> bounded recovery: guard `ship_rolled_back_at` set + `a1_inspect_ship_state == "clean_rollback"` (the LIVE inspection, not a cached flag) + `ship_attempt_count < A3_MAX_SHIP_ATTEMPTS`. Clears `ship_rolled_back_at` / `ship_rollback_reason` / `ship_partial_detected_at` / `ship_dispatch_started_at` / `ship_lease_id` (a fresh token+lease is minted on the next `mark-ship-dispatch-started`); keeps `ship_authorized + ship_repo_authored`. resume then returns `dispatch_ship`. If the live inspection is NOT `clean_rollback` (partial / marker / unknown) -> refuse (operator recovery; `run_full_ship`'s incomplete-rollback-marker refusal is the backstop). CLI `retry-ship`.

`A3_MAX_SHIP_ATTEMPTS = 3` (module constant). `ship_attempt_count` is SEPARATE from `revision_count` / `strategy_revision_count`.

**`a1_resume_action_locked` extension** (AFTER `if not payload.get("strategy_approved"): return "strategy_approval_gate"` and the existing `return "strategy_approved_await_ship"` line). Replace that terminal `return` with the A3 ladder:
```
if not payload.get("ship_authorized"):
    return "strategy_approved_await_ship"   # the 4th human gate -- UNCHANGED return string => A2 tests stay green
if payload.get("ship_verified"):
    return "terminal_shipped"               # defensive; verify-ship already set status terminal_shipped
if payload.get("ship_partial_detected_at"):
    return "ship_partial"                   # F1: approved-but-uncommitted partial -> recover (re-author reset), NEVER auto-retry
if payload.get("ship_rolled_back_at"):
    return "ship_rolled_back"               # failed+rolled-back: stay parked, NEVER auto-re-dispatch
if not payload.get("ship_repo_authored"):
    return "author_strategy_to_repo"
if not payload.get("ship_dispatch_started_at"):
    return "dispatch_ship"
return "verify_ship"                        # dispatch recorded; confirm the commit landed before terminal
```
Plus: `_payload_is_logically_terminal` already returns True on any `terminal_reason` (covers `ship_attempt_limit_exceeded`). Add a test. The top of `a1_resume_action_locked` must return `terminal_shipped` AS-IS for `status == "terminal_shipped"` (explicit branch alongside `terminal_bear_veto`, NOT the generic `f"terminal_{status}"` -- Checkpoint-1 F3).

### 4. `scripts/lib/orchestrator_store.py` -- `poll_once` A3 stage-match guard

Extend the A2 `a2_expected` stage-match map (Checkpoint-2 #6) so a hand-queued A3 child cannot run out of order:
```
a3_expected = {
    "k2bi-author-strategy-to-repo": "author_strategy_to_repo",
    "k2bi-run-full-ship": "dispatch_ship",
}.get(task.get("command_key",""))
```
If the dispatched child's command_key is an A3 key and `a1_resume_action_locked(conn, parent)` != the expected action, CANCEL the out-of-order child (release the entity lock), mirroring the A2 path. **This is what stops a stale/duplicate ship child from firing after the chain already shipped or rolled back** -- the orchestrator-side double-ship guard, in addition to the sha-bound token + the adapter's incomplete-rollback-marker refusal.

### 5. `scripts/lib/orchestrator_store.py` -- CLI wiring

Add subparsers + dispatch for: `approve-ship`, `record-ship-repo-authored <id> <path>`, `mark-ship-dispatch-started <id>` (prints `lease_id`, `repo_sha`, `approved_at` as JSON for the conductor), `verify-ship`, `record-ship-failed <id> --reason <r>`, `retry-ship`, and `inspect-ship-state <id>` (prints the classifier verdict as JSON -- debug/conductor visibility). Mirror the A2 CLI handlers (render-board on success, stderr + exit 1 on rejection).

### 5b. `scripts/lib/orchestrator_worker.py` (Checkpoint-1 F1 -- per-command timeout)

The worker hard-kills the child at `K2B_ORCH_CMD_TIMEOUT` (default `540s`, `subprocess.run(timeout=...)`). `run_full_ship` runs plan review (`REVIEW_TIMEOUT_S=420`) + `approve_handler` (atomically rewrites the file to `status: approved`) + diff review (`420`) + commit -- which can cross 540s, so a SIGKILL can land AFTER the approve rewrite but BEFORE commit/rollback -> an approved-uncommitted partial ship. Fix: resolve the timeout PER command_key -- for `k2bi-run-full-ship`, use `K2B_ORCH_SHIP_CMD_TIMEOUT` (default `1200s`, comfortably above `2 * REVIEW_TIMEOUT_S + commit margin`). The worker's heartbeat thread keeps the task alive across the long run, so the longer subprocess timeout does NOT expose it to zombie-reclaim. **The larger timeout makes a mid-ship SIGKILL unlikely; the independent `a1_inspect_ship_state` partial detector is the hard guarantee** that an approved-uncommitted partial can never be marked `terminal_shipped`. (Also note the worker comment at lines 157-168: the worker-local timeout SIGKILLs only the DIRECT child, so `run_full_ship`'s review descendants could survive a timeout. The reviews are read-only, so this is not a capital-safety issue for A3, but the larger timeout makes a ship-time timeout unlikely in the first place.) Test: simulate a kill/timeout after the approve rewrite -> prove `verify-ship` refuses `terminal_shipped` and routes to recovery (no approved-uncommitted "shipped").

### 6. `.claude/skills/k2b-orchestrator/SKILL.md`

New "**A3 chain conductor -- approved strategy -> author to repo -> run_full_ship -> engine**" section (mirror the A2 section): the durable model additions, the two new allowlisted dispatches, the EXACT `FullShipApproval` token format, the capital preflight, the replay-guard + rollback-recovery procedure, and the Stage-11 sequence (approve-ship -> author_strategy_to_repo -> record/verify -> mark-ship-dispatch-started + build token -> dispatch_ship -> verify-ship (independent `inspect-ship-state`) -> terminal_shipped; on error -> record-ship-failed -> surface, do NOT re-fire; `partial_approved_uncommitted` -> re-author reset; retry-ship only when the LIVE inspection is `clean_rollback`). Hard constraints restated: kill-switch READ-ONLY, validators READ-ONLY, engine owns state, `run_full_ship` IS the gate, the adapter owns the commit + rollback, A3 never hand-commits.

### 7. `tests/orchestrator/test_orchestrator_a3.py` (new)

Unit + regression coverage (fakes, like A2) PLUS a **REAL-K2Bi de-risk subprocess test** (mirror A2's `TestRealK2BiDecisionShape`):
- **Oracle:** A3 ladder (`strategy_approved_await_ship` backward-compat with no `ship_authorized`; -> `author_strategy_to_repo` -> `dispatch_ship` -> `verify_ship` -> `terminal_shipped`; `ship_rolled_back` parks). `terminal_shipped` is terminal + logically terminal.
- **Helpers:** `authorize-ship` guards (only at the ship gate); `record-ship-repo-authored` refuses a repo file whose sha != the A2 vault sha (evidence binding), refuses ticker!=entity; `mark-ship-dispatch-started` mints a regex-valid lease + records before firing + bounds attempts; `verify-ship` refuses when `inspect-ship-state` != `committed`, sets `terminal_shipped` only when it is; `record-ship-failed` parks with `ship_rollback_clean` derived from the independent inspector; `retry-ship` only when the LIVE `inspect-ship-state` is `clean_rollback` + bounded.
- **Independent ship-state inspector (F1+F2):** `inspect-ship-state` classifies `committed` / `clean_rollback` / `partial_approved_uncommitted` / `incomplete_rollback_marker` / `unknown` from git HEAD + the repo strategy file status + the rollback marker -- proven NOT to depend on the worker's `rollback_result`. Tests: an `approve_handler` raising `iss.ValidationError` (NO `rollback_result`) still yields a correct `clean_rollback` classification + a retry-eligible park; a simulated kill after the approve rewrite yields `partial_approved_uncommitted` + a NON-terminal park + a re-author reset path.
- **Capital preflight:** kill-switch present -> refuse (and asserts the test NEVER writes `.killed`); validators config missing -> refuse; stale-sha token -> refuse; dirty unrelated repo path -> refuse; tree clean-but-for-target -> pass.
- **`poll_once` A3 stage-match:** an out-of-order `k2bi-run-full-ship` child (parent not at `dispatch_ship`) is cancelled.
- **Adapter runner (fake K2Bi workspace):** `author-strategy-to-repo` writes into a REPO-only root + refuses a vault/`K2B_VAULT_PATH` root; `run-full-ship` builds a real `FullShipApproval`, dispatches a FAKE `run_full_ship`, serializes `FullShipResult`; an `OrchestratorGateError(rollback_result=...)` from the fake is surfaced as status error carrying `rollback_result`.
- **`TestRealK2BiShipShape` (skipif K2Bi checkout absent) -- the LANDMINE de-risk, run against the REAL `run_full_ship` (pre-review gates only, so no reviewer is needed):**
  - build a temp git repo (`git init`) with a real proposed `strategy_cdns.md` (authored by the real `write_complete_strategy_spec`) + real bear/backtest evidence under a temp vault;
  - **positive token-format proof:** call the REAL `ioa._validate_full_ship_approval(approval, slug, real_sha)` with our built token -> passes (the token format is proven against the real validator);
  - **negative (stale sha):** real `run_full_ship` with a token bound to a WRONG sha raises `OrchestratorGateError` at the token gate (BEFORE any review), `git log` unchanged (no commit);
  - **negative (dirty tree):** an unrelated dirty file -> real `run_full_ship` raises at `_assert_ship_clean_preflight`, no commit;
  - **negative (incomplete rollback marker):** a pre-seeded marker -> real `run_full_ship` refuses.
  (The POSITIVE full-commit-with-real-review path is the LIVE MVP, not a unit test -- the real plan+diff reviews need a live reviewer.)

Target: full orchestrator suite stays green (current baseline + A3 additions).

---

## The live MVP (continue flight `2026-06-07-001` from `strategy_approved_await_ship`)

1. **Surface the ship gate.** resume = `strategy_approved_await_ship`. The conductor runs the read-only capital pre-check (kill-switch absent, K2Bi tree clean, bear/backtest still fresh) and surfaces: "ready to ship CDNS to the engine; this commits the strategy + the engine picks it up next `/sync`+tick; it is a dormant $330 limit so it will NOT fill at ~$376." Keith -> **"ship it"** (the 4th human gate).
2. `approve-ship 2026-06-07-001`.
3. resume `author_strategy_to_repo`: reconstruct the `decision` (from the approved thesis + the vault `strategy_cdns.md`); write the decision JSON to the payload dir; create child `k2bi-author-strategy-to-repo` (`--parent-task 2026-06-07-001 --flight 2026-06-07-001 --entity CDNS`, payload `symbol=CDNS`, `repo_root=~/Projects/K2Bi`, `payload_path`); `poll-once`. After `done` + the repo file exists -> `record-ship-repo-authored 2026-06-07-001 ~/Projects/K2Bi/wiki/strategies/strategy_cdns.md` (asserts sha == `86fdaf96…`).
4. resume `dispatch_ship`: capital preflight; `mark-ship-dispatch-started 2026-06-07-001` (mints the lease, returns `repo_sha` + `approved_at`); build `FullShipApproval` token = `APPROVE_STRATEGY:cdns:<repo_sha>:<approved_at>:<lease>`; write the ship payload (`strategy_path`, `approval` {token, approved_by="keith", approved_at, ship_lease_id}, `vault_root=~/Projects/K2Bi-Vault`, `required_primary=<as K2Bi review.sh reports>`); create child `k2bi-run-full-ship` (same parent/flight/entity); `poll-once`. The worker runs `run_full_ship` -> commits; its internal plan+diff reviews pass (primary APPROVE).
5. After the ship child `done`: status ok -> `verify-ship 2026-06-07-001` -> `a1_inspect_ship_state` returns `committed` -> `terminal_shipped` -> surface "shipped: commit `<sha>`; engine picks it up next tick." Status error -> `record-ship-failed --reason <...>` -> the independent inspector sets `ship_rollback_clean` -> surface the outcome, stay parked, do NOT re-fire (a `partial_approved_uncommitted` routes to `record-ship-repo-authored` re-author reset before any retry).

**PROVE A NEGATIVE PATH live (one of):** a `mark-ship-dispatch-started` token built against a since-edited strategy file (stale sha), OR a deliberately dirty K2Bi tree, OR a temporarily-present `.killed`, dispatched and shown to be REFUSED + rolled back with NO partial ship (HEAD unchanged). Restore state afterward (the kill-switch is operator-only -- if used for the probe, Keith engages/clears it, NOT A3).

**Live-MVP unknowns to resolve at run time (flag, don't guess):**
- **`required_primary`.** `run_full_ship` defaults `required_primary="minimax"` and `_require_review_approved` checks `primary_used == required_primary`. Determine what K2Bi's `scripts/review.sh` actually reports (`minimax` per the historical naming, routed to Kimi -- but confirm), and pass that value. If it reports `kimi`, pass `kimi`.
- **Long-running ship + zombie reclaim.** `run_full_ship` runs TWO reviews (`REVIEW_TIMEOUT_S=420` each) -> the ship child can run several minutes, longer than the 300s zombie-reclaim heartbeat window. Confirm the worker heartbeats during the long run, OR raise the reclaim timeout for the ship child, so it is NOT reclaimed mid-commit. (Checkpoint-1 item.)
- **Forward-guidance ship-gate strictness.** `handle_approve_strategy`'s `validate_forward_guidance_check` may be stricter than the WRITE-time gate. The A2 decision's `forward_guidance_status: pass` + "none" metric passed the write; confirm it passes the ship gate (the de-risk test exercises `handle_approve_strategy` indirectly; the live run is authoritative).
- **Bear freshness.** `scan_bear_case_for_ticker` requires `bear-last-verified` within `ibc.FRESH_DAYS`. The CDNS bear verdict is from 2026-06-07; confirm it is still fresh at the live-run date (it is, 1-day-old). If stale, refresh the bear-case (a separate K2Bi op) before shipping.

---

## Hard constraints (non-negotiable -- this is the capital path)

- The kill-switch `K2Bi-Vault/System/.killed` is **OPERATOR-ONLY** -- A3 may READ it (capital preflight) but NEVER writes it.
- Validators are **READ-ONLY** from K2B -- A3 never edits `execution/validators/config.yaml`; changes go through `/invest-propose-limits` + operator approval, never mid-flight.
- The engine owns state; the orchestrator reads engine-published vault snapshots only -- no direct `ib_async` / broker calls.
- `run_full_ship` (the merged K2Bi adapter) IS the ship gate -- A3 dispatches it; it does NOT re-implement bear-fresh / backtest-sane / forward-guidance / review checks. All hand-writing of K2Bi state is forbidden; the adapter owns its commit + rollback. The orchestrator records the dispatch + verifies the commit before terminal, and on a returned `rollback_result` surfaces it + stays parked rather than re-firing.
- `~/Projects/K2Bi` must be a CLEAN git tree on origin/main before each dispatch (EXCEPT the single just-authored target strategy file before the ship dispatch); if dirty otherwise, surface to Keith -- do not mutate it.
- No K2Bi code change. If `run_full_ship` / `handle_approve_strategy` is found broken mid-build, it goes via a K2Bi PR, never a direct edit.
- Record `*_authored` / `ship_verified` / `terminal_shipped` flags ONLY after the worker reaches `done` AND the commit/artifact exists + is verified; never hand-set flags or hand-commit.

---

## Landmines A1/A2 already paid for (don't re-pay)

- **Match the REAL interface before dispatching.** A1 burned 4 attempts on stub-shape mismatches; A2 added a committed subprocess test against the REAL K2Bi checkout to de-risk the live call. A3 does the same for the `FullShipApproval` token + the `run_full_ship` pre-review gates (`TestRealK2BiShipShape`). The token format (`APPROVE_STRATEGY:{slug}:{sha}:{approved_at}:{lease}`, approved_at UTC `+00:00`, lease `^[A-Za-z0-9][A-Za-z0-9_.-]{2,127}$`) and the `run_full_ship` signature are taken VERBATIM from the real code (above), not assumed.
- **Clean K2Bi tree before every dispatch.** Surface a dirty tree to Keith; never mutate it. The ship dispatch tolerates EXACTLY the just-authored target strategy file.
- **Flags only after `done` + verified.** No hand-set flags, no hand-commit. The sha-equality binding (repo == A2 vault sha) + the commit re-verification are the evidence anchors.

---

## Checkpoint-1 disposition -- Codex, 2026-06-08 (NEEDS-ATTENTION, primary_used=codex, fallback_used=false) -- all 3 ACCEPTED

Codex grounded the review against the REAL K2B/K2Bi code paths (it read `orchestrator_worker.py`, the real `run_full_ship`, `handle_approve_strategy`, the store terminal constants). It **confirmed the core resolution holds**: "I did not find a supported blocker in the token string format itself; the real validator matches `APPROVE_STRATEGY:{slug}:{sha}:{approved_at}:{lease}` and checks UTC offset plus lease regex. The repo/vault split is directionally compatible with `handle_approve_strategy(vault_root=...)`." All 3 findings are real, exactly-cited capital-safety/correctness gaps and cheap to close at the plan stage (no code yet). Folded in:

**F1 (HIGH) -- worker SIGKILL mid-ship -> approved-uncommitted partial.** The K2B worker hard-kills the child at `K2B_ORCH_CMD_TIMEOUT` (default 540s); `run_full_ship`'s plan(≤420s) + approve_handler (rewrites file to `approved`) + diff(≤420s) + commit can cross 540s, SIGKILLing AFTER the approve rewrite but BEFORE commit/rollback -> a `status: approved` file that was never committed and never rolled back. **Fix:** per-command worker timeout (`K2B_ORCH_SHIP_CMD_TIMEOUT`, default 1200s, > `2*REVIEW_TIMEOUT_S + margin`); the heartbeat keeps the task alive so no reclaim fires; AND the independent `a1_inspect_ship_state` classifier guarantees an approved-uncommitted partial is NEVER marked `terminal_shipped` (it parks for re-author recovery). Test: simulate a kill after the approve rewrite -> no "shipped" partial survives.

**F2 (HIGH) -- `rollback_result` is absent for real ship-gate refusals.** `run_full_ship` only attaches `rollback_result` when the original exception is already `OrchestratorGateError`; but `handle_approve_strategy` raises its OWN `iss.ValidationError` for forward-guidance / vault / bear / backtest refusals, so the rollback runs (file restored) yet the re-raised exception carries NO `rollback_result`. The original plan's `_adapter_error_envelope(getattr(exc,"rollback_result"))` would see `None` for those (common) paths -> the conductor couldn't set `ship_rollback_clean` or safely offer retry. **Fix (K2B-side, no K2Bi change):** `a1_inspect_ship_state` is the INDEPENDENT source of truth -- it reads git HEAD + the repo strategy file status + the rollback marker and derives `ship_rollback_clean` itself; the worker's `rollback_result` is recorded only as supplementary evidence when present. Test: an `approve_handler` raising `iss.ValidationError` still yields a correct `clean_rollback` + retry-eligible park. (My live-MVP negative path -- stale sha / bad token / dirty tree / kill-switch -- all DO produce `OrchestratorGateError` or are caught by the K2B capital preflight pre-dispatch, so the live probe is unaffected; F2 hardens the operator-hit bear/backtest refusal class.)

**F3 (MEDIUM) -- `terminal_shipped` needs more than the constant.** `add_task`'s entity-lock + exemption queries and `poll_once`'s parent-liveness check hard-code `status NOT IN ('done','failed','cancelled','terminal_bear_veto')`, so a shipped chain would still hold the entity lock + be a live parent. **Fix:** derive the terminal IN-clause from `TERMINAL_STATUSES` at every hard-coded site; return `terminal_shipped` AS-IS in `a1_resume_action_locked` (not `terminal_terminal_shipped`). Tests: a shipped parent releases the entity lock + rejects/cancels a queued child.

No second Checkpoint-1 round -- the findings are concrete (not fine-grained style); they are folded into the spec above and will be implemented with regression tests. Codex's two HIGH findings are exactly the capital-path partial-ship / rollback-integrity class that AR7 does NOT permit deferring on this increment.

## Discipline

**Checkpoint-1 plan review (this file) -- Codex, MANDATORY before any code** (`scripts/review.sh plan --plan plans/2026-06-08_orchestrator-phase-a3-build-spec.md --wait`; Kimi fallback only if Codex is unreachable). The plan MUST resolve: (1) the repo-vs-vault blocker (RESOLVED above -- re-author + sha-bind, evidence in vault, Keith-confirmed) -- Codex confirms it against the REAL K2Bi `run_full_ship` + `handle_approve_strategy` + the engine loader; (2) the replay-guard + rollback-recovery design (dispatch recorded before firing, sha-bound token, incomplete-rollback-marker backstop, verify-commit-before-terminal, never-auto-re-dispatch, bounded retry-only-after-clean-rollback); (3) the long-running-ship vs zombie-reclaim window; (4) the `required_primary` value. Close every accepted finding here BEFORE code.

Checkpoint-1 findings are closed (folded in above). **Codex is the builder** (`codex/orchestrator-a3` branch, via `.codex/job.md`) -- so the official Checkpoint-2 adversarial gate is **Kimi-backed, NOT Codex** (builder != reviewer; cross-model rule, A1 pattern -- Keith-confirmed 2026-06-08). **Checkpoint-2 pre-commit review: `scripts/review.sh diff --files <changed> --primary minimax --no-fallback --wait`** (HEAVIEST -- expect multiple rounds; Codex-the-builder owns finding disposition; fix every real correctness/evidence-integrity finding with a regression test; apply the AR7 architect-disposition only once findings genuinely go fine-grained -- and on THIS capital-path increment the disposition bar is higher than A1/A2: a real correctness, partial-ship, or rollback-integrity finding is NEVER AR7-disposed). `--no-fallback` converts reviewer-unavailability into an explicit stop, never a silent Codex self-review of Codex-built code.

Then `/ship`: mark A3 gate-passed, update the feature note Shipping Status + Full Scope Tracker (Stage 11) + `wiki/concepts/index.md`, DEVLOG, wiki/log, `/sync`. **A3 closes Phase A.** The only remaining increment is Phase B (Stage-15 retro, a new K2Bi skill via PR).
