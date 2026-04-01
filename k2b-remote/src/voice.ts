import { readFileSync, renameSync } from 'node:fs'
import { request } from 'node:https'
import { basename, extname, dirname, resolve } from 'node:path'
import { HttpsProxyAgent } from 'https-proxy-agent'
import { GROQ_API_KEY, HTTP_PROXY } from './config.js'
import { logger } from './logger.js'

const proxyAgent = HTTP_PROXY ? new HttpsProxyAgent(HTTP_PROXY) : undefined

export function voiceCapabilities(): { stt: boolean; tts: boolean } {
  return {
    stt: !!GROQ_API_KEY,
    tts: false,
  }
}

export async function transcribeAudio(filePath: string): Promise<string> {
  if (!GROQ_API_KEY) {
    throw new Error('GROQ_API_KEY not configured')
  }

  // Rename .oga to .ogg (Groq requirement -- same format, different extension)
  let actualPath = filePath
  if (extname(filePath) === '.oga') {
    actualPath = resolve(dirname(filePath), basename(filePath, '.oga') + '.ogg')
    renameSync(filePath, actualPath)
  }

  const fileBuffer = readFileSync(actualPath)
  const filename = basename(actualPath)
  const boundary = '----FormBoundary' + Date.now().toString(36)

  // Build multipart/form-data manually
  const parts: Buffer[] = []

  // File part
  parts.push(
    Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${filename}"\r\nContent-Type: audio/ogg\r\n\r\n`
    )
  )
  parts.push(fileBuffer)
  parts.push(Buffer.from('\r\n'))

  // Model part
  parts.push(
    Buffer.from(
      `--${boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3\r\n`
    )
  )

  // Close boundary
  parts.push(Buffer.from(`--${boundary}--\r\n`))

  const body = Buffer.concat(parts)

  return new Promise((resolvePromise, reject) => {
    const req = request(
      {
        hostname: 'api.groq.com',
        path: '/openai/v1/audio/transcriptions',
        method: 'POST',
        headers: {
          Authorization: `Bearer ${GROQ_API_KEY}`,
          'Content-Type': `multipart/form-data; boundary=${boundary}`,
          'Content-Length': body.length,
        },
        agent: proxyAgent,
      },
      (res) => {
        const chunks: Buffer[] = []
        res.on('data', (chunk: Buffer) => chunks.push(chunk))
        res.on('end', () => {
          const raw = Buffer.concat(chunks).toString()
          try {
            const json = JSON.parse(raw)
            if (json.text) {
              logger.info({ chars: json.text.length }, 'Transcribed voice note')
              resolvePromise(json.text)
            } else {
              reject(new Error(`Groq response missing text: ${raw}`))
            }
          } catch {
            reject(new Error(`Failed to parse Groq response: ${raw}`))
          }
        })
      }
    )

    req.on('error', reject)
    req.write(body)
    req.end()
  })
}
