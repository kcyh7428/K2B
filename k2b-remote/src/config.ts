import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { readEnvFile } from './env.js'

const __dirname = dirname(fileURLToPath(import.meta.url))

const env = readEnvFile()

export const PROJECT_ROOT = resolve(__dirname, '..')
export const STORE_DIR = resolve(PROJECT_ROOT, 'store')
export const UPLOADS_DIR = resolve(PROJECT_ROOT, 'workspace', 'uploads')
export const TELEGRAM_OUTBOX_DIR = resolve(PROJECT_ROOT, 'workspace', 'telegram-outbox')

// K2B paths (relative: k2b-remote sits inside K2B/)
export const K2B_PROJECT_ROOT = env['CLAUDE_PROJECT_ROOT'] ?? resolve(__dirname, '../..')

// Vault path precedence (per Codex Tier 3 round 2 review): the code-side
// name `K2B_VAULT_PATH` should match the env-var name operators set. Honor
// (in order): process.env.K2B_VAULT_PATH, .env K2B_VAULT_PATH, process.env
// VAULT_PATH (legacy), .env VAULT_PATH (legacy), default sibling K2B-Vault.
// The legacy VAULT_PATH lookups keep existing .env files working without
// changes; new deployments should set K2B_VAULT_PATH.
export const K2B_VAULT_PATH =
  process.env['K2B_VAULT_PATH'] ??
  env['K2B_VAULT_PATH'] ??
  process.env['VAULT_PATH'] ??
  env['VAULT_PATH'] ??
  resolve(K2B_PROJECT_ROOT, '..', 'K2B-Vault')

// Telegram
export const TELEGRAM_BOT_TOKEN = env['TELEGRAM_BOT_TOKEN'] ?? ''
export const ALLOWED_CHAT_ID = env['ALLOWED_CHAT_ID'] ?? ''

const SILENT_CHAT_IDS_RAW = (env['SILENT_CHAT_IDS'] ?? '')
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
const VALID_CHAT_ID_RE = /^-?\d+$/
const SILENT_CHAT_IDS_INVALID = SILENT_CHAT_IDS_RAW.filter((s) => !VALID_CHAT_ID_RE.test(s))
if (SILENT_CHAT_IDS_INVALID.length > 0) {
  console.warn(
    `[config] SILENT_CHAT_IDS contains ${SILENT_CHAT_IDS_INVALID.length} invalid entr${
      SILENT_CHAT_IDS_INVALID.length === 1 ? 'y' : 'ies'
    } (must match /^-?\\d+$/, will be ignored): ${JSON.stringify(SILENT_CHAT_IDS_INVALID)}`
  )
}
export const SILENT_CHAT_IDS: string[] = SILENT_CHAT_IDS_RAW.filter((s) =>
  VALID_CHAT_ID_RE.test(s)
)

// Voice - Groq
export const GROQ_API_KEY = env['GROQ_API_KEY'] ?? ''

// Proxy (for System Proxy mode on Mac Mini -- check .env then process.env)
export const HTTP_PROXY = env['HTTP_PROXY'] || env['HTTPS_PROXY'] || process.env.HTTP_PROXY || process.env.HTTPS_PROXY || ''

// Canonical memory
export const MEMORIES_DIR = resolve(K2B_VAULT_PATH, 'wiki', 'context', 'memories')

// Limits
export const MAX_MESSAGE_LENGTH = 4096
export const TYPING_REFRESH_MS = 4000
