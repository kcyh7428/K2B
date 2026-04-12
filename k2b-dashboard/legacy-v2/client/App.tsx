import { Header } from './components/Header.js'
import { HealthStrip } from './components/HealthStrip.js'
import { SuggestedAction } from './components/SuggestedAction.js'
import { QuickActions } from './components/QuickActions.js'
import { SystemStatus } from './components/SystemStatus.js'
import { VaultStats } from './components/VaultStats.js'
import { VaultGrowth } from './components/VaultGrowth.js'
import { Roadmap } from './components/Roadmap.js'
import { YouTubeDigest } from './components/YouTubeDigest.js'
import { Intelligence } from './components/Intelligence.js'
import { SkillActivity } from './components/SkillActivity.js'
import { Review } from './components/Review.js'
import { ActivityFeed } from './components/ActivityFeed.js'
import { ScheduledTasks } from './components/ScheduledTasks.js'
import { ContentPipeline } from './components/ContentPipeline.js'
import { LinkedInPanel } from './components/LinkedInPanel.js'

export function App() {
  return (
    <div className="app">
      <Header />
      <HealthStrip />
      <SuggestedAction />
      <QuickActions />
      <div className="dashboard">
        {/* Row 1: System | Vault | ★ Roadmap */}
        <div className="dashboard-row dashboard-row-3col">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <SystemStatus />
            <VaultGrowth />
          </div>
          <VaultStats />
          <Roadmap />
        </div>

        {/* Row 2: ★ YouTube Digest */}
        <div className="dashboard-row dashboard-row-full">
          <YouTubeDigest />
        </div>

        {/* Row 3: Review | Tasks + Content Pipeline + LinkedIn */}
        <div className="dashboard-row dashboard-row-2col">
          <Review />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <ScheduledTasks />
            <ContentPipeline />
            <LinkedInPanel />
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
