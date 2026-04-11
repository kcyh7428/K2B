import { readFileSync, appendFileSync, writeFileSync, existsSync } from 'node:fs'
import { execSync } from 'node:child_process'
import { resolve } from 'node:path'
import { K2B_PROJECT_ROOT } from './config.js'
import { tasteModel } from './taste-model.js'
import { logger } from './logger.js'

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

/** Returns null on API error (skip verification), or string[] of video IDs on success (even if empty). */
export function getPlaylistVideoIds(playlistId: string): string[] | null {
  try {
    const output = execSync(
      `yt-dlp --flat-playlist --print "%(id)s" "https://www.youtube.com/playlist?list=${playlistId}" 2>/dev/null`,
      { encoding: 'utf-8', timeout: 30_000 }
    ).trim()
    if (!output) return []
    return output.split('\n').filter(Boolean)
  } catch {
    return null  // API error -- caller should skip verification
  }
}

export function getRecommendationsByStatus(status: string): YouTubeRecommendation[] {
  return readRecommendations().filter(r => r.status === status)
}

export function getScreenPending(): YouTubeRecommendation[] {
  return readRecommendations().filter(r => r.status === 'screen_pending')
}

// ---------------------------------------------------------------------------
// Canonical state functions
// ---------------------------------------------------------------------------
//
// Every YouTube video state change (add to Watch, skip, move to Screen, etc.)
// MUST go through these functions. Previously mutations were scattered across
// bot.ts and youtube-agent-loop.ts, each with slightly different semantics,
// which caused a regression loop (title=videoId, channel=unknown, missing
// taste-model updates, partial writes, etc.). Consolidating them kills the
// duplication.
//
// Invariant: runAgent() prompts never mutate state. All mutations happen here.

export interface VideoMetadata {
  videoId: string
  title: string          // must not be empty or equal videoId
  channel: string        // must not be empty or 'unknown'
  duration?: string
  uploadDate?: string    // YYYYMMDD
  verdict?: string
  verdictValue?: 'HIGH' | 'MEDIUM' | 'LOW'
  topics?: string[]
}

/** In-memory agent-state shared between bot.ts and youtube-agent-loop.ts.
 *  Owns pendingVideoIds (what cards are waiting for Keith) and the phase machine. */
export interface YouTubeAgentState {
  phase: 'idle' | 'checking-watch' | 'presenting-picks' | 'searching'
  pendingVideoIds: string[]
  sessionId?: string
  startedAt: string
  lastCycleAt: string | null
  cyclesToday: number
  lastCycleDate: string | null
}

export const youtubeAgentState: YouTubeAgentState = {
  phase: 'idle',
  pendingVideoIds: [],
  startedAt: '',
  lastCycleAt: null,
  cyclesToday: 0,
  lastCycleDate: null,
}

/** Cached metadata for candidates not yet persisted to JSONL (new-picks + direct URL).
 *  Populated by findNewContent and handleDirectYouTubeUrl; consumed by canonical functions. */
export interface PendingCandidate {
  videoId: string
  title: string
  channel: string
  duration: string
  uploadDate: string
  verdict: string
  reason: string
}
export const pendingCandidates = new Map<string, PendingCandidate>()

class MetadataValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'MetadataValidationError'
  }
}

function assertValidMetadata(meta: VideoMetadata): void {
  if (!meta.videoId) throw new MetadataValidationError('videoId is required')
  if (!meta.title || meta.title === meta.videoId) {
    throw new MetadataValidationError(`title is missing or equals videoId for ${meta.videoId}`)
  }
  if (!meta.channel || meta.channel === 'unknown') {
    throw new MetadataValidationError(`channel is missing or 'unknown' for ${meta.videoId}`)
  }
}

/** Clears a video from all in-memory agent state (pendingVideoIds + pendingCandidates).
 *  Safe to call even if the video is not tracked. Resets phase to 'idle' if no
 *  pending videos remain. */
export function clearFromAgentState(videoId: string): void {
  const idx = youtubeAgentState.pendingVideoIds.indexOf(videoId)
  if (idx >= 0) youtubeAgentState.pendingVideoIds.splice(idx, 1)
  pendingCandidates.delete(videoId)
  if (youtubeAgentState.pendingVideoIds.length === 0 && youtubeAgentState.phase !== 'searching') {
    youtubeAgentState.phase = 'idle'
  }
}

/** Canonical "add a video to the Watch list" path. Used by every add trigger:
 *  button:agent-add, text-reply add-all, text-reply parseAndExecute, direct URL flow. */
export function addVideoToWatch(meta: VideoMetadata, source: string): void {
  assertValidMetadata(meta)

  // Playlist op first -- if this fails, don't write JSONL
  playlistAdd(WATCH_PLAYLIST_ID, meta.videoId)

  const today = new Date().toISOString().slice(0, 10)
  // Does a rec already exist? Agent loop 'presenting-picks' phase has no JSONL row;
  // direct URL handler never wrote one either. Avoid duplicates if the caller
  // invokes this for a pre-existing rec.
  const existing = readRecommendations().find(r => r.video_id === meta.videoId)
  if (existing) {
    updateRecommendation(meta.videoId, {
      status: 'nudge_sent',
      nudge_sent: true,
      nudge_date: today,
      outcome: source,
      // Refresh metadata in case the existing row was placeholder
      ...(meta.title && meta.title !== meta.videoId ? { title: meta.title } : {}),
      ...(meta.channel && meta.channel !== 'unknown' ? { channel: meta.channel } : {}),
      ...(meta.duration ? { duration: meta.duration } : {}),
      ...(meta.uploadDate ? { upload_date: meta.uploadDate } : {}),
      ...(meta.verdict ? { verdict: meta.verdict } : {}),
      ...(meta.verdictValue ? { verdict_value: meta.verdictValue } : {}),
      ...(meta.topics ? { topics: meta.topics } : {}),
    })
  } else {
    appendRecommendation({
      ts: new Date().toISOString(),
      video_id: meta.videoId,
      title: meta.title,
      channel: meta.channel,
      playlist: 'K2B Watch',
      recommended_date: today,
      status: 'nudge_sent',
      nudge_sent: true,
      nudge_date: today,
      outcome: source,
      rating: null,
      promoted_to: null,
      vault_note: null,
      duration: meta.duration,
      upload_date: meta.uploadDate,
      verdict: meta.verdict,
      verdict_value: meta.verdictValue,
      topics: meta.topics,
    })
  }

  clearFromAgentState(meta.videoId)
  logger.info({ videoId: meta.videoId, source }, 'Canonical add-to-Watch completed')
}

/** Canonical "skip a video from Watch" path. Writes JSONL, feedback signal,
 *  taste model, playlist removal -- all together. */
export function skipVideoFromWatch(
  videoId: string,
  reason: string,
  source: string,
  userText?: string,
): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  const channel = rec?.channel ?? ''

  // Playlist removal -- tolerate failure so we always update tracking
  try {
    playlistRemove(WATCH_PLAYLIST_ID, videoId)
  } catch (err) {
    logger.error({ err, videoId }, 'skipVideoFromWatch: playlistRemove failed (continuing)')
  }

  if (rec) {
    updateRecommendation(videoId, {
      status: 'skipped',
      skip_reason: reason,
      outcome: source,
      ...(userText ? { feedback_text: userText } : {}),
    })
  }

  appendFeedbackSignal(videoId, 'skip_reason', reason, userText)

  if (channel && channel !== 'unknown') {
    tasteModel.recordAction(videoId, channel, 'skip', reason)
  }

  clearFromAgentState(videoId)
  logger.info({ videoId, source, reason }, 'Canonical skip-from-Watch completed')
}

/** Canonical "mark as watched" path. Does NOT remove from Watch playlist --
 *  Keith may re-open. */
export function markVideoWatched(videoId: string, source: string): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  const channel = rec?.channel ?? ''

  if (rec) {
    updateRecommendation(videoId, {
      status: 'watched',
      outcome: source,
    })
  }

  appendFeedbackSignal(videoId, 'watch', 'watched')

  if (channel && channel !== 'unknown') {
    tasteModel.recordAction(videoId, channel, 'watch')
  }

  clearFromAgentState(videoId)
  logger.info({ videoId, source }, 'Canonical mark-watched completed')
}

/** Canonical "move from Watch to Screen" path. */
export function moveVideoToScreen(videoId: string, source: string): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  const channel = rec?.channel ?? ''

  // Both playlist ops. If either fails we still update tracking so the JSONL
  // reflects Keith's intent; a later verify cycle will reconcile.
  try {
    playlistAdd(SCREEN_PLAYLIST_ID, videoId)
  } catch (err) {
    logger.error({ err, videoId }, 'moveVideoToScreen: playlistAdd(Screen) failed (continuing)')
  }
  try {
    playlistRemove(WATCH_PLAYLIST_ID, videoId)
  } catch (err) {
    logger.error({ err, videoId }, 'moveVideoToScreen: playlistRemove(Watch) failed (continuing)')
  }

  if (rec) {
    updateRecommendation(videoId, {
      status: 'screen_pending',
      outcome: source,
    })
  }

  appendFeedbackSignal(videoId, 'screen', 'screened-for-processing')

  if (channel && channel !== 'unknown') {
    tasteModel.recordAction(videoId, channel, 'screen')
  }

  clearFromAgentState(videoId)
  logger.info({ videoId, source }, 'Canonical move-to-Screen completed')
}

/** Canonical "skip from Screen" path. Parallel to skipVideoFromWatch but
 *  removes from the Screen playlist and (previously) skipped training the
 *  taste model -- now fixed. */
export function skipVideoFromScreen(videoId: string, reason: string, source: string): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  const channel = rec?.channel ?? ''

  try {
    playlistRemove(SCREEN_PLAYLIST_ID, videoId)
  } catch (err) {
    logger.error({ err, videoId }, 'skipVideoFromScreen: playlistRemove failed (continuing)')
  }

  if (rec) {
    updateRecommendation(videoId, {
      status: 'skipped',
      skip_reason: reason,
      outcome: source,
    })
  }

  appendFeedbackSignal(videoId, 'skip_reason', reason)

  if (channel && channel !== 'unknown') {
    tasteModel.recordAction(videoId, channel, 'skip', reason)
  }

  clearFromAgentState(videoId)
  logger.info({ videoId, source, reason }, 'Canonical skip-from-Screen completed')
}

/** Agent-loop-only path: mark a video as expired because it's no longer in
 *  the Watch playlist (Keith removed it via the YouTube app, etc.). This is
 *  NOT a user decision so we don't train the taste model. */
export function expireVideoFromWatch(videoId: string, source: string): void {
  const rec = readRecommendations().find(r => r.video_id === videoId)
  if (rec) {
    updateRecommendation(videoId, {
      status: 'expired',
      outcome: source,
    })
  }
  clearFromAgentState(videoId)
  logger.info({ videoId, source }, 'Canonical expire-from-Watch completed')
}
