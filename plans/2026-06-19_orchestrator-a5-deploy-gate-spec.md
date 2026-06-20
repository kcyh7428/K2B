# Orchestrator A5 Deploy Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build A5 deploy-to-engine so a K2Bi repo ship cannot silently stop before the live VPS engine is updated or a durable pending-deploy marker is left behind.

**Architecture:** A5 wraps the existing K2Bi `scripts/deploy-to-vps.sh` path instead of replacing it. K2B owns the human gate, flight state, approval token, pending marker, and independent verification; K2Bi owns the deploy script, runtime verification, rsync, restart, and any machine-readable deploy manifest helpers needed by K2B. Option 1 is selected: Codex/Claude may run the deploy only after an explicit permission rule exists and a per-flight `approve-deploy` token is minted.

**Tech Stack:** Python 3.12, SQLite orchestrator store, existing K2B orchestrator worker/profile modules, K2Bi bash deploy script, pytest/unittest.

---

## Human Decision

Keith selected **Option 1** on 2026-06-19:

- Add an explicit permission rule for `scripts/deploy-to-vps.sh`.
- Keep a per-flight human gate named `approve-deploy`.
- Let the orchestrator run the approved deploy only after preview + approval token + preflight pass.

This does **not** authorize a live deploy during implementation, live broker mutation, order placement, kill-switch mutation, validator mutation, or direct `ib_async` / Gateway mutation. Any later live deploy run needs its own written PM gate naming the action, account/clientId where relevant, expected pre-state, and stop condition.

## Exact MVP Behavior

Named bug: `ORCHESTRATOR-SHIPS-TO-REPO-BUT-STRATEGY-NEVER-REACHES-LIVE-ENGINE`.

A5 passes when all of this is true in tests, and later in a gated live run:

1. After an A3 `terminal_shipped` or A4 `terminal_limits_applied` event, the orchestrator can create or resume an A5 deploy flight for the target K2Bi repo sha.
2. The first A5 step is a **preview**: K2B runs the K2Bi deploy dry-run/manifest path, shows the repo sha, sync-state baseline, deploy categories, expected restart services, and the concrete live effect in human language.
3. If Keith defers, A5 writes a durable pending-deploy marker and leaves the flight visible. The next session can surface it. No deploy command runs.
4. If Keith approves, `approve-deploy` mints a token bound to:
   - K2Bi repo sha being deployed
   - remote/VPS sync-state baseline or remote runtime HEAD captured during preview
   - dry-run manifest hash
   - ISO approved-at timestamp
   - deploy lease id
5. The worker runs the existing K2Bi deploy path only with that token present and current.
6. Independent verification is not based on worker self-report alone. It must confirm:
   - remote runtime git checkout is valid and at the expected sha where applicable
   - `.sync-state/last-synced-commit` advanced to the approved sha for rsynced categories
   - `k2bi-engine.service` is active if a restart was expected
   - journal/recovery output has no new `recovery_state_mismatch`
   - live validator/ticker state matches the approved ship or limits change when applicable
7. Only a clean verification sets the parent flight to `terminal_deployed`.

## Non-Goals

- No live broker mutation in this implementation/spec step.
- No live deploy during implementation tests.
- No direct IBKR / `ib_async` / Gateway mutation from K2B.
- No kill-switch create/delete.
- No direct validator edit by K2B.
- No new deploy transport. Reuse K2Bi `scripts/deploy-to-vps.sh`.
- No attempt to make deploy automatic immediately after every commit. A5 is gated.

## File / Module Map

### K2B

- `scripts/lib/orchestrator_store.py`
  - Add `terminal_deployed` to terminal statuses.
  - Add A5 payload fields and resume ladder.
  - Add CLI subcommands:
    - `record-deploy-preview`
    - `authorize-deploy`
    - `mark-deploy-dispatch-started`
    - `verify-deploy`
    - `record-deploy-failed`
    - `defer-deploy`
    - `inspect-deploy-state`
  - Add durable pending marker writer/reader under `K2B-Vault/System/orchestrator/pending-deploy/`.

- `scripts/lib/orchestrator_profiles.py`
  - Add allowlisted command key `k2bi-deploy-to-vps`.
  - Add capital preflight for deploy:
    - requires Option-1 permission scope to exist in the local environment/config
    - requires approved deploy token
    - requires K2Bi repo path to be an actual git checkout
    - requires `scripts/deploy-to-vps.sh` to exist and be executable
    - refuses stale token, changed manifest, changed repo sha, or changed remote baseline
    - reads kill-switch state only; never writes it

- `scripts/lib/orchestrator_worker.py`
  - Treat `k2bi-deploy-to-vps` as a capital command.
  - Use `K2B_ORCH_SHIP_CMD_TIMEOUT` default, same as A3/A4.

- `scripts/k2b-orchestrator.sh`
  - Expose the new store subcommands if the shell wrapper currently enumerates them.

- `.agents/skills/k2b-orchestrator/SKILL.md`
- `.claude/skills/k2b-orchestrator/SKILL.md`
  - Add A5 conductor instructions.
  - Keep the two skill surfaces in parity.

- `tests/orchestrator/test_orchestrator_a5.py`
  - New focused test file. Write this first.

### K2Bi

- `scripts/lib/deploy_orchestrator.py` or equivalent small helper
  - Provide machine-readable A5 preview/deploy/verify envelopes around `scripts/deploy-to-vps.sh`.
  - Prefer JSON output so K2B does not parse human dry-run text.
  - No broker API calls.

- `scripts/deploy-to-vps.sh`
  - Only change if needed to emit a stable dry-run manifest or to expose restart/sync-state facts already known by the script.
  - Do not change its fundamental rsync/restart/snapshot behavior.

- `tests/test_deploy_coverage.py`
  - Add deploy helper coverage if K2Bi helper changes are required.

## A5 Durable Payload Fields

Use explicit A5 names. Do not overload A3/A4 token names.

```json
{
  "chain_kind": "deploy",
  "deploy_source_parent": "2026-06-19-001",
  "deploy_source_status": "terminal_shipped | terminal_limits_applied",
  "deploy_target_sha": "<40-char git sha>",
  "deploy_preview_recorded": true,
  "deploy_preview_manifest_path": "<vault/system path>",
  "deploy_preview_manifest_sha256": "<sha256>",
  "deploy_remote_baseline_sha": "<40-char git sha or sync-state sha>",
  "deploy_categories": ["execution", "scripts"],
  "deploy_restart_services": ["k2bi-engine.service"],
  "deploy_authorized": true,
  "deploy_lease_id": "<slug>-deploy-a1-YYYYMMDDTHHMMSSZ",
  "deploy_approved_at": "<ISO-8601 UTC>",
  "deploy_approval_token": "APPROVE_DEPLOY:<target_sha>:<remote_baseline_sha>:<manifest_sha256>:<approved_at>:<lease_id>",
  "deploy_dispatch_started_at": "<ISO-8601 UTC>",
  "deploy_attempt_count": 1,
  "deploy_verified": true,
  "deploy_verified_at": "<ISO-8601 UTC>",
  "deploy_deployed_sha": "<40-char git sha>",
  "deploy_partial_detected_at": null,
  "deploy_rolled_back_at": null
}
```

Token format:

```text
APPROVE_DEPLOY:<target_sha>:<remote_baseline_sha>:<manifest_sha256>:<approved_at>:<deploy_lease_id>
```

`approved_at` must be ISO-8601 UTC with `+00:00`. `deploy_lease_id` must match the same lease regex family used by A3/A4.

## Resume Ladder

For payload `chain_kind=deploy`:

1. No preview -> `preview_deploy`.
2. Preview recorded, no decision -> `await_deploy_approval`.
3. Deferred -> `deploy_deferred`.
4. Authorized, not dispatched -> `dispatch_deploy`.
5. Dispatched, not verified -> `verify_deploy`.
6. Verified or row status `terminal_deployed` -> `terminal_deployed`.
7. Partial/rollback marker -> `deploy_partial` or `deploy_rolled_back`.
8. Attempt limit hit -> park as `needs_human` with `terminal_reason=deploy_attempt_limit_exceeded`.

## Pending Marker Schema

Path:

```text
K2B-Vault/System/orchestrator/pending-deploy/<target_sha>-<source_parent>-<task_id>.json
```

Schema:

```json
{
  "type": "k2bi-pending-deploy",
  "created_at": "<ISO-8601 UTC>",
  "source_parent": "2026-06-19-001",
  "source_status": "terminal_shipped | terminal_limits_applied",
  "target_sha": "<40-char git sha>",
  "remote_baseline_sha": "<40-char git sha or sync-state sha>",
  "preview_manifest_sha256": "<sha256 or null>",
  "reason": "operator_deferred",
  "next_action": "run A5 preview again, then approve-deploy or defer"
}
```

## Verification Result Schema

Trusted verification evidence is JSON under:

```text
K2B-Vault/System/orchestrator/deploy-results/*.json
```

Minimum clean result:

```json
{
  "state": "deployed",
  "remote_head": "<approved target sha>",
  "sync_state_sha": "<approved target sha>",
  "services": {"k2bi-engine.service": true},
  "service_active": true,
  "recovery_state_mismatch_count": 0,
  "approval_token": "APPROVE_DEPLOY:...",
  "dispatch_started_at": "<deploy_dispatch_started_at from parent payload>",
  "dispatch_nonce": "<deploy_dispatch_nonce from parent payload>"
}
```

`services` is preferred and must mark every expected restart service true. `service_active: true` is only a
compatibility fallback when no named service map is present. Evidence whose token, dispatch time, or nonce does
not match the current dispatch is refused.

Category-scoped clean result, for deploys where `deploy-to-vps.sh` synced only the previewed categories and the
VPS git `HEAD` intentionally remains at an older checkout, extends the clean result with:

```json
{
  "verification_scope": "category_scoped",
  "remote_head": "<observed VPS git head, may be older than target>",
  "deployed_categories": ["scripts", "skills"],
  "category_results": {
    "scripts": {
      "matched_target": true,
      "path_count": 2,
      "missing_paths": [],
      "mismatched_paths": [],
      "extra_paths": []
    },
    "skills": {
      "matched_target": true,
      "path_count": 2,
      "missing_paths": [],
      "mismatched_paths": [],
      "extra_paths": []
    }
  }
}
```

`remote_head` must equal the preview manifest's recorded `remote_baseline_sha`, not an arbitrary stale SHA.
`sync_state_sha` must still equal the approved target SHA and must not still equal the recorded baseline SHA.
`verification_scope` must be exactly `category_scoped`; unknown scope strings are refused. `deployed_categories`
must exactly match the preview manifest's `categories` set, and both category lists must be non-empty string
lists with no empty or duplicate entries. Every expected category must have `matched_target: true`, positive
non-bool integer `path_count`, and present empty-list `missing_paths`, `mismatched_paths`, and `extra_paths`.
`path_count` is a trusted deploy-helper assertion over its category path set; the verifier requires it to be
positive but does not infer file names from the count. Successful category-scoped verification records
`deploy_verification_scope: category_scoped` in the parent payload for audit and recovery. This proves the
category-scoped deploy contract without pretending the full VPS checkout has advanced to the target commit.

## Tests To Add First

### K2B focused tests

File: `tests/orchestrator/test_orchestrator_a5.py`

- `test_a5_status_is_terminal_deployed_and_releases_entity_lock`
- `test_a5_resume_ladder_from_preview_to_terminal_deployed`
- `test_a5_defer_writes_pending_deploy_marker_without_dispatch`
- `test_a5_authorize_deploy_token_binds_target_sha_remote_baseline_and_manifest`
- `test_a5_preflight_refuses_stale_manifest_hash`
- `test_a5_preflight_refuses_changed_remote_baseline`
- `test_a5_preflight_refuses_missing_permission_scope`
- `test_a5_verify_requires_independent_inspector_clean_state`
- `test_a5_verify_refuses_recovery_mismatch`
- `test_a5_attempt_limit_parks_needs_human_terminal`

Run first and confirm red:

```bash
pytest tests/orchestrator/test_orchestrator_a5.py -q
```

### K2Bi focused tests if helper changes are required

File: `tests/test_deploy_coverage.py`

- `test_a5_preview_emits_json_manifest_without_rsync`
- `test_a5_deploy_helper_calls_existing_deploy_script_with_pinned_sha`
- `test_a5_verify_reports_service_active_sync_state_and_remote_head`
- `test_a5_verify_reports_recovery_mismatch_as_refusal`

Run first and confirm red:

```bash
cd /Users/keithmbpm2/Projects/K2Bi
pytest tests/test_deploy_coverage.py -q
```

## Implementation Tasks

### Task 1: K2B red tests for A5 state machine

- [ ] Add `tests/orchestrator/test_orchestrator_a5.py`.
- [ ] Cover the resume ladder, token binding, defer marker, terminal status, and independent verification refusal paths.
- [ ] Run `pytest tests/orchestrator/test_orchestrator_a5.py -q`.
- [ ] Expected before implementation: failures for missing A5 subcommands/statuses.

### Task 2: K2B store and CLI

- [ ] Add `terminal_deployed`.
- [ ] Add A5 resume ladder.
- [ ] Add A5 CLI subcommands.
- [ ] Add pending-deploy marker writer.
- [ ] Run `pytest tests/orchestrator/test_orchestrator_a5.py -q`.

### Task 3: K2B profile/worker deploy dispatch

- [ ] Add `k2bi-deploy-to-vps` command key.
- [ ] Add preflight checks for token, manifest hash, repo sha, remote baseline, script existence, and permission scope.
- [ ] Add capital timeout handling in `orchestrator_worker.py`.
- [ ] Run:

```bash
pytest tests/orchestrator/test_orchestrator_a5.py tests/orchestrator/test_orchestrator_worker.py -q
```

### Task 4: K2Bi deploy manifest helper if needed

- [ ] Add a small helper around `deploy-to-vps.sh` that emits JSON preview/deploy/verify envelopes.
- [ ] Keep the deploy script as the only real deploy executor.
- [ ] Do not add broker API calls.
- [ ] Run:

```bash
cd /Users/keithmbpm2/Projects/K2Bi
pytest tests/test_deploy_coverage.py -q
```

### Task 5: A5 conductor docs

- [ ] Update `.agents/skills/k2b-orchestrator/SKILL.md`.
- [ ] Mirror the same changes to `.claude/skills/k2b-orchestrator/SKILL.md`.
- [ ] Add the human wording:

```text
This deploys the already-approved K2Bi repo state to the live VPS engine. It may restart k2bi-engine.service. It does not approve a new trade, change validators, clear a kill-switch, or place an order by itself.
```

- [ ] Run parity check:

```bash
bash scripts/verify-skills-parity.sh
```

### Task 6: Verification and review

- [ ] Run focused K2B tests:

```bash
pytest tests/orchestrator/test_orchestrator_a5.py -q
pytest tests/orchestrator/test_orchestrator_a3.py tests/orchestrator/test_orchestrator_a4.py -q
pytest tests/orchestrator/test_orchestrator_worker.py -q
```

- [ ] Run focused K2Bi tests if changed:

```bash
cd /Users/keithmbpm2/Projects/K2Bi
pytest tests/test_deploy_coverage.py -q
```

- [ ] Run broader tests where reasonable:

```bash
pytest tests/orchestrator -q
bash tests/verify-skills-parity.test.sh
```

- [ ] Review gate: because Codex/OpenAI built the diff, use independent Kimi-backed review:

```bash
BUILDER_FAMILY=openai scripts/review.sh --builder-family openai --primary minimax --no-fallback --wait
```

## Live Run Gate For Later

The implementation PR does not run a live deploy. A later live A5 MVP run must have a written PM gate with:

- action: exact deploy command and category/sha
- client/account context: `DUQ220152` paper engine if applicable
- expected pre-state: remote HEAD/sync-state, service status, relevant ticker/validator state
- stop condition: abort on preview mismatch, token mismatch, service restart failure, recovery mismatch, or unexpected live state
- rollback/recovery plan: use existing `deploy-to-vps.sh` snapshot restore behavior and do not place/manual-cancel broker orders from K2B

## Spec Coverage Self-Review

- MVP behavior: covered.
- Option 1 decision: covered.
- No live broker mutation: covered.
- K2B/K2Bi file boundaries: covered.
- Tests first: covered.
- Verification commands: covered.
- Missing ambiguity: exact K2Bi helper shape may be refined during implementation after reading the deploy script tests, but the invariant is fixed -- K2B must consume structured deploy facts and must not parse brittle human dry-run text.
