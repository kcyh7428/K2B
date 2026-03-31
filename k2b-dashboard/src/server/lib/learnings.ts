import { readFile } from 'fs/promises'
import { config } from './config.js'

export interface Learning {
  id: string
  area: string
  learning: string
  reinforced: number
  confidence: string
  date: string
}

export async function getRecentLearnings(limit?: number): Promise<Learning[]> {
  const n = limit || 5
  try {
    const raw = await readFile(config.learningsPath, 'utf-8')
    const entries = raw.split(/(?=^### L-)/m).filter((block) => block.trim().startsWith('### L-'))

    const learnings: Learning[] = entries.map((block) => {
      const lines = block.trim().split('\n')
      const idLine = lines[0] || ''
      const id = idLine.replace(/^###\s*/, '').trim()

      // Extract date from ID: L-YYYY-MM-DD-NNN
      const dateMatch = id.match(/L-(\d{4}-\d{2}-\d{2})/)
      const date = dateMatch ? dateMatch[1] : ''

      let area = ''
      let learning = ''
      let reinforced = 0
      let confidence = ''

      for (const line of lines) {
        const trimmed = line.trim()
        const areaMatch = trimmed.match(/^-?\s*\*\*Area:\*\*\s*(.+)/)
        if (areaMatch) area = areaMatch[1].trim()

        const learningMatch = trimmed.match(/^-?\s*\*\*Learning:\*\*\s*(.+)/)
        if (learningMatch) learning = learningMatch[1].trim()

        const reinforcedMatch = trimmed.match(/^-?\s*\*\*Reinforced:\*\*\s*(\d+)/)
        if (reinforcedMatch) reinforced = parseInt(reinforcedMatch[1], 10)

        const confidenceMatch = trimmed.match(/^-?\s*\*\*Confidence:\*\*\s*(.+)/)
        if (confidenceMatch) confidence = confidenceMatch[1].trim()
      }

      return { id, area, learning, reinforced, confidence, date }
    })

    // Sort by date descending
    learnings.sort((a, b) => b.date.localeCompare(a.date))

    return learnings.slice(0, n)
  } catch {
    return []
  }
}
