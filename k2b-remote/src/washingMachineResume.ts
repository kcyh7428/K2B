/**
 * Washing Machine -- pending-confirmation resume handler (Ship 1B Commit 3).
 *
 * When normalizationGate flags needs_confirmation_reason on an attachment
 * ingest (OCR date vs message metadata mismatch, or date_confidence < 0.7),
 * it parks the extraction in
 *   wiki/context/shelves/.pending-confirmation/<uuid>.json
 * and posts a numbered-option prompt on Telegram. This module finalises
 * the write once Keith replies.
 *
 * Disambiguation rule when the same chat has multiple pending files:
 *   1. Prefer replyToMessageId match (Telegram's native reply-to-quote).
 *   2. Fall through to sole-pending-for-chat when exactly one exists.
 *   3. Return status: 'ambiguous' when 2+ pendings exist without a
 *      reply-to ref, so the UX can tell Keith to reply-to-quote.
 *
 * The shelf writer is injected via deps so the resume handler stays
 * unit-testable without spawning scripts.
 */
import { readdirSync, readFileSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { logger } from './logger.js'

export interface PendingCandidate {
  date: string
  label: string
}

export interface PendingRecord {
  chatId: string
  promptMessageId: number
  candidates: PendingCandidate[]
  row: Record<string, unknown>
}

export interface ResumeInput {
  chatId: string
  replyText: string
  replyToMessageId: number | null
}

export type ShelfWriter = (row: Record<string, unknown>, date: string) => Promise<boolean>

export interface ResumeDeps {
  pendingDir: string
  shelfWriter: ShelfWriter
}

export type ResumeStatus = 'resolved' | 'retry' | 'not-found' | 'ambiguous' | 'error' | 'corrupt-record'

export interface ResumeResult {
  status: ResumeStatus
  chosenDate?: string
  uuid?: string
  message?: string
}

const VALID_ROW_TYPES = new Set([
  'contact',
  'person',
  'org',
  'appointment',
  'decision',
  'preference',
  'fact',
  'location',
])

/**
 * Reject pending records whose row field wouldn't survive a shelf write.
 * Corrupt records (schema drift, manual edit, writer bug) should surface
 * as a distinct status so the caller can tell Keith "this capture is
 * broken" rather than looping forever on "try again".
 */
function isValidPendingRow(row: unknown): row is { type: string; fields: Record<string, unknown> } {
  if (!row || typeof row !== 'object') return false
  const r = row as { type?: unknown; fields?: unknown }
  if (typeof r.type !== 'string' || !VALID_ROW_TYPES.has(r.type)) return false
  if (!r.fields || typeof r.fields !== 'object') return false
  return true
}

const ISO_DATE_RE = /^(\d{4}-\d{2}-\d{2})$/

function listPendingForChat(
  dir: string,
  chatId: string
): Array<{ uuid: string; record: PendingRecord }> {
  let names: string[]
  try {
    names = readdirSync(dir)
  } catch {
    return []
  }
  const out: Array<{ uuid: string; record: PendingRecord }> = []
  for (const name of names) {
    if (!name.endsWith('.json')) continue
    const uuid = name.replace(/\.json$/, '')
    try {
      const parsed = JSON.parse(readFileSync(join(dir, name), 'utf8')) as PendingRecord
      if (parsed.chatId === chatId) out.push({ uuid, record: parsed })
    } catch (err) {
      logger.warn({ err: String(err), uuid }, 'pending: bad JSON, skipping')
    }
  }
  return out
}

function interpretReply(replyText: string, candidates: PendingCandidate[]): string | null {
  const trimmed = replyText.trim()
  // "1"/"2"/etc: numbered option
  if (/^\d+$/.test(trimmed)) {
    const idx = Number(trimmed) - 1
    if (idx >= 0 && idx < candidates.length) return candidates[idx].date
    return null
  }
  // Typed ISO date: accept even if not among the candidates. Keith may
  // know the real date after seeing the prompt.
  const isoMatch = trimmed.match(ISO_DATE_RE)
  if (isoMatch) return isoMatch[1]
  // Fallback: exact candidate match (e.g. Keith retyped "2026-04-01")
  for (const c of candidates) {
    if (c.date === trimmed) return c.date
  }
  return null
}

export async function resumePendingConfirmation(
  input: ResumeInput,
  deps: ResumeDeps
): Promise<ResumeResult> {
  const entries = listPendingForChat(deps.pendingDir, input.chatId)
  if (entries.length === 0) return { status: 'not-found' }

  let match: { uuid: string; record: PendingRecord } | null = null
  if (input.replyToMessageId !== null) {
    match = entries.find((e) => e.record.promptMessageId === input.replyToMessageId) ?? null
  }
  if (!match) {
    if (entries.length > 1) {
      return {
        status: 'ambiguous',
        message: 'Multiple pending confirmations in this chat. Reply-to-quote the specific prompt.',
      }
    }
    match = entries[0]
  }

  const chosen = interpretReply(input.replyText, match.record.candidates)
  if (!chosen) {
    return {
      status: 'retry',
      uuid: match.uuid,
      message: 'Reply with 1, 2, or a date in YYYY-MM-DD.',
    }
  }

  // Schema guard: a malformed row (writer bug, manual edit, or schema
  // drift) would otherwise loop the user forever on "try again" because
  // shelfWriter returns false. Surface as corrupt-record so the bot can
  // tell Keith the record is broken and move on.
  if (!isValidPendingRow(match.record.row)) {
    return {
      status: 'corrupt-record',
      uuid: match.uuid,
      message: 'Pending record is malformed; discarding.',
    }
  }

  const ok = await deps.shelfWriter(match.record.row, chosen)
  if (!ok) {
    return { status: 'retry', uuid: match.uuid, message: 'Shelf write failed; try again.' }
  }

  // rmSync failure after a successful shelf write is an error state, not
  // a clean resolution: the pending file still exists, so a duplicate
  // reply would re-trigger the shelf write. Return status:'error' so the
  // bot doesn't tell Keith "Saved." while the stale file sits on disk.
  try {
    rmSync(join(deps.pendingDir, `${match.uuid}.json`))
  } catch (err) {
    logger.error(
      { err: String(err), uuid: match.uuid, chosen },
      'pending: shelf write succeeded but file delete failed -- future replies risk duplicate writes'
    )
    return {
      status: 'error',
      uuid: match.uuid,
      chosenDate: chosen,
      message: 'Saved to shelf, but pending file could not be cleared. Alert ops.',
    }
  }
  return { status: 'resolved', chosenDate: chosen, uuid: match.uuid }
}
