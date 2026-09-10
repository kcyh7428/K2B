---
name: k2b-autoresearch
description: Run a bounded autoresearch self-improvement loop on a K2B skill using isolated patches, binary assertions, and explicit delivery authorization. Use when Keith says /autoresearch, "improve this skill", "run the loop", "optimize skill", "self-improve", or wants to iteratively enhance a K2B skill's output quality.
---

# K2B Autoresearch

The Karpathy loop adapted for K2B skills. It improves one target through bounded, isolated experiments and binary assertions. Experiment history is stored as patches and evaluation records; the loop never creates delivery commits.

## Commands

- `/autoresearch [skill-name]` -- Run the improvement loop on a specific skill
- `/autoresearch plan` -- Setup wizard to define target, eval criteria, guard, iteration count
- `/autoresearch status` -- Show results summary for all skills with evals

## Vault & Skill Paths

- Vault: `~/Projects/K2B-Vault`
- Skills: `~/Projects/K2B/.agents/skills/`
- Each skill's eval infrastructure: `.agents/skills/k2b-[name]/eval/`

## The Loop Protocol

### Phase 0: Precondition Checks

Before starting:
1. Confirm the repository and target file. If the checkout contains unrelated work or the target is already modified, create an isolated temporary worktree from the current revision. Never overwrite or absorb pre-existing changes.
2. Identify the target file: `.agents/skills/k2b-[name]/SKILL.md`
3. Identify the eval file: `.agents/skills/k2b-[name]/eval/eval.json`
4. If eval.json doesn't exist, CREATE it first (see Eval Creation below)
5. Identify guard command (if any)
6. Run the eval against current SKILL.md to establish baseline
7. Log baseline as iteration 0 in results.tsv
8. Create a machine-local temporary experiment directory. Record the starting revision, target hash, and a copy of the target there. Do not put experiment scratch files in the Vault.

### Phase 1: Review

Before each iteration:
1. Read the target SKILL.md
2. Read last 10-20 entries from `eval/results.tsv`
3. Review prior `results.tsv`, `learnings.md`, and relevant delivered file history
4. Read `eval/learnings.md` for accumulated patterns
5. Understand: what's working, what's failing, what's been tried

### Phase 2: Ideate

Choose the next change using this priority order:
1. **Fix crashes/errors** from last run
2. **Exploit successes** -- patterns from kept iterations
3. **Address failing assertions** -- target the most common failure
4. **Try untested approaches** -- something not yet in results.tsv
5. **Combine near-misses** -- merge two almost-working ideas
6. **Simplify** -- fewer instructions that achieve the same result (less is more)
7. **Radical experiment** -- only after 5+ consecutive discards

### Phase 3: Modify

Make ONE focused change to the SKILL.md.

**The rule: "If I need 'and' to describe it, it's two experiments."**

Examples of ONE change:
- Add a specific instruction for formatting action items
- Remove a redundant section that causes confusion
- Reword the cross-linking instructions to be more specific
- Add an example output block

Examples of TOO MANY changes:
- Rewrite the entire workflow section AND add new formatting rules
- Change the frontmatter instructions AND the cross-linking rules

### Phase 4: Snapshot the Experiment

Before verification, save the target-only diff to the machine-local experiment directory and assign it an ID such as `exp-003`. Record the parent experiment ID and target hash. Do not stage or commit the experiment.

Rules:
- Capture only the target file's patch; never include unrelated changes.
- Keep scratch patches outside the repository and Vault.
- A failed experiment must be reversible from its saved target snapshot without resetting the checkout.

### Phase 5: Verify

Run each test prompt from eval.json:
1. For each test in eval.json:
   a. Present the test prompt as if Keith said it
   b. Generate output following the SKILL.md instructions
   c. Score each assertion as PASS or FAIL
2. Calculate overall pass rate: (total passes) / (total assertions across all tests)
3. If any single test takes excessively long, note it as a concern

**How to score assertions:**
Each assertion is a binary yes/no question about the output. Read the output carefully and answer each assertion honestly. Do not be lenient -- the point is to find real failures.

### Phase 5.5: Guard Check (Optional)

If a guard command is defined:
1. Run the guard command
2. If guard FAILS despite metric improvement: attempt to rework the change (max 2 attempts)
3. If guard still fails after 2 rework attempts: revert and try a different approach

### Phase 6: Decide

Compare pass rate to previous best:
- **IMPROVED**: Keep the target change in the isolated experiment worktree. Log as `keep`.
- **SAME OR WORSE**: Restore only the target from the last kept snapshot. Log as `discard`.
- **CRASHED**: Log as `crash`, restore only the target from the last kept snapshot, and try a different focused change.

Never use `git reset`, `git checkout`, or `git revert` against Keith's active checkout to discard an experiment.

### Phase 7: Log to results.tsv

Append to `.agents/skills/k2b-[name]/eval/results.tsv`:

Format (tab-separated):
```
# metric_direction: higher_is_better
iteration	experiment	pass_rate	delta	guard	status	description
0	baseline-a1b2c3d	72.3	0.0	-	baseline	initial state
1	exp-001	80.0	+7.7	pass	keep	added explicit action item format
2	exp-002	73.3	-6.7	-	discard	removed content potential section
```

Valid statuses: `baseline`, `keep`, `keep (reworked)`, `discard`, `crash`

### Phase 8: Update Learnings

After each iteration, update `eval/learnings.md`:
- After a **keep**: log what worked and why under "## What Works"
- After a **discard**: log what didn't work under "## What Doesn't Work"
- After discovering a pattern: log under "## Patterns Discovered"

### Repeat

Continue until:
- Perfect score (100% pass rate) achieved
- Keith interrupts
- Bounded iteration count reached (default 10 when Keith does not specify one)
- Every 5 iterations, print a brief progress summary

**Stuck detection**: After 5+ consecutive discards:
1. Stop and review the entire results.tsv
2. Try combining previous successful approaches
3. Try the opposite of what's been failing
4. Try a radical structural change to the SKILL.md
5. If still stuck after 3 more attempts, stop and report to Keith

## Eval Creation

When a skill lacks an eval.json, create one:

1. Read the skill's SKILL.md thoroughly
2. Identify 3-5 structural/format requirements that define "good" output
3. Write 3 test prompts that exercise different aspects of the skill
4. Write 3-6 binary assertions per test prompt

**Rules for good assertions:**
- Binary yes/no ONLY -- no subjective judgments
- Test structure, format, required sections, naming conventions, forbidden patterns
- Do NOT test tone, creativity, or subjective quality
- Sweet spot: 3-6 assertions per test prompt
- Below 3: agent finds loopholes
- Above 6: agent games the checklist

**eval.json format:**
```json
{
  "tests": [
    {
      "prompt": "The exact prompt to test the skill with",
      "expected_output": "Brief description of what good output looks like",
      "assertions": [
        "Does the output contain X?",
        "Does it avoid Y?",
        "Is Z present in the correct format?"
      ]
    }
  ]
}
```

## /autoresearch plan -- Setup Wizard

Walk Keith through defining the autoresearch target:

1. **Goal**: "What skill do you want to improve?" (list available skills)
2. **Scope**: Confirm the target file path
3. **Eval check**: Does eval.json exist? If not, create one together.
4. **Guard**: "Any command that must always pass?" (optional)
5. **Iterations**: "How many iterations?" Use 10 when Keith does not specify a bounded count.
6. **Launch**: Confirm and start the loop

## /autoresearch status -- Dashboard

Show a summary table for all skills with eval infrastructure:

```
| Skill                  | Assertions | Last Pass Rate | Best | Iterations | Last Run   |
|------------------------|-----------|----------------|------|------------|------------|
| k2b-meeting-processor  | 15        | 80.0%          | 86.7%| 12         | 2026-03-24 |
| k2b-daily-capture      | 12        | 91.7%          | 91.7%| 5          | 2026-03-24 |
| k2b-tldr               | 12        | 75.0%          | 83.3%| 8          | 2026-03-23 |
```

Read from each skill's `eval/results.tsv` to populate this table.

## Core Principles

1. **Patch memory, delivery history** -- experiment patches remain machine-local; Git commits are created only by the authorized delivery flow.
2. **ONE change per iteration** -- isolation makes learning possible.
3. **Snapshot before test** -- each trial is reversible without touching unrelated work.
4. **Binary assertions** -- no subjective scoring or lenient interpretation.
5. **Mechanical verification** -- assertions must be answerable by reading the output.
6. **Learnings accumulate** -- `results.tsv`, `learnings.md`, and saved experiment IDs record what worked.
7. **Bounded autonomy** -- stop at success, interruption, the iteration limit, or the stuck threshold.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
python3 "$HOME/Projects/K2B/scripts/k2b-shared-append.py" usage --skill k2b-autoresearch --summary "ran autoresearch on SKILL"
```

## Post-Loop Handoff

After the loop completes (perfect score, interrupted, or iteration limit):
1. Report the summary (iterations run, kept, discarded, final pass rate)
2. Apply only the winning target patch to the original checkout after confirming it does not overlap pre-existing target changes. Leave it uncommitted unless Keith already used explicit delivery wording.
3. If delivery was explicitly authorized, invoke `k2b-ship` after the completed loop and its required review. Otherwise report the uncommitted target and verification state.

Autoresearch never creates commits itself. Commit, push, activation, and synchronization remain separate delivery actions governed by `k2b-ship` and require explicit delivery authorization.

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep iteration summaries brief -- don't narrate every step
- Print progress every 5 iterations
- The eval file is sacred -- never modify eval.json during a loop (that's gaming the test)
