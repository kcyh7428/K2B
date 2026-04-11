---
name: k2b-ship
description: End-of-session shipping workflow -- runs Codex pre-commit review, commits, pushes, updates the feature note, updates wiki/concepts/index.md lane membership, appends DEVLOG.md and wiki/log.md, suggests next Backlog promotion, and reminds Keith to /sync. Use when Keith says /ship, "ship it", "wrap up", "end of session", "done shipping", or at the natural end of a build session where code was modified.
---

# K2B Ship

Keystone skill for shipping discipline. Replaces the manual Session Discipline checklist with an enforceable workflow that keeps `wiki/concepts/index.md` (the canonical roadmap) honest.

## When to Trigger

**Explicit:** Keith says `/ship`, "ship it", "ship this", "wrap up", "end of session", "done shipping", "close out", "commit and push this".

**Proactive prompt:** At the natural end of any session where K2B modified code in `.claude/skills/`, `CLAUDE.md`, `K2B_ARCHITECTURE.md`, `k2b-remote/`, `scripts/`, `k2b-dashboard/`, or a feature note moved into `in-progress` or `shipped` state -- say: "We have uncommitted changes in [list]. Want me to /ship?"

**Do NOT auto-ship.** Always confirm the commit message and the Codex findings before committing.

## When NOT to Use

- Vault-only changes (daily notes, inbox processing, content drafts) -- these sync via Syncthing, no commit needed
- Emergency hotfixes where Keith explicitly says "just commit, skip review"
- When the user is mid-implementation and just wants an interim checkpoint -- they should say `/commit` or commit manually

## Commands

- `/ship` -- full workflow with Codex review + feature note updates + roadmap updates
- `/ship --skip-codex <reason>` -- skip Codex review with a recorded reason (must provide reason)
- `/ship --no-feature` -- ship code without touching feature notes or the roadmap (e.g. typo fix, config tweak)
- `/ship status` -- show what would ship without actually shipping

## Workflow

### 1. Scope detection

Run in parallel:

```bash
git status
git diff --stat
git log -5 --oneline
```

Categorize touched files into:

| Category | Matching paths | Needs /sync? |
|----------|---------------|--------------|
| skills   | `.claude/skills/`, `CLAUDE.md`, `K2B_ARCHITECTURE.md` | yes |
| code     | `k2b-remote/`, `k2b-dashboard/` | yes |
| scripts  | `scripts/` | yes |
| hooks    | `scripts/hooks/` | yes |
| vault    | `K2B-Vault/` | no (Syncthing) |
| plans    | `.claude/plans/` | no |
| devlog   | `DEVLOG.md` | no |

If there are NO changes at all, report "No changes to ship" and stop.

### 2. Identify the feature being shipped

Read `K2B-Vault/wiki/concepts/index.md`, find the **In Progress** lane.

- If exactly one feature is In Progress -> that is the candidate feature
- If zero features are In Progress -> ask Keith whether this ships under an existing Backlog feature (and if so, which), or is infrastructure work with no feature attached (`--no-feature`)
- If multiple features are In Progress (shouldn't happen per lane rules) -> ask Keith to disambiguate

For multi-ship features (e.g. `feature_mission-control-v3`), read the feature note's Shipping Status table. Identify the current ship row (`in-flight` / `in progress`). Ask Keith to confirm which ship this commit completes.

### 3. Codex pre-commit review gate

**Mandatory unless `--skip-codex <reason>` is passed.** Per CLAUDE.md Checkpoint 2.

```
/codex:review
```

on the uncommitted diff. Capture findings.

- Present findings neutrally to Keith. Do not argue with Codex (per CLAUDE.md).
- Keith decides: fix now, defer, or accept. If he fixes, re-run /codex:review on the new diff.
- Log the gate result: `reviewed / skipped:<reason>`, number of findings, fix verdict.

If Codex plugin is not configured: fail loudly with "Run `/codex:setup` or re-run with `/ship --skip-codex <reason>`."

### 4. Generate commit message

Build a commit message from the categorized diff. Format:

```
<type>: <short summary>

<optional body with bullet points of major changes>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```

Types: `feat`, `fix`, `refactor`, `docs`, `chore`, `infra`. **Never use em dashes** (K2B rule).

Show Keith the draft. Confirm before committing.

### 5. Stage + commit + push

Stage every file this session touched, regardless of category. The category table in step 1 is for `/sync` routing decisions, not for gating staging -- a touched file in `docs/`, `allhands/`, or any other uncategorized path still gets staged if it belongs to this session. Files in the working tree that predate the session and were not touched in this session must NOT be staged.

```bash
# Stage only the files we know about -- no git add -A (active rule: sensitive file avoidance)
git add <each file this session touched, from step 1 git status>
git commit -m "$(cat <<'EOF'
<message from step 4>
EOF
)"
git push origin main
```

Never pass `--no-verify`. Never pass `--amend` unless Keith explicitly asked. If pre-commit hooks fail, fix the underlying issue and create a NEW commit.

Capture the commit SHA.

### 6. Update the feature note

If `--no-feature` was passed, skip this step.

Read the feature note at `K2B-Vault/wiki/concepts/feature_<slug>.md`.

**Single-ship feature (no Shipping Status table):**
- Update frontmatter: `status: shipped`, add `shipped-date: YYYY-MM-DD`
- Append an `## Updates` section entry with: date, commit SHA, one-line what shipped, Codex findings summary, any follow-ups
- Move the file to `K2B-Vault/wiki/concepts/Shipped/feature_<slug>.md`

**Multi-ship feature (has Shipping Status table, e.g. mission-control-v3):**
- Do NOT set the top-level `status: shipped` -- only the current ship is done
- Update the Shipping Status table row for the current ship: mark `shipped: YYYY-MM-DD`, set `state: in-measurement` (or `state: gate-passed` if no measurement window), set gate date if applicable
- Append an `## Updates` entry with ship details, commit SHA, Codex findings
- If this was the final ship in the plan AND it has passed its gate, THEN set feature-level `status: shipped` and move to `Shipped/`. Otherwise leave in place.

### 7. Update `wiki/concepts/index.md`

Load the index, locate the feature's row, move it between lanes:

- **Single-ship feature shipped:** Remove from In Progress, add to Shipped with `shipped-date`. If Shipped now has more than 10 rows, move the oldest one's wiki-link target file into `Shipped/` (update its `up:` still points to `[[index]]`, but the wiki-link in the index now references `Shipped/feature_<slug>`).
- **Multi-ship feature, ship complete but feature not done:** Update In Progress row to show the new ship state (`Ship N (in measurement, gate YYYY-MM-DD)`). Do not move.
- **Multi-ship feature, final ship complete and gate passed:** Move to Shipped lane as above.

Also update `Last updated: YYYY-MM-DD` at top of index.

### 8. Append DEVLOG.md and create follow-up commit

`DEVLOG.md` is tracked in git at project root, so appending to it creates dirty state that must be committed. Because the entry needs to reference the code commit's SHA (captured in step 5), this is always a two-commit flow: code first, devlog second.

Read the last DEVLOG entry for style. Append a new entry:

```markdown
## YYYY-MM-DD -- <one-line title>

**Commit:** `<short-sha>` <commit message title>

**What shipped:** <one paragraph>

**Codex review:** <findings summary or "skipped: <reason>">

**Feature status change:** <feature slug> <status-from> -> <status-to>

**Follow-ups:** <bullets, or "none">

**Key decisions (if divergent from claude.ai project specs):** <bullets, or "none">
```

Then commit and push as a standalone devlog commit (matches the repo's existing pattern, e.g. `dc2ba69 docs: devlog for active rules staleness detection`):

```bash
git add DEVLOG.md
git commit -m "$(cat <<'EOF'
docs: devlog for <short-sha>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

Never `--amend` the step-5 commit to include DEVLOG.md -- amends rewrite history and can drop signed state. Always create a new commit.

If shipping multiple logical changes in one session (two or more code commits back-to-back), batch all their DEVLOG entries into a single follow-up `docs: devlog` commit after the last code commit, referencing each code SHA in its own entry.

### 9. Append wiki/log.md

Per Active Rule 13. Single-line entry:

```
YYYY-MM-DD HH:MM  /ship  <feature-slug>  <short-sha>  <summary>
```

### 10. Multi-ship gate handling

If the feature has a Shipping Status table and this ship has a gate scheduled (per minimax-offload phase gate pattern):

- Remind Keith: "Ship X of Y done. Gate review scheduled for YYYY-MM-DD. Nothing else should start on Ship X+1 until the gate passes."
- Offer to create a scheduled task via `/schedule` if the gate review is not already scheduled: the task should run `/observe` and the phase gate checklist from the feature note, then Telegram Keith the go/no-go summary.

### 11. Promote next Backlog item to Next Up (only for single-ship ships or final-ship ships)

If the just-shipped feature was removed from In Progress (leaving In Progress empty):

- Read Next Up lane. Count items.
- If Next Up has fewer than 3 items, look at the top of Backlog (sorted by priority then effort).
- Suggest to Keith: "Backlog top candidate: `feature_X`. Promote to Next Up? [Y/n]"
- On Y: move the row from Backlog to Next Up in `wiki/concepts/index.md`, ask Keith for a "Why now" reason for the Next Up table.
- **Never auto-promote.** Always require explicit confirmation.

### 12. Project file change detection -> /sync reminder

If any files in categories `skills`, `code`, `scripts`, `hooks` were in the diff:

> These changes are on your MacBook only. Run `/sync` to push to Mac Mini.

Do NOT auto-sync. Per Active Rule L-2026-03-29-002, never manual rsync.

### 13. Usage logging

```bash
echo -e "$(date +%Y-%m-%d)\tk2b-ship\t$(echo $RANDOM | md5sum | head -c 8)\tshipped FEATURE_SLUG SHORT_SHA" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Error Handling

- **Pre-commit hook fails** -> fix the underlying issue (per Active Rule 8, never `--no-verify`), re-stage, create a NEW commit (never `--amend`).
- **Push fails (not a force-push scenario)** -> investigate. Fetch, check if the branch diverged, ask Keith how to reconcile.
- **Codex plugin missing** -> loud failure with next-step instruction; do not silently skip.
- **Feature note not found** -> ask Keith which feature this belongs to, or offer to ship as `--no-feature`.
- **`wiki/concepts/index.md` parse failure** -> fail loudly, point Keith at the file, do not guess the lane structure.
- **DEVLOG.md / wiki/log.md append failure** -> commit has already landed, so degrade gracefully: print the entry Keith should add manually, continue with the rest of the workflow.

## What /ship Does NOT Do

- Auto-sync to Mac Mini (Keith must run `/sync` explicitly)
- Edit vault files other than the feature note, `wiki/concepts/index.md`, `wiki/log.md`, `DEVLOG.md`, and the skill-usage-log
- Overwrite `store/` (production SQLite on Mac Mini)
- Touch `.env` files
- Force-push, amend existing commits, rebase, or use any destructive git operation
- Run deployment scripts

## Notes

- `/ship` is intentional, not a hook. Shipping is a human-in-the-loop action.
- The Codex pre-commit review gate is mandatory per CLAUDE.md. Skipping requires a recorded reason.
- `wiki/concepts/index.md` is the source of truth. `/ship` is how state transitions get written safely -- never edit lane membership by hand mid-session.
- For multi-ship features, the Shipping Status table and phase gate pattern (modeled on `project_minimax-offload`) stay authoritative. `/ship` updates rows within it; it does not replace the table.
- `/ship --no-feature` is the escape hatch for infrastructure commits that don't map to a feature (e.g. fixing CI, rotating a credential). Use sparingly.
