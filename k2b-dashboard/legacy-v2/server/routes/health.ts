import { Router } from 'express'
import { execSync } from 'child_process'
import { getPm2Status } from '../lib/pm2.js'
import { listVaultFolder } from '../lib/vault.js'
import { getScheduledTasks } from '../lib/db.js'

const router = Router()

interface Alert {
  level: 'red' | 'yellow'
  message: string
}

function getRemotePm2Alerts(): Alert[] {
  try {
    const cmd = `ssh -o ConnectTimeout=3 macmini "pm2 jlist"`
    const output = execSync(cmd, { timeout: 8000 }).toString().trim()
    if (!output) return [{ level: 'red', message: 'k2b-remote: cannot reach Mac Mini' }]
    const procs = JSON.parse(output)
    const alerts: Alert[] = []
    for (const p of procs) {
      const env = p.pm2_env || {}
      const name = env.name || p.name || 'unknown'
      if (env.status !== 'online') {
        alerts.push({ level: 'red', message: `${name}: ${env.status || 'unknown'}` })
      }
    }
    return alerts
  } catch {
    return [{ level: 'yellow', message: 'Mac Mini: SSH unreachable' }]
  }
}

router.get('/', async (_req, res) => {
  try {
    const alerts: Alert[] = []

    // Check pm2 processes (local first, then remote)
    const localProcs = getPm2Status()
    if (localProcs.length > 0) {
      for (const p of localProcs) {
        if (p.status !== 'online') {
          alerts.push({ level: 'red', message: `${p.name}: ${p.status}` })
        }
      }
    } else {
      const remoteAlerts = getRemotePm2Alerts()
      alerts.push(...remoteAlerts)
    }

    // Check inbox age
    try {
      const inboxFiles = await listVaultFolder('Inbox')
      if (inboxFiles.length > 0) {
        const now = new Date()
        let oldestDays = 0
        for (const f of inboxFiles) {
          const dateStr = f.data.date as string
          if (!dateStr) continue
          const d = new Date(dateStr)
          if (isNaN(d.getTime())) continue
          const days = Math.floor((now.getTime() - d.getTime()) / (1000 * 60 * 60 * 24))
          if (days > oldestDays) oldestDays = days
        }
        if (oldestDays > 3) {
          alerts.push({ level: 'yellow', message: `Oldest inbox item: ${oldestDays} days` })
        }
      }
    } catch {
      // vault read failed, skip
    }

    // Check scheduled task failures
    try {
      const tasks = getScheduledTasks()
      for (const t of tasks) {
        if (t.status === 'error' || t.status === 'failed') {
          const name = t.prompt.split('\n')[0]?.trim().slice(0, 40) || 'Unknown task'
          alerts.push({ level: 'yellow', message: `Task failed: ${name}` })
        }
      }
    } catch {
      // db read failed, skip
    }

    const status = alerts.length === 0 ? 'nominal' : alerts.some(a => a.level === 'red') ? 'critical' : 'warning'

    res.json({ status, alerts })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Health check failed' })
  }
})

export { router as healthRouter }
