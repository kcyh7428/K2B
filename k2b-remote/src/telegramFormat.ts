import { MAX_MESSAGE_LENGTH } from './config.js'

// --- Telegram formatting ---
//
// Telegram renders no markdown tables. Anything the agent emits as a
// `| col | col |` / `|---|---|` block arrives on a phone as unreadable pipe
// noise. The `k2b-remote/CLAUDE.md` "NO tables" prose rule did not hold (the
// agent produced one anyway), so the guarantee lives here as code: every
// markdown table is rewritten into a bullet list BEFORE it is sent.

export function formatForTelegram(text: string): string {
  // Extract and protect code blocks
  const codeBlocks: string[] = []
  let result = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_match, lang, code) => {
    const idx = codeBlocks.length
    const escaped = escapeHtml(code.trimEnd())
    codeBlocks.push(lang ? `<pre><code class="language-${lang}">${escaped}</code></pre>` : `<pre>${escaped}</pre>`)
    return `\x00CB${idx}\x00`
  })

  // Protect inline code
  const inlineCodes: string[] = []
  result = result.replace(/`([^`]+)`/g, (_match, code) => {
    const idx = inlineCodes.length
    inlineCodes.push(`<code>${escapeHtml(code)}</code>`)
    return `\x00IC${idx}\x00`
  })

  // Convert markdown tables to bullet lists (code/inline already protected).
  result = convertMarkdownTables(result)

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

  // Restore code blocks and inline code
  for (let i = 0; i < codeBlocks.length; i++) {
    result = result.replace(`\x00CB${i}\x00`, codeBlocks[i])
  }
  for (let i = 0; i < inlineCodes.length; i++) {
    result = result.replace(`\x00IC${i}\x00`, inlineCodes[i])
  }

  return result.trim()
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

// Rewrite every markdown table (header row + `|---|` separator + data rows)
// into a bullet list. 2-column rows become `**label**: value`; wider rows
// become a bold label with `• header: value` sub-bullets so the column each
// value belongs to stays clear on a phone. Non-table text is returned as-is.
export function convertMarkdownTables(text: string): string {
  const lines = text.split('\n')
  const out: string[] = []
  let i = 0

  // Unwrap complete bold markers in the LABEL only, so a label cell like
  // `**A**` cannot collide with the converter's own `**label**` wrapper and
  // leave stray asterisks. Only complete `**...**` / `__...__` wrappers are
  // unwrapped (inner text kept) -- a bare `**` (e.g. `2 ** 3`) is left intact.
  // Value/header cells are NOT cleaned: they are not wrapped by the converter,
  // so the downstream bold regex renders any intentional inline bold correctly.
  const cleanLabel = (s: string): string =>
    s.replace(/\*\*(.+?)\*\*/g, '$1').replace(/__(.+?)__/g, '$1')

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
      i = j
      continue
    }

    out.push(line)
    i++
  }

  return out.join('\n')
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
