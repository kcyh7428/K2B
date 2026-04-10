import { Router } from 'express'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { paths } from '../lib/vault-paths.js'

const router = Router()

function countMarkdown(dir: string): number {
  if (!existsSync(dir)) return 0
  let n = 0
  const stack = [dir]
  while (stack.length) {
    const d = stack.pop()!
    let entries: string[] = []
    try {
      entries = readdirSync(d)
    } catch {
      continue
    }
    for (const e of entries) {
      if (e === 'index.md') continue
      const full = join(d, e)
      let st
      try {
        st = statSync(full)
      } catch {
        continue
      }
      if (st.isDirectory()) stack.push(full)
      else if (e.endsWith('.md')) n++
    }
  }
  return n
}

router.get('/flow', (_req, res) => {
  const layers = {
    raw: countMarkdown(paths.raw),
    wiki: countMarkdown(paths.wiki),
    review: countMarkdown(paths.review),
  }

  // 24h delta from wiki/log.md (best-effort: count entries from today)
  let logEntries24h = 0
  const lastLogEntries: string[] = []
  if (existsSync(paths.wikiLog)) {
    const log = readFileSync(paths.wikiLog, 'utf-8')
    const today = new Date().toISOString().slice(0, 10)
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10)
    const headerRe = /^## \[(\d{4}-\d{2}-\d{2})/gm
    let m: RegExpExecArray | null
    while ((m = headerRe.exec(log)) !== null) {
      if (m[1] === today || m[1] === yesterday) logEntries24h++
    }
    // Last 5 headers
    const allHeaders = [...log.matchAll(/^## \[.+?\] .+$/gm)].map((m) => m[0])
    lastLogEntries.push(...allHeaders.slice(-5).reverse())
  }

  res.json({
    layers,
    logEntries24h,
    lastLogEntries,
  })
})

export default router
