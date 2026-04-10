import { readFileSync } from 'fs'
import { config } from './config.js'

export interface SkillUsage {
  skill: string
  count: number
  lastUsed: string
}

interface UsageRecord {
  date: string
  skill: string
  session: string
  notes: string
}

function parseUsageLog(): UsageRecord[] {
  try {
    const raw = readFileSync(config.usageLogPath, 'utf-8')
    const lines = raw.trim().split('\n').filter(Boolean)

    return lines.map((line) => {
      const [date, skill, session, ...rest] = line.split('\t')
      return {
        date: date || '',
        skill: skill || '',
        session: session || '',
        notes: rest.join('\t'),
      }
    })
  } catch {
    return []
  }
}

function aggregate(records: UsageRecord[]): SkillUsage[] {
  const map = new Map<string, { count: number; lastUsed: string }>()

  for (const rec of records) {
    const existing = map.get(rec.skill)
    if (existing) {
      existing.count++
      if (rec.date > existing.lastUsed) {
        existing.lastUsed = rec.date
      }
    } else {
      map.set(rec.skill, { count: 1, lastUsed: rec.date })
    }
  }

  const result: SkillUsage[] = []
  for (const [skill, { count, lastUsed }] of map) {
    result.push({ skill, count, lastUsed })
  }

  result.sort((a, b) => b.count - a.count)
  return result
}

export function getSkillUsage(days: number = 7): SkillUsage[] {
  const records = parseUsageLog()
  const cutoff = new Date()
  cutoff.setDate(cutoff.getDate() - days)
  const cutoffStr = cutoff.toISOString().slice(0, 10)

  const filtered = records.filter((r) => r.date >= cutoffStr)
  return aggregate(filtered)
}

export function getAllTimeUsage(): SkillUsage[] {
  const records = parseUsageLog()
  return aggregate(records)
}
