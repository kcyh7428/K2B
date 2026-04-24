/**
 * Unit tests for attachmentIngest.ts (Ship 1B Commit 4).
 *
 * Exercises the bot-side attachment wrapper: extractor spawning is
 * injected via deps, gate call is injected via deps, filesystem-adjacent
 * behaviour is tested without actually running the scripts.
 */
import { describe, it, expect } from 'vitest'
import { ingestAttachment, sniffOcrDate } from './attachmentIngest.js'
import type { GateInput, GateInvocation } from './washingMachine.js'

describe('sniffOcrDate', () => {
  it('finds YYYY-MM-DD in free text', () => {
    expect(sniffOcrDate('Dr. Lo Hak Keung\nAppointment: 2026-04-01 at 10am')).toBe('2026-04-01')
  })

  it('finds YYYY/MM/DD and normalizes to ISO', () => {
    expect(sniffOcrDate('Issued 2025/04/11 Dr. Lo card')).toBe('2025-04-11')
  })

  it('zero-pads single-digit month/day', () => {
    expect(sniffOcrDate('Date: 2026-4-1')).toBe('2026-04-01')
  })

  it('returns undefined when no date present', () => {
    expect(sniffOcrDate('Dr. Lo Hak Keung\nTel: 2830 3709')).toBeUndefined()
  })

  it('returns undefined for clearly invalid year', () => {
    expect(sniffOcrDate('ref 1999-01-01 antiquity')).toBeUndefined()
  })

  it('prefers the earliest-appearing date when multiple present', () => {
    expect(sniffOcrDate('First 2026-04-01 second 2025-01-15')).toBe('2026-04-01')
  })
})

describe('ingestAttachment', () => {
  it('photo ingest: extractor text flows into gate with messageTs and chatId', async () => {
    const observed: GateInput[] = []
    const result = await ingestAttachment(
      {
        type: 'photo',
        path: '/tmp/card.png',
        caption: 'Dr. Lo',
        messageTsMs: 1711987200000,
        chatId: '42',
        promptMessageId: 123,
      },
      {
        extractor: async (input) => ({
          normalized_text: 'Dr. Lo Hak Keung\nTel: 2830 3709',
          attachment_type: 'photo',
          source_path: input.path,
          provider: 'minimax-vlm',
          message_ts: input.messageTsMs,
        }),
        gate: async (input) => {
          observed.push(input as GateInput)
          return { status: 'classified', rowsWritten: 1, latencyMs: 10 }
        },
      }
    )
    expect(result.ocrText).toBe('Dr. Lo Hak Keung\nTel: 2830 3709')
    expect(observed.length).toBe(1)
    expect(observed[0].rawText).toContain('2830 3709')
    expect(observed[0].messageTsMs).toBe(1711987200000)
    expect(observed[0].chatId).toBe('42')
    expect(observed[0].promptMessageId).toBe(123)
  })

  it('pending-confirmation gate result surfaces pendingPrompt', async () => {
    const result = await ingestAttachment(
      {
        type: 'photo',
        path: '/tmp/card.png',
        caption: '',
        messageTsMs: 1711987200000,
        chatId: '42',
        promptMessageId: 123,
      },
      {
        extractor: async () => ({
          normalized_text: 'Dr. Lo\n2025-04-11',
          attachment_type: 'photo',
          source_path: '/tmp/card.png',
          provider: 'minimax-vlm',
          message_ts: 1711987200000,
        }),
        gate: async () => ({
          status: 'pending-confirmation',
          rowsWritten: 0,
          latencyMs: 10,
          pendingUuid: 'abc',
          pendingPrompt: 'Reply 1 for ..., 2 for ...',
        } as GateInvocation),
      }
    )
    expect(result.pendingPrompt).toBe('Reply 1 for ..., 2 for ...')
    expect(result.gate.status).toBe('pending-confirmation')
  })

  it('picks up OCR date via regex + threads to gate', async () => {
    let observedOcrDate: string | undefined
    await ingestAttachment(
      {
        type: 'photo',
        path: '/tmp/c.png',
        caption: '',
        messageTsMs: 1711987200000,
        chatId: '42',
      },
      {
        extractor: async () => ({
          normalized_text: 'Dr. Lo issued 2025-04-11',
          attachment_type: 'photo',
          source_path: '/tmp/c.png',
          provider: 'minimax-vlm',
          message_ts: 1711987200000,
        }),
        gate: async (input) => {
          observedOcrDate = input.ocrDate
          return { status: 'classified', rowsWritten: 1, latencyMs: 1 }
        },
      }
    )
    expect(observedOcrDate).toBe('2025-04-11')
  })

  it('extractor-provided ocr_date beats regex sniff', async () => {
    let observedOcrDate: string | undefined
    await ingestAttachment(
      {
        type: 'photo',
        path: '/tmp/c.png',
        caption: '',
        messageTsMs: 1711987200000,
        chatId: '42',
      },
      {
        extractor: async () => ({
          normalized_text: 'text with 2025-04-11 date inside',
          attachment_type: 'photo',
          source_path: '/tmp/c.png',
          provider: 'minimax-vlm',
          message_ts: 1711987200000,
          ocr_date: '2026-01-15',
        }),
        gate: async (input) => {
          observedOcrDate = input.ocrDate
          return { status: 'classified', rowsWritten: 0, latencyMs: 1 }
        },
      }
    )
    expect(observedOcrDate).toBe('2026-01-15')
  })

  it('extractor failure propagates', async () => {
    await expect(
      ingestAttachment(
        { type: 'photo', path: '/tmp/c.png', caption: '', messageTsMs: 1, chatId: '42' },
        {
          extractor: async () => {
            throw new Error('extract boom')
          },
          gate: async () => ({ status: 'classified', rowsWritten: 0, latencyMs: 0 }),
        }
      )
    ).rejects.toThrow(/extract boom/)
  })
})
