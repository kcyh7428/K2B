import { randomUUID } from 'node:crypto'
import cronParser from 'cron-parser'
import { initDatabase, createTask, listAllTasks, deleteTask, pauseTask, resumeTask } from './db.js'
import { computeNextRun } from './scheduler.js'

function usage(): void {
  console.log(`
Usage:
  schedule create "<prompt>" "<cron>" <chat_id>        Create a recurring task
  schedule create-once "<prompt>" "<datetime>" <chat_id>  Create a one-time reminder
  schedule list                                         List all tasks
  schedule delete <id>                                  Delete a task
  schedule pause <id>                                   Pause a task
  schedule resume <id>                                  Resume a task

Examples:
  schedule create "Give me a daily briefing" "0 9 * * *" 123456789
  schedule create-once "Bring driving license" "2026-04-02 18:00" 123456789
`)
}

function main(): void {
  initDatabase()

  const args = process.argv.slice(2)
  const command = args[0]

  switch (command) {
    case 'create': {
      const prompt = args[1]
      const cron = args[2]
      const chatId = args[3]

      if (!prompt || !cron || !chatId) {
        console.error('Missing arguments. Usage: create "<prompt>" "<cron>" <chat_id>')
        process.exit(1)
      }

      // Validate cron expression
      try {
        cronParser.parseExpression(cron)
      } catch {
        console.error(`Invalid cron expression: ${cron}`)
        process.exit(1)
      }

      const id = randomUUID().slice(0, 8)
      const nextRun = computeNextRun(cron)
      createTask(id, chatId, prompt, cron, nextRun)

      console.log(`Task created:`)
      console.log(`  ID:       ${id}`)
      console.log(`  Prompt:   ${prompt}`)
      console.log(`  Schedule: ${cron}`)
      console.log(`  Next run: ${new Date(nextRun).toLocaleString()}`)
      break
    }

    case 'create-once': {
      const prompt = args[1]
      const datetime = args[2]
      const chatId = args[3]

      if (!prompt || !datetime || !chatId) {
        console.error('Missing arguments. Usage: create-once "<prompt>" "<datetime>" <chat_id>')
        console.error('Datetime format: "YYYY-MM-DD HH:MM" in local time (HKT)')
        process.exit(1)
      }

      const parsed = new Date(datetime.replace(' ', 'T') + '+08:00')
      if (isNaN(parsed.getTime())) {
        console.error(`Invalid datetime: ${datetime}. Use format: "YYYY-MM-DD HH:MM"`)
        process.exit(1)
      }

      const fireAt = parsed.getTime()
      if (fireAt <= Date.now()) {
        console.error(`Datetime is in the past: ${parsed.toLocaleString()}`)
        process.exit(1)
      }

      const id = randomUUID().slice(0, 8)
      createTask(id, chatId, prompt, 'once', fireAt, 'one-time')

      console.log(`One-time reminder created:`)
      console.log(`  ID:       ${id}`)
      console.log(`  Prompt:   ${prompt}`)
      console.log(`  Fire at:  ${parsed.toLocaleString()}`)
      break
    }

    case 'list': {
      const tasks = listAllTasks()
      if (tasks.length === 0) {
        console.log('No scheduled tasks.')
        return
      }

      console.log('\nScheduled Tasks:')
      console.log('-'.repeat(80))
      for (const t of tasks) {
        console.log(`  ID:       ${t.id}`)
        console.log(`  Type:     ${t.type}`)
        console.log(`  Status:   ${t.status}`)
        console.log(`  Schedule: ${t.type === 'one-time' ? 'once' : t.schedule}`)
        console.log(`  ${t.type === 'one-time' ? 'Fire at' : 'Next run'}: ${new Date(t.next_run).toLocaleString()}`)
        console.log(`  Prompt:   ${t.prompt.slice(0, 100)}`)
        if (t.last_run) {
          console.log(`  Last run: ${new Date(t.last_run).toLocaleString()}`)
        }
        console.log('-'.repeat(80))
      }
      break
    }

    case 'delete': {
      const id = args[1]
      if (!id) {
        console.error('Missing task ID')
        process.exit(1)
      }
      if (deleteTask(id)) {
        console.log(`Task ${id} deleted.`)
      } else {
        console.error(`Task ${id} not found.`)
      }
      break
    }

    case 'pause': {
      const id = args[1]
      if (!id) {
        console.error('Missing task ID')
        process.exit(1)
      }
      if (pauseTask(id)) {
        console.log(`Task ${id} paused.`)
      } else {
        console.error(`Task ${id} not found.`)
      }
      break
    }

    case 'resume': {
      const id = args[1]
      if (!id) {
        console.error('Missing task ID')
        process.exit(1)
      }
      // Need the cron expression to compute next run
      const tasks = listAllTasks()
      const task = tasks.find((t) => t.id === id)
      if (!task) {
        console.error(`Task ${id} not found.`)
        process.exit(1)
      }
      const nextRun = computeNextRun(task.schedule)
      if (resumeTask(id, nextRun)) {
        console.log(`Task ${id} resumed. Next run: ${new Date(nextRun).toLocaleString()}`)
      }
      break
    }

    default:
      usage()
  }
}

main()
