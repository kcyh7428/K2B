/**
 * Washing Machine Normalization Gate (Ship 1 Commit 3, text-only).
 *
 * Runs the MiniMax-M2.7 classifier on every qualifying Telegram text
 * message, resolves relative dates, and writes kept entities to the
 * semantic shelf. Non-blocking: classifier / shelf failures never break
 * the user-facing reply path; they log and the agent answers from the
 * legacy memory path (to be retired in Commit 4).
 *
 * Text-only branch: photo, document, and audio messages arrive wrapped
 * by media.ts and are skipped here -- VLM ingest is Ship 1B. Voice
 * transcriptions are unwrapped and classified as plain text.
 *
 * Spec: wiki/concepts/feature_washing-machine-memory.md (2026-04-23
 * compression). Plan: plans/2026-04-21_washing-machine-ship-1.md
 * Commit 3.
 */

import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import { logger } from './logger.js'
import { K2B_PROJECT_ROOT } from './config.js'

const CLASSIFIER_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/classify.sh')
const NORMALIZE_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/normalize.py')
const SHELF_WRITER_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/shelf-writer.sh')

const CLASSIFIER_TIMEOUT_MS = 15_000
const NORMALIZE_TIMEOUT_MS = 5_000
const SHELF_WRITE_TIMEOUT_MS = 5_000

const PINNED_TYPES = new Set(['contact', 'person', 'org', 'appointment', 'decision'])
const VALID_ENTITY_TYPES = new Set([
  'contact',
  'person',
  'org',
  'appointment',
  'decision',
  'preference',
  'fact',
  'location',
])

const ATTACHMENT_WRAPPER_PREFIXES = [
  '[Photo attached at ',
  '[Document attached: ',
]

const VOICE_WRAPPER_PREFIX = '[Voice transcribed]: '

export interface ClassifierResult {
  keep: boolean
  category?: string
  shelf?: string
  discard_reason?: string
  entities?: Array<{
    type: string
    fields: Record<string, unknown>
  }>
  timestamp_iso?: string | null
  date_confidence?: number
}

export interface GateInvocation {
  status: 'classified' | 'skipped-attachment' | 'skipped-empty' | 'error'
  reason?: string
  classifier?: ClassifierResult
  rowsWritten: number
  latencyMs: number
}

export interface GateDeps {
  classifierScript?: string
  normalizeScript?: string
  shelfWriterScript?: string
  spawnImpl?: typeof spawn
  now?: () => number
  anchorIsoDate?: () => string
  /** Override for testing timeout behaviour without waiting 15s. */
  classifierTimeoutMs?: number
  normalizeTimeoutMs?: number
  shelfWriteTimeoutMs?: number
}

/**
 * Main entry point. Call fire-and-forget from bot.handleMessage BEFORE
 * memory read. The promise resolves with a structured result for tests;
 * callers may ignore it. Errors are swallowed + logged.
 */
export async function normalizationGate(
  rawText: string,
  deps: GateDeps = {}
): Promise<GateInvocation> {
  const now = deps.now ?? Date.now
  const started = now()

  const unwrapped = unwrapForClassifier(rawText)
  if (unwrapped === null) {
    return {
      status: 'skipped-attachment',
      rowsWritten: 0,
      latencyMs: now() - started,
    }
  }

  if (!unwrapped.trim()) {
    return {
      status: 'skipped-empty',
      rowsWritten: 0,
      latencyMs: now() - started,
    }
  }

  const anchor = deps.anchorIsoDate ? deps.anchorIsoDate() : isoDate(new Date())

  try {
    const rewritten = await runNormalize(unwrapped, anchor, deps)
    const classifier = await runClassifier(rewritten, anchor, deps)

    if (!classifier.keep) {
      return {
        status: 'classified',
        classifier,
        rowsWritten: 0,
        latencyMs: now() - started,
      }
    }

    const rowsWritten = await writeAcceptedRows(classifier, anchor, deps)
    return {
      status: 'classified',
      classifier,
      rowsWritten,
      latencyMs: now() - started,
    }
  } catch (err) {
    logger.warn({ err: String(err) }, 'Washing Machine gate failed (agent reply continues)')
    return {
      status: 'error',
      reason: String(err),
      rowsWritten: 0,
      latencyMs: now() - started,
    }
  }
}

/**
 * Strip Telegram voice wrappers; return null for attachment wrappers we
 * do NOT classify in Ship 1 (photos, documents -- Ship 1B handles them).
 * Any other text passes through unchanged.
 */
export function unwrapForClassifier(rawText: string): string | null {
  if (typeof rawText !== 'string') return null
  for (const prefix of ATTACHMENT_WRAPPER_PREFIXES) {
    if (rawText.startsWith(prefix)) return null
  }
  if (rawText.startsWith(VOICE_WRAPPER_PREFIX)) {
    return rawText.slice(VOICE_WRAPPER_PREFIX.length)
  }
  return rawText
}

function isoDate(d: Date): string {
  const y = d.getUTCFullYear()
  const m = String(d.getUTCMonth() + 1).padStart(2, '0')
  const day = String(d.getUTCDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

async function runNormalize(text: string, anchor: string, deps: GateDeps): Promise<string> {
  const script = deps.normalizeScript ?? NORMALIZE_SCRIPT
  const spawner = deps.spawnImpl ?? spawn
  const timeout = deps.normalizeTimeoutMs ?? NORMALIZE_TIMEOUT_MS
  return await captureStdout(
    spawner('python3', [script, '--anchor', anchor], { stdio: ['pipe', 'pipe', 'pipe'] }),
    text,
    timeout,
    'normalize.py'
  )
}

async function runClassifier(text: string, anchor: string, deps: GateDeps): Promise<ClassifierResult> {
  const script = deps.classifierScript ?? CLASSIFIER_SCRIPT
  const spawner = deps.spawnImpl ?? spawn
  const timeout = deps.classifierTimeoutMs ?? CLASSIFIER_TIMEOUT_MS
  const raw = await captureStdout(
    spawner('bash', [script, '--anchor', anchor, '--input', '-'], { stdio: ['pipe', 'pipe', 'pipe'] }),
    text,
    timeout,
    'classify.sh'
  )
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch (err) {
    throw new Error(`classify.sh returned invalid JSON: ${String(err)}`)
  }
  return validateClassifier(parsed)
}

function validateClassifier(obj: unknown): ClassifierResult {
  if (!obj || typeof obj !== 'object') {
    throw new Error('classifier output is not an object')
  }
  const o = obj as Record<string, unknown>
  if (typeof o.keep !== 'boolean') {
    throw new Error('classifier output missing boolean keep')
  }
  const result: ClassifierResult = { keep: o.keep }
  if (o.keep) {
    if (typeof o.category !== 'string' || typeof o.shelf !== 'string') {
      throw new Error('keep=true output missing category or shelf')
    }
    result.category = o.category
    result.shelf = o.shelf
    if (Array.isArray(o.entities)) {
      const raw = o.entities
        .filter((e) => e && typeof e === 'object')
        .map((e) => {
          const entity = e as Record<string, unknown>
          const type = typeof entity.type === 'string' ? entity.type : ''
          const fields =
            entity.fields && typeof entity.fields === 'object'
              ? (entity.fields as Record<string, unknown>)
              : {}
          return { type, fields }
        })
      const kept = raw.filter((e) => VALID_ENTITY_TYPES.has(e.type))
      const dropped = raw.filter((e) => !VALID_ENTITY_TYPES.has(e.type))
      if (dropped.length > 0) {
        logger.warn(
          { droppedTypes: dropped.map((e) => e.type) },
          'Washing Machine dropped unknown entity types from classifier output'
        )
      }
      result.entities = kept
    }
    if (typeof o.timestamp_iso === 'string') result.timestamp_iso = o.timestamp_iso
    if (o.timestamp_iso === null) result.timestamp_iso = null
    if (typeof o.date_confidence === 'number') result.date_confidence = o.date_confidence
  } else {
    result.discard_reason = typeof o.discard_reason === 'string' ? o.discard_reason : 'low_signal'
  }
  return result
}

async function writeAcceptedRows(
  classifier: ClassifierResult,
  anchor: string,
  deps: GateDeps
): Promise<number> {
  if (!classifier.entities || classifier.entities.length === 0) {
    logger.warn(
      { category: classifier.category, shelf: classifier.shelf },
      'Washing Machine classifier kept message but produced zero entities -- shelf row silently dropped (prompt drift?)'
    )
    return 0
  }
  let written = 0
  for (const entity of classifier.entities) {
    // Defence-in-depth: validateClassifier already drops unknown types, but
    // re-check at the spawn boundary so a future refactor of the filter
    // cannot let an unvalidated type reach the shelf-writer argv.
    if (!VALID_ENTITY_TYPES.has(entity.type)) continue
    const pinned = PINNED_TYPES.has(entity.type) ? 'yes' : 'no'
    const slug = buildSlug(entity)
    if (!slug) continue
    const attrs = buildAttrs(entity, classifier, pinned)
    try {
      await runShelfWriter(entity.type, slug, rowDate(classifier, anchor), attrs, deps)
      written += 1
    } catch (err) {
      logger.error({ err: String(err), type: entity.type }, 'Washing Machine shelf write failed -- row permanently lost')
    }
  }
  return written
}

function rowDate(classifier: ClassifierResult, anchor: string): string {
  if (classifier.timestamp_iso && typeof classifier.timestamp_iso === 'string') {
    const match = classifier.timestamp_iso.match(/^(\d{4}-\d{2}-\d{2})/)
    if (match) return match[1]
  }
  return anchor
}

function buildSlug(entity: { type: string; fields: Record<string, unknown> }): string | null {
  const f = entity.fields
  const candidate =
    pickString(f, 'name') ||
    pickString(f, 'name_en') ||
    pickString(f, 'name_zh') ||
    pickString(f, 'subject') ||
    pickString(f, 'trigger') ||
    pickString(f, 'location')
  if (!candidate) return null
  return slugify(candidate)
}

function pickString(obj: Record<string, unknown>, key: string): string {
  const v = obj[key]
  return typeof v === 'string' ? v : ''
}

function slugify(s: string): string {
  const base = s
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return base || 'unnamed'
}

function buildAttrs(
  entity: { type: string; fields: Record<string, unknown> },
  classifier: ClassifierResult,
  pinned: 'yes' | 'no'
): string[] {
  const attrs: string[] = []
  for (const [key, value] of Object.entries(entity.fields)) {
    if (value === null || value === undefined) continue
    if (typeof value !== 'string' && typeof value !== 'number' && typeof value !== 'boolean') continue
    const str = String(value).replace(/[\r\n]+/g, ' ').trim()
    if (!str) continue
    const safeKey = normalizeKey(key)
    if (!safeKey) continue
    attrs.push(`${safeKey}:${str}`)
  }
  attrs.push(`pinned:${pinned}`)
  if (classifier.category) attrs.push(`category:${classifier.category}`)
  if (typeof classifier.date_confidence === 'number') {
    attrs.push(`date_confidence:${classifier.date_confidence.toFixed(2)}`)
  }
  attrs.push('source:telegram-classifier')
  return attrs
}

function normalizeKey(key: string): string {
  const lowered = key.toLowerCase().replace(/[^a-z0-9_-]+/g, '_')
  if (!/^[a-z]/.test(lowered)) return ''
  return lowered
}

async function runShelfWriter(
  entityType: string,
  slug: string,
  date: string,
  attrs: string[],
  deps: GateDeps
): Promise<void> {
  const script = deps.shelfWriterScript ?? SHELF_WRITER_SCRIPT
  const spawner = deps.spawnImpl ?? spawn
  const timeout = deps.shelfWriteTimeoutMs ?? SHELF_WRITE_TIMEOUT_MS
  const args = ['bash', script, '--shelf', 'semantic', '--date', date, '--type', entityType, '--slug', slug]
  for (const a of attrs) {
    args.push('--attr', a)
  }
  const child = spawner(args[0], args.slice(1), { stdio: ['ignore', 'pipe', 'pipe'] })
  await captureStdout(child, null, timeout, 'shelf-writer.sh')
}

interface ChildLike {
  stdin: NodeJS.WritableStream | null
  stdout: NodeJS.ReadableStream | null
  stderr: NodeJS.ReadableStream | null
  on(event: 'close', listener: (code: number | null) => void): this
  on(event: 'error', listener: (err: Error) => void): this
  kill(signal?: NodeJS.Signals | number): boolean | void
}

function captureStdout(
  child: ChildLike,
  stdin: string | null,
  timeoutMs: number,
  label: string
): Promise<string> {
  return new Promise((resolveFn, reject) => {
    let stdout = ''
    let stderr = ''
    let finished = false

    const finish = (fn: () => void) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      fn()
    }

    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        // ignore
      }
      finish(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)))
    }, timeoutMs)

    child.stdout?.on('data', (chunk: Buffer | string) => {
      stdout += typeof chunk === 'string' ? chunk : chunk.toString('utf-8')
    })
    child.stderr?.on('data', (chunk: Buffer | string) => {
      stderr += typeof chunk === 'string' ? chunk : chunk.toString('utf-8')
    })
    child.on('error', (err) => finish(() => reject(err)))
    child.on('close', (code) => {
      if (code === 0) {
        finish(() => resolveFn(stdout))
      } else {
        finish(() =>
          reject(
            new Error(
              `${label} exited with code ${code ?? 'null'}: ${stderr.trim() || '(no stderr)'}`
            )
          )
        )
      }
    })

    if (stdin !== null && child.stdin) {
      child.stdin.end(stdin)
    } else if (child.stdin) {
      child.stdin.end()
    }
  })
}
