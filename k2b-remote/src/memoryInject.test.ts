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
import {
  injectMemoryFromShelves,
  injectVaultContext,
  looksLikeFactRetrievalQuery,
  pickVaultFallbackTerm,
  resolveWashingMachinePython,
  truncateOnWordBoundary,
} from './memoryInject.js'
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

// ============================================================================
// injectVaultContext (feature_vault-notes-fallback, 2026-05-23)
//
// Vault-notes fallback fires when the shelf inject returned empty AND the
// user message looks like fact retrieval. These tests cover the fact-retrieval
// heuristic, the exact-query / fallback-term path, snippet formatting, the
// top-K cap, and the graceful-degradation contract.
// ============================================================================

function makeGrepHarness(
  exactHits: VaultGrepHit[] | Error,
  fallbackHits: VaultGrepHit[] | Error = [],
): { spawn: SpawnHarness; calls: SpawnCall[] } {
  let callIdx = 0
  const harness = makeSpawnHarness((cmd, args) => {
    if (cmd !== 'grep') {
      return { stdout: '', stderr: 'unexpected cmd', code: 127 }
    }
    const current = callIdx === 0 ? exactHits : fallbackHits
    callIdx += 1
    if (current instanceof Error) {
      return { stdout: '', stderr: current.message, code: 2 }
    }
    if (current.length === 0) {
      // grep exits 1 on no matches
      return { stdout: '', stderr: '', code: 1 }
    }
    const lines = current.map((h) => `${h.file}:${h.line}:${h.text}`)
    return { stdout: lines.join('\n') + '\n', stderr: '', code: 0 }
  })
  return { spawn: harness, calls: harness.calls }
}

interface VaultGrepHit {
  file: string
  line: number
  text: string
}

describe('looksLikeFactRetrievalQuery', () => {
  it('returns true for messages starting with a fact-retrieval question word + "?"', () => {
    expect(looksLikeFactRetrievalQuery('what did we decide about Andrew?')).toBe(true)
    expect(looksLikeFactRetrievalQuery('who is Dr Lo?')).toBe(true)
  })

  it('returns true for messages starting with a question word (no "?" required)', () => {
    expect(looksLikeFactRetrievalQuery('what did we decide about TalentSignals tier')).toBe(true)
    expect(looksLikeFactRetrievalQuery('who is Dr Lo')).toBe(true)
    expect(looksLikeFactRetrievalQuery('where did I leave my notes on K2Bi')).toBe(true)
    expect(looksLikeFactRetrievalQuery('how did Phase G end')).toBe(true)
  })

  it('returns true for "tell me" / "remind me" shapes', () => {
    expect(looksLikeFactRetrievalQuery('tell me about TalentSignals')).toBe(true)
    expect(looksLikeFactRetrievalQuery('remind me what we decided')).toBe(true)
  })

  it('returns false for command-shaped messages (Codex MEDIUM #2 -- command verb prefix)', () => {
    expect(looksLikeFactRetrievalQuery('create a note about TalentSignals')).toBe(false)
    expect(looksLikeFactRetrievalQuery('search vault for the meeting')).toBe(false)
    expect(looksLikeFactRetrievalQuery('save this conversation')).toBe(false)
    expect(looksLikeFactRetrievalQuery('draft a LinkedIn post')).toBe(false)
    expect(looksLikeFactRetrievalQuery('send the email')).toBe(false)
    expect(looksLikeFactRetrievalQuery('summarize this thread')).toBe(false)
  })

  it('returns false for polite-command forms (Codex MEDIUM #2 -- "can you draft" etc.)', () => {
    expect(looksLikeFactRetrievalQuery('can you draft a reply?')).toBe(false)
    expect(looksLikeFactRetrievalQuery('could you save this?')).toBe(false)
    expect(looksLikeFactRetrievalQuery('would you send the email?')).toBe(false)
    expect(looksLikeFactRetrievalQuery('will you summarize this?')).toBe(false)
    expect(looksLikeFactRetrievalQuery('please send the agenda')).toBe(false)
  })

  it('returns false for bare "?" alone -- requires a question word too (Codex MEDIUM #2)', () => {
    // Before the fix, ANY "?" triggered. Now the message must START with a
    // recognised question word (or "tell me" / "remind me") to qualify.
    expect(looksLikeFactRetrievalQuery('any updates?')).toBe(false)
    expect(looksLikeFactRetrievalQuery('TalentSignals?')).toBe(false)
  })

  it('strips URLs before heuristic check (Codex MEDIUM #2 -- YouTube ?v= must not trigger)', () => {
    // A bare YouTube URL contains "?v=" which under the old heuristic triggered
    // the question-mark path. With URL stripping, the "?" is gone and the
    // remaining text ("") fails the question-word check.
    expect(looksLikeFactRetrievalQuery('https://www.youtube.com/watch?v=PeqDWP_2zPE')).toBe(false)
    // URL + a real question still triggers because the question word survives.
    expect(looksLikeFactRetrievalQuery('what was this https://example.com/x?y=1 about?')).toBe(true)
  })

  it('returns false on empty or whitespace-only input', () => {
    expect(looksLikeFactRetrievalQuery('')).toBe(false)
    expect(looksLikeFactRetrievalQuery('   ')).toBe(false)
  })

  it('does not mis-trigger on words that contain a question word as a substring', () => {
    // "whatever" starts with "what" but the \b should prevent the regex match.
    expect(looksLikeFactRetrievalQuery('whatever happens, ship it')).toBe(false)
  })
})

describe('pickVaultFallbackTerm', () => {
  it('returns the longest non-stopword token', () => {
    expect(pickVaultFallbackTerm('what did we decide about TalentSignals tier')).toBe('talentsignals')
  })

  it('returns null when only stopwords remain', () => {
    expect(pickVaultFallbackTerm('what did we do')).toBe(null)
    expect(pickVaultFallbackTerm('')).toBe(null)
  })

  it('ignores tokens shorter than 4 chars', () => {
    expect(pickVaultFallbackTerm('what is AI for HR')).toBe(null)
  })

  it('handles hyphenated terms by lowercasing and keeping them', () => {
    expect(pickVaultFallbackTerm('what about washing-machine memory')).toMatch(/washing|machine|memory/)
  })
})

describe('truncateOnWordBoundary', () => {
  it('returns the string unchanged when within the limit', () => {
    expect(truncateOnWordBoundary('short text', 200)).toBe('short text')
  })

  it('truncates at the nearest preceding word boundary', () => {
    const long = 'park TalentSignals enterprise tier per Andrew strategy call decision'
    const out = truncateOnWordBoundary(long, 30)
    expect(out.length).toBeLessThanOrEqual(34) // 30 + '...'
    expect(out.endsWith('...')).toBe(true)
    // Word-boundary proof: stripping '...' must yield a strict prefix of the
    // original, and the character following that prefix in the original must
    // be whitespace -- proves the cut landed at a space, not mid-word.
    const trimmed = out.slice(0, -3)
    expect(long.startsWith(trimmed)).toBe(true)
    expect(long[trimmed.length]).toBe(' ')
  })

  it('falls back to explicit [cut] marker when no boundary fits in budget (Codex MEDIUM #4)', () => {
    // 60% of 10 = 6. Single 19-char token with NO whitespace or punctuation
    // can't be cut cleanly. We mark the truncation as mid-token so the agent
    // doesn't mistake the cut for a complete word.
    const out = truncateOnWordBoundary('verylongsingletoken', 10)
    expect(out.endsWith('[cut]...')).toBe(true)
    // 10 chars + '[cut]...' (8 chars) = 18 max
    expect(out.length).toBeLessThanOrEqual(18)
  })

  it('accepts punctuation as a boundary (Codex MEDIUM #4 -- not just ASCII space)', () => {
    // Comma-separated list. Old code would have refused to truncate here
    // since lastIndexOf(' ') returns -1; new code treats commas as boundaries.
    const s = 'alpha,beta,gamma,delta,epsilon,zeta,eta,theta'
    const out = truncateOnWordBoundary(s, 20)
    expect(out.endsWith('...')).toBe(true)
    expect(out).not.toContain('[cut]')
    // Should cut at one of the commas, not mid-word
    const trimmed = out.slice(0, -3)
    expect(s.startsWith(trimmed)).toBe(true)
  })
})

describe('injectVaultContext -- URL bypass (Codex round 2 MEDIUM)', () => {
  it('returns empty string when the raw message contains a URL', async () => {
    // bot.ts runs YouTube prefetch AFTER injectVaultContext, which provides
    // its own transcript context fenced with its own sentinel. Adding vault
    // hits on top would pollute the prompt with unrelated grep matches.
    // URL-bearing turns belong entirely to the URL-prefetch path.
    const harness = makeGrepHarness([
      { file: '/vault/raw/x.md', line: 1, text: 'should not surface' },
    ])
    const out = await injectVaultContext(
      'what was this https://youtube.com/watch?v=abc123 about?',
      { vaultPath: '/vault', spawnImpl: harness.spawn.makeSpawn() as never },
    )
    expect(out).toBe('')
    // Zero grep calls -- bypass is BEFORE spawn
    expect(harness.calls).toHaveLength(0)
  })

  it('returns empty for a bare URL even if heuristic would otherwise miss', async () => {
    const harness = makeGrepHarness([])
    const out = await injectVaultContext(
      'https://www.youtube.com/watch?v=PeqDWP_2zPE',
      { vaultPath: '/vault', spawnImpl: harness.spawn.makeSpawn() as never },
    )
    expect(out).toBe('')
    expect(harness.calls).toHaveLength(0)
  })
})

describe('injectVaultContext -- gating', () => {
  it('returns empty string for empty / whitespace input (no grep)', async () => {
    const { spawn } = makeGrepHarness([])
    expect(await injectVaultContext('', { spawnImpl: spawn.makeSpawn() as never })).toBe('')
    expect(await injectVaultContext('   ', { spawnImpl: spawn.makeSpawn() as never })).toBe('')
  })

  it('returns empty string for non-question commands (no grep)', async () => {
    const harness = makeGrepHarness([])
    const out = await injectVaultContext('create a note about Andrew', {
      spawnImpl: harness.spawn.makeSpawn() as never,
    })
    expect(out).toBe('')
    expect(harness.calls).toHaveLength(0)
  })
})

describe('injectVaultContext -- exact-query path', () => {
  it('formats a sentinel-fenced [Vault context] block when the exact query has hits', async () => {
    const hits: VaultGrepHit[] = [
      {
        file: '/vault/raw/meetings/2026-05-18_Andrew-Strategy-Call.md',
        line: 42,
        text: '- Decision: park TalentSignals enterprise tier per Q3 review',
      },
    ]
    const { spawn } = makeGrepHarness(hits)
    const out = await injectVaultContext('what did we decide about TalentSignals tier?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
    })
    // Block starts with the system-instruction header (Codex HIGH #1 -- prompt
    // injection defense), wraps content in a random sentinel fence, and contains
    // [Vault context] as a labeled line inside the fence.
    expect(out.startsWith('[System: vault-notes fallback')).toBe(true)
    expect(out).toMatch(/<VAULT_[A-F0-9]{12}>/)
    expect(out).toMatch(/<\/VAULT_[A-F0-9]{12}>/)
    expect(out).toContain('[Vault context]')
    expect(out).toContain('park TalentSignals enterprise tier')
    expect(out).toContain('raw/meetings/2026-05-18_Andrew-Strategy-Call.md:42')
    expect(out).toContain('UNTRUSTED data')
    expect(out.endsWith('\n\n')).toBe(true)
  })

  it('generates a fresh sentinel per call (Codex HIGH #1 -- prevents replay impersonation)', async () => {
    const hits: VaultGrepHit[] = [
      { file: '/vault/raw/a.md', line: 1, text: 'content' },
    ]
    const { spawn: spawn1 } = makeGrepHarness(hits)
    const { spawn: spawn2 } = makeGrepHarness(hits)
    const out1 = await injectVaultContext('what is in the vault?', {
      vaultPath: '/vault',
      spawnImpl: spawn1.makeSpawn() as never,
    })
    const out2 = await injectVaultContext('what is in the vault?', {
      vaultPath: '/vault',
      spawnImpl: spawn2.makeSpawn() as never,
    })
    const sentinel1 = out1.match(/<(VAULT_[A-F0-9]{12})>/)?.[1]
    const sentinel2 = out2.match(/<(VAULT_[A-F0-9]{12})>/)?.[1]
    expect(sentinel1).toBeDefined()
    expect(sentinel2).toBeDefined()
    expect(sentinel1).not.toBe(sentinel2)
  })

  it('strips trailing question mark from the exact-query term passed to grep', async () => {
    const harness = makeGrepHarness([
      { file: '/vault/raw/x.md', line: 1, text: 'a hit' },
    ])
    await injectVaultContext('what is TalentSignals?', {
      vaultPath: '/vault',
      spawnImpl: harness.spawn.makeSpawn() as never,
    })
    expect(harness.calls).toHaveLength(1)
    // grep -rniF --include=*.md -- <term> /vault/raw /vault/wiki
    // term is the arg right after `--`
    const dashDashIdx = harness.calls[0].args.indexOf('--')
    expect(dashDashIdx).toBeGreaterThanOrEqual(0)
    const term = harness.calls[0].args[dashDashIdx + 1]
    expect(term.endsWith('?')).toBe(false)
    expect(term).toBe('what is TalentSignals')
  })

  it('caps results at topK', async () => {
    const hits: VaultGrepHit[] = [
      { file: '/vault/raw/a.md', line: 1, text: 'one' },
      { file: '/vault/raw/b.md', line: 2, text: 'two' },
      { file: '/vault/raw/c.md', line: 3, text: 'three' },
      { file: '/vault/raw/d.md', line: 4, text: 'four' },
      { file: '/vault/raw/e.md', line: 5, text: 'five' },
    ]
    const { spawn } = makeGrepHarness(hits)
    const out = await injectVaultContext('what is in the vault?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
      topK: 3,
    })
    const bullets = out.split('\n').filter((l) => l.startsWith('- '))
    expect(bullets).toHaveLength(3)
    expect(out).toContain('a.md:1')
    expect(out).toContain('c.md:3')
    expect(out).not.toContain('d.md:4')
  })

  it('truncates snippets longer than 200 chars on a word boundary', async () => {
    const longText = 'park TalentSignals enterprise tier ' + 'detail '.repeat(60)
    const hits: VaultGrepHit[] = [
      { file: '/vault/raw/x.md', line: 1, text: longText },
    ]
    const { spawn } = makeGrepHarness(hits)
    const out = await injectVaultContext('what did we decide?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
    })
    // The bullet line: "- x.md:1 -- <snippet>..."
    const bullet = out.split('\n').find((l) => l.startsWith('- '))!
    const snippet = bullet.split(' -- ')[1]
    // 200 chars + '...' = 203 max
    expect(snippet.length).toBeLessThanOrEqual(203)
    expect(snippet.endsWith('...')).toBe(true)
  })
})

describe('injectVaultContext -- fallback term path', () => {
  it('falls back to longest content word when exact query misses', async () => {
    const harness = makeGrepHarness(
      [], // exact-query miss
      [{ file: '/vault/raw/m.md', line: 9, text: 'TalentSignals tier discussion' }],
    )
    const out = await injectVaultContext('what did we decide about TalentSignals tier last week?', {
      vaultPath: '/vault',
      spawnImpl: harness.spawn.makeSpawn() as never,
    })
    expect(out).toContain('TalentSignals tier discussion')
    expect(harness.calls).toHaveLength(2)
    // Second grep call should be invoked with the fallback term, not the full query
    const lastCall = harness.calls[1]
    const dashDashIdx = lastCall.args.indexOf('--')
    expect(lastCall.args[dashDashIdx + 1]).toBe('talentsignals')
  })

  it('returns empty when both exact and fallback miss', async () => {
    const { spawn } = makeGrepHarness([], [])
    const out = await injectVaultContext('what about TalentSignals last week?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
    })
    expect(out).toBe('')
  })

  it('returns empty when exact query misses and no fallback term survives stopwords', async () => {
    const harness = makeGrepHarness([])
    const out = await injectVaultContext('what did we do?', {
      vaultPath: '/vault',
      spawnImpl: harness.spawn.makeSpawn() as never,
    })
    expect(out).toBe('')
    // Only one grep call -- no fallback term to try
    expect(harness.calls).toHaveLength(1)
  })
})

describe('injectVaultContext -- exclude filters', () => {
  it('skips Syncthing sync-conflict files', async () => {
    const hits: VaultGrepHit[] = [
      { file: '/vault/raw/x.md', line: 1, text: 'good hit' },
      {
        file: '/vault/wiki/log.sync-conflict-20260512-211158-3H6OL5B.md',
        line: 99,
        text: 'stale conflict hit',
      },
    ]
    const { spawn } = makeGrepHarness(hits)
    const out = await injectVaultContext('what is in the vault?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
    })
    expect(out).toContain('good hit')
    expect(out).not.toContain('stale conflict hit')
    expect(out).not.toContain('sync-conflict')
  })

  it('skips wiki/context/shelves/ (already covered by injectMemoryFromShelves)', async () => {
    const hits: VaultGrepHit[] = [
      {
        file: '/vault/wiki/context/shelves/semantic.md',
        line: 16,
        text: 'shelf row that injectMemoryFromShelves already handles',
      },
      { file: '/vault/raw/meetings/x.md', line: 1, text: 'fresh meeting hit' },
    ]
    const { spawn } = makeGrepHarness(hits)
    const out = await injectVaultContext('what is on the shelf?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
    })
    expect(out).toContain('fresh meeting hit')
    expect(out).not.toContain('shelf row that injectMemoryFromShelves')
  })

  it('skips structural log.md and index.md files', async () => {
    const hits: VaultGrepHit[] = [
      { file: '/vault/wiki/log.md', line: 100, text: 'log entry' },
      { file: '/vault/wiki/concepts/index.md', line: 5, text: 'index row' },
      { file: '/vault/raw/research/2026-05-19_research.md', line: 12, text: 'real content' },
    ]
    const { spawn } = makeGrepHarness(hits)
    const out = await injectVaultContext('what did we research?', {
      vaultPath: '/vault',
      spawnImpl: spawn.makeSpawn() as never,
    })
    expect(out).toContain('real content')
    expect(out).not.toContain('log entry')
    expect(out).not.toContain('index row')
  })
})

describe('injectVaultContext -- graceful degradation (never throws)', () => {
  it('hard grep error on exact query returns empty without fallback retry (Codex MEDIUM #3)', async () => {
    // The exact-query grep errors (non-zero non-1 exit, e.g. bad path).
    // Before the fix, we caught the error and ran the fallback grep, doubling
    // the user-visible latency on a broken vault path. New behavior: hard
    // error fails closed, fallback is NOT attempted.
    const harness = makeGrepHarness(new Error('bad path'), [
      { file: '/vault/raw/x.md', line: 1, text: 'fallback hit -- should NOT surface' },
    ])
    const out = await injectVaultContext('who is Dr Lo Hak Keung?', {
      vaultPath: '/vault',
      spawnImpl: harness.spawn.makeSpawn() as never,
    })
    expect(out).toBe('')
    // CRITICAL: only one grep call (no fallback retry on hard error)
    expect(harness.calls).toHaveLength(1)
  })

  it('grep timeout on exact query returns empty without fallback retry (Codex MEDIUM #3)', async () => {
    // Hung process must NOT trigger a second hung process. Bounded user-
    // visible latency: ONE timeout window, not two.
    const harness = makeSpawnHarness(() => ({ hang: true }))
    const started = Date.now()
    const out = await injectVaultContext('what did we decide?', {
      vaultPath: '/vault',
      spawnImpl: harness.makeSpawn() as never,
      timeoutMs: 50,
    })
    const elapsed = Date.now() - started
    expect(out).toBe('')
    // Only one grep should have been spawned -- proves no fallback retry
    expect(harness.calls).toHaveLength(1)
    // 50ms timeout + small settling overhead. With the bug, this would be
    // ~100ms (two consecutive 50ms timeouts).
    expect(elapsed).toBeLessThan(500)
  })

  it('clean no-match still triggers fallback term (no false-degradation regression)', async () => {
    // Verify the Codex MEDIUM #3 fix didn't accidentally disable the fallback
    // for the legitimate use case: exact query missed cleanly (exit 1), fallback
    // term then finds hits.
    const harness = makeGrepHarness(
      [], // clean miss on exact
      [{ file: '/vault/raw/x.md', line: 1, text: 'TalentSignals decision' }],
    )
    const out = await injectVaultContext('what did we decide about TalentSignals tier?', {
      vaultPath: '/vault',
      spawnImpl: harness.spawn.makeSpawn() as never,
    })
    expect(out).toContain('TalentSignals decision')
    expect(harness.calls).toHaveLength(2)
  })
})
