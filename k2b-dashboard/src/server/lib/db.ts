import Database from 'better-sqlite3'
import { config } from './config.js'

function openDb(): Database.Database | null {
  try {
    return new Database(config.dbPath, { readonly: true, fileMustExist: true })
  } catch {
    return null
  }
}

export interface Memory {
  id: number
  type: string
  content: string
  accessed_at: string
  created_at: string
}

export interface MemorySummary {
  total: number
  semantic: number
  episodic: number
  recent: Memory[]
}

export function getMemories(limit: number = 20): MemorySummary {
  const db = openDb()
  if (!db) return { total: 0, semantic: 0, episodic: 0, recent: [] }

  try {
    const total = (db.prepare('SELECT COUNT(*) as count FROM memories').get() as { count: number }).count
    const semantic = (db.prepare("SELECT COUNT(*) as count FROM memories WHERE type = 'semantic'").get() as { count: number }).count
    const episodic = (db.prepare("SELECT COUNT(*) as count FROM memories WHERE type = 'episodic'").get() as { count: number }).count
    const recent = db.prepare(
      "SELECT id, type, content, accessed_at, created_at FROM memories WHERE type = 'semantic' ORDER BY accessed_at DESC LIMIT ?"
    ).all(limit) as Memory[]

    return { total, semantic, episodic, recent }
  } catch {
    return { total: 0, semantic: 0, episodic: 0, recent: [] }
  } finally {
    db.close()
  }
}

export interface ScheduledTask {
  id: number
  prompt: string
  schedule: string
  next_run: string
  last_run: string | null
  last_result: string | null
  status: string
  type: string
}

export function getScheduledTasks(): ScheduledTask[] {
  const db = openDb()
  if (!db) return []

  try {
    return db.prepare(
      'SELECT id, prompt, schedule, next_run, last_run, last_result, status, type FROM scheduled_tasks'
    ).all() as ScheduledTask[]
  } catch {
    return []
  } finally {
    db.close()
  }
}

export function getSessionCount(): number {
  const db = openDb()
  if (!db) return 0

  try {
    const row = db.prepare('SELECT COUNT(*) as count FROM sessions').get() as { count: number }
    return row.count
  } catch {
    return 0
  } finally {
    db.close()
  }
}
