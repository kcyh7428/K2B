import { Router } from 'express'

const router = Router()

router.post('/', async (req, res) => {
  try {
    const { command } = req.body as { command?: string }
    if (!command || typeof command !== 'string') {
      return res.status(400).json({ error: 'Missing command' })
    }

    // For v1: return the command for clipboard copy
    // Future: relay to k2b-remote Telegram API or command queue
    res.json({
      status: 'copied',
      command: command.trim(),
      message: `Command ready: ${command.trim()}`,
    })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Command failed' })
  }
})

export { router as commandRouter }
