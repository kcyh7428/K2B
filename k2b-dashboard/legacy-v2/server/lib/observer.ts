import { readFile } from 'fs/promises'
import { resolve } from 'path'
import { config } from './config.js'

export interface ObserverPattern {
  confidence: string
  description: string
  recommendation: string
}

export interface ObserverCandidate {
  confidence: string
  category: string
  description: string
  evidence: string
}

export interface ObserverData {
  lastAnalysis: string
  observationsAnalyzed: number
  summary: string
  patterns: ObserverPattern[]
  candidates: ObserverCandidate[]
  currentObservationCount: number
}

export async function getObserverData(): Promise<ObserverData> {
  const result: ObserverData = {
    lastAnalysis: '',
    observationsAnalyzed: 0,
    summary: '',
    patterns: [],
    candidates: [],
    currentObservationCount: 0,
  }

  // Count current observations
  try {
    const obsPath = resolve(config.vaultPath, 'Notes/Context/observations.jsonl')
    const obsRaw = await readFile(obsPath, 'utf-8')
    result.currentObservationCount = obsRaw.trim().split('\n').filter(Boolean).length
  } catch {
    // file missing, count stays 0
  }

  // Parse observer-candidates.md
  try {
    const candidatesPath = resolve(config.vaultPath, 'Notes/Context/observer-candidates.md')
    const raw = await readFile(candidatesPath, 'utf-8')
    const lines = raw.split('\n')

    let currentSection = ''
    const summaryLines: string[] = []

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]
      const trimmed = line.trim()

      // Metadata lines
      const lastAnalysisMatch = trimmed.match(/^Last analysis:\s*(.+)/)
      if (lastAnalysisMatch) {
        result.lastAnalysis = lastAnalysisMatch[1].trim()
        continue
      }

      const obsAnalyzedMatch = trimmed.match(/^Observations analyzed:\s*(\d+)/)
      if (obsAnalyzedMatch) {
        result.observationsAnalyzed = parseInt(obsAnalyzedMatch[1], 10)
        continue
      }

      // Section headers
      if (trimmed.startsWith('## ') || trimmed.startsWith('# ')) {
        const heading = trimmed.replace(/^#+\s*/, '').toLowerCase()
        if (heading.includes('summary')) {
          currentSection = 'summary'
        } else if (heading.includes('detected patterns') || heading.includes('patterns')) {
          currentSection = 'patterns'
        } else if (heading.includes('candidate learnings') || heading.includes('candidates')) {
          currentSection = 'candidates'
        } else {
          currentSection = ''
        }
        continue
      }

      // Collect content by section
      if (currentSection === 'summary' && trimmed) {
        summaryLines.push(trimmed)
      }

      if (currentSection === 'patterns' && trimmed.startsWith('- [')) {
        const patternMatch = trimmed.match(/^- \[(\w+)]\s*(.+)/)
        if (patternMatch) {
          const confidence = patternMatch[1]
          const rest = patternMatch[2]
          // Split on " -- " or ". Recommendation:" or similar
          const recSplit = rest.split(/\s*--\s*Recommendation:\s*|\s*\.\s*Recommendation:\s*/i)
          result.patterns.push({
            confidence,
            description: recSplit[0]?.trim() || rest,
            recommendation: recSplit[1]?.trim() || '',
          })
        }
      }

      if (currentSection === 'candidates' && trimmed.startsWith('- [')) {
        const candidateMatch = trimmed.match(/^- \[(\w+)]\s*\(([^)]+)\)\s*(.+)/)
        if (candidateMatch) {
          const confidence = candidateMatch[1]
          const category = candidateMatch[2]
          const rest = candidateMatch[3]
          const evidenceSplit = rest.split(/\s*Evidence:\s*/i)
          result.candidates.push({
            confidence,
            category,
            description: evidenceSplit[0]?.trim() || rest,
            evidence: evidenceSplit[1]?.trim() || '',
          })
        }
      }
    }

    result.summary = summaryLines.join(' ')
  } catch {
    // file missing, defaults are fine
  }

  return result
}
