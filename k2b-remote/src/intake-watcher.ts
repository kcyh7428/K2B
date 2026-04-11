import { watch, existsSync, mkdirSync, readdirSync, statSync, writeFileSync, readFileSync, renameSync, realpathSync } from 'node:fs'
import { resolve, join, basename, dirname } from 'node:path'
import { K2B_VAULT_PATH } from './config.js'
import { handleIntake, IntakePayload } from './intake.js'
import { logger } from './logger.js'

// Vault-drop intake watcher.
//
// Contract (see .claude/plans/stateful-leaping-newt.md):
// - Dashboard stages files under Assets/intake/<uuid>/ and writes manifest.json LAST.
// - Presence of manifest.json == ready to process.
// - Processed dirs move to Assets/intake/processed/<uuid>/ for idempotency.
// - On error, write .error sentinel inside the uuid dir, leave it for inspection.

const INTAKE_DIR = resolve(K2B_VAULT_PATH, 'Assets', 'intake')
const PROCESSED_DIR = resolve(INTAKE_DIR, 'processed')
const RECONCILE_INTERVAL_MS = 15_000   // sweep every 15s to catch missed fs events
const STABILITY_CHECK_MS = 2_000        // file size must be stable for 2s
const MAX_STABILITY_WAIT_MS = 120_000   // give up after 2 min

interface Manifest {
  uuid: string
  type: 'url' | 'text' | 'audio' | 'fireflies' | 'feedback'
  source?: string
  file?: string
  note?: string
  payload?: string
  feedbackType?: 'learn' | 'error' | 'request'
  createdAt?: string
  schemaVersion?: number
}

// uuids currently being processed -- prevents double-pickup between fs.watch and reconcile sweep.
const inFlight = new Set<string>()

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}

async function waitForStableSize(path: string): Promise<boolean> {
  const start = Date.now()
  let lastSize = -1
  let stableSince = 0
  while (Date.now() - start < MAX_STABILITY_WAIT_MS) {
    try {
      const s = statSync(path)
      if (s.size === lastSize && s.size > 0) {
        if (!stableSince) stableSince = Date.now()
        if (Date.now() - stableSince >= STABILITY_CHECK_MS) return true
      } else {
        lastSize = s.size
        stableSince = 0
      }
    } catch {
      // file not yet visible
    }
    await sleep(500)
  }
  return false
}

function parseManifest(manifestPath: string): Manifest | null {
  try {
    const raw = readFileSync(manifestPath, 'utf-8')
    const parsed = JSON.parse(raw) as unknown
    if (!parsed || typeof parsed !== 'object') return null
    const obj = parsed as Record<string, unknown>
    if (typeof obj.uuid !== 'string' || !obj.uuid) return null
    const validTypes = ['url', 'text', 'audio', 'fireflies', 'feedback']
    if (typeof obj.type !== 'string' || !validTypes.includes(obj.type)) return null
    // Optional fields -- reject non-string types, don't coerce
    if (obj.file !== undefined && typeof obj.file !== 'string') return null
    if (obj.note !== undefined && typeof obj.note !== 'string') return null
    if (obj.payload !== undefined && typeof obj.payload !== 'string') return null
    if (obj.source !== undefined && typeof obj.source !== 'string') return null
    if (obj.feedbackType !== undefined) {
      if (typeof obj.feedbackType !== 'string') return null
      if (!['learn', 'error', 'request'].includes(obj.feedbackType)) return null
    }
    return obj as unknown as Manifest
  } catch (err) {
    logger.warn({ err, manifestPath }, 'Intake: failed to parse manifest.json')
    return null
  }
}

// Resolve manifest.file against the uuid dir with containment check.
// Returns absolute path if safe, null otherwise.
function resolveFile(uuidDir: string, fileField: string): string | null {
  // Must be a pure basename, no separators, no dot-prefix
  const bn = basename(fileField)
  if (bn !== fileField) {
    logger.warn({ uuidDir, fileField }, 'Intake: manifest.file is not a basename, rejecting')
    return null
  }
  if (bn.startsWith('.') || bn.includes('/') || bn.includes('\\')) {
    return null
  }
  const candidate = join(uuidDir, bn)
  let realUuidDir: string
  let realCandidate: string
  try {
    realUuidDir = realpathSync(uuidDir)
    if (!existsSync(candidate)) return null
    realCandidate = realpathSync(candidate)
  } catch {
    return null
  }
  // Containment: candidate must live inside the uuid dir (after realpath)
  if (!realCandidate.startsWith(realUuidDir + '/') && realCandidate !== realUuidDir) {
    logger.warn({ realCandidate, realUuidDir }, 'Intake: resolved file escapes uuid dir')
    return null
  }
  // Must be a regular file -- reject symlinked dirs, sockets, FIFOs, etc.
  try {
    if (!statSync(realCandidate).isFile()) {
      logger.warn({ realCandidate }, 'Intake: resolved path is not a regular file')
      return null
    }
  } catch {
    return null
  }
  return realCandidate
}

function writeSentinel(dir: string, name: '.done' | '.error', body: string): void {
  try {
    writeFileSync(join(dir, name), body)
  } catch (err) {
    logger.error({ err, dir, name }, 'Intake: failed to write sentinel')
  }
}

// Move the uuid dir into processed/. Returns the destination dir on success,
// or null on failure (caller is responsible for turning failure into .error
// so the UI does not hang in 'processing').
function moveToProcessed(uuidDir: string, uuid: string): string | null {
  try {
    mkdirSync(PROCESSED_DIR, { recursive: true })
    let dest = join(PROCESSED_DIR, uuid)
    if (existsSync(dest)) {
      // Collision: append a timestamp suffix instead of clobbering
      const suffix = new Date().toISOString().replace(/[:.]/g, '-')
      dest = `${dest}.${suffix}`
    }
    renameSync(uuidDir, dest)
    return dest
  } catch (err) {
    logger.error({ err, uuidDir }, 'Intake: failed to move to processed')
    return null
  }
}

// Module-scope map so processOne can tear down the per-dir watcher after the
// uuid dir has been moved or errored. Without this, watchers leak forever.
const perDirWatchers = new Map<string, ReturnType<typeof watch>>()

function closePerDirWatcher(uuid: string): void {
  const w = perDirWatchers.get(uuid)
  if (!w) return
  try {
    w.close()
  } catch {
    // ignore
  }
  perDirWatchers.delete(uuid)
}

async function processOne(uuid: string): Promise<void> {
  if (inFlight.has(uuid)) return
  inFlight.add(uuid)

  const uuidDir = join(INTAKE_DIR, uuid)
  const manifestPath = join(uuidDir, 'manifest.json')
  const doneSentinel = join(uuidDir, '.done')
  const errorSentinel = join(uuidDir, '.error')

  try {
    // Idempotency: already processed in this location OR moved
    if (existsSync(doneSentinel)) return
    if (existsSync(errorSentinel)) return
    if (existsSync(join(PROCESSED_DIR, uuid))) return
    if (!existsSync(manifestPath)) return

    const manifest = parseManifest(manifestPath)
    if (!manifest) {
      writeSentinel(uuidDir, '.error', 'invalid manifest.json')
      return
    }
    if (manifest.uuid !== uuid) {
      writeSentinel(uuidDir, '.error', `manifest.uuid mismatch: ${manifest.uuid} != ${uuid}`)
      return
    }

    logger.info({ uuid, type: manifest.type, source: manifest.source }, 'Intake: picked up manifest')

    let filePath: string | undefined
    if (manifest.type === 'audio') {
      if (!manifest.file) {
        writeSentinel(uuidDir, '.error', 'audio manifest missing file field')
        return
      }
      const resolved = resolveFile(uuidDir, manifest.file)
      if (!resolved) {
        writeSentinel(uuidDir, '.error', `file not found or unsafe: ${manifest.file}`)
        return
      }
      // Wait for size to stabilize (Syncthing partial-write guard)
      const ok = await waitForStableSize(resolved)
      if (!ok) {
        writeSentinel(uuidDir, '.error', 'file did not stabilize within 2 minutes')
        return
      }
      filePath = resolved
    }

    // Build the payload for handleIntake.
    // For audio: note becomes the payload (voice-note context).
    // For text/url/fireflies/feedback: payload is the manifest.payload field.
    const body: IntakePayload = {
      type: manifest.type,
      source: manifest.source ?? 'dashboard',
      payload: manifest.type === 'audio' ? (manifest.note ?? '') : (manifest.payload ?? ''),
      filePath,
      feedbackType: manifest.feedbackType,
    }

    const result = await handleIntake(body)
    logger.info({ uuid, status: result.status }, 'Intake: handleIntake returned')

    if (result.status === 'ok') {
      // Close the per-dir watcher BEFORE the rename so fs.watch does not fire
      // on a directory that is about to disappear. The `finally` block will
      // close it again as a safety net, but that call is a no-op by then.
      closePerDirWatcher(uuid)
      const destDir = moveToProcessed(uuidDir, uuid)
      if (destDir) {
        // Best-effort metadata sidecar. The presence of processed/<uuid>/
        // is the authoritative "done" signal for the status endpoint; this
        // file just carries the echoed message for debugging.
        const doneBody = JSON.stringify(
          {
            status: 'ok',
            processedAt: new Date().toISOString(),
            echoedMessage: result.echoedMessage,
          },
          null,
          2,
        )
        writeSentinel(destDir, '.done', doneBody)
      } else {
        // Rename failed. Convert success into a visible error so the dashboard
        // UI does not hang in 'processing' forever. The dir still exists.
        const errBody = JSON.stringify(
          {
            status: 'error',
            processedAt: new Date().toISOString(),
            error: 'handleIntake succeeded but move-to-processed failed',
          },
          null,
          2,
        )
        if (existsSync(uuidDir)) writeSentinel(uuidDir, '.error', errBody)
      }
    } else {
      const errBody = JSON.stringify(
        { status: 'error', processedAt: new Date().toISOString(), error: result.error ?? 'unknown' },
        null,
        2,
      )
      writeSentinel(uuidDir, '.error', errBody)
    }
  } catch (err) {
    logger.error({ err, uuid }, 'Intake: processOne threw')
    try {
      if (existsSync(uuidDir)) {
        writeSentinel(uuidDir, '.error', `exception: ${(err as Error).message}`)
      }
    } catch {
      // swallow -- already logged
    }
  } finally {
    // closePerDirWatcher is idempotent and no-ops if the watcher was never
    // attached, so it's safe to call on every exit path including early
    // validation returns that never reached the success/error branches above.
    closePerDirWatcher(uuid)
    inFlight.delete(uuid)
  }
}

// Reconcile: scan INTAKE_DIR for uuid subdirs with manifest.json and no sentinel.
function reconcile(): void {
  if (!existsSync(INTAKE_DIR)) return
  let entries: string[]
  try {
    entries = readdirSync(INTAKE_DIR)
  } catch (err) {
    logger.error({ err, INTAKE_DIR }, 'Intake: reconcile readdir failed')
    return
  }
  for (const entry of entries) {
    // Skip the processed/ subfolder and anything that doesn't look like a uuid
    if (entry === 'processed') continue
    if (entry.startsWith('.')) continue
    const uuidDir = join(INTAKE_DIR, entry)
    let st
    try {
      st = statSync(uuidDir)
    } catch {
      continue
    }
    if (!st.isDirectory()) continue
    if (!existsSync(join(uuidDir, 'manifest.json'))) continue
    if (existsSync(join(uuidDir, '.done')) || existsSync(join(uuidDir, '.error'))) continue
    // Kick off (fire-and-forget; processOne guards against double-pickup)
    processOne(entry).catch((err) => logger.error({ err, uuid: entry }, 'Intake: processOne error'))
  }
}

export function startIntakeWatcher(): void {
  // Ensure the intake dir exists so fs.watch can attach
  mkdirSync(INTAKE_DIR, { recursive: true })
  mkdirSync(PROCESSED_DIR, { recursive: true })

  logger.info({ intakeDir: INTAKE_DIR }, 'intake watcher listening')

  // Initial sweep on boot
  reconcile()

  // fs.watch for fast notification on the parent dir.
  // We care about manifest.json inside any uuid subdir, but fs.watch(recursive:true)
  // is not reliable cross-platform. Instead, watch the parent INTAKE_DIR; when a new
  // child dir appears, open a per-dir watcher for it. processOne() is responsible
  // for closing the watcher when it's done with that uuid.
  try {
    watch(INTAKE_DIR, (_event, filename) => {
      if (!filename) return
      const name = filename.toString()
      if (name === 'processed' || name.startsWith('.')) return
      const childPath = join(INTAKE_DIR, name)
      let isDir = false
      try {
        isDir = statSync(childPath).isDirectory()
      } catch {
        return
      }
      if (!isDir) return

      // Attach a per-dir watcher once per uuid
      if (!perDirWatchers.has(name)) {
        try {
          const w = watch(childPath, (_e, innerFile) => {
            if (!innerFile) return
            if (innerFile.toString() === 'manifest.json') {
              processOne(name).catch((err) =>
                logger.error({ err, uuid: name }, 'Intake: watcher error'),
              )
            }
          })
          perDirWatchers.set(name, w)
        } catch (err) {
          logger.warn({ err, childPath }, 'Intake: could not attach per-dir watcher')
        }
      }

      // Also try processing immediately in case manifest is already there
      if (existsSync(join(childPath, 'manifest.json'))) {
        processOne(name).catch((err) => logger.error({ err, uuid: name }, 'Intake: watcher error'))
      }
    })
  } catch (err) {
    logger.error({ err, INTAKE_DIR }, 'Intake: fs.watch attach failed, relying on reconcile only')
  }

  // Periodic reconcile to catch anything fs.watch missed (Syncthing delivery, NFS, etc.)
  setInterval(reconcile, RECONCILE_INTERVAL_MS)
}
