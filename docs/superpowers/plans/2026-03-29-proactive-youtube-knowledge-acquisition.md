# Proactive YouTube Knowledge Acquisition - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate YouTube knowledge acquisition with morning nudges via Telegram inline buttons, promotion to content/feature pipeline, and observer integration.

**Architecture:** Extend k2b-youtube-capture skill with a `/youtube morning` subcommand. Add inline keyboard + callback_query handling to k2b-remote's bot.ts. Track all recommendations in a vault JSONL file that the background observer reads for pattern detection.

**Tech Stack:** Grammy (Telegram bot), Claude Agent SDK, yt-dlp, YouTube Data API v3, bash scripts, JSONL

---

### Task 1: Create youtube-recommended.jsonl and helper functions

**Files:**
- Create: `k2b-remote/src/youtube.ts`
- Create: `Notes/Context/youtube-recommended.jsonl` (vault, via script)

This task creates the data layer. `youtube.ts` provides functions to read, append, update, and dedup against the JSONL file.

- [ ] **Step 1: Create youtube.ts with JSONL helpers**

```typescript
// k2b-remote/src/youtube.ts
import { readFileSync, appendFileSync, writeFileSync, existsSync } from 'node:fs'

const VAULT = process.env.K2B_VAULT ?? '/Users/fastshower/Projects/K2B-Vault'
const RECOMMENDED_FILE = `${VAULT}/Notes/Context/youtube-recommended.jsonl`

export interface YouTubeRecommendation {
  ts: string
  video_id: string
  title: string
  channel: string
  playlist: string
  recommended_date: string
  status: 'pending' | 'nudge_sent' | 'watched' | 'highlights_sent' | 'skipped' | 'expired' | 'processed'
  nudge_sent: boolean
  nudge_date: string | null
  outcome: string | null
  rating: string | null
  promoted_to: string | null
  vault_note: string | null
}

export function readRecommendations(): YouTubeRecommendation[] {
  if (!existsSync(RECOMMENDED_FILE)) return []
  const lines = readFileSync(RECOMMENDED_FILE, 'utf-8').trim().split('\n').filter(Boolean)
  return lines.map(line => JSON.parse(line) as YouTubeRecommendation)
}

export function appendRecommendation(rec: YouTubeRecommendation): void {
  appendFileSync(RECOMMENDED_FILE, JSON.stringify(rec) + '\n')
}

export function updateRecommendation(videoId: string, updates: Partial<YouTubeRecommendation>): void {
  const all = readRecommendations()
  const updated = all.map(rec =>
    rec.video_id === videoId ? { ...rec, ...updates } : rec
  )
  writeFileSync(RECOMMENDED_FILE, updated.map(r => JSON.stringify(r)).join('\n') + '\n')
}

export function isAlreadyRecommended(videoId: string): boolean {
  return readRecommendations().some(r => r.video_id === videoId)
}

export function getPendingNudges(): YouTubeRecommendation[] {
  return readRecommendations().filter(r => r.status === 'nudge_sent')
}
```

- [ ] **Step 2: Seed an empty JSONL file in the vault**

```bash
touch ~/Projects/K2B-Vault/Notes/Context/youtube-recommended.jsonl
```

- [ ] **Step 3: Commit**

```bash
git add k2b-remote/src/youtube.ts
git commit -m "feat: add YouTube recommendation JSONL data layer"
```

---

### Task 2: Add inline keyboard support to bot.ts

**Files:**
- Modify: `k2b-remote/src/bot.ts`

Grammy has built-in `InlineKeyboard` support. This task adds the ability to send messages with buttons and handle callback queries.

- [ ] **Step 1: Add InlineKeyboard import and sendTelegramMessageWithButtons function**

Add to the top of `k2b-remote/src/bot.ts`, after the existing imports:

```typescript
import { InlineKeyboard } from 'grammy'
```

Add this new function after the existing `sendTelegramMessage` function (after line 330):

```typescript
export async function sendTelegramMessageWithButtons(
  chatId: string,
  text: string,
  buttons: Array<{ label: string; callbackData: string }>
): Promise<void> {
  if (!TELEGRAM_BOT_TOKEN) return
  const keyboard = new InlineKeyboard()
  for (const btn of buttons) {
    keyboard.text(btn.label, btn.callbackData)
  }
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`
  const body = JSON.stringify({
    chat_id: chatId,
    text: formatForTelegram(text),
    parse_mode: 'HTML',
    reply_markup: keyboard.toFlowed(2),
  })

  return new Promise((resolvePromise, reject) => {
    const req = httpsRequest(
      url,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(body),
        },
      },
      (res) => {
        res.resume()
        res.on('end', () => resolvePromise())
      }
    )
    req.on('error', reject)
    req.write(body)
    req.end()
  })
}
```

- [ ] **Step 2: Add callback_query handler in createBot()**

Add this inside `createBot()`, after the `bot.on('message:document', ...)` handler (after line 289), before the error handler:

```typescript
  // --- Callback query handler (inline buttons) ---

  bot.on('callback_query:data', async (ctx) => {
    const data = ctx.callbackQuery.data
    const chatId = String(ctx.chat?.id)
    if (!chatId) return

    try {
      // Acknowledge button press immediately
      await ctx.answerCallbackQuery()

      if (data.startsWith('youtube:')) {
        // Route to YouTube handler
        await handleYouTubeCallback(ctx, data, chatId)
      }
    } catch (err) {
      logger.error({ err, data }, 'Callback query error')
      try {
        await ctx.answerCallbackQuery({ text: 'Something went wrong.' })
      } catch { /* ignore */ }
    }
  })
```

- [ ] **Step 3: Add the YouTube callback handler function**

Add this function before `createBot()`:

```typescript
import { updateRecommendation } from './youtube.js'

async function handleYouTubeCallback(
  ctx: Context,
  data: string,
  chatId: string
): Promise<void> {
  const parts = data.split(':')
  // Format: youtube:ACTION:VIDEO_ID or youtube:promote:TYPE:VIDEO_ID
  const action = parts[1]
  const videoId = parts[parts.length - 1]

  if (action === 'highlights') {
    await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    // Run Claude agent to fetch transcript and generate highlights
    const prompt = `You are K2B. Run /youtube ${videoId} and send the highlights summary. Use the playlist prompt_focus for analysis. Keep it concise for Telegram.`
    const { text } = await runAgent(prompt)
    const result = text ?? '(could not generate highlights)'

    updateRecommendation(videoId, {
      status: 'highlights_sent',
      outcome: 'highlights',
    })

    // Send highlights
    const formatted = formatForTelegram(result)
    const chunks = splitMessage(formatted)
    for (const chunk of chunks) {
      try {
        await ctx.api.sendMessage(ctx.chat!.id, chunk, { parse_mode: 'HTML' })
      } catch {
        await ctx.api.sendMessage(ctx.chat!.id, chunk.replace(/<[^>]+>/g, ''))
      }
    }

    // Send promotion buttons
    const promoKeyboard = new InlineKeyboard()
      .text('Content idea', `youtube:promote:content-idea:${videoId}`)
      .text('Feature', `youtube:promote:feature:${videoId}`)
      .row()
      .text('Insight', `youtube:promote:insight:${videoId}`)
      .text('Nothing', `youtube:promote:nothing:${videoId}`)

    await ctx.api.sendMessage(ctx.chat!.id, 'What do you want to do with this?', {
      reply_markup: promoKeyboard,
    })

  } else if (action === 'skip') {
    updateRecommendation(videoId, {
      status: 'skipped',
      outcome: 'skipped',
    })
    await ctx.api.sendMessage(ctx.chat!.id, 'Skipped and removed from Watch.')

    // Remove from Watch playlist via agent
    runAgent(`Remove video ${videoId} from K2B Watch playlist using scripts/yt-playlist-remove.sh`).catch(err =>
      logger.error({ err, videoId }, 'Failed to remove from Watch playlist')
    )

  } else if (action === 'promote') {
    const promoteType = parts[2] // content-idea | feature | insight | nothing

    if (promoteType === 'nothing') {
      updateRecommendation(videoId, { promoted_to: null })
      await ctx.api.sendMessage(ctx.chat!.id, 'Got it, no promotion.')
      return
    }

    // Create vault note via agent
    await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    const prompt = `You are K2B. Create a ${promoteType} vault note from YouTube video ${videoId}. Look up the video details in Notes/Context/youtube-recommended.jsonl. Use k2b-vault-writer to create the note in Inbox/.`
    const { text } = await runAgent(prompt)
    const result = text ?? '(created)'

    updateRecommendation(videoId, {
      promoted_to: promoteType,
    })

    await ctx.api.sendMessage(
      ctx.chat!.id,
      formatForTelegram(`Saved as ${promoteType}: ${result}`),
      { parse_mode: 'HTML' }
    )
  }
}
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd k2b-remote && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add k2b-remote/src/bot.ts
git commit -m "feat: add Telegram inline buttons and YouTube callback handler"
```

---

### Task 3: Add `/youtube morning` subcommand to skill

**Files:**
- Modify: `.claude/skills/k2b-youtube-capture/SKILL.md`

- [ ] **Step 1: Add the morning subcommand to the Commands section**

After the existing commands (line 15), add:

```markdown
- `/youtube morning` -- Automated morning routine: nudge unwatched videos, poll inbound playlists (runs daily via scheduler, can also run manually)
```

- [ ] **Step 2: Add the morning workflow section**

Add after the "Workflow: Recommend" section (after line 227), before "Workflow: Status":

```markdown
## Workflow: Morning Routine (`/youtube morning`)

Automated daily check. Runs via scheduled task at 7am HKT. Can also be triggered manually.

### Paths (additional)

- Recommendations JSONL: `~/Projects/K2B-Vault/Notes/Context/youtube-recommended.jsonl`

### 1. Handle Stale Nudges

Read `youtube-recommended.jsonl` for entries with `status: "nudge_sent"`.

For each:
- If `nudge_date` was yesterday: send a re-nudge via Telegram with inline buttons:
  ```
  Still in your Watch list (added yesterday):

  {title}
  {channel} | {duration}

  [Get highlights]  [Skip]
  ```
  The buttons use callback data format: `youtube:highlights:{video_id}` and `youtube:skip:{video_id}`

- If `nudge_date` was 2+ days ago and still no response: mark `status: "expired"` in JSONL, remove from K2B Watch playlist via `scripts/yt-playlist-remove.sh`.

### 2. Check K2B Watch for New Additions

Poll K2B Watch playlist:
```bash
~/Projects/K2B/scripts/yt-playlist-poll.sh "<watch-playlist-url>" "~/Projects/K2B-Vault/Notes/Context/youtube-processed.md" --max 10
```

For each video found:
1. Check if `video_id` exists in `youtube-recommended.jsonl` -- if yes, skip (already tracked)
2. Get video metadata via `mcp__YouTube_Transcript_MCP_Server__get_video_info`
3. Append entry to `youtube-recommended.jsonl`:
   ```json
   {"ts":"...","video_id":"...","title":"...","channel":"...","playlist":"K2B Watch","recommended_date":"YYYY-MM-DD","status":"nudge_sent","nudge_sent":true,"nudge_date":"YYYY-MM-DD","outcome":null,"rating":null,"promoted_to":null,"vault_note":null}
   ```
4. Send Telegram nudge with inline buttons:
   ```
   New in your Watch list:

   {title}
   {channel} | {duration}
   Playlist: K2B Watch

   [Get highlights]  [Skip]
   ```
   Use `sendTelegramMessageWithButtons` from k2b-remote (or via the agent's Telegram output).

### 3. Poll Inbound Playlists

Run the standard playlist polling workflow (same as `/youtube`):
- Poll each inbound playlist via `yt-playlist-poll.sh`
- Filter against BOTH `youtube-processed.md` AND `youtube-recommended.jsonl`
- Process each new video (transcript, analysis, vault note)
- Append to `youtube-recommended.jsonl` with `status: "processed"`
- Send Telegram notification: "New video processed: {title} from {playlist}. Note in Inbox."

### 4. Summary

After all checks, send one summary message:
```
YouTube Morning Report:
- {N} stale nudges handled ({M} re-nudged, {K} expired)
- {N} new Watch videos nudged
- {N} new inbound videos processed
```

### Sending Telegram Messages with Buttons

When running via the scheduled task on Mac Mini, messages go through k2b-remote's `sendTelegramMessageWithButtons`. The Claude agent running the skill should output the message content and button definitions, and the scheduler sends them.

For the MVP: the agent sends a plain Telegram message describing the video plus instructions like "(Reply 'highlights abc123' or 'skip abc123')". Inline buttons are handled natively by bot.ts when it detects videos in the JSONL with `status: "nudge_sent"`.

**Preferred approach**: The k2b-youtube-capture skill writes the JSONL entries, then bot.ts has a post-task hook that reads new `nudge_sent` entries and sends the Telegram messages with inline buttons directly.
```

- [ ] **Step 3: Update the Scheduled Task section**

Replace the existing "Scheduled Task" section (lines 232-236) with:

```markdown
## Scheduled Task

YouTube morning runs daily at 7am HKT via the K2B scheduler on Mac Mini:

```
/schedule daily 7am "Run /youtube morning"
```

This replaces the previous manual polling approach. Keith can still run `/youtube` manually for on-demand processing.
```

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/k2b-youtube-capture/SKILL.md
git commit -m "feat: add /youtube morning subcommand to skill"
```

---

### Task 4: Add morning nudge sender to bot.ts

**Files:**
- Modify: `k2b-remote/src/bot.ts`
- Modify: `k2b-remote/src/index.ts`

The scheduled task runs Claude agent which writes JSONL entries. After the agent finishes, bot.ts reads new `nudge_sent` entries and sends Telegram messages with inline buttons. This is cleaner than having Claude try to send buttons directly.

- [ ] **Step 1: Add sendPendingNudges function to bot.ts**

Add after `sendTelegramMessageWithButtons`:

```typescript
import { getPendingNudges, readRecommendations } from './youtube.js'

// Track which nudges we've already sent buttons for (by video_id)
const sentNudgeIds = new Set<string>()

export async function sendPendingNudges(chatId: string): Promise<number> {
  const pending = getPendingNudges()
  let sent = 0

  for (const rec of pending) {
    if (sentNudgeIds.has(rec.video_id)) continue

    const text = rec.nudge_date && rec.nudge_date < new Date().toISOString().slice(0, 10)
      ? `Still in your Watch list (added ${rec.nudge_date}):\n\n<b>${rec.title}</b>\n${rec.channel}\nPlaylist: ${rec.playlist}`
      : `New in your Watch list:\n\n<b>${rec.title}</b>\n${rec.channel}\nPlaylist: ${rec.playlist}`

    const buttons = [
      { label: 'Get highlights', callbackData: `youtube:highlights:${rec.video_id}` },
      { label: 'Skip', callbackData: `youtube:skip:${rec.video_id}` },
    ]

    await sendTelegramMessageWithButtons(chatId, text, buttons)
    sentNudgeIds.add(rec.video_id)
    sent++
  }

  return sent
}
```

- [ ] **Step 2: Call sendPendingNudges after scheduled tasks complete**

In `k2b-remote/src/scheduler.ts`, after the agent result is sent (after line 46), add a call to check for pending nudges:

```typescript
import { sendPendingNudges } from './bot.js'
import { ALLOWED_CHAT_ID } from './config.js'

// Inside runDueTasks, after: await sendFn(task.chat_id, result)
// Add:
      if (task.prompt.includes('/youtube morning') && ALLOWED_CHAT_ID) {
        const nudged = await sendPendingNudges(ALLOWED_CHAT_ID)
        if (nudged > 0) {
          logger.info({ nudged }, 'Sent YouTube nudge buttons')
        }
      }
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd k2b-remote && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
git add k2b-remote/src/bot.ts k2b-remote/src/scheduler.ts
git commit -m "feat: send Telegram nudges with inline buttons after morning task"
```

---

### Task 5: Update observer prompt for YouTube patterns

**Files:**
- Modify: `scripts/observer-prompt.md`

- [ ] **Step 1: Add YouTube behavior section to observer prompt**

Add after the "### Confidence Updates" section (after line 34), before "## Output Format":

```markdown
### YouTube Behavior Patterns
Also read Notes/Context/youtube-recommended.jsonl for:
- Watch rate by playlist: which playlists have highest watched/total ratio?
- Watch rate by channel: which channels does Keith consistently watch vs skip?
- Promotion rate: what percentage of watched/highlighted videos get promoted?
- Promotion type by playlist: do K2B Claude videos become features while K2B Recruit becomes content ideas?
- Highlight vs full watch: does Keith prefer quick highlights or watching the video?
- Skip rate by playlist/channel: consistently skipped sources should be flagged
- Time to action: how quickly does Keith respond to nudges? (nudge_date vs outcome timestamp)
- Expiry rate: high expiry rate means recommendations aren't relevant enough
```

- [ ] **Step 2: Add youtube type to the patterns output format**

In the "Output Format" JSON example, update the `type` field comment:

```json
"type": "skill_adoption|revision|cross_skill|timing|youtube_behavior",
```

- [ ] **Step 3: Commit**

```bash
git add scripts/observer-prompt.md
git commit -m "feat: add YouTube behavior patterns to observer prompt"
```

---

### Task 6: Update CLAUDE.md and create scheduled task

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the YouTube Capture command in CLAUDE.md**

Find the `/youtube` entry in the Slash Commands section and update it:

```markdown
**`/youtube [subcommand]`** -- YouTube knowledge pipeline. `/youtube` polls all playlists manually. `/youtube <url>` processes a single video. `/youtube recommend` finds new videos. `/youtube morning` runs the automated daily routine (nudge unwatched, poll inbound, send via Telegram). `/youtube status` shows stats. Morning routine runs automatically at 7am HKT via scheduler.
```

- [ ] **Step 2: Update the Skill Data Flow diagram**

In the data flow section, update the CAPTURE column:

```markdown
/youtube --> Inbox/ + youtube-recommended.jsonl
            (morning auto-run: nudge via Telegram,
             poll inbound, observer learns patterns)
```

- [ ] **Step 3: Create the scheduled task on Mac Mini**

```bash
ssh macmini 'cd ~/Projects/K2B && node k2b-remote/dist/schedule-cli.js add --schedule "0 7 * * *" --prompt "Run /youtube morning" --name "youtube-morning"'
```

If the schedule CLI doesn't support this exact format, create via the K2B scheduler skill instead:
```
/schedule daily 7am "Run /youtube morning"
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document /youtube morning and scheduled task"
```

---

### Task 7: Build, deploy, and verify

**Files:**
- No new files

- [ ] **Step 1: Build k2b-remote**

```bash
cd k2b-remote && npm run build
```

Expected: successful compilation in `dist/`

- [ ] **Step 2: Run typecheck**

```bash
cd k2b-remote && npm run typecheck
```

Expected: no errors

- [ ] **Step 3: Deploy to Mac Mini**

```bash
rsync -av --exclude node_modules --exclude dist ~/Projects/K2B/k2b-remote/ macmini:~/Projects/K2B/k2b-remote/
ssh macmini "cd ~/Projects/K2B/k2b-remote && npm run build && pm2 restart k2b-remote"
```

- [ ] **Step 4: Sync skills and scripts**

```bash
rsync -av ~/Projects/K2B/.claude/skills/ macmini:~/Projects/K2B/.claude/skills/
rsync -av ~/Projects/K2B/scripts/ macmini:~/Projects/K2B/scripts/
rsync -av ~/Projects/K2B/CLAUDE.md macmini:~/Projects/K2B/CLAUDE.md
```

- [ ] **Step 5: Verify bot is running**

```bash
ssh macmini "pm2 status"
ssh macmini "pm2 logs k2b-remote --lines 10 --nostream"
```

Expected: k2b-remote online, no errors

- [ ] **Step 6: Manual test -- add a video to K2B Watch, run morning**

Add a test video to K2B Watch playlist manually in YouTube, then:

```bash
ssh macmini "cd ~/Projects/K2B && claude --print 'Run /youtube morning'"
```

Verify:
- Telegram message received with inline buttons
- `youtube-recommended.jsonl` has the new entry with `status: "nudge_sent"`
- Tapping "Get highlights" in Telegram produces a summary
- Tapping a promotion button creates a vault note

- [ ] **Step 7: Commit final state and push**

```bash
git add -A && git commit -m "feat: proactive YouTube knowledge acquisition with Telegram nudges"
git push origin main
```
