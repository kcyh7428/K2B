# P2-B manual recovery rehearsal preparation

Prepared on Home on 27 October 2026. This is the execution plan, not evidence
that a backup or restore ran. The manager released local preparation and
reviewed code delivery only. P1's verified target/access recipe and the
manager's separate execution release are still required before real source
backup, NAS writes or Mini snapshots. No automatic backup service, cadence,
retention configuration, purchase, new host role or migration is authorized.

## Release record required before execution

Bind a unique run ID, UTC start time, operator, exact reviewed code revision,
P1 evidence hash and manager release reference. Record the actual Home source
device, NAS target device/share, authenticated identity, private route, target
owner/ACL, available capacity and the supported archive/transfer/restore
commands P1 actually verified. The target must be recovery-only, versioned by
run ID and independent of today's Home source, outside any live sync folder.
Do not substitute an unverified share or reuse an existing run directory.

Record encryption/access protection and the actual custodian/recovery method
without recording keys, tokens or passwords. For an unencrypted manual test,
require explicit manager acceptance of restricted at-rest storage and its
limits; do not imply encryption. Record this single checkpoint's observation
time and age. No recurring age or retention policy is enabled by this plan.
Do not delete earlier points or failed test evidence as cleanup.

Fresh proposed Home test location, to be checked and bound in that release:
`~/.local/state/k2b/recovery-tests/<unique-run-id>/restored-knowledge`.
Its enclosing run directory must be mode 0700, outside the live vault, all
sync roots, native assistant history and active worker paths. Require it to
be absent before creating it and reject symlink ancestry. Restore into this
fresh area only. The restored collection root and selected parent directories
need their original permission bits; do not alter the live source to make a
test pass. Independent backup storage is the NAS device, not this second
Home directory. Reconfirm actual device separation in the execution receipt.

## Consistent approved-source checkpoint

1. Locally classify the exact approved current collection and immutable
   history. Preserve raw evidence, source/extraction/reconciliation bindings,
   admitted historical versions and provenance. Ordinary recall exclusions,
   including archives or conflict copies, are not blanket backup exclusions.
   Keep historical/conflicting bytes outside active recall after restoration.
   List every omitted class with its owner and reason. Path filters cannot
   certify that document contents contain no credentials or family material.
2. Arrange a finite coordinated writer/editor quiet window. Confirm no other
   session is editing selected files, direct editors are closed, and native
   extraction, staging, drain/publication, reconciliation, ordinary saves and
   index/log writers are idle. An extraction hold alone does not stop drain
   or publication. Inspect actual lock owners and supported writer controls;
   do not create guessed lock directories or kill a worker. A running batch
   or unsupported pause means wait for a new supervised window. Schedules,
   source windows, provider settings and writer authority stay unchanged.
3. If claiming Home/SJM convergence, require both relevant vault observations
   to show zero needed files/bytes and no errors plus matching approved
   manifests. Otherwise label the checkpoint Home-local and record SJM's
   unknown/pending state. Syncthing is not a distributed lock or backup.
4. Under the continuing quiet window, fix the explicit approved relative
   file list, root identities, collection membership, hashes, file modes,
   directory modes and source timestamps. Capture checkpoint time/provenance
   separately. New manifests cover selected directory permission bits. Any
   unknown/unclassified file, symlink, unsupported size, changing membership
   or hash aborts the checkpoint instead of silently skipping it.
5. Use P1's released supported archive recipe, preserving original bytes,
   mtimes, hierarchy and required metadata. Never split or rewrite immutable
   raw evidence to fit the verifier's 16 MiB file ceiling. Oversized files or
   empty directories need a separately recorded supported-tool verification
   path; until present, that class remains an explicit full-P2 gap.
6. Recheck the entire approved path list and manifest before releasing the
   quiet window. Reject if any selected bytes/modes or membership changed,
   private-state snapshot lost consistency, or a writer began during capture.
   Preserve the incomplete run as failed evidence and retry in a fresh run.
   A pair of equal scans alone is not an atomic or application-consistent
   snapshot; the supported quiet/snapshot procedure is part of the proof.

Source hashes bind their original bounded reads. The tool's final generation
recheck compares six stat fields, not a second payload read, and assumes
trustworthy kernel identity/ctime updates for ordinary writes. Stat-preserving
privileged or forged-metadata mutation is outside its guarantee. Its forged-stat
fixture explicitly demonstrates this boundary. Likewise the CLI success hash
binds the manifest's read-time bytes, with a final stat/path recheck, not a
second artifact read. Keep the real quiet/snapshot window and independent
read-back measurement; neither metadata checks nor passing synthetic tests
establish an atomic snapshot or current-byte equality under forged metadata.

## Separate private recovery-state manifest

Do not route private state through the shared-knowledge manifest API, relax
its path exclusions or send its payloads to DeepSeek/Kimi. Prepare a distinct
mode-0600 owner-reviewed record under the private run directory, stored in a
separate restricted recovery area only after its exact destination is released.
No private-state collection has been performed during this preparation.

The record must identify the owning host/root, run ID, cutoff and snapshot
method, explicitly approved relative paths, sizes and SHA-256 values,
file/directory modes and required ACL/ownership metadata, source and receipt
identities, accepted facts/revocations, pending operation IDs and delivery
acknowledgments. Include the matching content-addressed note before/after
copies, receipts, extraction/reconciliation records and staged/outbox state
needed to make those bindings recoverable. Preserve matching pairs; do not
claim a receipt alone restores its referenced extraction or bytes. Distinguish
accepted, pending, deferred, failed and ambiguous work without converting
unattempted operations to successes or failures.

The owning writer's quiet/snapshot mechanism must cover these private files
as well as the knowledge collection. Exclude credentials, cookies, keys,
native assistant sessions/account history, unrelated state and active lock or
socket files. Excluded credentials require existing private custody, not a
copy into the shared archive. An unreadable, inconsistent or unclassified
private-state class blocks full recovery acceptance and stays a named gap.
Restore private state into a separate inactive test root; never replace live
state or allow it to start a writer, replay queues or send notifications.

## Independent arrival, restoration and acceptance

After execution release, transfer to the exact fresh NAS recovery-only run
location using P1's recipe. Read back the archive and manifests from that
actual device and verify their exact byte hashes against the pre-transfer
values. Mounted-path naming, successful upload or a configured job is not
arrival proof. Retain original local hashes, remote observation time, device
identity, permission evidence and failures. No failed partial is backup proof.

Restore from those read-back bytes into the fresh Home test area. Verify
exact manifest artifact byte hash and canonical JSON identifier separately.
The canonical recipe preserves `files` array order. A reordered/regenerated
manifest has a different identity; compare the retained original artifact's
independently measured byte hash, not a regeneration's logical file set.
Run the read-only CLI verifier against the fixed approved manifest. P2-B
receipts must be CLI-produced and contain `manifest_artifact_checked: true`
plus both artifact and canonical hashes. Reject a parsed-dictionary API-only
receipt; it does not read the transferred artifact. Match the receipt's resolved
manifest/test-root paths and observed device to this run's released locations.
The coverage boolean is not authentication: compare `manifest_artifact_sha256`
to an independently measured SHA-256 of the actual NAS-transferred/read-back
manifest file and retain that observation plus the unedited receipt. Merely
editing an API result to claim true coverage proves nothing. Require new
manifests' `directory_modes_checked: true`, `file_modes_checked: true`,
`status: verified` and `directory_mode_gate: passed` for
the selected POSIX-mode claim. Legacy manifests explicitly report directory
coverage false and cannot pass that claim without a separate documented check.
Manually or through the released supported-tool recipe verify required
ownership, ACLs, extended attributes, mtimes, empty directories and unsupported
large evidence. A mode-bit pass does not verify those attributes. Record the
permission scope explicitly, including private enclosing directories.

Use the existing source-bound local recall path against the inactive restored
collection with explicit root overrides, not the live vault. Verify selected
real note-to-raw references and expected hash/source identities. Index rebuilding
is allowed only under a later exact execution recipe for this inactive root;
no model call, new ingestion, live writer or native-history scan is implied.
Record unknown/partial cases and broken links rather than inventing evidence.
Verify that private receipt/extraction/source pairs resolve within their
restored test roots and that pending/accepted/acknowledged inventories match
their cutoff. No queue is executed to prove that it exists.

Minnie and meter owners must use SQLite's supported online backup mechanism
on each operational database, preserving WAL-consistent committed state.
Their owning repository supplies the adapter, exact snapshot receipt, source
cutoff, SQLite integrity result and admitted/pending/acknowledgment or meter
continuity checks. Never copy a changing main DB alone, synchronize live DB
files, start workers from restored snapshots or put family rows in provider
inputs. This K2B package cannot invent those proofs or modify those adapters.

Record measured backup age and restore duration, exact source/destination
device identities, all archive/manifest hashes, every supported check and
its result, owner/reviewer and remaining gaps. All real execution requires
the manager's release; no part has run merely because this plan exists.

## Full-P2 gates still open

- P1's exact verified private NAS target, access/protection and supported
  manual commands, followed by manager execution release.
- Actual consistent approved collection plus immutable history, separate
  private-state checkpoint and NAS read-back on an independent device.
- Fresh restored bytes, references, scoped permissions/metadata and real
  source-bound recall acceptance, including unsupported classes explicitly.
- Both owner-provided SQLite online snapshots and inactive restore checks for
  accepted facts, pending operations, acknowledgments and meter continuity.
- Recovery custody, observed checkpoint age, measured restore result and
  manager acceptance of limitations. No live migration or P3 release follows
  automatically from a partial or synthetic success.
