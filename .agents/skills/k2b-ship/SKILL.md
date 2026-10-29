---
name: k2b-ship
description: Use when Keith explicitly authorizes committing, pushing, shipping, merging, or activating completed K2B changes.
---

# K2B Ship

## Authority boundary

Plain implementation wording is not delivery authority. Without explicit delivery wording, preserve an uncommitted checkpoint, report modified files and verification, and create no deferred-deployment state.

When Keith says `ship`, `commit`, `push`, `merge`, or gives equivalent delivery wording for completed current-repo work, that authorizes the reviewed Git delivery path. It does not authorize force-push, destructive reconciliation, credential changes, unrelated cleanup, paid fallbacks, or contact with excluded hosts.

The current K2B topology has local reviewed code/skill installations on Home and SJM reading one online NAS collection through the verified logical vault aliases and exact SMB bindings in `AGENTS.md`. Git carries code; NAS stores knowledge and is not an execution server. The former selected Syncthing vault folder `xyzwv-pgl4y` stays paused. Syncthing applies only to an explicitly selected legacy local-replication mode. Fallback, refresh and reconnection are not installed; no unmounted shadow folder or preserved original becomes current silently. The retired Mini deployment lane and separately authorized supervised browser/capture role retain their existing boundaries.

## Delivery workflow

1. Read `AGENTS.md`, the implementation plan, and relevant tests. List binary done checks and current blockers.
2. Inspect `git status`, staged state, diff, branch, and remotes. Preserve unrelated or pre-existing changes. Build an exact intended-file list; never use `git add -A`.
3. Run focused verification proportional to the diff, including `scripts/verify-codex-authority.sh` for instruction, hook, or skill changes. Run `git diff --check` immediately before review.
4. Stage exactly the intended files. Confirm the staged path set equals the intended path set.
5. Run independent review. OpenAI-built changes require Kimi with no fallback:

   ```bash
   scripts/review.sh diff \
     --files "<comma-separated-staged-files>" \
     --builder-family openai \
     --primary kimi \
     --no-fallback \
     --wait
   ```

   Kimi-built changes require Codex with no Kimi fallback. Historical Anthropic or mixed/other work must use the matrix in `AGENTS.md` and record reviewer independence. A transport success is not an approval; read and surface the actual verdict and findings. Fix material findings, rerun invalidated tests, and obtain another review. Do not weaken tests to manufacture a pass.
6. Verify the staged set still matches the reviewed set. Commit without `--no-verify` or `--amend`, then confirm the committed path set matches the reviewed set.
7. Push the reviewed commit to its intended branch. Never assume `main`; show the branch and remote ref. A rejection or divergence stops for deliberate reconciliation.
8. Update required tracked delivery records in a separate reviewed commit when needed. Home shared records use their designated hub/log/publication tools, never the ordinary-note writer. Require recorded storage checks from `k2b-vault-writer`, existing policy/authority/path/hash/lock gates and installed common NAS compile-lock binding. Failed or unverified checks stop with no fallback. Ordinary saves under installed bindings need no new per-note Home approval; activation/exceptional overlaps retain current-owner coordination. Home logging separately binds `K2B_WIKI_LOG="/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault/wiki/log.md"` and keeps its own log lock. Publication retains machine-local authority. Legacy mode requires Keith's direct selection of a verified target and healthy Syncthing convergence. No SJM scope expansion.
9. Activation on each Mac is a separate state from commit and push. Use `k2b-sync` to inspect both checkouts, preserve SJM's local `AGENTS.md` and `.codex/hooks.json` adaptations, and update each checkout deliberately. Per-machine credentials and local state are never copied through Git.

For NAS delivery, ordinary writers and Home publication/index invocation must use the same verified resolved NAS compile-lock directory. The existing writer holds local writer and designated compile locks throughout note/index mutation. Home native prompt step 5 binds `K2B_COMPILE_INDEX_LOCK="/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault/.k2b-compile-index.lock.d"` and `K2B_WIKI_LOG="/Users/keithmbpm2/Projects/K2B-NAS/K2B-Vault/wiki/log.md"` before the existing publication command. Other private locks remain; the Home log retains its separate lock and must not reuse the compile lock.

After these invocation bindings are installed and checked, ordinary notes require no new Home-owner approval per save. A Home-offline SJM save requires NAS only. Coordinate owners for activation and genuinely overlapping exceptional operations; stop if a required binding is absent/unverified. Never infer no writer from sleep, ping or silence. Use verified resolved-target receipt/hash readback; unreachable counterpart readback stays unverified without a liveness guarantee. Existing shared locks/expected hashes provide ordinary-write coordination, not a new per-save manual window or broad post-save lock/file search.

The observed nonce/EEXIST and SJM save also held Home's local compile lock; they do not accept general unattended/concurrent-client safety or latency. Actual owner evidence showed the prior publication prefix was unset; general safety remains unaccepted until the corrected invocation is installed and checked. Keep native roles/extraction/windows/budgets/holds/receipts/providers/schedules unchanged. Source delivery does not activate a prompt or run a job. Preserve expected hashes, conflicts, private backups and partial repair. No helper/wrapper/protocol/cache/storage-mode/TTL/automatic-reconnect change, forced lock deletion or permanent Syncthing removal. Keep selected `xyzwv-pgl4y` paused and originals preserved; no silent fallback.

## Required report

Require the effective-setting readback and busy-lock runbook in
`k2b-vault-writer`: inspect installed settings without main, compare to the
freshly verified NAS lock target, retain private path/hash/command evidence,
then launch the intended command with the same exported environment and host
interpreter. Missing/default/mismatched/unreadable settings stop. Unknown lock
ownership stays blocked within the existing operation budget, with one report
to root/Keith. Required missing indexes remain Home-owned index-state errors.

Lead with what Keith will notice. Then state:

- tests and concrete done-check evidence;
- independent reviewer, verdict, and findings;
- commit SHA, branch, and push result;
- home activation state and SJM activation state;
- Actual online SMB binding and representative source-backed recall on each affected host, or legacy Syncthing evidence when that mode is explicitly selected;
- exact remaining approvals or credential blockers.

Use the state words precisely: `prepared`, `reviewed`, `committed`, `pushed`, `activated-home`, `activated-sjm`, and `accepted`. Never call a prepared or pushed change operational on a Mac where it has not been activated and verified.

## Prohibited shortcuts

- No automatic shipping from a hook.
- No same-family review presented as independent.
- No credential value in logs or commits.
- No direct file copy as a substitute for Git code delivery.
- No blanket clean-state claim beyond inspected hosts and surfaces.
- No vault lane/status transition without its actual acceptance evidence.
- No claiming cross-Mac arrival from a local `saved-local` result: an ordinary-note save reports `synchronization unverified` until the counterpart's copy is hash-verified when reachable; fixture proof is not arrival.
- No deleting or hand-merging Syncthing `.sync-conflict-*.md` copies; both versions are preserved until Keith decides.
