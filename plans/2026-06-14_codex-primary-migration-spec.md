# Codex Primary Migration Spec

Status: draft
Date: 2026-06-14
Owner: Keith
Builder: Codex session after Keith approval
Review gate: Kimi-backed review for Codex-built diffs; no same-family self-review
Plan review: Kimi NEEDS-ATTENTION, `.code-reviews/2026-06-14T08-28-48Z_3fafce.log`, material findings folded below

## Goal

Move K2B away from Claude as the primary operating medium without breaking the behavior that makes K2B useful today:

1. Codex local sessions must load the same K2B operating context that Claude Code sessions currently get.
2. Codex must become the default desktop commander without weakening `/ship`, `/goal`, review, sync, memory, or pending-state discipline.
3. `k2b-remote` must stop depending on `@anthropic-ai/claude-agent-sdk` and gain an OpenAI-native provider path for Telegram.

This is not a one-shot SDK swap. K2B has Claude coupling in instructions, hooks, skill topology, deployment, review routing, and Telegram session state. The migration ships by plane, with a rollback point after each plane.

## Current Evidence From Repo Scan

The live repo scan on 2026-06-14 found these load-bearing surfaces:

- `AGENTS.md` exists but is stale: it still names Claude Code as commander, MiniMax M2.7 as worker, and Anthropic Agent SDK as the remote bridge.
- `AGENTS.md`, `.agents/`, and `.codex/` started as gitignored local-only Codex surfaces. Ship 1 changes that policy for stable surfaces: version `AGENTS.md`, `.agents/skills/**`, and `.codex/hooks.json`; keep `.codex` runtime jobs/archives local-only.
- `CLAUDE.md` is still the real operating prompt and explicitly says K2B runs via Claude Code.
- `.agents/skills` exists, but it is not in parity with `.claude/skills`; several current skills are only under `.claude/skills`, and some `.agents` skill text has incorrect Codex paths.
- Local `.codex/hooks.json` existed in the working copy before Ship 1, but it was not versioned. Ship 1 adds `.codex/hooks.json` and `.codex/.gitkeep` to git while keeping runtime `.codex` jobs/archives ignored. The local hook file also used `"$CLAUDE_PROJECT_DIR"/...`; with that env missing, hooks resolved to `/scripts/hooks/...` and failed.
- `scripts/deploy-to-mini.sh` syncs `CLAUDE.md` and `.claude/skills`, but not `AGENTS.md`, `.agents/skills`, or `.codex/hooks.json`.
- `scripts/audit-ownership.sh` and `scripts/lib/tier_detection.py` watch Claude-era instruction paths but do not fully account for Codex-era paths.
- `k2b-remote/src/agent.ts` imports `query` from `@anthropic-ai/claude-agent-sdk`, reads `CLAUDE.md` plus `k2b-remote/CLAUDE.md`, uses `systemPrompt: { preset: "claude_code" }`, and persists Claude session IDs.
- `k2b-remote/src/bot.ts`, `scheduler.ts`, and `intake.ts` all call `runAgent`, so the provider change has one central seam but multiple behavioral consumers.
- `k2b-remote/src/db.ts` stores only one opaque `session_id` per chat. Provider switch needs a session migration or provider-scoped session key.
- `k2b-remote/ecosystem.config.cjs` still exports `CLAUDE_PROJECT_ROOT`; `config.ts` still prefers `CLAUDE_PROJECT_ROOT` over a provider-neutral env var.

## Current Evidence From Vault Scan

The vault scan matters because Telegram is not optional:

- `wiki/context/context_capture-stack.md` says SJM blocks server-side calendar/email/push integrations, and Telegram Desktop on the work computer is the primary workday capture path.
- `wiki/context/context_llm-providers.md` says MiniMax text/media is dead or removed; text worker calls now route to Kimi K2.6 through historically named `minimax-*` scripts. Media runs through GPTsAPI/Groq.
- `System/memory/project_parked_items.md` parked `feature_pipeline-hardening` only because Telegram was not the primary interface. Moving primary operation toward Telegram/OpenAI reactivates that trigger.
- `System/memory/project_mac_mini_clash_required.md` supersedes older Clash assumptions: the Mac Mini and MacBook now sit behind router VPN, so `HTTP_PROXY`/`HTTPS_PROXY` should default empty unless a local proxy returns.
- `System/memory/feedback_codex_rescue_stalls.md` says official `/ship` review should use `scripts/review.sh`, not hidden Codex rescue agents.
- Shipped Telegram features already rely on pre-agent processing: Washing Machine Memory, vault-notes fallback, YouTube URL transcript prefetch, voice transcription, attachment ingest, and outbox manifests. The new provider must preserve that pre-agent pipeline.

## Current OpenAI Surface Check

Checked current official OpenAI surfaces on 2026-06-14:

- OpenAI Agents SDK for TypeScript is the closest replacement for Anthropic Agent SDK in `k2b-remote`: <https://openai.github.io/openai-agents-js/>
- Agents SDK sessions can use `OpenAIConversationsSession`, `MemorySession`, or a custom session backend; this maps to K2B's need for provider-scoped Telegram sessions: <https://openai.github.io/openai-agents-js/guides/sessions/>
- Agents SDK tools, approvals, and streaming cover the shapes K2B needs for safe local actions and Telegram typing/progress behavior: <https://openai.github.io/openai-agents-js/guides/tools/> and <https://openai.github.io/openai-agents-js/guides/streaming/>
- Codex SDK is appropriate as a coding worker/tool, not as the whole Telegram conversation brain: <https://developers.openai.com/codex/sdk>
- Responses/Conversations APIs are the lower-level fallback if K2B later needs direct control over state, tools, or storage beyond the Agents SDK abstraction.

Decision: use OpenAI Agents SDK for the Telegram agent provider, and expose Codex as a tool only for bounded repo/code jobs.

## Non-Goals

- Do not delete Claude compatibility until Codex local sessions and Telegram OpenAI provider have burned in.
- Do not bypass email send safety. Gmail send still requires a draft ID tied to a body preview Keith has seen.
- Do not move K2Bi live code, broker, trading, or production automation from this K2B migration.
- Do not introduce SJM calendar/email/server push integrations; the vault says those paths are blocked.
- Do not replace Kimi/GPTsAPI/Groq worker routing as part of this migration.
- Do not commit secrets or print `.env` values.

## Ship Sequence

There are three migration items, but item 3 is split into provider-bridge and cutover ships. That keeps Telegram rollback clean.

### Ship 1: Codex Local Parity

Purpose: make a fresh Codex session in this repo see the correct K2B world without depending on Claude-specific paths.

Scope:

- Add Codex-era instruction surfaces to the safety perimeter before editing them:
  - `AGENTS.md`
  - `.agents/skills/**`
  - `.codex/hooks.json`
  - any Codex skill-sync helper scripts created by this ship.
- Version stable Codex instruction surfaces in Ship 1 so clean clones and Mac Mini syncs are deterministic. Keep only runtime `.codex` artifacts such as `job.md` and `archive/` local-only.
- Rewrite `AGENTS.md` from a stale Claude-era onboarding doc into the Codex-facing operating guide.
- Bring `.agents/skills` into parity with `.claude/skills`, including skills that exist only under `.claude/skills` today.
- Fix stale `.agents` skill references such as `.Codex/skills` and old MiniMax wording.
- Make `.codex/hooks.json` resolve through a provider-neutral repo root, not `CLAUDE_PROJECT_DIR`.
- Adjust `scripts/hooks/session-start.sh` so active rules/learnings load from canonical K2B vault memory when possible, not only `~/.claude/projects`.
- Adjust `scripts/hooks/post-tool-skill-track.sh` to tolerate Codex hook payload shape, while preserving Claude compatibility.
- Keep `scripts/hooks/stop-observe.sh` path-safe and provider-neutral enough for Codex local runs.
- Update `scripts/deploy-to-mini.sh` and `tests/deploy-to-mini.test.sh` so `AGENTS.md`, `.agents/skills`, and `.codex/hooks.json` travel in the skills/docs sync category.
- Update ownership audit and tier detection to include Codex instruction surfaces.

Files likely touched:

- `AGENTS.md`
- `.gitignore`
- `.agents/skills/**`
- `.codex/.gitkeep`
- `.codex/hooks.json`
- `scripts/hooks/session-start.sh`
- `scripts/hooks/post-tool-skill-track.sh`
- `scripts/hooks/stop-observe.sh`
- `scripts/deploy-to-mini.sh`
- `tests/deploy-to-mini.test.sh`
- `scripts/audit-ownership.sh`
- `scripts/lib/tier_detection.py`
- `tests/ship-detect-tier.test.sh`
- `scripts/tier3-paths.yml`
- `scripts/verify-skills-parity.sh`
- `tests/verify-skills-parity.test.sh`
- `tests/codex-hooks.test.sh`
- `tests/post-tool-skill-track.test.sh`
- `tests/session-start-hook.test.sh`

Verification:

- New parity verifier:
  - every `.claude/skills/k2b-*/SKILL.md` has a corresponding `.agents/skills/k2b-*/SKILL.md`
  - frontmatter `name` and trigger descriptions are either identical or intentionally mapped in an allowlist
  - no `.agents` skill contains stale `.Codex/skills` paths
  - no `.agents` skill describes MiniMax M2.7 as the live text worker without the Kimi routing correction
  - all missing-skill and stale-reference cases fail the test on a fixture.
- `env -u CLAUDE_PROJECT_DIR bash scripts/hooks/session-start.sh`
- A fixture test that parses `.codex/hooks.json`, extracts all command strings, and proves they do not contain `CLAUDE_PROJECT_DIR` and do resolve to an executable path when `K2B_PROJECT_ROOT` is set.
- `bash tests/deploy-to-mini.test.sh`
- `bash tests/ship-detect-tier.test.sh`
- `bash tests/verify-skills-parity.test.sh`
- `bash tests/codex-hooks.test.sh`
- `bash tests/post-tool-skill-track.test.sh`
- `bash tests/session-start-hook.test.sh`
- `scripts/audit-ownership.sh` or documented accepted drift if the audit intentionally flags docs still being migrated.

Done:

- Fresh Codex K2B session loads current K2B instructions, skills, and session dashboard without hook path failure.
- `scripts/verify-skills-parity.sh` returns exit 0 on the real repo and fails on fixtures that remove a skill, stale a path, or reintroduce dead MiniMax-worker wording.
- Mac Mini sync can carry the Codex instruction surfaces.
- Claude compatibility remains intact.

Rollback:

- Revert this ship. Claude-era surfaces continue to work because `CLAUDE.md` and `.claude/skills` are preserved.

### Ship 2: Codex Commander And Review Matrix

Purpose: make Codex the default desktop commander while preserving K2B's ship safety model.

Scope:

- Update `CLAUDE.md` and `AGENTS.md` relationship so Codex is primary but Claude remains a compatibility surface.
- Remove or rewrite misleading commander claims: "Opus via Claude Code is commander" should become provider-aware.
- Document the current provider matrix in the top-level instruction surface: Codex/OpenAI primary local agent, Kimi text worker, GPTsAPI/Groq media/voice, NotebookLM deep-research worker.
- Update `k2b-ship` skill in both active skill trees or establish one canonical skill source and sync rule.
- Change review routing rule:
  - Add explicit builder-family metadata to the review flow. Minimal acceptable shape is an explicit `--builder-family openai|anthropic|kimi|other` flag or an equivalent ship manifest consumed by `scripts/review.sh`.
  - If builder family is `openai` (Codex, OpenAI Agents, OpenAI Responses), official review must be Kimi-backed through `scripts/review.sh --primary minimax --no-fallback` or `scripts/minimax-review.sh`.
  - If builder family is `kimi`, Codex can review only if it is not reviewing OpenAI-generated edits.
  - If builder family is `anthropic`, Codex can review while Claude compatibility exists.
  - Never allow same-family fallback to count as official review.
- Add a Codex `/goal` Goal Card template to the instruction surface:
  - objective
  - repo
  - owner
  - allowed workers
  - scope boundaries
  - binary done checks
  - verification commands
  - review gate
  - stop-if conditions
- Update docs and skill wording that says every session ends with "Claude Code" rather than K2B session or Codex primary session.

Files likely touched:

- `AGENTS.md`
- `CLAUDE.md`
- `.agents/skills/k2b-ship/SKILL.md`
- `.claude/skills/k2b-ship/SKILL.md`
- possibly `.agents/skills/k2b-orchestrator/SKILL.md`
- possibly `.claude/skills/k2b-orchestrator/SKILL.md`
- `scripts/review.sh`
- `scripts/lib/review_runner.py`
- `tests/review-runner.test.sh`
- `plans/templates/goal-card.md` or equivalent, if we choose a template file instead of embedding in `AGENTS.md`

Verification:

- Review runner test proving `--builder-family openai --primary codex` is rejected.
- Review runner test proving `--builder-family openai --primary minimax --no-fallback` is accepted.
- Review runner test proving Codex-built diffs route to Kimi/no-Codex fallback.
- `bash tests/review-runner.test.sh`
- `bash tests/ship-detect-tier.test.sh`
- Manual dry-run of the `/ship` instructions against a doc-only diff.
- Kimi review of this ship's diff before commit.

Done:

- Codex can be the local K2B commander without self-reviewing its own commits.
- Goal mode has a bounded stop condition instead of vague autonomy.
- Claude remains usable as a fallback lane but no longer owns the mental model.

Rollback:

- Revert docs/skill/review changes. Ship 1 parity still helps Codex, but Claude can remain primary.

### Ship 3: Telegram Agent Provider Bridge

Purpose: introduce a provider-neutral `runAgent` boundary before changing production behavior.

Scope:

- Split `k2b-remote/src/agent.ts` into a provider interface and provider implementations.
- Keep the existing Anthropic provider as the default for this ship.
- Add a fake/test provider for deterministic Vitest coverage.
- Add an OpenAI provider skeleton behind `K2B_REMOTE_AGENT_PROVIDER=openai`, but do not make it production default yet.
- Create `k2b-remote/AGENTS.md` as the provider-neutral Telegram channel context. It must carry the scheduling, outbox, formatting, and Mac Mini identity rules currently locked in `k2b-remote/CLAUDE.md`.
- Keep `k2b-remote/CLAUDE.md` as compatibility input for the Claude provider until cutover.
- Add an instruction loader with provider-aware behavior:
  - OpenAI provider reads root `AGENTS.md` plus `k2b-remote/AGENTS.md`.
  - Claude provider reads root `CLAUDE.md` plus `k2b-remote/CLAUDE.md`.
  - Tests prove the OpenAI provider does not depend on `CLAUDE.md`.
- Replace `CLAUDE_PROJECT_ROOT` preference with `K2B_PROJECT_ROOT`, while keeping `CLAUDE_PROJECT_ROOT` as a deprecated fallback.
- Add provider-scoped session storage:
  - add `provider TEXT NOT NULL DEFAULT 'claude'` to `sessions`
  - migrate the primary key to `(chat_id, provider)` or rebuild the table if SQLite requires it
  - expose `getSession(chatId, provider)`, `setSession(chatId, provider, sessionId)`, `clearSession(chatId, provider)`, and `clearSessionsByProvider(provider)`
  - keep existing rows as `provider='claude'`.
- On provider switch, do not pass Claude session IDs into OpenAI.
- Define an error taxonomy in the provider result:
  - `invalid_session`
  - `rate_limit`
  - `context_length`
  - `tool_failure`
  - `timeout`
  - `unknown`
- On `invalid_session`, clear only that provider's session for that chat and retry once without a session ID.
- Preserve all pre-agent pipeline behavior:
  - preference profile hash reset
  - Washing Machine normalization gate
  - memory shelf injection
  - vault-notes fallback
  - YouTube transcript prefetch
  - observation logging
  - Telegram outbox manifest scan
  - scheduler calls
  - intake calls

Provider interface target:

```ts
export interface AgentRunInput {
  message: string
  provider: AgentProviderName
  sessionId?: string
  onTyping?: () => void
}

export interface AgentRunResult {
  text: string | null
  newSessionId?: string
  hadError?: boolean
  errorType?: AgentErrorType
}

export interface AgentProvider {
  name: string
  run(input: AgentRunInput): Promise<AgentRunResult>
}
```

Files likely touched:

- `k2b-remote/package.json`
- `k2b-remote/package-lock.json`
- `k2b-remote/src/agent.ts`
- `k2b-remote/src/agentProvider.ts`
- `k2b-remote/src/claudeProvider.ts`
- `k2b-remote/src/openaiAgentsProvider.ts`
- `k2b-remote/src/config.ts`
- `k2b-remote/src/db.ts`
- `k2b-remote/src/bot.ts`
- `k2b-remote/src/scheduler.ts`
- `k2b-remote/src/intake.ts`
- new or updated Vitest files under `k2b-remote/src/*.test.ts`
- `k2b-remote/AGENTS.md`
- `k2b-remote/ecosystem.config.cjs`
- `k2b-remote/.env.example`, if present; otherwise document env names in `k2b-remote/README` or setup/status scripts

Verification:

- `cd k2b-remote && npm run typecheck`
- `cd k2b-remote && npm test`
- Unit test that fake provider receives exactly the full message after memory/vault/YouTube pre-processing.
- Unit test that provider switch clears or isolates old sessions.
- DB migration test proving existing `sessions` rows become `provider='claude'` and OpenAI sessions do not overwrite them.
- Unit test that `invalid_session` clears only the active provider's session and retries once.
- Scheduler test proving scheduled tasks still call the provider boundary.
- Scheduler test proving session-less scheduled prompts work through both fake and OpenAI providers.
- Intake test proving dashboard intake still calls the provider boundary.
- Config test proving `K2B_PROJECT_ROOT` wins over `CLAUDE_PROJECT_ROOT`, and use of `CLAUDE_PROJECT_ROOT` emits a deprecation warning.

Done:

- Production default remains safe.
- OpenAI provider can be selected in staging without changing Telegram pipeline behavior.
- Session incompatibility is handled explicitly.

Rollback:

- Set `K2B_REMOTE_AGENT_PROVIDER=claude`.
- If needed, revert this ship; the old Anthropic path remains structurally present.

### Ship 4: OpenAI Telegram Cutover And Hardening

Purpose: make OpenAI Agents SDK the production Telegram agent and retire Claude SDK only after burn-in.

Scope:

- Complete the OpenAI Agents SDK provider:
  - instructions from `AGENTS.md` and `k2b-remote/AGENTS.md`
  - model/env config
  - session persistence
  - timeout/error handling
  - full-response collection for the first cut; do not introduce incremental Telegram message edits in the MVP
  - timeout-aware typing behavior using the existing typing refresh interval
  - tool policy for safe local K2B operations
- Expose Codex as a bounded coding tool only if needed:
  - repo/code tasks
  - explicit cwd and scope
  - no auto-send, no auto-publish, no K2Bi live trading changes
- Add a production canary mode:
  - OpenAI provider for Keith DM only, or
  - OpenAI provider for one command/test chat before full bot default.
- Add a Telegram canary kill switch before canary starts:
  - `/abort-canary` clears the current chat's OpenAI session and flips that chat back to the Claude provider or the configured fallback without SSH.
  - A CLI fallback also exists: `node dist/session-cli.js clear --provider openai [--chat <id>]`.
- Update PM2 env on Mac Mini:
  - `K2B_REMOTE_AGENT_PROVIDER=openai`
  - `K2B_PROJECT_ROOT`
  - `OPENAI_API_KEY`
  - keep router VPN proxy defaults empty unless local proxy returns.
- Update startup/status scripts that currently check for Claude CLI.
- Update Telegram `/start` wording from Claude Code to K2B/OpenAI/Codex.
- Revisit `feature_pipeline-hardening`, because its parked trigger is now active:
  - FIFO message queue
  - exfil guard
  - cost/token footer
  - provider trace in logs
- Remove `@anthropic-ai/claude-agent-sdk` only after burn-in.

Verification:

- `cd k2b-remote && npm run typecheck`
- `cd k2b-remote && npm test`
- `/sync code` or explicit `scripts/deploy-to-mini.sh code` after approval.
- Mac Mini:
  - `pm2 restart k2b-remote --update-env`
  - `pm2 logs k2b-remote --lines 100 --nostream`
  - `pm2 jlist` shows online.
- Telegram smoke set:
  - `/start`
  - `/newchat`
  - simple text answer
  - memory retrieval question
  - YouTube URL summary
  - long answer over 10 seconds still refreshes typing and returns one clean final Telegram response
  - voice memo transcription
  - photo/document ingest happy path
  - `/media image` or at least media command rejection path
  - one scheduled task dry-run or short one-time reminder

Burn-in:

- Keep Claude provider available for at least 48-72 hours.
- Log provider name, model, session ID shape, duration, and error state for each run.
- After burn-in, remove Anthropic dependency and stale Claude CLI setup/status checks in a final cleanup commit.

Rollback:

- Set `K2B_REMOTE_AGENT_PROVIDER=claude`.
- Restart pm2 with `--update-env`.
- If OpenAI provider corrupts sessions, run the provider-scoped session clear path from Ship 3 before switching back; do not delete Claude historical sessions unless explicitly chosen.

## Kimi Plan Review Disposition

Initial Kimi review command:

```bash
scripts/review.sh plan --plan plans/2026-06-14_codex-primary-migration-spec.md --focus "Codex primary migration plan: missing K2B coupling, unsafe cutover, wrong ship boundaries" --primary minimax --no-fallback --wait
```

Result:

- Job: `2026-06-14T08-28-48Z_3fafce`
- Log: `.code-reviews/2026-06-14T08-28-48Z_3fafce.log`
- Verdict: NEEDS-ATTENTION

Material accepted changes folded into this revision:

- Ship 1 now has a binary skill-parity verifier.
- Ship 1 safety perimeter now explicitly adds `AGENTS.md`, `.agents/skills/**`, and `.codex/hooks.json` before edits.
- Ship 2 now requires builder-family metadata and reviewer-family enforcement.
- Ship 3 now chooses the provider-column DB migration instead of leaving column-vs-prefix open.
- Ship 3 now defines provider-aware instruction loading and error taxonomy.
- Ship 3 now requires invalid-session clearing and provider-specific retry behavior.
- Ship 4 now deliberately defers streaming edits and verifies long-response typing.
- Ship 4 now requires `/abort-canary` and provider-scoped session clearing.

Rejected or narrowed review points:

- The review called out `.codex/hooks.json` hardcoding as if the plan missed it. The original draft already included that fix, but the revised spec adds the stronger JSON fixture test.
- The review suggested implementing provider files before accepting the plan. This is implementation work, not plan work; the accepted correction is to make Ship 3's first commit the provider interface plus fake provider and tests.

## Cross-Ship Invariants

- Telegram remains reliable because it is the primary workday capture path.
- All shared-file writes keep their single-writer discipline.
- Raw captures stay immutable.
- Vault context injected into prompts is untrusted data and must stay fenced.
- Media stays on GPTsAPI/Groq unless a separate provider-routing feature says otherwise.
- Kimi remains the official non-Codex review path for Codex-built diffs.
- `/schedule` continues to use the persistent SQLite scheduler, never agent-session scheduled tasks.
- Mac Mini has no repo git history; sync must carry every runtime file the bot needs.
- No `.env` values or secrets appear in logs, docs, test fixtures, or final reports.

## Suggested Execution Order

1. Run Ship 1 first. It is foundational and low runtime risk.
2. Use Kimi review after Ship 1 because Codex will likely build it.
3. Run Ship 2 only after a fresh Codex session proves the hook/instruction parity is real.
4. Run Ship 3 with Claude provider still default. This lowers risk by creating the provider seam before using it.
5. Run Ship 4 as a canary and burn-in, not as an immediate dependency removal.

## Immediate Next Gate

The first Kimi plan review has already run and material findings are folded above. Before implementation:

```bash
scripts/review.sh plan --plan plans/2026-06-14_codex-primary-migration-spec.md --focus "Revised Codex primary migration plan after initial Kimi findings: remaining missing coupling or unsafe ship boundaries" --primary minimax --no-fallback --wait
```

Run this second pass only if Keith wants another external check before Ship 1. Otherwise Keith approval of this draft is the gate to start Ship 1.
