import { useState } from 'react'
import { X, Send } from 'lucide-react'
import {
  useLearningSignals,
  useLearningCandidates,
  useLearningRules,
  useLearningRuns,
  useIntakeMutation,
} from '../hooks/api'

export default function LearningInspector({ onClose }: { onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/60" onClick={onClose} />
      <div className="w-full max-w-[1200px] bg-bg-base border-l border-bg-border flex flex-col">
        <div className="flex items-center justify-between p-4 border-b border-bg-border">
          <div>
            <h2 className="text-lg text-ink-primary">Learning Inspector</h2>
            <p className="text-xs text-ink-muted">read-only · audit what K2B is learning · Ship 1</p>
          </div>
          <button onClick={onClose} className="p-2 hover:bg-bg-raised rounded">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 grid grid-cols-1 md:grid-cols-4 gap-3 p-4 overflow-hidden min-h-0">
          <SignalsCol />
          <CandidatesCol />
          <RulesCol />
          <CorrectionsCol />
        </div>
      </div>
    </div>
  )
}

function SignalsCol() {
  const { data, isLoading } = useLearningSignals()
  return (
    <div className="panel flex flex-col min-h-0">
      <div className="panel-title">signals</div>
      <div className="text-[10px] text-ink-muted mb-2 font-mono">
        obs:{data?.counts.observations ?? 0} · prefs:{data?.counts.preferenceSignals ?? 0} · skills:{data?.counts.skillUsage ?? 0} · errs:{data?.counts.errors ?? 0}
      </div>
      <div className="flex-1 overflow-y-auto space-y-1.5">
        {isLoading && <div className="text-xs text-ink-muted">loading...</div>}
        {data?.items.slice(0, 100).map((s, i) => (
          <div key={i} className="text-[11px] text-ink-secondary border-l-2 border-bg-border pl-2">
            <div className="text-[9px] text-ink-muted font-mono">[{s.source}] {s.ts}</div>
            <div className="line-clamp-2">{s.text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function CandidatesCol() {
  const { data, isLoading } = useLearningCandidates()
  const { data: runs } = useLearningRuns()

  return (
    <div className="panel flex flex-col min-h-0">
      <div className="panel-title">observer candidates</div>

      <details className="mb-3">
        <summary className="text-xs text-accent-blue cursor-pointer hover:underline">
          last {runs?.runs.length ?? 0} runs (prompt + response)
        </summary>
        <div className="mt-2 space-y-2 max-h-48 overflow-y-auto">
          {!runs?.exists && (
            <div className="text-[10px] text-ink-muted">No observer-runs.jsonl yet. Wait for next observer-loop run.</div>
          )}
          {runs?.runs.map((r, i) => (
            <div key={i} className="bg-bg-raised border border-bg-border rounded p-2">
              <div className="text-[9px] text-ink-muted font-mono">
                {r.ts} · {r.model ?? 'minimax'}
              </div>
              <details className="mt-1">
                <summary className="text-[10px] text-accent-blue cursor-pointer">prompt</summary>
                <pre className="text-[9px] text-ink-secondary whitespace-pre-wrap mt-1 max-h-32 overflow-y-auto">
                  {r.prompt ?? '(empty)'}
                </pre>
              </details>
              <details className="mt-1">
                <summary className="text-[10px] text-accent-blue cursor-pointer">response</summary>
                <pre className="text-[9px] text-ink-secondary whitespace-pre-wrap mt-1 max-h-32 overflow-y-auto">
                  {r.response ?? '(empty)'}
                </pre>
              </details>
            </div>
          ))}
        </div>
      </details>

      <div className="flex-1 overflow-y-auto space-y-1.5">
        {isLoading && <div className="text-xs text-ink-muted">loading...</div>}
        {data?.candidates.length === 0 && (
          <div className="text-xs text-ink-muted">No unreviewed candidates.</div>
        )}
        {data?.candidates.map((c, i) => (
          <div key={i} className="text-[11px] text-ink-secondary border-l-2 border-accent-amber/40 pl-2">
            {c.text}
          </div>
        ))}
      </div>
      <div className="text-[10px] text-ink-muted mt-2 pt-2 border-t border-bg-border">
        Ship 3 will add reject buttons. For now, audit only.
      </div>
    </div>
  )
}

function RulesCol() {
  const { data, isLoading } = useLearningRules()
  return (
    <div className="panel flex flex-col min-h-0">
      <div className="panel-title">rules + profile</div>
      <div className="flex-1 overflow-y-auto space-y-3">
        {isLoading && <div className="text-xs text-ink-muted">loading...</div>}

        <div>
          <div className="text-[10px] uppercase font-mono text-ink-muted mb-1">active rules</div>
          <div className="space-y-1.5">
            {data?.rules.map((r) => (
              <div key={r.id} className="text-[11px] text-ink-secondary border-l-2 border-accent-green/40 pl-2">
                <span className="text-accent-green font-mono">#{r.id}</span> {r.text.slice(0, 200)}
              </div>
            ))}
          </div>
        </div>

        {data?.profile && (
          <div>
            <div className="text-[10px] uppercase font-mono text-ink-muted mb-1">preference profile</div>
            <pre className="text-[10px] text-ink-secondary whitespace-pre-wrap bg-bg-raised border border-bg-border rounded p-2 max-h-48 overflow-y-auto">
              {data.profile.content}
            </pre>
          </div>
        )}

        <div>
          <div className="text-[10px] uppercase font-mono text-ink-muted mb-1">recent learnings</div>
          <div className="space-y-1.5">
            {data?.learnings.slice(0, 10).map((l) => (
              <div key={l.id} className="text-[11px] text-ink-secondary border-l-2 border-bg-border pl-2">
                <span className="text-ink-muted font-mono">{l.id}</span>
                <div className="line-clamp-3">{l.text}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function CorrectionsCol() {
  const [type, setType] = useState<'learn' | 'error' | 'request'>('learn')
  const [text, setText] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const intake = useIntakeMutation()

  const submit = async () => {
    if (!text.trim()) return
    setStatus('submitting...')
    const r = await intake.mutateAsync({ type: 'feedback', payload: text, feedbackType: type })
    if (r.status === 'ok') {
      setStatus('Captured.')
      setText('')
    } else {
      setStatus(`Error: ${r.error ?? 'unknown'}`)
    }
  }

  return (
    <div className="panel flex flex-col min-h-0">
      <div className="panel-title">corrections (feedback)</div>

      <div className="bg-bg-raised border border-bg-border rounded p-3 mb-3">
        <div className="flex gap-2 mb-2">
          {(['learn', 'error', 'request'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setType(t)}
              className={`px-2 py-1 text-[10px] uppercase font-mono rounded ${
                type === t ? 'bg-accent-blue text-bg-base' : 'text-ink-muted hover:text-ink-secondary'
              }`}
            >
              /{t}
            </button>
          ))}
        </div>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder={`What should K2B ${
            type === 'learn' ? 'remember' : type === 'error' ? 'fix' : 'be able to do'
          }?`}
          className="w-full bg-bg-base border border-bg-border rounded p-2 text-xs text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue"
        />
        <div className="flex items-center justify-between mt-2">
          <div className="text-[10px] text-ink-muted">{status ?? '\u00a0'}</div>
          <button
            onClick={submit}
            disabled={!text.trim() || intake.isPending}
            className="btn-primary flex items-center gap-1 text-xs"
          >
            <Send className="w-3 h-3" /> submit
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="text-[10px] uppercase font-mono text-ink-muted mb-1">how this works</div>
        <p className="text-[11px] text-ink-secondary">
          Submitting routes through the same k2b-feedback skill the chat /learn /error /request commands use.
          Your correction lands in System/memory/ within seconds.
        </p>
      </div>
    </div>
  )
}
