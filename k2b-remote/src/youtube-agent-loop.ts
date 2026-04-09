import { readFileSync, readdirSync, existsSync, appendFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { execSync, execFileSync } from 'node:child_process'
import { InlineKeyboard } from 'grammy'
import { K2B_VAULT_PATH, K2B_PROJECT_ROOT } from './config.js'
import { readRecommendations, appendRecommendation, updateRecommendation, playlistAdd, getPlaylistVideoIds, WATCH_PLAYLIST_ID, type YouTubeRecommendation } from './youtube.js'
import { tasteModel } from './taste-model.js'
import { runAgent } from './agent.js'
import { logger } from './logger.js'

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

function formatUploadDate(raw: string): string {
  if (!raw || raw === 'NA') return 'unknown'
  // Handle YYYYMMDD format from yt-dlp
  if (/^\d{8}$/.test(raw)) return `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6, 8)}`
  return raw
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface YouTubeAgentState {
  phase: 'idle' | 'checking-watch' | 'presenting-picks' | 'searching'
  pendingVideoIds: string[]
  sessionId?: string
  startedAt: string
  lastCycleAt: string | null
  cyclesToday: number
  lastCycleDate: string | null  // YYYY-MM-DD, reset counter when date changes
}

export const youtubeAgentState: YouTubeAgentState = {
  phase: 'idle',
  pendingVideoIds: [],
  startedAt: '',
  lastCycleAt: null,
  cyclesToday: 0,
  lastCycleDate: null,
}

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
  if (youtubeAgentState.phase !== 'idle') {
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
  if (youtubeAgentState.lastCycleDate !== today) {
    youtubeAgentState.cyclesToday = 0
    youtubeAgentState.lastCycleDate = today
  }

  // Step 1: Check existing Watch list ----------------------------------------

  try {
  youtubeAgentState.phase = 'checking-watch'
  youtubeAgentState.startedAt = new Date().toISOString()

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
          updateRecommendation(rec.video_id, { status: 'expired', outcome: 'not-in-playlist' })
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

    // Build context for each pending video
    const videoSummaries = pending.map(r => {
      const ageDays = r.recommended_date
        ? Math.floor((Date.now() - new Date(r.recommended_date).getTime()) / (1000 * 60 * 60 * 24))
        : 0
      const affinity = tasteModel.getChannelAffinity(r.channel)
      const stale = r.topics ? tasteModel.isVideoStale(r.recommended_date, r.topics) : false
      return `- "${r.title}" by ${r.channel} (${r.duration ?? '?'}, ${ageDays}d old, affinity: ${affinity.toFixed(1)}${stale ? ', STALE' : ''})`
    }).join('\n')

    // Ask agent to compose conversational check-in message
    const { text: checkInMsg } = await runAgent(
      `IMPORTANT: You are composing a SHORT message for Telegram. Do NOT use any tools. Do NOT read files. Do NOT run commands. Just write the message text based on the data provided below and return it. Nothing else.\n\nYou are K2B's YouTube curator. Keith has ${pending.length} unwatched videos in his Watch list:\n\n${videoSummaries}\n\nTaste model:\n${tasteModel.toSummary()}\n\nCompose a SHORT Telegram message (max 5 lines) that:\n1. Mentions the unwatched videos naturally\n2. Flags any that are stale\n3. Asks Keith: busy? not interested? want replacements?\n\nKeep it conversational, like a friend. Return ONLY the message text.`
    )

    if (checkInMsg) {
      await sendMessage(chatId, checkInMsg)
    }

    // Send video cards with info + buttons for each pending video
    youtubeAgentState.pendingVideoIds = pending.map(r => r.video_id)
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
    youtubeAgentState.phase = 'idle'
    youtubeAgentState.pendingVideoIds = []
  }
}

// ---------------------------------------------------------------------------
// Find new content (called from main loop and from handleYouTubeAgentResponse)
// ---------------------------------------------------------------------------

async function findNewContent(sendMessage: SendFn, sendWithButtons: SendWithButtonsFn, chatId: string): Promise<void> {
  try {
  youtubeAgentState.phase = 'searching'

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
    youtubeAgentState.phase = 'idle'
    await sendMessage(chatId, 'Searched but nothing good came up this round. Will try again later.')
    return
  }

  // Take top 5-7 candidates for screening
  const candidates = allResults.slice(0, 7)

  // Screen via agent
  const candidateList = candidates.map(c =>
    `- "${c.title}" by ${c.channel} (${c.duration_string}, uploaded ${c.upload_date})`
  ).join('\n')

  const { text: screeningResult } = await runAgent(
    `IMPORTANT: You are screening videos and composing a Telegram message. Do NOT use any tools. Do NOT read files. Do NOT run commands. Analyze the data provided below, give verdicts, compose the message, and return JSON. Nothing else.\n\nYou are K2B's YouTube curator screening videos for Keith.\n\nKeith's context:\n${vaultContext}\n\nTaste model:\n${tasteModel.toSummary()}\n\nCandidates:\n${candidateList}\n\nFor each candidate, give a verdict: RECOMMEND / MAYBE / SKIP with a one-sentence reason.\nThen compose a SHORT Telegram message presenting your top 2-3 picks conversationally:\n- Why each is worth Keith's time\n- How it connects to what he's working on\n- Any caveats\n\nEnd with: "Want me to add these to your Watch list?"\n\nReturn JSON: { "verdicts": [{"index": 0, "verdict": "RECOMMEND", "reason": "..."}], "message": "..." }`
  )

  if (!screeningResult) {
    youtubeAgentState.phase = 'idle'
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
    youtubeAgentState.phase = 'idle'
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

  youtubeAgentState.pendingVideoIds = []
  youtubeAgentState.phase = 'presenting-picks'

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

    const keyboard = new InlineKeyboard()
      .text('Add to Watch', `youtube:agent-add:${candidate.id}`)
      .text('Skip', `youtube:agent-skip:${candidate.id}`)
      .row()
      .text('Screen', `youtube:screen:${candidate.id}`)

    // Store as pending recommendation (not yet in JSONL)
    youtubeAgentState.pendingVideoIds.push(candidate.id)

    await sendWithButtons(chatId, infoLines, [], keyboard)
  }

  await sendMessage(chatId, 'Want all of these, or pick the ones that catch your eye?')
  // Wait for Keith's response via buttons or text

  } catch (err) {
    logger.error({ err }, 'findNewContent error')
    youtubeAgentState.phase = 'idle'
    youtubeAgentState.pendingVideoIds = []
  }
}

// ---------------------------------------------------------------------------
// Handle Keith's response when agent is active
// ---------------------------------------------------------------------------

export async function handleYouTubeAgentResponse(
  text: string,
  sendMessage: SendFn,
  sendWithButtons: SendWithButtonsFn,
  chatId: string
): Promise<boolean> {
  // Returns true if the message was handled, false if not a YouTube agent context

  if (youtubeAgentState.phase === 'idle') return false

  const lower = text.toLowerCase().trim()

  if (youtubeAgentState.phase === 'checking-watch') {
    if (lower.includes('busy') || lower.includes('not now') || lower.includes('later')) {
      youtubeAgentState.phase = 'idle'
      await sendMessage(chatId, 'No worries. Will check in next cycle.')
      return true
    }

    if (lower.includes('skip') || lower.includes('not interested') || lower.includes('swap') || lower.includes('replace')) {
      // Expire all pending
      for (const vid of youtubeAgentState.pendingVideoIds) {
        updateRecommendation(vid, { status: 'expired', outcome: 'agent-cleared' })
      }
      youtubeAgentState.pendingVideoIds = []
      await sendMessage(chatId, 'Cleared. Finding fresh content...')
      await findNewContent(sendMessage, sendWithButtons, chatId)
      return true
    }

    // If Keith says something else, use agent to interpret
    const { text: interpretation } = await runAgent(
      `Keith said: "${text}" in response to a YouTube Watch list check-in. He had ${youtubeAgentState.pendingVideoIds.length} unwatched videos. Interpret his response. Does he want to: keep all, skip all, keep some, or is he busy? Reply naturally and take appropriate action. If unclear, ask a clarifying question. Return ONLY the message text for Telegram.`
    )
    if (interpretation) {
      await sendMessage(chatId, interpretation)
    }
    return true
  }

  if (youtubeAgentState.phase === 'presenting-picks') {
    if (lower.includes('add') && (lower.includes('all') || lower.includes('them'))) {
      // Add all pending to Watch
      for (const vid of youtubeAgentState.pendingVideoIds) {
        try {
          playlistAdd(WATCH_PLAYLIST_ID, vid)
          // Create JSONL entry
          const today = new Date().toISOString().slice(0, 10)
          appendRecommendation({
            ts: new Date().toISOString(),
            video_id: vid,
            title: vid, // will be enriched later
            channel: 'unknown',
            playlist: 'K2B Watch',
            recommended_date: today,
            status: 'nudge_sent',
            nudge_sent: true,
            nudge_date: today,
            outcome: null,
            rating: null,
            promoted_to: null,
            vault_note: null,
          })
        } catch (err) {
          logger.error({ err, vid }, 'Failed to add to Watch')
        }
      }
      youtubeAgentState.phase = 'idle'
      youtubeAgentState.pendingVideoIds = []
      await sendMessage(chatId, 'Added to your Watch list.')
      return true
    }

    if (lower.includes('no') || lower.includes('skip') || lower.includes('pass')) {
      youtubeAgentState.phase = 'idle'
      youtubeAgentState.pendingVideoIds = []
      await sendMessage(chatId, 'No problem. Will find different content next time.')
      return true
    }

    // Complex response -- let agent interpret, keep phase active so buttons still work
    const { text: interpretation } = await runAgent(
      `IMPORTANT: Do NOT use any tools. Just interpret and reply.\n\nKeith said: "${text}" in response to YouTube video recommendations. The pending videos are: ${youtubeAgentState.pendingVideoIds.join(', ')}. Interpret: does he want to add all, some specific ones, or skip? Reply naturally. Return ONLY the Telegram message text.`
    )
    if (interpretation) {
      await sendMessage(chatId, interpretation)
    }
    // Don't reset phase -- Keith can still tap buttons on individual cards
    return true
  }

  return false
}

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
