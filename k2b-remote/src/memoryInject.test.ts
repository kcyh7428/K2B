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

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { injectMemoryFromShelves, resolveWashingMachinePython } from './memoryInject.js'
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

describe('resolveWashingMachinePython -- env-file fallback (Ship 1 Commit 5 fix)', () => {
  // Regression coverage for the 2026-04-23 Ship 1 MVP failure. pm2 on Mac
  // Mini did not have WASHING_MACHINE_PYTHON in its captured env, so the
  // resolver fell through to system python3 (no sentence-transformers) and
  // retrieve.py exited 3 on every call. Inject then swallowed the error
  // per its graceful-degradation contract, returning '' and skipping the
  // [Memory context] block entirely. Keith's doctor-phone query then fell
  // through to an Obsidian search tool call, blowing Condition 5. The
  // resolver now reads ~/.config/k2b/washing-machine.env as a fallback
  // before giving up on 'python3'. Validation layer (existence + trim)
  // added per MiniMax Checkpoint 2 HIGH-1 + HIGH-2 on the same commit.
  const originalEnvValue = process.env.WASHING_MACHINE_PYTHON

  beforeEach(() => {
    delete process.env.WASHING_MACHINE_PYTHON
  })

  afterEach(() => {
    if (originalEnvValue === undefined) {
      delete process.env.WASHING_MACHINE_PYTHON
    } else {
      process.env.WASHING_MACHINE_PYTHON = originalEnvValue
    }
  })

  // Default exists-check stub: every path looks like a valid file. Tests that
  // exercise the stale-path fall-through override this per-call.
  const existsAlways = () => true

  it('prefers process.env.WASHING_MACHINE_PYTHON over the env file', () => {
    process.env.WASHING_MACHINE_PYTHON = '/from/env/python3'
    const reader = () => 'export WASHING_MACHINE_PYTHON="/from/file/python3"\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('/from/env/python3')
  })

  it('reads WASHING_MACHINE_PYTHON from the env file when process.env is empty', () => {
    const reader = () =>
      '# Generated by scripts/washing-machine/preflight.sh\n' +
      'export WASHING_MACHINE_PYTHON="/Users/fastshower/Projects/K2B/venv/washing-machine/bin/python3"\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe(
      '/Users/fastshower/Projects/K2B/venv/washing-machine/bin/python3',
    )
  })

  it('parses unquoted values and bare assignment (no `export` prefix)', () => {
    const reader = () => 'WASHING_MACHINE_PYTHON=/opt/k2b/venv/bin/python3\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('/opt/k2b/venv/bin/python3')
  })

  it('falls back to system python3 when the env file is missing', () => {
    const reader = () => {
      const err = new Error('ENOENT: no such file or directory') as NodeJS.ErrnoException
      err.code = 'ENOENT'
      throw err
    }
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('falls back to python3 on non-ENOENT reader errors (EACCES / EIO)', () => {
    // The reader's try/catch is intentionally broad -- any filesystem error
    // falls through to system python3. A narrowed catch clause in a future
    // refactor would silently break this fallback; the test locks it in.
    // MiniMax Checkpoint 2 round-3 LOW-2.
    const reader = () => {
      const err = new Error('EACCES: permission denied') as NodeJS.ErrnoException
      err.code = 'EACCES'
      throw err
    }
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('falls back to python3 when env file exists but has no WASHING_MACHINE_PYTHON line', () => {
    const reader = () => '# unrelated env file\nUNRELATED_VAR=foo\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('ignores commented-out WASHING_MACHINE_PYTHON lines', () => {
    // Line-anchor match means a `#` comment at column 0 should NOT be picked up
    // as a valid assignment. Defensive against someone commenting out the line
    // during debugging without removing it.
    const reader = () =>
      '# export WASHING_MACHINE_PYTHON="/old/path/python3"\n' +
      '#WASHING_MACHINE_PYTHON=/old/path/python3\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('handles trailing whitespace in the env file value', () => {
    const reader = () => 'export WASHING_MACHINE_PYTHON="/venv/bin/python3"   \n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('/venv/bin/python3')
  })

  // --- HIGH-2 coverage: whitespace / empty / quoted-empty values ---

  it('falls through to python3 when env-file value is empty-quoted', () => {
    // `WASHING_MACHINE_PYTHON=""` would otherwise capture nothing AND the regex
    // fails to match at all (reluctant `[^"]+?` requires >=1 char). Confirm
    // we fall through cleanly rather than returning ''.
    const reader = () => 'export WASHING_MACHINE_PYTHON=""\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('falls through to python3 when env-file value is whitespace-only (quoted)', () => {
    // Without the .trim() guard, the regex captures `   ` (3 spaces) and
    // returns that as the Python path. spawn("   ", [...]) then fails with
    // an opaque ENOENT inside the graceful-degradation path, masking the
    // config bug. MiniMax Checkpoint 2 HIGH-2 regression.
    const reader = () => 'export WASHING_MACHINE_PYTHON="   "\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('falls through to python3 when env-file value has no content after the `=`', () => {
    const reader = () => 'WASHING_MACHINE_PYTHON=\n'
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('python3')
  })

  it('falls through when process.env value is whitespace-only', () => {
    process.env.WASHING_MACHINE_PYTHON = '   '
    const reader = () => 'export WASHING_MACHINE_PYTHON="/from/file/python3"\n'
    // process.env is whitespace, trim -> '' -> falsy -> skip to env-file fallback.
    expect(resolveWashingMachinePython(reader, existsAlways)).toBe('/from/file/python3')
  })

  // --- HIGH-1 coverage: existence validation on both process.env and env-file paths ---

  it('falls through to env-file path when process.env path does not exist', () => {
    process.env.WASHING_MACHINE_PYTHON = '/deleted/venv/bin/python3'
    const reader = () => 'export WASHING_MACHINE_PYTHON="/good/venv/bin/python3"\n'
    const existsCheck = (p: string) => p === '/good/venv/bin/python3'
    expect(resolveWashingMachinePython(reader, existsCheck)).toBe('/good/venv/bin/python3')
  })

  it('falls through to python3 when env-file path does not exist on disk', () => {
    // Stale env file pointing at a deleted venv. Without the existsCheck
    // guard this would return the bogus path and spawn would ENOENT inside
    // the graceful-degradation path -- same class of bug as HIGH-2.
    const reader = () => 'export WASHING_MACHINE_PYTHON="/stale/deleted/python3"\n'
    const existsCheck = () => false
    expect(resolveWashingMachinePython(reader, existsCheck)).toBe('python3')
  })

  it('returns process.env path directly when it exists (happy path)', () => {
    process.env.WASHING_MACHINE_PYTHON = '/live/venv/bin/python3'
    const reader = () => {
      throw new Error('env-file reader should not be called when process.env is valid')
    }
    const existsCheck = (p: string) => p === '/live/venv/bin/python3'
    expect(resolveWashingMachinePython(reader, existsCheck)).toBe('/live/venv/bin/python3')
  })

  // MiniMax Checkpoint 2 round-2 HIGH-1: non-executable file should be
  // treated as absent, not returned as a live path. Exercises the default
  // existsCheck via a real tmp file whose exec bit we control.
  it('default existsCheck rejects files without the exec bit', async () => {
    const { mkdtemp, writeFile, chmod, rm } = await import('node:fs/promises')
    const { tmpdir } = await import('node:os')
    const { join } = await import('node:path')
    const dir = await mkdtemp(join(tmpdir(), 'wmm-resolver-'))
    const tmpPath = join(dir, 'python3')
    try {
      await writeFile(tmpPath, '#!/bin/sh\necho stub\n')
      await chmod(tmpPath, 0o644) // readable but NOT executable
      process.env.WASHING_MACHINE_PYTHON = tmpPath
      const reader = () => 'export WASHING_MACHINE_PYTHON="/ignored"\n'
      // Default existsCheck rejects non-exec -> falls through env-file
      // branch (reader returns a path, default existsCheck rejects '/ignored'
      // for missing) -> returns python3.
      expect(resolveWashingMachinePython(reader)).toBe('python3')
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
