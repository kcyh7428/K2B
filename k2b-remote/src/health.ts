import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { STORE_DIR } from './config.js'
import { logger } from './logger.js'

const HEALTH_FILE = resolve(STORE_DIR, 'health.json')
const HEARTBEAT_INTERVAL = 5 * 60 * 1000 // 5 minutes

let heartbeatTimer: ReturnType<typeof setInterval>

export function startHeartbeat(): void {
  writeHeartbeat()
  heartbeatTimer = setInterval(writeHeartbeat, HEARTBEAT_INTERVAL)
  logger.info('Heartbeat started (every 5 min)')
}

export function stopHeartbeat(): void {
  if (heartbeatTimer) clearInterval(heartbeatTimer)
}

function writeHeartbeat(): void {
  const data = {
    timestamp: new Date().toISOString(),
    epoch: Date.now(),
    pid: process.pid,
    uptime: process.uptime(),
    memory: process.memoryUsage().rss,
  }
  try {
    writeFileSync(HEALTH_FILE, JSON.stringify(data, null, 2))
  } catch (err) {
    logger.error({ err }, 'Failed to write heartbeat')
  }
}
