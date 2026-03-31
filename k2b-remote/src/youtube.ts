import { readFileSync, appendFileSync, writeFileSync, existsSync } from 'node:fs'

const VAULT = process.env.K2B_VAULT ?? '/Users/fastshower/Projects/K2B-Vault'
const RECOMMENDED_FILE = `${VAULT}/Notes/Context/youtube-recommended.jsonl`
const FEEDBACK_SIGNALS_FILE = `${VAULT}/Notes/Context/youtube-feedback-signals.jsonl`

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
  topics?: string[]
  skip_reason?: string
  value_signal?: string
  search_query?: string
}

export interface FeedbackSignal {
  ts: string
  video_id: string
  channel: string
  title: string
  signal_type: 'skip_reason' | 'value_feedback' | 'promotion' | 'expiry'
  signal: string
  topics: string[]
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

export function appendFeedbackSignal(
  videoId: string,
  signalType: FeedbackSignal['signal_type'],
  signal: string
): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  const entry: FeedbackSignal = {
    ts: new Date().toISOString(),
    video_id: videoId,
    channel: rec?.channel ?? 'unknown',
    title: rec?.title ?? 'unknown',
    signal_type: signalType,
    signal,
    topics: rec?.topics ?? [],
  }
  appendFileSync(FEEDBACK_SIGNALS_FILE, JSON.stringify(entry) + '\n')
}
