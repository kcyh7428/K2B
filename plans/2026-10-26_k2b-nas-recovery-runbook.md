# P2 recovery verification tooling

This package prepares read-only verification and synthetic rehearsals. It
does not establish production recovery, activate backup configuration or
release real P2-B execution, P3 or a change of storage authority. On 27 October,
the manager released manual-rehearsal preparation and reviewed tooling delivery.
Real source backups, NAS writes and Mini snapshots still wait for its verified
target/access/recipe release. Automatic backup setup remains deferred.
The manager owns the shared delivery tracker and later package releases.
The exact preparation and execution gates are in
`plans/2026-10-27_k2b-nas-manual-recovery-rehearsal.md`.

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
Manifest artifact reads are capped at 64 MiB before parsing. An oversized or
unreadable artifact cannot provide a verified artifact identifier; retain its
external bounded acquisition receipt instead of claiming a complete read.
The generator checks its pretty-printed UTF-8 JSON plus final newline against
the same 64 MiB ceiling and refuses an oversized manifest before emission.
Very large collections require a separately recorded supported-tool path.
Never drop approved files silently or treat an oversized manifest as verified.

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
Payload hashes bind the original bounded read. Generation's final recheck is
stat-level: device, inode, size, mtime, ctime and mode, not a second payload
read. It assumes trustworthy kernel metadata and ordinary unprivileged writes
updating ctime. A privileged/forged stat-preserving byte mutation is outside
this guarantee; the synthetic forged-stat test demonstrates that limitation,
not protection against it. Observable-write tests prove stat-drift detection,
not byte equality against an attacker who can forge all six fields.
The final file open refuses symlinks and checks descriptor metadata. Ancestor
swaps remain outside the concurrency guarantee. Verification checks the frozen
manifest, not the latest live source: output binds a canonical identifier and states
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
On success as well as failure, the CLI artifact hash binds bytes at read time.
The final signature/path check assumes the same kernel-metadata integrity; it
does not perform a second artifact read or certify the bytes forever current.
Retain the independent transferred-file measurement and the supervised quiet
window. No stat-only success receipt replaces that real recovery evidence.

### Reproducible identifiers and permission coverage

`canonical_manifest_sha256` is SHA-256 of the parsed JSON serialized by
`json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")`.
Python's default ASCII escaping is part of this exact recipe. JSON indentation,
key order and trailing whitespace do not change that identifier. Array order
is preserved: reordering `files` changes the identifier even if verification
finds the same files. Do not compare regenerated manifests as the same artifact;
retain and compare the original transferred manifest's measured byte hash.
The old
`manifest_sha256` output is retained as a deprecated alias for this canonical
value, not the hash of a manifest file. The CLI additionally returns
`manifest_artifact_sha256`, SHA-256 of the exact manifest bytes read, including
whitespace and newlines. Compare that field to `hashlib.sha256(path.read_bytes())`
or a standard file SHA-256 command when checking transferred artifact identity.
Retain both values with the checkpoint's trusted provenance.

Newly generated schema-1 manifests always include `directory_modes`: the collection root
under `.` and every selected file's ancestor directory under its canonical
relative name. Restore verification requires the exact directory set and checks
their POSIX mode bits before and after reading payloads. Its output reports
`file_modes_checked: true` and `directory_modes_checked: true`. Legacy schema-1
manifests remain readable, with `directory_modes_checked: false`; they do not
pass a directory-permission claim without a separate recorded check. Restore
the original root/directory bits inside a private enclosing test directory.
Do not broaden original source permissions to make a rehearsal pass.
The CLI refuses legacy input by default. Its explicit
`--allow-legacy-no-directory-modes` option permits the older byte/file-mode
check only and cannot pass the P2-B directory-coverage gate. The Python API
also refuses legacy manifests by default; its keyword-only
`allow_legacy_no_directory_modes=True` enables the older check with false
directory coverage. This is deliberate fail-closed tightening of legacy calls.
Legacy opt-in returns `status: verified_legacy_no_directory_modes` and
`directory_mode_gate: not_passed_legacy`; it cannot pass the directory gate.
New full mode coverage returns `status: verified` and `directory_mode_gate: passed`.
That gate covers directory mode bits only, not full P2 recovery acceptance.
Exact restored-directory-set equality applies to legacy manifests too; the
opt-in flag does not relax contamination, symlink or unexpected-entry checks.
Once the CLI completes a stable read, operational failures include its
`manifest_artifact_sha256`. A read-race failure instead reports only
`observed_bytes_sha256`, which binds bytes observed, not a complete stable
artifact. Earlier incomplete/oversized acquisition omits identifiers that
cannot be established. Strict JSON parsing rejects NaN and Infinity. After
parsing a standard JSON object, failures also include its canonical hash;
parse failures never claim one.
Failure receipts describe bytes observed during that attempt, not a promise
that the path still contains them. Match their hashes against an independently
measured hash of the manifest file as found after the failed run. If the file
is missing or differs, retain the observed evidence and mismatch, and reject
recovery acceptance; never present the failure receipt as current-file proof.
P2-B acceptance receipts must come from the CLI, with both identifiers and
`manifest_artifact_checked: true`. A parsed-dictionary Python API result reports
`manifest_artifact_checked: false` and cannot satisfy that artifact/read-back
gate. Do not substitute an API-only receipt, even for a new manifest.
The boolean is a coverage marker, not authentication. Compare the CLI's
`manifest_artifact_sha256` to the independently measured SHA-256 of the actual
transferred/read-back manifest file; retain that measurement and the unedited
receipt with the released run record. An edited JSON receipt is not proof.
Success output includes the resolved `manifest_path`, `restored_root` and
observed `restored_root_device`. Match them to the released run's fresh test
area and retain independent source/NAS device evidence. A local device number
does not by itself prove physical independence, provenance or off-site recovery.

This is coverage of file/root/selected-parent POSIX bits only. Ownership, ACLs,
extended attributes, timestamps, directories above the collection root and
independent storage need the separate P2-B checks.
Directory before/after comparisons certify mode values, not persistent inode
identity through a rename-and-replace race. The supervised quiet/snapshot
window remains mandatory; this helper cannot certify an atomic snapshot.
The helper remains read-only
and reports rehearsal-only status even when used in a later supervised test;
the manager accepts actual recovery only from the complete retained evidence.
Hash equality preserves reference text; this tool does not prove semantic
completeness or resolve every wiki reference. P2-B retains representative
source-backed recall and reference acceptance on restored real content.

## Deferred P2-B recipe and gates

Before a real manual backup, the manager must release the exact destination,
capacity/access, checkpoint age/retained evidence, protection/key custody and
affected-host permissions. A one-off manual rehearsal does not configure a
recurring cadence or retention policy. No schedule, purchase or automatic
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
