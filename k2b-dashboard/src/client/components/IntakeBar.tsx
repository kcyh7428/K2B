import { useEffect, useRef, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Link2, FileAudio, FileText, Video, Loader2 } from 'lucide-react'
import {
  useIntakeMutation,
  useAudioIntakeMutation,
  fetchIntakeStatus,
  IntakeStatus,
} from '../hooks/api'

type Mode = 'url' | 'audio' | 'text' | 'fireflies'

// Poll the status endpoint every 5s, up to 2 minutes, reporting real progress.
const POLL_INTERVAL_MS = 5_000
const POLL_TIMEOUT_MS = 120_000

function formatStatus(s: IntakeStatus): string {
  switch (s.status) {
    case 'done':
      return 'Done. Processed by Mac Mini.'
    case 'error':
      return `Error: ${s.error}`
    case 'processing':
      return 'Processing on Mac Mini...'
    case 'pending-sync':
      return 'Staged. Waiting for Syncthing to deliver to Mac Mini...'
  }
}

export default function IntakeBar() {
  const [mode, setMode] = useState<Mode>('url')
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const pollStart = useRef<number>(0)
  const intake = useIntakeMutation()
  const audioIntake = useAudioIntakeMutation()

  const stopPolling = () => {
    if (pollTimer.current) {
      clearInterval(pollTimer.current)
      pollTimer.current = null
    }
  }

  useEffect(() => stopPolling, [])

  const startPolling = (uuid: string) => {
    stopPolling()
    pollStart.current = Date.now()
    const tick = async () => {
      try {
        const result = await fetchIntakeStatus(uuid)
        setStatus(formatStatus(result))
        if (result.status === 'done' || result.status === 'error') {
          stopPolling()
          return
        }
        if (Date.now() - pollStart.current > POLL_TIMEOUT_MS) {
          setStatus('Still waiting after 2 min. Check pm2 logs on Mac Mini.')
          stopPolling()
        }
      } catch (err) {
        setStatus(`Status check failed: ${(err as Error).message}`)
        stopPolling()
      }
    }
    pollTimer.current = setInterval(tick, POLL_INTERVAL_MS)
    // Fire first tick immediately
    tick()
  }

  const submit = async () => {
    setStatus(null)
    if (mode === 'audio') {
      setStatus('Use the drop zone for audio files.')
      return
    }
    if (!value.trim()) return
    const body =
      mode === 'fireflies'
        ? { type: 'fireflies', payload: value }
        : { type: mode, payload: value }
    try {
      const result = await intake.mutateAsync(body)
      if (result.status === 'staged' && result.uuid) {
        setStatus('Staged. Watching for processing...')
        setValue('')
        startPolling(result.uuid)
      } else if (result.status === 'ok') {
        // Backwards-compat in case something still returns the old shape
        setStatus("Submitted. Watch Today's Captures.")
        setValue('')
      } else {
        setStatus(`Error: ${result.error ?? 'unknown'}`)
      }
    } catch (err) {
      setStatus(`Error: ${(err as Error).message}`)
    }
  }

  const onDrop = async (files: File[]) => {
    if (files.length === 0) return
    setStatus(`Uploading ${files[0].name}...`)
    try {
      const result = await audioIntake.mutateAsync({ file: files[0], note })
      if (result.status === 'staged' && result.uuid) {
        setStatus('Staged. Watching for processing...')
        setNote('')
        startPolling(result.uuid)
      } else {
        setStatus(`Error: ${result.error ?? 'unknown'}`)
      }
    } catch (err) {
      setStatus(`Upload failed: ${(err as Error).message}`)
    }
  }

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'audio/*': ['.mp3', '.m4a', '.wav', '.ogg', '.oga', '.flac'] },
    multiple: false,
  })

  const busy = intake.isPending || audioIntake.isPending

  return (
    <div className="panel">
      <div className="flex items-center gap-2 mb-3">
        <button
          onClick={() => setMode('url')}
          className={`btn flex items-center gap-2 ${mode === 'url' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <Link2 className="w-4 h-4" /> URL
        </button>
        <button
          onClick={() => setMode('audio')}
          className={`btn flex items-center gap-2 ${mode === 'audio' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <FileAudio className="w-4 h-4" /> Audio
        </button>
        <button
          onClick={() => setMode('text')}
          className={`btn flex items-center gap-2 ${mode === 'text' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <FileText className="w-4 h-4" /> Text
        </button>
        <button
          onClick={() => setMode('fireflies')}
          className={`btn flex items-center gap-2 ${mode === 'fireflies' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <Video className="w-4 h-4" /> Fireflies
        </button>
      </div>

      {mode === 'audio' ? (
        <div className="space-y-2">
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional context -- what is this, who, which project? K2B uses this to understand the clip."
            rows={2}
            className="w-full bg-bg-raised border border-bg-border rounded-md p-3 text-sm text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue"
          />
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-md p-6 text-center cursor-pointer transition-colors ${
              isDragActive ? 'border-accent-blue bg-bg-raised' : 'border-bg-border'
            }`}
          >
            <input {...getInputProps()} />
            <FileAudio className="w-8 h-8 mx-auto mb-2 text-ink-muted" />
            <div className="text-sm text-ink-secondary">
              {isDragActive ? 'Drop the audio file here' : 'Drop audio file or click to select'}
            </div>
            <div className="text-xs text-ink-muted mt-1">mp3 / m4a / wav / ogg, up to 100MB</div>
          </div>
        </div>
      ) : mode === 'text' ? (
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Jot a thought..."
          rows={3}
          className="w-full bg-bg-raised border border-bg-border rounded-md p-3 text-sm text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue"
        />
      ) : (
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={mode === 'url' ? 'Paste a URL (YouTube, article, GitHub, tweet)' : 'Fireflies meeting URL or ID'}
          className="w-full bg-bg-raised border border-bg-border rounded-md p-3 text-sm text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue"
        />
      )}

      <div className="flex items-center justify-between mt-3">
        <div className="text-xs text-ink-muted min-h-[1em]">{status ?? '\u00a0'}</div>
        {mode !== 'audio' && (
          <button className="btn-primary flex items-center gap-2" onClick={submit} disabled={busy || !value.trim()}>
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            Submit
          </button>
        )}
      </div>
    </div>
  )
}
