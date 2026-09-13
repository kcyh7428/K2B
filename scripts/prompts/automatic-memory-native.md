# K2B native automatic-memory worker

Run only in an authorized native Codex Automation on the named host. This is a
bounded extraction and deterministic reconciliation workflow. Do not call a
metered API, scripted Kimi batch service, or any unrelated project.

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

Stop after durable Home reconciliation. Shared publication and runtime
activation remain separately gated. Preserve every command's JSON receipt in
the native task result.
