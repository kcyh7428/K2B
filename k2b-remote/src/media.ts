import { writeFileSync, mkdirSync, readdirSync, statSync, unlinkSync } from 'node:fs'
import { resolve, basename } from 'node:path'
import { request as httpsRequest } from 'node:https'
import { HttpsProxyAgent } from 'https-proxy-agent'
import { UPLOADS_DIR, TELEGRAM_BOT_TOKEN, HTTP_PROXY } from './config.js'
import { logger } from './logger.js'

const proxyAgent = HTTP_PROXY ? new HttpsProxyAgent(HTTP_PROXY) : undefined
const MEDIA_DOWNLOAD_ATTEMPTS = 3
const MEDIA_DOWNLOAD_RETRY_DELAY_MS = 500
const MEDIA_DOWNLOAD_TIMEOUT_MS = 30_000
const MEDIA_DOWNLOAD_MAX_REDIRECTS = 5
const MEDIA_DOWNLOAD_MAX_RETRY_DELAY_MS = 30_000
const MEDIA_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024

// Ensure uploads dir exists
mkdirSync(UPLOADS_DIR, { recursive: true })

function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '-')
}

type HttpGetter = (url: string) => Promise<Buffer>

interface TelegramFileInfo {
  ok?: boolean
  error_code?: number
  description?: string
  result?: {
    file_path?: string
  }
}

export interface DownloadMediaDeps {
  httpGet?: HttpGetter
  now?: () => number
  retryDelayMs?: number
  writeFile?: typeof writeFileSync
}

async function httpGet(url: string): Promise<Buffer> {
  return new Promise((resolvePromise, reject) => {
    let settled = false
    let activeReq: ReturnType<typeof httpsRequest> | undefined

    const timeout = setTimeout(() => {
      if (settled) return
      const err = new Error('Telegram media request timed out') as NodeJS.ErrnoException
      err.code = 'ETIMEDOUT'
      activeReq?.destroy(err)
      fail(err)
    }, MEDIA_DOWNLOAD_TIMEOUT_MS)

    const fail = (err: unknown) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      activeReq = undefined
      reject(err)
    }

    const succeed = (body: Buffer) => {
      if (settled) return
      settled = true
      clearTimeout(timeout)
      activeReq = undefined
      resolvePromise(body)
    }

    const requestUrl = (currentUrl: string, redirectCount: number) => {
      const req = httpsRequest(currentUrl, { agent: proxyAgent }, (res) => {
        res.on('error', fail)

        // Follow redirects
        if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
          res.resume()
          if (redirectCount >= MEDIA_DOWNLOAD_MAX_REDIRECTS) {
            const err = new Error('too many Telegram media redirects') as NodeJS.ErrnoException
            err.code = 'ERR_TOO_MANY_REDIRECTS'
            fail(err)
            return
          }
          requestUrl(new URL(res.headers.location, currentUrl).toString(), redirectCount + 1)
          return
        }

        if (res.statusCode && (res.statusCode < 200 || res.statusCode >= 300)) {
          res.resume()
          const endpoint = new URL(currentUrl).pathname.endsWith('/getFile')
            ? 'Telegram getFile endpoint'
            : 'Telegram media endpoint'
          const err = new Error(`HTTP ${res.statusCode} from ${endpoint}`) as NodeJS.ErrnoException
          err.code = `HTTP_${res.statusCode}`
          fail(err)
          return
        }

        const chunks: Buffer[] = []
        let totalBytes = 0
        res.on('data', (chunk: Buffer) => {
          totalBytes += chunk.length
          if (totalBytes > MEDIA_DOWNLOAD_MAX_BYTES) {
            const err = new Error('Telegram media response exceeded max size') as NodeJS.ErrnoException
            err.code = 'MEDIA_RESPONSE_TOO_LARGE'
            fail(err)
            res.destroy(err)
            return
          }
          chunks.push(chunk)
        })
        res.on('end', () => {
          if (settled) return
          const body = Buffer.concat(chunks)
          if (body.length === 0) {
            const err = new Error('empty Telegram media response') as NodeJS.ErrnoException
            err.code = 'EMPTY_RESPONSE'
            fail(err)
            return
          }
          res.removeListener('error', fail)
          succeed(body)
        })
      })

      activeReq = req
      req.on('error', fail)
      req.end()
    }

    requestUrl(url, 0)
  })
}

async function withRetry<T>(operation: () => Promise<T>, retryDelayMs: number): Promise<T> {
  let lastErr: unknown
  const baseDelayMs = Number.isFinite(retryDelayMs)
    ? Math.max(0, Math.min(retryDelayMs, MEDIA_DOWNLOAD_MAX_RETRY_DELAY_MS))
    : 0

  for (let attempt = 1; attempt <= MEDIA_DOWNLOAD_ATTEMPTS; attempt += 1) {
    try {
      return await operation()
    } catch (err) {
      lastErr = err
      if (attempt >= MEDIA_DOWNLOAD_ATTEMPTS || !isTransientDownloadError(err)) {
        throw err
      }
      logger.warn(
        { err: String(err), attempt, nextAttempt: attempt + 1 },
        'Telegram media download transient failure; retrying'
      )
      if (baseDelayMs > 0) {
        const delayMs = Math.min(baseDelayMs * 2 ** (attempt - 1), MEDIA_DOWNLOAD_MAX_RETRY_DELAY_MS)
        await sleep(delayMs)
      }
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr))
}

async function httpGetWithRetry(
  url: string,
  get: HttpGetter,
  retryDelayMs: number
): Promise<Buffer> {
  return withRetry(() => get(url), retryDelayMs)
}

function throwRetryableTelegramJsonError(fileInfo: TelegramFileInfo): void {
  if (fileInfo.ok !== false) return
  const code = fileInfo.error_code
  if (typeof code !== 'number') return
  if (code !== 429 && (code < 500 || code > 599)) return

  const err = new Error(
    `Telegram getFile returned retryable error ${code}: ${fileInfo.description ?? 'no description'}`
  ) as NodeJS.ErrnoException
  err.code = `HTTP_${code}`
  throw err
}

function parseTelegramFileInfo(body: Buffer): TelegramFileInfo {
  try {
    const parsed = JSON.parse(body.toString()) as unknown
    if (!parsed || typeof parsed !== 'object') {
      throw new Error('Telegram getFile response was not a JSON object')
    }
    return parsed as TelegramFileInfo
  } catch (err) {
    const parseErr = new Error(
      `Failed to parse Telegram getFile response: ${String(err)}`
    ) as NodeJS.ErrnoException
    parseErr.code = 'BAD_TELEGRAM_JSON'
    throw parseErr
  }
}

function isTransientDownloadError(err: unknown): boolean {
  const e = err as NodeJS.ErrnoException
  const code = typeof e?.code === 'string' ? e.code : ''
  if (
    [
      'ECONNRESET',
      'ETIMEDOUT',
      'EAI_AGAIN',
      'ENOTFOUND',
      'ECONNREFUSED',
      'EMPTY_RESPONSE',
      'BAD_TELEGRAM_JSON',
    ].includes(code)
  ) {
    return true
  }
  if (/^HTTP_5\d\d$/.test(code)) {
    return true
  }
  if (code === 'HTTP_429') {
    return true
  }
  // Other HTTP_4xx codes are permanent for Telegram media: bad file id,
  // unauthorized token, or inaccessible file. Do not retry them.
  const msg = err instanceof Error ? err.message : String(err)
  return /socket hang up|network socket disconnected|TLS|timeout|ECONNRESET|ETIMEDOUT/i.test(msg)
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolveSleep) => setTimeout(resolveSleep, ms))
}

export async function downloadMedia(
  fileId: string,
  originalFilename?: string,
  deps: DownloadMediaDeps = {}
): Promise<string> {
  const get = deps.httpGet ?? httpGet
  const writeFile = deps.writeFile ?? writeFileSync
  const now = deps.now ?? Date.now
  const retryDelayMs = deps.retryDelayMs ?? MEDIA_DOWNLOAD_RETRY_DELAY_MS
  // Get file path from Telegram
  const fileInfoUrl = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getFile?file_id=${encodeURIComponent(fileId)}`
  const fileInfo = await withRetry(async () => {
    const parsed = parseTelegramFileInfo(await get(fileInfoUrl))
    throwRetryableTelegramJsonError(parsed)
    return parsed
  }, retryDelayMs)

  if (fileInfo.ok !== true || !fileInfo.result?.file_path) {
    throw new Error(`Failed to get file info: ${JSON.stringify(fileInfo)}`)
  }

  const remotePath = fileInfo.result.file_path
  const downloadUrl = `https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/${remotePath}`
  const data = await httpGetWithRetry(downloadUrl, get, retryDelayMs)

  const ext = remotePath.includes('.') ? '.' + remotePath.split('.').pop() : ''
  const safeName = originalFilename
    ? sanitizeFilename(originalFilename)
    : `file${ext}`
  const localPath = resolve(UPLOADS_DIR, `${now()}_${safeName}`)

  writeFile(localPath, data)
  logger.info({ localPath, size: data.length }, 'Downloaded media')
  return localPath
}

export function buildPhotoMessage(localPath: string, caption?: string): string {
  const parts = [`[Photo attached at ${localPath}]`]
  parts.push('Analyze this image and respond to the user.')
  if (caption) parts.push(`Caption: ${caption}`)
  return parts.join('\n')
}

export function buildDocumentMessage(
  localPath: string,
  filename: string,
  caption?: string
): string {
  const parts = [`[Document attached: ${filename} at ${localPath}]`]
  parts.push('Read and process this document.')
  if (caption) parts.push(`Caption: ${caption}`)
  return parts.join('\n')
}

export function cleanupOldUploads(maxAgeMs = 24 * 60 * 60 * 1000): void {
  try {
    const files = readdirSync(UPLOADS_DIR)
    const now = Date.now()
    let cleaned = 0

    for (const file of files) {
      const filePath = resolve(UPLOADS_DIR, file)
      const stat = statSync(filePath)
      if (now - stat.mtimeMs > maxAgeMs) {
        unlinkSync(filePath)
        cleaned++
      }
    }

    if (cleaned > 0) {
      logger.info({ cleaned }, 'Cleaned up old uploads')
    }
  } catch {
    // uploads dir might not exist yet
  }
}
