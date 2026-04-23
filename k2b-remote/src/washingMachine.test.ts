/**
 * Unit tests for washingMachine.ts pure helpers: unwrap, entity
 * validation, pinning classification, shelf attribute assembly.
 * No child_process, no filesystem -- all deterministic.
 */

import { describe, it, expect } from 'vitest'
import { unwrapForClassifier } from './washingMachine.js'

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
