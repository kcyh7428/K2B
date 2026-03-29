import cronParser from 'cron-parser'
import { getDueTasks, updateTaskAfterRun, updateTaskNextRun, deleteTask } from './db.js'
import { runAgent } from './agent.js'
import { logger } from './logger.js'
import { sendPendingNudges } from './bot.js'
import { ALLOWED_CHAT_ID } from './config.js'

type Sender = (chatId: string, text: string) => Promise<void>

let sendFn: Sender
let pollInterval: ReturnType<typeof setInterval>
let isRunning = false

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
  if (isRunning) {
    logger.debug('Scheduler poll skipped: previous run still in progress')
    return
  }

  const tasks = getDueTasks()
  if (tasks.length === 0) return

  isRunning = true
  logger.info({ count: tasks.length }, 'Running due scheduled tasks')

  for (const task of tasks) {
    try {
      // For recurring tasks, advance next_run IMMEDIATELY to prevent re-picking
      // during the minutes-long agent execution
      if (task.type !== 'one-time') {
        const nextRun = computeNextRun(task.schedule)
        updateTaskNextRun(task.id, nextRun)
      }

      const label = task.type === 'one-time' ? 'Reminder' : 'Scheduled task'
      await sendFn(task.chat_id, `[${label} running: ${task.prompt.slice(0, 80)}...]`)

      const { text } = await runAgent(task.prompt)
      const result = text ?? '(no response)'

      if (task.type === 'one-time') {
        deleteTask(task.id)
        logger.info({ taskId: task.id }, 'One-time reminder fired and deleted')
      } else {
        updateTaskAfterRun(task.id, computeNextRun(task.schedule), result)
      }

      await sendFn(task.chat_id, result)

      // After YouTube morning task, send nudge buttons for any pending videos
      if (task.prompt.includes('/youtube morning') && ALLOWED_CHAT_ID) {
        const nudged = await sendPendingNudges(ALLOWED_CHAT_ID)
        if (nudged > 0) {
          logger.info({ nudged }, 'Sent YouTube nudge buttons')
        }
      }

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
  isRunning = false
}
