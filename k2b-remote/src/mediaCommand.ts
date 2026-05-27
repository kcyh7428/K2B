import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { lstatSync, openSync, closeSync, readSync, realpathSync, writeFileSync, renameSync } from 'node:fs'
import { resolve, relative, isAbsolute, sep, basename } from 'node:path'
import type { Context } from 'grammy'
import { ALLOWED_CHAT_ID, K2B_PROJECT_ROOT, K2B_VAULT_PATH, TELEGRAM_OUTBOX_DIR, TYPING_REFRESH_MS } from './config.js'
import { readEnvFile } from './env.js'
import { logger } from './logger.js'
import { sendMedia } from './telegram-outbox.js'

const MEDIA_ENV = readEnvFile(['GPTSAPI_KEY'])
const GPTSAPI_ASPECT_RATIOS = new Set(['1:1', '16:9', '9:16', '4:3', '3:4'])
const ASPECT_RATIO_PATTERN = /^\d+:\d+$/
const DEFAULT_IMAGE_ASPECT_RATIO = '16:9'
const MAX_IMAGE_PROMPT_LENGTH = 4000
const MAX_IMAGE_SLUG_LENGTH = 60
const MAX_SPEECH_TEXT_LENGTH = 4000
const SHELL_META_PATTERN = /[`|;&<>]|\$\(|\$\{/
const SLUG_PATTERN = /^[A-Za-z0-9._-]+$/
const IMAGE_EXTENSION_PATTERN = /\.(png|jpe?g)$/i
const AUDIO_EXTENSION_PATTERN = /\.mp3$/i
const SPEECH_VOICES = ['alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'] as const
const SPEECH_MODELS = ['tts-1', 'tts-1-hd'] as const
const SPEECH_VOICE_SET = new Set<string>(SPEECH_VOICES)
const SPEECH_MODEL_SET = new Set<string>(SPEECH_MODELS)
const DEFAULT_SPEECH_VOICE = 'onyx'
const DEFAULT_SPEECH_MODEL = 'tts-1-hd'
const PLAIN_OPTION_TOKEN_PATTERN = /^[A-Za-z]+$/
// Process lifetime, not a Telegram API request timeout. The bot already
// runs longer Agent SDK handlers; sendChatAction calls stay short and
// separate. The bash script's --timeout MUST be smaller than this minus
// the curl per-call max-time (GPTSAPI_CURL_TIMEOUT_S below) plus a safety
// margin, so the Node side never SIGTERMs a child that is mid-curl.
const GPTSAPI_IMAGE_TIMEOUT_MS = 150_000
const GPTSAPI_CURL_TIMEOUT_S = 30
const GPTSAPI_NODE_BUDGET_MARGIN_S = 10
const GPTSAPI_SPEECH_TIMEOUT_MS = 90_000

export interface MediaImageRequest {
  kind: 'image'
  prompt: string
  aspectRatio: string
  slug?: string
}

export interface MediaSpeechRequest {
  kind: 'speech'
  text: string
  voice: typeof SPEECH_VOICES[number]
  model: typeof SPEECH_MODELS[number]
  slug?: string
}

type MediaRequest = MediaImageRequest | MediaSpeechRequest

export interface ParsedMediaCommand {
  ok: boolean
  request?: MediaRequest
  message?: string
}

interface CommandToken {
  value: string
  quoted: boolean
}

function splitCommandArgTokens(input: string): CommandToken[] {
  const args: CommandToken[] = []
  let current = ''
  let quote: '"' | "'" | null = null
  let currentQuoted = false

  const trimmed = input.trim()
  for (let i = 0; i < trimmed.length; i++) {
    const char = trimmed[i]

    if (quote) {
      if (char === '\\') {
        let count = 1
        while (trimmed[i + count] === '\\') count += 1
        const next = trimmed[i + count]
        if (next === quote) {
          current += '\\'.repeat(Math.floor(count / 2))
          if (count % 2 === 1) {
            current += next
            i += count
          } else {
            i += count - 1
          }
        } else {
          current += '\\'.repeat(count)
          i += count - 1
        }
        continue
      }
      if (char === quote) {
        quote = null
      } else {
        current += char
      }
      continue
    }

    if (char === '"' || char === "'") {
      quote = char
      currentQuoted = true
      continue
    }
    if (/\s/.test(char)) {
      if (current || currentQuoted) {
        args.push({ value: current, quoted: currentQuoted })
        current = ''
        currentQuoted = false
      }
      continue
    }
    current += char
  }

  if (current || currentQuoted) {
    args.push({ value: current, quoted: currentQuoted })
  }
  return args
}

export function splitCommandArgs(input: string): string[] {
  return splitCommandArgTokens(input).map((token) => token.value)
}

export function parseMediaCommand(text: string): ParsedMediaCommand {
  const commandTokens = splitCommandArgTokens(text)
  const tokens = commandTokens.map((token) => token.value)
  const command = tokens[0]?.replace(/@.+$/, '')
  if (command !== '/media') {
    return { ok: false, message: 'Use /media image "prompt" [aspect] [slug].' }
  }

  const subcommand = tokens[1]
  if (!subcommand) {
    return { ok: false, message: 'Use /media image "prompt" [aspect] [slug].' }
  }
  if (subcommand === 'speech') {
    return parseSpeechCommand(commandTokens.slice(2))
  }
  if (subcommand !== 'image') {
    return {
      ok: false,
      message: 'Only /media image and /media speech are wired in Telegram. Use the MacBook session for video, music, and transcription.',
    }
  }

  const args: CommandToken[] = []
  for (const token of commandTokens.slice(2)) {
    if (token.value === '--minimax') {
      return {
        ok: false,
        message:
          'MiniMax image fallback was retired 2026-05-27. Use `/media image <prompt>` (GPTsAPI gpt-image-2 is the only path).',
      }
    } else {
      args.push(token)
    }
  }

  if (args.length === 0) {
    return { ok: false, message: 'Use /media image "prompt" [aspect] [slug].' }
  }

  const allowedAspectRatios = GPTSAPI_ASPECT_RATIOS
  const firstAspectIndex = args.findIndex((arg) => ASPECT_RATIO_PATTERN.test(arg.value))
  if (firstAspectIndex >= 0 && !args[0]?.quoted) {
    return {
      ok: false,
      message: 'Quote the prompt when using an aspect ratio or slug: /media image "prompt" 16:9 slug',
    }
  }

  let aspectRatio = DEFAULT_IMAGE_ASPECT_RATIO
  let prompt = ''
  let slug: string | undefined

  if (args[0]?.quoted) {
    prompt = args[0].value.trim()
    const maybeAspect = args[1]?.value
    if (maybeAspect && allowedAspectRatios.has(maybeAspect)) {
      aspectRatio = maybeAspect
      slug = args.slice(2).map((arg) => arg.value).join('-') || undefined
    } else if (maybeAspect && ASPECT_RATIO_PATTERN.test(maybeAspect)) {
      return {
        ok: false,
        message: `${maybeAspect} is not supported for gpt-image-2.`,
      }
    } else if (maybeAspect) {
      slug = args.slice(1).map((arg) => arg.value).join('-') || undefined
    }
  } else {
    prompt = args.map((arg) => arg.value).join(' ').trim()
  }

  if (!prompt) {
    return { ok: false, message: 'Image prompt is missing.' }
  }
  if (prompt.length > MAX_IMAGE_PROMPT_LENGTH) {
    return { ok: false, message: `Image prompt is too long. Keep it under ${MAX_IMAGE_PROMPT_LENGTH} characters.` }
  }
  if (/[\u0000-\u001f\u007f]/.test(prompt) || SHELL_META_PATTERN.test(prompt)) {
    return { ok: false, message: 'Image prompt contains unsupported shell metacharacters.' }
  }

  if (slug !== undefined) {
    if (slug.length > MAX_IMAGE_SLUG_LENGTH) {
      return { ok: false, message: `Slug is too long. Keep it under ${MAX_IMAGE_SLUG_LENGTH} characters.` }
    }
    if (!SLUG_PATTERN.test(slug)) {
      return { ok: false, message: 'Slug must be letters, numbers, dot, dash, or underscore only.' }
    }
  }

  return {
    ok: true,
    request: {
      kind: 'image',
      prompt,
      aspectRatio,
      ...(slug ? { slug } : {}),
    },
  }
}

function isSpeechVoice(value: string): value is MediaSpeechRequest['voice'] {
  return SPEECH_VOICE_SET.has(value)
}

function isSpeechModel(value: string): value is MediaSpeechRequest['model'] {
  return SPEECH_MODEL_SET.has(value)
}

function parseSpeechCommand(args: CommandToken[]): ParsedMediaCommand {
  if (!args[0]?.quoted) {
    return { ok: false, message: 'Quote the text when using /media speech: /media speech "text" [voice] [model] [slug]' }
  }

  const text = args[0].value.trim()
  if (!text) {
    return { ok: false, message: 'Speech text is missing.' }
  }
  if (text.length > MAX_SPEECH_TEXT_LENGTH) {
    return { ok: false, message: `Speech text is too long. Keep it under ${MAX_SPEECH_TEXT_LENGTH} characters.` }
  }
  if (/[\u0000-\u001f\u007f]/.test(text) || SHELL_META_PATTERN.test(text)) {
    return { ok: false, message: 'Speech text contains unsupported shell metacharacters.' }
  }

  let voice: MediaSpeechRequest['voice'] = DEFAULT_SPEECH_VOICE
  let model: MediaSpeechRequest['model'] = DEFAULT_SPEECH_MODEL
  let cursor = 1

  // Parse optional fields left-to-right: voice first, model second, then
  // slug fragments. A hyphenated token is treated as a slug, so
  // `nova my-greeting` consumes voice while `my-greeting nova` stays a slug.
  const maybeVoice = args[cursor]?.value
  if (maybeVoice && isSpeechVoice(maybeVoice)) {
    voice = maybeVoice
    cursor += 1
  } else if (maybeVoice && PLAIN_OPTION_TOKEN_PATTERN.test(maybeVoice)) {
    return {
      ok: false,
      message: `Voice ${maybeVoice} is not supported. Choose one of: ${SPEECH_VOICES.join(', ')}.`,
    }
  }

  const maybeModel = args[cursor]?.value
  if (maybeModel && isSpeechModel(maybeModel)) {
    model = maybeModel
    cursor += 1
  } else if (maybeModel && cursor > 1 && PLAIN_OPTION_TOKEN_PATTERN.test(maybeModel)) {
    return {
      ok: false,
      message: `Model ${maybeModel} is not supported. Choose one of: ${SPEECH_MODELS.join(', ')}.`,
    }
  }

  const slug = args.slice(cursor).map((arg) => arg.value).join('-') || undefined
  if (slug !== undefined) {
    if (slug.length > MAX_IMAGE_SLUG_LENGTH) {
      return { ok: false, message: `Slug is too long. Keep it under ${MAX_IMAGE_SLUG_LENGTH} characters.` }
    }
    if (!SLUG_PATTERN.test(slug)) {
      return { ok: false, message: 'Slug must be letters, numbers, dot, dash, or underscore only.' }
    }
  }

  return {
    ok: true,
    request: {
      kind: 'speech',
      text,
      voice,
      model,
      ...(slug ? { slug } : {}),
    },
  }
}

export function extractGeneratedImagePath(stdout: string): string | undefined {
  return extractGeneratedPath(stdout, IMAGE_EXTENSION_PATTERN)
}

export function extractGeneratedAudioPath(stdout: string): string | undefined {
  return extractGeneratedPath(stdout, AUDIO_EXTENSION_PATTERN)
}

function extractGeneratedPath(stdout: string, extPattern: RegExp): string | undefined {
  for (const rawLine of stdout.split(/\r?\n/)) {
    const line = rawLine.trim()
    if (!line) continue
    const savedMatch = line.match(/^Saved:\s*(.+)$/)
    if (savedMatch?.[1]) {
      const candidate = savedMatch[1].trim()
      const safePath = candidatePathLooksSafeForExt(candidate, extPattern)
      if (safePath) return safePath
    }
  }
  return undefined
}

// Pre-resolution check on the literal candidate string. Reject any
// candidate that is not an absolute path with an image extension, that
// contains a parent-dir component or NUL byte, or that does not lie
// inside one of our allowed roots (vault or /tmp). The '..' check runs
// BEFORE resolve() so a path like /tmp/../etc/passwd.png is rejected
// for the right reason instead of being silently collapsed first.
// Filesystem-level (symlink) resolution happens once, separately, in
// resolveImagePathInsideAllowedRoots — never paired with existsSync.
function candidatePathLooksSafeForExt(candidate: string, extPattern: RegExp): string | undefined {
  if (!candidate || candidate.includes('\0')) return undefined
  if (!isAbsolute(candidate)) return undefined
  if (!extPattern.test(candidate)) return undefined
  if (candidate.split(/[\\/]/).includes('..')) return undefined
  const normalized = resolve(candidate)
  const allowedRoots = [K2B_VAULT_PATH, '/tmp']
  const allowed = allowedRoots.some((root) => {
    const rel = relative(resolve(root), normalized)
    return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))
  })
  return allowed ? normalized : undefined
}

// resolveImagePathInsideAllowedRoots is the single entry point for
// turning a candidate string into a vetted real filesystem path. It
// calls realpathSync ONCE so there is no TOCTOU window between
// existence-check and stat/realpath. The caller must use the returned
// string for the actual sendMedia call so symlink swaps after this
// point cannot redirect the read.
function resolveImagePathInsideAllowedRoots(candidate: string): string | undefined {
  return resolvePathInsideAllowedRoots(candidate, IMAGE_EXTENSION_PATTERN)
}

function resolveAudioPathInsideAllowedRoots(candidate: string): string | undefined {
  return resolvePathInsideAllowedRoots(candidate, AUDIO_EXTENSION_PATTERN)
}

function resolvePathInsideAllowedRoots(candidate: string, extPattern: RegExp): string | undefined {
  const looksSafe = candidatePathLooksSafeForExt(candidate, extPattern)
  if (!looksSafe) return undefined
  let realPath: string
  let stat: ReturnType<typeof lstatSync>
  try {
    realPath = realpathSync(looksSafe)
    stat = lstatSync(realPath)
  } catch {
    return undefined
  }
  if (!stat.isFile()) return undefined
  // Resolve allowed roots through realpath too so a symlinked
  // K2B_VAULT_PATH still gates correctly.
  let resolvedRoots: string[]
  try {
    resolvedRoots = [realpathSync(resolve(K2B_VAULT_PATH)), realpathSync(resolve('/tmp'))]
  } catch {
    return undefined
  }
  const allowed = resolvedRoots.some((root) => {
    const rel = relative(root, realPath)
    return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))
  })
  return allowed ? realPath : undefined
}

function toVaultRelativePath(candidate: string): string | undefined {
  let vaultReal: string
  let candidateReal: string
  try {
    vaultReal = realpathSync(resolve(K2B_VAULT_PATH))
    candidateReal = realpathSync(resolve(candidate))
  } catch {
    return undefined
  }
  const rel = relative(vaultReal, candidateReal)
  if (rel === '' || rel.startsWith('..') || isAbsolute(rel)) return undefined
  return rel
}

// Read the first 12 bytes of the file and confirm a PNG or JPEG magic
// signature. This is the TypeScript-side guard that defends sendMedia
// against a tampered or non-image file even if the bash wrapper is
// compromised. JPEG: FF D8 FF. PNG: 89 50 4E 47 0D 0A 1A 0A.
const MIN_IMAGE_BYTES = 100

function fileLooksLikeImage(realPath: string): boolean {
  let fd: number | undefined
  try {
    const stat = lstatSync(realPath)
    if (!stat.isFile() || stat.size < MIN_IMAGE_BYTES) return false
    fd = openSync(realPath, 'r')
    const buf = Buffer.alloc(12)
    const n = readSync(fd, buf, 0, 12, 0)
    if (n < 3) return false
    const isPng = n >= 8 && buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47 &&
      buf[4] === 0x0d && buf[5] === 0x0a && buf[6] === 0x1a && buf[7] === 0x0a
    const isJpeg = buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff
    return isPng || isJpeg
  } catch {
    return false
  } finally {
    if (fd !== undefined) {
      try { closeSync(fd) } catch { /* ignore */ }
    }
  }
}

const MIN_AUDIO_BYTES = 256
const MP3_FRAME_SYNC_SECOND_BYTES = new Set([0xfb, 0xf3, 0xf2, 0xfa, 0xe3, 0xe2, 0xeb, 0xea])

function fileLooksLikeAudio(realPath: string): boolean {
  let fd: number | undefined
  try {
    const stat = lstatSync(realPath)
    if (!stat.isFile() || stat.size < MIN_AUDIO_BYTES) return false
    fd = openSync(realPath, 'r')
    const buf = Buffer.alloc(16)
    const n = readSync(fd, buf, 0, 16, 0)
    if (n < 3) return false
    const hasId3Header = buf[0] === 0x49 && buf[1] === 0x44 && buf[2] === 0x33
    const hasFrameSync = n >= 2 && buf[0] === 0xff && MP3_FRAME_SYNC_SECOND_BYTES.has(buf[1])
    return hasId3Header || hasFrameSync
  } catch {
    return false
  } finally {
    if (fd !== undefined) {
      try { closeSync(fd) } catch { /* ignore */ }
    }
  }
}

function runImageScript(request: MediaImageRequest): Promise<{ stdout: string; stderr: string }> {
  const gptsapiKey = gptsapiKeyForMedia()

  if (!gptsapiKey) {
    return Promise.reject(new Error('GPTSAPI_KEY is not set in the bot environment.'))
  }

  const script = resolve(K2B_PROJECT_ROOT, 'scripts', 'gptsapi-image.sh')
  const args = [
    '--prompt',
    request.prompt,
    '--aspect-ratio',
    request.aspectRatio,
    '--timeout',
    String(
      Math.max(
        30,
        Math.floor(GPTSAPI_IMAGE_TIMEOUT_MS / 1000) - GPTSAPI_CURL_TIMEOUT_S - GPTSAPI_NODE_BUDGET_MARGIN_S
      )
    ),
    ...(request.slug ? ['--slug', request.slug] : []),
  ]

  return new Promise((resolvePromise, reject) => {
    const child = spawn(script, args, {
      cwd: K2B_PROJECT_ROOT,
      env: {
        ...process.env,
        GPTSAPI_KEY: gptsapiKey,
        K2B_VAULT_PATH,
        // Pin the bash-side curl timeout so it stays coupled to the
        // Node-side timeout math even if a stray env override is
        // present in the parent shell.
        GPTSAPI_CURL_TIMEOUT_SECONDS: String(GPTSAPI_CURL_TIMEOUT_S),
      },
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    let killTimeout: ReturnType<typeof setTimeout> | undefined

    const clearTimers = () => {
      clearTimeout(timeout)
      if (killTimeout) {
        clearTimeout(killTimeout)
        killTimeout = undefined
      }
    }

    const signalChild = (signal: NodeJS.Signals) => {
      try {
        child.kill(signal)
      } catch {
        // Process is already gone.
      }
    }

    const timeoutMs = GPTSAPI_IMAGE_TIMEOUT_MS
    const timeout = setTimeout(() => {
      if (settled) return
      settled = true
      signalChild('SIGTERM')
      killTimeout = setTimeout(() => {
        signalChild('SIGKILL')
      }, 5000).unref()
      const err = new Error(`image generation timed out after ${timeoutMs / 1000}s`) as Error & {
        stdout?: string
        stderr?: string
        code?: number
      }
      err.stdout = stdout
      err.stderr = stderr
      reject(err)
    }, timeoutMs)

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk
    })
    child.on('error', (err) => {
      clearTimers()
      if (settled) return
      settled = true
      reject(err)
    })
    child.on('close', (code) => {
      clearTimers()
      if (settled) return
      settled = true
      if (code === 0) {
        resolvePromise({ stdout, stderr })
      } else {
        const err = new Error(stderr.trim() || `image script exited ${code}`) as Error & {
          stdout?: string
          stderr?: string
          code?: number
        }
        err.stdout = stdout
        err.stderr = stderr
        err.code = code ?? undefined
        reject(err)
      }
    })
  })
}

function runSpeechScript(request: MediaSpeechRequest): Promise<{ stdout: string; stderr: string }> {
  const gptsapiKey = gptsapiKeyForMedia()

  if (!gptsapiKey) {
    return Promise.reject(new Error('GPTSAPI_KEY is not set in the bot environment.'))
  }

  const script = resolve(K2B_PROJECT_ROOT, 'scripts', 'gptsapi-speech.sh')
  const args = [
    request.text,
    request.voice,
    request.model,
    ...(request.slug ? [request.slug] : []),
  ]

  return new Promise((resolvePromise, reject) => {
    const child = spawn(script, args, {
      cwd: K2B_PROJECT_ROOT,
      env: {
        ...process.env,
        GPTSAPI_KEY: gptsapiKey,
        K2B_VAULT_PATH,
        K2B_VAULT: K2B_VAULT_PATH,
      },
    })
    let stdout = ''
    let stderr = ''
    let settled = false
    let killTimeout: ReturnType<typeof setTimeout> | undefined

    const clearTimers = () => {
      clearTimeout(timeout)
      if (killTimeout) {
        clearTimeout(killTimeout)
        killTimeout = undefined
      }
    }

    const signalChild = (signal: NodeJS.Signals) => {
      try {
        child.kill(signal)
      } catch {
        // Process is already gone.
      }
    }

    const timeout = setTimeout(() => {
      if (settled) return
      settled = true
      signalChild('SIGTERM')
      killTimeout = setTimeout(() => {
        signalChild('SIGKILL')
      }, 5000).unref()
      const err = new Error(`speech generation timed out after ${GPTSAPI_SPEECH_TIMEOUT_MS / 1000}s`) as Error & {
        stdout?: string
        stderr?: string
        code?: number
      }
      err.stdout = stdout
      err.stderr = stderr
      reject(err)
    }, GPTSAPI_SPEECH_TIMEOUT_MS)

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk
    })
    child.on('error', (err) => {
      clearTimers()
      if (settled) return
      settled = true
      reject(err)
    })
    child.on('close', (code) => {
      clearTimers()
      if (settled) return
      settled = true
      if (code === 0) {
        resolvePromise({ stdout, stderr })
      } else {
        const err = new Error(stderr.trim() || `speech script exited ${code}`) as Error & {
          stdout?: string
          stderr?: string
          code?: number
        }
        err.stdout = stdout
        err.stderr = stderr
        err.code = code ?? undefined
        reject(err)
      }
    })
  })
}

function gptsapiKeyForMedia(): string {
  return process.env.GPTSAPI_KEY || MEDIA_ENV['GPTSAPI_KEY'] || ''
}

// Cap key length we are willing to use as a literal substitution token.
// Tokens longer than this (e.g., a stray 4KB blob masquerading as a key)
// would inflate regex/replace cost without benefit. Real API keys are
// well under 512 bytes.
const MAX_SECRET_LENGTH_FOR_REDACTION = 512

export function safeUserErrorMessage(detail: string): string {
  let safe = detail
  const variants = []
  const envSecrets = Object.entries(process.env)
    .filter(([key]) => /(API_KEY|_KEY|SECRET|TOKEN)$/i.test(key))
    .map(([, value]) => value)
  for (const secret of [gptsapiKeyForMedia(), process.env.GPTSAPI_KEY, ...envSecrets]) {
    if (!secret) continue
    if (secret.length > MAX_SECRET_LENGTH_FOR_REDACTION) continue
    variants.push(secret, encodeURIComponent(secret), JSON.stringify(secret).slice(1, -1))
  }
  // Use split/join for literal replacement. This is O(n) and immune to
  // regex engine size limits or catastrophic backtracking even if a
  // secret happens to contain regex meta-characters.
  for (const variant of Array.from(new Set(variants)).sort((a, b) => b.length - a.length)) {
    if (!variant) continue
    if (variant.length > MAX_SECRET_LENGTH_FOR_REDACTION) continue
    safe = safe.split(variant).join('<redacted>')
  }
  // Match `Bearer <token>` only when (1) `Bearer` is at a word boundary,
  // (2) the token is 8+ chars, and (3) the token contains at least one
  // non-letter (digit, `-`, `_`, `.`, `/`, `+`, `~`, `=`). Real API
  // tokens always meet that bar; English prose words like "of", "good",
  // or "authentication" do not. The char class includes `=` so that
  // base64-padded tokens are consumed in their entirety rather than
  // leaving a tail visible after the redaction span.
  safe = safe.replace(
    /(^|[^A-Za-z0-9_])Bearer\s+(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])([A-Za-z0-9._~+/=-]{8,})/g,
    '$1Bearer <redacted>'
  )
  safe = safe.replace(/(?:export\s+)?(GPTSAPI_KEY|MINIMAX_API_KEY)\s*[:=]\s*["']?[^"'\s]+["']?/g, '$1=<redacted>')
  safe = safe.replace(/(https?:\/\/)[^/\s:@]+:[^/\s@]+@/g, '$1<redacted>@')
  safe = safe.replaceAll(K2B_PROJECT_ROOT, '$K2B_PROJECT_ROOT')
  safe = safe.replaceAll(K2B_VAULT_PATH, '$K2B_VAULT_PATH')
  return safe
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function redactErrorForLog(err: unknown): unknown {
  if (!(err instanceof Error)) {
    return safeUserErrorMessage(String(err))
  }
  const detail = err as Error & { stdout?: string; stderr?: string; code?: number }
  return {
    name: err.name,
    message: safeUserErrorMessage(err.message),
    stack: err.stack ? safeUserErrorMessage(err.stack) : undefined,
    code: detail.code,
    stdout: detail.stdout ? safeUserErrorMessage(detail.stdout).slice(0, 2000) : undefined,
    stderr: detail.stderr ? safeUserErrorMessage(detail.stderr).slice(0, 2000) : undefined,
  }
}

export async function handleMediaCommand(ctx: Context): Promise<void> {
  const message = ctx.message as { text?: string } | undefined
  const text = message?.text ?? ''
  const parsed = parseMediaCommand(text)
  if (!parsed.ok || !parsed.request) {
    await replyText(ctx, parsed.message ?? 'Use /media image "prompt" [aspect] [slug].')
    return
  }

  const chatId = ctx.chat?.id
  if (chatId === undefined) {
    logger.warn('media command without chat id')
    return
  }
  if (ALLOWED_CHAT_ID && String(chatId) !== ALLOWED_CHAT_ID) {
    logger.warn({ chatId }, 'Unauthorized media command attempt')
    return
  }

  const request = parsed.request
  if (request.kind === 'speech') {
    await handleSpeechMediaCommand(ctx, chatId, request)
    return
  }

  const providerLabel = 'gpt-image-2'
  const waitText = 'Generating image with gpt-image-2. This usually takes about 40 seconds.'
  await replyText(ctx, waitText)

  const sendUploadAction = async () => {
    try {
      await ctx.api.sendChatAction(chatId, 'upload_photo')
    } catch {
      // Ignore transient chat action failures.
    }
  }
  await sendUploadAction()
  const uploadInterval = setInterval(sendUploadAction, TYPING_REFRESH_MS)

  try {
    const { stdout, stderr } = await runImageScript(request)
    if (stderr.trim()) {
      logger.info(
        { provider: 'gptsapi', stderr: safeUserErrorMessage(stderr).slice(0, 2000) },
        'media image script stderr'
      )
    }
    const imagePath = extractGeneratedImagePath(stdout)
    const safeImagePath = imagePath ? resolveImagePathInsideAllowedRoots(imagePath) : undefined
    if (!safeImagePath) {
      logger.error(
        {
          stdout: safeUserErrorMessage(stdout).slice(0, 2000),
          stderr: safeUserErrorMessage(stderr).slice(0, 2000),
        },
        'media image script did not produce a readable path'
      )
      await replyText(ctx, `Image generation finished, but I could not find the output file. Provider: ${providerLabel}.`)
      return
    }
    if (!fileLooksLikeImage(safeImagePath)) {
      logger.error({ imagePath: safeUserErrorMessage(safeImagePath) }, 'media image file failed magic-byte check')
      await replyText(ctx, 'Image generation produced a file that is not a valid PNG or JPEG. Skipping send.')
      return
    }
    const vaultRel = toVaultRelativePath(safeImagePath)
    if (!vaultRel) {
      logger.error({ imagePath: safeUserErrorMessage(safeImagePath) }, 'media image path outside vault')
      await replyText(ctx, 'Image generation finished outside the vault, so I will not send that file through Telegram.')
      return
    }

    const obsidianRel = vaultRel.split(sep).join('/')
    const caption = `Generated via ${providerLabel}`
    const sent = await sendMedia(ctx.api, chatId, {
      type: 'photo',
      path: safeImagePath,
      caption,
    })

    if (sent) {
      await replyText(ctx, `Done. Obsidian embed: ![[${obsidianRel}]]`)
    } else {
      // Hand the manifest to the outbox so the background scanner retries
      // delivery on the next loop. Keith still gets the Obsidian embed
      // immediately so the image is not lost.
      const queued = enqueueOutboxManifest({ type: 'photo', path: safeImagePath, caption })
      const tail = queued
        ? 'I have queued it for retry through the outbox.'
        : 'Could not queue a retry, so please pull it from the vault.'
      await replyText(ctx, `Generated image but Telegram could not send it. ${tail} Obsidian embed: ![[${obsidianRel}]]`)
    }
  } catch (err) {
    const detail = safeUserErrorMessage(errorMessage(err))
    logger.error({ err: redactErrorForLog(err) }, 'media image command failed')
    logger.info({ detail: detail.slice(0, 1000) }, 'media image failure detail')
    const code = (err as { code?: number } | null)?.code
    if (code === 2) {
      // exit 2 from gptsapi-image.sh = argument or validation error.
      // Surface the redacted stderr line so Keith knows what to fix.
      const stderrText = (err as { stderr?: string } | null)?.stderr ?? ''
      const hint = safeUserErrorMessage(stderrText).split('\n').filter(Boolean).slice(-1)[0] ?? detail
      const trimmed = hint.length > 280 ? `${hint.slice(0, 280)}...` : hint
      await replyText(ctx, `Image command rejected: ${trimmed}`)
    } else {
      await replyText(ctx, `Image generation failed. Provider: ${providerLabel}. Check bot logs for details.`)
    }
  } finally {
    clearInterval(uploadInterval)
  }
}

async function handleSpeechMediaCommand(ctx: Context, chatId: number, request: MediaSpeechRequest): Promise<void> {
  const providerLabel = `GPTsAPI ${request.model}`
  await replyText(ctx, `Generating speech with ${request.model}. This usually takes about 5 to 10 seconds.`)

  const sendUploadAction = async () => {
    try {
      await ctx.api.sendChatAction(chatId, 'upload_voice')
    } catch {
      // Ignore transient chat action failures.
    }
  }
  await sendUploadAction()
  const uploadInterval = setInterval(sendUploadAction, TYPING_REFRESH_MS)

  try {
    const { stdout, stderr } = await runSpeechScript(request)
    if (stderr.trim()) {
      logger.info(
        { model: request.model, voice: request.voice, stderr: safeUserErrorMessage(stderr).slice(0, 2000) },
        'media speech script stderr'
      )
    }
    const audioPath = extractGeneratedAudioPath(stdout)
    const safeAudioPath = audioPath ? resolveAudioPathInsideAllowedRoots(audioPath) : undefined
    if (!safeAudioPath) {
      logger.error(
        {
          stdout: safeUserErrorMessage(stdout).slice(0, 2000),
          stderr: safeUserErrorMessage(stderr).slice(0, 2000),
        },
        'media speech script did not produce a readable path'
      )
      await replyText(ctx, `Speech generation finished, but I could not find the output file. Provider: ${providerLabel}.`)
      return
    }
    if (!fileLooksLikeAudio(safeAudioPath)) {
      logger.error({ audioPath: safeUserErrorMessage(safeAudioPath) }, 'media speech file failed magic-byte check')
      await replyText(ctx, 'Speech generation produced a file that is not a valid MP3. Skipping send.')
      return
    }
    const vaultRel = toVaultRelativePath(safeAudioPath)
    if (!vaultRel) {
      logger.error({ audioPath: safeUserErrorMessage(safeAudioPath) }, 'media speech path outside vault')
      await replyText(ctx, 'Speech generation finished outside the vault, so I will not send that file through Telegram.')
      return
    }

    const obsidianRel = vaultRel.split(sep).join('/')
    const caption = `Generated via ${providerLabel}`
    const sent = await sendMedia(ctx.api, chatId, {
      type: 'audio',
      path: safeAudioPath,
      caption,
    })

    if (sent) {
      await replyText(ctx, `Done. Obsidian embed: ![[${obsidianRel}]]`)
    } else {
      const queued = enqueueOutboxManifest({ type: 'audio', path: safeAudioPath, caption })
      const tail = queued
        ? 'I have queued it for retry through the outbox.'
        : 'Could not queue a retry, so please pull it from the vault.'
      await replyText(ctx, `Generated speech but Telegram could not send it. ${tail} Obsidian embed: ![[${obsidianRel}]]`)
    }
  } catch (err) {
    const detail = safeUserErrorMessage(errorMessage(err))
    logger.error({ err: redactErrorForLog(err) }, 'media speech command failed')
    logger.info({ detail: detail.slice(0, 1000) }, 'media speech failure detail')
    const code = (err as { code?: number } | null)?.code
    if (code === 2) {
      const stderrText = (err as { stderr?: string } | null)?.stderr ?? ''
      const hint = safeUserErrorMessage(stderrText).split('\n').filter(Boolean).slice(-1)[0] ?? detail
      const trimmed = hint.length > 280 ? `${hint.slice(0, 280)}...` : hint
      await replyText(ctx, `Speech command rejected: ${trimmed}`)
    } else {
      await replyText(ctx, `Speech generation failed. Provider: ${providerLabel}. Check bot logs for details.`)
    }
  } finally {
    clearInterval(uploadInterval)
  }
}

function enqueueOutboxManifest(manifest: { type: 'photo' | 'audio' | 'video' | 'document'; path: string; caption?: string }): boolean {
  try {
    const filename = `${Date.now()}_${process.pid}_${randomBytes(6).toString('hex')}_media.json`
    const dest = resolve(TELEGRAM_OUTBOX_DIR, filename)
    const tmp = `${dest}.tmp`
    writeFileSync(tmp, JSON.stringify(manifest), { encoding: 'utf-8', mode: 0o600 })
    renameSync(tmp, dest)
    logger.info({ manifest: { type: manifest.type, path: basename(manifest.path) } }, 'media: queued outbox manifest for retry')
    return true
  } catch (err) {
    logger.error({ err: redactErrorForLog(err) }, 'media: failed to queue outbox manifest')
    return false
  }
}

async function replyText(ctx: Context, text: string): Promise<void> {
  try {
    await ctx.reply(text)
  } catch (err) {
    logger.warn({ err: redactErrorForLog(err) }, 'media command reply failed')
  }
}
