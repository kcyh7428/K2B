/**
 * Unit tests for washingMachineResume.ts (Ship 1B Commit 3).
 *
 * Exercises the resume handler in isolation -- no bot.ts, no shelf writer.
 * The shelf writer is injected via deps.shelfWriter so every test case
 * can assert the write row + date without hitting the real script.
 */
import { describe, it, expect } from 'vitest'
import { mkdtempSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { resumePendingConfirmation, type ShelfWriter } from './washingMachineResume.js'

function freshDir(): string {
  return mkdtempSync(join(tmpdir(), 'wmr-'))
}

function seed(dir: string, uuid: string, payload: Record<string, unknown>): void {
  writeFileSync(join(dir, `${uuid}.json`), JSON.stringify(payload, null, 2))
}

function recordingWriter(): {
  writer: ShelfWriter
  writes: Array<{ row: Record<string, unknown>; date: string }>
} {
  const writes: Array<{ row: Record<string, unknown>; date: string }> = []
  const writer: ShelfWriter = async (row, date) => {
    writes.push({ row, date })
    return true
  }
  return { writer, writes }
}

describe('resumePendingConfirmation', () => {
  it('reply "1" finalises with first candidate date', async () => {
    const dir = freshDir()
    seed(dir, 'aaa-111', {
      chatId: '42',
      promptMessageId: 999,
      candidates: [
        { date: '2026-04-01', label: 'Message date' },
        { date: '2025-04-11', label: 'OCR date' },
      ],
      row: { type: 'contact', fields: { name: 'Dr. Lo', phone: '2830 3709' } },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: 999 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('resolved')
    expect(result.chosenDate).toBe('2026-04-01')
    expect(writes.length).toBe(1)
    expect(writes[0].date).toBe('2026-04-01')
    expect(existsSync(join(dir, 'aaa-111.json'))).toBe(false)
  })

  it('reply "2" finalises with second candidate date', async () => {
    const dir = freshDir()
    seed(dir, 'bbb-222', {
      chatId: '42',
      promptMessageId: 1000,
      candidates: [
        { date: '2026-04-01', label: 'Message' },
        { date: '2025-04-11', label: 'OCR' },
      ],
      row: { type: 'contact', fields: { name: 'Dr. Lo' } },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '2', replyToMessageId: 1000 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('resolved')
    expect(result.chosenDate).toBe('2025-04-11')
    expect(writes[0].date).toBe('2025-04-11')
  })

  it('reply "2026-04-01" finalises with typed ISO date', async () => {
    const dir = freshDir()
    seed(dir, 'ccc-333', {
      chatId: '42',
      promptMessageId: 1001,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-11', label: 'b' },
      ],
      row: { type: 'contact', fields: { name: 'Dr. Lo' } },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '2026-04-01', replyToMessageId: 1001 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('resolved')
    expect(result.chosenDate).toBe('2026-04-01')
    expect(writes[0].date).toBe('2026-04-01')
  })

  it('typed date not among candidates is accepted if valid ISO', async () => {
    const dir = freshDir()
    seed(dir, 'ddd-444', {
      chatId: '42',
      promptMessageId: 1002,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-11', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '2026-05-15', replyToMessageId: 1002 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('resolved')
    expect(writes[0].date).toBe('2026-05-15')
  })

  it('nonsense reply keeps pending file, returns retry status', async () => {
    const dir = freshDir()
    seed(dir, 'eee-555', {
      chatId: '42',
      promptMessageId: 1003,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-11', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: 'banana', replyToMessageId: 1003 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('retry')
    expect(writes.length).toBe(0)
    expect(existsSync(join(dir, 'eee-555.json'))).toBe(true)
  })

  it('reply-to disambiguates concurrent pendings in same chat', async () => {
    const dir = freshDir()
    seed(dir, 'fff-666', {
      chatId: '42',
      promptMessageId: 2000,
      candidates: [
        { date: '2026-01-01', label: 'a' },
        { date: '2025-01-01', label: 'b' },
      ],
      row: { type: 'contact', fields: { name: 'Alice' } },
    })
    seed(dir, 'ggg-777', {
      chatId: '42',
      promptMessageId: 3000,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-01', label: 'b' },
      ],
      row: { type: 'contact', fields: { name: 'Bob' } },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: 3000 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('resolved')
    const row = writes[0].row as { fields?: { name?: string } }
    expect(row.fields?.name).toBe('Bob')
    expect(existsSync(join(dir, 'fff-666.json'))).toBe(true)
    expect(existsSync(join(dir, 'ggg-777.json'))).toBe(false)
  })

  it('2+ pendings without reply-to → ambiguous', async () => {
    const dir = freshDir()
    seed(dir, 'hhh-888', {
      chatId: '42',
      promptMessageId: 4000,
      candidates: [
        { date: '2026-01-01', label: 'a' },
        { date: '2025-01-01', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    seed(dir, 'iii-999', {
      chatId: '42',
      promptMessageId: 5000,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-01', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: null },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('ambiguous')
    expect(writes.length).toBe(0)
  })

  it('no pending for chat → not-found, no throw', async () => {
    const dir = freshDir()
    const { writer } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: null },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('not-found')
  })

  it('shelf writer returns false → retry, pending file kept', async () => {
    const dir = freshDir()
    seed(dir, 'jjj-000', {
      chatId: '42',
      promptMessageId: 6000,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-01', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    const writer: ShelfWriter = async () => false
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: 6000 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('retry')
    expect(existsSync(join(dir, 'jjj-000.json'))).toBe(true)
  })

  it('sole pending for chat resolves without reply-to if unambiguous', async () => {
    const dir = freshDir()
    seed(dir, 'kkk-aaa', {
      chatId: '42',
      promptMessageId: 7000,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-01', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '2', replyToMessageId: null },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('resolved')
    expect(writes[0].date).toBe('2025-04-01')
  })

  it('matches only pending for the same chat (cross-chat isolation)', async () => {
    const dir = freshDir()
    seed(dir, 'aaa-chat1', {
      chatId: '111',
      promptMessageId: 8000,
      candidates: [
        { date: '2026-01-01', label: 'a' },
        { date: '2025-01-01', label: 'b' },
      ],
      row: { type: 'contact', fields: {} },
    })
    const { writer } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '222', replyText: '1', replyToMessageId: null },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('not-found')
    expect(existsSync(join(dir, 'aaa-chat1.json'))).toBe(true)
  })

  // --- Round-2 folds ---

  it('malformed row (no type) → corrupt-record status, no shelf write', async () => {
    const dir = freshDir()
    seed(dir, 'corrupt-1', {
      chatId: '42',
      promptMessageId: 9000,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-01', label: 'b' },
      ],
      row: { fields: { name: 'no type field' } }, // missing row.type
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: 9000 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('corrupt-record')
    expect(writes.length).toBe(0)
  })

  it('row with unknown type → corrupt-record', async () => {
    const dir = freshDir()
    seed(dir, 'corrupt-2', {
      chatId: '42',
      promptMessageId: 9001,
      candidates: [
        { date: '2026-04-01', label: 'a' },
        { date: '2025-04-01', label: 'b' },
      ],
      row: { type: 'nonsense', fields: {} },
    })
    const { writer, writes } = recordingWriter()
    const result = await resumePendingConfirmation(
      { chatId: '42', replyText: '1', replyToMessageId: 9001 },
      { pendingDir: dir, shelfWriter: writer }
    )
    expect(result.status).toBe('corrupt-record')
    expect(writes.length).toBe(0)
  })

  // Note: rmSync-failure-after-successful-write returns status:'error'.
  // Unit-testing that path reliably requires fs mocking (chmod 000 on
  // macOS doesn't reproduce the failure consistently because the dir
  // is readable before rmSync is called). The error path is covered by
  // inspection -- see washingMachineResume.ts rmSync catch block. If
  // future schema changes alter the resume flow, add a vi.mock-based
  // test here rather than attempting filesystem permission tricks.
})
