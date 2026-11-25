# K2B automatic memory recovery

Keith confirmed the scoped recovery on 22 November 2026 in Codex chat
`01a2030e-0540-7f53-8fff-9110f3557ab2`, replying “go” to the recovery
recommendation informed by Neon’s audit. The affected hosts are Home and SJM.
Preserve their local edits and machine-specific settings. Work from one
reviewed recovery branch. Wider branch cleanup, scan performance changes and
NAS lock changes are separate work.

## Behaviour

Each permanent worker uses bounded completed dialogue to propose durable
items. Code checks its evidence and writes source-bound receipts. SJM stages
its deliveries locally. Home alone reconciles deliveries and publishes the
verified snapshot to NAS. Recall reads that snapshot through the approved host
read route. A newly captured decision from each Mac should become recallable
with its original source. After a replay the same decisions should remain
available once, with their source history intact.

Home hashes the original evidence bytes both when it creates a version and
when it verifies the version. Preserve the exact quote and existing IDs.
Count already accepted deliveries against Home’s 24-slot reconciliation
limit. Share available admission capacity across both queues, with spare
capacity available to the other queue. Reconcile pending deliveries before
replays. If existing accepted work remains, publication waits with an explicit
reason. Its complete reconciliation proof remains mandatory.

## Isolated reconciliation conflicts

On 22 November 2026, Keith replied “go” to the narrower failure rule in the
same recovery chat, after Neon’s design review. On Home, only a verified
memory-key kind collision on an accepted delivery is isolatable. Home records
a private diagnostic and finishes other already accepted work within the
existing limit. The original delivery and ledgers are preserved. The first
unresolved collision pauses admission from both queues and keeps publication
waiting behind the complete-receipt gate. SJM keeps staging locally.

Home retries a flagged delivery when the operator’s existing reviewed key
resolution changes. A successful retry supplies the ordinary reconciliation
receipt. Corrupt state, invalid resolution files, unsafe storage, and other
unexpected failures stop the drain. No delivery is discarded or marked
reconciled by the diagnostic.

Acceptance examples are a collision between two valid accepted deliveries,
zero new admissions while the collision persists, no repeated attempt on
unchanged evidence, successful retry after verified key resolution, and a
damaged inbox entry that stops the drain without an isolation diagnostic.

Receipt and status changes distinguish a hold, a worklist needing attention,
an SJM connection failure and a failed SJM command. A held run retains the
prior successful-run time and records the actual hold. An unavailable SJM
does not prevent independently eligible Home work. Home’s own outcome and
counterpart transfer are reported separately, with unfinished SJM delivery
visible. A changed or worsening hold warrants attention. A continuing hold
stays visible between background runs.

## Acceptance and rollout

1. Reconcile all 19 observed accepted deliveries on an isolated copy. An
   unchanged replay must append zero versions and preserve state bytes.
2. With 30 deliverable envelopes and an empty inbox, drain no more than 24
   combined in a pass and allow the rest to be processed on the next pass.
   Both hosts get capacity. With 25 already accepted at the start, reconcile
   24, admit zero new work, and check that direct publication still refuses
   until the last accepted item has a verified receipt.
3. A held run with zero extraction and a worklist that cannot safely proceed
   are visible as such. A genuinely idle Home drain can complete normally.
   A transport interruption and a failed remote command have distinct
   evidence. Independently eligible Home steps can continue on disconnection.
4. Independently review the exact diff. Verify Home’s NAS and effective lock
   bindings, drain the accepted records and read back the published hashes
   while extraction remains held. Release Home’s hold only with evidence that
   its triggering failure is resolved. Verify a bounded cycle and recall.
5. Preserve SJM’s separate changes. Verify SJM’s read configuration and bind
   the held approval to the actual source proposal before resuming extraction.
   Verify queued evidence, acknowledgement, Home reconciliation, publication
   and source-backed recall. Preserve the hold if its cause is unresolved.

Keep the existing writer roles, schedule, extraction limits, provider and
source window. An incomplete or failed acceptance check leaves the affected
deployment pending with its evidence preserved.

## Evidence before activation

- Home copy replay applied 19 accepted deliveries and 30 memory versions.
  Replaying again added zero versions and preserved the snapshot hash.
- A source-bound retry of the exact SJM held turn queued one reviewed decision
  on SJM in a temporary state root. Replay returned the same delivery identity.
  The quote is an explicit request with a named destination, and the retained
  context identifies the draft. The short-approval validator is unchanged.
- Home’s approved SMB mount, NAS compile lock binding, shared log binding and
  publication authority passed preflight. The independently reviewed quote
  fix was pushed as `492949c` and activated on Home. A bounded live drain
  reconciled all 19 already accepted deliveries while extraction stayed held.
  The two NAS artifacts matched the publication receipt, and a restored
  record from each source host was recallable with its citation.
- Home and SJM extraction holds stay in place until the rest of the recovery
  passes independent review and the relevant live checks.

## 24 November Home hold repair

Keith approved the repair, Home rollout, conditional Home hold release, and
scheduled acceptance in Codex thread `01a2030e-0540-7f53-8fff-9110f3557ab2`.
The 23 November Home worker read the patched prompt and created a new extraction
hold from earlier publication receipts before selecting a turn.

Home's worker uses a dedicated command to create a new hold. The command requires
a current worklist selection bound to Home's private work record, and checks
the reason against that turn's source, prepared input, or terminal extraction
diagnostic. A prior publication receipt cannot serve as hold evidence. For an
unverifiable quota or other stop, record the current failure and notify the
operator instead of asserting a new durable hold. Any existing hold remains
blocking. Publication and extraction decisions remain independent.

Acceptance covers a prior publication failure with no current evidence, a fresh
terminal extraction failure, a stale or mismatched work record, and a pre-existing
hold. Proof on Home requires the first scheduled batch after release to process
at least one pending turn and produce a valid source-bound receipt. SJM remains
held and receives no rollout in this operation.

## 25 November Home worklist and receipt repair

Keith approved the local-first worker change and Home deployment in Codex
thread `01a2030e-0540-7f53-8fff-9110f3557ab2`. An unmounted Home SMB share
is a publication constraint. Home's local worklist, extraction and staging
use the local sessions and private state, so they run first with a clear
extraction hold. The existing Home heartbeat starts with the worklist command.
Publication waits for the approved binding. Once it returns, a quiet cycle
can publish earlier reconciliation through the existing eligibility check.
Record a mount deferral only after a successful drain verifies zero
unreconciled deliveries. An unresolved delivery keeps its own backlog or
conflict reason.

The worklist command retains its latest result and the IDs selected so far
in one private evidence file per run, keyed by the original UTC start. The
next run keeps its own file so it cannot overwrite that evidence. The run receipt accounts for
those IDs and keeps independently
verified turn receipts even if publication fails. An unheld Home run without
the worklist evidence cannot report completion. New Home batch receipts use a
format that makes this check mandatory for runs started from 16:00 HKT
on 25 November (`2026-11-25T08:00:00Z`), before the first intended Home
run after rollout. Earlier receipts retain the status and
last-success history supported by their original format. Those timestamps
cannot attest the new worklist evidence. The 24 November 22:07 Home receipt
explicitly recorded no worklist observation and cannot attest completion.
A genuinely idle scan can complete, and a hold still blocks
extraction. Acceptance requires a scheduled
Home run to process a previously pending turn and leave a source-bound receipt.
The SJM hold and both existing schedules stay in place.

After approving and installing the reviewed Git commit on Home, prepend the
exact `scripts/prompts/automatic-memory-home-entry.md` to the existing Home
heartbeat with the Codex automation editor. Read back its registered prompt
and confirm the first shell step supplies `--run-started-at`. Verify the same
active job, target conversation and next schedule before watching that run.
Treat a scheduled run with a missing receipt as requiring attention.
The receipt validates the producer file in private state for consistency with
the original run start and command result. The Home status reader checks
that file against the digest in each v2 completed receipt. The existing single-owner native
batch uses one UTC start at microsecond precision; no interactive run shares that
state. Processes with the same local filesystem access share that trust
boundary.
The Home CLI serializes worklist scanning and evidenced receipt recording.
The existing kernel lock releases with the command. Its acquisition has a
30-second default timeout. The source start date is fixed by Keith's approval.
If scanning reaches its limit, report the unverified range and keep the
pending work for the next scheduled attempt.
Input preparation rechecks the hold so an operator stop after selection
does not open a selected turn to extraction.
Keep the small per-run evidence files with the private run audit history.
Each file stores the final worklist result and the bounded selected IDs.
Retain and back up each Home producer file for as long as its native run
receipt is kept. If status reports that the files no longer match, restore
the producer file whose embedded UTC start and digest match the receipt
from the same private-state backup and recheck `memory-status`. If the exact file is unavailable, retain the receipt and
its diagnostic. The per-turn receipts remain usable, and the next verified
batch can advance the success time. A backward clock change leaves the
original evidence in place so it can still be checked once the clock recovers.

The 16:08 HKT Home run on 25 November selected an archived chat after
receiving the parent Codex directory as its source root. It stopped before
extracting the selected turn. Home selection and backlog must both use the
registered `$HOME/.codex/sessions` root. With an older eligible archived turn
and a pending active-session turn, the next worklist must select only the
active-session turn and the backlog must count only that turn.
Native worklist and `memory-backlog --native-home` enforce that root at the
command boundary. Ordinary read-only audits keep their existing scope options.
An audit using a different Home root carries a separate scope diagnostic.
A legacy retained bundle can still point into the archive if the same turn is
also found in the current sessions. Leave those bytes intact. The worklist
reports the bounded affected IDs and continues to eligible work. The read-only
backlog counts active-session turns and validates their existing receipts.
A nonzero scope diagnostic requires a separate provenance decision for the
affected work ID. The scoped Home read-only audit found no such conflict
before rollout.
Compare each work-ID list length with its corresponding exception count to
identify a bounded sample. A mismatched binding can be reviewed separately
using the exact work ID, stored source path and current active copy. Keep
old files and receipts together during any approved repair.
