import { useLearningSummary } from '../hooks/api'
import { Brain, ChevronRight } from 'lucide-react'

export default function LearningPanel({ onOpenInspector }: { onOpenInspector: () => void }) {
  const { data, isLoading } = useLearningSummary()

  const lastRun = data?.observerLastRun
    ? `${Math.round((Date.now() - new Date(data.observerLastRun).getTime()) / 60000)}m ago`
    : 'never'

  return (
    <div className="panel flex flex-col h-full">
      <div className="panel-title flex items-center justify-between">
        <span className="flex items-center gap-2">
          <Brain className="w-3 h-3" />
          learning
        </span>
        <button className="text-accent-blue text-xs hover:underline flex items-center gap-1" onClick={onOpenInspector}>
          inspector <ChevronRight className="w-3 h-3" />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 flex-1">
        <Stat label="Signals (total)" value={data?.signals24h} loading={isLoading} />
        <Stat label="Candidates pending" value={data?.candidatesPending} loading={isLoading} />
        <Stat label="Rules changed (7d)" value={data?.rulesChanged7d} loading={isLoading} sub="Ship 2" />
        <Stat label="Observer last run" value={lastRun} loading={isLoading} small />
      </div>

      <div className="mt-3 pt-3 border-t border-bg-border text-xs text-ink-muted">
        {data?.observerRunsInLast24h ?? 0} observer runs in last 24h. Open the inspector to read prompts and responses.
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  loading,
  sub,
  small,
}: {
  label: string
  value: number | string | undefined
  loading: boolean
  sub?: string
  small?: boolean
}) {
  return (
    <div className="bg-bg-raised border border-bg-border rounded p-3">
      <div className="text-[10px] uppercase tracking-wider text-ink-muted font-mono">{label}</div>
      <div className={small ? 'text-base text-ink-primary font-mono mt-1' : 'text-2xl text-ink-primary font-mono mt-1'}>
        {loading ? '…' : value ?? '0'}
      </div>
      {sub && <div className="text-[10px] text-ink-muted mt-1">{sub}</div>}
    </div>
  )
}
