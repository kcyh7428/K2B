import { Router } from 'express'
import { listVaultFolder } from '../lib/vault.js'
import type { VaultFile } from '../lib/vault.js'

const router = Router()

interface ReviewItem {
  filename: string
  title: string
  type: string
  origin: string
  date: string
  tags: string[]
  reviewAction: string
  reviewNotes: string
  path: string
  excerpt: string
}

function toReviewItem(file: VaultFile, folder: string): ReviewItem {
  const data = file.data
  const tags = Array.isArray(data.tags) ? (data.tags as string[]) : []

  return {
    filename: file.filename,
    title: (data.title as string) || file.filename,
    type: (data.type as string) || '',
    origin: (data.origin as string) || '',
    date: (data.date as string) || '',
    tags,
    reviewAction: (data['review-action'] as string) || '',
    reviewNotes: (data['review-notes'] as string) || '',
    path: `${folder}/${file.filename}.md`,
    excerpt: file.excerpt,
  }
}

router.get('/', async (_req, res) => {
  try {
    const [reviewFiles, readyFiles] = await Promise.all([
      listVaultFolder('review'),
      listVaultFolder('review/Ready'),
    ])

    const items = [
      ...reviewFiles.map((f) => toReviewItem(f, 'review')),
      ...readyFiles.map((f) => toReviewItem(f, 'review/Ready')),
    ]

    const readyCount = readyFiles.length
    const totalCount = items.length

    // Compute oldest age and status counts
    const now = new Date()
    let oldestAgeDays = 0
    const statusCounts: Record<string, number> = { pending: 0, ready: readyCount }
    for (const item of items) {
      if (item.date) {
        const d = new Date(item.date)
        if (!isNaN(d.getTime())) {
          const days = Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24))
          if (days > oldestAgeDays) oldestAgeDays = days
        }
      }
      if (item.reviewAction) {
        statusCounts[item.reviewAction] = (statusCounts[item.reviewAction] || 0) + 1
      } else {
        statusCounts.pending = (statusCounts.pending || 0) + 1
      }
    }

    // Sort by date ascending (oldest first)
    items.sort((a, b) => {
      const da = a.date ? new Date(a.date).getTime() : Infinity
      const db = b.date ? new Date(b.date).getTime() : Infinity
      return da - db
    })

    res.json({ items, readyCount, totalCount, oldestAgeDays, statusCounts })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as reviewRouter }
