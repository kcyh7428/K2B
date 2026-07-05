import { describe, expect, it } from 'vitest'
import { convertMarkdownTables, displayWidth, formatForTelegram } from './telegramFormat.js'

describe('displayWidth (CJK / emoji aware)', () => {
  it('counts ASCII as one cell each', () => {
    expect(displayWidth('abc')).toBe(3)
    expect(displayWidth('')).toBe(0)
  })

  it('counts CJK glyphs as two cells each', () => {
    expect(displayWidth('陈')).toBe(2)
    expect(displayWidth('名字')).toBe(4)
    expect(displayWidth('a名b')).toBe(4)
  })

  it('counts a wide emoji as two cells', () => {
    expect(displayWidth('🚀')).toBe(2)
  })

  it('counts an emoji + skin-tone modifier as one glyph (two cells)', () => {
    expect(displayWidth('👍🏻')).toBe(2)
  })

  it('counts a ZWJ emoji sequence as one glyph (two cells)', () => {
    expect(displayWidth('👨‍👩‍👧')).toBe(2) // man ZWJ woman ZWJ girl
  })

  it('counts common BMP emoji anchors as two cells (with or without VS16)', () => {
    expect(displayWidth('✅')).toBe(2)
    expect(displayWidth('❌')).toBe(2)
    expect(displayWidth('⚠️')).toBe(2) // ⚠ + VS16
    expect(displayWidth('❤️')).toBe(2) // ❤ + VS16
  })

  it('treats combining marks and variation selectors as zero width', () => {
    expect(displayWidth('é')).toBe(1) // e + combining acute accent
    expect(displayWidth('⚠️')).toBe(displayWidth('⚠')) // ⚠ + VS16
  })
})

describe('convertMarkdownTables (standalone vertical fallback)', () => {
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

  it('drops empty trailing cells in a wide row', () => {
    const input = ['| Item | A | B |', '|---|---|---|', '| Row | x |  |'].join('\n')
    const out = convertMarkdownTables(input)
    expect(out).toContain('• A: x')
    expect(out).not.toContain('B:')
  })

  it('preserves a header+separator that has no data rows as readable text, not raw pipes', () => {
    const input = ['| Feature | Status |', '|---|---|', '', 'After the table'].join('\n')
    const out = convertMarkdownTables(input)
    expect(out).toContain('Feature')
    expect(out).toContain('Status')
    expect(out).toContain('After the table')
    expect(out).not.toContain('|')
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
})

describe('formatForTelegram tables', () => {
  it('renders a narrow table as an aligned <pre> monospace grid, not bullets', () => {
    const input = ['| Feature | Status |', '|---|---|', '| Hot cache | Done |', '| Audit | Pending |'].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('<pre>')
    expect(out).toContain('Hot cache')
    expect(out).toContain('Pending')
    expect(out).not.toContain('<b>Hot cache</b>:') // not the old bullet flattening
  })

  it('aligns CJK columns by DISPLAY width so every rendered row is the same width', () => {
    const input = ['| 名字 | Role |', '|---|---|', '| 陈 | Dev |', '| Alexander | Eng |'].join('\n')
    const out = formatForTelegram(input)
    const pre = out.match(/<pre>([\s\S]*?)<\/pre>/)
    expect(pre).not.toBeNull()
    const rows = pre![1].split('\n').filter((r) => r.length > 0)
    expect(rows.length).toBeGreaterThan(2)
    // The whole point of the port: an aligned monospace grid has one uniform
    // display width across the header, divider, and every body row. A naive
    // code-unit padding would make the CJK rows drift.
    const widths = rows.map((r) => displayWidth(r))
    expect(new Set(widths).size).toBe(1)
  })

  it('HTML-escapes cell content inside the aligned <pre> block', () => {
    const input = ['| Tag | Note |', '|---|---|', '| <b> | raw |'].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('&lt;b&gt;')
  })

  it('keeps cell text verbatim in the aligned grid (no markdown re-render)', () => {
    const input = ['| Item | Note |', '|---|---|', '| Cache | is **hot** |'].join('\n')
    const out = formatForTelegram(input)
    // Inside a monospace grid the raw markdown stays literal (it is not a phone
    // formatting surface); the vertical fallback is where bold gets rendered.
    expect(out).toContain('**hot**')
  })

  it('falls back to a vertical bold-label list for a table too wide to align', () => {
    const wideCell = 'x'.repeat(60)
    const input = ['| Item | Detail |', '|---|---|', `| Thing | ${wideCell} |`].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('<b>Thing</b>:')
    expect(out).not.toContain('<pre>')
  })

  it('restores inline code inside an aligned table cell (no leaked sentinel)', () => {
    const input = ['| Cmd | Note |', '|---|---|', '| `npm test` | run |'].join('\n')
    const out = formatForTelegram(input)
    expect(out).toContain('npm test')
    expect(out).not.toContain('PB0') // no raw protected-block marker leaked
  })

  it('aligns a status table with emoji anchors (✅ / ⚠️) by display width', () => {
    const input = ['| Check | Status |', '|---|---|', '| Cache | ✅ |', '| Audit | ⚠️ |'].join('\n')
    const out = formatForTelegram(input)
    const pre = out.match(/<pre>([\s\S]*?)<\/pre>/)
    expect(pre).not.toBeNull()
    const rows = pre![1].split('\n').filter((r) => r.length > 0)
    const widths = rows.map((r) => displayWidth(r))
    expect(new Set(widths).size).toBe(1)
  })

  it('does not touch a table inside a fenced code block', () => {
    const input = ['```', '| a | b |', '|---|---|', '| 1 | 2 |', '```'].join('\n')
    const out = formatForTelegram(input)
    // Code block content is preserved verbatim (HTML-escaped) inside <pre>.
    expect(out).toContain('| a | b |')
    expect(out).toContain('<pre>')
  })
})
