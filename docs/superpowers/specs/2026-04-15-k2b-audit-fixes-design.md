# K2B Audit Fixes -- Design Spec

**Date:** 2026-04-15
**Author:** Keith (direction) + Claude Opus 4.6 (synthesis)
**Source audit:** [[k2b-audit]] at `K2B-Vault/wiki/projects/k2b-investment/k2b-audit.md`
**Raw audit report:** [[2026-04-15_k2b-audit]] at `K2B-Vault/raw/research/k2b-investment/2026-04-15_k2b-audit.md`
**Status:** design -- awaiting Keith review before writing-plans handoff

## Context

The K2B audit was originally framed as "what to inherit into K2B-Investment, and what to fix first so the clone starts clean." But the fixes apply to K2B itself. This spec targets K2B. Once these 8 land, K2B-Investment can clone cleanly.

The audit identifies three systemic failure modes visible in K2B's error log:

1. **Advisory rules violated under cognitive load** -- e.g. `active_rules.md:24` "NEVER manual rsync", reinforced 3x, still violated (E-2026-03-31-001).
2. **Bookkeeping steps dropped when cognitive budget runs out** -- e.g. the compile 4-index rule, reinforced twice (L-2026-04-08-001, L-2026-04-12-002) and still failing (E-2026-04-12-001).
3. **Location rules living in skill bodies where the promoter can't see them** -- e.g. shipped-file-location rule, in `k2b-ship/SKILL.md:128` but missing from `active_rules.md` (L-2026-04-14-001).

The 8 fixes below target these failure modes via mechanism, not exhortation: single-writer helpers, atomic operations, code-enforced hard rules, auto-promotion with cap/LRU.

## Keith's ordering (dependency-aware)

```
1. Fix #4 CLAUDE.md cleanup              (pure strip, nothing depends on nothing)
2. Fix #1 wiki/log.md single-writer      (13 call-sites, needs /ship + test)
3. Fix #7 memory layer ownership         (depends on #4 -- can't enforce ownership until duplicates are stripped)
4. Fix #3 auto-promote 3x rules in /ship (touches k2b-ship, needs eval)
5. Fix #2 atomic 4-index helper          (touches k2b-compile, needs eval)
6. Fix #6 observer idempotency marker    (touches k2b-observer + session-start flow)
7. Fix #5 active_rules LRU cap rule      (trivial edit, but #3 depends on it)
8. Fix #8 git pre-commit hook            (catches regressions for #1 and #3's side effects)
```

**Note on ordering vs dependencies:** Keith's implementation order is #4, #1, #7, #3, #2, #6, #5, #8. But Fix #3 actually DEPENDS on Fix #5 (needs the LRU cap rule written down to apply it deterministically). During implementation, Fix #5's `active_rules.md` line-1 edit must land before Fix #3's `/ship` step 0 goes live. Two options:

- **Option A:** reorder to #4, #1, #7, #5, #3, #2, #6, #8 (strict dependency order).
- **Option B:** keep Keith's order but land Fix #5's LRU text as a prerequisite commit within Fix #3's work.

Recommendation: **Option B** -- keeps Keith's stated order, recognizes that #5 is a ~5-line edit that rides inside #3's branch.

## Fix #4 -- CLAUDE.md cleanup (pure strip pass)

**Goal:** remove procedural content from `CLAUDE.md`. Identity + taxonomy + soft rules stay. Every "how" moves into a skill body (or is verified already there and just deleted from CLAUDE.md).

**Strips:**

| # | Section | Approx lines | Action | Target home |
|---|---|---|---|---|
| A | Video Feedback via Telegram (run-level) | 40 | DELETE; replace with 1-line pointer to `[[k2b-review]]` | `k2b-review/SKILL.md` already contains flock/atomic rename/jq playlist logic, verify and expand if needed |
| B | Email Safety (4-line block) | 4 | DELETE; replace with 1-line pointer | `k2b-email/SKILL.md` |
| C | Codex Adversarial Review (two numbered checkpoints + when-to-skip + rules) | ~28 | DELETE procedural content; keep one-sentence statement | Codex plugin + `k2b-ship/SKILL.md` |
| D | Session Discipline long fallback block | ~15 | Keep 2-sentence "run /ship" mandate, DELETE manual-fallback recipe | `k2b-ship/SKILL.md` body |
| E | Session Start & Observer > Inline Observer Confirmation (detailed 3-option procedure) | ~20 | DELETE HIGH/MEDIUM decision recipe; keep 2-line pointer | `k2b-observer/SKILL.md` |
| F | Obsidian Cross-Linking + File Conventions | keep | -- | These ARE taxonomy, they belong in CLAUDE.md |
| G | Slash Commands (command index) | keep | -- | Command index stays |

**Chosen approach:** full 7-row strip (Option 2 from brainstorming). For strips C and E where the target skills may be missing procedural content, first verify and copy-into-skill if missing, then delete from CLAUDE.md. Same session.

**Expected shrink:** ~287 -> ~180 lines (well under the <400 line target from the audit).

**Implementation notes:**

- Before deleting strip C content: read `k2b-ship/SKILL.md` to confirm the Codex pre-commit review checkpoint is described there. If not, move the two numbered checkpoints + when-to-skip + rules into `k2b-ship/SKILL.md` section 2 or equivalent, THEN delete from CLAUDE.md.
- Before deleting strip E content: read `k2b-observer/SKILL.md` to confirm it has the HIGH/MEDIUM/REJECT inline confirmation recipe. If not, add a new "Session-start inline confirmation" section in k2b-observer before deleting from CLAUDE.md.

**Blast radius:** low. Pure strip. No skill logic changes. Readers follow pointers.

## Fix #1 -- wiki/log.md single-writer helper

**Goal:** all `wiki/log.md` appends go through one helper. No skill ever `>>`-appends directly. Prevents interleaved-write corruption and lets us change log format in one place.

**Current state:** 13 call-sites across 13 skills (k2b-compile, k2b-daily-capture, k2b-insight-extractor, k2b-linkedin, k2b-lint, k2b-meeting-processor, k2b-research, k2b-review, k2b-ship, k2b-tldr, k2b-vault-writer, k2b-weave, k2b-youtube-capture). Each using its own append pattern.

**New file:** `scripts/wiki-log-append.sh`

```bash
#!/usr/bin/env bash
# Single writer for ~/Projects/K2B-Vault/wiki/log.md
# Usage: wiki-log-append.sh <skill> <action> <summary>
# Example: wiki-log-append.sh /compile raw/research/foo.md "updated 3 wiki pages"

set -euo pipefail
SKILL="${1:?skill required}"
ACTION="${2:?action required}"
SUMMARY="${3:?summary required}"

LOG="$HOME/Projects/K2B-Vault/wiki/log.md"
LOCK="/tmp/k2b-wiki-log.lock"
TS="$(date '+%Y-%m-%d %H:%M')"
LINE="$TS  $SKILL  $ACTION  $SUMMARY"

# flock on macOS: stock macOS ships without flock. Use mkdir fallback.
# (MEDIUM-3 in plans/ already flagged this.)
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -x 9
  printf '%s\n' "$LINE" >> "$LOG"
else
  # macOS fallback: mkdir is atomic
  while ! mkdir "$LOCK.d" 2>/dev/null; do sleep 0.05; done
  trap 'rmdir "$LOCK.d"' EXIT
  printf '%s\n' "$LINE" >> "$LOG"
fi
```

**Log format enforced by helper** (single source of truth):

```
YYYY-MM-DD HH:MM  <skill>  <action>  <summary>
```

**Migration of 13 call-sites:** each skill currently has prose like "Append to wiki/log.md" in SKILL.md. Replace with "Call `scripts/wiki-log-append.sh <skill> <action> <summary>`". If the skill currently spells out a heredoc or `obsidian_append_content`, replace with the script call.

**Migration order** (low-risk first):

1. k2b-ship -- called every ship, immediate feedback
2. k2b-compile -- heavy user
3. k2b-review -- the Liam Ottley bug lived here, fix early
4. Remaining 10 skills alphabetical

**Test plan:**

- Unit: run `wiki-log-append.sh test test "hello"` 10x in parallel, confirm 10 lines appear, no interleaving, no lock leaks.
- Integration: run `/ship` on a trivial change, verify exactly one entry lands in new format.
- Regression: run `/compile` on a raw source, verify both helpers (4-index from Fix #2, log-append from Fix #1) play nicely.

**Design choices (locked in):**

- (a) Script at `scripts/wiki-log-append.sh` -- flat, consistent with other scripts.
- (b) Lockfile at `/tmp/k2b-wiki-log.lock` -- consistent with `/tmp/k2b-review-videos.lock`.
- (c) 3-arg signature `<skill> <action> <summary>` -- normalizes all call-sites. Richer multi-field entries get flattened into `<summary>`.
- (d) Enforcement via Fix #8 -- the pre-commit hook will grep for `>>.*wiki/log\.md` in staged diff and reject. Kills the last escape hatch.

## Fix #7 -- memory layer ownership enforcement

**Goal:** every fact has exactly one home. Kill the "compile-4-index rule lives in 4 places" problem.

**Depends on Fix #4** -- can't enforce ownership until duplicates are stripped.

**The ownership matrix** (from audit lines 92-101):

| Fact type | Single home | Loaded at session start? |
|---|---|---|
| Soft rules (no em dashes, no AI cliches, tone) | `CLAUDE.md` top-level prose | yes |
| Hard rules (rsync, manual feature-status edits) | Code -- pre-sync script + pre-commit hook | enforced, not loaded |
| Domain conventions (file naming, frontmatter, taxonomy) | `CLAUDE.md` File Conventions section | yes |
| Skill how-tos (flock patterns, atomic rename, multi-step procedures) | The skill's SKILL.md body | yes (on skill invoke) |
| Auto-promoted learned preferences | `active_rules.md` (cap 12, LRU) | yes |
| Raw learnings history | `self_improve_learnings.md` | no -- reference only |
| Raw errors history | `self_improve_errors.md` | no -- reference only |
| Memory index (pointers only) | `MEMORY.md` | yes |
| Index/log mutations | Single helper function (one flock holder each) | enforced |

**Deliverables:**

1. **Add "Memory Ownership Rule" section to CLAUDE.md** (~30 lines). The matrix above + three day-one consequences: no procedural content in CLAUDE.md; hard rules ship as code; single-writer hubs. Placed right before the "Rules" section.
2. **`scripts/audit-ownership.sh`** -- reads `scripts/ownership-watchlist.yml` listing known rule phrases + canonical home, greps K2B + vault for drift, exits non-zero if the same phrase appears in >1 location outside its canonical home.
3. **Wire into `/ship` step 0 as advisory warning** -- doesn't block, just prints `"⚠ ownership drift detected: 'X' appears in both A and B"`. Keith decides fix-inline or defer.
4. **Delete known duplicates** after Fix #4 runs:
   - `self_improve_learnings.md` L-2026-04-08-001 -- keep as historical record only, add pointer `"-> active_rules.md rule 7 for the live rule"`.
   - Same for `self_improve_errors.md` E-2026-04-12-001.
   - Add banner at line 1 of both learnings + errors files: `"This file is a historical record. Live behavior rules live in active_rules.md. Nothing here affects K2B behavior unless promoted."`

**New file:** `scripts/audit-ownership.sh` + `scripts/ownership-watchlist.yml`

**Design choices (locked in):**

- (a) Matrix lives in CLAUDE.md inline (not a standalone doc) -- it's identity-level, read every session.
- (b) `self_improve_learnings.md` stays as historical log with explicit banner; not a behavior source.
- (c) Audit script scope: K2B repo + K2B-Vault (via symlink path).

## Fix #3 -- auto-promote 3x-reinforced rules in /ship

**Goal:** no more manual audits. A learning reinforced 3+ times gets auto-promoted to `active_rules.md` at ship time.

**Current bug:** shipped-file-location rule (L-2026-04-14-001) is reinforced 3x per `self_improve_learnings.md:11-17` yet never made it into `active_rules.md`. Fix #3 closes this gap permanently.

**Implementation:** new **step 0** in `/ship` before anything else. Called "Active rules auto-promotion scan."

**Scan algorithm:**

1. Read `~/Projects/K2B-Vault/System/memory/self_improve_learnings.md` (symlinked path).
2. For each learning entry, extract `reinforcement-count:` frontmatter field. Fall back to grepping body text for "reinforced Nx" if frontmatter absent.
3. For any learning with count >= 3 whose distilled rule is NOT already in `active_rules.md`:
   - Present inline: `"L-2026-04-14-001 has been reinforced 3x and is not in active_rules. Promote now? [y/n/skip]"`
   - On `y`: append new rule to `active_rules.md` with distilled text
   - On `n`: mark learning with `auto-promote-rejected: true`, don't ask again
   - On `skip`: do nothing, ask again next ship
4. If cap of 12 would be exceeded, trigger LRU demotion (Fix #5 policy) -- least-reinforced-in-last-30-days rule demoted to `self_improve_learnings.md` "Demoted Rules" section with `demoted-date:` annotation. Show Keith before committing.
5. Log all promotions/demotions to `wiki/log.md` via Fix #1 helper.

**Why inline confirmation, not fully automatic:** phrasing a learning as a rule is a judgment call. Auto-promoting without Keith-in-the-loop risks bad rule copy. Inline gives him 2-second yes/no -- same pattern as inline observer confirmation.

**Distilled rule text sources (priority order):**

1. `distilled-rule:` frontmatter field on the learning -- use directly.
2. First bold sentence in the learning body (convention: TL;DR is bolded first).
3. Fall back to printing full learning and asking Keith to write inline.

**Recommendation:** require `distilled-rule:` field going forward. Update `k2b-feedback/SKILL.md` so `/learn` sets this field on capture. Old learnings need manual distillation on first promotion -- one-time backfill.

**Eval additions** (new `.claude/skills/k2b-ship/eval/` cases):

- Plant synthetic learning with `reinforced 3x` not in active_rules. Assert: output prompts for promotion.
- Plant a 13th rule being promoted over cap 12. Assert: output proposes LRU demotion.
- Plant learning with `auto-promote-rejected: true`. Assert: output skips silently.

**Design choices (locked in):**

- (a) Scan at ship time only -- not session start (session start is noisy enough).
- (b) Counting via `reinforcement-count:` frontmatter, body-grep fallback.
- (c) Demoted rules move to `self_improve_learnings.md` "Demoted Rules" section at bottom with `demoted-date:` frontmatter.

**Prerequisite:** Fix #5 must land first (or within same branch) so the LRU cap policy is written down.

## Fix #2 -- atomic 4-index helper for /compile

**Goal:** compile's 4-index-update step is the most-reinforced learning in K2B history and still fails under cognitive load. Turn from 4 advisory text steps into ONE function call.

**The 4 indices:**

1. `wiki/<subfolder>/index.md` -- page count + recent entries
2. `raw/<subfolder>/index.md` -- source count + recent entries
3. `wiki/index.md` -- master counts table
4. `wiki/log.md` -- via Fix #1 helper (sub-call)

**New file:** `scripts/compile-index-update.py` (Python, not bash -- frontmatter + count recomputation is annoying in bash)

**Sketch:**

```python
#!/usr/bin/env python3
"""Atomic 4-index update for /compile.

Usage: compile-index-update.py <raw-source-path> <wiki-pages-updated> <wiki-pages-created>
Example: compile-index-update.py raw/research/foo.md "wiki/projects/a.md,wiki/people/b.md" "wiki/concepts/c.md"
"""
import sys, os, subprocess, tempfile, shutil
from pathlib import Path

VAULT = Path.home() / "Projects" / "K2B-Vault"
LOCK = "/tmp/k2b-compile-index.lock"

def acquire_lock():
    # mkdir atomic fallback (see Fix #1)
    ...

def main():
    raw_path, updated, created = sys.argv[1], sys.argv[2], sys.argv[3]
    acquire_lock()
    try:
        # Stage 1: compute all 4 deltas into temp files (no mutation yet)
        tmpdir = tempfile.mkdtemp()
        wiki_sub = compute_wiki_subfolder_index(tmpdir, updated, created)
        raw_sub = compute_raw_subfolder_index(tmpdir, raw_path)
        master = compute_master_index(tmpdir)
        validate(wiki_sub, raw_sub, master)  # parse checks

        # Stage 2: atomic-rename all 4 into place
        shutil.move(wiki_sub, VAULT / "wiki" / subfolder_of(updated) / "index.md")
        shutil.move(raw_sub, VAULT / "raw" / subfolder_of(raw_path) / "index.md")
        shutil.move(master, VAULT / "wiki" / "index.md")

        # Stage 3: append to wiki/log.md via Fix #1 helper
        subprocess.run([
            os.path.dirname(__file__) + "/wiki-log-append.sh",
            "/compile", raw_path, f"updated: {updated} | created: {created}"
        ], check=True)

        print("compile-index-update: ok")
    finally:
        release_lock()
```

**Atomicity property:** stage 1 computes everything into temp files. Stage 2 does 4 renames. If stage 1 fails (malformed index, bad parse), nothing has moved. If stage 2 fails partway (disk full), 1-3 of 4 might be updated -- accepted risk for now.

**Chosen atomicity model:** Option 1 "best effort atomic" (rename one-by-one). Not the journaled Option 2. Reasoning: same-filesystem renames essentially don't partial-fail except on disk-full, which is a bigger problem than a half-compile.

**Migration of k2b-compile SKILL.md:**

- Delete steps 1-4 of the current "Update all 4 indices" procedural block.
- Replace with: `"Call scripts/compile-index-update.py <raw-source-path> <updated-pages> <created-pages>. This is the only permitted way to update indices during compile. Do not hand-edit any index file during a compile run."`
- Update eval `eval.json:33` to check that the script is called, not that all 4 points are enumerated.

**Eval additions:**

- Plant compile scenario. Assert: output calls `compile-index-update.py` exactly once.
- Plant malformed `wiki/index.md`. Assert: script exits non-zero without mutating any index file.

**Design choices (locked in):**

- (a) Option 1 best-effort atomic (not journaled).
- (b) Script computes content itself (reads old, recomputes counts, writes temp). Claude never generates index content during compile.
- (c) Python, not bash.

## Fix #6 -- observer idempotency marker

**Goal:** stop double-processing observer findings between session-start inline confirmation and `/observe` deep synthesis.

**Current state:** `preference-signals.jsonl` has no `processed_at:` field. Session-start inline confirmation acts on signals but never marks them. `/observe harvest` re-reads the whole file every pass.

**Schema addition:**

New field on every signal written going forward: `signal_id` -- content hash. Format: `sha256(date + source + description)[:8]`. Example `"a3f7b2c1"`.

After action is taken on a signal, a NEW line is appended (append-only is preserved):

```json
{"type":"signal-processed","signal_id":"a3f7b2c1","at":"2026-04-15T21:30:00+08:00","by":"session-start-inline","action":"confirmed","learn_id":"L-2026-04-15-003"}
```

Actions: `confirmed` | `rejected` | `watching`. Match CLAUDE.md:184-194 three inline options.

**Writers:**

- **session-start inline flow** -- on Keith's confirm/reject decision
- **`/observe` deep synthesis** -- when it promotes a pattern to a learning
- **background observer loop** -- NEVER writes `processed`, only appends new signals

**Reading logic:**

1. Stream the jsonl, bucket lines by `signal_id`.
2. A signal is "processed" if there exists any `type:"signal-processed"` line with the same `signal_id`.
3. Both session-start inline flow AND `/observe` filter out processed signals before acting.

**Migration -- existing signals have no signal_id:** grandfather. Write a one-shot line at top of jsonl:

```json
{"type":"grandfather-cutoff","at":"2026-04-15","note":"signals before this line are not subject to dedup -- treat as already processed"}
```

Simple, honest, no mutation of append-only data.

**New file:** `scripts/observer-mark-processed.sh` -- thin wrapper that appends the `signal-processed` line with flock.

```bash
#!/usr/bin/env bash
# Usage: observer-mark-processed.sh <signal_id> <action> [learn_id]
set -euo pipefail
SIG="${1:?signal_id required}"
ACTION="${2:?action required}"  # confirmed|rejected|watching
LEARN="${3:-}"
JSONL="$HOME/Projects/K2B-Vault/wiki/context/preference-signals.jsonl"
LOCK="/tmp/k2b-preference-signals.lock"
TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if [ -n "$LEARN" ]; then
  LINE=$(printf '{"type":"signal-processed","signal_id":"%s","at":"%s","by":"session-start-inline","action":"%s","learn_id":"%s"}' "$SIG" "$TS" "$ACTION" "$LEARN")
else
  LINE=$(printf '{"type":"signal-processed","signal_id":"%s","at":"%s","by":"session-start-inline","action":"%s"}' "$SIG" "$TS" "$ACTION")
fi

# Lock + append (same pattern as wiki-log-append.sh)
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"; flock -x 9
  printf '%s\n' "$LINE" >> "$JSONL"
else
  while ! mkdir "$LOCK.d" 2>/dev/null; do sleep 0.05; done
  trap 'rmdir "$LOCK.d"' EXIT
  printf '%s\n' "$LINE" >> "$JSONL"
fi
```

**Updates required:**

- `scripts/observer-loop.sh` -- add `signal_id` computation to jsonl append
- `k2b-observer/SKILL.md` Phase 1a -- add "filter out processed signals" step
- CLAUDE.md session-start observer section (OR `k2b-observer/SKILL.md` if Fix #4 strip E already moved it) -- add `"after confirming, call observer-mark-processed.sh"` step

**Design choices (locked in):**

- (a) `signal_id` = sha256 of (date + source + description), first 8 hex chars.
- (b) Separate helper script `observer-mark-processed.sh`, not inline append (one writer per hub).
- (c) Grandfather existing signals via cutoff marker. Don't backfill.

## Fix #5 -- LRU cap rule written into line 1 of active_rules.md

**Goal:** cap of 12 is stated in the audit but not the file. Without written policy, Fix #3 can't apply it deterministically.

**Edit target:** `~/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/active_rules.md` (symlinked to vault via memory sync).

**After edit:**

```markdown
# Active Rules

**Cap: 12 rules. LRU demotion policy:** when `/ship` auto-promotes a rule (reinforced 3x, per Fix #3) and this file would exceed 12 entries, the least-reinforced-in-last-30-days rule is demoted to `self_improve_learnings.md` under a "Demoted Rules" section. "Reinforced in last 30 days" = any `/learn` call or observer finding that re-cites the rule's ID within 30 days of today. Ties broken by last-reinforced date descending, then total reinforcement count descending, then L-ID alphabetical. Demotion is automatic inside `/ship`, surfaced to Keith as `"⚠ demoting rule N (<title>) to make room for <new rule>"`. No manual override needed; `/learn` can re-promote by reinforcing again.

Last promoted: 2026-04-12
Last audited: 2026-04-12 (manual -- pruned 14 -> 7, demoted 3 to MEMORY/CLAUDE.md, merged 2, moved 1 to YouTube agent backlog)
```

**Companion edit:** each existing rule's parenthetical gets `last-reinforced:` added. Example:

```
4. **NEVER manual rsync for k2b-remote.** ... (L-2026-03-29-002, reinforced 3x, last-reinforced: 2026-04-08)
```

**Backfill:** the 9 existing rules need `last-reinforced` added. One-time manual edit using original promotion date as default.

**Design choices (locked in):**

- (a) LRU text placed right after H1, before "Last promoted" -- session-start visible, Fix #3 parseable.
- (b) "Reinforced in last 30 days" = `/learn` calls citing the rule's L-ID only. Observer fuzzy matching is flaky, excluded.
- (c) Tiebreaker: last-reinforced date desc, total count desc, L-ID alpha.

## Fix #8 -- git pre-commit hook for status edits

**Goal:** block any commit that modifies `status:` line in `wiki/concepts/feature_*.md` unless the commit came from `/ship`. Closes L-2026-04-14-001: Keith strengthened the rule and broke it in the same session.

**Also carries:** `>>.*wiki/log\.md` direct-append check (closes Fix #1's last escape hatch).

**Installation pattern:** `core.hooksPath` pointing to repo-tracked `.githooks/` directory. One-line install, hook is version-controlled.

**New file:** `.githooks/pre-commit`

```bash
#!/usr/bin/env bash
# .githooks/pre-commit -- enforces /ship-only status edits + single-writer log hub
set -euo pipefail

# ---- Check 1: status: line edits in feature files ----
STATUS_CHANGES=$(git diff --cached --unified=0 -- \
  'K2B-Vault/wiki/concepts/feature_*.md' \
  'K2B-Vault/wiki/concepts/Shipped/feature_*.md' \
  2>/dev/null | grep -E '^[+-]status:' || true)

if [ -n "$STATUS_CHANGES" ]; then
  COMMIT_MSG_FILE="$(git rev-parse --git-dir)/COMMIT_EDITMSG"
  if ! grep -q '^Co-Shipped-By: k2b-ship' "$COMMIT_MSG_FILE" 2>/dev/null; then
    if [ "${K2B_ALLOW_STATUS_EDIT:-}" != "1" ]; then
      echo "error: status: line modified in wiki/concepts/feature_*.md outside /ship"
      echo ""
      echo "Changed status lines:"
      echo "$STATUS_CHANGES"
      echo ""
      echo "Use /ship to transition feature status. Override: K2B_ALLOW_STATUS_EDIT=1 git commit ..."
      exit 1
    fi
    echo "warning: status: line edited outside /ship, override acknowledged"
  fi
fi

# ---- Check 2: direct >> append to wiki/log.md ----
LOG_DIRECT=$(git diff --cached | grep -E '^\+.*>>\s*.*wiki/log\.md' || true)
if [ -n "$LOG_DIRECT" ]; then
  echo "error: direct >> append to wiki/log.md detected. Use scripts/wiki-log-append.sh instead."
  echo "$LOG_DIRECT"
  exit 1
fi

exit 0
```

**/ship provenance marker:** add `Co-Shipped-By: k2b-ship` trailer to `/ship` commits. Update `k2b-ship/SKILL.md` step 5 (Stage + commit + push) to append this trailer to the commit message heredoc.

**Override escape hatch:** `K2B_ALLOW_STATUS_EDIT=1` env var for one-off repairs. Prints warning, allows commit. Do NOT make this the default answer -- the whole point is friction.

**Installation:**

```bash
# One-time in each repo:
git config core.hooksPath .githooks
chmod +x .githooks/pre-commit
```

Add to `scripts/install-hooks.sh` so future clones auto-install.

**Test plan** (Keith's note: "test with a deliberate violation"):

1. Install hook.
2. Manually edit a `feature_*.md` to change `status:` value.
3. `git add` + `git commit` -> must fail with error.
4. Run `/ship` on the same change -> must succeed (trailer present).
5. `K2B_ALLOW_STATUS_EDIT=1 git commit` -> must succeed with warning.
6. Stage a change that contains `echo "..." >> wiki/log.md` -> must fail with error.

**Open question (d):** is `.git/hooks/` install needed in both K2B repo AND K2B-Vault repo? The feature files live in the vault. Need to verify vault git status first, install wherever the feature files are version-controlled. If both, both.

**Design choices (locked in):**

- (a) `.githooks/` + `core.hooksPath` (Option A from brainstorming).
- (b) `Co-Shipped-By: k2b-ship` trailer is the provenance signal.
- (c) Same hook catches `>>.*wiki/log\.md` direct appends.
- (d) Install scope -- TBD, verify vault repo status during implementation.

## Summary table

| Fix | Order | Deliverable | New files | Skills touched | Blast radius |
|---|---|---|---|---|---|
| #4 CLAUDE.md cleanup | 1 | ~287 -> ~180 lines | 0 | verify k2b-review, k2b-email, k2b-ship, k2b-observer already hold procedures | low |
| #1 wiki/log.md single-writer | 2 | `scripts/wiki-log-append.sh` | 1 | 13 skills get 1-line replacement | **high** (13 sites) |
| #7 memory ownership | 3 | CLAUDE.md matrix + `scripts/audit-ownership.sh` + `scripts/ownership-watchlist.yml` | 2 | k2b-ship (adds audit call) | medium |
| #3 auto-promote 3x rules | 4 | New `/ship` step 0 + eval cases + `k2b-feedback` adds `distilled-rule:` | 0 | k2b-ship, k2b-feedback | medium (+ eval) |
| #2 4-index atomic helper | 5 | `scripts/compile-index-update.py` + eval cases | 1 | k2b-compile | medium (+ eval) |
| #6 observer idempotency | 6 | `scripts/observer-mark-processed.sh` + schema update + grandfather marker | 1 | k2b-observer, observer-loop.sh, CLAUDE.md session-start section | medium |
| #5 LRU cap rule | 7 | Edit `active_rules.md` line 1 + backfill `last-reinforced:` on 9 existing rules | 0 | 1 file | trivial |
| #8 pre-commit hook | 8 | `.githooks/pre-commit` + `core.hooksPath` config + `scripts/install-hooks.sh` + `Co-Shipped-By` trailer in k2b-ship | 2 | k2b-ship (adds trailer) | low |

## Cross-cutting dependencies

- #7 depends on #4 (can't enforce ownership until duplicates are stripped)
- #3 depends on #5 (needs LRU cap rule written down to apply it) -- see "Ordering note" above
- #8 check 2 closes Fix #1's escape hatch -- implement #1 first so the helper exists, then #8 enforces it
- #1 and #2 share the same mkdir-lock fallback pattern -- keep them consistent

## Open questions to resolve during writing-plans

1. **Fix #4 strip C and E** -- verify target skills (k2b-ship for Codex, k2b-observer for inline confirmation) already hold the procedures. If not, copy-into-skill before deleting from CLAUDE.md.
2. **Fix #7 audit script** -- watchlist YAML format and initial known-duplicate phrases list.
3. **Fix #3 distilled-rule extraction** -- priority order for frontmatter field vs. first-bold-sentence vs. manual prompt. Nail down exact parser.
4. **Fix #2 Python vs bash trade-off** -- confirm Python is the right pick, check if `python3` is reliably available in all contexts where compile-index-update runs.
5. **Fix #6 grandfather cutoff** -- confirm append-only constraint is absolute, or if a one-time backfill is acceptable given the schema evolution.
6. **Fix #8 vault repo install** -- verify whether K2B-Vault has its own `.git/` and whether the hook needs to install there too.

## What this spec does NOT cover

- The 2 audit items from Axis 5 that Keith de-scoped: "NEVER manual rsync" hard rule as code, and the email-safety duplication (Axis 4 row 5). These are lower-leverage or already partially covered elsewhere.
- K2B-Investment scaffolding. This spec fixes K2B. K2B-Investment clones cleanly afterwards -- that's a separate plan in `wiki/projects/k2b-investment/`.

## Next step

Invoke `superpowers:writing-plans` skill to turn this design into an executable implementation plan with step-by-step tasks, verification gates, and review checkpoints per fix.

---

**Spec version:** 1.0
**Design approved by:** (pending Keith review)
**Planned next action:** writing-plans skill handoff
