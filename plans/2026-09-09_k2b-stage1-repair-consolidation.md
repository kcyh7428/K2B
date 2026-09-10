# K2B Stage 1 Repair and Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make two-Mac K2B conversation capture, source-backed recall, retained Kimi work/review, and truthful operational status dependable without the Mac Mini, retired Claude/Telegram runtime, local models, or new metered OpenAI API use.

**Architecture:** The home Mac is the sole writer for the synchronized vault and runs one catch-up-capable Codex-session reconciliation path. The SJM Mac discovers and exports selected immutable Codex sources and reads knowledge returned through Syncthing while code moves through Git; per-machine state and credentials stay local. Capture records durable source identity, provenance, attempts, and terminal/non-terminal state so interruption, offline peers, malformed output, and retries cannot be mistaken for success.

**Tech Stack:** Python 3.12, Bash 3.2-compatible shell, Codex JSONL, Kimi provider wrappers, Syncthing, pytest, shell integration tests.

**Spec:** `plans/2026-07-21_claude-removal-codex-home-manager-design.md`, `plans/2026-07-21_claude-removal-codex-home-manager-implementation.md`, and the delegated Stage 1 brief in task `01a0844d-59e9-7700-8faa-523187d245d1`.

## Global Constraints

- Authorized hosts are this home MacBook and `GLPs-MacBook-Pro`; never contact or create a deployment obligation for the Mac Mini.
- Stage 2 is excluded: no Ollama, MLX, model download, local-inference benchmark, or remote-inference design.
- Codex remains sole live instruction authority; Kimi remains the cloud worker and independent reviewer on both Macs.
- Preserve immutable raw transcripts, personal history, credentials, unrelated app state, Service Motion, Talent Radar, and K2Bi production/trading state.
- Home owns all synchronized-vault writes. SJM local source bundles, discovery receipts, and purpose-built small pending records live under `~/.local/state/k2b/`; other writes must be rerun on Home.
- No automatically enabled paid fallback and no new OpenAI API dependency.
- Keith subsequently authorized review, commit, push, two-Mac activation, scoped transport, backlog recovery, quarantine of the two identified legacy home LaunchAgents, and Obsidian credential rotation. No additional delivery confirmation is needed. No Git history rewrite or excluded-host access is authorized.
- Keith selected automatic discovery with interactive Codex/Kimi capture. Discovery alone may run unattended; Kimi Code membership must not be used for scripted batch extraction. Keep backlog evidence waiting until actually reconciled during a requested session.

---

### Task 1: Reconcile preserved migration work and freeze current evidence

**Files:**
- Modify: this plan and the dated Stage 1 acceptance record created in Task 6
- Reuse selectively: commits `fd91306` and `f87cdcd` from `codex/task-3a-3b-clean-slate`

- [ ] Verify this linked worktree, branch, baseline tests, canonical/SJM checkout status, current schedules/process owners, Syncthing peers, and per-host Kimi surfaces without printing credentials.
- [ ] Compare the preserved commits and review evidence to current `main`; retain only behavior consistent with the two-Mac scope and fix every known failed-review finding before reuse.
- [ ] Inventory active and dormant Claude/Telegram owners before any removal; classify unknown or destructive targets as decision-required.

### Task 2: Repair catch-up-capable, source-backed Codex capture

**Files:**
- Modify: `scripts/lib/eod_capture.py`, `scripts/eod-capture.py`, `scripts/eod-capture-cron.sh`, and focused tests
- Modify or create only if required by tests: capture status/receipt helpers under `scripts/lib/`

- [ ] Add failing tests for configured-root enforcement, discovery across missed dates, immutable-source identity, durable attempt state, retryable provider/malformed/interruption failures, and replay idempotency.
- [ ] Implement the minimum Codex-only discovery/extraction/reconciliation behavior that passes those tests and cannot label staged-only work as processed.
- [ ] Replace yesterday-only semantics with explicit backlog reconciliation and truthful `last discovered / extracted / reconciled / waiting / failed / disabled` status.

### Task 3: Consolidate live authority and retire obsolete K2B routes

**Files:**
- Modify: `.codex/hooks.json`, `AGENTS.md`, `scripts/verify-codex-authority.sh`, and focused authority/hook tests
- Reuse after correction where applicable: `scripts/export-claude-history.py`, `scripts/inventory-retired-runtime.sh`, their tests, and `docs/runbooks/k2b-clean-slate-removal.md`

- [ ] Write failing tests proving the authority scanner covers every actually loaded repo/vault/renderer surface while allowing narrow immutable-history references.
- [ ] Remove or disable unsupported Codex hook matchers and dormant background claims; ensure direct-file discovery is independent of a manual `Stop` hook.
- [ ] Correct live rules/context so Claude/Telegram are history or rollback evidence, never required runtime instructions.
- [ ] Perform only evidence-preserving, K2B-owned retirement whose ownership is proven; leave credential revocation and ambiguous deletion as explicit approvals.

### Task 4: Restore truthful manual intake and Kimi integration

**Files:**
- Modify as evidence requires: `k2b-dashboard/src/server/routes/intake.ts`, provider wrappers, and focused tests

- [ ] Add a failing intake test showing a manifest without a verified worker cannot be reported as processing; implement truthful manual/waiting/disabled states.
- [ ] Safely verify home and SJM Kimi CLI/provider configuration, supported CLI migration path, model identifier, and entitlement without extracting tokens or changing unrelated projects.
- [ ] Run harmless authenticated worker and independent-review probes through the intended per-host interfaces; record exact credential/entitlement blockers instead of adding a paid fallback.

### Task 5: Prove two-host behavior without enabling duplicate automation

**Files:**
- Create only local test fixtures/evidence paths; do not use real shared-hub notes until Syncthing is healthy and single-writer conditions are confirmed

- [ ] Prove one fresh synthetic/test conversation per Mac is discovered through a real supported lifecycle or direct-file mechanism, extracted with provenance, reconciled, and retrievable from a fresh counterpart session.
- [ ] Replay sources and exercise malformed extraction, provider failure, interruption, offline counterpart, missed-day catch-up, and concurrent-writer exclusion.
- [ ] Reconcile the historical backlog against immutable source IDs and existing receipts; report totals and unresolved exceptions without accessing the Mini.
- [ ] Verify representative manual read/write/recall on both Macs and healthy zero-pending Syncthing before shared-vault acceptance edits.

### Task 6: Acceptance evidence, independent review, and delivery checkpoint

**Files:**
- Create: `plans/2026-09-09_k2b-stage1-acceptance.md`
- Modify: operator-facing docs and tests required to keep status truthful

- [ ] Record done checks A-G as `implemented`, `tested`, `activated`, or `blocked`, with dated commands, host, source/receipt counts, and exact exceptions.
- [ ] Run focused tests first, then `scripts/verify-codex-authority.sh`, relevant hook/dashboard/provider suites, `git diff --check`, and the bounded final suite justified by changed paths.
- [ ] Run independent review with `scripts/review.sh ... --builder-family openai --primary kimi --no-fallback --wait`; fix findings using red-green tests and rerun only invalidated gates.
- [ ] After independent approval, deliver the exact reviewed cohort and activate home/SJM deliberately under Keith's granted authority; never create a Mini pending-sync entry.

## Acceptance Matrix

| Check | Required evidence |
|---|---|
| A | One new source from each Mac reaches counterpart recall with immutable provenance. |
| B | Replay is duplicate-free; malformed/provider/offline/interrupted runs remain retryable and truthful. |
| C | Source-versus-receipt backlog counts reconcile, with named unresolved source IDs. |
| D | Harmless authenticated Kimi worker and independent-review probe succeeds on both Macs. |
| E | Loaded live surfaces and active K2B runtime have no Claude/Telegram dependency; historical exceptions are explicit. |
| F | Manual read/write/recall, healthy Syncthing, and home-only shared-hub writer are demonstrated. |
| G | Docs, tests, receipts, and status distinguish implemented, tested, activated, waiting, failed, and disabled. |
