import { createHash } from 'node:crypto'
import Database from 'better-sqlite3'
import { resolve } from 'node:path'
import { mkdirSync } from 'node:fs'
import { STORE_DIR } from './config.js'
import { logger } from './logger.js'

let db: Database.Database

export function getDb(): Database.Database {
  if (!db) {
    throw new Error('Database not initialized. Call initDatabase() first.')
  }
  return db
}

export function initDatabase(): void {
  mkdirSync(STORE_DIR, { recursive: true })
  const dbPath = resolve(STORE_DIR, 'k2b-remote.db')
  db = new Database(dbPath)
  db.pragma('journal_mode = WAL')

  // Sessions table (v3: one session per chat, no scope)
  db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      chat_id TEXT PRIMARY KEY,
      session_id TEXT,
      updated_at INTEGER
    )
  `)

  // Migration: v1 (chat_id PK, no scope) or v2 (composite PK with scope) -> v3
  const sessionCols = db.prepare("PRAGMA table_info(sessions)").all() as Array<{ name: string }>
  if (sessionCols.some(c => c.name === 'scope')) {
    // v2 -> v3: drop the scope column, keep only the interactive rows. Stale
    // youtube-scope rows are dropped -- the new design (session-design-v3)
    // collapses both into one persistent conversational session per chat.
    db.exec('BEGIN TRANSACTION')
    try {
      db.exec(`
        CREATE TABLE sessions_v3 (
          chat_id TEXT PRIMARY KEY,
          session_id TEXT,
          updated_at INTEGER
        )
      `)
      db.exec(`
        INSERT INTO sessions_v3 (chat_id, session_id, updated_at)
        SELECT chat_id, session_id, updated_at FROM sessions WHERE scope = 'interactive'
      `)
      db.exec('DROP TABLE sessions')
      db.exec('ALTER TABLE sessions_v3 RENAME TO sessions')
      db.exec('COMMIT')
      logger.info('Migrated sessions: dropped scope column (v2 -> v3)')
    } catch (err) {
      db.exec('ROLLBACK')
      throw err
    }
  }

  // Full memory: semantic + episodic.
  // DEPRECATED post-Ship-1-Commit-4: accessed_at is inert. It was the sort key
  // for the legacy read-bumps-access path (touchMemory + buildMemoryContext).
  // That path is gone; accessed_at now equals created_at for every new row and
  // is not read by any live code. Kept in the schema so existing rows' history
  // survives until Ship 4 consolidation migration rewrites this table (drops
  // the column OR drops the whole table if the shelf fully supersedes it).
  // Do NOT add new read paths that depend on accessed_at -- use created_at.
  db.exec(`
    CREATE TABLE IF NOT EXISTS memories (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      chat_id TEXT NOT NULL,
      topic_key TEXT,
      content TEXT NOT NULL,
      sector TEXT NOT NULL CHECK(sector IN ('semantic','episodic')),
      salience REAL NOT NULL DEFAULT 1.0,
      created_at INTEGER NOT NULL,
      accessed_at INTEGER NOT NULL
    )
  `)

  // DEPRECATED: memories_fts virtual table is a tombstone post-Ship-1-Commit-4.
  // The Washing Machine semantic shelf + retrieve.py is the live read path;
  // memories_fts has no active reader and no active writer (triggers dropped
  // below). CREATE VIRTUAL TABLE IF NOT EXISTS stays so old databases keep
  // their historical FTS rows intact until Ship 4's consolidation migration
  // runs `DROP TABLE IF EXISTS memories_fts` after confirming no downstream
  // dependency. Do NOT rebuild triggers here -- any reader that wants FTS on
  // shelf data should query retrieve.py, not this table. Tracked against
  // feature_washing-machine-memory.md "Ship 4 (simpler)" section.
  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
      content,
      content='memories',
      content_rowid='id'
    )
  `)

  // Idempotent teardown of the old sync triggers. On fresh installs the DROPs
  // are no-ops; on existing DBs they peel off the triggers so new memory
  // inserts no longer index into memories_fts.
  db.exec('DROP TRIGGER IF EXISTS memories_ai')
  db.exec('DROP TRIGGER IF EXISTS memories_ad')
  db.exec('DROP TRIGGER IF EXISTS memories_au')

  // Scheduled tasks
  db.exec(`
    CREATE TABLE IF NOT EXISTS scheduled_tasks (
      id TEXT PRIMARY KEY,
      chat_id TEXT NOT NULL,
      prompt TEXT NOT NULL,
      schedule TEXT NOT NULL,
      next_run INTEGER NOT NULL,
      last_run INTEGER,
      last_result TEXT,
      status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused')),
      created_at INTEGER NOT NULL
    )
  `)
  db.exec(`
    CREATE INDEX IF NOT EXISTS idx_tasks_status_next ON scheduled_tasks(status, next_run)
  `)

  // Migration: add type column for one-time reminders
  const cols = db.prepare("PRAGMA table_info(scheduled_tasks)").all() as Array<{ name: string }>
  if (!cols.some(c => c.name === 'type')) {
    db.exec("ALTER TABLE scheduled_tasks ADD COLUMN type TEXT NOT NULL DEFAULT 'recurring'")
    logger.info('Migrated scheduled_tasks: added type column')
  }

  // Migration v3 -> v4: drop the youtube_agent_state table created by the
  // retired YouTube agent. The table held in-process loop state for the
  // Telegram-driven YouTube curation pipeline that was retired in Phase 4 of
  // feature_youtube-agent (replaced by /research videos via NotebookLM).
  // DROP IF EXISTS so this is safe on fresh installs and idempotent on rerun.
  db.exec('DROP TABLE IF EXISTS youtube_agent_state')

  // Migration: add source_hash column for canonical-memory dedup
  const memCols = db.prepare("PRAGMA table_info(memories)").all() as Array<{ name: string }>
  if (!memCols.some(c => c.name === 'source_hash')) {
    db.exec("ALTER TABLE memories ADD COLUMN source_hash TEXT")
    db.exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_hash ON memories(source_hash) WHERE source_hash IS NOT NULL")

    // Backfill existing rows so startup sync dedup works against migrated JSONL
    const rows = db.prepare('SELECT id, content, created_at FROM memories WHERE source_hash IS NULL').all() as Array<{ id: number; content: string; created_at: number }>
    const update = db.prepare('UPDATE memories SET source_hash = ? WHERE id = ?')
    const backfill = db.transaction(() => {
      for (const row of rows) {
        const ts = new Date(row.created_at).toISOString()
        const hash = createHash('sha256').update(row.content + ts + 'telegram').digest('hex').slice(0, 32)
        update.run(hash, row.id)
      }
    })
    backfill()
    logger.info(`Migrated memories: added source_hash column + backfilled ${rows.length} rows`)
  }

  // KV table for sync state
  db.exec(`
    CREATE TABLE IF NOT EXISTS kv (
      key TEXT PRIMARY KEY,
      value TEXT
    )
  `)

  logger.info('Database initialized')
}

// --- KV helpers ---

export function getKv(key: string): string | undefined {
  const row = getDb()
    .prepare('SELECT value FROM kv WHERE key = ?')
    .get(key) as { value: string } | undefined
  return row?.value
}

export function setKv(key: string, value: string): void {
  getDb()
    .prepare('INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?')
    .run(key, value, value)
}

// --- Session CRUD ---

export function getSession(chatId: string): string | undefined {
  const row = getDb()
    .prepare('SELECT session_id FROM sessions WHERE chat_id = ?')
    .get(chatId) as { session_id: string | null } | undefined
  return row?.session_id ?? undefined
}

export function setSession(chatId: string, sessionId: string): void {
  getDb()
    .prepare(
      'INSERT INTO sessions (chat_id, session_id, updated_at) VALUES (?, ?, ?) ON CONFLICT(chat_id) DO UPDATE SET session_id = ?, updated_at = ?'
    )
    .run(chatId, sessionId, Date.now(), sessionId, Date.now())
}

export function clearSession(chatId: string): void {
  getDb().prepare('DELETE FROM sessions WHERE chat_id = ?').run(chatId)
}

// --- Memory CRUD ---

export function insertMemory(
  chatId: string,
  content: string,
  sector: 'semantic' | 'episodic',
  topicKey?: string,
  salience = 1.0,
  sourceHash?: string
): boolean {
  const now = Date.now()
  if (sourceHash) {
    // Dedup: skip if this exact memory already exists
    const result = getDb()
      .prepare(
        'INSERT OR IGNORE INTO memories (chat_id, topic_key, content, sector, salience, created_at, accessed_at, source_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
      )
      .run(chatId, topicKey ?? null, content, sector, salience, now, now, sourceHash)
    return result.changes === 1
  } else {
    getDb()
      .prepare(
        'INSERT INTO memories (chat_id, topic_key, content, sector, salience, created_at, accessed_at) VALUES (?, ?, ?, ?, ?, ?, ?)'
      )
      .run(chatId, topicKey ?? null, content, sector, salience, now, now)
    return true
  }
}

export function decayAllMemories(): void {
  const oneDayAgo = Date.now() - 86400 * 1000
  getDb()
    .prepare('UPDATE memories SET salience = salience * 0.98 WHERE created_at < ?')
    .run(oneDayAgo)
  const deleted = getDb()
    .prepare('DELETE FROM memories WHERE salience < 0.1')
    .run()
  if (deleted.changes > 0) {
    logger.info(`Decayed and removed ${deleted.changes} stale memories`)
  }
}

export function getMemoryCount(chatId: string): number {
  const row = getDb()
    .prepare('SELECT COUNT(*) as count FROM memories WHERE chat_id = ?')
    .get(chatId) as { count: number }
  return row.count
}

export function getRecentMemoriesForDisplay(
  chatId: string,
  limit = 10
): Array<{ content: string; sector: string; salience: number; created_at: number }> {
  // Order by created_at (insertion time), not accessed_at. Ship 1 Commit 4
  // removed the read-bumps-access path (touchMemory was only called from the
  // legacy buildMemoryContext FTS join). Shelf reads never touch this table,
  // so accessed_at would freeze at insert time for every row and give
  // NULLS-LAST/tied ordering. created_at reflects the "recent conversation
  // turns" semantic the /memory command advertises and is always populated.
  return getDb()
    .prepare(
      `SELECT content, sector, salience, created_at
       FROM memories
       WHERE chat_id = ?
       ORDER BY created_at DESC
       LIMIT ?`
    )
    .all(chatId, limit) as Array<{
    content: string
    sector: string
    salience: number
    created_at: number
  }>
}

// --- Scheduled Tasks CRUD ---

export function createTask(
  id: string,
  chatId: string,
  prompt: string,
  schedule: string,
  nextRun: number,
  type: 'recurring' | 'one-time' = 'recurring'
): void {
  getDb()
    .prepare(
      'INSERT INTO scheduled_tasks (id, chat_id, prompt, schedule, next_run, status, created_at, type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
    )
    .run(id, chatId, prompt, schedule, nextRun, 'active', Date.now(), type)
}

export function getDueTasks(): Array<{
  id: string
  chat_id: string
  prompt: string
  schedule: string
  type: string
}> {
  const now = Date.now()
  return getDb()
    .prepare(
      `SELECT id, chat_id, prompt, schedule, type
       FROM scheduled_tasks
       WHERE status = 'active' AND next_run <= ?`
    )
    .all(now) as Array<{
    id: string
    chat_id: string
    prompt: string
    schedule: string
    type: string
  }>
}

export function updateTaskNextRun(id: string, nextRun: number): void {
  getDb()
    .prepare('UPDATE scheduled_tasks SET next_run = ? WHERE id = ?')
    .run(nextRun, id)
}

export function updateTaskAfterRun(
  id: string,
  nextRun: number,
  lastResult: string
): void {
  getDb()
    .prepare(
      'UPDATE scheduled_tasks SET last_run = ?, next_run = ?, last_result = ? WHERE id = ?'
    )
    .run(Date.now(), nextRun, lastResult, id)
}

export function deleteTask(id: string): boolean {
  const result = getDb()
    .prepare('DELETE FROM scheduled_tasks WHERE id = ?')
    .run(id)
  return result.changes > 0
}

export function pauseTask(id: string): boolean {
  const result = getDb()
    .prepare("UPDATE scheduled_tasks SET status = 'paused' WHERE id = ?")
    .run(id)
  return result.changes > 0
}

export function resumeTask(id: string, nextRun: number): boolean {
  const result = getDb()
    .prepare(
      "UPDATE scheduled_tasks SET status = 'active', next_run = ? WHERE id = ?"
    )
    .run(nextRun, id)
  return result.changes > 0
}

export function listAllTasks(): Array<{
  id: string
  chat_id: string
  prompt: string
  schedule: string
  next_run: number
  last_run: number | null
  status: string
  type: string
}> {
  return getDb()
    .prepare('SELECT id, chat_id, prompt, schedule, next_run, last_run, status, type FROM scheduled_tasks ORDER BY created_at')
    .all() as Array<{
    id: string
    chat_id: string
    prompt: string
    schedule: string
    next_run: number
    last_run: number | null
    status: string
    type: string
  }>
}

