---
name: k2b-orchestrator
description: Dispatch and monitor K2Bi analyst tasks via the orchestrator board. Use when Keith says /orchestrator, "dispatch a k2bi task", "orchestrator board", "what's on the board", "add an orchestrator task", or "show flights".
---

# K2B Orchestrator

Manage durable tasks that dispatch allowlisted analyst commands to the sibling K2Bi workspace. The orchestrator owns the control plane: task creation, preflight checks, worker spawn, heartbeats, result artifacts, and a board mirror.

## When to Trigger

Keith says any of:
- `/orchestrator`
- "dispatch a k2bi task"
- "orchestrator board"
- "what's on the board"
- "add an orchestrator task"
- "show flights"
- "list tasks"

## What it does

Runs `~/Projects/K2B/scripts/k2b-orchestrator.sh <subcommand>` and shows the output. The orchestrator is the single writer for the task board and result artifacts.

## Subcommands

| Subcommand | Arguments | Purpose |
|---|---|---|
| `init` | — | Initialize DB and directories |
| `add` | `--profile`, `--command-key`, `--success`, `--permissions`, `--flight`, `--entity`, `--payload`, `--workspace` | Create a task (returns task id) |
| `list` | `[--status S] [--json]` | List tasks |
| `flights` | — | List distinct flight ids |
| `show` | `<id> [--json]` | Show one task |
| `claim` | `<id>` | Mark a task running (manual override) |
| `complete` | `<id> [--result URL]` | Mark a task done |
| `block` | `<id> --reason R` | Block a task |
| `unblock` | `<id>` | Return a blocked task to ready |
| `cancel` | `<id>` | Cancel a task |
| `return` | `<id> [--text T]` | Return a task with payload (Ship 1b stub) |
| `poll-once` | — | Run one dispatcher tick (reclaim zombies, spawn one ready task) |
| `render-board` | — | Write `board.md` from current DB state |

## Ship 1a scope

- Only the `k2bi` profile exists.
- Only two `command_key` values are allowlisted:
  - `k2bi-smoke-enrich-lrcx` (live K2Bi analyst command)
  - `test-echo-readonly` (test-only, harmless)
- The orchestrator never approves strategies, never commits, never touches the K2Bi engine directly. Approval / commit / journal-retro are post-v1.
- `--workspace` is rejected for `k2bi` (workspace is operator-config only).

## Canonical add example

```bash
bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
  --profile k2bi \
  --command-key k2bi-smoke-enrich-lrcx \
  --success "LRCX Stage-2 enriched; result artifact written; no engine touch" \
  --permissions analyst-command
```

## Rendering convention

After any board-changing command (`add`, `block`, `unblock`, `cancel`, `complete`, `poll-once`), show the board:

```bash
cat ~/Projects/K2B-Vault/System/orchestrator/board.md
```

## Usage logging

After completing the main task, log this skill invocation:

```bash
echo -e "$(date +%Y-%m-%d)\tk2b-orchestrator\t$(echo $RANDOM | md5sum | head -c 8)\torchestrator operation" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
