import { Router } from 'express'

const router = Router()

router.get('/', (_req, res) => {
  res.json({
    items: [],
    available: false,
    status: 'disabled',
    reason: 'Stage 1 has no dashboard-owned scheduler',
  })
})

export default router
