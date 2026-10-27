# Bounded standard-tool verification supplement

Engineering method only. No source payload, private inventory, target identity,
account data or operational identifiers appear here. This supplements a future
manager-released one-time recovery recipe; it is not an execution release or
proof that real recovery ran. P1 owns orchestration and must incorporate and
review the final exact method and input bindings before use.

## Why a separate supported method

The delivered read-only K2B verifier deliberately limits file size and path
classes. Leave those protections unchanged. Original assets, legacy/root
knowledge, selected hidden history and separately admitted private state may
need broader file verification than that helper supports. Do not truncate,
rewrite, rename, drop or silently split immutable evidence to manufacture a
helper pass. A refused collection cannot be labeled helper-verified.

Use standard BSD tar/PAX for the existing approved file-list copy, and the
installed standard SHA-256 utility plus filesystem metadata observations for
every explicitly selected regular file. This adds no production verifier,
backup daemon or generic backup engine. The same method can verify files above
the helper limit within the separately released per-file/total resource budget.

## Required controls

- Fixed explicit source root, fresh exclusive inactive stage/restore roots and
  exact per-file list. Reject absolute/traversal/control-character paths,
  duplicate names and case/Unicode identity collisions. Check every ancestor
  with lstat; do not follow symlinks or admit special files or hard links.
- Preserve source bytes, original path names and admitted history. Credentials
  and native assistant account/session exports remain outside these archives.
  Application configuration/cache and purpose-bound private state have separate
  disposition and approval. Never archive an entire state or home directory.
- Keep the released quiet/snapshot window, source membership checks, pre/post
  device/inode/size/mtime/ctime/mode checks and final remeasurement. No atomic
  snapshot or forged-stat resistance is claimed by ordinary file reads.
- Set explicit per-file/total bytes, count, free-space and command deadlines
  before the run. The retained small fixture uses a 17,735,133-byte file. Do not infer unlimited
  capacity. The released recipe must reject a selection outside its bounds.
- P1's archive-inspection implementation must validate exact member equality,
  relative path safety, permitted regular/directory types, no duplicates or
  unexpected links and fixed root layout before any real extraction.

## Concrete envelope and operation order

The proposed envelope is at most 64 MiB per regular file, 1 GiB total selected
payload bytes, at least one and at most 16,384 regular files and 32,768 archive members including parent
directories. A synthetic fixture exercised these ceilings. This is not permission
to widen an existing released recipe's smaller bounds. A changed count/root
policy requires P1's fresh exact-scope review and manager release. Do not silently
split, shrink or omit a class to fit a bound. Measure all selected sizes/counts
with lstat before archive creation and reject any excess or unknown value.
Reject zero selected files before the window, and reject any empty or incomplete
NUL input before each hash phase. Never dispatch xargs on an empty stream.
Reject setuid, setgid and sticky bits on selected files/parents before the
window. They are outside this unprivileged POSIX-mode contract. Cover every
remaining selected permission-bit combination in the profile calibration.
This supplement permits archive creation and inactive extraction only on local
Home APFS volumes. NAS stores the encrypted archive under P1's separate contract;
this is not a raw NAS/SMB extraction method. Bind a statvfs capacity observation
to the actual archive directory and each stage/restore target volume, requiring
eight times selected payload bytes plus 1 GiB available on each before its step.
Recheck volume identity and capacity immediately before creation/extraction.
P1 separately retains its released NAS capacity gate. Unknown capacity or a
changed volume fails. No external/coarser filesystem is silently admitted.
Before the window, test integer nanosecond mtime preservation with an invented
fixture in each intended inactive target filesystem and require exact equality.
Failure holds the method; no timestamp downgrade is authorized.

Bound each UTF-8 path component to 240 bytes and each full absolute path to
900 bytes, with at most 16 MiB cumulative selected path bytes. Validate actual
source/target NAME_MAX and PATH_MAX via pathconf against the exact selection,
including the full inactive prefix. Unknown or insufficient limits fail before
the source window. A changed filesystem requires fresh review and release.

The supervised source capture window is at most 600 monotonic seconds. All
editors, source-sync writers, native batches, staging, reconciliation and
publication must be confirmed idle through P1's existing approved controls and
locks, without changing schedules or inventing new lock paths. Two identical
membership/stat scans two seconds apart are additional observations, never a
substitute for writer quiescence. If any writer/control is unknown, active, or
starts during the window, abort. There is no atomic-snapshot promise.

Within that window, use this exact order:

1. Validate root/list/entry types and resource envelope, confirm the released
   quiescence controls, and capture the two spaced membership/stat observations.
2. Retain R1: one digest and exact stat signature for every selected source file,
   parent modes and the exact allowed alias declaration. Every file must have
   st_nlink equal to one and distinct selected device/inode identity. Recheck
   link counts/types immediately before tar. A detected hard link fails the run; never
   break/dereference it or modify a source to force success. Link creation changes
   nlink/ctime and persistent changes are detected by R2. The precheck-to-read
   interval still relies on the cooperative-writer condition; no atomic claim.
3. Before archive creation, P1 must have exclusively created a fresh run and
   its control directory with mode 0700, checked their ancestors and bound their
   actual volume. Refuse any pre-existing run, control directory or archive path,
   including a symlink. The archive path must be absent immediately before tar;
   after tar require a regular operator-owned file with link count one.
   Under the cooperative-writer model, only this run's operator writes there.
   Create the transfer archive from that exact list in this control directory. Retain its SHA-256 and stat identity, and bind the member
   inspection artifact to that digest. Inspection must pass: exact files/parents,
   safe names/types, no links/duplicates/unexpected entries.
4. Create each run/stage/restore directory exclusively, refusing existing paths.
   Check every existing ancestor via lstat, disjoint real roots, owner and mode;
   use the enclosing fresh mode-0700 run from step 3. Place it in P1's explicitly
   released private local recovery area outside vaults, synced folders, indexed
   folders and application working directories. Confirm no other task/process
   uses this run-owned path and retain that destination exclusion throughout
   extraction. An unknown or concurrent destination writer aborts the run.
   P1 must record the actual configured Syncthing folder roots and Spotlight
   scope for this location immediately before the window, before extraction and
   after verification. Compare normalized ancestors, not path substrings. Any
   configured parent, indexed location, unavailable observation or changed scope
   holds the run for fresh review. The manager must coordinate no sync/index
   configuration changes during the window; this is an input obligation, not
   something output digests prove. No runtime exclusion/configuration change is
   permitted by this component.
   This is a supervised cooperative-writer model, not protection from malicious
   same-user processes; mode 0700 alone cannot provide that protection.
5. After archive inspection and immediately before tar reads, require the target
   to remain the same empty directory with sufficient free bytes. Rehash and
   restat the archive, requiring equality with the inspected digest/identity.
   Never chmod existing parents or overwrite a reused root.
6. Extract with the bounded command below. No other writer may access the
   private control or destination run while extraction or verification proceeds.
7. Rehash/restat the archive after extraction and require unchanged digest and
   identity. Independently enumerate every restored file and directory, reject
   links/specials/extras, and compare all parent modes including empty parents.
   Obtain exactly one digest and metadata observation per selected output file.
   Compare every digest to R1 plus sizes, modes, integer file mtime_ns, local
   restored owner and no source inode aliasing. Directory mtimes, birthtime,
   ACLs and xattrs are outside this fidelity claim and require explicit manager
   acceptance, like the other metadata normalization below. The archive checks
   narrow substitution risk; they do not prove atomic reads or resist malicious
   same-user writers forging observations. Output comparisons establish equality
   of the stated properties under the cooperative-writer preconditions.
8. Retain R2: remeasure source digests, signatures, membership, link counts and
   parent modes. Any difference from R1 or the fixed alias declaration fails
   the entire run. All steps must finish within the 600-second capture window;
   otherwise retain an unaccepted partial and restart only in a fresh released
   window. Later independent transport/custody work uses P1's separate bounded
   run deadline, never extends this source window silently.

Each tar create/extract and archive-hash command has a 60-second deadline.
Each of the three complete batched file-hash phases has its own 120-second
deadline, shared by all xargs child invocations in that phase. These allowances
are ceilings, not a promise that their sum fits the window. Before each command,
set its effective timeout to min(fixed allowance, remaining monotonic window).
At 600-second window expiry, terminate/reap the active process group regardless
of its nominal allowance; no subsequent step may start.
On timeout P1 must terminate and reap the entire command process group, including
xargs children, before recording failure. It must test this behavior.
Every R1/R2 stat signature is exactly st_dev, st_ino, st_size, st_mtime_ns,
st_ctime_ns, permission bits and st_nlink. Keep ctime to detect replacement or
metadata mutation during capture; even an apparently harmless xattr change is
source drift and aborts. Record the differing field and phase privately, without
payload. Atime is excluded because hashing itself may change it. Parent checks
cover membership, type and permission bits only. P1's source-quietness contract
must address Time Machine, Spotlight metadata work, antivirus and other actual
OS/background writers for the selected sources; if that cannot be established,
this ordinary-read method is unavailable, not a reason to omit ctime.

P1 must timestamp the start/end of every numbered step and check the same
monotonic deadline inside scans, parsing, enumeration and comparison. A stalled
in-process step must be bounded by P1's supervised worker timeout, not only
subprocess timeouts. Calibration must time the complete steps, not just tar/hash.
Before each next step, require remaining time at least twice its measured
calibration duration plus five seconds; otherwise abort before starting it.

Before admitting the method, P1 must run the maximum-envelope invented fixture
on the exact released Home host and volumes with these fixed deadlines, plus
an invented fixture matching or dominating the selected per-file byte sizes,
file/member counts and UTF-8 component/absolute-path-byte distribution. Use only
size/length metadata to construct invented names and contents; no real names or
payload enter that fixture. Retain its exact profile binding privately. Require
the complete profile rehearsal to finish within 300 seconds and each step within
half its fixed allowance, leaving at least 2x measured headroom. Reject a real
selection exceeding any calibrated dimension before the window. If it
misses any deadline, hold execution and obtain a fresh exact-scope review and
manager release for a revised envelope or deadline. Never silently reduce
coverage, split the collection or extend this 600-second window. If the real
selection cannot fit the reviewed envelope, report it uncovered until revised.
Preflight and record the installed tar, xargs, shasum and Perl availability,
versions and synthetic behavior/digest known-answer check. A missing dependency
or version/behavior change holds the recipe for fresh review. No fallback tool
or installation is authorized by this method.
P1 must capture argv, exit code, stdout and stderr privately and fail the run
on any nonzero exit, nonempty stderr, missing/malformed digest or deadline.
Require exactly one digest for each expected fixed path in every R1/output/R2
ledger. Partial tool completion is not partial acceptance. The final recipe
must implement/test these guards before release; the commands below must never
be run standalone as a substitute for that orchestration.

Both NUL list files, digest/stat ledgers, timing/profile evidence and archive
inspection artifacts must be freshly created mode 0600 inside the fresh mode-0700
control directory, using exclusive creation and refusing links/existing paths.
They inherit the same non-synced/non-indexed placement, encrypted control custody
and release-bound retention policy. No list belongs in shared temporary storage.
Failed run artifacts, list files and digest ledgers remain private and labeled
unaccepted.
Do not delete them automatically, retry over the same directories, remove live
originals, or proceed with the remaining files after a failed check. Any later
cleanup needs an exact run-owned-target release.

## Exact existing commands

Run argv as arrays with explicit literal paths, never interpolate a filename
into shell text. The list file is NUL-delimited and contains only validated
selected regular files and necessary parent directories, without recursion.
The following variable names describe those already-bound absolute paths.

```sh
COPYFILE_DISABLE=1 LC_ALL=C /usr/bin/tar --format=pax --no-acls --no-xattrs --no-fflags -cpf "$archive" -C "$source_root" --no-recursion --null -T "$archive_entries_nul"
COPYFILE_DISABLE=1 LC_ALL=C /usr/bin/tar -xpf "$archive" -C "$fresh_inactive_root" --preserve-permissions --no-same-owner --no-acls --no-xattrs --no-mac-metadata --no-fflags
LC_ALL=C /usr/bin/xargs -0 -n 128 -s 32768 /usr/bin/shasum -a 256 -- < "$validated_absolute_file_paths_nul"
LC_ALL=C /usr/bin/shasum -a 256 -- "$archive"
```

Build each command environment explicitly: set LC_ALL=C and COPYFILE_DISABLE=1;
remove PERL5OPT, PERL5LIB and PERLLIB entirely before tool preflight and execution.
Record only their absent/present-and-removed status, never arbitrary environment
values. Use the same environment for synthetic calibration and the real recipe.
`LC_ALL=C` is per command, not a global setting. For the nonempty selected
file list, feed validated absolute paths as NUL-delimited stdin to xargs.
Do not use parallel execution. Bound each invocation to 128 paths and 32,768
argument bytes. P1 must preflight the actual environment and ARG_MAX including
pointer/environment overhead, and fail if that envelope is unsupported.
Reject backslashes and control characters in selected names before this method.
Parse each shasum line as a 64-character lowercase hex digest, its fixed text
separator and one exact expected absolute path. Require each selected path once,
no unknown or duplicate path and an exact count. Do not depend on output order,
interpret filename text as commands, or use untrusted checksum-file execution.
Any mismatch fails the entire phase. Independently compare exact membership,
byte sizes, POSIX bits, integer mtime_ns, local restored owner and device/inode
non-aliasing. Capture required parent-directory bits and preserve the selected
collection root mode inside a mode-0700 enclosing run. The shown shell syntax
illustrates stdin binding; production orchestration uses argv and pipe input.

These tar flags intentionally suppress inherited ownership, ACLs, xattrs,
macOS metadata and file flags. They do not demonstrate original ACL/xattr
recovery. The manager must release that normalization policy or require a
separate supported metadata-preservation method; never claim full metadata
fidelity from POSIX bits alone. No live source permissions are changed.

## Explicit symlink exception remains a release dependency

For one known relative alias, record lstat metadata and exact readlink bytes
without dereferencing it. Validate its target stays within the approved root
through regular non-symlink ancestors, and include the target as a selected
regular file. Keep the link declaration in encrypted control metadata.
The current archive contract rejects symlink members. Therefore reconstruction
of that exact alias in an inactive restore requires P1's explicitly reviewed
bounded reconstruction step, or the link remains an uncovered recovery item.
Do not broaden archive extraction to arbitrary symlinks or use tar dereference.
If reconstruction is later released, the operator must recover the encrypted
control declaration through P1's private custody procedure and compare its exact
bytes to the retained trusted pre-transfer control digest. Revalidate the
source readlink declaration at R1/R2, and immediately before inactive
reconstruction require the exact recorded relative link path/target, absent
link entry, fresh regular target and non-symlink ancestry within the released
inactive root. Refuse any drift or unknown declaration. After creation verify
lstat/readlink and included-target digest against that same trusted declaration.
P1 must review/test this bounded step; it is not provided by this supplement.

## Synthetic evidence and acceptance boundary

The retained small test fixture exercises exactly a 17,735,133-byte binary,
assets, root/legacy notes, hidden version paths and invented private-like files.
The opt-in upper-envelope test is reproducible from the same test module with
K2B_RUN_UPPER_ENVELOPE=1. It constructs 16,384 regular files, 32,768 members and
exactly 1 GiB payload, including a 64 MiB and a 20 MiB file. It measures the
complete synthetic window and fails above 300 seconds, any batched hash phase
above 60 seconds, or tar/archive-hash commands above 30 seconds. Timings depend
on the host; no fixed throughput is promised. This supports command behavior
at these synthetic dimensions only, not final target calibration or quietness.

Both tests use invented data only. Output equality checks bytes, file mtimes,
POSIX bits, local ownership and distinct source/output inodes. The retained
negative tests exercise malformed/incomplete ledgers, occupied control paths,
archive changes, ctime drift and process-group timeout cleanup. Path and archive
INPUT ADMISSION safety is a separate P1 obligation: the tests inspect known
invented names/types but do not implement the adversarial admission layer.
The test-local budget arithmetic is illustrative, not production enforcement.
P1's final implementation must test all stated controls before release.
Symlink reconstruction remains outside this component. No actual private class
is admitted by these synthetic tests. Fixture payload is temporary.

This same-disk test proves command behavior only. It establishes neither a
real independent backup nor actual permissions, custody, consistency, SQLite
integrity or queue/admission continuity. Actual NAS read-back and the independent
measurements, encryption/key recovery, per-class coverage and receipt bindings
remain mandatory under the manager's exact future execution release.
Do not reuse an earlier helper-only method verdict for this broader coverage.
