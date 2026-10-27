# P2 SQLite recovery component

Preparation only. This component covers the existing Minnie memory database,
VPN meter database and Minnie's VPN delivery journal. It does not start an
application, restore a live path, deploy code or release a real snapshot.
The prospective school database remains uncovered. P5 remains held.

## Why a separate adapter

Minnie's MemoryStore constructor runs schema SQL and its close method checkpoints
WAL. The meter constructor changes permissions, enables WAL and creates schema.
Neither constructor is an inactive read-only validator. The meter's existing
backup uses SQLite's Online Backup API but can replace a same-day destination.
The P2 adapter uses that supported API directly, with a fresh private output
directory and no application imports or application schema changes.

SQLite documents the [Online Backup API](https://www.sqlite.org/backup.html)
for consistent database snapshots. A read transaction establishes this adapter's
database cutoff. This does not make two databases and a JSON journal atomic.

## Exact inputs and ownership

P1 owns the final controller, private destination placement, outer hard timeout,
quiet-window evidence, transport, custody and execution bindings. The manager
owns the execution release and the non-overlap interval with Minnie activation
or restart work. A changed deployed source binding holds the run.

The adapter requires explicit absolute paths, the memory or meter profile, a
trusted schema fingerprint, source device/inode identity, exact SQLite runtime,
code hashes and a cutoff identifier. There are no live-path defaults or CLI.
The schema fingerprint must come from trusted curated application DDL evaluated
in RAM, never learned from a candidate database to manufacture compatibility.

The memory profile admits conversations.sqlite3 and binds src/memory.mjs and
src/vpn.mjs. The meter profile admits usage.sqlite and binds meter.py. The
private execution record must bind the actual deployed paths and hashes,
configuration/database identity, backup compatibility and cutoff. Public-code
hashes are on-disk evidence, not proof of the bytes loaded by a running process.
Credentials, mailbox content and native assistant history are excluded.

For memory, the caller must also bind the existing vpn-broadcast.json journal
and its expected guild/channel identifiers privately. The meter profile accepts
no journal. P1 must coordinate the same stable cutoff across both profiles;
the helper cannot establish cross-application quietness from a supplied label.

## Snapshot and preservation

capture opens the existing source through SQLite URI mode=ro, establishes a
read transaction, fingerprints that transaction and backs it up through the same
connection. It compares the closed, standalone output with that baseline. It
does not copy live WAL or SHM files. SQLite can use normal shared-memory and
locking metadata, so source auxiliary files are not claimed byte-immutable.

The logical fingerprint includes the complete schema and all typed values in
every allowed table, with row multiplicity. This includes retained source IDs,
admission evidence, active and forgotten memories, candidates, review batches,
pending outbox operations, acknowledged receipts and sqlite_sequence. It also
includes meter meta, checkpoint identities, usage and health state. Fixed
aggregate counters and byte totals aid comparison. No record is re-admitted,
promoted, acknowledged or sent by this component.

Memory capture preserves the journal's original bytes and every field, including
delivery phase, nonce, message ID, attempts, absence checks and reconciliation
state. It validates the documented version/identity/ASCII-date/phase structure and
refuses malformed or changing input. It never constructs DailyBroadcast,
reconciles delivery, retries a report or assumes uncertain means unsent.

## Bounds and failure

The component rejects links, nonregular inputs, wrong ownership, unsafe or
overlapping paths, existing output roots, wrong bindings and unexpected schema.
The caller supplies an existing private parent directory. New run directories
are mode 0700; bundle files are mode 0600. The helper does not create or change
sync/index exclusions. P1 must observe and bind those conditions separately.

The data bounds are 512 MiB per database, 512 MiB combined source WAL/SHM,
500,000 rows, 1 MiB values and 256 MiB cumulative encoded logical data. Code
inputs are bounded to 512 KiB each, the journal to 65,536 bytes and the receipt
to 1 MiB. Exceeding a bound fails without truncation or partial acceptance.
Cooperative checks bound SQL, backup and Python work to 30 seconds. P1 must
also enforce a hard process deadline; this library is not a process controller.

The final receipt is written last, after successful validation and durable
output writes. It binds profile, cutoff, provenance, logical fingerprints,
physical output hashes and the journal. Errors expose generic codes, not family
rows or SQLite error values. A failed directory remains private and unaccepted.
The helper withdraws a receipt on failure. If filesystem failure also prevents
withdrawal, it reports that cleanup failure and returns no trusted receipt hash.
P1 must quarantine the whole failed run even if a receipt-shaped file remains.
There is no automatic payload cleanup or overwrite retry.

## Inactive verification

verify requires a separately trusted receipt hash and exact expected profile
and runtime. It checks exact bundle membership before opening the database,
rejecting WAL/SHM/journal sidecars. Source paths in the receipt are provenance
only and must never be opened by verification.

Cross-host verification requires P1's released extraction to create a fresh
bundle owned by the local verifying operator, with root mode 0700 and members
mode 0600. Capture uid is receipt provenance and may differ from the verifying
uid. There is no ownership override, privileged chown or acceptance of another
user's private files. A copy retaining foreign ownership is refused; P1 must
rehydrate a fresh copy through its reviewed owner-normalizing extraction method.

The standalone inactive database opens with mode=ro and immutable=1. Verification
checks integrity, schema, all logical state, physical hashes and journal bytes
against the trusted receipt. It does not migrate or repair data. It checks
bytes, mtime/ctime, permissions, identities and directory membership; access time
is excluded because ordinary reads may change it. It proves inactive equality,
not application startup, live delivery, independent NAS recovery or custody.

## Acceptance evidence

The required synthetic tests use invented rows and temporary directories only.
They must exercise committed WAL, a read-transaction cutoff, accepted/pending/
forgotten/acknowledged state, deleted high sequence values, meter retry and
counter state, and every delivery phase. Mutation tests must catch loss or change
of those fields, not merely compare a backup file with itself.

Negative checks cover wrong bindings/schema/runtime/profile, corrupt or truncated
copies, journal/schema drift, unsafe or occupied paths, sidecars, bounds and
timeouts. Verification must leave inactive bytes, write metadata and membership
unchanged and never revisit the original source. Test results and an exact Kimi
approval are required before this component is reported prepared and reviewed.
Target-Mini compatibility remains unverified until a separately released
synthetic probe on that exact runtime. No such probe or real capture is released
by this document.
