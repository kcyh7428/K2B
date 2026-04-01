import { writeFileSync, mkdirSync, readdirSync, statSync, unlinkSync } from 'node:fs'
import { resolve, basename } from 'node:path'
import { request as httpsRequest } from 'node:https'
import { HttpsProxyAgent } from 'https-proxy-agent'
import { UPLOADS_DIR, TELEGRAM_BOT_TOKEN, HTTP_PROXY } from './config.js'
import { logger } from './logger.js'

const proxyAgent = HTTP_PROXY ? new HttpsProxyAgent(HTTP_PROXY) : undefined

// Ensure uploads dir exists
mkdirSync(UPLOADS_DIR, { recursive: true })

function sanitizeFilename(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '-')
}

async function httpGet(url: string): Promise<Buffer> {
  return new Promise((resolvePromise, reject) => {
    const handler = (res: import('node:http').IncomingMessage) => {
      // Follow redirects
      if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        httpsRequest(res.headers.location, { agent: proxyAgent }, handler).on('error', reject).end()
        return
      }
      const chunks: Buffer[] = []
      res.on('data', (chunk: Buffer) => chunks.push(chunk))
      res.on('end', () => resolvePromise(Buffer.concat(chunks)))
    }
    httpsRequest(url, { agent: proxyAgent }, handler).on('error', reject).end()
  })
}

export async function downloadMedia(
  fileId: string,
  originalFilename?: string
): Promise<string> {
  // Get file path from Telegram
  const fileInfoUrl = `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getFile?file_id=${fileId}`
  const fileInfoBuf = await httpGet(fileInfoUrl)
  const fileInfo = JSON.parse(fileInfoBuf.toString())

  if (!fileInfo.ok || !fileInfo.result?.file_path) {
    throw new Error(`Failed to get file info: ${JSON.stringify(fileInfo)}`)
  }

  const remotePath = fileInfo.result.file_path
  const downloadUrl = `https://api.telegram.org/file/bot${TELEGRAM_BOT_TOKEN}/${remotePath}`
  const data = await httpGet(downloadUrl)

  const ext = remotePath.includes('.') ? '.' + remotePath.split('.').pop() : ''
  const safeName = originalFilename
    ? sanitizeFilename(originalFilename)
    : `file${ext}`
  const localPath = resolve(UPLOADS_DIR, `${Date.now()}_${safeName}`)

  writeFileSync(localPath, data)
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
