import { existsSync } from 'node:fs'
import { runAgent } from './agent.js'
import { transcribeAudio, voiceCapabilities } from './voice.js'
import { logger } from './logger.js'

// Intake messages from non-Telegram sources (currently: k2b-dashboard).
// The contract mirrors how bot.ts hands off to runAgent: build a text message,
// route through the same agent loop, return the result.

export interface IntakePayload {
  type: 'url' | 'text' | 'audio' | 'fireflies' | 'feedback'
  payload?: string
  filePath?: string
  source?: string
  feedbackType?: 'learn' | 'error' | 'request'
}

export interface IntakeResult {
  status: 'ok' | 'error'
  text?: string | null
  hadError?: boolean
  error?: string
  echoedMessage?: string
}

function buildMessage(body: IntakePayload, transcribed?: string): string {
  const source = body.source ?? 'dashboard'
  const tag = `[Source: ${source}]`

  switch (body.type) {
    case 'url': {
      // The skills already detect URL types -- just hand them the URL.
      return `${tag} Process this URL: ${body.payload ?? ''}`
    }
    case 'text': {
      return `${tag} ${body.payload ?? ''}`
    }
    case 'audio': {
      // Mirror bot.ts:858 -- "[Voice transcribed]: ..." prefix is what the
      // skills already recognise from Telegram voice notes.
      return `${tag} [Voice transcribed]: ${transcribed ?? ''}`
    }
    case 'fireflies': {
      // Hand off to the meeting processor with the URL or ID payload.
      return `${tag} Process this Fireflies meeting: ${body.payload ?? ''}`
    }
    case 'feedback': {
      const slash = body.feedbackType ?? 'learn'
      return `${tag} /${slash} ${body.payload ?? ''}`
    }
  }
}

export async function handleIntake(body: IntakePayload): Promise<IntakeResult> {
  if (!body.type) {
    return { status: 'error', error: 'missing type' }
  }

  let transcribed: string | undefined

  // Audio: replicate bot.ts:836-858 -- transcribe first, then run the agent on text.
  if (body.type === 'audio') {
    if (!body.filePath) {
      return { status: 'error', error: 'audio intake missing filePath' }
    }
    if (!existsSync(body.filePath)) {
      return { status: 'error', error: `file not found: ${body.filePath}` }
    }
    const caps = voiceCapabilities()
    if (!caps.stt) {
      return { status: 'error', error: 'GROQ_API_KEY not set, cannot transcribe' }
    }
    try {
      transcribed = await transcribeAudio(body.filePath)
      logger.info({ chars: transcribed.length, source: body.source }, 'Intake audio transcribed')
    } catch (err) {
      logger.error({ err }, 'Intake transcribe failed')
      return { status: 'error', error: `transcription failed: ${(err as Error).message}` }
    }
  }

  const message = buildMessage(body, transcribed)
  logger.info({ type: body.type, source: body.source, length: message.length }, 'Intake -> runAgent')

  try {
    const { text, hadError } = await runAgent(message)
    return {
      status: hadError ? 'error' : 'ok',
      text,
      hadError,
      echoedMessage: message.slice(0, 200),
    }
  } catch (err) {
    logger.error({ err }, 'Intake runAgent failed')
    return { status: 'error', error: (err as Error).message }
  }
}
