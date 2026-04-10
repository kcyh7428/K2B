import { Router } from 'express'
import { execSync } from 'child_process'
import { getScheduledTasks } from '../lib/db.js'
import { getPm2Status } from '../lib/pm2.js'

const router = Router()

interface TaskEntry {
  id: number | string
  name: string
  prompt: string
  schedule: string
  nextRun: string
  lastRun: string | null
  status: string
  source: string
}

interface RemoteTask {
  id: string
  prompt: string
  schedule: string
  next_run: number | null
  last_run: number | null
  status: string
  type: string
}

function getRemoteTasks(): RemoteTask[] {
  try {
    const cmd = `ssh -o ConnectTimeout=3 macmini "sqlite3 -json ~/Projects/K2B/k2b-remote/store/k2b-remote.db 'SELECT id, prompt, schedule, next_run, last_run, status, type FROM scheduled_tasks WHERE status = \\\"active\\\"'"`
    const output = execSync(cmd, { timeout: 8000 }).toString().trim()
    if (!output) return []
    return JSON.parse(output)
  } catch {
    return []
  }
}

function formatCron(cron: string): string {
  // Convert common cron patterns to human-readable
  const parts = cron.split(' ')
  if (parts.length !== 5) return cron

  const [min, hour, dom, _mon, dow] = parts
  const time = `${hour.padStart(2, '0')}:${min.padStart(2, '0')}`

  if (dom === '*' && dow === '*') return `${time} daily`
  if (dow === '1') return `${time} Mon`
  if (dow === '3') return `${time} Wed`
  if (dow === '5') return `${time} Fri`
  if (dom.startsWith('*/')) return `${time} every ${dom.replace('*/', '')} days`
  if (hour.startsWith('*/')) return `every ${hour.replace('*/', '')}h`
  return `${time} cron(${cron})`
}

router.get('/', async (_req, res) => {
  try {
    const [dbTasks, pm2Processes] = await Promise.all([
      Promise.resolve(getScheduledTasks()),
      Promise.resolve(getPm2Status()),
    ])

    const tasks: TaskEntry[] = []

    // Try local DB first
    let scheduledTasks = dbTasks

    // If local DB is empty, try Mac Mini via SSH
    if (scheduledTasks.length === 0) {
      const remoteTasks = getRemoteTasks()
      for (const task of remoteTasks) {
        const firstLine = task.prompt.split('\n').map((l: string) => l.trim()).find((l: string) => l.length > 0) || task.prompt
        const name = firstLine.slice(0, 80) + (firstLine.length > 80 ? '...' : '')
        tasks.push({
          id: task.id,
          name,
          prompt: task.prompt,
          schedule: formatCron(task.schedule),
          nextRun: task.next_run ? new Date(task.next_run).toISOString() : '',
          lastRun: task.last_run ? new Date(task.last_run).toISOString() : null,
          status: task.status,
          source: 'mac-mini',
        })
      }
    } else {
      // Local DB tasks
      for (const task of scheduledTasks) {
        const firstLine = task.prompt.split('\n').map((l: string) => l.trim()).find((l: string) => l.length > 0) || task.prompt
        const name = firstLine.slice(0, 80) + (firstLine.length > 80 ? '...' : '')
        tasks.push({
          id: task.id,
          name,
          prompt: task.prompt,
          schedule: formatCron(task.schedule),
          nextRun: task.next_run ? new Date(task.next_run).toISOString() : '',
          lastRun: task.last_run ? new Date(task.last_run).toISOString() : null,
          status: task.status,
          source: 'scheduler',
        })
      }
    }

    // pm2-managed processes as synthetic task entries
    for (const proc of pm2Processes) {
      const isObserver = proc.name.toLowerCase().includes('observer')
      tasks.push({
        id: `pm2-${proc.name}`,
        name: proc.name,
        prompt: proc.name,
        schedule: isObserver ? 'every 5m' : 'continuous',
        nextRun: '',
        lastRun: null,
        status: proc.status,
        source: 'pm2',
      })
    }

    // Find earliest next run
    let nextRun = ''
    for (const task of tasks) {
      if (task.nextRun && (task.source === 'scheduler' || task.source === 'mac-mini')) {
        if (!nextRun || task.nextRun < nextRun) {
          nextRun = task.nextRun
        }
      }
    }

    res.json({ tasks, nextRun })
  } catch (err) {
    res.status(500).json({ error: err instanceof Error ? err.message : 'Failed to load tasks data' })
  }
})

export { router as tasksRouter }
