import { Router } from 'express'
import { execSync } from 'child_process'
import { readFileSync } from 'fs'
import { getPm2Status } from '../lib/pm2.js'
import { getVaultCounts } from '../lib/vault.js'
import { getLastCommit } from '../lib/git.js'
import { config } from '../lib/config.js'

const router = Router()

interface Pm2Process {
  name: string
  status: string
  uptime: number
  memory: number
  cpu: number
  pid: number
  restarts: number
}

function getRemotePm2Status(): Pm2Process[] {
  try {
    const cmd = `ssh -o ConnectTimeout=3 macmini "pm2 jlist"`
    const output = execSync(cmd, { timeout: 8000 }).toString().trim()
    if (!output) return []
    const procs = JSON.parse(output)
    return procs.map((p: any) => {
      const env = p.pm2_env || {}
      const monit = p.monit || {}
      return {
        name: env.name || p.name || 'unknown',
        status: env.status || 'unknown',
        uptime: typeof env.pm_uptime === 'number' ? Date.now() - env.pm_uptime : 0,
        memory: monit.memory || 0,
        cpu: monit.cpu || 0,
        pid: p.pid || 0,
        restarts: env.restart_time || 0,
      }
    })
  } catch {
    return []
  }
}

router.get('/', async (_req, res) => {
  try {
    // Try local pm2 first, fall back to Mac Mini via SSH
    let processes = getPm2Status()
    let source = 'local'

    if (processes.length === 0) {
      processes = getRemotePm2Status()
      source = processes.length > 0 ? 'mac-mini' : 'unavailable'
    }

    const vault = await getVaultCounts()
    const git = getLastCommit()

    let health: Record<string, unknown> | null = null
    try {
      const raw = readFileSync(config.healthPath, 'utf-8')
      health = JSON.parse(raw)
    } catch {
      // health.json may not exist
    }

    res.json({ processes, health, vault, git, source })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error'
    res.status(500).json({ error: message })
  }
})

export { router as systemRouter }
