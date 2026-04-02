# K2B Remote Scheduler System - Exploration Report

**Date:** 2026-03-31  
**Scope:** Understanding scheduler storage, YouTube morning job configuration, and persistence mechanism

---

## Executive Summary

The k2b-remote scheduler is a **SQLite-backed, polling-based system** that:
- Stores all scheduled tasks in a persistent SQLite database (`k2b-remote.db`)
- Polls every 60 seconds for due tasks and executes them via Claude Code agents
- Supports both recurring (cron-based) and one-time reminder tasks
- Persists across restarts by reading from the database at startup
- Has **NO current YouTube morning job configured** (database is empty)
- Includes special handling for YouTube morning tasks to send nudge buttons after completion

---

## 1. Scheduler Storage: SQLite Database

### Location
```
/Users/keithmbpm2/Projects/K2B/k2b-remote/store/k2b-remote.db
```

### Database Structure
The `scheduled_tasks` table schema (from `db.ts` lines 73-95):

| Column | Type | Details |
|--------|------|---------|
| `id` | TEXT PRIMARY KEY | 8-char UUID (e.g., `a1b2c3d4`) |
| `chat_id` | TEXT | Telegram chat ID where output is sent |
| `prompt` | TEXT | The prompt/command to execute (e.g., `Run /youtube morning`) |
| `schedule` | TEXT | Cron expression OR `'once'` for one-time |
| `next_run` | INTEGER | Unix milliseconds of next scheduled execution |
| `last_run` | INTEGER | Unix milliseconds of last execution |
| `last_result` | TEXT | Output from last run |
| `status` | TEXT | `'active'` or `'paused'` |
| `created_at` | INTEGER | Creation timestamp (Unix ms) |
| `type` | TEXT | `'recurring'` or `'one-time'` (added via migration) |

### Current State
```
$ node dist/schedule-cli.js list
No scheduled tasks.
```
**There are NO scheduled tasks currently configured, including no YouTube morning job.**

### Storage Format
- SQLite 3 with WAL (Write-Ahead Logging) enabled
- Uses `better-sqlite3` npm package for synchronous database access
- Database auto-initializes on first run via `initDatabase()` (db.ts:16-98)
- Migration system in place: new `type` column added automatically if missing (db.ts:90-95)

---

## 2. YouTube Morning Job: Current Status and Wiring

### Documented but Not Yet Scheduled
According to DEVLOG.md (entry 2026-03-30, "Proactive YouTube Knowledge Acquisition"):
- The `/youtube morning` subcommand was created in the `k2b-youtube-capture` skill
- A scheduled task was documented in the design: `daily 7am "Run /youtube morning"`
- **But the task itself was never actually created in the database**

### How YouTube Morning is Wired (When It Runs)

**In scheduler.ts (lines 68-74):**
```typescript
// After YouTube morning task, send nudge buttons for any pending videos
if (task.prompt.includes('/youtube morning') && ALLOWED_CHAT_ID) {
  const nudged = await sendPendingNudges(ALLOWED_CHAT_ID)
  if (nudged > 0) {
    logger.info({ nudged }, 'Sent YouTube nudge buttons')
  }
}
```

This means when a task containing `/youtube morning` completes:
1. Scheduler calls `sendPendingNudges()` from bot.ts
2. Reads pending recommendations from the JSONL vault file
3. Sends each video as a Telegram message with [Get highlights] [Skip] buttons
4. Tracks sent videos to avoid duplicates

**In bot.ts (lines 438-461):**
- `sendPendingNudges()` reads from `youtube-recommended.jsonl` vault file
- Filters for videos with `status: 'nudge_sent'`
- Sends each with inline keyboard buttons via Telegram
- Uses `sendTelegramMessageWithButtons()` to render the [Get highlights] [Skip] UI

**In observe.ts (line 27):**
```typescript
if (/\/youtube|youtube morning/.test(lower)) return 'k2b-youtube-capture'
```
The observer tracks YouTube morning runs for self-improvement analysis.

### Architecture
```
Scheduler Poll Loop
│
├─ getDueTasks() -> check task.prompt
│
└─ task.prompt.includes('/youtube morning')?
   ├─ runAgent() -> execute the skill
   ├─ updateTaskNextRun() -> compute next 7am
   └─ sendPendingNudges(ALLOWED_CHAT_ID)
      ├─ readRecommendations() from JSONL
      ├─ Filter status='nudge_sent'
      └─ Send Telegram [Get highlights] [Skip] buttons
```

---

## 3. How Scheduler Persists Across Restarts

### Initialization Flow (index.ts:45-123)
```
1. initDatabase()           // db.ts:16-98
   ├─ Create store/ dir
   ├─ Open k2b-remote.db (SQLite)
   ├─ Create tables (sessions, memories, scheduled_tasks)
   └─ Run migration: add 'type' column if missing

2. initScheduler(send)      // scheduler.ts:20-24
   ├─ Store send function
   └─ setInterval(runDueTasks, 60_000)  // Poll every 60 seconds

3. bot.start()              // grammy bot starts listening
```

### Runtime Polling Loop (scheduler.ts:30-87)
```
Every 60 seconds:

1. getDueTasks()            // db.ts:258-279
   └─ SELECT FROM scheduled_tasks 
      WHERE status='active' AND next_run <= NOW()

2. For each due task:
   a) Update next_run immediately (prevents re-picking)
   b) Run agent with task.prompt
   c) If recurring: updateTaskNextRun()
   d) If one-time: deleteTask()
   e) Send results to Telegram

3. Log observations (vault file changes)
```

### Restart Resilience
- ✅ Tasks stored in database persist across restart
- ✅ On restart, `initDatabase()` reads existing tasks
- ✅ Poll loop resumes and checks for any overdue tasks
- ✅ No in-memory state lost (stateless architecture)
- ⚠️ If a task is interrupted mid-run, it may be picked again on restart (handled by checking `next_run`)

---

## 4. CLI Tools for Task Management

### Command-Line Interface (schedule-cli.ts)

Run with: `cd k2b-remote && node dist/schedule-cli.js <command>`

#### Create Recurring Task
```bash
node dist/schedule-cli.js create "<prompt>" "<cron>" <chat_id>

# Example: Daily 7am YouTube morning
node dist/schedule-cli.js create "Run /youtube morning" "0 7 * * *" 8394008217
```

#### Create One-Time Reminder
```bash
node dist/schedule-cli.js create-once "<prompt>" "<datetime>" <chat_id>

# Example: Tomorrow at 3pm HKT
node dist/schedule-cli.js create-once "Check dashboard" "2026-04-02 15:00" 8394008217
```

#### List All Tasks
```bash
node dist/schedule-cli.js list
```

#### Pause/Resume/Delete
```bash
node dist/schedule-cli.js pause <id>
node dist/schedule-cli.js resume <id>
node dist/schedule-cli.js delete <id>
```

### Cron Expression Format
Standard 5-field cron (minute hour day month weekday):
- `0 7 * * *` = Every day at 7am
- `0 7 * * 1-5` = Weekdays at 7am
- `30 14 * * *` = Every day at 2:30pm

### Implementation Details (schedule-cli.ts:1-170)

**Create recurring task (lines 29-56):**
1. Parse and validate cron expression
2. Generate 8-char UUID for task ID
3. Compute next run time using `computeNextRun()` (from scheduler.ts)
4. Insert into database via `createTask()`

**Create one-time reminder (lines 59-90):**
1. Parse datetime in HKT timezone (+08:00)
2. Validate it's in the future
3. Store with type='one-time' and schedule='once'
4. Task auto-deletes after firing

---

## 5. File Structure and Code Organization

### k2b-remote Source Files

| File | Purpose |
|------|---------|
| `scheduler.ts` | 60s poll loop, task execution, agent invocation |
| `schedule-cli.ts` | CLI for creating/listing/managing tasks |
| `db.ts` | SQLite database initialization, CRUD operations |
| `bot.ts` | Telegram bot, message handlers, YouTube buttons |
| `agent.ts` | Claude Code agent invocation via SDK |
| `youtube.ts` | JSONL layer for video recommendations |
| `observe.ts` | Vault observation logging (file change tracking) |
| `index.ts` | Main entry point, initialization sequence |
| `config.ts` | Environment and path configuration |

### Key Dependencies
- `better-sqlite3` - Synchronous SQLite database
- `cron-parser` - Parse/compute next run times
- `grammy` - Telegram bot framework
- `@anthropic-ai/claude-agent-sdk` - Agent execution
- `pino` - Structured logging

### Build and Runtime
- **Source:** TypeScript in `src/` directory
- **Compiled:** JavaScript in `dist/` directory
- **Build:** `npm run build` (runs `tsc`)
- **Start:** `npm start` (runs `node dist/index.js`)
- **Schedule CLI:** `node dist/schedule-cli.js`

---

## 6. Configuration and Deployment

### Environment Variables (.env)
```bash
TELEGRAM_BOT_TOKEN=...          # Required for bot
ALLOWED_CHAT_ID=8394008217      # Where scheduled task output goes
LOG_LEVEL=info                  # Logging verbosity
HTTP_PROXY=...                  # Optional proxy (for System Proxy Mode)
```

### Database Location
- **Path:** `/Users/keithmbpm2/Projects/K2B/k2b-remote/store/k2b-remote.db`
- **Permissions:** User-writable
- **Size:** ~57KB (currently empty of scheduled tasks)
- **WAL files:** `-shm` and `-wal` for crash recovery

### Polling Behavior
- **Frequency:** Every 60 seconds
- **Execution:** Sequential (one task at a time, respects `isRunning` flag)
- **Timezone:** Tasks use Unix milliseconds; cron expressions evaluated by `cron-parser` library
- **Overdue handling:** If a task is due but skipped (e.g., bot offline), it runs on next poll after restart

---

## 7. Special Handling and Edge Cases

### YouTube Morning Task Special Flow (scheduler.ts:68-74)
When a task prompt contains `/youtube morning`:
1. Task executes normally (agent runs the skill)
2. **After completion**, immediately send pending nudges
3. Pending nudges = videos in JSONL with `status='nudge_sent'`
4. Each sends a Telegram button group: [Get highlights] [Skip]
5. Clicking buttons triggers `handleYouTubeCallback()` in bot.ts

### One-Time Reminders (Type = 'one-time')
- Stored with type='one-time' and schedule='once'
- After execution, automatically deleted from database
- No reschedule computation

### Recurring Tasks (Type = 'recurring')
- After execution, next_run computed immediately before agent runs
- Prevents double-execution if agent takes longer than poll interval
- Next run computed from cron expression, not relative to current time

### Pause/Resume
- Paused tasks are skipped in `getDueTasks()` query
- Resume requires recomputing next_run from cron expression
- Useful for temporary disabling without deletion

---

## 8. Missing YouTube Morning Configuration

### What Was Designed
From DEVLOG.md (2026-03-30, "Proactive YouTube Knowledge Acquisition"):
```
- Created scheduled task: daily 7am HKT `Run /youtube morning`
```

### What Actually Exists
- ✅ The `/youtube morning` skill subcommand (in k2b-youtube-capture)
- ✅ The post-task nudge wiring (scheduler.ts:68-74)
- ✅ The Telegram button UI (bot.ts inline keyboards)
- ❌ **No actual scheduled task entry in the database**

### Why It's Missing
The design document specifies creating it, but the actual CLI command was never executed:
```bash
# This command was never run:
node k2b-remote/dist/schedule-cli.js create "Run /youtube morning" "0 7 * * *" 8394008217
```

### To Enable YouTube Morning (What Would Need To Happen)
```bash
cd /Users/keithmbpm2/Projects/K2B/k2b-remote
node dist/schedule-cli.js create "Run /youtube morning" "0 7 * * *" 8394008217
```
This would:
1. Create task with ID (e.g., `a1b2c3d4`)
2. Set next_run to tomorrow at 7am HKT
3. Start executing daily at 7am
4. Send nudge buttons to chat 8394008217 after each run

---

## 9. Data Files and Vault Integration

### YouTube Recommendations JSONL (Vault)
**Path:** `~/Projects/K2B-Vault/Notes/Context/youtube-recommended.jsonl`

**Format:** One JSON object per line

Status Values:
- `pending` - Just recommended, waiting for nudge
- `nudge_sent` - Button sent to Telegram
- `highlights_sent` - User got summary
- `watched` - User indicated watched
- `skipped` - User skipped
- `expired` - Too old
- `processed` - Fully promoted to vault

### Observations JSONL (Vault)
**Path:** `~/Projects/K2B-Vault/Notes/Context/observations.jsonl`

Records every file change from agent runs for pattern analysis.

---

## 10. Summary Table

| Aspect | Details |
|--------|---------|
| **Storage** | SQLite (`k2b-remote.db`) with WAL, synchronous access |
| **Polling** | Every 60 seconds |
| **Persistence** | Database survives process restart |
| **YouTube Morning Configured** | ❌ No (designed but not created) |
| **YouTube Morning Wiring** | ✅ Yes (scheduler.ts, bot.ts callbacks) |
| **CLI Tool** | `node dist/schedule-cli.js` |
| **Task Types** | Recurring (cron) + One-time (datetime) |
| **Execution** | Sequential, with `isRunning` guard |
| **Agent Integration** | Claude Code SDK |
| **Data Format** | JSONL for video recommendations, JSONL for observations |
| **Deployment** | Mac Mini via npm start / pm2 |

---

## Key Findings

1. **Scheduler Type:** Polling-based (60s intervals), not cron daemon
2. **Storage:** SQLite with WAL, survives restarts automatically
3. **Persistence:** Completely stateless - all state in database
4. **YouTube Morning:** Infrastructure is ready, just needs one CLI command to activate
5. **Database Location:** `/Users/keithmbpm2/Projects/K2B/k2b-remote/store/k2b-remote.db`
6. **CLI:** `node dist/schedule-cli.js` in k2b-remote directory
7. **Chat ID:** 8394008217 (where output is sent)
8. **Vault Integration:** Via JSONL files, synced with Syncthing
9. **Observer Tracking:** Special handling logs YouTube runs separately
10. **Migration System:** Automatically adds new columns if missing

