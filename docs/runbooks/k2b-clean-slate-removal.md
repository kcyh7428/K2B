# K2B clean-slate removal runbook

This runbook covers rollback evidence creation and retired-runtime inventory for the K2B clean-slate removal (Task 3A/3B) on the home and SJM Macs.

## Scope

- Verify immutable, redacted Claude-history archives before removing tracked K2B runtime paths.
- Inventory retired PM2, launchd, cron, MCP, and state artifacts.
- Do not record secret values, credentials, cookies, OAuth material, or raw logs.
- Do not contact or inventory the Mac Mini. Do not mutate K2Bi, personal application history, or unrelated runtime state.

## Prerequisites

- Python 3.12+
- `jq`
- Kimi CLI managed membership (for Task 3B extraction, not this runbook)

## 1. Export redacted Claude history

Run only when explicitly asked to produce a rollback archive. The exporter reads JSONL files from an explicit source directory and emits deterministic, sorted, redacted artifacts plus a schema-v1 manifest.

Preview the manifest without writing anything:

```bash
python3 scripts/export-claude-history.py \
  --preview \
  --redact \
  --source ~/.claude/projects \
  --destination /Users/keithmbpm2/Projects/K2B-Vault/Archive/claude-history/20260729T144334Z \
  --projects K2B
```

Write the archive:

```bash
python3 scripts/export-claude-history.py \
  --freeze \
  --redact \
  --source ~/.claude/projects \
  --destination /Users/keithmbpm2/Projects/K2B-Vault/Archive/claude-history/20260729T144334Z \
  --projects K2B
```

Verify the manifest file:

```bash
python3 scripts/export-claude-history.py \
  --verify-manifest /Users/keithmbpm2/Projects/K2B-Vault/Archive/claude-history/20260729T144334Z/manifest.json
```

### CLI contract

- `--source` and `--destination` are required for `--preview` and `--freeze`.
- `--preview` prints the schema-v1 manifest without writing artifacts or manifest.
- `--freeze` atomically writes artifacts and manifest last.
- `--verify-manifest` reads an existing manifest and checks counts/hashes.
- `--redact` scrubs secret-bearing values and credential keys.

### Manifest schema

- `schemaVersion`: 1
- `frozen`: true
- `sourceSessionCount`: integer
- `artifacts`: sorted by `relativePath`, each with `sha256`

### Safety rules

- Never archive live vault, credentials, or environment files.
- Preview before freezing when the destination already exists.
- Verify the manifest before considering the archive complete.
- Identical `--freeze` reruns are idempotent; a changed or malformed manifest fails closed.

## 2. Rollback

The exported archive is redacted evidence, not a hot-restore snapshot. Rollback is manifest-driven:

1. Dry-run review: compare manifest `sourceSessionCount` and `artifacts` against the retired source.
2. Ownership verification: confirm every artifact owner before any action.
3. Hash verification: run `--verify-manifest` and resolve mismatches manually.
4. Never copy artifacts back automatically and never restart retired services from the archive.

## 3. Inventory retired runtime

Capture sanitized metadata for retired runtime artifacts.

```bash
scripts/inventory-retired-runtime.sh \
  --host home \
  --output /path/to/inventory.json
```

Verify the inventory hashes later:

```bash
scripts/inventory-retired-runtime.sh \
  --verify \
  --output /path/to/inventory.json
```

### Recorded fields

- Host, creation time, source commit.
- PM2: name, command, hash, owner, disposition, env variable names only.
- launchd: label, path, plist hash, owner, disposition.
- cron: source path/hash, owner/disposition, and sanitized command lines only
  (no schedule expression, no raw crontab).
- MCP: source path/hash, owner/disposition, server names, sanitized command
  identity, and env variable names only.
- State: path, hash, owner, disposition.

### Disposition

All entries with unknown ownership are marked `decision-required`. This manifest cannot restart anything.

The default launchd source is the current user's `~/Library/LaunchAgents`. Use `--host sjm` on SJM. Per-machine evidence stays local; do not put runtime inventories or credentials in the synchronized vault.

## 4. Task 3B EOD capture is Codex-only

After Task 3B:

- `scripts/eod-capture-cron.sh` accepts the three capture modes `job-a`,
  `job-b`, and `job-a-then-b`, plus the local-output-only `digest` mode.
- `job-a`, `job-b`, and `job-a-then-b` are compatibility entrypoints for
  interactive, user-requested diagnostics only. Never register or invoke them
  from an unattended schedule, and do not use them for bulk backlog extraction.
- The only unattended capture action is deterministic local discovery through
  `scripts/codex-discovery-job.sh` with the host's approved role.
- Discovery defaults to `~/.codex/sessions/**/*.jsonl`.
- The `claude_code` source fallback, Telegram helpers, and OpenAI API routes are removed.
- Digest output is printed to stdout; no Telegram send path remains.
