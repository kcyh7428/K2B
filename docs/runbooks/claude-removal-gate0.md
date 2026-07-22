# Claude Removal Gate 0: Runtime Inventory and Credential Freeze

## Purpose

Gate 0 creates a read-only inventory of Claude-era runtime surfaces before any
removal work. Run the auditor on the MacBook and Mac Mini, preserve the
sanitized JSON evidence, and resolve every finding before starting a dependent
gate.

## Safe audit procedure

```bash
bash scripts/audit-claude-runtime.sh > /private/tmp/k2b-claude-audit-macbook.json
jq '{host, reachable, finding_counts: (.findings | group_by(.kind) | map({kind: .[0].kind, count: length}))}' /private/tmp/k2b-claude-audit-macbook.json

ssh -o BatchMode=yes -o ConnectTimeout=8 macmini \
  'K2B_AUDIT_ROOT="$HOME" bash -s' \
  < scripts/audit-claude-runtime.sh \
  > /private/tmp/k2b-claude-audit-mini.json
jq '{host, reachable, findings: (.findings | length)}' /private/tmp/k2b-claude-audit-mini.json
```

The output contains only names, paths, labels, and dependency keys. Do not add
audit output containing environment values, headers, tokens, auth files, or
credential values to Git, the vault, or a review request.

The `probe_status` object records whether the read-only `ps`, `launchctl`, PM2,
and crontab inventories completed with valid output. On a Mini audit, all four
must be `true` before its runtime surface is considered verified; an absent,
failed, timed-out, or malformed probe is not an empty inventory and produces
`unverified-mini-surface`.

`K2B_AUDIT_HOST_ROLE` accepts only the exact values `macbook` and `mini` (or an
empty value). With an empty live role, the auditor uses `sysctl -n hw.model`;
only known Mac Mini and MacBook model prefixes identify a host. An invalid role,
unavailable probe, or unknown model is `ambiguous`/unverified, never inferred
from a hostname. Fixture mode may provide `K2B_AUDIT_HARDWARE_MODEL` solely to
exercise this identity rule; live mode ignores that environment variable and
uses the hardware probe only.

For PM2 under minimal cron environments, set `K2B_PM2_PATH` to the approved
executable path. Without that override, the auditor safely checks `PATH`,
`/opt/homebrew/bin/pm2`, and `/usr/local/bin/pm2`; it never evaluates shell
configuration. The process, launchctl, PM2, and crontab probes use bounded
5- or 8-second subprocess timeouts. Process arguments are used only to match
wrapped workloads, and are never output; the report retains the executable
token only. XML and binary LaunchAgent plists are parsed with Python's
standard-library plist parser and report only their label/path.

Keychain credential enumeration is deliberately out of scope for Gate 0. The
auditor never runs `security dump-keychain`, does not enumerate account or
service metadata, and reports `keychain_credential_status: out_of_scope` plus
`keychain-credential-enumeration-unverified`. This is an incomplete credential
surface, not evidence that the keychain is clean. This is the approved plan
interpretation: the Gate 0 design requires credentials without printing secrets
(`design` Gate 0), while Task 1's explicit interface consumes environment
variable names and its required fixture covers an environment-file secret
(`implementation` Task 1, lines 96-108). Neither Task 1 interface specifies
macOS keychain enumeration, and the source job forbids auth-value output and
scope expansion. A later explicitly authorized credential operation must own
any keychain work.

## Filesystem scope

In live mode the auditor scans only these paths beneath `K2B_AUDIT_ROOT`:

- `Projects/K2B/` recursively, excluding `.git`, `node_modules`, virtual
  environments, `.cache`, and `.Trash`;
- `.claude/` recursively with the same exclusions;
- `Library/LaunchAgents/` recursively with the same exclusions; and
- files directly in `K2B_AUDIT_ROOT` itself.

It does not recurse through other home subdirectories. The `filesystem_scope`
field and `filesystem-scope-limited` finding make that boundary explicit.
Other directories are out of scope, not clean. Relevant process and PM2
matches are surfaced in both their collection and safe structured findings;
only executable paths and PM2 names/paths are emitted. Malformed crontab lines
are never printed: the auditor records a path-only
`unparseable-crontab-entry` finding, continues inspecting the remaining lines,
and reports the probe status after the full output is processed.

Fixed audit filenames and extensions are compared case-insensitively to match
the normal macOS APFS behavior. Symlinked files and directories are never
followed; each receives a path-only skipped-symlink finding. Files over the
one-megabyte inspection limit likewise receive a path-only
`skipped-oversized-file` finding rather than silently appearing empty.
Malformed `ps` and `launchctl` rows are skipped individually with generic
path/name-only findings, while the remaining successful rows continue to be
inventoried.

## Review disposition

This Gate 0 tooling was committed on 2026-07-22 under Keith's explicit local
commit override with status `DONE_WITH_CONCERNS`. Independent Kimi review found
multiple issues that were corrected, but the final clean-verdict attempts were
repeatedly empty, truncated, or unparseable. Record the review gate as
`REVIEW_BLOCKED`, not PASS. The visible keychain and filesystem-scope findings
above remain deliberate limitations; this commit does not authorize Task 2,
credential rotation, deployment, sync, or Claude removal.

## Credential freeze

This Task 1 run is inventory-only. Do not rotate, revoke, update, validate, or
otherwise mutate a credential consumer. Record only the credential names from
the `credential_names` field and any name-only MCP findings.

The known names requiring owner and replacement-consumer confirmation are:

- `FIREFLIES_API_KEY`
- `OPENAI_API_KEY`

Stop the credential work if the credential owner, approved credential store, or
replacement consumer is unknown. A future, explicitly authorized rotation gate
must record name-only completion timestamps and separately verify the approved
replacement consumer before revocation. Secret values must never be recorded.

## Stop conditions

- If the Mini SSH command fails or its output is not valid JSON, record
  `BLOCKED_MINI_UNREACHABLE`; do not begin a dependent task.
- If `unverified-mini-surface` remains, Mini evidence is incomplete.
- If `stale-k2b-launchagent` reports `com.k2b-remote.app`, preserve it as a
  removal dependency; do not unload or edit it in Gate 0.
- If a finding names a live Claude, Anthropic, Telegram, MCP, schedule, or
  memory-reader surface, document it for the corresponding later gate. Gate 0
  does not remove or modify the surface.
