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

  it('preserves a header+separator that has no data rows as readable text, not raw pipes', () => {
    const input = ['| Feature | Status |', '|---|---|', '', 'After the table'].join('\n')
    const out = convertMarkdownTables(input)
    // No silent data loss, and no raw markdown pipes leaking to the user.
    expect(out).toContain('Feature')
    expect(out).toContain('Status')
    expect(out).toContain('After the table')
    expect(out).not.toContain('|')
  })

  it('preserves intentional bold inside a value cell', () => {
    const input = ['| Item | Note |', '|---|---|', '| Cache | this is **hot** |'].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('<b>hot</b>')
  })

  it('does not corrupt a bare double-asterisk that is not a complete wrapper', () => {
    const input = ['| Item | Note |', '|---|---|', '| Math | 2 ** 3 |'].join('\n')
    const out = convertMarkdownTables(input)
    expect(out).toContain('2 ** 3')
  })

  it('does not treat a pipe line as a table when separator column count differs from header', () => {
    const input = ['| a | b | c |', '|---|---|', '| 1 | 2 | 3 |'].join('\n')
    // separator has 2 columns, header has 3 -> malformed, leave untouched
    expect(convertMarkdownTables(input)).toBe(input)
  })

  it('strips bold markers inside cells so they do not leave stray asterisks', () => {
    const input = ['| Item | Note |', '|---|---|', '| **A** | plain |'].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('<b>A</b>: plain')
    expect(out).not.toContain('*')
  })
})
