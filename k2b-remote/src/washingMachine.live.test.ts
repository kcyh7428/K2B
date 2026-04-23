/**
 * Live end-to-end test: drives normalizationGate() with REAL classify.sh
 * (one MiniMax API call), real normalize.py, and real shelf-writer.sh
 * writing to a temp shelves directory. Gated by LIVE_MINIMAX=1 so it
 * does not run on every CI build.
 *
 * This is the "production pipe does not crash" gate. Commit 3's narrow
 * job is to prove the bot.ts hook point reaches the shelf via the three
 * scripts without glue errors.
 */

import { describe, it, expect } from 'vitest'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { normalizationGate } from './washingMachine.js'

const LIVE = process.env.LIVE_MINIMAX === '1' || !!process.env.MINIMAX_API_KEY

describe.skipIf(!LIVE)('normalizationGate live pipeline', () => {
  it('classifies "Andrew 9876 5432 at Apex Capital" and lands a shelf row', async () => {
    const tmpShelves = mkdtempSync(resolve(tmpdir(), 'wm-live-shelves-'))
    const tmpLock = mkdtempSync(resolve(tmpdir(), 'wm-live-lock-'))
    process.env.K2B_SHELVES_DIR = tmpShelves
    process.env.K2B_SHELF_LOCK_DIR = tmpLock

    const result = await normalizationGate(
      "Andrew's new number is 9876 5432, works at Apex Capital.",
      { anchorIsoDate: () => '2026-04-01' }
    )

    expect(result.status).toBe('classified')
    expect(result.classifier?.keep).toBe(true)
    expect(result.rowsWritten).toBeGreaterThanOrEqual(1)

    const shelfPath = resolve(tmpShelves, 'semantic.md')
    expect(existsSync(shelfPath)).toBe(true)

    const shelfContent = readFileSync(shelfPath, 'utf-8')
    expect(shelfContent).toMatch(/row-count: [1-9]/)
    expect(shelfContent).toMatch(/## Rows/)
    expect(shelfContent).toContain('9876 5432')
    expect(shelfContent).toMatch(/pinned:yes/)
    expect(shelfContent).toMatch(/source:telegram-classifier/)
    expect(shelfContent).toContain('| contact |')
  }, 30_000)

  it('skips shelf write for a rejected question and returns keep=false', async () => {
    const tmpShelves = mkdtempSync(resolve(tmpdir(), 'wm-live-shelves-'))
    const tmpLock = mkdtempSync(resolve(tmpdir(), 'wm-live-lock-'))
    process.env.K2B_SHELVES_DIR = tmpShelves
    process.env.K2B_SHELF_LOCK_DIR = tmpLock

    const result = await normalizationGate("What's my doctor's phone number?", {
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('classified')
    expect(result.classifier?.keep).toBe(false)
    expect(result.classifier?.discard_reason).toBe('question')
    expect(result.rowsWritten).toBe(0)

    const shelfPath = resolve(tmpShelves, 'semantic.md')
    expect(existsSync(shelfPath)).toBe(false)
  }, 30_000)
})
