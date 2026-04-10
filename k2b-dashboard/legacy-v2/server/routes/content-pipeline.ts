import { Router } from 'express'
import { listVaultFolder } from '../lib/vault.js'

const router = Router()

router.get('/', async (_req, res) => {
  try {
    const [inboxFiles, contentFiles] = await Promise.all([
      listVaultFolder('Inbox'),
      listVaultFolder('Notes/Content-Ideas'),
    ])

    // Count content-idea type files in Inbox
    const ideas = inboxFiles.filter(
      (f) => f.data.type === 'content-idea'
    ).length

    // Group content ideas by status
    let adopted = 0
    let drafts = 0
    let published = 0

    for (const file of contentFiles) {
      const status = (file.data.status as string) || 'new'
      switch (status) {
        case 'draft':
          drafts++
          break
        case 'published':
          published++
          break
        case 'adopted':
          adopted++
          break
        default:
          break
      }
    }

    res.json({ ideas, adopted, drafts, published })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as contentPipelineRouter }
