# K2B native automatic-memory worker

Run only in an authorized native Codex Automation on the named host. This is a
bounded extraction and deterministic reconciliation workflow. Do not call a
metered API, scripted Kimi batch service, or any unrelated project.

## Activation bounds

The registered job must declare its host, project, writer role, approved local
source root, private state root, inclusive start date and per-run limit. Missing
configuration is a stop, not permission to guess paths. The repaired production
jobs run every six hours, with at most 12 completed turns, 240000 cumulative
input-view bytes and 20 minutes per host per run, whichever comes first. These
batch limits also apply to operator-configured hourly recovery of the same
named jobs. Only the interactive operator may activate that recovery interval;
native workers must never change their own schedule or other activation bounds.
The hourly recovery interval does not widen the source-date window or enable
any additional provider or background routine.
These runs use only the signed-in native Codex product, never DeepSeek or another paid
API, with no paid fallback. The 2026-09-25 DeepSeek handoff was a bounded
engineering lane for the repair itself, not a change to scheduled extraction.
Use 2026-09-13 through the current HKT date (including missed dates). Do not widen that range or backfill
earlier history. Use Home's Python 3.12 or SJM's project
`venv/washing-machine/bin/python`, never a provider wrapper.

Home performs its local worklist and extraction steps before inspecting NAS
mounts or starting the Home drain. The local source and private state are
available independently of SMB. An unmounted share affects publication in
Step 5. It never gates the worklist or extraction. Follow each step in this
file, starting with the local gate, even when a previous run did other work.

The machine-local automatic-memory state root belongs to this native job alone
while a batch runs: do not run an interactive or second native batch against the
same state root. Record the UTC start time BEFORE preparing or reading the first
view; the receipt's prepared-view discovery window starts at that instant. That
window is current-run evidence, not proof of exclusive ownership: the receipt can
bound which prepared views it accounts for, but it cannot prove that a
concurrent producer prepared nothing, so one owner and start-before-prepare
discipline are required.

Run selection, input preparation, and receipt commands as separate calls that
complete without interactive input. Do not keep a helper waiting on stdin
between steps, including a Python helper launched with a here-document. Use
the selected work ID and the same recorded start time in the next command.
The native job remains the sole owner of the batch across those calls.

Before SJM worklist selection in each scheduled run, check
`<state-root>/extraction-hold.json` afresh in private machine-local state.
Home's Step 1 command checks that same file before selecting and reports
`blocked` for a present hold or `needs_attention` for an unverifiable hold.
If it is absent at this run's first check, proceed to Step 1 even if the
previous run saw a hold. An operator release between scheduled runs is
permission to resume the normal bounded selection. On Home, Step 1 is the
first work decision when the current hold is absent. Prior publication errors
never justify an extraction hold. If present
or unreadable/malformed, do not select or extract another source; the hold needs
operator intervention and must not be removed by a native run. Home still
performs independently eligible drain/publication steps 4 and 5. On a new
input-size/item-count limit, quota exhaustion, persistent failure, or repeated
selection without progress, stop taking new turns. On Home, use only
`memory-record-home-hold --state-root <state> --started-at <UTC-start>
--work-id <current-work-id> --reason <type> --worklist-json <current-worklist>`
to create a durable hold. Supply `--reviewed-json <current-reviewed-output>`
for `item_count_exceeded` only. Use the actual private worklist JSON from the
selection of that turn, even if it is different from the last worklist used
for the run receipt. Use the original run start. The helper rechecks the
source limit, current input, or terminal extraction diagnostic. Never create,
replace, or remove a Home hold directly. If it refuses, or a current quota
error has no locally verifiable hold evidence, stop the batch and report the
current failure and required operator action. This is a stop for this run
without a new durable hold. SJM retains its operator-hold procedure with UTC
`created_at`, work ID when known, reason, and operator-action requirement.
Preserve any existing hold and record the actual run failure.
Notify when a hold is created, its reason, work ID
or required action changes. Report the first unverifiable observation, or the
transition from readable to unverifiable, using the previous run's saved
hold observation for comparison. Keep reporting the blocked state without
sending another hold-change notification while it stays unverifiable.
Use the previous run's observation only for notifications. If a hold is
present or unverifiable at any check in this run, stop extraction for the
rest of this run, even if the file later becomes absent. Keep the observed
hold state and time in the private run artifacts and report any same-run change.
If the worklist itself returned `blocked`, pass that actual result to the receipt
helper. The helper can report that the hold changed after the worklist check.
The receipt helper checks the hold file directly and records `hold_check`
as `absent`, `present`, or `unknown`. An unknown hold blocks extraction and
requires operator attention. Startup also shows the observed hold.
Existing valid holds remain in force. A hold does not certify extraction
or a successful run.

Immediately before each extraction, recheck the local hold file. If it is
present or unverifiable, stop extracting for the rest of this run and leave
the affected selection pending. Before reading dialogue, call
`scripts/eod-capture.py memory-extraction-input`
with the selected `--work-id`, configured `--state-root` and `--writer-role`.
Home's input command rechecks the hold and exits 3 with `blocked` or
`needs_attention` when it cannot prepare a view because of that hold.
Keep the selected turn pending and record the observed stop.
Read only the returned `input_path`, never the full source bundle. This view
contains every event of the selected completed turn and bounded recent context;
the full immutable source stays on disk for validation. Check the view file's
byte size before reading it. If the command returns `input_budget_exceeded` or
the view exceeds 48000 bytes, set the operator hold; do not truncate the current
turn or silently claim it was processed. A large earlier conversation is not
itself a hold: completed turns are processed as separate pieces. Extract at most 20
durable items per source. If the complete source requires more, set the hold and
do not record a partial extraction as success. Do not read other sessions, launch
reviewers/subagents, run a test suite, or change code, live rules, credentials,
publication authority or schedules. Empty worklists require no model extraction.

Record the UTC start time ONCE before the loop; never reset it when selecting
another turn. Stop selecting new turns after 18 minutes to leave time for
drain/publication and the canonical receipt inside the 20-minute total budget.

1. Select one completed user-origin turn with
   `scripts/eod-capture.py memory-worklist --limit 1`. After recording its extraction,
   repeat steps 1–3 while under all three batch limits. Inspect each input view's
   byte size before reading it. If reading the next view would exceed the cumulative
   budget, leave it pending; do not mark it reviewed-empty or create an operator hold
   for this normal batch boundary. Track every attempted work ID, including failures;
   record each selected-but-unread turn separately as a deferred ID with the reason
   that stopped selection (`byte_budget`, `time_budget`, or `turn_budget`); declare
   `time_budget` only after the 18-minute selection cutoff and `turn_budget` only
   after 12 attempted turns, or the receipt fails instead of completing.
   Stop on empty work, a hold, retry/needs-attention, or a normal batch boundary.
   Use the explicit Home or
   `sjm-source-only` writer role, the host's local Codex sessions root, the
   machine-local automatic-memory state root, and an inclusive recovery range.
   A worklist with source_scan_time_budget is a partial scan, never proof of no
   backlog. Its scan.first_unverified_source identifies where scanning stopped.
   Every selection scans from the configured source root's beginning, including
   older-source appends and any previously unavailable sources. A clean partial
   scan may be retried with --max-scan-seconds bounded by the remaining time.
   On Home the scan ends by 17:30 elapsed and selection persistence by 18:00
   elapsed. Use the remaining time through 20:00 elapsed to complete the
   batch and write its receipt. Do not reset the
   original start or exceed the 20-minute total. If the remaining budget cannot
   finish scanning, retain the pending work and report the capacity limit.
   source_persistence_time_budget is a separate private-record validation limit.
   Keep pending records; retry only within this run's remaining original budget.
   On Home pass `--run-started-at <original-UTC-start>` on every selection
   call and keep the entry command's HKT through date for that batch so
   every call follows the same window. The command saves its own private evidence. Read its actual
   output and handle the selected turn before inspecting any NAS mount.
   Set `--codex-root` to the exact registered Home source root
   `$HOME/.codex/sessions`. The parent directory includes archived chats
   outside this job's source scope.
   If that directory is missing or relocated, report the failed source
   binding. Resume when the Home operator restores or reviews the registered
   binding.
   Save each actual worklist JSON in this run's existing private artifacts.
   If a full scan cannot finish in the remaining budget, report the capacity
   limit and leave work pending. Never claim empty coverage, skip unavailable
   sources through a saved cursor, or add another progress store.
2. For each returned input view, read all `dialogue_events` and `context_events` and
   produce extraction JSON conforming to `scripts/prompts/eod-capture-extract.md`.
   The automatic-memory destination accepts only `fact`, `decision`, `preference`
   and `commitment`. Do not emit the generic extractor's `learning` kind here;
   retain an explicit durable observation as a `fact` when its evidence supports it.
   Preserve exact evidence quotes and event identities. Record facts,
   Keith-authored decisions, explicit preferences, and open commitments. Do not
   promote assistant suggestions to Keith decisions. Earlier turns in a
   completed prefix are context only: every extracted item's evidence event must
   have the work item's `completed_turn_id`. Facts from an earlier turn belong
   to that turn's earlier immutable worklist cursor and must not be redated.
   The native record command also needs source binding beyond the generic
   extractor example: copy `raw_source_sha256`, `transcript_sha256`,
   `completed_cursor`, `completed_prefix_sha256`, `completed_prefix_mode`,
   `completed_date`, `host_id`, and `session_id` unchanged from this source
   input view into the output JSON, set `review_state` to `reviewed`, and include
   each item's exact `evidence_event_id`. For a genuinely empty extraction use
   `items: []` and `reviewed_empty: true`; never use empty success for a budget
   hold or failure. Do not invent source metadata or evidence IDs.
   `context_omitted_events` reports earlier context not shown, not lost current
   evidence. Never extract old context again or infer a fact from an unresolved
   "yes", "that", or similar reference. If an apparent durable decision needs
   omitted context, establish an ambiguity hold for operator review; do not
   guess or report reviewed-empty success for unresolved durable content.
   A short explicit acceptance such as "yes" may be a decision when the
   immediately preceding assistant event contains the actual proposal. Keep
   the exact user quote and evidence_event_id, and add acceptance_context with
   that proposal's event_id and an exact quote of the proposal. Read and resolve
   the proposal before extracting the decision. Never pad the user quote or
   infer approval from an ambiguous, qualified or unrelated reply. Missing
   proposal evidence remains an ambiguity hold. Compression of a source to
   .jsonl.zst is supported by the source reader; preserve the original files.
3. Save that JSON to a private machine-local temporary file and call
   `scripts/eod-capture.py memory-record-extraction` with the returned work ID.
   Respect `waiting_retry` / `retry_backoff` and terminal `needs_attention`
   results. Never reset attempt counters or create a success receipt manually.
   Ordinary successful or unchanged runs stay quiet; notify only for explicit
   persistent failure, genuine ambiguity, exhausted quota, or required user
   action.
4. On Home only, run `scripts/eod-capture.py memory-home-drain --writer-role home
   --pull-sjm --limit 24 --memory-path <state>/memory.json` with the configured
   machine-local state root.
   The command may use only the established `sjm-ai` connection and must report
   Home/SJM downtime honestly.
   Save the actual drain output under the private run-artifacts directory
   with a name that identifies this run. Read `reconciliation_conflicts` and
   `unreconciled_remaining` from that output only. Both must be
   nonnegative integers. Report missing or invalid counts and omit
   publication JSON. Do not invoke publication for this run.
   Report the recorder's actual diagnostic and retain local turn receipts.
   Invalid or missing backlog counts can yield
   `publication_backlog_evidence_invalid` with exit 2. If the backlog counts
   are valid but `reconciled` is invalid, the receipt can record a publication
   failure with `InvalidDrainEvidence` and exit 3. Unreadable drain JSON has
   its own `command_json_unreadable` diagnostic.
   Report missing fields as possible drain
   command incompatibility only if the drain command succeeded and omitted the
   fields. Report a failed invocation or unreadable output as such. An SJM
   connection wait can appear in valid Home drain JSON. In the user-facing
   run summary, report verified conflicts and the need for reviewed Home key
   resolution, or report that the counts could not be verified. Preserve
   accepted deliveries and diagnostics.

5. On Home only, decide whether publication is due. The read-only
   preflight and publisher both use
   `scripts/eod-capture.py memory-publish --native-smb` with the configured
   state root, `--writer-role home`, the exact Home vault and
   `--memory-path <state>/memory.json`. Add `--check-only` for the read-only
   operation.
   Read `reconciliation_conflicts` and `unreconciled_remaining` directly from
   this run's saved drain JSON. Two integer zeroes are verified counts,
   including when `reconciled` is zero. If either backlog count is missing or
   invalid, follow step 4 and omit publication JSON. If the drain JSON itself
   is unreadable, do not invoke publication. Pass the expected drain path
   to the receipt command without publication JSON.
   The next scheduled run uses fresh drain output and may
   publish when its own verified counts and the normal eligibility check pass.
   If the drain reports either `reconciliation_conflicts` or
   `unreconciled_remaining` greater than zero, omit publication JSON.
   The recorder supplies `reconciliation_needs_attention` if
   `reconciliation_conflicts` is positive, otherwise use
   `unreconciled_backlog_remaining`. For backlog alone, the next bounded drain
   can continue automatically. Report the verified remaining count and
   deferral reason in the user-facing run summary. If this run reports
   `reconciled: 0` while accepted work remains, flag Home operator
   investigation. A drain labeled `reconciled` can have `reconciled: 0`
   and verified replays. Replays confirm earlier work.
   Attempt publication only if both counts are verified zero and either a new
   reconciliation occurred or a prior durable reconciliation still awaits
   publication. Once both backlog counts are verified zero, require
   `reconciled` to be a nonnegative integer in the same drain output. A missing
   or invalid `reconciled` count also requires omitting publication JSON.
   With those drain counts verified, run `memory-publish --native-smb --check-only`
   as the read-only preflight, including when the mount appears absent or uncertain.
   The command itself verifies the binding before any vault read and takes no
   writer locks. If a publish or eligibility command returns
   `not_attempted` with reason `publication_mount_unavailable` or
   `publication_mount_probe_timed_out`, pass that actual JSON and
   skip any further publication attempt. Never infer the
   SMB source from the local mount folder.
   Always pass actual mount deferral JSON when available. Never write a mount
   deferral by hand. If the publisher produced no usable result and the
   recorder independently finds the binding unavailable, it records the wait.
   If the binding is ready at that check, publication is still due and there
   is no verified publisher receipt, the recorder reports `MissingRunEvidence`.
   With otherwise valid evidence the recorder returns status `recorded`,
   outcome `failed` and exit 3. Valid local turn receipts stay verified.
   The next normal run can attempt publication.
   With otherwise valid evidence, an absent or replaced binding records
   outcome `waiting`, exit 3, and the diagnostic
   `Home publication waiting for the approved SMB binding`. Keep the verified
   local extraction and drain outcomes. A timed-out probe also records
   `waiting` with exit 3 and a separate timeout diagnostic. An approved
   but read-only mount or malformed probe result records a publication
   failure while keeping the local turn results. A recovered mount requires the full
   binding checks before any write.
   Keep the preflight's actual JSON in the private run artifacts. On exit 0
   with `publication_due: false`, omit publication JSON. The recorder verifies
   current publication evidence. On exit 0 with `publication_due: true`,
   continue through the write gates below. If the check fails or the decision is
   missing, malformed, or unclear, do not invoke publication. Pass the helper's
   actual structured failure or mount deferral JSON whenever present. It takes
   precedence over an eligibility fallback. Only if the check returned neither
   a usable decision nor a structured failure or deferral, keep the actual exit
   code and sanitized output privately and pass `not_attempted` with reason `publication_eligibility_unverified`, which
   the recorder rejects with exit 2, diagnostic
   `publication_eligibility_unverified` and a durable failed run receipt.
   Retain the returned receipt path and report its outcome and diagnostic.
   Pass the publication result, a structured failure emitted by the check,
   or the recorded deferral to the run receipt. A bare eligibility decision
   is not a publication result.

   Before the normal publication command, verify the current mount and alias.
   In one subshell set `K2B_COMPILE_INDEX_LOCK="/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault/.k2b-compile-index.lock.d"`
   and `K2B_WIKI_LOG="/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault/wiki/log.md"`.
   Follow the effective-setting readback and busy-lock runbook in
   `k2b-vault-writer`. In that subshell export both bindings and the verified
   resolved `K2B_VAULT_PATH`. Import
   the installed compile helper without main and compare its `LOCK_DIR`
   to that NAS root's `.k2b-compile-index.lock.d`. Retain the setting,
   source hash and publication command identity privately. Missing, default,
   mismatched or unreadable settings defer publication. In that subshell
   run the publication command without `--check-only` using Home Python 3.12
   and the verified exported environment. Retain the existing run budget.
   Unknown lock ownership stays blocked and never permits timeout deletion.
   The separately provisioned Home publication authority, approved policy-ledger hash, exclusive managed paths,
   publisher locks and the approved exact SMB publication-authority/live
   preflight remain mandatory. The imported index helper inherits the NAS
   compile-lock binding; private publication/reconciliation locks remain intact.
   Shared logging keeps its separate Home log lock, not the compile lock.
   A blocked publication is waiting/failed, never published; do not bypass checks or edit
   shared memory/index/log files yourself. SJM must never run this step.

Save actual drain and publication command JSON to private files. The only
handwritten publication artifact allowed here is the explicit
`publication_eligibility_unverified` error when the eligibility command
has no usable result. That artifact can only produce a failed run receipt.
Never handwrite a mount deferral or success.
Generate the canonical run receipt with `scripts/eod-capture.py memory-record-run
--writer-role <role> --state-root <state> --started-at <UTC-start>` and one
`--work-id` for each attempted turn.

### Home run receipt

Add `--finish-home` and
`--drain-json <actual-drain-output>`, `--vault-root <configured-vault>` and
`--memory-path <actual-memory-path>`. Pass the same machine-local memory
path to all three Home commands. The registered job uses `<state>/memory.json`.
Use the first Home command's printed UTC start verbatim in every selection
and the recorder. The first command captures it once in UTC with six
fractional-second digits and a trailing `Z`. The helpers parse the timestamp
and derive the filename from its normalized UTC value. The selector
atomically retains evidence under that key. Recover a lost start only
from the first tool result in this wake. If that result cannot establish
the start, stop and report a specific operator alert in the permanent
chat. Leave receipt status at the last verified run and report any
per-turn receipts actually verified from this wake.
The recorder uses that start to find the exact
`home-worklist-evidence-<UTC-start>.json` file under the private state root.
Omit `--worklist-json` on Home. If this evidence is missing, unreadable
or has a different start, preserve it as found and report the returned
producer-evidence diagnostic and receipt path. With a clear hold such a
run cannot record `completed`. Keep a missing selection pending for a fresh
scheduled run. Pass the actual
`--publication-json <actual-publication-output>` when available.
A claim of a new publication in this wake requires a durable publisher
receipt for the approved binding with `published_at` at or after this
wake's original UTC start. An older duplicate attests only that the shared
publication was already current. Its original timestamp is preserved. The
recorder independently verifies the exact current SMB binding,
reconciliation watermark and shared artifact hashes before accepting that
readback. Report it as already current. Claim a new write only when the
actual publisher output proves a new write in this wake.

When publication JSON is absent, the recorder checks the actual drain
counts and approved SMB binding without writing. Missing JSON by itself
proves neither a mount wait nor publication. A verified durable current
publisher receipt can recover an omitted result under the same rules. If
recovery returns mismatched or unreadable receipt evidence, report the
recorder's specific validation diagnostic.
The same-run worklist and turn evidence come from this wake's original UTC
start and attempted turn receipts. An older duplicate can establish current
shared state only. It cannot establish this wake's worklist or any new write.
If publication becomes due again between an actual duplicate response and
the recorder's check, the recorder saves a `publication_due_again` wait. The
next eligible Home run retries. If a different publication is already current,
the older supplied duplicate cannot establish this run's publication result.
If the approved SMB binding is ready, publication remains due and no same-run
receipt can be recovered, the recorder saves a publication failure with
error type `MissingRunEvidence`. With otherwise valid evidence, the command
returns status `recorded`, outcome `failed` and exit 3. Verified local turn
results are retained.

### SJM run receipt

On SJM only, pass the final selection via `--worklist-json` whenever the
tool ran, even if it selected zero turns. If unreadable, use its expected
path on SJM. Preserve both hosts' actual source-bound turn receipts and earlier
worklist results. Home's local work remains eligible through a publication
wait. A `blocked` worklist still stops extraction and exits normally.

### Receipt arguments for both hosts

Home omits `--worklist-json` and follows the retained producer evidence
procedure above.
A selected or prepared turn stays pending if extraction never started.
Leave that work ID out of `--work-id`.
Pass each selected-but-unread boundary deferral with `--deferred-work-id` and its
`--deferred-reason`; the helper requires the view to be inside this run's window,
in the current writer role, and without an existing extraction receipt, so a
stale or already-extracted view is never subtracted from the byte budget. A
`byte_budget` deferral must be the view that crossed the cumulative byte budget.
Record unexpected command failures and holds as structured schema-v1 entries in
a private JSON array passed with `--exceptions-json`; free text is rejected and
malformed exceptions fail the run instead of completing it. The helper discovers
input views prepared since the original start time, and a prepared-but-unaccounted
turn makes the outcome ambiguous rather than failed, so a prepared turn cannot
silently vanish from the receipt; it cannot prove that a concurrent producer
prepared nothing. Do not handwrite
native-run-receipts: the helper validates per-turn extraction artifacts, writes
schema version 2 on Home and 1 on SJM, and leaves durable failed evidence with a sanitized
diagnostic when command JSON is unreadable or the evidence is malformed. Never
omit a failed attempted work ID to manufacture a successful batch.

Exit 0 with status `recorded` means the run completed. Exit 3 with status
`recorded` means a valid receipt for a non-completed outcome, including
`blocked`, `waiting`, or `needs_attention`. Include the recorded outcome
and diagnostic in the run summary. Keep the recorded receipt and do not
report a receipt write failure for exit 3. On exit 2, report the helper's
failure and the private evidence location. Keep the original artifacts and
stop this batch. Only the existing verified per-turn extraction receipts
prove turns were recorded. Never rerun an extraction to replace a missing
or invalid run receipt.

Run `memory-backlog --writer-role <role> --state-root <state> --codex-root <root>
--since 2026-09-13 --through <current-HKT-date>` after the batch. On Home
add `--native-home` and use the same registered sessions root as selection. Add
`--max-scan-seconds <n>` only when a bounded scan genuinely needs longer; the
budget is enforced inside single large session files too, so an incomplete scan
reports `complete: false` with `status: needs_attention` and never "no backlog".
Use a Home backlog for run reporting only when it says
`home_source_scope: registered_sessions`.
Save its aggregate JSON beside the run receipt; it validates extraction evidence
without selecting or extracting work. Report pending turns and oldest pending time
when attention is needed. If the worklist reports a retained bundle outside
the registered root, report its work ID, keep the evidence, and continue
with eligible work. If pending work remains with no completed extraction, or
backlog age exceeds 24 hours after initial catch-up, flag the capacity/hold problem
once and when it changes; do not claim current recall from an ACTIVE registration.
Report `empty_completed_turns` from the backlog JSON as excluded empty completions
in those summaries. Empty completions are not successful extractions; a rising
count with no real dialogue progress requires operator diagnosis of source format.
`memory-status` observes registration, latest run evidence, holds and persisted
state; `memory-backlog` measures source coverage. Neither proves shared arrival.
Do not claim achieved cadence or seven-day acceptance without actual evidence.
Ordinary successful runs stay quiet. An unchanged operator hold is still blocked
ingestion, never healthy memory: report that state with pending-turn count,
oldest pending time and the required operator action in the user-facing final
message. Preserve the canonical receipt schema and existing hold/exception fields;
do not add prose or new fields to command evidence or receipt JSON.
Use helper-produced backlog/status fields exactly as returned; the restriction
above forbids worker-authored alterations, not fields emitted by the helpers.
Keep the explanation compact; do not describe a blocked batch as ordinary success.
