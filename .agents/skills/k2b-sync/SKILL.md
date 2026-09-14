---
name: k2b-sync
description: Use when Keith asks to synchronize, update, or compare K2B across the home and SJM Macs, or asks whether either machine is current.
---

# K2B Two-Mac Sync

## Operating contract

K2B currently spans two authorized Macs: Keith's home MacBook and the SJM MacBook `GLPs-MacBook-Pro`. Code moves through Git. Vault moves through Syncthing. Credentials and machine-local state stay on the machine that owns them.

The Mac Mini deployment lane is retired and outside this skill. A request that explicitly names the Mini requires a new scope decision; do not contact it, run a legacy deploy script, or create a deferred Mini obligation.

## Before any mutation

1. Read `AGENTS.md` and inspect both Git working states.
2. Preserve all uncommitted work. Before updating SJM, save and compare any local `AGENTS.md` and `.codex/hooks.json` adaptations; never overwrite them with a blind pull, checkout, or copy. Remove a divergence only after the incoming portable file is shown to preserve its behavior.
3. Compare the common base and commits before proposing a Git update.
4. For direct edits to shared notes and shared hubs, check Syncthing from each Mac: require the K2B vault folder to be idle, with zero needed files, zero needed bytes, and no errors. Helper-based ordinary-note saves (`scripts/vault-note-write.py`) are the exception -- they are designed to complete locally on either Mac, including while Home is offline, and they refuse to overwrite real Syncthing conflict copies.
5. Confirm no other session is editing the same shared note. Syncthing is replication, not locking or backup.

## Code synchronization

Use ordinary reviewed Git commits as the transfer unit. Commit and push require Keith's delivery authorization. Updating the other checkout is a separate activation step: preserve local adaptations, fetch, inspect, and merge or rebase deliberately. Never copy `.env`, Codex authentication, SSH private keys, MCP secrets, provider tokens, build output, or user-local state.

If either checkout is dirty or has diverged, stop before a destructive Git operation and report the exact files/commits requiring reconciliation.

## Vault synchronization

Syncthing owns the vault transport. Ordinary wiki notes (`wiki/work`, `wiki/people`, `wiki/projects`, `wiki/concepts`, `wiki/insights`, `wiki/reference`) may be saved locally on either Mac through `scripts/vault-note-write.py` during active user-requested conversations -- an urgent SJM save while Home is offline uses the local writer, not a Home task. Shared hubs (`wiki/log.md`, policy/control state, master compile indexes as batch operations, raw capture, background memory) remain Home-owned; SJM never appends Home shared logs directly and queues only its small records under `~/.local/state/k2b/`.

A `.sync-conflict-*.md` copy means both versions exist and must be preserved. Do not delete, rename, or hand-merge conflict files; the writer refuses to overwrite a conflicted target, and the SessionStart hook warns on real conflict copies. Resolution is Keith's call after both versions have been read.

Only purpose-built source exports and small append records may queue under `~/.local/state/k2b/` for later Home reconciliation; other requested writes must be rerun on Home.

Verify counterpart arrival by content hash or a unique synthetic marker, then remove test artifacts only when that cleanup was part of the authorized test.

## Status request

For read-only status, report separately:

- Git head, branch, dirty paths, divergence, and local adaptations on each Mac.
- Syncthing connection, folder state, needed files/bytes, and errors on each Mac.
- Whether a requested code commit is present and whether a requested vault marker has arrived.
- Any credential or host-access blocker without printing secret values.

Do not equate a connected device with an up-to-date folder, or a clean Git checkout with an activated runtime.

## Completion report

State what moved through Git, what arrived through Syncthing, what local configuration remained untouched, and whether the result is only prepared, committed, pushed, pulled, or activated. If delivery authorization is absent, leave a clearly labeled uncommitted checkpoint and create no deferred-deployment marker.
