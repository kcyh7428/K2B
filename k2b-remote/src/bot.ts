import { request as httpsRequest } from 'node:https'
import { Bot, Context } from 'grammy'
import { HttpsProxyAgent } from 'https-proxy-agent'
import {
  TELEGRAM_BOT_TOKEN,
  ALLOWED_CHAT_ID,
  MAX_MESSAGE_LENGTH,
  TYPING_REFRESH_MS,
  HTTP_PROXY,
} from './config.js'
import { getSession, setSession, clearSession, getRecentMemoriesForDisplay, getMemoryCount, getKv, setKv } from './db.js'
import { runAgent } from './agent.js'
import { buildMemoryContext, saveConversationTurn, loadPreferenceProfile } from './memory.js'
import { voiceCapabilities, transcribeAudio } from './voice.js'
import { downloadMedia, buildPhotoMessage, buildDocumentMessage } from './media.js'
import { logger } from './logger.js'
import { markObservationStart, logObservations } from './observe.js'
import { scanOutbox, sendMedia, consumeManifest } from './telegram-outbox.js'

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
  rawText: string
): Promise<void> {
  const chatId = String(ctx.chat?.id)
  if (!chatId) return

  let typingInterval: ReturnType<typeof setInterval> | undefined

  try {
    // Load preference profile and check for changes (hash persisted to KV for durability across restarts)
    const { text: preferenceContext, hash: profileHash } = loadPreferenceProfile()
    const kvKey = `profile_hash:${chatId}`
    const prevHash = getKv(kvKey) ?? ''
    if (prevHash && prevHash !== profileHash) {
      // Profile changed (including removal) -- reset session to avoid stale context
      clearSession(chatId)
      logger.info({ chatId }, 'Preference profile changed, reset agent session')
    }
    if (profileHash !== prevHash) setKv(kvKey, profileHash)

    // Build memory context
    const memoryContext = await buildMemoryContext(chatId, rawText)
    const fullMessage = preferenceContext + memoryContext + rawText

    // Get existing session (may have been cleared above)
    const sessionId = getSession(chatId)

    // Start typing indicator
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
    const outboxMark = Date.now()
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

    // Send any media files the agent dropped into the outbox
    const outboxItems = scanOutbox(outboxMark)
    for (const { manifest, manifestPath } of outboxItems) {
      const sent = await sendMedia(ctx.api, ctx.chat!.id, manifest)
      if (sent) consumeManifest(manifestPath)
      // On failure, manifest stays for diagnosis (won't re-trigger -- afterMs has passed)
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

  } catch (err) {
    logger.error({ err, chatId, rawText }, 'handleMessage failed')
    try {
      await ctx.api.sendMessage(ctx.chat!.id, 'Something went wrong processing that. Try again.')
    } catch (sendErr) {
      logger.error({ err: sendErr }, 'Failed to send error fallback to Telegram')
    }
  } finally {
    if (typingInterval) clearInterval(typingInterval)
  }
}

// --- Bot creation ---

export function createBot(): Bot {
  if (!TELEGRAM_BOT_TOKEN) {
    throw new Error('TELEGRAM_BOT_TOKEN not set. Run: npm run setup')
  }

  // Stall-defense timeout ladder (innermost to outermost):
  //   Telegram long-poll     50s   (bot.start timeout, server-side wait)
  //   grammY HTTP client     55s   (client.timeoutSeconds, aborts request on hang)
  //   HttpsProxyAgent socket 60s   (timeout, OS idle-socket cleanup)
  //   TCP keepalive probes   30s   (detect dead-but-held connections)
  // Each layer is slightly looser than the one inside it, so the tightest
  // applicable deadline fires first and the others act as fallbacks.
  const bot = new Bot(TELEGRAM_BOT_TOKEN, HTTP_PROXY ? {
    client: {
      timeoutSeconds: 55,
      baseFetchConfig: {
        agent: new HttpsProxyAgent(HTTP_PROXY, {
          keepAlive: true,
          keepAliveMsecs: 30000,
          timeout: 60000,
        }),
        compress: true,
      },
    },
  } : {
    client: { timeoutSeconds: 55 },
  })

  // Diagnostic: log getUpdates lifecycle so a future stall is visible in logs.
  // Fired for every Telegram API call; we only emit on getUpdates to keep volume low.
  // Errors and abnormally long polls go at warn so they surface at default log level.
  bot.api.config.use(async (prev, method, payload, signal) => {
    if (method !== 'getUpdates') return prev(method, payload, signal)
    const start = Date.now()
    logger.debug({ method }, 'poll start')
    try {
      const result = await prev(method, payload, signal)
      const durationMs = Date.now() - start
      if (durationMs > 52000) {
        // Above Telegram's 50s long-poll but below our 55s client abort --
        // a near-miss signal that something upstream is slow.
        logger.warn({ method, durationMs }, 'poll slow')
      } else {
        logger.debug({ method, durationMs }, 'poll end')
      }
      return result
    } catch (err) {
      logger.warn({ method, durationMs: Date.now() - start, err }, 'poll error')
      throw err
    }
  })

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
      await handleMessage(ctx, `[Voice transcribed]: ${transcript}`)
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

  // Error handler
  bot.catch((err) => {
    logger.error({ err: err.error }, 'Bot error')
  })

  return bot
}

// --- Outbound Telegram helper (used by scheduler.ts) ---

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
