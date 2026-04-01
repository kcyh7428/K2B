import { request as httpsRequest } from 'node:https'
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
import { updateRecommendation, getPendingNudges, readRecommendations, appendFeedbackSignal } from './youtube.js'
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
  const sessionId = getSession(chatId)

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
    const { text, newSessionId } = await runAgent(fullMessage, sessionId, sendTyping)

    // Log observations (vault file changes from this agent run)
    logObservations(obsMarker, newSessionId ?? sessionId ?? 'telegram-interactive', rawText)

    // Save session
    if (newSessionId) {
      setSession(chatId, newSessionId)
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

    // After YouTube-related messages, send nudge buttons for pending videos
    if (rawText.toLowerCase().includes('youtube') || rawText.toLowerCase().includes('/youtube')) {
      const nudged = await sendPendingNudges(chatId)
      if (nudged > 0) {
        logger.info({ nudged }, 'Sent YouTube nudge buttons after message')
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
Get the transcript (if Chinese/Mandarin, use Whisper via scripts/yt-transcribe-whisper.sh).
Use the playlist prompt_focus if the video is tracked in youtube-recommended.jsonl. Produce:

1. Concise highlights summary with key points and timestamps for the best segments
2. Your honest assessment: Is this worth Keith's time? He's deeply familiar with AI, builds Claude Code skills (18+), runs a full AI second brain (K2B). Be direct -- don't pad weak content.
3. End with a short question asking Keith what he thinks -- worth keeping or skip?

Keep it concise for Telegram. When Keith responds with his feedback:
- Update youtube-recommended.jsonl: set value_signal and feedback_text for video ${videoId}
- Append to youtube-feedback-signals.jsonl: signal_type "value_feedback", signal (categorize as exactly-my-level/gave-idea/good-but-basic/not-worth-it), signal_text (Keith's actual words)
- If Keith wants to save something (content idea, feature, insight), create the vault note via k2b-vault-writer
- If Keith wants to move the video to a category playlist (K2B Claude, K2B Recruit, etc.), use scripts/yt-playlist-add.sh`
    const highlightsMarker = markObservationStart()
    const { text } = await runAgent(prompt)
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
    const rec = readRecommendations().find(r => r.video_id === videoId)
    updateRecommendation(videoId, {
      status: 'watched',
      outcome: 'watched',
    })
    appendFeedbackSignal(videoId, 'watch', 'watched')

    const link = `https://youtu.be/${videoId}`
    await ctx.api.sendMessage(ctx.chat!.id, `${link}`, { parse_mode: 'HTML' })

  } else if (action === 'comment') {
    awaitingComment.set(chatId, videoId)
    await ctx.api.sendMessage(ctx.chat!.id, "What's your take?")

  } else if (action === 'screen') {
    updateRecommendation(videoId, {
      status: 'processed',
      outcome: 'screened',
    })
    appendFeedbackSignal(videoId, 'screen', 'screened-for-processing')

    // Add to K2B Screen playlist and remove from Watch in background
    runAgent(`Add video ${videoId} to K2B Screen playlist using scripts/yt-playlist-add.sh. Remove from K2B Watch playlist using scripts/yt-playlist-remove.sh.`).catch(err =>
      logger.error({ err, videoId }, 'Failed to screen video')
    )

    await ctx.api.sendMessage(ctx.chat!.id, 'Sent to Screen for full processing.')

  } else if (action === 'skip') {
    updateRecommendation(videoId, {
      status: 'skipped',
      outcome: 'skipped',
    })
    appendFeedbackSignal(videoId, 'skip_reason', 'skipped')

    // Remove from Watch playlist in background
    runAgent(`Remove video ${videoId} from K2B Watch playlist using scripts/yt-playlist-remove.sh`).catch(err =>
      logger.error({ err, videoId }, 'Failed to remove from Watch playlist')
    )

    // Optional reason: log skip immediately, then ask why
    await ctx.api.sendMessage(ctx.chat!.id, 'Skipped. Why?')
    awaitingComment.set(chatId, `skip:${videoId}`)

  } else if (action === 'promote') {
    const promoteType = parts[2] // content-idea | feature | insight | nothing

    if (promoteType === 'nothing') {
      updateRecommendation(videoId, { promoted_to: null })
      await ctx.api.sendMessage(ctx.chat!.id, 'Got it, no promotion.')
      return
    }

    await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
    const prompt = `You are K2B. Create a ${promoteType} vault note from YouTube video ${videoId}. Look up the video details in Notes/Context/youtube-recommended.jsonl. Use k2b-vault-writer to create the note in Inbox/.`
    const promoteMarker = markObservationStart()
    const { text } = await runAgent(prompt)
    logObservations(promoteMarker, `youtube-promote-${videoId}`, prompt)
    const result = text ?? '(created)'

    updateRecommendation(videoId, { promoted_to: promoteType })

    await ctx.api.sendMessage(
      ctx.chat!.id,
      formatForTelegram(`Saved as ${promoteType}: ${result}`),
      { parse_mode: 'HTML' }
    )
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
    // Skip reason capture
    const videoId = pendingKey.slice(5)
    updateRecommendation(videoId, { skip_reason: text, feedback_text: text })
    appendFeedbackSignal(videoId, 'skip_reason', 'user-provided', text)
    await ctx.api.sendMessage(ctx.chat!.id, 'Got it, skip reason noted.')
  } else {
    // Regular comment capture
    const videoId = pendingKey
    updateRecommendation(videoId, { comment_text: text, feedback_text: text })
    appendFeedbackSignal(videoId, 'comment', 'user-comment', text)
    await ctx.api.sendMessage(ctx.chat!.id, 'Got it, comment saved.')
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
    await ctx.reply('Session cleared. Starting fresh.')
  })

  bot.command('forget', async (ctx) => {
    clearSession(String(ctx.chat.id))
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

const sentNudgeIds = new Set<string>()
const awaitingComment = new Map<string, string>()  // chatId -> videoId awaiting comment

export async function sendPendingNudges(chatId: string): Promise<number> {
  const pending = getPendingNudges()
  let sent = 0

  for (const rec of pending) {
    if (sentNudgeIds.has(rec.video_id)) continue

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
    sentNudgeIds.add(rec.video_id)
    sent++
  }

  return sent
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
