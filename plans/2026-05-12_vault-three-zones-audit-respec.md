# Vault Three-Zone Audit + Re-Spec — fresh session handoff

This file is a self-contained handoff for a fresh Claude Code session. It assumes the session remembers nothing from 2026-05-12. Open a new K2B session and paste the prompt at the bottom verbatim.

## Context (one paragraph)

[[wiki/concepts/feature_vault-three-zones]] was drafted 2026-04-24 and parked the same day after conflicting with the WMM bake period. The revisit date was 2026-05-08; the WMM bake gate passed 2026-05-12 (today). The calendar is now clear, but the three "revisit signals" from the park note (shelf row count > 200, real retrieval miss from junk-drawer ambiguity, concrete index-drift on Keith's domain work) **did not fire during the bake**. The spec's named Bug 1 example (Dr Lo's phone) was killed by WMM Ship 1 Commit 5 on 2026-04-24, so the spec's MVP Test 1 currently tests a problem that no longer exists. Keith wants to deep-dive in a fresh session and decide whether to ship the spec as written, re-scope it around bugs that are actually alive today, scope down to a smaller surgical change, or re-park.

## Mandatory pre-read (in this order)

Read these files in this order. Don't skip. The audit depends on all five.

1. `~/Projects/K2B-Vault/wiki/concepts/feature_vault-three-zones.md` — the parked spec itself. Treat as a starting hypothesis, NOT a plan.
2. `~/Projects/K2B-Vault/raw/tldrs/2026-04-24_tldr_wmm-loop-complete-bake-period.md` — why the spec was parked (the conflicts with the bake period).
3. `~/Projects/K2B-Vault/wiki/concepts/Shipped/feature_washing-machine-memory.md` — what WMM Ship 1 actually solved. This is what NOT to re-solve.
4. `~/Projects/K2B-Vault/wiki/context/context_open-items.md` — today's operational snapshot for context.
5. `~/Projects/K2B-Vault/raw/sessions/2026-05-12_mid-afternoon_session-checkpoint.md` — what shipped today (router-watchdog cascade-safety patch + WMM bake verdict + review queue triage). Tells you what NOT to repeat.

## Session goal

**Audit-and-re-spec, NOT build-the-parked-spec.**

The spec was K2B-generated (`origin: k2b-generate`) in one sitting on Telegram 2026-04-24. Three more weeks of usage data exists now. The architecture argument may still hold; the named MVPs probably need updating. Do the audit before any code is touched.

## What to do (in order)

### Step 1 — Test each named bug against current data

For each bug, decide: still alive? if so, is it user-visible or only theoretical?

**Bug 1: junk drawer (wiki/context/ has too many homes for one fact)**
- Search `self_improve_errors.md` for entries since 2026-04-24 mentioning "wrong file", "wrong row", "ambiguous path", or fact-resolution failure.
- Run `find ~/Projects/K2B-Vault/wiki/context/ -maxdepth 1 -type f | wc -l` — has the folder grown?
- Check `~/Projects/K2B-Vault/wiki/context/shelves/semantic.md` row count. (2026-05-12: 5 rows. Bake threshold was 200.)
- Look for new junk-drawer-class incidents in Keith-domain work specifically (not K2B self-improvement) — these are the ones that matter.
- Verdict needed: dead, alive-but-theoretical, or alive-and-biting.

**Bug 2: hand-maintained indexes drift**
- Run `/lint` to get fresh drift data.
- Check the 2 tracked writer-side bugs: `/ship` and `/tldr` not updating subfolder index for `raw/sessions/` and `raw/tldrs/`. Reproduce both. Are they still real?
- Search `self_improve_learnings.md` for index-drift learnings since 2026-04-24.
- Verdict needed: dead, alive-but-theoretical, or alive-and-biting.

**Bug 3: review rot**
- `ls ~/Projects/K2B-Vault/review/*.md`
- For each item, check `review-action:` and `date:` frontmatter. Anything `pending` and older than 30 days = rot. Anything past 7 days = warning sign.
- As of 2026-05-12 night: 10 items total (4 video files 6 days old, 4 video files 2 days old, 2 crosslinks digests untouched). Pending items past TTL? Count them.
- Verdict needed: dead, alive-but-theoretical, or alive-and-biting.

### Step 2 — Decide the path

Based on Step 1, pick one of four paths. Surface the recommendation to Keith with one-line rationale before writing anything.

| Path | When to pick |
|---|---|
| A. Build spec as written (4 ships, L effort) | All 3 bugs alive-and-biting, urgency high, no smaller alternative kills them |
| B. Re-scope to revised spec (smaller, new MVPs) | Some bugs alive, but the architecture cut or the MVP tests are wrong for today's reality |
| C. Split into multiple smaller features | Bugs alive but unrelated — fix each independently (e.g. just an auto-index script + just a TTL sweeper, no vault restructure) |
| D. Re-park with documented rationale | Bugs not actually biting, or the cost (moving 100+ files breaks every hardcoded path) is too high for current pain |

### Step 3 — Produce the output

Output goes to `~/Projects/K2B-Vault/wiki/concepts/feature_vault-three-zones.md` (overwrite or append depending on path).

- **Path A:** Update the spec's MVPs around CURRENT alive bugs (replace Dr Lo's case with a new named example). Add a "## Re-spec rationale 2026-05-XX" section to the top. Move to In Progress lane only with Keith's confirm.
- **Path B:** Rewrite the spec entirely. Old spec stays at the bottom under `## Original parked spec (2026-04-24)`. Frontmatter `status: ideating`. Add `## Re-spec rationale` explaining what changed.
- **Path C:** Create new feature notes for each split. Park the three-zones spec with status `superseded`. Update `wiki/concepts/index.md` lane membership.
- **Path D:** Update the park note with fresh signals and a new revisit-date. Status stays `parked`. Update `wiki/concepts/index.md` Parked row.

In all cases: update `wiki/concepts/index.md`. Append to `wiki/log.md` via the helper script.

## Constraints

- **No code changes this session.** The deliverable is a written decision + a revised spec (or revised park note). Implementation is a separate /ship.
- **No vault file moves.** Especially do NOT move anything out of `wiki/context/` even if you decide Path A is right — that's Ship 1's job, not this session's.
- **Use the binary named-bug discipline** (L-2026-04-22-007). Any new MVP must name a real bug alive today with pass/fail conditions Keith can verify.
- **Cite evidence.** Every "alive" or "dead" claim in Step 1 must point at a file path + line number or a count + command that produced it. No vibes.
- **K2B PM hat.** This is Scope B (K2B self-improvement) so K2B may propose freely per L-2026-04-22-006. But the audit must be honest about weak signals, not maximalist.

## Stop and ask Keith if...

- The audit shows all 3 bugs are dead. (Counter-intuitive; sanity-check before recommending Path D.)
- The audit suggests Path A but the migration cost estimate exceeds 8 hours of /ship work. (Worth scoping smaller alternatives first.)
- A bug is alive but the named example is sensitive to misclassify (e.g. a real retrieval miss on Keith-domain data). Keith should verify the example before it goes into the spec.
- Step 2's "right path" is ambiguous between two options.

## Output format expected

End of session, produce a brief inline summary back to Keith:

```
Audit verdict:
  Bug 1 (junk drawer): [dead | theoretical | biting] -- evidence: <file:line or count>
  Bug 2 (index drift): [dead | theoretical | biting] -- evidence: <file:line or count>
  Bug 3 (review rot):  [dead | theoretical | biting] -- evidence: <file:line or count>

Recommended path: [A | B | C | D] -- one-line rationale

Output file: <path>
Updates summary: <what changed in the spec/index/log>
```

No `/ship` this session. The output is a written spec, not code. The next session implements (if Path A or B) or moves on (Path C or D).

---

## Prompt to paste into the fresh session

```
Read ~/Projects/K2B/plans/2026-05-12_vault-three-zones-audit-respec.md and execute it. This is a Scope B audit-and-re-spec task for feature_vault-three-zones. Read the mandatory pre-read files first, then audit each named bug against current data, then recommend a path (A/B/C/D), then produce the written output. No code changes. K2B PM hat.
```
