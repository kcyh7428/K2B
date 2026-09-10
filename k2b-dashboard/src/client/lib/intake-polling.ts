export type IntakePollStatus = 'done' | 'error' | 'staged' | 'not-found'

// Syncthing can briefly remove intake/<uuid> before processed/<uuid> arrives
// while replaying the home-side rename. Only durable terminal evidence stops
// polling; staged and not-found remain transitional until the UI timeout.
export function shouldStopIntakePolling(status: IntakePollStatus): boolean {
  return status === 'done' || status === 'error'
}
