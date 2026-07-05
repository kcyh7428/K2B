import { MAX_MESSAGE_LENGTH } from './config.js'

// --- Telegram formatting ---
//
// Telegram renders no markdown tables. Anything the agent emits as a
// `| col | col |` / `|---|---|` block used to arrive on a phone either as
// unreadable pipe noise or, once flattened to bullets, as a lossy list that
// dropped the grid entirely -- and NEITHER path was display-width aware, so
// CJK (Chinese) columns drifted out of alignment.
//
// Ported from NousResearch/hermes-agent's `agent/markdown_tables.py`
// (CJK/wide-character aware realignment): a table that fits a phone-friendly
// width is rewritten into a `wcwidth`-aligned monospace grid inside a <pre>
// block (Telegram renders <pre> fixed-width, so the columns line up and the
// block scrolls horizontally rather than soft-wrapping mid-cell). A table too
// wide to fit falls back to a vertical `label: value` list so it never
// soft-wraps into an unreadable mess on a narrow screen.

// Widest aligned grid (in monospace display cells) we still render as a <pre>
// table. Wider tables fall back to the vertical list. Tuned for a phone: a
// couple of columns of short cells fit; a 4-column or long-prose table goes
// vertical.
const TABLE_ALIGN_MAX_WIDTH = 48

// --- Display width (CJK / emoji aware) -----------------------------------
//
// Monospace alignment must pad by DISPLAY width, not code-unit length: CJK
// glyphs and most emoji occupy two monospace cells, combining marks occupy
// zero. This is a pragmatic East Asian Width approximation (not a full Unicode
// wcwidth table) -- dependency-free and good enough for table alignment, which
// is exactly what hermes-agent's wcwidth call provides. Like that code, we
// clamp anything ambiguous (control chars, variation selectors) to a
// non-negative width so it cannot corrupt the column math.

function charDisplayWidth(cp: number): number {
  // Zero width: combining marks, zero-width spaces/joiners, variation
  // selectors (e.g. the VS16 in emoji like ⚠️), control characters.
  if (
    cp === 0x200b || cp === 0x200c || cp === 0x200d || cp === 0xfeff ||
    (cp >= 0x0300 && cp <= 0x036f) || // combining diacritical marks
    (cp >= 0x1ab0 && cp <= 0x1aff) ||
    (cp >= 0x1dc0 && cp <= 0x1dff) ||
    (cp >= 0x20d0 && cp <= 0x20ff) || // combining marks for symbols
    (cp >= 0xfe00 && cp <= 0xfe0f) || // variation selectors
    (cp >= 0x1f3fb && cp <= 0x1f3ff)  // emoji skin-tone modifiers (zero-width)
  ) {
    return 0
  }
  if (cp < 0x20 || (cp >= 0x7f && cp <= 0x9f)) {
    return 0 // control characters
  }
  // Wide (2 cells): East Asian Wide + Fullwidth ranges + most emoji.
  if (
    (cp >= 0x1100 && cp <= 0x115f) || // Hangul Jamo
    (cp >= 0x2e80 && cp <= 0x303e) || // CJK radicals .. Kangxi symbols
    (cp >= 0x3041 && cp <= 0x33ff) || // Hiragana/Katakana .. CJK compat
    (cp >= 0x3400 && cp <= 0x4dbf) || // CJK Unified Ext A
    (cp >= 0x4e00 && cp <= 0x9fff) || // CJK Unified Ideographs
    (cp >= 0xa000 && cp <= 0xa4cf) || // Yi Syllables
    (cp >= 0xac00 && cp <= 0xd7a3) || // Hangul Syllables
    (cp >= 0xf900 && cp <= 0xfaff) || // CJK Compatibility Ideographs
    (cp >= 0xfe10 && cp <= 0xfe19) || // vertical forms
    (cp >= 0xfe30 && cp <= 0xfe6f) || // CJK compat + small form variants
    (cp >= 0xff00 && cp <= 0xff60) || // Fullwidth forms
    (cp >= 0xffe0 && cp <= 0xffe6) || // Fullwidth signs
    (cp >= 0x2600 && cp <= 0x27bf) ||   // Misc Symbols + Dingbats (✅ ⚠ ❌ ✈ ❤)
    (cp >= 0x2b00 && cp <= 0x2bff) ||   // Misc Symbols and Arrows (⭐ ⬅)
    (cp >= 0x1f300 && cp <= 0x1faff) || // emoji + pictographs + symbols
    (cp >= 0x20000 && cp <= 0x3fffd)    // CJK Unified Ext B and beyond
  ) {
    return 2
  }
  return 1
}

// Emoji ZWJ sequences (e.g. a family emoji) and base+modifier pairs (e.g. a
// thumbs-up with a skin tone) render as a SINGLE glyph, so measuring per code
// point over-counts them and drifts the following columns. Measure per grapheme
// cluster instead (Node >=20 ships Intl.Segmenter). A cluster is two cells if it
// contains any wide code point (CJK / emoji), otherwise the widest of its parts
// (1, or 0 for a lone combining / control cluster).
const graphemeSegmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

export function displayWidth(s: string): number {
  let w = 0
  for (const { segment } of graphemeSegmenter.segment(s)) {
    let clusterWidth = 0
    let emojiPresentation = false
    for (const ch of segment) {
      const cp = ch.codePointAt(0)!
      // A VS16 (U+FE0F) forces emoji presentation, which Telegram renders as a
      // two-cell glyph even for a base symbol (e.g. ⚠️, ❤️) that is otherwise
      // one cell. The selector itself is zero-width, so flag the whole cluster.
      if (cp === 0xfe0f) emojiPresentation = true
      const cw = charDisplayWidth(cp)
      if (cw > clusterWidth) clusterWidth = cw
    }
    w += emojiPresentation ? 2 : clusterWidth
  }
  return w
}

function padToWidth(s: string, target: number): string {
  return s + ' '.repeat(Math.max(0, target - displayWidth(s)))
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// Split a markdown table row into trimmed cells, dropping the outer pipes and
// honouring escaped pipes (`\|`) inside a cell.
function splitTableCells(line: string): string[] {
  let t = line.trim()
  if (t.startsWith('|')) t = t.slice(1)
  if (t.endsWith('|')) t = t.slice(0, -1)
  return t
    .replace(/\\\|/g, '\x00P\x00')
    .split('|')
    .map((c) => c.replace(/\x00P\x00/g, '|').trim())
}

// A markdown table separator row: pipe-delimited cells that are only dashes,
// colons and spaces (e.g. `|---|:--:|`). Must contain a pipe so a bare `---`
// horizontal rule is not mistaken for one.
function isTableSeparator(line: string): boolean {
  if (!line.includes('|')) return false
  const cells = splitTableCells(line)
  return cells.length > 0 && cells.every((c) => /^:?-{1,}:?$/.test(c))
}

// Per-column display widths for an aligned grid (min 3, matching the divider's
// minimum dash run). `ncols` is the widest row so ragged tables still align.
function columnWidths(rows: string[][], ncols: number): number[] {
  const widths: number[] = []
  for (let c = 0; c < ncols; c++) {
    let w = 3
    for (const row of rows) {
      w = Math.max(w, displayWidth(row[c] ?? ''))
    }
    widths[c] = w
  }
  return widths
}

// Total display width of the rendered horizontal grid: `| ` + cell + ` ` per
// column, plus the final closing `|`. Used to decide aligned-grid vs vertical.
function alignedGridWidth(rows: string[][], ncols: number): number {
  const widths = columnWidths(rows, ncols)
  return widths.reduce((a, w) => a + w, 0) + 3 * ncols + 1
}

// Render header + body rows as a uniform-width monospace grid (no <pre>
// wrapper -- the caller escapes + wraps it).
function renderAlignedGrid(rows: string[][], ncols: number): string {
  const padded = rows.map((r) => {
    const cells = r.slice()
    while (cells.length < ncols) cells.push('')
    return cells
  })
  const widths = columnWidths(padded, ncols)
  const renderRow = (cells: string[]): string =>
    '| ' + cells.map((c, k) => padToWidth(c, widths[k])).join(' | ') + ' |'

  const out: string[] = []
  out.push(renderRow(padded[0]))
  out.push('|' + widths.map((w) => '-'.repeat(w + 2)).join('|') + '|')
  for (let i = 1; i < padded.length; i++) out.push(renderRow(padded[i]))
  return out.join('\n')
}

// Unwrap complete bold markers in the LABEL only, so a label cell like `**A**`
// cannot collide with the converter's own `**label**` wrapper and leave stray
// asterisks. A bare `**` (e.g. `2 ** 3`) is left intact.
function cleanLabel(s: string): string {
  return s.replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1')
}

// Vertical fallback: 2-column rows become `**label**: value`; wider rows become
// a bold label with `• header: value` sub-bullets so the column each value
// belongs to stays clear on a phone.
function renderVerticalRows(header: string[], rows: string[][], out: string[]): void {
  for (const row of rows) {
    const label = cleanLabel(row[0] ?? '')
    const values = row.slice(1)
    if (values.length === 0) {
      out.push(`• ${label}`)
    } else if (values.length === 1) {
      out.push(`**${label}**: ${values[0]}`)
    } else {
      out.push(`**${label}**`)
      for (let c = 0; c < values.length; c++) {
        if (values[c] === '') continue // drop empty cells
        const head = header[c + 1]
        out.push(head ? `• ${head}: ${values[c]}` : `• ${values[c]}`)
      }
    }
  }
  out.push('') // blank line so the next block separates cleanly
}

// Rewrite every markdown table (header row + `|---|` separator + data rows).
//
// When `protect` is supplied (the formatForTelegram path), a table whose
// aligned grid fits TABLE_ALIGN_MAX_WIDTH is rendered as a CJK-aligned
// monospace <pre> block and handed to `protect` so it survives the later
// HTML-escape pass untouched; wider tables use the vertical fallback. When
// `protect` is null (the standalone convertMarkdownTables path) every table
// uses the vertical fallback, so the function stays pure markdown-in / -out.
function processTables(text: string, protect: ((html: string) => string) | null): string {
  const lines = text.split('\n')
  const out: string[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    const next = i + 1 < lines.length ? lines[i + 1] : undefined

    // A table is a row with a pipe immediately followed by a separator row
    // whose column count matches the header (a well-formed GFM table). A
    // column-count mismatch means it is not a real table -> leave it untouched.
    if (line.includes('|') && next !== undefined && isTableSeparator(next)) {
      const header = splitTableCells(line)
      const separator = splitTableCells(next)
      if (separator.length !== header.length) {
        out.push(line)
        i++
        continue
      }

      const rows: string[][] = []
      let j = i + 2 // skip header + separator
      while (j < lines.length && lines[j].includes('|') && lines[j].trim() !== '') {
        rows.push(splitTableCells(lines[j]))
        j++
      }

      // No data rows: not a usable table. Emit the header cells as readable
      // text (joined, no pipes) rather than silently dropping them OR leaking
      // raw markdown pipes to the user.
      if (rows.length === 0) {
        out.push(header.join(' · '))
        i += 2
        continue
      }

      const ncols = Math.max(header.length, ...rows.map((r) => r.length))
      if (protect && alignedGridWidth([header, ...rows], ncols) <= TABLE_ALIGN_MAX_WIDTH) {
        // Narrow enough: keep it a real, CJK-aligned table in a <pre> block.
        const grid = renderAlignedGrid([header, ...rows], ncols)
        out.push(protect(`<pre>${escapeHtml(grid)}</pre>`))
      } else {
        renderVerticalRows(header, rows, out)
      }
      i = j
      continue
    }

    out.push(line)
    i++
  }

  return out.join('\n')
}

// Standalone table converter: always the vertical `label: value` fallback (no
// <pre> alignment, since that is an HTML-output concern owned by
// formatForTelegram). Pure markdown in / markdown out.
export function convertMarkdownTables(text: string): string {
  return processTables(text, null)
}

export function formatForTelegram(text: string): string {
  // One protected-block store shared by fenced code, inline code, and aligned
  // <pre> tables. Each protected chunk is swapped for a sentinel before the
  // HTML-escape + markdown passes, then restored verbatim at the end.
  const protectedBlocks: string[] = []
  const protect = (html: string): string => {
    const idx = protectedBlocks.length
    protectedBlocks.push(html)
    return `\x00PB${idx}\x00`
  }

  // Protect fenced code blocks
  let result = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_match, lang, code) => {
    const escaped = escapeHtml(code.trimEnd())
    return protect(
      lang ? `<pre><code class="language-${lang}">${escaped}</code></pre>` : `<pre>${escaped}</pre>`
    )
  })

  // Convert markdown tables BEFORE protecting inline code. A narrow table
  // becomes an aligned <pre> block (protected); a wide table becomes a bullet
  // list whose cells still hold raw `inline code` for the pass below. Doing
  // this AFTER inline protection would nest an inline-code sentinel inside the
  // table's own sentinel, and the single-pass restore loop (low index -> high)
  // would never reach the inner one, leaking raw marker text to the user.
  result = processTables(result, protect)

  // Protect inline code (now also covers any `code` that sat in a table cell)
  result = result.replace(/`([^`]+)`/g, (_match, code) => protect(`<code>${escapeHtml(code)}</code>`))

  // Escape HTML in remaining text
  result = escapeHtml(result)

  // Headings
  result = result.replace(/^#{1,6}\s+(.+)$/gm, '<b>$1</b>')

  // Bold
  result = result.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
  result = result.replace(/__(.+?)__/g, '<b>$1</b>')

  // Italic
  result = result.replace(/\*(.+?)\*/g, '<i>$1</i>')
  result = result.replace(/_(.+?)_/g, '<i>$1</i>')

  // Strikethrough
  result = result.replace(/~~(.+?)~~/g, '<s>$1</s>')

  // Links
  result = result.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2">$1</a>')

  // Checkboxes
  result = result.replace(/- \[ \]/g, '☐')
  result = result.replace(/- \[x\]/g, '☑')

  // Strip horizontal rules
  result = result.replace(/^---+$/gm, '')
  result = result.replace(/^\*\*\*+$/gm, '')

  // Restore protected blocks (function replacer so `$` in the content is not
  // treated as a replacement pattern; each sentinel occurs exactly once).
  for (let i = 0; i < protectedBlocks.length; i++) {
    result = result.replace(`\x00PB${i}\x00`, () => protectedBlocks[i])
  }

  return result.trim()
}

// Sentinel the agent can emit to force a Telegram message break. Splits the
// outgoing reply into multiple Telegram messages at that point, regardless of
// length.
export const TELEGRAM_MESSAGE_BREAK = '__TELEGRAM_MESSAGE_BREAK__'

export function splitMessage(text: string, limit = MAX_MESSAGE_LENGTH): string[] {
  // First, honor explicit agent-requested breaks. Each segment is then further
  // split by length if it exceeds the Telegram limit.
  const segments = text
    .split(TELEGRAM_MESSAGE_BREAK)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)

  const chunks: string[] = []
  for (const segment of segments) {
    if (segment.length <= limit) {
      chunks.push(segment)
      continue
    }

    let remaining = segment
    while (remaining.length > 0) {
      if (remaining.length <= limit) {
        chunks.push(remaining)
        break
      }

      // Find last newline before limit
      let splitAt = remaining.lastIndexOf('\n', limit)
      if (splitAt <= 0) {
        // Find last space
        splitAt = remaining.lastIndexOf(' ', limit)
      }
      if (splitAt <= 0) {
        splitAt = limit
      }

      chunks.push(remaining.slice(0, splitAt))
      remaining = remaining.slice(splitAt).trimStart()
    }
  }

  return chunks.length > 0 ? chunks : [text]
}
