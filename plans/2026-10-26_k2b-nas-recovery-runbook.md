# P2-A recovery preparation

This package prepares read-only verification and synthetic rehearsals. It
does not establish production recovery, activate backup configuration or
release P2-B, P3 or a change of storage authority. Keith deferred backup setup.
The manager owns the shared delivery tracker and later package releases.

## Existing helpers and reuse

`scripts/vault-note-write.py:durable_private_copy` preserves content-addressed
before/after bytes for ordinary note saves, with private receipts. Its local
copies are useful recovery inputs, not independent collection backups. Do not
invoke the note writer to take a collection backup.

`scripts/lib/k2b_context.py:_sha256_file_bounded` supplies the existing bounded
streaming SHA-256 pattern. The recovery helper reuses that pattern with
pre/post metadata checks and a second collection pass. Importing the large
private recall module solely for hashing would couple unrelated behavior.
Existing automatic-memory manifest and receipt routines retain their own
source windows and authority. No collection restore verifier was found in
the inspected scripts. No existing helper or provider infrastructure changes.

## Required source classes for later owner approval

1. Approved current wiki, immutable raw evidence, knowledge metadata and
   approved historical evidence, including archive material. Ordinary recall
   exclusions are not automatically recovery exclusions. Preserve provenance
   and original versions. Manifest approval is a file selection decision, not
   permission inferred from a directory name.
2. Separately protected private recovery state: note before/after copies,
   receipts, extraction/reconciliation bindings, staged operations and delivery
   state. The shared-knowledge verifier deliberately excludes these classes.
   Their later private manifest needs owner review and restricted storage.
3. Consistent Minnie and VPN-meter operational SQLite snapshots, made by their
   owners using SQLite online backup. Preserve admitted IDs, approval/source
   metadata, pending outboxes, acknowledgments, retention state and meter
   continuity. Never copy a changing main DB without accounting for its WAL.
4. Necessary nonsecret configuration and approved host adaptation records.
   Credentials, keys, cookies and native assistant account/session history
   stay outside the shared knowledge archive and coding-provider packets.

No live payloads or databases were collected for this inventory. Path filters
cannot detect secrets embedded in an otherwise approved Markdown document.
The owner must review the exact approved collection before later real use.

## Tool contract and rehearsal

The CLI requires explicit roots and individual approved relative file paths.
It has no default vault path, recursive selection, copy command, output-file
option or restore writer. It only prints a manifest or checks an already
restored collection. Thus it cannot overwrite a live restore target. Shell
redirection is outside this protection and must never point to a live file.

The ceiling is 16 MiB per file to bound this small knowledge verifier. Larger
evidence requires an owner-reviewed limit change or a separately verified
supported recovery tool, never silent exclusion or splitting immutable raw
evidence. The restored area must contain exactly the selected files and their
parent directories, with no extra files or empty directories. This verifies
an explicitly selected collection, not an arbitrary whole-vault filesystem
clone. Preserve any required empty directories through the later owner recipe.
Disk filenames are compared in NFC Unicode form and case-insensitive name
collisions fail closed; original disk spellings are used for payload reads.

Use `python3 scripts/k2b-recovery.py --help` for the exact CLI. Run the
synthetic regression suite with Python 3.12 and pytest:

```bash
python3 -m pytest tests/test_k2b_recovery.py -q
```

All fixtures belong in pytest temporary directories. The fixture creates
linked synthetic note/evidence bytes, copies them only within test storage,
then checks hashes, permissions and the known relative reference. It also
checks changing sources, missing/altered bytes, unsafe paths, symlinks,
collisions and excluded private state. Temporary local copies demonstrate
verification behavior only, never separate-device recovery.

Manifests contain paths, byte counts, hashes and original metadata, not file
text. Store later real manifests privately with source provenance. A JSON
manifest is trusted local evidence, not a cryptographic signature. Checks
detect observable changes during reads, not an atomic filesystem snapshot or
protection from a hostile process swapping paths between system calls. Later
real recovery requires quiescent sources or supported consistent snapshots.
The final file open refuses symlinks and checks descriptor metadata. Ancestor
swaps remain outside the concurrency guarantee. Verification checks the frozen
manifest, not the latest live source: output binds `manifest_sha256` and states
`source_freshness: not_checked`. P2-B must separately bind checkpoint time,
provenance and the selected acceptable age. A successful check of an old
manifest is not evidence of a current backup.
Output also states `mtime_checked: false` and `independence_checked: false`.
Original mtimes are recorded for later recovery, not checked by this byte/mode
verifier. It does not detect hard-link aliasing to source files, prove device
separation or check ownership, ACLs or extended attributes. P2-B must verify
these separately through its selected supported restore procedure. The CLI
reads its trusted manifest with a no-follow descriptor and before/after
identity checks; this still does not authenticate a replaced manifest or
defend every ancestor-path race. A caller must retain the trusted manifest
identity and provenance separately.
Hash equality preserves reference text; this tool does not prove semantic
completeness or resolve every wiki reference. P2-B retains representative
source-backed recall and reference acceptance on restored real content.

## Deferred P2-B recipe and gates

Before any real backup, the manager must release the exact destination,
capacity/access, acceptable age, retained versions, encryption/recovery-key
custody, cadence and affected-host permissions. No schedule, purchase or
backup activation is part of this package.

The reviewed P1 recommendation uses known storage: for today's Home-owned
collection, create a recovery-only checkpoint on the NAS, then restore to a
separate Home/SJM test area. A second Home directory is not independent of
today's Home source. Once NAS owns the collection, retain independently
versioned private Home archives, distinct from Drive replicas. Mini online
SQLite snapshots need independent Home storage and owner-provided integrity,
accepted/pending/acknowledgment and meter checks. SJM is an optional additional
copy only after separately selected and verified. Available laptop space does
not prove unattended availability or an off-site copy.

P1 proposed, but did not approve or enable, a maximum age of 24 hours, 30 daily
restore points and manual release checkpoints. Key recovery must name the
actual private custodian and demonstrate decryption without copying keys into
Git, the vault or provider input. No actual custody method is established here.

After release: inventory and freeze the approved source consistently, create
the independent versioned backup through supported owner tools, retain
manifest/provenance and private-state bindings, restore onto selected separate
test storage, then verify bytes, permissions, references, source-backed reads
and operational queues without starting live workers. Record device identity,
backup age, measured restore time, missing classes and exact limitations.
Missing or changed bytes, unavailable destination, unknown custody, queue
inconsistency or private-state leakage stops recovery acceptance. Preserve
failed evidence and original sources. A configured job, RAID, same-disk copy
or successful synthetic suite cannot pass P2-B.
