/**
 * Unit tests for washingMachine.ts pure helpers: unwrap, entity
 * validation, pinning classification, shelf attribute assembly.
 * No child_process, no filesystem -- all deterministic.
 */

import { describe, it, expect } from 'vitest'
import { unwrapForClassifier, isoDate } from './washingMachine.js'

describe('unwrapForClassifier', () => {
  it('passes through plain text unchanged', () => {
    expect(unwrapForClassifier("Dr. Lo's phone is 2830 3709")).toBe(
      "Dr. Lo's phone is 2830 3709"
    )
  })

  it('returns null for photo wrappers (Ship 1B handles VLM)', () => {
    const wrapped = '[Photo attached at /tmp/foo.jpg]\nAnalyze this image and respond to the user.'
    expect(unwrapForClassifier(wrapped)).toBeNull()
  })

  it('returns null for document wrappers (Ship 1B handles extraction)', () => {
    const wrapped =
      '[Document attached: report.pdf at /tmp/report.pdf]\nRead and process this document.'
    expect(unwrapForClassifier(wrapped)).toBeNull()
  })

  it('strips voice transcription prefix and keeps the spoken text', () => {
    expect(unwrapForClassifier('[Voice transcribed]: Dr. Lo phone is 2830 3709')).toBe(
      'Dr. Lo phone is 2830 3709'
    )
  })

  it('returns null when input is not a string', () => {
    expect(unwrapForClassifier(null as unknown as string)).toBeNull()
    expect(unwrapForClassifier(undefined as unknown as string)).toBeNull()
    expect(unwrapForClassifier(42 as unknown as string)).toBeNull()
  })

  it('does not misidentify inline "[...]" references as attachment wrappers', () => {
    // Only the documented prefixes count. Plain brackets in text go through.
    expect(unwrapForClassifier('See [spec] for details')).toBe('See [spec] for details')
  })
})

describe('isoDate (HKT anchor)', () => {
  it('returns HKT date for UTC-midnight instants (00:00-07:59 HKT boundary)', () => {
    // 2026-04-23T00:30:00+08:00 == 2026-04-22T16:30:00Z.
    // Prior getUTCDate() code returned 2026-04-22; the Gate would then resolve
    // "tomorrow" to 2026-04-23 instead of the user-expected 2026-04-24.
    const hktEarlyMorning = new Date('2026-04-23T00:30:00+08:00')
    expect(isoDate(hktEarlyMorning)).toBe('2026-04-23')
  })

  it('returns HKT date for noon HKT instants (no boundary ambiguity)', () => {
    const hktNoon = new Date('2026-04-23T12:00:00+08:00')
    expect(isoDate(hktNoon)).toBe('2026-04-23')
  })

  it('handles the reverse boundary (late-night UTC already tomorrow HKT)', () => {
    // 2026-04-22T20:00:00Z == 2026-04-23T04:00:00+08:00 -> HKT already Apr 23.
    const utcEvening = new Date('2026-04-22T20:00:00Z')
    expect(isoDate(utcEvening)).toBe('2026-04-23')
  })
})
