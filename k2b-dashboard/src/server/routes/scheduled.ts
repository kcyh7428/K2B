import { Router } from 'express'
import { existsSync } from 'node:fs'
import Database from 'better-sqlite3'
import { paths } from '../lib/vault-paths.js'

const router = Router()

interface ScheduledRow {
  id: string
  prompt: string
  schedule: string
  next_run: number
  last_run: number | null
  status: string
  type: string
}

router.get('/', (_req, res) => {
  if (!existsSync(paths.remoteDb)) {
    return res.json({ items: [], available: false })
  }

  try {
    const db = new Database(paths.remoteDb, { readonly: true, fileMustExist: true })
    const rows = db
      .prepare(
        `SELECT id, prompt, schedule, next_run, last_run, status, type
         FROM scheduled_tasks
         WHERE status = 'active'
         ORDER BY next_run ASC LIMIT 5`
      )
      .all() as ScheduledRow[]
    db.close()

    const items = rows.map((r) => ({
      id: r.id,
      prompt: r.prompt.slice(0, 200),
      schedule: r.schedule,
      type: r.type,
      nextRun: r.next_run,
      lastRun: r.last_run,
      enabled: r.status === 'active',
    }))

    res.json({ items, available: true })
  } catch (err) {
    res.json({ items: [], available: false, error: (err as Error).message })
  }
})

export default router
