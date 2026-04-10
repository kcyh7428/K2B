import { execSync } from 'child_process'

export interface Pm2Process {
  name: string
  status: string
  uptime: number
  memory: number
  cpu: number
  pid: number
  restarts: number
}

function mapStatus(pmStatus: string): string {
  switch (pmStatus) {
    case 'online': return 'online'
    case 'stopping': return 'stopping'
    case 'stopped': return 'stopped'
    case 'errored': return 'errored'
    case 'launching': return 'launching'
    default: return pmStatus
  }
}

export function getPm2Status(): Pm2Process[] {
  try {
    const output = execSync('pm2 jlist', { encoding: 'utf-8', timeout: 5000 })
    const processes = JSON.parse(output)

    if (!Array.isArray(processes)) return []

    return processes.map((proc: Record<string, unknown>) => {
      const env = (proc.pm2_env || {}) as Record<string, unknown>
      const monit = (proc.monit || {}) as Record<string, unknown>

      return {
        name: (proc.name as string) || 'unknown',
        status: mapStatus((env.status as string) || 'unknown'),
        uptime: typeof env.pm_uptime === 'number' ? Date.now() - env.pm_uptime : 0,
        memory: (monit.memory as number) || 0,
        cpu: (monit.cpu as number) || 0,
        pid: (proc.pid as number) || 0,
        restarts: (env.restart_time as number) || 0,
      }
    })
  } catch {
    return []
  }
}
