---
name: k2b-orchestrator
description: Dispatch and monitor K2Bi analyst tasks via the orchestrator board, AND run the Chat-1 "deep-research booster" (read the news on a domain, build a Kimi Deep Research prompt, pause for Keith's manual Kimi run, then validate-and-repair the returned source links). Use when Keith says /orchestrator, "dispatch a k2bi task", "orchestrator board", "what's on the board", "show flights", "read the news on <domain>", "what's forming in <area>", "deep research on <topic-or-ticker>", "go deeper", or pastes Kimi Deep Research output back for a waiting flight.
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

**Chat-1 deep-research booster (Increment 1):**
- "read the news on `<domain>`" / "what's forming in `<area>`" / "what trends are happening in `<X>`"
- "take trend N deeper" / "go deeper on `<X>`" / "deep research on `<topic-or-ticker>`"
- Keith pastes Kimi Deep Research output back (for a flight waiting on his Kimi run)

## What it does

Runs `~/Projects/K2B/scripts/k2b-orchestrator.sh <subcommand>` and shows the output. The orchestrator is the single writer for the task board and result artifacts.

## Subcommands

| Subcommand | Arguments | Purpose |
|---|---|---|
| `init` | — | Initialize DB and directories |
| `add` | `--profile`, `--command-key`, `--success`, `--permissions`, `--flight`, `--entity`, `--payload`, `--workspace`, `--status` | Create a task (returns task id). `--status` may be `ready` (default), `waiting_for_kimi_output`, or `needs_human` -- creating a flight directly parked. One-flight lock: refuses a 2nd non-terminal task with the same `--entity`. |
| `list` | `[--status S] [--json]` | List tasks |
| `flights` | — | List distinct flight ids |
| `show` | `<id> [--json]` | Show one task |
| `claim` | `<id>` | Mark a task running (manual override) |
| `complete` | `<id> [--result URL]` | Mark a task done. Refuses running/zombie and the parked *input* states (`waiting_for_kimi_output`/`needs_human` -- resolve those via `return` first) and already-terminal rows; ACCEPTS `returned` (the post-Kimi state), `ready`, `blocked`. |
| `block` | `<id> --reason R` | Block a task |
| `unblock` | `<id>` | Return a blocked task to ready |
| `cancel` | `<id>` | Cancel a task |
| `return` | `<id> (--text T \| --path P)` | For a `waiting_for_kimi_output` flight: run the acceptance gate on the pasted/file Kimi output (size 500B-2MB, >=3 URLs, >=5 substantive lines, and a task-bound completion sentinel -- the last non-empty line must contain `END OF KIMI RESEARCH` plus the task id, case-insensitive), store raw + sha256 -> `returned`. For `blocked`/`needs_human`: re-ready it (no gate). Prefer `--path` for multi-line content (see the conductor). |
| `poll-once` | — | Run one dispatcher tick (reclaim zombies, spawn one ready task) |
| `render-board` | — | Write `board.md` from current DB state |

## Ship 1a scope

- Only the `k2bi` profile exists.
- Only two `command_key` values are allowlisted:
  - `k2bi-smoke-enrich-lrcx` (live K2Bi analyst command)
  - `test-echo-readonly` (test-only, harmless)
- The orchestrator never approves strategies, never commits, never touches the K2Bi engine directly. Approval / commit / journal-retro are post-v1.
- `--workspace` is rejected for `k2bi` (workspace is operator-config only).
- **Increment-1 note:** Chat-1 deep-research flights use a `k2b` profile + `k2b-kimi-research` command_key. They are created PARKED (`--status waiting_for_kimi_output`) and NEVER dispatched, so the K2Bi allowlist + preflight -- which only run when the dispatcher claims a `ready` task -- never apply to them. The `k2bi` allowlist above is unchanged; the `k2b` profile is K2Bi-free (no workspace, no engine).

## Chat 1 conductor -- news scan + Kimi deep-research booster (Increment 1)

This is the procedure K2B (you, the agent) follows at runtime. The reasoning -- the news scan, the prompt writing, the link repair -- is done by YOU with your own web-search/fetch tools. The orchestrator is only the durable pause/lock/return ledger. Flights are created **directly parked** (never `ready`), so the dispatcher never spawns a worker for them; you drive the lifecycle by hand. Design: [[feature_k2b-orchestrator-v1]] "the 3 chats" + `plans/2026-05-30_orchestrator-ship-1b-spec.md`.

### A. "read the news on `<domain>`" -> trends

1. Web-search recent news in the domain (your tools; ~12 searches max). If no provider is reachable, say "news search unavailable -- can't scan now" and stop; never fabricate.
2. Synthesize **2-4 emerging trends**. For each: a one-line *why it's forming*, 2-3 source links that you **HTTP-verify** (drop or replace any that don't resolve -- never show a broken link as good), and a few rough candidate names (starting points for Keith's judgment, NOT picks).
3. Present them. Offer: "want me to take any of these deeper with Kimi DR?"

### B. "go deeper on trend N" / "deep research on `<topic-or-ticker>`" -> build the Kimi prompt + park the flight

1. **Create the flight first** (so the task-id and the one-flight lock exist), parked:
   ```bash
   TID=$(bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
     --profile k2b --command-key k2b-kimi-research \
     --success "clean, link-verified deep research on <topic>" \
     --status waiting_for_kimi_output --entity "<canonical topic or TICKER>" | awk '{print $NF}')
   ```
   - `--entity`: canonicalize (lowercase-trim a topic; uppercase a ticker). If `add` errors `flight already active for ...`, a live flight for that topic exists -- tell Keith and offer to reuse or `cancel` it. Do NOT use the `k2bi` profile (that path resolves a K2Bi workspace); `k2b` keeps it agent-managed and K2Bi-free.
2. **Build the Kimi DR prompt**, seeded by your scan findings for that trend: a falsifiable thesis, the driver chain, 5-8 seed queries, 8-15 source anchors, a counter-thesis, and an avoid-list. The prompt **MUST end with this exact instruction** (fill in the real TID):
   > Finish your output with this exact line and nothing after it: `=== END OF KIMI RESEARCH: <TID> ===`
   This sentinel is the gate's anti-truncation + paste-binding proof -- without it the return is rejected. (You instruct the exact line for reliability; the gate itself matches *leniently* -- the last non-empty line need only contain `END OF KIMI RESEARCH` and the task id, case-insensitive -- so small formatting differences still pass.)
3. If the scan found too little signal to write a focused prompt, park as `needs_human` instead (`--status needs_human --payload '{"question":"<one clarifying question>"}'`) and ask Keith that one question rather than emitting a generic prompt.
4. Hand Keith the prompt (tap-to-copy) and tell him: run it on kimi.com, paste the result back here.

### C. Keith pastes the Kimi output back -> gate + link repair -> clean research

1. **Ingest through the gate.** Multi-line research with URLs/quotes/backticks/`$` breaks an inline `--text` shell argument, so write Keith's paste to a vault file FIRST and return by path:
   ```bash
   # Write the paste to e.g. K2B-Vault/raw/orchestrator-inbox/<TID>-paste.md (use your Write tool), then:
   bash ~/Projects/K2B/scripts/k2b-orchestrator.sh return <TID> --path <file>
   ```
   (`--text` is for short single-line returns only.)
   - On **reject** the gate prints the exact reason; tell Keith which, with the recovery:
     - `missing completion sentinel` -> the paste is likely cut off (or from a different flight) -> re-paste the full output.
     - `fewer than 3 source URLs` / `fewer than 5 substantive lines` -> the Kimi output was thin -> re-run Kimi with a fuller prompt and paste again.
     - `size ... outside [500, 2000000]` -> empty/garbled or oversized -> check the paste.
   - On **pass** the flight is `returned`; the raw is stored at `K2B-Vault/raw/orchestrator-results/<TID>-kimi-raw.md` (byte count + sha256 recorded in the task payload).
2. **Validate AND repair the source links** (your tools; the honesty rule is non-negotiable):
   - For each source URL bound to a claim: fetch it. Resolves AND supports the claim -> `cite-ok`. Resolves but does NOT support it -> `cite-suspect` (treat as unverified). Broken -> web-search the claim text for a replacement that **actually supports the same claim**; found -> `repaired` (record old->new); none -> `unverified`. **Never** swap in a link that merely loads but doesn't back the claim.
   - **Hard-stop:** if fewer than 60% of claims end `cite-ok` or `repaired`, STOP and tell Keith "this Kimi output's sourcing is too weak (X% supported) -- not producing a clean doc."
3. Write the clean research doc (repaired links + a per-claim ledger: supported / repaired old->new / unverified) to a vault file.
4. **Finish the flight:**
   ```bash
   bash ~/Projects/K2B/scripts/k2b-orchestrator.sh complete <TID>
   ```
5. Present the clean research to Keith. **Increment 1 stops here** -- he takes it into his K2Bi narrative/thesis himself (the auto-handoff is a later increment).

### Monitoring

Parked flights show on `/portfolio active` ("waiting on your Kimi run" / "needs your input"). Forgotten flights auto-expire via the TTL sweep on `poll-once` (14 days for `waiting_for_kimi_output`, 7 for `needs_human`).

## Canonical add example

```bash
bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
  --profile k2bi \
  --command-key k2bi-smoke-enrich-lrcx \
  --success "LRCX Stage-2 enriched; result artifact written; no engine touch" \
  --permissions analyst-command
```

Parked Chat-1 deep-research flight (Increment 1) -- created directly in a parked state with an `--entity` for the one-flight lock (the lock dedups case-insensitively, so canonicalizing the entity is just for tidiness):

```bash
bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
  --profile k2b \
  --command-key k2b-kimi-research \
  --success "clean, link-verified deep research on grid-power AI bottleneck" \
  --status waiting_for_kimi_output \
  --entity "grid power ai bottleneck"
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
