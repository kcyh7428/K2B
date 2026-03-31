import { Router } from 'express'
import { getObserverData } from '../lib/observer.js'
import { getRecentLearnings } from '../lib/learnings.js'

const router = Router()

router.get('/', async (_req, res) => {
  try {
    const [observerData, learnings] = await Promise.all([
      getObserverData(),
      getRecentLearnings(20),
    ])

    res.json({
      candidates: observerData.candidates,
      patterns: observerData.patterns,
      observer: {
        lastAnalysis: observerData.lastAnalysis,
        observationsAnalyzed: observerData.observationsAnalyzed,
        currentObservations: observerData.currentObservationCount,
        summary: observerData.summary,
      },
      learnings: learnings.map((l) => ({
        id: l.id,
        area: l.area,
        learning: l.learning,
        reinforced: l.reinforced,
        confidence: l.confidence,
        date: l.date,
      })),
    })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed to load intelligence data' })
  }
})

export { router as intelligenceRouter }
