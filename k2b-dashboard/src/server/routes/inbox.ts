import { Router } from 'express'
import { listVaultFolder } from '../lib/vault.js'
import type { VaultFile } from '../lib/vault.js'

const router = Router()

interface InboxItem {
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

function toInboxItem(file: VaultFile, folder: string): InboxItem {
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
    const [inboxFiles, readyFiles] = await Promise.all([
      listVaultFolder('Inbox'),
      listVaultFolder('Inbox/Ready'),
    ])

    const items = [
      ...inboxFiles.map((f) => toInboxItem(f, 'Inbox')),
      ...readyFiles.map((f) => toInboxItem(f, 'Inbox/Ready')),
    ]

    const readyCount = readyFiles.length
    const totalCount = items.length

    res.json({ items, readyCount, totalCount })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as inboxRouter }
