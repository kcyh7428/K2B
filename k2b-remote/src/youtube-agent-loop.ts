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
  clearFromAgentState,
  addVideoToWatch,
  skipVideoFromWatch,
  expireVideoFromWatch,
  type VideoMetadata,
  type PendingCandidate,
  type YouTubeAgentState,
} from './youtube.js'
import { tasteModel } from './taste-model.js'
import { runAgent } from './agent.js'
import { getSession, setSession } from './db.js'
import { logger } from './logger.js'

// ---------------------------------------------------------------------------
// Session-aware agent wrapper
// ---------------------------------------------------------------------------
// Every agent call for a given chat must resume the same Claude Code session
// so that proactive YouTube messages and Keith's subsequent replies share one
// continuous conversation. Previously each runAgent() call here spawned a
// fresh session, which caused contradicting replies when Keith switched topic.

async function runAgentWithSession(
  chatId: string,
  prompt: string,
): Promise<{ text: string | null; newSessionId?: string; hadError?: boolean }> {
  const sessionId = getSession(chatId, 'youtube')
  const result = await runAgent(prompt, sessionId)
  if (result.newSessionId) setSession(chatId, result.newSessionId, 'youtube')
  return result
}

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
    const { text: checkInMsg } = await runAgentWithSession(chatId, checkInPrompt)

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

  const { text: screeningResult } = await runAgentWithSession(
    chatId,
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
// Handle Keith's response when agent is active
// ---------------------------------------------------------------------------

export async function handleYouTubeAgentResponse(
  text: string,
  sendMessage: SendFn,
  sendWithButtons: SendWithButtonsFn,
  chatId: string
): Promise<boolean> {
  // Returns true if the message was handled, false if not a YouTube agent context

  // Read state once -- use this snapshot throughout to avoid race conditions
  const state = getYtState()
  if (state.phase === 'idle') return false

  const lower = text.toLowerCase().trim()

  // Escape hatch: if the message clearly isn't about the pending YouTube videos,
  // let it fall through to regular chat instead of swallowing it as a YouTube response.
  // This prevents "show me the investment infographic" from being eaten when
  // the YouTube agent is waiting for a response about video recommendations.
  const youtubeKeywords = ['add', 'skip', 'pass', 'keep', 'watch', 'busy', 'not now', 'later',
    'not interested', 'swap', 'replace', 'screen', 'youtube', 'video', 'claude', 'all', 'them',
    'first', 'second', 'both', 'neither', 'outdated', 'old', 'stale']
  const hasVideoId = state.pendingVideoIds.some(id => lower.includes(id.toLowerCase()))
  const hasYoutubeKeyword = youtubeKeywords.some(kw => lower.includes(kw))
  if (!hasVideoId && !hasYoutubeKeyword) {
    // Not a YouTube response -- let it through to regular chat
    logger.info({ phase: state.phase, text: text.slice(0, 80) }, 'YouTube handler: message not YouTube-related, passing through')
    return false
  }

  if (state.phase === 'checking-watch') {
    if (lower.includes('busy') || lower.includes('not now') || lower.includes('later')) {
      resetYtState()
      await sendMessage(chatId, 'No worries. Will check in next cycle.')
      return true
    }

    if (lower.includes('skip') || lower.includes('not interested') || lower.includes('swap') || lower.includes('replace')) {
      const toSkip = [...state.pendingVideoIds]
      for (const vid of toSkip) {
        try {
          skipVideoFromWatch(vid, 'user-requested-swap', 'text-reply:swap', text)
        } catch (err) {
          logger.error({ err, vid }, 'skipVideoFromWatch failed during swap-all')
        }
      }
      await sendMessage(chatId, 'Cleared. Finding fresh content...')
      await findNewContent(sendMessage, sendWithButtons, chatId)
      return true
    }

    // Complex response about videos -- parse into structured actions
    const pendingRecs = readRecommendations().filter(r => state.pendingVideoIds.includes(r.video_id))
    const executed = await parseAndExecuteActions(text, pendingRecs, 'watch-list', sendMessage, chatId)
    if (executed.actedCount > 0 && executed.remainingCount === 0) {
      resetYtState()
    }
    return true
  }

  if (state.phase === 'presenting-picks') {
    if (lower.includes('add') && (lower.includes('all') || lower.includes('them'))) {
      const toAdd = [...state.pendingVideoIds]
      const candidatesMap = getYtPendingCandidates()
      let addedCount = 0
      for (const vid of toAdd) {
        const cand = candidatesMap.get(vid)
        if (!cand || !cand.title || cand.title === vid || !cand.channel || cand.channel === 'unknown') {
          logger.warn({ vid, hasCached: !!cand }, 'add-all rejected: no real metadata')
          continue
        }
        const meta: VideoMetadata = {
          videoId: vid,
          title: cand.title,
          channel: cand.channel,
          duration: cand.duration || undefined,
          uploadDate: cand.uploadDate || undefined,
          verdict: cand.reason || undefined,
          verdictValue: (cand.verdict as 'HIGH' | 'MEDIUM' | 'LOW' | undefined) || undefined,
        }
        try {
          addVideoToWatch(meta, 'text-reply:add-all')
          addedCount++
        } catch (err) {
          logger.error({ err, vid }, 'addVideoToWatch failed during add-all')
        }
      }
      resetYtState()
      await sendMessage(chatId, `Added ${addedCount} to your Watch list.`)
      return true
    }

    if (lower.includes('no') || lower.includes('skip') || lower.includes('pass')) {
      resetYtState()
      await sendMessage(chatId, 'No problem. Will find different content next time.')
      return true
    }

    // Complex response about videos -- parse into structured actions
    const pendingRecs = readRecommendations().filter(r => state.pendingVideoIds.includes(r.video_id))
    const executed = await parseAndExecuteActions(text, pendingRecs, 'new-picks', sendMessage, chatId)
    if (executed.actedCount > 0 && executed.remainingCount === 0) {
      resetYtState()
    }
    return true
  }

  return false
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface ParsedActionPlan {
  actions: Array<{ videoId: string; action: 'keep' | 'skip' | 'add' }>
  message: string
}

interface ActionablePending {
  videoId: string
  title: string
  channel: string
  uploadDate: string
}

async function parseAndExecuteActions(
  userText: string,
  pendingRecs: YouTubeRecommendation[],
  phase: 'watch-list' | 'new-picks',
  sendMessage: SendFn,
  chatId: string,
): Promise<{ actedCount: number; remainingCount: number }> {
  // Build the actionable list from either pendingRecs (watch-list phase)
  // or pendingCandidates cache (new-picks phase, not yet in JSONL)
  const currentCandidates = getYtPendingCandidates()
  const currentPendingIds = getYtState().pendingVideoIds
  const actionable: ActionablePending[] = phase === 'watch-list'
    ? pendingRecs.map(r => ({ videoId: r.video_id, title: r.title ?? r.video_id, channel: r.channel || 'unknown', uploadDate: r.upload_date ?? '' }))
    : currentPendingIds
        .map((vid: string) => currentCandidates.get(vid))
        .filter((c): c is PendingCandidate => !!c)
        .map((c: PendingCandidate) => ({ videoId: c.videoId, title: c.title, channel: c.channel, uploadDate: c.uploadDate }))

  if (actionable.length === 0) {
    await sendMessage(chatId, 'No pending videos to act on.')
    return { actedCount: 0, remainingCount: 0 }
  }

  const videoList = actionable.map(r => {
    const publishDate = formatUploadDate(r.uploadDate)
    const ageDays = r.uploadDate ? ageDaysFromYmd(r.uploadDate) : null
    const ageStr = ageDays !== null ? `, ${ageDays}d since publish` : ''
    return `  ${r.videoId}: "${r.title}" by ${r.channel} (published ${publishDate}${ageStr})`
  }).join('\n')

  const allowedActions = phase === 'watch-list'
    ? '"keep" (leave in Watch list) or "skip" (remove from Watch list)'
    : '"add" (add to Watch list) or "skip" (discard this recommendation)'

  const prompt = [
    'IMPORTANT: Return ONLY a valid JSON object. Do NOT use tools. Do NOT read files. Do NOT run commands.',
    '',
    `Keith said: "${userText}"`,
    '',
    `He is reviewing these pending videos:`,
    videoList,
    '',
    `Interpret his intent and return JSON with this exact shape:`,
    '{',
    '  "actions": [',
    `    { "videoId": "<id from list above>", "action": ${allowedActions.replace(/\(.+?\)/g, '').trim()} }`,
    '  ],',
    '  "message": "Short confirmation text (1-2 sentences) for Telegram"',
    '}',
    '',
    'Rules:',
    `- Include one action entry for EACH pending video. Use exactly the videoIds from the list.`,
    `- If Keith mentions "outdated", "old", "2024", "2025": those match videos with the older publish dates.`,
    `- If he says "keep the Claude one": match the video whose title contains "Claude".`,
    `- action must be one of: ${allowedActions}`,
    `- If intent is unclear, return message asking for clarification and empty actions array.`,
    '',
    'Return ONLY the JSON, no markdown fences, no prose.',
  ].join('\n')

  let parsed: ParsedActionPlan
  try {
    const { text: raw } = await runAgentWithSession(chatId, prompt)
    if (!raw) throw new Error('empty response')
    const jsonStr = raw.replace(/```json?\n?/g, '').replace(/```/g, '').trim()
    parsed = JSON.parse(jsonStr) as ParsedActionPlan
  } catch (err) {
    logger.error({ err, userText }, 'Failed to parse action plan')
    await sendMessage(chatId, "Sorry, I couldn't parse that. Please tap the buttons on each card to add or skip individually.")
    return { actedCount: 0, remainingCount: pendingRecs.length }
  }

  if (!parsed.actions || parsed.actions.length === 0) {
    if (parsed.message) await sendMessage(chatId, parsed.message)
    return { actedCount: 0, remainingCount: pendingRecs.length }
  }

  const actionableMap = new Map(actionable.map(a => [a.videoId, a]))
  let actedCount = 0

  for (const a of parsed.actions) {
    const pending = actionableMap.get(a.videoId)
    if (!pending) continue

    try {
      if (a.action === 'skip') {
        // Canonical skip. For new-picks phase the video isn't in JSONL yet,
        // so skipVideoFromWatch becomes a no-op on JSONL + a taste-model learn.
        if (phase === 'watch-list') {
          skipVideoFromWatch(a.videoId, 'text-reply-skip', `text-reply:${phase}`, userText)
        } else {
          // new-picks: just train the taste model and clear state; no playlist op needed
          // (the video was never added).
          if (pending.channel && pending.channel !== 'unknown') {
            tasteModel.recordAction(a.videoId, pending.channel, 'skip', userText)
          }
          clearFromAgentState(a.videoId)
        }
        actedCount++
      } else if (a.action === 'add' && phase === 'new-picks') {
        // Canonical add-to-Watch. Requires real metadata from pendingCandidates.
        const cand = currentCandidates.get(a.videoId)
        if (!cand || !cand.title || cand.title === a.videoId || !cand.channel || cand.channel === 'unknown') {
          logger.warn({ videoId: a.videoId, hasCached: !!cand }, 'text-reply add rejected: no real metadata')
          continue
        }
        const meta: VideoMetadata = {
          videoId: a.videoId,
          title: cand.title,
          channel: cand.channel,
          duration: cand.duration || undefined,
          uploadDate: cand.uploadDate || undefined,
          verdict: cand.reason || undefined,
          verdictValue: (cand.verdict as 'HIGH' | 'MEDIUM' | 'LOW' | undefined) || undefined,
        }
        addVideoToWatch(meta, `text-reply:${phase}`)
        actedCount++
      } else if (a.action === 'keep') {
        // Leave as-is, just clear from pending state
        clearFromAgentState(a.videoId)
        actedCount++
      }
    } catch (err) {
      logger.error({ err, videoId: a.videoId, action: a.action }, 'Action execution failed')
    }
  }

  if (parsed.message) {
    await sendMessage(chatId, parsed.message)
  }

  return { actedCount, remainingCount: getYtState().pendingVideoIds.length }
}

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
