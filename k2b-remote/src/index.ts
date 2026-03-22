import { writeFileSync, readFileSync, unlinkSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { STORE_DIR, TELEGRAM_BOT_TOKEN } from './config.js'
import { initDatabase } from './db.js'
import { runDecaySweep } from './memory.js'
import { cleanupOldUploads } from './media.js'
import { createBot, sendTelegramMessage } from './bot.js'
import { initScheduler, stopScheduler } from './scheduler.js'
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

  // Create and start bot
  const bot = createBot()

  // Initialize scheduler with send function
  initScheduler(sendTelegramMessage)

  // Graceful shutdown
  const shutdown = async () => {
    logger.info('Shutting down...')
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
      onStart: (botInfo) => {
        logger.info({ username: botInfo.username }, 'K2B Remote is running')
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
