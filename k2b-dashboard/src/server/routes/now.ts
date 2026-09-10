import { Router } from 'express'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
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

  // Observer and scheduler lanes are disabled in Stage 1. Historical files do
  // not become live status merely because they still exist in the vault.
  res.json({
    priority: 'idle',
    title: 'All clear',
    preview: 'Use Codex or stage an item in the manual intake below.',
    cta: { label: 'Focus intake', target: 'intake-bar' },
  })
})

export default router
