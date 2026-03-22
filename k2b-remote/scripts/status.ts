import { execSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = resolve(__dirname, '..')

const GREEN = '\x1b[32m'
const RED = '\x1b[31m'
const YELLOW = '\x1b[33m'
const BOLD = '\x1b[1m'
const RESET = '\x1b[0m'

function ok(msg: string) { console.log(`  ${GREEN}\u2713${RESET} ${msg}`) }
function warn(msg: string) { console.log(`  ${YELLOW}\u26a0${RESET} ${msg}`) }
function fail(msg: string) { console.log(`  ${RED}\u2717${RESET} ${msg}`) }

function main(): void {
  console.log(`\n${BOLD}K2B Remote Status${RESET}\n`)

  // Node version
  const nodeVersion = process.version
  const major = parseInt(nodeVersion.slice(1).split('.')[0])
  if (major >= 20) {
    ok(`Node.js: ${nodeVersion}`)
  } else {
    fail(`Node.js: ${nodeVersion} (need v20+)`)
  }

  // Claude CLI
  try {
    const v = execSync('claude --version 2>/dev/null', { encoding: 'utf-8' }).trim()
    ok(`Claude CLI: ${v}`)
  } catch {
    fail('Claude CLI: not found')
  }

  // .env check
  const envPath = resolve(PROJECT_ROOT, '.env')
  if (existsSync(envPath)) {
    const env = readFileSync(envPath, 'utf-8')
    const lines = env.split('\n')
    const getVal = (key: string) => {
      const line = lines.find((l) => l.startsWith(key + '='))
      return line?.split('=').slice(1).join('=').trim() ?? ''
    }

    const token = getVal('TELEGRAM_BOT_TOKEN')
    if (token) {
      ok(`Bot token: ...${token.slice(-8)}`)
    } else {
      fail('Bot token: not set')
    }

    const chatId = getVal('ALLOWED_CHAT_ID')
    if (chatId) {
      ok(`Chat ID: ${chatId}`)
    } else {
      warn('Chat ID: not set (first-run mode -- anyone can use the bot)')
    }

    const groq = getVal('GROQ_API_KEY')
    if (groq) {
      ok(`Groq STT: configured`)
    } else {
      warn('Groq STT: not configured (voice notes disabled)')
    }
  } else {
    fail('.env file: missing')
  }

  // Database
  const dbPath = resolve(PROJECT_ROOT, 'store', 'k2b-remote.db')
  if (existsSync(dbPath)) {
    ok(`Database: ${dbPath}`)
  } else {
    warn('Database: not yet created (starts on first run)')
  }

  // PID / running check
  const pidPath = resolve(PROJECT_ROOT, 'store', 'k2b-remote.pid')
  if (existsSync(pidPath)) {
    const pid = readFileSync(pidPath, 'utf-8').trim()
    try {
      process.kill(parseInt(pid), 0)
      ok(`Process: running (PID ${pid})`)
    } catch {
      warn(`Process: stale PID file (PID ${pid} not running)`)
    }
  } else {
    warn('Process: not running')
  }

  // Service check
  try {
    const result = execSync('launchctl list com.k2b-remote.app 2>/dev/null', {
      encoding: 'utf-8',
    })
    if (result.includes('com.k2b-remote.app')) {
      ok('launchd service: installed')
    }
  } catch {
    warn('launchd service: not installed')
  }

  // Build check
  const distIndex = resolve(PROJECT_ROOT, 'dist', 'index.js')
  if (existsSync(distIndex)) {
    ok('Build: dist/index.js exists')
  } else {
    warn('Build: not yet built (run npm run build)')
  }

  console.log('')
}

main()
