/**
 * Bot-side attachment ingest wrapper (Ship 1B Commit 4).
 *
 * Called from bot.ts photo / document handlers. Extracts OCR text via
 * scripts/washing-machine/extract-attachment.sh, then feeds that text
 * into normalizationGate with the message metadata timestamp so the
 * Ship 1B pending-confirmation UX can fire when the OCR date and the
 * metadata disagree.
 *
 * The caller gets back:
 *   - ocrText: the extracted plain text (append to the agent prompt so
 *     Opus sees what the OCR found)
 *   - gate: full GateInvocation from the Normalization Gate
 *   - pendingPrompt: when set, bot.ts should post this to Telegram
 *     INSTEAD of calling the agent; Keith's reply is handled by the
 *     resume interceptor in handleMessage.
 *
 * All filesystem / subprocess work is injected via `deps` so unit tests
 * never spawn a real VLM or classifier.
 */
import { spawn } from 'node:child_process'
import { resolve } from 'node:path'
import { logger } from './logger.js'
import { K2B_PROJECT_ROOT } from './config.js'
import { isoDate, normalizationGate, type GateInput, type GateInvocation } from './washingMachine.js'

const EXTRACT_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/extract-attachment.sh')
const SHELF_WRITER_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/shelf-writer.sh')
const EXTRACT_TIMEOUT_MS = 60_000
const RAW_OCR_SHELF_TIMEOUT_MS = 5_000
const RAW_OCR_FALLBACK_REASONS = new Set(['too_short', 'no_date', 'ambiguous', 'unparseable'])

export interface AttachmentInput {
  type: 'photo' | 'document'
  path: string
  caption?: string
  messageTsMs: number
  chatId?: string
  promptMessageId?: number
}

export interface ExtractResult {
  normalized_text: string
  attachment_type: string
  source_path: string
  provider: string
  message_ts: number
  ocr_date?: string
}

export interface IngestResult {
  ocrText: string
  gate: GateInvocation
  pendingPrompt?: string
  rawOcrRowsWritten: number
  rawOcrShelfWriterFailed: boolean
}

export interface IngestDeps {
  extractor?: (input: AttachmentInput) => Promise<ExtractResult>
  gate?: (input: GateInput) => Promise<GateInvocation>
  rawOcrShelfWriter?: (row: RawOcrShelfRow) => Promise<boolean>
}

export interface RawOcrShelfRow {
  text: string
  messageTsMs: number
  provider: string
  attachmentType: string
  sourcePath: string
}

async function runExtractor(input: AttachmentInput): Promise<ExtractResult> {
  return new Promise((resolveFn, reject) => {
    const child = spawn(EXTRACT_SCRIPT, [], { stdio: ['pipe', 'pipe', 'pipe'] })
    let out = ''
    let err = ''
    child.stdout.on('data', (c) => {
      out += c.toString()
    })
    child.stderr.on('data', (c) => {
      err += c.toString()
    })
    const timer = setTimeout(() => {
      try {
        child.kill('SIGTERM')
      } catch {
        // ignore
      }
      reject(new Error(`extract-attachment timed out after ${EXTRACT_TIMEOUT_MS}ms`))
    }, EXTRACT_TIMEOUT_MS)
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code !== 0) {
        reject(new Error(`extract-attachment exited ${code}: ${err.trim() || '(no stderr)'}`))
        return
      }
      let parsed: unknown
      try {
        parsed = JSON.parse(out)
      } catch (e) {
        reject(new Error(`extract-attachment bad JSON: ${(e as Error).message}`))
        return
      }
      if (!isExtractResult(parsed)) {
        reject(
          new Error(
            `extract-attachment returned unexpected shape: ${JSON.stringify(parsed).slice(0, 200)}`
          )
        )
        return
      }
      resolveFn(parsed)
    })
    child.on('error', (e) => {
      clearTimeout(timer)
      reject(e)
    })
    child.stdin.end(
      JSON.stringify({
        type: input.type,
        path: input.path,
        message_ts: input.messageTsMs,
      })
    )
  })
}

/**
 * Runtime schema guard for the extractor envelope. Cheap: checks required
 * string / number fields exist. Accepts unknown additional fields so a
 * forward-compatible extractor can add ocr_confidence, field_confidence,
 * etc., without breaking this reader.
 */
function isExtractResult(x: unknown): x is ExtractResult {
  if (!x || typeof x !== 'object') return false
  const o = x as Record<string, unknown>
  if (typeof o.normalized_text !== 'string') return false
  if (typeof o.attachment_type !== 'string') return false
  if (typeof o.provider !== 'string') return false
  return true
}

async function runRawOcrShelfWriter(row: RawOcrShelfRow): Promise<boolean> {
  const attrs = [
    `text:${collapseForShelf(row.text)}`,
    `pinned:no`,
    `category:fact`,
    `source:telegram-attachment-ocr`,
    `provider:${collapseForShelf(row.provider)}`,
    `attachment_type:${collapseForShelf(row.attachmentType)}`,
    `source_path:${collapseForShelf(row.sourcePath)}`,
  ]
  const args = [
    SHELF_WRITER_SCRIPT,
    '--shelf',
    'semantic',
    '--date',
    isoDate(new Date(row.messageTsMs)),
    '--type',
    'fact',
    '--slug',
    buildRawOcrSlug(row.text),
  ]
  for (const attr of attrs) {
    args.push('--attr', attr)
  }

  return new Promise((resolveFn) => {
    const child = spawn('bash', args, { stdio: ['ignore', 'pipe', 'pipe'] })
    let stderr = ''
    child.stderr.on('data', (c) => {
      stderr += c.toString()
    })
    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        // ignore
      }
      logger.error({ slug: args[args.indexOf('--slug') + 1] }, 'attachmentIngest: raw OCR shelf fallback timed out')
      resolveFn(false)
    }, RAW_OCR_SHELF_TIMEOUT_MS)
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code === 0) {
        resolveFn(true)
      } else {
        logger.error(
          { code, stderr: stderr.trim(), slug: args[args.indexOf('--slug') + 1] },
          'attachmentIngest: raw OCR shelf fallback failed'
        )
        resolveFn(false)
      }
    })
    child.on('error', (err) => {
      clearTimeout(timer)
      logger.error({ err: String(err) }, 'attachmentIngest: raw OCR shelf fallback spawn error')
      resolveFn(false)
    })
  })
}

function collapseForShelf(value: string): string {
  return value.replace(/[\r\n]+/g, ' ').trim()
}

function buildRawOcrSlug(text: string): string {
  const base = collapseForShelf(text)
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80)
    .replace(/-+$/g, '')
  return base || 'attachment-ocr'
}

const DATE_RE = /\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b/

/**
 * Best-effort ISO date sniff out of OCR text. Returns the first match
 * that parses cleanly. Used as a fallback when the extractor doesn't
 * emit a structured ocr_date field. The Ship 1B classifier v1 doesn't
 * have a separate OCR-date extractor yet, so this regex carries the
 * contradiction-detection signal.
 */
export function sniffOcrDate(text: string): string | undefined {
  const m = text.match(DATE_RE)
  if (!m) return undefined
  const [, y, mo, d] = m
  const year = Number(y)
  if (year < 2000 || year > 2100) return undefined
  const iso = `${y}-${mo.padStart(2, '0')}-${d.padStart(2, '0')}`
  const parsed = new Date(iso)
  return isNaN(parsed.getTime()) ? undefined : iso
}

export async function ingestAttachment(
  input: AttachmentInput,
  deps: IngestDeps = {}
): Promise<IngestResult> {
  const extractor = deps.extractor ?? runExtractor
  const gate =
    deps.gate ?? ((gi: GateInput) => normalizationGate(gi))
  const rawOcrShelfWriter = deps.rawOcrShelfWriter ?? runRawOcrShelfWriter

  const extracted = await extractor(input)
  const ocrText = extracted.normalized_text ?? ''
  const ocrDate = extracted.ocr_date ?? sniffOcrDate(ocrText)
  const gateResult = await gate({
    rawText: ocrText,
    messageTsMs: input.messageTsMs,
    chatId: input.chatId,
    promptMessageId: input.promptMessageId,
    ocrDate,
  })
  let rawOcrRowsWritten = 0
  let rawOcrShelfWriterFailed = false
  if (shouldWriteRawOcrFallback(extracted, gateResult)) {
    const wrote = await rawOcrShelfWriter({
      text: ocrText,
      messageTsMs: input.messageTsMs,
      provider: extracted.provider,
      attachmentType: extracted.attachment_type,
      sourcePath: extracted.source_path ?? input.path,
    })
    rawOcrRowsWritten = wrote ? 1 : 0
    rawOcrShelfWriterFailed = !wrote
    logger.info(
      {
        provider: extracted.provider,
        attachmentType: extracted.attachment_type,
        gateStatus: gateResult.status,
        discardReason: gateResult.classifier?.discard_reason,
        rawOcrRowsWritten,
        rawOcrShelfWriterFailed,
      },
      'attachmentIngest: raw OCR fallback after zero classifier rows'
    )
  }
  if (gateResult.status === 'pending-confirmation') {
    logger.info(
      { uuid: gateResult.pendingUuid, chatId: input.chatId, ocrDate, provider: extracted.provider },
      'attachmentIngest: parked pending-confirmation'
    )
  }
  return {
    ocrText,
    gate: gateResult,
    pendingPrompt: gateResult.pendingPrompt,
    rawOcrRowsWritten,
    rawOcrShelfWriterFailed,
  }
}

function shouldWriteRawOcrFallback(extracted: ExtractResult, gateResult: GateInvocation): boolean {
  const provider = extracted.provider.trim().toLowerCase()
  const discardReason = gateResult.classifier?.discard_reason
  return (
    provider !== 'pdftotext' &&
    provider !== 'passthrough' &&
    extracted.normalized_text.trim().length > 0 &&
    gateResult.status === 'classified' &&
    gateResult.rowsWritten === 0 &&
    typeof discardReason === 'string' &&
    RAW_OCR_FALLBACK_REASONS.has(discardReason)
  )
}
