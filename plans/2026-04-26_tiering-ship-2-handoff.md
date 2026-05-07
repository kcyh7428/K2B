# Tiering Ship 2 Handoff — fire this on or after 2026-04-26

This file is a self-contained handoff for a fresh Claude Code session. It assumes the session remembers nothing from 2026-04-19 when tiering Ship 1 landed. Paste the prompts below verbatim.

## Context Ship 1 shipped on 2026-04-19

- `feature_adversarial-review-tiering` Ship 1: 4-tier classifier (`scripts/ship-detect-tier.py`, `scripts/lib/tier_detection.py`) + Tier-3 allowlist (`scripts/tier3-paths.yml`) + `k2b-ship/SKILL.md` Step 3 routing surgery. Commits `40f39c3..2f54136`. See [[Shipped/feature_adversarial-review-tiering]].
- Bake gate set at **2026-04-26** (7 days after ship) to observe tier-boundary edge cases in real use before designing Ship 2.
- Ship 2 scope: `/ship --tier N` manual override + Codex `--cached` default scope fix.

## Step 1 — Evaluate the week's tier classifications

Run this first. It is a read-only audit. It tells you whether Ship 2's override flag is enough or whether the bake surfaced additional needed features.

```
Evaluate how the adversarial-review-tiering Ship 1 classifier performed during its bake week (2026-04-19 to 2026-04-26). Read-only analysis. Do NOT modify any files.

Context: scripts/ship-detect-tier.py + scripts/lib/tier_detection.py classify ship diffs into Tier 0-3. k2b-ship/SKILL.md Step 3 routes to review rigor per tier. The classifier records its decision in each ship's DEVLOG.md entry (per SKILL.md step 379 "record the classification in the ship audit trail").

Task:

1. List every ship commit during the bake window:
   git log --since="2026-04-19" --until="2026-04-26" --pretty=format:"%h %ai %s" --no-merges ~/Projects/K2B

2. For each feat/fix/refactor commit (skip "docs: devlog for..." follow-ups), pull its DEVLOG.md entry:
   grep -A 20 "Commit: `<sha>`" ~/Projects/K2B/DEVLOG.md

3. For each ship, extract:
   - Tier chosen by classifier (grep for "tier-0|tier-1|tier-2|tier-3" in the DEVLOG entry)
   - Review rigor actually used (Codex rounds count, MiniMax passes, or "skipped")
   - Rough review time (from commit timestamps if multi-commit + adjacent devlog commit)

4. Flag any mismatches:
   - Pseudo-code SKILL.md edit that got Tier 2 or Tier 3 -> classifier too strict -> Ship 2 needs rule tightening
   - Real code with tests that got Tier 1 -> classifier too loose -> Ship 2 needs Tier 1/2 boundary review
   - Any file under the tier3-paths.yml allowlist (e.g. memory schema, shared state) that got anything below Tier 3 -> DANGEROUS -> Ship 2 must harden allowlist matching
   - Any ship where Keith manually overrode (look for "--tier N" flag usage, which if Ship 1 had, would indicate missing capability -- but Ship 1 DOES NOT have --tier override yet, so any evidence of need for manual override is a Ship 2 signal)

5. Count and report:
   - Ships per tier: N at Tier 0, M at Tier 1, K at Tier 2, J at Tier 3
   - Mismatches found (with severity)
   - Estimated time saved vs. "everything gets Tier 3" baseline (~20 min per Tier 1 ship that would otherwise have been multi-round Codex)
   - Any patterns of needed-but-missing override cases

6. Write findings to raw/research/2026-04-26_tiering-bake-eval.md with:
   - Frontmatter: type: research-briefing, origin: k2b-generate, date: 2026-04-26
   - Tier distribution table
   - Mismatch list with examples
   - Recommendations for Ship 2 scope adjustments beyond the pre-planned --tier override + Codex --cached fix

Report the bake evaluation summary inline to the user before continuing. Do NOT start Ship 2 until Keith reviews and approves the findings.
```

## Step 2 — Ship 2 itself

Run this after the bake evaluation is written and Keith approves the scope. The prompt below assumes the evaluation found no new requirements beyond the pre-planned scope. If the evaluation surfaced additional needs, expand the feature spec accordingly before the plan-review checkpoint.

```
Ship feature_adversarial-review-tiering Ship 2. Depends on Ship 1 (shipped 2026-04-19 commits 40f39c3..2f54136) and the 2026-04-26 bake evaluation at raw/research/2026-04-26_tiering-bake-eval.md. Read both before starting.

Scope:

1. /ship --tier N manual override
   - New flag in k2b-ship SKILL.md accepted as first argument or after /ship.
   - N in {0, 1, 2, 3}.
   - Skips scripts/ship-detect-tier.py detection, forces the tier.
   - Logs both the detected tier AND the override in the ship audit trail so future eval can see when overrides were used.
   - If the override value disagrees with the classifier by more than one tier (e.g. detected Tier 1 but Keith forced Tier 3), surface a warning inline before proceeding -- not a block, just a confirmation.

2. Codex --cached default scope fix
   - Today (Ship 1 era), the codex:codex-rescue Agent call defaults to scope --cached. If the change is unstaged, the first pass returns empty and the session burns ~4 minutes before noticing.
   - Fix: k2b-ship SKILL.md Step 3 Codex invocation explicitly passes scope working-tree (not cached). Recorded motivation: 2026-04-19 73984d3 ship wasted its first Codex pass on this mis-scope.

3. Any additional requirements that surfaced during bake evaluation (read raw/research/2026-04-26_tiering-bake-eval.md "Recommendations" section). Fold into feature spec before plan review.

Feature spec update:
- Open wiki/concepts/Shipped/feature_adversarial-review-tiering.md. Append a "Ship 2 scope" section to the existing Updates.
- Status stays "shipped" (Ship 1 already in Shipped/); Ship 2 is an Updates entry, not a lane move.

K2B conventions:
- Write a plan in plans/ first. Plan review via Codex (Checkpoint 1) is MANDATORY because this touches the /ship skill body -- shared state.
- TDD for the --tier override flag parser. Test matrix: tier 0/1/2/3 accepted, "4" rejected, empty string rejected, disagreement warning fires when detected/override differ by 2+.
- Checkpoint 2 Codex at /ship time.
- One commit (or 2-3 if broken up for readability). /sync after (skills + scripts).

After Ship 2 lands:
- K2B side is complete on tiering. 
- Propose architect PR against K2Bi to port tiering + the bake-evaluation methodology. That PR will be K2B's PR #3 to K2Bi (after Teach Mode and Phase B).
- Once that PR merges in K2Bi, the CLAUDE.md trigger to formalize k2b-cross-project-pr skill fires. Handle in a separate session after.
```

## Step 3 — Telegram reminder on 2026-04-26

If you want a reminder instead of needing to remember yourself, drop this line in your Telegram bot conversation on or before 2026-04-25:

```
Remind me on 2026-04-26 at 09:00 Asia/Hong_Kong to run the tiering bake evaluation: ~/Projects/K2B/plans/2026-04-26_tiering-ship-2-handoff.md
```

Or schedule it via `/schedule` in a K2B session:

```
/schedule add "tiering-bake-eval-reminder" "2026-04-26 09:00 Asia/Hong_Kong" "Send Telegram: tiering bake week is done. Run the eval + Ship 2 per ~/Projects/K2B/plans/2026-04-26_tiering-ship-2-handoff.md"
```

## Who does what

- **You (Keith)**: on 2026-04-26, paste Step 1 prompt into a fresh session. Read the bake evaluation. Approve or adjust. Paste Step 2 prompt.
- **The fresh session**: executes both prompts in sequence. Stops between them for your review.
- **Future-me (this session's Claude)**: does NOT exist in 7 days. Each session is fresh. This handoff file IS the memory.

## If the bake evaluation shows issues before 2026-04-26

If during the week something breaks (e.g. a Tier 2 ship accidentally gets Tier 1 and under-reviews), don't wait for the bake. Ship a hotfix immediately and update this handoff accordingly.
