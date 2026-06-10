import { describe, expect, it } from 'vitest'
import { convertMarkdownTables, formatForTelegram } from './telegramFormat.js'

describe('convertMarkdownTables', () => {
  it('converts a 2-column table to label: value lines', () => {
    const input = ['| Feature | Status |', '|---|---|', '| Hot cache | Done |', '| Audit | Pending |'].join('\n')
    const out = convertMarkdownTables(input)
    expect(out).toContain('**Hot cache**: Done')
    expect(out).toContain('**Audit**: Pending')
    // No raw table pipes survive.
    expect(out).not.toContain('|')
    expect(out).not.toContain('---')
  })

  it('converts a 3-column table to a bold label with header: value sub-bullets', () => {
    const input = [
      '| Dimension | Herk 2 | K2B |',
      '| --- | --- | --- |',
      '| Default harness | Claude Code | Claude Code (Opus) |',
    ].join('\n')
    const out = convertMarkdownTables(input)
    expect(out).toContain('**Default harness**')
    expect(out).toContain('• Herk 2: Claude Code')
    expect(out).toContain('• K2B: Claude Code (Opus)')
    expect(out).not.toContain('|')
  })

  it('leaves non-table text untouched', () => {
    const input = 'Just a sentence with a | pipe but no separator row.'
    expect(convertMarkdownTables(input)).toBe(input)
  })

  it('does not touch a table inside a fenced code block (via formatForTelegram)', () => {
    const input = ['```', '| a | b |', '|---|---|', '| 1 | 2 |', '```'].join('\n')
    const out = formatForTelegram(input)
    // Code block content is preserved verbatim (HTML-escaped) inside <pre>.
    expect(out).toContain('| a | b |')
    expect(out).toContain('<pre>')
  })

  it('drops empty trailing cells in a wide row', () => {
    const input = ['| Item | A | B |', '|---|---|---|', '| Row | x |  |'].join('\n')
    const out = convertMarkdownTables(input)
    expect(out).toContain('• A: x')
    expect(out).not.toContain('B:')
  })

  it('renders converted bold labels through the full HTML formatter', () => {
    const input = ['| Feature | Status |', '|---|---|', '| Hot cache | Done |'].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('<b>Hot cache</b>: Done')
    expect(out).not.toContain('|')
  })
})
