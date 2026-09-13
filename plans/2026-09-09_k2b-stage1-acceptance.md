# K2B Stage 1 acceptance record — 2026-09-10

Status: **the 2026-09-10 manual-capture baseline is activated on both Macs; the 2026-09-12 automatic-memory extension is implemented and synthetically tested but is not committed, pushed, activated, or accepted**

Scope is the Home MacBook Pro and SJM MacBook Pro only. The retired Mac Mini was not contacted. Stage 2 local-model work was not started.

Keith authorized review, commit, push, two-Mac activation, scoped SJM source transport, interactive backlog recovery, quarantine of the two identified Home LaunchAgents, and Obsidian credential rotation. Those configuration actions were completed during the earlier repair continuation. No Git history rewrite, excluded-host access, unattended Kimi extraction, or paid OpenAI fallback is authorized.

## Selected operating model

- Home (`keithmbpm2`) is the only writer to the synchronized K2B vault.
- SJM (`keithcheung`) discovers and exports selected immutable Codex sources, reads the synchronized vault, and keeps machine-local queues/receipts under `~/.local/state/k2b/`.
- Git carries reviewed project code. Syncthing carries vault content. Credentials and local state remain per-machine.
- The only permitted unattended SJM job is `scripts/codex-discovery-job.sh sjm-source-only`.
- Capture extraction/review and reconciliation are interactive. Kimi Code membership is not used for scripted batch work.
- Claude, Telegram, the Mac Mini, observer, weave, batch YouTube, LinkedIn, and other legacy background lanes are not active Stage 1 runtimes.

## Current evidence

- Baseline before the candidate: `8de89d093791eb237a128ddfdabbb71469f17c8f` on both authorized checkouts.
- Reviewed implementation commits: `59ab8a8fdd9d478f1b63323d0ce84f841e32db1d` (`feat: complete K2B Stage 1 two-Mac repair`) and `4db14d596da4b3b1f04025173af9f9ef12f7f45b` (`fix: select Python 3.12 for discovery jobs`). Home and SJM `main` are both activated at `4db14d5`.
- The branch was delivered to the Home Git remote over the existing authorized SSH path. Pushes to the HTTPS GitHub origin failed on both Macs because no non-interactive GitHub credential is available; `gh` is not installed. No credential or SSH-host configuration was changed.
- Post-canary discovery is truthful and failure-free: Home reports **64 discovered / 63 waiting / 1 reconciled / 0 failed**; SJM reports **13 discovered / 12 waiting / 1 reconciled / 0 failed**. The machine-local receipts are under each host's `~/.local/state/k2b/capture-discovery-receipts/` and the latest status is `~/.local/state/k2b/capture-status.json`.
- Final broad Python regression after the residual fixes: **804 passed, 5 skipped, 5 subtests passed**. The lower pass count versus earlier intermediate runs reflects deliberate removal of obsolete router-watchdog, observer-marking, and local-embedding tests rather than skipped failures.
- All **49** remaining shell integration suites passed, including capture, authority, hooks, inventory, provider, credentials, two-Mac sync/ship, dormant lanes, media, YouTube, weave, orchestrator, loop, and date-handling coverage.
- All **26** live K2B skills passed the Codex skill validator.
- Dashboard intake tests passed (**4/4**); TypeScript typecheck, production build, and `npm audit --omit=dev` also passed with zero reported vulnerabilities.
- `scripts/verify-codex-authority.sh`, `git diff --check`, and the staged/unstaged diff checks passed on the candidate cohort.
- Home Obsidian Local REST API credential was rotated and verified without printing it; the old credential returned 401 and the new credential returned 200. The shared launcher uses a per-machine private credential file.
- `com.k2b-remote.app` and `com.k2b.forge-audit-mover` were unloaded on Home and their plist evidence preserved privately. Personal application history was untouched.
- Home's untracked retired `k2b-remote/` runtime residue was moved, not deleted, to `/Users/keithmbpm2/.local/state/k2b/retired-runtime/20260910-stage1/k2b-remote`; the quarantine parent is private and the retained `.env` is mode `0600`.
- SJM's former local `AGENTS.md` and `.codex/hooks.json` adaptations remain recoverable in named stash `pre-stage1-sjm-adaptations-2026-09-10`. The incoming portable SJM policy supersedes them; the stash was not applied or dropped. Sparse checkout was disabled so the active `.mcp.json` authority surface is materialized.
- Tracked Telegram/Claude runtime, router-watchdog/Mini launchd residue, legacy observer writers, and the prior local sentence-transformer preflight/index/retrieval lane were removed. Historical plans and DEVLOG evidence remain historical and are not scanned as live authority.

## Live activation canaries

- Authority guards passed on Home and SJM after activation.
- Harmless authenticated Kimi worker probes passed on Home and SJM. Kimi remains available for requested worker tasks; no unattended Kimi extraction was enabled.
- Syncthing reached `idle` with zero needed items and bytes on both sides before the Home-only canary writes.
- Home reconciled one immutable Home source and one redacted, hash-bound SJM source bundle. Source-host receipts were created for both. A second completed SJM source was then reconciled to prove semantic recall in the opposite direction.
- SJM recalled the Home-derived `decision:k2b-stage1:two-mac-recall-core` row with `/Users/keithmbpm2/...` provenance. Home recalled the SJM-derived `fact:stage1-credential-hardening:negative-path-tests-pass` row with `/Users/keithcheung/...` provenance. Both rows subsequently appeared on SJM through Syncthing with an exact count of one.
- Replaying Home reconciliation for both dates produced zero new semantic writes and kept both dedupe-key counts at exactly one.
- The SJM Codex automation `k2b-discovery-sjm` is active daily at **23:30 HKT**, runs only `scripts/codex-discovery-job.sh sjm-source-only`, and notifies only on failed runs. Direct creation of the equivalent Home automation from SJM was rejected by the remote-project automation surface; the Home Codex task channel was also unavailable. No ad-hoc `crontab`, launchd, PM2, or cross-host workaround was installed.

## Review record and exception

Kimi remains the required independent reviewer for OpenAI-built K2B changes. Multiple bounded Kimi attempts failed to produce a usable final gate because of timeout, truncated output, or empty output. The relevant logs include:

- `.code-reviews/2026-09-10T06-10-28Z_75f332.log`
- `.code-reviews/2026-09-10T06-28-20Z_537b64.log`
- `.code-reviews/2026-09-10T06-47-44Z_00d886.log`
- `.code-reviews/2026-09-10T06-54-33Z_d03621.log`
- `.code-reviews/2026-09-10T07-13-35Z_5ecf28.log`

All substantive Kimi findings that were returned were fixed and regression-tested. Keith explicitly allowed a GPT model only when Kimi could not deliver. GPT-5.6 Sol emergency reviews approved the capture, provider, dashboard, and operational cohorts after fixes. Its first stable-cohort pass found two high-severity blockers: a residual Codex-review dependency on a Claude-managed plugin, and incomplete short/semantic credential redaction in the history exporter. A follow-up caught quoted JSON keys and prefixed secret flags. All were fixed with focused regression coverage; the inventory sanitizer was hardened for the same header forms. The final repaired-cohort Sol pass returned `APPROVE` with no blocker/high regression. These are same-family reviews and are recorded as a user-authorized emergency quality gate, **not independent review**.

## Acceptance checks

| Check | Current state | Evidence still required |
|---|---|---|
| A — one source from each Mac reaches counterpart recall | **Passed live** | Reciprocal semantic rows and source-host receipts with opposite-account provenance |
| B — replay/failure truthfulness | **Passed live and in regression** | Replay produced zero new writes; malformed/provider/offline/interruption paths are regression-covered |
| C — backlog remains source/receipt truthful | **Passed live** | Home 64/63/1/0 and SJM 13/12/1/0 discovered/waiting/reconciled/failed |
| D — Kimi worker/reviewer availability | **Worker passed; independent final review exception** | Both worker probes passed; Kimi final gates failed; user-authorized Sol approval is same-family, not independent |
| E — retired runtime is absent from live authority | **Passed live** | Authority guards passed after activation; retired Home residue is private quarantine only |
| F — Home-only vault writer and two-Mac sync | **Passed live** | Home-only reconciliation, reciprocal recall, Syncthing idle/zero-needed |
| G — truthful docs/tests/status | **Operationally passed; delivery-control exceptions open** | Both Macs activated and receipts truthful; GitHub origin push and Home schedule registration remain blocked |

## Remaining external-control actions

1. Restore a supported non-interactive GitHub credential on either authorized Mac, then push `main` to `origin`. The reviewed commits and both active checkouts are already preserved locally and on the Home SSH remote.
2. Bring the Home Codex host control surface online and register/read back the matching daily 23:30 HKT Home discovery automation. Until then, Home discovery remains available as the verified deterministic manual entrypoint.

No Stage 2 local-model work is included. These two control-plane blockers do not invalidate the live capture, Kimi, recall, Syncthing, authority, or replay canaries, but they prevent claiming completely closed Stage 1 acceptance.

## Automatic-memory extension rollout preparation — 2026-09-12

This section preserves the historical baseline evidence above while tracking the
newly approved automatic-memory repair as a distinct delivery state.

| State | Automatic-memory extension evidence |
|---|---|
| Proposed/approved | Approved Stage 1 plan in `2026-09-11_k2b-stage1-automatic-memory-repair.md` |
| Implemented | Source-verifying adapter; durable local queue, acceptance, reconciliation, recall and status; Home-only injected publication boundary |
| Tested | Synthetic public flow, twenty seeded items, offline retry, crash/replay, correction ordering, provisional/current recall and failed acceptance |
| Architecture reviewed | Checkpoint-1 ingestion, deterministic memory foundation, publisher, native orchestration and final capture candidate have scoped Astra PASS verdicts |
| Independently reviewed | Publisher and native orchestration have scoped Kimi APPROVE; capture/persistence review is blocked after two truncated no-verdict packets |
| Committed/pushed | **No** — no delivery authority has been exercised for this extension |
| Activated Home/SJM | **No** — existing manual/discovery behavior remains unchanged |
| Accepted | **No** — live two-host proof and the seven-day unattended trial have not run |

### Replacement proof: retain before removal

| Existing mechanism | Replacement evidence now available | Removal decision |
|---|---|---|
| Completed-turn capture parser and source-bundle validation | Reused directly by the automatic adapter; local/transported attribution and malformed-input suites pass | Retain as the single parser boundary |
| Machine-local discovery and status | Automatic-memory status is additive; native runner feasibility is still under test | Retain unchanged |
| Manual `stage-reviewed` and semantic shelf recall | New durable recall works synthetically, but real Home publication and fresh-task parity have not passed | Retain until post-trial parity |
| Legacy interactive extraction paths | No supported production automatic runner has yet completed the bounded trial | Keep dormant; do not schedule or delete |
| Manual redacted SJM source transfer | Durable outbox/acceptance is tested with an injected boundary, not live two-host transport | Retain until live delivery/retry proof |
| On-demand research, media, NotebookLM and email capabilities | Outside the replacement target | Retain |

No redundant mechanism qualifies for deletion at this checkpoint.

### Exact activation sequence after review and delivery authority

1. Freeze and independently review the final cohort: bounded Astra integration
   verdict, then the required Kimi adversarial shipping verdict. Correct concrete
   findings test-first and rerun only invalidated gates.
2. Request separate authority for commit/push and for activation. A reviewed
   worktree is not an activation obligation.
3. Before any Home shared-memory publication, verify the live K2B Syncthing
   folder is idle with zero needed files/bytes and no errors, confirm Home owns
   the write, and confirm no concurrent editor has the target note open. Obtain
   the explicit publication authority and update the activated worker prompt;
   the current reviewed native prompt intentionally stops after durable Home
   reconciliation. Publish only through the designated Home writer and verify
   counterpart arrival.
4. Activate Home first with production defaults still disabled. Run one
   synthetic queue/accept/reconcile/recall canary and verify immutable receipts,
   cited current recall and duplicate-free replay.
5. Preserve SJM's local `AGENTS.md`, hooks and private state adaptations; update
   its reviewed Git checkout deliberately. Verify SJM cannot write the shared
   vault, can retain provisional recall while Home is offline, and consumes the
   Home-published redacted state after Syncthing convergence. Before authorizing
   unattended SJM extraction, reconcile the current discover-only `AGENTS.md`
   policy as an explicit reviewed policy change.
6. Register supported native runners on each required host only after a
   per-host synthetic Automation probe establishes host/worktree access,
   observed cadence, quota reporting, missed-run behavior and durable receipts.
   The SJM runner may only build its local worklist, record native extraction
   results and retain the resulting machine-local outbox; it remains read-only
   for the shared vault. The Home runner drains Home work plus that SJM outbox
   through the established pull/ack boundary before Home reconciliation. Do not
   substitute cron, launchd or a hidden app. Start with bounded eligible sources;
   broad backlog ingestion remains disabled.
7. Seed twenty reviewed facts/decisions/preferences/commitments through the live
   path. In fresh tasks on both Macs, verify the exact current values, citations
   and superseded history, plus late-message preservation, crash/replay,
   old-SJM/new-Home ordering and offline recovery.

### Seven-day unattended acceptance trial

The trial starts only after authorized activation and a passing live canary. For
seven consecutive days, retain immutable runner and reconciliation receipts and
record:

- scheduled versus actual start time and observed cadence;
- eligible completed sources, exclusions and completed-prefix cursors;
- extraction, quota, retry, exhaustion and malformed-output outcomes;
- SJM offline queue age and successful Home drain after reconnection;
- Home shared-state publication hash and confirmed SJM arrival;
- fresh-task recall results for seeded current, superseded and ambiguous items;
- any ordinary memory that incorrectly requires Keith review.

Acceptance requires all twenty seeds to remain correctly cited on both Macs,
late turns to survive, replay to stay duplicate-free, older offline corrections
not to override newer source evidence, and no silent lost/false-success state.
Any genuine ambiguity, exhausted quota or persistent failure must remain an
explicit exception. The proposed 15-minute recall target is accepted only if the
observed runner-to-SJM arrival distribution supports it; registration or a
configured interval is not evidence of achieved cadence.

If a trial invariant fails, pause the native runner, preserve queues and
receipts, keep the existing manual paths available, and return to the reviewed
candidate. Do not delete durable state or silently fall back to a paid provider.
