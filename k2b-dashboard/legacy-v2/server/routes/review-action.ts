import { Router } from 'express'
import { resolve } from 'path'
import { renameSync, unlinkSync, readFileSync, writeFileSync } from 'fs'
import { config } from '../lib/config.js'

const router = Router()

router.post('/:filename/action', (req, res) => {
  try {
    const { filename } = req.params
    const { action } = req.body as { action?: string }

    if (!action || !['archive', 'snooze'].includes(action)) {
      return res.status(400).json({ error: 'Invalid action. Use: archive, snooze' })
    }

    // Find the file in review/ or review/Ready/
    const reviewPath = resolve(config.vaultPath, 'review', `${filename}.md`)
    const readyPath = resolve(config.vaultPath, 'review/Ready', `${filename}.md`)
    let filePath: string | null = null
    try {
      readFileSync(reviewPath)
      filePath = reviewPath
    } catch {
      try {
        readFileSync(readyPath)
        filePath = readyPath
      } catch {
        return res.status(404).json({ error: 'File not found in review queue' })
      }
    }

    if (action === 'archive') {
      const archivePath = resolve(config.vaultPath, 'Archive', `${filename}.md`)
      renameSync(filePath, archivePath)
      return res.json({ status: 'archived', filename })
    }

    if (action === 'snooze') {
      // Set review-action to snooze and update date to 3 days from now
      const raw = readFileSync(filePath, 'utf-8')
      const snoozeDate = new Date()
      snoozeDate.setDate(snoozeDate.getDate() + 3)
      const snoozeDateStr = snoozeDate.toISOString().slice(0, 10)

      let updated = raw
      if (raw.includes('review-action:')) {
        updated = updated.replace(/review-action:.*/, `review-action: snoozed-until-${snoozeDateStr}`)
      } else {
        // Add after the last frontmatter field before ---
        updated = updated.replace(/^(---\n[\s\S]*?)(---)/, `$1review-action: snoozed-until-${snoozeDateStr}\n$2`)
      }
      writeFileSync(filePath, updated)
      return res.json({ status: 'snoozed', filename, until: snoozeDateStr })
    }

    res.status(400).json({ error: 'Unknown action' })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Action failed' })
  }
})

export { router as reviewActionRouter }
