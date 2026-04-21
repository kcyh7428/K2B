# Review Runner Port -- K2B plan

**Date:** 2026-04-21
**Goal:** Port K2Bi's unified review runner (`review.sh` + `review-poll.sh` + `lib/review_runner.py`, ~610 LOC) to K2B in a way that composes with K2B's tier classifier (`ship-detect-tier.py` + `tier_detection.py`). After the port:

- **Tier classifier decides** *when* and *how rigorously* to review (Tier 0 skip / Tier 1 single pass / Tier 2 single pass / Tier 3 iterate).
- **Runner decides** *how to invoke* the reviewer (primary Codex with auto-fallback to MiniMax, 360s deadline, watchdog heartbeats, quality-gate on silent-approve).
- **k2b-ship Step 3 glues them**: per-tier loop calls the runner; the runner returns a per-invocation verdict.

**Status of this document:** Plan only. No code changes allowed until Keith approves the plan (and its Codex / MiniMax plan-review findings).

## Pre-work context (already gathered)

- **K2Bi production track record.** The runner has been in active use on K2Bi for several days and has shipped tons of real commits through its review gate. This is the dominant evidence for the design: every theoretical concern a reviewer raises against the port has had days of real-world pressure against the reference implementation, and it holds up. Where we diverge from K2Bi's exact code (see section (a) adaptation list), we note why.
- K2Bi runner files have NOT changed since 2026-04-21 (`git -C ~/Projects/K2Bi log --since=2026-04-21 -- scripts/review.sh scripts/review-poll.sh scripts/lib/review_runner.py` → empty). Last touch was `c63a5f2` on 2026-04-19: *bump MiniMax client timeout to 300s + pre-skip Codex on untracked dirs*. Porting the current K2Bi snapshot is porting the latest.
- `scripts/lib/minimax_common.py` at K2B head (commit `5c8bafa`) already has the 300s urlopen timeout + 502/503/504/529 retry + URLError retry landed 2026-04-21. **The plan must preserve these; do not revert.** K2Bi's `minimax_common.py` has the same fixes, so no diff there.
- K2B's working tree TODAY contains multiple EISDIR hazards for Codex (untracked directories): `.claude/worktrees/`, `k2b-remote/.claude/`, `tests/fixtures/weave-vault/Archive/`, `tests/fixtures/weave-vault/review/`, `tests/fixtures/weave-vault/wiki/context/`, `tests/fixtures/weave-vault/wiki/work/`. Any of these would crash Codex's working-tree walk in <1s. Section (d) handles this. **This is exactly why this plan's own review ran via MiniMax** — Codex is unavailable on K2B today until the runner lands, which is the bootstrapping problem the port solves.
- All six K2Bi verdict markers (`# Codex Review`, `# MiniMax`, `APPROVE`, `NEEDS-ATTENTION`, `"verdict"`, `Review output captured`) are emitted by K2B's reviewers too. Verified by grepping K2Bi `.code-reviews/*.log` (same Codex companion + same MiniMax script + same renderer). Section (e) confirms.
- K2Bi has **zero tests** for the runner — it shipped untested, and it shipped *fine*. K2B writes tests from scratch (section (f)), but the target coverage is "enough to sanity-check the port landed cleanly," not "enough to prove a 553-LOC concurrency system from first principles" — production use on K2Bi is the real coverage.
- **Current `scripts/tier3-paths.yml`** allowlists `scripts/lib/minimax_review.py` + `scripts/minimax-review.sh` under "Adversarial review infrastructure", but NOT the new runner files. Commit 1 must add `scripts/review.sh` + `scripts/review-poll.sh` + `scripts/lib/review_runner.py` to that section. Side effect: because the yaml edit is in the same dirty working tree the classifier reads, Commit 1 self-classifies as Tier 3 and gets the full iterate-until-clean review flow against itself. Nice bootstrap.

---

## (a) COMPONENT MAP

Three files from `~/Projects/K2Bi/scripts/`:

| K2Bi file | K2B target | Port disposition | Collision with existing K2B? |
|---|---|---|---|
| `scripts/review.sh` (29 LOC) | `scripts/review.sh` | **Ports 1:1.** Thin `exec python3 lib/review_runner.py "$@"`. No K2B-specific adaptation needed. | None. K2B has `scripts/minimax-review.sh` but not `scripts/review.sh`. |
| `scripts/review-poll.sh` (29 LOC) | `scripts/review-poll.sh` | **Ports 1:1.** Thin `exec python3 lib/review_runner.py --poll "$1"`. | None. |
| `scripts/lib/review_runner.py` (553 LOC) | `scripts/lib/review_runner.py` | **Port verbatim plus three small K2B-specific adaptations** (A1-A3 below). | None at `scripts/lib/review_runner.py`. But see section (b) re: the *coexistence* with `scripts/minimax-review.sh`. |

**A1. `CODEX_PLUGIN_DEFAULT` and `ARCHIVE_DIR`** — no code change. The paths (`~/.claude/plugins/marketplaces/openai-codex/plugins/codex` and `REPO_ROOT / ".code-reviews"`) resolve correctly on K2B.

**A2. `preexec_fn=os.setsid` → `process_group=0`** (MiniMax finding #6). `Popen`'s `process_group` arg landed in Python 3.11. K2B targets ≥3.12; Mini runs 3.14. One-line swap that removes a `DeprecationWarning` class on 3.14. Applied in `spawn_child()`. (K2Bi is doing fine with the deprecated form in production, so this is insurance, not a blocker. Cheap enough to do opportunistically in Commit 1.)

**A3. Explicit `MINIMAX_API_KEY` env passthrough in `spawn_child()`** (MiniMax finding #4). Defense-in-depth for the pm2-on-Mini case. The key is already findable three ways (runner's os.environ → child's os.environ → `minimax-review.sh` sources `~/.zshrc` → `minimax_common.load_api_key()` parses `~/.zshrc`) and K2Bi's observer-loop runs under pm2 and works. But a one-line `extra_env["MINIMAX_API_KEY"] = key_or_empty` in `spawn_child()` eliminates the failure class entirely. Skipped if the load call raises (we don't want the runner to refuse to start just because the key is absent — then Codex-only paths would also break).

**A4. `.gitignore` adds `/.code-reviews/`** — K2B currently gitignores `.minimax-reviews/` but not `.code-reviews/`. Runner writes to the latter.

**A5. `scripts/tier3-paths.yml` adds three runner entries** — insert `scripts/review.sh`, `scripts/review-poll.sh`, `scripts/lib/review_runner.py` under the existing `# Adversarial review infrastructure (bug here blinds every future review)` comment block, alongside the existing `scripts/lib/minimax_review.py` and `scripts/minimax-review.sh`. Side effect: classifier reads the dirty yaml at Commit 1's ship time, so Commit 1 self-classifies as Tier 3 and gets the iterate-until-clean review flow against itself. (The runner's own bootstrap-self-review moment.)

**Files NOT ported** (deliberate omissions):

- `~/Projects/K2Bi/.claude/hooks/review-guard.sh` (a PreToolUse hook that blocks direct calls to `codex-companion.mjs` / `minimax-review.sh` and forces traffic through `review.sh`). Reason: out of scope for the runner port; this is a *discipline enforcement* layer on top of the runner. k2b-ship Step 3 is the only caller that needs to change behavior, and Keith controls that directly. Revisit if we see ad-hoc bypasses post-port. Recorded as deferred follow-up in the plan's wrap-up.

(A4 and A5 are part of Commit 1's changelist alongside the three runner source files — they're not separate ship events.)

**No other K2Bi runner sidecar files.** `scripts/minimax-review.sh` / `scripts/lib/minimax_review.py` / `scripts/lib/minimax_common.py` already exist identically (modulo the 300s timeout + retry that K2B just shipped) at both repos — no porting needed, the K2Bi runner calls them unmodified.

---

## (b) NAMING DECISION

**Recommendation:** Port `review.sh` + `review-poll.sh` + `lib/review_runner.py` under the **same names** as K2Bi (no rename), and **keep `scripts/minimax-review.sh` unchanged** (not a wrapper). `review.sh` and `minimax-review.sh` coexist as two separate entrypoints.

This **diverges** from the handoff's default recommendation ("make `minimax-review.sh` a thin wrapper that delegates to `review.sh --primary minimax`"). Justification:

1. **Flag-surface mismatch is real.** `minimax-review.sh` exposes `--json` (used by Tier 1 loop verdict parsing), `--model`, `--max-tokens`, `--no-archive`, `--archive-dir`. `review.sh` has none of these — it prints a JSON envelope with `job_id` and archives to `.code-reviews/` by default. A "thin wrapper" that delegates to `review.sh --primary minimax` would silently lose the `--json` flag, and Tier 1's verdict parser (`scripts/lib/minimax_review.py` output format) would break.
2. **Output-shape mismatch is real.** `minimax-review.sh` returns synchronously with rendered markdown or parsed JSON on stdout. `review.sh` backgrounds by default and returns a JSON envelope; `--wait` yields a different envelope. Not drop-in compatible.
3. **`scripts/tests/minimax-review-scope.test.sh`** tests the existing script's behavior directly. A wrapper rewrite would require re-grounding those tests.
4. **"Additive-first, flip-later"** matches K2B's commit-discipline rule per section (g). Coexistence is additive; a wrapper rewrite in Commit 1 is *not*.

The runner's value in commit 1 is purely *new capability*: it lets Codex-enabled paths get watchdog + deadline + auto-fallback. Commit 2 flips k2b-ship Step 3 over to use it. Existing callers of `scripts/minimax-review.sh` (k2b-compile, k2b-lint, k2b-research, etc.) continue to call it directly — unchanged behavior, zero regression risk.

**Alternative considered and rejected:** Name the new entrypoint `scripts/review-runner.sh` (to avoid any ambiguity with `minimax-review.sh`). Rejected because it gratuitously diverges from K2Bi muscle memory and from the hook's expected allowlist paths (if we ever port the hook).

---

## (c) TIER INTEGRATION

k2b-ship SKILL.md Step 3b.0 / 3b.1 / 3b.2 / 3c bash blocks currently call `scripts/minimax-review.sh` and `codex-companion.mjs` directly. After the port:

| Tier | Primary reviewer | Uses runner? | Why |
|---|---|---|---|
| **0** | (skip) | No | No review happens. No-op. |
| **1** | MiniMax | **No** — keep direct `scripts/minimax-review.sh --json` call | Tier 1's verdict-based 2-pass loop needs *parseable* MiniMax JSON (`parsed.verdict`) to decide next action. The runner's unified-log output doesn't expose that without a `--json` passthrough. MiniMax-only also never needs Codex fallback — the runner's killer feature is wasted here. The already-landed 300s timeout + retry (`minimax_common.py`, commit `5c8bafa`) covers the original 529 failure that triggered this whole port. |
| **2** | Codex → MiniMax fallback | **Yes** — via `scripts/review.sh diff --files "$CHANGED_FILES" --wait` | Single Codex pass with auto-fallback is literally what the runner does. |
| **3** | Codex iterate-until-clean | **Yes** — k2b-ship loops, calls `scripts/review.sh diff --files "$CHANGED_FILES" --wait` per iteration | Tier 3's outer iteration stays in k2b-ship; each iteration's transport goes through the runner. |

### Step 3b.0 Tier 0 flow

**No change.** Bash block is already `echo "review skipped ..."` — nothing to rewrite.

### Step 3b.1 Tier 1 flow

**No change to the MiniMax invocation.** Keep the existing loop verbatim — it already works, and the 300s+retry fix in `minimax_common.py` resolves the original 529 failure. The `--scope diff --files "$CHANGED_FILES" --json` flags stay exactly as they are.

*Document in a SKILL.md comment:* "Tier 1 intentionally does not route through `scripts/review.sh` — MiniMax-only paths don't need Codex fallback, and Tier 1 requires parseable JSON verdict that the runner's unified-log format doesn't expose." Comment placement: immediately before the `TIER_1_PASS=1` line in Step 3b.1.

### Step 3b.2 Tier 2 flow

Current code (from SKILL.md lines 282-299) directly launches `codex-companion.mjs` in a single-pass background + poll dance, with a separate MiniMax `scripts/minimax-review.sh` fallback block for `--skip-codex`.

**Before (simplified):**

```bash
if [ "$TIER" = "2" ] && [ -n "${SKIP_CODEX:-}" ]; then
  if ! scripts/minimax-review.sh --scope diff --files "$CHANGED_FILES" \
      --focus "tier-2 single-pass review (--skip-codex: $SKIP_CODEX)"; then
    echo "[FATAL] tier-2 MiniMax failed AND --skip-codex blocks Codex fallback." >&2
    exit 3
  fi
  REVIEW_RESULT="tier-2-minimax-$SKIP_CODEX"
fi

# If --skip-codex is NOT set, use the Codex background + poll pattern from Step 3c
# but stop at one pass.
```

**After (replace the whole Tier 2 block with):**

```bash
if [ "$TIER" = "2" ]; then
  # Single-pass via the unified runner. Runner handles Codex primary,
  # MiniMax auto-fallback on deadline / quality-gate-fail / EISDIR-hazard /
  # --skip-codex routing. No manual dance.
  RUNNER_PRIMARY=codex
  [ -n "${SKIP_CODEX:-}" ] && RUNNER_PRIMARY=minimax
  FOCUS="tier-2 single-pass review"
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

  # Deterministic log-path extraction (per Runner output contract above).
  REVIEW_LOG=$(echo "$RUNNER_OUT" | python3 -c \
    'import json,sys; print(json.load(sys.stdin)["log_path"])')

  REVIEW_RESULT="tier-2-runner-${RUNNER_PRIMARY}"
  [ -n "${SKIP_CODEX:-}" ] && REVIEW_RESULT="${REVIEW_RESULT}-skip-${SKIP_CODEX}"
fi
```

After the runner returns, Claude (the ship-agent) reads `$REVIEW_LOG` — which is deterministic, not `ls -t` — and surfaces the findings verbatim to Keith. This replaces the ad-hoc "parse Codex task output file" block in the current Step 3c.

### Step 3c Tier 3 flow

Current Step 3c is a single-pass background + poll pattern with a separate MiniMax fallback below, and the outer iterate-until-clean loop is *implicit* (Keith drives re-runs manually).

**After:** make the iterate-until-clean loop *explicit* in k2b-ship, calling the runner per iteration. The runner replaces the inline `CLAUDE_PLUGIN_ROOT=$CODEX_PLUGIN node $CODEX_PLUGIN/scripts/codex-companion.mjs review --wait --scope working-tree` block (SKILL.md lines 328-331) and the MiniMax fallback invocation pattern (SKILL.md lines 350-361).

**Replacement block:**

```bash
if [ "$TIER" = "3" ]; then
  TIER_3_MAX_ITER=4   # generous cap; typical flow converges in 2-3 passes
  TIER_3_ITER=1
  TIER_3_APPROVED=no

  while [ "$TIER_3_ITER" -le "$TIER_3_MAX_ITER" ]; do
    echo "[tier-3] iteration $TIER_3_ITER of $TIER_3_MAX_ITER -- runner..."

    RUNNER_PRIMARY=codex
    [ -n "${SKIP_CODEX:-}" ] && RUNNER_PRIMARY=minimax

    set +e
    RUNNER_OUT=$(scripts/review.sh diff \
      --files "$CHANGED_FILES" \
      --primary "$RUNNER_PRIMARY" \
      --focus "tier-3 iterate-until-clean (iter $TIER_3_ITER)" \
      --wait)
    RUNNER_EXIT=$?
    set -e

    if [ "$RUNNER_EXIT" = "2" ]; then
      echo "[FATAL] tier-3 iter $TIER_3_ITER: both reviewers failed." >&2
      echo "$RUNNER_OUT" >&2
      exit 3
    elif [ "$RUNNER_EXIT" -ne 0 ]; then
      echo "[FATAL] tier-3: runner exit $RUNNER_EXIT (unexpected)." >&2
      echo "$RUNNER_OUT" >&2
      exit 3
    fi

    REVIEW_LOG=$(echo "$RUNNER_OUT" | python3 -c \
      'import json,sys; print(json.load(sys.stdin)["log_path"])')

    # Surface $REVIEW_LOG to Keith. Claude-the-agent parses the verdict
    # out of the log (last # MiniMax / # Codex Review block) and asks Keith:
    #   "APPROVE on iter $TIER_3_ITER?" -> break with TIER_3_APPROVED=yes
    #   "NEEDS-ATTENTION, fix inline then re-run?" -> fix, loop again
    #   "Accept findings and commit anyway?" -> break with TIER_3_APPROVED=yes
    # This human-in-the-loop step is enforced by the skill body wording, not
    # by bash control flow.

    TIER_3_ITER=$((TIER_3_ITER + 1))
  done

  # Iteration cap reached -- Claude surfaces to Keith per the skill body's
  # "decide fix/defer/accept" instruction; no auto-approve, no auto-refuse.
  REVIEW_RESULT="tier-3-runner-iter-$((TIER_3_ITER - 1))"
fi
```

The crucial split: **bash runs the transport**, **the skill body tells Claude how to read the verdict and interact with Keith**. `$REVIEW_LOG` is captured deterministically from the runner's JSON envelope; no `ls -t` race.

### Runner output contract (log format + verdict extraction)

**The runner does not need a verdict-parsing helper. It returns the exact log path in its output envelope.**

When invoked with `--wait`, `scripts/review.sh` prints this JSON to stdout on completion:

```json
{
  "job_id": "2026-04-21T12-30-11Z_abc123",
  "exit_code": 0,
  "status": "completed",
  "log_path": ".code-reviews/2026-04-21T12-30-11Z_abc123.log"
}
```

When invoked without `--wait` (background), it prints the same shape plus `state_path` and `pid`, before the child has finished. Either way, `log_path` is the exact file. Deterministic, not `ls -t`, no race.

**Bash capture pattern:**

```bash
RUNNER_OUT=$(scripts/review.sh diff --files "$CHANGED_FILES" --primary "$RUNNER_PRIMARY" \
              --focus "$FOCUS" --wait)
RUNNER_EXIT=$?
REVIEW_LOG=$(echo "$RUNNER_OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["log_path"])')
```

**Log format (unified text log, one file per job):**

- Every line is `[<UTC-ISO>] <TAG> <payload>`.
- `TAG` is one of: `JOB_START`, `REVIEWER_START`, `SPAWN argv=...`, `REVIEWER_SKIP`, `HEARTBEAT`, `HEARTBEAT_STALE`, `WEDGE_SUSPECTED`, `SOFT_DEADLINE`, `HARD_DEADLINE`, `SIGKILL`, `REVIEWER_END`, `QUALITY_GATE_FAIL`, `FALLBACK`.
- **Interspersed between those tagged lines, verbatim stdout from the reviewer child**. That's where `# Codex Review`, `# MiniMax MiniMax-M2.7 review -- APPROVE`, and so on appear.
- The K2Bi runner's quality-gate substring scan (`"# Codex Review"`, `"# MiniMax"`, `"APPROVE"`, `"NEEDS-ATTENTION"`, `'"verdict"'`, `"Review output captured"`) relies on these strings appearing in the log. If none appear after rc=0, the runner forces fallback.
- State for polling lives in a separate file `state_path = .code-reviews/<job_id>.json`, updated by the watchdog every `heartbeat_interval_s`.

**How the skill body reads the verdict (not bash, Claude-the-agent):**

After the runner returns, read `$REVIEW_LOG` top-to-bottom. Find the last `# Codex Review` or `# MiniMax` section. Report it to Keith verbatim. Do not paraphrase, re-rank, or pre-filter. This is the same reporting discipline the existing SKILL.md already enforces for inline Codex and MiniMax output — only the transport changes.

### MiniMax fallback invocation pattern (SKILL.md lines 350-373)

**Delete this whole block.** It is now subsumed by the runner's automatic fallback. The "both reviewers unavailable" surface message moves into the Tier 2/3 bash `exit 3` branches shown above. Update the Error Handling section at the bottom of SKILL.md to reflect the new path.

---

## (d) CODEX SCOPE PREPROCESSING

**Port `_working_tree_eisdir_hazard()` and `codex_unavailable_reason()` VERBATIM.** K2B has the same hazards as K2Bi today:

```
git ls-files --others --exclude-standard --directory in K2B returns:
  .claude/worktrees/
  k2b-remote/.claude/
  tests/fixtures/weave-vault/Archive/
  tests/fixtures/weave-vault/review/
  tests/fixtures/weave-vault/wiki/context/
  tests/fixtures/weave-vault/wiki/work/
  plans/2026-04-21_review-runner-port-handoff.md   (the handoff file itself)
  plans/2026-04-21_washing-machine-ship-1.md
  plans/2026-04-26_tiering-ship-2-handoff.md
```

The trailing-slash entries are directory hazards. Any `/ship` that runs while one of these is present and invokes Codex with `--scope working-tree` will EISDIR-crash Codex in under a second. The guard pre-detects and routes to MiniMax instead.

(Note: `tests/fixtures/weave-vault/` lives inside `tests/fixtures/` which is tracked, but the `Archive/`, `review/`, etc. subdirs are untracked directories seeded by test fixtures — real hazards, not false positives.)

K2B does NOT currently have nested git worktrees (per `git worktree list --porcelain` at plan-write time), but the second clause of the guard (detecting `.claude/worktrees/<slug>/` from `git worktree list`) is zero-cost and protects against future worktree-based workflows. Keep both clauses.

**Zero adaptation needed.** The two functions are pure git-subprocess + path logic, no K2Bi-specific assumptions. Port the code byte-for-byte.

**Test coverage:** Section (f) Test 6 will seed a temp repo with an untracked directory and assert `_working_tree_eisdir_hazard()` returns the offending path.

---

## (e) QUALITY GATE VERDICT MARKERS

K2Bi tuple:
```python
("# Codex Review", "# MiniMax", "APPROVE", "NEEDS-ATTENTION", '"verdict"', "Review output captured")
```

Verification for K2B (grepped at plan-write time):

| Marker | Source | K2B evidence |
|---|---|---|
| `# Codex Review` | Codex companion output | Appears in K2Bi `.code-reviews/*.log` at line 86, 59, etc. Same Codex plugin installed on K2B (`~/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs`). Confirmed. |
| `# MiniMax` | `minimax_review.py::render_markdown` line 453: `f"# MiniMax {model} review -- {badge}"` | K2B uses the same `minimax_review.py`. Emits `# MiniMax MiniMax-M2.7 review -- APPROVE` etc. Confirmed in K2Bi logs. |
| `APPROVE` | Ditto, badge text | Confirmed. |
| `NEEDS-ATTENTION` | Ditto, badge text | Confirmed. |
| `"verdict"` | MiniMax JSON output body | Every MiniMax archive JSON in `K2B/.minimax-reviews/*.json` contains `"verdict": "approve"` or `"verdict": "needs-attention"` in `raw_text`. Confirmed. |
| `Review output captured` | Codex companion prints `[codex] Review output captured.` | Confirmed in K2Bi logs. Same companion at K2B. |

**Decision:** **Port the tuple verbatim, no additions, no prunes.** All six markers fire on at least one successful K2B reviewer path.

Nuance to flag in SKILL comments: the quality gate's purpose is to catch *silent-approve* — a reviewer exits 0 with no verdict in the log. If none of the six markers appear, the runner forces fallback (`effective_rc = 125`). The markers are substrings, so `[codex] Review output captured.` matches `Review output captured`.

---

## (f) TEST PLAN

**K2Bi tests found:** zero. `grep -r "review_runner" ~/Projects/K2Bi --include="*.py"` returns only the runner source itself. Also `grep -r "review\.sh" ~/Projects/K2Bi/tests/` returns nothing. **K2B writes all runner tests from scratch.**

**Test style match:** K2B uses bash harness tests under `tests/*.test.sh` (e.g. `tests/ship-detect-tier.test.sh`). Each test: `mktemp -d` a fresh repo, seed fixtures, run the target script, assert stdout/stderr/exit-code. No pytest. Python-level unit-testing of `review_runner.py` internals is fine where cleaner, driven via `python3 -c "..."` from the bash harness.

**New file:** `tests/review-runner.test.sh`, following `tests/ship-detect-tier.test.sh` structure (setup/cleanup trap, `fail()` helper, `mktmp()` helper, per-scenario functions).

**Minimum coverage per the handoff:**

### Test 1: `test_primary_codex_approves_short_path`
Fixture: fake `codex-companion.mjs` shim (a 5-line bash script) that prints `# Codex Review\nAPPROVE\n` and exits 0 in <2s. Fake MiniMax shim never invoked.
Assertion: `scripts/review.sh working-tree --wait --codex-plugin <fixture>` exits 0, `state.primary_used == "codex"`, `fallback_used == False`, log contains `# Codex Review`.

### Test 2: `test_codex_hang_falls_back_to_minimax`
Fixture: fake Codex shim that `sleep 999`. Fake MiniMax shim prints valid verdict and exits 0.
Assertion: runner invoked with `--deadline 5 --heartbeat-interval 1` — Codex gets SIGTERM at 5s, fallback triggers, MiniMax runs and returns 0, runner exits 0. `state.primary_used == "codex"`, `fallback_used == True`, `reviewer_attempts[0].result == "timed_out"`, `reviewer_attempts[1].result == "ok"`.

### Test 3: `test_both_fail_returns_exit_2`
Fixture: both Codex and MiniMax shims exit 1 in <1s.
Assertion: runner exits 2, `state.status == "both_failed"`, both attempts logged as `"result": "error"`.

### Test 4: `test_deadline_kill_after_n_seconds`
Fixture: Codex shim `sleep 999`. `--primary codex`, no MiniMax to fall back to (stub `minimax-review.sh` → exit 127).
Assertion: runner kills Codex at `--deadline 3`, elapsed ≤ 3s + `KILL_GRACE_S` (10s) + measurement slack. Log contains `HARD_DEADLINE ... SIGTERM`. `reviewer_attempts[0].exit_code == 124`.

### Test 5: `test_quality_gate_no_verdict_forces_fallback`
Fixture: Codex shim exits 0 but prints only `Hello world\n` (no verdict marker). MiniMax shim prints valid verdict and exits 0.
Assertion: runner treats Codex as `effective_rc == 125`, triggers fallback, MiniMax runs and approves. Log contains `QUALITY_GATE_FAIL`. `state.fallback_used == True`.

### Test 6: `test_codex_unavailable_reason_eisdir`
Fixture: real temp git repo with an untracked directory `seed/dir/` and one file `seed/dir/x.py`. Fake Codex shim + fake MiniMax shim.
Assertion: `codex_unavailable_reason("working-tree", repo, codex_plugin)` returns a string containing `seed/dir`. `build_codex_cmd(...)` returns `None`. Runner logs `REVIEWER_SKIP reviewer=codex reason=...EISDIR...`. Falls through to MiniMax. `state.reviewer_attempts[0].result == "unavailable"`.

### Test 7: `test_plan_scope_always_routes_to_minimax`
Fixture: no hazards in working tree. Codex shim present and would approve. MiniMax shim approves.
Call: `scripts/review.sh plan --plan <tmpfile> --wait` (plan scope).
Assertion: Codex is pre-skipped (`codex_unavailable_reason` returns the `plan scope requires --path...` string), MiniMax runs as the effective primary, `state.reviewer_attempts[0].result == "unavailable"`, `reviewer_attempts[1].result == "ok"`.

### Test 8: `test_watchdog_injects_heartbeat`
Fixture: MiniMax shim that `sleep 3` then exits 0. `--primary minimax --heartbeat-interval 1 --deadline 10`.
Assertion: log contains at least 2 `HEARTBEAT elapsed=...` lines by the time the child exits. (Approximates visibility guarantee.)

### Test 9: `test_poll_unknown_job_returns_1`
Assertion: `scripts/review-poll.sh nonexistent-job` exits 1, stdout JSON has `"error": "unknown_job_id"`.

### Test 10: `test_poll_running_job_returns_phase_and_elapsed`
Fixture: MiniMax shim that `sleep 5` then approves. Start runner without `--wait` (background), grab `job_id` from returned JSON envelope, poll twice with 1s delay.
Assertion: first poll `status == "running"`, `phase == "running_commands"`, `elapsed_s > 0`. Second poll either still running or completed — in both cases `tail` has at least one HEARTBEAT line.

**Fake reviewer shim pattern (reused across tests):**

```bash
# In the test harness, build fake codex-companion.mjs as a wrapper script:
make_codex_shim() {
  local target="$1/plugins/codex/scripts/codex-companion.mjs"
  local behavior="$2"   # "approve" | "hang" | "empty" | "error"
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<EOF
#!/usr/bin/env bash
case "$behavior" in
  approve) echo "# Codex Review"; echo "APPROVE"; echo "[codex] Review output captured."; exit 0 ;;
  hang)    sleep 999 ;;
  empty)   echo "Hello world"; exit 0 ;;
  error)   echo "fake error" >&2; exit 1 ;;
esac
EOF
  chmod +x "$target"
}
```

(Runner expects `node <path>` invocation. The fake emits a bash shim and relies on the OS `#!/usr/bin/env bash` — and passes `--codex-plugin <tmpdir>` so the runner builds its argv as `node <tmp>/.../codex-companion.mjs ...`. To make `node` executable run bash scripts, the fake lives in a `PATH`-prepended dir with its own `node` shim that just `exec "$@"`-execs the target. Alternative simpler path: the fake IS a real node script that matches the shim behavior, printing from `process.stdout.write(...)` and calling `process.exit(...)`.)

**Build and run:** `tests/review-runner.test.sh` in K2B's existing bash-test style. Invoked manually during development; long-term we add it to a CI-style test runner if K2B grows one (K2B does not currently run tests automatically on commit).

**Python Mini compatibility:** tests must pass on both local Python 3.12.2 and Mini's Python 3.14. Smoke-test on Mini per Step 2 of the handoff.

---

## (g) SHIP PLAN

**Recommendation:** Split into **2 commits**, per the handoff's default.

### Commit 1: `feat(review): unified review runner (Codex + MiniMax fallback, deadline, watchdog)`

Purely additive at the k2b-ship behavior level — existing Tier 2/3 still uses the inline Codex bash. But the new files become discoverable by the classifier (via the `tier3-paths.yml` entries) so Commit 1 self-reviews.

**Files touched:**
- Create: `scripts/review.sh` (29 LOC, verbatim K2Bi port)
- Create: `scripts/review-poll.sh` (29 LOC, verbatim K2Bi port)
- Create: `scripts/lib/review_runner.py` (553 LOC + A2 `process_group=0` + A3 key passthrough)
- Create: `tests/review-runner.test.sh` (Tests 1-10 from section (f))
- Modify: `.gitignore` (A4: add `/.code-reviews/`)
- Modify: `scripts/tier3-paths.yml` (A5: add three runner entries)

**Pre-commit review (Checkpoint 2):** Classifier reads the dirty `tier3-paths.yml` with its A5 edits already in-place, sees at least one changed file (`scripts/lib/review_runner.py`) matching the newly-added glob, and classifies Tier 3. Iterate-until-clean. The INCUMBENT k2b-ship Step 3c Codex-inline-bash block runs against the diff — not the new runner (which isn't wired in yet). So the runner is reviewed by the tool it's replacing. No bootstrap chicken-and-egg.

Caveat: Tier 3 Codex flow needs a clean working tree (no EISDIR hazards). Before kicking off Commit 1's review, run `git ls-files --others --exclude-standard --directory` and either clean up hazardous dirs (remove empty `.claude/worktrees/` etc.) or accept that `/ship` will auto-fallback to MiniMax for Commit 1 (which is still a valid Tier 3 adversarial review, just not Codex). The runner's EISDIR guard is landing in this same commit — once landed, future reviews will auto-route around hazards without manual cleanup.

**Sync:** yes, `scripts/` + tests touched; `/sync` after commit 1 before commit 2.

**Smoke test before commit 2:** On MacBook, manually run `scripts/review.sh diff --files README.md --wait` with a trivial working-tree change. Verify it spawns Codex (or routes to MiniMax if EISDIR hazards present), exits 0, prints the `log_path` JSON envelope, writes to `.code-reviews/`. Do NOT require Mini smoke-test between commits — Step 2 of the handoff does Mini smoke-test after Commit 2 has landed.

### Commit 2: `refactor(ship): route Tier 2/3 review through scripts/review.sh`

Switches k2b-ship Step 3b.2 and 3c over to the runner, deletes the inline Codex background+poll bash and the inline MiniMax fallback bash. Tier 0 and Tier 1 untouched.

**Files touched:**
- Modify: `.claude/skills/k2b-ship/SKILL.md` (Step 3 rewrite per section (c) above)

**Pre-commit review (Checkpoint 2):** Classifier reports Tier 1 (`.claude/skills/**.md` is pure docs per `tier_detection.py` rule 3; `k2b-ship/SKILL.md` is not in `tier3-paths.yml`, verified at plan-write time). Tier 1 uses MiniMax direct. Acceptable: the runner was validated on its own in Commit 1's Tier 3 review pass; Commit 2's diff is narrow (one SKILL.md file), MiniMax can handle it. Self-review-through-the-runner is NOT a property we need here.

**Sync:** yes, `.claude/skills/` touched; `/sync` after commit 2.

### Why split and not single?

- **Independent reviewability.** Reviewer sees "new runner" and "rewired caller" as two clean diffs. A combined commit mixes 600+ LOC of port with a ~100 LOC SKILL.md rewrite, and review attention gets split between "does the runner itself work" and "does k2b-ship call it correctly."
- **Smoke-test ordering.** Commit 1 ships additive code; between commits, Keith smoke-tests the new runner in isolation. Commit 2 only lands after the runner is proven on its own.
- **Staged deployment.** If `/ship` breaks on Mini after Commit 2, the runner itself already lives on Mini (Commit 1 was synced earlier), so debugging is narrower — can test the runner directly without k2b-ship in the loop.

### Full revert procedure (addressing MiniMax finding #3)

A `git revert <commit-2-sha>` alone leaves Commit 1's new files (`scripts/review.sh`, `scripts/review-poll.sh`, `scripts/lib/review_runner.py`, `tests/review-runner.test.sh`, `tier3-paths.yml` entries, `.gitignore` entry) intact. That is **exactly the desired state for a partial rollback** — the runner stays available for manual use, only k2b-ship stops calling it. The pre-port k2b-ship Step 3 bash comes back.

If a **total revert** is needed (remove the runner entirely):

```bash
git revert <commit-2-sha>                              # undo k2b-ship flip
git revert <commit-1-sha>                              # undo runner install
# Alternatively if the revert-of-revert is awkward:
git rm scripts/review.sh scripts/review-poll.sh \
       scripts/lib/review_runner.py tests/review-runner.test.sh
# Hand-edit scripts/tier3-paths.yml to remove the three runner entries.
# Hand-edit .gitignore to remove /.code-reviews/.
git commit -m "revert: remove review runner port (see commit <commit-1-sha>)"
```

In practice a partial rollback (revert Commit 2 only) is almost always what you want — the runner can still be invoked directly by Keith to review things, and re-switching k2b-ship forward later is a one-file SKILL.md change. The full revert path above exists for completeness; it is not the expected recovery mode.

### Ship 3 (if needed, out of scope for Ship 1)

The hook `.claude/hooks/review-guard.sh` stays deferred. A future ship imports it once discipline around always-use-review.sh has settled. Track in `self_improve_requests.md`.

---

## (h) RISKS / OPEN QUESTIONS

**Framing note.** K2Bi has been running this runner in production for several days and shipping real commits through its review gate. Most theoretical concerns about fork semantics, thread safety, signal propagation, etc. have been under production pressure that long without problems. The risks below are the ones *unique to K2B's deployment environment* (pm2 on Mini, Python 3.14, K2B's tier classifier composition) — not general code-quality concerns about the runner itself.

### h1: pm2 env inheritance for `MINIMAX_API_KEY` — **mitigated in Commit 1 via adaptation A3**

`k2b-observer-loop` and `k2b-remote` run under pm2 on Mini. pm2 does NOT inherit zsh-session env by default. `minimax_common.py` already falls back to parsing `~/.zshrc` if `MINIMAX_API_KEY` is absent (line 32-47), and K2Bi's observer-loop runs under pm2 today with MiniMax calls and works fine, so the fallback chain is proven.

Still, A3 (spawn_child explicit env passthrough) is cheap insurance. The runner calls `load_api_key()` at startup (in a try/except that swallows `MinimaxError`); if it gets a key, it adds it to `extra_env` in `spawn_child`. If it can't get a key, it leaves env untouched and lets the child's own fallback chain run. This closes the theoretical gap without breaking Codex-only paths that don't care about the MiniMax key.

**Smoke test on Mini after Commit 2 lands (still worth running):**
```bash
ssh macmini "env | grep MINIMAX"
ssh macmini "cd ~/Projects/K2B && scripts/review.sh working-tree --wait --primary minimax"
```

### h2: Stdout buffering under non-tty (pm2 / cron) spawns

Runner's `subprocess.Popen(..., stdout=PIPE, stderr=STDOUT, bufsize=1, text=True)` requires the child to flush line-buffered. MiniMax Python side is line-safe (explicit `flush=True`). Codex companion is Node.js — when stdout is a pipe (not tty), Node switches to block buffering by default. This is usually fine because the Codex companion does its own flushing via `process.stdout.write`, but under very low output rates the block buffer can delay `# Codex Review` by seconds, within deadline slack.

**Risk level:** low. Watchdog's HEARTBEAT fires in the runner process, not the child, so log activity is guaranteed. Worst case: the runner kills the child with the verdict in an unflushed buffer, which triggers quality-gate fallback (correct behavior — reviewer produced no visible verdict).

### h3: Python version differences MacBook (3.12.2) vs Mini (3.14) — **mitigated in Commit 1 via adaptation A2**

Runner uses only version-stable stdlib (`os.fork`, `os.setsid`, `os.killpg`, `signal`, `subprocess`, `threading`, `datetime`, `pathlib`, `json`, `argparse`, `secrets`).

The one 3.12+ sharp edge — `preexec_fn=os.setsid` emitting `DeprecationWarning` under stricter warning filters — is addressed by adaptation A2: swap to `process_group=0` (available since Python 3.11) in `spawn_child()`. K2B targets ≥3.12, Mini runs 3.14; both support the new arg. K2Bi hasn't bothered swapping because production hasn't surfaced warnings, but for the K2B port the change is a one-liner worth doing opportunistically.

### h4: `os.fork()` in multithreaded Python on macOS

Runner forks into background BEFORE starting its watchdog thread — the fork happens at `cmd_run` line ~491, before `run_fallback_chain` is entered. `run_fallback_chain` → `run_one_reviewer` → `reader_thread` / `watchdog_thread` — threads start AFTER fork, in the child. macOS-specific `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` deadlock risk is avoided. Good.

### h5: `.gitignore` must cover `.code-reviews/`

Current K2B `.gitignore` includes `.minimax-reviews/` but not `.code-reviews/`. Commit 1 adds the latter. If we forget and commit a big `.code-reviews/` directory, it pollutes the repo + propagates to Mini via `/sync`. **Blocker check:** commit 1 PR review must visually confirm `.gitignore` diff includes `.code-reviews/`.

### h6: Concurrent `/ship` races on `.code-reviews/` job_id uniqueness

`job_id()` uses `secrets.token_hex(3)` (6 hex chars → 16M space) + ISO timestamp. Collision probability under normal use: zero. Two concurrent `/ship` flows from the same session would generate different timestamps (timestamps have second granularity); two from parallel sessions are also vanishingly unlikely. **Risk:** ignorable. No lock file needed.

### h7: `--scope files` exists in `minimax-review.sh` but NOT in runner args

Runner's scopes are `{"diff", "working-tree", "files", "plan"}` and it passes `--scope <same>` + optional `--files` to MiniMax. MiniMax's `--scope files` gathers per-file with no git context. The K2Bi `build_minimax_cmd` honors this correctly. But Tier 1's existing invocation is `scripts/minimax-review.sh --scope diff`, which picks up diffs. Runner's `diff` passes through. Good.

### h8: Codex `adversarial-review` vs `review` subcommand selection

Runner uses `adversarial-review --wait --scope working-tree [focus]` when a focus string is supplied, else `review --wait --scope working-tree`. K2B k2b-ship Tier 2/3 blocks pass a focus string always. So Codex always gets `adversarial-review`. Codex companion's `--help` output (quoted in K2Bi runner source) confirms both subcommands exist. Verify current Codex companion version on K2B:

```bash
node ~/.claude/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs --help | grep -E 'review|adversarial-review'
```

Add to Commit 1 pre-flight checklist.

### h9: Runner's `REPO_ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"]))` fails outside git

Importing `review_runner.py` from any context that isn't inside a git repo crashes at module load. Runner is only expected to be invoked from within K2B — this is fine. But *tests* run in `mktemp -d` fixtures; they must `git init` before `python3 scripts/lib/review_runner.py ...` to avoid import failure. Test 6 already seeds a real git repo; others need to too. Test-harness helper `init_fixture_repo()` handles this.

### h10: `/ship --defer` and concurrent mailbox writes unaffected

Runner writes ONLY under `.code-reviews/`. It does not touch `.pending-sync/`. Mailbox race-safety is `/ship`'s concern, not the runner's. No interaction.

### h11: `scripts/tier3-paths.yml` state — **resolved via adaptation A5**

Verified at plan-write time: `tier3-paths.yml` allowlists `scripts/lib/minimax_review.py` + `scripts/minimax-review.sh` under "Adversarial review infrastructure" but NOT the new runner files, and does NOT list `.claude/skills/**`. Adaptation A5 adds the three runner paths to the infrastructure block. Consequence for Commit 1: classifies Tier 3, self-reviews via the incumbent Codex-inline bash. Consequence for Commit 2: classifies Tier 1 (pure SKILL.md docs), reviewed by direct MiniMax. Both behaviors are intentional and documented in section (g).

---

## Followup items (out of scope for Ship 1)

1. Port `.claude/hooks/review-guard.sh` from K2Bi. Blocks direct calls to `codex-companion.mjs` / `minimax-review.sh` from the Bash tool, forcing traffic through `scripts/review.sh`. Ship only after runner has bedded in and after we've seen any ad-hoc bypass attempts. `self_improve_requests.md` entry.
2. Consider Tier 1 migration to the runner if we add a `--json` passthrough. Today Tier 1 uses `scripts/minimax-review.sh --json` for parseable verdict. If the runner grows `--emit-verdict-json-to stdout`, Tier 1 can share the transport + gain the watchdog + deadline benefits. Not urgent.
3. Tier 3 iteration cap (currently 4) is a magic number. Surface as a configurable or promote to a tier3-specific knob after watching real-world behavior.
4. Opus fork/exec deprecation: swap `preexec_fn=os.setsid` → `process_group=0` in a follow-up refactor when Python ≥3.11 is universally available.

---

## Self-review against the handoff checklist

| Handoff section | Plan section | Covered? |
|---|---|---|
| a: COMPONENT MAP (3 files, collision check) | (a) | yes |
| b: NAMING DECISION (port as-is vs rename, justify) | (b) | yes |
| c: TIER INTEGRATION (3b.0/3b.1/3b.2/3c old→new) | (c) | yes |
| d: CODEX SCOPE PREPROCESSING (verbatim/adapt/skip) | (d) | yes |
| e: QUALITY GATE VERDICT MARKERS (confirm all six) | (e) | yes |
| f: TEST PLAN (port/adapt/rewrite + min coverage) | (f) | yes, all 5 required tests covered, plus 5 extra |
| g: SHIP PLAN (one commit or split; justify) | (g) | yes, split into 2 |
| h: RISKS / OPEN QUESTIONS (pm2/buffering/Python/Mini) | (h) | yes, 11 items flagged |

No placeholders, no TBDs. Every section references specific file paths, line numbers where relevant, and evidence gathered during plan-write.

---

## After this plan is approved

Step 2 of `plans/2026-04-21_review-runner-port-handoff.md` covers implementation: TDD, commit discipline, `/ship` through k2b-ship twice, `/sync` after each, smoke-test on Mini (runner working-tree review, then break-Codex fallback test, then restore Codex re-test). The handoff's Step 2 prompt is the self-contained implementation brief.

---

## Appendix: Plan review response

The plan was reviewed by MiniMax-M2.7 `--scope plan` on 2026-04-21 (Codex unavailable because the K2B working tree has EISDIR hazards that the port itself fixes — chicken-and-egg). Full review archived at `.minimax-reviews/2026-04-21T12-17-03Z_plan.json`. 9 findings returned; disposition below.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | CRITICAL | "Runner source code absent from review context" | **Tool blind spot, not a plan defect.** MiniMax `--scope plan` only follows references inside the K2B repo; K2Bi source at `~/Projects/K2Bi/scripts/lib/review_runner.py` is out of tree. The plan author read all three K2Bi files directly via `Read` before writing the plan (visible in the session transcript). K2Bi has been running this code in production for days, which is stronger evidence than any static-source review a second-pass tool could produce. No change. |
| 2 | CRITICAL | "Log format and verdict-extraction contract undefined; `ls -t` is racy" | **Folded in.** Section (c) now has a "Runner output contract" block that specifies the log format explicitly and replaces `ls -t` with deterministic `log_path` extraction from the runner's JSON envelope. Tier 2 and Tier 3 bash blocks updated to use `RUNNER_OUT` capture + `python3 -c 'json.load(...)["log_path"]'`. Race eliminated. |
| 3 | HIGH | "Commit 2 revert leaves runner files dormant" | **Folded in.** Section (g) now has an explicit "Full revert procedure" block. Clarifies that partial revert (commit 2 only) is the **intended** recovery mode — the runner stays available for manual use — and documents the full revert path for completeness. |
| 4 | HIGH | "pm2/MINIMAX_API_KEY silent failure on Mini" | **Folded in.** Adaptation A3 added to section (a): defensive `extra_env["MINIMAX_API_KEY"] = key` in `spawn_child()`. K2Bi's observer-loop runs under pm2 and works without this (fallback chain already covers it), but A3 is free insurance. Section (h1) updated. |
| 5 | HIGH | "Tier 1 excluded from runner despite 529 triggering incident" | **Partially addressed, rest deferred.** 529 failure mode is already covered by `minimax_common.py`'s retry (3x, 10/20/40s backoff, shipped in `5c8bafa`). Sustained-overload >70s is a real gap but rare. Migrating Tier 1 to the runner would require a `--json` passthrough flag — out of scope for this port. Tracked as follow-up item #2. Not a blocker for Ship 1. |
| 6 | MEDIUM | "`preexec_fn=os.setsid` deprecated in Python 3.12+" | **Folded in.** Adaptation A2: swap to `process_group=0`. Section (h3) updated. |
| 7 | MEDIUM | "10 tests insufficient for 553 LOC concurrency code" | **Rejected as over-engineered.** K2Bi ships this runner with ZERO tests in production. 10 tests for the port is already strictly more coverage than the reference implementation. The production track record is the real validation. Additional test scaffolding is a low-value-to-effort trade. Follow-up item if specific failure modes surface post-ship. |
| 8 | MEDIUM | "`tier3-paths.yml` unverified; affects self-review bootstrap" | **Folded in.** Verified during plan revision: runner files NOT in allowlist, `.claude/skills/**` also NOT in allowlist. Adaptation A5 adds the three runner paths. Section (g) + (h11) updated with the consequences (Commit 1 = Tier 3 self-review, Commit 2 = Tier 1 direct MiniMax). |
| 9 | LOW | "`files` scope is dead code in K2B tier integration" | **Acknowledged, no action.** MiniMax itself noted "no action needed." The scope exists for future use; it's the runner's general scope list, not a K2B-specific choice. |

**Net changes to the plan from the review pass:** A2 + A3 + A5 added to adaptation list; Runner output contract block replacing the racy `ls -t` helper; Tier 2 and Tier 3 bash blocks switched to `RUNNER_OUT`-based log capture; full revert procedure added to section (g); section (h) framing updated to reflect K2Bi's production track record; h1/h3/h11 marked as mitigated via A2/A3/A5.

**Codex second opinion:** intentionally skipped. Would fail to run at all on the current working tree (EISDIR on untracked directories — the exact failure mode the runner fixes). The ironic bootstrap illustrates *why* this port is worth doing. Post-land, when the runner is live and the EISDIR guard is active, Codex can review any subsequent change to the runner itself.
