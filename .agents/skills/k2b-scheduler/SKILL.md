---
name: k2b-scheduler
description: Create, list, pause, resume, and delete registered host jobs for K2B. Use when Keith says /schedule, "schedule", "remind me", "set up a recurring", "every week do X", "run this daily", "automate", or wants any task to run on a timer. Handles recurring jobs and one-time reminders with observable receipts.
---

# K2B Scheduler

## Live K2B Authority

- `AGENTS.md` is the instruction authority and `.agents/skills` is the only live skill root.
- Codex is the interactive commander; Kimi K2.7 is the background text worker.
- OpenAI-built diffs use Kimi review with no fallback; Kimi-built diffs use Codex review.
- Background work is disabled unless Keith explicitly authorizes a named job with an observable receipt.
- Capture enters through the dashboard or a vault drop, never Telegram.
- Canonical memory is `K2B-Vault/System/memory`; read Codex sessions only when explicitly required and never read Claude state.

Manage K2B scheduled work as registered host jobs. A valid job has:

- a stable job identifier and explicit owner host;
- a checked-in or otherwise reviewable command entrypoint;
- an HKT schedule or one-time fire time;
- an observable receipt containing start time, finish time, exit status, and artifact paths;
- a machine-local failure/status record that the active Codex session can inspect;
- no interactive agent-session launch and no message-channel delivery dependency.

Do not use the legacy `k2b-remote` SQLite scheduler. Do not encode a chat ID or route results through Telegram.

Two job types:
- **recurring** -- registered with the supported host scheduler and runs on a defined cadence;
- **one-time** -- registered for one HKT datetime and disabled or removed after a successful receipt.

## Host and Registration Rules

1. Identify the intended host from the requested workload and verify it live before registration.
2. On SJM, refuse every unattended K2B job except the exact entrypoint `scripts/codex-discovery-job.sh sjm-source-only`. A request for any other SJM schedule is new scope and requires Keith's explicit authorization before registration.
3. The SJM discovery exception is local and source-only: it must not call a provider, edit the Vault or shared hubs, reconcile captures, or start another routine.
4. Prefer the Codex automation/host-job control surface available in the current environment. If no supported registration surface is available, stop with a precise proposal; do not edit `crontab`, launchd, PM2, or service state ad hoc.
5. Register only a deterministic script or command that can run without an interactive Codex session.
6. Store receipts in the job's declared receipt path and make failures discoverable in machine-local status.
7. After every create, pause, resume, delete, or run-now action, read back the registered job and report the exact state.

## Commands

- `/schedule <frequency> "<command-purpose>"` -- Create a recurring registered host job
- `/schedule once "<datetime>" "<command-purpose>"` -- Create a one-time registered host job
- `/schedule list` -- Show registered K2B jobs
- `/schedule pause <job-id>` -- Pause a job
- `/schedule resume <job-id>` -- Resume a paused job
- `/schedule delete <job-id>` -- Delete a job after confirming the exact target

## Creating a Recurring Task

### Step 1: Parse the request

Extract:
- **purpose**: the intended outcome;
- **entrypoint**: the deterministic K2B command or script;
- **schedule**: parse the frequency in HKT;
- **owner host**: the machine that owns the required files and dependencies;
- **receipt path**: the observable completion artifact;
- **failure route**: the machine-local receipt/status location for failures.

### Frequency Parsing

All times in Keith's LOCAL timezone (HKT, UTC+8).

| Input | Host schedule | Notes |
|-------|----------------|-------|
| `daily` or `daily 9am` | `0 9 * * *` | Default 9am if no time given |
| `weekly` or `weekly monday` | `0 9 * * 1` | Default Monday 9am |
| `weekly wednesday 2pm` | `0 14 * * 3` | Specific day and time |
| `weekdays 8:30am` | `30 8 * * 1-5` | Mon-Fri |
| `monthly` | `0 9 1 * *` | 1st of month, 9am |
| `every 2h` | `0 */2 * * *` | Every 2 hours |
| `friday 4pm` | `0 16 * * 5` | Every Friday |

### Step 2: Validate before registration

- Confirm the entrypoint exists and has a bounded, non-interactive execution contract.
- If the owner is SJM, require the exact entrypoint `scripts/codex-discovery-job.sh sjm-source-only`; refuse every other entrypoint unless Keith has explicitly opened new scope for that schedule.
- Confirm no OpenAI API key or unrelated global credential is inherited.
- Confirm the receipt and machine-local failure/status paths are writable by the owner host.
- Preview the exact job name, host, cadence, entrypoint, and receipt location.

### Step 3: Register and verify

Use the supported host-job control surface, then read the job back. Show: job ID, owner host, human-readable schedule, next run, entrypoint, receipt path, and failure/status path.

## Creating a One-Time Reminder

Use for "remind me", "on [date] do X", or any single-fire task.

### Step 1: Parse the request

Extract the purpose, deterministic entrypoint, owner host, and `YYYY-MM-DD HH:MM` HKT fire time. Register through the supported host-job surface, verify by reading it back, and report its job ID, fire time, receipt path, and post-success disable/delete behavior.

## Listing Tasks

Format output as a table:

```
| ID | Host | Type | Schedule | Status | Next Run | Receipt |
|----|------|------|----------|--------|----------|---------|
```

## Pausing/Resuming/Deleting

Resolve the exact registered job first, perform one bounded action through the supported host-job control surface, then read it back. Deletion requires an exact job ID and explicit user intent.

## Usage Logging

After creating, modifying, or listing tasks, append to the usage log:

```bash
python3 "$HOME/Projects/K2B/scripts/k2b-shared-append.py" usage --skill k2b-scheduler --summary "created/listed/paused/deleted task"
```

## Examples

### One-time reminder
```
/schedule once "Apr 2 6pm" "Bring driving license for Shanghai car rental pickup"
```

### Home weekly research receipt
```
/schedule weekly wednesday "On the home Mac, run the reviewed research entrypoint, save results to review/, and write a receipt"
```

### SJM discovery exception
```
/schedule daily 11:30pm "On SJM, run scripts/codex-discovery-job.sh sjm-source-only and write its machine-local receipt"
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Jobs run on their declared owner host; do not assume the Mac Mini.
- Results land in their declared artifacts and receipt; failures remain explicit in machine-local status.
- One-time jobs disable or remove themselves only after a successful receipt.
- When Keith asks "what reminders do I have" or similar, list the registered K2B host jobs; do not inspect the retired SQLite scheduler.
