# K2B Claude Removal, Codex Operations, and Home Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every live Claude Code, Anthropic, and Telegram dependency from K2B while preserving MacBook Codex operation, Mini background routines, vault synchronization, specialist providers, and read-only home infrastructure visibility without OpenAI API usage charges.

**Architecture:** The MacBook is the Codex command plane and the Mac Mini is the always-on operations plane. A Claude-free `k2b-core` service handles intake and deterministic processing, host-scoped JSONL receipts make every job observable, the existing dashboard becomes the Operations Console, and a separate read-only Home Manager owns NAS and home-network connectors. Kimi, GPTsAPI, Groq, and Higgsfield remain explicit specialist providers.

**Tech Stack:** Node.js 20, TypeScript ESM, React 18, Vite, Express, Vitest, Python 3.12 standard library, Bash, macOS launchd, PM2, Syncthing, SSH, Kimi K2.7, GPTsAPI, Groq, Higgsfield.

## Global Constraints

- Authoritative design: `plans/2026-07-21_claude-removal-codex-home-manager-design.md` at commit `310ca3f`.
- No production K2B process may require `OPENAI_API_KEY` or silently fall back to an OpenAI API credential.
- Do not add the OpenAI Agents SDK. Telegram is retired, not replaced.
- Retain Kimi, GPTsAPI, Groq, and Higgsfield according to `K2B-Vault/wiki/context/context_llm-providers.md`.
- `.agents/skills` becomes the only deployed skill tree. `AGENTS.md` becomes the only live top-level instruction authority.
- Preserve useful Claude history as an immutable archive, but no production reader may treat `~/.claude` as live memory after cutover.
- The Operations Console and Home Manager v1 are read-only. No general shell, retry, schedule-edit, or device-control endpoint.
- Every background job must identify its executor host and provider and must emit evidence. Silence is stale, never success.
- Home Manager credentials are unavailable to general K2B jobs.
- Raw vault captures remain immutable.
- The Mini has no Git history. Runtime-visible files must travel through `scripts/deploy-to-mini.sh`.
- Use `K2B_*` environment variables for host-specific overrides. Never depend on `CLAUDE_PROJECT_DIR`.
- Every task uses red-green-refactor where code changes. Each task gets an independent review before its task commit unless Keith explicitly overrides that task's review gate.
- For Codex-built changes, official review is `scripts/review.sh ... --builder-family openai --primary kimi --no-fallback --wait`.
- Do not start Task 2 until Task 1 has current Mac Mini evidence.
- Do not start Task 11 until Tasks 2 through 10 have passed their rollback and parity gates.
- Preserve unrelated dirty files in the main checkout. Execute in an isolated worktree created with `superpowers:using-git-worktrees`.

---

## Program Dependency Order

| Package | Tasks | Starts after | Production effect |
|---|---:|---|---|
| Gate 0 evidence | 1 | none | read-only inventory and credential rotation |
| Codex authority | 2 | Task 1 | Codex surfaces become canonical; Claude remains rollback-only |
| History and memory | 3 | Task 1 | Claude history freezes; live readers move to Codex/vault |
| Job observability | 4-6 | Task 1 | receipts, API, and Console are introduced read-only |
| Claude-free intake | 7 | Tasks 4-6 | new service runs in shadow mode |
| Routine migration | 8 | Tasks 4-7 | schedules move one job at a time |
| Home Manager | 9 | Tasks 4-6 | read-only NAS/router/Syncthing visibility |
| Mini deployment cutover | 10 | Tasks 2-9 | new services become production |
| Claude/Telegram removal | 11 | Task 10 burn-in gate | old runtime and instruction surfaces are removed |
| Final burn-in | 12 | Task 11 | zero-Claude and zero-OpenAI-API evidence closes migration |

## Locked File Structure

### New runtime units

- `k2b-core/`: Claude-free intake, extraction, memory maintenance, and health service.
- `home-manager/`: read-only NAS, router, Syncthing, and host-status service.
- `scripts/jobs/registry.json`: versioned job definitions and evidence contracts.
- `scripts/lib/job_events.py`: the only writer for host-scoped job event JSONL.
- `scripts/job-run.sh`: command wrapper that emits started and terminal events.
- `K2B-Vault/System/operations/job-events/<host>.jsonl`: one writer per host; never a shared multi-host file.
- `K2B-Vault/System/operations/artifacts/<job-id>/`: sanitized job evidence.
- `K2B-Vault/System/operations/home-manager/nodes.json`: sanitized read-only node snapshot.

### Dashboard additions

- `k2b-dashboard/src/server/lib/job-status.ts`: folds registry plus host event streams into current state.
- `k2b-dashboard/src/server/lib/home-status.ts`: validates Home Manager snapshots.
- `k2b-dashboard/src/server/routes/operations.ts`: read-only operations endpoints.
- `k2b-dashboard/src/server/routes/home.ts`: read-only home endpoints.
- `k2b-dashboard/src/client/components/OperationsOverview.tsx`: infrastructure and attention summary.
- `k2b-dashboard/src/client/components/JobTable.tsx`: job state table.
- `k2b-dashboard/src/client/components/EventTimeline.tsx`: chronological receipts.
- `k2b-dashboard/src/client/components/HomeNodes.tsx`: read-only home-node state.

### Final removals

- `CLAUDE.md`
- `k2b-remote/CLAUDE.md`
- `.claude/`
- Anthropic and Telegram-only sources under `k2b-remote/`
- `scripts/claude-minimaxi.sh`
- `scripts/claude-minimaxi-usage-report.sh`
- `scripts/router-watchdog/bin/checks/claude.sh`

Historical plans, migration exports, review logs, and the immutable Claude archive remain allowed to mention Claude.

---

### Task 1: Gate 0 Runtime Inventory and Credential Freeze

**Files:**
- Create: `scripts/audit-claude-runtime.sh`
- Create: `tests/audit-claude-runtime.test.sh`
- Create: `docs/runbooks/claude-removal-gate0.md`
- Create at execution time, do not commit: `/private/tmp/k2b-claude-audit-macbook.json`
- Create on Mini at execution time, do not commit: `/private/tmp/k2b-claude-audit-mini.json`

**Interfaces:**
- Consumes: local filesystem, `launchctl`, `pm2`, `crontab`, package manifests, hook configs, and environment variable names.
- Produces: sanitized JSON with `host`, `reachable`, `processes`, `launchd`, `pm2`, `cron`, `packages`, `instruction_paths`, `hook_paths`, `credential_names`, and `findings`.

- [ ] **Step 1: Write the failing audit test**

Create a temporary fixture containing a Claude package, `.claude` hook, `CLAUDE_PROJECT_DIR`, Telegram dependency, and an environment file with a secret value. Assert that the audit reports names and paths but never the fixture's secret value.

```bash
result="$(K2B_AUDIT_ROOT="$fixture" K2B_AUDIT_FIXTURE=1 bash scripts/audit-claude-runtime.sh)"
jq -e '.findings[] | select(.kind == "anthropic-package")' <<<"$result"
jq -e '.findings[] | select(.kind == "claude-hook")' <<<"$result"
jq -e '.findings[] | select(.kind == "telegram-runtime")' <<<"$result"
if grep -Fq 'fixture-secret-value' <<<"$result"; then exit 1; fi
```

- [ ] **Step 2: Run the test and verify red**

Run: `bash tests/audit-claude-runtime.test.sh`

Expected: FAIL because `scripts/audit-claude-runtime.sh` does not exist.

- [ ] **Step 3: Implement the read-only auditor**

The script must start with:

```bash
#!/usr/bin/env bash
set -euo pipefail
AUDIT_ROOT="${K2B_AUDIT_ROOT:-$HOME}"
FIXTURE_MODE="${K2B_AUDIT_FIXTURE:-0}"
```

It must inspect only names, executable paths, labels, and dependency keys. It must never print environment values, plist credential values, `.env` contents, MCP headers, tokens, or auth files. Use Python's `json` module to serialize the final object, not string-built JSON.

Required finding kinds are:

```text
anthropic-package
claude-instruction
claude-skill
claude-hook
claude-schedule
claude-launch-action
claude-mcp
claude-memory-reader
telegram-runtime
openai-api-credential-name
stale-k2b-launchagent
unverified-mini-surface
```

- [ ] **Step 4: Run fixture and syntax verification**

Run:

```bash
bash -n scripts/audit-claude-runtime.sh
bash tests/audit-claude-runtime.test.sh
```

Expected: both PASS and the test prints `audit-claude-runtime: PASS`.

- [ ] **Step 5: Run live MacBook audit**

Run:

```bash
bash scripts/audit-claude-runtime.sh > /private/tmp/k2b-claude-audit-macbook.json
jq '{host, finding_counts: (.findings | group_by(.kind) | map({kind: .[0].kind, count: length}))}' /private/tmp/k2b-claude-audit-macbook.json
```

Expected: valid sanitized JSON. Confirm the stale `com.k2b-remote.app` LaunchAgent is represented.

- [ ] **Step 6: Run live Mini audit**

Run:

```bash
ssh -o BatchMode=yes -o ConnectTimeout=8 macmini \
  'K2B_AUDIT_ROOT="$HOME" bash -s' \
  < scripts/audit-claude-runtime.sh \
  > /private/tmp/k2b-claude-audit-mini.json
jq '{host, reachable, findings: (.findings | length)}' /private/tmp/k2b-claude-audit-mini.json
```

Expected: `reachable: true`. Stop the program if SSH fails or the result is not valid JSON.

- [ ] **Step 7: Rotate exposed Claude-era MCP credentials**

Follow `docs/runbooks/claude-removal-gate0.md`. Rotate Fireflies and every literal credential found in project or Claude MCP configuration, update only the approved credential store, verify the replacement consumer, and revoke the old credential. Record credential names and completion timestamps, never values.

Stop if any credential owner or replacement consumer is unknown.

- [ ] **Step 8: Commit Gate 0 tooling**

```bash
git add scripts/audit-claude-runtime.sh tests/audit-claude-runtime.test.sh docs/runbooks/claude-removal-gate0.md
git commit -m "chore: add Claude runtime inventory gate"
```

---

### Task 2: Codex-Only Instruction, Skill, and Hook Authority

**Files:**
- Modify: `AGENTS.md`
- Modify: `.codex/hooks.json`
- Modify: `.agents/skills/k2b-compile/SKILL.md`
- Modify: `.agents/skills/k2b-daily-capture/SKILL.md`
- Modify: `.agents/skills/k2b-email/SKILL.md`
- Modify: `.agents/skills/k2b-improve/SKILL.md`
- Modify: `.agents/skills/k2b-infographic/SKILL.md`
- Modify: `.agents/skills/k2b-lint/SKILL.md`
- Modify: `.agents/skills/k2b-media-generator/SKILL.md`
- Modify: `.agents/skills/k2b-observer/SKILL.md`
- Modify: `.agents/skills/k2b-orchestrator/SKILL.md`
- Modify: `.agents/skills/k2b-plate/SKILL.md`
- Modify: `.agents/skills/k2b-portfolio/SKILL.md`
- Modify: `.agents/skills/k2b-research/SKILL.md`
- Modify: `.agents/skills/k2b-review/SKILL.md`
- Modify: `.agents/skills/k2b-scheduler/SKILL.md`
- Modify: `.agents/skills/k2b-ship/SKILL.md`
- Modify: `.agents/skills/k2b-sync/SKILL.md`
- Modify: `.agents/skills/k2b-tldr/SKILL.md`
- Modify: `.agents/skills/k2b-weave/SKILL.md`
- Create: `scripts/verify-codex-authority.sh`
- Create: `tests/verify-codex-authority.test.sh`
- Modify: `scripts/hooks/session-start.sh`
- Modify: `scripts/hooks/post-tool-skill-track.sh`
- Modify: `scripts/hooks/stop-observe.sh`
- Modify: `scripts/hooks/youtube-transcript-prefetch.sh`
- Modify: `tests/codex-hooks.test.sh`
- Modify: `tests/session-start-hook.test.sh`
- Modify: `tests/post-tool-skill-track.test.sh`
- Modify: `tests/stop-observe-hook.test.sh`
- Keep unchanged until Task 11: `CLAUDE.md`, `.claude/`

**Interfaces:**
- Consumes: Codex `AGENTS.md`, skill, and hook contracts.
- Produces: one canonical Codex instruction and skill system that does not read Claude state.

- [ ] **Step 1: Write authority failures before changing instructions**

The fixture test must fail when any deployed file under `AGENTS.md`, `.agents/skills`, `.codex/hooks.json`, or active hook scripts contains these live dependencies:

```text
CLAUDE_PROJECT_DIR
~/.claude/projects
.claude/skills
Claude Code desktop
Telegram scheduler
K2B_LLM_PROVIDER=minimax
```

The scanner must allow accurate historical prose only under `plans/`, `docs/migration-exports/`, `.code-reviews/`, and the future immutable archive.

- [ ] **Step 2: Run the authority test and verify red**

Run: `bash tests/verify-codex-authority.test.sh`

Expected: FAIL with findings from the current eighteen skills and active hooks.

- [ ] **Step 3: Rewrite skill behavior, not only names**

Apply these exact routing rules to every listed skill:

```text
instruction authority = AGENTS.md
skill root = .agents/skills
interactive commander = Codex
background text worker = Kimi K2.7
OpenAI-built diff reviewer = Kimi, no fallback
Kimi-built diff reviewer = Codex
scheduler = registered host job plus observable receipt
alerts = Operations Console attention queue
capture = dashboard or vault drop, never Telegram
memory = K2B-Vault/System/memory plus Codex sessions where explicitly needed
```

Do not reactivate dormant capture or publishing skills.

- [ ] **Step 4: Port required hooks**

`.codex/hooks.json` must register:

```json
{
  "hooks": {
    "SessionStart": [{"matcher":"","hooks":[{"type":"command","command":"export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/session-start.sh\"","timeout":10}]}],
    "UserPromptSubmit": [{"matcher":"","hooks":[{"type":"command","command":"export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/youtube-transcript-prefetch.sh\"","timeout":180}]}],
    "PostToolUse": [{"matcher":"Skill","hooks":[{"type":"command","command":"export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/post-tool-skill-track.sh\"","timeout":3}]}],
    "Stop": [{"hooks":[{"type":"command","command":"export K2B_HOOK_PROVIDER=codex; export K2B_PROJECT_ROOT=\"${K2B_PROJECT_ROOT:-$HOME/Projects/K2B}\"; \"$K2B_PROJECT_ROOT/scripts/hooks/stop-observe.sh\"","timeout":5}]}]
  }
}
```

Port YouTube payload extraction to the verified Codex UserPromptSubmit field. If a fixture proves the event has no prompt text, remove this hook and keep explicit URL capture instead of guessing a payload field.

- [ ] **Step 5: Run focused Codex authority verification**

```bash
bash tests/verify-codex-authority.test.sh
bash tests/codex-hooks.test.sh
bash tests/session-start-hook.test.sh
bash tests/post-tool-skill-track.test.sh
bash tests/stop-observe-hook.test.sh
```

Expected: all PASS. `scripts/verify-codex-authority.sh` returns zero on the real repo's live surfaces.

- [ ] **Step 6: Commit the Codex authority ship**

Stage only the files listed in this task, then commit:

```bash
git commit -m "refactor: make Codex the sole K2B authority"
```

Do not delete the rollback-only Claude files in this task.

---

### Task 3: Freeze Claude History and Migrate Live Memory Readers

**Files:**
- Create: `scripts/export-claude-history.py`
- Create: `tests/test_export_claude_history.py`
- Modify: `scripts/lib/eod_capture.py`
- Modify: `tests/test_eod_capture.py`
- Modify: `scripts/hooks/session-start.sh`
- Modify: `scripts/observer-loop.sh`
- Modify: `.agents/skills/k2b-observer/SKILL.md`
- Modify: `.agents/skills/k2b-improve/SKILL.md`
- Modify: local Codex automation `k2b-forge-audit` through the automation manager
- Create at execution time: `K2B-Vault/Archive/claude-history/manifest.json`

**Interfaces:**
- Consumes: Claude JSONL history read-only and Codex session JSONL.
- Produces: immutable archive manifest with source path hash, export time, session count, and sanitized artifact paths; live readers use Codex and vault sources only.

- [ ] **Step 1: Write export and reader tests**

Test that the exporter:

```python
assert manifest["schemaVersion"] == 1
assert manifest["frozen"] is True
assert manifest["sourceSessionCount"] == 2
assert "secret-value" not in json.dumps(manifest)
assert all("../" not in item["relativePath"] for item in manifest["artifacts"])
```

Test that `eod_capture` default discovery includes Codex sessions and excludes live Claude directories after `K2B_CLAUDE_ARCHIVE_COMPLETE=1`.

- [ ] **Step 2: Run tests and verify red**

```bash
python3 -m pytest tests/test_export_claude_history.py tests/test_eod_capture.py -q
```

Expected: exporter tests FAIL because the module does not exist.

- [ ] **Step 3: Implement one-time export**

The CLI contract is:

```bash
python3 scripts/export-claude-history.py \
  --source "$HOME/.claude/projects" \
  --destination "$K2B_VAULT/Archive/claude-history" \
  --redact \
  --freeze
```

It copies selected K2B/K2Bi session JSONL into a date-stamped immutable directory, removes tool payloads and obvious credential assignments, hashes each output, and writes `manifest.json` last. A second `--freeze` run with the same source hashes must exit zero without rewriting artifacts.

- [ ] **Step 4: Rewrite live readers**

Use these source rules:

```python
LIVE_SESSION_ROOTS = [Path.home() / ".codex" / "sessions"]
CANONICAL_MEMORY_ROOT = Path(os.environ.get("K2B_VAULT_PATH", default_vault)) / "System" / "memory"
```

Claude archives may be used only by an explicit historical-analysis flag, never default discovery.

- [ ] **Step 5: Update Forge Audit automation**

Remove `~/.claude/projects` and `CLAUDE.md` from the prompt. Keep proposal-only behavior. Read `/Users/keithmbpm2/.codex/sessions` and canonical vault memory. Delivery remains a sanitized report through the existing mover until job receipts land in Task 8.

- [ ] **Step 6: Run export in preview and then freeze**

Run preview first and inspect counts without printing content. Then run the frozen export. Verify hashes and open three random artifacts to confirm redaction and readability.

- [ ] **Step 7: Run memory suites and commit**

```bash
python3 -m pytest tests/test_export_claude_history.py tests/test_eod_capture.py -q
bash tests/session-start-hook.test.sh
bash tests/observer-mark-processed.test.sh
git commit -m "refactor: archive Claude history and migrate live memory"
```

---

### Task 4: Host-Scoped Job Event Protocol

**Files:**
- Create: `scripts/jobs/registry.json`
- Create: `scripts/lib/job_events.py`
- Create: `scripts/job-run.sh`
- Create: `tests/test_job_events.py`
- Create: `tests/job-run.test.sh`
- Modify: `scripts/tier3-paths.yml`
- Modify: `scripts/ownership-watchlist.yml`

**Interfaces:**
- Consumes: one job definition and a command argv.
- Produces: append-only `JobEventV1` JSONL in a host-specific file.

Define the event exactly:

```python
class JobEventV1(TypedDict, total=False):
    schemaVersion: Literal[1]
    eventId: str
    runId: str
    jobId: str
    host: str
    provider: str
    state: Literal["started", "succeeded", "failed", "skipped", "timed_out", "disabled"]
    ts: str
    durationMs: int
    exitCode: int
    summary: str
    error: str
    artifacts: list[str]
    retryCount: int
```

- [ ] **Step 1: Write failing single-writer and wrapper tests**

Tests must prove:

- two concurrent writers produce valid complete JSON lines;
- one host writes only its own `<host>.jsonl`;
- summaries and errors redact values matching `KEY=...`, bearer headers, and common token patterns;
- success, failure, skip, and timeout create terminal events;
- the wrapped command receives its original argv without shell re-parsing;
- a missing registry job ID fails before executing the command.

- [ ] **Step 2: Run tests and verify red**

```bash
python3 -m pytest tests/test_job_events.py -q
bash tests/job-run.test.sh
```

Expected: both fail because the implementation is absent.

- [ ] **Step 3: Implement the event writer**

Use `fcntl.flock` around a single `os.write` append. Resolve the event root from `K2B_JOB_EVENT_ROOT`, defaulting to `K2B-Vault/System/operations/job-events`. Reject a caller-supplied host containing characters outside `[A-Za-z0-9._-]`.

CLI contract:

```bash
python3 scripts/lib/job_events.py emit \
  --job-id k2b-weave \
  --run-id UUID \
  --provider kimi \
  --state started \
  --summary "weave started"
```

- [ ] **Step 4: Implement the wrapper without `eval`**

Contract:

```bash
bash scripts/job-run.sh --job k2b-weave --timeout 1800 -- \
  bash scripts/k2b-weave.sh
```

The wrapper looks up provider and enabled state in `registry.json`, emits `started`, runs the argv after `--`, and emits exactly one terminal event using a trap. Use a child process group for timeout termination. Never build the command from a string.

- [ ] **Step 5: Seed the registry**

Register these stable IDs even before activation:

```text
k2b-weave
k2b-compile-maintenance
k2b-observer
k2b-daily-inbox
k2b-weekly-vault-health
k2b-self-improvement
k2b-external-research
k2b-forge-audit
k2b-aws-router-monitor
k2b-nas-health
k2b-syncthing-health
k2b-core-intake
```

Every record includes `displayName`, `category`, `owner`, `executorHost`, `provider`, `enabled`, `freshnessSeconds`, `timeoutSeconds`, `artifactGlobs`, and `runbook`.

- [ ] **Step 6: Run tests and commit**

```bash
python3 -m pytest tests/test_job_events.py -q
bash tests/job-run.test.sh
git commit -m "feat: add observable K2B job receipts"
```

---

### Task 5: Operations Read Model and API

**Files:**
- Modify: `k2b-dashboard/package.json`
- Modify: `k2b-dashboard/package-lock.json`
- Modify: `k2b-dashboard/src/server/lib/vault-paths.ts`
- Create: `k2b-dashboard/src/server/lib/job-status.ts`
- Create: `k2b-dashboard/src/server/lib/job-status.test.ts`
- Create: `k2b-dashboard/src/server/routes/operations.ts`
- Create: `k2b-dashboard/src/server/routes/operations.test.ts`
- Modify: `k2b-dashboard/src/server/index.ts`

**Interfaces:**
- Consumes: `scripts/jobs/registry.json` and all host event JSONL files.
- Produces: `GET /api/operations/summary`, `/jobs`, `/events`, and `/jobs/:id/runs`.

Define the public status shape:

```ts
export type JobState = 'healthy' | 'running' | 'waiting' | 'degraded' | 'failed' | 'stale' | 'disabled'

export interface JobStatus {
  jobId: string
  displayName: string
  category: string
  executorHost: string
  provider: string
  state: JobState
  lastRunAt: string | null
  lastSuccessAt: string | null
  durationMs: number | null
  nextExpectedAt: string | null
  summary: string
  runbook: string
  artifacts: string[]
}
```

- [ ] **Step 1: Add Vitest and write folding tests**

Add `"test": "vitest run"` and Vitest as a dev dependency. Fixtures must cover out-of-order lines, malformed lines, duplicate event IDs, running, failed after previous success, stale, disabled, and unknown registry job IDs.

- [ ] **Step 2: Run tests and verify red**

```bash
cd k2b-dashboard
npm test -- src/server/lib/job-status.test.ts
```

Expected: FAIL because `job-status.ts` does not exist.

- [ ] **Step 3: Implement deterministic event folding**

Rules:

```text
disabled registry entry -> disabled
latest event started with no terminal and within timeout -> running
latest event started beyond timeout -> failed
latest terminal failed or timed_out -> failed
last success older than freshnessSeconds -> stale
never run and enabled -> waiting
success within freshness -> healthy
malformed lines -> ignored and counted as degraded evidence
```

Sort events by parsed timestamp plus file order. Deduplicate on `eventId`.

- [ ] **Step 4: Implement read-only routes**

All routes are GET. Reject invalid job IDs with HTTP 400. Resolve artifact links only when they remain under the configured operations artifact root. Do not return raw environment, arbitrary log paths, or command argv.

- [ ] **Step 5: Run API, type, and build verification**

```bash
cd k2b-dashboard
npm test
npm run typecheck
npm run build
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: expose read-only operations status API"
```

---

### Task 6: Operations Console UI

**Files:**
- Modify: `k2b-dashboard/src/client/hooks/api.ts`
- Create: `k2b-dashboard/src/client/components/OperationsOverview.tsx`
- Create: `k2b-dashboard/src/client/components/JobTable.tsx`
- Create: `k2b-dashboard/src/client/components/EventTimeline.tsx`
- Create: `k2b-dashboard/src/client/components/AttentionQueue.tsx`
- Create: `k2b-dashboard/src/client/components/operations-format.ts`
- Create: `k2b-dashboard/src/client/components/operations-format.test.ts`
- Modify: `k2b-dashboard/src/client/App.tsx`
- Modify: `k2b-dashboard/src/client/index.css`

**Interfaces:**
- Consumes: Task 5 API shapes.
- Produces: a read-only Console showing current state and evidence without mutation controls.

- [ ] **Step 1: Add client contract tests or pure formatting tests**

Extract `formatJobAge`, `sortAttention`, and `stateLabel` as pure exports. Test that failed precedes stale, stale precedes degraded, disabled never appears in Attention, and timestamps render in `Asia/Macau`.

- [ ] **Step 2: Run client tests and verify red**

Run: `cd k2b-dashboard && npm test`

Expected: FAIL because the components and formatters are absent.

- [ ] **Step 3: Implement the Console views**

The initial screen must show:

```text
overview cards: Mini, MacBook, vault sync, NAS, router, providers
attention queue: failed, stale, degraded
job table columns: job, state, host, provider, last success, duration, next expected
timeline: timestamp, job, state, host, summary
```

There must be no buttons or API calls for start, stop, retry, enable, disable, edit schedule, shell, or device control. Runbooks are displayed as text or copied locally only.

- [ ] **Step 4: Verify loading, empty, malformed, and error states**

Use fixture responses and confirm each state says what is unknown. Never render missing data as healthy.

- [ ] **Step 5: Run verification and commit**

```bash
cd k2b-dashboard
npm test
npm run typecheck
npm run build
git commit -m "feat: add K2B Operations Console"
```

---

### Task 7: Claude-Free Core Intake Service

**Files:**
- Create: `k2b-core/package.json`
- Create: `k2b-core/package-lock.json`
- Create: `k2b-core/tsconfig.json`
- Create: `k2b-core/ecosystem.config.cjs`
- Create: `k2b-core/src/config.ts`
- Create: `k2b-core/src/logger.ts`
- Create: `k2b-core/src/types.ts`
- Create: `k2b-core/src/receipt.ts`
- Create: `k2b-core/src/intake-processor.ts`
- Create: `k2b-core/src/intake-watcher.ts`
- Create: `k2b-core/src/voice.ts`
- Create: `k2b-core/src/memory-maintenance.ts`
- Create: `k2b-core/src/health.ts`
- Create: `k2b-core/src/index.ts`
- Create: `k2b-core/src/intake-processor.test.ts`
- Create: `k2b-core/src/intake-watcher.test.ts`
- Create: `k2b-core/src/config.test.ts`
- Read from existing implementation: `k2b-remote/src/intake-watcher.ts`, `voice.ts`, `memory.ts`, `health.ts`, `attachmentIngest.ts`
- Do not modify yet: `k2b-remote/`

**Interfaces:**
- Consumes: dashboard manifests under `Assets/intake/<uuid>/manifest.json`.
- Produces: deterministic extraction, optional registered Kimi job enqueue artifact, processed/error sentinels, and `k2b-core-intake` job receipts.

Define the result:

```ts
export interface IntakeReceiptV1 {
  schemaVersion: 1
  uuid: string
  type: 'url' | 'text' | 'audio' | 'fireflies' | 'feedback'
  source: string
  state: 'accepted' | 'extracted' | 'queued' | 'failed'
  createdAt: string
  completedAt: string
  artifactPaths: string[]
  processingJobId?: string
  error?: string
}
```

`k2b-core/package.json` must expose `build`, `start`, `dev`, `test`, and `typecheck` scripts matching the existing TypeScript/Vitest conventions. Generate and commit `package-lock.json` with `npm install`, then use `npm ci` for all subsequent verification.

- [ ] **Step 1: Write red tests for the extraction boundary**

Prove:

- text, URL, fireflies, and feedback are captured without invoking an agent;
- audio uses an injected transcriber and records the transcript artifact;
- invalid manifests fail closed;
- path traversal and symlink escape are rejected;
- processed moves are idempotent;
- `.done` contains an `IntakeReceiptV1`, not an Anthropic response;
- provider unavailability does not lose the accepted capture;
- no source imports `@anthropic-ai/claude-agent-sdk`, `grammy`, or Telegram modules.

- [ ] **Step 2: Run tests and verify red**

```bash
cd k2b-core
npm ci
npm test
```

Expected: FAIL until the service is implemented.

- [ ] **Step 3: Implement provider-neutral configuration**

Required environment names:

```text
K2B_PROJECT_ROOT
K2B_VAULT_PATH
K2B_CORE_STORE_DIR
K2B_JOB_EVENT_ROOT
GROQ_API_KEY
HTTP_PROXY
HTTPS_PROXY
```

Reject `CLAUDE_PROJECT_ROOT`, `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_ID`, and `OPENAI_API_KEY` as active configuration. The process must start without all four.

- [ ] **Step 4: Implement deterministic processing and optional queueing**

`intake-processor.ts` validates, extracts, writes receipt artifacts, and optionally writes a bounded Kimi job request into `System/operations/requests/`. It must never execute a prompt inline from an HTTP request or filesystem event.

- [ ] **Step 5: Implement shadow mode**

`K2B_CORE_SHADOW=1` reads copied fixture manifests from a shadow input directory and writes to a shadow processed directory. It must not race the production `k2b-remote` watcher.

- [ ] **Step 6: Run unit, type, and build verification**

```bash
cd k2b-core
npm test
npm run typecheck
npm run build
```

Expected: all PASS and dependency scan finds no Anthropic or Telegram package.

- [ ] **Step 7: Run shadow parity corpus**

Replay sanitized text, URL, feedback, audio, invalid, traversal, and duplicate fixtures. Compare durable acceptance and extraction outputs, not Claude-generated prose.

- [ ] **Step 8: Commit**

```bash
git add k2b-core
git commit -m "feat: add Claude-free K2B core intake service"
```

---

### Task 8: Migrate Background Routines and Schedules

**Files:**
- Modify: `scripts/k2b-weave.sh`
- Modify: `scripts/observer-loop.sh`
- Create: `scripts/jobs/daily-inbox.sh`
- Create: `scripts/jobs/weekly-vault-health.sh`
- Create: `scripts/jobs/self-improvement.sh`
- Create: `scripts/jobs/external-research.sh`
- Create: `scripts/jobs/nas-health.sh`
- Create: `scripts/jobs/syncthing-health.sh`
- Create: `launchd/jobs/com.k2b.weave.plist`
- Create: `launchd/jobs/com.k2b.observer.plist`
- Create: `launchd/jobs/com.k2b.daily-inbox.plist`
- Create: `launchd/jobs/com.k2b.weekly-vault-health.plist`
- Create: `launchd/jobs/com.k2b.self-improvement.plist`
- Create: `launchd/jobs/com.k2b.external-research.plist`
- Create: `scripts/jobs/install.sh`
- Create: `tests/jobs-routines.test.sh`
- Modify: `scripts/jobs/registry.json`
- Modify: `.agents/skills/k2b-scheduler/SKILL.md`
- Modify: `.agents/skills/k2b-weave/SKILL.md`

**Interfaces:**
- Consumes: Task 4 wrapper and registry.
- Produces: launchd-triggered observable routines with no Telegram output.

- [ ] **Step 1: Write schedule and wrapper tests**

The test parses every plist and proves:

- command begins with `scripts/job-run.sh`;
- job ID exists in `registry.json`;
- Mini-only job paths resolve under `/Users/fastshower/Projects/K2B` through an installer substitution, not hardcoded MacBook paths;
- no plist invokes Claude, Telegram, `.claude`, or an OpenAI API key;
- no job ID has two enabled schedule definitions;
- every routine writes a declared artifact or a terminal skip reason.

- [ ] **Step 2: Run tests and verify red**

Run: `bash tests/jobs-routines.test.sh`

Expected: FAIL because routine plists do not exist.

- [ ] **Step 3: Implement deterministic routine front halves**

Daily inbox and Syncthing/NAS health are deterministic. Weekly vault health gathers deterministic counts before optional Kimi summary. Self-improvement and external research use Kimi with explicit timeout and no provider fallback. Weave keeps its existing Kimi route but removes Claude session startup and Telegram delivery.

- [ ] **Step 4: Install disabled schedules on the Mini**

`scripts/jobs/install.sh --host mini --disabled` substitutes project/vault paths, validates plists with `plutil -lint`, and installs none as active. Compare each new schedule against PM2, cron, Claude backups, and existing launchd labels before enabling it.

- [ ] **Step 5: Migrate one routine at a time**

Order:

```text
daily-inbox
syncthing-health
weekly-vault-health
observer
weave
self-improvement
external-research
```

For each job: disable its old trigger, enable one new launchd label, force one run, confirm receipt plus artifact in the Console, wait one natural interval or approved compressed canary, then continue.

- [ ] **Step 6: Rewrite Forge and AWS automation evidence**

Keep the two MacBook Codex automations only while needed. Their prompts must create sanitized evidence artifacts and the Console must mark them stale when artifacts stop arriving. Forge uses Codex sessions only. AWS/router monitoring must not claim Mini execution until credentials and network behavior are proven there.

- [ ] **Step 7: Run verification and commit**

```bash
bash tests/jobs-routines.test.sh
bash tests/test-k2b-weave.sh
bash tests/observer-mark-processed.test.sh
git commit -m "refactor: migrate K2B routines to observable jobs"
```

---

### Task 9: Read-Only Home Manager and NAS Integration

**Files:**
- Create: `home-manager/package.json`
- Create: `home-manager/package-lock.json`
- Create: `home-manager/tsconfig.json`
- Create: `home-manager/ecosystem.config.cjs`
- Create: `home-manager/src/types.ts`
- Create: `home-manager/src/config.ts`
- Create: `home-manager/src/command.ts`
- Create: `home-manager/src/connectors/nas.ts`
- Create: `home-manager/src/connectors/router.ts`
- Create: `home-manager/src/connectors/syncthing.ts`
- Create: `home-manager/src/snapshot.ts`
- Create: `home-manager/src/server.ts`
- Create: `home-manager/src/index.ts`
- Create: `home-manager/src/connectors/nas.test.ts`
- Create: `home-manager/src/connectors/router.test.ts`
- Create: `home-manager/src/connectors/syncthing.test.ts`
- Create: `home-manager/src/server.test.ts`
- Create: `k2b-dashboard/src/server/lib/home-status.ts`
- Create: `k2b-dashboard/src/server/lib/home-status.test.ts`
- Create: `k2b-dashboard/src/server/routes/home.ts`
- Create: `k2b-dashboard/src/client/components/HomeNodes.tsx`
- Modify: `k2b-dashboard/src/server/index.ts`
- Modify: `k2b-dashboard/src/client/hooks/api.ts`
- Modify: `k2b-dashboard/src/client/App.tsx`

**Interfaces:**
- Consumes: scoped NAS SSH identity, router read-only HTTP endpoint, Syncthing local REST status.
- Produces: localhost-only `GET /health` and `GET /v1/nodes`; sanitized snapshot file for the dashboard.

Define the node shape:

```ts
export interface HomeNodeV1 {
  schemaVersion: 1
  nodeId: string
  kind: 'nas' | 'router' | 'syncthing' | 'host'
  displayName: string
  state: 'healthy' | 'degraded' | 'failed' | 'unknown' | 'disabled'
  observedAt: string
  summary: string
  metrics: Record<string, number | string | boolean | null>
  evidence: string[]
}
```

`home-manager/package.json` must expose `build`, `start`, `dev`, `test`, and `typecheck`. It may use Node 20 built-ins plus `pino`; do not introduce a home-automation framework in this release. Generate and commit `package-lock.json` with `npm install`, then use `npm ci` for subsequent verification.

- [ ] **Step 1: Write connector refusal tests**

Prove:

- command runner accepts only configured argv templates and never a caller-supplied shell string;
- NAS connector uses a dedicated identity and read-only command allowlist;
- router URLs must match the configured local controller origin;
- server exposes GET only and returns 404/405 for mutation attempts;
- snapshots redact credentials and raw headers;
- a connector timeout yields `unknown` or `failed`, never healthy;
- camera and IoT control routes do not exist.

- [ ] **Step 2: Run tests and verify red**

```bash
cd home-manager
npm ci
npm test
```

Expected: FAIL until connectors are implemented.

- [ ] **Step 3: Provision scoped Mini-to-NAS identity**

Use a new NAS account or key restricted to the required read-only health commands and explicitly approved backup paths. Do not copy the MacBook admin identity to the Mini. Verify `ssh -o BatchMode=yes` and document the NAS account name and allowed operations without recording its private key.

Stop if Synology cannot enforce the agreed scope.

- [ ] **Step 4: Implement read-only connectors and snapshot**

NAS metrics include reachability, volume state, used/free bytes, share reachability for approved shares, and last scoped backup evidence. Router metrics include controller reachability and routing-group health. Syncthing metrics include vault folder state, local/remote bytes, pending files, and last error.

- [ ] **Step 5: Implement dashboard Home view**

Dashboard reads the snapshot or localhost API. It shows node state, observation age, concise metrics, and evidence. It has no device action controls.

- [ ] **Step 6: Run full verification and commit**

```bash
cd home-manager && npm test && npm run typecheck && npm run build
cd ../k2b-dashboard && npm test && npm run typecheck && npm run build
git commit -m "feat: add read-only K2B Home Manager"
```

---

### Task 10: Mini Deployment, Shadow Burn-In, and Cutover

**Files:**
- Modify: `scripts/deploy-to-mini.sh`
- Modify: `tests/deploy-to-mini.test.sh`
- Create: `scripts/verify-mini-cutover.sh`
- Create: `tests/verify-mini-cutover.test.sh`
- Modify: `README.md`
- Modify: `DEVLOG.md` through the ship workflow
- Deploy: `k2b-core/ecosystem.config.cjs`
- Deploy: `home-manager/ecosystem.config.cjs`
- Keep rollback artifact: current `k2b-remote` deployment bundle and sanitized state manifest

**Interfaces:**
- Consumes: Tasks 2 through 9 artifacts.
- Produces: Mini running dashboard, `k2b-core`, Home Manager, and observable jobs with the old remote process stopped only after parity proof.

- [ ] **Step 1: Write deployment fixture tests**

Prove deployment:

- syncs `AGENTS.md`, `.agents/skills`, `.codex/hooks.json`, `k2b-core`, `home-manager`, dashboard, scripts, job registry, and launchd definitions;
- does not require `.claude` or `CLAUDE.md` for new services;
- builds before restart;
- verifies exact PM2 process names `k2b-core`, `k2b-dashboard`, and `k2b-home-manager`;
- preserves `.env`, stores, artifacts, and the vault;
- creates a rollback manifest before stopping `k2b-remote`;
- fails closed if Mini SSH is unavailable.

- [ ] **Step 2: Run deployment test and verify red**

Run: `bash tests/deploy-to-mini.test.sh && bash tests/verify-mini-cutover.test.sh`

Expected: cutover fixture fails until deployment supports the new services.

- [ ] **Step 3: Add deployment categories and health checks**

Supported explicit categories become:

```text
skills
core
dashboard
home
jobs
scripts
all
```

Remove the ambiguous `code` name after keeping one documented compatibility release that maps `code` to `core` with a warning.

- [ ] **Step 4: Deploy shadow services**

Deploy `k2b-core` with `K2B_CORE_SHADOW=1` and Home Manager read-only. Keep `k2b-remote` active only for the parity window. Confirm no shared intake directory and no duplicate scheduler.

- [ ] **Step 5: Pass shadow gates**

Required evidence:

```text
seven intake fixture classes accepted correctly
no lost or duplicate capture
job Console shows both hosts honestly
vault Syncthing healthy
NAS and router node timestamps fresh
no OpenAI API credential loaded
no Anthropic call from shadow services
```

- [ ] **Step 6: Cut over intake**

Stop `k2b-remote`, point `k2b-core` at the production intake directory, submit one text, one URL, one feedback, and one audio capture, and verify receipts plus artifacts. Keep the rollback bundle but do not permit automatic Claude restart.

- [ ] **Step 7: Run Mini cutover verifier and commit**

```bash
bash scripts/verify-mini-cutover.sh
bash tests/deploy-to-mini.test.sh
bash tests/verify-mini-cutover.test.sh
git commit -m "deploy: cut Mini over to Claude-free K2B services"
```

---

### Task 11: Remove Claude, Anthropic, and Telegram Runtime Surfaces

**Files:**
- Delete: `CLAUDE.md`
- Delete: `k2b-remote/CLAUDE.md`
- Delete: `.claude/`
- Delete after retained fixtures and runtime state are archived: `k2b-remote/`
- Delete: `scripts/claude-minimaxi.sh`
- Delete: `scripts/claude-minimaxi-usage-report.sh`
- Delete: `scripts/router-watchdog/bin/checks/claude.sh`
- Modify: `scripts/deploy-to-mini.sh`
- Modify: `scripts/router-watchdog/bin/check.sh`
- Modify: `scripts/router-watchdog/bin/score-nodes.py`
- Modify: `scripts/router-watchdog/bin/state-machine.py`
- Modify: `scripts/review.sh`
- Modify: `scripts/lib/review_runner.py`
- Modify: `scripts/audit-ownership.sh`
- Modify: `scripts/lint-memory.sh`
- Modify: `scripts/promote-learnings.py`
- Modify: `scripts/ownership-watchlist.yml`
- Modify: `scripts/tier3-paths.yml`
- Create: `scripts/verify-no-claude-runtime.sh`
- Create: `tests/verify-no-claude-runtime.test.sh`
- Create: `scripts/verify-zero-openai-api.sh`
- Create: `tests/verify-zero-openai-api.test.sh`

**Interfaces:**
- Consumes: successful Task 10 production evidence.
- Produces: executable proof that no live K2B surface depends on Claude, Anthropic, Telegram, or OpenAI API billing.

- [ ] **Step 1: Write final scanner fixtures before deletion**

`verify-no-claude-runtime` must fail on active fixtures containing an Anthropic import, `.claude` hook, Claude PM2 name, Telegram package, Claude MCP, live Claude memory reader, or `CLAUDE_PROJECT_DIR`. It must pass accurate historical references under the explicit archive allowlist.

`verify-zero-openai-api` must fail when production config, PM2 env-name output, launchd definitions, or runtime package code references `OPENAI_API_KEY`, `api.openai.com`, `@openai/agents`, or an API-key fallback. It may allow official documentation URLs and historical plans through exact path allowlists.

- [ ] **Step 2: Run scanners and verify red**

```bash
bash tests/verify-no-claude-runtime.test.sh
bash tests/verify-zero-openai-api.test.sh
```

Expected: fixture assertions pass, while real-repo mode reports current live surfaces before deletion.

- [ ] **Step 3: Remove active source and configuration surfaces**

Before deletion, run `git status --short -- CLAUDE.md .claude k2b-remote` in the execution worktree and in the main checkout. Stop if either checkout contains uncommitted user changes that are not already archived or incorporated. After that check, remove only after confirming each retained behavior's new owner. Delete the tracked `k2b-remote/` package in full; its sanitized runtime-state archive and rollback deployment bundle live outside the source tree. Rewrite the review matrix so OpenAI-built work routes to Kimi and Kimi-built work routes to Codex. Anthropic may remain as a historical label in old review records, not an active recommended command.

- [ ] **Step 4: Remove user-level runtime state with explicit target checks**

On MacBook and Mini:

```text
unload com.k2b-remote.app and remove its exact plist
remove Claude K2B scheduled backups after archive verification
remove Claude K2B MCP configuration after credential revocation
disable Claude K2B plugins and hooks
remove K2B-specific Claude auth/runtime state
```

Do not recursively delete user-wide Claude history until Keith confirms the exact archived paths and destructive targets. Prefer moving recoverable application state to a dated quarantine directory before permanent deletion.

- [ ] **Step 5: Run full removal verification**

```bash
bash scripts/verify-no-claude-runtime.sh
bash scripts/verify-zero-openai-api.sh
bash tests/verify-no-claude-runtime.test.sh
bash tests/verify-zero-openai-api.test.sh
bash scripts/verify-mini-cutover.sh
```

Expected: all PASS on both MacBook and Mini.

- [ ] **Step 6: Run affected regression suites**

```bash
cd k2b-core && npm test && npm run typecheck && npm run build
cd ../k2b-dashboard && npm test && npm run typecheck && npm run build
cd ../home-manager && npm test && npm run typecheck && npm run build
cd ..
bash tests/codex-hooks.test.sh
bash tests/deploy-to-mini.test.sh
bash tests/review-runner.test.sh
bash tests/router-watchdog.test.sh
bash tests/verify-codex-authority.test.sh
```

- [ ] **Step 7: Commit the removal**

```bash
git commit -m "refactor: remove Claude and Telegram from K2B"
```

---

### Task 12: Zero-Spend Burn-In and Program Closeout

**Files:**
- Create: `docs/runbooks/k2b-claude-free-burn-in.md`
- Create: `docs/evidence/k2b-claude-free-burn-in-YYYY-MM-DD.md`
- Modify: `plans/2026-06-14_codex-primary-migration-spec.md`
- Modify: `plans/2026-07-21_claude-removal-codex-home-manager-design.md`
- Modify: `README.md`
- Modify through `/ship`: `DEVLOG.md`, vault feature notes, concepts indexes, `wiki/log.md`, session summary, reminders, handoffs, and pending sync state

**Interfaces:**
- Consumes: all verification commands and Operations Console receipts.
- Produces: signed-off evidence that the new architecture remained healthy without Claude, Telegram, or OpenAI API spend.

- [ ] **Step 1: Define the burn-in window before starting it**

Use seven consecutive days unless Keith explicitly approves a shorter evidence window. Record start/end timestamps, expected runs for every enabled job, and the billing evidence source.

- [ ] **Step 2: Observe without repairing evidence**

The Console must show natural scheduled runs. If a job fails, preserve the failed receipt and create a new retry run; never edit the failed event. Any Claude process, Anthropic call, Telegram activity, API-key fallback, missing Mini evidence, or OpenAI API charge resets the burn-in window after correction.

- [ ] **Step 3: Verify final binary gates**

Record outputs for:

```bash
bash scripts/verify-no-claude-runtime.sh
bash scripts/verify-zero-openai-api.sh
bash scripts/verify-mini-cutover.sh
bash scripts/verify-codex-authority.sh
```

Also record:

```text
all enabled job freshness gates pass
MacBook and Mini vault replicas agree
NAS, router, and Syncthing nodes are fresh
Kimi review route works
GPTsAPI, Groq, and Higgsfield canaries follow routing authority
OpenAI API billing attributable to K2B is zero
```

- [ ] **Step 4: Mark superseded designs accurately**

Update the 2026-06-14 plan to state that its Telegram OpenAI Agents SDK bridge and Claude compatibility ships were superseded by the 2026-07-21 design. Do not rewrite its historical evidence.

- [ ] **Step 5: Run the authorized ship workflow**

Use `k2b-ship` with `BUILDER_FAMILY=openai`. Complete review or an explicit Keith override, commit, push, DEVLOG, vault feature-note lane movement, `wiki/log.md`, Step 14 vault sweep, and Mini sync resolution.

- [ ] **Step 6: Close the migration**

The evidence note must state each Definition of Done item as PASS or FAIL. Close only when every item is PASS. Otherwise keep the program open with the exact failed gate and evidence path.

---

## Plan Self-Review Checklist

- [ ] Every design workstream maps to at least one task.
- [ ] Gate 0 includes a fresh Mini inspection and credential rotation before code migration.
- [ ] Claude authority changes keep rollback files until production parity exists.
- [ ] History export happens before live Claude readers and files are removed.
- [ ] Job receipts use one writer per host and never a Syncthing-shared SQLite database.
- [ ] Operations Console is read-only and treats silence as stale.
- [ ] Core intake preserves captures without inline agent execution.
- [ ] Schedule migration is one job at a time with duplicate-trigger checks.
- [ ] Home Manager uses a separate scoped credential boundary.
- [ ] Final scanners distinguish historical references from live dependencies.
- [ ] OpenAI API credentials and Agents SDK are prohibited in production.
- [ ] Final decommission waits for Mini cutover and burn-in evidence.
- [ ] No step authorizes K2Bi trading or unrestricted home-device control.
