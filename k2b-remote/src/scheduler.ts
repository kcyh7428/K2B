import cronParser from 'cron-parser'
import { getDueTasks, updateTaskAfterRun, deleteTask } from './db.js'
import { runAgent } from './agent.js'
import { logger } from './logger.js'

type Sender = (chatId: string, text: string) => Promise<void>

let sendFn: Sender
let pollInterval: ReturnType<typeof setInterval>

export function computeNextRun(cronExpression: string): number {
  const interval = cronParser.parseExpression(cronExpression)
  return interval.next().getTime()
}

export function initScheduler(send: Sender): void {
  sendFn = send
  pollInterval = setInterval(runDueTasks, 60_000)
  logger.info('Scheduler initialized (polling every 60s)')
}

export function stopScheduler(): void {
  if (pollInterval) clearInterval(pollInterval)
}

async function runDueTasks(): Promise<void> {
  const tasks = getDueTasks()
  if (tasks.length === 0) return

  logger.info({ count: tasks.length }, 'Running due scheduled tasks')

  for (const task of tasks) {
    try {
      const label = task.type === 'one-time' ? 'Reminder' : 'Scheduled task'
      await sendFn(task.chat_id, `[${label} running: ${task.prompt.slice(0, 80)}...]`)

      const { text } = await runAgent(task.prompt)
      const result = text ?? '(no response)'

      if (task.type === 'one-time') {
        deleteTask(task.id)
        logger.info({ taskId: task.id }, 'One-time reminder fired and deleted')
      } else {
        const nextRun = computeNextRun(task.schedule)
        updateTaskAfterRun(task.id, nextRun, result)
      }

      await sendFn(task.chat_id, result)
      logger.info({ taskId: task.id }, `${label} completed`)
    } catch (err) {
      logger.error({ err, taskId: task.id }, 'Scheduled task failed')
      try {
        await sendFn(task.chat_id, `Scheduled task failed: ${(err as Error).message}`)
      } catch {
        // ignore send failure
      }
    }
  }
}
