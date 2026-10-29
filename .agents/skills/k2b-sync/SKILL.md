---
name: k2b-sync
description: Use when Keith asks to synchronize, update, or compare K2B across the home and SJM Macs, or asks whether either machine is current.
---

# K2B Two-Mac Sync

## Operating contract

K2B spans two authorized Macs: Keith's home MacBook and the SJM MacBook `GLPs-MacBook-Pro`. Reviewed code and skills move through Git and execute locally. Both Macs read one online NAS knowledge collection through the logical `Projects/K2B-Vault` aliases and exact SMB bindings in `AGENTS.md`. NAS is storage, not an execution server. Credentials and machine-local state stay on their owning machines; no automatic non-Codex chat capture is enabled.

The selected former Syncthing vault folder `xyzwv-pgl4y` stays paused on both Macs. Automatic local fallback, refresh and reconnection are not installed. Preserved originals are dated copies, not current fallback. Never treat a bare mount directory as NAS evidence or silently use it or an old copy when SMB is unavailable. Syncthing checks below apply only to an explicitly selected legacy local-replication mode.

The former Mac Mini deployment lane remains retired. Keith authorized a separate
supervised Mini browser/capture role on 25 October 2026 and reaffirmed it on
26 October. That role may use the existing Home SSH relay and an available
computer-use connection; it does not restore the old Mini checkout, vault sync,
Telegram runtime or background jobs. Preserve this newer narrow authorization
when reconciling dated notes with the retired deployment history. Report network,
browser control, capture, NAS arrival and knowledge integration separately.
For Mini capture status, follow the Facebook evidence procedure in k2b-research.
Report the manifest with Mini-local pre-transfer hashes and NAS read-back
status. Failed or unverified arrival remains Mini-local and unintegrated.

## Before any mutation

Use the effective-setting readback and busy-lock runbook in `k2b-vault-writer`.
Inspect installed settings without main, compare to the freshly verified NAS
lock target and retain private setting/source-hash/command evidence. Launch the
intended command with the same exported environment and host interpreter.
Missing/default/mismatched/unreadable settings stop. Unknown lock ownership
stays blocked within the existing operation budget, with one report to
root/Keith. Required missing indexes remain Home-owned index-state errors.

1. Read `AGENTS.md` and inspect both Git working states.
2. Preserve all uncommitted work. Before updating SJM, save and compare any local `AGENTS.md` and `.codex/hooks.json` adaptations; never overwrite them with a blind pull, checkout, or copy. Remove a divergence only after the incoming portable file is shown to preserve its behavior.
3. Compare the common base and commits before proposing a Git update.
4. Read and carry out the numbered online procedure in `k2b-vault-writer`, not just its title: retain the exact `/sbin/mount` source/mount/`smbfs` flags, resolved alias matching `AGENTS.md`, and bounded index-read result. Missing procedure or unavailable/stale/unverifiable checks abort before writing; never substitute a local path. Confirm through the existing authorized local Syncthing status/UI that selected folder `xyzwv-pgl4y` remains paused, retaining that observation. A resumed or unverified folder blocks this online operation; do not change it automatically. Legacy mode requires Keith's direct selection of a specific verified target, recorded before its idle/zero-needed-files/zero-needed-bytes/no-error checks; incidental mount/folder state never selects a mode.
5. Verify the installed ordinary-writer and Home publication/index invocation bindings use the same resolved NAS compile-lock directory in `AGENTS.md`. Ordinary saves then use the existing local writer lock, common designated compile lock and expected hash, not a new per-note Home approval. Missing/unverified required binding means stop. Current-owner coordination remains for activation and genuinely overlapping exceptional operations. A failed ping, sleep or silence is not proof of no writer; unavailable counterpart hash readback stays separately unverified. No new sleep-monitoring or distributed-lock protocol.

## Code synchronization

Use ordinary reviewed Git commits as the transfer unit. Commit and push require Keith's delivery authorization. Updating the other checkout is a separate activation step: preserve local adaptations, fetch, inspect, and merge or rebase deliberately. Never copy `.env`, Codex authentication, SSH private keys, MCP secrets, provider tokens, build output, or user-local state.

If either checkout is dirty or has diverged, stop before a destructive Git operation and report the exact files/commits requiring reconciliation.

## Vault synchronization

In online mode there is one NAS collection, not two vault replicas to synchronize. Ordinary wiki notes (`wiki/work`, `wiki/people`, `wiki/projects`, `wiki/concepts`, `wiki/insights`, `wiki/reference`) use the local `scripts/vault-note-write.py` during active user-requested conversations and target the verified NAS alias. SJM does not require Home to be online, but does require its SMB target. Shared hubs (`wiki/log.md`, policy/control state, master compile indexes as batch operations, raw capture, background memory) remain Home-owned; SJM never appends Home shared logs directly and queues only its permitted small records under `~/.local/state/k2b/`. The helper's interactive folder-row/master-count update remains the sole two-Mac index exception. Generic saved-local receipt wording is not counterpart arrival evidence.

Home remains the sole shared hub writer; storage on NAS does not transfer that authority.

Use the storage/readback checks and existing writer in `k2b-vault-writer`. Set `nas_root` only after verifying the exact mounted source and logical alias; use `K2B_COMPILE_INDEX_LOCK="$nas_root/.k2b-compile-index.lock.d"` for ordinary writers and Home publication/index maintenance. The Home native prompt step 5 supplies its exact resolved NAS prefix. Ordinary writers hold local and designated compile locks across their full note/index operation. Once invocation bindings are installed and checked, ordinary SJM saves need NAS, not Home availability or new per-note Home approval. Coordinate activation/exceptional overlaps with current owners; do not infer installed bindings from a sample.

The same-nonce/EEXIST and real SJM save, 44.566 seconds with matching Home hashes/marker and a simultaneous Home local compile lock, are bounded evidence only. General unattended/concurrent-client safety remains unaccepted until corrected invocations are installed and checked; latency target remains unmet. Home shared logs retain their separate lock, never the compile lock. Preserve mkdir/rmdir cleanup/busy refusal, private repair and real conflicts; 13 retained identical copies are not blanket resolved. No automatic stale deletion, new protocol/service/wrapper or runtime default is introduced.

Syncthing owns transport only in the explicitly selected legacy local-replication mode. Its ordinary local-writer exception, including Home-offline saves, is retained for that mode and does not install automatic NAS fallback.

After a host reconnects, reread the current NAS note and expected hash before writing. Compare any private pending draft with that version; unexpected divergence blocks overwrite and preserves both versions. Do not publish stale local copies or infer that automatic fallback/reconnection is installed. General unattended/concurrent-client writes remain outside this procedure until root accepts owner bindings; separately authorized native jobs retain their own authority and schedules.

A `.sync-conflict-*.md` copy means both versions exist and must be preserved. Do not delete, rename, or hand-merge conflict files; the writer refuses to overwrite a conflicted target, and the SessionStart hook warns on real conflict copies. Resolution is Keith's call after both versions have been read.

Only purpose-built source exports and small append records may queue under `~/.local/state/k2b/` for later Home reconciliation; other requested writes must be rerun on Home.

Verify counterpart arrival by content hash or a unique synthetic marker, then remove test artifacts only when that cleanup was part of the authorized test.

## Status request

For read-only status, report separately:

- Git head, branch, dirty paths, divergence, and local adaptations on each Mac.
- Active logical vault target and verified SMB source/mount on each authorized host; any unverified host or unavailable share. For legacy mode only, Syncthing connection, folder state, needed files/bytes and errors.
- Whether a requested code commit is present and whether a requested vault marker has arrived.
- Any credential or host-access blocker without printing secret values.

Do not equate a connected device with an up-to-date folder, or a clean Git checkout with an activated runtime.

## Completion report

State what moved through Git, what was verified in the online NAS collection from each authorized host (or arrived through legacy replication), what local configuration remained untouched, and whether the result is only prepared, committed, pushed, pulled, or activated. Unavailable or unverified counterpart access remains unverified. If delivery authorization is absent, leave a clearly labeled uncommitted checkpoint and create no deferred-deployment marker.
