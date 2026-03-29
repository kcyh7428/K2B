import { readFileSync, appendFileSync, writeFileSync, existsSync } from 'node:fs'

const VAULT = process.env.K2B_VAULT ?? '/Users/fastshower/Projects/K2B-Vault'
const RECOMMENDED_FILE = `${VAULT}/Notes/Context/youtube-recommended.jsonl`

export interface YouTubeRecommendation {
  ts: string
  video_id: string
  title: string
  channel: string
  playlist: string
  recommended_date: string
  status: 'pending' | 'nudge_sent' | 'watched' | 'highlights_sent' | 'skipped' | 'expired' | 'processed'
  nudge_sent: boolean
  nudge_date: string | null
  outcome: string | null
  rating: string | null
  promoted_to: string | null
  vault_note: string | null
}

export function readRecommendations(): YouTubeRecommendation[] {
  if (!existsSync(RECOMMENDED_FILE)) return []
  const lines = readFileSync(RECOMMENDED_FILE, 'utf-8').trim().split('\n').filter(Boolean)
  return lines.map(line => JSON.parse(line) as YouTubeRecommendation)
}

export function appendRecommendation(rec: YouTubeRecommendation): void {
  appendFileSync(RECOMMENDED_FILE, JSON.stringify(rec) + '\n')
}

export function updateRecommendation(videoId: string, updates: Partial<YouTubeRecommendation>): void {
  const all = readRecommendations()
  const updated = all.map(rec =>
    rec.video_id === videoId ? { ...rec, ...updates } : rec
  )
  writeFileSync(RECOMMENDED_FILE, updated.map(r => JSON.stringify(r)).join('\n') + '\n')
}

export function isAlreadyRecommended(videoId: string): boolean {
  return readRecommendations().some(r => r.video_id === videoId)
}

export function getPendingNudges(): YouTubeRecommendation[] {
  return readRecommendations().filter(r => r.status === 'nudge_sent')
}
