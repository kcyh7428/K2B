import { useReview } from '../hooks/api'

export default function ReviewQueue() {
  const { data, isLoading } = useReview()
  return (
    <div className="panel flex flex-col min-h-0">
      <div className="panel-title flex items-center justify-between">
        <span>review queue</span>
        <span className="text-accent-amber">{data?.count ?? 0}</span>
      </div>
      <div className="flex-1 overflow-y-auto space-y-2">
        {isLoading && <div className="text-xs text-ink-muted">loading...</div>}
        {data?.items.length === 0 && (
          <div className="text-xs text-ink-muted">No items pending review.</div>
        )}
        {data?.items.map((item) => (
          <div key={item.filename} className="bg-bg-raised border border-bg-border rounded p-2">
            <div className="flex items-baseline justify-between gap-2">
              <div className="text-sm text-ink-primary truncate flex-1">{item.title}</div>
              <span className="text-[10px] uppercase font-mono text-ink-muted">{item.type}</span>
            </div>
            <div className="text-xs text-ink-secondary line-clamp-2 mt-1">{item.preview}</div>
            <div className="flex items-center gap-3 mt-2 text-[10px] font-mono text-ink-muted">
              <span>origin: {item.origin}</span>
              {item.reviewAction && <span className="text-accent-blue">action: {item.reviewAction}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
