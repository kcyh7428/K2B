---
name: k2b-ship
description: Use when Keith explicitly authorizes committing, pushing, shipping, merging, or activating completed K2B changes.
---

# K2B Ship

## Authority boundary

Plain implementation wording is not delivery authority. Without explicit delivery wording, preserve an uncommitted checkpoint, report modified files and verification, and create no deferred-deployment state.

When Keith says `ship`, `commit`, `push`, `merge`, or gives equivalent delivery wording for completed current-repo work, that authorizes the reviewed Git delivery path. It does not authorize force-push, destructive reconciliation, credential changes, unrelated cleanup, paid fallbacks, or contact with excluded hosts.

The current K2B topology is the home and SJM Macs. Git carries code; Syncthing carries the vault. The Mac Mini lane is retired and outside normal shipping. A new request explicitly reopening that host requires a separate scope decision.

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
8. Update required tracked delivery records in a separate reviewed commit when the project convention requires them. Shared-vault records are written by the home Mac only, after healthy Syncthing convergence, through the applicable K2B vault helper.
9. Activation on each Mac is a separate state from commit and push. Use `k2b-sync` to inspect both checkouts, preserve SJM's local `AGENTS.md` and `.codex/hooks.json` adaptations, and update each checkout deliberately. Per-machine credentials and local state are never copied through Git.

## Required report

Lead with what Keith will notice. Then state:

- tests and concrete done-check evidence;
- independent reviewer, verdict, and findings;
- commit SHA, branch, and push result;
- home activation state and SJM activation state;
- Syncthing and representative recall evidence where relevant;
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
