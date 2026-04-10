import { useCapturesToday } from '../hooks/api'

const LAYER_COLORS: Record<string, string> = {
  youtube: 'text-accent-red',
  meetings: 'text-accent-blue',
  research: 'text-accent-green',
  tldrs: 'text-accent-amber',
  daily: 'text-ink-secondary',
}

export default function TodayCaptures() {
  const { data, isLoading } = useCapturesToday()
  return (
    <div className="panel flex flex-col min-h-0 flex-1">
      <div className="panel-title flex items-center justify-between">
        <span>today's captures</span>
        <span>{data?.count ?? 0}</span>
      </div>
      <div className="flex-1 overflow-y-auto space-y-2">
        {isLoading && <div className="text-xs text-ink-muted">loading...</div>}
        {data?.items.length === 0 && (
          <div className="text-xs text-ink-muted">Nothing captured in the last 24h.</div>
        )}
        {data?.items.map((item) => (
          <div key={item.filename} className="bg-bg-raised border border-bg-border rounded p-2">
            <div className="flex items-baseline justify-between gap-2">
              <div className="text-sm text-ink-primary truncate flex-1">{item.title}</div>
              <span className={`text-[10px] uppercase font-mono ${LAYER_COLORS[item.layer] ?? 'text-ink-muted'}`}>
                {item.layer}
              </span>
            </div>
            <div className="text-xs text-ink-secondary line-clamp-1 mt-1">{item.preview}</div>
            <div className="text-[10px] font-mono text-ink-muted mt-1">
              {new Date(item.mtime).toLocaleTimeString('en-HK', { hour12: false })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
