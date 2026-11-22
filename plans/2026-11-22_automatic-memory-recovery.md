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
