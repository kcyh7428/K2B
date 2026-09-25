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
runs use only the signed-in native Codex product, never DeepSeek or another paid
API, with no paid fallback. The 2026-09-25 DeepSeek handoff was a bounded
engineering lane for the repair itself, not a change to scheduled extraction.
Use 2026-09-13 through the current HKT date (including missed dates). Do not widen that range or backfill
earlier history. Use Home's Python 3.12 or SJM's project
`venv/washing-machine/bin/python`, never a provider wrapper.

The machine-local automatic-memory state root belongs to this native job alone
while a batch runs: do not run an interactive or second native batch against the
same state root. Record the UTC start time BEFORE preparing or reading the first
view; the receipt's prepared-view discovery window starts at that instant. That
window is current-run evidence, not proof of exclusive ownership: the receipt can
bound which prepared views it accounts for, but it cannot prove that a
concurrent producer prepared nothing, so one owner and start-before-prepare
discipline are required.

Before worklist selection, check `<state-root>/extraction-hold.json`. If present
or unreadable/malformed, do not select or extract another source; the hold needs
operator intervention and must not be removed by a native run. Home still
performs independently eligible drain/publication steps 4 and 5. On a new
input-size/item-count limit, quota exhaustion, persistent failure, or repeated
selection without progress, create this private hold with UTC timestamp, work
ID when known, reason, and operator-action requirement. Preserve any existing
hold; record failures in the run receipt and do not continue extraction if the
hold cannot be written. Notify when a hold is newly created or changes, not on
every unchanged check. A hold is not an extraction/success receipt.

Before reading dialogue, call `scripts/eod-capture.py memory-extraction-input`
with the selected `--work-id`, configured `--state-root` and `--writer-role`.
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
2. For each returned input view, read all `dialogue_events` and `context_events` and
   produce extraction JSON conforming to `scripts/prompts/eod-capture-extract.md`.
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
3. Save that JSON to a private machine-local temporary file and call
   `scripts/eod-capture.py memory-record-extraction` with the returned work ID.
   Respect `waiting_retry` / `retry_backoff` and terminal `needs_attention`
   results. Never reset attempt counters or create a success receipt manually.
   Ordinary successful or unchanged runs stay quiet; notify only for explicit
   persistent failure, genuine ambiguity, exhausted quota, or required user
   action.
4. On Home only, run `scripts/eod-capture.py memory-home-drain --writer-role home
   --pull-sjm --limit 24` with the configured machine-local state root.
   The command may use only the established `sjm-ai` connection and must report
   Home/SJM downtime honestly.

5. On Home only, publish through `scripts/eod-capture.py memory-publish` using
   the configured state root, `--writer-role home`, and the exact Home vault.
   Only attempt this when a new reconciliation occurred or a prior durable
   reconciliation still awaits publication. The separately provisioned Home
   publication authority, approved policy-ledger hash, exclusive managed paths,
   publisher locks and live Syncthing preflight remain mandatory. A blocked
   publication is waiting/failed, never published; do not bypass checks or edit
   shared memory/index/log files yourself. SJM must never run this step.

Save actual drain and publication command JSON to private files. When publication
is correctly not attempted, use the explicit status `not_attempted` and reason
`no_new_reconciliation_or_pending_publication`. Generate the canonical run receipt
with `scripts/eod-capture.py memory-record-run --writer-role <role> --state-root
<state> --started-at <UTC-start>` and one `--work-id` for each attempted turn.
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
prepared nothing. On Home also supply `--drain-json <actual-drain-output>` and
`--publication-json <actual-publication-output>`. Do not handwrite
native-run-receipts: the helper validates per-turn extraction artifacts, writes
integer schema version 1, and leaves durable failed evidence with a sanitized
diagnostic when command JSON is unreadable or the evidence is malformed. Never
omit a failed attempted work ID to manufacture a successful batch.

Run `memory-backlog --writer-role <role> --state-root <state> --codex-root <root>
--since 2026-09-13 --through <current-HKT-date>` after the batch. Add
`--max-scan-seconds <n>` only when a bounded scan genuinely needs longer; the
budget is enforced inside single large session files too, so an incomplete scan
reports `complete: false` with `status: needs_attention` and never "no backlog".
Save its aggregate JSON beside the run receipt; it validates extraction evidence
without selecting or extracting work. Report pending turns and oldest pending time
when attention is needed. If pending work remains with no completed extraction, or
backlog age exceeds 24 hours after initial catch-up, flag the capacity/hold problem
once and when it changes; do not claim current recall from an ACTIVE registration.
`memory-status` observes registration, latest run evidence, holds and persisted
state; `memory-backlog` measures source coverage. Neither proves shared arrival.
Do not claim achieved cadence or seven-day acceptance without actual evidence.
Ordinary successful or unchanged runs stay quiet.
