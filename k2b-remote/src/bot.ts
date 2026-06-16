import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import { request as httpsRequest } from 'node:https'
import { Bot, Context } from 'grammy'
import { HttpsProxyAgent } from 'https-proxy-agent'
import {
  TELEGRAM_BOT_TOKEN,
  ALLOWED_CHAT_ID,
  SILENT_CHAT_IDS,
  TYPING_REFRESH_MS,
  HTTP_PROXY,
  K2B_PROJECT_ROOT,
  K2B_VAULT_PATH,
} from './config.js'
import { getSession, setSession, clearSession, getRecentMemoriesForDisplay, getMemoryCount, getKv, setKv } from './db.js'
import { runAgent } from './agent.js'
import { saveConversationTurn, loadPreferenceProfile } from './memory.js'
import { injectMemoryFromShelves, injectVaultContext } from './memoryInject.js'
import { normalizationGate } from './washingMachine.js'
import { ingestAttachment } from './attachmentIngest.js'
import { resumePendingConfirmation, type ShelfWriter } from './washingMachineResume.js'
import { voiceCapabilities, transcribeAudio } from './voice.js'
import { downloadMedia, buildPhotoMessage, buildDocumentMessage } from './media.js'
import { logger } from './logger.js'
import { markObservationStart, logObservations } from './observe.js'
import { scanOutbox, sendMedia, consumeManifest } from './telegram-outbox.js'
import { buildAgentInputWithYouTubeContext } from './url-prefetch.js'
import { handleMediaCommand } from './mediaCommand.js'

import { formatForTelegram, splitMessage } from './telegramFormat.js'

function isAuthorised(chatId: number): boolean {
  if (!ALLOWED_CHAT_ID) return true // first-run mode
  return String(chatId) === ALLOWED_CHAT_ID
}

// --- Ship 1B helpers (pending-confirmation resume path) ---

const SHELF_WRITER_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/shelf-writer.sh')
const PENDING_DIR = resolve(K2B_VAULT_PATH, 'wiki/context/shelves/.pending-confirmation')
const SHELF_WRITER_TIMEOUT_MS = 10_000

const PINNED_TYPES = new Set(['contact', 'person', 'org', 'appointment', 'decision'])

/**
 * Shelf-writer helper for pending-confirmation resume. Translates a
 * parked `{type, fields}` row into shelf-writer.sh args with the
 * Keith-chosen date. Returns true on clean write, false so the resume
 * handler can surface "try again" to Telegram.
 */
const shelfWriterForResume: ShelfWriter = async (rowUnknown, chosenDate) => {
  const row = rowUnknown as { type?: string; fields?: Record<string, unknown> }
  const type = row.type ?? ''
  const fields = row.fields ?? {}
  if (!type) {
    logger.warn({ row }, 'pending-resume: missing row.type, cannot write shelf row')
    return false
  }
  const nameCandidate =
    pickStringField(fields, 'name') ||
    pickStringField(fields, 'name_en') ||
    pickStringField(fields, 'name_zh') ||
    pickStringField(fields, 'subject') ||
    'unnamed'
  const slug = slugifyForShelf(nameCandidate)
  const attrs: string[] = []
  for (const [key, value] of Object.entries(fields)) {
    if (value === null || value === undefined) continue
    if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') continue
    const str = String(value).replace(/[\r\n]+/g, ' ').trim()
    if (!str) continue
    const safeKey = key.toLowerCase().replace(/[^a-z0-9_-]+/g, '_')
    if (!/^[a-z]/.test(safeKey)) continue
    attrs.push(`${safeKey}:${str}`)
  }
  attrs.push(`pinned:${PINNED_TYPES.has(type) ? 'yes' : 'no'}`)
  attrs.push('source:telegram-pending-resume')
  const args = [SHELF_WRITER_SCRIPT, '--shelf', 'semantic', '--date', chosenDate, '--type', type, '--slug', slug]
  for (const a of attrs) {
    args.push('--attr', a)
  }
  return await new Promise<boolean>((resolveFn) => {
    const child = spawn('bash', args, { stdio: ['ignore', 'pipe', 'pipe'] })
    let stderr = ''
    child.stderr.on('data', (c) => {
      stderr += c.toString()
    })
    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        // ignore
      }
      logger.error({ slug, type }, 'pending-resume: shelf-writer timed out')
      resolveFn(false)
    }, SHELF_WRITER_TIMEOUT_MS)
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) {
        resolveFn(true)
      } else {
        logger.error({ slug, type, code, stderr: stderr.trim() }, 'pending-resume: shelf-writer failed')
        resolveFn(false)
      }
    })
    child.on('error', (err) => {
      clearTimeout(timer)
      logger.error({ err: String(err), slug, type }, 'pending-resume: shelf-writer spawn error')
      resolveFn(false)
    })
  })
}

function pickStringField(fields: Record<string, unknown>, key: string): string {
  const v = fields[key]
  return typeof v === 'string' ? v : ''
}

function slugifyForShelf(s: string): string {
  const base = s
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return base || 'unnamed'
}

/**
 * Rewrite the promptMessageId in a pending-confirmation record once we
 * know the Telegram message_id of the prompt we just sent. The gate
 * wrote the original user message's id as promptMessageId; swapping to
 * the reply's message id makes Keith's reply-to-quote match cleanly.
 * Best-effort: atomic rewrite via temp + rename.
 */
async function rewritePendingPromptId(uuid: string, newPromptMessageId: number): Promise<void> {
  const { readFile, writeFile, rename, rm } = await import('node:fs/promises')
  const path = resolve(PENDING_DIR, `${uuid}.json`)
  const tmp = resolve(PENDING_DIR, `.${uuid}.json.tmp`)
  try {
    const raw = await readFile(path, 'utf8')
    const record = JSON.parse(raw) as Record<string, unknown>
    record.promptMessageId = newPromptMessageId
    await writeFile(tmp, JSON.stringify(record, null, 2), 'utf8')
    await rename(tmp, path)
  } catch (err) {
    // Best-effort cleanup of any stale temp file so a later retry isn't blocked.
    await rm(tmp, { force: true }).catch(() => {
      // ignore
    })
    throw err
  }
}

/**
 * Startup-time pending cleanup: remove any `.pending-confirmation/*.json`
 * files older than TTL, and drop stray `.tmp` files from a previous run
 * that failed mid-write. Called once from createBot. Non-fatal: logs and
 * continues on failure so the bot still boots.
 */
const PENDING_TTL_MS = 24 * 60 * 60 * 1000  // 24h

async function sweepExpiredPending(): Promise<void> {
  try {
    const { readdirSync, statSync, rmSync } = await import('node:fs')
    const now = Date.now()
    const names = readdirSync(PENDING_DIR)
    let expired = 0
    let tmpCleaned = 0
    for (const name of names) {
      const full = resolve(PENDING_DIR, name)
      try {
        const stat = statSync(full)
        if (name.endsWith('.tmp')) {
          rmSync(full)
          tmpCleaned += 1
        } else if (name.endsWith('.json') && now - stat.mtimeMs > PENDING_TTL_MS) {
          rmSync(full)
          expired += 1
        }
      } catch {
        // ignore per-file errors; continue
      }
    }
    if (expired > 0 || tmpCleaned > 0) {
      logger.info({ expired, tmpCleaned }, 'pending-cleanup: swept .pending-confirmation/')
    }
  } catch (err) {
    // Pending dir may not exist yet on a fresh deploy; that's fine.
    logger.debug({ err: String(err) }, 'pending-cleanup: sweep skipped')
  }
}

// --- Main message handler ---

async function handleMessage(
  ctx: Context,
  rawText: string
): Promise<void> {
  const chatId = String(ctx.chat?.id)
  if (!chatId) return

  let typingInterval: ReturnType<typeof setInterval> | undefined
  let gatePromise: Promise<unknown> | undefined

  try {
    // --- Ship 1B pending-confirmation interceptor ------------------------
    // Before any Washing Machine / agent work, check whether this message
    // is a reply to a pending-confirmation prompt. Matches by Telegram
    // reply-to-message-id first, then falls through to sole-pending-for-
    // chat. Short-circuits the rest of handleMessage on any resolve path
    // so the agent is never called with Keith's "1"/"2"/date reply.
    const replyToMessageId =
      (ctx.message as { reply_to_message?: { message_id: number } } | undefined)
        ?.reply_to_message?.message_id ?? null
    try {
      const resume = await resumePendingConfirmation(
        { chatId, replyText: rawText, replyToMessageId },
        { pendingDir: PENDING_DIR, shelfWriter: shelfWriterForResume }
      )
      if (resume.status === 'resolved') {
        await ctx.reply(`Saved. Using date ${resume.chosenDate}.`)
        return
      }
      if (resume.status === 'retry') {
        await ctx.reply(resume.message ?? 'Reply with 1, 2, or a date in YYYY-MM-DD.')
        return
      }
      if (resume.status === 'corrupt-record') {
        await ctx.reply('That pending capture is malformed; discarding. Please send the card again.')
        return
      }
      if (resume.status === 'ambiguous') {
        await ctx.reply(
          resume.message ??
            'Multiple pending confirmations in this chat. Reply-to-quote the specific prompt.'
        )
        return
      }
      if (resume.status === 'error') {
        logger.error({ uuid: resume.uuid, chatId }, 'pending-resume: reported error, notifying user')
        await ctx.reply(
          resume.message ?? 'Something went wrong resolving the pending capture. Check logs.'
        )
        return
      }
      // status === 'not-found' → fall through to normal message handling
    } catch (err) {
      logger.warn({ err: String(err), chatId }, 'pending-resume intercept threw; continuing with normal handleMessage')
    }

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

    // Washing Machine Normalization Gate (text-only in Ship 1 -- photo/document
    // wrappers are skipped here and handled by Ship 1B's VLM pipeline).
    // Fire-and-forget future-turn-only contract (ratified 2026-04-23b after
    // Codex Tier 3 on Commit 3): facts in message N do NOT affect message N's
    // reply. The gate writes for message N+1. injectMemoryFromShelves below
    // reads the shelf snapshot at call time and does NOT await the gate --
    // race-free property locked in by memoryInject.test.ts.
    gatePromise = normalizationGate(rawText).catch((err: unknown) => {
      logger.error({ err: String(err), chatId }, 'normalizationGate threw; shelf row lost for this turn')
      return undefined
    })

    // Inject top-K raw rows from the semantic shelf (Ship 1 Commit 4).
    // Replaces the legacy buildMemoryContext FTS+recent path. Runs in
    // parallel with the gate above; neither blocks the other.
    const memoryContext = await injectMemoryFromShelves(rawText)

    // Vault-notes fallback (feature_vault-notes-fallback, 2026-05-23).
    // Fires only when the shelf inject returned empty (raw-rows hit nothing)
    // AND the message looks like fact retrieval. Searches the local vault
    // filesystem with a single grep call so meetings, dailies, research notes
    // captured via /meeting, /tldr, /research, /daily can still surface even
    // though EOD doesn't ingest them onto the shelf. Same graceful-degradation
    // contract: any error returns '' and the agent proceeds without it.
    const vaultContext = memoryContext === '' ? await injectVaultContext(rawText) : ''

    // Get existing session (may have been cleared above)
    const sessionId = getSession(chatId)

    // Start typing indicator -- fires before any slow pre-fetch so the user
    // sees "typing..." while a YouTube transcript is being downloaded.
    const sendTyping = async () => {
      try {
        await ctx.api.sendChatAction(ctx.chat!.id, 'typing')
      } catch {
        // ignore typing errors
      }
    }
    await sendTyping()
    typingInterval = setInterval(sendTyping, TYPING_REFRESH_MS)

    // Pre-fetch transcript for any YouTube URL in the user's message. On
    // success, `agentInput` carries the transcript + a system instruction that
    // tells the agent what to do (summary only vs. answer question). On
    // failure or when no URL is present, it falls back to the raw text so
    // the agent still sees what the user sent.
    let agentInput = rawText
    try {
      agentInput = await buildAgentInputWithYouTubeContext(rawText)
    } catch (err) {
      logger.warn({ err: String(err) }, 'YouTube pre-fetch threw; continuing with raw text')
    }

    const fullMessage = preferenceContext + memoryContext + vaultContext + agentInput

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
    // Await the gate here so if it is still running when the reply has
    // already been sent, the handler scope holds onto it instead of
    // leaving a dangling promise. Zero added latency if already settled.
    // gatePromise's own .catch handler (attached at fire-time above) is
    // the single error-suppression point -- it always resolves, never
    // rejects. Await without a second wrap so a future refactor that
    // removes the .catch cannot silently mask errors here.
    if (gatePromise) await gatePromise
  }
}

type VoiceMessageContext = Context & {
  message: NonNullable<Context['message']> & {
    voice: {
      file_id: string
    }
  }
  reply(message: string): Promise<unknown>
}

interface VoiceMessageDeps {
  voiceCapabilities: typeof voiceCapabilities
  downloadMedia: (fileId: string, originalFilename?: string) => Promise<string>
  transcribeAudio: typeof transcribeAudio
  handleMessage: (ctx: Context, rawText: string) => Promise<void>
  logVoiceError: (stage: 'download' | 'transcription' | 'processing' | 'reply', err: unknown) => void
}

async function replyVoiceFailure(
  ctx: VoiceMessageContext,
  deps: VoiceMessageDeps,
  message: string
): Promise<void> {
  try {
    await ctx.reply(message)
  } catch (err) {
    deps.logVoiceError('reply', err)
  }
}

export async function handleVoiceMessage(ctx: VoiceMessageContext, deps: VoiceMessageDeps): Promise<void> {
  const caps = deps.voiceCapabilities()
  if (!caps.stt) {
    await ctx.reply('Voice transcription is not configured.')
    return
  }

  let localPath: string
  try {
    // Telegram voice notes do not carry a user filename; media.ts resolves the
    // .ogg extension from Telegram's getFile response and retries transients.
    localPath = await deps.downloadMedia(ctx.message.voice.file_id)
  } catch (err) {
    deps.logVoiceError('download', err)
    await replyVoiceFailure(ctx, deps, 'Failed to transcribe voice note. Try again or type your message.')
    return
  }

  let transcript: string
  try {
    transcript = await deps.transcribeAudio(localPath)
  } catch (err) {
    deps.logVoiceError('transcription', err)
    await replyVoiceFailure(ctx, deps, 'Failed to transcribe voice note. Try again or type your message.')
    return
  }

  try {
    await ctx.reply(`[Transcribed]: ${transcript}`)
  } catch (err) {
    deps.logVoiceError('reply', err)
  }

  try {
    await deps.handleMessage(ctx, `[Voice transcribed]: ${transcript}`)
  } catch (err) {
    deps.logVoiceError('processing', err)
    await replyVoiceFailure(ctx, deps, 'Transcribed, but processing failed. Try typing the transcribed text above.')
  }
}

// --- Bot creation ---

export function createBot(): Bot {
  if (!TELEGRAM_BOT_TOKEN) {
    throw new Error('TELEGRAM_BOT_TOKEN not set. Run: npm run setup')
  }

  // Ship 1B: best-effort startup sweep of stale pending-confirmation files.
  // Runs once at createBot and continues if the directory is missing or
  // the sweep fails. Keeps .pending-confirmation/ from growing unbounded
  // when users send a card and then go silent.
  sweepExpiredPending().catch((err: unknown) => {
    logger.warn({ err: String(err) }, 'pending-cleanup: sweep threw (continuing)')
  })

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
    if (ctx.chat) {
      const chatIdStr = String(ctx.chat.id)
      // Silent-drop list: bot can post outbound to these chats, but ALL
      // inbound updates from them are dropped without invoking any handler
      // (no commands, no auto-reply, no warn log). Used for chats where the
      // bot is a one-way alert sink (e.g. K2Bi alerts supergroup) so the
      // channel stays clean -- only bot-originated alert posts appear, no
      // command replies, no "Not authorized." rejections. Logged at debug
      // level so audit-curious operators can see traffic without noise at
      // default log levels.
      if (SILENT_CHAT_IDS.includes(chatIdStr)) {
        logger.debug(
          { chatId: ctx.chat.id, updateType: ctx.update ? Object.keys(ctx.update).filter((k) => k !== 'update_id')[0] : 'unknown' },
          'silent-drop inbound update'
        )
        return
      }
      if (!isAuthorised(ctx.chat.id)) {
        logger.warn({ chatId: ctx.chat.id }, 'Unauthorized access attempt')
        await ctx.reply('Not authorized.')
        return
      }
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
      '/voice - Check voice capabilities\n' +
      '/media image - Generate an image\n' +
      '/media speech - Generate speech (TTS)'
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

  bot.command('media', async (ctx) => {
    await handleMediaCommand(ctx)
  })

  // --- Message handlers ---

  bot.on('message:text', async (ctx) => {
    const text = ctx.message.text
    if (text.startsWith('/')) return // skip unhandled commands
    await handleMessage(ctx, text)
  })

  bot.on('message:voice', async (ctx) => {
    await handleVoiceMessage(ctx, {
      voiceCapabilities,
      downloadMedia,
      transcribeAudio,
      handleMessage,
      logVoiceError: (stage, err) => logger.error({ err, stage }, 'Voice message handling failed'),
    })
  })

  bot.on('message:photo', async (ctx) => {
    try {
      const photos = ctx.message.photo
      const largest = photos[photos.length - 1]
      const localPath = await downloadMedia(largest.file_id)
      const caption = ctx.message.caption ?? ''
      const chatId = String(ctx.chat.id)
      // Telegram sends seconds; washingMachine needs epoch-ms.
      const messageTsMs = (ctx.message.date ?? Math.floor(Date.now() / 1000)) * 1000
      const promptMessageId = ctx.message.message_id

      // Ship 1B: route through the VLM attachment-ingest pipeline BEFORE
      // the agent call. OCR text is produced by extract-attachment.sh and
      // fed through normalizationGate. On pending-confirmation, post the
      // prompt and short-circuit; Keith's reply is caught by the
      // pending-resume interceptor at the top of handleMessage.
      //
      // Error differentiation: "unsupported format" and similar expected
      // rejects log at info level (dropping through to agent-only path is
      // correct UX). Everything else logs at error so broken extractors
      // are visible without digging through debug logs.
      const ingest = await ingestAttachment({
        type: 'photo',
        path: localPath,
        caption,
        messageTsMs,
        chatId,
        promptMessageId,
      }).catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err)
        const benign = /unsupported mime|GIF not supported|image not found/i.test(msg)
        if (benign) {
          logger.info({ err: msg, chatId }, 'attachmentIngest: benign reject; agent-only path')
        } else {
          logger.error(
            { err: msg, chatId },
            'attachmentIngest failed; investigate extractor. Falling through to agent-only path.'
          )
        }
        return null
      })

      if (ingest?.pendingPrompt) {
        // Post the confirmation prompt; Telegram records its message_id so
        // Keith's reply-to-quote routes to the right pending UUID.
        const sent = await ctx.reply(ingest.pendingPrompt)
        if (ingest.gate.pendingUuid) {
          rewritePendingPromptId(ingest.gate.pendingUuid, sent.message_id).catch((err: unknown) => {
            logger.warn(
              { err: String(err), uuid: ingest.gate.pendingUuid },
              'pending-ingest: prompt id rewrite failed; resume still routes by sole-pending fallback'
            )
          })
        }
        return
      }

      if (ingest?.rawOcrShelfWriterFailed) {
        await ctx.reply('OCR text was extracted, but K2B could not save it to the semantic shelf. I will still include it in this reply.')
      }

      // Append OCR text as context so the agent sees what was extracted,
      // even on the normal (no-contradiction) path. The agent response
      // runs through handleMessage below.
      const wrapper = buildPhotoMessage(localPath, caption)
      const messageWithOcr = ingest?.ocrText
        ? `${wrapper}\n\n[OCR extracted]: ${ingest.ocrText}`
        : wrapper
      await handleMessage(ctx, messageWithOcr)
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
      const filename = doc.file_name ?? 'unknown'
      const chatId = String(ctx.chat.id)
      const messageTsMs = (ctx.message.date ?? Math.floor(Date.now() / 1000)) * 1000
      const promptMessageId = ctx.message.message_id

      // Route PDFs / text docs through the Ship 1B ingest pipeline too.
      // Binary documents the extractor can't read exit non-zero; the
      // catch below differentiates benign rejects (unsupported mime) from
      // real extractor failures so operators can tell them apart.
      const ingest = await ingestAttachment({
        type: 'document',
        path: localPath,
        caption,
        messageTsMs,
        chatId,
        promptMessageId,
      }).catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : String(err)
        const benign = /unsupported|scanned PDF|document path not found/i.test(msg)
        if (benign) {
          logger.info(
            { err: msg, chatId, filename },
            'document ingest: benign reject; agent-only path'
          )
        } else {
          logger.error(
            { err: msg, chatId, filename },
            'document ingest failed; investigate extractor. Falling through to agent-only path.'
          )
        }
        return null
      })

      if (ingest?.pendingPrompt) {
        const sent = await ctx.reply(ingest.pendingPrompt)
        if (ingest.gate.pendingUuid) {
          rewritePendingPromptId(ingest.gate.pendingUuid, sent.message_id).catch((err: unknown) => {
            logger.warn(
              { err: String(err), uuid: ingest.gate.pendingUuid },
              'pending-ingest: prompt id rewrite failed'
            )
          })
        }
        return
      }

      if (ingest?.rawOcrShelfWriterFailed) {
        await ctx.reply('Document text was extracted, but K2B could not save it to the semantic shelf. I will still include it in this reply.')
      }

      const wrapper = buildDocumentMessage(localPath, filename, caption)
      const messageWithOcr = ingest?.ocrText
        ? `${wrapper}\n\n[Document text]: ${ingest.ocrText}`
        : wrapper
      await handleMessage(ctx, messageWithOcr)
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
  text: string,
  threadId?: string | null
): Promise<void> {
  if (!TELEGRAM_BOT_TOKEN) return
  const url = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`
  const payload: Record<string, unknown> = {
    chat_id: chatId,
    text: formatForTelegram(text),
    parse_mode: 'HTML',
  }
  if (threadId) {
    payload.message_thread_id = Number(threadId)
  }
  const body = JSON.stringify(payload)

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
