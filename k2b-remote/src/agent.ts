import { query, createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk'
import { z } from 'zod'
import { K2B_PROJECT_ROOT, TYPING_REFRESH_MS, HTTP_PROXY } from './config.js'
import { logger } from './logger.js'
import {
  guardedAddVideoToWatch,
  guardedSkipVideoFromWatch,
  guardedKeepVideo,
  guardedSwapAll,
  getYtState,
  getYtPendingCandidates,
  readRecommendations,
  type VideoMetadata,
} from './youtube.js'

// ---------------------------------------------------------------------------
// YouTube MCP tools (module singleton)
// ---------------------------------------------------------------------------
//
// State lives in SQLite (`youtube_agent_state` table). The agent reads it only
// when it judges the current message might be about videos -- via
// `youtube_get_pending`. That keeps YouTube state out of the persisted
// session transcript (state is tool output, not prompt history).
//
// Every mutation tool goes through the `guarded*` wrappers in youtube.ts,
// which acquire `ytMutex` and re-check pendingVideoIds inside the lock. If a
// button click has already cleared the video, the tool returns
// `already_handled` and the agent reports that to Keith instead of double-
// adding or throwing.
//
// Tools are intentionally minimal: add_to_watch, skip, keep, swap_all, and a
// read-only get_pending. Anything more nuanced stays in the conversational
// layer -- the agent decides what to say.

function textResult(text: string) {
  return {
    content: [{ type: 'text' as const, text }],
  }
}

function errorResult(text: string) {
  return {
    content: [{ type: 'text' as const, text }],
    isError: true,
  }
}

const youtubeGetPending = tool(
  'youtube_get_pending',
  'Get the current YouTube video candidates Keith is reviewing (if any). Call this ONLY when Keith\'s message sounds like it might be about pending video recommendations (e.g. "add both", "skip the first one", "not interested"). Returns the current phase and a list of pending videos with title/channel/duration/publish date. Returns `phase: idle` and an empty list when no videos are pending -- in that case, treat Keith\'s message as an ordinary conversation.',
  {},
  async () => {
    const state = getYtState()
    if (state.phase === 'idle' || state.pendingVideoIds.length === 0) {
      return textResult(JSON.stringify({ phase: 'idle', pending: [] }))
    }
    const candidates = getYtPendingCandidates()
    const recs = readRecommendations()
    const pending = state.pendingVideoIds.map(videoId => {
      const cand = candidates.get(videoId)
      const rec = recs.find(r => r.video_id === videoId)
      return {
        videoId,
        title: cand?.title ?? rec?.title ?? videoId,
        channel: cand?.channel ?? rec?.channel ?? 'unknown',
        duration: cand?.duration ?? rec?.duration ?? '',
        uploadDate: cand?.uploadDate ?? rec?.upload_date ?? '',
        verdict: cand?.verdict ?? rec?.verdict_value ?? '',
        reason: cand?.reason ?? rec?.verdict ?? '',
      }
    })
    return textResult(
      JSON.stringify({
        phase: state.phase,
        pending,
      })
    )
  }
)

const youtubeAddToWatch = tool(
  'youtube_add_to_watch',
  'Add a pending YouTube video to Keith\'s Watch list. Only call this after Keith explicitly says to add or keep THIS specific video. The videoId MUST come from a prior `youtube_get_pending` call -- do NOT guess. Returns `already_handled` if a button click or other action cleared the video first (in that case, tell Keith the video was already handled; do not retry).',
  {
    videoId: z.string().describe('The YouTube video ID from youtube_get_pending.pending[].videoId'),
  },
  async ({ videoId }) => {
    const candidates = getYtPendingCandidates()
    const cand = candidates.get(videoId)
    if (!cand || !cand.title || cand.title === videoId || !cand.channel || cand.channel === 'unknown') {
      logger.warn({ videoId, hasCached: !!cand }, 'youtube_add_to_watch: metadata missing')
      return errorResult(
        `Cannot add ${videoId}: metadata unavailable. Ask Keith to paste the URL again so K2B can refetch.`
      )
    }
    const meta: VideoMetadata = {
      videoId,
      title: cand.title,
      channel: cand.channel,
      duration: cand.duration || undefined,
      uploadDate: cand.uploadDate || undefined,
      verdict: cand.reason || undefined,
      verdictValue: (cand.verdict as 'HIGH' | 'MEDIUM' | 'LOW' | undefined) || undefined,
    }
    const result = await guardedAddVideoToWatch(meta, 'agent-tool:add_to_watch')
    return textResult(JSON.stringify(result))
  }
)

const youtubeSkip = tool(
  'youtube_skip',
  'Skip a pending YouTube video (remove from Watch if present, train the taste model). Call this when Keith explicitly rejects THIS specific video. The videoId MUST come from a prior `youtube_get_pending` call. Returns `already_handled` if the video was already cleared.',
  {
    videoId: z.string().describe('The YouTube video ID from youtube_get_pending.pending[].videoId'),
    reason: z.string().describe('Keith\'s stated reason for skipping, in his own words (e.g. "too basic", "outdated", "wrong topic")'),
  },
  async ({ videoId, reason }) => {
    const result = await guardedSkipVideoFromWatch(videoId, reason, 'agent-tool:skip')
    return textResult(JSON.stringify(result))
  }
)

const youtubeKeep = tool(
  'youtube_keep',
  'Mark a pending YouTube video as "leave it alone" -- clears it from Keith\'s pending review without adding or removing anything from playlists. Call this when Keith says to keep a video as-is (e.g. "leave the Claude one"). Returns `already_handled` if the video was already cleared.',
  {
    videoId: z.string().describe('The YouTube video ID from youtube_get_pending.pending[].videoId'),
  },
  async ({ videoId }) => {
    const result = await guardedKeepVideo(videoId)
    return textResult(JSON.stringify(result))
  }
)

const youtubeSwapAll = tool(
  'youtube_swap_all',
  'Skip ALL currently pending videos at once. Call this when Keith says something like "swap them all" or "none of these". The skip reason is applied to every video. After this runs, Keith\'s pending review is empty and the agent loop will find fresh content on its next cycle.',
  {
    reason: z.string().describe('Keith\'s stated reason for swapping all (e.g. "not the right week", "none of these land")'),
  },
  async ({ reason }) => {
    const result = await guardedSwapAll(reason, 'agent-tool:swap_all')
    return textResult(JSON.stringify(result))
  }
)

// Module singleton -- one MCP server instance reused across every runAgent()
// call. Codex flagged that constructing a fresh server per call would be both
// wasteful and could race on tool registration.
const k2bYoutubeToolsServer = createSdkMcpServer({
  name: 'k2b-youtube',
  version: '1.0.0',
  tools: [
    youtubeGetPending,
    youtubeAddToWatch,
    youtubeSkip,
    youtubeKeep,
    youtubeSwapAll,
  ],
})

// ---------------------------------------------------------------------------
// runAgent: persistent interactive session (one per chat)
// ---------------------------------------------------------------------------

export async function runAgent(
  message: string,
  sessionId?: string,
  onTyping?: () => void
): Promise<{ text: string | null; newSessionId?: string; hadError?: boolean }> {
  let responseText: string | null = null
  let newSessionId: string | undefined
  let hadError = false

  let typingInterval: ReturnType<typeof setInterval> | undefined
  if (onTyping) {
    typingInterval = setInterval(onTyping, TYPING_REFRESH_MS)
  }

  try {
    const options: Parameters<typeof query>[0] = {
      prompt: message,
      options: {
        cwd: K2B_PROJECT_ROOT,
        permissionMode: 'bypassPermissions' as const,
        settingSources: ['project', 'user'] as const,
        mcpServers: { 'k2b-youtube': k2bYoutubeToolsServer },
        ...(sessionId ? { resume: sessionId } : {}),
        ...(HTTP_PROXY ? {
          env: {
            ...process.env,
            HTTPS_PROXY: HTTP_PROXY,
            HTTP_PROXY: HTTP_PROXY,
            NO_PROXY: 'localhost,127.0.0.1',
          },
        } : {}),
      },
    }

    logger.info({ sessionId, messageLength: message.length }, 'Running agent')

    for await (const event of query(options)) {
      logger.info({ eventType: event.type, event: JSON.stringify(event).slice(0, 500) }, 'Agent event')

      if (event.type === 'system' && 'subtype' in event && event.subtype === 'init') {
        newSessionId = (event as Record<string, unknown>).session_id as string | undefined
        logger.info({ newSessionId }, 'Session initialized')
      }

      if (event.type === 'result') {
        const resultEvent = event as Record<string, unknown>
        responseText = (resultEvent.result as string) ?? null
        if (resultEvent.is_error) {
          hadError = true
        }
      }
    }

    logger.info({ hasResponse: !!responseText, responseLength: responseText?.length }, 'Agent finished')
  } catch (err) {
    logger.error({ err }, 'Agent error')
    hadError = true
    if (!responseText) {
      responseText = 'Something went wrong processing that request. Try again or /newchat to start fresh.'
    }
  } finally {
    if (typingInterval) clearInterval(typingInterval)
  }

  return { text: responseText, newSessionId, hadError }
}

// ---------------------------------------------------------------------------
// runStatelessQuery: ephemeral one-shot call for background classifiers
// ---------------------------------------------------------------------------
//
// Used by youtube-agent-loop.ts for check-in copy, screening JSON, and any
// other "analyze this text and return a string" call. `persistSession: false`
// (verified real in runtimeTypes.d.ts:360) guarantees the call does NOT write
// a new session file to `~/.claude/projects/`, so background loops cannot
// pollute disk or accumulate orphan sessions over time. No MCP tools are
// registered -- classifiers shouldn't touch YouTube state, only return text.

export async function runStatelessQuery(prompt: string): Promise<string | null> {
  let responseText: string | null = null
  try {
    const options: Parameters<typeof query>[0] = {
      prompt,
      options: {
        cwd: K2B_PROJECT_ROOT,
        permissionMode: 'bypassPermissions' as const,
        settingSources: ['project', 'user'] as const,
        persistSession: false,
        mcpServers: {},
        ...(HTTP_PROXY ? {
          env: {
            ...process.env,
            HTTPS_PROXY: HTTP_PROXY,
            HTTP_PROXY: HTTP_PROXY,
            NO_PROXY: 'localhost,127.0.0.1',
          },
        } : {}),
      },
    }

    logger.info({ promptLength: prompt.length }, 'Running stateless query')

    for await (const event of query(options)) {
      if (event.type === 'result') {
        const resultEvent = event as Record<string, unknown>
        responseText = (resultEvent.result as string) ?? null
      }
    }
  } catch (err) {
    logger.error({ err }, 'runStatelessQuery error')
    return null
  }

  return responseText
}
