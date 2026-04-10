import { useState } from 'react'
import NowCard from './components/NowCard'
import IntakeBar from './components/IntakeBar'
import ReviewQueue from './components/ReviewQueue'
import TodayCaptures from './components/TodayCaptures'
import LearningPanel from './components/LearningPanel'
import LearningInspector from './components/LearningInspector'
import FooterRow from './components/FooterRow'

export default function App() {
  const [inspectorOpen, setInspectorOpen] = useState(false)

  return (
    <div className="min-h-screen flex flex-col gap-3 p-4 max-w-[1600px] mx-auto">
      <header className="flex items-baseline justify-between mb-1">
        <h1 className="text-lg font-mono text-ink-secondary">
          k2b · mission control <span className="text-ink-muted">v3</span>
        </h1>
        <span className="text-xs text-ink-muted font-mono">
          {new Date().toLocaleString('en-HK', { timeZone: 'Asia/Hong_Kong', hour12: false })}
        </span>
      </header>

      <NowCard />
      <IntakeBar />

      <div className="grid grid-cols-12 gap-3 flex-1 min-h-0">
        <div className="col-span-12 lg:col-span-7 flex flex-col gap-3 min-h-0">
          <ReviewQueue />
          <TodayCaptures />
        </div>
        <div className="col-span-12 lg:col-span-5 min-h-0">
          <LearningPanel onOpenInspector={() => setInspectorOpen(true)} />
        </div>
      </div>

      <FooterRow />

      {inspectorOpen && <LearningInspector onClose={() => setInspectorOpen(false)} />}
    </div>
  )
}
