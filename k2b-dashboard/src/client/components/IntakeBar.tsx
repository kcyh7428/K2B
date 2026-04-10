import { useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Link2, FileAudio, FileText, Video, Loader2 } from 'lucide-react'
import { useIntakeMutation, useAudioIntakeMutation } from '../hooks/api'

type Mode = 'url' | 'audio' | 'text' | 'fireflies'

export default function IntakeBar() {
  const [mode, setMode] = useState<Mode>('url')
  const [value, setValue] = useState('')
  const [status, setStatus] = useState<string | null>(null)
  const intake = useIntakeMutation()
  const audioIntake = useAudioIntakeMutation()

  const submit = async () => {
    setStatus(null)
    if (mode === 'audio') {
      setStatus('Use the drop zone for audio files.')
      return
    }
    if (!value.trim()) return
    const body = mode === 'fireflies'
      ? { type: 'fireflies', payload: value }
      : { type: mode, payload: value }
    const result = await intake.mutateAsync(body)
    if (result.status === 'ok') {
      setStatus('Submitted. Watch Today\'s Captures.')
      setValue('')
    } else {
      setStatus(`Error: ${result.error ?? 'unknown'}`)
    }
  }

  const onDrop = async (files: File[]) => {
    if (files.length === 0) return
    setStatus(`Uploading ${files[0].name}...`)
    const result = await audioIntake.mutateAsync(files[0])
    if (result.status === 'ok') {
      setStatus('Transcribed and processed.')
    } else {
      setStatus(`Error: ${result.error ?? 'unknown'}`)
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
