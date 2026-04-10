import { useScheduled, useVaultFlow, useActivity } from '../hooks/api'

export default function FooterRow() {
  const sched = useScheduled()
  const flow = useVaultFlow()
  const act = useActivity()

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      <div className="panel">
        <div className="panel-title">scheduled (next 3)</div>
        {sched.data?.items.slice(0, 3).map((t) => (
          <div key={t.id} className="text-xs text-ink-secondary truncate mb-1">
            <span className="text-ink-muted">
              {t.nextRun ? new Date(t.nextRun).toLocaleString('en-HK', { hour12: false, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '?'}
            </span>{' '}
            {t.prompt}
          </div>
        ))}
        {sched.data?.items.length === 0 && <div className="text-xs text-ink-muted">No upcoming tasks.</div>}
      </div>

      <div className="panel">
        <div className="panel-title">vault flow (24h)</div>
        <div className="flex items-center justify-around mb-2">
          <Layer label="raw" n={flow.data?.layers.raw} color="text-accent-amber" />
          <span className="text-ink-muted">→</span>
          <Layer label="wiki" n={flow.data?.layers.wiki} color="text-accent-green" />
          <span className="text-ink-muted">←</span>
          <Layer label="review" n={flow.data?.layers.review} color="text-accent-blue" />
        </div>
        <div className="text-[10px] text-ink-muted">
          {flow.data?.logEntries24h ?? 0} log entries in last 24h
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">recent activity</div>
        {act.data?.items.slice(0, 4).map((a, i) => (
          <div key={i} className="text-xs text-ink-secondary truncate mb-1">
            <span className="text-ink-muted font-mono text-[10px]">[{a.source}]</span> {a.text}
          </div>
        ))}
      </div>
    </div>
  )
}

function Layer({ label, n, color }: { label: string; n?: number; color: string }) {
  return (
    <div className="text-center">
      <div className={`text-2xl font-mono ${color}`}>{n ?? '·'}</div>
      <div className="text-[10px] uppercase font-mono text-ink-muted">{label}</div>
    </div>
  )
}
