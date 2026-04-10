import express from 'express'
import cors from 'cors'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
import { config } from './lib/config.js'
import { systemRouter } from './routes/system.js'
import { roadmapRouter } from './routes/roadmap.js'
import { youtubeRouter } from './routes/youtube.js'
import { intelligenceRouter } from './routes/intelligence.js'
import { skillsRouter } from './routes/skills.js'
import { inboxRouter } from './routes/inbox.js'
import { activityRouter } from './routes/activity.js'
import { tasksRouter } from './routes/tasks.js'
import { contentPipelineRouter } from './routes/content-pipeline.js'
import { healthRouter } from './routes/health.js'
import { vaultGrowthRouter } from './routes/vault-growth.js'
import { suggestedActionRouter } from './routes/suggested-action.js'
import { commandRouter } from './routes/command.js'
import { inboxActionRouter } from './routes/inbox-action.js'

const app = express()

app.use(cors())
app.use(express.json())

// API routes
app.use('/api/system', systemRouter)
app.use('/api/roadmap', roadmapRouter)
app.use('/api/youtube', youtubeRouter)
app.use('/api/intelligence', intelligenceRouter)
app.use('/api/skills', skillsRouter)
app.use('/api/inbox', inboxRouter)
app.use('/api/activity', activityRouter)
app.use('/api/tasks', tasksRouter)
app.use('/api/content-pipeline', contentPipelineRouter)
app.use('/api/health', healthRouter)
app.use('/api/vault/growth', vaultGrowthRouter)
app.use('/api/suggested-action', suggestedActionRouter)
app.use('/api/command', commandRouter)
app.use('/api/inbox', inboxActionRouter)

// Serve static client in production
const clientDir = resolve(__dirname, '../client')
app.use(express.static(clientDir))
app.get('*', (_req, res) => {
  res.sendFile(resolve(clientDir, 'index.html'))
})

app.listen(config.port, () => {
  console.log(`K2B Dashboard running on http://localhost:${config.port}`)
})
