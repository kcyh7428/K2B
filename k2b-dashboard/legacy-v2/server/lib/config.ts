import dotenv from 'dotenv'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
dotenv.config({ path: resolve(__dirname, '../../../.env') })

export const config = {
  port: parseInt(process.env.PORT || '3200'),
  vaultPath: process.env.VAULT_PATH || '/Users/keithmbpm2/Projects/K2B-Vault',
  dbPath: process.env.K2B_DB_PATH || '/Users/keithmbpm2/Projects/K2B/k2b-remote/store/k2b-remote.db',
  healthPath: process.env.K2B_HEALTH_PATH || '/Users/keithmbpm2/Projects/K2B/k2b-remote/store/health.json',
  projectPath: process.env.K2B_PROJECT_PATH || '/Users/keithmbpm2/Projects/K2B',
  skillsPath: process.env.SKILLS_PATH || '/Users/keithmbpm2/Projects/K2B/.claude/skills',
  learningsPath: process.env.LEARNINGS_PATH || '/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_learnings.md',
  usageLogPath: process.env.USAGE_LOG_PATH || '/Users/keithmbpm2/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv',
  pollIntervalMs: parseInt(process.env.POLL_INTERVAL_MS || '30000'),
}
