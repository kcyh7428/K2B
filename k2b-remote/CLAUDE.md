# K2B Remote -- Telegram Channel Context

This file supplements the parent `../CLAUDE.md`. Do not duplicate rules, vault structure, or slash commands from there.

## You Are Running on the Mac Mini

You are NOT on Keith's MacBook. You are running on the Mac Mini (`Matthews-Mac-mini.local`, user `fastshower`) via the Telegram bot (k2b-remote). This changes how you operate:

### Run everything locally
- All scripts, CLI tools, and file operations run LOCALLY on this machine
- Vault is at `~/Projects/K2B-Vault/` (synced to MacBook via Syncthing)
- Scripts are at `~/Projects/K2B/scripts/`
- Do NOT SSH to `macmini` or `Matthews-Mac-mini.local` -- you are already here
- `schedule-cli.js` runs locally: `cd ~/Projects/K2B/k2b-remote && node dist/schedule-cli.js <command>`

### Network: Tailscale SSH
- Mac Mini and MacBook are connected via Tailscale (set up 2026-03-30)
- Mac Mini Tailscale IP: `100.116.205.17`
- MacBook Tailscale IP: `100.68.35.19`
- SSH from MacBook to Mac Mini uses Tailscale (works from anywhere, not just LAN)
- Mac Mini runs Clash Verge in System Proxy mode (not TUN) to avoid conflict with Tailscale
- HTTP_PROXY env var is set when Clash proxy is active -- k2b-remote passes it to spawned agents

### What works the same
- All K2B skills, slash commands, vault conventions from parent CLAUDE.md
- gws CLI (Gmail, Calendar) -- authenticated locally with file-based keyring
- YouTube API (OAuth token at `~/.config/k2b/youtube-token.json`)
- MiniMax API (`MINIMAX_API_KEY` in environment)
- LinkedIn publishing (`~/.linkedin_token`)

### Skill discipline
- Before answering any question that matches a K2B skill (youtube, inbox, daily, email, etc.), invoke the Skill tool first. Do NOT answer from memory or local files when a skill exists for that domain.
- For real-time external data (playlists, emails, calendar), ALWAYS fetch live data. Never report content from cached files (like youtube-recommended.jsonl) as current playlist contents.
- When in doubt, use the skill. The skill knows the correct scripts, APIs, and data flow.

### What's different from MacBook
- Responses go to Telegram, not a terminal -- keep them concise
- No Obsidian GUI -- Keith sees vault changes via Obsidian on his MacBook/phone after Syncthing sync
- Claude Code memory path is `~/.claude/projects/-Users-fastshower-Projects-K2B/memory/` (not keithmbpm2)
- MCP servers may differ -- check `.mcp.json` if a tool isn't available

## Reminders & Scheduling

CRITICAL: NEVER use Claude Code's built-in scheduling tools (CronCreate, scheduled-tasks MCP, RemoteTrigger) for reminders or scheduled tasks. These are session-scoped and get wiped on pm2 restart.

ALWAYS use the persistent SQLite-backed scheduler via schedule-cli.js:

### One-time reminders
```bash
cd ~/Projects/K2B/k2b-remote && node dist/schedule-cli.js create-once "<prompt>" "YYYY-MM-DD HH:MM" 8394008217
```

### Recurring tasks
```bash
cd ~/Projects/K2B/k2b-remote && node dist/schedule-cli.js create "<prompt>" "<cron>" 8394008217
```

### Other commands
```bash
node dist/schedule-cli.js list          # List all tasks
node dist/schedule-cli.js delete <id>   # Delete a task
node dist/schedule-cli.js pause <id>    # Pause a task
node dist/schedule-cli.js resume <id>   # Resume a task
```

- Chat ID for Keith: `8394008217`
- Datetime format: "YYYY-MM-DD HH:MM" in HKT (UTC+8)
- The prompt is what the scheduler will execute via Claude agent when the time comes
- For reminders, use a simple prompt like: "Send Keith a Telegram message: Reminder -- [description]"

## Telegram Message Format

You are writing for a phone screen. Every response will be read on mobile Telegram.

### Structure
- Lead with the answer or status in 1-2 lines
- Use short paragraphs (2-3 lines max)
- For long outputs: summary first, then "Want details on any of these?"
- Max 4096 chars per message (hard Telegram limit)

### Do
- Bullet lists for multiple items
- Bold for emphasis (`**key point**`)
- Short inline code for filenames/commands
- Numbered lists when order matters
- Emoji sparingly as visual anchors (checkmarks, warnings)

### Don't
- NO tables -- they break on mobile. Use bullet lists instead
- NO wide code blocks (anything over ~40 chars per line wraps badly)
- NO ASCII art, horizontal rules, or box-drawing characters
- NO dense multi-column layouts
- NO long unbroken paragraphs

### When reporting structured data (e.g., audit results, comparisons)
Instead of a table like:
  | Feature | Status | Notes |

Use a list:
  **Feature A** -- Done. Notes here.
  **Feature B** -- Pending. Needs X.

### Confirmations
- Vault writes: "Saved to [path]. Linked to [[related]]."
- Commands: "Done." or one-line summary
- Voice messages arrive as `[Voice transcribed]: ...` -- treat as normal text

## Telegram Session Management

### `convolife`
Check remaining context window:
1. Find latest session JSONL: `~/.claude/projects/` + project path with slashes to hyphens
2. Get last cache_read_input_tokens value
3. Calculate: used / 200000 * 100
4. Report: "Context window: XX% used -- ~XXk tokens remaining"

### `checkpoint`
Save session summary to SQLite:
1. Write 3-5 bullet summary of key decisions/findings
2. Confirm: "Checkpoint saved. Safe to /newchat."

## Memory

Context persists via Claude Code session resumption.
You don't need to re-introduce yourself each message.
