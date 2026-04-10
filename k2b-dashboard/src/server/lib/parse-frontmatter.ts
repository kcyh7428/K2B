import { readFileSync, existsSync, statSync } from 'node:fs'
import matter from 'gray-matter'

export interface VaultNote {
  filename: string
  path: string
  mtime: number
  data: Record<string, unknown>
  content: string
}

export function readNote(filePath: string): VaultNote | null {
  if (!existsSync(filePath)) return null
  try {
    const raw = readFileSync(filePath, 'utf-8')
    const parsed = matter(raw)
    const stat = statSync(filePath)
    return {
      filename: filePath.split('/').pop() ?? filePath,
      path: filePath,
      mtime: stat.mtimeMs,
      data: parsed.data as Record<string, unknown>,
      content: parsed.content,
    }
  } catch {
    return null
  }
}
