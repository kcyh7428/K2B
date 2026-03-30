import { execSync } from 'node:child_process'
import { appendFileSync, writeFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { K2B_VAULT_PATH } from './config.js'
import { logger } from './logger.js'

const MARKER_PATH = '/tmp/k2b-last-observe-remote'
const OBS_FILE = resolve(K2B_VAULT_PATH, 'Notes/Context/observations.jsonl')

// Skill detection from file path (mirrors stop-observe.sh logic)
function detectSkillFromPath(relpath: string): string {
  if (/^Inbox\/content_/.test(relpath)) return 'k2b-insight-extractor'
  if (/^Inbox\/.*tldr/.test(relpath)) return 'k2b-tldr'
  if (/^Inbox\/.*(youtube|video)/.test(relpath)) return 'k2b-youtube-capture'
  if (/^Daily\//.test(relpath)) return 'k2b-daily-capture'
  if (/^Notes\/People\//.test(relpath)) return 'k2b-vault-writer'
  if (/^Notes\/Projects\//.test(relpath)) return 'k2b-vault-writer'
  if (/^Notes\/Content-Ideas\//.test(relpath)) return 'k2b-inbox'
  if (/^Archive\//.test(relpath)) return 'k2b-inbox'
  if (/^Notes\/Context\/preference-/.test(relpath)) return 'k2b-observer'
  return 'unknown'
}

// Fallback skill detection from prompt text
function detectSkillFromPrompt(prompt: string): string {
  const lower = prompt.toLowerCase()
  if (/\/youtube|youtube morning/.test(lower)) return 'k2b-youtube-capture'
  if (/\/daily|start the day|end of day/.test(lower)) return 'k2b-daily-capture'
  if (/\/inbox|process inbox/.test(lower)) return 'k2b-inbox'
  if (/\/tldr|summarize this/.test(lower)) return 'k2b-tldr'
  if (/\/linkedin/.test(lower)) return 'k2b-linkedin'
  if (/\/meeting/.test(lower)) return 'k2b-meeting-processor'
  if (/\/research/.test(lower)) return 'k2b-research'
  if (/\/insight|\/content/.test(lower)) return 'k2b-insight-extractor'
  if (/\/observe/.test(lower)) return 'k2b-observer'
  if (/\/email/.test(lower)) return 'k2b-email'
  if (/\/media/.test(lower)) return 'k2b-media-generator'
  return 'unknown'
}

function detectAction(relpath: string): string {
  if (/^Archive\//.test(relpath)) return 'archive'
  if (/^Notes\/Content-Ideas\//.test(relpath)) return 'promote'
  return 'modify'
}

/**
 * Touch a marker file before an agent run.
 * Returns the marker path for use with logObservations().
 */
export function markObservationStart(): string {
  try {
    writeFileSync(MARKER_PATH, '', { flag: 'w' })
  } catch {
    // If we can't write the marker, observations won't be logged -- that's OK
  }
  return MARKER_PATH
}

/**
 * Scan vault for files changed since the marker and append observations.
 * Call this after runAgent() completes.
 */
export function logObservations(
  markerPath: string,
  sessionId: string,
  prompt: string
): void {
  try {
    if (!existsSync(markerPath)) return

    // Find vault .md files newer than marker (same approach as stop-observe.sh)
    const result = execSync(
      `find "${K2B_VAULT_PATH}" -name "*.md" -newer "${markerPath}" -type f 2>/dev/null | head -20`,
      { encoding: 'utf8', timeout: 5000 }
    ).trim()

    if (!result) return

    const ts = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
    const promptSkill = detectSkillFromPrompt(prompt)
    const lines: string[] = []

    for (const filepath of result.split('\n')) {
      if (!filepath) continue
      const relpath = filepath.replace(K2B_VAULT_PATH + '/', '')

      // Skip non-vault paths and context files that change frequently
      if (relpath.startsWith('.') || relpath === 'Notes/Context/observations.jsonl') continue

      const skill = detectSkillFromPath(relpath) !== 'unknown'
        ? detectSkillFromPath(relpath)
        : promptSkill
      const action = detectAction(relpath)

      const obs = JSON.stringify({ ts, session: sessionId, skill, action, file: relpath, source: 'telegram' })
      lines.push(obs)
    }

    if (lines.length > 0) {
      appendFileSync(OBS_FILE, lines.join('\n') + '\n')
      logger.info({ count: lines.length, sessionId }, 'Logged observations from Telegram session')
    }
  } catch (err) {
    // Never let observation logging break message delivery
    logger.warn({ err }, 'Failed to log observations (non-fatal)')
  }
}
