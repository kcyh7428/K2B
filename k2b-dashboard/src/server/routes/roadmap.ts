import { Router } from 'express'
import { existsSync } from 'fs'
import { resolve } from 'path'
import { readVaultFile } from '../lib/vault.js'
import { config } from '../lib/config.js'

const router = Router()

interface Feature {
  name: string
  status: string
  date: string
  description: string
  filePath: string
  hasEval: boolean
}

const statusOrder: Record<string, number> = { next: 0, planned: 1, backlog: 1, shipped: 2 }

router.get('/', async (_req, res) => {
  try {
    const roadmap = readVaultFile('MOC_K2B-Roadmap.md')

    // Extract [[feature_name]] links from roadmap content
    const linkPattern = /\[\[(feature_[^\]|]+)(?:\|[^\]]+)?\]\]/g
    const featureNames: string[] = []
    let match: RegExpExecArray | null
    while ((match = linkPattern.exec(roadmap.content)) !== null) {
      featureNames.push(match[1])
    }

    const features: Feature[] = []

    for (const name of featureNames) {
      try {
        const file = readVaultFile(`Notes/Features/${name}.md`)
        const status = (file.data.status as string) || 'planned'
        const date = (file.data.date as string) || ''
        const evalDir = resolve(config.vaultPath, 'Notes/Features', name, 'eval')

        features.push({
          name: file.filename,
          status,
          date,
          description: file.excerpt,
          filePath: `Notes/Features/${name}.md`,
          hasEval: existsSync(evalDir),
        })
      } catch {
        // Feature file doesn't exist, skip
      }
    }

    features.sort((a, b) => {
      const orderA = statusOrder[a.status] ?? 1
      const orderB = statusOrder[b.status] ?? 1
      return orderA - orderB
    })

    const shipped = features.filter((f) => f.status === 'shipped').length
    const planned = features.filter((f) => f.status !== 'shipped').length

    res.json({ features, stats: { shipped, planned, total: features.length } })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as roadmapRouter }
