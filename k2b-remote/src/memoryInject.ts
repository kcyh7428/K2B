/**
 * Washing Machine memory-inject station (Ship 1 Commit 4, raw-rows).
 *
 * Replaces the legacy `buildMemoryContext` FTS+recent path. Spawns the
 * hybrid retriever (scripts/washing-machine/retrieve.py, shipped Commit 2)
 * against the semantic shelf and prepends the top-K raw rows to the
 * commander prompt under a `[Memory context]` marker.
 *
 * Contract (ratified 2026-04-23b after Codex Tier 3 on Commit 3):
 *   - Fire-and-forget gate + inject = future-turn-only. Facts captured
 *     from message N do NOT affect message N's reply; they land on
 *     message N+1 once the classifier completes.
 *   - This function reads the shelf snapshot at call time. It MUST NOT
 *     await the gate's write for the current turn. The race-free
 *     property is enforced structurally: inject only spawns
 *     retrieve.py (read side) and does not share any promise, lock, or
 *     shelf-write coordination with normalizationGate.
 *
 * Failure policy (same spirit as the gate): never throw. On any
 * retrieval failure (missing index, corrupt JSON, subprocess timeout,
 * sentence-transformers import failure) return '' so the agent proceeds
 * without memory context and the warning surfaces in logs.
 *
 * Spec: wiki/concepts/feature_washing-machine-memory.md (Ship 1 Commit 4).
 */

import { spawn } from 'node:child_process'
import { readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { resolve } from 'node:path'
import { logger } from './logger.js'
import { K2B_PROJECT_ROOT } from './config.js'

const RETRIEVE_SCRIPT = resolve(K2B_PROJECT_ROOT, 'scripts/washing-machine/retrieve.py')

// retrieve.py defaults --k to 10. Five rows is enough for phone-number
// lookups and keeps the injected block tight on long shelves.
const DEFAULT_K = 5

// retrieve.py cold-starts ~5-10s on Apple Silicon because
// sentence-transformers re-imports and reloads the MiniLM model on every
// subprocess. 15s upper bound matches the classifier's timeout and keeps
// Ship 1 MVP retrieval alive even when the model cache is cold. This is
// a known deviation from the feature note's 0.5s budget, which assumed
// a warmed-daemon retriever that does not yet exist. A persistent
// embedding daemon is Ship 1B-era follow-up; until then, inject pays
// cold-start latency on every call. A hung child gets SIGKILLed and we
// return '' gracefully.
const RETRIEVE_TIMEOUT_MS = 15_000

// Hard ceiling on retrieve.py stdout to prevent a corrupt shelf, runaway
// embedding blob, or misconfigured retriever from exhausting Node heap
// via unbounded string concatenation. Normal responses are ~2-10 KB; 1 MB
// is ~2 orders of magnitude above the upper end of legitimate output.
// Exceeding the cap kills the child and returns '' -- same graceful-
// degradation contract as every other failure path.
const MAX_RETRIEVE_STDOUT_BYTES = 1_000_000

// Shelf scoping note: the Washing Machine semantic shelf is currently a
// single global markdown file with no per-chatId partition. K2B's Telegram
// bot is gated to ALLOWED_CHAT_ID (config.ts), so the bot is effectively
// single-user today. If multi-user or group-chat support ever lands, this
// function must thread a chatId (or tenant id) through to retrieve.py and
// the shelf schema must add a tenant column. Intentional Ship 1 scope --
// the spec (feature_washing-machine-memory.md) does not carry multi-user
// isolation. Codex/MiniMax flags tracked in the Ship 1 Commit 4 devlog.

// Resolve the washing-machine Python interpreter. Production writes
// WASHING_MACHINE_PYTHON into ~/.config/k2b/washing-machine.env via
// `scripts/washing-machine/preflight.sh`. pm2 captures its env at
// process-start time and does NOT source shell dotfiles, so on Mac Mini
// the k2b-remote process ran without WASHING_MACHINE_PYTHON set even
// though the env file was present on disk. `python3` (the fallback at
// the end of this chain) resolves to system Python, which lacks
// sentence-transformers -- retrieve.py exits 3 and inject returns ''.
// The 2026-04-23 Ship 1 MVP failure was exactly this: every Telegram
// turn silently skipped memory inject. Reading the env file here closes
// that gap without requiring a pm2 restart dance on every deploy.
// Precedence: process.env > env file > 'python3'. System python3 is
// kept as the final fallback for dev / CI where the env file is absent.
//
// Validation layer (MiniMax Checkpoint 2 HIGH-1 + HIGH-2, 2026-04-23):
//   - Whitespace/empty values (e.g. `WASHING_MACHINE_PYTHON="   "`) are
//     rejected and fall through. Without this guard the regex captured
//     whitespace and spawn got a bogus command, failing opaquely via
//     the graceful-degradation path.
//   - The resolved path is existence-checked via statSync. A stale env
//     file pointing at a deleted venv converts what would be an opaque
//     ENOENT-inside-spawn into a clean fall-through to system python3.
//     Exec-bit check via `(s.mode & 0o111) !== 0` -- a venv python that
//     lost its exec bit would otherwise pass isFile() and fail opaquely
//     with EACCES/ENOEXEC inside spawn. Folded round-2 HIGH-1. Not a
//     command-injection threat model (single-user machine), just a
//     failure-mode-distinguisher so operators can tell "no binary" from
//     "binary with wrong perms".
export function resolveWashingMachinePython(
  envReader: (path: string) => string = (p) => readFileSync(p, 'utf-8'),
  existsCheck: (path: string) => boolean = (p) => {
    try {
      const s = statSync(p)
      // isFile + any exec bit (owner/group/other). A venv python binary
      // missing its exec bit would otherwise pass isFile() and then
      // fail EACCES/ENOEXEC inside spawn -- same opaque failure mode
      // as a non-existent path. MiniMax Checkpoint 2 round-2 HIGH-1.
      return s.isFile() && (s.mode & 0o111) !== 0
    } catch {
      return false
    }
  },
): string {
  const fromEnv = process.env.WASHING_MACHINE_PYTHON?.trim()
  if (fromEnv) {
    if (existsCheck(fromEnv)) return fromEnv
    // process.env value points at a non-file path. Surface once (debug-
    // adjacent), then fall through to env-file + python3 chain rather
    // than handing spawn a path that will ENOENT opaquely.
    logger.warn(
      { fromEnv },
      'resolveWashingMachinePython: process.env.WASHING_MACHINE_PYTHON does not exist; falling through',
    )
  }
  try {
    const envPath = resolve(homedir(), '.config/k2b/washing-machine.env')
    const raw = envReader(envPath)
    const match = raw.match(
      /^\s*(?:export\s+)?WASHING_MACHINE_PYTHON\s*=\s*"?([^"\n]+?)"?\s*$/m,
    )
    const candidate = match?.[1]?.trim()
    if (candidate) {
      if (existsCheck(candidate)) return candidate
      logger.warn(
        { candidate, envPath },
        'resolveWashingMachinePython: env-file path does not exist; falling through to system python3',
      )
    }
  } catch {
    // env file missing or unreadable -- fall through to system python3
  }
  return 'python3'
}

const DEFAULT_PYTHON_BIN = resolveWashingMachinePython()

export interface InjectDeps {
  retrieveScript?: string
  pythonBin?: string
  spawnImpl?: typeof spawn
  timeoutMs?: number
  k?: number
}

export async function injectMemoryFromShelves(
  userMsg: string,
  deps: InjectDeps = {}
): Promise<string> {
  if (!userMsg || !userMsg.trim()) return ''

  const script = deps.retrieveScript ?? RETRIEVE_SCRIPT
  const pythonBin = deps.pythonBin ?? DEFAULT_PYTHON_BIN
  const timeoutMs = deps.timeoutMs ?? RETRIEVE_TIMEOUT_MS
  const k = deps.k ?? DEFAULT_K
  const spawner = deps.spawnImpl ?? spawn

  let raw: string
  try {
    raw = await runRetrieve(spawner, pythonBin, script, userMsg, k, timeoutMs)
  } catch (err) {
    logger.warn(
      { err: String(err) },
      'injectMemoryFromShelves: retrieve.py failed; agent proceeds without memory context'
    )
    return ''
  }

  let rows: string[]
  try {
    rows = parseRowTexts(raw)
  } catch (err) {
    logger.warn(
      { err: String(err), preview: raw.slice(0, 200) },
      'injectMemoryFromShelves: retrieve.py emitted invalid output; returning empty context'
    )
    return ''
  }

  if (rows.length === 0) return ''

  const lines = rows.map((rowText) => `- ${rowText}`)
  return `[Memory context]\n${lines.join('\n')}\n\n`
}

function parseRowTexts(raw: string): string[] {
  const parsed = JSON.parse(raw)
  if (!Array.isArray(parsed)) {
    throw new Error('retrieve.py did not return a JSON array')
  }
  const rows: string[] = []
  for (const entry of parsed) {
    if (!entry || typeof entry !== 'object') continue
    const row = entry as Record<string, unknown>
    if (typeof row.row_text === 'string') {
      rows.push(row.row_text)
    }
  }
  return rows
}

interface ChildLike {
  stdout: NodeJS.ReadableStream | null
  stderr: NodeJS.ReadableStream | null
  on(event: 'close', listener: (code: number | null) => void): this
  on(event: 'error', listener: (err: Error) => void): this
  kill(signal?: NodeJS.Signals | number): boolean | void
}

function runRetrieve(
  spawner: typeof spawn,
  pythonBin: string,
  script: string,
  query: string,
  k: number,
  timeoutMs: number
): Promise<string> {
  return new Promise((resolveFn, rejectFn) => {
    const child = spawner(
      pythonBin,
      [script, query, '--shelf', 'semantic', '--k', String(k)],
      { stdio: ['ignore', 'pipe', 'pipe'] }
    ) as unknown as ChildLike

    let stdout = ''
    let stderr = ''
    let finished = false

    const finish = (fn: () => void) => {
      if (finished) return
      finished = true
      clearTimeout(timer)
      fn()
    }

    const timer = setTimeout(() => {
      try {
        child.kill('SIGKILL')
      } catch {
        // ignore kill failures; the close listener will still settle us
      }
      finish(() => rejectFn(new Error(`retrieve.py timed out after ${timeoutMs}ms`)))
    }, timeoutMs)

    let stdoutBytes = 0
    child.stdout?.on('data', (c: Buffer | string) => {
      // Accumulate bytes on the wire, not string code units. JavaScript strings
      // are UTF-16 internally, so stdout.length would under-count multi-byte
      // UTF-8 (Chinese, emoji, extended Latin) -- a 1M-code-unit response of
      // Chinese text would be ~3MB UTF-8 on the wire but pass a naive
      // stdout.length check. Measuring bytes directly is the only correct cap.
      const bytes = typeof c === 'string' ? Buffer.byteLength(c, 'utf-8') : c.length
      stdoutBytes += bytes
      stdout += typeof c === 'string' ? c : c.toString('utf-8')
      if (stdoutBytes > MAX_RETRIEVE_STDOUT_BYTES) {
        try {
          child.kill('SIGKILL')
        } catch {
          // kill is best-effort; the close listener will settle us
        }
        finish(() =>
          rejectFn(
            new Error(
              `retrieve.py stdout exceeded ${MAX_RETRIEVE_STDOUT_BYTES} bytes (shelf corruption or runaway response)`
            )
          )
        )
      }
    })
    child.stderr?.on('data', (c: Buffer | string) => {
      stderr += typeof c === 'string' ? c : c.toString('utf-8')
    })
    child.on('error', (err: Error) => finish(() => rejectFn(err)))
    child.on('close', (code: number | null) => {
      if (code === 0) {
        finish(() => resolveFn(stdout))
      } else {
        finish(() =>
          rejectFn(
            new Error(
              `retrieve.py exited with code ${code ?? 'null'}: ${stderr.trim() || '(no stderr)'}`
            )
          )
        )
      }
    })
  })
}
