# K2B Mission Control -- Design Spec

## Context

K2B has grown to 20 skills, a background observer, scheduled tasks, a YouTube pipeline, and an Obsidian vault with 109+ notes. The only interfaces are Telegram (message-by-message, no overview), Claude Code terminal (session-bound), and Obsidian (passive). None answer: "What has K2B been doing? What needs my attention? What's the state of my system?"

Mission Control is a read-only dashboard (v1) that gives Keith a single-screen view of K2B's state, surfacing what needs attention and making the self-improvement loop visible.

## Architecture

Standalone Express + React app. Separate pm2 process from k2b-remote. Reads shared data sources directly (vault files, SQLite, JSONL, health files). Does not write to vault or DB in v1.

```
Mac Mini (always-on)
  |-- k2b-remote (pm2, Telegram bot)
  |-- k2b-dashboard (pm2, port 3200) -- NEW
  |     |-- Express API server (TypeScript)
  |     |-- React SPA (Vite, served statically)
  |     |-- Reads from shared data sources:
  |
  Shared data sources:
    |-- K2B-Vault/ (Obsidian markdown, synced via Syncthing)
    |-- k2b-remote/store/k2b-remote.db (SQLite: sessions, memories, scheduled tasks)
    |-- k2b-remote/store/health.json (heartbeat: timestamp, pid, uptime, memory)
    |-- K2B-Vault/Notes/Context/skill-usage-log.tsv (71+ entries)
    |-- K2B-Vault/Notes/Context/youtube-recommended.jsonl (Watch pipeline)
    |-- K2B-Vault/Notes/Context/youtube-processed.md (Queue pipeline log)
    |-- K2B-Vault/Notes/Context/observer-candidates.md (observer findings)
    |-- K2B-Vault/Notes/Context/preference-signals.jsonl (behavior signals)
    |-- K2B-Vault/Notes/Context/observations.jsonl (raw observations)
    |-- K2B-Vault/MOC_K2B-Roadmap.md (feature status)
    |-- K2B-Vault/Notes/Features/*.md (feature specs)
    |-- ~/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_learnings.md
    |-- pm2 CLI (process status via `pm2 jlist`)
    |-- git log (recent repo activity)
```

### Key Design Decisions

1. **Read-only v1.** Dashboard reads data, does not write. Action buttons (YouTube skip, inbox triage) are v2 -- they'll need a write-back mechanism (likely an internal endpoint on k2b-remote or a command queue file).
2. **No auth for localhost.** Accessed via localhost or Tailscale private network. No login needed.
3. **Polling, not WebSocket (v1).** Dashboard polls API every 30 seconds. Simple, reliable.
4. **Separate pm2 process.** Independent from k2b-remote. If bot crashes, dashboard shows the error state. If dashboard crashes, K2B keeps working.
5. **Single-page app.** One page, no routing. Everything visible at once.
6. **Always-show subtitle + click-to-expand.** Every item shows title + one-line preview by default. Click/tap expands for full detail and action buttons. Works on both desktop and mobile.

## Dashboard Layout

5 rows, 10 panels. Priority panels marked with ★.

```
Row 1 (3-col):  System Status | Vault Stats | ★ Roadmap: What's Next
Row 2 (full):   ★ YouTube Digest (Watch + Queue side by side)
Row 3 (2-col):  ★ K2B Intelligence | ★ Skill Activity
Row 4 (full):   Inbox
Row 5 (3-col):  Activity Feed | Scheduled Tasks | Content Pipeline
```

### Row 1: System | Vault | Roadmap

**System Status**
- pm2 process status for k2b-remote, k2b-observer, k2b-dashboard (green dot = running, amber = restarting, red = stopped)
- Uptime per process
- Memory usage, active sessions
- Source: `pm2 jlist`, `store/health.json`

**Vault Stats**
- Total note count (large number)
- Breakdown: daily, people, projects, features, insights, content ideas
- Source: glob counts on vault directories

**★ Roadmap: What's Next**
- Features from `MOC_K2B-Roadmap.md` and `Notes/Features/*.md`
- Sorted: `next` status first, then `planned`
- Each item shows title + one-line subtitle (extracted from feature spec frontmatter or first paragraph)
- Click-to-expand: full description from feature spec, link to vault note, eval status if autoresearch exists
- Footer: shipped count, planned count
- Source: parse MOC for feature links, read each feature file's frontmatter (`status`, `date`, description)

### Row 2: YouTube Digest (full width)

Two columns side by side:

**K2B Watch (left) -- recommended to Keith**
- Videos from `youtube-recommended.jsonl` with status `recommended` or `nudge_sent`
- Each item: title + subtitle (channel, duration, when nudged)
- Expand: why recommended, relevance context, Watch/Skip/Summarize buttons (v2 actions)
- Source: `youtube-recommended.jsonl`

**K2B Queue (right) -- Keith sent for processing**
- Recent entries from `youtube-processed.md` (last 7 days)
- Each item: title + subtitle (process date, outcome -- done/skipped + reason)
- Expand: full processing notes, link to vault note if created
- Source: parse `youtube-processed.md` pipe-separated log

Header shows total processed count.

### Row 3: K2B Intelligence | Skill Activity

**★ K2B Intelligence**
Three sections, no vanity metrics:

1. **Pending Confirmation** -- candidate learnings from observer that need Keith's yes/no
   - Each item: confidence tag + one-line description
   - Expand: evidence, recommendation, Confirm/Dismiss buttons (v2 actions)
   - Source: parse `observer-candidates.md` "Candidate Learnings" section

2. **Recently Learned** -- latest confirmed learnings with reinforcement count
   - Each item: reinforcement badge (x6, x4, x1) + learning text
   - Show top 5 by recency
   - Source: parse `self_improve_learnings.md`

3. **Observer Status** -- one-line: last analysis time, signal count, pattern count, next trigger condition
   - Source: `observer-candidates.md` header, `observations.jsonl` count

**★ Skill Activity**
Two sections:

1. **Active (7 days)** -- skills used recently, compact rows
   - Each row: colored dot (green = hot, blue = warm, gray = cooling) + skill name + use count + last used date
   - Source: `skill-usage-log.tsv`, filter last 7 days, aggregate by skill

2. **Never Used** -- dormant skills with descriptions and hints
   - Each item: skill name + what it does (from skill SKILL.md description field) + "Try:" hint with example command
   - Source: compare all k2b-* skills in `.claude/skills/` against `skill-usage-log.tsv` to find skills with 0 invocations
   - Skill descriptions: read each SKILL.md's `description:` frontmatter field

### Row 4: Inbox (full width)

- All items from `Inbox/` and `Inbox/Ready/`
- Each item: filename + subtitle (note type, origin, age in days) + date badge
- Expand: first paragraph of note content, frontmatter details, Promote/Archive/Delete buttons (v2 actions)
- "Ready for processing" count shown in header
- Source: glob `K2B-Vault/Inbox/*.md` and `Inbox/Ready/*.md`, parse YAML frontmatter with gray-matter

### Row 5: Activity | Tasks | Content Pipeline

**Activity Feed**
- Timestamped log of recent K2B activity (last 48h)
- Merge from: vault file modification times, skill-usage-log.tsv, scheduled task run history, git log
- Format: `HH:MM  description` with "yesterday" separator
- Source: multiple (file mtimes, TSV, SQLite, git)

**Scheduled Tasks**
- All tasks from SQLite `scheduled_tasks` table + pm2-managed recurring processes
- Each row: task name + schedule (cron human-readable)
- Footer: next upcoming run
- Source: SQLite query + `pm2 jlist` for observer loop

**Content Pipeline**
- Visual funnel: ideas → adopted → drafts → published (with counts)
- Source: count files in `Inbox/` with type=content-idea, `Notes/Content-Ideas/`, filter by status frontmatter field

## Interaction Pattern

**Always-show subtitle + click-to-expand:**
- Every list item renders with title (line 1) and a one-line preview subtitle (line 2, muted color)
- The subtitle gives enough context to decide if you need more detail
- Click/tap anywhere on the row to expand: reveals full description, metadata, and action buttons
- Chevron indicator (›) rotates on expand
- Only one item expanded per panel at a time (accordion behavior)
- Works identically on desktop (click) and mobile (tap)

Action buttons shown in expanded state are non-functional in v1 (grayed out or show "coming soon" tooltip). They establish the UI pattern for v2.

## Visual Design

Dark theme, monospace, mission control aesthetic.

- **Font:** JetBrains Mono (with SF Mono, monospace fallback) throughout
- **Background:** `#0a0a0a` (page), `#141414` (panels)
- **Borders:** `#1e1e1e` (default), `#3b82f633` (priority panels blue tint), `#8b5cf633` (intelligence panels purple tint)
- **Text:** `#e0e0e0` (primary), `#888888` (secondary), `#555555` (muted)
- **Status:** `#22c55e` (green/ok), `#f59e0b` (amber/attention), `#ef4444` (red/error), `#3b82f6` (blue/interactive)
- **Header bar:** K2B MISSION CONTROL left, status + uptime + refresh indicator right, pulsing green dot
- **Priority panels:** blue-tinted border, blue panel title with ★ prefix
- **Intelligence panels:** purple-tinted border
- **Tags:** semi-transparent colored backgrounds (`#color22`)
- **Responsive:** panels stack vertically on mobile. Priority order: System, Roadmap, YouTube, Intelligence, Skills, Inbox

## API Endpoints

### GET /api/system
Aggregates: pm2 process status (`pm2 jlist`), health.json, vault folder counts, git last commit.

### GET /api/roadmap
Parses MOC_K2B-Roadmap.md for feature links, reads each feature file's frontmatter and first paragraph. Returns features sorted by status (next > planned > shipped).

### GET /api/youtube
Reads youtube-recommended.jsonl (Watch pipeline) and parses youtube-processed.md (Queue pipeline). Returns both lists with metadata.

### GET /api/intelligence
Parses observer-candidates.md (pending confirmations + observer status), reads self_improve_learnings.md (recent learnings sorted by date, top 5).

### GET /api/skills
Reads skill-usage-log.tsv, aggregates by skill for last 7 days. Lists all k2b-* skills from .claude/skills/, compares against usage to find dormant skills. Reads each dormant skill's SKILL.md for description.

### GET /api/inbox
Reads all .md files from Inbox/ and Inbox/Ready/. Parses YAML frontmatter. Returns sorted by date descending.

### GET /api/activity
Merges: vault file mtimes (last 48h), skill-usage-log.tsv entries, scheduled task history from SQLite, git log entries. Sorted by timestamp descending, limit 50.

### GET /api/tasks
Reads scheduled_tasks from SQLite + pm2 process list. Computes next run times.

### GET /api/content-pipeline
Counts: content-idea files in Inbox, files in Notes/Content-Ideas grouped by status frontmatter.

## Project Structure

```
k2b-dashboard/
  package.json
  tsconfig.json
  tsconfig.server.json
  vite.config.ts
  .env
  src/
    server/
      index.ts              # Express entry, serves API + static client
      routes/
        system.ts           # /api/system
        roadmap.ts          # /api/roadmap
        youtube.ts          # /api/youtube
        intelligence.ts     # /api/intelligence
        skills.ts           # /api/skills
        inbox.ts            # /api/inbox
        activity.ts         # /api/activity
        tasks.ts            # /api/tasks
        content-pipeline.ts # /api/content-pipeline
      lib/
        vault.ts            # Vault file reading, frontmatter parsing (gray-matter)
        db.ts               # SQLite reader (read-only connection)
        pm2.ts              # pm2 status via `pm2 jlist` shell exec
        usage.ts            # Parse skill-usage-log.tsv
        git.ts              # Git log parsing
        youtube-data.ts     # Parse youtube-recommended.jsonl + youtube-processed.md
        observer.ts         # Parse observer-candidates.md
        learnings.ts        # Parse self_improve_learnings.md
        config.ts           # Paths, ports, env vars
    client/
      index.html
      main.tsx              # React entry
      App.tsx               # Root: polling orchestration, layout grid
      hooks/
        usePolling.ts       # Generic polling hook (30s default)
      components/
        Header.tsx          # Top bar: title, status, uptime, refresh
        SystemStatus.tsx
        VaultStats.tsx
        Roadmap.tsx         # Priority panel
        YouTubeDigest.tsx   # Priority panel (Watch + Queue)
        Intelligence.tsx    # Priority panel (confirmations + learnings + observer)
        SkillActivity.tsx   # Priority panel (active + dormant with hints)
        Inbox.tsx
        ActivityFeed.tsx
        ScheduledTasks.tsx
        ContentPipeline.tsx
        ExpandableRow.tsx   # Reusable: title + subtitle + expand behavior
        StatusDot.tsx       # Reusable: green/amber/red dot
        Tag.tsx             # Reusable: colored tag
      styles/
        global.css          # Dark theme, typography, layout grid, responsive
  dist/                     # Built output (Vite client + compiled server)
```

## Tech Stack

- **Server:** Express.js + TypeScript
- **Client:** React 18 + TypeScript + Vite
- **Styling:** Plain CSS (no Tailwind -- direct control over dark theme)
- **Data:** better-sqlite3 (read-only), gray-matter (YAML), glob (file scanning)
- **Build:** Vite builds client to dist/, TypeScript compiles server, Express serves both
- **Process:** pm2 on Mac Mini

## Config (.env)

```bash
PORT=3200
VAULT_PATH=/Users/fastshower/Projects/K2B-Vault
K2B_DB_PATH=/Users/fastshower/Projects/K2B/k2b-remote/store/k2b-remote.db
K2B_HEALTH_PATH=/Users/fastshower/Projects/K2B/k2b-remote/store/health.json
K2B_PROJECT_PATH=/Users/fastshower/Projects/K2B
SKILLS_PATH=/Users/fastshower/Projects/K2B/.claude/skills
LEARNINGS_PATH=/Users/fastshower/.claude/projects/-Users-fastshower-Projects-K2B/memory/self_improve_learnings.md
# Note: verify exact Claude projects path on Mac Mini -- may differ from MacBook
USAGE_LOG_PATH=/Users/fastshower/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
POLL_INTERVAL_MS=30000
```

Note: Mac Mini paths use `/Users/fastshower/`, not `/Users/keithmbpm2/` (MacBook).

## Responsive Behavior

On screens < 768px (phone via Tailscale):
- All grid rows collapse to single column
- Panels stack vertically in priority order: System → Roadmap → YouTube → Intelligence → Skills → Inbox → Activity → Tasks → Pipeline
- YouTube Watch and Queue stack vertically instead of side-by-side
- Font size reduces slightly for density
- Expanded rows use full width

## Verification

1. Start the dashboard: `cd k2b-dashboard && npm run dev`
2. Verify each API endpoint returns valid data by hitting them directly
3. Confirm the React app renders all 10 panels with real data
4. Test click-to-expand on multiple panels
5. Test responsive layout by resizing browser to phone width
6. Build for production: `npm run build`
7. Verify Express serves the built client: `node dist/server/index.js`
8. Deploy to Mac Mini: rsync + pm2 start
9. Access via Tailscale from phone to confirm mobile layout

## v2 Considerations (not in scope)

- **Action buttons:** YouTube Watch/Skip, Inbox Promote/Archive/Delete, Intelligence Confirm/Dismiss -- require write-back to vault/JSONL via k2b-remote internal endpoint
- **WebSocket upgrade:** Live activity feed streaming
- **Quick Command input:** Send slash commands to k2b-remote
- **Vault graph mini-view:** Network visualization of recent note connections
- **Content calendar:** Visual timeline of planned/published content