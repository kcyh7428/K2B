# Foreground recovery control proposal

Status: preparation only. Neither this proposal nor its component acquires live
resources during engineering. Real execution remains unreleased.

## Observed structural limits

P1's retained writer-resource graph has SHA-256
`c55171c9617c39ea4fbc5712bde6705facee5af3766c39ba8c3fa94992cfaa33`.
It identifies private extraction, worklist, outbox, inbox, memory, discovery,
append and whole-native-run bookkeeping beyond the five baseline witnesses.
Those five witnesses cannot establish a complete cutoff. Both hosts have
active heartbeat registrations, but the observed registrations do not bind all
actual launch working directories, explicit CLI roots or named environment
overrides. Diagnostic shell defaults are not substitute evidence.

Native per-step locks do not cover the complete native-run receipt interval.
Outbox and inbox operations do not establish one atomic cross-host cutoff.
Syncthing can replace selected files and membership without participating in
application locks. Direct editors and actual selected-source metadata writers
also need identified ownership and a supported bounded non-overlap interval.
No new global lock can manufacture cooperation from these owners.

The P1 prose currently groups Time Machine, Spotlight and antivirus with
writers. The proposed amendment classifies actual selected-source effects:
control every actor capable of changing selected bytes, any of the seven
source-signature fields, directory membership or modes, the declared alias,
or selected SQLite/journal state. A proven reader that changes only excluded
atime or data outside the selection need not acquire an invented source lock.
An application name alone proves nothing. Unknown selected-source effects
remain a hold. This proposal does not drop ctime or any privacy gate.

No reader exemption applies without a retained per-host, per-actor proof record
bound to the immutable run record. It identifies the actual physical actor,
code/tool version and nonsecret configuration, exact selected roots, supported
read-only operating mode or supported exclusion, owner and bounded interval.
It covers bytes, directory membership/modes, alias, SQLite-adjacent state and
the exact seven fields: device, inode, size, mode, link count, integer mtime_ns
and ctime_ns. The record must bind that same field list in every participating
P1/P2/R2 gate on both hosts. Atime is excluded; ownership is checked separately.
Xattr or file-flag changes that alter ctime are source effects, even though the
restore fidelity policy excludes those attributes. Observed equal scans alone
are insufficient to prove read-only behavior. Configuration/interval/effect
observations corroborate the supported behavior; any unsupported or unobserved
effect class remains a hold. Home evidence never exempts an SJM actor, or the
reverse. No general OS application exemption is activated by this proposal.

## Concrete cooperative holder

`scripts/lib/recovery_resource_holder.py` implements only the existing atomic
mkdir and descriptor-held exclusive flock primitives. A foreground owner must
first bind each exact canonical path and protocol from actual nonsecret launch
evidence and the admitted data owner set. The holder supplies no defaults.
Before binding any real resource, the run record must retain per-path actual
Syncthing folder/ignore and Spotlight-scope evidence and the permitted effect.
New lock artifacts may only be created where that evidence proves their scope
is permitted and nonsynchronized/nonindexed. An existing selected append
target is a distinct case: it must already exist, stays in its actual owner
namespace, and takes only the existing kernel flock without creating content
or a replacement lock path. Its normal replication and metadata actors still
need the selected-writer controls. Existing owner lock paths inside a source
tree require explicit actual ignore/control evidence; absent evidence holds
that resource rather than relocating its lock. The component supplies no
scope proof, and no real resource is bound or acquired by this preparation.
Creation additionally requires a caller-held `ParentAnchor`, opened against
the actual permitted parent identity and linked to its scope-receipt digest.
`open_parent` checks held and named device/inode/owner/mode and rejects symlink
ancestry. Every mkdir and missing flock creation uses that descriptor and one
relative leaf. Absolute-path creation without this anchor is refused. The
anchor must remain open, and its physical identity is checked through ownership
verification and release. The receipt digest is a private provenance link,
not a substitute for actual scope evidence or a caller approval flag. Existing
append targets still cannot be created. No global readiness is inferred by
opening a directory descriptor.
It acquires all resources without waiting in the supplied confirmed order;
contention rolls back owned resources. Actual source-owner ordering must be
confirmed before use. It creates no parent directories or owner marker files.
Append locks use the existing target descriptor and cannot create a missing
target. Bounded resource labels allow multiple exact append targets or memory
state files under the same recognized role; each has a unique name and path.
Flock paths are never unlinked. Pre-existing mkdir locks are never
adopted or removed.

Fresh evidence verifies live held descriptors against named device/inode,
owner, file type and mode, and checks mkdir resources remain empty. It reports
the actual PID, UID, hostname and descriptors. A fork cannot claim or release
the parent's ownership. Release is independent of deadline checks so an
expired window still closes handles. Cleanup uncertainty is an error and
retains unknown or changed paths. The component never reports global idleness,
native-run completion, source quietness, privacy or snapshot success.

The controller start identity comes from a bounded actual `/bin/ps` query of
its own PID, without reading other process arguments or environments. Bind that
system tool in the private runtime record. Live records include all seven
fields and an observation time, anchored to the held descriptor. Private
`postmortem()` observations survive handle closure, including original owned
identities, cleanup reasons and the separate post-mkdir lstat when opening a
directory descriptor failed. Historical observations are not live ownership.
The acquisition-free `inspect_busy` helper retains bounded lstat/listdir
evidence and always reports ownership unknown; it never resolves or removes
debris. Path uniqueness uses NFD/casefold identity semantics.

After any ambiguous cleanup, retain the private attempt's resource paths,
owned identities, original acquisition reason and cleanup reasons before any
retry. The operator must inspect actual lstat/listdir and current owning
process evidence and resolve ownership manually. A later busy result may be
retained debris; it is not proof of a competitor. No automatic removal or
retry is permitted. No marker is written inside an existing owner's lock
directory. `terminal_state` distinguishes clean from ambiguous release, and
repeating release cannot turn ambiguity into success. Even an unusual umask
that prevents creating mode 0700 is an explicit unresolved attempt, not a
reason to adopt or silently remove an unverified directory.

The holder is single-process and cooperative. It does not protect against a
malicious same-user process replacing directory entries between checks. It
does not implement SJM transport or a distributed lock, stop native tasks,
pause synchronization, change indexing, alter schedules or contact a host.
It is one building block for the bounded one-time operator, not a released
writer controller or a complete recovery executor.

## Required one-time binding and claim

The final run must bind manager-owned completion and non-overlap of the whole
native/manual workflow intervals on both knowledge hosts, actual foreground
invocation roots and overrides, the admitted owner/resource graph, and any
supported synchronization/direct-editor/metadata-writer controls. Held resource
evidence is monitored throughout the capture interval. Loss, replacement,
unclassified effects or new activity aborts the attempt. The run must preserve
all selected source signatures, membership and pending-delivery provenance.
R1/R2 hashes and equal scans detect changes but do not replace ownership.

The supported claim is recovery of the exact approved collection during its
observed and controlled bounded interval, with independent restored-byte,
metadata and pending-state checks. It is not an atomic snapshot of the whole
system or one atomic cross-host instant. Existing stage/restore exclusions,
credential custody, actual tool calibration, real fetched ciphertext and all
P1 privacy/integrity gates remain required. Missing evidence means hold; it
does not authorize excluding an inconvenient selected lane or reducing scope
to Home alone.

P1's existing Home `probe_sjm` builds a pinned bounded Python observation
payload and sends it through the existing SSH route. It does not import an
observer from the SJM checkout. Its transient flock availability observation
is separate from a held foreground source controller. No new SJM installation
or invocation is part of this preparation.

This proposal requires independent Kimi approval of its exact wording and
component. P1 must bind and independently review any affected consumer or
contract amendment before real use. Manager admission must resolve every
source disposition, runtime calibration input and private custody item. No
caller-provided approval or quietness Boolean can replace this evidence.
