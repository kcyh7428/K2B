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

### What works the same
- All K2B skills, slash commands, vault conventions from parent CLAUDE.md
- gws CLI (Gmail, Calendar) -- authenticated locally with file-based keyring
- YouTube API (OAuth token at `~/.config/k2b/youtube-token.json`)
- MiniMax API (`MINIMAX_API_KEY` in environment)
- LinkedIn publishing (`~/.linkedin_token`)

### What's different from MacBook
- Responses go to Telegram, not a terminal -- keep them concise
- No Obsidian GUI -- Keith sees vault changes via Obsidian on his MacBook/phone after Syncthing sync
- Claude Code memory path is `~/.claude/projects/-Users-fastshower-Projects-K2B/memory/` (not keithmbpm2)
- MCP servers may differ -- check `.mcp.json` if a tool isn't available

## Telegram Message Format

- Keep responses tight and readable -- max 4096 chars per message
- Use plain text over heavy markdown (Telegram renders markdown differently)
- For long outputs: summary first, offer to expand
- When saving to Obsidian, confirm briefly: "Saved to [path]. Linked to [[related]]."
- Voice messages arrive as `[Voice transcribed]: ...` -- treat as normal text, execute commands

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
