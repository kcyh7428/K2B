import { useNow } from '../hooks/api'
import { AlertCircle, Brain, Clock, CheckCircle } from 'lucide-react'

const ICONS: Record<string, typeof AlertCircle> = {
  review: AlertCircle,
  observer: Brain,
  scheduled: Clock,
  idle: CheckCircle,
}

const COLORS: Record<string, string> = {
  review: 'border-accent-amber text-accent-amber',
  observer: 'border-accent-blue text-accent-blue',
  scheduled: 'border-accent-blue text-accent-blue',
  idle: 'border-accent-green text-accent-green',
}

export default function NowCard() {
  const { data, isLoading } = useNow()
  if (isLoading || !data) {
    return <div className="panel border-l-4 border-bg-border h-24 animate-pulse" />
  }

  const Icon = ICONS[data.priority] ?? CheckCircle
  const colorClass = COLORS[data.priority] ?? 'border-bg-border text-ink-secondary'

  return (
    <div className={`panel border-l-4 ${colorClass} flex items-center gap-4`}>
      <Icon className="w-8 h-8 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-xs uppercase tracking-wider text-ink-muted font-mono">{data.priority}</div>
        <div className="text-xl font-medium text-ink-primary">{data.title}</div>
        <div className="text-sm text-ink-secondary truncate">{data.preview}</div>
      </div>
      <button className="btn-primary whitespace-nowrap">{data.cta.label}</button>
    </div>
  )
}
