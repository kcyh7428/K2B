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
export const K2B_VAULT_PATH = env['VAULT_PATH'] ?? resolve(K2B_PROJECT_ROOT, '..', 'K2B-Vault')

// Telegram
export const TELEGRAM_BOT_TOKEN = env['TELEGRAM_BOT_TOKEN'] ?? ''
export const ALLOWED_CHAT_ID = env['ALLOWED_CHAT_ID'] ?? ''

// Voice - Groq
export const GROQ_API_KEY = env['GROQ_API_KEY'] ?? ''

// Proxy (for System Proxy mode on Mac Mini -- check .env then process.env)
export const HTTP_PROXY = env['HTTP_PROXY'] || env['HTTPS_PROXY'] || process.env.HTTP_PROXY || process.env.HTTPS_PROXY || ''

// Limits
export const MAX_MESSAGE_LENGTH = 4096
export const TYPING_REFRESH_MS = 4000
