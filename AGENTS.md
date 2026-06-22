# K2B -- Codex Agent Onboarding Guide

This file is for Codex and other AI coding agents working in this repo. Read it before modifying files. If it conflicts with a direct user instruction, follow the user. If it conflicts with `CLAUDE.md`, treat this file as the Codex-facing entrypoint. `CLAUDE.md` is the Claude Code compatibility surface.

## Project Overview

K2B (Keith's Second Brain) is Keith's personal AI operating system. It is a single-user workflow machine, not a framework or library.

- MacBook project: `/Users/keithmbpm2/Projects/K2B`
- MacBook vault: `/Users/keithmbpm2/Projects/K2B-Vault`
- Mac Mini project: `/Users/fastshower/Projects/K2B`
- Mac Mini vault: `/Users/fastshower/Projects/K2B-Vault`

The Mac Mini runs the always-on services. The vault syncs through Syncthing. Code does not auto-sync; use `/sync` or `scripts/deploy-to-mini.sh`.

## Current Provider Model

- **Codex**: primary local coding agent and default desktop commander for K2B work.
- **Claude Code**: supported compatibility surface. `CLAUDE.md` and `.claude/skills` remain live, but they no longer define the main mental model.
- **Kimi K2.7 Code**: primary text worker for background analysis, compile, lint deep, research extraction, weave, observer, and non-Codex review. Historical `scripts/minimax-*.sh` names are preserved only for backward compatibility and route to Kimi by default through `K2B_LLM_PROVIDER=kimi`.
- **MiniMax**: subscription dead since 2026-05-27. Never set `K2B_LLM_PROVIDER=minimax`, and do not create new scripts or callers with `minimax-*` names. Treat existing `minimax-*` filenames as compatibility wrappers, not live MiniMax guidance.
- **GPTsAPI/Groq**: media, VLM/OCR, TTS, STT, and Telegram voice transcription paths.
- **NotebookLM/Gemini**: deep multi-source synthesis through the `notebooklm` skill.

Single source for provider routing: `K2B-Vault/wiki/context/context_llm-providers.md`.

## Directory Structure

```text
K2B/
  AGENTS.md                  Codex-facing instructions
  CLAUDE.md                  legacy Claude Code compatibility prompt
  .agents/skills/k2b-*/      Codex repo skills
  .claude/skills/k2b-*/      Claude Code repo skills
  .codex/hooks.json          Codex project hooks, versioned starting Ship 1 on 2026-06-14
  .claude/settings.json      Claude Code project settings
  k2b-remote/                Telegram bot, TypeScript, Node 20, grammy
  k2b-dashboard/             React + Vite + Express dashboard
  scripts/                   bash/python tools, hooks, provider wrappers
  tests/                     bash integration tests and fixtures
  plans/                     implementation plans and shipped specs
```

Keep `.agents/skills` in parity with `.claude/skills` during the migration. Ship 1 adds `scripts/verify-skills-parity.sh` as the guard. Exit `0` means the mirrored skill set, frontmatter, and Codex path/provider policy passed. Any non-zero exit blocks shipping until missing skills, orphan skills, frontmatter drift, stale Claude/Codex paths, or live MiniMax-worker wording are fixed.

## Build And Test Commands

### k2b-remote

```bash
cd k2b-remote
npm install
npm run build
npm run dev
npm start
npm run typecheck
npm test
```

### k2b-dashboard

```bash
cd k2b-dashboard
npm install
npm run dev
npm run build
npm start
npm run typecheck
```

### Python and Bash

```bash
source ~/Projects/K2B/venv/washing-machine/bin/activate
scripts/washing-machine/preflight.sh
pytest tests/loop/test_loop_lib.py
bash tests/deploy-to-mini.test.sh
bash tests/ship-detect-tier.test.sh
```

## Testing Conventions

- Vitest tests are co-located in `k2b-remote/src/*.test.ts`.
- Bash tests live under `tests/**/*.test.sh`.
- Python tests live under `tests/loop/` and selected script directories.
- Bash tests use `mktemp -d` and clean up with traps.
- Many tests redirect real paths through `K2B_*` environment variables.
- WMM tests use `K2B_WMM_SHELF` and `K2B_WMM_INDEX` for temporary stores.

## Coding Conventions

### TypeScript

- ES modules everywhere.
- Strict mode.
- `k2b-remote` uses NodeNext; import paths include `.js`.
- Prefer `node:` prefixes for built-ins.

### Bash

- Start scripts with `set -euo pipefail`.
- Resolve `REPO_ROOT` or `SCRIPT_DIR` from the script path.
- Use `K2B_*` environment overrides for tests.
- Shared files need one writer and locking where concurrent writes are possible.

### Python

- Python 3.12 target.
- `scripts/lib/` modules should remain importable.
- Washing-machine code expects the project venv, not system Python.

### Markdown and Vault

- Vault notes use YAML frontmatter with `tags`, `date`, and `type`.
- Internal links use `[[filename_without_extension]]`.
- Wiki pages use `up:` frontmatter.
- Review notes need `review-action:` and `review-notes:`.
- Raw captures under `raw/` are immutable after creation.

## Capture Stack

SJM blocks server-side integrations from corporate systems to K2B. Do not propose SJM Outlook calendar sync, email forwarding, or push integrations that require SJM IT approval.

Primary workday capture path is Telegram Desktop on Keith's SJM work computer. `k2b-remote` receives Telegram messages and routes them:

- Plain text: pass through to the agent and Washing Machine Memory.
- Photo/image: OCR through GPTsAPI VLM via `scripts/washing-machine/extract-attachment.sh`.
- PDF/document text: text extraction through `pdftotext` where supported.
- Voice memo: `k2b-remote/src/voice.ts` using Groq Whisper.

Unsupported Office binaries should be exported to PDF or copied as text first. Full reference: `K2B-Vault/wiki/context/context_capture-stack.md`.

## Key Architecture

### Commander and Worker

Codex owns orchestration by default in active desktop sessions. Claude Code can still own orchestration when Keith deliberately uses that lane. Kimi owns cheap worker analysis and non-OpenAI review through historical `minimax-*` wrapper scripts. The agent reads structured output and applies changes after validating against repo/vault state.

Do not inline new provider calls into random TypeScript unless the feature is explicitly provider infrastructure. Use existing script wrappers and routing modules.

### Single-Writer Hubs

- `wiki/log.md` -> `scripts/wiki-log-append.sh`
- Compile indexes -> `scripts/compile-index-update.py`
- `observer-candidates.md` -> `scripts/observer-loop.sh`
- `observer-defers.jsonl` -> `scripts/loop/loop-apply.sh` / `loop_apply.py`

### Memory Layer Ownership

Every fact has one canonical home:

- Soft behavior rules -> `CLAUDE.md`/`AGENTS.md` during migration
- Hard enforcement -> scripts, hooks, and tests
- Skill procedures -> matching `SKILL.md`
- Active preferences -> `K2B-Vault/System/memory/active_rules.md`
- Raw learnings -> `self_improve_learnings.md`
- Raw errors -> `self_improve_errors.md`
- Executable guards -> `policy-ledger.jsonl`

## Hooks

Codex hooks live in `.codex/hooks.json`, versioned starting Ship 1 on 2026-06-14. Older branches may not have this file. Hook commands must not depend on `CLAUDE_PROJECT_DIR` or Git metadata. Commands resolve through `K2B_PROJECT_ROOT`, defaulting to `$HOME/Projects/K2B` for MacBook/Mini parity. If hook behavior is in doubt, treat `.codex/hooks.json` as a first-class instruction surface alongside this file and the relevant skill.

Project hook scripts:

- `scripts/hooks/session-start.sh`: surfaces usage triggers, review queue, wiki index, loop dashboard, active rules, learnings, and pending sync entries.
- `scripts/hooks/post-tool-skill-track.sh`: tracks the active skill where the hook payload exposes one.
- `scripts/hooks/stop-observe.sh`: records vault changes for observer analysis.

## Deployment

Use `scripts/deploy-to-mini.sh`.

```bash
scripts/deploy-to-mini.sh
scripts/deploy-to-mini.sh skills
scripts/deploy-to-mini.sh code
scripts/deploy-to-mini.sh dashboard
scripts/deploy-to-mini.sh scripts
scripts/deploy-to-mini.sh all
```

The skills/docs category includes `AGENTS.md`, `CLAUDE.md`, `.agents/skills`, `.claude/skills`, `.codex/hooks.json`, `.mcp.json`, `README.md`, and `DEVLOG.md`.

The Mac Mini has no project `.git` history. Anything runtime-visible on the Mini must travel through sync or Syncthing.

## Review And Shipping

Every commit needs adversarial review.

- If Codex, OpenAI Agents, or OpenAI Responses built the diff, use Kimi review only: `scripts/review.sh ... --builder-family openai --primary kimi --no-fallback --wait`.
- If Kimi built the diff, use Codex only and do not fall back to Kimi: `scripts/review.sh ... --builder-family kimi --primary codex --no-fallback --wait`.
- If Claude Code built the diff, use `scripts/review.sh ... --builder-family anthropic --primary codex --wait`; fallback to Kimi is allowed because both reviewers are independent of Claude.
- If the builder is unknown, mixed, or not represented by the named families, choose one independent reviewer and use `--builder-family other --primary <codex|kimi> --no-fallback`; record why that reviewer is independent.
- Same-family fallback does not count as official independent review.

Use reviewer key `kimi` for the live Kimi K2.7 reviewer. The key `minimax` remains a deprecated compatibility alias only and should not appear in recommended commands. The review runner invokes `scripts/kimi-review.sh`; it no longer falls back to `scripts/minimax-review.sh`.
Omitting `--builder-family` is for ad-hoc reviews only, not official `/ship` gates.
`/ship` must not guess the builder. Codex desktop sessions must set `BUILDER_FAMILY=openai`; Claude, Kimi, and mixed-builder sessions must set their actual family before `/ship`.

`/ship` owns commit, push, feature-note lane changes, DEVLOG, `wiki/log.md`, and pending-sync mailbox behavior. If `/ship` is not available in this harness, follow the `k2b-ship` skill body manually and state any missing step clearly.

## Session Discipline

At the END of every K2B desktop session, before closing, run **`/ship`**. Codex does not have Claude Code's slash-command runtime in every harness, so this means invoking `k2b-ship` and executing its manual fallback end to end: review, commit, push, DEVLOG, `wiki/log.md`, Step 14 vault sweep, and sync-now/defer resolution. Keep this section in sync with the `Codex Desktop manual ship contract` in `k2b-ship`.

When Keith has already used delivery command wording -- for example "ship it", "commit this", "push this", "sync this to the Mini", "deploy this", "merge this", "do all the commit/etc.", "make sure this is shipped", or "implement X, then commit/push/sync it" -- treat that as authorization to complete the ship path without asking a second "should I ship?" question. Plain implementation requests without delivery command wording are not ship authorization. Still surface reviewer findings, commit message, test results, sync result, and any explicit override in the final report.

It is never allowed to end with a bare reminder like "run `/ship`" or "run `/sync`" after Codex modified K2B project files. Either the work is shipped and synced now, or a durable `.pending-sync/` entry exists for later.

If shipping is blocked by review findings, missing credentials, network failure, a rejected push, or an explicit Keith stop, do not loop and do not pretend the ship finished. Surface the blocking condition, preserve the work in the repo, and resolve the deploy obligation explicitly: fix-and-retry when the blocker is actionable now, or write the normal `.pending-sync/` entry only after a commit has landed but Mini deployment must be deferred.

If Keith explicitly declines to ship or says to leave the work uncommitted, treat that as an intentional checkpoint, not a failed ship. Report the uncommitted files and verification state, do not create a `.pending-sync/` entry because no commit exists, and do not claim the work is shipped.

## Goal Mode

Use Codex `/goal` only for bounded missions with a real stop condition. For serious K2B or K2Bi runs, start from `plans/templates/goal-card.md` or include the same fields inline:

- objective
- repo
- owner
- allowed workers
- scope boundaries
- binary done checks
- verification commands
- review gate
- stop-if conditions

Do not treat `/goal` as vague "keep working" mode. If the done checks or stop-if conditions cannot be written clearly, do not start the goal yet.

## Ship Briefs

When Keith asks what changed, what shipped, whether he is affected, or whether Claude Code still works, answer from his operating experience first. Do not lead with file counts, commit stats, or implementation internals unless he explicitly asks for codebase detail.

Use this order:

1. **What you will notice** -- day-to-day behavior change.
2. **What stays the same** -- especially Claude Code and Telegram compatibility.
3. **What is not included yet** -- avoid overclaiming later ships.
4. **What to do now** -- switch tools, restart, test, sync, or do nothing.
5. **Under the hood** -- commits, files, hooks, tests, provider names.
6. **Risk / rollback** -- one short sentence when relevant.

Prefer before/after examples and "this means / this does not mean" wording. Keith has explicitly asked for the meaning behind shipped changes, not only artifact lists.

## Safety

- Never commit secrets. Do not print `.env` values.
- Gmail send requires a draft ID tied to a body preview Keith has seen. Bare "send", "ok", "yes", or "go" never authorizes send.
- Do not delete email.
- Do not edit raw captures after creation.
- Do not make K2Bi live trading, broker, or production automation changes from K2B unless Keith explicitly opens that scope.
- Router VPN is current as of 2026-05-02. `HTTP_PROXY` and `HTTPS_PROXY` default empty unless a local proxy returns.
- During the migration, safety-critical rules in this section must stay mirrored with the corresponding safety rules in `CLAUDE.md`; automated top-level prompt parity is later-ship scope.

## When In Doubt

1. Read this file for Codex-facing repo instructions.
2. Read `CLAUDE.md` for legacy K2B operating detail that has not yet been ported.
3. Read the relevant `.agents/skills/k2b-*/SKILL.md`.
4. Check `.codex/hooks.json` when hook/session behavior is involved.
5. Check `DEVLOG.md` and the active plan under `plans/`.
6. Run the focused tests before broad test suites.
