import { Router } from 'express'
import { getWatchPipeline, getQueuePipeline } from '../lib/youtube-data.js'

const router = Router()

router.get('/', async (_req, res) => {
  try {
    const watchResult = await getWatchPipeline()
    const queueResult = await getQueuePipeline()

    res.json({
      watch: {
        pending: watchResult.pending,
        totalCount: watchResult.total,
      },
      queue: {
        current: queueResult.current,
        recentlyProcessed: queueResult.recentlyProcessed,
        totalProcessed: queueResult.totalProcessed,
      },
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as youtubeRouter }
