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
import { randomUUID } from 'node:crypto'
import { writeFileSync, renameSync, mkdirSync } from 'node:fs'
import { logger } from './logger.js'
import { K2B_PROJECT_ROOT, K2B_VAULT_PATH } from './config.js'

const CLASSIFIER_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/classify.sh')
const NORMALIZE_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/normalize.py')
const SHELF_WRITER_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/shelf-writer.sh')
const PENDING_DIR_DEFAULT = resolve(K2B_VAULT_PATH, 'wiki/context/shelves/.pending-confirmation')

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
  /** Ship 1B: reasons the gate should park rather than write directly. */
  needs_confirmation_reason?: string[]
}

export interface GateInvocation {
  /**
   * Ship 1B added `pending-confirmation`. Everything else preserves the
   * Ship 1 contract for existing callers (bot.ts, memoryInject.test.ts,
   * washingMachine.gate.test.ts).
   */
  status:
    | 'classified'
    | 'skipped-attachment'
    | 'skipped-empty'
    | 'pending-confirmation'
    | 'error'
  reason?: string
  classifier?: ClassifierResult
  rowsWritten: number
  latencyMs: number
  /** Ship 1B pending-confirmation path: UUID of the .pending file written. */
  pendingUuid?: string
  /** Ship 1B pending-confirmation path: Telegram prompt text to post. */
  pendingPrompt?: string
}

/**
 * Ship 1B: structured input for the gate. String input is still accepted
 * via the overload so the Ship 1 callers (bot.ts line 171, gate tests,
 * live tests) keep working unchanged.
 */
export interface GateInput {
  rawText: string
  /** Epoch-ms timestamp of the source Telegram message. */
  messageTsMs?: number
  /** Chat ID -- required for the pending-confirmation write path. */
  chatId?: string
  /** message_id of the prompt we'll send, for reply-to disambiguation. */
  promptMessageId?: number
  /** ISO date detected by OCR on the attachment, if any. */
  ocrDate?: string
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
  /** Ship 1B: override where .pending-confirmation/ files land. */
  pendingDirOverride?: string
}

/**
 * Main entry point. Call fire-and-forget from bot.handleMessage BEFORE
 * memory read. The promise resolves with a structured result for tests;
 * callers may ignore it. Errors are swallowed + logged.
 *
 * Overloaded for backward compatibility: callers may pass a bare string
 * (Ship 1 contract) or a GateInput object (Ship 1B attachment path with
 * chatId / ocrDate / messageTsMs). String input is equivalent to
 * `{rawText: <string>}` and never triggers the pending-confirmation path.
 */
export async function normalizationGate(
  input: string | GateInput,
  deps?: GateDeps
): Promise<GateInvocation>
export async function normalizationGate(
  input: string | GateInput,
  deps: GateDeps = {}
): Promise<GateInvocation> {
  const gateInput: GateInput = typeof input === 'string' ? { rawText: input } : input
  const now = deps.now ?? Date.now
  const started = now()

  const unwrapped = unwrapForClassifier(gateInput.rawText)
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
    const { rewrittenText, needsConfirmationReason } = await runNormalize(
      unwrapped,
      anchor,
      gateInput,
      deps
    )
    const classifier = await runClassifier(rewrittenText, anchor, deps)
    // Attach normalize's confirmation reasons so the park decision has a
    // single source of truth. Also preserve any reasons the classifier
    // itself emitted (future prompt revisions may populate this).
    const mergedReasons = dedupe([
      ...(classifier.needs_confirmation_reason ?? []),
      ...needsConfirmationReason,
    ])
    if (mergedReasons.length > 0) {
      classifier.needs_confirmation_reason = mergedReasons
    }

    if (!classifier.keep) {
      return {
        status: 'classified',
        classifier,
        rowsWritten: 0,
        latencyMs: now() - started,
      }
    }

    // Ship 1B: when the Gate has a contradiction reason AND a chatId is
    // known, park the extraction in .pending-confirmation/ instead of
    // writing to the shelf. If chatId is missing (e.g. a legacy text-only
    // caller that didn't pass GateInput), fall through to the normal
    // write path rather than losing the row silently.
    if (mergedReasons.length > 0 && gateInput.chatId) {
      const park = parkPendingConfirmation(gateInput, classifier, deps)
      return {
        status: 'pending-confirmation',
        classifier,
        rowsWritten: 0,
        latencyMs: now() - started,
        pendingUuid: park.uuid,
        pendingPrompt: park.prompt,
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

function dedupe<T>(xs: T[]): T[] {
  return Array.from(new Set(xs))
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

// Anchor is formatted in Asia/Hong_Kong because the user's "today" is HKT.
// Using getUTC* would flip the anchor back one day during 00:00-07:59 HKT
// every day (HKT is UTC+8, so UTC is still on the previous calendar date),
// silently mis-resolving "tomorrow" and appointment timestamps for a third
// of each day. formatToParts is defensive against locale-format drift.
const HKT_DATE_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Hong_Kong',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

export function isoDate(d: Date): string {
  const parts = HKT_DATE_FORMATTER.formatToParts(d)
  const year = parts.find((p) => p.type === 'year')?.value
  const month = parts.find((p) => p.type === 'month')?.value
  const day = parts.find((p) => p.type === 'day')?.value
  if (!year || !month || !day) {
    throw new Error(`isoDate: HKT formatter missing parts for ${d.toISOString()}`)
  }
  return `${year}-${month}-${day}`
}

interface NormalizeResult {
  rewrittenText: string
  needsConfirmationReason: string[]
}

async function runNormalize(
  text: string,
  anchor: string,
  gateInput: GateInput,
  deps: GateDeps
): Promise<NormalizeResult> {
  const script = deps.normalizeScript ?? NORMALIZE_SCRIPT
  const spawner = deps.spawnImpl ?? spawn
  const timeout = deps.normalizeTimeoutMs ?? NORMALIZE_TIMEOUT_MS
  // Always use --json so we can surface needs_confirmation_reason even on
  // the text-only path (empty array when no OCR flags are set).
  const args: string[] = [script, '--anchor', anchor, '--json']
  if (gateInput.ocrDate) args.push('--ocr-date', gateInput.ocrDate)
  if (gateInput.messageTsMs !== undefined) args.push('--message-ts', String(gateInput.messageTsMs))
  const raw = await captureStdout(
    spawner('python3', args, { stdio: ['pipe', 'pipe', 'pipe'] }),
    text,
    timeout,
    'normalize.py'
  )
  // Tests may stub normalize with a spawn that emits plain text (Ship 1
  // contract). Tolerate both JSON and plain-text outputs so every existing
  // gate test keeps passing without changes.
  try {
    const parsed = JSON.parse(raw) as {
      rewritten_text?: string
      needs_confirmation_reason?: string[]
    }
    if (typeof parsed.rewritten_text === 'string') {
      return {
        rewrittenText: parsed.rewritten_text,
        needsConfirmationReason: Array.isArray(parsed.needs_confirmation_reason)
          ? parsed.needs_confirmation_reason
          : [],
      }
    }
  } catch {
    // fall through: assume plain-text output
  }
  return { rewrittenText: raw, needsConfirmationReason: [] }
}

function parkPendingConfirmation(
  gateInput: GateInput,
  classifier: ClassifierResult,
  deps: GateDeps
): { uuid: string; prompt: string } {
  const dir = deps.pendingDirOverride ?? PENDING_DIR_DEFAULT
  const uuid = randomUUID()
  const messageDate = gateInput.messageTsMs
    ? new Date(gateInput.messageTsMs).toISOString().slice(0, 10)
    : isoDate(new Date())
  const candidates: Array<{ date: string; label: string }> = [
    { date: messageDate, label: 'message date' },
  ]
  if (gateInput.ocrDate && gateInput.ocrDate !== messageDate) {
    candidates.push({ date: gateInput.ocrDate, label: 'OCR date' })
  }
  const row = classifier.entities?.[0] ?? {}
  const record = {
    chatId: gateInput.chatId,
    promptMessageId: gateInput.promptMessageId ?? 0,
    candidates,
    row,
  }
  const finalPath = resolve(dir, `${uuid}.json`)
  const tmpPath = resolve(dir, `.${uuid}.json.tmp`)
  // Atomic write: mkdir + tempfile + rename. Any disk / permission failure
  // throws to the outer normalizationGate try-catch, which returns
  // status:'error'. Since bot.ts only posts the Telegram prompt when
  // status is 'pending-confirmation', the user is never told to reply to
  // a file that doesn't exist. Clean up any half-written tempfile so a
  // retry on the next message isn't blocked by a stray .tmp.
  try {
    mkdirSync(dir, { recursive: true })
    writeFileSync(tmpPath, JSON.stringify(record, null, 2), 'utf8')
    renameSync(tmpPath, finalPath)
  } catch (err) {
    try {
      // Best-effort cleanup; swallow the secondary error so the primary
      // error surfaces with its original context.
      const { rmSync } = require('node:fs') as typeof import('node:fs')
      rmSync(tmpPath, { force: true })
    } catch {
      // ignore
    }
    throw new Error(`pending-write-failed: ${err instanceof Error ? err.message : String(err)}`)
  }
  const promptParts = [
    'Date mismatch on capture.',
    ` Reply 1 for ${candidates[0].date} (${candidates[0].label})`,
  ]
  if (candidates[1]) {
    promptParts.push(`, 2 for ${candidates[1].date} (${candidates[1].label})`)
  }
  promptParts.push(', or type YYYY-MM-DD.')
  return { uuid, prompt: promptParts.join('') }
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
    if (Array.isArray(o.needs_confirmation_reason)) {
      result.needs_confirmation_reason = o.needs_confirmation_reason.filter(
        (r) => typeof r === 'string'
      ) as string[]
    }
    // Prompt-drift / API-error guard: keep=true with no usable entities would
    // otherwise be silently reported as classified rowsWritten=0, identical to
    // a legitimate keep=false. Surface as an error so monitoring can see it.
    if (!result.entities || result.entities.length === 0) {
      const rawCount = Array.isArray(o.entities) ? o.entities.length : 0
      throw new Error(
        `classifier kept message (category=${String(o.category)}) but produced zero valid entities (raw=${rawCount}, all dropped or missing) -- likely prompt drift`
      )
    }
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
  // validateClassifier throws on keep=true with empty entities, so callers
  // only reach here with a non-empty typed list. `?? []` is belt-and-braces
  // for the optional type shape; at runtime the fallback is unreachable.
  const entities = classifier.entities ?? []
  let written = 0
  for (const entity of entities) {
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
