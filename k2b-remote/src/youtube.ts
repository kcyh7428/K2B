import { readFileSync, appendFileSync, writeFileSync, existsSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { resolve } from 'node:path'
import { K2B_PROJECT_ROOT } from './config.js'

const VAULT = process.env.K2B_VAULT ?? '/Users/fastshower/Projects/K2B-Vault'
const RECOMMENDED_FILE = `${VAULT}/wiki/context/youtube-recommended.jsonl`
const FEEDBACK_SIGNALS_FILE = `${VAULT}/wiki/context/youtube-feedback-signals.jsonl`

// Playlist IDs (from youtube-playlists.md)
export const WATCH_PLAYLIST_ID = 'PLg0PUkz5itjwIXWVuSlvxud0ZR2JBsacX'
export const SCREEN_PLAYLIST_ID = 'PLg0PUkz5itjzmQhB2s49SLfmkR-2zntQD'

const SCRIPTS_DIR = resolve(K2B_PROJECT_ROOT, 'scripts')

export function playlistAdd(playlistId: string, videoId: string): string {
  return execSync(
    `"${SCRIPTS_DIR}/yt-playlist-add.sh" "${playlistId}" "${videoId}"`,
    { encoding: 'utf-8', timeout: 30_000 }
  ).trim()
}

export function playlistRemove(playlistId: string, videoId: string): string {
  return execSync(
    `"${SCRIPTS_DIR}/yt-playlist-remove.sh" "${playlistId}" "${videoId}"`,
    { encoding: 'utf-8', timeout: 30_000 }
  ).trim()
}

export interface YouTubeRecommendation {
  ts: string
  video_id: string
  title: string
  channel: string
  playlist: string
  recommended_date: string
  status: 'pending' | 'nudge_sent' | 'watched' | 'highlights_sent' | 'skipped' | 'expired' | 'processed' | 'screen_pending'
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
  pick_reason?: string
  duration?: string
  feedback_text?: string
  verdict?: string                              // 3-5 sentence screening verdict from Pass 2
  verdict_value?: 'HIGH' | 'MEDIUM' | 'LOW'    // estimated value from transcript screening
  pillars_matched?: string[]                     // matched content pillars
  comment_text?: string                          // Keith's comment (text or transcribed voice)
  upload_date?: string                            // actual video publish date (YYYYMMDD or YYYY-MM-DD)
}

export interface FeedbackSignal {
  ts: string
  video_id: string
  channel: string
  title: string
  signal_type: 'skip_reason' | 'value_feedback' | 'promotion' | 'expiry' | 'comment' | 'screen' | 'watch'
  signal: string
  signal_text?: string
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
  signal: string,
  signalText?: string
): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  const entry: FeedbackSignal = {
    ts: new Date().toISOString(),
    video_id: videoId,
    channel: rec?.channel ?? 'unknown',
    title: rec?.title ?? 'unknown',
    signal_type: signalType,
    signal,
    ...(signalText ? { signal_text: signalText } : {}),
    topics: rec?.topics ?? [],
  }
  appendFileSync(FEEDBACK_SIGNALS_FILE, JSON.stringify(entry) + '\n')
}

export function getRecommendationsByStatus(status: string): YouTubeRecommendation[] {
  return readRecommendations().filter(r => r.status === status)
}

export function getScreenPending(): YouTubeRecommendation[] {
  return readRecommendations().filter(r => r.status === 'screen_pending')
}
