import { Router } from 'express'
import multer from 'multer'
import { mkdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { paths } from '../lib/vault-paths.js'

const router = Router()

// Stage uploads under Assets/intake/{uuid}/<original-name>
const upload = multer({
  storage: multer.diskStorage({
    destination: (_req, _file, cb) => {
      const uuid = randomUUID()
      const dir = join(paths.intakeAssets, uuid)
      mkdirSync(dir, { recursive: true })
      cb(null, dir)
    },
    filename: (_req, file, cb) => {
      // Preserve original filename so transcribers see meaningful extensions
      cb(null, file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_'))
    },
  }),
  limits: { fileSize: 100 * 1024 * 1024 }, // 100MB cap
})

interface IntakePayload {
  type: 'url' | 'text' | 'audio' | 'fireflies' | 'feedback'
  payload?: string
  filePath?: string
  source?: string
  // For feedback type:
  feedbackType?: 'learn' | 'error' | 'request'
}

async function forwardToRemote(body: IntakePayload): Promise<{ status: string; result?: unknown; error?: string }> {
  const url = `${paths.remoteIntakeUrl}/intake`
  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!resp.ok) {
      return { status: 'error', error: `k2b-remote returned ${resp.status}` }
    }
    return (await resp.json()) as { status: string; result?: unknown }
  } catch (err) {
    return { status: 'error', error: (err as Error).message }
  }
}

// Generic JSON intake -- url/text/fireflies/feedback
router.post('/', async (req, res) => {
  const body = req.body as IntakePayload
  if (!body || !body.type) {
    return res.status(400).json({ status: 'error', error: 'missing type' })
  }
  if (body.type === 'audio') {
    return res
      .status(400)
      .json({ status: 'error', error: 'use multipart upload at /api/intake/audio for audio' })
  }
  const result = await forwardToRemote({ ...body, source: body.source ?? 'dashboard' })
  res.json(result)
})

// Multipart intake for audio uploads
router.post('/audio', upload.single('file'), async (req, res) => {
  const file = req.file
  if (!file) return res.status(400).json({ status: 'error', error: 'no file' })

  // Compute the path k2b-remote will see (relative to vault, since both run on same host)
  const filePath = file.path

  if (!existsSync(filePath)) {
    return res.status(500).json({ status: 'error', error: 'upload missing on disk' })
  }

  const result = await forwardToRemote({
    type: 'audio',
    payload: req.body.note ?? '',
    filePath,
    source: 'dashboard',
  })
  res.json(result)
})

export default router
