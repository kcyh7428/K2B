# Plan: weave auto-apply (remove the human review gate)

## Problem

`k2b-weave` finds high-confidence cross-links between wiki pages, writes a digest to
`review/`, and waits for Keith to mark each row `check/x/defer` before
`cmd_apply` adds the `[[backlink]]`. Keith never marks them. Result: 4 digests / 31
links sat unapplied for up to a month (cleared by hand 2026-06-29). The cross-link
system is effectively dead because it waits on a gate that is never passed.

A weave proposal only ever adds one slug to a page's `related:` frontmatter array
(additive, reversible, low stakes). The review gate is mismatched to the stakes.

## Goal

Make weave apply its own high-confidence links on each scheduled run, with no human
gate. Keep a full audit trail and an undo path (the ledger). Low-confidence links are
recorded silently, never queued. Also fix the apply-path durability bug Codex flagged
(rows lost / digest deleted on apply failure).

## Design

### 1. Config knobs (top of `scripts/k2b-weave.sh`)

```
WEAVE_AUTO_APPLY="${WEAVE_AUTO_APPLY:-true}"             # master switch
WEAVE_AUTO_APPLY_THRESHOLD="${WEAVE_AUTO_APPLY_THRESHOLD:-0.80}"  # min confidence to auto-apply
```

Env-overridable so a run can fall back to the old digest behaviour
(`WEAVE_AUTO_APPLY=false`) without a code change. Not `readonly` (env override).

### 2. `apply_one_proposal` -- distinct exit codes (fixes Codex high finding root)

The helper `k2b-weave-add-related.py` already returns: `0` added-or-already-present,
`1` hard error, `2` concurrency-retry. Today `apply_one_proposal` collapses helper
`1` and `2` into its own `1`, and reuses `2` for "FROM page not found". Re-map so the
retryable concurrency signal survives:

| apply_one_proposal rc | meaning | source |
|---|---|---|
| 0 | applied (or already present) | helper 0 |
| 2 | stale -- FROM page not found | path lookup fails before helper |
| 3 | concurrency-retry | helper 2 |
| 1 | hard error | helper 1 |

### 3. New ledger statuses + exclusion treatment

| status | when | excluded from re-proposal? | rationale |
|---|---|---|---|
| `applied` | auto-apply rc 0 | yes (existing) | link exists |
| `stale-renamed` | rc 2 | yes (existing) | FROM gone |
| `held-low-confidence` | conf < threshold | **yes (new)** | deliberately not added; silent, no churn |
| `apply-failed` | rc 1, retry_count < MAX | **no (new)** | must be retried -> re-proposed next run |
| `apply-failed-permanent` | rc 1, retry_count >= MAX_RETRY_COUNT | yes (new) | give up after 3, surface in status/alerts |

`get_ledger_exclusions`: add `held-low-confidence` and `apply-failed-permanent` to the
suppressed set. Deliberately DO NOT suppress `apply-failed` (transient, e.g.
concurrency rc 3 also routed here) so the pair re-enters the next proposal cycle and
auto-apply retries it. retry_count increments on each failed attempt; at
`MAX_RETRY_COUNT` (3) it flips to `apply-failed-permanent` and a `notify_failure` alert
fires.

Concurrency-retry (rc 3) is treated like `apply-failed` but does NOT consume a retry
slot on the first occurrence (it is expected under the lock-free helper). Simplest safe
rule for MVP: rc 3 -> record/leave as `apply-failed` WITHOUT incrementing retry_count
(so transient mtime races never exhaust the budget); rc 1 -> `apply-failed` WITH
increment. Both are non-suppressed, so both retry next run.

### 4. `cmd_run` -- auto-apply branch (replaces blanket digest write)

After `scored` (top-N verified) is computed, before writing any digest:

```
if WEAVE_AUTO_APPLY != true:   # legacy path unchanged
    write_digest + append_proposals_to_ledger(pending)   # as today
else:
    split scored -> auto (conf >= threshold), held (conf < threshold)
    for p in auto:
        apply_one_proposal(p.from, p.to)
        rc 0 -> ledger row status=applied
        rc 2 -> ledger row status=stale-renamed
        rc 3 -> ledger row status=apply-failed (no retry increment)
        rc 1 -> ledger row status=apply-failed (retry increment; ->permanent at max)
    for p in held:
        ledger row status=held-low-confidence
    NO digest is written.
    summary counts -> log + metrics + skill-usage ("N applied, M held, K failed")
```

Ledger rows are appended with their FINAL status (not blanket `pending`), so the
exclusion set is correct on the next run. `append_proposals_to_ledger` gains a status
argument, or a sibling `append_proposal_to_ledger_with_status` is used per row.

Re-application safety: the helper is idempotent (already-present -> rc 0), so a pair
that slips through twice never double-writes the link.

### 5. `cmd_apply` -- harden (fixes Codex high finding directly)

Kept as a manual fallback for any digest that still exists (legacy mode / hand-made).
Changes:
- Track a `failed` counter. rc 1 -> `mark_ledger_apply_failed` (not left `pending`).
  rc 3 -> same, no increment.
- **Only `rm -f "$digest_file"` when `failed == 0`.** If any checked row failed,
  PRESERVE the digest and `exit 1` so the failure is visible and the digest can be
  retried. This is the exact Codex high finding ("checked rows can be lost permanently
  after apply failure").
- Append the `failed` count to the apply log line.

### 6. Out of scope (note, do not build now)

Codex medium finding ("apply trusts edited rows before validating against pending
ledger") matters mostly for the manual `cmd_apply` path with human-edited tables. With
auto-apply as the default, `cmd_apply` is rarely used and operates on K2B-generated
digests. Deferred to a follow-up; flagged in the ship note, not silently dropped.

## MVP test (binary, named-bug)

Named bug: *"weave links wait for a human gate Keith never passes, and a failed apply
silently loses the approved link."*

Pass conditions (all must hold):
1. A run with a >=0.80-confidence proposal adds `[[to_slug]]` to the FROM page's
   `related:` field with NO digest written and NO human step. Ledger row = `applied`.
2. A <0.80-confidence proposal does NOT alter any page, writes NO digest, and lands as
   `held-low-confidence` in the ledger (suppressed from re-proposal).
3. When the add-related helper hard-fails (rc 1) on a checked row in `cmd_apply`, the
   digest is NOT deleted and the command exits non-zero; ledger row = `apply-failed`,
   not `applied`, not silently `pending`.
4. Full existing `tests/test-k2b-weave.sh` suite still green (legacy digest path under
   `WEAVE_AUTO_APPLY=false` unchanged).

## Files

- `scripts/k2b-weave.sh` -- config, `apply_one_proposal` rc map, `get_ledger_exclusions`,
  `cmd_run` auto-apply branch, `cmd_apply` hardening, new `mark_ledger_*` helpers.
- `tests/test-k2b-weave.sh` -- new tests for the 4 pass conditions; keep legacy tests
  green by setting `WEAVE_AUTO_APPLY=false` in the existing run/apply tests OR updating
  them to the new default (decide during TDD; prefer explicit env in legacy tests).
- `.claude/skills/k2b-weave/SKILL.md` -- document the auto-apply contract + thresholds.
- `wiki/concepts/feature_*` -- feature note (new or update) with the MVP test.

## Review + ship

- Claude-built diff -> Codex reviewer, both checkpoints (this plan review now;
  pre-commit review before commit). `--builder-family anthropic --primary codex --wait`.
- `/ship` with `BUILDER_FAMILY=anthropic`, then `/sync` to Mac Mini so the next
  scheduled weave run is hands-off.

---

## Plan review incorporated (Codex NEEDS-ATTENTION 2026-06-29, job 12d5e3)

Three findings, all accepted. Revisions:

### R1 -- enforce exclusions AFTER the worker returns (was: worker-hint only)

Today `combined_excl` is only sent to Kimi as input; nothing filters the response, so
the worker COULD re-propose an already rejected/deferred/held/applied pair and (under
auto-apply) silently override that prior decision. Fix: in `cmd_run`, immediately
before the auto-apply split, recompute `get_ledger_exclusions` + `build_wikilink_exclusions`
and deterministically DROP any `scored` proposal whose `{from,to}` is excluded. Log the
drop count (`log_info` + metrics). New test: feed a high-confidence proposal for a pair
already `rejected` (and one already `held-low-confidence`) in the ledger; assert it is
NOT applied and the page is unchanged. This filter also protects the legacy digest path.

### R2 -- pair-level retry upsert (was: append fresh row, retry never accumulates)

`_update_ledger_pair` only mutates `status=="pending"` rows and `append_proposals_to_ledger`
resets `retry_count:0` per row, so appending a fresh `apply-failed` row each run would
never reach `apply-failed-permanent`. Fix: add `scripts/k2b-weave-record-apply.py`
(pure-Python, unit-testable) that upserts ONE row per `{from,to}` pair: reads prior
retry_count across existing rows, and:
- `applied` -> status `applied`
- `stale` (rc 2) -> status `stale-renamed`
- hard fail (rc 1) -> retry_count+1; status `apply-failed-permanent` if
  retry_count >= `MAX_RETRY_COUNT` (3) else `apply-failed`
- concurrency (rc 3) -> status `apply-failed` WITHOUT increment
- `held` -> status `held-low-confidence`
It updates the existing pair row in place (rewrite JSONL via tmp+rename under the held
lock) or appends if the pair has no row yet. `cmd_run` auto-apply and the hardened
`cmd_apply` both call it. New test: three consecutive hard-failing auto-runs for one
pair end in `apply-failed-permanent` with a `notify_failure` alert, not three
`apply-failed` rows.

### R3 -- make the policy-ledger autonomy gate EXECUTABLE, and flip it (with approval)

`policy-ledger.jsonl:9` has `{scope:k2b-weave, action:crosslink_apply,
auto_eligible:false, graduation_threshold:10}` and SKILL.md calls the check MANDATORY,
but NO runtime code reads it -- the cron calls `k2b-weave.sh run` directly, so the gate
is documentation only. Fix:
1. `cmd_run` reads `policy-ledger.jsonl`, finds the `k2b-weave`/`crosslink_apply`
   autonomy entry, and auto-applies ONLY IF `auto_eligible == true`. If the entry is
   absent or false, fall back to the legacy digest path. The guard becomes real.
2. Effective gate = `WEAVE_AUTO_APPLY=true` (env kill-switch, default true) AND
   policy-ledger `auto_eligible == true`. Either can disable.
3. Flip the ledger entry to `auto_eligible:true` as part of THIS approved change
   (Keith authorized auto-apply 2026-06-29), bumping `approved` and adding an
   `approved_by:"keith"`,`approved_date:"2026-06-29"` note. This honors the guard
   mechanism rather than bypassing it.
4. Update BOTH `.claude/skills/k2b-weave/SKILL.md` AND `.agents/skills/k2b-weave/SKILL.md`
   (AGENTS.md parity) to document executable auto-apply, and run
   `scripts/verify-skills-parity.sh` before ship.

MVP test gains a 4th condition: with the policy entry `auto_eligible:false`, a run
writes a digest and applies nothing (gate respected); with it `true`, the run
auto-applies and writes no digest.
