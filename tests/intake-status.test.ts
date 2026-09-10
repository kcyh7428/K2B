import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveIntakeStatus } from '../k2b-dashboard/src/server/lib/intake-status.js'
import { shouldStopIntakePolling } from '../k2b-dashboard/src/client/lib/intake-polling.js'

test('a manifest remains staged until home processing produces evidence', () => {
  assert.deepEqual(
    resolveIntakeStatus({ processed: false, errored: false, manifest: true }),
    {
      status: 'staged',
      action: 'process-on-home',
    },
  )
})

test('processed and error evidence outrank the staged manifest', () => {
  assert.deepEqual(
    resolveIntakeStatus({ processed: true, errored: true, manifest: true }),
    { status: 'done' },
  )
  assert.deepEqual(
    resolveIntakeStatus({ processed: false, errored: true, manifest: true }),
    { status: 'error' },
  )
})

test('an unknown uuid is reported as not found rather than pending sync', () => {
  assert.deepEqual(
    resolveIntakeStatus({ processed: false, errored: false, manifest: false }),
    { status: 'not-found' },
  )
})

test('polling survives a Syncthing staged to not-found transition', () => {
  assert.deepEqual(
    ['staged', 'not-found', 'done'].map((status) =>
      shouldStopIntakePolling(status as 'staged' | 'not-found' | 'done'),
    ),
    [false, false, true],
  )
  assert.equal(shouldStopIntakePolling('error'), true)
})
