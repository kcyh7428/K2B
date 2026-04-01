import { Router } from 'express'
import { listVaultFolder } from '../lib/vault.js'
import { getScheduledTasks } from '../lib/db.js'
import { glob } from 'glob'
import { statSync } from 'fs'
import { resolve } from 'path'
import { config } from '../lib/config.js'

const router = Router()

interface Suggestion {
  priority: number
  message: string
  command: string
}

router.get('/', async (_req, res) => {
  try {
    const suggestions: Suggestion[] = []

    // 1. Check inbox age
    try {
      const inboxFiles = await listVaultFolder('Inbox')
      const readyFiles = await listVaultFolder('Inbox/Ready')
      const totalInbox = inboxFiles.length + readyFiles.length
      if (totalInbox > 0) {
        const now = new Date()
        let oldestDays = 0
        for (const f of [...inboxFiles, ...readyFiles]) {
          const dateStr = f.data.date as string
          if (!dateStr) continue
          const d = new Date(dateStr)
          if (isNaN(d.getTime())) continue
          const days = Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24))
          if (days > oldestDays) oldestDays = days
        }
        if (oldestDays > 3) {
          suggestions.push({
            priority: 1,
            message: `Review inbox (${totalInbox} items, oldest ${oldestDays} days)`,
            command: '/inbox',
          })
        } else if (totalInbox > 0) {
          suggestions.push({
            priority: 4,
            message: `${totalInbox} inbox items pending review`,
            command: '/inbox',
          })
        }
      }
    } catch { /* skip */ }

    // 2. Check scheduled task failures
    try {
      const tasks = getScheduledTasks()
      const failed = tasks.filter(t => t.status === 'error' || t.status === 'failed')
      if (failed.length > 0) {
        const name = failed[0].prompt.split('\n')[0]?.trim().slice(0, 40) || 'Unknown'
        suggestions.push({
          priority: 2,
          message: `Scheduled task "${name}" failed. Check logs`,
          command: '/schedule list',
        })
      }
    } catch { /* skip */ }

    // 3. Check vault activity (any notes created in last 2 days?)
    try {
      const twoDaysAgo = new Date()
      twoDaysAgo.setDate(twoDaysAgo.getDate() - 2)
      const recentFiles = await glob('**/*.md', { cwd: config.vaultPath })
      let recentCount = 0
      for (const file of recentFiles) {
        try {
          const stat = statSync(resolve(config.vaultPath, file))
          if (stat.birthtime >= twoDaysAgo) recentCount++
        } catch { /* skip */ }
      }
      if (recentCount === 0) {
        suggestions.push({
          priority: 3,
          message: 'No vault activity in 2+ days. Start with /daily',
          command: '/daily',
        })
      }
    } catch { /* skip */ }

    // Sort by priority and pick the top one
    suggestions.sort((a, b) => a.priority - b.priority)
    const top = suggestions[0] || { priority: 99, message: 'All clear. System nominal.', command: '' }

    res.json({ suggestion: top, all: suggestions })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed' })
  }
})

export { router as suggestedActionRouter }
