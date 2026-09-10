# K2B Stage 1 acceptance record — 2026-09-10

Status: **activated and operationally verified on both Macs; final acceptance is blocked only by GitHub-origin authentication and Home automation registration**

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
