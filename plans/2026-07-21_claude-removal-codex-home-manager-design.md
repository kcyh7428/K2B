# K2B Claude Removal, Codex Operations, and Home Manager Design

Status: approved design, implementation not started
Date: 2026-07-21
Owner: Keith
Design author: Codex
Review disposition: Keith manually reviewed and approved on 2026-07-21; Kimi review job `2026-07-21T06-21-22Z_c649c0` returned no verdict after four network connection closures, and Keith explicitly waived a retry
Supersedes: the Claude compatibility and Telegram provider portions of `plans/2026-06-14_codex-primary-migration-spec.md`

## Decision

K2B will remove Claude Code and Anthropic completely from live operation.

The target provider boundary is:

- Codex is the primary interactive commander, coding environment, orchestration surface, instruction loader, skill runtime, and desktop automation surface.
- Kimi K2.7 remains the background text worker for weave, compile, observer, research extraction, lint-deep work, and independent review of OpenAI-built changes.
- GPTsAPI, Groq, and Higgsfield remain specialist media, OCR, speech, transcription, image, video, and music providers.
- Deterministic scripts remain preferred where reasoning is unnecessary.
- Claude Code, Anthropic SDKs, Anthropic authentication, and Claude-specific runtime files are removed from every live K2B path.

This is Claude and Anthropic removal, not an all-providers-to-OpenAI migration.

## Cost Constraint

Keith's OpenAI subscription is the allowed OpenAI execution route. K2B must not create usage-based OpenAI API charges.

Hard rules:

1. No production K2B service may require `OPENAI_API_KEY` or another OpenAI API credential.
2. No production component may silently fall back from ChatGPT-authenticated Codex to an OpenAI API key.
3. The OpenAI Agents SDK is not part of the target runtime. Telegram is being retired, so its proposed agent-provider replacement is no longer needed.
4. Background reasoning on the always-on Mac Mini uses Kimi or an explicitly deterministic implementation unless a separately approved, subscription-authenticated Codex route is proven durable and zero-spend.
5. A zero-spend burn-in and billing check block final decommission completion.

The design does not assume that an unattended Mac Mini process is covered merely because interactive Codex on the MacBook uses a subscription. Mini authentication, reauthentication, and account-policy behavior must be proven before any subscription-authenticated Codex job is treated as durable infrastructure.

## User Experience

Keith works with K2B from the MacBook through Codex. Telegram is no longer an input, output, reminder, or alert channel.

The Mac Mini remains always on and provides:

- background K2B routines;
- the synchronized vault replica;
- the Operations Console backend;
- Home Manager services;
- NAS, router, network, camera, and future IoT observation;
- durable job evidence and history.

The Operations Console is the visual aid for understanding what is working, what is waiting, and what failed. It extends the existing K2B dashboard rather than introducing another disconnected control plane.

The first release is read-only. It may show status, evidence, alerts, and safe copyable recovery instructions. Device control and job mutation require later, explicitly designed authorization gates.

## As-Is Audit

The July 21 audit found Claude dependencies beyond `k2b-remote`.

| Surface | Current behavior | Target disposition |
|---|---|---|
| Root instructions | `CLAUDE.md` remains a live compatibility prompt | Move all live authority to `AGENTS.md`; archive or remove `CLAUDE.md` after parity proof |
| Nested instructions | `k2b-remote/CLAUDE.md` injects channel behavior | Replace retained service context with `AGENTS.md` or service-owned configuration; remove the nested Claude file |
| Skill topology | `.claude/skills` mirrors `.agents/skills` and is enforced by parity tests | Make `.agents/skills` canonical; replace parity with Codex-only integrity checks |
| Codex skills | Eighteen active `.agents` skills still contain Claude, `.claude`, or Claude Desktop assumptions | Rewrite behavior intentionally; do not perform a blind text replacement |
| Project Claude hooks | Session start, skill tracking, observer capture, and zombie cleanup are registered in Claude settings | Rebuild supported behavior in Codex hooks or the Mini job runner |
| Global Claude hooks | YouTube transcript prefetch and Superset notifications use Claude hook events | Port required behavior to Codex hook payloads; retire duplicate notifications |
| Async cleanup | Claude runs zombie cleanup as an asynchronous Stop hook | Move cleanup to a synchronous bounded hook or a separate launchd/Mini maintenance job because Codex skips asynchronous command hooks |
| Claude launch actions | Five desktop launch configurations exist, including K2B dashboard actions | Recreate only useful actions as Codex desktop actions or explicit scripts; retire unrelated or unused actions |
| Claude native schedules | The discovered active scheduled-task file contains zero tasks | No active task to migrate from that store |
| Schedule backups | Daily inbox, Friday self-improvement, weekly external research, and weekly vault health definitions exist | Reassess each, then register useful routines in the observable Mini job platform |
| Codex automations | AWS/router monitoring and Forge Audit are active | Keep or relocate by execution needs; rewrite Forge Audit so Claude transcripts are historical input only |
| Agent runtime | Dashboard HTTP intake and the vault-drop watcher eventually call `@anthropic-ai/claude-agent-sdk` | Extract retained intake services before deleting the Telegram agent process |
| Telegram runtime | Bot, scheduler, outbox, startup alerts, and chat sessions are bundled with useful services | Retire Telegram features and their state after extraction and archive |
| Memory readers | Observer, EOD capture, Forge Audit, and other scripts scan `~/.claude` | Import required history once, then read Codex sessions and canonical vault memory only |
| Deployment | Mini deployment copies Claude instructions and skills and restarts `k2b-remote` | Deploy Codex instructions, the new core service, dashboard, job runner, and Home Manager |
| MCPs and plugins | Several required integrations are configured only or differently in Claude | Port required capabilities to Codex MCPs, plugins, apps, or local wrappers; retire unused integrations |
| Credentials | Claude-era MCP configuration contains inline credentials | Rotate affected credentials and store replacements through the approved environment or credential mechanism |
| Review routing | Anthropic remains modeled as a live builder family | Remove Anthropic as a K2B builder route; preserve historical review records without keeping a live Claude path |
| Router monitoring | Health scoring includes Claude endpoints | Replace with OpenAI/Codex-relevant checks or remove if the check does not prove an operational dependency |
| MacBook LaunchAgent | `com.k2b-remote.app` points at a missing path and has repeatedly failed with `EX_CONFIG` | Unload and remove during controlled cutover |
| Claude application state | Global settings, plugins, auth, histories, and desktop MCP state remain | Export needed history, revoke credentials, then remove live Claude application state with explicit destructive-action approval |

The Mini was unreachable by SSH during the audit. Its PM2, launchd, cron, Claude authentication, and filesystem state are unverified and form a mandatory Gate 0 inspection.

## Target Architecture

### 1. MacBook Command Plane

The MacBook is the normal interactive K2B surface.

It contains:

- Codex desktop and CLI using ChatGPT sign-in;
- root `AGENTS.md` and nested `AGENTS.md` files where execution context genuinely differs;
- canonical `.agents/skills`;
- `.codex/hooks.json` for supported project hooks;
- useful Codex desktop actions;
- optional MacBook-local scheduled tasks where the MacBook and Codex app being online is an acceptable dependency.

MacBook jobs still report their receipts to the shared Operations Console. Execution location must not make work invisible.

### 2. Mac Mini Operations Plane

The Mac Mini is the always-on operational host.

It contains:

- the Syncthing-managed K2B vault replica at `/Users/fastshower/Projects/K2B-Vault`;
- the deployed K2B project without relying on Git history;
- a Claude-free K2B core service;
- the registered background job runner;
- Kimi worker routing and deterministic scripts;
- the Operations Console server;
- Home Manager;
- health, event, and evidence stores.

The Mini must not depend on Telegram, Claude Code, Anthropic authentication, or an OpenAI API key.

### 3. Specialist Worker Plane

Provider use is explicit and observable:

| Work type | Provider |
|---|---|
| Interactive command, architecture, coding, orchestration | Codex through ChatGPT sign-in |
| Background text analysis, weave, compile, observer, research extraction | Kimi K2.7 |
| Independent review of OpenAI-built changes | Kimi K2.7, no same-family fallback |
| Review of Kimi-built changes | Codex |
| OCR, image, speech, transcription | GPTsAPI or Groq according to live routing authority |
| Premium image, image editing, video, music | Higgsfield |
| Mechanical checks and maintenance | Deterministic scripts |

Provider identity is a required field in every reasoning or media job receipt.

### 4. Home Manager Plane

Home Manager is a separate service boundary on the Mini. It owns home-device connectors and credentials.

Initial nodes are:

- Synology NAS;
- Mac Mini;
- MacBook presence and synchronization state;
- router and Mihomo routing state;
- future home camera;
- future IoT controllers and appliances.

K2B background jobs may read approved Home Manager status. Direct device mutation is not included in the first release. A later control plane must provide explicit allowlists, user confirmation for material actions, audit logs, bounded credentials, and emergency disable controls.

## Claude-Free K2B Core Service

`k2b-remote` currently combines unrelated responsibilities. Migration must split the retained capabilities before deleting the process.

Retain and move into a provider-neutral core service:

- dashboard HTTP intake;
- vault-drop intake watcher;
- manifest validation and idempotent processed/error handling;
- audio transcription using the retained voice provider;
- attachment extraction;
- memory synchronization and decay where still required;
- health heartbeat;
- structured intake receipts.

Retire:

- Grammy Telegram bot;
- Telegram long polling;
- Telegram startup and alert messages;
- Telegram outbox;
- Telegram scheduler and schedule CLI;
- Telegram chat and thread session state;
- Anthropic Agent SDK;
- Claude system-prompt injection;
- Claude session resume and error handling.

Dashboard intake must not automatically turn every capture into an autonomous agent action. The target contract separates capture from processing:

1. Validate and store the capture.
2. Emit a durable intake receipt.
3. Route deterministic extraction where applicable.
4. Queue optional Kimi processing through a registered job.
5. Surface the result and evidence in the Operations Console.

This makes capture reliable even when a reasoning provider is unavailable.

## Observable Job Contract

Every scheduled or background routine must register before it can be treated as production.

Required job metadata:

- stable job ID and display name;
- category and owner;
- executor host;
- execution engine and provider;
- schedule or trigger;
- enabled state;
- timeout and retry policy;
- expected evidence and artifact locations;
- freshness threshold;
- runbook or safe recovery instruction.

Required run events:

- registered;
- started;
- succeeded;
- failed;
- skipped;
- timed out;
- disabled;
- stale.

Required run evidence:

- run ID;
- start and end times;
- duration;
- concise summary;
- sanitized error;
- artifact paths;
- last successful run;
- next expected run;
- host and provider;
- retry count.

Console states are healthy, running, waiting, degraded, failed, stale, and disabled. Silence is never interpreted as success.

## Operations Console

The existing K2B dashboard becomes the console rather than adding a second dashboard.

First-release views:

1. Overview: Mini, MacBook, vault, NAS, router, Home Manager, and provider status.
2. Jobs: all registered routines with state, last success, duration, next trigger, and executor.
3. Timeline: chronological job and infrastructure events.
4. Evidence: sanitized logs and artifact links for a selected run.
5. Attention: failed, stale, degraded, or approval-required items.
6. Home: read-only NAS, router, camera, and IoT node status.

The first release has no arbitrary shell execution and no general start, stop, retry, edit-schedule, or device-control endpoints. Safe copyable commands or runbook instructions are allowed.

## Routine Placement

Default placement rules:

- Jobs requiring always-on execution, vault-local access, NAS access, or home-network observation run on the Mini.
- Jobs requiring MacBook-only credentials or an interactive Codex subscription session may remain on the MacBook temporarily.
- The Console aggregates both hosts.
- A job cannot exist in both schedulers unless one is explicitly a disabled fallback.

Initial routine mapping:

| Routine | Target |
|---|---|
| Weave | Mini, Kimi, registered job |
| Compile and lint maintenance | Mini, deterministic plus Kimi where needed |
| Observer | Mini, canonical vault signals plus Kimi |
| Daily inbox check | Mini, deterministic |
| Weekly vault health | Mini, deterministic plus Kimi summary |
| Friday self-improvement | Mini, canonical vault memory plus Kimi |
| Weekly external research | Mini, Kimi and retained search providers |
| Forge Audit | Prefer Mini after Codex-session export contract exists; otherwise MacBook with Claude paths removed |
| AWS and router monitor | Keep MacBook initially if credentials require it; report into Console; migrate only after Mini credential and network proof |
| NAS health and backup checks | Mini, deterministic |
| Media jobs | Mini or MacBook according to credential availability, always registered |

## Instruction, Hook, and Skill Migration

### Instructions

- `AGENTS.md` is the sole top-level live authority.
- Nested `AGENTS.md` exists only for a real execution-context boundary.
- `CLAUDE.md` is removed from deployment and runtime discovery.
- Historical documents may mention Claude when describing history, but cannot be required input.

### Hooks

- Session startup continues to surface active rules, queues, job health, vault status, and pending deployment state.
- Skill tracking uses the verified Codex hook payload.
- Stop observation uses Codex session evidence and canonical vault memory.
- YouTube prefetch is ported to Codex UserPromptSubmit only if it remains useful in the MacBook workflow.
- Zombie cleanup becomes a bounded maintenance job if synchronous Stop execution is unsuitable.
- Duplicate Superset notifications are removed.

### Skills

- `.agents/skills` is the only deployed skill directory.
- The old mirror verifier becomes a Codex skill-integrity verifier.
- Each of the eighteen identified skills receives a behavior-level review for Claude paths, Telegram instructions, scheduling assumptions, rendering differences, review routing, and Mini execution context.
- Dormant capture and publishing skills remain dormant unless Keith explicitly reactivates them.

## Integration and Credential Migration

Each Claude MCP or plugin is classified as port, replace, or retire.

Candidates requiring explicit parity decisions include:

- Exa;
- Perplexity;
- YouTube transcript;
- Obsidian;
- Hostinger;
- Fireflies;
- Pipedrive;
- Brave Search;
- filesystem and computer-use functions;
- Claude browser and preview functions;
- scheduled-task functions.

Preference order is:

1. Existing Codex capability or installed app/plugin.
2. Existing provider-neutral local wrapper.
3. Codex MCP configuration.
4. Retirement when the capability has no confirmed K2B consumer.

Credentials exposed inline in Claude-era configuration are considered compromised for migration purposes. Values must never be copied into the design, logs, commits, or Console. Rotate the credential, update the approved store, verify the new consumer, and revoke the old value.

## History and Memory Migration

Complete Claude removal does not require deleting useful history.

The migration performs a one-time historical closeout:

1. Freeze live Claude writes.
2. Export K2B-relevant Claude conversations, plans, and decisions into an immutable archive.
3. Import only canonical facts or decisions that still belong in the vault.
4. Record provenance and export date.
5. Update EOD, observer, Forge Audit, session-start, and memory tools to ignore live `~/.claude` paths.
6. Preserve the archive as history, not as a runtime database.

Deletion of user-wide Claude application state is a separate destructive closeout action and requires explicit target confirmation after the archive is verified.

## Migration Sequence

### Gate 0: Live Inventory and Security Freeze

- Reach the Mini and inventory PM2, launchd, cron, Claude, Codex, project, vault, and credentials without printing secrets.
- Inventory MacBook and Mini job definitions and remove duplicate assumptions from the plan.
- Rotate exposed Claude-era MCP credentials.
- Capture baseline behavior for intake, vault sync, dashboard, weave, observer, compile, review, media, NAS, and router monitoring.
- Confirm the allowed Codex authentication route and absence of OpenAI API-key fallback.

Stop if the Mini cannot be reached, provider billing behavior is ambiguous, or a live job has no identified owner.

### Ship 1: Codex-Only Authority

- Complete `AGENTS.md` authority.
- Rewrite `.agents/skills` and integrity checks.
- Port hooks.
- Remove Claude deployment parity while retaining rollback files locally.
- Update active review and ship instructions.

Rollback: restore the previous instruction deployment. Do not remove Claude yet.

### Ship 2: Core Service Extraction

- Build the Claude-free intake and health service.
- Preserve dashboard and vault-drop capture behavior.
- Decouple capture from reasoning.
- Add structured intake receipts.

Rollback: run the old process without exposing Telegram externally if a retained intake parity gate fails.

### Ship 3: Job Registry and Operations Console

- Implement the job contract and event store.
- Instrument existing jobs before relocating them.
- Add Overview, Jobs, Timeline, Evidence, and Attention views.
- Show executor host and provider.

Rollback: keep existing schedulers running while the Console remains observation-only.

### Ship 4: Routine Migration

- Move approved routines to the Mini job runner.
- Rewrite Forge Audit and memory consumers.
- Migrate or retire the four Claude schedule backups.
- Eliminate Telegram reminders and alerts.
- Prove no duplicate schedules.

Rollback: re-enable the prior non-Claude scheduler entry for one named job only.

### Ship 5: Home Manager and NAS

- Establish a scoped Mini-to-NAS identity.
- Add NAS, router, Syncthing, storage, and backup health.
- Add the read-only Home view.
- Define camera and IoT connector contracts without enabling general device control.

Rollback: disable individual connectors without affecting K2B jobs or the vault.

### Ship 6: Claude and Telegram Decommission

- Stop and remove Telegram services.
- Remove Anthropic dependencies and configuration.
- Stop live Claude memory readers.
- Unload stale LaunchAgents.
- Remove Claude instructions, skills, hooks, launch actions, router checks, and deployment paths.
- Archive required history.
- Revoke Claude-era credentials.

Rollback: limited to the retained pre-cutover archive and tagged deployment artifact. No silent production fallback to Claude is allowed.

### Ship 7: Burn-In and Final Removal

- Run the Mini and MacBook without Claude or Telegram through the agreed observation window.
- Confirm routine freshness, Console accuracy, vault sync, NAS visibility, review independence, and provider routing.
- Confirm zero OpenAI API usage and no API-key fallback.
- Remove remaining Claude application state only after explicit approval.
- Declare the old 2026-06-14 Telegram provider bridge superseded.

## Verification Gates

### Functional

- Dashboard text, URL, feedback, file, and audio intake reach a durable receipt without Telegram.
- Weave, compile, observer, review, Forge Audit, vault health, and research run through their declared provider paths.
- MacBook and Mini vault replicas remain synchronized.
- NAS and router state appear in the Console.
- Every registered job has current evidence or is honestly stale/disabled.

### Claude Removal

- No production dependency imports an Anthropic package.
- No production process, PM2 entry, launchd job, cron entry, hook, desktop action, or scheduler invokes Claude.
- No deployed instruction or skill requires `CLAUDE.md`, `.claude`, `CLAUDE_PROJECT_DIR`, or Claude session state.
- No active memory scanner treats `~/.claude` as a live source.
- No active MCP or plugin depends on Claude Desktop.
- Router and provider health checks contain no required Claude target.
- Mini and MacBook both pass the audit.

Historical plans, exports, review records, and archives may retain accurate references to Claude. They must be excluded from live-dependency scanners through an explicit archive allowlist, not by weakening the scanner globally.

### Cost

- Production environment allowlists exclude OpenAI API credentials.
- Tests prove no API-key fallback.
- Codex subscription login remains the only allowed OpenAI execution path.
- Billing evidence across the burn-in window shows zero OpenAI API spend attributable to K2B.

### Security

- Rotated Claude-era credentials are revoked.
- Home Manager credentials are unavailable to general K2B jobs.
- Console logs and evidence are sanitized.
- No first-release endpoint performs general shell execution, scheduler mutation, or home-device control.

## Definition of Done

The migration is complete only when:

1. Keith can operate K2B from Codex on the MacBook without Claude or Telegram.
2. The Mini continues all approved background routines and exposes honest status in the Operations Console.
3. The Mini retains a healthy synchronized vault replica.
4. NAS, router, and Home Manager nodes are visible.
5. All retained specialist providers work through explicit routing.
6. No live Claude, Anthropic, Telegram, or OpenAI API-billed dependency remains.
7. Independent review rules remain intact.
8. The zero-spend burn-in passes.
9. Historical Claude material is archived and no longer live state.
10. The final MacBook and Mini audits both pass with evidence.

## Non-Goals

- Replacing Kimi, GPTsAPI, Groq, or Higgsfield.
- Restoring Telegram through another provider.
- Making the first Operations Console release a general command console.
- Giving K2B background jobs unrestricted home-device credentials.
- Moving K2Bi trading or capital controls into Home Manager.
- Deleting historical records solely because they mention Claude.
- Treating a successful MacBook cutover as proof that the Mini is clean.

## Implementation Planning Rule

The implementation plan must use bounded ships with binary gates and rollback points. It must begin with the live Mini audit and credential rotation, not code replacement. It must identify every file, service, job, credential class, test, and deployment transition for each ship. No ship may combine Claude decommission with an unproven replacement for a load-bearing behavior.
