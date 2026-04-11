import dotenv from 'dotenv'
import { existsSync } from 'node:fs'
import { resolve, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { homedir } from 'node:os'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Load .env if present, but do NOT rely on it for host paths.
// This file is host-specific and not guaranteed to be deployed in sync.
dotenv.config({ path: resolve(__dirname, '../../../.env') })

// Auto-detect the project root by walking up until we find k2b-dashboard/package.json.
// This works on MacBook (keithmbpm2), Mac Mini (fastshower), and anywhere else.
function findProjectRoot(): string {
  // __dirname is either src/server/lib (dev) or dist/server/lib (prod).
  // Project root (K2B/) is 4 levels up in both cases.
  const candidate = resolve(__dirname, '../../../..')
  if (existsSync(join(candidate, 'k2b-dashboard/package.json'))) return candidate
  // Fallback: walk up
  let cur = __dirname
  for (let i = 0; i < 8; i++) {
    if (existsSync(join(cur, 'k2b-dashboard/package.json'))) return cur
    cur = dirname(cur)
  }
  return candidate
}

// Auto-detect the vault by checking common locations. Vault is a SIBLING of the
// K2B project directory on both known hosts (~/Projects/K2B + ~/Projects/K2B-Vault).
function findVaultPath(projectRoot: string): string {
  const sibling = resolve(projectRoot, '..', 'K2B-Vault')
  if (existsSync(sibling)) return sibling

  // Known host candidates (last-resort only; the sibling check above handles both)
  const candidates = [
    join(homedir(), 'Projects', 'K2B-Vault'),
    '/Users/fastshower/Projects/K2B-Vault',
    '/Users/keithmbpm2/Projects/K2B-Vault',
  ]
  for (const c of candidates) {
    if (existsSync(c)) return c
  }
  return sibling
}

const PROJECT = process.env.K2B_PROJECT_PATH ?? findProjectRoot()
const VAULT = process.env.VAULT_PATH ?? findVaultPath(PROJECT)

// Single source of truth -- everything derives from VAULT and PROJECT.
// No path strings should appear in route handlers.
export const paths = {
  vault: VAULT,
  project: PROJECT,

  // Vault layer roots
  raw: join(VAULT, 'raw'),
  wiki: join(VAULT, 'wiki'),
  review: join(VAULT, 'review'),
  daily: join(VAULT, 'Daily'),
  assets: join(VAULT, 'Assets'),
  intakeAssets: join(VAULT, 'Assets', 'intake'),
  intakeProcessed: join(VAULT, 'Assets', 'intake', 'processed'),

  // Wiki subfolders
  wikiContext: join(VAULT, 'wiki', 'context'),
  wikiConcepts: join(VAULT, 'wiki', 'concepts'),
  wikiProjects: join(VAULT, 'wiki', 'projects'),
  wikiPeople: join(VAULT, 'wiki', 'people'),
  wikiInsights: join(VAULT, 'wiki', 'insights'),
  wikiContentPipeline: join(VAULT, 'wiki', 'content-pipeline'),
  wikiLog: join(VAULT, 'wiki', 'log.md'),
  wikiIndex: join(VAULT, 'wiki', 'index.md'),

  // Memory (symlinked into the vault, but read via vault path so both
  // MacBook and Mac Mini work without env-specific tweaks).
  memory: join(VAULT, 'System', 'memory'),
  activeRules: join(VAULT, 'System', 'memory', 'active_rules.md'),
  learnings: join(VAULT, 'System', 'memory', 'self_improve_learnings.md'),
  errors: join(VAULT, 'System', 'memory', 'self_improve_errors.md'),
  requests: join(VAULT, 'System', 'memory', 'self_improve_requests.md'),

  // Observer / signals
  observerCandidates: join(VAULT, 'wiki', 'context', 'observer-candidates.md'),
  observerRuns: join(VAULT, 'wiki', 'context', 'observer-runs.jsonl'),
  observations: join(VAULT, 'wiki', 'context', 'observations.jsonl'),
  preferenceSignals: join(VAULT, 'wiki', 'context', 'preference-signals.jsonl'),
  preferenceProfile: join(VAULT, 'wiki', 'context', 'preference-profile.md'),
  skillUsageLog: join(VAULT, 'wiki', 'context', 'skill-usage-log.tsv'),

  // k2b-remote store (read-only from dashboard side)
  remoteDb: process.env.K2B_REMOTE_DB_PATH ?? join(PROJECT, 'k2b-remote', 'store', 'k2b-remote.db'),
} as const

export const config = {
  port: parseInt(process.env.PORT ?? '3200', 10),
  pollIntervalMs: parseInt(process.env.POLL_INTERVAL_MS ?? '15000', 10),
}
