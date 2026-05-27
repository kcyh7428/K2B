import { describe, expect, it } from 'vitest'
import { downloadMedia } from './media.js'

describe('downloadMedia', () => {
  it('retries transient Telegram download failures before writing the file', async () => {
    const requestedUrls: string[] = []
    const writes: Array<{ path: string; data: Buffer }> = []
    let downloadAttempts = 0

    const localPath = await downloadMedia('telegram-file-id', undefined, {
      now: () => 1711987200000,
      retryDelayMs: 0,
      httpGet: async (url) => {
        requestedUrls.push(url)
        if (url.includes('/getFile?')) {
          return Buffer.from(
            JSON.stringify({ ok: true, result: { file_path: 'photos/file_42.jpg' } }),
            'utf8'
          )
        }
        downloadAttempts += 1
        if (downloadAttempts === 1) {
          const err = new Error('socket hang up') as NodeJS.ErrnoException
          err.code = 'ECONNRESET'
          throw err
        }
        return Buffer.from('image-bytes', 'utf8')
      },
      writeFile: (path, data) => {
        const bytes =
          typeof data === 'string'
            ? Buffer.from(data)
            : Buffer.from(data.buffer, data.byteOffset, data.byteLength)
        writes.push({ path: String(path), data: bytes })
      },
    })

    expect(downloadAttempts).toBe(2)
    expect(requestedUrls).toHaveLength(3)
    expect(localPath).toMatch(/1711987200000_file\.jpg$/)
    expect(writes).toHaveLength(1)
    expect(writes[0].data.toString('utf8')).toBe('image-bytes')
  })

  it('retries HTTP 429 and HTTP 5xx Telegram media responses', async () => {
    let downloadAttempts = 0

    const localPath = await downloadMedia('telegram-file-id', undefined, {
      now: () => 1711987200000,
      retryDelayMs: 0,
      httpGet: async (url) => {
        if (url.includes('/getFile?')) {
          return Buffer.from(
            JSON.stringify({ ok: true, result: { file_path: 'photos/file_42.jpg' } }),
            'utf8'
          )
        }
        downloadAttempts += 1
        if (downloadAttempts === 1) {
          const err = new Error('HTTP 429 from Telegram media endpoint') as NodeJS.ErrnoException
          err.code = 'HTTP_429'
          throw err
        }
        if (downloadAttempts === 2) {
          const err = new Error('HTTP 502 from Telegram media endpoint') as NodeJS.ErrnoException
          err.code = 'HTTP_502'
          throw err
        }
        return Buffer.from('image-bytes', 'utf8')
      },
      writeFile: () => undefined,
    })

    expect(downloadAttempts).toBe(3)
    expect(localPath).toMatch(/1711987200000_file\.jpg$/)
  })

  it('retries empty Telegram media responses', async () => {
    let downloadAttempts = 0

    const localPath = await downloadMedia('telegram-file-id', undefined, {
      now: () => 1711987200000,
      retryDelayMs: 0,
      httpGet: async (url) => {
        if (url.includes('/getFile?')) {
          return Buffer.from(
            JSON.stringify({ ok: true, result: { file_path: 'photos/file_42.jpg' } }),
            'utf8'
          )
        }
        downloadAttempts += 1
        if (downloadAttempts === 1) {
          const err = new Error('empty Telegram media response') as NodeJS.ErrnoException
          err.code = 'EMPTY_RESPONSE'
          throw err
        }
        return Buffer.from('image-bytes', 'utf8')
      },
      writeFile: () => undefined,
    })

    expect(downloadAttempts).toBe(2)
    expect(localPath).toMatch(/1711987200000_file\.jpg$/)
  })
})
