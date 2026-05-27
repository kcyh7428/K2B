import { writeFileSync, mkdirSync, readdirSync, statSync, unlinkSync } from 'node:fs'
import { resolve, basename } from 'node:path'
import { request as httpsRequest } from 'node:https'
import { HttpsProxyAgent } from 'https-proxy-agent'
import { UPLOADS_DIR, TELEGRAM_BOT_TOKEN, HTTP_PROXY } from './config.js'
import { logger } from './logger.js'

const proxyAgent = HTTP_PROXY ? new HttpsProxyAgent(HTTP_PROXY) : undefined
const MEDIA_DOWNLOAD_ATTEMPTS = 3
const MEDIA_DOWNLOAD_RETRY_DELAY_MS = 500

// Ensure uploads dir exists
mkdirSync(UPLOADS_DIR, { recursive: true })

function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '-')
}

type HttpGetter = (url: string) => Promise<Buffer>

export interface DownloadMediaDeps {
  httpGet?: HttpGetter
  now?: () => number
  retryDelayMs?: number
  writeFile?: typeof writeFileSync
}

async function httpGet(url: string): Promise<Buffer> {
  return new Promise((resolvePromise, reject) => {
    const handler = (res: import('node:http').IncomingMessage) => {
      // Follow redirects
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        httpsRequest(res.headers.location, { agent: proxyAgent }, handler).on('error', reject).end()
        return
      }
      if (res.statusCode && (res.statusCode >= 500 || res.statusCode === 429)) {
        res.resume()
        const err = new Error(`HTTP ${res.statusCode} from Telegram media endpoint`) as NodeJS.ErrnoException
        err.code = `HTTP_${res.statusCode}`
        reject(err)
        return
      }
      const chunks: Buffer[] = []
      res.on('data', (chunk: Buffer) => chunks.push(chunk))
      res.on('end', () => {
        const body = Buffer.concat(chunks)
        if (body.length === 0) {
          const err = new Error('empty Telegram media response') as NodeJS.ErrnoException
          err.code = 'EMPTY_RESPONSE'
          reject(err)
          return
        }
        resolvePromise(body)
      })
    }
    httpsRequest(url, { agent: proxyAgent }, handler).on('error', reject).end()
  })
}

async function httpGetWithRetry(
  url: string,
  get: HttpGetter,
  retryDelayMs: number
): Promise<Buffer> {
  let lastErr: unknown
  for (let attempt = 1; attempt <= MEDIA_DOWNLOAD_ATTEMPTS; attempt += 1) {
    try {
      return await get(url)
    } catch (err) {
      lastErr = err
      if (attempt >= MEDIA_DOWNLOAD_ATTEMPTS || !isTransientDownloadError(err)) {
        throw err
      }
      logger.warn(
        { err: String(err), attempt, nextAttempt: attempt + 1 },
        'Telegram media download transient failure; retrying'
      )
      if (retryDelayMs > 0) {
        await sleep(retryDelayMs * attempt)
      }
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr))
}

function isTransientDownloadError(err: unknown): boolean {
  const e = err as NodeJS.ErrnoException
  const code = typeof e?.code === 'string' ? e.code : ''
  if (['ECONNRESET', 'ETIMEDOUT', 'EAI_AGAIN', 'ENOTFOUND', 'ECONNREFUSED', 'EMPTY_RESPONSE'].includes(code)) {
    return true
  }
  if (/^HTTP_5\d\d$/.test(code)) {
    return true
  }
  if (code === 'HTTP_429') {
    return true
  }
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
  const fileInfoUrl = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getFile?file_id=${fileId}`
  const fileInfoBuf = await httpGetWithRetry(fileInfoUrl, get, retryDelayMs)
  const fileInfo = JSON.parse(fileInfoBuf.toString())

  if (!fileInfo.ok || !fileInfo.result?.file_path) {
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
