/**
 * Unit tests for memoryInject.ts (Ship 1 Commit 4).
 *
 * Covers the happy path, failure modes (missing index, bad JSON, hangs),
 * formatting of the `[Memory context]` block, and the current-turn race
 * regression test mandated by the 2026-04-23b spec update that ratified
 * the fire-and-forget Gate's future-turn-only contract. Inject reads
 * from the semantic shelf snapshot at call time and does NOT wait for
 * the current turn's normalizationGate to finish writing its row.
 */

import { describe, it, expect } from 'vitest'
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { injectMemoryFromShelves } from './memoryInject.js'
import { normalizationGate } from './washingMachine.js'

interface SpawnCall {
  cmd: string
  args: string[]
}

interface SpawnResult {
  stdout?: string
  stderr?: string
  code?: number | null
  /** When true, never emits close -- exercises the timeout path. */
  hang?: boolean
}

interface SpawnHarness {
  calls: SpawnCall[]
  makeSpawn(): (cmd: string, args: readonly string[]) => unknown
}

function makeSpawnHarness(
  handler: (cmd: string, args: string[]) => SpawnResult
): SpawnHarness {
  const calls: SpawnCall[] = []
  return {
    calls,
    makeSpawn() {
      return (cmd: string, args: readonly string[]) => {
        const copyArgs = [...args]
        calls.push({ cmd, args: copyArgs })
        const stdout = new PassThrough()
        const stderr = new PassThrough()
        const emitter: EventEmitter & {
          stdin: null
          stdout: PassThrough
          stderr: PassThrough
          kill: () => boolean
        } = Object.assign(new EventEmitter(), {
          stdin: null,
          stdout,
          stderr,
          kill: () => {
            // SIGKILL arrives from inject's timeout path -- emit a close so
            // the capture promise can settle. Matches Node's child_process
            // semantics where the runtime reaps a killed child.
            setImmediate(() => emitter.emit('close', null))
            return true
          },
        })
        const result = handler(cmd, copyArgs)
        if (result.hang) {
          // Never write stdout, never emit close; let timeout drive the flow.
          return emitter
        }
        setImmediate(() => {
          stdout.end(result.stdout ?? '')
          stderr.end(result.stderr ?? '')
          setImmediate(() => emitter.emit('close', result.code ?? 0))
        })
        return emitter
      }
    },
  }
}

function mockRetrieveRows(
  rows: Array<{ row_text: string; slug?: string; score?: number }>
): SpawnHarness {
  return makeSpawnHarness((_cmd, args) => {
    if (!args.some((a) => a.endsWith('retrieve.py'))) {
      return { stdout: '', stderr: 'unexpected script', code: 127 }
    }
    return { stdout: JSON.stringify(rows), code: 0 }
  })
}

const DR_LO_ROW = {
  slug: 'person_Dr-Lo-Hak-Keung',
  row_text:
    '2026-04-01 | contact | person_Dr-Lo-Hak-Keung | name:Dr. Lo Hak Keung | tel:2830 3709 | whatsapp:9861 9017 | role:Urology | organization:St. Paul\'s Hospital',
  score: 0.42,
}

const THIS_TURN_DR_SMITH_ROW = {
  slug: 'person_Dr-Smith',
  row_text:
    '2026-04-23 | contact | person_Dr-Smith | name:Dr. Smith | tel:5555 1234 | role:Dentist',
  score: 0.30,
}

describe('injectMemoryFromShelves -- basic behaviour', () => {
  it('returns empty string for an empty query (no spawn)', async () => {
    const harness = makeSpawnHarness(() => {
      throw new Error('spawn should not be invoked for empty query')
    })
    const out = await injectMemoryFromShelves('', { spawnImpl: harness.makeSpawn() as never })
    expect(out).toBe('')
    expect(harness.calls).toHaveLength(0)
  })

  it('returns empty string for whitespace-only query', async () => {
    const harness = makeSpawnHarness(() => {
      throw new Error('spawn should not be invoked')
    })
    const out = await injectMemoryFromShelves('   \n\t ', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('returns empty string when retrieve.py emits []', async () => {
    const harness = mockRetrieveRows([])
    const out = await injectMemoryFromShelves('who is dr lo', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('formats rows under a [Memory context] block with one bullet per row', async () => {
    const harness = mockRetrieveRows([
      { row_text: 'row alpha' },
      { row_text: 'row beta' },
    ])
    const out = await injectMemoryFromShelves('anything', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('[Memory context]\n- row alpha\n- row beta\n\n')
  })

  it('passes query + --shelf semantic + --k to retrieve.py', async () => {
    const harness = mockRetrieveRows([DR_LO_ROW])
    await injectMemoryFromShelves("what's my doctor phone", {
      spawnImpl: harness.makeSpawn() as never,
      k: 7,
    })
    expect(harness.calls).toHaveLength(1)
    const call = harness.calls[0]
    expect(call.args.some((a) => a.endsWith('retrieve.py'))).toBe(true)
    expect(call.args).toContain("what's my doctor phone")
    expect(call.args).toContain('--shelf')
    expect(call.args).toContain('semantic')
    expect(call.args).toContain('--k')
    expect(call.args).toContain('7')
  })

  it('surfaces Dr. Lo row text (doctor-phone MVP keyword path)', async () => {
    const harness = mockRetrieveRows([DR_LO_ROW])
    const out = await injectMemoryFromShelves('whats my doctor phone', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toContain('2830 3709')
    expect(out).toContain('Dr. Lo Hak Keung')
    expect(out.startsWith('[Memory context]\n')).toBe(true)
  })

  it('filters out rows without a string row_text field', async () => {
    // Mix a valid row with shapes that retrieve.py wouldn't emit in practice
    // but which inject should survive without throwing (defence in depth).
    const badShape = [
      { row_text: 'valid row' },
      { row_text: 42 as unknown as string },
      { something_else: 'no row_text key' } as unknown as { row_text: string },
    ]
    const harness = mockRetrieveRows(badShape)
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toContain('valid row')
    expect(out).not.toContain('42')
    expect(out).not.toContain('something_else')
  })
})

describe('injectMemoryFromShelves -- failure modes (never throws)', () => {
  it('returns empty string when retrieve.py exits non-zero', async () => {
    const harness = makeSpawnHarness(() => ({
      stdout: '',
      stderr: 'retrieve: sentence-transformers not importable',
      code: 3,
    }))
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('returns empty string when retrieve.py stdout is not JSON', async () => {
    const harness = makeSpawnHarness(() => ({ stdout: 'not json at all\n', code: 0 }))
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('returns empty string when retrieve.py returns a non-array JSON value', async () => {
    const harness = makeSpawnHarness(() => ({ stdout: '{"oops":"object"}', code: 0 }))
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('returns empty string and kills the child when retrieve.py hangs past timeout', async () => {
    const harness = makeSpawnHarness(() => ({ hang: true }))
    const started = Date.now()
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
      timeoutMs: 50,
    })
    const elapsedMs = Date.now() - started
    expect(out).toBe('')
    // Bounded latency: 50ms timeout should not take > 2s even under test jitter.
    expect(elapsedMs).toBeLessThan(2_000)
  })

  it('returns empty string when retrieve.py stdout exceeds the 1MB byte cap', async () => {
    // Simulates a corrupt shelf or runaway retriever response: a single
    // massive row_text pushes stdout past the MAX_RETRIEVE_STDOUT_BYTES
    // ceiling. Inject must kill the child and return '' rather than
    // hoarding a multi-megabyte string in Node heap.
    const giantRow = { row_text: 'x'.repeat(2_000_000) }
    const harness = mockRetrieveRows([giantRow])
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('counts BYTES not UTF-16 code units when enforcing the stdout cap (Chinese/emoji safe)', async () => {
    // Every Chinese character is 3 bytes UTF-8 but 1 code unit UTF-16. A
    // naive `stdout.length > cap` check would let ~3MB of Chinese through
    // the 1MB cap before firing. 400_000 Chinese chars = ~1.2MB UTF-8,
    // which must trigger the cap. This test regresses if a future refactor
    // switches the guard back to stdout.length.
    const chineseRow = { row_text: '\u7f85\u514b\u5f37\u91ab\u751f'.repeat(80_000) }
    const harness = mockRetrieveRows([chineseRow])
    const out = await injectMemoryFromShelves('query', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(out).toBe('')
  })
})

describe('injectMemoryFromShelves -- current-turn race (future-turn-only contract 2026-04-23b)', () => {
  // Gate contract (ratified 2026-04-23b after Codex Tier 3 on Commit 3):
  // facts in message N do NOT affect message N's reply. The gate runs
  // fire-and-forget; inject must read the shelf snapshot at call time and
  // NEVER wait for the current turn's classifier/shelf-write to finish.
  //
  // These tests prove the contract holds for Commit 4's injectMemoryFromShelves.

  it('inject resolves while real normalizationGate is still in-flight (handleMessage orchestration)', async () => {
    // This test replicates bot.ts handleMessage's gate-then-inject pattern
    // verbatim -- using the REAL normalizationGate, not a hand-rolled promise,
    // so a future refactor that re-introduced a shared promise, lock, or
    // shelf-write coordination between them would fail here. The gate's
    // classifier spawn is wired to a slow-mock that never emits close, so
    // gatePromise is guaranteed to be pending while inject runs.

    // (A) Hung classifier spawn for the gate. Never writes stdout, never
    // emits close. normalizationGate must be relying on its own timeout,
    // not on inject, to settle -- that's the point.
    const hungGateCalls: SpawnCall[] = []
    const hungGateSpawn = (cmd: string, args: readonly string[]) => {
      const copy = [...args]
      hungGateCalls.push({ cmd, args: copy })
      const stdout = new PassThrough()
      const stderr = new PassThrough()
      const stdin = new PassThrough()
      // Drain stdin so normalizationGate's stdin.end() call doesn't back-pressure.
      stdin.resume()
      const emitter = Object.assign(new EventEmitter(), {
        stdin,
        stdout,
        stderr,
        kill: () => {
          setImmediate(() => emitter.emit('close', null))
          return true
        },
      })
      // Never emit close unless kill fires. This models a hung classifier.
      return emitter as unknown
    }

    // Fire the gate exactly like bot.ts does: fire-and-forget with its own
    // classifier timeout generous enough to outlive the inject call.
    const gatePromise = normalizationGate('I prefer tea over coffee', {
      spawnImpl: hungGateSpawn as never,
      classifierTimeoutMs: 30_000,
      normalizeTimeoutMs: 30_000,
    }).catch(() => undefined)

    // Inject runs on a separate spawn mock (retrieve.py). Same bot.ts pattern.
    const retrieveHarness = mockRetrieveRows([DR_LO_ROW])

    const injectStarted = Date.now()
    const injected = await Promise.race([
      injectMemoryFromShelves('whats my doctor phone', {
        spawnImpl: retrieveHarness.makeSpawn() as never,
      }),
      new Promise<string>((_, reject) =>
        setTimeout(() => reject(new Error('inject blocked on gate')), 2_000)
      ),
    ])
    const elapsedMs = Date.now() - injectStarted

    expect(injected).toContain('2830 3709')
    expect(elapsedMs).toBeLessThan(2_000)
    // The gate's classifier spawn was triggered; the gate is still in-flight
    // (its hung subprocess has not closed). The only way this assertion holds
    // is if inject ran to completion without waiting on the gate.
    expect(hungGateCalls.length).toBeGreaterThan(0)

    // Clean up the hung gate: the hung classifier mock's kill() emits close,
    // so awaiting gatePromise here resolves once the gate's own timeout fires
    // SIGKILL. Cap the await so a regression in the gate doesn't hang CI.
    await Promise.race([
      gatePromise,
      new Promise<void>((resolve) => setTimeout(resolve, 100)),
    ])
  })

  it('pre-gate and post-gate shelf snapshots both surface the pre-existing Dr. Lo row', async () => {
    // Scenario A: retrieve.py sees only the pre-existing shelf state -- the
    // current-turn gate has not yet written its row. This is the nominal
    // fire-and-forget timeline where classifier latency (~5-8s) exceeds
    // retrieval latency (~50ms-1s).
    const preGate = mockRetrieveRows([DR_LO_ROW])
    const outA = await injectMemoryFromShelves('whats my doctor phone', {
      spawnImpl: preGate.makeSpawn() as never,
    })
    expect(outA).toContain('2830 3709')
    expect(outA).not.toContain('5555 1234')

    // Scenario B: retrieve.py happens to see post-gate state -- the gate
    // finished unusually fast and its row is already in the shelf. Inject
    // still faithfully returns the current snapshot; the contract is
    // enforced at retrieval order (inject runs in parallel, never awaits
    // the gate) rather than by filtering the shelf post-hoc.
    const postGate = mockRetrieveRows([DR_LO_ROW, THIS_TURN_DR_SMITH_ROW])
    const outB = await injectMemoryFromShelves('whats my doctor phone', {
      spawnImpl: postGate.makeSpawn() as never,
    })
    // The pre-existing Dr. Lo row is ALWAYS present under both timings --
    // inject's correctness is a function of the shelf snapshot, not gate
    // timing. THAT is what "race-free" means for the future-turn-only
    // contract: inject does not conditionally include rows based on whether
    // the current turn's gate ran first.
    expect(outB).toContain('2830 3709')
  })

  it('only spawns retrieve.py -- never the classifier, normalizer, or shelf-writer', async () => {
    // Structural invariant. A future refactor might introduce a classifier
    // call or a shelf write from the inject path; this test catches it.
    // Race-freedom depends on inject touching only the READ subprocess.
    const harness = mockRetrieveRows([DR_LO_ROW])
    await injectMemoryFromShelves('any query text', {
      spawnImpl: harness.makeSpawn() as never,
    })
    expect(harness.calls.length).toBeGreaterThan(0)
    for (const call of harness.calls) {
      const joined = call.args.join(' ')
      expect(joined).toMatch(/retrieve\.py/)
      expect(joined).not.toMatch(/classify\.sh/)
      expect(joined).not.toMatch(/normalize\.py/)
      expect(joined).not.toMatch(/shelf-writer\.sh/)
    }
  })
})
