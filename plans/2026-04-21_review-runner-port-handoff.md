# Review Runner Port -- handoff for a fresh session

This file is a self-contained handoff. Paste the Step 1 prompt verbatim into a fresh Claude Code session. That session remembers nothing from 2026-04-21 when this was written -- all required context is in this file.

## Why this exists

2026-04-21 `/ship` of `feature_research-videos-pick-deepextract` hit two reviewer infrastructure failures back-to-back:

1. **Codex hung.** The `codex:rescue` Agent call investigated source files for 15+ min then stopped emitting verdicts silently -- hit the exact anti-pattern L-2026-04-19-001 warned about (agent-tool hides progress, makes session look hung).
2. **MiniMax HTTP 529.** Server-side "overloaded" rejection hit both `--scope diff` and `--scope files` calls. Required manual retry via sleep-and-rerun.

Point fixes landed in commit `5c8bafa`:
- MiniMax `urlopen` timeout bumped 180s -> 300s (`scripts/lib/minimax_common.py`)
- HTTP retry on 502/503/504/529 with 10s/20s/40s exponential backoff
- Same file, network errors (URLError) also get 1-3 retries with the same backoff

Those are point fixes, not the architectural answer. **K2Bi already solved the orchestration problem properly** with a unified review runner that has auto-fallback + watchdog + hard deadline + quality gate. This handoff is the port plan for K2B.

## What K2Bi has (the port target)

Three files (~610 LOC combined) under `~/Projects/K2Bi/scripts/`:

| File | LOC | Role |
|---|---|---|
| `review.sh` | 29 | Thin bash entrypoint; `exec python3 lib/review_runner.py $@` |
| `review-poll.sh` | 29 | Thin bash entrypoint for polling a running job by ID |
| `lib/review_runner.py` | 553 | The Python orchestrator -- the substance of the port |

What `review_runner.py` does:

1. **Auto fallback chain** (`run_fallback_chain`): tries primary (codex by default); on any non-zero rc, deadline, quality-gate fail, or unavailable-reason, AUTOMATICALLY runs the secondary (minimax) for the same scope. No manual intervention. Both-fail → exit 2.
2. **Hard deadline + SIGTERM** (`run_one_reviewer`, default 360s): forcibly kills the child when deadline hits. No more "hang forever."
3. **Watchdog thread** (`watchdog_thread`): injects `HEARTBEAT` / `HEARTBEAT_STALE` / `WEDGE_SUSPECTED` lines into the unified log every few seconds. Polling shows fresh activity even during pure-inference phases (which would otherwise look identical to a hung process).
4. **Background mode by default** (`os.fork()` + `os.setsid()`): returns JSON envelope with `job_id` + `log_path` + `state_path` + `pid` + `hint_poll_cmd` immediately. `--wait` forces foreground.
5. **Quality gate on rc=0** (lines 360-374): after child exits clean, scans log for verdict markers (`"# Codex Review"`, `"# MiniMax"`, `"APPROVE"`, `"NEEDS-ATTENTION"`, `'"verdict"'`, `"Review output captured"`). If none present, treats as failure (rc=125) and forces fallback. Prevents silent-approve from a reviewer that exited clean but produced no verdict.
6. **Codex scope preprocessing** (`_working_tree_eisdir_hazard`, `codex_unavailable_reason`, lines 79-147): pre-detects scopes Codex can't handle (dirty directories that trip EISDIR because Codex's working-tree walk calls `read()` on directory paths; `plan` scope which Codex's working-tree walk can't target). Routes straight to MiniMax instead of burning a failed Codex attempt + timing it out.
7. **Poll command** (`cmd_poll`): returns JSON snapshot with `status`, `phase`, `elapsed_s`, `last_activity_s_ago`, `deadline_remaining_s`, `reviewer_current`, `reviewer_attempts[]`, `primary_used`, `fallback_used`, `exit_code`, log tail 20 lines, `should_poll_again`, `recommended_poll_interval_s`.

Scopes: `diff` (default), `working-tree`, `files`, `plan`.

## What K2B has today (the current baseline)

- `scripts/minimax-review.sh` -- thin wrapper, execs `scripts/lib/minimax_review.py`. Has `--scope {working-tree,diff,plan,files}` but no Codex fallback built-in. MiniMax-only caller.
- `scripts/lib/minimax_review.py` -- MiniMax-only, synchronous, no fallback.
- `scripts/lib/minimax_common.py` -- shared HTTP client; as of `5c8bafa` has 300s timeout + retry on 502/503/504/529 + network-error retry.
- `.claude/skills/k2b-ship/SKILL.md` Step 3 -- inline bash patterns for Codex background-poll + MiniMax fallback. Codex invocation via `$CODEX_PLUGIN/scripts/codex-companion.mjs` with `run_in_background: true` + output-file tail. MiniMax fallback via a separate bash block that runs `scripts/minimax-review.sh` when Codex unreachable.
- `scripts/ship-detect-tier.py` + `scripts/lib/tier_detection.py` + `scripts/tier3-paths.yml` -- the tier classifier from `feature_adversarial-review-tiering` Ship 1 (shipped 2026-04-19 at `2f54136`).

K2B's flow: tier classifier first (0/1/2/3); per-tier routing decides pass count + reviewer. K2Bi's runner does NOT know about tier classification -- K2Bi's `invest-ship` uses a simpler "primary + fallback" model. The port must compose with K2B's tier-awareness.

## Goal

Port K2Bi's runner to K2B in a way that **composes** with K2B's tier classifier. Division of responsibilities after the port:

- **Tier classifier decides**: WHEN to review (Tier 0 skips) and HOW RIGOROUSLY (Tier 1 = single pass, Tier 2 = single pass, Tier 3 = iterate until clean).
- **Runner decides**: HOW TO INVOKE the reviewer (codex first with fallback to minimax, deadline, watchdog, quality gate).
- **`k2b-ship` Step 3 glues them**: calls the runner per tier-specific loop. Tier 0 skips, Tier 1/2 call the runner once and use its verdict, Tier 3 loops calling the runner until APPROVE or iteration cap.

## Step 1 -- Study + scope the port (plan only, no code)

Paste this prompt into a fresh session:

```
Port K2Bi's unified review runner to K2B. Fresh session -- no prior context. This is a PLAN-WRITING task only. Do NOT implement.

Read IN THIS ORDER:

1. ~/Projects/K2B/plans/2026-04-21_review-runner-port-handoff.md (this handoff file -- gives you the full context, risks, and goals)
2. ~/Projects/K2Bi/scripts/review.sh
3. ~/Projects/K2Bi/scripts/review-poll.sh
4. ~/Projects/K2Bi/scripts/lib/review_runner.py (the whole 553 LOC)
5. ~/Projects/K2B/scripts/minimax-review.sh
6. ~/Projects/K2B/scripts/lib/minimax_review.py
7. ~/Projects/K2B/scripts/lib/minimax_common.py (note: 300s timeout + 502/503/504/529 retry landed 2026-04-21 in commit 5c8bafa -- preserve these, do not undo)
8. ~/Projects/K2B/.claude/skills/k2b-ship/SKILL.md Step 3 section in full (adversarial pre-commit review gate, including the tier routing blocks 3b.0 / 3b.1 / 3b.2 / 3c and the MiniMax fallback invocation pattern)
9. ~/Projects/K2B/scripts/ship-detect-tier.py
10. ~/Projects/K2B/scripts/lib/tier_detection.py

Before writing the plan, also check whether K2Bi has extended the runner since 2026-04-21:

cd ~/Projects/K2Bi && git log --since="2026-04-21" --oneline -- scripts/review.sh scripts/review-poll.sh scripts/lib/review_runner.py

If material changes landed (new scopes, new reviewers, better watchdog), fold them into the port scope. Port the LATEST K2Bi version, not the 2026-04-21 snapshot.

Then write a plan at ~/Projects/K2B/plans/<today-ISO-date>_review-runner-port.md covering EXACTLY these sections (do not reorder or drop):

a) COMPONENT MAP
   For each of the 3 K2Bi files, state: ports 1:1 | needs adaptation (specify what) | not applicable (specify why). Flag files that collide with existing K2B names.

b) NAMING DECISION
   Port as scripts/review.sh + scripts/review-poll.sh + scripts/lib/review_runner.py (same names as K2Bi)? Or rename to avoid confusion with the existing scripts/minimax-review.sh? Default recommendation: port as-is and make scripts/minimax-review.sh a thin backward-compat wrapper that delegates to scripts/review.sh --primary minimax (preserves existing callers, zero behavioral change for them, new callers use review.sh). Justify your call either way.

c) TIER INTEGRATION
   Concrete rewrite of k2b-ship SKILL.md Step 3b.0/3b.1/3b.2/3c bash blocks to call the new runner. Show old-code -> new-code diff sketch per tier. Tier 3 iterate-until-clean loop stays in k2b-ship (runner returns per-invocation verdict, k2b-ship decides whether to iterate).

d) CODEX SCOPE PREPROCESSING
   K2Bi's _working_tree_eisdir_hazard() and codex_unavailable_reason() protect Codex from scopes it can't handle. Does K2B's working tree produce EISDIR hazards? (Check whether K2B's dirty diff ever includes directory paths that Codex would read() -- look at recent diffs under .claude/skills/ and scripts/, both of which K2B ships regularly). Decide: port these guards verbatim, adapt, or skip.

e) QUALITY GATE VERDICT MARKERS
   K2Bi's markers tuple: ("# Codex Review", "# MiniMax", "APPROVE", "NEEDS-ATTENTION", '"verdict"', "Review output captured"). Confirm K2B's reviewers emit at least one. Grep recent .minimax-reviews/*.json archives + codex review output from recent ships to verify. Expand or prune the tuple as needed.

f) TEST PLAN
   Find K2Bi's tests: grep ~/Projects/K2Bi -r "test_review" --include="*.py" | head. Decide per test: port 1:1, port with adaptation, or rewrite. Minimum coverage K2B needs: runner-available-with-codex, runner-codex-hang-falls-back-to-minimax, runner-both-fail-returns-exit-2, deadline-kill-after-N-seconds, quality-gate-no-verdict-forces-fallback. Integration tests with mocked Codex/MiniMax subprocess are acceptable.

g) SHIP PLAN
   One commit or split into "land the runner" + "switch k2b-ship over"? Default recommendation: split into 2 commits. Commit 1 = purely additive (new files, no behavioral change for k2b-ship callers -- runner exists but nothing calls it yet). Commit 2 = flip k2b-ship over to call runner + delete the inlined bash Codex+MiniMax blocks. Easier rollback if commit 2 breaks something. Justify your call.

h) RISKS / OPEN QUESTIONS
   Everything that could bite in production on the Mac Mini (pm2 env inheritance, stdout buffering differences, signal handling, Python version differences between MacBook and Mini -- Mini runs Homebrew Python 3.14). Flag anything that would need verification on Mini before shipping.

After the plan is written, run plan review:
- Primary: Codex via /codex:adversarial-review challenge the plan -- point it at the plan file.
- Fallback: MiniMax via scripts/minimax-review.sh --scope plan --plan <path>. If the working-tree/diff scope would include the plan file's references (the plan file lives under plans/ which is NOT in any deploy category), you can pass --scope plan directly.

Present the review findings to Keith VERBATIM. Do not paraphrase. Do not implement until Keith approves the plan (with or without changes).
```

## Step 2 -- Implement (run only after Keith approves the plan)

Paste this prompt into a fresh session (or continue the Step 1 session if it's still warm).

```
Implement the review-runner port per the approved plan at ~/Projects/K2B/plans/<today-ISO-date>_review-runner-port.md. Fresh session -- read the plan in full before writing any code.

K2B conventions:
- TDD: write failing tests first, then implementation. Integration tests with mocked Codex/MiniMax subprocess via patch or a minimal fake-subprocess helper are fine.
- Commit discipline: one commit per logical unit. Default per the plan: commit 1 lands the runner (additive), commit 2 switches k2b-ship over (behavioral flip). Never batch both into one commit.
- Checkpoint 2 MANDATORY per commit. For commit 2 specifically, the runner reviews itself (bootstrap -- the new code path is in the diff being reviewed). If the runner rejects its own switchover diff, fix and re-review.
- /ship through the k2b-ship skill for both commits. /ship will invoke Codex Checkpoint 2 (or MiniMax fallback via the NEW runner for commit 2) automatically.
- /sync after each commit that touches scripts/ or .claude/skills/ (both will, for this port).
- Update or create a feature note in ~/Projects/K2B-Vault/wiki/concepts/. Default: extend feature_adversarial-review-tiering's Updates section (this work is a direct infrastructure followup to that feature's Ship 1 shipping pattern). Alternative: new feature_review-runner-port.md. Plan's section (g) should have specified which.

Post-land smoke test on Mini:
1. /sync to push runner + updated k2b-ship to Mini.
2. ssh macmini "cd ~/Projects/K2B && scripts/review.sh working-tree --wait" on a trivial Mini-side change (e.g. touch a file). Verify runner exits 0, log contains a verdict marker, archive file landed at .minimax-reviews/ or .codex-reviews/.
3. Temporarily break Codex on Mini (e.g. rename the codex plugin dir) and re-run. Verify auto-fallback to MiniMax, both recorded in state.reviewer_attempts[].
4. Restore Codex, re-run, verify primary path works again.

If smoke test fails on Mini but passes on MacBook, pm2/env/Python-version differences are the likely culprit -- the plan's section (h) should have flagged these. Fix and re-ship.
```

## Who does what

- **You (Keith)**: paste the Step 1 prompt into a fresh session. Read the plan the session writes + the plan-review findings. Approve (or adjust + re-review). Paste the Step 2 prompt.
- **The Step 1 session**: plan + plan review only. Stops for Keith's approval.
- **The Step 2 session** (can be the same session or fresh): implements per the approved plan, ships via `/ship`, smoke-tests on Mini.
- **Future-me (2026-04-21's Claude)**: does not exist in these sessions. This handoff file IS the memory.

## If the port turns out harder than expected

The runner is 553 LOC of subtle concurrency code (fork, threads, signal handling, subprocess lifecycle). If the Step 1 plan-review surfaces more than 5 P1s or the implementation session starts fighting the design, **stop and reconsider**. Alternatives worth weighing before forcing the port:

1. **Stay with the point-fixes.** The `scripts/lib/minimax_common.py` retry + 300s timeout shipped 2026-04-21 already resolves the specific failure modes that triggered this port. Check: has `/ship` hit the same failure modes in the intervening N days? If not, the port may be premature optimization.
2. **Port only the watchdog + quality-gate parts** into K2B's existing k2b-ship Step 3 bash blocks, without adopting the full Python orchestrator. Smaller change, less to get wrong, covers the "reviewer hangs silently" failure mode that was the real source of pain 2026-04-21.
3. **Wait for K2Bi to extend the runner further** -- K2Bi's `invest-ship` is under active development and may grow features (e.g. multi-model consensus, context-size-aware scope selection) that are easier to port once stable than chase the moving target.

Keith's call on which path. The handoff's default assumption is the full port, but it's not dogma.
