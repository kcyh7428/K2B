# K2B Stage 1 acceptance record — 2026-09-10

Status: **reviewed candidate; not yet committed, pushed, activated, or accepted**

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
- Historical Home discovery snapshot: 63 eligible sources, 63 waiting, zero falsely reconciled. This is evidence from the repair run, not a live post-activation count.
- Final broad Python regression after the residual fixes: **804 passed, 5 skipped, 5 subtests passed**. The lower pass count versus earlier intermediate runs reflects deliberate removal of obsolete router-watchdog, observer-marking, and local-embedding tests rather than skipped failures.
- All **49** remaining shell integration suites passed, including capture, authority, hooks, inventory, provider, credentials, two-Mac sync/ship, dormant lanes, media, YouTube, weave, orchestrator, loop, and date-handling coverage.
- All **26** live K2B skills passed the Codex skill validator.
- Dashboard intake tests passed (**4/4**); TypeScript typecheck, production build, and `npm audit --omit=dev` also passed with zero reported vulnerabilities.
- `scripts/verify-codex-authority.sh`, `git diff --check`, and the staged/unstaged diff checks passed on the candidate cohort.
- Home Obsidian Local REST API credential was rotated and verified without printing it; the old credential returned 401 and the new credential returned 200. The shared launcher uses a per-machine private credential file.
- `com.k2b-remote.app` and `com.k2b.forge-audit-mover` were unloaded on Home and their plist evidence preserved privately. Personal application history was untouched.
- Tracked Telegram/Claude runtime, router-watchdog/Mini launchd residue, legacy observer writers, and the prior local sentence-transformer preflight/index/retrieval lane were removed. Historical plans and DEVLOG evidence remain historical and are not scanned as live authority.

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
| A — one source from each Mac reaches counterpart recall | Implemented and fixture-tested | Live post-activation Home/SJM canary and counterpart recall |
| B — replay/failure truthfulness | Tested | Live post-activation canary |
| C — backlog remains source/receipt truthful | Implemented; historical 63 waiting snapshot | Live post-activation status and named unresolved exceptions |
| D — Kimi worker/reviewer availability | Worker probes succeeded on both; Kimi final review unreliable | Harmless post-activation worker probes; Sol exception recorded |
| E — retired runtime is absent from live authority | Implemented and tested | Post-activation authority/inventory canary |
| F — Home-only vault writer and two-Mac sync | Implemented; SJM guards added | Healthy Syncthing plus live scoped-source canary |
| G — truthful docs/tests/status | Full regression clean | Reviewed commit, push, activation, and live receipts |

## Delivery sequence still outstanding

1. Commit and push the reviewed cohort.
2. Activate Home and SJM deliberately, preserving and comparing SJM's existing `AGENTS.md` and `.codex/hooks.json` adaptations before resolving any divergence.
3. Run discovery/Kimi/scoped-source/Syncthing/recall canaries and update this record to `accepted` only if they pass.
