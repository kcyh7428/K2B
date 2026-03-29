---
name: k2b-sync
description: Sync K2B project files to the Mac Mini server -- detects what changed, syncs skills/code/scripts, rebuilds if needed. Use when Keith says /sync, "sync to mini", "deploy to mini", "push to mini", or after K2B modifies project files (skills, CLAUDE.md, k2b-remote code, scripts).
---

# K2B Sync to Mac Mini

Push K2B project file changes from MacBook to the always-on Mac Mini server.

## When to Trigger

**Explicitly:** Keith says `/sync`, "sync to mini", "deploy", "push to mini".

**Proactively prompt Keith** at the end of any session where K2B modified files in:
- `.claude/skills/` (any skill SKILL.md)
- `CLAUDE.md` or `K2B_ARCHITECTURE.md`
- `k2b-remote/` (bot code)
- `scripts/` (utility scripts)

Say: "Changes were made to [list]. These are on your MacBook only. Run /sync to push to Mac Mini?"

Do NOT auto-sync without Keith's confirmation. Always ask first.

## Commands

- `/sync` -- auto-detect what changed and sync it
- `/sync skills` -- sync only skills + CLAUDE.md + K2B_ARCHITECTURE.md
- `/sync code` -- sync only k2b-remote code (+ build + restart pm2)
- `/sync scripts` -- sync only scripts/
- `/sync all` -- force full sync of everything
- `/sync status` -- check what's out of sync without changing anything

## Paths

- Deploy script: `~/Projects/K2B/scripts/deploy-to-mini.sh`
- Mac Mini SSH alias: `macmini`
- Remote K2B project: `~/Projects/K2B/` on Mac Mini

## Workflow

### 1. Detect Changes

**Primary method: Use conversation context.** K2B knows which files it modified in the current session. List exactly those files -- don't scan the whole repo.

**If context is unclear** (e.g., Keith runs `/sync` in a fresh session), use rsync dry-run to compare MacBook vs Mac Mini:
```bash
# Skills + config
rsync -avn --delete \
  ~/Projects/K2B/.claude/skills/ macmini:~/Projects/K2B/.claude/skills/ 2>&1 | grep -E "^(sending|deleting|\.claude)"

rsync -avn ~/Projects/K2B/CLAUDE.md ~/Projects/K2B/K2B_ARCHITECTURE.md \
  macmini:~/Projects/K2B/ 2>&1 | grep -v "^$"

# Scripts
rsync -avn ~/Projects/K2B/scripts/ macmini:~/Projects/K2B/scripts/ 2>&1 | grep -E "^(sending|deleting|scripts)"

# Code
rsync -avn --exclude node_modules --exclude dist \
  ~/Projects/K2B/k2b-remote/ macmini:~/Projects/K2B/k2b-remote/ 2>&1 | grep -E "^(sending|deleting|k2b-remote)"
```

This compares actual file contents between machines -- not git state. Only files that genuinely differ will show up.

**Do NOT use `git diff --name-only HEAD`** -- that shows all uncommitted changes since last commit, including files already synced in previous sessions. It produces false positives.

### 2. Categorize and Summarize

Group changed files into categories:

| Category | Matched Paths | Needs Build? |
|----------|--------------|-------------|
| skills | `.claude/skills/`, `CLAUDE.md`, `K2B_ARCHITECTURE.md` | No |
| code | `k2b-remote/` | Yes -- npm run build + pm2 restart |
| scripts | `scripts/` | No |

Show Keith a summary of only the files that actually differ:
```
Out of sync with Mac Mini:
  - .claude/skills/k2b-media-generator/SKILL.md
  - .claude/skills/k2b-youtube-capture/SKILL.md
  Category: skills

Sync to Mac Mini?
```

### 3. Execute Sync

For `/sync status` or when Keith wants to preview:
```bash
~/Projects/K2B/scripts/deploy-to-mini.sh --dry-run
```

For actual sync, run the deploy script with the appropriate mode:
```bash
~/Projects/K2B/scripts/deploy-to-mini.sh auto
```

Or with explicit category:
```bash
~/Projects/K2B/scripts/deploy-to-mini.sh skills
~/Projects/K2B/scripts/deploy-to-mini.sh code
~/Projects/K2B/scripts/deploy-to-mini.sh all
```

### 4. Verify

After sync completes, verify:

**For skills:**
```bash
ssh macmini "head -3 ~/Projects/K2B/CLAUDE.md"
ssh macmini "ls ~/Projects/K2B/.claude/skills/ | wc -l"
```

**For code:**
```bash
ssh macmini "pm2 status"
```

**For both:** Report what was synced and any warnings.

### 5. Report

Tell Keith:
- What categories were synced
- How many files transferred
- Verification results (skill count match, pm2 status)
- Any errors or warnings

## Error Handling

- **Mac Mini unreachable**: "Can't reach Mac Mini via SSH. Is it on the network?"
- **Build failure**: Show the npm error output. Don't restart pm2 if build failed.
- **pm2 restart failure**: Show pm2 logs. Suggest `ssh macmini "pm2 logs k2b-remote --lines 30 --nostream"`
- **No changes detected**: "No syncable changes found. Use `/sync all` to force a full sync."

## What Does NOT Sync

- **Vault** (`K2B-Vault/`) -- handled by Syncthing, not this skill
- **node_modules/** and **dist/** -- excluded from rsync, rebuilt on Mini
- **store/** -- production SQLite database lives on Mac Mini, NEVER overwrite from MacBook
- **.env** -- environment config stays local to each machine
- **.git/** -- each machine has its own git state

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-sync\t$(echo $RANDOM | md5sum | head -c 8)\tsynced CATEGORY to mac mini" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes

- Always confirm with Keith before syncing. Never auto-sync.
- The deploy script handles SSH connectivity checks.
- Code changes (k2b-remote) require build + restart. Skills don't.
- If Keith is iterating fast on k2b-remote, suggest batching changes before syncing.
