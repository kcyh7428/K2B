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

  // Sessions table (v2: scoped by workflow)
  db.exec(`
    CREATE TABLE IF NOT EXISTS sessions (
      chat_id TEXT NOT NULL,
      scope TEXT NOT NULL DEFAULT 'interactive',
      session_id TEXT NOT NULL,
      updated_at INTEGER NOT NULL,
      PRIMARY KEY (chat_id, scope)
    )
  `)

  // Migration: if sessions has old single-column PK (no scope), rebuild
  const sessionCols = db.prepare("PRAGMA table_info(sessions)").all() as Array<{ name: string }>
  if (!sessionCols.some(c => c.name === 'scope')) {
    db.exec('BEGIN TRANSACTION')
    try {
      db.exec(`
        CREATE TABLE sessions_v2 (
          chat_id TEXT NOT NULL,
          scope TEXT NOT NULL DEFAULT 'interactive',
          session_id TEXT NOT NULL,
          updated_at INTEGER NOT NULL,
          PRIMARY KEY (chat_id, scope)
        )
      `)
      db.exec(`
        INSERT INTO sessions_v2 (chat_id, scope, session_id, updated_at)
        SELECT chat_id, 'interactive', session_id, updated_at FROM sessions
      `)
      db.exec('DROP TABLE sessions')
      db.exec('ALTER TABLE sessions_v2 RENAME TO sessions')
      db.exec('COMMIT')
      logger.info('Migrated sessions: added scope column')
    } catch (err) {
      db.exec('ROLLBACK')
      throw err
    }
  }

  // Full memory: semantic + episodic
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

  // FTS5 virtual table for full-text search
  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
      content,
      content='memories',
      content_rowid='id'
    )
  `)

  // Triggers to keep FTS in sync
  db.exec(`
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END
  `)
  db.exec(`
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
    END
  `)
  db.exec(`
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.id, old.content);
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END
  `)

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

  // YouTube agent workflow state (persists across restarts)
  db.exec(`
    CREATE TABLE IF NOT EXISTS youtube_agent_state (
      chat_id TEXT PRIMARY KEY,
      phase TEXT NOT NULL DEFAULT 'idle',
      pending_video_ids TEXT NOT NULL DEFAULT '[]',
      pending_candidates TEXT NOT NULL DEFAULT '{}',
      started_at TEXT NOT NULL DEFAULT '',
      stale_after INTEGER,
      last_cycle_at TEXT,
      cycles_today INTEGER NOT NULL DEFAULT 0,
      last_cycle_date TEXT
    )
  `)

  logger.info('Database initialized')
}

// --- Session CRUD ---

export function getSession(chatId: string, scope: 'interactive' | 'youtube' = 'interactive'): string | undefined {
  const row = getDb()
    .prepare('SELECT session_id FROM sessions WHERE chat_id = ? AND scope = ?')
    .get(chatId, scope) as { session_id: string } | undefined
  return row?.session_id
}

export function setSession(chatId: string, sessionId: string, scope: 'interactive' | 'youtube' = 'interactive'): void {
  getDb()
    .prepare(
      'INSERT INTO sessions (chat_id, scope, session_id, updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(chat_id, scope) DO UPDATE SET session_id = ?, updated_at = ?'
    )
    .run(chatId, scope, sessionId, Date.now(), sessionId, Date.now())
}

export function clearSession(chatId: string, scope?: 'interactive' | 'youtube'): void {
  if (scope) {
    getDb().prepare('DELETE FROM sessions WHERE chat_id = ? AND scope = ?').run(chatId, scope)
  } else {
    getDb().prepare('DELETE FROM sessions WHERE chat_id = ?').run(chatId)
  }
}

// --- Memory CRUD ---

export function insertMemory(
  chatId: string,
  content: string,
  sector: 'semantic' | 'episodic',
  topicKey?: string,
  salience = 1.0
): void {
  const now = Date.now()
  getDb()
    .prepare(
      'INSERT INTO memories (chat_id, topic_key, content, sector, salience, created_at, accessed_at) VALUES (?, ?, ?, ?, ?, ?, ?)'
    )
    .run(chatId, topicKey ?? null, content, sector, salience, now, now)
}

export function searchMemoriesFts(
  chatId: string,
  query: string,
  limit = 3
): Array<{ id: number; content: string; sector: string; salience: number }> {
  // Sanitize query for FTS5
  const sanitized = query.replace(/[^a-zA-Z0-9\s]/g, '').trim()
  if (!sanitized) return []

  const terms = sanitized
    .split(/\s+/)
    .map((t) => t + '*')
    .join(' ')

  try {
    return getDb()
      .prepare(
        `SELECT m.id, m.content, m.sector, m.salience
         FROM memories m
         JOIN memories_fts f ON m.id = f.rowid
         WHERE memories_fts MATCH ? AND m.chat_id = ?
         ORDER BY rank
         LIMIT ?`
      )
      .all(terms, chatId, limit) as Array<{
      id: number
      content: string
      sector: string
      salience: number
    }>
  } catch {
    return []
  }
}

export function getRecentMemories(
  chatId: string,
  limit = 5
): Array<{ id: number; content: string; sector: string; salience: number }> {
  return getDb()
    .prepare(
      `SELECT id, content, sector, salience
       FROM memories
       WHERE chat_id = ?
       ORDER BY accessed_at DESC
       LIMIT ?`
    )
    .all(chatId, limit) as Array<{
    id: number
    content: string
    sector: string
    salience: number
  }>
}

export function touchMemory(id: number): void {
  getDb()
    .prepare(
      'UPDATE memories SET accessed_at = ?, salience = MIN(salience + 0.1, 5.0) WHERE id = ?'
    )
    .run(Date.now(), id)
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
  return getDb()
    .prepare(
      `SELECT content, sector, salience, created_at
       FROM memories
       WHERE chat_id = ?
       ORDER BY accessed_at DESC
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

// --- YouTube Agent State CRUD ---

export interface YouTubeAgentStateRow {
  phase: string
  pending_video_ids: string
  pending_candidates: string
  started_at: string
  stale_after: number | null
  last_cycle_at: string | null
  cycles_today: number
  last_cycle_date: string | null
}

export function getYouTubeAgentState(chatId: string): YouTubeAgentStateRow | undefined {
  return getDb()
    .prepare('SELECT phase, pending_video_ids, pending_candidates, started_at, stale_after, last_cycle_at, cycles_today, last_cycle_date FROM youtube_agent_state WHERE chat_id = ?')
    .get(chatId) as YouTubeAgentStateRow | undefined
}

export function upsertYouTubeAgentState(chatId: string, updates: Partial<YouTubeAgentStateRow>): void {
  const existing = getYouTubeAgentState(chatId)
  if (!existing) {
    getDb().prepare(
      `INSERT INTO youtube_agent_state (chat_id, phase, pending_video_ids, pending_candidates, started_at, stale_after, last_cycle_at, cycles_today, last_cycle_date)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
    ).run(
      chatId,
      updates.phase ?? 'idle',
      updates.pending_video_ids ?? '[]',
      updates.pending_candidates ?? '{}',
      updates.started_at ?? '',
      updates.stale_after ?? null,
      updates.last_cycle_at ?? null,
      updates.cycles_today ?? 0,
      updates.last_cycle_date ?? null
    )
  } else {
    const fields: string[] = []
    const values: unknown[] = []
    for (const [key, val] of Object.entries(updates)) {
      fields.push(`${key} = ?`)
      values.push(val)
    }
    if (fields.length > 0) {
      values.push(chatId)
      getDb().prepare(`UPDATE youtube_agent_state SET ${fields.join(', ')} WHERE chat_id = ?`).run(...values)
    }
  }
}

export function resetYouTubeAgentState(chatId: string): void {
  getDb().prepare(
    `INSERT INTO youtube_agent_state (chat_id, phase, pending_video_ids, pending_candidates, started_at, stale_after)
     VALUES (?, 'idle', '[]', '{}', '', NULL)
     ON CONFLICT(chat_id) DO UPDATE SET phase = 'idle', pending_video_ids = '[]', pending_candidates = '{}', started_at = '', stale_after = NULL`
  ).run(chatId)
}
