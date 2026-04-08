import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { K2B_VAULT_PATH } from './config.js'
import { logger } from './logger.js'

export interface ChannelStats {
  watched: number
  skipped: number
  screened: number
  lastAction: string
}

export interface ActionEntry {
  ts: string
  videoId: string
  channel: string
  action: 'watch' | 'skip' | 'screen' | 'comment'
  skipReason?: string
}

interface TasteModelData {
  version: 1
  lastUpdated: string
  channels: Record<string, ChannelStats>
  topicFreshness: Record<string, number>
  skipReasons: string[]
  recentActions: ActionEntry[]
}

const DEFAULT_TOPIC_FRESHNESS: Record<string, number> = {
  'claude-code': 30,
  'ai-tools': 45,
  'agent-frameworks': 45,
  'obsidian': 90,
  'recruitment': 180,
  'investment': 180,
  'leadership': 365,
  'default': 90,
}

const MAX_RECENT_ACTIONS = 50
const MAX_SKIP_REASONS = 20

export class TasteModel {
  private data: TasteModelData
  private filePath: string

  constructor() {
    this.filePath = resolve(K2B_VAULT_PATH, 'wiki/context/youtube-taste-model.json')

    if (existsSync(this.filePath)) {
      const raw = readFileSync(this.filePath, 'utf-8')
      this.data = JSON.parse(raw) as TasteModelData
      const channelCount = Object.keys(this.data.channels).length
      logger.info(`TasteModel loaded: ${channelCount} channels, ${this.data.recentActions.length} recent actions`)
    } else {
      this.data = {
        version: 1,
        lastUpdated: new Date().toISOString(),
        channels: {},
        topicFreshness: { ...DEFAULT_TOPIC_FRESHNESS },
        skipReasons: [],
        recentActions: [],
      }
      logger.info('TasteModel created (empty)')
      this.save()

      // Auto-backfill from existing feedback signals
      const signalsPath = resolve(K2B_VAULT_PATH, 'wiki/context/youtube-feedback-signals.jsonl')
      this.backfillFromSignals(signalsPath)
    }
  }

  recordAction(
    videoId: string,
    channel: string,
    action: 'watch' | 'skip' | 'screen' | 'comment',
    skipReason?: string,
  ): void {
    // Update channel stats
    if (!this.data.channels[channel]) {
      this.data.channels[channel] = { watched: 0, skipped: 0, screened: 0, lastAction: '' }
    }
    const stats = this.data.channels[channel]
    if (action === 'watch') stats.watched++
    else if (action === 'skip') stats.skipped++
    else if (action === 'screen') stats.screened++
    // comment doesn't increment any counter
    stats.lastAction = new Date().toISOString()

    // Append to recent actions ring buffer
    const entry: ActionEntry = {
      ts: new Date().toISOString(),
      videoId,
      channel,
      action,
    }
    if (skipReason) entry.skipReason = skipReason
    this.data.recentActions.push(entry)
    if (this.data.recentActions.length > MAX_RECENT_ACTIONS) {
      this.data.recentActions = this.data.recentActions.slice(-MAX_RECENT_ACTIONS)
    }

    // Track skip reasons
    if (skipReason) {
      this.data.skipReasons.push(skipReason)
      if (this.data.skipReasons.length > MAX_SKIP_REASONS) {
        this.data.skipReasons = this.data.skipReasons.slice(-MAX_SKIP_REASONS)
      }
    }

    this.save()
  }

  getChannelAffinity(channel: string): number {
    const stats = this.data.channels[channel]
    if (!stats) return 0
    const total = stats.watched + stats.skipped + stats.screened
    if (total === 0) return 0
    return ((stats.watched + 0.5 * stats.screened) - stats.skipped) / total
  }

  isChannelFlagged(channel: string): boolean {
    const stats = this.data.channels[channel]
    if (!stats) return false
    const total = stats.watched + stats.skipped + stats.screened
    if (stats.skipped < 3) return false
    const positiveRate = (stats.watched + stats.screened) / total
    return positiveRate < 0.3
  }

  isVideoStale(uploadDate: string, topics: string[]): boolean {
    const ageDays = this.getAgeDays(uploadDate)
    if (ageDays < 0) return false

    const freshness = this.data.topicFreshness
    let minWindow = freshness['default'] ?? DEFAULT_TOPIC_FRESHNESS['default']

    for (const topic of topics) {
      const window = freshness[topic] ?? DEFAULT_TOPIC_FRESHNESS[topic] ?? minWindow
      if (window < minWindow) minWindow = window
    }

    return ageDays > minWindow
  }

  deduceSkipReason(channel: string, uploadDate: string, topics: string[]): string {
    // Channel has >60% skip rate with 3+ total
    const stats = this.data.channels[channel]
    if (stats) {
      const total = stats.watched + stats.skipped + stats.screened
      if (total >= 3 && stats.skipped / total > 0.6) {
        return `Not a fan of ${channel}?`
      }
    }

    // Video is stale
    if (this.isVideoStale(uploadDate, topics)) {
      return 'Too old for this topic?'
    }

    // Channel is new
    if (!stats) {
      return 'New channel -- not what you expected?'
    }

    return 'Not relevant right now?'
  }

  scoreCandidate(
    channel: string,
    topics: string[],
    uploadDate: string,
    durationMinutes: number,
  ): number {
    // Channel score (0-40)
    let channelScore: number
    if (this.isChannelFlagged(channel)) {
      channelScore = 0
    } else if (!this.data.channels[channel]) {
      channelScore = 20
    } else {
      const affinity = this.getChannelAffinity(channel)
      channelScore = affinity > 0 ? 20 + affinity * 20 : 20 + affinity * 20
    }

    // Freshness score (0-30)
    const ageDays = this.getAgeDays(uploadDate)
    let freshnessScore = 15  // default middle score for unknown/invalid dates
    if (ageDays >= 0) {
      const freshness = this.data.topicFreshness
      let minWindow = freshness['default'] ?? DEFAULT_TOPIC_FRESHNESS['default']
      for (const topic of topics) {
        const window = freshness[topic] ?? DEFAULT_TOPIC_FRESHNESS[topic] ?? minWindow
        if (window < minWindow) minWindow = window
      }
      if (ageDays <= minWindow) {
        freshnessScore = 30
      } else if (ageDays <= minWindow * 2) {
        freshnessScore = 15
      } else {
        freshnessScore = 0
      }
    }

    // Duration score (0-15)
    let durationScore: number
    if (durationMinutes >= 10 && durationMinutes <= 25) {
      durationScore = 15
    } else if (durationMinutes > 25 && durationMinutes <= 45) {
      durationScore = 10
    } else {
      durationScore = 5
    }

    // Topic score (0-15)
    let topicScore = 5
    const recentWatches = this.data.recentActions.filter(a => a.action === 'watch')
    if (topics.length > 0 && recentWatches.length > 0) {
      // Check if any topic appears in recent watch channels' patterns
      // Simple heuristic: if the channel was recently watched, topic is relevant
      const recentChannels = new Set(recentWatches.map(a => a.channel))
      if (recentChannels.has(channel)) {
        topicScore = 15
      }
    }

    return Math.round(channelScore + freshnessScore + durationScore + topicScore)
  }

  toSummary(): string {
    const channelCount = Object.keys(this.data.channels).length
    const actionCount = this.data.recentActions.length

    const lines: string[] = [
      `YouTube Taste Model (${channelCount} channels tracked, ${actionCount} recent actions)`,
      '',
    ]

    // Top channels by affinity
    const sorted = Object.entries(this.data.channels)
      .map(([name, stats]) => ({ name, affinity: this.getChannelAffinity(name), stats }))
      .sort((a, b) => b.affinity - a.affinity)

    const top = sorted.slice(0, 5)
    if (top.length > 0) {
      lines.push('Top channels: ' + top.map(c =>
        `${c.name} (${c.stats.watched}w/${c.stats.skipped}s/${c.stats.screened}sc, aff=${c.affinity.toFixed(2)})`
      ).join(', '))
    } else {
      lines.push('Top channels: none yet')
    }

    // Flagged channels
    const flagged = Object.keys(this.data.channels).filter(ch => this.isChannelFlagged(ch))
    lines.push('Flagged channels: ' + (flagged.length > 0 ? flagged.join(', ') : 'none'))

    // Recent skip reasons
    const recentSkips = this.data.skipReasons.slice(-5)
    lines.push('Recent skip reasons: ' + (recentSkips.length > 0 ? recentSkips.join('; ') : 'none'))

    // Topic freshness
    const tf = this.data.topicFreshness
    const topicParts = Object.entries(tf).map(([t, d]) => `${t} (${d}d)`)
    lines.push('Topic freshness: ' + topicParts.join(', '))

    return lines.join('\n')
  }

  backfillFromSignals(signalsPath: string): void {
    if (this.data.recentActions.length > 0) return
    if (!existsSync(signalsPath)) {
      logger.info('TasteModel backfill: no signals file found')
      return
    }

    const raw = readFileSync(signalsPath, 'utf-8')
    const lines = raw.trim().split('\n').filter(l => l.length > 0)
    let count = 0
    const processedVideos = new Set<string>()

    for (const line of lines) {
      try {
        const signal = JSON.parse(line)
        const videoId = signal.video_id || ''
        const channel = signal.channel || ''
        if (!videoId || !channel) continue
        // Only count the first action per video to avoid double-counting
        if (processedVideos.has(videoId)) continue
        processedVideos.add(videoId)
        let action: 'watch' | 'skip' | 'screen' | 'comment'
        let skipReason: string | undefined

        switch (signal.signal_type) {
          case 'watch':
            action = 'watch'
            break
          case 'skip_reason':
            action = 'skip'
            skipReason = signal.signal_text
            break
          case 'screen':
            action = 'screen'
            break
          case 'comment':
            action = 'comment'
            break
          default:
            continue
        }

        this.recordAction(videoId, channel, action, skipReason)
        count++
      } catch {
        // skip malformed lines
      }
    }

    logger.info(`TasteModel backfilled ${count} actions from signals`)
  }

  private save(): void {
    this.data.lastUpdated = new Date().toISOString()
    writeFileSync(this.filePath, JSON.stringify(this.data, null, 2), 'utf-8')
  }

  private getAgeDays(uploadDate: string): number {
    // Handle YYYYMMDD (yt-dlp) or YYYY-MM-DD
    let parsed: Date
    if (uploadDate.length === 8 && !uploadDate.includes('-')) {
      const y = uploadDate.slice(0, 4)
      const m = uploadDate.slice(4, 6)
      const d = uploadDate.slice(6, 8)
      parsed = new Date(`${y}-${m}-${d}`)
    } else {
      parsed = new Date(uploadDate)
    }
    if (isNaN(parsed.getTime())) return -1
    const now = new Date()
    return Math.floor((now.getTime() - parsed.getTime()) / (1000 * 60 * 60 * 24))
  }
}

// Singleton instance -- shared across bot.ts, youtube-agent-loop.ts, etc.
export const tasteModel = new TasteModel()

export default TasteModel
