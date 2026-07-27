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

- **Codex**: sole live instruction authority, primary local coding agent, and default desktop commander for K2B work.
- **Claude Code**: rollback-only compatibility state. `CLAUDE.md` and `.claude/` stay unchanged until the authorized removal task, but live Codex sessions, skills, and hooks must not read them.
- **Kimi K2.7 Code**: primary text worker for background analysis, compile, lint deep, research extraction, weave, observer, and independent review of OpenAI-built diffs. Historical `scripts/minimax-*.sh` names are compatibility wrappers that route to Kimi through `K2B_LLM_PROVIDER=kimi`.
- **GPTsAPI/Groq**: default image generation, VLM/OCR, TTS, STT, and retained voice-transcription paths.
- **Higgsfield**: premium image and image-edit path, and the active video and music provider. Use `scripts/higgsfield.sh`; authentication is per-machine OAuth.
- **MiniMax**: disabled in current K2B routing. Never select it as a provider, create new `minimax-*` callers, or use it as a media fallback. Existing names are compatibility wrappers only.
- **NotebookLM/Gemini**: deep multi-source synthesis through the `notebooklm` skill.

Single source for provider routing: `K2B-Vault/wiki/context/context_llm-providers.md`.

## Directory Structure

```text
K2B/
  AGENTS.md                  Codex-facing instructions
  CLAUDE.md                  rollback-only Claude compatibility prompt
  .agents/skills/k2b-*/      sole live K2B skill root
  .claude/                   rollback-only Claude compatibility state
  .codex/hooks.json          Codex project hooks
  k2b-remote/                Telegram bot, TypeScript, Node 20, grammy
  k2b-dashboard/             React + Vite + Express dashboard
  scripts/                   bash/python tools, hooks, provider wrappers
  tests/                     bash integration tests and fixtures
  plans/                     implementation plans and shipped specs
```

`.agents/skills` is the only live K2B skill root. Run `scripts/verify-codex-authority.sh` as the authority guard. Exit `0` means active instructions, skills, hook registration, and hook scripts do not depend on Claude state or retired routing. Any non-zero exit blocks delivery.

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

Dashboard intake and vault drop are the live Codex capture routes:

- Plain text: pass through to Codex and Washing Machine Memory.
- Photo/image: OCR through GPTsAPI VLM via `scripts/washing-machine/extract-attachment.sh`.
- PDF/document text: text extraction through `pdftotext` where supported.
- Voice memo: transcription through the retained Groq Whisper path.

Unsupported Office binaries should be exported to PDF or copied as text first. Full reference: `K2B-Vault/wiki/context/context_capture-stack.md`.

Telegram is retired as a required K2B input, output, reminder, or alert channel. Any remaining `k2b-remote` or Telegram implementation is dormant retirement residue awaiting a later authorized removal task. It is not a live Codex intake, scheduler, delivery, reminder, or alert route.

## Key Architecture

### Commander and Worker

Codex is the sole live desktop orchestrator. Kimi owns cheap worker analysis and non-OpenAI review through historical `minimax-*` wrapper scripts. Claude files are rollback-only residue pending their separately authorized removal and do not define an active orchestration lane. The agent reads structured output and applies changes after validating against repo/vault state.

Do not inline new provider calls into random TypeScript unless the feature is explicitly provider infrastructure. Use existing script wrappers and routing modules.

### Single-Writer Hubs

- `wiki/log.md` -> `scripts/wiki-log-append.sh`
- Compile indexes -> `scripts/compile-index-update.py`
- `observer-candidates.md` -> `scripts/observer-loop.sh`
- `observer-defers.jsonl` -> `scripts/loop/loop-apply.sh` / `loop_apply.py`

### Memory Layer Ownership

Every fact has one canonical home:

- Soft behavior rules -> `AGENTS.md`
- Hard enforcement -> scripts, hooks, and tests
- Skill procedures -> matching `SKILL.md`
- Active preferences -> `K2B-Vault/System/memory/active_rules.md`
- Raw learnings -> `self_improve_learnings.md`
- Raw errors -> `self_improve_errors.md`
- Executable guards -> `policy-ledger.jsonl`

## Hooks

Codex hooks live in `.codex/hooks.json`. Hook commands must not depend on legacy Claude project variables, Claude state, or Git metadata. Commands resolve through `K2B_PROJECT_ROOT`, defaulting to `$HOME/Projects/K2B` for MacBook/Mini parity. If hook behavior is in doubt, treat `.codex/hooks.json` as a first-class instruction surface alongside this file and the relevant skill.

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

The live skills/docs category includes `AGENTS.md`, `.agents/skills`, `.codex/hooks.json`, `.mcp.json`, `README.md`, and `DEVLOG.md`. Rollback-only Claude files are preserved locally until their authorized removal task and do not define live behavior.

The Mac Mini has no project `.git` history. Anything runtime-visible on the Mini must travel through sync or Syncthing.

## Review And Shipping

Every commit needs adversarial review.

- If Codex, OpenAI Agents, or OpenAI Responses built the diff, use Kimi review only: `scripts/review.sh ... --builder-family openai --primary kimi --no-fallback --wait`.
- If Kimi built the diff, use Codex only and do not fall back to Kimi: `scripts/review.sh ... --builder-family kimi --primary codex --no-fallback --wait`.
- For a pre-existing diff whose historical metadata records an Anthropic builder, use `scripts/review.sh ... --builder-family anthropic --primary codex --wait`; fallback to Kimi remains an independent historical review route.
- If the builder is unknown, mixed, or not represented by the named families, choose one independent reviewer and use `--builder-family other --primary <codex|kimi> --no-fallback`; record why that reviewer is independent.
- Same-family fallback does not count as official independent review.

Use reviewer key `kimi` for the live Kimi K2.7 reviewer. The key `minimax` remains a deprecated compatibility alias only and should not appear in recommended commands. The review runner invokes `scripts/kimi-review.sh`; it no longer falls back to `scripts/minimax-review.sh`.
Omitting `--builder-family` is for ad-hoc reviews only, not official `/ship` gates.
`/ship` must not guess the builder. Live Codex desktop sessions must set `BUILDER_FAMILY=openai`; Kimi and mixed-builder sessions must set their actual family before `/ship`. Pre-existing work may retain its recorded historical builder family.

`/ship` owns commit, push, feature-note lane changes, DEVLOG, `wiki/log.md`, and pending-sync mailbox behavior. If `/ship` is not available in this harness, follow the `k2b-ship` skill body manually and state any missing step clearly.

## Session Discipline

The global `shipping-efficiently` skill owns plan eligibility, model routing, implementation, drift control, verification economy, and the bounded review/fix path. `k2b-ship` is the K2B delivery adapter only.

At the end of every K2B desktop session that modified files, resolve the delivery state. When delivery is authorized, run **`/ship`**. Codex does not have a slash-command runtime in every harness, so this means invoking `k2b-ship` and executing its manual fallback end to end: review, commit, push, DEVLOG, `wiki/log.md`, Step 14 vault sweep, and sync-now/defer resolution. Keep this section in sync with the `Codex Desktop manual ship contract` in `k2b-ship`.

When Keith has already used delivery command wording -- for example "ship it", "commit this", "push this", "sync this to the Mini", "deploy this", "merge this", "do all the commit/etc.", "make sure this is shipped", or "implement X, then commit/push/sync it" -- treat that as authorization to complete the ship path without asking a second "should I ship?" question. Plain implementation requests without delivery command wording are not ship authorization. Still surface reviewer findings, commit message, test results, sync result, and any explicit override in the final report.

Do not end with a bare reminder like "run `/ship`" or "run `/sync`" after Codex modified K2B project files. If delivery is authorized, ship and sync now. If committed work cannot reach the Mini, create the normal durable `.pending-sync/` entry. If delivery was not authorized or Keith requests an uncommitted checkpoint, report the modified files and verification state without creating a pending-sync entry.

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

When Keith asks what changed, what shipped, whether he is affected, or what happened to former Claude or Telegram paths, answer from his operating experience first. Do not lead with file counts, commit stats, or implementation internals unless he explicitly asks for codebase detail.

Use this order:

1. **What you will notice** -- day-to-day behavior change.
2. **What stays the same** -- especially Mini sync, retained providers, and rollback options; describe Claude state as rollback-only residue and Telegram code as retired dormant residue.
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
- Do not assume proxy or VPN state from documentation. Verify the live network path before setting proxy variables or diagnosing connectivity.
- Rollback-only Claude files do not define live safety behavior. Changes to those files belong to their separately authorized compatibility or removal task.

## When In Doubt

1. Read this file for Codex-facing repo instructions.
2. Do not import instructions from rollback-only Claude state into a live Codex session.
3. Read the relevant `.agents/skills/k2b-*/SKILL.md`.
4. Check `.codex/hooks.json` when hook/session behavior is involved.
5. Check `DEVLOG.md` and the active plan under `plans/`.
6. Run the focused tests before broad test suites.
