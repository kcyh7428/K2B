# K2B native automatic-memory worker

Run only in an authorized native Codex Automation on the named host. This is a
bounded extraction and deterministic reconciliation workflow. Do not call a
metered API, scripted Kimi batch service, or any unrelated project.

## Activation bounds

The registered job must declare its host, project, writer role, approved local
source root, private state root, inclusive start date and per-run limit. Missing
configuration is a stop, not permission to guess paths. Initial production jobs
run hourly, select at most one completed source, and use 2026-09-13 through the
current HKT date (including missed dates). Do not widen that range or backfill
earlier history. Use Home's Python 3.12 or SJM's project
`venv/washing-machine/bin/python`, never a provider wrapper.

Before worklist selection, check `<state-root>/extraction-hold.json`. If present
or unreadable/malformed, do not select or extract another source; the hold needs
operator intervention and must not be removed by a native run. Home still
performs independently eligible drain/publication steps 4 and 5. On a new
input-size/item-count limit, quota exhaustion, persistent failure, or repeated
selection without progress, create this private hold with UTC timestamp, work
ID when known, reason, and operator-action requirement. Preserve any existing
hold; record failures in the run receipt and do not continue extraction if the
hold cannot be written. Notify when a hold is newly created or changes, not on
every unchanged hourly check. A hold is not an extraction/success receipt.

Before reading dialogue, check the returned bundle file's byte size. If it
exceeds 48000 bytes, set the operator hold and report an input-budget exception;
do not truncate evidence or silently claim it was processed. Extract at most 20
durable items per source. If the complete source requires more, set the hold and
do not record a partial extraction as success. Do not read other sessions, launch
reviewers/subagents, run a test suite, or change code, live rules, credentials,
publication authority or schedules. Empty worklists require no model extraction.

1. Prepare at most the configured number of completed user-origin sources with
   `scripts/eod-capture.py memory-worklist`. Use the explicit Home or
   `sjm-source-only` writer role, the host's local Codex sessions root, the
   machine-local automatic-memory state root, and an inclusive recovery range.
2. For each returned source bundle, read the complete bounded dialogue and
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
   bundle into the output JSON, set `review_state` to `reviewed`, and include
   each item's exact `evidence_event_id`. For a genuinely empty extraction use
   `items: []` and `reviewed_empty: true`; never use empty success for a budget
   hold or failure. Do not invent source metadata or evidence IDs.
3. Save that JSON to a private machine-local temporary file and call
   `scripts/eod-capture.py memory-record-extraction` with the returned work ID.
   Respect `waiting_retry` / `retry_backoff` and terminal `needs_attention`
   results. Never reset attempt counters or create a success receipt manually.
   Ordinary successful or unchanged runs stay quiet; notify only for explicit
   persistent failure, genuine ambiguity, exhausted quota, or required user
   action.
4. On Home only, run `scripts/eod-capture.py memory-home-drain --writer-role home
   --pull-sjm` with the configured bounded limit and machine-local state root.
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

Preserve compact command JSON receipts and UTC start/finish timestamps under
the host's private automatic-memory state root, with the native task/run id
when available. Record selected work IDs, extraction outcomes, reconciliation,
publication or waiting state, and exceptions, not full transcripts. A command
exit code alone is not success: inspect its JSON status. Use worklist exception
statuses and prior native run receipts to identify exhausted retries or repeated
selection without progress; establish the operator hold instead of increasing
limits. `memory-status` exposes aggregate persisted-state counts, not a complete
pending/exhausted inventory or unseen backlog size, and its runtime flags do not
inspect native scheduler activation. Never infer total backlog or activation
from those fields. Do not claim shared arrival, an achieved cadence or seven-day acceptance
without their actual evidence. Ordinary successful or unchanged runs stay quiet.
