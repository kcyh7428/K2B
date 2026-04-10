import express from 'express'
import cors from 'cors'
import { config, paths } from './lib/vault-paths.js'

import nowRoute from './routes/now.js'
import reviewRoute from './routes/review.js'
import capturesRoute from './routes/captures.js'
import learningRoute from './routes/learning.js'
import vaultRoute from './routes/vault.js'
import scheduledRoute from './routes/scheduled.js'
import activityRoute from './routes/activity.js'
import intakeRoute from './routes/intake.js'

const app = express()

app.use(cors())
app.use(express.json({ limit: '2mb' }))

app.get('/api/health', (_req, res) => {
  res.json({
    status: 'ok',
    vault: paths.vault,
    intakeUrl: paths.remoteIntakeUrl,
    ts: new Date().toISOString(),
  })
})

app.use('/api/now', nowRoute)
app.use('/api/review', reviewRoute)
app.use('/api/captures', capturesRoute)
app.use('/api/learning', learningRoute)
app.use('/api/vault', vaultRoute)
app.use('/api/scheduled', scheduledRoute)
app.use('/api/activity', activityRoute)
app.use('/api/intake', intakeRoute)

// In production, also serve the built client
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
const __dirname = dirname(fileURLToPath(import.meta.url))
const clientDist = resolve(__dirname, '../client')
if (existsSync(clientDist)) {
  app.use(express.static(clientDist))
  app.get('*', (_req, res) => {
    res.sendFile(resolve(clientDist, 'index.html'))
  })
}

app.listen(config.port, () => {
  console.log(`k2b-dashboard v3 listening on http://localhost:${config.port}`)
  console.log(`vault: ${paths.vault}`)
  console.log(`intake forwards to: ${paths.remoteIntakeUrl}`)
})
