# Storage, writer and publication operations

Mandatory reading before any NAS read or write, ordinary note save, publication,
lock decision or conflict-copy decision. This runbook carries the authority of
`AGENTS.md`; if it is missing or unreadable, stop the affected operation.

## Topology, bindings and read fallback

K2B is Keith's single-user knowledge and workflow system. Its current authorized topology is:

- Home project: `/Users/keithmbpm2/Projects/K2B`
- Home vault: `/Users/keithmbpm2/Projects/K2B-Vault`
- SJM project: `/Users/keithcheung/Projects/K2B`
- SJM vault: `/Users/keithcheung/Projects/K2B-Vault`
- SJM host: `GLPs-MacBook-Pro`, reached only through its existing authorized SSH configuration

Code and skills remain installed locally on both Macs and move through reviewed Git commits. Both Macs use one online knowledge collection on NAS through their logical `Projects/K2B-Vault` aliases. Credentials and machine-local state remain on their owning machines.

The current online bindings are Home `/Users/keithmbpm2/Projects/K2B-Vault` to `/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault` on `//cheung@192.168.50.67/Keith-Work`, and SJM `/Users/keithcheung/Projects/K2B-Vault` to `/Volumes/Keith-Work/K2B-Vault` on `//keith.cheung@100.75.5.21/Keith-Work`. Before online reads or writes, verify the logical alias resolves to the approved target on the actual mounted SMB share. A directory alone is not mount evidence. If unavailable or mismatched, stop the online operation; never save into an unmounted shadow directory or silently use a preserved original.

The manager confirmed collection equivalence with a common Home/SJM nonce, SJM EEXIST and matching Home readback hashes/marker after an actual SJM save. Different IP/account strings alone are not equivalence evidence. If a binding changes, require authorized marker/hash confirmation again before treating the targets as one collection.

The selected former vault Syncthing folder `xyzwv-pgl4y` stays paused on both Macs. Syncthing descriptions below apply only to an explicitly selected legacy local-replication mode, not this online NAS default. Preserved local originals are dated copies, never fallback targets. No automatic write fallback, write return or reconnection is enabled. NAS stores knowledge; it is not an execution server and does not enable automatic capture of non-Codex chats.

For active user-requested read/recall, first prefer the exact verified NAS
mount/alias. The following is an insufficient summary, not an executable
fallback procedure. Fallback is permitted only after performing the complete
sole authoritative checklist in `.agents/skills/k2b-vault-writer/SKILL.md`,
including its consumed-source recording and post-command hash gates.
If that online check fails, use only the receipt-verified downloaded
read copy under the procedure in `k2b-vault-writer`, without another routine
permission question: Home `/Users/keithmbpm2/Projects/K2B-Vault-Offline`, SJM
`/Users/keithcheung/Projects/K2B-Vault-Offline/SynologyDrive`. Require the actual
host/user/root/inode/device/time and comparison-receipt/hash bindings in
`~/.local/state/k2b/nas-offline-copy-verification.json`; missing or unverifiable
evidence stops fallback. Verify each consumed ordinary file's stable bytes
against the locally retained compared-source manifest bound by that comparison
receipt; do not use bootstrap/gather output until every consumed Vault input
passes. Missing or changed inputs stop the read pending fresh comparison.
Announce the local path; every fallback output carries last-full-comparison UTC
time/age and labels checked-unchanged local bytes as potentially stale relative
to NAS. Pin that
read root and pass explicit `K2B_VAULT_PATH` only to this operation's existing
bootstrap/gather commands. Recheck root identity and receipt bindings before
and after each consuming command, stopping on change. Parse `verified_utc`
strictly as `YYYY-MM-DDTHH:MM:SSZ` UTC; reject invalid or future times.
A verified snapshot is not ongoing freshness; there is no age cap or full-copy
rehash per read. Eligibility is the stated evidence/current-file checks, not a
separate runtime activation marker.
Never change aliases or global settings, select originals/shadow directories,
read hidden/client metadata or a private family subtree as fallback, or use
offline `System/log.md` as the current log; use actual `wiki/log.md` only.
Generated `wiki/reference/minnie-family-memory` remains separately owner-controlled
and outside this acceptance. Saves and Home publication remain verified-NAS-only;
retain private drafts as pending during NAS failure. No mid-save diversion,
offline writing, automatic write return or headless Drive completion is enabled.

Use the concise operating procedure in `k2b-vault-writer`: inspect `/sbin/mount` for the exact approved source, mount point, `smbfs` and mount flags, resolve the logical alias with the installed host Python 3.12 runtime, and read a bounded index sample before saving. Stop on unavailable, stale or unverifiable checks. These checks are operator procedure, not new writer/hook mount enforcement, an atomic mount lease or proof of future write permission. Exact observed source spellings are required. Legacy mode requires an explicit operator decision and verified target; NAS failure never selects it automatically. Separately bound Home shared logs require an expanded `K2B_WIKI_LOG` path to the same NAS `wiki/log.md`. Counterpart access that cannot be checked stays unverified.

## Synchronization and writers

Before a NAS writer or Home publication invocation, follow the effective-setting
readback in `k2b-vault-writer`: inspect the installed helper's existing `LOCK_DIR`
without main, compare to the freshly verified resolved NAS root's
`.k2b-compile-index.lock.d`, and retain private setting/path/source-hash/command
evidence. Launch the intended command with the same exported environment and
host interpreter. Missing/default/mismatched/unreadable settings stop. Apply
that skill's busy-lock owner/receipt escalation and exact-target recovery
runbook; unknown ownership stays blocked within the existing operation budget.
Required missing indexes remain Home-owned index-state errors.

In online NAS mode, use the verified mounted target and existing designated writer, expected-hash and lock checks. Home-only shared-hub ownership remains unchanged. Home publication retains its separate machine-local authority, approved policy hash, managed paths and exact SMB live preflight. NAS storage is not a new execution host, backup or general distributed-lock guarantee.

The supported per-invocation compile lock is the same verified resolved NAS directory on each host: Home `/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault/.k2b-compile-index.lock.d`, SJM `/Volumes/Keith-Work/K2B-Vault/.k2b-compile-index.lock.d`. Ordinary writers hold their local writer lock and designated compile lock throughout note/index mutation. Home publication step 5 in `scripts/prompts/automatic-memory-native.md` now prefixes the existing NAS compile-lock and Home log bindings so its imported index helper inherits them; private publication/reconciliation locks remain intact. The Home shared log retains its separate log lock: never bind it to the compile lock, which can deadlock standalone compile.

Once both ordinary-writer and Home publisher/index invocations are installed with this common NAS binding and checked, ordinary saves need no new Home-owner approval per note. SJM needs NAS, not Home availability. Coordinate current owners for activation and genuinely overlapping exceptional operations, not a blanket manual window for ordinary saves. Missing/unverified required binding stops the operation. Do not infer no writer from sleep, failed ping or silence; unavailable counterpart hash readback is only an unverified-readback result. Shared lock and expected hash are the actual ordinary-write coordination.

The observed common nonce/EEXIST and one real SJM save, 44.566 seconds with matching Home hashes/marker while also holding Home's local compile lock, do not establish general unattended/concurrent-client safety or the latency target. The Home owner identified an unset compile-lock prefix in the prior publication command; this source change delivers the existing binding, not job activation. General safety remains unaccepted until corrected invocations are installed and checked. Preserve every native role, extraction/source/budget/hold/receipt/provider/schedule boundary; no job is launched or reconfigured here. The existing mkdir/rmdir busy refusal and acquiring-owner cleanup remain; no PID/TTL protocol, automatic stale cleanup or forced deletion is added.

Real retained conflict copies affecting a note or index still require the existing conflict checks and manual resolution. Paused Syncthing does not authorize clearing or bypassing them. The 13 retained identical historical copies are not blanket resolved status; conflict-copy detection alone is not NAS concurrency protection.

For an explicitly selected legacy local-replication mode, Syncthing is replication, not a lock or backup. Direct shared-note/hub edits require an idle vault folder, zero needed files/bytes and no errors. The helper-based ordinary-note exception can complete locally on either Mac, including while Home is offline, and refuses real Syncthing conflict copies. This legacy behavior does not install automatic offline write fallback for the current NAS aliases; the receipt-verified read-copy exception above is separate.

### Ordinary note saves (both Macs)

The subtree `wiki/reference/minnie-family-memory/` is a Mini-owned generated projection of explicitly accepted or owner-approved memories. Ordinary K2B saves must refuse that subtree and its case variants, including in offline copies. Only the configured Minnie exporter may update `approved-memories.md` and its atomic temporary files after the exact NAS destination and scoped access are verified. Home owns the initial note/index/link registration; Mini never writes general indexes or shared hubs. Keep the existing `wiki/reference/family-discord-memory.md` and provenance. Conversation history, school/mail bodies and broader K2B retrieval are not admitted by this exception. Register the initial projection before activating the ordinary-writer guard on both Macs, and activate both guards before Mini export writes.

Ordinary notes, Markdown under `wiki/work`, `wiki/people`, `wiki/projects`, `wiki/concepts`, `wiki/insights`, `wiki/reference`, save through the local `scripts/vault-note-write.py` on either Mac during active user-requested conversations, without Home availability. In online mode the target is the verified NAS alias and requires that SMB share to be available. The writer takes a local lock and the designated index lock, checks the expected content hash, refuses to overwrite a real Syncthing conflict copy, keeps durable private before/after recovery copies plus a small receipt under `~/.local/state/k2b` (never in the shared vault), and updates the note's folder index row and master counts through the imported `compile-index-update.py` helpers. Its generic `saved locally; synchronization unverified` text is a helper receipt, not proof of counterpart arrival; verify matching NAS bytes through the counterpart before claiming that. A `partial` result means the note saved but a later step failed: resolve the reported cause, then retry the original command. If `failed_step` is `conflict` (exit 2), stop and resolve it manually first; retrying alone cannot fix a conflict. The SessionStart hook warns only on real `.sync-conflict-*.md` copies, via the writer's read-only `conflicts` subcommand.

The writer is not a broadened capture path: it rejects index.md targets, `Shipped/`, hidden or escaping paths, symlinks, non-regular files, control/policy paths and malformed indexes. Policy ledgers, shared hubs, automatic memory, raw capture and bulk compile keep their existing ownership; only `k2b-vault-writer` routes ordinary saves this way.

Home alone writes shared hubs and performs background index maintenance. The ordinary interactive helper's folder-row/master-count update described above is the only two-Mac index exception:

- `wiki/log.md` through `scripts/wiki-log-append.sh`
- background/bulk compile indexes through `scripts/compile-index-update.py`
- observer candidates/defers through their designated locked writers
- usage logs and policy/memory ledgers through their designated writer
- semantic capture reconciliation through the home capture path
