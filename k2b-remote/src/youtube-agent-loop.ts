import { readFileSync, readdirSync, existsSync, appendFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { execSync, execFileSync } from 'node:child_process'
import { InlineKeyboard } from 'grammy'
import { K2B_VAULT_PATH, K2B_PROJECT_ROOT } from './config.js'
import {
  readRecommendations,
  updateRecommendation,
  getPlaylistVideoIds,
  WATCH_PLAYLIST_ID,
  type YouTubeRecommendation,
  // Canonical state machinery (persisted to SQLite via db.ts)
  getYtState,
  setYtState,
  resetYtState,
  getYtPendingCandidates,
  setYtPendingCandidates,
  expireVideoFromWatch,
  type PendingCandidate,
  type YouTubeAgentState,
} from './youtube.js'
import { tasteModel } from './taste-model.js'
import { runStatelessQuery } from './agent.js'
import { logger } from './logger.js'

// ---------------------------------------------------------------------------
// Stateless classifier calls
// ---------------------------------------------------------------------------
// The background loop only needs one thing from the agent: turn a structured
// prompt into text. It does NOT need conversation history, tool access, or a
// persisted session. `runStatelessQuery` wraps query() with
// `persistSession: false` so these calls never write to ~/.claude/projects/.
// See plans/2026-04-13_session-design-v3.md section D.

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fetchUploadDate(videoId: string): string {
  try {
    return execFileSync('yt-dlp', [
      '--print', '%(upload_date)s',
      `https://www.youtube.com/watch?v=${videoId}`
    ], { encoding: 'utf-8', timeout: 15_000, stdio: ['pipe', 'pipe', 'pipe'] }).trim()
  } catch {
    return ''
  }
}

function fetchVideoMetadata(videoId: string): { title: string; channel: string; duration: string; uploadDate: string } {
  try {
    const out = execFileSync('yt-dlp', [
      '--print', '%(title)s\t%(channel)s\t%(duration_string)s\t%(upload_date)s',
      `https://www.youtube.com/watch?v=${videoId}`,
    ], { encoding: 'utf-8', timeout: 15_000, stdio: ['pipe', 'pipe', 'pipe'] }).trim()
    const [title, channel, duration, uploadDate] = out.split('\t')
    return { title: title || '', channel: channel || '', duration: duration || '', uploadDate: uploadDate || '' }
  } catch {
    return { title: '', channel: '', duration: '', uploadDate: '' }
  }
}

function formatUploadDate(raw: string): string {
  if (!raw || raw === 'NA') return 'unknown'
  // Handle YYYYMMDD format from yt-dlp
  if (/^\d{8}$/.test(raw)) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`
  return raw
}

function ageDaysFromYmd(raw: string): number | null {
  if (!raw) return null
  let iso = raw
  if (/^\d{8}$/.test(raw)) iso = `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return Math.floor((Date.now() - t) / (1000 * 60 * 60 * 24))
}

// Re-export for backward compatibility with existing importers (bot.ts, index.ts)
export { getYtState, setYtState, resetYtState, getYtPendingCandidates, setYtPendingCandidates }
export type { YouTubeAgentState, PendingCandidate }

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SendFn = (chatId: string, text: string) => Promise<void>
type SendWithButtonsFn = (chatId: string, text: string, buttons: Array<{label: string; callbackData: string}>, prebuiltKeyboard?: InlineKeyboard) => Promise<void>

// ---------------------------------------------------------------------------
// Main loop entry point
// ---------------------------------------------------------------------------

export async function runYouTubeAgentLoop(
  sendMessage: SendFn,
  sendWithButtons: SendWithButtonsFn,
  chatId: string
): Promise<void> {
  // Step 0: Guards -----------------------------------------------------------

  // Don't run if already in a cycle
  const initialState = getYtState()
  if (initialState.phase !== 'idle') {
    logger.info('YouTube agent loop skipped -- already in progress')
    return
  }

  // Time guard: no cycles between 23:00-09:00 HKT
  const hktHour = new Date().toLocaleString('en-US', { timeZone: 'Asia/Hong_Kong', hour: 'numeric', hour12: false })
  const hour = parseInt(hktHour, 10)
  if (hour >= 23 || hour < 9) {
    logger.info({ hour }, 'YouTube agent loop skipped -- quiet hours')
    return
  }

  // Reset daily counter
  const today = new Date().toISOString().slice(0, 10)
  if (initialState.lastCycleDate !== today) {
    setYtState({ cyclesToday: 0, lastCycleDate: today })
  }

  // Step 1: Check existing Watch list ----------------------------------------

  try {
  setYtState({ phase: 'checking-watch', startedAt: new Date().toISOString() })

  let pending = readRecommendations().filter(r => r.status === 'nudge_sent')

  if (pending.length > 0) {
    // Verify against actual Watch playlist -- expire phantom entries
    const playlistIds = getPlaylistVideoIds(WATCH_PLAYLIST_ID)
    if (playlistIds !== null) {
      const verified: YouTubeRecommendation[] = []
      for (const rec of pending) {
        if (playlistIds.includes(rec.video_id)) {
          verified.push(rec)
        } else {
          expireVideoFromWatch(rec.video_id, 'not-in-playlist')
          logger.info({ videoId: rec.video_id, title: rec.title }, 'Auto-expired nudge_sent video not found in Watch playlist')
        }
      }
      pending = verified
      if (pending.length === 0) {
        // All phantom -- find fresh content instead
        await findNewContent(sendMessage, sendWithButtons, chatId)
        return
      }
    } else {
      logger.warn('Could not verify Watch playlist -- skipping verification')
    }

    // Build context for each pending video. Prefer upload_date (actual publish)
    // over recommended_date (when K2B added it). If missing, fetch via yt-dlp.
    const videoSummaries = pending.map(r => {
      let uploadDate = r.upload_date ?? ''
      let title = r.title ?? r.video_id
      if (!uploadDate || title === r.video_id) {
        const meta = fetchVideoMetadata(r.video_id)
        if (meta.uploadDate) uploadDate = meta.uploadDate
        if (meta.title && title === r.video_id) title = meta.title
        // Persist so we don't re-fetch every cycle
        const updates: Partial<YouTubeRecommendation> = {}
        if (meta.uploadDate && !r.upload_date) updates.upload_date = meta.uploadDate
        if (meta.title && r.title === r.video_id) updates.title = meta.title
        if (meta.channel && (!r.channel || r.channel === 'unknown')) updates.channel = meta.channel
        if (Object.keys(updates).length > 0) updateRecommendation(r.video_id, updates)
      }
      const ageDays = uploadDate ? ageDaysFromYmd(uploadDate) : null
      const ageStr = ageDays !== null ? `${ageDays}d since publish` : 'age unknown'
      const published = formatUploadDate(uploadDate)
      const affinity = tasteModel.getChannelAffinity(r.channel)
      const affinityStr = (!r.channel || r.channel === 'unknown') ? 'unknown channel' : `affinity ${affinity.toFixed(1)}`
      return `- [id=${r.video_id}] "${title}" by ${r.channel || 'unknown'} (${r.duration ?? '?'}, published ${published}, ${ageStr}, ${affinityStr})`
    }).join('\n')

    // Ask agent to compose conversational check-in message
    const checkInPrompt = [
      'IMPORTANT: Do NOT use any tools. Do NOT read files. Do NOT run commands. Just write the message text based on the data provided and return it. Nothing else.',
      '',
      `You are K2B's YouTube curator. Keith has ${pending.length} unwatched videos in his Watch list:`,
      '',
      videoSummaries,
      '',
      'Taste model:',
      tasteModel.toSummary(),
      '',
      'Compose a SHORT Telegram message (max 5 lines) that:',
      '1. Names each unwatched video by its actual title (the text between quotes above)',
      '2. Flags any published more than 60 days ago as potentially outdated (use the "since publish" age, not the recommendation age)',
      '3. Asks Keith: busy? not interested? want replacements?',
      '',
      'Keep it conversational, like a friend. Return ONLY the message text.',
    ].join('\n')
    const checkInMsg = await runStatelessQuery(checkInPrompt)

    if (checkInMsg) {
      await sendMessage(chatId, checkInMsg)
    }

    // Send video cards with info + buttons for each pending video
    setYtState({ pendingVideoIds: pending.map(r => r.video_id) })
    for (const rec of pending) {
      const link = `https://youtu.be/${rec.video_id}`
      // Send URL first for thumbnail preview
      await sendMessage(chatId, link)

      // Fetch actual publish date if not stored
      let publishDate = rec.upload_date ?? ''
      if (!publishDate) {
        publishDate = fetchUploadDate(rec.video_id)
        if (publishDate) {
          updateRecommendation(rec.video_id, { upload_date: publishDate })
        }
      }
      const displayDate = formatUploadDate(publishDate)

      // Send info card with verdict context + buttons
      const infoLines = [
        `<b>${rec.title}</b>`,
        `${rec.channel} -- ${rec.duration ?? '?'}`,
        `Published: ${displayDate}`,
        rec.verdict ? `\n${rec.verdict}` : '',
        rec.verdict_value ? `Value: ${rec.verdict_value}` : '',
      ].filter(Boolean).join('\n')

      const keyboard = new InlineKeyboard()
        .url('Watch', `https://youtu.be/${rec.video_id}`)
        .text('Watched', `youtube:watch:${rec.video_id}`)
        .text('Skip', `youtube:skip:${rec.video_id}`)
        .row()
        .text('Screen', `youtube:screen:${rec.video_id}`)

      await sendWithButtons(chatId, infoLines, [], keyboard)
    }

    await sendMessage(chatId, 'Still interested, or should I swap these out?')

    // Wait for Keith's response -- the bot message handler will route it back
    // via handleYouTubeAgentResponse() when youtubeAgentState.phase !== 'idle'
    return  // Cycle continues when Keith responds
  }

  // No pending videos -- go straight to finding new content
  await findNewContent(sendMessage, sendWithButtons, chatId)

  } catch (err) {
    logger.error({ err }, 'YouTube agent loop error')
    resetYtState()
  }
}

// ---------------------------------------------------------------------------
// Find new content (called from main loop and from handleYouTubeAgentResponse)
// ---------------------------------------------------------------------------

async function findNewContent(sendMessage: SendFn, sendWithButtons: SendWithButtonsFn, chatId: string): Promise<void> {
  try {
  setYtState({ phase: 'searching' })

  // Read vault context
  const vaultContext = readVaultContext()

  // Generate search queries + screen in ONE agent call
  const existingIds = readRecommendations().map(r => r.video_id)

  // Search YouTube via scripts
  const queries = generateSearchQueries(vaultContext)
  let allResults: Array<{ id: string; title: string; channel: string; duration_string: string; upload_date: string; view_count: number; query: string }> = []

  for (const q of queries) {
    try {
      const output = execSync(
        `"${resolve(K2B_PROJECT_ROOT, 'scripts/yt-search.sh')}" "${q.replace(/"/g, '\\"')}" --max 5`,
        { encoding: 'utf-8', timeout: 30_000 }
      ).trim()
      if (!output) continue
      const results = output.split('\n').map(line => {
        const parsed = JSON.parse(line)
        return { ...parsed, query: q }
      })
      allResults.push(...results)
    } catch (err) {
      logger.error({ err, query: q }, 'YouTube search failed')
    }
  }

  // Dedup against existing recommendations
  allResults = allResults.filter(r => !existingIds.includes(r.id))

  // Filter by taste model
  allResults = allResults.filter(r => {
    if (tasteModel.isChannelFlagged(r.channel)) return false
    const score = tasteModel.scoreCandidate(r.channel, [], r.upload_date, parseDuration(r.duration_string))
    return score >= 30
  })

  if (allResults.length === 0) {
    resetYtState()
    await sendMessage(chatId, 'Searched but nothing good came up this round. Will try again later.')
    return
  }

  // Take top 5-7 candidates for screening
  const candidates = allResults.slice(0, 7)

  // Enrich candidates with real metadata where the flat-playlist search left gaps.
  // yt-search.sh runs `yt-dlp --flat-playlist -j` which frequently returns empty
  // upload_date (and sometimes empty channel/duration). Fetch per-video metadata
  // so cards can show a real publish date instead of "unknown".
  for (const c of candidates) {
    if (!c.upload_date || !c.channel || !c.duration_string) {
      const meta = fetchVideoMetadata(c.id)
      if (meta.uploadDate && !c.upload_date) c.upload_date = meta.uploadDate
      if (meta.channel && !c.channel) c.channel = meta.channel
      if (meta.duration && !c.duration_string) c.duration_string = meta.duration
      if (meta.title && (!c.title || c.title === c.id)) c.title = meta.title
    }
  }

  // Screen via agent
  const candidateList = candidates.map(c =>
    `- "${c.title}" by ${c.channel} (${c.duration_string}, uploaded ${c.upload_date})`
  ).join('\n')

  const screeningResult = await runStatelessQuery(
    `IMPORTANT: You are screening videos and composing a Telegram message. Do NOT use any tools. Do NOT read files. Do NOT run commands. Analyze the data provided below, give verdicts, compose the message, and return JSON. Nothing else.\n\nYou are K2B's YouTube curator screening videos for Keith.\n\nKeith's context:\n${vaultContext}\n\nTaste model:\n${tasteModel.toSummary()}\n\nCandidates:\n${candidateList}\n\nFor each candidate, give a verdict: RECOMMEND / MAYBE / SKIP with a one-sentence reason.\nThen compose a SHORT Telegram message presenting your top 2-3 picks conversationally:\n- Why each is worth Keith's time\n- How it connects to what he's working on\n- Any caveats\n\nEnd with: "Want me to add these to your Watch list?"\n\nReturn JSON: { "verdicts": [{"index": 0, "verdict": "RECOMMEND", "reason": "..."}], "message": "..." }`,
  )

  if (!screeningResult) {
    resetYtState()
    await sendMessage(chatId, 'Screening failed. Will try again next cycle.')
    return
  }

  // Parse agent response
  let parsed: { verdicts: Array<{ index: number; verdict: string; reason: string }>; message: string }
  try {
    // Agent might wrap in markdown code block
    const jsonStr = screeningResult.replace(/```json?\n?/g, '').replace(/```/g, '').trim()
    parsed = JSON.parse(jsonStr)
  } catch {
    // If JSON parsing fails, just send the raw text
    await sendMessage(chatId, screeningResult)
    resetYtState()
    return
  }

  // Send conversational message
  if (parsed.message) {
    await sendMessage(chatId, parsed.message)
  }

  // Send cards for recommended videos
  const recommended = parsed.verdicts
    .filter(v => v.verdict === 'RECOMMEND' || v.verdict === 'MAYBE')
    .slice(0, 4)

  setYtState({ pendingVideoIds: [], phase: 'presenting-picks' })
  setYtPendingCandidates(new Map())

  for (const v of recommended) {
    const candidate = candidates[v.index]
    if (!candidate) continue

    const link = `https://youtu.be/${candidate.id}`
    // Send URL first for thumbnail preview
    await sendMessage(chatId, link)

    // Send info card with verdict + buttons
    const verdict = v.reason ?? ''
    const infoLines = [
      `<b>${candidate.title}</b>`,
      `${candidate.channel} -- ${candidate.duration_string}`,
      `Published: ${formatUploadDate(candidate.upload_date)}`,
      verdict ? `\n${verdict}` : '',
      `Verdict: ${v.verdict}`,
    ].filter(Boolean).join('\n')

    // Use the unified `skip` callback so new-pick cards go through the same
    // deduction+confirmation flow as Watch-list cards. The skip handler falls
    // back to pendingCandidates when the video isn't in JSONL.
    const keyboard = new InlineKeyboard()
      .text('Add to Watch', `youtube:agent-add:${candidate.id}`)
      .text('Skip', `youtube:skip:${candidate.id}`)
      .row()
      .text('Screen', `youtube:screen:${candidate.id}`)

    // Store as pending recommendation (not yet in JSONL) + cache metadata
    const currentState = getYtState()
    currentState.pendingVideoIds.push(candidate.id)
    setYtState({ pendingVideoIds: currentState.pendingVideoIds })

    const candidates_map = getYtPendingCandidates()
    candidates_map.set(candidate.id, {
      videoId: candidate.id,
      title: candidate.title,
      channel: candidate.channel,
      duration: candidate.duration_string,
      uploadDate: candidate.upload_date,
      verdict: v.verdict,
      reason: v.reason,
    })
    setYtPendingCandidates(candidates_map)

    await sendWithButtons(chatId, infoLines, [], keyboard)
  }

  await sendMessage(chatId, 'Want all of these, or pick the ones that catch your eye?')
  // Wait for Keith's response via buttons or text

  } catch (err) {
    logger.error({ err }, 'findNewContent error')
    resetYtState()
  }
}

// ---------------------------------------------------------------------------
// NOTE: Text-message routing used to live here (handleYouTubeAgentResponse,
// parseAndExecuteActions, youtubeKeywords) and dispatched Keith's messages
// to forced-choice prompts. Deleted in session-design-v3: the interactive
// agent now reads YouTube state on demand via the `youtube_get_pending` MCP
// tool in agent.ts and decides conversationally what to do. No text router
// means "show me both" about infographics can no longer be keyword-caught
// and handed to a forced-choice prompt that hallucinates add/skip actions.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function readVaultContext(): string {
  const lines: string[] = []

  // Last 7 daily notes
  const dailyDir = resolve(K2B_VAULT_PATH, 'Daily')
  if (existsSync(dailyDir)) {
    const dailyFiles = readdirSync(dailyDir)
      .filter(f => f.endsWith('.md'))
      .sort()
      .slice(-7)
    for (const f of dailyFiles) {
      try {
        const content = readFileSync(resolve(dailyDir, f), 'utf-8')
        // Just take first 500 chars for context
        lines.push(`[Daily ${f}]: ${content.slice(0, 500)}`)
      } catch { /* skip */ }
    }
  }

  // Active projects
  const projectsDir = resolve(K2B_VAULT_PATH, 'wiki', 'projects')
  if (existsSync(projectsDir)) {
    const projectFiles = readdirSync(projectsDir).filter(f => f.endsWith('.md') && f !== 'index.md')
    for (const f of projectFiles) {
      try {
        const content = readFileSync(resolve(projectsDir, f), 'utf-8')
        const statusMatch = content.match(/status:\s*(\w+)/)
        if (statusMatch && statusMatch[1] !== 'done' && statusMatch[1] !== 'archived') {
          const titleMatch = content.match(/^#\s+(.+)/m)
          lines.push(`[Project]: ${titleMatch?.[1] ?? f}`)
        }
      } catch { /* skip */ }
    }
  }

  return lines.join('\n').slice(0, 3000) // Cap context size
}

function generateSearchQueries(vaultContext: string): string[] {
  // Extract key themes from vault context for search queries
  // Simple approach: use predefined pillars + extract recent topics
  const queries = [
    'Claude Code agents automation 2026',
    'AI second brain knowledge management',
    'AI recruitment talent acquisition',
  ]

  // Add project-specific queries from vault context
  const projectMatches = vaultContext.match(/\[Project\]: (.+)/g)
  if (projectMatches) {
    for (const match of projectMatches.slice(0, 2)) {
      const name = match.replace('[Project]: ', '')
      queries.push(`${name} AI automation`)
    }
  }

  return queries.slice(0, 5)
}

function parseDuration(durationStr: string): number {
  if (!durationStr || durationStr === 'unknown') return 15 // default
  const parts = durationStr.split(':').map(Number)
  if (parts.length === 3) return parts[0] * 60 + parts[1]
  if (parts.length === 2) return parts[0]
  return 15
}

function logCycle(action: string, details: string): void {
  const logPath = resolve(K2B_VAULT_PATH, 'wiki', 'context', 'youtube-agent-log.jsonl')
  const entry = { ts: new Date().toISOString(), action, details }
  appendFileSync(logPath, JSON.stringify(entry) + '\n')
}
