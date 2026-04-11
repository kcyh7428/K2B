import { useEffect, useRef, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Link2, FileAudio, FileText, Video, Loader2, X } from 'lucide-react'
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

function humanSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function IntakeBar() {
  const [mode, setMode] = useState<Mode>('url')
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [stagedFile, setStagedFile] = useState<File | null>(null)
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

  const submitText = async () => {
    setStatus(null)
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
        setStatus("Submitted. Watch Today's Captures.")
        setValue('')
      } else {
        setStatus(`Error: ${result.error ?? 'unknown'}`)
      }
    } catch (err) {
      setStatus(`Error: ${(err as Error).message}`)
    }
  }

  const submitAudio = async () => {
    if (!stagedFile) return
    setStatus(`Uploading ${stagedFile.name}...`)
    try {
      const result = await audioIntake.mutateAsync({ file: stagedFile, note })
      if (result.status === 'staged' && result.uuid) {
        setStatus('Staged. Watching for processing...')
        setStagedFile(null)
        setNote('')
        startPolling(result.uuid)
      } else {
        setStatus(`Error: ${result.error ?? 'unknown'}`)
      }
    } catch (err) {
      setStatus(`Upload failed: ${(err as Error).message}`)
    }
  }

  // Drop STAGES the file. No upload yet. User adds context then clicks Submit.
  const onDrop = (files: File[]) => {
    if (files.length === 0) return
    setStagedFile(files[0])
    setStatus(null)
  }

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'audio/*': ['.mp3', '.m4a', '.wav', '.ogg', '.oga', '.flac'] },
    multiple: false,
    noClick: stagedFile !== null, // once staged, clicking the zone should not reopen the picker
  })

  const busy = intake.isPending || audioIntake.isPending

  const canSubmit =
    mode === 'audio' ? stagedFile !== null && !busy : !busy && value.trim().length > 0

  const doSubmit = mode === 'audio' ? submitAudio : submitText

  return (
    <div className="panel">
      <div className="flex items-center gap-2 mb-3">
        <button
          onClick={() => {
            setMode('url')
            setStatus(null)
          }}
          className={`btn flex items-center gap-2 ${mode === 'url' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <Link2 className="w-4 h-4" /> URL
        </button>
        <button
          onClick={() => {
            setMode('audio')
            setStatus(null)
          }}
          className={`btn flex items-center gap-2 ${mode === 'audio' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <FileAudio className="w-4 h-4" /> Audio
        </button>
        <button
          onClick={() => {
            setMode('text')
            setStatus(null)
          }}
          className={`btn flex items-center gap-2 ${mode === 'text' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <FileText className="w-4 h-4" /> Text
        </button>
        <button
          onClick={() => {
            setMode('fireflies')
            setStatus(null)
          }}
          className={`btn flex items-center gap-2 ${mode === 'fireflies' ? 'border-accent-blue text-accent-blue' : ''}`}
        >
          <Video className="w-4 h-4" /> Fireflies
        </button>
      </div>

      {mode === 'audio' ? (
        <div className="space-y-2">
          {stagedFile ? (
            <div className="flex items-center justify-between bg-bg-raised border border-bg-border rounded-md px-3 py-2">
              <div className="flex items-center gap-2 min-w-0">
                <FileAudio className="w-4 h-4 text-accent-blue flex-shrink-0" />
                <span className="text-sm text-ink-primary truncate">{stagedFile.name}</span>
                <span className="text-xs text-ink-muted flex-shrink-0">{humanSize(stagedFile.size)}</span>
              </div>
              <button
                onClick={() => setStagedFile(null)}
                className="text-ink-muted hover:text-ink-primary flex-shrink-0 ml-2"
                aria-label="Remove staged file"
                disabled={busy}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ) : (
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
          )}
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={
              stagedFile
                ? 'Optional context -- who is in the clip, which project, what to listen for. Then click Submit.'
                : 'Optional context (fill in after dropping the file if you prefer)'
            }
            rows={2}
            className="w-full bg-bg-raised border border-bg-border rounded-md p-3 text-sm text-ink-primary placeholder:text-ink-muted focus:outline-none focus:border-accent-blue"
          />
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
        <button
          className="btn-primary flex items-center gap-2"
          onClick={doSubmit}
          disabled={!canSubmit}
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
          Submit
        </button>
      </div>
    </div>
  )
}
