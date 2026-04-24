/**
 * Integration tests for the full normalizationGate() orchestration.
 * Drives washingMachine.ts with a fake spawn implementation that
 * returns scripted classifier / normalize / shelf-writer outputs,
 * then asserts the correct shelf-writer invocations happen.
 *
 * This is the Gate-behavior test: pinning policy, skip rules, and
 * field-to-attr mapping all land here.
 */

import { describe, it, expect } from 'vitest'
import { EventEmitter } from 'node:events'
import { PassThrough } from 'node:stream'
import { normalizationGate } from './washingMachine.js'

interface ScriptedCall {
  cmd: string
  args: string[]
  stdin?: string
  stdoutText: string
  exitCode: number
}

interface MockController {
  expect(matcher: (cmd: string, args: string[]) => boolean, stdoutText: string, exitCode?: number): void
  calls: ScriptedCall[]
  makeSpawn(): (cmd: string, args: readonly string[]) => unknown
}

function createMockController(): MockController {
  const script: Array<{
    matcher: (cmd: string, args: string[]) => boolean
    stdoutText: string
    exitCode: number
  }> = []
  const calls: ScriptedCall[] = []

  return {
    calls,
    expect(matcher, stdoutText, exitCode = 0) {
      script.push({ matcher, stdoutText, exitCode })
    },
    makeSpawn() {
      return (cmd: string, args: readonly string[]) => {
        const copyArgs = [...args]
        const match = script.find((s) => s.matcher(cmd, copyArgs))
        if (!match) {
          const e: EventEmitter & {
            stdin: PassThrough
            stdout: PassThrough
            stderr: PassThrough
            kill: () => boolean
          } = Object.assign(new EventEmitter(), {
            stdin: new PassThrough(),
            stdout: new PassThrough(),
            stderr: new PassThrough(),
            kill: () => true,
          })
          setImmediate(() => e.emit('close', 127))
          calls.push({ cmd, args: copyArgs, stdoutText: '', exitCode: 127 })
          return e
        }

        const stdin = new PassThrough()
        const stdout = new PassThrough()
        const stderr = new PassThrough()
        const emitter: EventEmitter & {
          stdin: PassThrough
          stdout: PassThrough
          stderr: PassThrough
          kill: () => boolean
        } = Object.assign(new EventEmitter(), {
          stdin,
          stdout,
          stderr,
          kill: () => true,
        })

        const chunks: Buffer[] = []
        stdin.on('data', (c: Buffer | string) => {
          chunks.push(typeof c === 'string' ? Buffer.from(c) : c)
        })
        stdin.on('end', () => {
          const stdinText = Buffer.concat(chunks).toString('utf-8')
          calls.push({
            cmd,
            args: copyArgs,
            stdin: stdinText,
            stdoutText: match.stdoutText,
            exitCode: match.exitCode,
          })
          stdout.end(match.stdoutText)
          stderr.end('')
          setImmediate(() => emitter.emit('close', match.exitCode))
        })

        return emitter
      }
    },
  }
}

function matchNormalize(cmd: string, args: string[]): boolean {
  return cmd === 'python3' && args.some((a) => a.endsWith('/normalize.py'))
}

function matchClassify(cmd: string, args: string[]): boolean {
  return cmd === 'bash' && args.some((a) => a.endsWith('/classify.sh'))
}

function matchShelfWriter(cmd: string, args: string[]): boolean {
  return cmd === 'bash' && args.some((a) => a.endsWith('/shelf-writer.sh'))
}

describe('normalizationGate orchestration', () => {
  it('writes a contact with pinned:yes for keep=true + type=contact', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, "Andrew's new number is 9876 5432, works at Apex Capital.")
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [
          {
            type: 'contact',
            fields: { name: 'Andrew', phone: '9876 5432', org: 'Apex Capital' },
          },
        ],
      })
    )
    mock.expect(matchShelfWriter, '')

    const result = await normalizationGate("Andrew's new number is 9876 5432, works at Apex Capital.", {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('classified')
    expect(result.classifier?.keep).toBe(true)
    expect(result.rowsWritten).toBe(1)

    const writerCall = mock.calls.find((c) => matchShelfWriter(c.cmd, c.args))
    expect(writerCall).toBeDefined()
    expect(writerCall?.args).toContain('--shelf')
    expect(writerCall?.args).toContain('semantic')
    expect(writerCall?.args).toContain('contact')
    const attrs = extractAttrs(writerCall!.args)
    expect(attrs).toContain('name:Andrew')
    expect(attrs).toContain('phone:9876 5432')
    expect(attrs).toContain('org:Apex Capital')
    expect(attrs).toContain('pinned:yes')
    expect(attrs).toContain('category:fact')
    expect(attrs).toContain('source:telegram-classifier')
  })

  it('pins decision + appointment + org + person entities (full pinning policy)', async () => {
    const pinnedTypes: Array<{ type: string; fields: Record<string, string> }> = [
      { type: 'person', fields: { name: 'Dr. Lisa Chen' } },
      { type: 'org', fields: { name: 'Peak Talent' } },
      { type: 'appointment', fields: { subject: 'SJM board meeting' } },
      { type: 'decision', fields: { subject: 'hiring', choice: 'offer' } },
    ]

    for (const entity of pinnedTypes) {
      const mock = createMockController()
      mock.expect(matchNormalize, 'input unchanged')
      mock.expect(
        matchClassify,
        JSON.stringify({
          keep: true,
          category: 'fact',
          shelf: 'semantic',
          entities: [entity],
        })
      )
      mock.expect(matchShelfWriter, '')

      const result = await normalizationGate('some text', {
        spawnImpl: mock.makeSpawn() as never,
        anchorIsoDate: () => '2026-04-01',
      })

      expect(result.rowsWritten).toBe(1)
      const writerCall = mock.calls.find((c) => matchShelfWriter(c.cmd, c.args))
      const attrs = extractAttrs(writerCall!.args)
      expect(attrs).toContain('pinned:yes')
    }
  })

  it('does NOT pin preference / context / fact entities (subject to Ship 4 decay)', async () => {
    const unpinnedTypes: Array<{ type: string; fields: Record<string, string> }> = [
      { type: 'preference', fields: { trigger: 'outbound email', rule: 'not after 22:00 HKT' } },
      { type: 'fact', fields: { subject: 'HQ location', location: 'Macau' } },
      { type: 'location', fields: { name: 'Central' } },
    ]

    for (const entity of unpinnedTypes) {
      const mock = createMockController()
      mock.expect(matchNormalize, 'input unchanged')
      mock.expect(
        matchClassify,
        JSON.stringify({
          keep: true,
          category: entity.type === 'preference' ? 'preference' : 'fact',
          shelf: 'semantic',
          entities: [entity],
        })
      )
      mock.expect(matchShelfWriter, '')

      const result = await normalizationGate('some text', {
        spawnImpl: mock.makeSpawn() as never,
        anchorIsoDate: () => '2026-04-01',
      })

      expect(result.rowsWritten).toBe(1)
      const writerCall = mock.calls.find((c) => matchShelfWriter(c.cmd, c.args))
      const attrs = extractAttrs(writerCall!.args)
      expect(attrs).toContain('pinned:no')
    }
  })

  it('skips photo attachment wrappers (Ship 1B will handle them)', async () => {
    const mock = createMockController()
    // No spawn expectations -- the gate should short-circuit.

    const result = await normalizationGate(
      '[Photo attached at /tmp/card.jpg]\nAnalyze this image and respond to the user.',
      {
        spawnImpl: mock.makeSpawn() as never,
        anchorIsoDate: () => '2026-04-01',
      }
    )

    expect(result.status).toBe('skipped-attachment')
    expect(result.rowsWritten).toBe(0)
    expect(mock.calls).toHaveLength(0)
  })

  it('skips document attachment wrappers', async () => {
    const mock = createMockController()
    const result = await normalizationGate('[Document attached: foo.pdf at /tmp/foo.pdf]', {
      spawnImpl: mock.makeSpawn() as never,
    })
    expect(result.status).toBe('skipped-attachment')
    expect(mock.calls).toHaveLength(0)
  })

  it('classifies voice-transcribed text normally (strip the wrapper)', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, 'Dr. Lo phone is 2830 3709')
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [{ type: 'contact', fields: { name: 'Dr. Lo', phone: '2830 3709' } }],
      })
    )
    mock.expect(matchShelfWriter, '')

    const result = await normalizationGate('[Voice transcribed]: Dr. Lo phone is 2830 3709', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-23',
    })

    expect(result.status).toBe('classified')
    expect(result.rowsWritten).toBe(1)

    // The voice prefix must NOT be sent to normalize.py.
    const normalizeCall = mock.calls.find((c) => matchNormalize(c.cmd, c.args))
    expect(normalizeCall?.stdin).toBe('Dr. Lo phone is 2830 3709')
  })

  it('records rejection without writing a shelf row (keep=false)', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, "What's my doctor's phone number?")
    mock.expect(matchClassify, JSON.stringify({ keep: false, discard_reason: 'question' }))
    // No shelf writer expectation.

    const result = await normalizationGate("What's my doctor's phone number?", {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('classified')
    expect(result.classifier?.keep).toBe(false)
    expect(result.classifier?.discard_reason).toBe('question')
    expect(result.rowsWritten).toBe(0)
    expect(mock.calls.filter((c) => matchShelfWriter(c.cmd, c.args))).toHaveLength(0)
  })

  it('swallows classifier errors and returns error status (never throws)', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, 'some input')
    mock.expect(matchClassify, 'not-json-at-all', 0)

    const result = await normalizationGate('some input', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('error')
    expect(result.rowsWritten).toBe(0)
  })

  it('uses the resolved timestamp_iso date for the shelf row when present', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, 'Reminder: annual physical 2026-04-02 at 9am with Dr. Wong.')
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        timestamp_iso: '2026-04-02T09:00:00+08:00',
        date_confidence: 0.95,
        entities: [
          { type: 'appointment', fields: { subject: 'annual physical', with: 'Dr. Wong' } },
        ],
      })
    )
    mock.expect(matchShelfWriter, '')

    await normalizationGate('Reminder: annual physical tomorrow at 9am with Dr. Wong.', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    const writerCall = mock.calls.find((c) => matchShelfWriter(c.cmd, c.args))
    expect(writerCall?.args).toContain('--date')
    const dateIdx = writerCall!.args.indexOf('--date')
    expect(writerCall!.args[dateIdx + 1]).toBe('2026-04-02')
    const attrs = extractAttrs(writerCall!.args)
    expect(attrs).toContain('date_confidence:0.95')
  })

  it('writes multi-entity messages as separate shelf rows', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, 'multi entity message')
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [
          { type: 'contact', fields: { name: 'Lisa Chen', phone: '2522 9876' } },
          { type: 'contact', fields: { name: 'Dr. Mike Ho', org: "St. Paul's" } },
        ],
      })
    )
    mock.expect(matchShelfWriter, '')
    mock.expect(matchShelfWriter, '')

    const result = await normalizationGate('multi entity message', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.rowsWritten).toBe(2)
    expect(mock.calls.filter((c) => matchShelfWriter(c.cmd, c.args))).toHaveLength(2)
  })

  it('returns error status when classifier responds with an API-error JSON object', async () => {
    // MiniMax rate-limit / transient error responses deserialize as valid
    // JSON but lack the required keep:boolean contract. Must be treated as
    // error, not silently swallowed as keep=false.
    const mock = createMockController()
    mock.expect(matchNormalize, 'input unchanged')
    mock.expect(
      matchClassify,
      JSON.stringify({ error: 'rate limited', retry_after_ms: 30000 })
    )

    const result = await normalizationGate('some input', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('error')
    expect(result.rowsWritten).toBe(0)
  })

  it('kills hanging child processes via SIGKILL and returns error status', async () => {
    // Real subprocess -- classifier hangs forever. Gate must trip the
    // (shortened) timeout, send SIGKILL, and return error. Uses real spawn
    // so kill() is exercised against a real PID, not a stub method.
    const { spawn: realSpawn } = await import('node:child_process')

    const spawnImpl = ((cmd: string, args: readonly string[]) => {
      if (cmd === 'bash' && args.some((a) => a.endsWith('/classify.sh'))) {
        // node reads stdin forever; will NOT exit until killed.
        return realSpawn(
          'node',
          ['-e', 'process.stdin.resume(); setInterval(()=>{}, 1000)'],
          { stdio: ['pipe', 'pipe', 'pipe'] }
        )
      }
      if (cmd === 'python3' && args.some((a) => a.endsWith('/normalize.py'))) {
        // cat passes stdin through. Fast, exits cleanly.
        return realSpawn('cat', [], { stdio: ['pipe', 'pipe', 'pipe'] })
      }
      return realSpawn('true', [], { stdio: ['pipe', 'pipe', 'pipe'] })
    }) as never

    const result = await normalizationGate('text that triggers hanging classify', {
      spawnImpl,
      anchorIsoDate: () => '2026-04-01',
      classifierTimeoutMs: 400,
      normalizeTimeoutMs: 2_000,
    })

    expect(result.status).toBe('error')
    expect(result.reason).toMatch(/classify\.sh timed out after 400ms/i)
    expect(result.rowsWritten).toBe(0)
  }, 10_000)

  it('drops invalid entity types silently (prompt drift / hallucinated type)', async () => {
    const mock = createMockController()
    mock.expect(matchNormalize, 'input')
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [
          { type: 'contact', fields: { name: 'OK Keep' } },
          { type: 'hallucinated_type', fields: { name: 'Should be dropped' } },
        ],
      })
    )
    mock.expect(matchShelfWriter, '')

    const result = await normalizationGate('input', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.rowsWritten).toBe(1)
    expect(mock.calls.filter((c) => matchShelfWriter(c.cmd, c.args))).toHaveLength(1)
  })

  it('returns error status when keep=true but every entity is an invalid type (total prompt drift)', async () => {
    // Separate from the "keep=false" legit-reject path: this is the model
    // claiming there IS a fact to store but naming only unknown types. Prior
    // implementation silently returned classified / rowsWritten=0 -- monitoring
    // could not tell this apart from a legitimate keep=false reject.
    const mock = createMockController()
    mock.expect(matchNormalize, 'input')
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [
          { type: 'hallucinated_type_1', fields: { name: 'A' } },
          { type: 'hallucinated_type_2', fields: { name: 'B' } },
        ],
      })
    )

    const result = await normalizationGate('input', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('error')
    expect(result.reason).toMatch(/zero valid entities/i)
    expect(result.rowsWritten).toBe(0)
    expect(mock.calls.filter((c) => matchShelfWriter(c.cmd, c.args))).toHaveLength(0)
  })

  it('returns error status when keep=true with the entities array missing entirely', async () => {
    // Missing entities field is the simpler prompt-drift shape; same contract.
    const mock = createMockController()
    mock.expect(matchNormalize, 'input')
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        // entities field intentionally omitted
      })
    )

    const result = await normalizationGate('input', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })

    expect(result.status).toBe('error')
    expect(result.reason).toMatch(/zero valid entities/i)
    expect(result.rowsWritten).toBe(0)
  })

  // --- Ship 1B: pending-confirmation park path ---------------------------
  it('parks pending when needs_confirmation_reason returned by normalize AND chatId present', async () => {
    const mock = createMockController()
    // normalize.py --json output with date_mismatch reason
    mock.expect(
      matchNormalize,
      JSON.stringify({
        rewritten_text: 'Dr. Lo card capture',
        substitutions: [],
        needs_confirmation_reason: ['date_mismatch'],
      })
    )
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [
          {
            type: 'contact',
            fields: { name: 'Dr. Lo Hak Keung', phone: '2830 3709' },
          },
        ],
      })
    )
    // shelf-writer must NOT be called on park path

    const { mkdtempSync, readdirSync, readFileSync } = await import('node:fs')
    const { tmpdir } = await import('node:os')
    const { join } = await import('node:path')
    const pendingDir = mkdtempSync(join(tmpdir(), 'park-'))

    const result = await normalizationGate(
      {
        rawText: 'Dr. Lo card capture',
        chatId: '42',
        promptMessageId: 12345,
        messageTsMs: Date.parse('2026-04-01T19:25:00Z'),
        ocrDate: '2025-04-11',
      },
      {
        spawnImpl: mock.makeSpawn() as never,
        anchorIsoDate: () => '2026-04-01',
        pendingDirOverride: pendingDir,
      }
    )

    expect(result.status).toBe('pending-confirmation')
    expect(result.rowsWritten).toBe(0)
    expect(result.pendingUuid).toBeDefined()
    expect(result.pendingPrompt).toContain('Reply 1')
    expect(result.pendingPrompt).toContain('2025-04-11')

    // Exactly one pending file written, not zero, not two
    const files = readdirSync(pendingDir).filter((n) => n.endsWith('.json'))
    expect(files.length).toBe(1)
    const record = JSON.parse(readFileSync(join(pendingDir, files[0]), 'utf8'))
    expect(record.chatId).toBe('42')
    expect(record.promptMessageId).toBe(12345)
    expect(record.candidates.length).toBe(2)
    expect(record.candidates[0].date).toBe('2026-04-01')
    expect(record.candidates[1].date).toBe('2025-04-11')

    // Verify shelf-writer was NOT called (park bypasses the write path)
    const writerCalls = mock.calls.filter((c) => matchShelfWriter(c.cmd, c.args))
    expect(writerCalls.length).toBe(0)
  })

  it('writes to shelf (not park) when needs_confirmation is empty', async () => {
    const mock = createMockController()
    mock.expect(
      matchNormalize,
      JSON.stringify({
        rewritten_text: 'clean capture',
        substitutions: [],
        needs_confirmation_reason: [],
      })
    )
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [{ type: 'contact', fields: { name: 'Test Person', phone: '1234 5678' } }],
      })
    )
    mock.expect(matchShelfWriter, '')

    const result = await normalizationGate(
      { rawText: 'clean capture', chatId: '42', messageTsMs: Date.parse('2026-04-01T00:00:00Z') },
      { spawnImpl: mock.makeSpawn() as never, anchorIsoDate: () => '2026-04-01' }
    )
    expect(result.status).toBe('classified')
    expect(result.rowsWritten).toBe(1)
    expect(result.pendingUuid).toBeUndefined()
  })

  it('falls through to write path when chatId is missing, even if normalize flags contradiction', async () => {
    // Covers the back-compat case: a legacy caller passes a bare string.
    // We do not have a chatId to park against, so the gate writes the row
    // rather than silently losing it. This is an explicit choice: Ship 1B
    // park requires chatId to route the prompt back.
    const mock = createMockController()
    mock.expect(
      matchNormalize,
      JSON.stringify({
        rewritten_text: 'text',
        substitutions: [],
        needs_confirmation_reason: ['low_confidence'],
      })
    )
    mock.expect(
      matchClassify,
      JSON.stringify({
        keep: true,
        category: 'fact',
        shelf: 'semantic',
        entities: [{ type: 'contact', fields: { name: 'Anon', phone: '9999 9999' } }],
      })
    )
    mock.expect(matchShelfWriter, '')

    const result = await normalizationGate('text', {
      spawnImpl: mock.makeSpawn() as never,
      anchorIsoDate: () => '2026-04-01',
    })
    expect(result.status).toBe('classified')
    expect(result.rowsWritten).toBe(1)
  })
})

function extractAttrs(args: string[]): string[] {
  const result: string[] = []
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '--attr' && i + 1 < args.length) {
      result.push(args[i + 1])
    }
  }
  return result
}
