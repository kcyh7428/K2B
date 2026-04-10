import { existsSync, statSync, openSync, readSync, closeSync } from 'node:fs'

// Read the last N lines of a file efficiently by reading from the end in chunks.
// Used for jsonl streams that grow append-only and may be large.
export function tailLines(filePath: string, maxLines: number): string[] {
  if (!existsSync(filePath)) return []

  const stat = statSync(filePath)
  if (stat.size === 0) return []

  const fd = openSync(filePath, 'r')
  try {
    const chunkSize = 8192
    let buf = Buffer.alloc(0)
    let position = stat.size
    let lines: string[] = []

    while (position > 0 && lines.length <= maxLines) {
      const readSize = Math.min(chunkSize, position)
      position -= readSize
      const chunk = Buffer.alloc(readSize)
      readSync(fd, chunk, 0, readSize, position)
      buf = Buffer.concat([chunk, buf])
      lines = buf.toString('utf-8').split('\n')
    }

    // Drop the empty trailing element if present
    while (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    return lines.slice(-maxLines)
  } finally {
    closeSync(fd)
  }
}

export function parseJsonlTail<T = unknown>(filePath: string, maxLines: number): T[] {
  const lines = tailLines(filePath, maxLines)
  const out: T[] = []
  for (const line of lines) {
    try {
      out.push(JSON.parse(line) as T)
    } catch {
      // skip malformed lines silently -- jsonl streams sometimes get truncated mid-write
    }
  }
  return out
}

export function countLines(filePath: string): number {
  if (!existsSync(filePath)) return 0
  const stat = statSync(filePath)
  if (stat.size === 0) return 0
  // Cheap-enough for our scale (a few MB worst case)
  const fd = openSync(filePath, 'r')
  try {
    const buf = Buffer.alloc(stat.size)
    readSync(fd, buf, 0, stat.size, 0)
    let count = 0
    for (let i = 0; i < buf.length; i++) {
      if (buf[i] === 0x0a) count++
    }
    return count
  } finally {
    closeSync(fd)
  }
}
