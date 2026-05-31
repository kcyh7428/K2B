# Orchestrator Increment 2 = Chat 2 (K2Bi narrative dispatch) -- Checkpoint-1 plan

Plan review BEFORE implementation, per K2B adversarial-review discipline and the Ship-1b spec
(`plans/2026-05-30_orchestrator-ship-1b-spec.md` line 142: Increment 2 is blocked on a full
K2Bi-prerequisite enumeration turned into a read-only preflight with tests, done at its own
Checkpoint-1). This document is that enumeration + the preflight design + the binary gate.

Grounded in a read-only investigation of `~/Projects/K2Bi/` and `~/Projects/K2Bi-Vault/`
(2026-05-31). Every prerequisite below cites the K2Bi file it came from.

## Named bug (what this kills)

> Keith brings a trend (often a YouTube video) but can't get it mapped to non-obvious tickers
> without manual K2Bi work.

Increment 2 closes Stage 2 of the Full Scope Tracker -- v1's finish line. After it ships, the
only remaining v1 gate is a theme file being ready for Keith's promotion review.

## Headline finding -- this is simpler than the original Ship-1b sketch feared

The "ticker is OUTPUT not INPUT" architectural concern is already satisfied by K2Bi. The narrative
pipeline:

- takes **raw text** as its seed (`--narrative "<text>"`) -- no ticker required as input;
- produces a theme file with a **candidate ticker list as output**;
- is a **clean non-interactive python module** -- the exact same `python3 -m scripts.lib.X` shape
  the LRCX smoke (Ship 1a) already proved through the allowlisted door;
- **fails fast on its own critical prerequisites** (empty registry, too-few sub-themes, too-few
  candidates, no 2nd/3rd-order survivor) with clear `ValueError` messages.

So Increment 2 reuses Ship 1a's dispatch path almost verbatim. The additive work is: one
parameterized `command_key`, a narrative-specific read-only preflight, the one-flight lock for the
narrative lane, and the conductor procedure in the skill.

## The dispatch

| Field | Value | Evidence |
|---|---|---|
| Command | `python3 -m scripts.lib.invest_narrative_pipeline --narrative "<text>"` | `invest_narrative_pipeline.py:8`, `:1013-1030` |
| cwd | trusted K2Bi workspace (operator config only -- Ship 1a cond 10) | reuse existing `k2bi` profile guard |
| Input | raw text seed (YouTube transcript / article / paragraph), passed verbatim | `:116-183`, `:1013-1028` |
| Output | `K2Bi-Vault/wiki/macro-themes/theme_<slug>.md` (path printed to stdout) | `:298-312`, `:1029` |
| Self-enforced rails | >=4 sub-themes, >=5 validated candidates, >=1 2nd/3rd-order survivor | `:520-533`, `:696-701` |

The narrative seed is **short by design**: the K2B conductor distills the dropped content into a
focused 1-3 sentence narrative statement before dispatch (that is the point of "K2B reads it").
We do NOT dump a raw 40-minute transcript into an argv element.

## The narrative preflight (read-only, ADDED on top of the existing Ship-1a k2bi preflight)

Ship 1a already runs: repo path exists, vault path exists, worker lock free, human-session lock
free, repo `git status --short`, command-allowlist-first ordering, no path leak in blockers. Those
all still run. The narrative lane ADDS these read-only checks, each blocking dispatch with a
specific named reason:

| # | Check | Pass condition | Blocker reason on fail | Evidence |
|---|---|---|---|---|
| P1 | macro-themes dir | `K2Bi-Vault/wiki/macro-themes/` exists, is a dir, is writable | `macro-themes output dir missing or not writable` | dir confirmed live |
| P2 | ticker registry | `K2Bi-Vault/wiki/tickers/canonical-registry.json` exists, size>0, parses as JSON, non-empty dict | `canonical ticker registry missing or empty -- run: python3 -m scripts.build_canonical_registry` | `:503-508` |
| P3 | LLM provider key | `KIMI_API_KEY` present (env or ~/.zshrc) when `K2B_LLM_PROVIDER=kimi` (default); else `MINIMAX_API_KEY` | `LLM API key not configured (KIMI_API_KEY)` | `minimax_common.py:75-91` |
| P4 | narrative non-empty | `len(narrative.strip())` within `[40, 4000]` chars | `narrative seed empty / too thin` or `too long -- distill first` | `:1013-1028` |

Presence-only on the key (P3): we cannot validate a key read-only without spending a call. Open
question Q5 below asks whether a cheap liveness ping is worth it before a parked dispatch.

## Clean-tree decision

The narrative pipeline writes to the **K2Bi vault**, NOT the K2Bi repo (confirmed: no repo writes
in the module; `promote_to_watchlist` symbol-locks are post-narrative). The vault is Syncthing-
synced and is dirty by design most of the time. Therefore:

- **No vault-cleanliness gate.** Gating on a dirty vault would block almost always and is wrong.
- The existing Ship-1a **repo** `git status` check is kept as-is for consistency (it is cheap and
  the narrative command genuinely does not touch the repo, so it will essentially always pass).
- Open question Q1 asks Codex whether the repo gate should be dropped for this lane entirely.

## New command_key + arg-injection safety

- New allowlist entry `k2bi-narrative` with a **fixed** command template
  (`python3 -m scripts.lib.invest_narrative_pipeline --narrative`) plus **exactly one** operator-
  payload slot (the distilled narrative text).
- The payload is passed as a **single argv element** via the worker's subprocess list form, never
  shell-interpolated and never split into multiple args. A payload containing shell metacharacters
  or extra `--flags` cannot inject commands or arguments.
- Workspace trust boundary (Ship 1a cond 10) applies unchanged: same `-m scripts.lib.X` shape, so
  the cwd-module-swap protection (cwd == trusted workspace, workspace from operator config only)
  is reused verbatim.

## Flight lock

- One narrative flight per `entity_key` at a time. `entity_key` = lowercase-trim-slug of the
  trend/topic (Ship-1b spec Section 4 lock-key rule).
- Parked states (`waiting_for_kimi_output`, `needs_human`) participate in the lock so a second
  cycle for the same topic cannot interleave, overwrite, or double-dispatch (`assignee_lock_held`
  extension, spec Section 4).
- Concurrent narrative cycles for different entities are allowed; same entity is serialized.

## Output handling + citation honesty

- Post-run: confirm a `theme_<slug>.md` path was emitted to stdout; read its `candidate-count`
  frontmatter; report path + count + top candidates to Keith.
- The pipeline self-enforces the >=5 / 2nd-3rd-order rails and raises on failure; a raise leaves
  the task `failed` with the error surfaced (no auto-retry), per spec Section 4 failure modes.
- **Citation honesty (spec line 84, Codex round-2 HIGH):** the theme file uses **K2Bi's own**
  citation generation. The orchestrator does NOT claim cite-ok governance over those citations and
  must not present them as K2B-validated. Chat 2 is honest about this; only Chat 1/Chat 3 deliver
  K2B-side link-repaired docs.

## Binary gate (made concrete from spec line 69)

PASS only if all hold:

1. A trend input (including a YouTube link) -> K2B distills -> dispatches -> `theme_<slug>.md`
   exists with `candidate-count >= 5` and at least one 2nd/3rd-order beneficiary.
2. The narrative preflight BLOCKS dispatch with the specific named reason (P1-P4) when each
   prerequisite is missing -- tested independently per check -- instead of failing mid-run.
3. One narrative flight per entity: a concurrent second dispatch for the same entity_key is
   refused, not run.
4. Arg-injection safe: a narrative payload containing shell metacharacters or extra `--flags`
   cannot inject args/commands (passed as a single argv element) -- proven by test.
5. A dirty K2Bi **vault** does NOT block dispatch (Syncthing churn is normal); only the read-only
   resource checks + the existing repo preflight gate.
6. Honest scope: the theme file's citations are reported as K2Bi's own, with no cite-ok claim.

FAIL if any condition does not hold or any required test (preflight-blocks-per-missing-prereq,
arg-injection-safe, one-flight-lock, dirty-vault-does-NOT-block, registry-empty-blocks) is absent
or red. Half-passes do not count.

## Build split (matches Increment 1)

- **Kimi (plumbing, `.kimi/job.md`):** the `k2bi-narrative` command_key with one parameterized
  payload slot; the narrative preflight function (P1-P4) + named blocker reasons; wire the
  entity_key lock for the narrative lane incl. parked-state participation; output-confirm
  (stdout theme path + candidate-count) -> done/failed; the test suite for the binary gate above.
- **Architect (conductor, into `k2b-orchestrator/SKILL.md`):** the Chat-2 runtime procedure --
  accept a dropped YouTube link / article / paragraph, fetch the transcript (prefetch hook exists)
  or read the article, distill into a focused narrative statement, optional "go deeper" pre-deepen
  via the booster, create the flight, dispatch, present the resulting theme file + candidate list.

## Open questions for Codex Checkpoint-1

1. Keep or drop the Ship-1a **repo** `git status` gate for a vault-writing command? (The repo is
   not touched; the mutation target is the vault.)
2. Long-seed handling: is distill-to-short-statement sufficient, or do we need a `--narrative-file`
   input mode in the K2Bi pipeline for long seeds (small K2Bi PR)?
3. Lock granularity: one flight per entity vs one global narrative lane for v1 simplicity?
4. Post-run verification: should the orchestrator parse the theme file's `candidate-count`
   frontmatter, or trust the pipeline's internal >=5 rail + exit code?
5. P3 key check: presence-only acceptable, or worth a cheap liveness ping to catch a dead key
   before a parked dispatch?
6. Anything still over-scoped for a first increment that should be cut further.

---

## Checkpoint-1 disposition (2026-05-31) -- BUILD-READY RESOLUTION (supersedes the open questions above)

Reviewer: Kimi-backed adversarial pass (`scripts/review.sh plan`); Codex itself was skipped due
to a tooling bug (codex-companion.mjs dropped plan-scope `--path`, so plan reviews currently route
to the Kimi reviewer -- valid gate, but the Codex plan path needs a fix; logged separately).
Verdict: NEEDS-ATTENTION / NO-SHIP, 12 findings. Each verified against K2Bi source before
acceptance. This section is now the authoritative design.

### Verified-and-resolved

- **F2 [CRITICAL] arg parsing (ACCEPT, fix corrected).** Real: a narrative value starting with `-`
  breaks Python `argparse`. The reviewer's `--` separator is WRONG for a value-bearing flag. Fix:
  dispatch as **`--narrative=<payload>`** (the `=` form binds the value even with a leading dash;
  confirmed `--narrative` is a standard value optional, `invest_narrative_pipeline.py:1003`). Belt-
  and-suspenders: preflight rejects a seed starting with `-`. Framed as **robustness** (operator
  text), not an adversary threat. Test: a seed `"-AI capex --help"` runs as literal narrative text.
- **F4 [HIGH] clean-tree (ACCEPT, resolves Q1).** DROP the Ship-1a **repo** `git status` gate for
  the `k2bi-narrative` lane. The mutation target is the vault; repo cleanliness is irrelevant and
  would false-block after a prior manual session. Test: a dirty repo does NOT block narrative.
- **F3 [HIGH] module importability (ACCEPT, add P0).** Add **P0**: `python3 -c 'import
  scripts.lib.invest_narrative_pipeline'` in the trusted workspace; blocker `narrative pipeline
  module not importable -- check K2Bi deploy state`. Read-only; catches stale/wrong-branch deploy.
- **F8 [MEDIUM] post-run verify (ACCEPT, resolves Q4).** The orchestrator MUST parse the written
  theme file's frontmatter and confirm `candidate-count >= 5`; on parse-fail or short count ->
  task `failed`, reason `theme file malformed or under-count`. Exit code alone is not trusted.
  This becomes binary-gate condition 1b.
- **F9 [MEDIUM] long-seed (ACCEPT, resolves Q2).** Lower **P4 max from 4000 to 500 chars** to
  enforce distillation. The conductor's distill-to-1-3-sentences step is documented as
  load-bearing in SKILL.md, not advisory. No K2Bi `--narrative-file` PR needed for v1.
- **F12 [MEDIUM] gate-5 untestable (ACCEPT, rephrase).** Rewrite binary-gate condition 5:
  "the narrative preflight does NOT check vault git status or Syncthing sync state." Concrete test:
  create `.syncthing-*` / `*.sync-conflict-*` files + uncommitted vault changes, verify dispatch
  still proceeds.
- **F1 [CRITICAL] provider liveness (ACCEPT-LIGHT, resolves Q5).** Add **P5**: a cheap best-effort
  reachability check (provider base URL or 1-token ping) with blocker `LLM provider unreachable`.
  Marked LIGHT not blocking-critical: narrative runs **synchronously** (not a parked Kimi-paste
  flow), so a dead key fails fast via the per-call socket timeout and releases the lock -- the
  "wedged task" risk the reviewer feared does not apply. P5 just upgrades a mid-run failure to a
  clean pre-dispatch message.
- **F6 [HIGH] timeout (ACCEPT-REFINED).** Per-call socket timeouts already exist
  (`minimax_common.py:142-312`); Ship-1a heartbeat->zombie reclaim covers a true hang. Refinement:
  confirm the worker **keeps heartbeating during** the multi-call narrative run so a legitimate
  long run is not falsely reclaimed at the 5-min stale threshold. No new global timeout invented.
- **F7 [MEDIUM] registry schema (PARTIAL).** Strengthen **P2**: not just non-empty JSON dict --
  also assert one known ticker key (e.g. `AAPL`) has a `name` field (cheap schema sanity).
  REJECT the freshness/mtime gate: a stale registry only means newer tickers are missing, which
  the pipeline's validators skip gracefully -- not a dispatch blocker for v1.
- **F10 [MEDIUM] cross-increment lock (ACCEPT, resolves Q3).** Keep one-flight-per-`entity_key`.
  Document the cross-increment semantics: a parked Chat-1 booster flight on the same topic WILL
  block a Chat-2 narrative on that topic, **intentionally** ("finish or cancel the deep-research
  flight before mapping the same topic"). Blocker message names the holding flight + how to cancel.
  Test: booster-parked-on-topic-X blocks narrative-on-topic-X with a clear message.

### Pushed back (reviewer assumption wrong / over-scoped for v1)

- **F5 [HIGH] slug overwrite "data loss" -- REJECTED.** `invest_narrative_pipeline.py:64-68`
  auto-versions (`theme_foo.md` -> `theme_foo_2.md`); it never overwrites. No data loss. Residual:
  re-dispatch creates `_2/_3` clutter -- documented, not gated. No P6 collision-block needed.
- **F11 [MEDIUM] disk-space / Syncthing-health preflight -- REJECTED for v1.** A MacBook
  on-demand tool writing one small markdown file. Disk-full is vanishingly rare and surfaces a
  clear OS error; Syncthing-health probing is scope creep. Parked as a post-v1 note, not built.

### Resulting preflight (final): P0, P1, P2(+schema), P3, P4(max 500), P5

(P4 is length-only -- the `reject leading '-'` belt rule was DROPPED per amendment A2 below; the `--narrative=<payload>` `=` form already makes a leading-dash seed safe, so a reject rule would contradict the A2 literal-narrative test. Do not reintroduce it.)

Plus the kept Ship-1a checks MINUS the repo `git status` gate (dropped per F4). Dispatch uses the
`--narrative=<payload>` `=` form. Post-run frontmatter verification is a hard gate (F8). Binary
gate condition 5 rephrased (F12). Everything else in this plan stands.

### Re-review

K2B discipline requires plan review AND pre-commit review. This plan got TWO independent plan
passes: the Kimi pass above, then a real Codex pass (via the codex-companion `task` subcommand --
`adversarial-review` cannot scope to a plan file, so `review.sh plan` routes to Kimi; the `task`
path is how Codex reviews a single document, and is the fix for the `review.sh plan` gap). The
pre-commit pass on Kimi's actual code remains the third, mandated gate.

## Codex pass (2026-05-31) -- final amendments (build-ready after A1+A2)

Codex verdict: "build-ready after adding the vault-root alignment check, and ideally tightening
the post-run 2nd/3rd verification or documenting that it is delegated." It independently verified,
by running live code, that the two REJECTED findings (F5 slug auto-version `:64`; F11 disk/
Syncthing over-scope) were sound calls, and that the F2 `=`-form fix is correct (`--narrative=--help`
binds as literal text; `--narrative --help` fails). Two amendments before build:

- **A1 [MUST] vault-root alignment (new).** The orchestrator side reads `K2BI_VAULT_PATH`
  (`scripts/lib/orchestrator_profiles.py:17`); the K2Bi pipeline writes via `resolve_vault_root()`
  which reads the **differently-named** `K2BI_VAULT_ROOT` (`invest_ship_strategy.py:230`). Aligned
  by default today, but a split could make preflight validate one vault while the pipeline writes
  another. Fix by construction: when the worker spawns the narrative pipeline it MUST set
  `K2BI_VAULT_ROOT` in the child env to the orchestrator's trusted K2Bi vault path (the same path
  P1/P2 validated). Belt: preflight asserts K2Bi `resolve_vault_root()` == the orchestrator's
  intended vault. Test: a mismatched `K2BI_VAULT_ROOT` is overridden/blocked, never silently honored.
- **A2 [MUST] remove the self-contradiction (Codex catch).** DROP the "preflight rejects a seed
  starting with `-`" belt rule -- it contradicts the F2 test that a seed like `"-AI capex --help"`
  must run as literal narrative text. The `--narrative=<payload>` `=` form (subprocess list,
  `shell=False`) already makes a leading-dash seed safe, so the reject rule is both unnecessary and
  contradictory. Keep only: dispatch as `--narrative=<payload>`; test that a leading-dash,
  `--flag`-containing seed runs as literal text. P4 keeps only the `[40, 500]` length bound.
- **A3 [DOCUMENT] 2nd/3rd-order rail is delegated.** The pipeline raises before writing if no
  2nd/3rd-order beneficiary survives (`invest_narrative_pipeline.py:696`), so a theme file existing
  on disk already implies the rail passed. The orchestrator post-run gate verifies
  `candidate-count >= 5` (frontmatter from `len(candidates)`, `:298`) and TRUSTS the 2nd/3rd-order
  rail to K2Bi's successful-exit path. (Optional cheap upgrade: also scan the candidate table for a
  `2nd`/`3rd` tag -- not required for v1.)
- **A4 [OPTIONAL residual] index.md shape.** A malformed/symlinked `wiki/macro-themes/index.md`
  can fail `_update_macro_themes_index()` AFTER the theme write (`:412`), leaving the task `failed`
  with a partial artifact. Accepted as a v1 runtime-failure residual (surfaced, not silent); an
  index-readable preflight check is a nice-to-have, not a blocker.

**Status: BUILD-READY.** Two adversarial plan passes (Kimi + Codex) complete; A1+A2 fold into the
Kimi build spec; A3 is documented; A4 parked. Proceed to the build split (Kimi plumbing +
architect conductor); the code gets its own mandated pre-commit pass.
