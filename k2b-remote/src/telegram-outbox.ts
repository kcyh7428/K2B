import { readdirSync, readFileSync, unlinkSync, statSync, existsSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { InputFile } from 'grammy'
import { TELEGRAM_OUTBOX_DIR } from './config.js'
import { logger } from './logger.js'

// Telegram file size limits
const PHOTO_MAX_BYTES = 10 * 1024 * 1024   // 10MB for sendPhoto
const FILE_MAX_BYTES  = 50 * 1024 * 1024   // 50MB for sendDocument

export interface OutboxManifest {
  type: 'photo' | 'audio' | 'video' | 'document'
  path: string
  caption?: string
}

/** Ensure the outbox directory exists. Call at startup. */
export function ensureOutboxDir(): void {
  mkdirSync(TELEGRAM_OUTBOX_DIR, { recursive: true })
}

/** Scan outbox for manifests created after the given timestamp. Returns manifests with their file paths for cleanup after sending. */
export function scanOutbox(afterMs: number): Array<{ manifest: OutboxManifest; manifestPath: string }> {
  const results: Array<{ manifest: OutboxManifest; manifestPath: string }> = []
  let files: string[]
  try {
    files = readdirSync(TELEGRAM_OUTBOX_DIR)
  } catch {
    return results
  }
  for (const f of files) {
    if (!f.endsWith('.json') || f.startsWith('.')) continue
    const full = resolve(TELEGRAM_OUTBOX_DIR, f)
    try {
      const stat = statSync(full)
      if (stat.mtimeMs <= afterMs) continue
      const manifest = JSON.parse(readFileSync(full, 'utf-8')) as OutboxManifest
      if (manifest.path && manifest.type) {
        results.push({ manifest, manifestPath: full })
      } else {
        unlinkSync(full)  // invalid manifest -- clean up
      }
    } catch (err) {
      logger.error({ err, file: f }, 'Failed to read outbox manifest')
      try { unlinkSync(full) } catch { /* ignore */ }
    }
  }
  return results
}

/** Clean up a consumed manifest file. Call after sendMedia succeeds. */
export function consumeManifest(manifestPath: string): void {
  try { unlinkSync(manifestPath) } catch { /* ignore */ }
}

// Minimal type for the grammy context API methods we need
interface TelegramApi {
  sendPhoto(chatId: number, photo: InputFile, options?: { caption?: string }): Promise<unknown>
  sendAudio(chatId: number, audio: InputFile, options?: { caption?: string }): Promise<unknown>
  sendVideo(chatId: number, video: InputFile, options?: { caption?: string }): Promise<unknown>
  sendDocument(chatId: number, document: InputFile, options?: { caption?: string }): Promise<unknown>
}

/** Send a single media item via Telegram Bot API. Returns true if sent successfully. */
export async function sendMedia(
  api: TelegramApi,
  chatId: number,
  manifest: OutboxManifest
): Promise<boolean> {
  try {
    if (!existsSync(manifest.path)) {
      logger.warn({ path: manifest.path }, 'Outbox file not found, skipping')
      return false
    }

    const size = statSync(manifest.path).size
    if (size > FILE_MAX_BYTES) {
      logger.warn({ path: manifest.path, size }, 'File exceeds 50MB Telegram limit, skipping')
      return false
    }

    const caption = manifest.caption || undefined
    const file = new InputFile(manifest.path)

    // Fall back to document if photo exceeds sendPhoto limit
    let effectiveType = manifest.type
    if (effectiveType === 'photo' && size > PHOTO_MAX_BYTES) {
      logger.info({ path: manifest.path, size }, 'Photo too large for sendPhoto, sending as document')
      effectiveType = 'document'
    }

    switch (effectiveType) {
      case 'photo':
        await api.sendPhoto(chatId, file, { caption })
        break
      case 'audio':
        await api.sendAudio(chatId, file, { caption })
        break
      case 'video':
        await api.sendVideo(chatId, file, { caption })
        break
      default:
        await api.sendDocument(chatId, file, { caption })
    }
    logger.info({ type: effectiveType, path: manifest.path }, 'Sent media via Telegram')
    return true
  } catch (err) {
    logger.error({ err, path: manifest.path, type: manifest.type }, 'Failed to send media via Telegram')
    return false
  }
}
