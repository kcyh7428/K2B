---
name: k2b-orchestrator
description: Dispatch and monitor K2Bi analyst tasks via the orchestrator board, run the Chat-1 "deep-research booster" (read the news on a domain, build a Kimi Deep Research prompt, pause for Keith's manual Kimi run, then validate-and-repair the returned source links), AND answer "where are we in the orchestrator" by rendering the Full Scope Tracker. Use when Keith says /orchestrator, "dispatch a k2bi task", "orchestrator board", "what's on the board", "show flights", "read the news on <domain>", "what's forming in <area>", "deep research on <topic-or-ticker>", "go deeper", "where are we in the orchestrator", "orchestrator status", "orchestrator scope", "how much does the orchestrator do", or pastes Kimi Deep Research output back for a waiting flight.
---

# K2B Orchestrator

Manage durable tasks that dispatch allowlisted analyst commands to the sibling K2Bi workspace. The orchestrator owns the control plane: task creation, preflight checks, worker spawn, heartbeats, result artifacts, and a board mirror.

## When to Trigger

Keith says any of:
- `/orchestrator`
- "dispatch a k2bi task"
- "orchestrator board"
- "what's on the board"
- "add an orchestrator task"
- "show flights"
- "list tasks"

**Chat-1 deep-research booster (Increment 1):**
- "read the news on `<domain>`" / "what's forming in `<area>`" / "what trends are happening in `<X>`"
- "take trend N deeper" / "go deeper on `<X>`" / "deep research on `<topic-or-ticker>`"
- Keith pastes Kimi Deep Research output back (for a flight waiting on his Kimi run)

**"Where are we" status (render the Full Scope Tracker):**
- "where are we (now) in the orchestrator" / "orchestrator status" / "orchestrator scope" / "orchestrator full picture"
- "how much does the orchestrator do / orchestrate now" / "what's built in the orchestrator"

## Where are we? -- render the Full Scope Tracker

When Keith asks any "where are we" / status / scope / how-much question above, **render the canonical Full Scope Tracker -- do NOT recompute or synthesize it from memory.**

1. Read `~/Projects/K2B-Vault/wiki/concepts/feature_k2b-orchestrator-v2.md` and locate the section whose heading **contains "Full scope tracker"** (match leniently on those three words -- do not depend on the exact arrow glyph or whitespace). **Tie-break if more than one matches:** ignore any heading marked `draft` / `old` / `deprecated`; among the rest, use the LAST one in the file (newest). That section is the single source of truth: the condensed Stage 0->15 status map with the ✅/🖐/🟡/⛔/🔒 legend.
2. **Fail loud, never fabricate.** If the file cannot be read, or no qualifying "Full scope tracker" heading is found (file renamed/moved, section removed, Syncthing lag, Mac Mini not yet synced), say plainly: "Full Scope Tracker source unavailable at `wiki/concepts/feature_k2b-orchestrator-v2.md` -- not rendering from memory." and STOP. Do NOT improvise a tracker; a hallucinated status map is worse than no answer.
3. On success, show that table verbatim, then the "**Where we are now**" one-liner from the same section.
4. ONLY after a successful render (step 3), you MAY append live flight state from `bash ~/Projects/K2B/scripts/k2b-orchestrator.sh list` (what's actually on the board right now) as a supplement -- the tracker table is the headline answer. If step 2 stopped, do NOT run this; emit nothing but the unavailable message.
5. The tracker's Status column is updated on every orchestrator ship (`/ship` step 6). If it looks stale vs the feature note's Updates log, say so rather than guessing.

## What it does

Runs `~/Projects/K2B/scripts/k2b-orchestrator.sh <subcommand>` and shows the output. The orchestrator is the single writer for the task board and result artifacts.

## Subcommands

| Subcommand | Arguments | Purpose |
|---|---|---|
| `init` | — | Initialize DB and directories |
| `add` | `--profile`, `--command-key`, `--success`, `--permissions`, `--flight`, `--entity`, `--payload`, `--workspace`, `--status` | Create a task (returns task id). `--status` may be `ready` (default), `waiting_for_kimi_output`, or `needs_human` -- creating a flight directly parked. One-flight lock: refuses a 2nd non-terminal task with the same `--entity`. |
| `list` | `[--status S] [--json]` | List tasks |
| `flights` | — | List distinct flight ids |
| `show` | `<id> [--json]` | Show one task |
| `claim` | `<id>` | Mark a task running (manual override) |
| `complete` | `<id> [--result URL]` | Mark a task done. Refuses running/zombie and the parked *input* states (`waiting_for_kimi_output`/`needs_human` -- resolve those via `return` first) and already-terminal rows; ACCEPTS `returned` (the post-Kimi state), `ready`, `blocked`. |
| `block` | `<id> --reason R` | Block a task |
| `unblock` | `<id>` | Return a blocked task to ready |
| `cancel` | `<id>` | Cancel a task |
| `return` | `<id> (--text T \| --path P)` | For a `waiting_for_kimi_output` flight: run the acceptance gate on the pasted/file DR output (size 500B-2MB, >=3 URLs, >=5 substantive lines, and a task-bound completion sentinel `=== END OF <ENGINE> RESEARCH: <id> ===` -- ENGINE-AGNOSTIC, any engine token KIMI/CHATGPT/PERPLEXITY/..., and POSITION-TOLERANT so a trailing `## References`/footnote block may follow it), store raw + sha256 -> `returned`. For `blocked`/`needs_human`: re-ready it (no gate). Prefer `--path` for multi-line content (see the conductor). |
| `poll-once` | — | Run one dispatcher tick (reclaim zombies, spawn one ready task) |
| `render-board` | — | Write `board.md` from current DB state |

## Ship 1a scope

- Only the `k2bi` profile exists.
- Only two `command_key` values are allowlisted:
  - `k2bi-smoke-enrich-lrcx` (live K2Bi analyst command)
  - `test-echo-readonly` (test-only, harmless)
- The orchestrator never approves strategies, never commits, never touches the K2Bi engine directly. Approval / commit / journal-retro are post-v1.
- `--workspace` is rejected for `k2bi` (workspace is operator-config only).
- **Increment-1 note:** Chat-1 deep-research flights use a `k2b` profile + `k2b-kimi-research` command_key. They are created PARKED (`--status waiting_for_kimi_output`) and NEVER dispatched, so the K2Bi allowlist + preflight -- which only run when the dispatcher claims a `ready` task -- never apply to them. The `k2bi` allowlist above is unchanged; the `k2b` profile is K2Bi-free (no workspace, no engine).

## Chat 1 conductor -- news scan + deep-research booster (Increment 1)

This is the procedure K2B (you, the agent) follows at runtime. The reasoning -- the news scan, the prompt writing, the link repair -- is done by YOU with your own web-search/fetch tools. The orchestrator is only the durable pause/lock/return ledger. Flights are created **directly parked** (never `ready`), so the dispatcher never spawns a worker for them; you drive the lifecycle by hand. Design: [[feature_k2b-orchestrator-v1]] "the 3 chats" + `plans/2026-05-30_orchestrator-ship-1b-spec.md`.

### A. "read the news on `<domain>`" -> trends

1. Web-search recent news in the domain (your tools; ~12 searches max). If no provider is reachable, say "news search unavailable -- can't scan now" and stop; never fabricate.
2. Synthesize **2-4 emerging trends**. For each: a one-line *why it's forming*, 2-3 source links that you **HTTP-verify** (drop or replace any that don't resolve -- never show a broken link as good), and a few rough candidate names (starting points for Keith's judgment, NOT picks).
3. Present them. Offer: "want me to take any of these deeper with a deep-research run?"

### B. "go deeper on trend N" / "deep research on `<topic-or-ticker>`" -> build the DR prompt + park the flight

1. **Create the flight first** (so the task-id and the one-flight lock exist), parked:
   ```bash
   TID=$(bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
     --profile k2b --command-key k2b-kimi-research \
     --success "clean, link-verified deep research on <topic>" \
     --status waiting_for_kimi_output --entity "<canonical topic or TICKER>" | awk '{print $NF}')
   ```
   - `--entity`: canonicalize (lowercase-trim a topic; uppercase a ticker). If `add` errors `flight already active for ...`, a live flight for that topic exists -- tell Keith and offer to reuse or `cancel` it. Do NOT use the `k2bi` profile (that path resolves a K2Bi workspace); `k2b` keeps it agent-managed and K2Bi-free.
2. **Build the deep-research prompt**, seeded by your scan findings for that trend: a falsifiable thesis, the driver chain, 5-8 seed queries, 8-15 source anchors, a counter-thesis, and an avoid-list. The prompt **MUST include both of these instructions**:
   - **Citation mandate (fill the gate AND survive link-repair):** Tell the engine, verbatim:
     > Every factual claim must carry a real, working source URL. Either put the full `https://` URL inline right after the claim, OR use footnote markers `[^N]` **and** end with a `## References` section that lists each marker with its real URL (e.g. `[^31]: https://...`). Never emit a footnote marker without its URL. Any claim you cannot source, mark `(unverified)`. The `## References` section may sit just before OR just after the final completion line below.
     Without this, engines emit bare `[^N]` markers with zero URLs and the gate rejects `fewer than 3 source URLs` (verified live, JOBY probe 2026-05-31 -- Kimi's first pass had 0 URLs). Repairing fabricated URLs in step C is the conductor's core value, but the gate needs >=3 real `https://` links present to admit the doc at all.
   - **Completion sentinel (anti-truncation + paste-binding)** -- the engine's final *content* line (a trailing references block may follow it). Fill in the real TID (and, if you like, the engine's own name -- the gate accepts any):
     > Finish your output with this exact line: `=== END OF KIMI RESEARCH: <TID> ===`
     The gate is **engine-agnostic** (it matches `end of <engine> research` + the task id, case-insensitive, for KIMI / CHATGPT / PERPLEXITY / any token) and **position-tolerant** (it accepts a trailing `## References`/footnote block after the sentinel; only *substantive prose* after the sentinel is rejected). This is why all three engines pass with the same prompt -- you do NOT need to standardize the engine name or reorder references.
3. If the scan found too little signal to write a focused prompt, park as `needs_human` instead (`--status needs_human --payload '{"question":"<one clarifying question>"}'`) and ask Keith that one question rather than emitting a generic prompt.
4. Hand Keith the prompt (tap-to-copy) and tell him: run it on **his deep-research engine of choice (ChatGPT / Perplexity / Claude Deep Research / kimi.com -- the gate accepts any)**, paste the result back here. Note for citation-heavy work: the 3-engine JOBY bake-off (2026-05-31) found Kimi's URLs were the least reliable (often fabricated 404s) while ChatGPT/Perplexity grounded in primary sources -- prefer those when source fidelity matters.

### C. Keith pastes the Kimi output back -> gate + link repair -> clean research

1. **Ingest through the gate.** Multi-line research with URLs/quotes/backticks/`$` breaks an inline `--text` shell argument, so write Keith's paste to a vault file FIRST and return by path:
   ```bash
   # Write the paste to e.g. K2B-Vault/raw/orchestrator-inbox/<TID>-paste.md (use your Write tool), then:
   bash ~/Projects/K2B/scripts/k2b-orchestrator.sh return <TID> --path <file>
   ```
   (`--text` is for short single-line returns only.)
   - **The gate is engine-agnostic + position-tolerant** (no manual fix-up needed). It accepts the sentinel from ANY engine (`=== END OF KIMI/CHATGPT/PERPLEXITY/... RESEARCH: <TID> ===`) and tolerates a trailing `## References`/footnote block (`[^N]: https://...`) *after* the sentinel -- this is how Perplexity-style outputs (refs below the sentinel) pass. It rejects only *substantive prose* after the sentinel (reason `substantive content after the completion sentinel` = genuinely truncated/garbled -> re-paste the full output).
   - On **reject** the gate prints the exact reason; tell Keith which, with the recovery:
     - `missing completion sentinel` -> the paste is likely cut off (or from a different flight) -> re-paste the full output.
     - `fewer than 3 source URLs` / `fewer than 5 substantive lines` -> the Kimi output was thin -> re-run Kimi with a fuller prompt and paste again.
     - `size ... outside [500, 2000000]` -> empty/garbled or oversized -> check the paste.
   - On **pass** the flight is `returned`; the raw is stored at `K2B-Vault/raw/orchestrator-results/<TID>-kimi-raw.md` (byte count + sha256 recorded in the task payload).
2. **Validate AND repair the source links** (your tools; the honesty rule is non-negotiable):
   - For each source URL bound to a claim: fetch it. Resolves AND supports the claim -> `cite-ok`. Resolves but does NOT support it -> `cite-suspect` (treat as unverified). Broken -> web-search the claim text for a replacement that **actually supports the same claim**; found -> `repaired` (record old->new); none -> `unverified`. **Never** swap in a link that merely loads but doesn't back the claim.
   - **Hard-stop:** if fewer than 60% of claims end `cite-ok` or `repaired`, STOP and tell Keith "this Kimi output's sourcing is too weak (X% supported) -- not producing a clean doc."
3. Write the clean research doc (repaired links + a per-claim ledger: supported / repaired old->new / unverified) to a vault file.
4. **Finish the flight:**
   ```bash
   bash ~/Projects/K2B/scripts/k2b-orchestrator.sh complete <TID>
   ```
5. Present the clean research to Keith. **Increment 1 stops here** -- he takes it into his K2Bi narrative/thesis himself (the auto-handoff is a later increment).

### Monitoring

Parked flights show on `/portfolio active` ("waiting on your Kimi run" / "needs your input"). Forgotten flights auto-expire via the TTL sweep on `poll-once` (14 days for `waiting_for_kimi_output`, 7 for `needs_human`).

## Chat 2 conductor -- trend -> candidate-ticker theme file (AGENT-NATIVE, no Kimi)

This is the runtime procedure K2B (you, the agent) follows when Keith drops a trend and wants it mapped to non-obvious tickers. Chat 2 is **agent-native**, the same shape as Chat 1: YOU (Claude Code / Codex) do the candidate generation AND the citation validation/repair in-session -- there is **NO Kimi call and NO dispatched K2Bi command** anywhere in this path. (Why: K2Bi's `invest_narrative_pipeline` used Kimi to invent candidates + citation URLs; Kimi fabricates URLs and the pipeline dropped every dead-link candidate, so 2 live MVP runs failed for the same trend-independent cause. Kimi is a code worker, not a grounded-research generator. Fix = retire Kimi from this path. Design + reviews: [[feature_k2b-orchestrator-v1]] + `plans/2026-05-31_chat2-agent-native-spec.md`.)

The flight is created **PARKED** (`waiting_for_agent_theme`) so `poll-once` never dispatches it; the agent writes the theme directly and the `verify-theme` gate (>=5 supported candidates + >=1 2nd/3rd-order + citation-ledger >=60% supported) is the ONLY exit to `done`. Plumbing in `orchestrator_store.py` (parked state, `verify-theme`, `_verify_theme_gate`).

**Honest-scope rule (UPGRADED):** the agent fetches + repairs every citation live, so the theme's citations **ARE K2B-validated**. When you present the theme, say "citations K2B-validated (fetched + repaired)". (This replaces the old "K2Bi's own, not K2B-validated" caveat -- that was true only for the retired Kimi-dispatch path.)

### Triggers

- "map this trend / video / article to tickers", "what stocks does this touch", "what's the supply chain", "run a narrative on `<X>`"
- Keith pastes a YouTube link / article / paragraph and asks for the tickers behind it.

### Procedure

1. **Read the source.** YouTube link -> the transcript-prefetch hook already provides the transcript (do not re-fetch). Article URL -> fetch and read it. Pasted paragraph -> use as-is. If nothing readable resolves, say so and stop; never invent a trend.
2. **Distill to a focused seed (load-bearing).** Compress the source into a **1-3 sentence** falsifiable macro statement (40-500 chars). Do NOT dump a raw transcript. If too thin to distill a real thesis, ask Keith one clarifying question.
3. **(Optional) deepen first.** If Keith says "go deeper" before mapping, run the Chat-1 booster on the trend first, then distill from the cleaned research. Not required.
4. **Create the flight PARKED (fail-closed).** `entity_key` = the topic **lowercase-trimmed** (a STRING with spaces, e.g. `ai supply chain`), the SAME canonicalization Chat 1 uses, so a Chat-1 booster and a Chat-2 narrative on the same topic share the one-flight lock. The flight is a tracking + lock ledger only -- never dispatched (k2b profile, parked):
   ```bash
   # Write {"narrative": "<distilled seed>"} to a temp file with your Write tool, then:
   out=$(bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
     --profile k2b --command-key k2b-narrative-agent \
     --status waiting_for_agent_theme \
     --success "agent-native candidate-ticker theme for <topic>" \
     --entity "<topic lowercase-trimmed, spaces kept>" \
     --payload "$(cat /tmp/<tid>-payload.json)") \
     || { echo "add FAILED: $out"; exit 1; }
   TID=$(printf '%s\n' "$out" | awk '{print $NF}')
   test -n "$TID" || { echo "empty task id"; exit 1; }
   ```
   If `add` errored `flight already active for ...`, a live flight for that topic exists -- tell Keith, offer to reuse or `cancel` it.
5. **Generate candidates YOURSELF (in-session, no Kimi).** Decompose the seed into **>=4 sub-themes**. Produce **>=5 candidate tickers** with **>=1 non-obvious 2nd/3rd-order beneficiary** (the value is the non-obvious names, not the headline play). Validate every symbol against the K2Bi canonical registry (`K2Bi-Vault/wiki/tickers/canonical-registry.json`) -- drop hallucinated/unlisted symbols. For each candidate produce a reasoning chain, an `order` (1st/2nd/3rd), and the ARK 6-metric score (people_culture, rd_execution, moat, product_leadership, thesis_risk, valuation, each /10).
6. **Validate + repair every citation (the honesty rule -- non-negotiable).** For each candidate's load-bearing claim: **fetch** the source URL with your web tools. Resolves AND supports the claim -> `cite-ok`. Resolves but does NOT support it, or broken -> **web-search a real replacement that ACTUALLY backs the same claim**; found -> `repaired` (record old->new); none -> `unverified` (drop the candidate from the displayed set). NEVER use a link that merely loads but does not support the claim. **Hard-stop** if fewer than 60% of all candidate claims end `cite-ok` or `repaired` -- tell Keith the trend's sourcing is too weak, do not write a theme.
7. **Write the theme via the LOCKED HELPER (the lock must span the actual write -- a bash flock that releases before your Write-tool edits is theatre, Codex F5).** Compose the FULL theme markdown (frontmatter contract below + body) and Write it to a temp file (e.g. `/tmp/<tid>-theme.md`). Then call the helper, which holds ONE `fcntl.flock` across slug-derivation (`_2`/`_3` auto-version -- NEVER overwrites an existing theme), the atomic theme write, and the `index.md` row append:
   ```bash
   THEME=$(python3 ~/Projects/K2B/scripts/macro-theme-write.py /tmp/<tid>-theme.md "<distilled topic phrase>") \
     || { echo "theme-write rejected/failed (nothing published on exit 4 -- fix the theme and re-run)"; exit 1; }
   echo "wrote: $THEME"   # final path incl. any _2/_3 suffix -- use it for verify-theme
   ```
   The helper runs the SAME `_verify_theme_gate` on the temp content BEFORE it publishes (Codex r4): a theme that fails the gate is NEVER moved to `theme_<slug>.md` and NEVER appended to the index, so a bad theme can never appear as a K2Bi-promotable `candidates-pending-review` row. Exit 4 = gate-rejected, nothing written -- read the printed clause, fix the theme, re-run. The theme frontmatter MUST carry the existing contract (`type: macro-theme`, `date`, `origin: k2b-extract`, `narrative`, `sub-themes`, `candidate-count` = the number of **distinct supported candidates**, `candidate_ark_scores: {SYM: {...6 metrics...}}`, `status: candidates-pending-review`, `up: "[[index]]"`) PLUS the **`citation_ledger`** list, one row per candidate: `{symbol, order, claim, url (a real http(s) URL), status: cite-ok|repaired|unverified, support_note, checked_at (ISO-8601)}`. Distinct symbols only; `candidate-count` MUST equal the count of distinct `cite-ok|repaired` rows (the gate rejects duplicates / mismatch / non-http URL / non-ISO time). Body: the candidate table + sub-themes (match the existing macro-theme layout). K2Bi-Vault docs are directly writable by K2B (L-2026-05-31-001: K2Bi-owned artifact, staging not pipeline -- promotion stays Keith's gate).
8. **Verify + complete (the gate).** Run `bash ~/Projects/K2B/scripts/k2b-orchestrator.sh verify-theme "$TID" "$THEME"`. The gate first confirms `$THEME` is a durable vault artifact (under `wiki/macro-themes/`, `theme_*.md`, referenced in `index.md` -- Codex F2), then requires `candidate-count>=5`, >=5 DISTINCT `cite-ok|repaired` candidates (each with a real http(s) url + support_note + ISO checked_at), >=1 of those `order` 2nd/3rd, and ledger supported-ratio >=60%; on pass it flips the parked flight -> `done`. On reject it prints the specific failing clause and leaves the flight parked -- fix the theme (add candidates / repair more citations) and re-run `verify-theme`. Do NOT use `complete` (it refuses the parked state by design).
9. **Report.** Present the candidate tickers from the theme (flag the 2nd/3rd-order / non-obvious ones), and state the citations are **K2B-validated (fetched + repaired)**. Then **stop** -- Keith picks which ticker to promote (Stage 3 is a permanent human gate; do NOT write the watchlist).

### Monitoring

Chat-2 flights are parked `waiting_for_agent_theme` (k2b profile) -- they show on `/portfolio` as agent-owned and auto-expire via the TTL sweep (2 days) if abandoned. A failed `verify-theme` prints the exact gate clause that failed.

## Canonical add example

```bash
bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
  --profile k2bi \
  --command-key k2bi-smoke-enrich-lrcx \
  --success "LRCX Stage-2 enriched; result artifact written; no engine touch" \
  --permissions analyst-command
```

Parked Chat-1 deep-research flight (Increment 1) -- created directly in a parked state with an `--entity` for the one-flight lock (the lock dedups case-insensitively, so canonicalizing the entity is just for tidiness):

```bash
bash ~/Projects/K2B/scripts/k2b-orchestrator.sh add \
  --profile k2b \
  --command-key k2b-kimi-research \
  --success "clean, link-verified deep research on grid-power AI bottleneck" \
  --status waiting_for_kimi_output \
  --entity "grid power ai bottleneck"
```

## Rendering convention

After any board-changing command (`add`, `block`, `unblock`, `cancel`, `complete`, `poll-once`), show the board:

```bash
cat ~/Projects/K2B-Vault/System/orchestrator/board.md
```

## Usage logging

After completing the main task, log this skill invocation:

```bash
echo -e "$(date +%Y-%m-%d)\tk2b-orchestrator\t$(echo $RANDOM | md5sum | head -c 8)\torchestrator operation" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
