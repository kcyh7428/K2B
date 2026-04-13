import { writeFileSync, readFileSync, unlinkSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { hostname } from 'node:os'
import { STORE_DIR, TELEGRAM_BOT_TOKEN, ALLOWED_CHAT_ID } from './config.js'
import { initDatabase } from './db.js'
import { runDecaySweep } from './memory.js'
import { cleanupOldUploads } from './media.js'
import { createBot, sendTelegramMessage } from './bot.js'
import { initScheduler, stopScheduler } from './scheduler.js'
import { startHeartbeat, stopHeartbeat } from './health.js'
import { startIntakeServer } from './http-server.js'
import { startIntakeWatcher } from './intake-watcher.js'
import { ensureOutboxDir } from './telegram-outbox.js'
import { logger } from './logger.js'

const PID_FILE = resolve(STORE_DIR, 'k2b-remote.pid')

function acquireLock(): void {
  mkdirSync(STORE_DIR, { recursive: true })

  try {
    const existingPid = readFileSync(PID_FILE, 'utf-8').trim()
    const pid = parseInt(existingPid, 10)
    if (pid) {
      try {
        process.kill(pid, 0) // check if alive
        logger.warn({ pid }, 'Killing existing instance')
        process.kill(pid, 'SIGTERM')
      } catch {
        // process doesn't exist, stale PID file
      }
    }
  } catch {
    // no PID file exists
  }

  writeFileSync(PID_FILE, String(process.pid))
}

function releaseLock(): void {
  try {
    unlinkSync(PID_FILE)
  } catch {
    // ignore
  }
}

async function main(): Promise<void> {
  console.log(`
  ╔═══════════════════════════════╗
  ║    K2B Remote                 ║
  ║    Telegram -> Claude Code    ║
  ╚═══════════════════════════════╝
  `)

  // Check token
  if (!TELEGRAM_BOT_TOKEN) {
    console.error('\n  TELEGRAM_BOT_TOKEN not set.')
    console.error('  Run: npm run setup')
    console.error('  Or copy .env.example to .env and fill in your token.\n')
    process.exit(1)
  }

  // Acquire lock
  acquireLock()

  // Initialize database
  initDatabase()

  // Run memory decay sweep + schedule daily
  runDecaySweep()
  setInterval(runDecaySweep, 24 * 60 * 60 * 1000)

  // Cleanup old uploads
  cleanupOldUploads()

  // Ensure Telegram outbox directory exists
  ensureOutboxDir()

  // Create and start bot
  const bot = createBot()

  // Initialize scheduler with send function
  initScheduler(sendTelegramMessage)

  // Start health heartbeat
  startHeartbeat()

  // Start HTTP intake server (for k2b-dashboard browser intake)
  startIntakeServer()

  // Start vault-drop intake watcher (scans Assets/intake for manifest.json drops)
  startIntakeWatcher()

  // Graceful shutdown
  const shutdown = async () => {
    logger.info('Shutting down...')
    stopHeartbeat()
    stopScheduler()
    bot.stop()
    releaseLock()
    process.exit(0)
  }

  process.on('SIGINT', shutdown)
  process.on('SIGTERM', shutdown)

  // Start the bot
  try {
    await bot.start({
      onStart: async (botInfo) => {
        logger.info({ username: botInfo.username }, 'K2B Remote is running')
        if (ALLOWED_CHAT_ID) {
          const startMsg = [
            '--- K2B Online ---',
            `Host: ${hostname()}`,
            `PID: ${process.pid}`,
            `Time: ${new Date().toLocaleString('en-HK', { timeZone: 'Asia/Hong_Kong' })}`,
            `Bot: @${botInfo.username}`,
          ].join('\n')
          try {
            await sendTelegramMessage(ALLOWED_CHAT_ID, startMsg)
          } catch (err) {
            logger.error({ err }, 'Failed to send startup notification')
          }
        }
      },
    })
  } catch (err) {
    logger.error({ err }, 'Failed to start bot')
    console.error('\nFailed to start. Check your TELEGRAM_BOT_TOKEN in .env')
    releaseLock()
    process.exit(1)
  }
}

main().catch((err) => {
  logger.error({ err }, 'Fatal error')
  releaseLock()
  process.exit(1)
})
