import { readFileSync } from 'fs'
import { resolve, basename } from 'path'
import matter from 'gray-matter'
import { glob } from 'glob'
import { config } from './config.js'

export interface VaultFile {
  filename: string
  data: Record<string, unknown>
  content: string
  excerpt: string
}

function extractExcerpt(content: string): string {
  const trimmed = content.trim()
  if (!trimmed) return ''
  const firstParagraph = trimmed.split(/\n\s*\n/)[0]
  return firstParagraph?.trim() || ''
}

export function readVaultFile(filePath: string): VaultFile {
  const fullPath = resolve(config.vaultPath, filePath)
  const raw = readFileSync(fullPath, 'utf-8')
  const { data, content } = matter(raw)
  return {
    filename: basename(filePath, '.md'),
    data,
    content,
    excerpt: extractExcerpt(content),
  }
}

export async function listVaultFolder(folderPath: string): Promise<VaultFile[]> {
  const fullFolder = resolve(config.vaultPath, folderPath)
  const files = await glob('*.md', { cwd: fullFolder })

  const results: VaultFile[] = []
  for (const file of files) {
    try {
      const relativePath = `${folderPath}/${file}`
      results.push(readVaultFile(relativePath))
    } catch {
      // skip unreadable files
    }
  }

  results.sort((a, b) => {
    const dateA = a.data.date ? new Date(a.data.date as string).getTime() : 0
    const dateB = b.data.date ? new Date(b.data.date as string).getTime() : 0
    return dateB - dateA
  })

  return results
}

export interface VaultCounts {
  total: number
  daily: number
  people: number
  projects: number
  features: number
  insights: number
  contentIdeas: number
  work: number
  reference: number
  context: number
}

async function countMdFiles(folderPath: string): Promise<number> {
  const fullFolder = resolve(config.vaultPath, folderPath)
  try {
    const files = await glob('*.md', { cwd: fullFolder })
    return files.length
  } catch {
    return 0
  }
}

export async function getVaultCounts(): Promise<VaultCounts> {
  const [daily, people, projects, features, insights, contentIdeas, work, reference, context] =
    await Promise.all([
      countMdFiles('Daily'),
      countMdFiles('Notes/People'),
      countMdFiles('Notes/Projects'),
      countMdFiles('Notes/Features'),
      countMdFiles('Notes/Insights'),
      countMdFiles('Notes/Content-Ideas'),
      countMdFiles('Notes/Work'),
      countMdFiles('Notes/Reference'),
      countMdFiles('Notes/Context'),
    ])

  const allFiles = await glob('**/*.md', { cwd: config.vaultPath })
  const total = allFiles.length

  return { total, daily, people, projects, features, insights, contentIdeas, work, reference, context }
}
