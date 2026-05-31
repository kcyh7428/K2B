# Chat-2 agent-native: retire Kimi from the narrative path -- Checkpoint-1 plan

Status: DRAFT for Codex Checkpoint-1 BEFORE implementation. Scope B (K2B orchestrator).
Directive: Keith, 2026-05-31. Companion to [[feature_k2b-orchestrator-v1]].

## Named bug (what this kills)

> Stage-2's >=5-candidate MVP cannot pass because K2Bi's `invest_narrative_pipeline` uses **Kimi**
> to generate candidate tickers + their citation URLs; Kimi fabricates URLs; the pipeline's citation
> validator then drops every candidate with a dead link. Run 1 (EDA): 15/18 dropped -> 1 candidate
> (under-count). Run 2 (supply-chain): so many dropped that 0 2nd/3rd-order survived -> pipeline
> raised. **Root cause is the model choice, not the trend** -- proven across 2 trends. Kimi is a
> code-writing worker, not a grounded equity-research generator.

## The change (mirror Chat-1: in-session agent does reasoning AND citations)

Chat-2 becomes **agent-native**, the same shape Chat-1 already uses. NO Kimi anywhere in the path.

1. **Distillation unchanged:** Claude compresses the dropped source to a 40-500 char macro seed.
2. **Agent generates candidates itself** (Claude Code / Codex in-session): >=4 sub-themes, >=5
   candidates, >=1 non-obvious 2nd/3rd-order beneficiary. Validate each ticker against the K2Bi
   canonical registry (`wiki/tickers/canonical-registry.json`) -- no hallucinated symbols.
3. **Per-candidate citation, Chat-1 step-C honesty rule:** fetch the URL -> supports the claim =
   `cite-ok`; broken -> web-search a real replacement that ACTUALLY backs the same claim =
   `repaired`; none = `unverified`/drop. NEVER swap in a link that merely loads. Hard-stop if
   < 60% of claims end cite-ok or repaired.
4. **Agent reproduces the ARK 6-metric score** per candidate (people_culture, rd_execution, moat,
   product_leadership, thesis_risk, valuation) -- see ARK decision below.
5. **Agent writes the theme directly** to `K2Bi-Vault/wiki/macro-themes/theme_<slug>.md` with the
   EXISTING frontmatter contract (incl. `candidate_ark_scores`, `sub-themes`, `candidate-count`,
   `narrative`, `status: candidates-pending-review`, `up`). K2Bi-Vault docs are directly writable
   by K2B (advisory-on-CODE; vault docs editable; ownership principle L-2026-05-31-001 -- this is a
   K2Bi-owned artifact, and a macro-theme is pre-promotion ideation, NOT a pipeline-promotion).
6. **Keep the >=5 candidate-count gate + the one-flight entity lock.** The gate now passes on real
   validated candidates, not luck of which Kimi URL resolved.

## ARK decision (the flagged open question) -- RESOLVED: Option A + agent reproduces ARK

Checked: `promote_to_watchlist` (`K2Bi invest_narrative_pipeline.py:803`) reads
`candidate_ark_scores` from the theme **if present, else `{}`**; `invest_screen.py:355` accepts
`ark_6_metric_initial_scores` as **"dict or null"**. So ARK is **consumed-if-present but NOT
required** -- promotion + screening degrade gracefully without it. Therefore ARK is NOT a Stage-3
blocker. **Option A** (agent writes theme directly, bypassing the Kimi pipeline) is chosen; the
agent REPRODUCES the ARK 6-metric scores itself (cheap; it already produces reasoning + citations)
so the frontmatter contract stays complete and promotion picks them up exactly as before. No K2Bi
PR, no Kimi, zero downstream degradation. (Option B -- keep the pipeline for scoring, change its
citation source via a K2Bi PR -- is rejected: heavier, touches K2Bi repo, unnecessary given the
agent can score.)

## Orchestrator flight lifecycle (agent-driven, like Chat-1) -- proposed design

Today Chat-2 is DISPATCHED: `add` (ready) -> `poll-once` -> worker runs the `k2bi-narrative`
command -> pipeline writes theme -> worker's `_parse_candidate_count` gate on the stdout-emitted
path. Under Option A there is no dispatched command, so:

- **Keep the flight** for the one-flight `entity_key` lock + tracking + the gate record. Create it
  with `add` (as today).
- **Retire the dispatched `k2bi-narrative` command path.** The agent does the work inline (the
  conductor), never `poll-once`-dispatches it. Remove `k2bi-narrative` from the worker-dispatch
  allowlist OR guard the dispatcher so it never spawns this command_key. (Decide in review: cleanest
  is to NOT register it as a dispatchable command and make the conductor purely agent-driven.)
- **Keep the >=5 gate, re-sourced to the agent-written theme path.** `orchestrator_worker.py`'s
  `_parse_candidate_count` (fail-closed YAML parser) is reused, but invoked as a **verification on
  the agent-written `theme_<slug>.md`** rather than on command stdout. Proposed: a thin
  `verify-theme <TID> <path>` (or `complete <TID> --verify-theme <path>`) orchestrator step the
  conductor calls after writing the theme; it runs `_parse_candidate_count` >= 5 (and optionally a
  2nd/3rd-order scan) before allowing `complete`. Fail-closed: malformed/under-count -> flight
  stays non-done.
- The agent self-checks prerequisites inline (registry present, macro-themes writable) -- the old
  dispatch preflight P0-P5 collapses; the Kimi provider-reachability check (P5) is DROPPED (no Kimi).

## Honest-scope upgrade

The agent now fetches + repairs citations live, so the theme's citations ARE K2B-validated. Drop
the "K2Bi's own citations, not K2B-validated" caveat for Chat-2; replace with "citations
K2B-validated (fetched + repaired)". (Chat-1 already does this.)

## Chat-1 wording (no gate code change)

The Chat-1 return gate is already engine-agnostic (KIMI/CHATGPT/PERPLEXITY/any). Just stop
privileging Kimi in the conductor + prompt text: call it "your deep-research engine (ChatGPT /
Perplexity / Claude Deep Research -- your choice)". Sentinel instruction unchanged; only the example
engine name flips. No `orchestrator_*.py` change for this.

## Files to touch (all K2B-side, directly editable)

- `.claude/skills/k2b-orchestrator/SKILL.md` -- rewrite the Chat-2 conductor to the agent-native
  shape (steps 1-6 above); update Chat-1 engine wording; update honest-scope line.
- `scripts/lib/orchestrator_profiles.py` -- retire the dispatched `k2bi-narrative` command + its
  Kimi-dispatch preflight branch (or repurpose to the agent-driven lifecycle); drop the Kimi
  provider-reachability check for this path.
- `scripts/lib/orchestrator_worker.py` -- keep `_parse_candidate_count` + the >=5 gate; re-source
  the theme path to the agent-written file (verify step) instead of command stdout.
- Tests: update the orchestrator narrative tests for the new lifecycle (the dispatched-command tests
  for k2bi-narrative are retired/repurposed; add agent-written-theme verify-gate tests).
- **K2Bi repo: NO direct edit.** (Option A needs none. Only Option B would, via PR -- rejected.)

## MVP binary re-test (must pass before Stage-2 is marked done)

Keith drops a real trend. End-to-end produces `theme_<slug>.md` with **>=5 candidate tickers, each
carrying a K2B-validated (fetched/repaired) citation, >=1 non-obvious 2nd/3rd-order name, and NO
Kimi call anywhere in the path**. PASS = that file exists with >=5 validated candidates + the gate
accepts it. FAIL = anything less, or any Kimi call in the path.

## Open questions for Codex Checkpoint-1

1. Flight lifecycle: cleanest way to retire the dispatched `k2bi-narrative` command while keeping
   the one-flight lock + the >=5 gate -- a new `verify-theme` step, or fold the gate into `complete`?
2. Does removing `k2bi-narrative` from the dispatch allowlist break any Ship-1a invariant or
   existing test (the allowlist + preflight tests)? What's the minimal safe change?
3. Slug / auto-version: the K2Bi pipeline auto-versioned `_2`/`_3` on collision. The agent writing
   directly must reproduce that (don't overwrite an existing theme for the same slug).
4. Citation-honesty enforcement: how to make the 60%-hard-stop + "supports the claim, not just
   loads" rule concrete + checkable in the conductor (it's agent judgment -- any guard?).
5. Anything over-scoped for the MVP re-test that should be cut.

## Checkpoint-1 disposition (Codex, 2026-05-31 -- NEEDS-ATTENTION, all 3 ACCEPTED)

Direction (Option A) confirmed; these harden the mechanism. Folded in:

1. **Parked agent-owned flight state (ACCEPT).** Chat-2 flights are created PARKED in a new
   `waiting_for_agent_theme` state (via `add --status waiting_for_agent_theme`), NOT `ready` --
   `poll_once()` dispatches every `ready` task globally and would then `blocked` it once the
   command is retired (`orchestrator_store.py:796/823`). A new **`verify-theme <TID> <path>`** is
   the ONLY transition out (parked -> done, gated). Do NOT route through `complete` -- it refuses
   parked states by design (`orchestrator_store.py:1000`) to prevent gate-bypass. This mirrors
   Chat-1's `waiting_for_kimi_output` parked lifecycle.
2. **Macro-theme write lock (ACCEPT).** The dispatched path gave assignee+worker locks for free;
   the direct-write path keeps only the per-`entity_key` lock, but K2Bi slugging is check-then-write
   (`theme_<slug>`/`_2`/`_3`) + index-update-after (`invest_narrative_pipeline.py:712/731`), so two
   DIFFERENT entity_keys sharing the same first-6-word slug can race and overwrite / lose an index
   row. Fix: the conductor holds a **flock** (e.g. `/tmp/k2b-orch-macro-themes.lock`) around
   slug-derivation + theme-write + rejected-sidecar + `wiki/macro-themes/index.md` update. Keeps
   Option A (no K2Bi PR); just restores the serialization. (This is the only place Option B would
   have helped; the flock closes it without the PR.)
3. **Citation ledger + honest verify (ACCEPT).** `_parse_candidate_count` only proves a well-formed
   int count -- it does NOT prove citations are real/supporting or that the 60% rule held. So the
   agent-written theme MUST carry a **citation ledger**: per candidate `{claim, url, status:
   cite-ok|repaired, support_note, checked_at}`. `verify-theme` FAILS unless (a) count >= 5, (b)
   >= 1 candidate is `order: 2nd|3rd`, (c) EVERY displayed candidate's ledger row is `cite-ok` or
   `repaired`, and (d) the ledger's supported ratio >= 60%. This makes the honesty rule
   machine-checkable, not agent self-report.

### Revised gate (verify-theme) = the new Stage-2 acceptance

count >= 5 AND >= 1 second/third-order AND every shown candidate cite-ok|repaired AND ledger
supported-ratio >= 60%. Fail-closed on any miss; flight stays `waiting_for_agent_theme`.

### Revised build split

- **Kimi (plumbing, code-work only -- consistent with "keep Kimi for code"):** add the
  `waiting_for_agent_theme` parked state to the status enum + `add --status` + lock participation
  (`orchestrator_store.py`); retire the dispatched `k2bi-narrative` command path
  (`orchestrator_profiles.py`); add the `verify-theme <TID> <path>` subcommand running the revised
  gate (count/2nd-3rd/ledger) reusing/extending `_parse_candidate_count` (`orchestrator_worker.py`
  + CLI); the macro-theme flock helper; full tests (parked-not-dispatchable, verify-theme
  pass/fail per gate clause, ledger-honesty, slug-collision-under-lock).
- **Architect (me, judgment):** the Chat-2 conductor in `SKILL.md` (agent-native candidate +
  sub-theme generation, per-candidate fetch/repair with the honesty rule + ledger emission + ARK
  scoring + locked theme-write), Chat-1 engine wording, honest-scope line.

## MVP binary re-test (updated with the ledger)

Keith drops a real trend. End-to-end produces `theme_<slug>.md` with **>=5 candidates, each with a
ledger row `cite-ok|repaired` (K2B fetched + verified-supporting), >=1 non-obvious 2nd/3rd-order,
ledger supported-ratio >=60%, and NO Kimi call anywhere**. `verify-theme` accepts it (parked ->
done). PASS = that file + a passing verify-theme. FAIL = anything less, or any Kimi call.

## DON'T (from the directive)

- Don't add more trends/videos to "get past" the gate -- proven not the blocker.
- Don't edit K2Bi repo code directly (PR only, and only under the rejected Option B).
- Don't claim "K2B-validated" unless the agent actually fetched + checked the links.
