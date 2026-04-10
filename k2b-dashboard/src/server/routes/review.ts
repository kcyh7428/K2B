import { Router } from 'express'
import { existsSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { paths } from '../lib/vault-paths.js'
import { readNote } from '../lib/parse-frontmatter.js'

const router = Router()

router.get('/', (_req, res) => {
  if (!existsSync(paths.review)) return res.json({ items: [] })

  const items = readdirSync(paths.review)
    .filter((f) => f.endsWith('.md') && !f.startsWith('index'))
    .map((f) => {
      const fullPath = join(paths.review, f)
      const note = readNote(fullPath)
      if (!note) return null
      return {
        filename: f,
        title: (note.data.title as string | undefined) ?? f.replace(/\.md$/, ''),
        type: (note.data.type as string | undefined) ?? 'unknown',
        origin: (note.data.origin as string | undefined) ?? 'unknown',
        reviewAction: (note.data['review-action'] as string | undefined) ?? '',
        reviewNotes: (note.data['review-notes'] as string | undefined) ?? '',
        date: (note.data.date as string | undefined) ?? '',
        mtime: statSync(fullPath).mtimeMs,
        preview: note.content.slice(0, 200).trim(),
      }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((a, b) => b.mtime - a.mtime)

  res.json({ items, count: items.length })
})

export default router
