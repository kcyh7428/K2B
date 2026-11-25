At every Home heartbeat, run local selection as the first tool action. Run
the following from the Home checkout. Record the printed UTC start and keep
it for every selection and for the final receipt in this batch.

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
  --state-root "$HOME/.local/state/k2b/automatic-memory" --codex-root "$HOME/.codex" \
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
every worklist call. A hold stops
selection and extraction for this batch. After the local steps or a hold,
continue independently eligible Steps 4 and 5 and record actual outcomes. A missing
SMB mount affects publication only. A run without verified local worklist
evidence cannot report completion. Only the worklist command writes its
retained evidence. Preserve that file as generated.

Finish every batch with the native prompt's `memory-record-run` command,
including a `blocked` or `needs_attention` stop. Use the original UTC start
and pass the final worklist JSON whenever the tool returned it.

If setup fails, report the failed step, UTC start and missing receipt. If setup
passes, exit 3 with valid worklist JSON is a recorded stop. Preserve that JSON.
If the worklist command fails without valid JSON, use the receipt failure path
in the worker prompt. An absent receipt requires attention.
