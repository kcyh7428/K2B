/**
 * One-time migration: export all SQLite memories to JSONL files in the vault.
 *
 * Run on Mac Mini before deploying canonical-memory changes:
 *   cd ~/Projects/K2B/k2b-remote && npx tsx scripts/migrate-memories.ts
 *
 * Safe to re-run: appends only, downstream dedup uses content hashes.
 */

import Database from 'better-sqlite3'
import { mkdirSync, appendFileSync, existsSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { readEnvFile } from '../src/env.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const PROJECT_ROOT = resolve(__dirname, '..')

const env = readEnvFile()
const STORE_DIR = resolve(PROJECT_ROOT, 'store')
const K2B_VAULT_PATH = env['VAULT_PATH'] ?? resolve(PROJECT_ROOT, '..', '..', 'K2B-Vault')
const MEMORIES_DIR = resolve(K2B_VAULT_PATH, 'wiki', 'context', 'memories')

interface MemoryRow {
  id: number
  chat_id: string
  topic_key: string | null
  content: string
  sector: string
  salience: number
  created_at: number
  accessed_at: number
}

function main(): void {
  const dbPath = resolve(STORE_DIR, 'k2b-remote.db')
  if (!existsSync(dbPath)) {
    console.error(`Database not found at ${dbPath}`)
    process.exit(1)
  }

  const db = new Database(dbPath, { readonly: true })

  const rows = db.prepare('SELECT * FROM memories ORDER BY created_at').all() as MemoryRow[]
  if (rows.length === 0) {
    console.log('No memories found in SQLite. Nothing to export.')
    db.close()
    return
  }

  // Group by chat_id
  const grouped = new Map<string, MemoryRow[]>()
  for (const row of rows) {
    const existing = grouped.get(row.chat_id) ?? []
    existing.push(row)
    grouped.set(row.chat_id, existing)
  }

  // Ensure output directory
  mkdirSync(MEMORIES_DIR, { recursive: true })

  let totalExported = 0

  for (const [chatId, memories] of grouped) {
    const outPath = resolve(MEMORIES_DIR, `telegram-${chatId}.jsonl`)
    const lines: string[] = []

    for (const m of memories) {
      const entry = {
        ts: new Date(m.created_at).toISOString(),
        sector: m.sector,
        topic_key: m.topic_key ?? undefined,
        content: m.content,
        salience: 1.0, // birth salience -- SQLite decay is projection-only
        source: 'telegram',
      }
      lines.push(JSON.stringify(entry))
    }

    appendFileSync(outPath, lines.join('\n') + '\n', 'utf-8')
    totalExported += memories.length
    console.log(`  ${chatId}: ${memories.length} memories -> ${outPath}`)
  }

  db.close()
  console.log(`\nExported ${totalExported} memories for ${grouped.size} chat(s) to ${MEMORIES_DIR}`)
}

main()
