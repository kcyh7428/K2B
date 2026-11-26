At every Home heartbeat, run local selection as the first tool action. Run
the following from the Home checkout. Record the printed UTC start once
and reuse it verbatim for every selection and the final receipt.

```sh
run_start=$(/usr/local/bin/python3.12 -c '
import sys
from datetime import datetime, timezone

if sys.version_info[:2] != (3, 12):
    raise SystemExit(2)
print(datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))
') || {
  printf 'Home Python 3.12 setup failed at %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >&2
  exit 2
}
through_date=$(TZ=Asia/Hong_Kong date +%F)
printf 'Home run start %s\nHome run through %s\n' "$run_start" "$through_date"
cd /Users/keithmbpm2/Projects/K2B || exit 2
/usr/local/bin/python3.12 scripts/eod-capture.py memory-worklist \
  --state-root "$HOME/.local/state/k2b/automatic-memory" --codex-root "$HOME/.codex/sessions" \
  --writer-role home --since 2026-09-13 --through "$through_date" \
  --limit 1 --run-started-at "$run_start"
```

Do not inspect the NAS before local selection and extraction. This command
checks the hold before each selection. It returns `blocked` for a present
hold and `needs_attention` with a hold diagnostic when the hold cannot be
verified. `blocked` returns exit 0 and follows the existing hold notification
rules. `needs_attention` returns exit 3. Include its hold diagnostic and the
required operator action in the run summary. Both outcomes stop extraction.
Read all of `scripts/prompts/automatic-memory-native.md`. Follow the selected turn
through Steps 2 and 3. Repeat Steps 1 through 3 as permitted by the existing
batch limits, using the same printed UTC start and HKT through date with
every worklist call. Use the same `$HOME/.codex/sessions` source root for each
selection and for the final backlog check with `--native-home`. A hold stops
selection and extraction for this batch. After the local steps or a hold,
continue independently eligible Steps 4 and 5 and record actual outcomes. A missing
SMB mount affects publication only. A run without verified local worklist
evidence cannot report completion. Only the worklist command writes its
retained evidence. Preserve that file as generated.
Recover the original UTC value from the first tool result in this scheduled
turn. This permanent chat already retains that result. Save this run's drain,
publication and backlog outputs to private files under its configured
machine-local state root. If the original start cannot be recovered, report an operator
alert in this permanent chat. Report that the recorder was never invoked
and list any per-turn receipts you can verify from this wake. Receipt-based
status can only show the last verified run until the next receipt. Use
the wake's retained tool evidence for operator investigation. Never select
a timestamp from another wake or create a substitute receipt.

Finish every batch with `memory-record-run --finish-home`, including a
`blocked` or `needs_attention` stop. Pass `--writer-role home`, the
configured `--state-root`, original UTC `--started-at`,
actual `--drain-json`, configured `--vault-root` and machine-local
`--memory-path`. Include
one `--work-id` per attempted turn and use `--deferred-work-id` with its
actual `--deferred-reason` for an eligible deferral. Use the actual
`--publication-json` when available and the native prompt's
`--exceptions-json` rules. Preserve the producer's
worklist evidence. The recorder reads that file for the original start.
Follow Step 5 for the publication decision. The read-only
`memory-publish --native-smb --check-only` is the mount preflight when the
mount is absent or uncertain. Use it after verifying the drain counts.
Publication writes require fresh mount and alias verification, compile-helper
readback, and the per-invocation `K2B_COMPILE_INDEX_LOCK` and `K2B_WIKI_LOG`
bindings.
If no publication JSON exists, the receipt command checks approved publication
evidence without writing to the vault.

Read the run recorder's top-level status, outcome and exit code together.
A failed publication command can sit inside a successfully written run receipt.

| Exit | Returned status | Outcome and reporting |
| --- | --- | --- |
| 0 | `recorded` | `completed` |
| 3 | `recorded` | Report the actual outcome and diagnostic. A mount wait is `waiting`. `MissingRunEvidence` is `failed`, with verified local turn receipts retained. It can mean the mount became ready after publication had been deferred. |
| 2 | `failed` if recorder JSON includes `receipt_path` | Report the actual diagnostic and durable failed receipt. Missing or invalid reconciliation backlog counts yield `publication_backlog_evidence_invalid`. An unverifiable eligibility artifact yields `publication_eligibility_unverified`. |
| 2 | No `receipt_path` | Setup, inputs or receipt writing failed before yielding durable evidence. Treat missing or invalid JSON and JSON without a receipt path the same way. Report the actual error and the missing receipt. |

If setup fails, report the failed step, UTC start and missing receipt.
If exit 2 has no usable receipt path, report the sanitized stderr and flag
a missing receipt for operator attention.
The separate worklist command can return exit 3 with valid worklist JSON.
Preserve that JSON and follow its stop decision.
If the worklist command fails without valid JSON, use the receipt failure path
in the worker prompt. An absent receipt requires attention.
