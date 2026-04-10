import { Router } from 'express'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import Database from 'better-sqlite3'
import { paths } from '../lib/vault-paths.js'
import { readNote } from '../lib/parse-frontmatter.js'

const router = Router()

// Now-card resolution. Top-down priority, first match wins.
router.get('/', (_req, res) => {
  // 1. review/ has items
  if (existsSync(paths.review)) {
    const reviewFiles = readdirSync(paths.review).filter(
      (f) => f.endsWith('.md') && !f.startsWith('index')
    )
    if (reviewFiles.length > 0) {
      const top = readNote(join(paths.review, reviewFiles[0]))
      return res.json({
        priority: 'review',
        title: `${reviewFiles.length} item${reviewFiles.length === 1 ? '' : 's'} need your judgment`,
        preview: top?.data?.title ?? top?.filename ?? reviewFiles[0],
        cta: { label: 'Open triage', target: 'review-queue' },
      })
    }
  }

  // 2. Observer has new candidates
  const observerNote = readNote(paths.observerCandidates)
  if (observerNote && observerNote.content.trim().length > 0) {
    const firstLine = observerNote.content
      .split('\n')
      .find((l) => l.trim().startsWith('-') || l.trim().startsWith('*'))
      ?.replace(/^[-*]\s*/, '')
      ?.slice(0, 120)
    if (firstLine) {
      return res.json({
        priority: 'observer',
        title: 'Observer noticed a pattern',
        preview: firstLine,
        cta: { label: 'Open Inspector', target: 'learning-inspector' },
      })
    }
  }

  // 3. Scheduled task firing in < 1h
  try {
    if (existsSync(paths.remoteDb)) {
      const db = new Database(paths.remoteDb, { readonly: true, fileMustExist: true })
      const row = db
        .prepare(
          `SELECT id, prompt, next_run FROM scheduled_tasks
           WHERE status = 'active' AND next_run > strftime('%s','now')*1000
             AND next_run < strftime('%s','now')*1000 + 3600000
           ORDER BY next_run ASC LIMIT 1`
        )
        .get() as { id: string; prompt: string; next_run: number } | undefined
      db.close()
      if (row) {
        const minutes = Math.round((row.next_run - Date.now()) / 60000)
        return res.json({
          priority: 'scheduled',
          title: `K2B is about to run a task in ${minutes}m`,
          preview: row.prompt.slice(0, 120),
          cta: { label: 'See schedule', target: 'scheduled-row' },
        })
      }
    }
  } catch {
    // db read failed -- silently fall through to "all clear"
  }

  // 4. Default: all clear
  res.json({
    priority: 'idle',
    title: 'All clear',
    preview: 'Drop something into the intake below.',
    cta: { label: 'Focus intake', target: 'intake-bar' },
  })
})

export default router
