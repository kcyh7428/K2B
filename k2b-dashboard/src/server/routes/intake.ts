import { Router, Request, Response, NextFunction, RequestHandler } from 'express'
import multer from 'multer'
import { mkdirSync, existsSync, writeFileSync, renameSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { randomUUID } from 'node:crypto'
import { paths } from '../lib/vault-paths.js'

const router = Router()

// Stage uploads under Assets/intake/{uuid}/<original-name>.
// multer.diskStorage creates the uuid dir BEFORE the request body is parsed,
// so `req.body.note` is available in the handler but not inside `destination`.
const upload = multer({
  storage: multer.diskStorage({
    destination: (req, _file, cb) => {
      const uuid = randomUUID()
      // Stash the uuid on the request so the handler can reach it
      ;(req as unknown as { _intakeUuid?: string })._intakeUuid = uuid
      const dir = join(paths.intakeAssets, uuid)
      mkdirSync(dir, { recursive: true })
      cb(null, dir)
    },
    filename: (_req, file, cb) => {
      cb(null, file.originalname.replace(/[^a-zA-Z0-9._-]/g, '_'))
    },
  }),
  limits: { fileSize: 100 * 1024 * 1024 }, // 100MB cap
})

type IntakeType = 'url' | 'text' | 'audio' | 'fireflies' | 'feedback'

const INTAKE_TYPES: ReadonlyArray<IntakeType> = ['url', 'text', 'audio', 'fireflies', 'feedback']
const FEEDBACK_TYPES = ['learn', 'error', 'request'] as const

function isIntakeType(value: unknown): value is IntakeType {
  return typeof value === 'string' && (INTAKE_TYPES as ReadonlyArray<string>).includes(value)
}

// Wrap a multer middleware so its errors become clean JSON instead of bubbling
// out of the handler chain. Without this, an oversize upload or rejected mime
// type produces HTML (or empty body) that the client's r.json() will choke on.
function runUpload(middleware: RequestHandler): RequestHandler {
  return (req: Request, res: Response, next: NextFunction) => {
    middleware(req, res, (err: unknown) => {
      if (!err) return next()
      const message =
        err instanceof multer.MulterError
          ? `upload rejected: ${err.code} (${err.message})`
          : err instanceof Error
            ? err.message
            : 'upload failed'
      res.status(400).json({ status: 'error', error: message })
    })
  }
}

interface Manifest {
  schemaVersion: 1
  uuid: string
  type: IntakeType
  source: string
  createdAt: string
  file?: string
  note?: string
  payload?: string
  feedbackType?: 'learn' | 'error' | 'request'
}

// Atomically write manifest.json: write to .tmp then rename.
// Rename is atomic on the same filesystem, so the watcher never sees a half-written file.
function writeManifest(uuidDir: string, manifest: Manifest): string {
  const tmp = join(uuidDir, 'manifest.json.tmp')
  const dest = join(uuidDir, 'manifest.json')
  writeFileSync(tmp, JSON.stringify(manifest, null, 2))
  renameSync(tmp, dest)
  return dest
}

function stageNonAudio(body: {
  type: Exclude<IntakeType, 'audio'>
  payload?: string
  source?: string
  feedbackType?: 'learn' | 'error' | 'request'
}): { uuid: string; manifestPath: string } {
  const uuid = randomUUID()
  const uuidDir = join(paths.intakeAssets, uuid)
  mkdirSync(uuidDir, { recursive: true })
  const manifest: Manifest = {
    schemaVersion: 1,
    uuid,
    type: body.type,
    source: body.source ?? 'dashboard',
    createdAt: new Date().toISOString(),
    payload: body.payload ?? '',
    feedbackType: body.feedbackType,
  }
  const manifestPath = writeManifest(uuidDir, manifest)
  return { uuid, manifestPath }
}

// Generic JSON intake: url / text / fireflies / feedback
router.post('/', (req, res) => {
  const body = req.body as {
    type?: IntakeType
    payload?: string
    source?: string
    feedbackType?: 'learn' | 'error' | 'request'
  }
  if (!body || !body.type) {
    return res.status(400).json({ status: 'error', error: 'missing type' })
  }
  if (!isIntakeType(body.type)) {
    return res
      .status(400)
      .json({ status: 'error', error: `unknown type: ${String(body.type)}` })
  }
  if (body.type === 'audio') {
    return res
      .status(400)
      .json({ status: 'error', error: 'use multipart upload at /api/intake/audio for audio' })
  }
  if (body.feedbackType !== undefined && !(FEEDBACK_TYPES as ReadonlyArray<string>).includes(body.feedbackType)) {
    return res
      .status(400)
      .json({ status: 'error', error: `unknown feedbackType: ${body.feedbackType}` })
  }
  try {
    const { uuid, manifestPath } = stageNonAudio({
      type: body.type,
      payload: body.payload,
      source: body.source,
      feedbackType: body.feedbackType,
    })
    res.json({ status: 'staged', uuid, manifestPath })
  } catch (err) {
    res.status(500).json({ status: 'error', error: (err as Error).message })
  }
})

// Multipart intake for audio uploads
router.post('/audio', runUpload(upload.single('file')), (req, res) => {
  const file = req.file
  if (!file) return res.status(400).json({ status: 'error', error: 'no file' })
  const uuid = (req as unknown as { _intakeUuid?: string })._intakeUuid
  if (!uuid) {
    return res.status(500).json({ status: 'error', error: 'missing uuid for upload' })
  }
  if (!existsSync(file.path)) {
    return res.status(500).json({ status: 'error', error: 'upload missing on disk' })
  }

  try {
    const manifest: Manifest = {
      schemaVersion: 1,
      uuid,
      type: 'audio',
      source: 'dashboard',
      createdAt: new Date().toISOString(),
      file: file.filename,
      note: typeof req.body?.note === 'string' ? req.body.note : '',
    }
    const uuidDir = join(paths.intakeAssets, uuid)
    const manifestPath = writeManifest(uuidDir, manifest)
    res.json({ status: 'staged', uuid, manifestPath })
  } catch (err) {
    res.status(500).json({ status: 'error', error: (err as Error).message })
  }
})

// Status endpoint: the UI polls this to report real outcomes instead of silently
// swallowing failures.
//
// Contract (authoritative signals, in priority order):
//   processed/<uuid>/ exists (directory)      -> { status: 'done' }
//   intake/<uuid>/.error present               -> { status: 'error', error }
//   intake/<uuid>/manifest.json present        -> { status: 'processing' }
//   otherwise                                  -> { status: 'pending-sync' }
//
// The .done sentinel is a best-effort metadata sidecar (it carries the echoed
// message for debugging), NOT the authoritative signal. The watcher's rename
// into processed/ IS the authoritative "done" marker. This prevents a silent
// writeSentinel failure from stranding the UI in 'processing' forever.
router.get('/status/:uuid', (req, res) => {
  const uuid = req.params.uuid
  if (!/^[a-zA-Z0-9-]{8,}$/.test(uuid)) {
    return res.status(400).json({ status: 'error', error: 'invalid uuid' })
  }

  const processedDir = join(paths.intakeProcessed, uuid)
  const intakeDir = join(paths.intakeAssets, uuid)

  // Done: dir exists in processed/. The rename is authoritative; read .done
  // only for the details payload if present.
  if (existsSync(processedDir)) {
    let doneBody: unknown = null
    const processedDone = join(processedDir, '.done')
    if (existsSync(processedDone)) {
      try {
        doneBody = JSON.parse(readFileSync(processedDone, 'utf-8'))
      } catch {
        // sentinel unreadable is still "done"
      }
    }
    return res.json({ status: 'done', details: doneBody })
  }

  // Error: watcher left the dir in place with a .error sentinel
  const errorSentinel = join(intakeDir, '.error')
  if (existsSync(errorSentinel)) {
    let errBody: unknown = null
    let errMessage = 'unknown error'
    try {
      errBody = JSON.parse(readFileSync(errorSentinel, 'utf-8'))
      if (errBody && typeof errBody === 'object' && 'error' in errBody) {
        errMessage = String((errBody as { error: unknown }).error)
      }
    } catch {
      try {
        errMessage = readFileSync(errorSentinel, 'utf-8')
      } catch {
        // fall through
      }
    }
    return res.json({ status: 'error', error: errMessage, details: errBody })
  }

  // Processing: manifest is present locally, watcher hasn't completed yet
  if (existsSync(join(intakeDir, 'manifest.json'))) {
    return res.json({ status: 'processing' })
  }

  // Nothing here: either still syncing to the Mac Mini, or uuid unknown
  return res.json({ status: 'pending-sync' })
})

export default router
