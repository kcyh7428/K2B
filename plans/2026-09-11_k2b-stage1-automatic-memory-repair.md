# K2B Stage 1 Automatic Memory Repair Implementation Plan

> **For agentic workers:** Execute checkpoint-by-checkpoint with test-first changes. Each checkpoint ends with focused verification, an Astra architecture review, correction of actionable findings, and a truthful tracker update. No checkpoint authorizes commit, push, activation, paid services, or Stage 2 work.

**Goal:** Make useful approved K2B conversations automatically digestible into accurate, source-backed current knowledge that either authorized Mac can recall, while Home remains the only shared-vault writer.

**Architecture:** Codex session JSONL is parsed once into immutable completed-turn snapshots with stable host/session/event identities and bounded full-dialogue chunks. Supported Codex Automations will eventually trigger a bounded worker, but only after a synthetic Home feasibility probe establishes the actual cadence, host access, quota, missed-run recovery, and receipt behavior. SJM keeps durable local delivery state and provisional recall; Home orders corrections by source evidence and publishes canonical recall through one indexed entry point.

**Tech stack:** Python 3.12, Bash 3.2-compatible shell, Codex JSONL, local durable JSON state, Syncthing, pytest, shell integration tests.

**Spec:** `/Users/keithmbpm2/Projects/K2B-Vault/wiki/reference/2026-09-11_astra-k2b-design-audit.md` and `/Users/keithmbpm2/Projects/K2B-Vault/wiki/concepts/feature_codex-primary-migration.md`

## Global constraints

- Authorized hosts: Home MacBook and `GLPs-MacBook-Pro` only. Never contact or create work for the retired Mac Mini.
- Home is the sole shared-vault writer. Code moves only through reviewed Git delivery; vault content moves only through Syncthing.
- Preserve all existing Home untracked work and SJM adaptations. Do not touch credentials, personal app history, Service Motion, Talent Radar, or K2Bi production/trading systems.
- Kimi remains interactive worker and required independent reviewer for OpenAI-built diffs; its subscription is not an unattended extraction service.
- No metered OpenAI API dependency, paid fallback, production schedule, commit, push, activation, or Stage 2 local model work.
- Status vocabulary is exact: proposed, implemented, tested, reviewed, activated, accepted.

### Task 1: Checkpoint 1 -- runner feasibility and trustworthy ingestion

**Files:**

- Modify: `scripts/lib/eod_capture.py`
- Modify: `tests/test_eod_capture.py`
- Modify: `tests/test_eod_capture_status.py`
- Modify only if the extraction contract changes: `scripts/prompts/eod-capture-extract.md`
- Modify only to reconcile live policy after behavior is implemented: `AGENTS.md`

- [ ] Record the supported Codex Automation capability and the current Home control-surface limitation; register only a synthetic bounded probe if the native control becomes callable, then disable it after evidence is collected.
- [ ] RED: add regression fixtures proving dialogue after 500, 4,000, and 8,000 characters survives; an incomplete JSONL tail is excluded without losing the completed prefix; late turns in an old active task are discoverable; roles remain explicit; assistant proposals are not Keith decisions; worker/imported sources are excluded.
- [ ] GREEN: introduce one parser that emits stable host/session/event identities, immutable completed-turn cursors, and bounded chunks without truncating eligible user/assistant dialogue. Reuse it for local extraction and transported bundles.
- [ ] GREEN: bind extraction and receipts to the completed prefix rather than a growing whole-session hash, so appending later events does not invalidate already completed work and replay remains duplicate-free.
- [ ] Run `python3 -m pytest tests/test_eod_capture.py tests/test_eod_capture_status.py` and `bash tests/codex-discovery-job.test.sh`.
- [ ] Obtain a bounded GPT-6 Astra architecture review of the checkpoint diff, resolve actionable findings with failing tests first, and rerun invalidated checks.

### Task 2: Checkpoint 2 -- automatic digestion, delivery, current knowledge, and recall

**Files:**

- Modify: `scripts/lib/eod_capture.py`
- Modify: `scripts/eod-capture.py`
- Modify: `scripts/codex-discovery-job.sh`
- Create or modify focused durable-state helpers under `scripts/lib/` only when the checkpoint-1 interfaces cannot own the responsibility cleanly
- Modify: `tests/test_eod_capture.py`, `tests/test_eod_capture_status.py`, and `tests/codex-discovery-job.test.sh`
- Create: one indexed Home recall note and its source/provenance state through the authorized Home writer after a healthy Syncthing pre-write check

- [ ] RED/GREEN: implement bounded automatic Home digestion with durable retry and no paid/Kimi batch fallback; quota exhaustion, persistent failure, and genuine ambiguity remain explicit exceptions.
- [ ] RED/GREEN: implement durable SJM-to-Home export/delivery state that survives restart, reports Home downtime honestly, and drains idempotently when Home returns.
- [ ] RED/GREEN: preserve facts, decisions, explicit preferences, and open commitments with source timestamps and citations; an older late-arriving bundle cannot override newer source evidence.
- [ ] RED/GREEN: provide one current-recall entry point that returns the current answer plus provenance and retains superseded history.
- [ ] Run focused Python/shell checks, obtain Astra review, resolve findings, and update the shared tracker without claiming activation.

**2026-09-12 integration checkpoint:** The coordinator-preserved Astra `PASS`
closes the unchanged deterministic module findings only. The expanded frozen
candidate adds bounded worklist/extraction receipts, enforced retry cadence,
explicit filtered outcomes, durable SJM pull/ack, a resumable Home drain, a
gated real Home publisher and a native-automation prompt that omits publication.
Evidence from an earlier cumulative turn is rejected instead of being redated
to a later work item. Extraction receipts bind the retained reviewed artifact
and exact source-derived delivery identities. The final synthetic flow starts
with twenty literal source statements and reaches the real publisher through an
injected healthy preflight, then recalls the shared JSON with citations through
fresh SJM-role processes. The frozen candidate is recorded in
`task-2-native-integration-correction.md`; 416 combined tests and the five
external correction reproducers pass. After the final Astra/Kimi findings were
reproduced and corrected at their exact boundaries, the stable candidate is
`c60e4826c4a40fc46fc72c2b6d334e6b3bfdb3f4f0d4a50cb8ee2216857f07db`;
446 combined tests and all five external reproducers pass. The unchanged
coordinator-owned publisher has scoped Astra and independent Kimi approval.
Native orchestration also has scoped Astra PASS and Kimi APPROVE. The final
capture candidate has scoped Astra PASS, while its independent Kimi persistence
review is blocked after two informed packets truncated without a verdict. No
live SJM transport, vault publication or activation has occurred.

### Task 3: Checkpoint 3 -- replacement proof, simplification, and rollout readiness

**Files:**

- Modify only mechanisms proven redundant by replacement tests in `scripts/`, `.agents/skills/`, `.codex/hooks.json`, `AGENTS.md`, and corresponding focused tests
- Modify: `plans/2026-09-09_k2b-stage1-acceptance.md`

- [ ] Prove replacement guarantees before removing duplicate parser, queue, scheduler, and live-rule paths; retain on-demand research, media, NotebookLM, and email capabilities.
- [ ] Run the twenty-seed recall set, late-message, crash/replay, out-of-order correction, worker-recursion, and 48-hour offline recovery simulations.
- [ ] Run authority, hook, capture, dashboard, and diff gates justified by changed paths; obtain Astra architecture review and the required Kimi independent code review.
- [ ] Prepare exact Home/SJM activation and seven-day unattended-trial steps, preserving SJM adaptations. Do not activate without separate authority.
- [ ] Validate a practical shared-recall target from observed runner cadence; do not claim the proposed 15-minute target until measured.

**2026-09-12 rollout preparation:** The synthetic twenty-item flow passes across
fact, decision, preference and commitment kinds with cited SJM-role recall. The
acceptance record now contains a replacement-proof matrix, exact two-host
activation order, seven-day unattended-trial evidence and rollback boundary.
No existing mechanism was removed because live replacement proof is incomplete.

## Checkpoint 1 Ship Card

**Base/HEAD:** `078981f6feb99e8c4bf0e52ca1bc5cd00e794b49` in the isolated Codex worktree. Home main is the same commit with pre-existing untracked `output/`, `plans/2026-07-27_k2b-clean-slate-removal-design.md`, `plans/2026-07-29_k2b-clean-slate-removal-implementation.md`, and `tmp/`, all protected.

**Manager-owned:** this plan, `plans/.stage1-automatic-memory-ship-state.json`, review evidence, and shared-vault tracker updates.

**Done checks:** complete eligible dialogue survives bounded chunking; stable identities and completed-prefix cursors permit old active tasks and late turns; local and transported parsing agree; worker/imported inputs are excluded; replay does not duplicate; runner evidence is truthful.

**Focused gate:** `python3 -m pytest tests/test_eod_capture.py tests/test_eod_capture_status.py && bash tests/codex-discovery-job.test.sh`

**Review:** GPT-6 Astra for checkpoint architecture; Kimi remains the required independent shipping reviewer for the eventual stable OpenAI-built cohort.

**Stops:** material scope drift, required writes outside the allowed paths, inability to distinguish a completed turn safely, a repeated identical failure after one informed correction, unavailable mandatory credential/host control, or quota exhaustion. Runner UI unavailability is recorded as a limitation and does not authorize an off-grid scheduler.

## Current checkpoint status — 2026-09-11

Checkpoint 1 ingestion is implemented and locally tested at four-file candidate
`ab7627820052458d1223c777e6770b4d48292e3aa88820a38943e00c27f205d0`
with production-file SHA-256
`fad00ee2f775b843e186d24562560843ec683d133ba863672560e5cf2e320679`.
The coordinator added the missing modern string guard and public 18-case type
matrix: 38 focused and 307 capture/status tests passed. The existing Astra audit
reviewer ran 66 focused tests and returned scoped `PASS` for the recurring
malformed-event closure and reviewed attribution/provenance callers. This is
not runner feasibility or independent cross-provider shipping approval; the
seven earlier findings remain preserved in cumulative history.

Checkpoint 2's runner-independent delivery/retry, source-ordering and recall
interfaces are implemented and manager-tested at combined candidate
`c60e4826c4a40fc46fc72c2b6d334e6b3bfdb3f4f0d4a50cb8ee2216857f07db`.
The final local gate is 446 combined automatic-memory/publisher/capture/status
tests and five external correction reproducers, plus target-Python discovery,
hooks, authority, syntax and diff checks. Final Astra/Kimi reviews of predecessor
`b2cf32ef...` returned concrete findings; accepted findings were reproduced and
corrected test-first and rejected compatibility changes were independently
disposition-checked. Native orchestration and the publisher have scoped Kimi
approval. The final source-unavailable capture safeguard awaits independent
Kimi closure as part of the capture/persistence partition, and two informed
persistence-review packets truncated without a verdict; the review stop rule is
reached and a specific independent review alternative remains required. Final
capture architecture is scoped Astra PASS. Do not treat local test success or
same-family review as independent shipping approval.

A supported Home Codex Automation completed the smallest synthetic probe and is
paused. That proves one bounded native turn only; production session scope, SJM
access, cadence, quota behavior, missed-run recovery and the proposed 15-minute
target remain unverified. The current sandbox still blocks the wider cron
harness at process enumeration. The shared tracker update passed live
Syncthing/ownership checks and arrived on SJM, but live SJM memory transport,
activation-time publication preflight and publication, final persistence/source
closure, code delivery, activation and seven-day acceptance remain outstanding.
No production automatic digestion has been claimed.

## Delivery authorization — 2026-09-13

Keith answered "do it" to the explicit question authorizing the one-time Astra
review exception and committing, pushing, installing and activating this repair
on Home and SJM. For frozen candidate `c60e4826c4a40fc46fc72c2b6d334e6b3bfdb3f4f0d4a50cb8ee2216857f07db`,
the existing scoped Astra PASS is accepted for the remaining capture/persistence
partition only. It is same-family review, not independent approval. Independent
Kimi approvals for publisher (`2026-09-12T07-31-41Z_536193`) and native
orchestration (`2026-09-12T08-05-36Z_9c5aba`) remain valid; persistence NO VERDICT
records and cumulative counters are retained unchanged.

This authorization removes the previous delivery approval blocker; it does not
claim activation or acceptance. The unchanged nine-file implementation fingerprint
was verified again before delivery, retaining the existing 446-test evidence.
Home and SJM were both at `078981f6feb99e8c4bf0e52ca1bc5cd00e794b49` with
identical AGENTS.md and hook hashes; SJM tracked checkout was clean. Preserve
Home's unrelated untracked paths and SJM's historical adaptation stash.
Register native runners only through supported app controls. SJM scheduling is
not exposed by the current Home app tool surface; do not substitute an off-grid
scheduler. Record installed, activated and accepted separately.
