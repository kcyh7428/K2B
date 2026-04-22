---
name: k2b-research
description: Deep dive into external topics -- scan for new AI tools, techniques, and ideas; analyze URLs, YouTube videos, and GitHub repos. This skill should be used when Keith says /research, "look into this", "what's new in AI", or wants to deep-dive into a topic, URL, or repo. Also triggers on /research deep for multi-source NotebookLM research. For internal system health, use /improve instead.
---

# K2B Research Agent

On-demand research that scans externally for new tools, techniques, and ideas, and deep dives into specific topics or URLs. Supports multi-source deep research via NotebookLM.

## Commands

- `/research` -- External scanning using research-topics.md
- `/research "topic"` -- Deep dive on a specific topic
- `/research <url>` -- Deep dive on a specific URL (YouTube, GitHub, article)
- `/research deep <topic>` -- Multi-source deep research via NotebookLM (see below)
- `/research deep <topic> --sources <url1> <url2> ...` -- Deep research with specific sources
- `/research notebook <subcommand>` -- Persistent named NotebookLM notebooks for multi-angle research (see "Named notebook library" section)
- `/research videos "<query>"` -- On-demand YouTube discovery + NotebookLM filter (one-shot, notebook auto-deleted after the run)

> For internal vault health and system auditing, use `/improve vault` instead.

## Vault & Skill Paths

- Vault: `~/Projects/K2B-Vault`
- Skills: `~/Projects/K2B/.claude/skills/`
- Research topics: `~/Projects/K2B-Vault/wiki/context/research-topics.md`
- Output: `~/Projects/K2B-Vault/raw/research/`

## External Scanning

### Default Mode (no topic/URL)

1. Read `K2B-Vault/wiki/context/research-topics.md` for the topic list
2. For each topic category, run 1-2 targeted web searches
3. For each finding:
   - Brief summary (2-3 sentences max)
   - K2B relevance: how could this be applied to K2B specifically?
   - Actionability: is this something Keith could use now, soon, or someday?
4. Prioritize findings by relevance to K2B

### Topic Mode (`/research "topic"`)

1. Run 3-5 targeted web searches on the specific topic
2. Read and synthesize findings
3. Produce a deep-dive analysis focused on K2B applicability
4. Include specific recommendations: "K2B could implement X by doing Y"

### URL Mode (`/research <url>`)

Detect URL type and handle accordingly:

**YouTube URLs:**
1. Fetch transcript using the YouTube Transcript MCP tool
2. Analyze the full transcript
3. Extract key concepts, techniques, tools mentioned
4. Map each to K2B applicability
5. Note timestamps for the most relevant segments

**GitHub repo URLs:**
1. Fetch and read the README
2. Explore the repo structure (key files, directory layout)
3. Assess: what patterns or code could K2B reuse?
4. Note specific files or techniques worth adopting

**Article/web page URLs:**
1. Fetch the page content
2. Extract key insights and techniques
3. Analyze through the lens of "what can K2B learn from this?"

**All URL types produce** the **Lens-Based Review Format** (see section below). The review leads with a verdict, runs the universal checks (gated, novelty, skepticism), applies a lens-specific already-have check against K2B's existing stack, and stakes one claim (adopt / retrofit / skip / post seed / seed ticker / etc., depending on detected lens).

Save to `raw/research/YYYY-MM-DD_research_<topic-slug>.md` per the format's skeleton.

## Deep Research Mode (`/research deep <topic>`) -- added 2026-04-12

Multi-source research powered by Google NotebookLM. Creates a dedicated notebook, loads multiple sources, runs structured queries (analysis done by Gemini at zero token cost to K2B), then synthesizes findings into the vault.

**Prerequisites**: `notebooklm-py` installed and authenticated (`notebooklm auth check --test`).

### When to use deep vs regular research

- **Regular** (`/research "topic"` or `/research <url>`): Single source or quick web scan. Fast, cheap.
- **Deep** (`/research deep <topic>`): Multi-source synthesis across 10-50 sources. When the topic requires cross-referencing multiple perspectives, comparing implementations, or building a comprehensive understanding.

### Workflow

#### Phase 1: Source Gathering

1. Search for sources in parallel:
   - **YouTube**: Run `python3 ~/Projects/K2B/scripts/yt-search.py "<topic>" --count 15 --months 6` for relevant videos. Uses YouTube Data API v3 with K2B's OAuth credentials (works on both MacBook and Mac Mini). Costs ~101 quota units per search (100 for search.list + 1 for videos.list) out of 10,000/day.
   - **Perplexity**: Use `mcp__perplexity-ask__perplexity_ask` for broader research including GitHub repos, Reddit discussions, blog posts, tweets. Ask for specific URLs and repo names.
   - **Vault**: Grep `~/Projects/K2B-Vault/wiki/` for existing vault notes on the topic
2. Present a numbered source list to Keith. Include title, source type, and brief reason for inclusion.
3. Keith reviews, adds/removes sources, approves.

If Keith provides `--sources <url1> <url2>`, skip the search phase and use those directly.

#### Phase 2: NotebookLM Setup

Run these commands sequentially:

```bash
notebooklm create "K2B Research: <topic>" --json
# Parse notebook_id from JSON output
notebooklm use <notebook_id>
```

Add each approved source:
```bash
# URLs, YouTube, articles
notebooklm source add "<url>"

# Vault notes (pass path directly -- notebooklm-py handles .md files natively)
notebooklm source add ~/Projects/K2B-Vault/wiki/path/to/note.md

# Local files (PDFs, text, markdown, Word docs)
notebooklm source add ./path/to/file.pdf
```

Wait for all sources to be indexed:
```bash
notebooklm source list --json
# All sources should show status: "ready"
```

**Source limit**: 50 per notebook (standard tier). If more than 50 sources, prioritize by relevance.

#### Phase 3: Structured Research Queries

Run 5-8 targeted questions against the notebook. NotebookLM (Gemini) does ALL the analysis -- zero Opus tokens for this phase.

```bash
notebooklm ask "<question>"
```

**Standard question categories** (adapt to topic):
1. **Landscape**: "What are the main approaches/patterns for <topic> across these sources?"
2. **Architecture**: "What architectural or implementation patterns do these sources recommend?"
3. **Comparison**: "Compare the different approaches. What are the tradeoffs?"
4. **Risks**: "What are the biggest failure modes, limitations, or mistakes people report?"
5. **Minimal viable**: "What's the simplest starting point, and what's the recommended evolution path?"
6. **Keith-specific**: "How would this apply to someone who is [Keith's context -- SJM executive, building a personal AI second brain, uses Obsidian vault]?"

Add 1-2 topic-specific questions based on Keith's original prompt.

Use `--json` if you need citation references for attribution in the vault note.

#### Phase 4: Optional Deliverables

Ask Keith: "Want an audio overview to listen to, a mind map, or an infographic?"

If yes:
```bash
# Audio overview (podcast)
notebooklm generate audio "Focus on <specific angle>" --json
# Wait for completion (can use subagent or poll)
notebooklm artifact wait <artifact_id> --timeout 1200
notebooklm download audio ~/Projects/K2B-Vault/Assets/audio/<date>_research_<topic>.mp3

# Mind map (instant)
notebooklm generate mind-map
notebooklm download mind-map ~/Projects/K2B-Vault/Assets/<date>_research_<topic>_mindmap.json

# Infographic
notebooklm generate infographic --detail detailed
notebooklm artifact wait <artifact_id> --timeout 600
notebooklm download infographic ~/Projects/K2B-Vault/Assets/images/<date>_research_<topic>.png
```

#### Phase 5: Synthesis

Opus reads all NotebookLM answers and writes a structured vault note. This is the ONLY phase that costs Opus tokens.

Output format: use the **Lens-Based Review Format** (see section below). The lens is usually implied by the topic Keith chose (for example, "multi-agent coding patterns" is Stack lens; "semiconductor earnings trends" is K2Bi lens), but multi-lens is common on cross-cutting topics and both lenses should run in full when they apply.

Deep Research adds one extra section before `## What they're actually showing`:

```markdown
## Sources Analyzed

N sources (X YouTube, Y GitHub repos, Z articles, W vault notes)
NotebookLM notebook: [notebook-id] (persistent, can revisit for follow-up queries)
```

And one extra frontmatter field: `notebooklm-notebook: "<notebook-id>"`. Everything else is identical to URL Deep Dive.

Save to `raw/research/YYYY-MM-DD_research_<topic-slug>.md`.

#### Phase 6: Compile

Trigger k2b-compile on the new raw research note:
- Updates relevant wiki pages (concepts, projects, reference)
- Creates new reference pages if needed
- Updates cross-links

### Deep Research Output Format

Deep research uses the **Lens-Based Review Format** (see section below). The deep-research-specific additions (sources-analyzed section, notebooklm-notebook frontmatter field, optional audio / mind-map deliverables) are documented in Phase 5 above.

If audio overview or mind-map artifacts were generated in Phase 4, append them to the review under `## Deliverables`:

```markdown
## Deliverables

- Audio overview: [[Assets/audio/YYYY-MM-DD_research_topic.mp3]]
- Mind map: [[Assets/YYYY-MM-DD_research_topic_mindmap.json]]
```

### Commander/Worker Pattern for Deep Research

Deep research adds Gemini (via NotebookLM) as a third worker alongside MiniMax:

| Role | Who | What they do in deep research |
|------|-----|-------------------------------|
| Commander | Opus | Source gathering, question design, K2B framing, vault integration |
| Worker 1 | Gemini (NotebookLM) | Multi-document analysis, cross-referencing, citation-grounded answers |
| Worker 2 | MiniMax M2.7 | Bulk extraction on individual long sources (if needed, per size gate) |

Gemini handles the expensive multi-doc synthesis for free. Opus adds identity-aware judgment. MiniMax handles individual source extraction when sources exceed the 10K char size gate.

## Lens-Based Review Format

Used by **URL Deep Dive** (`/research <url>`) and **Deep Research synthesis** (Phase 5 of `/research deep <topic>`). Other modes (default, topic, videos, notebook ask) keep their own output formats.

Why this format exists: the research skill's job is not to summarize, it's to produce a **verdict** and **stake a claim** calibrated to what kind of content this is and to Keith's existing stack. A Claude Code tool demo, a founder interview, a macro-economy essay, and a recruiting industry piece all need different relevance anchors. One generic output shape cannot do all four well.

### Step 1: Detect the lens (keyword classifier)

Scan the fetched transcript (URL mode) or the topic string plus source list (deep research) for the clusters below. Pick the single strongest match, or multiple lenses when content genuinely spans domains. State the detected lens or lenses at the top of the review so Keith can redirect ("run as Investment lens").

| Keyword cluster | Lens |
|---|---|
| `agent harness, hooks, SDK, codebase, MCP, Claude Code, skills, codex, worktree` | **Stack** |
| `founder, ARR, solopreneur, launched, raised, product-market fit, solo` | **Content** |
| `economy, labor market, AGI, policy, concentration, geopolitics, regulation` | **Worldview** |
| `talent acquisition, recruiter, hire, HR, candidate, sourcing, ATS` | **Day-job** |
| `position, trade, ticker, sector, Fed, yield, earnings, macro, backtest` | **K2Bi** |
| `leadership, productivity, habits, exec coaching, decision-making, focus` | **Growth** |

Multi-lens is allowed (example: a Claude-Code-for-recruiters piece hits both Stack and Day-job). When running multi-lens, present both lens sections in full so Keith can scan both views.

### Step 2: Motivations pre-check

Before writing the review, grep `K2B-Vault/wiki/context/active-motivations.md` Active Questions list for keyword overlap with the content. If any active question matches, flag at the very top of the review:

> **Touches active question**: "[the matching question]"

This is the highest-value relevance signal because it's what Keith said he cares about right now.

### Step 3: Universal checks (apply regardless of lens)

Every review leads with these, in this order:

1. **Verdict line** -- exactly one of `Substance` / `Clickbait` / `Partial` / `Gated` / `Hype`, plus a one-sentence reason.
2. **Gated flag** -- if the source paywalls code, community, or key details (course paywall, locked repo, course-members-only codebase), call it out at the verdict line.
3. **Novelty check** -- is this actually new, or repackaging of prior art (AutoGen, CrewAI, Devin, standard agent patterns, etc.)? One line.
4. **Skepticism flags** -- overstated claims, missing numbers, cavalier cost stance ("tokens are cheap, spend more"), survivorship bias, cherry-picked time windows, unchecked assumptions.

### Step 4: Per-lens body

Each lens has a specific "already-have anchor" and a fixed set of "stake-a-claim" options. The review body checks content against the anchor and picks one stake-a-claim per lens.

#### Stack lens (Claude Code, agent harness, dev tool content)

- **Already-have anchor**: K2B's harness -- Commander/Worker (Opus + MiniMax M2.7), adversarial review (Codex primary + MiniMax fallback), 30+ specialized skills, hooks, Ship/Sync workflow, `active_rules` + `policy-ledger.jsonl`, background observer.
- **Check every claim against the anchor**: state explicitly "you already have this as X" or "new to you".
- **Stake-a-claim options** (pick one): `Adopt` (rare -- whole-cloth migration justified) / `Retrofit` (steal specific ideas into existing stack, with file paths) / `Skip` / `Watch` (track, revisit later).

#### Content lens (founder interviews, startup stories, AI creator content)

- **Already-have anchor**: Keith's LinkedIn lane -- senior executive in a traditional corporate (SJM Resorts) using AI to 10x, the content angle demonstrated in `wiki/content-pipeline/` and vault posts.
- **Check**: does this make a good counterpoint or post seed? What specific quotes land? Is the interviewee's framing usefully contrastable with Keith's senior-exec view?
- **Stake-a-claim options** (pick one): `Post seed` (one-line angle for `review/content_*.md`) / `Counterpoint` (Keith's senior-exec angle vs the interviewee's) / `Quote mine` (specific pull-quotes worth archiving) / `Skip`.

#### Worldview lens (AI strategy, macro, AGI, labor market, policy, geopolitics)

- **Already-have anchor**: `wiki/concepts/` mental models + `active-motivations.md` active questions.
- **Check**: does this update, confirm, or contradict existing K2B concept notes?
- **Stake-a-claim options** (pick one): `Track` (add to watch list) / `Disagree` (write counter-note) / `Integrate` (propose update to concept note, include the slug) / `Skip`.

#### Day-job lens (recruiting, TA, HR industry content)

- **Already-have anchor**: Signhub Tech + TalentSignals + Agency at Scale state + TA industry trends tracked in `wiki/concepts/`.
- **Check**: is this a product competitor (to Signhub or TalentSignals specifically), a service competitor, or an industry trend worth watching?
- **Stake-a-claim options** (pick one): `Signhub intel` (draft competitive entry for `wiki/work/work_signhub.md`) / `TalentSignals intel` (same for `work_talentsignals.md`) / `Trend track` (add to a TA trends concept note) / `Post seed` / `Skip`.
- **Explicitly NOT an option**: "Try at SJM". Keith does not route TA content into SJM operations via the research skill.

#### K2Bi lens (investing, trading, markets)

- **Already-have anchor**: K2Bi-Vault (`wiki/tickers/<SYMBOL>.md`, `wiki/theses/`, `data-sources.md`). K2Bi is in Phase 3.6 paper-trade shakedown as of 2026-04-22 -- still stocking the shelf of candidate strategies and tickers, not actively trading discretionary signals.
- **Output shape for this lens**: produce numbered candidate lists so Keith can cherry-pick:
  - **Candidate tickers**: `<SYMBOL> -- <sector> -- <one-line why>`
  - **Candidate theses**: testable hypothesis with entry/exit rules if mentioned in the source
  - **Candidate data sources**: feed / MCP / dataset with tier (free / paid) and coverage
  - **Candidate regime signals**: indicator worth tracking (yield curve, VIX regime, sector rotation trigger)
- **Stake-a-claim options** (pick one or more): `Seed ticker` / `Seed thesis` / `Seed data source` / `Seed regime signal` / `Skip`.
- **Cross-vault handling**: output lands in K2B-Vault like any other review note. Keith copies the items he wants to K2Bi-Vault manually when he is next in a K2Bi session. Do NOT write directly to K2Bi-Vault from this skill.
- **Investment-specific skepticism flags** (in addition to the universal ones):
  - Overfit risk: claim tested on enough history? one market regime only?
  - Survivorship bias: are failed versions of this strategy shown?
  - Capital-tier mismatch: does it require size or fees retail cannot get?
  - Decay guess: fast (news / earnings, ~5-day half-life) / medium (sector rotation, ~30-day) / slow (structural, ~180-day).

#### Growth lens (leadership, exec coaching, personal productivity)

- **Already-have anchor**: `active-motivations.md` active questions and Building section.
- **Check**: does this add a new question worth tracking or confirm an existing one?
- **Stake-a-claim options** (pick one): `Add to motivations` (route via `scripts/motivations-helper.sh add-question`) / `Bookmark` (save the quote or reference only) / `Skip`.

### Step 5: Output frontmatter and skeleton

```markdown
---
tags: [research, lens-review, {lens-slug}, {topic-tags}]
date: YYYY-MM-DD
type: research-briefing
origin: k2b-generate
source: "[Title](URL)"
lens: "{Stack | Content | Worldview | Day-job | K2Bi | Growth | multi-lens}"
follow-up-delivery: null  # feature slug this research commits to; "none" if purely informational; null while pending. /lint flags null/absent older than 30 days.
up: "[[Home]]"
---

# Lens Review: [Title] -- [Lens] lens

**Verdict**: [Substance / Clickbait / Partial / Gated / Hype] -- [one-sentence reason]

**Touches active question**: "[question]"   _(omit this line entirely if no motivations match)_

## What they're actually showing

- [5-8 bullets summarizing the substantive content]

## Universal checks

- **Gated**: [what is behind paywall / what is free]
- **Novelty**: [new idea or repackaged prior art, one line with comparison]
- **Skepticism flags**: [overstated claims, missing numbers, cavalier cost stance, etc.]

## Already-have check ([Lens] lens)

[Table or bullet list mapping content claims to what K2B already has. Be explicit: "you have this as X" or "new to you".]

## K2B vs K2Bi   _(include only when content has investment relevance)_

- **K2B** (knowledge work): [relevance or "not relevant"]
- **K2Bi** (trading): [relevance or "not relevant"]

## Candidate list   _(K2Bi lens only; omit entire section for other lenses)_

- **Tickers**: [numbered list if any]
- **Theses**: [numbered list if any]
- **Data sources**: [numbered list if any]
- **Regime signals**: [numbered list if any]

## Stake a claim

**[Adopt / Retrofit / Skip / Watch / Post seed / Counterpoint / Quote mine / Track / Disagree / Integrate / Signhub intel / TalentSignals intel / Trend track / Seed ticker / Seed thesis / Seed data source / Seed regime signal / Add to motivations / Bookmark]**: [1-3 sentence reasoning and, if the stake is action-worthy, a concrete next step with file paths]

## Linked Notes

[wikilinks to related vault notes]
```

**For multi-lens content**: repeat the `## Already-have check` and `## Stake a claim` sections for each lens, labeled with the lens name. Keep universal checks as one section at top.

**For Deep Research synthesis (vs URL mode)**: add these before `## What they're actually showing`:

```markdown
## Sources Analyzed

N sources (X YouTube, Y GitHub repos, Z articles, W vault notes)
NotebookLM notebook: [notebook-id] (persistent, can revisit for follow-up queries)
```

And add `notebooklm-notebook: "<notebook-id>"` to the frontmatter. Everything else is identical.

### Step 6: Writing tips

- **Lead with the verdict.** Keith reads the first line. Make it count.
- **Be blunt about gating and hype.** No polite softening. "Paywalled", "overstated", "already done elsewhere".
- **Map every substantive claim to what K2B already has.** Do not recommend "X pattern" without first checking if K2B already implements X.
- **Stake exactly one claim per lens.** "Adopt and retrofit" is a hedge. Pick.
- **Offer a concrete next action at the end** only if the stake is action-worthy (Adopt / Retrofit / Post seed / Seed ticker / Integrate / Signhub intel / etc.). Skip / Watch / Counterpoint usually do not need one.
- **State detected lens upfront in the first output line** so Keith can redirect before reading the rest.

## `/research videos "<query>"` -- on-demand video discovery via NotebookLM

Retires the old YouTube recommend agent. Finds videos matching a query, filters them via NotebookLM using Keith's baked framing + the tail of `wiki/context/video-preferences.md`, adds suitable ones to the K2B Watch playlist, drops per-video review notes, sends a Telegram notification.

**Prerequisites:** `notebooklm auth check --test` passes. `K2B_BOT_TOKEN` is set in env.

### Baked Keith framing (do not edit per query)

> Senior TA leader running AI transformation in a large traditional corporate (SJM Resorts, Macau). Also operates Signhub Tech (HK), TalentSignals, Agency at Scale. Content angle: showing how senior executives in traditional corporates use AI to 10x effectiveness. Prefer content creators with clear concrete examples over academic papers. Prefer actionable over theoretical. Prefer deployable in 90 days over visionary. Skip pure hype, skip thumbnails with "SHOCKING" / "INSANE", skip anything under 3 minutes, skip Chinese-only content unless specifically requested.

### Flow

**Capture the query as the first thing the skill does.** The query is user-supplied and may contain quotes, backticks, dollar signs, or other shell metacharacters. Never interpolate `<query>` literally into a bash block -- always go through the bound variables defined here.

```bash
QUERY="$1"                           # raw user input, never echoed unescaped into shell
QUERY_SAFE=$(printf '%q' "$QUERY")   # safe for inline use inside double-quoted strings
QUERY_SLUG=$(printf '%s' "$QUERY" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]\{1,\}/-/g; s/^-//; s/-$//' | cut -c1-60)
```

1. **Get candidate YouTube URLs** via `yt-search.py` (not NotebookLM -- NotebookLM is the filter, not the discovery engine). Use a per-run scratch file via `mktemp` so concurrent scheduled runs cannot stomp each other:
   ```bash
   CANDIDATES=$(mktemp -t k2b-candidates.XXXXXX.json)
   trap 'rm -f "$CANDIDATES"' EXIT
   python3 ~/Projects/K2B/scripts/yt-search.py "$QUERY" --count 25 --months 1 --json > "$CANDIDATES"
   ```
   Parse the JSON (shape: `{"query", "count", "results": [{"url", "title", "channel", "duration", "published", ...}]}`).

   **Zero-candidate + unparseable-output guard (mandatory before Step 2).** The old `[[ "$COUNT" == "0" ]]` check silently passed on any non-numeric / empty `$COUNT`, so a broken `$CANDIDATES` (disk full, truncated write, upstream yt-search failure writing garbage to stdout) would bypass the guard and hit `notebooklm create` anyway. Fail loud on parse errors, separately from the legitimate zero-result case:
   ```bash
   COUNT=$(jq '.count' "$CANDIDATES" 2>/dev/null)
   if [[ -z "$COUNT" || ! "$COUNT" =~ ^[0-9]+$ ]]; then
     echo "FATAL: yt-search produced unparseable output in $CANDIDATES" >&2
     ~/Projects/K2B/scripts/send-telegram.sh "K2B /research videos: yt-search output unparseable for: $QUERY -- aborting, see run log" || true
     exit 1
   fi
   CANDIDATES_SCREENED="$COUNT"   # consumed by Step 10 zero-picks Telegram message
   if (( COUNT == 0 )); then
     ~/Projects/K2B/scripts/send-telegram.sh "K2B /research videos: no recent videos found for: $QUERY"
     exit 0
   fi
   ```

   **Why these args:** `--count 25` gives higher recall before filtering (Gemini typically rejects 60-80%, so 25 candidates yields ~5-10 suitable, matching the 3-10-per-run target). `--months 1` keeps the semantics at "what's new and worth knowing" -- six months of backlog dilutes recency. If a topic is thin in the last month, the run returns fewer videos and that's fine; silence beats stale.

2. **Create fresh notebook and add each candidate as a YouTube source.** Build the notebook title via the safe-quoted variable; iterate URLs via a `while read` loop (NOT `for URL in $(jq ...)`) so URLs containing whitespace or shell metacharacters cannot break the loop:
   ```bash
   NB_ID=$(notebooklm create "Videos: $QUERY" --json | jq -r '.notebook.id')
   SOURCE_IDS=()
   while IFS= read -r URL; do
     [[ -z "$URL" ]] && continue
     SRC_ID=$(notebooklm source add "$URL" -n "$NB_ID" --json 2>/dev/null | jq -r '.source.id // .id // empty')
     if [[ -n "$SRC_ID" ]]; then
       SOURCE_IDS+=("$SRC_ID")
     fi
   done < <(jq -r '.results[].url' "$CANDIDATES")
   ```
   `notebooklm source add` auto-detects YouTube URLs and pulls the transcript as the indexed content. `source add` itself returns quickly; processing happens in the background and is polled via `source wait`.

3. **Wait for all sources to index in parallel, with per-source retry, then canonical-reconcile against `notebooklm source list --json` before enforcing the >=5 ready threshold.** A bare `wait` only checks the last backgrounded job, so we collect each child PID and `wait $PID` individually. A single-shot `source wait` loses transient failures to silent timeouts (the first-run incident on 2026-04-15 dropped 8 of 25 sources with no retry and no visibility into whether those were real failures or indexer stalls), so each child gets one retry with a 2s backoff before it concedes. And the ephemeral `$READY_LOG` can diverge from NotebookLM's actual state because it reflects the per-child `source wait` return code, not notebook truth -- the same run observed NBLM describe 24 sources while `$READY_LOG` said only 17 were ready. After the parallel loop returns, we re-query the notebook once more via `source list --json` and prefer that canonical count for the threshold check, falling back to `$READY_LOG` only if the canonical query fails.
   ```bash
   declare -a WAIT_PIDS=()
   declare -a WAIT_IDS=()
   READY_LOG=$(mktemp -t k2b-ready.XXXXXX.log)
   CANONICAL_JSON=$(mktemp -t k2b-canonical.XXXXXX.json)
   trap 'rm -f "$CANDIDATES" "$READY_LOG" "$CANONICAL_JSON"' EXIT

   for ID in "${SOURCE_IDS[@]}"; do
     (
       # One retry with 2s backoff. `source wait` may spuriously time out on
       # transient indexer stalls -- a second attempt after a short pause
       # usually resolves it. Total worst-case per source: 600 + 2 + 600 = 1202s.
       for ATTEMPT in 1 2; do
         if notebooklm source wait "$ID" -n "$NB_ID" --timeout 600 --json 2>/dev/null \
              | jq -e '.status == "ready"' >/dev/null 2>&1; then
           echo "ready $ID attempt=$ATTEMPT" >> "$READY_LOG"
           exit 0
         fi
         (( ATTEMPT == 1 )) && sleep 2
       done
       echo "fail  $ID attempts=2" >> "$READY_LOG"
     ) &
     WAIT_PIDS+=("$!")
     WAIT_IDS+=("$ID")
   done

   for PID in "${WAIT_PIDS[@]}"; do wait "$PID" || true; done

   # Canonical reconcile: ask NotebookLM for the current source list once more
   # after all child waits have returned. Prefer this over $READY_LOG because
   # (a) parallel children race on shared-file appends (<PIPE_BUF so atomic, but
   # the counts can still diverge from truth), and (b) a `source wait` that
   # returned non-ready may have quietly finished indexing just after its timeout.
   notebooklm source list -n "$NB_ID" --json > "$CANONICAL_JSON" 2>/dev/null || true
   READY_COUNT=""
   TOTAL_COUNT=""
   if [[ -s "$CANONICAL_JSON" ]] && jq empty "$CANONICAL_JSON" 2>/dev/null; then
     READY_COUNT=$(jq '
       if type == "array" then [.[] | select((.status // "") == "ready")] | length
       elif type == "object" and has("sources") then [.sources[] | select((.status // "") == "ready")] | length
       else empty end' "$CANONICAL_JSON" 2>/dev/null)
     TOTAL_COUNT=$(jq '
       if type == "array" then length
       elif type == "object" and has("sources") then .sources | length
       else empty end' "$CANONICAL_JSON" 2>/dev/null)
   fi
   if [[ -z "$READY_COUNT" || -z "$TOTAL_COUNT" ]]; then
     READY_COUNT=$(grep -c '^ready ' "$READY_LOG" 2>/dev/null || true)
     TOTAL_COUNT=${#SOURCE_IDS[@]}
     RECONCILE_SOURCE="ephemeral \$READY_LOG (canonical reconcile failed)"
   else
     RECONCILE_SOURCE="canonical source list"
   fi
   FAIL_COUNT=$(grep -c '^fail '  "$READY_LOG" 2>/dev/null || true)

   if (( READY_COUNT < 5 )); then
     ~/Projects/K2B/scripts/send-telegram.sh \
       "K2B /research videos: only $READY_COUNT of $TOTAL_COUNT videos indexed for: $QUERY (reconcile=$RECONCILE_SOURCE) -- aborting, see run record"
     # Still write a partial run record + delete the notebook before exiting
     notebooklm delete -n "$NB_ID" -y >/dev/null 2>&1 || true
     exit 1
   fi
   ```

   Per-source failures below the threshold (private video, transcription unavailable, rate-limited) are tolerated -- only failures that drop the canonical ready count below 5 abort the run. Log BOTH the per-source results from `$READY_LOG` (with `attempt=` / `attempts=` suffixes so the retry pattern is auditable) AND the `$RECONCILE_SOURCE` marker into the run record in Step 9.

4. **Read the preference tail** into a variable:
   ```bash
   PREF_TAIL=$(tail -n 30 ~/Projects/K2B-Vault/wiki/context/video-preferences.md | sed 's/"/\\"/g')
   ```

5. **Ask NotebookLM for content descriptions only -- NotebookLM is the reader, K2B is the judge.** The baked Keith framing and the preference tail are NOT passed to Gemini; K2B holds them for Step 6. Gemini must not rank, filter, or judge suitability, even when viewer context is provided (see below).

   Load Keith's active motivations (gated by the `K2B_MOTIVATIONS_ENABLED` rollback toggle, default `true`). When empty, the prompt reverts byte-for-byte to its pre-feature form:
   ```bash
   MOTIVATIONS=""
   if [[ "${K2B_MOTIVATIONS_ENABLED:-true}" == "true" ]]; then
     MOTIVATIONS=$(~/Projects/K2B/scripts/motivations-helper.sh read 2>/dev/null || true)
   fi

   if [ -n "$MOTIVATIONS" ]; then
     MOT_BLOCK=$(cat <<EOF

   Viewer context (use for EXTRACTION GUIDANCE only, not ranking):
   ${MOTIVATIONS}

   For every video, maintain the baseline description quality defined above
   (2-3 sentences in what_it_covers, all schema fields populated).

   Additionally, when a video's content touches any viewer context area,
   add these fields to that video's entry:
   - "motivation_overlap": ["short phrase from context that connects"]
   - "motivation_detail": "2-3 sentences describing specifically how the video
     addresses the matching motivation (which timestamps, which examples, which claims)"

   Do NOT remove detail from videos that don't match viewer context. Do NOT use
   this context to judge, rank, filter, or downgrade any video. Extraction is
   equal for all videos; overlap videos get ADDITIONAL fields, never reduced
   ones.
   EOF
   )
   else
     MOT_BLOCK=""
   fi
   ```

   Then compose and send the prompt:
   ```bash
   PROMPT=$(cat <<EOF
   Each source in this notebook is a YouTube video. For each one, return an objective content description.

   Return JSON: [
     {
       "url",
       "title",
       "channel",
       "duration",
       "what_it_covers": "2-3 sentences describing the topic and main claims",
       "style": "tutorial | talk | demo | news | listicle | interview",
       "level": "beginner | intermediate | advanced",
       "concrete_examples": true,
       "key_speakers_or_companies": ["..."]
     }
   ]
   ${MOT_BLOCK}

   Do NOT judge whether a video is good, suitable, relevant, or recommended. Do NOT rank or sort. Describe what the video contains and stop. Return ONLY the JSON array, no prose before or after.
   EOF
   )

   notebooklm ask "$PROMPT" -n "$NB_ID"
   ```

   `motivation_overlap` / `motivation_detail` are additive; they appear only on videos whose content touches the viewer context. Non-overlap videos retain the full baseline schema. `scripts/parse-nblm.py` preserves unknown fields through its `dict(nblm_entry)` copy (line 165), so these fields survive parse into Step 6.

   Capture the raw answer into a variable `NBLM_RAW`.

6. **K2B-as-judge -- Claude reasoning inline (NOT a bash invocation).** This step is the Claude session running the skill applying judgment to the NBLM descriptions and writing `$SUITABLE_JSON`. Execute the following substeps explicitly:

   a. **Parse the NBLM answer defensively** via the committed helper `scripts/parse-nblm.py`. The helper encapsulates three defensive passes that a prior incident (2026-04-15) hit in a single run, so this step no longer describes inline Python: (1) citation-marker stripping with a regex that handles comma lists AND dash ranges (`[44, 47]`, `[1-4]`, mixed `[13-16, 17-20]`; the old inline regex was comma-only and silently let dash markers survive into `json.loads`), (2) literal-newline normalization inside JSON string literals via a character walker that tracks `in_string` state (NBLM sometimes wraps long `what_it_covers` values with raw `\n` bytes which `json.loads` rejects as invalid control characters), and (3) rejoin-by-title against `$CANDIDATES` so every entry carries authoritative `real_url`, `real_title`, `real_channel`, `real_duration`, `real_published`, and `video_id` from yt-search instead of NBLM's synthetic `v=<name>` placeholders.

   Run the helper via:
   ```bash
   NBLM_RAW_FILE=$(mktemp -t k2b-nblm-raw.XXXXXX.txt)
   PARSED_JSON=$(mktemp -t k2b-nblm-parsed.XXXXXX.json)
   NBLM_PARSE_ERR=$(mktemp -t k2b-nblm-err.XXXXXX.log)
   trap 'rm -f "$CANDIDATES" "$READY_LOG" "$CANONICAL_JSON" "$NBLM_RAW_FILE" "$PARSED_JSON" "$NBLM_PARSE_ERR"' EXIT
   printf '%s' "$NBLM_RAW" > "$NBLM_RAW_FILE"

   if ! python3 ~/Projects/K2B/scripts/parse-nblm.py "$NBLM_RAW_FILE" "$CANDIDATES" > "$PARSED_JSON" 2>"$NBLM_PARSE_ERR"; then
     # Parse failed after all defensive passes in parse-nblm.py. Write the raw
     # NBLM answer + the parse-nblm.py stderr to raw/research/ as the durable
     # audit trail BEFORE aborting, then send Telegram, delete the notebook,
     # exit 1. The audit trail is most valuable on failure, so it is written
     # first and always.
     #
     # Not implemented here (deliberate): an NBLM ask retry with a stricter
     # prompt. parse-nblm.py already encapsulates every defensive pass we
     # know about, so a failure here almost always means NBLM returned
     # genuinely malformed output (safety refusal, API error, empty body)
     # that a retry would not fix. If you see repeat failures in production,
     # harden parse-nblm.py rather than adding a retry loop around it.
     echo "FATAL: parse-nblm.py could not normalize NBLM output for: $QUERY" >&2
     cat "$NBLM_PARSE_ERR" >&2

     QUERY="$QUERY" QUERY_SLUG="$QUERY_SLUG" NBLM_RAW="$NBLM_RAW" \
       NBLM_PARSE_ERR_FILE="$NBLM_PARSE_ERR" python3 - <<'PYEOF'
import os, datetime
vault = os.path.expanduser("~/Projects/K2B-Vault")
slug  = os.environ.get("QUERY_SLUG") or "unknown"
query = os.environ.get("QUERY") or slug
query_yaml = query.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
today = datetime.date.today().isoformat()
path  = os.path.join(vault, "raw", "research", f"{today}_videos_{slug}.md")
raw   = os.environ.get("NBLM_RAW") or "(NBLM_RAW not captured)"
err_path = os.environ.get("NBLM_PARSE_ERR_FILE") or ""
try:
    err = open(err_path).read() if err_path else ""
except OSError:
    err = ""
with open(path, "w") as f:
    f.write(
        f'---\ntype: research-run\nstatus: nblm-parse-failed\nquery: "{query_yaml}"\n---\n\n'
        f'# NBLM parse failed\n\n## parse-nblm.py stderr\n\n```\n{err}\n```\n\n'
        f'## Raw NBLM answer\n\n```\n{raw}\n```\n'
    )
PYEOF

     ~/Projects/K2B/scripts/send-telegram.sh "K2B /research videos: NBLM parse failed for: $QUERY -- see run record" || true
     notebooklm delete -n "$NB_ID" -y >/dev/null 2>&1 || true
     exit 1
   fi
   ```

   The helper outputs a JSON array where each entry carries both the NBLM content fields (`what_it_covers`, `style`, `level`, `concrete_examples`, `key_speakers_or_companies`) AND the candidate-sourced identity fields. Entries where title-rejoin failed appear with `identity_resolved: false`, `match_method: "failed"`, `video_id: ""`, and `real_published: "unknown"` -- **these are NOT dropped silently**; Step 6d must sort them into `rejects[]` with `reason: "identity resolution failed"`. `real_published` is pre-normalized by the helper to `YYYY-MM-DD` or the literal `"unknown"`, and the jq schema gate in Step 6g accepts both forms. The recency veto in Step 6d simply skips candidates with `"unknown"` (neither fresh nor stale).

   b. **Load Keith's framing from the skill header, explicitly.** Read the block at the "### Baked Keith framing (do not edit per query)" callout above (lines 262-264 of this SKILL.md). The Claude session reads that block directly from the skill file -- single source of truth, explicit load, no implicit "scroll up and remember". Do NOT re-inline the framing text into the Step 5 NBLM prompt; Gemini does not get taste context.

   c. **Load the preference tail AND reload active motivations** (point-in-time snapshots, both re-read fresh here because Step 6 runs in Claude reasoning, not in the Step 5 shell):
   ```bash
   PREF_TAIL=$(tail -n 30 ~/Projects/K2B-Vault/wiki/context/video-preferences.md)
   MOTIVATIONS=""
   if [[ "${K2B_MOTIVATIONS_ENABLED:-true}" == "true" ]]; then
     MOTIVATIONS=$(~/Projects/K2B/scripts/motivations-helper.sh read 2>/dev/null || true)
   fi
   ```

   `$MOTIVATIONS` here must be reloaded even though Step 5 also loaded it -- Step 5's bash scope does not survive into the Claude-side judgment. When non-empty, it represents Keith's current active projects (Building section) and self-added learning questions, available to K2B-the-judge for the match-bonus rule in (d). When empty (rollback toggle off or helper returns nothing), the judgment rubric below runs identically to the pre-feature flow. Step 5 and Step 6 must use the SAME motivations text for a given run; do not re-run the helper between them in a way that could produce different output (the helper is deterministic given unchanged vault state, so back-to-back reads are safe).

   d. **Apply the judgment rubric** to each parsed NBLM entry:
   - A candidate is a **pick** only if K2B can name a *specific* reason Keith will finish it through one of these lenses: concrete-examples, 90-day-deployable, senior-TA-in-traditional-corporate.
   - Any line in the preference tail that contradicts a candidate (disliked channel, disliked style, level mismatch) is a **veto** -- it goes into `rejects`.
   - **Recency veto.** If the topic moves fast (new model capabilities, tool releases, market news, "what's new in X") AND `real_published` is more than 6 months older than the run date (i.e. `today - real_published > 180 days`), the candidate goes into `rejects` with `reason: "outdated: published <date>, topic moves faster than that"`. Evergreen topics (mindset, frameworks, interview patterns, management principles) are NOT subject to this veto. K2B decides per candidate whether the topic is fast-moving. The run date anchor is always "today" at run time, never hardcoded, so the veto window drifts with the calendar.
   - **Motivation match bonus.** When a candidate's `motivation_overlap` (populated by NBLM in Step 5) intersects a Building item or Active Question in `$MOTIVATIONS`, treat that as ADDITIONAL evidence toward pick. This bonus DOES NOT bypass the quality bar -- candidates must still satisfy the concrete-examples / 90-day-deployable / senior-TA-in-traditional-corporate lenses to be picked. Motivation alignment alone is not enough.
   - **`why_k2b` enrichment.** When a pick has a non-empty `motivation_overlap` array, `why_k2b` MUST include a motivation-aware sentence of the form: "Connects to [Building item or Active Question]: [how, referencing motivation_detail when present]". When `motivation_overlap` is empty or absent, `why_k2b` stays as-is (no mention of motivations; do not fabricate a connection).
   - Fewer than 5 clear winners → pick fewer. **Pad-to-target is forbidden.**
   - **Zero picks is allowed and explicit.** If nothing clears the bar, emit `picks: []`.
   - **Cap is 5.** Even if 10 candidates look good, pick the top 5 by confidence.
   - For each pick, K2B chooses a `suggested_category` from the playlist map (see below). If nothing fits, K2B may suggest a new category name -- that pick is flagged for Keith's approval in the run note and `/review` leaves it `playlist_action: pending` with `new category suggested: <name>` appended to `notes:`.

   e. **Write `$SUITABLE_JSON` with this strict shape.** Every field is mandatory.
   ```json
   {
     "picks": [
       {
         "pick_id": "2026-04-14-<query-slug>-01",
         "video_id": "...",
         "real_url": "...",
         "real_title": "...",
         "real_channel": "...",
         "real_duration": "...",
         "real_published": "2026-03-20",
         "why_k2b": "2-3 sentences in K2B's voice: what the video covers, why it matches Keith's lens, which preference-tail lines it respects or avoids. Mention age only when relevant (e.g. 'from 3 weeks ago, so tool versions still current')",
         "suggested_category": "K2B Claude",
         "confidence": 0.85,
         "preference_evidence": ["2026-04-13 liked: Matt Wolfe -- clear concrete examples"]
       }
     ],
     "rejects": [
       {
         "real_title": "...",
         "real_channel": "...",
         "reason": "one sentence in K2B's voice"
       }
     ]
   }
   ```

   **Hard rules K2B MUST follow when building this JSON:**
   - `picks` sorted by `confidence` descending.
   - Every pick has `confidence` in `[0.0, 1.0]`. If K2B cannot confidently rate a candidate, it belongs in `rejects`, not `picks`.
   - Every pick has `preference_evidence: []` -- a list of zero or more verbatim lines from `$PREF_TAIL` that justified the decision. Empty list is allowed; the field must exist.
   - `rejects` MUST include every non-pick candidate parsed from the NBLM answer. Silent drops are forbidden. `len(picks) + len(rejects)` must equal the count of parsed NBLM entries.
   - `pick_id` format: `YYYY-MM-DD-<query-slug>-NN` (1-indexed, zero-padded NN, stable across the run).
   - `suggested_category` must be one of the names in `scripts/k2b-playlists.json` OR a new name clearly flagged by K2B for Keith's approval.

   f. **Allocate the tmpfile, extend the trap, and write the JSON.** The Claude session running this skill executes these steps explicitly via its Bash and Write tools (the session IS the judge, so the "write" is not a subagent hand-off -- it is Claude directly emitting the validated object to the file):

   ```bash
   SUITABLE_JSON=$(mktemp -t k2b-suitable.XXXXXX.json)
   trap 'rm -f "$CANDIDATES" "$READY_LOG" "$CANONICAL_JSON" "$NBLM_RAW_FILE" "$PARSED_JSON" "$NBLM_PARSE_ERR" "$SUITABLE_JSON"' EXIT
   echo "$SUITABLE_JSON"   # print the path so Claude can reference it explicitly
   ```

   Then Claude writes the `{picks, rejects}` JSON object to that exact path via the Write tool (preferred for JSON correctness) OR via an atomic heredoc:
   ```bash
   cat > "$SUITABLE_JSON" <<'JSON_EOF'
   { "picks": [...], "rejects": [...] }
   JSON_EOF
   ```
   Do NOT use `jq -n --argjson` with shell-interpolated pick data -- the JSON is too structured and too long, and shell metacharacters in `why_k2b` / `preference_evidence` will corrupt it. The Write tool or a quoted heredoc is the only safe path.

   After the write, verify the file is non-empty and parseable as JSON before proceeding:
   ```bash
   [[ -s "$SUITABLE_JSON" ]] || { echo "FATAL: $SUITABLE_JSON is empty" >&2; exit 1; }
   jq empty "$SUITABLE_JSON" || { echo "FATAL: $SUITABLE_JSON is not valid JSON" >&2; exit 1; }
   ```

   g. **Schema validation gate (mandatory before Step 7).** Validates BOTH `picks[]` and `rejects[]` object shapes. Identity fields are checked with `type == "string"` AND `length > 0` so an empty-string `video_id` cannot slip past a bare `has(...)` check and hit `yt-playlist-add.sh` with a blank ID. On failure, log the raw NBLM answer to the run record, Telegram abort, delete notebook, exit 1.
   ```bash
   jq -e '
     (type == "object") and
     (has("picks") and (.picks | type == "array")) and
     (has("rejects") and (.rejects | type == "array")) and
     (.picks | all(
       (has("pick_id")) and ((.pick_id | type) == "string") and ((.pick_id | length) > 0) and
       (has("video_id")) and ((.video_id | type) == "string") and ((.video_id | length) > 0) and
       (has("real_url")) and ((.real_url | type) == "string") and ((.real_url | length) > 0) and
       (has("real_title")) and ((.real_title | type) == "string") and ((.real_title | length) > 0) and
       (has("real_channel")) and ((.real_channel | type) == "string") and ((.real_channel | length) > 0) and
       (has("real_duration")) and ((.real_duration | type) == "string") and
       (has("real_published")) and ((.real_published | type) == "string") and
       ((.real_published | test("^[0-9]{4}-[0-9]{2}-[0-9]{2}")) or (.real_published == "unknown")) and
       (has("why_k2b")) and ((.why_k2b | type) == "string") and ((.why_k2b | length) > 0) and
       (has("suggested_category")) and ((.suggested_category | type) == "string") and ((.suggested_category | length) > 0) and
       (has("confidence")) and ((.confidence | type) == "number") and (.confidence >= 0) and (.confidence <= 1) and
       (has("preference_evidence")) and ((.preference_evidence | type) == "array")
     )) and
     (.rejects | all(
       (has("real_title")) and ((.real_title | type) == "string") and ((.real_title | length) > 0) and
       (has("real_channel")) and ((.real_channel | type) == "string") and ((.real_channel | length) > 0) and
       (has("reason")) and ((.reason | type) == "string") and ((.reason | length) > 0)
     ))
   ' "$SUITABLE_JSON" >/dev/null || {
     # Write raw NBLM answer to partial run record FIRST -- the audit trail is most valuable on failure.
     # Env vars are passed explicitly on the python3 line because QUERY / QUERY_SLUG / NBLM_RAW are set
     # earlier in the skill without `export`, so a child process won't inherit them otherwise.
     QUERY="$QUERY" QUERY_SLUG="$QUERY_SLUG" NBLM_RAW="$NBLM_RAW" python3 - <<'PYEOF'
import os, datetime
vault = os.path.expanduser("~/Projects/K2B-Vault")
slug  = os.environ.get("QUERY_SLUG") or "unknown"
query = os.environ.get("QUERY") or slug   # real query for the YAML frontmatter; fall back to slug if unset
query_yaml = query.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
today = datetime.date.today().isoformat()
path  = os.path.join(vault, "raw", "research", f"{today}_videos_{slug}.md")
raw   = os.environ.get("NBLM_RAW") or "(NBLM_RAW not captured)"
with open(path, "w") as f:
    f.write(f'---\ntype: research-run\nstatus: schema-validation-failed\nquery: "{query_yaml}"\n---\n\n# Schema validation failed\n\n## Raw NBLM answer\n\n```\n{raw}\n```\n')
PYEOF
     ~/Projects/K2B/scripts/send-telegram.sh "K2B /research videos: schema validation failed for: $QUERY -- see run record"
     notebooklm delete -n "$NB_ID" -y >/dev/null 2>&1 || true
     exit 1
   }
   ```

6.5. **Per-pick deep-extract via second NBLM ask (enrichment, not gating).** Picks-only follow-up that returns rich per-pick detail Keith can use to triage long-form content (founder interviews, podcasts, talks where 2-3 sentences cannot answer "is this worth my hour"). Skips when picks are empty. Per-pick fallback to thin format on any extraction failure -- the run continues regardless. Notebook is still alive at this step (Step 11 deletes it later).

   ```bash
   PICK_DETAILS_JSON=$(mktemp -t k2b-pick-details.XXXXXX.json)
   PICK_DETAILS_RAW_FILE=$(mktemp -t k2b-pick-details-raw.XXXXXX.txt)
   PICK_DETAILS_ERR=$(mktemp -t k2b-pick-details-err.XXXXXX.log)
   trap 'rm -f "$CANDIDATES" "$READY_LOG" "$CANONICAL_JSON" "$NBLM_RAW_FILE" "$PARSED_JSON" "$NBLM_PARSE_ERR" "$SUITABLE_JSON" "$PICK_DETAILS_JSON" "$PICK_DETAILS_RAW_FILE" "$PICK_DETAILS_ERR"' EXIT

   PICKS_COUNT=$(jq '.picks | length' "$SUITABLE_JSON")
   DEEPEXTRACT_STATUS="skipped-no-picks"   # default; reassigned below if picks > 0
   echo "[]" > "$PICK_DETAILS_JSON"        # safe default so Step 8 lookups never fail on parse

   if (( PICKS_COUNT > 0 )); then
     PICK_URLS_JSON=$(jq -c '[.picks[] | {url: .real_url, title: .real_title, duration: .real_duration}]' "$SUITABLE_JSON")

     DEEP_PROMPT=$(cat <<DEEP_PROMPT_EOF
   For each video in this notebook listed below (matched by url), return a JSON object with the EXACT shape below. Do NOT judge, rank, or filter. Do NOT add or omit fields. Return ONLY a JSON array, no prose before or after.

   Videos to extract (by url, title, duration):
   ${PICK_URLS_JSON}

   Schema per entry:
   [
     {
       "url": "<verbatim from input>",
       "summary_paragraph": "5-7 sentences describing the video's arc, main argument, and what kind of viewer would benefit. Plain English, no marketing language.",
       "key_claims": [
         {
           "claim": "specific assertion in the speaker's voice (paraphrase ok)",
           "evidence_or_example": "what they cite -- a number, a story, a demo, a comparison, a name. If the claim is asserted without backing, write the literal string 'asserted without evidence'",
           "timestamp_approx": "MM:SS if NBLM has timing data; otherwise one of: 'early' | 'mid' | 'late'"
         }
       ],
       "concrete_numbers": [
         "verbatim numbers cited with WHAT they refer to and WHO claimed them, e.g. '\$1.5M ARR in 30 days, Polsia (Ben Cera)'"
       ],
       "named_entities": {
         "people": ["full names with role if stated"],
         "companies": ["company names"],
         "tools": ["specific products / frameworks / platforms mentioned"]
       },
       "watch_priority": "skim_5min | watch_30min | watch_full",
       "skim_pitch": "If you only have 5-10 minutes, watch [timestamp range or segment] for [specific reason]. If the video has no 5-min worth-watching segment, write the literal string 'skip -- substance distributed across full length, no single skim segment'",
       "red_flags": [
         "specific weakness in the content -- e.g. 'round-numbers framing without unit economics', 'cites investors but not customers', 'product demo only, no architecture'. Empty array means K2B sees no red flags worth flagging."
       ]
     }
   ]

   Return 5-12 key_claims per video depending on density. Empty arrays where genuinely empty. Return ONLY the JSON array.
   DEEP_PROMPT_EOF
   )

     # NBLM ask. Capture raw output to file (not via $() to avoid $IFS truncation on long answers).
     # One retry with 10s backoff on transient failures (rate limit / network / 503).
     NBLM_ASK_OK=false
     for ATTEMPT in 1 2; do
       if notebooklm ask "$DEEP_PROMPT" -n "$NB_ID" >"$PICK_DETAILS_RAW_FILE" 2>"$PICK_DETAILS_ERR"; then
         NBLM_ASK_OK=true
         break
       fi
       if (( ATTEMPT == 1 )); then
         echo "WARN: NBLM ask attempt 1 failed for pick deep-extract; retrying in 10s. See $PICK_DETAILS_ERR" >&2
         sleep 10
       fi
     done
     if [[ "$NBLM_ASK_OK" != "true" ]]; then
       DEEPEXTRACT_STATUS="ask-failed"
       echo "WARN: NBLM ask failed for pick deep-extract (2 attempts) -- falling back to thin format. See $PICK_DETAILS_ERR" >&2
     fi

     # Defensive parse: strip NBLM citation markers, repair literal-newlines inside string values, json.loads.
     # Inline rather than reusing parse-nblm.py because that helper is hardcoded to Step 5's title-rejoin
     # contract against $CANDIDATES which we don't need here.
     if [[ "$DEEPEXTRACT_STATUS" == "skipped-no-picks" ]]; then   # not yet reassigned; ask succeeded
       if PICK_DETAILS_RAW_FILE="$PICK_DETAILS_RAW_FILE" python3 - >"$PICK_DETAILS_JSON" 2>>"$PICK_DETAILS_ERR" <<'PYEOF'
   import json, os, re, sys
   raw = open(os.environ["PICK_DETAILS_RAW_FILE"]).read()
   # Strip NBLM citation markers like [1], [1, 4], [1-4], [13-16, 17-20].
   stripped = re.sub(r'\[\s*\d+(?:\s*[-,]\s*\d+)*(?:\s*,\s*\d+(?:\s*[-,]\s*\d+)*)*\s*\]', '', raw)
   # Strip trailing commas before } and ] -- NBLM occasionally emits them and json.loads rejects.
   stripped = re.sub(r',(\s*[}\]])', r'\1', stripped)
   m = re.search(r'\[.*\]', stripped, re.DOTALL)
   if not m:
     print("[]"); sys.exit(0)
   try:
     data = json.loads(m.group(0))
   except json.JSONDecodeError:
     # Repair pass: literal \n inside JSON string literals trips json.loads.
     candidate = m.group(0)
     out, in_str, esc = [], False, False
     for ch in candidate:
       if esc:
         out.append(ch); esc = False; continue
       if ch == '\\':
         out.append(ch); esc = True; continue
       if ch == '"':
         in_str = not in_str
       if ch in ('\n', '\r') and in_str:
         out.append('\\n' if ch == '\n' else '\\r'); continue
       out.append(ch)
     try:
       data = json.loads(''.join(out))
     except json.JSONDecodeError:
       print("[]"); sys.exit(0)
   if not isinstance(data, list):
     print("[]"); sys.exit(0)
   print(json.dumps(data))
   PYEOF
       then
         PARSED_COUNT=$(jq 'length' "$PICK_DETAILS_JSON" 2>/dev/null || echo "0")
         # Per-entry schema check: required strings non-empty, required arrays/object present.
         GOOD_COUNT=$(jq '[.[] | select(
           (.url | type == "string") and ((.url | length) > 0) and
           (.summary_paragraph | type == "string") and ((.summary_paragraph | length) > 0) and
           (.watch_priority | type == "string") and ((.watch_priority | length) > 0) and
           ((.watch_priority == "skim_5min") or (.watch_priority == "watch_30min") or (.watch_priority == "watch_full")) and
           (.skim_pitch | type == "string") and ((.skim_pitch | length) > 0) and
           (.key_claims | type == "array") and
           (.concrete_numbers | type == "array") and
           (.red_flags | type == "array") and
           (.named_entities | type == "object") and
           ((.named_entities.people // []) | type == "array") and
           ((.named_entities.companies // []) | type == "array") and
           ((.named_entities.tools // []) | type == "array")
         )] | length' "$PICK_DETAILS_JSON" 2>/dev/null || echo "0")

         # URL-match check: NBLM is known (from Step 5 / parse-nblm.py history) to occasionally return
         # synthetic v=<name> URLs instead of the real ones. If url keys don't match picks' real_url,
         # every Step 8 lookup returns empty and the feature silently degrades to thin for all picks
         # while DEEPEXTRACT_STATUS still reports "ok" (because parse + schema both passed). Detect
         # this distinct mode so the run record + Telegram diagnostics show what happened.
         URL_MATCH_COUNT=$(jq -r --slurpfile picks "$SUITABLE_JSON" '
           [.[] | .url] as $detail_urls
           | $picks[0].picks
           | [.[] | select(.real_url as $u | $detail_urls | index($u))] | length
         ' "$PICK_DETAILS_JSON" 2>/dev/null || echo "0")

         if (( GOOD_COUNT == PICKS_COUNT && URL_MATCH_COUNT == PICKS_COUNT )); then
           DEEPEXTRACT_STATUS="ok"
         elif (( URL_MATCH_COUNT == 0 && GOOD_COUNT > 0 )); then
           DEEPEXTRACT_STATUS="url-mismatch"
           echo "WARN: NBLM returned $GOOD_COUNT schema-valid entries but ZERO URLs match picks' real_url. Synthetic-URL pattern likely. All picks rendered thin." >&2
         elif (( URL_MATCH_COUNT < PICKS_COUNT )); then
           DEEPEXTRACT_STATUS="partial-url-mismatch"
           echo "WARN: $URL_MATCH_COUNT of $PICKS_COUNT picks have URL match in PICK_DETAILS_JSON; remaining picks render thin" >&2
         elif (( GOOD_COUNT > 0 )); then
           DEEPEXTRACT_STATUS="partial"
           echo "WARN: pick deep-extract returned $GOOD_COUNT schema-valid entries for $PICKS_COUNT picks -- mixing rich + thin per pick" >&2
         else
           DEEPEXTRACT_STATUS="schema-failed"
           echo "WARN: pick deep-extract returned 0 schema-valid entries -- falling back to thin format for all picks" >&2
         fi
       else
         DEEPEXTRACT_STATUS="parse-failed"
         echo "WARN: pick deep-extract parse failed -- falling back to thin format. See $PICK_DETAILS_ERR" >&2
       fi
     fi
   fi

   # Step 8 looks up each pick's url in $PICK_DETAILS_JSON. Per-pick fallback to thin when no match
   # or when entry fails schema check. Step 9 records $DEEPEXTRACT_STATUS in the run record for audit.
   ```

   This step is enrichment. Step 7 onward proceeds regardless of `$DEEPEXTRACT_STATUS`. Status values:
   - `ok` -- all picks have valid rich detail AND every entry's `url` matches a pick's `real_url`; Step 8 renders rich for all.
   - `partial` -- some picks got valid rich detail (schema), others did not, but URL matches were complete for the schema-good entries; Step 8 renders rich where schema-good, thin otherwise.
   - `partial-url-mismatch` -- schema all valid, but some entries' `url` failed to match any pick's `real_url` (likely synthetic-URL pattern from NBLM); Step 8 renders rich only for the URL-matched picks, thin for the rest.
   - `url-mismatch` -- schema all valid, but ZERO entries' `url` matched any pick's `real_url`. NBLM returned synthetic URLs for the entire batch; Step 8 renders thin for all.
   - `schema-failed`, `parse-failed`, `ask-failed` -- nothing usable; Step 8 renders thin for all.
   - `skipped-no-picks` -- 0 picks; Step 8 still writes the empty-picks note.

   The two URL-mismatch values are distinct from `schema-failed` so the run record + future `/observe` analysis can tell "NBLM gave us garbage" apart from "NBLM gave us good shapes but referenced things we can't match." If `url-mismatch` shows up repeatedly across runs, the fix is a title-rejoin pass on `$PICK_DETAILS_JSON` (mirrors what `parse-nblm.py` does for Step 5) -- not a retry on the ask.

7. **For each `.picks[]` entry, add to K2B Watch.** Playlist ID comes from the canonical map, never hardcoded:
   ```bash
   K2B_WATCH_ID=$(jq -r '."K2B Watch"' ~/Projects/K2B/scripts/k2b-playlists.json)
   jq -c '.picks[]' "$SUITABLE_JSON" | while IFS= read -r PICK; do
     VIDEO_ID=$(printf '%s' "$PICK" | jq -r '.video_id')
     ~/Projects/K2B/scripts/yt-playlist-add.sh "$K2B_WATCH_ID" "$VIDEO_ID" \
       || echo "WARN: playlist add failed for $VIDEO_ID" >&2
   done
   ```

8. **Write one run-level review note.** Replaces the old per-video template. Path:
   ```
   K2B-Vault/review/videos_$(date +%F)_${QUERY_SLUG}.md
   ```
   Use an atomic write-then-rename so `/review` and Telegram feedback paths can never observe a half-written file:
   ```bash
   RUN_NOTE="$K2B_VAULT/review/videos_$(date +%F)_${QUERY_SLUG}.md"
   RUN_NOTE_TMP="$K2B_VAULT/review/.videos_$(date +%F)_${QUERY_SLUG}.md.tmp.$$"
   # ... build the rendered content into $RUN_NOTE_TMP via cat/python/jq ...
   mv "$RUN_NOTE_TMP" "$RUN_NOTE"
   ```

   **Per-pick rich-detail lookup.** For each pick, look up its `real_url` in `$PICK_DETAILS_JSON` (populated by Step 6.5). When the lookup returns a non-empty entry that passes the per-entry schema check, render the **rich** template. Otherwise render the **thin** template. This works at any `$DEEPEXTRACT_STATUS` value: `ok` → all picks rich; `partial` → some rich some thin; `failed` / `skipped-no-picks` → all thin.

   ```bash
   # Per pick (inside the picks render loop):
   PICK_DETAIL=$(jq --arg url "$REAL_URL" '.[] | select(.url == $url)' "$PICK_DETAILS_JSON" 2>/dev/null)
   if [[ -n "$PICK_DETAIL" ]] && \
      printf '%s' "$PICK_DETAIL" | jq -e '
        (.summary_paragraph | type == "string") and ((.summary_paragraph | length) > 0) and
        (.watch_priority | type == "string") and ((.watch_priority | length) > 0) and
        ((.watch_priority == "skim_5min") or (.watch_priority == "watch_30min") or (.watch_priority == "watch_full")) and
        (.skim_pitch | type == "string") and ((.skim_pitch | length) > 0) and
        (.key_claims | type == "array") and
        (.concrete_numbers | type == "array") and
        (.red_flags | type == "array") and
        (.named_entities | type == "object") and
        ((.named_entities.people // []) | type == "array") and
        ((.named_entities.companies // []) | type == "array") and
        ((.named_entities.tools // []) | type == "array")
      ' >/dev/null; then
     RENDER_MODE="rich"
   else
     RENDER_MODE="thin"
   fi
   ```

   **Thin template** (current behavior, unchanged):

   ````markdown
   ### N. <real_title>

   <why_k2b prose -- this is what Keith reads>

   - **url:** <real_url>
   - **channel:** <real_channel>  ·  **duration:** <real_duration>  ·  **published:** <real_published>

   ```yaml
   pick_id: 2026-04-14-<query-slug>-NN
   video_id: <video_id>
   real_url: <real_url>
   real_title: "<real_title>"
   real_channel: "<real_channel>"
   real_published: "<real_published>"
   suggested_category: K2B Claude
   category_override: ""
   decision: pending          # keith: keep | drop | neutral
   playlist_action: pending   # pending | done | failed
   preference_logged: false
   processed_at: null
   notes: ""
   ```
   ````

   **Rich template** (when `$RENDER_MODE == "rich"`). Adds prose sections above the YAML fence and a `details:` subkey inside the YAML; existing fields unchanged so `/review` and the Telegram feedback parse contract still holds:

   ````markdown
   ### N. <real_title>

   <why_k2b prose -- this is what Keith reads>

   **Summary:** <summary_paragraph from $PICK_DETAIL>

   **Key claims:**
   - [<timestamp_approx>] <claim> -- <evidence_or_example>
   - ... (5-12 bullets, one per key_claims entry)

   **Specific numbers cited:** *(omit this section entirely when $PICK_DETAIL.concrete_numbers is empty)*
   - <concrete_numbers item>
   - ...

   **People:** <comma-joined named_entities.people>  ·  **Companies:** <comma-joined named_entities.companies>  ·  **Tools:** <comma-joined named_entities.tools>

   **Watch priority:** <watch_priority>  ·  **Skim pitch:** <skim_pitch>

   **Red flags K2B noticed:** *(omit this section entirely when $PICK_DETAIL.red_flags is empty)*
   - <flag>
   - ...

   - **url:** <real_url>
   - **channel:** <real_channel>  ·  **duration:** <real_duration>  ·  **published:** <real_published>

   ```yaml
   pick_id: 2026-04-14-<query-slug>-NN
   video_id: <video_id>
   real_url: <real_url>
   real_title: "<real_title>"
   real_channel: "<real_channel>"
   real_published: "<real_published>"
   suggested_category: K2B Claude
   category_override: ""
   decision: pending          # keith: keep | drop | neutral
   playlist_action: pending   # pending | done | failed
   preference_logged: false
   processed_at: null
   notes: ""
   details:
     watch_priority: <watch_priority>
   ```
   ````

   **YAML safety inside `details:`.** Only `watch_priority` lives in YAML, and ONLY when it matches one of the three known enum values (`skim_5min`, `watch_30min`, `watch_full`). NBLM-returned strings (`named_entities.*`, `summary_paragraph`, `skim_pitch`, individual `key_claims[].claim` text) all live in the **prose** above the YAML fence -- never in the YAML -- because they routinely contain colons, double-quotes, brackets, and literal newlines that break PyYAML parsing in `/review` and the Telegram feedback path. Validate `watch_priority` against the enum before writing; on miss, omit the `details:` subkey entirely and treat the pick as thin even though prose details are present. The YAML stays the sole machine-parsed surface; the prose carries the human-readable depth.

   **Full template skeleton:**

   ````markdown
   ---
   type: video-run
   review-action: pending
   review-notes: ""
   query: "<QUERY>"
   run-date: <YYYY-MM-DD>
   picks-count: <N>
   deepextract-status: <DEEPEXTRACT_STATUS>      # ok | partial | partial-url-mismatch | url-mismatch | schema-failed | parse-failed | ask-failed | skipped-no-picks
   watch-playlist: K2B Watch
   up: "[[index]]"
   ---

   # Videos for: <QUERY>

   **Run:** <YYYY-MM-DD> · **Candidates screened:** <CANDIDATES_SCREENED> · **K2B picked:** <N>

   ## K2B's picks

   <one block per pick using rich or thin template per $RENDER_MODE>

   ## Candidates K2B rejected (<N_rejected>)

   - <real_title> · <real_channel> -- <reason>
   - <real_title> · <real_channel> -- <reason>
   - ... one bullet per rejected video, no exceptions
   ````

   The `real_url`, `real_title`, `real_channel`, and `real_published` fields are duplicated from `$SUITABLE_JSON` into the YAML block so `/review` and the Telegram feedback path can match picks and distill preference lines without parsing the prose above the fence. This keeps the "YAML block is the sole parse surface" contract clean. `real_published` is sourced from yt-search, never NBLM.

   **Rejects MUST NOT be compressed.** Every reject from `$SUITABLE_JSON.rejects[]` gets its own bullet with its own one-line reason. Do NOT lump multiple rejects into meta-summaries like "15 Chinese drama videos · various channels -- entertainment content" even when many rejects share a category. The per-video reason is what `/review` and `/observe` learn from; lumping silently destroys that signal. `len(reject bullets in note) == len($SUITABLE_JSON.rejects[])`. If 21 videos were rejected, write 21 bullets.

   **Parsing contract.** The `/review` handler and the Telegram feedback path parse ONLY the fenced ` ```yaml ... ``` ` block after each `### N. ` heading (via PyYAML). The prose above the YAML fence is for Keith to read, never parsed. Keith edits only `decision:`, `category_override:`, and `notes:` -- everything else is K2B-managed state. The new `details:` subkey under YAML is K2B-managed too.

   **N=0 still writes the note.** The note exists with an empty picks section and the full rejects list. Audit trail is mandatory even when nothing cleared the bar.

9. **Write run record** at `K2B-Vault/raw/research/$(date +%F)_videos_${QUERY_SLUG}.md`. Sections:
   - Frontmatter: `type: research-run`, `query`, `candidates_screened: <count from $CANDIDATES, never hardcoded>`, `picks-count`, `outcome: zero-picks | has-picks`, `deepextract_status: <DEEPEXTRACT_STATUS>`.
   - Query + baked framing version in use.
   - Preference tail snapshot that K2B saw (the literal `$PREF_TAIL` value).
   - **Picks-mode runs (`outcome: has-picks`):** the NBLM `what_it_covers` descriptions for picks only, plus K2B's `why_k2b` reasoning per pick, plus the rejects list (title · channel · one-line reason).
   - **Zero-picks runs (`outcome: zero-picks`):** include ALL NBLM descriptions (all 25, or however many were parsed) so the "why nothing was worth watching" evidence trail is durable. Zero-picks runs are exactly the ones Keith will want to audit later.
   - **Pick deep-extract section (Step 6.5 output).** When `$DEEPEXTRACT_STATUS == "ok"` or `"partial"`, append the entire `$PICK_DETAILS_JSON` content as a fenced JSON block under a `## Pick deep-extract (NBLM)` heading. When status is `schema-failed`, `parse-failed`, or `ask-failed`, append the raw NBLM response from `$PICK_DETAILS_RAW_FILE` AND the `$PICK_DETAILS_ERR` log under `## Pick deep-extract (failed)` so the failure is diagnosable later. When status is `skipped-no-picks`, write a one-line note "No picks, deep-extract skipped".
   - Ready/fail log from Step 3.
   - Per-video playlist-add results from Step 7.
   - Any Telegram failures from Step 10.

   This is the durable audit trail. It persists after `/review` moves the run note out of `review/`.

10. **Send Telegram notification.** Count picks from `.picks[]` (NOT `READY_COUNT`). Zero picks gets a dedicated message pointing Keith at the run note.
    ```bash
    PICKS_COUNT=$(jq '.picks | length' "$SUITABLE_JSON")

    if (( PICKS_COUNT == 0 )); then
      MSG="K2B: nothing worth watching this week for: $QUERY"$'\n\n'
      MSG+="Candidates screened: $CANDIDATES_SCREENED. K2B picked 0. See the run note for per-candidate reasons."
    else
      MSG="K2B picked $PICKS_COUNT videos for: $QUERY"$'\n\n'
      MSG+=$(jq -r '.picks[] | "- \(.real_title)\n  why: \(.why_k2b)\n  category: \(.suggested_category)\n  \(.real_url)\n"' "$SUITABLE_JSON")
    fi

    if ! ~/Projects/K2B/scripts/send-telegram.sh "$MSG"; then
      echo "WARN: telegram notification failed for query: $QUERY" >&2
      # Continue: notebook delete + run record still need to happen.
    fi
    ```
    `send-telegram.sh` handles the 4096-byte chunking internally; callers do NOT pre-batch. Exit-code check is mandatory.

11. **Delete the notebook** (fresh per run, no accumulation):
    ```bash
    notebooklm delete -n "$NB_ID" -y >/dev/null 2>&1 || true
    ```

12. **Append to skill-usage-log** as usual.

### Scheduling (not a skill concern)

Wrap with `/schedule`:
```
/schedule add "research-videos-ai-recruiting" weekly "/research videos \"AI recruiting tools for large enterprises\""
```
The scheduler runs the command on the Mac Mini weekly. Output lands in the vault via Syncthing. Telegram notification fires from the Mac Mini using `send-telegram.sh`.

### Playlist ID resolution

All playlist IDs come from `~/Projects/K2B/scripts/k2b-playlists.json` -- the canonical name→ID map. Lookup via `jq -r '."<name>"' ~/Projects/K2B/scripts/k2b-playlists.json`. Never hardcode an ID in this skill, `k2b-review/SKILL.md`, or CLAUDE.md. If K2B suggests a category name not present in the JSON (K2B-invented category), the run note still writes the pick but `/review` leaves `playlist_action: pending` with a "new category suggested" note until Keith approves creating the playlist.

### Failure modes

- **NotebookLM ask times out or errors (Step 5):** abort, log to run record, notify Keith "research timed out on: <query>", delete the notebook.
- **NBLM JSON parse fails twice (Step 6a, parse-nblm.py):** log raw answer to run record, notify Keith "NBLM descriptions weren't parseable, see raw/research/", abort downstream steps.
- **K2B schema validation fails on `$SUITABLE_JSON` (Step 6g):** Write partial run record with raw NBLM answer to `raw/research/` first, then Telegram abort, delete notebook, exit 1. This is the Step 6g gate.
- **Pick deep-extract fails (Step 6.5):** ENRICHMENT FAILURE, NOT GATING. Step 7 onward continues. Status recorded in run note frontmatter (`deepextract-status:`) and run record. Per-pick fallback to thin template -- Keith still gets the run note, just without the rich detail for affected picks. The notebook is NOT deleted on this failure (Step 11 handles deletion at end of run regardless).
- **Playlist add fails for a pick:** log per-video in run record, continue with the others, include the failure count in the Telegram summary.
- **Zero picks:** still write the run note + run record + Telegram message ("nothing worth watching this week"), still delete the notebook. N=0 is normal, not an error. Step 6.5 is skipped (`deepextract-status: skipped-no-picks`).

### What NOT to do

- Do NOT cache NotebookLM notebooks across runs. Fresh per run.
- Do NOT dedupe across runs via a URL log. The preference tail handles this naturally once Keith rates videos.
- Do NOT ask Keith to confirm each add. K2B already decided in Step 6. Keith's feedback comes after watching via Telegram reaction or `/review`.
- Do NOT write to `wiki/context/video-preferences.md` from `/research videos`. Only `/review` and the Telegram feedback path append there, and both do so via atomic write-then-rename.
- Do NOT re-inline the baked Keith framing into the Step 5 NBLM prompt. Gemini must not see Keith's taste. Only K2B (Step 6) sees framing + preference tail.
- Do NOT hardcode playlist IDs. Always `jq`-lookup from `scripts/k2b-playlists.json`.
- Do NOT pad picks to 5 when fewer clearly deserve it. Zero is a valid outcome.

## Named notebook library (`/research notebook`) -- added 2026-04-18

Persistent NotebookLM notebooks that survive across sessions. Use this when Keith wants to ask multiple angles against the same source corpus over time (days or weeks) without paying the 2-5 minute re-indexing cost for every new question.

**When to use which NBLM path:**

| Path | Notebook lifecycle | Use case |
|---|---|---|
| `/research videos "<query>"` | **Deleted after one run.** Fresh candidates every run, so re-indexing is unavoidable anyway. | One-shot YouTube filter. |
| `/research deep <topic>` | Persists **unnamed** -- notebook ID only appears in the `raw/research/` note frontmatter. Practical reuse requires digging that ID back out. | One-time multi-source synthesis on a topic Keith won't revisit. |
| `/research notebook <name>` | Persists **named.** Registered in `wiki/context/notebooklm-registry.md` via `scripts/nblm-registry-helper.sh`. Reusable by name from any future session. | Multi-angle research on a topic Keith expects to revisit. |

**Registry ownership** per the K2B memory ownership matrix: `scripts/nblm-registry-helper.sh` is the single writer. Never hand-edit the registry markdown. Never `sed`/`awk` it from other scripts. Other skills read via `nblm-registry-helper.sh get <name>` and then invoke the `notebooklm` CLI directly with the returned ID.

### Subcommands

Every subcommand uses kebab-case names (a-z, 0-9, dash, 1-48 chars). The helper rejects anything else.

#### `/research notebook create <name> "<topic>"`

Create a fresh notebook, gather sources, add them, wait for indexing, register in the library, and report back. Mirrors the Phase 1-2 flow of `/research deep` but with naming upfront.

```bash
# Phase 1: source gathering (same as /research deep Phase 1 -- yt-search + perplexity + vault grep)
# Keith reviews + approves the list.

# Phase 2a: create notebook with a human-readable title AND register it.
NB_TITLE="K2B Library: $NAME"   # e.g. "K2B Library: investment-sba"
NB_ID=$(notebooklm create "$NB_TITLE" --json | jq -r '.notebook.id')
scripts/nblm-registry-helper.sh add "$NAME" "$NB_ID" "$DESCRIPTION"

# Phase 2b: add each approved source. After all sources indexed, update the
# registry row with the real source count so /research notebook list shows it.
for URL in "${SOURCES[@]}"; do
  notebooklm source add "$URL" -n "$NB_ID"
done
# Wait for indexing (reuse the same parallel-wait + canonical-reconcile pattern
# from /research videos Step 3 if source count > 5).
READY_COUNT=$(notebooklm source list -n "$NB_ID" --json | jq '[.[] | select(.status=="ready")] | length')
scripts/nblm-registry-helper.sh update "$NAME" --sources "$READY_COUNT" --touch
```

Also supports `--sources <url1> <url2> ...` to skip the discovery phase and use explicit URLs (same semantics as `/research deep --sources`).

#### `/research notebook ask <name> "<question>"`

Ask a new question against an existing named notebook. No source-gathering, no re-indexing. Answers land in `raw/research/YYYY-MM-DD_notebook-ask_<name>_<slug>.md` with citations if `notebooklm ask --json` is used.

```bash
NB_ID=$(scripts/nblm-registry-helper.sh get "$NAME")    # exits 4 if name not found
[ -z "$NB_ID" ] && exit 4
notebooklm ask "$QUESTION" -n "$NB_ID"                  # or --json for citations
scripts/nblm-registry-helper.sh update "$NAME" --touch  # bump last-used
```

Write the Q+A to `raw/research/` in a lightweight format (not the full Deep Research Output Format -- ask answers are follow-ups, not fresh briefings):

```markdown
---
tags: [research, notebook-ask, {name}]
date: YYYY-MM-DD
type: notebook-ask
origin: k2b-extract
notebook-name: {name}
notebook-id: {nb-id}
up: "[[Home]]"
---

# Notebook Ask: {name}

**Question:** {question}

## Answer

{notebooklm ask output, verbatim}

## K2B framing (optional, Opus-added)

{1-3 sentences if Opus has an identity-aware take; otherwise omit this section.}
```

#### `/research notebook add-source <name> <url>`

Add a source to an existing named notebook. Useful when Keith finds a new article / paper / YouTube video that should join an ongoing research corpus.

```bash
NB_ID=$(scripts/nblm-registry-helper.sh get "$NAME")
notebooklm source add "$URL" -n "$NB_ID"
notebooklm source wait -n "$NB_ID" --timeout 300   # wait for the new source only
NEW_COUNT=$(notebooklm source list -n "$NB_ID" --json | jq '[.[] | select(.status=="ready")] | length')
scripts/nblm-registry-helper.sh update "$NAME" --sources "$NEW_COUNT" --touch
```

#### `/research notebook expand <name> "<refinement-angle>"` -- added 2026-04-18

Layer NotebookLM's native Discover Sources on top of the manually curated corpus. Useful when Keith wants Gemini to fill coverage gaps **without replacing** the K2B-curated base (which carries the yt-search / perplexity / vault-grep judgement calls that Gemini can't reproduce). Runs `source add-research` against an existing named notebook, previews candidates, dedupes against existing sources, imports Keith's approved subset.

**Why this is additive, not a replacement for the manual path:**

The `/research notebook create` Phase 1 flow is deliberately K2B-curated -- yt-search applies Keith's 6-month recency window and preference-tail taste, Perplexity pulls GitHub / Reddit / X discussions with K2B's lens, vault grep surfaces prior Keith work. `expand` adds what those miss: arxiv papers, niche academic sources, Drive documents, cross-language content, items NotebookLM's own search infrastructure finds relevant that K2B's three channels won't produce. Use `expand` for the **gap-fill** pass, never the **primary** pass.

**Flags:**
- `--mode fast|deep` (default `deep`). Fast returns ~5-10 candidates in 30-60s. Deep returns ~10-25 candidates in 2-5 min with broader academic / long-tail coverage.
- `--auto-import` -- skip the review step and import all candidates that survive dedup. Use sparingly; Gemini's discovery is well-indexed but not K2B-taste-aware, so deep-mode results on unfamiliar topics will occasionally include SEO spam or thematic misses that a manual glance would catch.

**Contract the skill MUST preserve:**

- Once `NB_ID` has been resolved from the registry, every terminal state flows through the **Phase F audit log append** before `exit`. Log-then-exit, never exit-then-log. Set a `STATUS` variable as phases proceed, then a single finalization block at the end appends to `expand-log.md` and exits accordingly. The only exception is the upstream "notebook not in registry" check before any state is touched -- there is nothing to audit-log against an unknown notebook, so that path exits 4 directly with no Phase F entry. Once we cross into the expansion flow itself, Phase F is mandatory.
- `notebooklm source list --json` can return **either** a top-level array of source objects **or** an object with a `.sources` key depending on CLI version. The `/research videos` canonical reconcile at SKILL.md lines 354-362 handles both shapes; `expand` MUST use the same defensive accessor in BOTH the Phase B dedup and the Phase E `NEW_COUNT` snippet. The helper `_normalize_sources(data)` inlined in both Python heredocs below is the canonical form.
- Per-URL `source add` failures in Phase D are tracked separately from indexing failures in `source wait`. A URL that never got a source-id back must NOT appear in the wait pool and must NOT count toward `NEW_COUNT`. The registry `--sources N` update must reflect post-wait canonical truth, not "URLs Keith approved."
- URL normalization for dedup uses `urllib.parse` -- strip fragment, collapse default ports, lowercase host -- not just `.lower().rstrip("/")`.
- Helper code lives inline in each Python heredoc, never passed through `os.environ` and `exec()`. Inlining is verbose but transparent; env-passed exec is a code-in-data pattern that is harder to review, harder to audit, and a net loss even when the risk is theoretical.

**Flow:**

```bash
NAME="$1"
REFINEMENT="$2"
MODE="${MODE:-deep}"
AUTO_IMPORT="${AUTO_IMPORT:-false}"
NB_ID=$(scripts/nblm-registry-helper.sh get "$NAME")
# Pre-Phase-F exit (documented exception in the Contract above): unknown
# notebook has no audit-log scope to land in.
[ -z "$NB_ID" ] && { echo "notebook '$NAME' not in registry" >&2; exit 4; }

STATUS="unknown"
REASON=""
IMPORTED_URLS=()
SKIPPED_URLS=()
FAILED_ADD_URLS=()
CANDIDATES_SHOWN=0
REGISTRY_UPDATE_FAILED=false   # set true only if Phase E helper call errors

RESULT=$(mktemp -t k2b-expand.XXXXXX.json)
EXISTING=$(mktemp -t k2b-expand-existing.XXXXXX.json)
CANDIDATES=$(mktemp -t k2b-expand-cand.XXXXXX.json)
trap 'rm -f "$RESULT" "$EXISTING" "$CANDIDATES"' EXIT

# Helper (inlined into each Python heredoc that needs it, below).
# Definition, for reference:
#
#   def _normalize_sources(data):
#       """Accept both top-level-array and object-with-.sources CLI shapes."""
#       if isinstance(data, list):
#           return [s for s in data if isinstance(s, dict)]
#       if isinstance(data, dict):
#           inner = data.get("sources")
#           if isinstance(inner, list):
#               return [s for s in inner if isinstance(s, dict)]
#       return []

# ---- Phase A: discovery -----------------------------------------------------
if ! notebooklm source add-research "$REFINEMENT" --mode "$MODE" --no-wait -n "$NB_ID" 2>/dev/null; then
  STATUS="failed"; REASON="add-research-rejected"
elif ! notebooklm research wait -n "$NB_ID" --timeout 600 --json > "$RESULT" 2>/dev/null; then
  STATUS="timeout"; REASON="research-wait-timeout-or-error"
elif ! python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$RESULT" 2>/dev/null; then
  STATUS="failed"; REASON="research-wait-malformed-json"
fi

# ---- Phase B: dedup ---------------------------------------------------------
if [[ "$STATUS" == "unknown" ]]; then
  notebooklm source list -n "$NB_ID" --json > "$EXISTING" 2>/dev/null || true
  if ! python3 -c 'import json,sys;json.load(open(sys.argv[1]))' "$EXISTING" 2>/dev/null; then
    STATUS="failed"; REASON="source-list-malformed-json"
  else
    RESULT="$RESULT" EXISTING="$EXISTING" \
      python3 > "$CANDIDATES" <<'PY'
import json, os
from urllib.parse import urlparse, urlunparse

def _normalize_sources(data):
    """Accept both top-level-array and object-with-.sources CLI shapes."""
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    if isinstance(data, dict):
        inner = data.get("sources")
        if isinstance(inner, list):
            return [s for s in inner if isinstance(s, dict)]
    return []

DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21}

def normalize(url):
    if not url:
        return ""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower()
    port = p.port
    if port is not None and DEFAULT_PORTS.get(p.scheme) == port:
        port = None
    netloc = host + (f":{port}" if port else "")
    path = (p.path or "").rstrip("/")
    # drop fragment; keep query (two URLs differing only by tracking params
    # are not worth deduping -- false positives risk dropping real sources).
    return urlunparse((p.scheme.lower(), netloc, path, p.params, p.query, ""))

result   = json.load(open(os.environ["RESULT"]))
existing = json.load(open(os.environ["EXISTING"]))
existing_urls = { normalize(s.get("url") or "") for s in _normalize_sources(existing) }
existing_urls.discard("")
candidates_raw = _normalize_sources(result)
kept = [s for s in candidates_raw if normalize(s.get("url") or "") and normalize(s.get("url") or "") not in existing_urls]
print(json.dumps({"kept": kept, "raw_count": len(candidates_raw)}))
PY
    RAW_COUNT=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["raw_count"])' "$CANDIDATES")
    COUNT=$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["kept"]))' "$CANDIDATES")
    CANDIDATES_SHOWN="$COUNT"
    if (( RAW_COUNT == 0 )); then
      STATUS="no-candidates"; REASON="gemini-returned-empty"
    elif (( COUNT == 0 )); then
      STATUS="no-new-sources"; REASON="all-duplicates-of-existing-corpus"
    fi
  fi
fi

# ---- Phase C: review (if candidates remain) ---------------------------------
# [Claude reasoning step, not bash.] Show Keith numbered candidates with
# title + URL + domain tag. Parse selection in Python per the grammar below.
# On double-ambiguous input, set STATUS="skipped"; REASON="unparseable-selection".
# On "none", set STATUS="skipped-by-user"; REASON="keith-selected-none".
# Otherwise populate an APPROVED_URLS array from the selection.

# ---- Phase D: import + index ------------------------------------------------
# For each APPROVED_URL, call `source add -n "$NB_ID" --json` inside a safe
# `while IFS= read -r URL` loop (never `for URL in $(...)`, which word-splits
# on whitespace in Gemini-returned URLs). Capture the returned source-id via
# python3 --json parse. URLs that return a valid source-id get queued into
# the wait set; URLs that fail go into FAILED_ADD_URLS[] and do NOT enter
# the wait set. Mirror /research videos Step 3 for the parallel wait:
# per-child PID + one retry + 2s backoff + canonical reconcile against
# `source list --json` afterwards (using _normalize_sources above).
# Partial indexing failures warn but do not abort -- expand is additive.

# ---- Phase E: registry update ----------------------------------------------
# NEW_COUNT reflects canonical ready-count, NOT count of approved URLs.
NEW_COUNT=$(notebooklm source list -n "$NB_ID" --json 2>/dev/null \
  | python3 -c '
import json, sys

def _normalize_sources(data):
    """Accept both top-level-array and object-with-.sources CLI shapes."""
    if isinstance(data, list):
        return [s for s in data if isinstance(s, dict)]
    if isinstance(data, dict):
        inner = data.get("sources")
        if isinstance(inner, list):
            return [s for s in inner if isinstance(s, dict)]
    return []

data = json.load(sys.stdin)
print(sum(1 for s in _normalize_sources(data) if s.get("status") == "ready"))
')
if ! scripts/nblm-registry-helper.sh update "$NAME" --sources "$NEW_COUNT" --touch 2>/dev/null; then
  # Not fatal: imports already landed, registry drift is recoverable via manual re-run.
  REGISTRY_UPDATE_FAILED=true
fi

if [[ "$STATUS" == "unknown" ]]; then
  STATUS="imported"; REASON="ok"
fi

# ---- Phase F: audit log (the single exit point) -----------------------------
# Always runs, regardless of STATUS. Appends one YAML-fronted block to
# ~/Projects/K2B-Vault/raw/research/expand-log.md via atomic tmp+mv, recording:
# date, notebook, refinement, mode, status, reason, candidates_shown,
# imported[], skipped[], failed_add[], registry_update_failed flag.
# After the append, exit with:
#   0 on STATUS in {imported, no-new-sources, no-candidates, skipped-by-user}
#   1 on STATUS in {failed, timeout, skipped}   (genuine error / abort)
#   4 on the upstream "notebook not in registry" path (handled at the very top)
```

**Phase C selection grammar.** Accept one of:
- `"all"` -- import every surviving candidate
- `"none"` -- skip this expansion entirely (status `skipped-by-user`)
- `"1,3,5-8"` -- comma-separated indices and dash-ranges
- `"keep 1-4"` / `"drop 7,9"` -- whitelist or blacklist phrasing

Parse in Python, not bash. Ranges and the "drop" grammar are fragile in pure shell. On ambiguous input, re-prompt once; on second ambiguous input, set `STATUS="skipped"; REASON="unparseable-selection"` and fall through to Phase F (do not exit mid-flow).

**Phase F audit log format.** One block per run in `K2B-Vault/raw/research/expand-log.md`:

```yaml
---
date: YYYY-MM-DD
notebook: <name>
refinement: "<refinement>"
mode: fast|deep
status: imported | no-new-sources | no-candidates | skipped-by-user | skipped | timeout | failed
reason: <short machine-readable reason>
candidates_shown: N
imported: ["url1", "url2", ...]
skipped: ["url3", ...]
failed_add: ["url4", ...]
registry_update_failed: false|true
---
```

This is the durable trail distinguishing Gemini-discovered sources from manually-curated ones across the corpus, and lets `/improve` spot patterns (e.g. "Gemini adds 80% arxiv for this notebook, so the topic leans academic").

**Failure-mode reference (all route through Phase F):**

| Trigger | STATUS | REASON | Notebook state |
|---|---|---|---|
| `source add-research` rejects the request | `failed` | `add-research-rejected` | Unchanged |
| `research wait` timeout / non-zero exit | `timeout` | `research-wait-timeout-or-error` | Unchanged |
| `research wait` returns malformed JSON | `failed` | `research-wait-malformed-json` | Unchanged |
| `source list --json` returns malformed JSON | `failed` | `source-list-malformed-json` | Unchanged |
| Gemini returned zero candidates | `no-candidates` | `gemini-returned-empty` | Unchanged |
| All candidates deduped | `no-new-sources` | `all-duplicates-of-existing-corpus` | Unchanged |
| Keith typed `"none"` | `skipped-by-user` | `keith-selected-none` | Unchanged |
| Selection grammar ambiguous twice | `skipped` | `unparseable-selection` | Unchanged |
| Phase D partial: some `source add` calls fail | `imported` | `ok` (with `failed_add[]` populated) | Partial -- successful adds persist |
| Phase E registry update fails | `imported` | `ok` (with `registry_update_failed: true`) | Sources landed, registry stale -- re-run `update` manually |

#### `/research notebook list`

Print the registry table. Useful on its own, and used by other skills to discover what's available.

```bash
scripts/nblm-registry-helper.sh list
```

#### `/research notebook remove <name>`

Delete the NotebookLM notebook AND the registry entry. The helper does NOT call `notebooklm delete` itself (separation of concerns: the helper manages the registry file, the skill orchestrates NBLM operations). The skill must call both, IN ORDER, and ABORT if the NBLM-side delete fails -- otherwise the registry entry disappears while the notebook is still alive (quota-consuming orphan, invisible to the library).

```bash
NB_ID=$(scripts/nblm-registry-helper.sh get "$NAME")
if ! notebooklm delete -n "$NB_ID" -y; then
  echo "notebooklm delete failed for $NAME ($NB_ID) -- registry entry NOT removed" >&2
  echo "Try again later, or manually verify via 'notebooklm list' whether the notebook still exists." >&2
  exit 1
fi
scripts/nblm-registry-helper.sh remove "$NAME"
```

If `notebooklm delete` succeeds but the helper `remove` then fails (disk full, lock contention past 10s, permission error): the notebook is gone but the registry row remains. Surface the error to Keith so he can manually re-run the helper remove or edit the row out of the next ship. The state is recoverable (run `remove` again once the underlying issue is fixed).

### Advanced NBLM operations on named notebooks

The `notebooklm` CLI exposes much more than `ask`: audio overviews, mind maps, infographics, slide decks, video, reports, persistent notes, sharing, source refresh, fulltext retrieval. K2B does NOT wrap every command. Keith (or the skill) looks up the ID and invokes `notebooklm` directly:

```bash
NB_ID=$(scripts/nblm-registry-helper.sh get investment-sba)

# Audio overview
notebooklm generate audio "Focus on risk frameworks" -n "$NB_ID" --json
notebooklm artifact wait <artifact_id> --timeout 1200
notebooklm download audio ~/Projects/K2B-Vault/Assets/audio/2026-04-20_investment-sba_risk.mp3

# Mind map
notebooklm generate mind-map -n "$NB_ID"
notebooklm download mind-map ~/Projects/K2B-Vault/Assets/2026-04-20_investment-sba_mindmap.json

# Save the current conversation as a persistent NotebookLM note
notebooklm history --save "Risk framework Q&A 2026-04-20" -n "$NB_ID"

# Source hygiene: check which sources went stale (articles updated upstream)
notebooklm source stale -n "$NB_ID" --json
notebooklm source refresh <source_id> -n "$NB_ID"

# Generate a full NotebookLM report artifact
notebooklm generate report "Executive summary across all sources" -n "$NB_ID"
notebooklm artifact wait <artifact_id> --timeout 600
notebooklm download report ~/Projects/K2B-Vault/raw/research/2026-04-20_investment-sba_report.md

# Suggest follow-up prompts based on the corpus
notebooklm artifact suggestions -n "$NB_ID"

# Share read-only
notebooklm share public -n "$NB_ID" --view-level viewer
```

The `notebooklm` skill at `~/.claude/skills/notebooklm/` has full command documentation.

### What NOT to do

- Do NOT hand-edit `wiki/context/notebooklm-registry.md`. The block between the `REGISTRY-TABLE-START` / `REGISTRY-TABLE-END` markers is rewritten atomically on every helper call, and your manual edits inside that block WILL be overwritten. Prose above and below the markers is preserved.
- Do NOT reuse a named notebook for `/research videos`. That flow uses one-shot notebooks with 25 YouTube candidates that change every run; there is no reuse case.
- Do NOT use `/research notebook expand` as a replacement for `/research notebook create` Phase 1. Expand is additive gap-fill on top of a K2B-curated base. Skipping the manual yt-search + perplexity + vault-grep base loses Keith's taste filter (recency window, preference tail, prior-work context) -- those channels are features, not overhead.
- Do NOT skip the `--touch` / `--sources` update calls after `ask` or `add-source`. The registry's "Last Used" and "Sources" columns are how Keith (and `/improve`) know which notebooks are still live.
- Do NOT attempt to rename a notebook in place by editing the registry. Use `remove` + `create` (registered names are the K2B-side handle, not the NBLM-side title -- `notebooklm rename` changes the latter and is a separate concern).

## MiniMax extraction offload (added 2026-04-10)

**Why**: Bulk extraction (TLDR, key claims, entities) is pattern-matching work that burns Opus tokens on long sources (YouTube transcripts, papers, READMEs). Offload the extraction to MiniMax M2.7 and keep Opus focused on K2B applicability analysis, which requires identity-aware judgment. See `wiki/projects/project_minimax-offload.md` for the full rationale, provenance contract, and phase-gate protocol.

**Contract**:
- MiniMax produces a compressed, citation-backed digest: `{tldr, source_type, key_claims[], entities[], methodology_notes[], open_questions[]}`.
- Every `key_claim` carries a verbatim `source_span`, a `confidence` rating, and an `ambiguity` note.
- Opus reads the digest (not the raw source) and adds the K2B applicability section before writing the `raw/research/` note.
- Fail-open: if MiniMax is unavailable or returns invalid JSON, fall back to Opus-direct extraction on the raw source with a visible warning. Research notes are not durable commitment memory, so fail-open is safe.

**When to use (size gate)**:
- URL deep-dive mode (`/research <url>`) when the fetched source exceeds **~10,000 chars**.
- Long YouTube transcripts, full papers, READMEs for large repos, long-form articles.
- SKIP for short topic-scan findings, landing pages, or anything under 10K chars. On short sources, MiniMax's structured digest is typically LARGER than the original, so there are no token savings (this was measured empirically on 2026-04-10 against 3-9KB K2B research notes where the digest ran 1.0x-2.8x the input size). In that range, Opus-direct is cheaper AND faster.
- Rule of thumb: if you would only read the source once to extract, Opus-direct wins. If you would read it multiple times or the source is longer than Keith would skim in one sitting, MiniMax-extract wins.

**Workflow**:
1. Fetch the source content as usual (WebFetch, YouTube transcript MCP, Read for GitHub README, etc.).
2. If fetched content is under 10K chars, skip the offload entirely and extract on Opus. See size gate above.
3. Otherwise, write the fetched content to a temp file, e.g. `/tmp/k2b-research-input-$(date +%s).txt`, remembering the exact filename for the next step.
4. Call the extractor with the SAME filename from step 3:
   ```bash
   ~/Projects/K2B/scripts/minimax-research-extract.sh \
     "$TEMP_FILE" \
     "<source-url>" \
     "<source-title>"
   ```
5. Parse the returned JSON.
6. Spot-check 3 random `source_span` values against the fetched content. A simple substring match after collapsing whitespace is sufficient (`python3 -c 'import json,re,sys; ...'` or just visual). If any spot-check fails, fall back to Opus-direct extraction on the full source and append a manual-override entry to `wiki/context/minimax-jobs.jsonl`.
7. Write the K2B applicability section (this is Opus's job, NOT MiniMax's) using the digest as input plus Keith's framing (SJM/Signhub/TalentSignals positioning, content angle, his role).
8. Compose the final `raw/research/` note: frontmatter, Source, Key Takeaways (from digest), K2B Applicability (from Opus), Implementation Ideas.
9. Delete the temp file.
10. Trigger k2b-compile on the new note as usual.

**Fallback behavior**: if the extractor script exits non-zero (network error, invalid JSON, empty content), do NOT retry the script. Instead read the raw source content directly and extract in Opus, mentioning "MiniMax extractor unavailable, using Opus-direct path" in the session. The `minimax-jobs.jsonl` log already captured the failure via the script's own logging, so no additional action is needed.

**Observability**: every invocation appends a line to `wiki/context/minimax-jobs.jsonl` via the `log_job_invocation` helper in `scripts/minimax-common.sh`. Parse failure rate, cost, and duration are surfaced by `/improve`.

**Revert criteria** (per project_minimax-offload.md):
- If parse failure rate exceeds 5% over two weeks, revert to the Opus-direct path.
- If a sample audit of 10 outputs shows semantic drift (dropped claims, invented content, flattened voice) in 2+ cases, revert.
- If Keith manually overrides the extractor output more than twice in the first two weeks, revert.

## Output Format

Save to `raw/research/YYYY-MM-DD_research-briefing.md` (or `raw/research/YYYY-MM-DD_research-[topic-slug].md` for focused research).

After saving to raw/research/, trigger k2b-compile to digest the raw source into wiki pages. k2b-compile reads the raw research note, shows Keith a summary of wiki pages to update, and on approval updates wiki pages, indexes, and wiki/log.md.

```markdown
---
tags: [research, k2b-system]
date: YYYY-MM-DD
type: reference
origin: k2b-generate
up: "[[Home]]"
---

# Research Briefing -- YYYY-MM-DD

## External Findings
### [Finding 1 Title]
- **Source**: [URL or search]
- **Summary**: [2-3 sentences]
- **K2B Relevance**: [how this applies]
- **Action**: [what Keith could do with this]

### [Finding 2 Title]
...

## Recommendations
### New Ideas from Research
- [ ] [actionable idea with implementation sketch]

## Linked Notes
[wikilinks to related vault notes]
```

## For URL Deep Dives

Use the **Lens-Based Review Format** (defined earlier in this file). The old "Source / Key Takeaways / K2B Applicability / Implementation Ideas" skeleton is superseded because it produced generic summaries regardless of content type (tool demo, founder interview, investment content, etc.). The lens format replaces it with a verdict-first, type-specific review that leads with "Substance / Clickbait / Partial / Gated / Hype" and stakes one claim per detected lens.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-research\t$(echo $RANDOM | md5sum | head -c 8)\tran research: FOCUS" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Be specific in recommendations -- "improve the meeting processor" is useless, "add an explicit instruction for formatting action items with owner names in brackets" is actionable
- External findings should be filtered for relevance -- don't dump every search result
- When scanning YouTube videos, use the transcript MCP tools
- When scanning GitHub repos, focus on README, key source files, and patterns
- Always cross-link findings to existing vault notes where relevant
