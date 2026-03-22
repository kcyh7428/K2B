import {
  searchMemoriesFts,
  getRecentMemories,
  touchMemory,
  insertMemory,
  decayAllMemories,
} from './db.js'
import { logger } from './logger.js'

const SEMANTIC_SIGNALS = /\b(my|i am|i'm|i prefer|remember|always|never|our|we have|my team)\b/i

export async function buildMemoryContext(
  chatId: string,
  userMessage: string
): Promise<string> {
  const ftsResults = searchMemoriesFts(chatId, userMessage, 3)
  const recentResults = getRecentMemories(chatId, 5)

  // Deduplicate by id
  const seen = new Set<number>()
  const combined: Array<{ id: number; content: string; sector: string }> = []

  for (const m of [...ftsResults, ...recentResults]) {
    if (!seen.has(m.id)) {
      seen.add(m.id)
      combined.push(m)
      touchMemory(m.id)
    }
  }

  if (combined.length === 0) return ''

  const lines = combined.map((m) => `- ${m.content} (${m.sector})`)
  return `[Memory context]\n${lines.join('\n')}\n\n`
}

export async function saveConversationTurn(
  chatId: string,
  userMsg: string,
  assistantMsg: string
): Promise<void> {
  // Skip short or command messages
  if (userMsg.length <= 20 || userMsg.startsWith('/')) return

  const sector = SEMANTIC_SIGNALS.test(userMsg) ? 'semantic' : 'episodic'

  // Save user message as memory
  insertMemory(chatId, userMsg, sector)

  // Save a condensed version of the assistant response if substantial
  if (assistantMsg && assistantMsg.length > 50) {
    const truncated =
      assistantMsg.length > 500
        ? assistantMsg.slice(0, 500) + '...'
        : assistantMsg
    insertMemory(chatId, truncated, 'episodic')
  }

  logger.debug({ chatId, sector }, 'Saved conversation turn to memory')
}

export function runDecaySweep(): void {
  logger.info('Running memory decay sweep')
  decayAllMemories()
}
