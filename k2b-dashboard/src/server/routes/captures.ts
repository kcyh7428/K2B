import { Router } from 'express'
import { glob } from 'glob'
import { statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { paths } from '../lib/vault-paths.js'
import { readNote } from '../lib/parse-frontmatter.js'

const router = Router()

router.get('/today', async (_req, res) => {
  const cutoff = Date.now() - 24 * 60 * 60 * 1000
  const files = await glob('**/*.md', { cwd: paths.raw, ignore: '**/index.md', absolute: true })

  const items = files
    .map((fullPath) => {
      const stat = statSync(fullPath)
      if (stat.mtimeMs < cutoff) return null
      const note = readNote(fullPath)
      if (!note) return null
      const rel = relative(paths.raw, fullPath)
      const layer = rel.split('/')[0] ?? 'raw'
      return {
        filename: rel,
        layer,
        title: (note.data.title as string | undefined) ?? rel.split('/').pop()?.replace(/\.md$/, ''),
        type: (note.data.type as string | undefined) ?? 'capture',
        origin: (note.data.origin as string | undefined) ?? 'unknown',
        mtime: stat.mtimeMs,
        preview: note.content.slice(0, 160).trim(),
      }
    })
    .filter((x): x is NonNullable<typeof x> => x !== null)
    .sort((a, b) => b.mtime - a.mtime)
    .slice(0, 50)

  res.json({ items, count: items.length, since: cutoff })
})

export default router
