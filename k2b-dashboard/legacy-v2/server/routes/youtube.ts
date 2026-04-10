import { Router } from 'express'
import { readFile } from 'fs/promises'
import { resolve } from 'path'
import { config } from '../lib/config.js'
import { getQueuePipeline } from '../lib/youtube-data.js'

const router = Router()

interface Recommendation {
  video_id: string
  title: string
  channel: string
  duration: string
  status: string
  outcome: string | null
  rating: string | null
  recommended_date: string
  nudge_date: string
  pick_reason: string
  topics: string[]
  verdict_value?: string
}

interface DigestStats {
  totalRecs: number
  responseRate: number
  lastRun: string
}

async function getRecommendations(): Promise<{ stats: DigestStats; recommendations: Recommendation[] }> {
  const filePath = resolve(config.vaultPath, 'Notes/Context/youtube-recommended.jsonl')
  try {
    const raw = await readFile(filePath, 'utf-8')
    const lines = raw.trim().split('\n').filter(Boolean)
    const all: Recommendation[] = []
    for (const line of lines) {
      try {
        const d = JSON.parse(line)
        all.push({
          video_id: d.video_id || '',
          title: d.title || '',
          channel: d.channel || '',
          duration: d.duration || '',
          status: d.status || 'unknown',
          outcome: d.outcome || null,
          rating: d.rating || null,
          recommended_date: d.recommended_date || '',
          nudge_date: d.nudge_date || '',
          pick_reason: d.pick_reason || '',
          topics: d.topics || [],
          verdict_value: d.verdict_value,
        })
      } catch {
        // skip malformed lines
      }
    }

    // Compute stats
    const responded = all.filter(v => v.outcome || v.status === 'done' || v.status === 'promoted' || v.status === 'skipped')
    const responseRate = all.length > 0 ? Math.round((responded.length / all.length) * 100) : 0
    const lastDates = all.map(v => v.recommended_date).filter(Boolean).sort()
    const lastRun = lastDates.length > 0 ? lastDates[lastDates.length - 1] : ''

    // Sort newest first, limit to recent
    all.sort((a, b) => (b.recommended_date || '').localeCompare(a.recommended_date || ''))

    return {
      stats: { totalRecs: all.length, responseRate, lastRun },
      recommendations: all.slice(0, 10),
    }
  } catch {
    return {
      stats: { totalRecs: 0, responseRate: 0, lastRun: '' },
      recommendations: [],
    }
  }
}

router.get('/', async (_req, res) => {
  try {
    const { stats, recommendations } = await getRecommendations()
    const queueResult = await getQueuePipeline()

    // Split queue into pending screening and recently extracted
    const pending = queueResult.current
    const extracted = queueResult.recentlyProcessed.filter(
      v => !(v.notes || '').toLowerCase().includes('skip')
    )
    const skippedCount = queueResult.recentlyProcessed.filter(
      v => (v.notes || '').toLowerCase().includes('skip')
    ).length

    res.json({
      stats,
      recommendations,
      pending,
      extracted,
      skippedCount,
      totalProcessed: queueResult.totalProcessed,
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as youtubeRouter }
