import { readFile } from 'fs/promises'
import { execSync } from 'child_process'
import { resolve } from 'path'
import { config } from './config.js'

export interface RecommendedVideo {
  ts: string
  video_id: string
  title: string
  channel: string
  duration: string
  playlist: string
  recommended_date: string
  status: string
  nudge_sent: boolean
  nudge_date: string
  outcome: string
  rating: string
  promoted_to: string
  vault_note: string
}

export interface WatchPipelineResult {
  pending: RecommendedVideo[]
  total: number
}

export async function getWatchPipeline(): Promise<WatchPipelineResult> {
  const filePath = resolve(config.vaultPath, 'Notes/Context/youtube-recommended.jsonl')
  try {
    const raw = await readFile(filePath, 'utf-8')
    const lines = raw.trim().split('\n').filter(Boolean)
    const all: RecommendedVideo[] = lines.map((line) => {
      try {
        return JSON.parse(line) as RecommendedVideo
      } catch {
        return null
      }
    }).filter((v): v is RecommendedVideo => v !== null)

    const pending = all.filter(
      (v) => v.status === 'recommended' || v.status === 'nudge_sent' || v.status === 'added'
    )

    return { pending, total: all.length }
  } catch {
    return { pending: [], total: 0 }
  }
}

// --- K2B Queue: live playlist state ---

export interface QueueVideo {
  videoId: string
  title: string
  duration: string
  channel: string
}

export interface ProcessedVideo {
  videoId: string
  date: string
  title: string
  notes: string
}

export interface QueuePipelineResult {
  current: QueueVideo[]
  recentlyProcessed: ProcessedVideo[]
  totalProcessed: number
}

// Cache live playlist data (refreshed every 5 minutes)
let queueCache: { videos: QueueVideo[], fetchedAt: number } = { videos: [], fetchedAt: 0 }
const QUEUE_CACHE_TTL = 60 * 60 * 1000 // 1 hour

const QUEUE_PLAYLIST_URL = 'https://www.youtube.com/playlist?list=PLg0PUkz5itjzmQhB2s49SLfmkR-2zntQD'

function fetchLiveQueuePlaylist(): QueueVideo[] {
  try {
    // Try Mac Mini first (yt-dlp works there), fall back to local
    const cmd = `ssh -o ConnectTimeout=3 macmini "yt-dlp --flat-playlist --print '%(id)s|%(title)s|%(duration_string)s|%(channel)s' '${QUEUE_PLAYLIST_URL}'" 2>/dev/null`
    const output = execSync(cmd, { timeout: 15000 }).toString().trim()
    if (!output) return []

    return output.split('\n').filter(Boolean).map((line) => {
      const [videoId, title, duration, channel] = line.split('|')
      return { videoId, title: title || '', duration: duration || '', channel: channel || '' }
    })
  } catch {
    try {
      // Fallback: local yt-dlp
      const output = execSync(
        `yt-dlp --flat-playlist --print '%(id)s|%(title)s|%(duration_string)s|%(channel)s' '${QUEUE_PLAYLIST_URL}' 2>/dev/null`,
        { timeout: 15000 }
      ).toString().trim()
      if (!output) return []
      return output.split('\n').filter(Boolean).map((line) => {
        const [videoId, title, duration, channel] = line.split('|')
        return { videoId, title: title || '', duration: duration || '', channel: channel || '' }
      })
    } catch {
      return []
    }
  }
}

function getLiveQueue(): QueueVideo[] {
  const now = Date.now()
  if (now - queueCache.fetchedAt < QUEUE_CACHE_TTL) {
    return queueCache.videos
  }
  const videos = fetchLiveQueuePlaylist()
  queueCache = { videos, fetchedAt: now }
  return videos
}

async function getProcessedHistory(days: number): Promise<{ recent: ProcessedVideo[], total: number }> {
  const filePath = resolve(config.vaultPath, 'Notes/Context/youtube-processed.md')
  try {
    const raw = await readFile(filePath, 'utf-8')
    const lines = raw.split('\n')

    const cutoff = new Date()
    cutoff.setDate(cutoff.getDate() - days)

    const recent: ProcessedVideo[] = []
    let total = 0

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#') || trimmed.includes('Format:')) continue

      const parts = trimmed.split('|').map((p) => p.trim())
      if (parts.length < 4) continue

      const [videoId, date, playlist, title, ...rest] = parts
      if (!videoId || !date || playlist !== 'K2B Queue') continue

      total++

      const entryDate = new Date(date)
      if (isNaN(entryDate.getTime()) || entryDate < cutoff) continue

      recent.push({
        videoId,
        date,
        title: title || '',
        notes: rest.join('|').trim(),
      })
    }

    return { recent, total }
  } catch {
    return { recent: [], total: 0 }
  }
}

export async function getQueuePipeline(days?: number): Promise<QueuePipelineResult> {
  const [current, processed] = await Promise.all([
    Promise.resolve(getLiveQueue()),
    getProcessedHistory(days || 7),
  ])

  return {
    current,
    recentlyProcessed: processed.recent,
    totalProcessed: processed.total,
  }
}
