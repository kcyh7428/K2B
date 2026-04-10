import { createServer, IncomingMessage, ServerResponse } from 'node:http'
import { handleIntake, IntakePayload } from './intake.js'
import { logger } from './logger.js'

// Minimal HTTP server for k2b-dashboard intake.
// No express dep -- the surface area is two routes; node:http keeps deploys clean.

const INTAKE_PORT = parseInt(process.env.K2B_REMOTE_INTAKE_PORT ?? '3300', 10)

function readJsonBody(req: IncomingMessage): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf-8')
        if (!raw) return resolve({})
        resolve(JSON.parse(raw))
      } catch (err) {
        reject(err)
      }
    })
    req.on('error', reject)
  })
}

function send(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  })
  res.end(JSON.stringify(body))
}

export function startIntakeServer(): void {
  const server = createServer(async (req, res) => {
    try {
      // CORS preflight
      if (req.method === 'OPTIONS') {
        res.writeHead(204, {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type',
        })
        return res.end()
      }

      // Health
      if (req.method === 'GET' && req.url === '/health') {
        return send(res, 200, { status: 'ok', ts: new Date().toISOString() })
      }

      // Intake
      if (req.method === 'POST' && req.url === '/intake') {
        const body = (await readJsonBody(req)) as IntakePayload
        const result = await handleIntake(body)
        return send(res, result.status === 'ok' ? 200 : 500, result)
      }

      send(res, 404, { status: 'error', error: 'not found' })
    } catch (err) {
      logger.error({ err }, 'HTTP server error')
      send(res, 500, { status: 'error', error: (err as Error).message })
    }
  })

  server.listen(INTAKE_PORT, '127.0.0.1', () => {
    logger.info({ port: INTAKE_PORT }, 'k2b-remote intake server listening on 127.0.0.1')
  })
}
