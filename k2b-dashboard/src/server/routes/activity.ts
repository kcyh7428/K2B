import { Router } from 'express'
import { execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { paths } from '../lib/vault-paths.js'
import { tailLines } from '../lib/parse-jsonl.js'

const router = Router()

router.get('/', (_req, res) => {
  const items: { ts: string; source: string; text: string }[] = []

  // Recent git commits (project)
  try {
    const gitLog = execSync(
      'git log -n 10 --pretty=format:"%aI|%h|%s"',
      { cwd: paths.project, encoding: 'utf-8' }
    )
    for (const line of gitLog.trim().split('\n')) {
      const [ts, hash, ...rest] = line.split('|')
      items.push({ ts, source: 'git', text: `${hash} ${rest.join('|')}` })
    }
  } catch {
    // git not available
  }

  // Recent skill usage
  if (existsSync(paths.skillUsageLog)) {
    const lines = tailLines(paths.skillUsageLog, 15)
    for (const line of lines) {
      const parts = line.split('\t')
      if (parts.length >= 2) {
        items.push({
          ts: parts[0],
          source: 'skill',
          text: `${parts[1]}: ${parts.slice(3).join(' ').slice(0, 100)}`,
        })
      }
    }
  }

  // Sort desc
  items.sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''))

  res.json({ items: items.slice(0, 30) })
})

export default router
