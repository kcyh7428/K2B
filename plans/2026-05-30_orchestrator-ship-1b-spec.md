# Orchestrator Ship 1b v2 -- "The 3 Chats" (Codex Checkpoint-1 plan review)

Status: DRAFT v2 for adversarial review. No code written. Supersedes v1 (which both Codex + Kimi reviewed NO-SHIP / NEEDS-ATTENTION on 2026-05-30; this rewrite is the response to that review PLUS a workflow correction from Keith).
Parent feature: `K2B-Vault/wiki/concepts/feature_k2b-orchestrator-v1.md`
Author: K2B (Scope B mechanism; Scope A workflow follows Keith's lived process).

## 0. What changed from v1 (why this is a rewrite, not an edit)

v1 designed a single "Kimi DR -> K2Bi /invest-narrative" chain that fed Kimi research INTO the narrative. Talking it through, Keith corrected two load-bearing facts about his ACTUAL workflow:

1. **Kimi DR is used to REPLACE K2Bi's deep-research step, not feed the narrative.** In the trial run, K2Bi's own deep research came back too shallow; Kimi DR is the replacement tool. So Kimi is a reusable "deep-research booster," not a fixed pre-narrative stage.
2. **The real funnel has three entry points**, and Keith wants all three. v1 only modeled the middle.

Both adversarial reviews (v1) independently said: cut over-engineering, stop claiming citation-safety we can't deliver, design the locking/failure modes instead of punting, and ship a smaller honest slice. This v2 does that.

## 1. The product: 3 chats over one reusable funnel

Keith never types a command or touches a terminal. He talks to K2B; K2B works the orchestrator (Ship 1a rails), K2Bi, and the manual Kimi pause behind the scenes.

```
LEVEL 0 / Chat 1 -- DISCOVER (no trend yet)        [pure K2B]
  "read this week's news on <domain>, what's forming + who benefits"
    -> fast automatic news scan -> 2-4 TRENDS (one-line why, verified links, rough names)
LEVEL 1 / Chat 2 -- MAP (you bring a trend)        [drives K2Bi]
  drop a YouTube link / article / paragraph -> K2Bi NARRATIVE -> candidate ticker LIST
LEVEL 2 / Chat 3 -- DEEP-DIVE (you have a ticker)  [pure K2B]
  "deep research on <ticker>" -> Kimi booster -> clean research -> your thesis
```

**The Kimi "deep-research booster" is one reusable tool** that plugs in at any level when K2B's quick answer is too shallow: trigger phrase is **"go deeper."** Booster = (build a Kimi DR prompt seeded by what K2B already found) -> (Keith runs kimi.com, pastes back) -> (K2B validates AND REPAIRS every source link, honestly flags the unverifiable) -> clean research doc.

**The two human gates (only places Keith acts):** running Kimi.com + paste-back; and picking which trend/ticker advances. Everything else K2B does silently.

## 2. Relationship to Ship 1a (reuse, do NOT rebuild)

Ship 1a (shipped `a439d5d`) is the foundation. Ship 1b builds on it; the only 1a-code changes are ADDITIVE.

| 1a component | Ship 1b uses it for | Change to 1a |
|---|---|---|
| SQLite task board (states/flights) | track the multi-step flow + the Kimi pause | ADD 2 parked states |
| dispatcher + worker harness (flock, heartbeat, reclaim) | run the scan / prompt-build / link-repair / narrative workers | none |
| allowlisted K2Bi dispatch + clean-tree preflight + workspace trust | Chat 2 narrative dispatch (the LRCX smoke proved this exact path) | ADD 1 command_key + a narrative preflight |
| `/orchestrator` skill | conversational triggers | EXTEND with `return` + the 3 chat intents |

No rebuild. The fencing-token deferred from 1a stays deferred and orthogonal: the new pauses have NO worker running (parked), so they never reach the running/zombie reclaim race the token protects; the short-lived workers register their PID on line 2 as in 1a.

## 3. Ship increments (small, each with a binary gate)

### Increment 1 = Chat 1 + the Kimi booster (FIRST; pure K2B, lowest risk)
The booster is built here because Chat 1 needs it and Chat 3 reuses it verbatim.

Named bug: *"Keith has no trend and no idea where the tickers are; and when he does deep research, he hand-checks and hand-repairs every Kimi source link."*

Flow: `"read the news on <domain>"` -> automatic news scan (web search) -> 2-4 trends w/ verified links + rough names -> Keith: `"take trend N deeper"` -> booster builds a Kimi prompt seeded by the scan -> task parks `waiting_for_kimi_output` (NO worker running) -> Keith runs Kimi, pastes back via `/orchestrator return <id> <paste-or-path>` -> link-validate-and-REPAIR worker -> clean, link-checked trend-research doc in `raw/orchestrator-results/`. STOPS there; Keith feeds the narrative himself.

Binary gate (all must hold):
1. A domain query returns 2-4 trends, each with >=2 source URLs that pass an HTTP reachability check (broken ones are dropped or repaired, never shown as good).
2. `"take trend N deeper"` produces a Kimi DR prompt that visibly references the scan's findings for that trend (not a generic template).
3. Task parks in `waiting_for_kimi_output` with no running worker; `/portfolio active` shows it as `waiting on your Kimi run`.
4. **Return acceptance gate:** a paste/file is stored with byte count + SHA-256 ONLY if it meets minimum structure (>= N extracted URLs/claims, not truncated mid-token); too-thin or truncated returns are rejected with a clear message; a second return for the same task is rejected (idempotent) unless Keith explicitly re-opens.
5. The link worker **repairs**: for each broken/missing URL it web-searches using the surrounding claim text for a working replacement; if none is found it labels the claim `unverified` and never invents a link. Output lists: verified / repaired (old->new) / unverified counts.
6. Read-only on K2Bi (Chat 1 writes nothing into K2Bi).

### Increment 2 = Chat 2 (adds the K2Bi narrative dispatch)
Named bug: *"Keith brings a trend (often a YouTube video) but can't get it mapped to non-obvious tickers without manual K2Bi work."*

Flow: drop a YouTube link / article / paragraph -> K2B reads it (transcript prefetch already exists) -> dispatches the K2Bi narrative via a NEW allowlisted command_key, behind a **narrative-specific preflight** (clean tree + ticker registry present + macro-themes dir writable + provider creds) -> `theme_<slug>.md` with the candidate ticker list. Honest scope: the narrative uses K2Bi's OWN citation generation; we do NOT claim cite-ok governance here (that is a separate later K2Bi PR if Keith wants it). The booster's `"go deeper"` can deepen the trend BEFORE the narrative.

Binary gate: a trend input (incl. a YouTube link) yields a theme file with >= the K2Bi-defined candidate count; the narrative preflight blocks dispatch (with a specific reason) when any prerequisite is missing, instead of failing mid-run; one narrative flight at a time (see Section 4).

### Increment 3 = Chat 3 (ticker deep-dive; mostly reuses Increment-1 booster)
Named bug: *"K2Bi deep research on a chosen ticker is too shallow."*

Flow: `"deep research on <ticker>"` -> K2B READS K2Bi's existing context on the ticker (watchlist note, narrative reasoning, screen scores -- read-only, NOT running K2Bi deep research) -> booster (same as Increment 1) -> clean ticker-research doc -> Keith feeds his thesis. **Explicitly replaces** K2Bi's deep-research step with Kimi DR.

Binary gate: `"deep research on LRCX"` reads the existing LRCX context, builds a Kimi prompt referencing it, parks, accepts the return, repairs links, and produces a clean LRCX research doc -- with zero K2Bi writes.

## 4. Designed (not punted) -- the things the v1 review flagged

- **Concurrency / locking.** ONE active narrative-or-Kimi flight per entity/topic at a time for v1. The parked states (`needs_human`, `waiting_for_kimi_output`) DO participate in a narrative-flight lock so a second cycle cannot interleave, overwrite a prompt brief, or double-dispatch K2Bi. `assignee_lock_held` is extended to include these parked states for the narrative lane. Concurrent cycles are explicitly OUT for v1.
- **New parked states + TTL.** `waiting_for_kimi_output` and `needs_human` carry a TTL (e.g. auto-cancel after 14d / 7d respectively, with one reminder) so forgotten flights don't wedge the lock or clutter `/portfolio` forever.
- **Return idempotency + double-spawn guard.** Transition to the validator runs ONLY from `waiting_for_kimi_output` for that exact task-id; a second return is rejected, not silently re-spawned.
- **Failure modes.** Partial/truncated Kimi paste -> rejected at the return gate (Section 3 gate 4). All-citations-broken -> hard-stop with a clear "this Kimi output's sources don't hold up" message, do NOT proceed. Narrative dispatch fails mid-run -> the narrative preflight catches most; a mid-run failure leaves the task `failed` with the partial path, no auto-retry, surfaced on `/portfolio`.
- **Citation honesty.** Chat 1/Chat 3 deliver a K2B-side clean research doc (repaired links + unverified flags) -- we control those, so the claim is honest. Chat 2's theme file uses K2Bi's own citations; we do NOT claim those are validated. No over-claim anywhere.
- **portfolio.sh.** Add explicit `section_active` next-action labels for `needs_human` (`needs your input`) and `waiting_for_kimi_output` (`waiting on your Kimi run`) -- required UX, not a "tiny follow-on".
- **Web search provider.** Resolved: K2B has web search available (WebSearch / exa / brave / perplexity). The scan (Chat 1) and the link-repair search use it; MacBook on-demand only, with a per-run call cap.

## 5. Open questions for Codex

1. Is "one flight at a time per entity/topic" the right lock granularity, or should it be one global narrative lane for v1 simplicity?
2. Return acceptance gate (Section 3 gate 4): what is the right minimum-structure check that rejects a truncated paste without rejecting a legitimately short one?
3. Increment ordering: is Chat 1-first correct, or does Chat 3-first (pure booster, no news-scan dependency) de-risk faster since the booster is the shared core?
4. Narrative preflight (Increment 2): which K2Bi prerequisites are genuinely load-bearing vs nice-to-have, and can K2B check them read-only before dispatch?
5. Anything still over-scoped for a first increment that should be cut further.

## 6. Build sequencing

1. Codex Checkpoint-1 review of THIS spec. Revise.
2. Update the feature note's Ship 1b section to "the 3 chats" model + increment plan.
3. Build Increment 1 (Chat 1 + booster), gate, ship, bake.
4. Build Increment 2 (Chat 2 narrative dispatch + preflight), gate, ship.
5. Build Increment 3 (Chat 3 ticker), gate, ship.

## 7. v3 hardening -- Increment 1 made build-ready (resolves Codex round-2)

Codex round-2 ACCEPTED the architecture and asked that Increment 1's safety dials be turned from placeholders into concrete, testable values. This section does that and SUPERSEDES the Section-3 Increment-1 placeholders + the Section-5 open questions FOR INCREMENT 1 ONLY. Increments 2 and 3 remain sketches and get their own Checkpoint-1 review before they are built -- so Codex's "2 and 3 no-ship" is expected; they are not next.

### 7.1 Return acceptance gate (concrete)
A pasted/file Kimi return is stored (byte count + SHA-256) ONLY if ALL hold:
- size in [500 bytes, 2 MB];
- >= 3 distinct http(s) source URLs;
- >= 5 substantive content lines (bullet/numbered/sentence lines > 40 chars);
- not truncated: final non-empty line ends in `.`/`!`/`?`/`"`/`)` or a closed markdown row; reject if the tail holds a dangling `http...` with no trailing whitespace, or an unclosed `[`/`(`;
- idempotent: accepted ONLY when the task is `waiting_for_kimi_output` for that exact id; a second return is refused: `rejected: already returned (task <id>)`.
Each rejection emits its specific reason. Negative tests: empty / no-URLs / truncated-tail / duplicate-return / oversize.

### 7.2 One-flight lock + TTL (concrete)
- Lock key = canonical `entity_key` (already a column): Chat 2 topic -> lowercase-trim-slug; Chat 3 -> uppercased ticker.
- Rule: at most ONE non-terminal task per `entity_key`. Enforced at creation inside a `BEGIN IMMEDIATE` txn (same atomic pattern as 1a's claim): SELECT a non-terminal task with that entity_key; if found, refuse: `flight already active for <key> (task <id>)`.
- `assignee_lock_held` extended: parked states (`needs_human`, `waiting_for_kimi_output`) count as holding the lane for their entity_key.
- TTL sweep (in the existing dispatcher poll, beside reclaim): cancel `waiting_for_kimi_output` > 14d and `needs_human` > 7d with reason `ttl-expired`, releasing the lock; surface one reminder before expiry.
- Tests: duplicate trigger (same + alias/case-variant key), expired-parked sweep, concurrent return + sweep.

### 7.3 Link repair = SUPPORT, not just reachability (the honesty fix -- Codex round-2 HIGH)
Every source URL is bound to the CLAIM it backs. Per claim:
- original URL fetches AND content supports the claim (entity/number overlap + an LLM "does this page support this claim?" yes/no) -> `cite-ok`;
- original URL fetches but content does NOT support the claim -> `cite-suspect` (treated as unverified for trust);
- original URL broken -> web-search the claim text -> fetch top candidates -> a candidate whose content supports the SAME claim -> `repaired` (record old->new); if none supports it -> `unverified`. A merely-reachable but unsupportive link is NEVER substituted.
- Mixed-failure hard-stop: produce the clean doc ONLY if >= 60% of claims end `cite-ok` or `repaired`; below that, STOP: `sourcing too weak (X% supported) -- not producing a clean doc`, surfaced for Keith.
- The clean doc lists per-claim status, so "clean" means every claim is supported or explicitly flagged. Tests: all-broken (hard-stop), reachable-but-unsupportive (suspect, not repaired), broken-but-repairable (repaired old->new), broken-unrepairable (unverified).

### 7.4 Web-search contract (named Increment-1 dependency)
- Provider order: exa -> brave -> perplexity -> WebSearch; first available wins.
- Preflight: >= 1 provider reachable/credentialed, else Chat 1 fails clean: `news search unavailable -- cannot scan now` (never partial-as-authoritative).
- Per-call timeout 20s; per-run caps: scan <= 12 searches, link-repair <= 5 per claim; on cap/quota exhaustion, return what was gathered + note `search budget reached`.
- Tests use recorded fixtures (no live web in tests), so the gate is replayable.

### 7.5 portfolio.sh
Add `section_active` labels: `needs_human` -> `⚠ needs your input`; `waiting_for_kimi_output` -> `waiting on your Kimi run`. Required before Increment 1 ships.

### 7.6 Scope statement
ONLY Increment 1 is build-ready after this section. Increment 2 (Chat 2 narrative dispatch) is blocked on a full K2Bi-prerequisite enumeration (every required command, path, registry, credential, provider, writable output dir, clean-tree condition, expected artifact contract) turned into a read-only preflight with tests -- done at Increment-2's own Checkpoint-1, not here. Increment 3 reuses the hardened booster and gets a thin Checkpoint-1 before build.
