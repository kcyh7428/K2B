import { Router } from 'express'
import { readdirSync, readFileSync, statSync } from 'fs'
import { resolve } from 'path'
import { config } from '../lib/config.js'
import { getSkillUsage, getAllTimeUsage } from '../lib/usage.js'

const router = Router()

interface SkillMeta {
  name: string
  description: string
  tryHint: string
}

function parseSkillFrontmatter(skillDir: string): SkillMeta {
  const skillMdPath = resolve(skillDir, 'SKILL.md')
  const meta: SkillMeta = { name: '', description: '', tryHint: '' }

  try {
    const raw = readFileSync(skillMdPath, 'utf-8')
    const lines = raw.split('\n')

    // Parse YAML frontmatter
    if (lines[0]?.trim() === '---') {
      for (let i = 1; i < lines.length; i++) {
        const line = lines[i]
        if (line.trim() === '---') break

        const nameMatch = line.match(/^name:\s*["']?(.+?)["']?\s*$/)
        if (nameMatch) meta.name = nameMatch[1]

        const descMatch = line.match(/^description:\s*["']?(.+?)["']?\s*$/)
        if (descMatch) meta.description = descMatch[1]
      }
    }

    // Look for example commands in the full content for tryHint
    const slashMatch = raw.match(/\/\w[\w-]*/g)
    if (slashMatch) {
      // Find the first slash command that looks like a usage example
      const exampleLine = raw.split('\n').find(
        (l) => (l.includes('`/') || l.includes('Try:') || l.includes('Usage:')) && /\/\w/.test(l)
      )
      if (exampleLine) {
        const cmdMatch = exampleLine.match(/`(\/[\w-]+(?:\s+[^`]*)?)`/)
        if (cmdMatch) {
          meta.tryHint = `Try: ${cmdMatch[1]}`
        }
      }
      if (!meta.tryHint && slashMatch[0]) {
        meta.tryHint = `Try: ${slashMatch[0]}`
      }
    }
  } catch {
    // SKILL.md missing or unreadable
  }

  return meta
}

router.get('/', async (_req, res) => {
  try {
    const weekUsage = getSkillUsage(7)
    const allTimeUsage = getAllTimeUsage()

    // Build set of all skills that have ever been used
    const usedSkills = new Set(allTimeUsage.map((u) => u.skill))

    // List all k2b-* skill directories
    let skillDirs: string[] = []
    try {
      const entries = readdirSync(config.skillsPath)
      skillDirs = entries.filter((entry) => {
        if (!entry.startsWith('k2b-')) return false
        try {
          return statSync(resolve(config.skillsPath, entry)).isDirectory()
        } catch {
          return false
        }
      })
    } catch {
      // skills directory missing
    }

    // Find dormant skills (exist as directories but 0 all-time invocations)
    const dormant: { skill: string; description: string; tryHint: string }[] = []
    for (const dir of skillDirs) {
      if (!usedSkills.has(dir)) {
        const meta = parseSkillFrontmatter(resolve(config.skillsPath, dir))
        const skillName = dir.replace(/^k2b-/, '')
        const tryHint = meta.tryHint || `Try: /${skillName}`
        dormant.push({
          skill: dir,
          description: meta.description || meta.name || dir,
          tryHint,
        })
      }
    }

    // Active skills from 7-day window
    const active = weekUsage.map((u) => ({
      skill: u.skill,
      count: u.count,
      lastUsed: u.lastUsed,
    }))

    const totalInvocations = allTimeUsage.reduce((sum, u) => sum + u.count, 0)

    res.json({
      active,
      dormant,
      totalInvocations,
      activeCount: active.length,
      dormantCount: dormant.length,
    })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed to load skills data' })
  }
})

export { router as skillsRouter }
