import { useQuery, useMutation } from '@tanstack/react-query'

async function fetchJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${url} ${r.status}`)
  return r.json() as Promise<T>
}

export function useNow() {
  return useQuery({
    queryKey: ['now'],
    queryFn: () => fetchJson<{ priority: string; title: string; preview: string; cta: { label: string; target: string } }>('/api/now'),
  })
}

export interface ReviewItem {
  filename: string
  title: string
  type: string
  origin: string
  reviewAction: string
  reviewNotes: string
  date: string
  mtime: number
  preview: string
}

export function useReview() {
  return useQuery({
    queryKey: ['review'],
    queryFn: () => fetchJson<{ items: ReviewItem[]; count: number }>('/api/review'),
  })
}

export interface CaptureItem {
  filename: string
  layer: string
  title: string
  type: string
  origin: string
  mtime: number
  preview: string
}

export function useCapturesToday() {
  return useQuery({
    queryKey: ['captures-today'],
    queryFn: () => fetchJson<{ items: CaptureItem[]; count: number; since: number }>('/api/captures/today'),
  })
}

export function useLearningSummary() {
  return useQuery({
    queryKey: ['learning-summary'],
    queryFn: () =>
      fetchJson<{
        signals24h: number
        candidatesPending: number
        rulesChanged7d: number
        observerLastRun: string | null
        observerRunsInLast24h: number
      }>('/api/learning/summary'),
  })
}

export function useLearningSignals() {
  return useQuery({
    queryKey: ['learning-signals'],
    queryFn: () =>
      fetchJson<{
        items: { ts?: string; source: string; text: string }[]
        counts: { observations: number; preferenceSignals: number; skillUsage: number; errors: number }
      }>('/api/learning/signals'),
  })
}

export function useLearningCandidates() {
  return useQuery({
    queryKey: ['learning-candidates'],
    queryFn: () =>
      fetchJson<{ candidates: { text: string; line: number }[]; lastUpdated: number | null; raw: string }>(
        '/api/learning/candidates'
      ),
  })
}

export function useLearningRules() {
  return useQuery({
    queryKey: ['learning-rules'],
    queryFn: () =>
      fetchJson<{
        rules: { id: number; text: string }[]
        learnings: { id: string; text: string }[]
        profile: { updated: number; content: string } | null
        lastUpdated: number | null
      }>('/api/learning/rules'),
  })
}

export function useLearningRuns() {
  return useQuery({
    queryKey: ['learning-runs'],
    queryFn: () =>
      fetchJson<{
        runs: { ts?: string; model?: string; prompt?: string; response?: string }[]
        count: number
        exists: boolean
      }>('/api/learning/runs'),
  })
}

export function useVaultFlow() {
  return useQuery({
    queryKey: ['vault-flow'],
    queryFn: () =>
      fetchJson<{ layers: { raw: number; wiki: number; review: number }; logEntries24h: number; lastLogEntries: string[] }>(
        '/api/vault/flow'
      ),
  })
}

export function useScheduled() {
  return useQuery({
    queryKey: ['scheduled'],
    queryFn: () =>
      fetchJson<{
        items: { id: string; prompt: string; schedule: string; type: string; nextRun: number | null; lastRun: number | null; enabled: boolean }[]
        available: boolean
      }>('/api/scheduled'),
  })
}

export function useActivity() {
  return useQuery({
    queryKey: ['activity'],
    queryFn: () => fetchJson<{ items: { ts: string; source: string; text: string }[] }>('/api/activity'),
  })
}

export function useIntakeMutation() {
  return useMutation({
    mutationFn: async (body: { type: string; payload?: string; feedbackType?: string }) => {
      const r = await fetch('/api/intake', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      return r.json() as Promise<{
        status: 'staged' | 'ok' | 'error'
        uuid?: string
        manifestPath?: string
        error?: string
      }>
    },
  })
}

export function useAudioIntakeMutation() {
  return useMutation({
    mutationFn: async (args: { file: File; note?: string }) => {
      const fd = new FormData()
      fd.append('file', args.file)
      if (args.note && args.note.trim()) {
        fd.append('note', args.note.trim())
      }
      const r = await fetch('/api/intake/audio', { method: 'POST', body: fd })
      return r.json() as Promise<{
        status: 'staged' | 'error'
        uuid?: string
        manifestPath?: string
        error?: string
      }>
    },
  })
}

export type IntakeStatus =
  | { status: 'done'; details?: unknown }
  | { status: 'error'; error: string; details?: unknown }
  | { status: 'processing' }
  | { status: 'pending-sync' }

export async function fetchIntakeStatus(uuid: string): Promise<IntakeStatus> {
  const r = await fetch(`/api/intake/status/${uuid}`)
  if (!r.ok) throw new Error(`status ${r.status}`)
  return r.json() as Promise<IntakeStatus>
}
