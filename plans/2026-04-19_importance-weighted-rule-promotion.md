---
title: Importance-weighted rule promotion
date: 2026-04-19
status: designed
feature: feature_importance-weighted-rule-promotion
ships-under: feature_importance-weighted-rule-promotion
checkpoint-1: codex-review-complete-go-with-fixes
checkpoint-2: at-ship-time
up: "[[plans/index]]"
---

# Plan: Importance-weighted rule promotion (Codex-reviewed v2)

Implements Item 1 of the 2026-04-19 memory-architecture plan (research note: `raw/research/2026-04-19_research_memory-architecture-plan.md`). M effort. Codex plan-review v1 returned GO-WITH-FIXES with 7 P1 + 2 P2 findings; this v2 folds every P1 and both P2s into the design before implementation.

## Goal

Replace K2B's current promotion/eviction ordering with a blended importance score so architecturally important rules stop falling off the LRU-12 cap when they are rarely re-affirmed but frequently cited.

## v2 design pivots (in response to Codex v1 review)

- **P1 #4 (single-writer):** access counts live in a NEW standalone TSV file `K2B-Vault/System/memory/access_counts.tsv`, not in `self_improve_learnings.md`. `/learn` remains the sole writer of the learnings file; `/ship` only writes the TSV. No concurrency risk on the big file.
- **P1 #1 (age anchor):** for PROMOTE candidates the anchor is `Date:` (first captured); for EVICT candidates the anchor is `last-reinforced:` from the parenthetical. Both are fields the scripts already parse. No new schema on the learnings file. `importance_score()` takes the anchor ISO string as a parameter; caller decides which to pass.
- **P1 #2 (non-L active rules):** rules without a parenthetical `L-ID` are PINNED -- `select-lru-victim.py` skips them entirely in the sort. Foundation rules Keith wrote manually never get auto-demoted by this system.
- **P1 #3 (rules missing reinforcement metadata):** rules without an explicit `reinforced Nx` count default to `reinforcement_count=1`, same as today. The blended score still orders correctly; it just de-weights those rules vs ones with real reinforcement signal. Documented in the helper docstring.
- **P1 #5 (citation detection):** tightened contract. A rule counts as cited ONLY if the session's conversation contains: (a) the explicit L-ID token (e.g. `L-2026-04-01-001`), OR (b) the distilled-rule text verbatim as a quoted substring, OR (c) Claude explicitly says "applying rule N" or "per rule N" naming an active rule by number/title. Ambiguous or paraphrased mentions are SKIPPED -- under-count is preferred over over-count because false positives bias promotion/eviction systematically.
- **P1 #6 (active_rules.md prose drift):** `active_rules.md` header prose is updated in this ship to describe the new score-based eviction rule.
- **P1 #7 (test coverage):** four new tests added covering missing-Reinforced, missing-last-reinforced, malformed/future dates, non-L rules, and tie behavior.
- **P2 #8 (access_count semantics):** default is 0 (raw citation count, not seeded prior). Scoring formula floors to `max(1, access_count)`. This keeps the stored value honest as a citation counter.
- **P2 #9 (step 13.5 failure path):** `/ship` surfaces `[warn] access-count bump failed: <reason>` if the helper exits non-zero. The session-summary file is written first (independent step); bump is best-effort after. Subsequent `/ship` runs do not retry past bumps.

## Storage model

New file: `K2B-Vault/System/memory/access_counts.tsv`

```
# access_counts.tsv -- citation counts per L-ID. Written by
# scripts/increment-access-count.py only (called from /ship step 13.5).
# Schema: tab-separated columns, header required.
learn_id	count	last_accessed
L-2026-04-01-001	3	2026-04-17
L-2026-04-05-001	5	2026-04-18
```

- Single writer: `scripts/increment-access-count.py`
- Readers: `scripts/promote-learnings.py`, `scripts/select-lru-victim.py`, via a shared loader in `scripts/lib/importance.py`
- Atomic rewrite via temp + `os.replace` on every bump
- No schema migration needed for the corpus: any L-ID not in the TSV defaults to count=0 (scoring floors to 1 in the formula)

## Scoring formula

```
score = (reinforcement_count * max(1, access_count)) / max(1, age_in_days)
```

Where:
- `reinforcement_count` -- bullet `- **Reinforced:** N` in learnings OR `reinforced Nx` in the rule parenthetical. Missing defaults to 1.
- `access_count` -- count from `access_counts.tsv` keyed by L-ID. Missing defaults to 0; formula floors to 1 for the multiplier.
- `age_in_days` -- days between caller-supplied `last_reinforced_iso` (or `Date:` for promote candidates) and today. Empty / malformed / future dates -> floored to 1.

## Architecture

- `scripts/lib/importance.py` (NEW, already written in this session): `importance_score()` pure function plus `load_access_counts()` loader from the TSV.
- `scripts/increment-access-count.py` (NEW): takes L-IDs as argv, reads `access_counts.tsv`, increments each, writes atomically via temp + `os.replace`. Dedups argv per call. Unknown L-IDs: warn to stderr, record with count=1 anyway (starts the counter). Exit codes: 0 success, 1 usage, 2 IO error.
- `scripts/promote-learnings.py` (MODIFIED): loads access counts, parses `Date:` for age, computes score, sorts candidates DESC. Ties: fall back to existing file-order (dict insertion).
- `scripts/select-lru-victim.py` (MODIFIED): skips rules without L-IDs in the parenthetical (P1 #2), loads access counts, parses `last-reinforced:`, computes score, sorts ASC, picks `rules[0]`. Ties: fall back to existing (last_reinforced asc, reinforcement_count asc, learn_id asc).
- `.claude/skills/k2b-ship/SKILL.md` step 13.5 (MODIFIED): after writing the session-summary file, emit `cited-rules: [L-id1, L-id2]` frontmatter on the summary, call `scripts/increment-access-count.py <L-ids>`. If helper exits non-zero, print `[warn] access-count bump failed` but DO NOT fail the ship. Detection contract is tightened per P1 #5 -- document the three allowed patterns explicitly.
- `.claude/skills/k2b-feedback/SKILL.md` (MODIFIED): no schema change to learnings entries (P1 #4 pivot). Documentation note: new entries default to access_count=0 via absence from `access_counts.tsv`.
- `K2B-Vault/System/memory/active_rules.md` (MODIFIED): update the header prose describing the eviction rule from "oldest last-reinforced wins" to "lowest importance score wins; non-L rules pinned exempt".

## Plan of attack (TDD-driven, v2)

1. **Tests first, all five files:**
   - `tests/sort-key.test.sh` (ALREADY PASSING): 12 cases covering formula edge cases.
   - `tests/increment-access-count.test.sh` (rewrite needed): fixtures with `access_counts.tsv`, not learnings file. 10+ cases including default-0 semantics, dedup, unknown L-ID handling, atomic write, multiple bumps.
   - `tests/promote-learnings-importance.test.sh` (new): fixtures with three L-entries at different (reinforced, access, Date:) points, confirm JSON output order is score DESC. Include cases for missing `Reinforced`, missing entry from TSV, malformed `Date:`.
   - `tests/select-lru-victim-importance.test.sh` (new): fixtures with mix of L-linked and non-L rules, confirm non-L rules are SKIPPED, confirm victim is lowest-score L-rule. Include fixture with missing `last-reinforced`, missing `reinforced Nx`, tie-breaking.
   - `tests/load-access-counts.test.sh` (new): loader tests for the TSV parser. Empty file, malformed row, missing file, duplicate L-ID (last row wins).
2. **Verify RED on all new/modified tests.**
3. **Implement:** `increment-access-count.py`, loader in `importance.py`, modifications to `promote-learnings.py` and `select-lru-victim.py`.
4. **Update SKILL.md files** per the architecture section above.
5. **Update `active_rules.md` prose** to describe the new eviction rule.
6. **Verify GREEN on all tests.**
7. **Smoke test** on real corpus: `promote-learnings.py` should emit candidates in a reasonable order. `select-lru-victim.py` should pick a plausible victim. No crashes, no empty output.

## Files changed (final tally, v2)

New:
- `scripts/lib/importance.py` (shared scoring + TSV loader)
- `scripts/increment-access-count.py` (bump helper, TSV only)
- `K2B-Vault/System/memory/access_counts.tsv` (seed header on first run of increment helper)
- `tests/increment-access-count.test.sh`
- `tests/promote-learnings-importance.test.sh`
- `tests/select-lru-victim-importance.test.sh`
- `tests/load-access-counts.test.sh`
- `tests/sort-key.test.sh` (already written, already GREEN)
- `wiki/concepts/feature_importance-weighted-rule-promotion.md` (already written in this session)

Modified:
- `scripts/promote-learnings.py` (score sort + Date: age anchor + TSV lookup)
- `scripts/select-lru-victim.py` (score sort + TSV lookup + non-L skip)
- `.claude/skills/k2b-ship/SKILL.md` (step 13.5 citation contract + increment call + warn on failure)
- `.claude/skills/k2b-feedback/SKILL.md` (note on access_count provenance; no schema change)
- `K2B-Vault/System/memory/active_rules.md` (header prose describing new eviction rule)

Unchanged (P1 #4 pivot):
- `K2B-Vault/System/memory/self_improve_learnings.md` (no schema additions; `/learn` remains sole writer)

## Risks and mitigations (v2)

1. **Access count signal quality.** Tightened contract -- only explicit L-ID / verbatim-rule / numbered-citation counts. If Claude misses a legit citation, rule drifts toward eviction slowly. Mitigation: the reinforcement signal still dominates; access_count is multiplicative but capped by `max(1, N)` when zero. A frequently-reinforced rule can never score 0 on access alone.
2. **`access_counts.tsv` corruption.** Single-writer, atomic-rewrite, TSV format. Worst case: hand-edit to repair, or delete and let counts rebuild from zero. No data loss (citations are ephemeral signals, not source of truth).
3. **Step 13.5 failure.** Fail-open: warn and continue. Surface to the ship output so the operator sees it. Does not block ship.
4. **Non-L active rules never gain signal.** Intentional per P1 #2 -- foundation rules Keith wrote manually are PINNED outside the LRU system. Document in `active_rules.md` prose.
5. **Clock skew.** Future `last-reinforced` clamps age to 1 (defensive, already unit-tested in sort-key.test.sh test 10).
6. **Score ties.** Existing tiebreaker chains in both scripts. Confirmed stable.

## What NOT to do (v2)

- Do NOT add `Access count:` bullets to `self_improve_learnings.md` entries (P1 #4 pivot). Access counts live in the TSV only.
- Do NOT auto-demote rules based on score drops. Demotion stays explicit via `/ship` step 0 + Keith confirmation.
- Do NOT over-count citations. Under-count is safer.
- Do NOT include non-L rules in LRU eviction ranking. Pinned exempt.
- Do NOT change reinforcement semantics. `/learn` still owns the `Reinforced:` increment; access is an ORTHOGONAL dimension.
- Do NOT backfill the TSV with guessed counts for existing rules. Let the counter start at 0 for every rule and accumulate organically.

## Checkpoint 1 (plan review) outcome

Codex review via Agent+codex:codex-rescue, completed 2026-04-19, duration 351s.
- 7 P1 findings: ALL addressed in v2 (see v2 design pivots section above).
- 2 P2 findings: ALL addressed.
- Verdict: GO-WITH-FIXES. Fixes applied, implementation proceeds.
- Risks to watch per Codex: writer ownership (solved by TSV), metadata completeness, citation determinism, parser tolerance, observer-loop ordering.

Checkpoint 2 runs at `/ship` commit time as normal.
