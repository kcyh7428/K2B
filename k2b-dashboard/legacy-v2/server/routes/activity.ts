import { Router } from 'express'
import { readFileSync, statSync } from 'fs'
import { resolve } from 'path'
import { glob } from 'glob'
import { config } from '../lib/config.js'
import { getRecentCommits } from '../lib/git.js'

const router = Router()

interface ActivityItem {
  type: string
  timestamp: string
  description: string
}

function getSkillUsageActivity(cutoff: Date): ActivityItem[] {
  try {
    const raw = readFileSync(config.usageLogPath, 'utf-8')
    const lines = raw.trim().split('\n').filter(Boolean)
    const items: ActivityItem[] = []

    for (const line of lines) {
      const [date, skill, _session, ...rest] = line.split('\t')
      if (!date || !skill) continue

      const entryDate = new Date(date)
      if (entryDate < cutoff) continue

      items.push({
        type: 'skill_usage',
        timestamp: entryDate.toISOString(),
        description: `Skill invoked: ${skill}${rest.length ? ` -- ${rest.join(' ')}` : ''}`,
      })
    }

    return items
  } catch {
    return []
  }
}

async function getVaultFileActivity(cutoff: Date): Promise<ActivityItem[]> {
  try {
    const files = await glob('**/*.md', { cwd: config.vaultPath })
    const items: ActivityItem[] = []

    for (const file of files) {
      try {
        const fullPath = resolve(config.vaultPath, file)
        const stat = statSync(fullPath)
        if (stat.mtime >= cutoff) {
          items.push({
            type: 'vault_change',
            timestamp: stat.mtime.toISOString(),
            description: `Vault file modified: ${file}`,
          })
        }
      } catch {
        // skip unreadable files
      }
    }

    return items
  } catch {
    return []
  }
}

function getGitActivity(cutoff: Date): ActivityItem[] {
  try {
    const commits = getRecentCommits(50)
    const items: ActivityItem[] = []

    for (const commit of commits) {
      const commitDate = new Date(commit.date)
      if (commitDate < cutoff) continue

      items.push({
        type: 'git_commit',
        timestamp: commitDate.toISOString(),
        description: `Commit: ${commit.message}`,
      })
    }

    return items
  } catch {
    return []
  }
}

router.get('/', async (_req, res) => {
  try {
    const cutoff = new Date()
    cutoff.setHours(cutoff.getHours() - 48)

    const [skillActivity, vaultActivity, gitActivity] = await Promise.all([
      Promise.resolve(getSkillUsageActivity(cutoff)),
      getVaultFileActivity(cutoff),
      Promise.resolve(getGitActivity(cutoff)),
    ])

    const all = [...skillActivity, ...vaultActivity, ...gitActivity]
    all.sort((a, b) => b.timestamp.localeCompare(a.timestamp))

    res.json(all.slice(0, 50))
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed to load activity data' })
  }
})

export { router as activityRouter }
