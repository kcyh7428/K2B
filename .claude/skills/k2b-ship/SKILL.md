---
name: k2b-ship
description: End-of-session shipping workflow -- runs adversarial pre-commit review (Codex primary, MiniMax-M2.7 fallback), commits, pushes, updates the feature note, updates wiki/concepts/index.md lane membership, appends DEVLOG.md and wiki/log.md, suggests next Backlog promotion, and reminds Keith to /sync. Use when Keith says /ship, "ship it", "wrap up", "end of session", "done shipping", or at the natural end of a build session where code was modified.
---

# K2B Ship

Keystone skill for shipping discipline. Replaces the manual Session Discipline checklist with an enforceable workflow that keeps `wiki/concepts/index.md` (the canonical roadmap) honest.

## When to Trigger

**Explicit:** Keith says `/ship`, "ship it", "ship this", "wrap up", "end of session", "done shipping", "close out", "commit and push this".

**Proactive prompt:** At the natural end of any session where K2B modified code in `.claude/skills/`, `CLAUDE.md`, `K2B_ARCHITECTURE.md`, `k2b-remote/`, `scripts/`, `k2b-dashboard/`, or a feature note moved into `in-progress` or `shipped` state -- say: "We have uncommitted changes in [list]. Want me to /ship?"

**Do NOT auto-ship.** Always confirm the commit message and the reviewer findings before committing.

## When NOT to Use

- Vault-only changes (daily notes, review processing, content drafts) -- these sync via Syncthing, no commit needed
- Emergency hotfixes where Keith explicitly says "just commit, skip review"
- When the user is mid-implementation and just wants an interim checkpoint -- they should say `/commit` or commit manually

## Commands

- `/ship` -- full workflow with adversarial review (Codex primary, MiniMax fallback) + feature note updates + roadmap updates
- `/ship --skip-codex <reason>` -- skip the Codex reviewer specifically (NOT skip review entirely). MiniMax fallback still runs unless also unavailable. Reason is required (e.g. `codex-quota-depleted`, `codex-cli-wedged`, `codex-plugin-missing`).
- `/ship --no-feature` -- ship code without touching feature notes or the roadmap (e.g. typo fix, config tweak)
- `/ship status` -- show what would ship without actually shipping

## Workflow

### 0. Active rules auto-promotion scan

Before anything else, scan for learnings that have crossed the promotion threshold (reinforced 3x) and surface them to Keith for inline y/n/skip confirmation. This step runs on every `/ship` call, including `--no-feature` and `--defer` variants. It is read-only until Keith answers `y`.

Run (skip gracefully if the script is absent -- sibling repos like K2Bi do not carry the active-rules pipeline yet, so absence is normal, not an error):

```bash
if [ -x scripts/promote-learnings.py ]; then
  scripts/promote-learnings.py
else
  echo "auto-promote: skipped (no scripts/promote-learnings.py in $(pwd))"
fi
```

If the script is absent, skip the rest of step 0 entirely (no candidate surfacing, no wiki-log-append at end of section) and proceed to step 0a. When the script IS present, the scanner prints a JSON array of candidate learnings. Each candidate has: `learn_id`, `count`, `distilled_rule`, `area`, `source_excerpt`, `would_exceed_cap`, `current_active_count`, `cap`. If the array is empty, print `auto-promote: 0 candidates` and continue to step 0a.

For each candidate, surface Keith inline:

```
L-<id> has been reinforced <count>x and is not in active_rules.
Distilled: "<distilled_rule>"
Promote now? [y/n/skip]
```

If `distilled_rule` is `null` (no frontmatter line, no bolded first sentence in the body), print the full `source_excerpt` first and ask Keith to supply the rule text inline before promoting. Save his answer as the rule text for the append step.

Act on Keith's answer:

- **y**: Append a new numbered rule to `active_rules.md` using the distilled rule text. Section placement is by topical fit (Identity, Vault, Deployment, Karpathy); if unsure, drop it in the section the source learning's `Area:` field maps to. Include `(<L-id>, last-reinforced: <today>)` in the parenthetical per the Fix #5 format.
  - **Before** appending, if `would_exceed_cap` is `true` OR the post-append rule count would exceed `cap`, resolve the LRU victim:
    ```bash
    scripts/select-lru-victim.py
    ```
    The helper reads `active_rules.md`, parses `last-reinforced:` and reinforcement count, and prints the oldest rule as JSON (`{"rule_number": N, "title": "...", "learn_id": "...", "last_reinforced": "..."}`). Surface the demotion to Keith as `[warn] demoting rule <N> (<title>) to make room for <new rule>` and wait for his confirmation. On `y`, call:
    ```bash
    scripts/demote-rule.sh <N>
    ```
    which moves the rule block intact into `self_improve_learnings.md`'s `## Demoted Rules` section, renumbers the remaining rules contiguously, and logs via the Fix #1 helper. Only after the demotion returns success do you append the new rule.
- **n**: Append `auto-promote-rejected: true` to the learning's entry body in `self_improve_learnings.md` (as a bullet: `- **auto-promote-rejected:** true`) so the scanner skips it on future `/ship` runs. Do not modify the count.
- **skip**: Do nothing. The candidate will re-appear on the next `/ship`.

After all candidates are processed, log the net change via the Fix #1 helper:

```bash
scripts/wiki-log-append.sh /ship "step-0" "promoted=<N> rejected=<M> skipped=<K> demoted=<D>"
```

Then continue to step 0a.

### 0a. Ownership drift check (advisory)

Run (skip gracefully if absent -- sibling repos like K2Bi may not carry this script):

```bash
if [ -x scripts/audit-ownership.sh ]; then
  scripts/audit-ownership.sh || true
else
  echo "ownership-drift: skipped (no scripts/audit-ownership.sh in $(pwd))"
fi
```

When the script IS present, it exits non-zero when it finds known rule phrases outside their canonical home (see `scripts/ownership-watchlist.yml`). This step is **advisory**. Drift does not block `/ship`. Surface the offenders to Keith inline:

```
[warn] ownership drift: rule=<id> phrase=<phrase>
  offender: <path>
```

Keith decides fix-inline or defer. When he defers, append the drift summary to the ship commit body under a "Deferred:" trailer so the next session sees it.

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
| skills    | `.claude/skills/`, `CLAUDE.md`, `K2B_ARCHITECTURE.md` | yes |
| code      | `k2b-remote/` | yes (build + pm2 restart k2b-remote) |
| dashboard | `k2b-dashboard/` | yes (build + pm2 restart k2b-dashboard) |
| scripts   | `scripts/` including `scripts/hooks/` | yes |
| vault     | `K2B-Vault/` | no (Syncthing) |
| plans     | `.claude/plans/` | no |
| devlog    | `DEVLOG.md` | no |

**Category names must match `/sync`'s category table exactly.** `/sync` currently defines: `skills`, `code`, `dashboard`, `scripts`. Any category label that `/ship --defer` writes into a mailbox entry must be one of those four -- otherwise `/sync` would consume the entry without a deploy target, silently dropping the change. In particular, `scripts/hooks/**` rolls up into `scripts` (not a separate `hooks` category): the deploy script's `scripts` mode already rsyncs `scripts/` recursively, which covers hooks.

If there are NO changes at all, report "No changes to ship" and stop.

### 2. Identify the feature being shipped

Read `K2B-Vault/wiki/concepts/index.md`, find the **In Progress** lane.

- If exactly one feature is In Progress -> that is the candidate feature
- If zero features are In Progress -> ask Keith whether this ships under an existing Backlog feature (and if so, which), or is infrastructure work with no feature attached (`--no-feature`)
- If multiple features are In Progress (shouldn't happen per lane rules) -> ask Keith to disambiguate

For multi-ship features (e.g. `feature_mission-control-v3`), read the feature note's Shipping Status table. Identify the current ship row (`in-flight` / `in progress`). Ask Keith to confirm which ship this commit completes.

### 3. Adversarial pre-commit review gate

**Mandatory unless `--skip-codex <reason>` is passed AND no fallback reviewer is run.** This is **Checkpoint 2** of the two K2B adversarial review checkpoints. (Checkpoint 1 is **plan review** -- see below -- and runs earlier, before implementation. `/ship` only owns Checkpoint 2.)

#### 3a. Tier detection

Run `scripts/ship-detect-tier.py` first to classify the uncommitted diff into Tier 0, 1, 2, or 3 per [[wiki/concepts/feature_adversarial-review-tiering]]. Classifier errors (not in a git repo, missing/malformed `scripts/tier3-paths.yml`, etc.) are **fail-safe to Tier 3** -- never silently soften the gate.

```bash
set +e
TIER_OUTPUT="$(scripts/ship-detect-tier.py 2>&1)"
TIER_EXIT=$?
set -e

if [ "$TIER_EXIT" -ne 0 ]; then
  echo "[warn] ship-detect-tier exited $TIER_EXIT -- falling back to Tier 3." >&2
  echo "$TIER_OUTPUT" >&2
  TIER=3
  TIER_REASON="classifier-error: $TIER_OUTPUT"
else
  TIER=$(echo "$TIER_OUTPUT" | sed -n 's/^tier: //p')
  TIER_REASON=$(echo "$TIER_OUTPUT" | sed -n 's/^reason: //p')
  if [ -z "$TIER" ]; then
    echo "[warn] ship-detect-tier produced no tier line -- falling back to Tier 3." >&2
    TIER=3
    TIER_REASON="classifier-output-malformed"
  fi
fi

echo "tier: $TIER -- $TIER_REASON"
```

#### 3b. Tier routing

| Tier | Routing |
|---|---|
| 0 | Skip review (log only). |
| 1 | MiniMax `--scope diff` single-pass, cap at 2 iterations, escalate to Tier 2 on MiniMax failure. See Step 3b.1. |
| 2 | Codex single-pass via today's background + poll pattern. `--skip-codex` routes to MiniMax `--scope diff` single-pass. Both-fail -> REFUSE (same as Tier 3). |
| 3 | Today's iterate-until-clean flow, verbatim. See Step 3c. |

**Get the safe changed-file list once** (used by Tier 1 and Tier 2 diff-scoped reviewer invocations; handles renames + spaces via `-z`):

```bash
CHANGED_FILES=$(git diff --name-only -z HEAD | tr '\0' ',' | sed 's/,$//')
```

##### 3b.0 Tier 0 flow

```bash
if [ "$TIER" = "0" ]; then
  echo "review skipped (tier-0: vault/devlog/plans only, $TIER_REASON)"
  REVIEW_RESULT="skipped-tier-0"
  # proceed to step 4
fi
```

##### 3b.1 Tier 1 flow

MiniMax `--scope diff` with a 2-pass cap. On MiniMax failure, escalate to Tier 2 Codex. If `--skip-codex` blocks Codex too, REFUSE (both reviewers unavailable).

```bash
if [ "$TIER" = "1" ]; then
  TIER_1_PASS=1
  TIER_1_MAX=2
  TIER_1_APPROVED=no
  TIER_1_MINIMAX_FAILED=no

  while [ "$TIER_1_PASS" -le "$TIER_1_MAX" ]; do
    echo "[tier-1] pass $TIER_1_PASS of $TIER_1_MAX -- running MiniMax --scope diff..."
    set +e
    VERDICT_JSON=$(scripts/minimax-review.sh \
      --scope diff \
      --files "$CHANGED_FILES" \
      --focus "tier-1 docs review (pass $TIER_1_PASS): enumerate ALL material findings severity>=medium with file:line, ranked by severity+confidence. Do not stop at top blocker -- Keith fixes top-down. Cap at ~15 findings max; if budget requires trimming, drop lowest severity first so the JSON stays parseable." \
      --json 2>/tmp/tier1_pass_${TIER_1_PASS}.err)
    MINIMAX_EXIT=$?
    set -e

    if [ "$MINIMAX_EXIT" -ne 0 ]; then
      echo "[tier-1] MiniMax FAILED on pass $TIER_1_PASS (exit $MINIMAX_EXIT):" >&2
      cat /tmp/tier1_pass_${TIER_1_PASS}.err >&2
      TIER_1_MINIMAX_FAILED=yes
      break
    fi

    # Parse verdict: parsed.verdict (case-insensitive). minimax_review.py
    # --json prints the parsed dict directly; verdict is lowercase "approve".
    VERDICT=$(echo "$VERDICT_JSON" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
except json.JSONDecodeError:
    print('parse-error')
    sys.exit(0)
v = data.get('verdict', '') or ''
print(v.strip().lower())
")

    # Treat empty/parse-error verdict as MiniMax failure (malformed response
    # with exit 0 otherwise slips past exit-code-only escalation gate and
    # silently advances the pass counter, which masks a broken reviewer).
    # Per MiniMax Checkpoint 2 HIGH-1.
    if [ -z "$VERDICT" ] || [ "$VERDICT" = "parse-error" ]; then
      echo "[tier-1] MiniMax returned malformed response (verdict empty/unparseable) on pass $TIER_1_PASS -- treating as failure." >&2
      TIER_1_MINIMAX_FAILED=yes
      break
    fi

    if [ "$VERDICT" = "approve" ]; then
      echo "[tier-1] APPROVED on pass $TIER_1_PASS"
      TIER_1_APPROVED=yes
      break
    fi

    # Non-approve verdict. Surface findings to Keith verbatim; Keith triages
    # and decides whether to fix-and-repass, accept, or escalate.
    echo "[tier-1] pass $TIER_1_PASS verdict=$VERDICT -- surfacing findings:"
    echo "$VERDICT_JSON" | python3 -m json.tool
    # Claude waits for Keith's reply here before advancing the loop.
    TIER_1_PASS=$((TIER_1_PASS + 1))
  done

  if [ "$TIER_1_MINIMAX_FAILED" = "yes" ]; then
    if [ -n "${SKIP_CODEX:-}" ]; then
      echo "[FATAL] tier-1 MiniMax failed AND --skip-codex blocks Codex." >&2
      echo "Both reviewers unavailable. REFUSE /ship." >&2
      exit 3
    fi
    echo "[tier-1] escalating to tier-2 Codex single-pass (MiniMax failed)."
    TIER=2
    # Defensive: seed REVIEW_RESULT in case Tier 2 block fails to run for
    # any reason -- the Tier 2 success path will overwrite this with the
    # final value ("tier-2-runner-<primary>").
    REVIEW_RESULT="tier-1-escalated-tier-2-minimax-failed"
    # Fall through to Tier 2 block below
  elif [ "$TIER_1_APPROVED" = "no" ]; then
    echo "[tier-1] 2 passes non-approve; auto-promoting to tier-2."
    TIER=2
    REVIEW_RESULT="tier-1-escalated-tier-2-2-pass-non-approve"
    # Fall through to Tier 2 block
  else
    REVIEW_RESULT="tier-1-approve-pass-$TIER_1_PASS"
  fi
fi
```

##### 3b.2 Tier 2 flow

Single-pass adversarial review via the unified runner `scripts/review.sh`. The runner handles Codex primary, MiniMax automatic fallback (on deadline / quality-gate-fail / EISDIR hazard / plan scope), and `--skip-codex` routing. Keith treats first-pass findings as final -- fix inline OR defer, then commit. Do NOT iterate-until-clean (that is Tier 3 behavior).

```bash
if [ "$TIER" = "2" ]; then
  RUNNER_PRIMARY=codex
  [ -n "${SKIP_CODEX:-}" ] && RUNNER_PRIMARY=minimax
  FOCUS="tier-2 review: enumerate ALL material findings severity>=medium with file:line, ranked by severity+confidence. Do not stop at top blocker -- Keith fixes top-down in one sitting, so trailing issues cost another review pass. Cap at ~15 findings max; if budget requires trimming, drop lowest severity first so the JSON stays parseable."
  [ -n "${SKIP_CODEX:-}" ] && FOCUS="$FOCUS (--skip-codex: $SKIP_CODEX)"

  set +e
  RUNNER_OUT=$(scripts/review.sh diff \
    --files "$CHANGED_FILES" \
    --primary "$RUNNER_PRIMARY" \
    --focus "$FOCUS" \
    --wait)
  RUNNER_EXIT=$?
  set -e

  if [ "$RUNNER_EXIT" = "2" ]; then
    echo "[FATAL] tier-2: both Codex and MiniMax failed." >&2
    echo "$RUNNER_OUT" >&2
    echo "Surface to Keith: options are (a) fix underlying reviewer problem," >&2
    echo "(b) /ship --skip-codex <reason> with Checkpoint 1 in-session." >&2
    exit 3
  elif [ "$RUNNER_EXIT" -ne 0 ]; then
    echo "[FATAL] tier-2: runner returned exit $RUNNER_EXIT (unexpected)." >&2
    echo "$RUNNER_OUT" >&2
    exit 3
  fi

  # Deterministic log path from runner's JSON envelope (per Runner output
  # contract below). Guard against malformed runner output.
  if ! REVIEW_LOG=$(echo "$RUNNER_OUT" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["log_path"])' 2>/dev/null) \
      || [ -z "$REVIEW_LOG" ]; then
    echo "[FATAL] tier-2: runner output missing or malformed log_path:" >&2
    echo "$RUNNER_OUT" >&2
    exit 3
  fi

  REVIEW_RESULT="tier-2-runner-${RUNNER_PRIMARY}"
  [ -n "${SKIP_CODEX:-}" ] && REVIEW_RESULT="${REVIEW_RESULT}-skip-${SKIP_CODEX}"
fi
```

After the runner returns, Claude (the ship-agent) reads `$REVIEW_LOG` and surfaces the findings to Keith verbatim. Find the last `# Codex Review` or `# MiniMax ... review --` section in the log and present it -- do not paraphrase, rank, or pre-filter. Codex's own prioritization (P0/P1/P2/P3) and MiniMax's own ordering (CRITICAL/HIGH/MEDIUM/LOW) stay intact.

#### 3c. Tier 3 flow -- iterate-until-clean via runner

Single-pass reviewer invocation per `/ship` call via `scripts/review.sh`. The runner handles Codex primary, MiniMax automatic fallback (on failure / deadline / quality-gate / EISDIR / plan-scope), and `--skip-codex` routing. Iteration in the Tier 3 sense is **human-driven**: after a NEEDS-ATTENTION verdict, Keith fixes inline and re-runs `/ship`. Each `/ship` is one reviewer pass. This matches the pre-runner Step 3c behavior; the runner replaces the transport only.

```bash
if [ "$TIER" = "3" ]; then
  RUNNER_PRIMARY=codex
  [ -n "${SKIP_CODEX:-}" ] && RUNNER_PRIMARY=minimax
  FOCUS="tier-3 review (single pass per /ship, human-driven iteration): enumerate ALL material findings severity>=medium with file:line, ranked by severity+confidence. Do not stop at top blocker -- Keith fixes top-down, so trailing issues would cost another full /ship round. Cap at ~15 findings max; if budget requires trimming, drop lowest severity first so the JSON stays parseable."
  [ -n "${SKIP_CODEX:-}" ] && FOCUS="$FOCUS (--skip-codex: $SKIP_CODEX)"

  set +e
  RUNNER_OUT=$(scripts/review.sh diff \
    --files "$CHANGED_FILES" \
    --primary "$RUNNER_PRIMARY" \
    --focus "$FOCUS" \
    --wait)
  RUNNER_EXIT=$?
  set -e

  if [ "$RUNNER_EXIT" = "2" ]; then
    echo "[FATAL] tier-3: both Codex and MiniMax failed." >&2
    echo "$RUNNER_OUT" >&2
    echo "Surface to Keith: only proceed with /ship --skip-codex <reason>" >&2
    echo "if Checkpoint 1 (plan review) ran earlier in the session." >&2
    exit 3
  elif [ "$RUNNER_EXIT" -ne 0 ]; then
    echo "[FATAL] tier-3: runner returned exit $RUNNER_EXIT (unexpected)." >&2
    echo "$RUNNER_OUT" >&2
    exit 3
  fi

  if ! REVIEW_LOG=$(echo "$RUNNER_OUT" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["log_path"])' 2>/dev/null) \
      || [ -z "$REVIEW_LOG" ]; then
    echo "[FATAL] tier-3: runner output missing or malformed log_path:" >&2
    echo "$RUNNER_OUT" >&2
    exit 3
  fi

  REVIEW_RESULT="tier-3-runner-${RUNNER_PRIMARY}"
  [ -n "${SKIP_CODEX:-}" ] && REVIEW_RESULT="${REVIEW_RESULT}-skip-${SKIP_CODEX}"
fi
```

**How Claude reads the verdict:** open `$REVIEW_LOG`, find the last `# Codex Review` or `# MiniMax ... review --` section, and present it to Keith verbatim. Do NOT paraphrase, rank, or pre-filter. Use the **last** section in the log because quality-gate-triggered fallbacks write both a failed Codex attempt AND a successful MiniMax run into the same log; only the final section is the actual verdict.

**Keith's iteration loop** (human-driven across `/ship` invocations, not inside bash):

- APPROVE verdict -> proceed to commit (step 4 of this workflow).
- NEEDS-ATTENTION verdict, real findings -> fix inline, then re-run `/ship`. Each re-run is a new invocation with a fresh runner job.
- NEEDS-ATTENTION verdict, false positives (common with MiniMax "file not verified" items for files outside the working tree) -> triage via direct `grep` / `git log`, dismiss as FP in the ship record; if all findings dismissed, proceed to commit. If some real + some FP, fix the real ones and re-run.
- Accept findings and commit anyway -> proceed to commit with explicit "accepted despite N findings" note in the ship record + the commit message body.

No in-bash loop. Bash runs ONE review pass per `/ship`; Keith drives the loop by re-running.

**Runner output contract (shared by Tier 2 and Tier 3):**

When invoked with `--wait`, `scripts/review.sh` prints a JSON envelope on completion:

```json
{
  "job_id": "2026-04-21T12-30-11Z_abc123",
  "exit_code": 0,
  "status": "completed",
  "log_path": ".code-reviews/2026-04-21T12-30-11Z_abc123.log"
}
```

Exit codes:
- `0` = at least one reviewer (primary or fallback) approved.
- `2` = both reviewers failed (primary failed AND fallback failed). Either surface to Keith for `--skip-codex` escalation, or abort if `--skip-codex` was already in play.
- `1` / `127` / other = runner-level argv error or spawn failure. Always escalate to Keith.

The log at `log_path` is a plain-text unified log with two kinds of lines:
- **Runner-tagged** (`[ISO] TAG payload`) -- `JOB_START`, `REVIEWER_START`, `SPAWN`, `REVIEWER_SKIP`, `HEARTBEAT`, `HEARTBEAT_STALE`, `WEDGE_SUSPECTED`, `SOFT_DEADLINE`, `HARD_DEADLINE`, `SIGKILL`, `REVIEWER_END`, `QUALITY_GATE_FAIL`, `FALLBACK`, `MINIMAX_KEY_LOAD_FAILED`.
- **Verbatim reviewer stdout** -- including the actual `# Codex Review` sections and `# MiniMax MiniMax-M2.7 review -- APPROVE|NEEDS-ATTENTION` headers. These are what Claude reads to surface findings.

Runner state lives in `.code-reviews/<job_id>.json` (updated by the watchdog while running; finalized at completion with `status`, `primary_used`, `fallback_used`, `exit_code`, `reviewer_attempts[]`). For background mode (no `--wait`), `scripts/review-poll.sh <job_id>` returns a snapshot with `phase`, `elapsed_s`, `last_activity_s_ago`, `tail`, `should_poll_again`, and `recommended_poll_interval_s`. The skill doesn't use background mode in the Tier 2/3 flows (they use `--wait`) but the poll path exists for ad-hoc use.

**Why the runner replaces the old inline patterns:** the old Step 3c ran `node codex-companion.mjs review` synchronously and relied on Claude-side polling + manual MiniMax fallback when Codex wedged. Observed failure modes: Codex silent hang during WebSocket cold-start (~10+ min), MiniMax HTTP 529 with no retry, "both reviewers available but k2b-ship bash can't coordinate the fallback." The runner solves all three: hard SIGTERM at deadline, automatic fallback on any failure mode, and watchdog HEARTBEAT lines that make "still working" observable. Built once in K2Bi, validated in production there for several days, ported here.

**Presentation rules for reviewer findings (unchanged):**

- Report findings neutrally. Do not argue with the reviewer.
- Do not pre-filter findings by "importance" before Keith sees them.
- Let Keith decide which to fix, defer, or accept.
- For MiniMax findings specifically: the reviewer cannot see files outside the git working tree, so "file not verified" / "consumer not visible" findings often dissolve under a quick direct `grep`. Triage real-vs-false-positive before triggering a fix.
- The archived JSON at `.minimax-reviews/<ts>_<scope>.json` (MiniMax) and the unified log at `.code-reviews/<job_id>.log` (runner) are durable evidence of the gate.

**MiniMax invocation contract (inherited through the runner).** The `scripts/minimax-review.sh` child exits 0 on success (any verdict including NEEDS-ATTENTION counts as success -- the verdict is review output, not script status), non-zero on failure (missing API key, network error, malformed MiniMax response, JSON Schema validation failure). Empty stdout from a 0-exit run is impossible by design (the formatter always emits a verdict). If the runner observes exit 0 with no verdict marker in the log, the quality gate forces a fallback (`effective_rc = 125`) -- silent emptiness is worse than a loud error.

#### 3d. Record the tier used

After the tier-specific flow finishes (any of 3b.0 Tier 0 skip, 3b.1 Tier 1, 3b.2 Tier 2, or 3c Tier 3), record the classification in the ship audit trail + DEVLOG entry + (for multi-ship features) the Shipping Status table row:

```
tier: <N> (<classifier | classifier-error-fallback | Ship-2-override>)
tier-reason: <TIER_REASON>
review-result: <skipped-tier-0 | tier-1-approve-pass-N | tier-1-auto-promoted-to-tier-2 | tier-2-codex-single-pass | tier-2-minimax-<reason> | tier-3-codex-multiround | tier-3-minimax-fallback>
```

For multi-ship features (Shipping Status table), append the tier + reason to the current ship row so future ships in the same feature can see the historical tier pattern.

### Adversarial Review -- the two checkpoints

K2B uses a second-model reviewer (Codex primary, MiniMax-M2.7 fallback) to catch blind spots Claude cannot see in its own work. Two mandatory checkpoints bracket any non-trivial build:

**Checkpoint 1: Plan Review.** Before implementing any new feature, skill, or significant refactor, after the plan is written but before code is touched:

- **Codex (primary)**: `/codex:adversarial-review challenge the plan` with the plan file path. Codex has Read tool access, so it can fetch any plan file from any path -- the typical locations (`~/.claude/plans/`, `<repo>/plans/`) are both reachable.
- **MiniMax fallback** when Codex unavailable: MiniMax CANNOT see files outside the git working tree, and plan files live at `~/.claude/plans/` (Claude Code plans, outside any repo) or `<repo>/plans/` (gitignored or untracked) -- `minimax-review.sh` gathers context from `git status` / `git diff` only, so a bare invocation would produce a generic review with no plan content. Workarounds:
  - **(a) Inline the plan content into the `--focus` prompt** (preferred for non-trivial plans). Read the plan file, paste its content as: `scripts/minimax-review.sh --focus "challenge this plan: <PASTE FULL PLAN HERE>. Look for: over-engineering, simpler alternatives, missing edge cases, unnecessary complexity."` MiniMax M2.7 has a generous prompt window so even multi-thousand-line plans fit.
  - **(b) Copy the plan into the working tree as a temp file** (e.g., `cp ~/.claude/plans/<file> .minimax-review-plan.tmp.md`) so the working-tree scan picks it up; delete after the review. Less clean but works without prompt-stuffing.
  - **(c) Skip Plan Review entirely and rely on Checkpoint 2 being mandatory** when Checkpoint 1 was skipped. Acceptable for small plans where adversarial review at pre-commit time catches the same issues; not acceptable for large architectural plans where catching the issue early matters.
- Look for: over-engineering, simpler alternatives, missing edge cases, unnecessary complexity
- Adjust the plan based on findings BEFORE writing code

This checkpoint lives outside `/ship` -- it is the author's responsibility at plan-time. `/ship` only sees the result (the already-reviewed plan, or its absence) via the diff it is about to commit. If neither Codex nor MiniMax was run on the plan, Checkpoint 2 (pre-commit) becomes mandatory and cannot be skipped via `--skip-codex`.

**Checkpoint 2: Pre-Commit Review.** Before committing changes from a build session, `/ship` runs adversarial review on the uncommitted diff via the tier-specific block in step 3 above. Tier 2 and Tier 3 flows both invoke the unified runner `scripts/review.sh` (Codex primary, MiniMax automatic fallback on failure / deadline / quality-gate / EISDIR / plan-scope). Tier 1 stays on the direct `scripts/minimax-review.sh --json` loop because Tier 1 verdict parsing requires parseable JSON that the runner's unified log doesn't expose. Look for: bugs, logic errors, drift from the plan, edge cases. Fix issues before committing.

**When pre-commit review can be skipped (gate-not-applicable cases):**

- Vault-only changes (daily notes, review processing, content drafts)
- Config tweaks, typo fixes, one-line changes
- Emergency hotfixes where the bug-fix speed matters more than review (review after the fact)

**`--skip-codex` does NOT mean "skip review."** It means "skip the Codex reviewer specifically -- usually because Codex is unavailable." The runner auto-switches `--primary` to MiniMax when `SKIP_CODEX` is set, so MiniMax still runs unless the change is in the gate-not-applicable list above. Use `--skip-codex codex-quota-depleted` (or similar reason) and record the reason in `REVIEW_RESULT`.

**Never skip both reviewers.** If Checkpoint 1 was skipped because the feature was small enough that no plan was written, Checkpoint 2 becomes mandatory. Conversely, if Codex AND MiniMax are both unavailable, the build has had no adversarial review at all, and `/ship` should refuse to proceed without Keith's explicit override.

**Rules for presenting reviewer findings to Keith:**

- Report findings neutrally. Do not argue with the reviewer.
- Do not pre-filter findings by "importance" before Keith sees them.
- Let Keith decide which to fix, defer, or accept.
- For MiniMax findings specifically: the reviewer cannot see files outside the git working tree, so "file not verified" / "consumer not visible" findings often dissolve under a quick direct `grep`. Triage real-vs-false-positive before triggering a fix.

### 4. Generate commit message

Build a commit message from the categorized diff. Format:

```
<type>: <short summary>

<optional body with bullet points of major changes>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
Co-Shipped-By: k2b-ship
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

# Push only if an `origin` remote is configured. Sibling repos like K2Bi may
# not have one yet (Phase 1), which is expected, not an error. Match by exact
# name -- a remote called `upstream` would otherwise pass `git remote | grep -q .`
# and then fail at push time when we still target `origin`.
if git remote | grep -qx 'origin'; then
  git push origin main
else
  echo "push: skipped (no 'origin' remote configured in $(pwd))"
fi
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
Co-Shipped-By: k2b-ship
EOF
)"

# Same remote-guard as step 5 -- match by exact name `origin`, not "any remote".
if git remote | grep -qx 'origin'; then
  git push origin main
else
  echo "push: skipped (no 'origin' remote configured in $(pwd))"
fi
```

Never `--amend` the step-5 commit to include DEVLOG.md -- amends rewrite history and can drop signed state. Always create a new commit.

If shipping multiple logical changes in one session (two or more code commits back-to-back), batch all their DEVLOG entries into a single follow-up `docs: devlog` commit after the last code commit, referencing each code SHA in its own entry.

### 9. Append wiki/log.md

Call the single-writer helper (never append to wiki/log.md directly):

```bash
scripts/wiki-log-append.sh /ship "<feature-slug>" "shipped <feature-slug>: <one-line-summary>"
```

Replace `<feature-slug>` with the feature note basename (e.g. `feature_k2b-ship`) and `<one-line-summary>` with the same text used in the commit message subject. Helper handles locking, timestamp, and format.

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

### 12. Deployment handoff -- explicit sync-now or defer

**Pre-check: does this repo even have a deploy target?** Before prompting sync-now / defer, verify the current repo has a deploy script:

```bash
if [ ! -x scripts/deploy-to-mini.sh ]; then
  echo "deploy-handoff: skipped (no scripts/deploy-to-mini.sh in $(pwd) -- repo has no Mac Mini deploy target yet)"
  # Skip the rest of step 12 entirely. Do not write to .pending-sync/.
fi
```

For K2B, the script exists -- flow continues as normal. For K2Bi (Phase 1 through Phase 3), no Mini provisioning exists yet, so the sync question is meaningless and the mailbox entry would be a dead letter. Once K2Bi gets its own `deploy-to-mini.sh` in Phase 4, this check starts passing and the rest of step 12 engages automatically.

If the deploy script exists, continue:

If any files in categories `skills`, `code`, `dashboard`, or `scripts` were in the commits, the Mac Mini is now out of date with the pushed code. (`scripts/hooks/**` rolls up into `scripts` -- do not write a separate `hooks` category into mailbox entries, `/sync` has no deploy target for it and would silently drop the change.) A soft reminder is not enough because it can be missed and leaves no recovery signal. Ask Keith an explicit question:

> Project files changed (list the categories + files). Run `/sync` now, or defer to a later session?
> - **now** -- invoke `/sync` in-line, confirm it completed, done
> - **defer** -- drop a new entry in the `.pending-sync/` mailbox so the next session (or the next `/sync`) catches up

**If Keith picks `now`:**
1. Invoke the `k2b-sync` skill via the Skill tool (or run `"$(git rev-parse --show-toplevel)"/scripts/deploy-to-mini.sh auto` if skill invocation is unavailable in the current harness -- the path resolves to the current repo's deploy script, not hardcoded to K2B, so a sibling repo with its own `scripts/deploy-to-mini.sh` deploys its own tree).
2. Report what was synced.
3. **Do NOT touch the `.pending-sync/` mailbox.** `/sync` is the sole owner of the mailbox lifecycle. It consumes and deletes its own entries on success. Any cleanup `/ship` did after-the-fact would race with a concurrent `/ship --defer` in another session and could silently destroy a newer deferred entry. Leave the mailbox alone.

**If Keith picks `defer`:**

1. Write a **new unique entry** in the **current repo's** `.pending-sync/` mailbox directory -- that is, the `.pending-sync/` folder at the root of whichever git repo `/ship` is running from. For K2B sessions this resolves to `~/Projects/K2B/.pending-sync/`; for sibling repos like K2Bi it resolves to the sibling's own `.pending-sync/` (each repo has its own mailbox and its own `/sync` consumer). Each defer creates its own file -- we never rewrite an existing file -- so concurrent defers from other sessions cannot race. Write via temp-file + `os.replace()` so a crash mid-write cannot leave partial JSON that downstream readers would flag as UNREADABLE:

   ```bash
   python3 <<PYEOF
   import json, os, datetime, tempfile, uuid, subprocess
   # Derive mailbox dir from git repo root, NOT hardcoded to K2B
   repo_root = subprocess.check_output(
     ["git", "rev-parse", "--show-toplevel"], text=True
   ).strip()
   dir_ = os.path.join(repo_root, ".pending-sync")
   os.makedirs(dir_, exist_ok=True)

   now = datetime.datetime.now(datetime.timezone.utc)
   entry_id = f"{now.strftime('%Y%m%dT%H%M%S')}_<short-sha from step 5>_{uuid.uuid4().hex[:8]}"
   final_path = os.path.join(dir_, f"{entry_id}.json")

   payload = {
     "pending": True,
     "set_at": now.isoformat(),
     "set_by_commit": "<short-sha from step 5>",
     "categories": ["<list from above>"],
     "files": ["<list from step 1>"],
     "entry_id": entry_id,
   }

   # Atomic write: temp file in the SAME directory, then os.replace into final name.
   # Temp names start with '.tmp_' so mailbox readers know to ignore in-progress writes.
   fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=dir_)
   try:
       with os.fdopen(fd, "w") as f:
           json.dump(payload, f, indent=2)
           f.flush()
           os.fsync(f.fileno())
       os.replace(tmp, final_path)
   except Exception:
       try: os.unlink(tmp)
       except FileNotFoundError: pass
       raise
   PYEOF
   ```

   Required schema fields: `pending` (bool, must be `true` for an active entry), `set_at` (ISO-8601 UTC timestamp), `set_by_commit` (short SHA from step 5), `categories` (list of strings matching the category table), `files` (list of file paths relative to `~/Projects/K2B/`), and `entry_id` (matches the filename stem for traceability). `k2b-sync`'s Step 0 validates these fields and fails loud if any are missing.

2. Tell Keith: "Deferred. Entry `<entry_id>` added to `.pending-sync/` mailbox. Next session's startup hook will surface pending mailbox entries, and any later `/sync` invocation will consume them before checking conversation context."

3. The mailbox directory is gitignored (`/.pending-sync/` in `.gitignore`), never propagates to the Mini, and survives session boundaries on the MacBook only. **Consuming and deleting mailbox entries is `/sync`'s exclusive responsibility**, and it only deletes the specific entries it actually processed -- a `/ship --defer` running concurrently writes to a different filename, so nothing can be clobbered.

**Race-safety invariant:** The mailbox is a multi-producer / single-consumer queue where each producer writes a unique filename. Producers (`/ship --defer`) never read or delete. The consumer (`/sync`) deletes only filenames it has observed and processed. No state is ever rewritten in place. This makes the lifecycle race-free on POSIX without locks.

**If no syncable files changed:** Skip the question entirely. Do not write a marker. Report "Nothing to sync -- all changes were vault/plan/devlog only."

Do NOT auto-sync without asking. Per Active Rule L-2026-03-29-002, never run manual rsync -- always go through the deploy script via `/sync` or `k2b-sync`.

### 13. Usage logging

```bash
echo -e "$(date +%Y-%m-%d)\tk2b-ship\t$(echo $RANDOM | md5sum | head -c 8)\tshipped FEATURE_SLUG SHORT_SHA" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

### 13.5. Session summary capture

Extract implicit behavioral signals from this session and write a compact summary to the vault. The observer picks these up asynchronously and feeds them into the preference pipeline. This step runs on ALL /ship variants (including `--no-feature` and `--skip-codex`).

**Signal extraction:** Scan the conversation for up to 10 signals across 5 types:
- **[interest]** -- topics Keith drilled into vs skipped
- **[anti-pref]** -- things Keith redirected or pushed back on
- **[decision]** -- choices made and the reasoning behind them
- **[priority]** -- what Keith focused on when time was limited
- **[connection]** -- links Keith made that K2B didn't anticipate

**Best-effort:** If the conversation is too short or heavily compacted, emit what's available. If no signals are found, log "session-capture: no signals detected, skipping" and move on. Do not write an empty file.

**Grounding rule:** Every signal must cite a specific moment from the conversation. Do not invent timings, counts, or motives not directly evidenced. "Keith spent time on X" requires X to be visible in the conversation. If unsure whether something happened, omit it.

**Write the summary** (atomic, via temp + rename):

```bash
SESSIONS_DIR="$HOME/Projects/K2B-Vault/raw/sessions"
mkdir -p "$SESSIONS_DIR"
FILENAME="$(date +%Y-%m-%d_%H%M%S)_session-summary.md"
TMPFILE="$SESSIONS_DIR/.tmp_${FILENAME}"
# Write frontmatter + body to TMPFILE, then:
mv "$TMPFILE" "$SESSIONS_DIR/$FILENAME"
```

**Frontmatter:**
```yaml
---
tags: [raw, session-summary]
date: YYYY-MM-DD
type: session-summary
origin: k2b-extract
commit: <short-sha from step 5>
feature: <feature-slug or "infrastructure">
cited-rules: []  # [L-id1, L-id2] if any active rules met the citation contract below
up: "[[index]]"
---
```

**Body:** One bullet per signal, max 10 lines. Example:
```
- [interest] Keith spent 40 min on source-hash dedup design, skipped decay model
- [anti-pref] Keith rejected MVP-only approach, wanted full 4-phase implementation
- [decision] Write-through model chosen over rebuild-only after Codex flagged gap
- [priority] All 6 Codex findings fixed before commit, no deferral
- [connection] Canonical memory completes the observer->profile->k2b-remote chain
```

**First-run setup** (only if `raw/sessions/index.md` does not exist):
1. Create `raw/sessions/index.md` with standard raw subfolder index format
2. Add a sessions row to `raw/index.md` if not already listed

**Access-count bump (Item 1 of 2026-04-19 memory-architecture plan):**

After the session summary has been written, bump the citation count for every active rule that was cited or applied during this session. Citation-detection contract (tightened per Codex plan review P1 #5 -- under-count is preferred to over-count because access counts drive promotion/eviction ranking):

A rule counts as cited ONLY if the session conversation matches one of these three patterns:
1. Explicit L-ID token appears (e.g. the exact string `L-2026-04-01-001`).
2. The rule's distilled-rule text appears verbatim as a quoted substring.
3. Claude explicitly writes "applying rule N" or "per rule N" referencing the rule by its number or title.

Ambiguous paraphrases, vibes, or "might have applied" cases are SKIPPED. If you're not sure, don't count it.

Collect the unique L-IDs that passed the contract. Add them to the summary frontmatter as `cited-rules: [L-id1, L-id2]`. Then pass them to the increment helper:

```bash
if [ -n "$CITED_L_IDS" ]; then
  if ! /Users/keithmbpm2/Projects/K2B/scripts/increment-access-count.py $CITED_L_IDS 2>&1; then
    echo "[warn] access-count bump failed for session $(date +%Y-%m-%d_%H%M%S); session summary was written, subsequent ships do not retry past bumps" >&2
  fi
fi
```

Fail-open: helper crashes do NOT fail the ship. The summary file is already on disk before the bump runs, so the observer loop still picks it up.

## Error Handling

- **Pre-commit hook fails** -> fix the underlying issue (per Active Rule 8, never `--no-verify`), re-stage, create a NEW commit (never `--amend`).
- **Push fails (not a force-push scenario)** -> investigate. Fetch, check if the branch diverged, ask Keith how to reconcile.
- **Codex plugin missing OR Codex quota depleted** -> step 3 decision tree routes to MiniMax fallback (`scripts/minimax-review.sh`). Do NOT silently skip review.
- **MiniMax fallback fails** (script missing, `MINIMAX_API_KEY` unset, network error, non-zero exit, empty stdout despite 0 exit) -> escalate to "both reviewers unavailable" surface message in step 3. Require explicit Keith override via `/ship --skip-codex <reason>` AND confirmation that Checkpoint 1 plan review ran earlier in the session, before /ship may proceed.
- **Both Codex AND MiniMax unavailable, no Checkpoint 1 ran either** -> /ship REFUSES to proceed. The build has had zero adversarial review. Fix the underlying reviewer problem first.
- **Feature note not found** -> ask Keith which feature this belongs to, or offer to ship as `--no-feature`.
- **`wiki/concepts/index.md` parse failure** -> fail loudly, point Keith at the file, do not guess the lane structure.
- **DEVLOG.md / wiki/log.md append failure** -> commit has already landed, so degrade gracefully: print the entry Keith should add manually, continue with the rest of the workflow.

## What /ship Does NOT Do

- Auto-sync to Mac Mini (Keith must run `/sync` explicitly)
- Edit vault files other than the feature note, `wiki/concepts/index.md`, `wiki/log.md`, `DEVLOG.md`, the skill-usage-log, and `raw/sessions/`
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
