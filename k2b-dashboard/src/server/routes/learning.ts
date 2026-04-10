import { Router } from 'express'
import { existsSync, readFileSync, statSync } from 'node:fs'
import { paths } from '../lib/vault-paths.js'
import { parseJsonlTail, countLines, tailLines } from '../lib/parse-jsonl.js'
import { readNote } from '../lib/parse-frontmatter.js'

const router = Router()

interface SignalEntry {
  ts?: string
  source: string
  text: string
  raw?: unknown
}

// Combined signal feed: observations + preference signals + recent skill usage + recent errors
router.get('/signals', (_req, res) => {
  const signals: SignalEntry[] = []

  // observations.jsonl
  const obs = parseJsonlTail<Record<string, unknown>>(paths.observations, 100)
  for (const o of obs) {
    signals.push({
      ts: (o.ts as string) ?? (o.timestamp as string),
      source: 'observations',
      text: typeof o === 'object' ? JSON.stringify(o).slice(0, 240) : String(o),
      raw: o,
    })
  }

  // preference-signals.jsonl
  const prefs = parseJsonlTail<Record<string, unknown>>(paths.preferenceSignals, 100)
  for (const p of prefs) {
    signals.push({
      ts: (p.ts as string) ?? (p.timestamp as string),
      source: 'preference-signals',
      text: typeof p === 'object' ? JSON.stringify(p).slice(0, 240) : String(p),
      raw: p,
    })
  }

  // skill-usage-log.tsv (tab-separated: date \t skill \t hash \t description)
  if (existsSync(paths.skillUsageLog)) {
    const lines = tailLines(paths.skillUsageLog, 100)
    for (const line of lines) {
      const parts = line.split('\t')
      if (parts.length >= 2) {
        signals.push({
          ts: parts[0],
          source: 'skill-usage',
          text: `${parts[1]}: ${parts.slice(3).join(' ')}`,
          raw: { date: parts[0], skill: parts[1], hash: parts[2], note: parts.slice(3).join(' ') },
        })
      }
    }
  }

  // errors (markdown bullets, surface the most recent)
  if (existsSync(paths.errors)) {
    const errText = readFileSync(paths.errors, 'utf-8')
    const errLines = errText.split('\n').filter((l) => l.match(/^- E-\d{4}-\d{2}-\d{2}/)).slice(-20)
    for (const line of errLines) {
      const dateMatch = line.match(/E-(\d{4}-\d{2}-\d{2})/)
      signals.push({
        ts: dateMatch ? dateMatch[1] : undefined,
        source: 'errors',
        text: line.replace(/^- /, '').slice(0, 240),
        raw: { line },
      })
    }
  }

  // Sort by timestamp desc (best effort -- mixed formats)
  signals.sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''))

  res.json({
    items: signals.slice(0, 200),
    counts: {
      observations: countLines(paths.observations),
      preferenceSignals: countLines(paths.preferenceSignals),
      skillUsage: countLines(paths.skillUsageLog),
      errors: existsSync(paths.errors) ? readFileSync(paths.errors, 'utf-8').split('\n').filter((l) => l.match(/^- E-/)).length : 0,
    },
  })
})

// Observer candidates (markdown, no IDs in Ship 1)
router.get('/candidates', (_req, res) => {
  const note = readNote(paths.observerCandidates)
  if (!note) return res.json({ candidates: [], lastUpdated: null, raw: '' })

  // Parse markdown bullet items as candidates (best-effort, no schema in Ship 1)
  const candidates: { text: string; line: number }[] = []
  const lines = note.content.split('\n')
  lines.forEach((line, idx) => {
    const trimmed = line.trim()
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
      candidates.push({ text: trimmed.replace(/^[-*]\s*/, ''), line: idx + 1 })
    }
  })

  res.json({
    candidates,
    lastUpdated: statSync(paths.observerCandidates).mtimeMs,
    raw: note.content,
  })
})

// Active rules + learnings + preference profile
router.get('/rules', (_req, res) => {
  const rulesNote = readNote(paths.activeRules)
  const learningsNote = readNote(paths.learnings)
  const profileNote = readNote(paths.preferenceProfile)

  const parseRules = (content: string) => {
    // Each rule starts with a numbered heading: "1. **Rule name.**"
    const rules: { id: number; text: string }[] = []
    const re = /^(\d+)\.\s+(.+?)(?=^\d+\.\s+|\Z)/gms
    let m: RegExpExecArray | null
    while ((m = re.exec(content + '\n0. ')) !== null) {
      if (m[1] === '0') break
      rules.push({ id: parseInt(m[1], 10), text: m[2].trim() })
    }
    return rules
  }

  const parseLearnings = (content: string) => {
    const items: { id: string; text: string }[] = []
    const re = /^##\s+(L-\d{4}-\d{2}-\d{2}-\d+)\s*(.*?)$([\s\S]*?)(?=^##\s+L-|\Z)/gms
    let m: RegExpExecArray | null
    while ((m = re.exec(content)) !== null) {
      items.push({
        id: m[1],
        text: (m[2] + '\n' + m[3]).trim().slice(0, 600),
      })
    }
    return items
  }

  res.json({
    rules: rulesNote ? parseRules(rulesNote.content) : [],
    learnings: learningsNote ? parseLearnings(learningsNote.content).slice(0, 50) : [],
    profile: profileNote
      ? {
          updated: statSync(paths.preferenceProfile).mtimeMs,
          content: profileNote.content.slice(0, 4000),
        }
      : null,
    lastUpdated: rulesNote ? statSync(paths.activeRules).mtimeMs : null,
  })
})

// Observer runs (the streaming audit -- prompt + response per run)
router.get('/runs', (_req, res) => {
  const runs = parseJsonlTail<Record<string, unknown>>(paths.observerRuns, 20)
  res.json({
    runs,
    count: countLines(paths.observerRuns),
    exists: existsSync(paths.observerRuns),
  })
})

// Aggregate summary for the LearningPanel headline numbers
router.get('/summary', (_req, res) => {
  const obsCount = countLines(paths.observations)
  const prefCount = countLines(paths.preferenceSignals)
  const skillCount = countLines(paths.skillUsageLog)
  const last24hCutoff = Date.now() - 24 * 60 * 60 * 1000

  // Best-effort 24h count from observer runs
  const recentRuns = parseJsonlTail<{ ts?: string; candidates?: number }>(paths.observerRuns, 50)
  const recentInWindow = recentRuns.filter((r) => {
    if (!r.ts) return false
    return new Date(r.ts).getTime() >= last24hCutoff
  })
  const last = recentRuns[recentRuns.length - 1]

  const candidatesNote = readNote(paths.observerCandidates)
  const candidatesCount = candidatesNote
    ? candidatesNote.content.split('\n').filter((l) => l.trim().match(/^[-*]\s/)).length
    : 0

  res.json({
    signals24h: obsCount + prefCount + skillCount, // total accumulated; 24h delta would need timestamp filtering per source
    candidatesPending: candidatesCount,
    rulesChanged7d: 0, // Ship 2 will populate from provenance
    observerLastRun: last?.ts ?? null,
    observerRunsInLast24h: recentInWindow.length,
  })
})

export default router
