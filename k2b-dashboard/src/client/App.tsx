import { Header } from './components/Header.js'
import { SystemStatus } from './components/SystemStatus.js'
import { VaultStats } from './components/VaultStats.js'
import { Roadmap } from './components/Roadmap.js'
import { YouTubeDigest } from './components/YouTubeDigest.js'
import { Intelligence } from './components/Intelligence.js'
import { SkillActivity } from './components/SkillActivity.js'
import { Inbox } from './components/Inbox.js'
import { ActivityFeed } from './components/ActivityFeed.js'
import { ScheduledTasks } from './components/ScheduledTasks.js'
import { ContentPipeline } from './components/ContentPipeline.js'

export function App() {
  return (
    <div className="app">
      <Header />
      <div className="dashboard">
        {/* Row 1: System | Vault | ★ Roadmap */}
        <div className="dashboard-row dashboard-row-3col">
          <SystemStatus />
          <VaultStats />
          <Roadmap />
        </div>

        {/* Row 2: ★ YouTube Digest */}
        <div className="dashboard-row dashboard-row-full">
          <YouTubeDigest />
        </div>

        {/* Row 3: Inbox (2-col: items + preview) | Activity + Tasks + Pipeline */}
        <div className="dashboard-row dashboard-row-2col">
          <Inbox />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <ScheduledTasks />
            <ContentPipeline />
          </div>
        </div>

        {/* Row 4: (Intelligence + Activity Feed) | Skill Activity */}
        <div className="dashboard-row dashboard-row-2col">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Intelligence />
            <ActivityFeed />
          </div>
          <SkillActivity />
        </div>
      </div>
    </div>
  )
}
