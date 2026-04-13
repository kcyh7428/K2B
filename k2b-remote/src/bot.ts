import { request as httpsRequest } from 'node:https'
import { execSync, execFileSync } from 'node:child_process'
import { Bot, Context, InlineKeyboard } from 'grammy'
import { HttpsProxyAgent } from 'https-proxy-agent'
import {
  TELEGRAM_BOT_TOKEN,
  ALLOWED_CHAT_ID,
  MAX_MESSAGE_LENGTH,
  TYPING_REFRESH_MS,
  HTTP_PROXY,
} from './config.js'
import { getSession, setSession, clearSession, getRecentMemoriesForDisplay, getMemoryCount } from './db.js'
import { runAgent } from './agent.js'
import { buildMemoryContext, saveConversationTurn } from './memory.js'
import { voiceCapabilities, transcribeAudio } from './voice.js'
import {
  updateRecommendation,
  getPendingNudges,
  readRecommendations,
  appendFeedbackSignal,
  playlistAdd,
  playlistRemove,
  WATCH_PLAYLIST_ID,
  SCREEN_PLAYLIST_ID,
  // Canonical state functions -- every YouTube state change goes through these
  addVideoToWatch,
  skipVideoFromWatch,
  markVideoWatched,
  moveVideoToScreen,
  skipVideoFromScreen,
  clearFromAgentState,
  getYtState,
  setYtState,
  resetYtState,
  getYtPendingCandidates,
  setYtPendingCandidates,
  type VideoMetadata,
} from './youtube.js'
import { tasteModel } from './taste-model.js'
import { handleYouTubeAgentResponse } from './youtube-agent-loop.js'
import { downloadMedia, buildPhotoMessage, buildDocumentMessage } from './media.js'
import { logger } from './logger.js'
import { markObservationStart, logObservations } from './observe.js'

// --- Telegram formatting ---

export function formatForTelegram(text: string): string {
  // Extract and protect code blocks
  const codeBlocks: string[] = []
  let result = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_match, lang, code) => {
    const idx = codeBlocks.length
    const escaped = escapeHtml(code.trimEnd())
    codeBlocks.push(lang ? `<pre><code class="language-${lang}">${escaped}</code></pre>` : `<pre>${escaped}</pre>`)
    return `\x00CB${idx}\x00`
  })

  // Protect inline code
  const inlineCodes: string[] = []
  result = result.replace(/`([^`]+)`/g, (_match, code) => {
    const idx = inlineCodes.length
    inlineCodes.push(`<code>${escapeHtml(code)}</code>`)
    return `\x00IC${idx}\x00`
  })

  // Escape HTML in remaining text
  result = escapeHtml(result)

  // Headings
  result = result.replace(/^#{1,6}\s+(.+)$/gm, '<b>$1</b>')

  // Bold
  result = result.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
  result = result.replace(/__(.+?)__/g, '<b>$1</b>')

  // Italic
  result = result.replace(/\*(.+?)\*/g, '<i>$1</i>')
  result = result.replace(/_(.+?)_/g, '<i>$1</i>')

  // Strikethrough
  result = result.replace(/~~(.+?)~~/g, '<s>$1</s>')

  // Links
  result = result.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2">$1</a>')

  // Checkboxes
  result = result.replace(/- \[ \]/g, '\u2610')
  result = result.replace(/- \[x\]/g, '\u2611')

  // Strip horizontal rules
  result = result.replace(/^---+$/gm, '')
  result = result.replace(/^\*\*\*+$/gm, '')

  // Restore code blocks and inline code
  for (let i = 0; i < codeBlocks.length; i++) {
    result = result.replace(`\x00CB${i}\x00`, codeBlocks[i])
  }
  for (let i = 0; i < inlineCodes.length; i++) {
    result = result.replace(`\x00IC${i}\x00`, inlineCodes[i])
  }

  return result.trim()
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

export function splitMessage(text: string, limit = MAX_MESSAGE_LENGTH): string[] {
  if (text.length <= limit) return [text]

  const chunks: string[] = []
  let remaining = text

  while (remaining.length > 0) {
    if (remaining.length <= limit) {
      chunks.push(remaining)
      break
    }

    // Find last newline before limit
    let splitAt = remaining.lastIndexOf('\n', limit)
    if (splitAt <= 0) {
      // Find last space
      splitAt = remaining.lastIndexOf(' ', limit)
    }
    if (splitAt <= 0) {
      splitAt = limit
    }

    chunks.push(remaining.slice(0, splitAt))
    remaining = remaining.slice(splitAt).trimStart()
  }

  return chunks
}

function isAuthorised(chatId: number): boolean {
  if (!ALLOWED_CHAT_ID) return true // first-run mode
  return String(chatId) === ALLOWED_CHAT_ID
}

// --- Main message handler ---

async function handleMessage(
  ctx: Context,
  rawText: string,
  forceVoiceReply = false
): Promise<void> {
  const chatId = String(ctx.chat?.id)
  if (!chatId) return

  // Build memory context
  const memoryContext = await buildMemoryContext(chatId, rawText)
  const fullMessage = memoryContext + rawText

  // Get existing session
  const sessionId = getSession(chatId, 'interactive')

  // Start typing indicator
  let typingInterval: ReturnType<typeof setInterval> | undefined
  const sendTyping = async () => {
    try {
      await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    } catch {
      // ignore typing errors
    }
  }
  await sendTyping()
  typingInterval = setInterval(sendTyping, TYPING_REFRESH_MS)

  const obsMarker = markObservationStart()

  try {
    const { text, newSessionId, hadError } = await runAgent(fullMessage, sessionId, sendTyping)

    // Log observations (vault file changes from this agent run)
    logObservations(obsMarker, newSessionId ?? sessionId ?? 'telegram-interactive', rawText)

    // Save session
    if (newSessionId) {
      setSession(chatId, newSessionId, 'interactive')
    }

    // Save to memory
    if (text) {
      await saveConversationTurn(chatId, rawText, text)
    }

    // Send response
    const response = text ?? '(no response from agent)'
    const formatted = formatForTelegram(response)
    const chunks = splitMessage(formatted)

    for (const chunk of chunks) {
      try {
        await ctx.api.sendMessage(ctx.chat!.id, chunk, { parse_mode: 'HTML' })
      } catch {
        // Fallback to plain text if HTML parsing fails
        await ctx.api.sendMessage(ctx.chat!.id, chunk.replace(/<[^>]+>/g, ''))
      }
    }

    // After YouTube-related messages, send appropriate buttons
    // Skip if agent returned an error or if the message is just a URL (not a command)
    const lowerText = rawText.toLowerCase()
    const isYouTubeRelated = lowerText.includes('youtube') && !rawText.match(/^https?:\/\//)
    if (isYouTubeRelated && !hadError) {
      if (rawText.toLowerCase().includes('screen')) {
        const screenCount = await sendScreenOptions(chatId)
        if (screenCount > 0) {
          logger.info({ screenCount }, 'Sent YouTube screen options after message')
        }
      } else {
        const nudged = await sendPendingNudges(chatId)
        if (nudged > 0) {
          logger.info({ nudged }, 'Sent YouTube nudge buttons after message')
        }
      }
    }
  } finally {
    if (typingInterval) clearInterval(typingInterval)
  }
}

// --- YouTube callback handler ---

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
    const rec = readRecommendations().find(r => r.video_id === videoId)
    const prompt = `You are K2B. Process YouTube video https://www.youtube.com/watch?v=${videoId}.
Get the transcript (if Chinese/Mandarin, extract audio with 'scripts/yt-playlist-poll.sh --extract-audio <url> /tmp/k2b-yt-audio/', then transcribe chunks via Groq Whisper (GROQ_API_KEY from k2b-remote/.env)).
Use the playlist prompt_focus if the video is tracked in wiki/context/youtube-recommended.jsonl. Produce:

1. Concise highlights summary with key points and timestamps for the best segments
2. Your honest assessment: Is this worth Keith's time? He's deeply familiar with AI, builds Claude Code skills (18+), runs a full AI second brain (K2B). Be direct -- don't pad weak content.
3. End with a short question asking Keith what he thinks -- worth keeping or skip?

Keep it concise for Telegram. When Keith responds with his feedback:
- Update youtube-recommended.jsonl: set value_signal and feedback_text for video ${videoId}
- Append to youtube-feedback-signals.jsonl: signal_type "value_feedback", signal (categorize as exactly-my-level/gave-idea/good-but-basic/not-worth-it), signal_text (Keith's actual words)
- If Keith wants to save something (content idea, feature, insight), create the vault note via k2b-vault-writer
- If Keith wants to move the video to a category playlist (K2B Claude, K2B Recruit, etc.), use scripts/yt-playlist-add.sh`
    const highlightsMarker = markObservationStart()
    const priorSessionId = getSession(chatId, 'interactive')
    const { text, newSessionId } = await runAgent(prompt, priorSessionId)
    if (newSessionId) setSession(chatId, newSessionId, 'interactive')
    logObservations(highlightsMarker, `youtube-highlights-${videoId}`, prompt)
    const result = text ?? '(could not generate highlights)'

    updateRecommendation(videoId, {
      status: 'highlights_sent',
      outcome: 'highlights',
    })

    const formatted = formatForTelegram(result)
    const chunks = splitMessage(formatted)
    for (const chunk of chunks) {
      try {
        await ctx.api.sendMessage(ctx.chat!.id, chunk, { parse_mode: 'HTML' })
      } catch {
        await ctx.api.sendMessage(ctx.chat!.id, chunk.replace(/<[^>]+>/g, ''))
      }
    }

  } else if (action === 'watch') {
    // Canonical mark-watched path
    try {
      markVideoWatched(videoId, 'button:watch')
      await ctx.api.sendMessage(ctx.chat!.id, `Marked as watched. Enjoy!`)
    } catch (err) {
      logger.error({ err, videoId }, 'markVideoWatched failed')
      await ctx.api.sendMessage(ctx.chat!.id, 'Marked as watched (with tracking errors, see logs).')
    }

  } else if (action === 'comment') {
    awaitingComment.set(chatId, videoId)
    await ctx.api.sendMessage(ctx.chat!.id, "What's your take?")

  } else if (action === 'screen') {
    // Canonical move-to-Screen path
    try {
      moveVideoToScreen(videoId, 'button:screen')
      await ctx.api.sendMessage(ctx.chat!.id, 'Sent to Screen.')
    } catch (err) {
      logger.error({ err, videoId }, 'moveVideoToScreen failed')
      await ctx.api.sendMessage(ctx.chat!.id, 'Tried to move to Screen, but tracking failed. See logs.')
    }

  } else if (action === 'skip') {
    // Step 1 of the skip flow: deduce a likely reason and ask Keith to confirm.
    // Works for BOTH Watch-list videos (rec in JSONL) and new-pick candidates
    // (only in pendingCandidates). The actual mutation is deferred to skip-confirm
    // / skip-other / skip-reason-typed to avoid split-phase writes.
    const rec = readRecommendations().find(r => r.video_id === videoId)
    const cand = getYtPendingCandidates().get(videoId)
    const title = rec?.title ?? cand?.title ?? 'this video'
    // Normalize into a shape deduceSkipReason understands
    const deductionSource = rec ?? (cand ? { channel: cand.channel, upload_date: cand.uploadDate, topics: [] as string[] } : undefined)
    const deducedReason = tasteModel.deduceSkipReason(deductionSource)

    const keyboard = new InlineKeyboard()
      .text('Yeah', `youtube:skip-confirm:${videoId}`)
      .text('No, other reason', `youtube:skip-other:${videoId}`)

    await ctx.api.sendMessage(
      ctx.chat!.id,
      `About to skip "${title}". ${deducedReason}`,
      { reply_markup: keyboard }
    )

  } else if (action === 'skip-confirm') {
    // Keith confirmed the deduced reason -- execute the skip now.
    // Watch-list path: rec exists in JSONL -> canonical skipVideoFromWatch.
    // New-pick path: no rec, only pendingCandidate -> lightweight skip (no
    // playlist op since never added, no JSONL update since never written,
    // but still train taste model and log the feedback signal).
    const rec = readRecommendations().find(r => r.video_id === videoId)
    const cand = getYtPendingCandidates().get(videoId)
    const deductionSource = rec ?? (cand ? { channel: cand.channel, upload_date: cand.uploadDate, topics: [] as string[] } : undefined)
    const reason = tasteModel.deduceSkipReasonKey(deductionSource)

    try {
      if (rec) {
        skipVideoFromWatch(videoId, reason, 'button:skip-confirm')
      } else if (cand) {
        // New-pick skip: train taste model, log signal, clear state
        appendFeedbackSignal(videoId, 'skip_reason', reason)
        if (cand.channel && cand.channel !== 'unknown') {
          tasteModel.recordAction(videoId, cand.channel, 'skip', reason)
        }
        clearFromAgentState(videoId)
      } else {
        logger.warn({ videoId }, 'skip-confirm: no rec or pendingCandidate, clearing state only')
        clearFromAgentState(videoId)
      }
      await ctx.api.sendMessage(ctx.chat!.id, 'Skipped. Got it.')
    } catch (err) {
      logger.error({ err, videoId }, 'skip-confirm failed')
      await ctx.api.sendMessage(ctx.chat!.id, 'Skip failed, see logs.')
    }

  } else if (action === 'skip-other') {
    // Keith says the deduced reason is wrong -- ask for the real one
    awaitingComment.set(chatId, `skip:${videoId}`)
    await ctx.api.sendMessage(ctx.chat!.id, "What's off about it?")

  } else if (action === 'agent-add') {
    // Canonical add-to-Watch path. REQUIRES real metadata from pendingCandidates
    // (populated by handleDirectYouTubeUrl or agent loop's findNewContent).
    // Refuses to save placeholder data.
    const cached = getYtPendingCandidates().get(videoId)
    if (!cached || !cached.title || cached.title === videoId || !cached.channel || cached.channel === 'unknown') {
      logger.warn({ videoId, hasCached: !!cached }, 'agent-add rejected: no real metadata in cache')
      await ctx.api.sendMessage(
        ctx.chat!.id,
        "Couldn't add -- metadata for this video isn't available. Please paste the URL again so I can refetch."
      )
      return
    }
    const meta: VideoMetadata = {
      videoId,
      title: cached.title,
      channel: cached.channel,
      duration: cached.duration,
      uploadDate: cached.uploadDate,
      verdict: cached.reason,
      verdictValue: cached.verdict as 'HIGH' | 'MEDIUM' | 'LOW' | undefined,
    }
    try {
      addVideoToWatch(meta, 'button:agent-add')
      await ctx.api.sendMessage(ctx.chat!.id, `Added "${cached.title}" to Watch list.`)
    } catch (err) {
      logger.error({ err, videoId }, 'addVideoToWatch failed')
      await ctx.api.sendMessage(ctx.chat!.id, 'Add failed, see logs.')
    }

  } else if (action === 'agent-skip') {
    // LEGACY: new recommendation cards now use 'skip' for the deduction+
    // confirmation flow. This handler remains as a safety net for any stale
    // cards still in Keith's Telegram history pointing at the old callback.
    // Behaviour: redirect to the same deduction flow as `skip`.
    const cand = getYtPendingCandidates().get(videoId)
    const title = cand?.title ?? 'this video'
    const deductionSource = cand ? { channel: cand.channel, upload_date: cand.uploadDate, topics: [] as string[] } : undefined
    const deducedReason = tasteModel.deduceSkipReason(deductionSource)

    const keyboard = new InlineKeyboard()
      .text('Yeah', `youtube:skip-confirm:${videoId}`)
      .text('No, other reason', `youtube:skip-other:${videoId}`)

    await ctx.api.sendMessage(
      ctx.chat!.id,
      `About to skip "${title}". ${deducedReason}`,
      { reply_markup: keyboard }
    )

  } else if (action === 'promote') {
    const promoteType = parts[2] // content-idea | feature | insight | nothing

    if (promoteType === 'nothing') {
      updateRecommendation(videoId, { promoted_to: null })
      await ctx.api.sendMessage(ctx.chat!.id, 'Got it, no promotion.')
      return
    }

    await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    const prompt = `You are K2B. Create a ${promoteType} vault note from YouTube video ${videoId}. Look up the video details in wiki/context/youtube-recommended.jsonl. Use k2b-vault-writer to create the note in review/.`
    const promoteMarker = markObservationStart()
    const priorSessionId = getSession(chatId, 'interactive')
    const { text, newSessionId } = await runAgent(prompt, priorSessionId)
    if (newSessionId) setSession(chatId, newSessionId, 'interactive')
    logObservations(promoteMarker, `youtube-promote-${videoId}`, prompt)
    const result = text ?? '(created)'

    updateRecommendation(videoId, { promoted_to: promoteType })

    await ctx.api.sendMessage(
      ctx.chat!.id,
      formatForTelegram(`Saved as ${promoteType}: ${result}`),
      { parse_mode: 'HTML' }
    )

  } else if (action === 'screen-process') {
    await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    const rec = readRecommendations().find(r => r.video_id === videoId)
    const title = rec?.title ?? videoId

    await ctx.api.sendMessage(ctx.chat!.id, `Processing: "${title}"\nGetting transcript and creating vault note -- this may take a few minutes.`)
    // Constrained prompt: do the analysis, but DO NOT mutate playlists or JSONL.
    // Code handles state mutations via skipVideoFromScreen after the agent returns.
    const prompt = `You are K2B. Analyze YouTube video https://www.youtube.com/watch?v=${videoId} from the K2B Screen playlist.

IMPORTANT: Do NOT modify the K2B Screen playlist. Do NOT edit youtube-recommended.jsonl. Do NOT edit youtube-processed.md. The calling code handles state mutations after you return.

Your job:
1. Get the transcript (if Chinese/Mandarin, extract audio with 'scripts/yt-playlist-poll.sh --extract-audio <url> /tmp/k2b-yt-audio/', then transcribe chunks via Groq Whisper using GROQ_API_KEY from k2b-remote/.env).
2. Create a vault note in raw/youtube/ via k2b-vault-writer.
3. Return a short summary of the note you created.`
    const screenMarker = markObservationStart()
    const priorSessionId = getSession(chatId, 'interactive')
    const { text, newSessionId } = await runAgent(prompt, priorSessionId)
    if (newSessionId) setSession(chatId, newSessionId, 'interactive')
    logObservations(screenMarker, `youtube-screen-${videoId}`, prompt)

    // Canonical state mutation: mark processed + remove from Screen playlist
    try {
      skipVideoFromScreen(videoId, 'screen-processed', 'button:screen-process')
      // Override status from 'skipped' to 'processed' (skipVideoFromScreen uses 'skipped')
      updateRecommendation(videoId, { status: 'processed', outcome: 'screen-processed' })
    } catch (err) {
      logger.error({ err, videoId }, 'screen-process post-agent state mutation failed')
    }

    const result = text ?? '(processed)'
    const formatted = formatForTelegram(result)
    const chunks = splitMessage(formatted)
    for (const chunk of chunks) {
      try {
        await ctx.api.sendMessage(ctx.chat!.id, chunk, { parse_mode: 'HTML' })
      } catch {
        await ctx.api.sendMessage(ctx.chat!.id, chunk.replace(/<[^>]+>/g, ''))
      }
    }

  } else if (action === 'screen-skip') {
    // Canonical skip-from-Screen path
    try {
      skipVideoFromScreen(videoId, 'screen-skipped', 'button:screen-skip')
      await ctx.api.sendMessage(ctx.chat!.id, 'Skipped and removed from Screen.')
    } catch (err) {
      logger.error({ err, videoId }, 'skipVideoFromScreen failed')
      await ctx.api.sendMessage(ctx.chat!.id, 'Skip failed, see logs.')
    }

  } else if (action === 'screen-all') {
    // Poll the actual playlist
    let output: string
    try {
      output = execSync(
        `yt-dlp --flat-playlist --print "%(id)s\\t%(title)s" "https://www.youtube.com/playlist?list=${SCREEN_PLAYLIST_ID}" 2>/dev/null`,
        { encoding: 'utf-8', timeout: 30_000 }
      ).trim()
    } catch (err) {
      logger.error({ err }, 'Failed to poll Screen playlist for process-all')
      await ctx.api.sendMessage(ctx.chat!.id, 'Failed to read Screen playlist.')
      return
    }

    if (!output) {
      await ctx.api.sendMessage(ctx.chat!.id, 'K2B Screen is empty.')
      return
    }

    const videos = output.split('\n').map(line => {
      const [id, title] = line.split('\t')
      return { id, title: title ?? id }
    }).filter(v => v.id)

    await ctx.api.sendMessage(ctx.chat!.id, `Processing ${videos.length} videos from Screen...`)
    await ctx.api.sendChatAction(ctx.chat!.id, 'typing')

    for (let i = 0; i < videos.length; i++) {
      const v = videos[i]
      await ctx.api.sendMessage(ctx.chat!.id, `[${i + 1}/${videos.length}] Processing: "${v.title}"`)
      // Same constrained prompt pattern as single screen-process
      const prompt = `You are K2B. Analyze YouTube video https://www.youtube.com/watch?v=${v.id} from the K2B Screen playlist.

IMPORTANT: Do NOT modify the K2B Screen playlist. Do NOT edit youtube-recommended.jsonl. Do NOT edit youtube-processed.md. The calling code handles state mutations after you return.

Your job:
1. Get the transcript (if Chinese/Mandarin, extract audio with 'scripts/yt-playlist-poll.sh --extract-audio <url> /tmp/k2b-yt-audio/', then transcribe chunks via Groq Whisper using GROQ_API_KEY from k2b-remote/.env).
2. Create a vault note in raw/youtube/ via k2b-vault-writer.
3. Return a short summary of the note you created.`
      const screenMarker = markObservationStart()
      try {
        const priorSessionId = getSession(chatId, 'interactive')
        const { text, newSessionId } = await runAgent(prompt, priorSessionId)
        if (newSessionId) setSession(chatId, newSessionId, 'interactive')
        logObservations(screenMarker, `youtube-screen-${v.id}`, prompt)
        // Canonical state mutation after agent returns
        try {
          skipVideoFromScreen(v.id, 'screen-processed', 'button:screen-all')
          updateRecommendation(v.id, { status: 'processed', outcome: 'screen-processed' })
        } catch (mutErr) {
          logger.error({ err: mutErr, videoId: v.id }, 'screen-all post-agent state mutation failed')
        }

        const result = text ?? '(processed)'
        await ctx.api.sendMessage(
          ctx.chat!.id,
          formatForTelegram(`Processed: ${v.title}\n${result}`),
          { parse_mode: 'HTML' }
        )
      } catch (err) {
        logger.error({ err, videoId: v.id }, 'Screen processing failed')
        await ctx.api.sendMessage(ctx.chat!.id, `Failed to process: ${v.title}`)
      }
      await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    }

    await ctx.api.sendMessage(ctx.chat!.id, `Screen processing complete: ${videos.length} videos.`)
  }
}

// --- YouTube comment/skip-reason capture ---

async function handleCommentOrSkipReason(
  ctx: Context,
  pendingKey: string,
  text: string,
  _chatId: string
): Promise<void> {
  if (pendingKey.startsWith('skip:')) {
    // Skip reason capture -- execute the canonical skip now that we have the reason.
    // Handles BOTH Watch-list skips (rec in JSONL) and new-pick skips (pendingCandidates only).
    const videoId = pendingKey.slice(5)
    const rec = readRecommendations().find(r => r.video_id === videoId)
    const cand = getYtPendingCandidates().get(videoId)
    try {
      if (rec) {
        skipVideoFromWatch(videoId, text, 'text-reply:skip-reason', text)
      } else if (cand) {
        // New-pick skip: train taste model, log signal, clear state
        appendFeedbackSignal(videoId, 'skip_reason', 'user-provided', text)
        if (cand.channel && cand.channel !== 'unknown') {
          tasteModel.recordAction(videoId, cand.channel, 'skip', text)
        }
        clearFromAgentState(videoId)
      } else {
        logger.warn({ videoId }, 'skip-reason capture: no rec or pendingCandidate')
        clearFromAgentState(videoId)
      }
      await ctx.api.sendMessage(ctx.chat!.id, 'Got it, skip reason noted.')
    } catch (err) {
      logger.error({ err, videoId }, 'skip-reason capture failed')
      await ctx.api.sendMessage(ctx.chat!.id, 'Noted, but skip failed. See logs.')
    }
  } else {
    // Regular comment capture
    const videoId = pendingKey
    updateRecommendation(videoId, { comment_text: text, feedback_text: text })
    appendFeedbackSignal(videoId, 'comment', 'user-comment', text)
    await ctx.api.sendMessage(ctx.chat!.id, 'Got it, comment saved.')
  }
}

// --- Direct YouTube URL handler ---

// oEmbed fallback for when yt-dlp fails (common on Shorts). Returns title +
// channel so the screening agent still has something to reason about instead
// of replying "can't get the video details".
function fetchYouTubeOEmbed(videoId: string): { title: string; channel: string } | null {
  try {
    const target = `https://www.youtube.com/watch?v=${videoId}`
    const url = `https://www.youtube.com/oembed?url=${encodeURIComponent(target)}&format=json`
    const json = execFileSync('curl', ['-sSL', '--max-time', '10', url], {
      encoding: 'utf-8',
      timeout: 12_000,
      stdio: ['pipe', 'pipe', 'pipe'],
    }).trim()
    if (!json) return null
    const parsed = JSON.parse(json) as { title?: string; author_name?: string }
    if (!parsed.title) return null
    return { title: parsed.title, channel: parsed.author_name ?? 'unknown' }
  } catch {
    return null
  }
}

async function handleDirectYouTubeUrl(
  ctx: Context,
  videoId: string,
  originalUrl: string,
  chatId: string
): Promise<void> {
  // Instant acknowledgment
  await ctx.api.sendMessage(ctx.chat!.id, 'Checking this video...')
  await ctx.api.sendChatAction(ctx.chat!.id, 'typing')

  // Send the URL first (triggers thumbnail preview in Telegram)
  await ctx.api.sendMessage(ctx.chat!.id, originalUrl)
  await ctx.api.sendChatAction(ctx.chat!.id, 'typing')

  // Get metadata via yt-dlp using clean URL (raw URL may have ?si= tracking params)
  let title = videoId
  let channel = 'unknown'
  let duration = 'unknown'
  let uploadDate = 'unknown'
  let isShort = originalUrl.includes('/shorts/')

  try {
    const cleanUrl = `https://www.youtube.com/watch?v=${videoId}`
    const meta = execFileSync('yt-dlp', [
      '--print', '%(title)s\t%(channel)s\t%(duration_string)s\t%(upload_date)s',
      cleanUrl
    ], { encoding: 'utf-8', timeout: 15_000, stdio: ['pipe', 'pipe', 'pipe'] }).trim()
    const parts = meta.split('\t')
    if (parts.length >= 4) {
      title = parts[0]
      channel = parts[1]
      duration = parts[2]
      uploadDate = parts[3]
    }
    if (duration !== 'unknown') {
      const dParts = duration.split(':').map(Number)
      const totalSec = dParts.length === 3 ? dParts[0]*3600+dParts[1]*60+dParts[2] : dParts.length === 2 ? dParts[0]*60+dParts[1] : dParts[0]
      if (totalSec <= 90) isShort = true
    }
  } catch (err) {
    logger.error({ err, videoId }, 'Failed to get video metadata via yt-dlp, trying oEmbed fallback')
    const oembed = fetchYouTubeOEmbed(videoId)
    if (oembed) {
      title = oembed.title
      channel = oembed.channel
      logger.info({ videoId, title, channel }, 'Recovered metadata via oEmbed fallback')
    }
  }

  // Cache metadata so agent-add can write a real JSONL entry later without
  // a second yt-dlp round trip. This is the ONLY place direct-URL metadata
  // gets into pendingCandidates; if it's absent, agent-add will reject the save.
  if (title && title !== videoId && channel && channel !== 'unknown') {
    const candidates = getYtPendingCandidates()
    candidates.set(videoId, {
      videoId,
      title,
      channel,
      duration: duration === 'unknown' ? '' : duration,
      uploadDate: uploadDate === 'unknown' ? '' : uploadDate,
      verdict: '',
      reason: 'direct-url',
    })
    setYtPendingCandidates(candidates)
  }

  // Check taste model
  const affinity = tasteModel.getChannelAffinity(channel)
  const flagged = tasteModel.isChannelFlagged(channel)
  const stale = uploadDate !== 'unknown' ? tasteModel.isVideoStale(uploadDate, []) : false
  const hasChinese = /[\u4e00-\u9fff\u3400-\u4dbf]/.test(title)

  // Screen via constrained agent -- gives Keith a verdict, not raw metadata
  await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
  const screenPrompt = [
    'IMPORTANT: Do NOT use any tools. Do NOT read files. Do NOT run commands. Just analyze and return the message text.',
    '',
    "You are K2B's YouTube curator. Keith just sent you this video:",
    '',
    `Title: ${title}`,
    `Channel: ${channel}`,
    `Duration: ${duration}`,
    `Published: ${uploadDate}${stale ? ' (OLD for this topic category)' : ''}`,
    affinity > 0.3 ? 'Channel affinity: high (Keith likes this channel)' : flagged ? 'Channel affinity: low (Keith usually skips this channel)' : '',
    hasChinese ? 'Language: likely Chinese/Mandarin' : '',
    isShort ? 'Format: YouTube Short' : '',
    '',
    'Keith builds K2B, a Claude Code AI second brain with 18+ skills, vault compilation, and taste-model-driven YouTube curation. He is AVP Talent Acquisition at SJM Resorts (Macau).',
    '',
    'Compose a SHORT Telegram message (3-5 lines):',
    '1. What this video likely covers (infer from title, channel, duration)',
    '2. Why it IS or ISN\'T relevant to Keith right now',
    '3. Verdict: Worth watching / Highlights only / Skip',
    '',
    'Be direct and honest. If basic or stale, say so.',
    'Return ONLY the message text for Telegram.',
  ].filter(Boolean).join('\n')

  // Always show all options
  const keyboard = new InlineKeyboard()
    .text('Add to Watch', `youtube:agent-add:${videoId}`)
    .text('Process Now', `youtube:screen-process:${videoId}`)
    .row()
    .text('Screen', `youtube:screen:${videoId}`)
    .text('Skip', `youtube:skip:${videoId}`)

  try {
    const priorSessionId = getSession(chatId, 'interactive')
    const { text: verdict, newSessionId } = await runAgent(screenPrompt, priorSessionId)
    if (newSessionId) setSession(chatId, newSessionId, 'interactive')
    const verdictText = verdict ?? `**${title}**\n${channel} -- ${duration}\nPublished: ${uploadDate}\n(Could not screen this video)`
    const formatted = formatForTelegram(verdictText)
    await ctx.api.sendMessage(ctx.chat!.id, formatted, { parse_mode: 'HTML', reply_markup: keyboard })
  } catch (err) {
    logger.error({ err, videoId }, 'URL screening failed')
    const fallback = `**${title}**\n${channel} -- ${duration}\nPublished: ${uploadDate}\n(Screening failed)`
    const formatted = formatForTelegram(fallback)
    await ctx.api.sendMessage(ctx.chat!.id, formatted, { parse_mode: 'HTML', reply_markup: keyboard })
  }
}

// --- Bot creation ---

export function createBot(): Bot {
  if (!TELEGRAM_BOT_TOKEN) {
    throw new Error('TELEGRAM_BOT_TOKEN not set. Run: npm run setup')
  }

  const bot = new Bot(TELEGRAM_BOT_TOKEN, HTTP_PROXY ? {
    client: {
      baseFetchConfig: {
        agent: new HttpsProxyAgent(HTTP_PROXY),
        compress: true,
      },
    },
  } : {})

  // Auth middleware
  bot.use(async (ctx, next) => {
    if (ctx.chat && !isAuthorised(ctx.chat.id)) {
      logger.warn({ chatId: ctx.chat.id }, 'Unauthorized access attempt')
      await ctx.reply('Not authorized.')
      return
    }
    await next()
  })

  // --- Commands ---

  bot.command('start', async (ctx) => {
    await ctx.reply(
      'K2B Remote is running. Send me messages and I will process them through Claude Code.\n\n' +
      'Commands:\n' +
      '/chatid - Show your chat ID\n' +
      '/newchat - Start a fresh session\n' +
      '/memory - Show recent memories\n' +
      '/forget - Clear session (alias for /newchat)\n' +
      '/voice - Check voice capabilities'
    )
  })

  bot.command('chatid', async (ctx) => {
    await ctx.reply(`Your chat ID: ${ctx.chat.id}`)
  })

  bot.command('newchat', async (ctx) => {
    clearSession(String(ctx.chat.id))
    resetYtState()
    await ctx.reply('Session cleared. Starting fresh.')
  })

  bot.command('forget', async (ctx) => {
    clearSession(String(ctx.chat.id))
    resetYtState()
    await ctx.reply('Session cleared. Starting fresh.')
  })

  bot.command('memory', async (ctx) => {
    const chatId = String(ctx.chat.id)
    const count = getMemoryCount(chatId)
    const recent = getRecentMemoriesForDisplay(chatId, 10)

    if (recent.length === 0) {
      await ctx.reply('No memories stored yet.')
      return
    }

    const lines = recent.map((m) => {
      const date = new Date(m.created_at).toLocaleDateString()
      const sal = m.salience.toFixed(1)
      return `[${m.sector}] (${sal}) ${date}: ${m.content.slice(0, 100)}`
    })

    await ctx.reply(
      `Memories: ${count} total\n\nRecent:\n${lines.join('\n\n')}`
    )
  })

  bot.command('voice', async (ctx) => {
    const caps = voiceCapabilities()
    await ctx.reply(
      `Voice capabilities:\n` +
      `- Speech-to-text (Groq): ${caps.stt ? 'Enabled' : 'Not configured'}\n` +
      `- Text-to-speech: ${caps.tts ? 'Enabled' : 'Not configured'}`
    )
  })

  // --- Message handlers ---

  bot.on('message:text', async (ctx) => {
    const text = ctx.message.text
    const chatId = String(ctx.chat?.id)

    // Check if we're awaiting a comment/skip-reason for a YouTube video
    const pendingKey = awaitingComment.get(chatId)
    if (pendingKey && !text.startsWith('/')) {
      awaitingComment.delete(chatId)
      await handleCommentOrSkipReason(ctx, pendingKey, text, chatId)
      return
    }

    // Detect YouTube URLs (regular videos, shorts, youtu.be links)
    const ytMatch = text.match(
      /(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})/
    )
    if (ytMatch) {
      await handleDirectYouTubeUrl(ctx, ytMatch[1], text.trim(), chatId)
      return
    }

    // Check if YouTube agent loop is waiting for a response
    const handled = await handleYouTubeAgentResponse(
      text,
      async (cid, msg) => {
        try {
          await ctx.api.sendMessage(Number(cid), formatForTelegram(msg), { parse_mode: 'HTML' })
        } catch {
          await ctx.api.sendMessage(Number(cid), msg.replace(/<[^>]+>/g, ''))
        }
      },
      async (cid, msg, _buttons, prebuiltKeyboard) => {
        const opts: Record<string, unknown> = { parse_mode: 'HTML' }
        if (prebuiltKeyboard) opts.reply_markup = prebuiltKeyboard
        try {
          await ctx.api.sendMessage(Number(cid), formatForTelegram(msg), opts)
        } catch {
          await ctx.api.sendMessage(Number(cid), msg.replace(/<[^>]+>/g, ''))
        }
      },
      chatId
    )
    if (handled) return

    if (text.startsWith('/')) return // skip unhandled commands
    await handleMessage(ctx, text)
  })

  bot.on('message:voice', async (ctx) => {
    const caps = voiceCapabilities()
    if (!caps.stt) {
      await ctx.reply('Voice transcription not configured. Set GROQ_API_KEY in .env')
      return
    }

    try {
      const file = await ctx.getFile()
      const localPath = await downloadMedia(file.file_id)
      const transcript = await transcribeAudio(localPath)
      await ctx.reply(`[Transcribed]: ${transcript}`)

      // Check if we're awaiting a comment/skip-reason (voice note as response)
      const chatId = String(ctx.chat?.id)
      const pendingKey = awaitingComment.get(chatId)
      if (pendingKey) {
        awaitingComment.delete(chatId)
        await handleCommentOrSkipReason(ctx, pendingKey, transcript, chatId)
        return
      }

      await handleMessage(ctx, `[Voice transcribed]: ${transcript}`, true)
    } catch (err) {
      logger.error({ err }, 'Voice transcription failed')
      await ctx.reply('Failed to transcribe voice note. Try again or type your message.')
    }
  })

  bot.on('message:photo', async (ctx) => {
    try {
      const photos = ctx.message.photo
      const largest = photos[photos.length - 1]
      const localPath = await downloadMedia(largest.file_id)
      const caption = ctx.message.caption ?? ''
      const message = buildPhotoMessage(localPath, caption)
      await handleMessage(ctx, message)
    } catch (err) {
      logger.error({ err }, 'Photo processing failed')
      await ctx.reply('Failed to process photo.')
    }
  })

  bot.on('message:document', async (ctx) => {
    try {
      const doc = ctx.message.document
      const localPath = await downloadMedia(doc.file_id, doc.file_name ?? undefined)
      const caption = ctx.message.caption ?? ''
      const message = buildDocumentMessage(localPath, doc.file_name ?? 'unknown', caption)
      await handleMessage(ctx, message)
    } catch (err) {
      logger.error({ err }, 'Document processing failed')
      await ctx.reply('Failed to process document.')
    }
  })

  // --- Callback query handler (inline buttons) ---
  bot.on('callback_query:data', async (ctx) => {
    const data = ctx.callbackQuery.data
    const chatId = String(ctx.chat?.id)
    if (!chatId) return

    try {
      await ctx.answerCallbackQuery()

      if (data.startsWith('youtube:')) {
        await handleYouTubeCallback(ctx, data, chatId)
      }
    } catch (err) {
      logger.error({ err, data }, 'Callback query error')
      try {
        await ctx.answerCallbackQuery({ text: 'Something went wrong.' })
      } catch { /* ignore */ }
    }
  })

  // Error handler
  bot.catch((err) => {
    logger.error({ err: err.error }, 'Bot error')
  })

  return bot
}

const sentNudgeIds = new Map<string, number>()  // video_id -> timestamp when nudge was sent
const awaitingComment = new Map<string, string>()  // chatId -> videoId awaiting comment

export async function sendPendingNudges(chatId: string): Promise<number> {
  const pending = getPendingNudges()
  let sent = 0

  for (const rec of pending) {
    const lastSent = sentNudgeIds.get(rec.video_id)
    if (lastSent && Date.now() - lastSent < 24 * 60 * 60 * 1000) continue

    const isRenudge = rec.nudge_date && rec.nudge_date < new Date().toISOString().slice(0, 10)
    const prefix = isRenudge ? `Still in your Watch list (added ${rec.nudge_date}):` : '<b>K2B YouTube Recommendation</b>'
    const duration = rec.duration ? rec.duration : ''
    const date = rec.recommended_date ?? ''
    const meta = [rec.channel, duration, date].filter(Boolean).join(' -- ')

    // Use verdict if available, fall back to pick_reason for legacy entries
    let verdictBlock = ''
    if (rec.verdict) {
      const value = rec.verdict_value ? `\nEstimated value: <b>${rec.verdict_value}</b>` : ''
      verdictBlock = `\n\n<b>K2B Verdict:</b> ${rec.verdict}${value}`
    } else if (rec.pick_reason) {
      verdictBlock = `\n\n${rec.pick_reason}`
    }

    const text = `${prefix}\n\n"<b>${rec.title}</b>"\n${meta}${verdictBlock}`

    const keyboard = new InlineKeyboard()
      .text('Watch', `youtube:watch:${rec.video_id}`)
      .text('Comment', `youtube:comment:${rec.video_id}`)
      .row()
      .text('Skip', `youtube:skip:${rec.video_id}`)
      .text('Screen', `youtube:screen:${rec.video_id}`)

    await sendTelegramMessageWithButtons(chatId, text, [], keyboard)
    sentNudgeIds.set(rec.video_id, Date.now())
    sent++
  }

  return sent
}

export async function sendScreenOptions(chatId: string): Promise<number> {
  // Poll the actual playlist -- not JSONL
  let output: string
  try {
    output = execSync(
      `yt-dlp --flat-playlist --print "%(id)s\\t%(title)s\\t%(channel)s\\t%(duration_string)s" "https://www.youtube.com/playlist?list=${SCREEN_PLAYLIST_ID}" 2>/dev/null`,
      { encoding: 'utf-8', timeout: 30_000 }
    ).trim()
  } catch (err) {
    logger.error({ err }, 'Failed to poll K2B Screen playlist')
    return 0
  }

  if (!output) return 0

  const videos = output.split('\n').map(line => {
    const [id, title, channel, duration] = line.split('\t')
    return { id, title: title ?? id, channel: channel ?? '', duration: duration ?? '' }
  }).filter(v => v.id)

  if (videos.length === 0) return 0

  for (const v of videos) {
    const meta = [v.channel, v.duration].filter(Boolean).join(' -- ')
    const text = `<b>K2B Screen</b>\n\n"<b>${v.title}</b>"\n${meta}`

    const keyboard = new InlineKeyboard()
      .text('Process', `youtube:screen-process:${v.id}`)
      .text('Skip', `youtube:screen-skip:${v.id}`)

    await sendTelegramMessageWithButtons(chatId, text, [], keyboard)
  }

  if (videos.length > 1) {
    const keyboard = new InlineKeyboard()
      .text(`Process All (${videos.length})`, 'youtube:screen-all')

    await sendTelegramMessageWithButtons(
      chatId,
      `${videos.length} videos in K2B Screen. Process all at once?`,
      [],
      keyboard
    )
  }

  return videos.length
}

export async function sendTelegramMessage(
  chatId: string,
  text: string
): Promise<void> {
  if (!TELEGRAM_BOT_TOKEN) return
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`
  const body = JSON.stringify({
    chat_id: chatId,
    text: formatForTelegram(text),
    parse_mode: 'HTML',
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
        ...(HTTP_PROXY ? { agent: new HttpsProxyAgent(HTTP_PROXY) } : {}),
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

export async function sendTelegramMessageWithButtons(
  chatId: string,
  text: string,
  buttons: Array<{ label: string; callbackData: string }>,
  prebuiltKeyboard?: InlineKeyboard
): Promise<void> {
  if (!TELEGRAM_BOT_TOKEN) return
  const keyboard = prebuiltKeyboard ?? (() => {
    const kb = new InlineKeyboard()
    for (const btn of buttons) {
      kb.text(btn.label, btn.callbackData)
    }
    return kb
  })()
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`
  const isPreformatted = text.includes('<b>') || text.includes('<i>') || text.includes('<a ')
  const body = JSON.stringify({
    chat_id: chatId,
    text: isPreformatted ? text : formatForTelegram(text),
    parse_mode: 'HTML',
    reply_markup: prebuiltKeyboard ? keyboard : keyboard.toFlowed(2),
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
        ...(HTTP_PROXY ? { agent: new HttpsProxyAgent(HTTP_PROXY) } : {}),
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
