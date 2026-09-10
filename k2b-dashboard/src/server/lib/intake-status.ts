export type IntakeEvidence = {
  processed: boolean
  errored: boolean
  manifest: boolean
}

export type IntakeResolvedStatus =
  | { status: 'done' }
  | { status: 'error' }
  | { status: 'staged'; action: 'process-on-home' }
  | { status: 'not-found' }

export function resolveIntakeStatus(evidence: IntakeEvidence): IntakeResolvedStatus {
  if (evidence.processed) return { status: 'done' }
  if (evidence.errored) return { status: 'error' }
  if (evidence.manifest) {
    return {
      status: 'staged',
      action: 'process-on-home',
    }
  }
  return { status: 'not-found' }
}
