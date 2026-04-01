import { Router } from 'express'
import { statSync } from 'fs'
import { resolve } from 'path'
import { glob } from 'glob'
import { config } from '../lib/config.js'

const router = Router()

interface DayCount {
  date: string
  count: number
}

let cachedGrowth: DayCount[] = []
let cacheTime = 0
const CACHE_TTL = 5 * 60 * 1000 // 5 minutes

async function computeGrowth(): Promise<DayCount[]> {
  const now = Date.now()
  if (cachedGrowth.length > 0 && now - cacheTime < CACHE_TTL) {
    return cachedGrowth
  }

  const thirtyDaysAgo = new Date()
  thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30)

  const allFiles = await glob('**/*.md', { cwd: config.vaultPath })
  const dayCounts: Record<string, number> = {}

  // Initialize all 30 days
  for (let i = 0; i < 30; i++) {
    const d = new Date()
    d.setDate(d.getDate() - i)
    const key = d.toISOString().slice(0, 10)
    dayCounts[key] = 0
  }

  for (const file of allFiles) {
    try {
      const fullPath = resolve(config.vaultPath, file)
      const stat = statSync(fullPath)
      const created = stat.birthtime
      if (created >= thirtyDaysAgo) {
        const key = created.toISOString().slice(0, 10)
        if (key in dayCounts) {
          dayCounts[key]++
        }
      }
    } catch {
      // skip unreadable files
    }
  }

  const result = Object.entries(dayCounts)
    .map(([date, count]) => ({ date, count }))
    .sort((a, b) => a.date.localeCompare(b.date))

  cachedGrowth = result
  cacheTime = now
  return result
}

router.get('/', async (_req, res) => {
  try {
    const growth = await computeGrowth()
    res.json({ growth })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed to compute vault growth' })
  }
})

export { router as vaultGrowthRouter }
