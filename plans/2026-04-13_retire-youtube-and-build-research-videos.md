# Retire YouTube Agent + Build /research videos Implementation Plan

**Spec:** `K2B-Vault/wiki/concepts/feature_research-videos-notebooklm.md`

## Context

The YouTube agent in `k2b-remote` has been a source of bugs from day one — session contamination, keyword routing leaks (the "show me both" bug), forced-choice hallucinations, TOCTOU races. Phase 1 of the `session-design-v3` refactor just shipped on `main` (fixes the routing bug via tools + mutex), but the underlying model is still wrong: "K2B runs a background agent that decides what videos to surface and nags Keith via Telegram."

Keith has decided to retire the whole concept and replace it with an on-demand / scheduled skill powered by NotebookLM. NotebookLM reads full video content via Gemini at zero token cost, filters by Keith's stated preferences, and returns a JSON verdict per video. K2B adds the suitable ones to the K2B Watch playlist, drops per-video review notes into the vault, and sends a Telegram notification. Keith watches on mobile at his own pace and feeds reactions back via Obsidian edits or free-form Telegram messages. `/review` distills reactions into a persistent preference log that the next run consults. No background loop, no routing, no mutex, no taste-model math.

This plan has two phases. **Phase A retires the YouTube agent code** (one PR, deploys to Mac Mini, validates clean boot). **Phase B builds `/research videos`** as a new subcommand in the existing `k2b-research` skill. Phase B assumes a clean base — Phase A must land and deploy first.

## Architecture Overview

- **Phase A:** Delete whole files (`youtube.ts`, `youtube-agent-loop.ts`, `taste-model.ts`), strip YouTube MCP tools + callbacks from `agent.ts` / `bot.ts`, drop the `youtube_agent_state` table from `db.ts`, remove `async-mutex` from `package.json`, delete three vault state files, update the `feature_youtube-agent.md` feature note to mark Phase 4 shipped. Keep `scripts/yt-playlist-add.sh`, `scripts/yt-playlist-remove.sh`, `scripts/yt-search.py`, the YouTube OAuth token at `~/.config/k2b/youtube-token.json`, the K2B Watch playlist on YouTube itself, and the `k2b-youtube-capture` skill (still batches playlist videos into `raw/youtube/`).
- **Phase B:** Add `/research videos "<query>"` flow to `k2b-research/SKILL.md`. One new script (`scripts/send-telegram.sh`, ~15 lines of curl). Update `k2b-review/SKILL.md` with a handler for `review/video_*.md` notes. Add a rule block to the project root `CLAUDE.md` for the Telegram feedback path (no new code — the interactive Claude session already has Edit/Glob/Grep). Seed an empty `wiki/context/video-preferences.md`.

## Critical Files (reference)

### Phase A — delete or strip

| File | Action |
|---|---|
| `k2b-remote/src/youtube.ts` | Delete whole file |
| `k2b-remote/src/youtube-agent-loop.ts` | Delete whole file |
| `k2b-remote/src/taste-model.ts` | Delete whole file (only used by YouTube) |
| `k2b-remote/src/agent.ts` | Strip YouTube MCP tools, strip `runStatelessQuery` (dead once loop is gone), strip `mcpServers: { 'k2b-youtube': ... }` from `runAgent` |
| `k2b-remote/src/bot.ts` | Strip all YouTube imports, all YouTube button callbacks (`watch`, `screen`, `skip-confirm`, `agent-add`, `screen-process`, `screen-skip`, `screen-all`), `handleCommentOrSkipReason` if YouTube-only, any remaining keyword-router residue, orphaned `handleDirectYouTubeUrl` if present |
| `k2b-remote/src/db.ts` | Drop `youtube_agent_state` table creation + `getYouTubeAgentState` / `upsertYouTubeAgentState` / `resetYouTubeAgentState` exports. Keep the `sessions` table migration from Phase 1 (single PK on `chat_id`, `scope` removed). |
| `k2b-remote/src/index.ts` | Remove any `startYouTubeAgentLoop` invocation |
| `k2b-remote/src/config.ts` | Remove any YouTube-specific config keys (WATCH_PLAYLIST_ID / SCREEN_PLAYLIST_ID if referenced here — they currently live in `youtube.ts`) |
| `k2b-remote/package.json` + `package-lock.json` | Remove `async-mutex` dependency |
| `K2B-Vault/wiki/context/youtube-recommended.jsonl` | Delete |
| `K2B-Vault/wiki/context/youtube-feedback-signals.jsonl` | Delete |
| `K2B-Vault/wiki/context/youtube-preference-profile.md` | Delete |
| `K2B-Vault/wiki/concepts/feature_youtube-agent.md` | Update: flip Phase 4 row in Shipping Status to `shipped`, set feature `status: retired`, add retirement date |

### Phase A — keep unchanged

- `scripts/yt-playlist-add.sh`, `scripts/yt-playlist-remove.sh`, `scripts/yt-playlist-poll.sh` — reused by Phase B.
- `scripts/yt-search.py` — still used by `/research deep <topic>`.
- `~/.config/k2b/youtube-token.json` — OAuth token for playlist writes.
- `k2b-youtube-capture` skill (`.claude/skills/k2b-youtube-capture/`) — batch playlist processor, not agent.
- The K2B Watch playlist on YouTube itself (`PLg0PUkz5itjwIXWVuSlvxud0ZR2JBsacX`).

### Phase B — new or modify

| File | Action |
|---|---|
| `scripts/send-telegram.sh` | New. ~15 lines, curl POST to Telegram Bot API. Uses `K2B_BOT_TOKEN` from env, `K2B_CHAT_ID` (defaults to `8394008217`). Takes message text as arg, supports `--file` flag for piping. |
| `.claude/skills/k2b-research/SKILL.md` | Add `/research videos "<query>"` section: baked Keith framing, filter prompt template, JSON schema, flow steps, output file conventions. |
| `.claude/skills/k2b-review/SKILL.md` | Add `review/video_*.md` handler section: read all non-pending items, distill each into one line appended to `wiki/context/video-preferences.md`, then delete the review note. |
| `CLAUDE.md` (project root) | Add "Video Feedback via Telegram" rule block: glob `review/video_*.md`, read to match reference, Edit frontmatter + notes, no confirmation, reply in Telegram confirming the update. |
| `K2B-Vault/wiki/context/video-preferences.md` | Create empty file with frontmatter and a "First run hasn't happened yet" placeholder. This is the persistent preference log. |

## Tech Stack

- **Retirement (Phase A):** TypeScript (`tsc --noEmit` clean), Node.js, existing `k2b-remote` stack, `pm2` for Mac Mini process management.
- **Build (Phase B):** Bash (`send-telegram.sh`), `notebooklm-py` CLI (already installed and authenticated on MacBook), existing K2B skill infrastructure (`.claude/skills/*.md` markdown rules), existing `scripts/yt-playlist-add.sh` for playlist writes.

---

# PHASE A — Retire YouTube Agent

**Goal:** Land one PR that removes every trace of the YouTube agent from `k2b-remote` and the vault while keeping the K2B Watch playlist, the `k2b-youtube-capture` skill, and the OAuth token alive for Phase B.

**Branch:** Work on `main` directly (K2B convention — the `feature_youtube-agent.md` feature note tracks this as Phase 4).

**Commit discipline:** One commit per task below. Each task leaves `tsc --noEmit` and `npm run build` clean. Frequent commits so rollback is granular if something unexpected surfaces on Mac Mini.

## Task A1: Read current k2b-remote YouTube surface area

**Files to read:**
- `k2b-remote/src/youtube.ts` (full file — understand every export)
- `k2b-remote/src/youtube-agent-loop.ts` (full file)
- `k2b-remote/src/taste-model.ts` (confirm it's YouTube-only)
- `k2b-remote/src/agent.ts` (find `k2bYoutubeToolsServer`, YouTube tools, `runStatelessQuery`, and the `mcpServers` block in `runAgent`)
- `k2b-remote/src/bot.ts` (find every `await ytMutex.runExclusive`, every YouTube button callback, every YouTube import)
- `k2b-remote/src/db.ts` (find `youtube_agent_state` table creation, `getYouTubeAgentState` / `upsertYouTubeAgentState` / `resetYouTubeAgentState`)
- `k2b-remote/src/index.ts` (find YouTube loop startup)
- `k2b-remote/src/config.ts` (find any YouTube keys)

**Why:** Before deleting anything, confirm the exact footprint and build a mental map of the dependency graph. The handoff doc from the Phase 1 session has most of this but it's stale relative to committed `main`.

- [ ] **Step 1:** Read each file above with the Read tool, tracking: every symbol exported from YouTube files, every caller of those symbols, every import of YouTube modules from non-YouTube files.
- [ ] **Step 2:** Write a one-paragraph "deletion map" into your scratch notes listing every call site that will become an orphan once the YouTube files are deleted. This is your checklist for Tasks A3–A6.

## Task A2: Baseline — confirm current build is clean

- [ ] **Step 1:** `cd k2b-remote && npm run build`
   Expected: clean exit. If not clean, stop and investigate — the base is not what we think it is.
- [ ] **Step 2:** `cd k2b-remote && npx tsc --noEmit`
   Expected: clean exit.
- [ ] **Step 3:** `git status` — expected: clean working tree (Phase 1 is committed and pushed per Keith's report).
- [ ] **Step 4:** `git log --oneline -5` — confirm the last commit is the Phase 1 ship.

## Task A3: Delete whole files — youtube.ts, youtube-agent-loop.ts, taste-model.ts

**Files:** Delete three files.

- [ ] **Step 1:** `rm k2b-remote/src/youtube.ts k2b-remote/src/youtube-agent-loop.ts k2b-remote/src/taste-model.ts`
- [ ] **Step 2:** `cd k2b-remote && npx tsc --noEmit`
   Expected: **many errors** — every file that imports from these three modules now fails. That's the todo list for Tasks A4 and A5. Capture the error output for reference.
- [ ] **Step 3:** Do **not commit yet**. The build is broken and will stay broken until A4 and A5 land. Commit the whole retirement as one atomic change.

## Task A4: Strip YouTube code from `k2b-remote/src/agent.ts`

**Files:** Modify `k2b-remote/src/agent.ts`.

Phase 1 registered `k2bYoutubeToolsServer` via `createSdkMcpServer` with 5 tools (`youtube_get_pending`, `youtube_add_to_watch`, `youtube_skip`, `youtube_keep`, `youtube_swap_all`), imported `guardedAddVideoToWatch` / `guardedSkipVideoFromWatch` / `guardedKeepVideo` / `guardedSwapAll` from `youtube.ts`, and added `runStatelessQuery` for the background loop classifiers.

- [ ] **Step 1:** Remove every import from `./youtube.js` at the top of `agent.ts`.
- [ ] **Step 2:** Remove the entire `k2bYoutubeToolsServer` declaration + its `createSdkMcpServer` block + all 5 tool handlers.
- [ ] **Step 3:** Remove `mcpServers: { 'k2b-youtube': k2bYoutubeToolsServer }` from every `runAgent` call (or remove the key from the options object passed to `query`).
- [ ] **Step 4:** Remove the `runStatelessQuery` export entirely. It was only used by the YouTube loop's background classifiers. If it has no other callers (confirm with grep), delete it. If grep finds callers in non-YouTube code, keep it — but that's unlikely.
- [ ] **Step 5:** `cd k2b-remote && npx tsc --noEmit` — confirm `agent.ts` is now clean (may still have errors elsewhere from Task A3 fallout).

## Task A5: Strip YouTube code from `k2b-remote/src/bot.ts`

**Files:** Modify `k2b-remote/src/bot.ts`.

Phase 1 wrapped every YouTube mutation in `ytMutex.runExclusive` and left the button callbacks themselves intact. All of that is now going away.

- [ ] **Step 1:** Remove every import from `./youtube.js` and `./youtube-agent-loop.js` at the top of `bot.ts`.
- [ ] **Step 2:** Remove every `bot.callbackQuery(/...watch.../)` / `screen` / `skip-confirm` / `agent-add` / `screen-process` / `screen-skip` / `screen-all` handler. Walk the file top to bottom — if the handler body references any YouTube symbol or reads `youtube-recommended.jsonl`, it's YouTube.
- [ ] **Step 3:** Remove `handleCommentOrSkipReason` (the text-reply path Phase 1's round 4 Codex review flagged and wrapped). It was YouTube-only.
- [ ] **Step 4:** Remove any `handleDirectYouTubeUrl` or `handleYouTubeAgentResponse` residue (Phase 1 deleted most of this but grep to be sure).
- [ ] **Step 5:** `cd k2b-remote && npx tsc --noEmit` — confirm `bot.ts` is clean.

## Task A6: Strip YouTube tables + helpers from `k2b-remote/src/db.ts`

**Files:** Modify `k2b-remote/src/db.ts`.

The `youtube_agent_state` table was added in Phase 1 (v3 migration) as the SQLite-backed home for agent loop state. Dead now.

- [ ] **Step 1:** Delete the `CREATE TABLE IF NOT EXISTS youtube_agent_state` block in the schema initialization.
- [ ] **Step 2:** Delete `getYouTubeAgentState`, `upsertYouTubeAgentState`, `resetYouTubeAgentState` exports.
- [ ] **Step 3:** Delete the `YouTubeAgentStateRow` interface.
- [ ] **Step 4:** Add a **v3 → v4 migration** block that runs `DROP TABLE IF EXISTS youtube_agent_state` on existing databases, so the Mac Mini's live DB loses the orphan table on next boot. Put it next to the v2 → v3 sessions migration Phase 1 added. Bump the internal schema version marker from 3 to 4.
- [ ] **Step 5:** Keep the sessions table as-is (single PK on `chat_id`, no `scope` column). That's Phase 1's permanent win.
- [ ] **Step 6:** `cd k2b-remote && npx tsc --noEmit` — confirm `db.ts` is clean.

## Task A7: Strip YouTube startup + config residue

**Files:** Modify `k2b-remote/src/index.ts` and `k2b-remote/src/config.ts`.

- [ ] **Step 1:** In `index.ts`, remove any `startYouTubeAgentLoop()` call and its import.
- [ ] **Step 2:** In `config.ts`, remove any `WATCH_PLAYLIST_ID` / `SCREEN_PLAYLIST_ID` / YouTube-specific keys if they live here. (They may actually live inside `youtube.ts` which we already deleted — verify with grep.)
- [ ] **Step 3:** Grep the whole `k2b-remote/src/` directory for residue: `rg "youtube|Youtube|YouTube|yt-|ytMutex|guarded(Add|Skip|Keep|Swap)|taste-?model|youtube_agent_state" k2b-remote/src -n` — every match should be either a filename containing "youtube" that we've already decided to keep (there shouldn't be any in `src/`) or a harmless string literal (log message). If you find live code references, fix them here.
- [ ] **Step 4:** `cd k2b-remote && npx tsc --noEmit` — **must be clean now**. If not, go back to the grep.
- [ ] **Step 5:** `cd k2b-remote && npm run build` — must be clean.

## Task A8: Remove `async-mutex` dependency

**Files:** `k2b-remote/package.json`, `k2b-remote/package-lock.json`.

- [ ] **Step 1:** `cd k2b-remote && npm uninstall async-mutex`
- [ ] **Step 2:** Verify `package.json` no longer lists `async-mutex`.
- [ ] **Step 3:** `cd k2b-remote && npm run build` — clean.

## Task A9: Delete vault state files

**Files:** Three files in the vault.

- [ ] **Step 1:** `rm K2B-Vault/wiki/context/youtube-recommended.jsonl`
- [ ] **Step 2:** `rm K2B-Vault/wiki/context/youtube-feedback-signals.jsonl`
- [ ] **Step 3:** `rm K2B-Vault/wiki/context/youtube-preference-profile.md`
- [ ] **Step 4:** Grep `wiki/` for any wikilinks pointing at these files: `rg "youtube-recommended|youtube-feedback-signals|youtube-preference-profile" K2B-Vault/wiki -n`. Fix or remove the links.

## Task A10: Update `feature_youtube-agent.md` to retired

**Files:** `K2B-Vault/wiki/concepts/feature_youtube-agent.md`.

- [ ] **Step 1:** Flip frontmatter `status: in-progress` → `status: retired`. Add `retired-date: 2026-04-13` (or the real date of execution).
- [ ] **Step 2:** In the Shipping Status table, flip the Phase 4 row from `designed` to `shipped` with today's date.
- [ ] **Step 3:** Add a short "Retirement notes" section at the bottom: one paragraph linking to `feature_research-videos-notebooklm.md` as the replacement, one bullet listing what was kept (K2B Watch playlist, `k2b-youtube-capture` skill, OAuth token, `yt-*` shell scripts, `yt-search.py`).
- [ ] **Step 4:** Move the file under `wiki/concepts/Shipped/` per CLAUDE.md convention, OR leave it in `wiki/concepts/` if Keith prefers retired features to stay visible. Default: leave in place.
- [ ] **Step 5:** Update `wiki/concepts/index.md` lane membership: remove `feature_youtube-agent` from any active lane, add to Shipped-Recent-10.

## Task A11: Commit Phase A

- [ ] **Step 1:** `git status` — expect deletions of three .ts files, modifications to agent.ts/bot.ts/db.ts/index.ts/config.ts, modifications to package.json/package-lock.json, deletions of three vault files, modification to feature_youtube-agent.md and wiki/concepts/index.md.
- [ ] **Step 2:** Run the verification grep one more time: `rg "ytMutex|youtube_agent_state|guardedAddVideoToWatch|handleYouTubeAgentResponse|parseAndExecuteActions|taste-?model" k2b-remote/src K2B-Vault/wiki -n` — expected: zero matches (or only matches in `feature_youtube-agent.md` retirement notes, which is fine).
- [ ] **Step 3:** Stage specifically — do NOT `git add -A`. List files explicitly:
   ```
   git add k2b-remote/src/agent.ts k2b-remote/src/bot.ts k2b-remote/src/db.ts k2b-remote/src/index.ts k2b-remote/src/config.ts k2b-remote/package.json k2b-remote/package-lock.json
   git rm k2b-remote/src/youtube.ts k2b-remote/src/youtube-agent-loop.ts k2b-remote/src/taste-model.ts
   git add K2B-Vault/wiki/concepts/feature_youtube-agent.md K2B-Vault/wiki/concepts/index.md
   git rm K2B-Vault/wiki/context/youtube-recommended.jsonl K2B-Vault/wiki/context/youtube-feedback-signals.jsonl K2B-Vault/wiki/context/youtube-preference-profile.md
   ```
- [ ] **Step 4:** Commit with message:
   ```
   feat(k2b-remote): retire youtube agent (phase 4 of feature_youtube-agent)

   Delete youtube.ts, youtube-agent-loop.ts, taste-model.ts wholesale.
   Strip youtube MCP tools, button callbacks, DB table, async-mutex dep.
   Keep scripts/yt-playlist-*.sh, yt-search.py, k2b-youtube-capture skill,
   YouTube OAuth token, K2B Watch playlist. Replacement is
   feature_research-videos-notebooklm (Phase B, separate PR).

   Co-Authored-By: Claude <noreply@anthropic.com>
   ```
- [ ] **Step 5:** `git log --oneline -3` — confirm the commit landed.

## Task A12: Deploy Phase A to Mac Mini

The retirement touches `k2b-remote` which runs under pm2 on the Mac Mini. This is NOT a vault-only change — it requires sync.

- [ ] **Step 1:** `git push origin main` (so the Claude.ai project also sees the latest).
- [ ] **Step 2:** Run `/sync` or `scripts/deploy-to-mini.sh auto`. This handles the file copy.
- [ ] **Step 3:** SSH to Mac Mini and take a DB snapshot before restarting pm2:
   ```
   ssh macmini "cp ~/Projects/K2B/k2b-remote/tasks.db ~/Projects/K2B/k2b-remote/tasks.db.pre-retire-youtube-$(date +%s)"
   ```
   This is the rollback point if anything goes wrong.
- [ ] **Step 4:** SSH to Mac Mini, rebuild, restart:
   ```
   ssh macmini "cd ~/Projects/K2B/k2b-remote && npm install && npm run build && pm2 restart k2b-remote"
   ```
- [ ] **Step 5:** Watch logs for the first minute:
   ```
   ssh macmini "pm2 logs k2b-remote --lines 50 --nostream"
   ```
   Expected: clean startup, sessions table loaded, v3→v4 migration ran successfully and dropped `youtube_agent_state`, no "module not found" or "unknown tool" errors.
- [ ] **Step 6:** Smoke test from Keith's phone: send a normal Telegram message to the bot ("hello" or "what's on today"). Expected: bot replies normally via the interactive Claude session. No YouTube errors in logs.
- [ ] **Step 7:** Verify `k2b-observer-loop` (the other pm2 process) is still running cleanly: `ssh macmini "pm2 status"`.

## Task A13: Append devlog + wiki/log entry for Phase A

- [ ] **Step 1:** Append to `DEVLOG.md`:
   ```
   ## 2026-04-13 — k2b-remote: retire youtube agent (feature_youtube-agent Phase 4)
   Delete youtube.ts, youtube-agent-loop.ts, taste-model.ts wholesale. Strip youtube MCP tools (5), button callbacks, youtube_agent_state DB table, async-mutex dep, vault state files. Keep yt-playlist scripts, yt-search.py, k2b-youtube-capture skill, OAuth token. Replacement skill /research videos lands in Phase B. Deployed to Mac Mini at HH:MM HKT, pm2 restart clean, smoke test green.
   ```
- [ ] **Step 2:** Append to `K2B-Vault/wiki/log.md` with the same one-paragraph entry.
- [ ] **Step 3:** Commit both:
   ```
   git add DEVLOG.md K2B-Vault/wiki/log.md
   git commit -m "docs: devlog for youtube agent retirement"
   git push origin main
   ```

**Phase A checkpoint:** Mac Mini is running k2b-remote with zero YouTube code. Keith can chat with the bot normally. No scheduled jobs are hitting the dead `youtube-agent-loop`. The K2B Watch playlist still exists on YouTube but nothing writes to it. Safe to move to Phase B.

---

# PHASE B — Build /research videos subcommand

**Goal:** Add `/research videos "<query>"` to the existing `k2b-research` skill. End-to-end: NotebookLM deep research → JSON filter answer → playlist write → review notes → Telegram notification → run record in `raw/research/`. Plus `/review` processing and the `CLAUDE.md` rule for Telegram feedback.

**Prerequisite:** Phase A deployed and validated (Task A12 clean).

## Task B1: Write `scripts/send-telegram.sh`

**Files:**
- Create: `scripts/send-telegram.sh`

- [ ] **Step 1:** Create the script:
   ```bash
   #!/usr/bin/env bash
   # Send a plain text message to Keith via Telegram Bot API.
   # Usage: scripts/send-telegram.sh "message text"
   #        scripts/send-telegram.sh --file path/to/message.txt
   # Env:   K2B_BOT_TOKEN (required), K2B_CHAT_ID (defaults to 8394008217)

   set -euo pipefail

   : "${K2B_BOT_TOKEN:?K2B_BOT_TOKEN env var not set}"
   CHAT_ID="${K2B_CHAT_ID:-8394008217}"

   if [[ "${1:-}" == "--file" ]]; then
     [[ -f "$2" ]] || { echo "file not found: $2" >&2; exit 1; }
     TEXT="$(cat "$2")"
   else
     TEXT="${1:?message text required}"
   fi

   curl -fsS -X POST "https://api.telegram.org/bot${K2B_BOT_TOKEN}/sendMessage" \
     -d "chat_id=${CHAT_ID}" \
     --data-urlencode "text=${TEXT}" \
     -d "parse_mode=Markdown" \
     -d "disable_web_page_preview=false"
   ```
- [ ] **Step 2:** `chmod +x scripts/send-telegram.sh`
- [ ] **Step 3:** Test it (requires `K2B_BOT_TOKEN` in env):
   ```
   K2B_BOT_TOKEN="$(grep K2B_BOT_TOKEN k2b-remote/.env | cut -d= -f2)" scripts/send-telegram.sh "test from /research videos plan"
   ```
   Expected: message arrives on Keith's phone within seconds.
- [ ] **Step 4:** Commit:
   ```
   git add scripts/send-telegram.sh
   git commit -m "feat(scripts): add send-telegram.sh for text notifications"
   ```

## Task B2: Verify `yt-playlist-add.sh` still works post-retirement

This is a sanity check — Phase A was supposed to leave this script alone, but we need to confirm.

- [ ] **Step 1:** `ls -la scripts/yt-playlist-add.sh` — expect it exists and is executable.
- [ ] **Step 2:** Read the script to understand its arg shape: `cat scripts/yt-playlist-add.sh`. Note whether it takes `(playlist_id, video_id)` positional or flag args.
- [ ] **Step 3:** Run a dry smoke test — can you invoke it with `--help` or `-h`? If it has no help flag, skip this step; we'll test end-to-end in B9.

## Task B3: Seed `wiki/context/video-preferences.md`

**Files:**
- Create: `K2B-Vault/wiki/context/video-preferences.md`

- [ ] **Step 1:** Create the file:
   ```markdown
   ---
   type: preference-log
   domain: video-recommendations
   up: "[[Home]]"
   ---

   # Video Preferences

   Rolling preference log consulted by `/research videos` on every run. One line per distilled preference, most recent at bottom. `/review` appends here after Keith rates videos.

   Format: `YYYY-MM-DD <action>: <channel or title> — <distilled one-sentence reason>`

   ## Preferences

   <!-- No preferences yet. First /research videos run will bootstrap. -->
   ```
- [ ] **Step 2:** Commit:
   ```
   git add K2B-Vault/wiki/context/video-preferences.md
   git commit -m "feat(vault): seed video-preferences.md for /research videos"
   ```

## Task B4: Extend `k2b-research` skill with `/research videos` subcommand

**Files:**
- Modify: `.claude/skills/k2b-research/SKILL.md`

Add a new top-level section after the existing `/research deep` section. The content below is the full text to paste in — no placeholders.

- [ ] **Step 1:** Open `.claude/skills/k2b-research/SKILL.md` and add the following section:

   ```markdown
   ## `/research videos "<query>"` — on-demand video discovery via NotebookLM

   Retires the old YouTube recommend agent. Finds videos matching a query, filters them via NotebookLM using Keith's baked framing + the tail of `wiki/context/video-preferences.md`, adds suitable ones to the K2B Watch playlist, drops per-video review notes, sends a Telegram notification.

   **Prerequisites:** `notebooklm auth check --test` passes. `K2B_BOT_TOKEN` is set in env.

   ### Baked Keith framing (do not edit per query)

   > Senior TA leader running AI transformation in a large traditional corporate (SJM Resorts, Macau). Also operates Signhub Tech (HK), TalentSignals, Agency at Scale. Content angle: showing how senior executives in traditional corporates use AI to 10x effectiveness. Prefer content creators with clear concrete examples over academic papers. Prefer actionable over theoretical. Prefer deployable in 90 days over visionary. Skip pure hype, skip thumbnails with "SHOCKING" / "INSANE", skip anything under 3 minutes, skip Chinese-only content unless specifically requested.

   ### Flow

   1. **Create fresh notebook:**
      ```bash
      NB_ID=$(notebooklm create "Videos: <query>" --json | jq -r '.id')
      ```

   2. **Deep research on the query:**
      ```bash
      notebooklm source add-research "<query>" --mode deep --no-wait -n "$NB_ID"
      ```

   3. **Wait for research + source indexing** via subagent pattern (see notebooklm skill docs). Use timeout 1800s.

   4. **Read the preference tail** into a variable:
      ```bash
      PREF_TAIL=$(tail -n 30 ~/Projects/K2B-Vault/wiki/context/video-preferences.md | sed 's/"/\\"/g')
      ```

   5. **Ask NotebookLM with the JSON filter prompt** (below), capture the answer:
      ```bash
      notebooklm ask "$(cat <<EOF
      From the sources in this notebook, list every YouTube video and classify it.

      Return JSON: [{"url", "title", "channel", "duration", "suitable": true|false, "why"}].

      Definition of "suitable" — this is for Keith Cheung:

      Senior TA leader running AI transformation in a large traditional corporate (SJM Resorts, Macau). Also operates Signhub Tech (HK), TalentSignals, Agency at Scale. Content angle: showing how senior executives in traditional corporates use AI to 10x effectiveness. Prefer content creators with clear concrete examples over academic papers. Prefer actionable over theoretical. Prefer deployable in 90 days over visionary. Skip pure hype, skip thumbnails with "SHOCKING" / "INSANE", skip anything under 3 minutes, skip Chinese-only content unless specifically requested.

      Recent feedback from Keith to consider (most recent at bottom):

      $PREF_TAIL

      For each video, set suitable=true only if it matches the framing AND does not contradict recent feedback. For suitable=true videos, "why" should be a single sentence explaining the relevance hook (what specifically Keith will get out of watching). For suitable=false videos, "why" should be a single sentence explaining what disqualified it.

      Return ONLY the JSON array, no prose before or after.
      EOF
      )" -n "$NB_ID"
      ```

   6. **Parse the JSON answer defensively.** If the answer has prose around the JSON, extract the array with `jq` or a Python one-liner. If parse fails, retry the ask once with a stricter prompt. If second parse fails, log raw answer to the run record, notify Keith the run failed parsing, abort the playlist/review-note steps.

   7. **For each `suitable: true` entry:**
      a. Extract `video_id` from the URL (the `v=` query param, or last path segment for `youtu.be/`).
      b. Call `scripts/yt-playlist-add.sh "PLg0PUkz5itjwIXWVuSlvxud0ZR2JBsacX" "$VIDEO_ID"`. Log success/failure per video.
      c. Create review note at `K2B-Vault/review/video_$(date +%F)_<title-slug>.md` using the template in Step 8.

   8. **Review note template** (one per added video):
      ```markdown
      ---
      type: video-feedback
      review-action: pending
      review-notes: ""
      video-url: <url from JSON>
      video-title: "<title from JSON>"
      channel: "<channel from JSON>"
      duration: "<duration from JSON>"
      added: <YYYY-MM-DD>
      why-suitable: "<why from JSON>"
      query: "<original query>"
      up: "[[index]]"
      ---
      ```
      Body is empty. Keith will fill `review-notes` and flip `review-action` after watching.

   9. **Write run record** at `K2B-Vault/raw/research/$(date +%F)_videos_<query-slug>.md` with frontmatter + sections: query, baked framing version, preference tail used, full JSON response (suitable + skipped with reasons), playlist adds, review notes created, any errors. This is the durable audit trail.

   10. **Send Telegram notification:**
       ```bash
       MSG="K2B found <N> videos for: *<query>*\n\n"
       # For each suitable video, append: "• <title> — <why>\n<url>\n"
       scripts/send-telegram.sh "$MSG"
       ```
       Keep under 4000 chars (Telegram hard limit is 4096). If more than 4 videos, batch into 2 messages.

   11. **Delete the notebook** (fresh per run, no accumulation):
       ```bash
       notebooklm notebook delete "$NB_ID"
       ```

   12. **Append to skill-usage-log** as usual.

   ### Scheduling (not a skill concern)

   Wrap with `/schedule`:
   ```
   /schedule add "research-videos-ai-recruiting" weekly "/research videos \"AI recruiting tools for large enterprises\""
   ```
   The scheduler runs the command on the Mac Mini weekly. Output lands in the vault via Syncthing. Telegram notification fires from the Mac Mini using `send-telegram.sh`.

   ### Failure modes

   - **NotebookLM research times out (1800s):** abort, log to run record, notify Keith "research timed out on: <query>".
   - **JSON parse fails twice:** log raw answer, notify Keith "filter response wasn't parseable, see raw/research/".
   - **Playlist add fails for a video:** log per-video in run record, continue with the others, include the failure count in the Telegram summary.
   - **Zero suitable videos:** still write the run record, notify Keith "nothing new worth watching for: <query> (N candidates screened, all rejected — see run record for why)".

   ### What NOT to do

   - Do NOT cache NotebookLM notebooks across runs. Fresh per run.
   - Do NOT dedupe across runs via a URL log. The filter prompt's preference tail handles this naturally once Keith rates videos.
   - Do NOT ask Keith to confirm each add. The filter already decided. Keith's feedback comes after watching.
   - Do NOT write to `wiki/context/video-preferences.md` from `/research videos`. Only `/review` appends there.
   ```

- [ ] **Step 2:** Commit:
   ```
   git add .claude/skills/k2b-research/SKILL.md
   git commit -m "feat(k2b-research): add /research videos subcommand"
   ```

## Task B5: Extend `k2b-review` skill with video feedback handler

**Files:**
- Modify: `.claude/skills/k2b-review/SKILL.md`

- [ ] **Step 1:** Add a new section to the skill:

   ```markdown
   ## Video feedback from `/research videos`

   `review/video_*.md` notes are dropped by `/research videos` when suitable videos are added to the K2B Watch playlist. Each note has frontmatter with `review-action: pending` initially. Keith watches the video, then updates the note (via Obsidian or Telegram) — flipping `review-action` to `liked` / `disliked` / `neutral` and writing his reaction in `review-notes`.

   ### Processing

   For each `review/video_*.md` file where `review-action != pending`:

   1. Read the file frontmatter. Extract `review-action`, `review-notes`, `channel`, `video-title`, `added` date.
   2. Compose one distilled line: `<added-date> <review-action>: <channel or title> — <one-sentence distillation of review-notes>`.
      Example: `2026-04-13 liked: Matt Wolfe — clear concrete examples, prefer tools demos with deployment numbers`.
      Keep the distillation under ~25 words — this is for the NotebookLM filter prompt, which reads the tail of `video-preferences.md` each run.
   3. Append the line to `K2B-Vault/wiki/context/video-preferences.md` (after the `## Preferences` heading, at the end of the list).
   4. Delete the review note: `rm K2B-Vault/review/<file>.md`. The distilled line is the durable record — the raw note is transient.
   5. Log the action to `wiki/log.md` as usual ("processed N video feedback notes").

   Review notes with `review-action: pending` are left untouched — Keith hasn't watched those videos yet.
   ```

- [ ] **Step 2:** Commit:
   ```
   git add .claude/skills/k2b-review/SKILL.md
   git commit -m "feat(k2b-review): handle video feedback notes from /research videos"
   ```

## Task B6: Add "Video Feedback via Telegram" rule to `CLAUDE.md`

**Files:**
- Modify: `CLAUDE.md` (project root)

- [ ] **Step 1:** Add a new section before the "Email Safety" section:

   ```markdown
   ## Video Feedback via Telegram

   When Keith reacts to a video in a Telegram conversation (examples: "the Wolfe video was great", "that Operator breakdown was shallow, skip Matthew Berman", "I liked #3 from yesterday's batch"), do the following without asking for confirmation:

   1. Glob `K2B-Vault/review/video_*.md`.
   2. Read the candidates to find the match. Prefer URL match if Keith pasted one, then exact title match, then channel match, then recency (most recently added first). If Keith says "yesterday's batch #3", match by `added` date and ordinal position.
   3. If exactly one match: Edit the frontmatter to set `review-action` to `liked` / `disliked` / `neutral` based on Keith's tone, and write his distilled reaction into `review-notes`. Reply in Telegram confirming which video was updated ("updated: <title> → liked, notes saved").
   4. If zero matches: reply with "no matching video in review queue — want me to log this as a standalone preference line?" and wait for Keith's answer.
   5. If multiple matches: list the candidates in Telegram with their `added` dates and ask Keith to disambiguate.
   6. Do NOT append to `wiki/context/video-preferences.md` directly — that's `/review`'s job. You are only updating the transient review note.

   This rule relies on the interactive Claude session's built-in Edit/Glob/Grep tools. No new MCP tool, no routing code, no keyword matching — the "is this video feedback?" decision is made by reading the conversation context.
   ```

- [ ] **Step 2:** Commit:
   ```
   git add CLAUDE.md
   git commit -m "docs(CLAUDE.md): add video feedback via telegram rule"
   ```

## Task B7: End-to-end test — manual `/research videos` run

This is the real test. Run the subcommand against a real query and confirm every piece of the flow works.

- [ ] **Step 1:** Pick a test query that will return real YouTube content: `"AI agents for corporate workflows 2026"`.
- [ ] **Step 2:** Invoke `/research videos "AI agents for corporate workflows 2026"` from Claude Code on the MacBook.
- [ ] **Step 3:** Watch for each milestone:
   a. NotebookLM notebook created — `notebooklm list` shows it.
   b. Deep research started — `notebooklm research status -n <id>` eventually shows complete.
   c. Sources indexed — `notebooklm source list -n <id>` shows all `ready`.
   d. JSON filter answer received — visible in the run's stdout.
   e. Playlist adds happen — check K2B Watch playlist on YouTube Studio.
   f. Review notes created — `ls K2B-Vault/review/video_*.md`.
   g. Run record written — `ls K2B-Vault/raw/research/*_videos_*.md`.
   h. Telegram notification received on Keith's phone.
   i. Notebook deleted — `notebooklm list` no longer shows it.
- [ ] **Step 4:** Open the K2B Watch playlist on Keith's phone. Confirm the new videos are there, in order.
- [ ] **Step 5:** Open one of the review notes in Obsidian. Confirm the frontmatter is correct.
- [ ] **Step 6:** If any milestone fails, diagnose from the run record file first — it should have the failure reason. Fix, re-run, repeat until clean.

## Task B8: End-to-end test — Telegram feedback rule

- [ ] **Step 1:** After Task B7 completes successfully, pick one video from the test batch that Keith has actually watched (or will watch right now).
- [ ] **Step 2:** From Keith's phone, send a Telegram message to the bot like: "the <title> video was good, clear examples of <thing>".
- [ ] **Step 3:** Expected bot reply within 30 seconds: "updated: <title> → liked, notes saved".
- [ ] **Step 4:** Open the matching review note in Obsidian. Confirm `review-action: liked` and `review-notes` contain Keith's distilled reaction.
- [ ] **Step 5:** If the bot fails to update (picks wrong video, asks for disambiguation unnecessarily, errors out), refine the CLAUDE.md rule and retry.

## Task B9: End-to-end test — `/review` distillation

- [ ] **Step 1:** After Task B8, run `/review` in Claude Code on the MacBook.
- [ ] **Step 2:** Expected: `/review` picks up the one non-pending video feedback note, distills it, appends a line to `K2B-Vault/wiki/context/video-preferences.md`, deletes the review note.
- [ ] **Step 3:** Read `video-preferences.md` — confirm the new line is there in the right format.
- [ ] **Step 4:** `ls K2B-Vault/review/video_*.md` — the processed note is gone, others (still `pending`) remain.

## Task B10: Second `/research videos` run — validate feedback loop

This is the proof the feedback loop actually tunes the filter.

- [ ] **Step 1:** Run `/research videos "AI agents for corporate workflows 2026"` a second time (same query, so we can compare).
- [ ] **Step 2:** Read the run record — confirm the filter prompt included the preference line from Task B9.
- [ ] **Step 3:** Compare the JSON response — did the filter's `why` field reference the preference? (e.g., "matches your stated preference for clear concrete examples").
- [ ] **Step 4:** If the feedback is visibly affecting the filter, the loop is closed. If not, check the filter prompt in the run record to see whether the tail was actually injected.

## Task B11: Schedule one weekly run

- [ ] **Step 1:** Add a weekly scheduled run on the Mac Mini:
   ```
   /schedule add "research-videos-ai-recruiting" "0 9 * * MON" "/research videos \"AI recruiting tools for large enterprises\""
   ```
   (Cron: every Monday 09:00 HKT.)
- [ ] **Step 2:** Verify with `/schedule list`.
- [ ] **Step 3:** Wait for the first scheduled run and confirm it lands a Telegram notification + review notes + run record. Alternatively, trigger a manual run with `/schedule run research-videos-ai-recruiting`.

## Task B12: Update `feature_research-videos-notebooklm.md` to in-progress → shipped

- [ ] **Step 1:** Flip frontmatter `status: designed` → `status: shipped`. Set `shipped-date: 2026-04-13`.
- [ ] **Step 2:** Add a "Shipping notes" section at the bottom listing what shipped in Phase B (send-telegram.sh, skill sections, CLAUDE.md rule, first weekly schedule, test run results).
- [ ] **Step 3:** Update `wiki/concepts/index.md` — move this feature from `in-progress` / `next-up` to Shipped-Recent-10.
- [ ] **Step 4:** Commit with `/ship` (which handles the devlog + wiki/log + push + sync).

## Task B13: Run `/ship` end of session

- [ ] **Step 1:** `/ship` — this handles Codex pre-commit review, commit, push, devlog, wiki/log, feature note updates, sync to Mac Mini, and the explicit "sync now or defer?" gate.

---

# Verification Section

## Phase A verification (quick)

Run these from the K2B project root after Task A12:

```bash
# Project-level checks
rg "youtube.ts|youtube-agent-loop|taste-model|ytMutex|guardedAdd|guardedSkip|guardedKeep|guardedSwap|youtube_agent_state|handleYouTubeAgentResponse|parseAndExecuteActions" k2b-remote/src -n
# Expected: zero matches

ls k2b-remote/src/youtube*.ts k2b-remote/src/taste-model.ts 2>&1
# Expected: "No such file or directory" for all three

grep '"async-mutex"' k2b-remote/package.json
# Expected: no match

cd k2b-remote && npx tsc --noEmit && npm run build
# Expected: clean exit

# Mac Mini checks (SSH)
ssh macmini "pm2 status | grep k2b-remote"
# Expected: status "online", no recent restarts after deploy

ssh macmini "pm2 logs k2b-remote --lines 30 --nostream | grep -iE 'error|youtube|mutex'"
# Expected: no YouTube / mutex errors
```

## Phase B verification (end-to-end)

- After Task B7: the K2B Watch playlist on YouTube has new videos that weren't there before.
- After Task B7: `K2B-Vault/raw/research/*_videos_*.md` exists for the test run, contains the JSON filter response, reports playlist add success per video.
- After Task B7: `K2B-Vault/review/video_*.md` files exist, one per added video, all with `review-action: pending`.
- After Task B7: Keith's phone got a Telegram notification with the video list + reasons.
- After Task B8: the CLAUDE.md rule successfully updates a review note from a free-form Telegram message.
- After Task B9: `wiki/context/video-preferences.md` has a new distilled preference line, the processed review note is deleted.
- After Task B10: the second run's filter prompt includes the preference tail, and the filter response reflects it.
- After Task B11: `/schedule list` shows the weekly research-videos entry active.

## Rollback plan

- **Phase A rollback:** If Mac Mini pm2 logs show startup errors after deploy, SSH in, restore the DB snapshot from Task A12 Step 3, `git revert` the retirement commits on both MacBook and Mac Mini, `/sync` again, `pm2 restart`. The retirement is a clean whole-file delete so `git revert` is safe.
- **Phase B rollback:** No runtime impact on the bot — Phase B is pure skill documentation + one bash script. If `/research videos` misbehaves, it just doesn't run. Revert the three commits (B4, B5, B6), delete `scripts/send-telegram.sh` and `wiki/context/video-preferences.md`.

---

## Open Questions

None. All decisions locked during brainstorming on 2026-04-13. Spec at `K2B-Vault/wiki/concepts/feature_research-videos-notebooklm.md`.
