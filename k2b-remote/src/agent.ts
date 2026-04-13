import { query } from '@anthropic-ai/claude-agent-sdk'
import { K2B_PROJECT_ROOT, TYPING_REFRESH_MS, HTTP_PROXY } from './config.js'
import { logger } from './logger.js'

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
