import { createHash } from 'node:crypto'
import { readFileSync, appendFileSync, readdirSync, createReadStream, mkdirSync, rmdirSync } from 'node:fs'
import { createInterface } from 'node:readline'
import { resolve } from 'node:path'
import { insertMemory, decayAllMemories } from './db.js'
import { K2B_VAULT_PATH, MEMORIES_DIR } from './config.js'
import { logger } from './logger.js'

const SEMANTIC_SIGNALS = /\b(my|i am|i'm|i prefer|remember|always|never|our|we have|my team)\b/i

// --- Preference profile cache ---

const PROFILE_PATH = resolve(K2B_VAULT_PATH, 'wiki', 'context', 'preference-profile.md')
const PROFILE_REFRESH_MS = 10 * 60 * 1000 // 10 minutes

let profileCache: { text: string; hash: string; loadedAt: number } = {
  text: '',
  hash: '',
  loadedAt: 0,
}

export function loadPreferenceProfile(): { text: string; hash: string } {
  const now = Date.now()
  if (profileCache.text && now - profileCache.loadedAt < PROFILE_REFRESH_MS) {
    return { text: profileCache.text, hash: profileCache.hash }
  }

  try {
    const raw = readFileSync(PROFILE_PATH, 'utf-8')
    const hash = createHash('sha256').update(raw).digest('hex').slice(0, 16)
    const text = `[Preference context]\n${raw}\n\n`
    profileCache = { text, hash, loadedAt: now }
    return { text, hash }
  } catch {
    profileCache = { text: '', hash: '', loadedAt: now }
    return { text: '', hash: '' }
  }
}

// --- Vault JSONL append ---

function acquireLock(chatId: string): boolean {
  const lockDir = resolve(MEMORIES_DIR, `.lock-${chatId}`)
  try {
    mkdirSync(lockDir)
    return true
  } catch {
    return false
  }
}

function acquireLockWithRetry(chatId: string, maxRetries = 3): boolean {
  for (let i = 0; i < maxRetries; i++) {
    if (acquireLock(chatId)) return true
    // Busy-wait briefly (10ms, 20ms, 40ms) -- contention is rare and short-lived
    const waitMs = 10 * Math.pow(2, i)
    const end = Date.now() + waitMs
    while (Date.now() < end) { /* spin */ }
  }
  return false
}

function releaseLock(chatId: string): void {
  const lockDir = resolve(MEMORIES_DIR, `.lock-${chatId}`)
  try {
    rmdirSync(lockDir)
  } catch {
    // already released
  }
}

export function appendMemoryToVault(
  chatId: string,
  content: string,
  sector: 'semantic' | 'episodic',
  ts: string,
  topicKey?: string,
  salience = 1.0
): void {
  if (!acquireLockWithRetry(chatId)) {
    logger.warn({ chatId }, 'Could not acquire vault memory lock after retries, skipping vault write')
    return
  }

  try {
    mkdirSync(MEMORIES_DIR, { recursive: true })
    const entry: Record<string, unknown> = {
      ts,
      sector,
      content,
      salience,
      source: 'telegram',
    }
    if (topicKey) entry.topic_key = topicKey
    const outPath = resolve(MEMORIES_DIR, `telegram-${chatId}.jsonl`)
    appendFileSync(outPath, JSON.stringify(entry) + '\n', 'utf-8')
    logger.debug({ chatId, sector }, 'Appended memory to vault JSONL')
  } catch (err) {
    logger.error({ err, chatId }, 'Failed to append memory to vault')
  } finally {
    releaseLock(chatId)
  }
}

export async function saveConversationTurn(
  chatId: string,
  userMsg: string,
  assistantMsg: string
): Promise<void> {
  // Skip short or command messages
  if (userMsg.length <= 20 || userMsg.startsWith('/')) return

  const sector = SEMANTIC_SIGNALS.test(userMsg) ? 'semantic' : 'episodic'
  const ts = new Date().toISOString()

  // Primary: write to vault JSONL (canonical store)
  appendMemoryToVault(chatId, userMsg, sector, ts)

  // Secondary: write-through to SQLite (live FTS queries)
  const userHash = computeSourceHash(userMsg, ts, 'telegram')
  try {
    insertMemory(chatId, userMsg, sector, undefined, 1.0, userHash)
  } catch (err) {
    logger.warn({ err, chatId }, 'SQLite write-through failed for user message (vault has it)')
  }

  // Save a condensed version of the assistant response if substantial
  if (assistantMsg && assistantMsg.length > 50) {
    const truncated =
      assistantMsg.length > 500
        ? assistantMsg.slice(0, 500) + '...'
        : assistantMsg

    appendMemoryToVault(chatId, truncated, 'episodic', ts)

    const assistHash = computeSourceHash(truncated, ts, 'telegram')
    try {
      insertMemory(chatId, truncated, 'episodic', undefined, 1.0, assistHash)
    } catch (err) {
      logger.warn({ err, chatId }, 'SQLite write-through failed for assistant message (vault has it)')
    }
  }

  logger.debug({ chatId, sector }, 'Saved conversation turn to memory')
}

export function runDecaySweep(): void {
  logger.info('Running memory decay sweep')
  decayAllMemories()
}

// --- Source hash helper ---

export function computeSourceHash(content: string, ts: string, source: string): string {
  return createHash('sha256').update(content + ts + source).digest('hex').slice(0, 32)
}

// --- Vault sync on startup ---

interface MemoryEvent {
  ts: string
  sector: 'semantic' | 'episodic'
  topic_key?: string
  content: string
  salience: number
  source: string
}

export async function syncMemoriesFromVault(): Promise<void> {
  let files: string[]
  try {
    files = readdirSync(MEMORIES_DIR)
  } catch {
    logger.info('No memories directory found, skipping vault sync')
    return
  }

  const jsonlFiles = files.filter(
    (f) => f.endsWith('.jsonl') && !f.includes('.sync-conflict')
  )

  if (jsonlFiles.length === 0) {
    logger.info('No JSONL memory files found in vault')
    return
  }

  let scanned = 0
  let imported = 0

  for (const filename of jsonlFiles) {
    // Extract chatId from filename: telegram-{chatId}.jsonl or use 'default'
    const telegramMatch = filename.match(/^telegram-(.+)\.jsonl$/)
    const chatId = telegramMatch?.[1] ?? 'default'

    const filePath = resolve(MEMORIES_DIR, filename)

    // Stream line-by-line for bounded memory usage on large files
    const rl = createInterface({
      input: createReadStream(filePath, { encoding: 'utf-8' }),
      crlfDelay: Infinity,
    })

    for await (const line of rl) {
      if (!line.trim()) continue
      scanned++

      let event: MemoryEvent
      try {
        event = JSON.parse(line) as MemoryEvent
      } catch {
        // Tolerate truncated trailing lines (incomplete Syncthing writes)
        continue
      }

      const sector = event.sector === 'semantic' ? 'semantic' : 'episodic'
      const source = event.source ?? 'unknown'
      const sourceHash = computeSourceHash(event.content, event.ts, source)

      const wasInserted = insertMemory(
        chatId,
        event.content,
        sector,
        event.topic_key,
        event.salience ?? 1.0,
        sourceHash
      )
      if (wasInserted) imported++
    }
  }

  logger.info(
    { files: jsonlFiles.length, scanned, imported },
    'Synced memories from vault'
  )
}
