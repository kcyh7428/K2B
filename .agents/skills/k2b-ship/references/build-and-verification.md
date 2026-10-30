# Build, tests and repository layout

Mandatory reading before building, running project test suites, or claiming the
repository layout and clean-slate removal state. This runbook carries the authority of
`AGENTS.md`; if it is missing or unreadable, stop the affected operation.

## Repository map

```text
K2B/
  AGENTS.md                  live Codex instructions
  .agents/skills/k2b-*/      live K2B skills
  .codex/hooks.json          live Codex project hooks
  k2b-dashboard/             optional manual intake/status UI
  scripts/                   capture, provider, review, and maintenance tools
  tests/                     shell, Python, and TypeScript tests
  plans/                     implementation plans and acceptance records
```

The Stage 1 clean-slate change removes tracked `CLAUDE.md`, `.claude/`, `k2b-remote/`, and Telegram/Claude-only scripts after archive and inventory verification. Personal application history and unrelated app state are retained; neither defines current behavior.

## Build and tests

### Dashboard

```bash
cd k2b-dashboard
npm ci
npm run typecheck
npm run build
```

### Capture and authority

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/test_eod_capture.py tests/test_eod_capture_status.py
bash tests/codex-discovery-job.test.sh
bash tests/eod-capture-cron.test.sh
bash tests/codex-hooks.test.sh
bash tests/session-start-hook.test.sh
bash tests/verify-codex-authority.test.sh
scripts/verify-codex-authority.sh
python3 -m pytest tests/test_vault_note_write.py -q
bash tests/vault-note-authority.test.sh
```

Run focused tests before broader suites. Shell tests use `mktemp -d`, clean up through traps, and redirect real paths through `K2B_*` overrides. Python 3.12 is the target. TypeScript is strict and uses ES modules.

Use `apply_patch` for hand edits. Bash scripts start with `set -euo pipefail`, resolve paths from the script, and lock shared writers. Raw vault captures are immutable after creation.
