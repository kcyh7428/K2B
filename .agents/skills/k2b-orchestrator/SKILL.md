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

1. Read `~/Projects/K2B-Vault/wiki/concepts/feature_k2b-orchestrator.md` and locate the section whose heading **contains "Full scope tracker"** (match leniently on those three words -- do not depend on the exact arrow glyph or whitespace). **Tie-break if more than one matches:** ignore any heading marked `draft` / `old` / `deprecated`; among the rest, use the LAST one in the file (newest). That section is the single source of truth: the condensed Stage 0->15 status map with the ✅/🖐/🟡/⛔/🔒 legend.
2. **Fail loud, never fabricate.** If the file cannot be read, or no qualifying "Full scope tracker" heading is found (file renamed/moved, section removed, Syncthing lag, Mac Mini not yet synced), say plainly: "Full Scope Tracker source unavailable at `wiki/concepts/feature_k2b-orchestrator.md` -- not rendering from memory." and STOP. Do NOT improvise a tracker; a hallucinated status map is worse than no answer.
3. On success, show that table verbatim, then the "**Where we are now**" one-liner from the same section.
4. ONLY after a successful render (step 3), you MAY append live flight state from `bash ~/Projects/K2B/scripts/k2b-orchestrator.sh list` (what's actually on the board right now) as a supplement -- the tracker table is the headline answer. If step 2 stopped, do NOT run this; emit nothing but the unavailable message.
5. The tracker's Status column is updated on every orchestrator ship (`/ship` step 6). If it looks stale vs the feature note's Updates log, say so rather than guessing.

## What it does

Runs `~/Projects/K2B/scripts/k2b-orchestrator.sh <subcommand>` and shows the output. The orchestrator is the single writer for the task board and result artifacts.

## K2Bi Path Contract

Before any A1/A2/A3 ready worker dispatch, or any direct K2Bi helper/adapter call, validate the target K2Bi roots explicitly. Do not infer them from the current K2B repo or from `~/Projects/K2B-Vault`.

```bash
K2BI_REPO="${K2B_ORCH_K2BI_WORKSPACE:-$HOME/Projects/K2Bi}"
K2BI_VAULT="${K2B_ORCH_K2BI_VAULT:-$HOME/Projects/K2Bi-Vault}"

test -d "$K2BI_REPO/.git" || { echo "missing K2Bi git repo: $K2BI_REPO" >&2; exit 2; }
test -d "$K2BI_REPO/scripts/lib" || { echo "missing K2Bi scripts/lib: $K2BI_REPO" >&2; exit 2; }

(cd "$K2BI_REPO" && python3 - <<'PY'
import importlib.util, sys
sys.path.insert(0, ".")
mods = [
    "scripts.lib.orchestrator_store",
    "scripts.lib.invest_orchestrator_adapters",
    "scripts.lib.invest_screen",
]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    print("missing K2Bi helper modules: " + ", ".join(missing), file=sys.stderr)
    sys.exit(2)
PY
)

# Required before A2/A3 strategy, backtest, or ship paths:
test -d "$K2BI_VAULT/wiki/strategies" || { echo "missing K2Bi vault strategies dir: $K2BI_VAULT" >&2; exit 2; }
```

Path meanings are intentionally split. In A2 `write_complete_strategy_spec(..., repo_root=<K2Bi VAULT>)`, the adapter kwarg is unfortunately named `repo_root` but means the K2Bi vault artifact root where `wiki/strategies/` lives. In A3 `write_complete_strategy_spec(..., repo_root=<K2Bi REPO>)`, `repo_root` means the K2Bi git checkout. Never substitute the K2B repo or K2B vault for either root.

Before writing any worker payload, bind stage-specific variable names and assert the path type at the dispatch site:

```bash
# A2 strategy spec/backtest payloads:
a2_vault_root="$K2BI_VAULT"
test -d "$a2_vault_root/wiki/strategies" || { echo "A2 repo_root must be K2Bi vault: $a2_vault_root" >&2; exit 2; }

# A3 author-to-repo/run_full_ship payloads:
a3_repo_root="$K2BI_REPO"
test -d "$a3_repo_root/.git" || { echo "A3 repo_root must be K2Bi git repo: $a3_repo_root" >&2; exit 2; }
test -d "$a3_repo_root/scripts/lib" || { echo "A3 repo_root missing scripts/lib: $a3_repo_root" >&2; exit 2; }
```

Do not carry a generic shell variable named `repo_root` between A2 and A3. Use `a2_vault_root` or `a3_repo_root` until the final JSON payload field is written.

## Subcommands

| Subcommand | Arguments | Purpose |
|---|---|---|
| `init` | - | Initialize DB and directories |
| `add` | `--profile`, `--command-key`, `--success`, `--permissions`, `--flight`, `--entity`, `--payload`, `--workspace`, `--status` | Create a task (returns task id). `--status` may be `ready` (default), `waiting_for_kimi_output`, or `needs_human` -- creating a flight directly parked. One-flight lock: refuses a 2nd non-terminal task with the same `--entity`. |
| `list` | `[--status S] [--json]` | List tasks |
| `flights` | - | List distinct flight ids |
| `show` | `<id> [--json]` | Show one task |
| `claim` | `<id>` | Mark a task running (manual override) |
| `complete` | `<id> [--result URL]` | Mark a task done. Refuses running/zombie and the parked *input* states (`waiting_for_kimi_output`/`needs_human` -- resolve those via `return` first) and already-terminal rows; ACCEPTS `returned` (the post-Kimi state), `ready`, `blocked`. |
| `block` | `<id> --reason R` | Block a task |
| `unblock` | `<id>` | Return a blocked task to ready |
| `cancel` | `<id>` | Cancel a task |
| `return` | `<id> (--text T \| --path P)` | For a `waiting_for_kimi_output` flight: run the acceptance gate on the pasted/file DR output (size 500B-2MB, >=3 URLs, >=5 substantive lines, and a task-bound completion sentinel `=== END OF <ENGINE> RESEARCH: <id> ===` -- ENGINE-AGNOSTIC, any engine token KIMI/CHATGPT/PERPLEXITY/..., and POSITION-TOLERANT so a trailing `## References`/footnote block may follow it), store raw + sha256 -> `returned`. For `blocked`/`needs_human`: re-ready it (no gate). Prefer `--path` for multi-line content (see the conductor). |
| `poll-once` | - | Run one dispatcher tick (reclaim zombies, spawn one ready task) |
| `render-board` | - | Write `board.md` from current DB state |
| `record-deploy-preview` | `<id> <manifest.json>` | Record the A5 deploy dry-run/manifest preview |
| `authorize-deploy` | `<id>` | Mint the A5 `APPROVE_DEPLOY` token after preview review |
| `defer-deploy` | `<id> [--reason R]` | Write a durable pending-deploy marker without dispatching deploy |
| `resume-deferred-deploy` | `<id>` | Validate the pending marker and return a deferred A5 flight to a fresh preview gate |
| `mark-deploy-dispatch-started` | `<id>` | Consume one A5 attempt and return deploy dispatch evidence |
| `verify-deploy` | `<id>` | Verify independent deploy evidence and set `terminal_deployed` only when clean |
| `record-deploy-failed` | `<id> --reason R` | Record a failed A5 deploy attempt |
| `retry-deploy` | `<id>` | Reopen dispatch only after independent `clean_rollback` evidence |
| `inspect-deploy-state` | `<id>` | Show recorded/fixture deploy inspection evidence |

## Ship 1a scope

- Ship 1a introduced the `k2bi` dispatched profile.
- Historical Ship-1a `command_key` values:
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

## A1 chain conductor -- promote -> screen -> thesis+T7 -> bear -> thesis gate

This is the Ship-2 Phase-A1 procedure for contract Stages 3-8. It is **agent-native** like Chat 1 / Chat 2: YOU conduct the single K2B conversation, gather Keith's decisions inline, and dispatch only bounded K2Bi adapter/helper calls through the allowlisted worker door. A1 has **NO capital path**: do not dispatch `write_complete_strategy_spec`, do not dispatch `run_full_ship`, do not approve a strategy, do not commit or touch the K2Bi engine. A1 ends parked at the thesis-approval gate or terminally rejected by bear VETO.

### A1 durable model

- Create one parent chain flight per ticker with `profile=k2b`, `entity=<TICKER>`, and `status=needs_human`. The parent is the conversation/portfolio ledger; it is not dispatched.
- Store resume flags in the parent payload. Use these flags, not a single `stage` string: `promote_done`, `screen_done`, `thesis_written`, `thesis_artifact_verified`, `bear_done`.
- Store T7 evidence in payload for replay/amendment: `claim_decisions`, `claim_decisions_hash`, `operator_override_reason`, `calx_override_acknowledged`, `vendor_warning_acknowledged`, `vendor_provenance`.
- `scripts.lib.orchestrator_store.a1_resume_action(<tid>)` is the resume oracle. It ignores stale `payload.stage` and returns the next missing subtask.
- Bear VETO is terminal: status `terminal_bear_veto`. Show the rejection and stop. Never offer approve/revise, never re-dispatch bear-case, never advance to A2 for that flight. A fresh chain is required.
- Revisions are bounded: at most 3. A fourth revise call leaves the flight in `needs_human` with `terminal_reason=revision_limit_exceeded`; surface that Keith must make a fresh human decision.

### A1 allowlisted dispatches

A1 uses the existing Ship-1a worker preflight: trusted K2Bi workspace, worker lock, human lock, clean K2Bi git tree. New K2Bi command keys:

- `k2bi-verify-and-generate-thesis` -> K2B runner calls K2Bi `invest_orchestrator_adapters.verify_and_generate_thesis(...)`.
- `k2bi-run-bear-case` -> K2B runner calls K2Bi `invest_bear_case.run_bear_case(...)`.
- `k2bi-screen-enrich` -> parameterized `python3 -m scripts.lib.invest_screen --enrich <TICKER>` with strict ticker validation.
- `k2bi-smoke-enrich-lrcx` remains the Ship-1a screen smoke.

The runner converts JSON into K2Bi dataclasses and delegates. It must not infer missing adapter fields, default source evidence, or rewrite operator marks. If a field cannot be filled honestly, mark that claim `advisory` or `refused` in the T7 conversation; let the adapter refuse if the claim is load-bearing and not framed.

### Exact T7 adapter contract

For every claim, emit exactly this shape:

```yaml
claim_id: str
claim_text: str
claim_load_bearing: bool
source_url: str | null
source_excerpt: str
curated_framing: str
operator_mark: verified | refused | override | advisory
operator_note: str | null
source_vendor: str
spot_check_vendor: str | null
```

Function kwargs passed to `verify_and_generate_thesis(...)`:

```yaml
operator_override_reason: str | null
calx_override_acknowledged: bool
vendor_warning_acknowledged: bool
vendor_provenance: object | null
refresh: bool
```

Rules:
- Do not coerce missing `source_excerpt`, `source_vendor`, `curated_framing`, or `source_url` to make the adapter happy.
- If any load-bearing claim is `refused` or `override`, collect a framed `operator_override_reason` and explicit `calx_override_acknowledged=true`.
- If `vendor_provenance` is supplied, collect `vendor_warning_acknowledged=true`.
- On re-dispatch after `thesis_written=true`, use `refresh=true` so the K2Bi adapter overwrites instead of appending.
- Adapter success stdout is JSON with `status: ok`. Adapter failure stdout is JSON with `status: error`, `category`, `retryable`, `exit_code`, `exception_type`, and `message`; stderr contains the same human-readable message. Treat `category=validation`/`exit_code=2`/`retryable=false` as an adapter refusal or permanent gate failure, and `category=transient`/`exit_code=3`/`retryable=true` as eligible for bounded worker retry.
- On adapter refusal during thesis dispatch, leave `thesis_written=false`, leave `bear_done=false`, keep the parent in `needs_human`, and surface the refusal text to Keith. The next resume action remains `dispatch_thesis` only after the operator amends the T7 payload; do not mark subtask flags from partial files or handwritten K2Bi state.
- On adapter process death, zombie reclaim, laptop sleep, or any worker result that is not a clean `done` artifact, do not trust parent flags or partial K2Bi files. Re-open the worker artifact/log and use the locked helpers: `verify-thesis-artifact <task-id> [path]` for normal verification, `force-verify-thesis-artifact <task-id> <path> --i-checked-the-log` only after manual log/artifact inspection, or `clear-thesis-artifact <task-id> --reason <why>` to explicitly clear invalid thesis/bear progress before redispatch. `--i-checked-the-log` is MANDATORY for force-verify; without it, the helper fails with `force verification requires --i-checked-the-log`. Do not edit payload JSON or hand-set flags. The resume oracle returns `verify_thesis_artifact` when `thesis_written=true` but `thesis_artifact_verified` is not set, and `thesis_artifact_invalid` when a previously verified artifact no longer validates; never advance to bear-case solely because `thesis_written=true`.
- Confirmed zombie reclaim clears partial thesis/bear flags and screen approval before re-readying an A1 worker task; resume must re-surface the screen approval gate or re-run/verify the thesis path after reclaim rather than trusting stale subtask flags.
- `a1_prepare_thesis_dispatch_payload` records `thesis_dispatch_started_at` for each thesis dispatch and hashes the full T7 context. Use `python3 -m scripts.lib.orchestrator_store verify-thesis-artifact <task-id> [path]` for deterministic artifact verification before bear-case dispatch. The helper records `thesis_artifact_verified=true`, `thesis_artifact_verified_at`, and `thesis_artifact_sha256` only after the artifact exists, is non-empty, and is not older than the recorded thesis dispatch when no prior SHA is available. Bear-case preflight re-checks the artifact path and recorded SHA/timestamp immediately before dispatch.
- Before Stage 4 or adapter dispatch, the K2Bi canonical registry must exist and contain the ticker. If preflight says the registry is missing/unreadable or empty, run `python3 -m scripts.build_canonical_registry` from the K2Bi checkout before redispatching. If it says malformed JSON, inspect disk/sync state before rebuilding. If it says `unknown canonical ticker`, reject or add the ticker upstream first.
- Adapter payload files, including nested `*_path` fields, are size bounded and require fd-path verification. If the adapter reports fd verification unavailable, stop and inspect the filesystem; do not bypass the path guard.
- `revision_count=3` means the third revision is in progress or complete. A fourth `revise` request terminalizes the flight as `needs_human` with `terminal_reason=revision_limit_exceeded`; the board blocker will say the entity lock is held, so cancel that old flight before starting a fresh A1 chain. Terminal-reason parks auto-expire through `poll-once` after `K2B_ORCH_TERMINAL_REASON_TTL_DAYS` (default 7). Do not call `a1_register_revision` for casual discussion or non-revision edits.

### Procedure

1. **Promote gate (Stage 3).** Keith must choose the ticker. If there is no watchlist entry yet, dispatch/route the existing K2Bi promotion entrypoint with human-confirmed inputs. Before any thesis dispatch, the K2B preflight requires `wiki/watchlist/<TICKER>.md` frontmatter `status: promoted`; if not, stop with the preflight error instead of hand-constructing downstream state.
2. **Screen (Stage 4).** Dispatch screen enrichment through the K2Bi allowlisted worker door. Record the verified screen artifact only after the worker task reaches `done` and the result artifact exists, using `python3 -m scripts.lib.orchestrator_store record-screen-done <parent-task-id> <artifact-path>`. Surface the score/band and ask Keith whether to run the chain. Only after Keith explicitly says yes, run `python3 -m scripts.lib.orchestrator_store approve-screen <parent-task-id>`; resume returns `await_screen_approval` until that approval is recorded.
3. **Thesis source fetch + T7 (Stages 5-7).** Gather sources inline with the Chat-1 honesty rule: fetch/repair every load-bearing citation. Build the full `ThesisInput` JSON and the exact `claim_decisions` list above. Surface each load-bearing claim to Keith and capture `verified`, `refused`, `override`, or `advisory` without changing his mark. Dispatch `k2bi-verify-and-generate-thesis`. Record `thesis_written=true` and `thesis_artifact_verified=true` only after the worker reaches `done` and the expected thesis artifact exists. On adapter refusal, show the refusal and keep the parent parked; do not write K2Bi state yourself.
4. **Bear case (Stage 7).** Dispatch `k2bi-run-bear-case`. Record `bear_done=true` only after the worker task reaches `done` and the result shows a verdict. If verdict is `VETO`, call the A1 VETO transition and stop terminally. If `PROCEED`, park at the thesis gate.
5. **Thesis gate (Stage 8).** Surface: thesis path, bear verdict/conviction, claim count and any refused/override/advisory claims. Keith may answer `approve` or `revise`. `approve` opens A2 -- run `python3 -m scripts.lib.orchestrator_store approve-thesis <parent-task-id>` (see the A2 conductor). `revise` must run `python3 -m scripts.lib.orchestrator_store register-revision <parent-task-id>`, then re-run Stages 5-7 with a fresh source fetch, previous `claim_decisions` resurfaced for amendment, and `refresh=true`.

## A2 chain conductor -- approved thesis -> strategy spec -> backtest -> strategy gate

Ship-2 Phase-A2 (contract Stages 9-10). A2 EXTENDS the same A1 parent chain flight past an APPROVED thesis. It is agent-native like A1: YOU conduct the single conversation and dispatch only bounded K2Bi calls through the allowlisted worker door. **A2 has NO capital path:** do not dispatch `run_full_ship`/`/invest-ship`, do not ship a strategy to the engine, do not commit or touch the K2Bi engine. A2 ends parked at the strategy-approval gate (the 2nd of the 4 human gates) or in a bounded-revision terminal park.

### A2 durable model (adds to the A1 model)

- Same parent flight (`profile=k2b`, `entity=<TICKER>`, `needs_human`). New parent-payload flags:
  `thesis_approved`, `strategy_spec_written`, `strategy_path`, `strategy_artifact_verified`,
  `strategy_artifact_sha256`, `backtest_done`, `backtest_artifact_path`, `backtest_look_ahead_check`,
  `strategy_approved`, `strategy_revision_count`.
- `a1_resume_action` extends past the thesis gate: with `thesis_approved` set it returns
  `dispatch_strategy` -> `verify_strategy_artifact` -> `dispatch_backtest` -> `strategy_approval_gate` ->
  (after approve) `strategy_approved_await_ship` (A3 not built yet). Backward-compatible: with no
  `thesis_approved`, it returns `thesis_approval_gate` exactly as A1.
- **Thesis/bear shared-file sha drift (latent A1 finding #5):** thesis + bear-case write the SAME
  `wiki/tickers/<SYM>.md`, so the pre-bear `thesis_artifact_sha256` no longer matches after bear appends.
  Fixed: `a1_mark_bear_verdict` re-anchors the sha synchronously on PROCEED (forward); for a flight created
  before that fix the resume oracle reports `thesis_artifact_invalid` and you recover with
  `python3 -m scripts.lib.orchestrator_store reanchor-thesis-artifact <task-id> [path] --i-checked-the-log
  --reason "<why>"` -- the `--i-checked-the-log` ack is MANDATORY (you attest the current file is the
  legitimate post-bear thesis+bear artifact, not corruption) and it only works after `bear_done=true`. Pre-bear
  thesis drift is real corruption -> use `clear-thesis-artifact`, not re-anchor.
- Strategy revisions are bounded at 3 (separate `strategy_revision_count`); a 4th `register-strategy-revision`
  terminalizes the flight `needs_human` with `terminal_reason=strategy_revision_limit_exceeded` (cancel it for
  a fresh chain).

### A2 allowlisted dispatches

Same Ship-1a worker preflight (trusted K2Bi workspace, worker lock, clean `~/Projects/K2Bi` git tree). New
command keys, dispatched through the `payload_path` carrier (allowed payload dir + adapter fd-guard) exactly
like A1's thesis/bear:

- `k2bi-write-strategy-spec` -> the K2B runner calls
  `invest_orchestrator_adapters.write_complete_strategy_spec(decision, repo_root=<K2Bi VAULT>)`. **NOTE the
  kwarg is `repo_root`** (where `wiki/strategies/` lives -- the K2Bi vault), not `vault_root`. The runner
  resolves `repo_root` to the K2Bi vault ONLY (a K2B-vault repo_root is refused).
- `k2bi-run-backtest` -> the runner calls `invest_backtest.run_backtest(slug, vault_root=<K2Bi VAULT>)`. The
  strategy writer does NOT chain the backtest, so this is a SECOND bounded dispatch (strategy child terminal
  before the backtest child is created -- strictly sequential). It pulls live yfinance bars and writes an
  immutable capture under `raw/backtests/`; it NEVER writes the strategy file.

Set `K2B_ORCH_ADAPTER_PAYLOAD_DIR` on the `poll-once` env (the allowed payload dir for the carrier). Children
are created with `--parent-task <parent> --flight <parent flight>` so the chain-scoped lock admits them.

### Exact StrategySpecDecision contract (no inferred schema)

`write_complete_strategy_spec`'s `decision` is a frozen dataclass; the runner builds it via the SAME strict
coercion thesis uses, which requires EVERY field present (it ignores dataclass defaults). Build the decision
JSON with all 20 fields (the 5 optionals as explicit `null`/`[]`):

```yaml
slug: str                       # ^[A-Za-z0-9][A-Za-z0-9_-]*$
symbol: str                     # MUST equal order.ticker (and the dispatched payload symbol)
sigid: str
risk_envelope_pct: str|float    # e.g. "0.0025" for 0.25% NAV-at-risk
order:                          # ticker == symbol; qty positive int; order_type MKT|LMT
  ticker: str
  side: str                     # e.g. "buy"
  qty: int
  order_type: MKT | LMT
  limit_price: str|null         # REQUIRED for LMT; MUST be null for MKT
  stop_loss: str
  time_in_force: str            # e.g. "DAY"
forward_guidance_metrics:       # non-empty list; sits_inside_guide MUST be a real bool
  - {metric, locked_threshold_text, guide_source_text, guide_range_text, sits_inside_guide}
forward_guidance_status: pass | override | waive
how_this_works: str             # non-empty -- the plain-English gate
bucket_rules: [str, ...]        # all of these are NON-EMPTY string lists
entry_rules:  [str, ...]
stop_rules:   [str, ...]
target_rules: [str, ...]
hold_rules:   [str, ...]
kill_rules:   [str, ...]
accepted_gaps:[str, ...]
forward_guidance_override_reason: str|null
forward_guidance_waive_reason: str|null
regime_filter: [..]|null        # [] is fine
date: str|null                  # ISO-8601
extra_frontmatter: dict|null
```

The runner refuses (status `error`, exit 2) if `payload.symbol != decision.symbol != decision.order.ticker`
or if any `sits_inside_guide` is not a real boolean (the K2Bi writer would otherwise coerce a string to True).
The K2Bi `write_complete_strategy_spec` is the REAL gate (slug/order/forward-guidance validators, atomic
write + sha verification); it raises `OrchestratorGateError` (adapter status `error`) on any violation -- this
is the A2 negative path (e.g. an empty `how_this_works` writes NO file).

### Procedure

1. **Approve the thesis gate (Stage 8 -> A2 open).** Resume returns `thesis_approval_gate`. Surface the thesis
   path + bear verdict/conviction + claim summary. On Keith's `approve`, run
   `python3 -m scripts.lib.orchestrator_store approve-thesis <parent-task-id>`. (`revise` stays the A1 path.)
   If resume reports `thesis_artifact_invalid` first, recover with
   `reanchor-thesis-artifact ... --i-checked-the-log` after confirming the ticker file is the legit post-bear
   artifact.
2. **Strategy spec (Stage 9).** Build the `decision` from the approved thesis (entry/stop/targets/hold/kill +
   plain-English How-This-Works). `~/Projects/K2Bi` MUST be a CLEAN tree on origin/main (worker preflight
   enforces; if dirty, surface to Keith -- do not mutate). Write the decision JSON to the payload dir, create
   the child (`k2bi-write-strategy-spec`, `--parent-task` + `--flight`, `--entity <TICKER>`, payload carries
   `symbol`, `repo_root=<K2Bi VAULT>`, `payload_path`), set `K2B_ORCH_ADAPTER_PAYLOAD_DIR`, `poll-once`. After
   the worker reaches `done` AND the strategy file exists, run `record-strategy-done <parent> <path>` then
   `verify-strategy-artifact <parent>`. On adapter refusal, show it and keep parked -- do not write K2Bi state.
3. **Backtest (Stage 10).** Create the second child (`k2bi-run-backtest`, same parent/flight/entity, payload
   carries `symbol`, `vault_root=<K2Bi VAULT>`, `slug`), `poll-once`. After `done` + the capture exists, run
   `record-backtest-done <parent> <capture-path> --look-ahead-check <passed|suspicious>`. Surface Sharpe /
   max-DD / win-rate and the `look_ahead_check` (the overfit/look-ahead rejector).
4. **Strategy gate (Stage 9 approval -- END of A2).** Resume returns `strategy_approval_gate`. Surface the
   strategy spec + backtest result. Keith answers `approve` -> `approve-strategy <parent>` (A2 done; the chain
   parks `strategy_approved_await_ship` for the future A3 ship-to-engine increment) or `revise` ->
   `register-strategy-revision <parent>` + re-run Stage 9-10 with an amended decision.

## A3 chain conductor -- approved strategy -> author to repo -> run_full_ship -> engine

Ship-2 Phase-A3 (contract Stage 11) is the capital path. It extends the SAME A1/A2 parent chain after
`strategy_approved_await_ship`, records Keith's fourth human gate, authors the approved proposed strategy into
the git-tracked K2Bi repo, dispatches K2Bi's merged `run_full_ship`, and marks `terminal_shipped` ONLY after
independent git/file inspection confirms the commit landed. A3 never hand-commits and never re-implements the
K2Bi ship gate; `run_full_ship` owns reviews, `handle_approve_strategy`, commit, and rollback.

### A3 durable model additions

- Parent payload flags: `ship_authorized`, `ship_repo_authored`, `ship_strategy_repo_path`,
  `ship_strategy_repo_sha256`, `ship_proposed_commit_sha`, `ship_proposed_committed_at`,
  `ship_dispatch_started_at`, `ship_attempt_count`, `ship_lease_id`, `ship_approved_at`,
  `ship_verified`, `ship_commit_sha`, `ship_rolled_back_at`, `ship_rollback_clean`,
  `ship_partial_detected_at`.
- Resume ladder after `strategy_approved`: no `ship_authorized` -> `strategy_approved_await_ship`;
  not repo-authored -> `author_strategy_to_repo`; repo-authored but not proposed-committed ->
  `commit_strategy_proposed`; not dispatched -> `dispatch_ship`; dispatched -> `verify_ship`.
  `ship_verified` or row status `terminal_shipped` returns `terminal_shipped` as-is; `ship_partial_detected_at`
  returns `ship_partial`; `ship_rolled_back_at` returns `ship_rolled_back`.
- Ship attempts are bounded at 3. Hitting the limit parks `needs_human` with
  `terminal_reason=ship_attempt_limit_exceeded`; cancel or make a fresh operator decision rather than forcing a
  fourth fire.

### A3 allowlisted dispatches

- `k2bi-author-strategy-to-repo` -> the K2B runner calls
  `invest_orchestrator_adapters.write_complete_strategy_spec(decision, repo_root=<K2Bi REPO>)`. This is repo-only:
  `repo_root` must match `K2B_ORCH_K2BI_WORKSPACE` / `~/Projects/K2Bi`, not the vault.
- `k2bi-run-full-ship` -> the runner calls
  `invest_orchestrator_adapters.run_full_ship(strategy_path=<repo strategy>, approval=<FullShipApproval>,
  vault_root=<K2Bi VAULT>, required_primary=<review primary>)`. The ship child uses
  `K2B_ORCH_SHIP_CMD_TIMEOUT` (default 1200s); other worker commands keep `K2B_ORCH_CMD_TIMEOUT` (default 540s).

Both children are created with `--parent-task <parent> --flight <parent flight> --entity <TICKER>` and
`payload_path`. `poll-once` cancels out-of-order A3 children unless the parent resume action matches the
child's stage: `k2bi-author-strategy-to-repo` requires `author_strategy_to_repo`, and `k2bi-run-full-ship`
requires **`verify_ship`** -- the ship child is dispatched AFTER `mark-ship-dispatch-started` records the
dispatch intent + mints the token, which advances the oracle to `verify_ship` (do NOT expect `dispatch_ship`
for the ship child).

### FullShipApproval token

Build the approval from the values returned by `mark-ship-dispatch-started`:

```text
APPROVE_STRATEGY:<slug>:<repo_sha256>:<approved_at>:<ship_lease_id>
```

`approved_at` must be ISO-8601 UTC with an explicit `+00:00` offset. `ship_lease_id` is generated as
`<slug>-ship-a1-YYYYMMDDTHHMMSSZ` and must match K2Bi's lease regex. The token binds the current repo strategy
bytes; if the file changes after the token is minted, capital preflight refuses before dispatch and K2Bi's
adapter repeats the same guard.

### Capital preflight and recovery constraints

- Kill-switch is read-only: if `<K2Bi VAULT>/System/.killed` exists, refuse. A3 never writes or clears it.
- Validators are read-only: `<K2Bi REPO>/execution/validators/config.yaml` must exist and be non-empty.
- Allowed-list (instrument whitelist) is read-only and checked UP FRONT: the entity ticker must be on
  `instrument_whitelist.symbols`, else the ship is refused with a route to `/invest-propose-limits` (Keith's
  workflow finding 2026-06-08 -- catch a non-whitelisted ticker here, not as a last-step `run_full_ship`
  rollback). A3 only READS the list; it NEVER adds a ticker -- that is operator-only via `/invest-propose-limits`
  + approval. Helper: `scripts.lib.orchestrator_profiles.ticker_whitelisted(<TICKER>)`.
- Strategy path must be `<K2Bi REPO>/wiki/strategies/strategy_<slug>.md`, status `proposed`, with frontmatter
  ticker/order.ticker matching the parent entity.
- K2Bi git tree must be clean except for the single target strategy file before `run_full_ship`.
- `inspect-ship-state` is the independent source of truth. It classifies `committed`, `clean_rollback`,
  `partial_approved_uncommitted`, `incomplete_rollback_marker`, or `unknown` from git HEAD, strategy
  frontmatter/status, and `<repo>/.k2bi-orchestrator/rollback/<slug>.json`; do not rely on the worker's
  `rollback_result` to decide retry or terminal state.
- On ship error, run `record-ship-failed <parent> --reason "<worker/preflight reason>"`, surface the inspector
  result, and do NOT re-fire. `retry-ship` is allowed only when a fresh live `inspect-ship-state` returns
  `clean_rollback`. `partial_approved_uncommitted` is never terminal; re-author/reset the repo strategy before
  any retry.
- If a flight is parked `needs_human_terminal` with `terminal_reason=ship_attempt_limit_exceeded` AND a live
  `inspect-ship-state` returns `clean_rollback` AND the spent attempts died on now-fixed flow bugs, recover with
  `python3 -m scripts.lib.orchestrator_store reset-ship-attempts <parent> --i-checked-the-log --reason "<why>"`
  then `retry-ship`. Never reset on `partial_approved_uncommitted`.

### Procedure

1. **Ship gate.** Resume returns `strategy_approved_await_ship`. FIRST run the read-only allowed-list pre-check
   (`scripts.lib.orchestrator_profiles.ticker_whitelisted(<TICKER>)`): if the ticker is NOT on the engine
   allowed-list, STOP -- offer the inline A4 path: "approve adding <TICKER> to the engine whitelist now?"
   If Keith says yes, run the A4 limits conductor below to `terminal_limits_applied`, then resume this A3 ship
   gate and re-run the read-only whitelist check. If an earlier ship attempt is parked at `ship_rolled_back`,
   use `retry-ship` only after the A4 apply is terminally verified. If Keith says no, do not author or
   authorize a ship. Otherwise surface that this commits the strategy to the K2Bi engine repo and ask Keith
   for the explicit ship decision. On approval, run
   `python3 -m scripts.lib.orchestrator_store approve-ship <parent>`.
2. **Author to repo.** Build or reconstruct the exact `StrategySpecDecision` for the approved A2 strategy. Create
   `k2bi-author-strategy-to-repo` with `repo_root=<K2Bi REPO>`, then `poll-once`. After the child is `done` and
   the repo strategy exists, run
   `python3 -m scripts.lib.orchestrator_store record-ship-repo-authored <parent> <K2Bi REPO>/wiki/strategies/strategy_<slug>.md`.
   This asserts repo file sha256 equals the approved A2 `strategy_artifact_sha256` and ticker equals entity.
2b. **Commit proposed first.** K2Bi's commit-msg hook forbids `(new file) -> approved`; the strategy must land
   tracked as `proposed` before `run_full_ship` does `proposed -> approved`. After `record-ship-repo-authored`,
   run `python3 -m scripts.lib.orchestrator_store commit-ship-repo-proposed <parent>`. This commits only the
   strategy file as `(new file) -> proposed`, re-checks the bound sha, refuses a non-proposed file or an
   unrelated-dirty tree, and is idempotent. The resume action advances to `dispatch_ship`.
3. **Dispatch ship.** Run
   `python3 -m scripts.lib.orchestrator_store mark-ship-dispatch-started <parent>` and use its JSON
   `lease_id`, `repo_sha`, `approved_at`, and `approval_token` to create the `k2bi-run-full-ship` payload with
   `strategy_path`, `approval`, `vault_root=<K2Bi VAULT>`, and `required_primary`. Then `poll-once`.
4. **Verify.** After the ship child reaches `done`, run
   `python3 -m scripts.lib.orchestrator_store verify-ship <parent>`. This calls `inspect-ship-state`; only
   `committed` sets `terminal_shipped` and records `ship_commit_sha`. If the child errors or verification refuses,
   run `record-ship-failed`, surface the state, and stop.

## A4 limits conductor -- approve -> author proposal -> apply -> verify

A4 is the operator-approved limits apply path. It is a standalone limits flight that applies a K2Bi limits
proposal, such as adding a ticker to `instrument_whitelist.symbols`, after Keith approves the exact YAML patch.
K2B never edits `execution/validators/config.yaml`, never clears the kill-switch, and never hand-commits. The
only write/commit path is K2Bi's shipped `invest_orchestrator_adapters.apply_approved_limits(...)`, which owns
review, `handle_approve_limits`, commit, and rollback.

### A4 durable model

- Parent flight: `profile=k2b`, `entity=<proposal-slug>`, `status=needs_human`, payload
  `{"chain_kind":"limits"}`.
- Parent payload flags: `limits_proposal_recorded`, `limits_proposal_path`, `limits_proposal_sha256`,
  `limits_proposal_slug`, `limits_expected_after_symbols`, `limits_authorized`,
  `limits_dispatch_started_at`, `limits_attempt_count`, `limits_apply_lease_id`, `limits_approved_at`,
  `limits_approval_token`, `limits_verified`, `limits_commit_sha`, `limits_rolled_back_at`,
  `limits_rollback_clean`, `limits_partial_detected_at`.
- Resume ladder: no proposal -> `author_limits_proposal`; recorded -> `await_limits_approval`; authorized ->
  `dispatch_limits`; dispatched -> `verify_limits`; verified or row status `terminal_limits_applied` ->
  `terminal_limits_applied`; partial/rollback markers return `limits_partial` / `limits_rolled_back`.
- Apply attempts are bounded at 3. Hitting the limit parks `needs_human` with
  `terminal_reason=limits_apply_attempt_limit_exceeded`.

### A4 allowlisted dispatch

- `k2bi-apply-limits` -> the K2B runner calls
  `invest_orchestrator_adapters.apply_approved_limits(proposal_path, approval=<LimitsApproval>,
  required_primary=<review primary>)`.
- The apply child uses `K2B_ORCH_SHIP_CMD_TIMEOUT` (default 1200s), same as `k2bi-run-full-ship`.
- The apply child is created with `--parent-task <parent> --flight <parent flight> --entity <proposal-slug>`.
  `poll-once` admits it only when parent resume action is `verify_limits`, because
  `mark-limits-dispatch-started` must run first and mint the token.

### LimitsApproval token

Build the approval from the values returned by `mark-limits-dispatch-started`:

```text
APPROVE_LIMITS:<slug>:<proposal_sha256>:<config_sha256>:<approved_at>:<apply_lease_id>
```

`approved_at` must be ISO-8601 UTC with an explicit `+00:00` offset. `apply_lease_id` is generated as
`<slug>-limits-a1-YYYYMMDDTHHMMSSZ` and must match K2Bi's lease regex. The token binds both the proposal Keith
approved and the current on-disk `config.yaml` bytes. Dispatch promptly; K2Bi enforces a 300s clock-skew window.

### Procedure

1. **Author the proposal.** Run K2Bi's read-only generator from the K2Bi repo:
   `python3 -m scripts.lib.propose_limits write --text "<Keith's ask>" --rationale "<why>"`.
   Create the parent flight with payload `{"chain_kind":"limits"}` and entity equal to the derived proposal slug.
   Then run `python3 -m scripts.lib.orchestrator_store record-limits-proposal <parent> <proposal-path>`.
   A4 scope is only `instrument_whitelist` / `add`; any other limits proposal stays on the manual
   `/invest-ship --approve-limits` path.
2. **Human gate.** Show Keith the proposal's actual `## YAML Patch`. On explicit approval of that exact patch,
   run `python3 -m scripts.lib.orchestrator_store authorize-limits <parent>`. Veto stops the flight; nothing is
   committed.
3. **Dispatch apply.** Run `python3 -m scripts.lib.orchestrator_store mark-limits-dispatch-started <parent>`.
   Use the returned JSON to build the `k2bi-apply-limits` child payload. The DB row `--payload` MUST carry
   `proposal_path`, `approval={final_approval_token, approved_by, approved_at, apply_lease_id}`, and
   `required_primary` inline for preflight, PLUS `payload_path` pointing to a JSON file with the same
   `{proposal_path, approval, required_primary}` for the adapter fd-path guard. Then run `poll-once`.
4. **Verify.** After the child reaches `done`, run
   `python3 -m scripts.lib.orchestrator_store verify-limits <parent>`. Only inspector state `committed` sets
   `terminal_limits_applied`. The inspector requires proposal `status: approved`, proposal and config tracked at
   the advanced HEAD, clean target status, no rollback marker, and `instrument_whitelist.symbols` exactly equal
   the proposal's expected `after` list, with no extra symbols.
5. **Failure and retry.** If the child errors or verification refuses, run
   `python3 -m scripts.lib.orchestrator_store record-limits-failed <parent> --reason "<worker/preflight reason>"`
   and surface `inspect-limits-state`. Do not re-fire. `retry-limits` is allowed only when a fresh live
   `inspect-limits-state` returns `clean_rollback`; `partial_approved_uncommitted` requires human recovery.

## A5 deploy conductor -- preview -> approve/defer -> dispatch -> verify

A5 closes the gap where a K2Bi repo ship can reach `terminal_shipped` or `terminal_limits_applied` without the
live VPS engine being updated. A5 wraps K2Bi's existing `scripts/deploy-to-vps.sh` path; it does not replace it.
K2B owns the human gate, state ledger, approval token, pending marker, and independent verification evidence.
K2Bi owns the actual deploy script and runtime verification. No live deploy is authorized unless Keith gives a
separate PM gate for that deploy. No live broker mutation is part of A5.

### A5 durable model

- Parent flight: `profile=k2b`, `entity=<ticker-or-change>`, `status=needs_human`, payload
  `{"chain_kind":"deploy", "deploy_source_parent":"<A3/A4 id>", "deploy_source_status":"terminal_shipped|terminal_limits_applied", "deploy_target_sha":"<40-char K2Bi sha>"}`.
- Parent payload flags: `deploy_preview_recorded`, `deploy_preview_manifest_path`,
  `deploy_preview_manifest_sha256`, `deploy_remote_baseline_sha`, `deploy_categories`,
  `deploy_restart_services`, `deploy_authorized`, `deploy_lease_id`, `deploy_approved_at`,
  `deploy_approval_token`, `deploy_dispatch_started_at`, `deploy_attempt_count`, `deploy_verified`,
  `deploy_dispatch_nonce`, `deploy_deployed_sha`, `deploy_deferred_at`, `deploy_pending_marker_path`.
- Resume ladder: no preview -> `preview_deploy`; preview recorded -> `await_deploy_approval`; deferred ->
  `deploy_deferred`; authorized -> `dispatch_deploy`; dispatched -> `verify_deploy`; row status
  `terminal_deployed` -> `terminal_deployed`.
- Deploy attempts are bounded at 3. Hitting the limit parks `needs_human` with
  `terminal_reason=deploy_attempt_limit_exceeded`.

### A5 allowlisted dispatch

- `k2bi-deploy-to-vps` resolves to `bash scripts/deploy-to-vps.sh auto`.
- The worker and preflight both require `K2B_ORCH_ALLOW_DEPLOY_TO_VPS=1` plus a per-flight
  `APPROVE_DEPLOY` token. Without both, the deploy command is not resolved.
- The deploy child uses `K2B_ORCH_SHIP_CMD_TIMEOUT` (default 1200s), same as A3/A4 capital commands.
- The preflight is read-only: it checks the K2Bi repo is a git checkout, `scripts/deploy-to-vps.sh` exists and
  is executable/tracked, the preview manifest hash still matches, the current remote baseline still equals the
  preview baseline, local K2Bi `HEAD` equals the approved `target_sha`, the kill-switch is not present, and the
  K2Bi tree is fully clean. It never writes broker, kill-switch, or validator state.

### APPROVE_DEPLOY token

Build the approval from the values in the A5 parent payload:

```text
APPROVE_DEPLOY:<target_sha>:<remote_baseline_sha>:<manifest_sha256>:<approved_at>:<deploy_lease_id>
```

`approved_at` must be ISO-8601 UTC with an explicit `+00:00` offset. `deploy_lease_id` uses
`<entity>-deploy-a1-YYYYMMDDTHHMMSSZ` and must match the same lease regex family as A3/A4.

### Procedure

1. **Preview.** Use a closed fixture/backtest or K2Bi deploy dry-run helper to produce a JSON manifest with
   `target_sha`, `remote_baseline_sha`, `categories`, `restart_services`, and `live_effect`. Then run
   `python3 -m scripts.lib.orchestrator_store record-deploy-preview <parent> <manifest.json>`.
2. **Human gate.** Show Keith the target sha, remote baseline, categories, restart services, and live effect.
   If he defers, run `defer-deploy`; this writes
   `K2B-Vault/System/orchestrator/pending-deploy/<target_sha>-<source_parent>-<task_id>.json` and does not
   dispatch. To resume a deferred flight, run `resume-deferred-deploy`; it verifies the pending marker and that
   local K2Bi `HEAD` still equals the deferred `deploy_target_sha`, then clears the old preview/approval fields
   and returns to `preview_deploy` so the remote baseline and manifest are captured again before approval. If
   K2Bi `HEAD` advanced, create a fresh A5 flight for the current HEAD instead. If he approves after the fresh
   preview, run `authorize-deploy`.
3. **Dispatch.** Run `mark-deploy-dispatch-started <parent>`. Build the `k2bi-deploy-to-vps` child payload
   from the returned evidence: `target_sha`, `remote_baseline_sha`, `manifest_path`, `manifest_sha256`,
   `dispatch_nonce`, `remote_baseline_path` or `current_remote_baseline_sha`, `categories`, `restart_services`,
   and `approval={final_approval_token, approved_by, approved_at, deploy_lease_id}`. Set
   `K2B_ORCH_ALLOW_DEPLOY_TO_VPS=1` only inside the explicit deploy PM-gated run, then run `poll-once`.
4. **Verify.** After the child finishes, record independent verification evidence as JSON under
   `K2B-Vault/System/orchestrator/deploy-results/` and set `deploy_verify_result_path` or
   `deploy_inspect_path` in the parent payload. Run `verify-deploy <parent>`.
   Full-checkout evidence with state `deployed`, `remote_head == target_sha`, `sync_state_sha == target_sha`,
   expected named services active, zero `recovery_state_mismatch_count`, and the matching dispatch
   token/time/nonce sets `terminal_deployed`. Category-scoped evidence may pass with a stale `remote_head` only
   when it still has `sync_state_sha == target_sha`, that sync-state SHA is not still the recorded baseline,
   explicitly sets `verification_scope: category_scoped`, its `remote_head` equals the recorded
   `remote_baseline_sha`, `deployed_categories` exactly matches the preview `categories` as a non-empty string
   list with no empty or duplicate entries, and `category_results` proves every expected category
   `matched_target: true` with positive non-bool integer `path_count` and present
   empty-list `missing_paths`, `mismatched_paths`, and `extra_paths`. Unknown `verification_scope` values are
   refused, and successful category-scoped verification records
   `deploy_verification_scope: category_scoped` in the parent payload for audit/recovery.
5. **Failure.** If the worker errors or verification refuses, run `record-deploy-failed <parent> --reason
   "<worker/preflight reason>"` and surface `inspect-deploy-state`. `retry-deploy` is allowed only when fresh
   trusted deploy-results evidence reports `clean_rollback`; otherwise do not retry without a fresh human
   decision.

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
