# Exact-name extraction correction

Status: local preparation, not approved for a real recovery run.

The delivered P1 consumer admits an uncompressed archive but its BSD tar
extraction route decomposes NFC filenames on this Home host. The independent
restore comparison correctly rejects that result. Renaming, normalizing or
omitting selected names would change the evidence and is not an acceptable fix.

The proposed adapter in `scripts/lib/recovery_exact_extract.py` calls the
unchanged P1 `inspect_archive` before creating output entries. It binds that
report to an already-held regular archive descriptor, its original signature,
its SHA-256 and the selection digest. It then uses Python's standard tar reader
and exclusive descriptor-relative file creation, without `extract` or
`extractall`. Relative names retain their codepoints. The second reader must
encounter exactly the admitted file and directory members. File size, bytes,
mode and integer nanosecond mtime bind to the P1 report. Directory modes bind
to the same report. Output ownership belongs to the local operator.

All admitted directories are created before file extraction, allowing archive
members to appear before their parent directory records. Traversal holds a
constant number of descriptors rather than one per selected directory. The
destination must already exist, be empty, have mode 0700 and have an owned
parent with mode 0700. Symlink ancestors and overlap with the selected source
are refused. The archive is checked again after inspection, and its signature
and bytes are checked after extraction. An archive replacement immediately
after actual P1 inspection fails before output entries are created.
Disjointness uses P1's NFD/casefold path identity semantics. Before creating
output entries the adapter repeats P1's capacity headroom check, eight times
the selected bytes plus 1 GiB. P1's pre-creation destination-profile admission
is still required independently by the owning operator.

The restored root intentionally takes the source root mode required by P1,
which may differ from its initial 0700 preparation mode. Its enclosing owned
parent remains 0700 and supplies the privacy boundary. The adapter does not
clamp admitted modes or change the metadata policy to pass a comparison.
P1's 64 MiB member bound is checked before creating output entries. Its
casefold/NFD identity admission rejects colliding selected names. The adapter
checks P1's member count and alias declaration against the bound selection.
A declared alias is excluded from archive members. After regular-tree
comparison, the separate existing P1 alias-control and restore phase must
restore that exact declared link. The adapter never creates arbitrary links.

The cooperative same-user assumption remains explicit: the adapter does not
defend its newly created directory entries against a malicious process running
as the operator. It does not establish writer ownership, source quietness,
transport, encryption, credential custody or an atomic system snapshot. Its
receipt says that restore comparison is still required. The caller must produce
an independent output hash ledger and run the unchanged P1 `compare_restore`.
Failed partials remain in the private destination and are never accepted.

The tests import the actual P1 contract from its frozen reviewed dependency,
commit `8b4629bfa354757f125682d1010bd66e82f1b478`, rather than fabricating its
return schema. An automatic fixture verifies the imported module's SHA-256
`7942016c89560444b822befb4b2f770578ee49b4a5e8d00913a5dadadee6cb44`
before every test. A later P1 rebind must update this pin through fresh review.
The Home activation receipt identifies commit
`56fd997d29694c1c23fc19cf0813b0e959a4c7e8`. Preparation on this worktree does
not imply a second activation. Synthetic tests cover Chinese, NFC, NFD, emoji,
spaces, empty directories, exact names, file bytes and metadata, unchanged
seven-field source signatures, output non-aliasing, actual P1 comparison,
file-before-parent ordering, prefixed names and trailing directory slashes,
300 sibling directories and 70 nested directories under a 64-descriptor limit,
admission failure before writes, unsafe destinations and archive replacement.
The BSD environment witness observed the rejected NFC name change on this
Darwin host; it accepts future exact-name behavior and is not adapter coverage.
Additional tests bind P1's oversized-file and folding-collision refusals, source
root mode restoration and the separate declared-alias phase. No real knowledge,
native assistant database, Mini state or NAS data is
used by these tests.
Literal, case-folded and normalization-folded source overlaps and insufficient
capacity are refused before creating output entries in dedicated tests.

Independent Kimi approval of these exact files is required. P1 must separately
bind and review the affected extraction phase before real use. This proposal
does not alter the frozen P1 package or its original approval.

## Admitted-field archive producer

A broader combined run exposed another independent frozen-route limitation:
BSD tar can emit `LIBARCHIVE.creationtime` for an ordinary directory, while
P1's parser refuses that unsupported PAX key. An invented directory with a
later mtime reproduces the refusal deterministically. The original P1/P2
producer, parser and receipts remain unchanged; that negative evidence is
retained. It is separate from the filename decomposition on extraction.

The separately proposed `recovery_exact_archive.py` emits uncompressed PAX
using only admitted directory and regular-file records. It uses the actual
bound P1 selection, exact relative codepoints and directory modes. File size,
mode, bytes and signed integer nanosecond mtime bind to R1. It reads each
source through held no-follow directory/file descriptors and checks the named
and held seven-field signatures before and after streaming. Source UID/GID,
directory mtime, birthtime, ACLs, xattrs and flags are not exported as new
fidelity promises. These are the existing explicit normalization limits that
the manager still must accept. No selected payload is omitted or renamed.

The foreground parent exclusively creates and holds the archive descriptor
before P1's supervised fork. The producer closes only its duplicate writer
and source descriptors. It measures the completed archive, invokes the actual
unchanged P1 inspector and binds the returned size/device/inode, digest,
selection and exact member count to the held output. Only JSON-safe evidence
returns. The source R2 hash/membership check and independent restored output
comparison remain mandatory. No successful source capture or seal is inferred
before R2, and the aggregate source window is not restarted per collection.

Tests cover both umasks, Unicode, empty files/directories, negative nanosecond
mtime, unchanged source signatures, same-byte ctime mutation refusal, fresh
output refusal and the original BSD creation-time negative. A full invented
1 GiB, 16384-file, 32768-member fixture passes through the new producer, actual
P1 inspection, exact-name extraction, actual restore comparison and R2. This
is preparation evidence, not a real source or NAS run. The new producer route
also needs exact independent Kimi approval and a separately reviewed P1
consumer binding before real use.
