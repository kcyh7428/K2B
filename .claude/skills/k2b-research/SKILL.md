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

**All URL types produce:**
- Source summary
- Key takeaways (5-10 bullet points)
- K2B applicability analysis
- Specific recommendations with implementation ideas

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

Opus reads all NotebookLM answers and writes a structured vault note. This is the ONLY phase that costs Opus tokens. Apply K2B identity framing:
- How does this apply to Keith's SJM/Signhub/TalentSignals context?
- What maps to K2B's existing architecture (raw/wiki/review, commander/worker)?
- What's actionable now vs later?

Save to `raw/research/YYYY-MM-DD_research_<topic-slug>.md` using the Deep Research Output Format below.

#### Phase 6: Compile

Trigger k2b-compile on the new raw research note:
- Updates relevant wiki pages (concepts, projects, reference)
- Creates new reference pages if needed
- Updates cross-links

### Deep Research Output Format

```markdown
---
tags: [research, deep-dive, {topic-tags}]
date: YYYY-MM-DD
type: research-briefing
origin: k2b-generate
source: "NotebookLM deep research, N sources"
notebooklm-notebook: "<notebook-id>"
up: "[[Home]]"
---

# Deep Dive: [Topic Title]

## Sources Analyzed
N sources (X YouTube, Y GitHub repos, Z articles, W vault notes)
NotebookLM notebook: [notebook-id] (persistent -- can revisit for follow-up queries)

## Key Findings
1. [finding with context]
2. ...

## Architecture/Patterns
### [Pattern/Approach A]
- What it is
- Who uses it
- Tradeoffs

### [Pattern/Approach B]
...

## K2B Applicability
### What maps directly to our architecture
- [specific mapping]

### What we'd need to build new
- [gap analysis]

### Recommended approach for Keith
- [concrete recommendation]

## Risks and Limitations
- [risk 1]
- [risk 2]

## Implementation Ideas
- [ ] [concrete next step]
- [ ] ...

## Deliverables
- Audio overview: [[Assets/audio/YYYY-MM-DD_research_topic.mp3]] (if generated)
- Mind map: [[Assets/YYYY-MM-DD_research_topic_mindmap.json]] (if generated)

## Linked Notes
[wikilinks to related vault notes]
```

### Commander/Worker Pattern for Deep Research

Deep research adds Gemini (via NotebookLM) as a third worker alongside MiniMax:

| Role | Who | What they do in deep research |
|------|-----|-------------------------------|
| Commander | Opus | Source gathering, question design, K2B framing, vault integration |
| Worker 1 | Gemini (NotebookLM) | Multi-document analysis, cross-referencing, citation-grounded answers |
| Worker 2 | MiniMax M2.7 | Bulk extraction on individual long sources (if needed, per size gate) |

Gemini handles the expensive multi-doc synthesis for free. Opus adds identity-aware judgment. MiniMax handles individual source extraction when sources exceed the 10K char size gate.

## `/research videos "<query>"` -- on-demand video discovery via NotebookLM

Retires the old YouTube recommend agent. Finds videos matching a query, filters them via NotebookLM using Keith's baked framing + the tail of `wiki/context/video-preferences.md`, adds suitable ones to the K2B Watch playlist, drops per-video review notes, sends a Telegram notification.

**Prerequisites:** `notebooklm auth check --test` passes. `K2B_BOT_TOKEN` is set in env.

### Baked Keith framing (do not edit per query)

> Senior TA leader running AI transformation in a large traditional corporate (SJM Resorts, Macau). Also operates Signhub Tech (HK), TalentSignals, Agency at Scale. Content angle: showing how senior executives in traditional corporates use AI to 10x effectiveness. Prefer content creators with clear concrete examples over academic papers. Prefer actionable over theoretical. Prefer deployable in 90 days over visionary. Skip pure hype, skip thumbnails with "SHOCKING" / "INSANE", skip anything under 3 minutes, skip Chinese-only content unless specifically requested.

### Flow

1. **Get candidate YouTube URLs** via `yt-search.py` (not NotebookLM — NotebookLM is the filter, not the discovery engine):
   ```bash
   python3 ~/Projects/K2B/scripts/yt-search.py "<query>" --count 25 --months 1 --json > /tmp/candidates.json
   ```
   Parse the JSON (shape: `{"query", "count", "results": [{"url", "title", "channel", "duration", "published", ...}]}`). If `.count` is zero, abort with a Telegram notification: `no recent videos found for <query>`.

   **Why these args:** `--count 25` gives higher recall before filtering (Gemini typically rejects 60-80%, so 25 candidates yields ~5-10 suitable, matching the 3-10-per-run target). `--months 1` keeps the semantics at "what's new and worth knowing" -- six months of backlog dilutes recency. If a topic is thin in the last month, the run returns fewer videos and that's fine; silence beats stale.

2. **Create fresh notebook and add each candidate as a YouTube source:**
   ```bash
   NB_ID=$(notebooklm create "Videos: <query>" --json | jq -r '.notebook.id')
   SOURCE_IDS=()
   for URL in $(jq -r '.results[].url' /tmp/candidates.json); do
     SRC_ID=$(notebooklm source add "$URL" -n "$NB_ID" --json | jq -r '.source.id // .id')
     SOURCE_IDS+=("$SRC_ID")
   done
   ```
   `notebooklm source add` auto-detects YouTube URLs and pulls the transcript as the indexed content. `source add` itself returns quickly; processing happens in the background and is polled via `source wait`.

3. **Wait for all sources to index** via the subagent pattern (parallel, 600s per source):
   ```bash
   # In a subagent: wait for each source ID in parallel
   for ID in "${SOURCE_IDS[@]}"; do
     notebooklm source wait "$ID" -n "$NB_ID" --timeout 600 &
   done
   wait
   ```
   If any source fails to index (private video, transcription unavailable, rate-limited), log the failure per-source in the run record and continue with the rest. **Require at least 5 successful sources to proceed**; below that, abort with a Telegram notification `only <N> of <total> videos indexed for <query>, aborting -- see run record`.

4. **Read the preference tail** into a variable:
   ```bash
   PREF_TAIL=$(tail -n 30 ~/Projects/K2B-Vault/wiki/context/video-preferences.md | sed 's/"/\\"/g')
   ```

5. **Ask NotebookLM with the JSON filter prompt** (below), capture the answer:
   ```bash
   notebooklm ask "$(cat <<EOF
   Each source in this notebook is a YouTube video. Classify each one.

   Return JSON: [{"url", "title", "channel", "duration", "suitable": true|false, "why"}].

   Definition of "suitable" -- this is for Keith Cheung:

   Senior TA leader running AI transformation in a large traditional corporate (SJM Resorts, Macau). Also operates Signhub Tech (HK), TalentSignals, Agency at Scale. Content angle: showing how senior executives in traditional corporates use AI to 10x effectiveness. Prefer content creators with clear concrete examples over academic papers. Prefer actionable over theoretical. Prefer deployable in 90 days over visionary. Skip pure hype, skip thumbnails with "SHOCKING" / "INSANE", skip anything under 3 minutes, skip Chinese-only content unless specifically requested.

   Recent feedback from Keith to consider (most recent at bottom):

   $PREF_TAIL

   For each video, set suitable=true only if it matches the framing AND does not contradict recent feedback. For suitable=true videos, "why" should be a single sentence explaining the relevance hook (what specifically Keith will get out of watching). For suitable=false videos, "why" should be a single sentence explaining what disqualified it.

   Return ONLY the JSON array, no prose before or after.
   EOF
   )" -n "$NB_ID"
   ```

6. **Parse the JSON answer defensively.** If the answer has prose around the JSON, extract the array with `jq` or a Python one-liner. If parse fails, retry the ask once with a stricter prompt. If second parse fails, log raw answer to the run record, notify Keith the run failed parsing, abort the playlist/review-note steps.

7. **For each `suitable: true` entry:**
   a. Extract `video_id` from the URL (the `v=` query param, or last path segment for `youtu.be/`).
   b. Call `scripts/yt-playlist-add.sh "PLg0PUkz5itjwIXWVuSlvxud0ZR2JBsacX" "$VIDEO_ID"`. Log success/failure per video.
   c. Create review note at `K2B-Vault/review/video_$(date +%F)_<title-slug>.md` using the template in Step 8.

8. **Review note template** (one per added video):
   ```markdown
   ---
   type: video-feedback
   review-action: pending
   review-notes: ""
   video-url: <url from JSON>
   video-title: "<title from JSON>"
   channel: "<channel from JSON>"
   duration: "<duration from JSON>"
   added: <YYYY-MM-DD>
   why-suitable: "<why from JSON>"
   query: "<original query>"
   up: "[[index]]"
   ---
   ```
   Body is empty. Keith will fill `review-notes` and flip `review-action` after watching.

9. **Write run record** at `K2B-Vault/raw/research/$(date +%F)_videos_<query-slug>.md` with frontmatter + sections: query, baked framing version, preference tail used, full JSON response (suitable + skipped with reasons), playlist adds, review notes created, any errors. This is the durable audit trail.

10. **Send Telegram notification:**
    ```bash
    MSG="K2B found <N> videos for: *<query>*\n\n"
    # For each suitable video, append: "• <title> -- <why>\n<url>\n"
    scripts/send-telegram.sh "$MSG"
    ```
    Keep under 4000 chars (Telegram hard limit is 4096). If more than 4 videos, batch into 2 messages.

11. **Delete the notebook** (fresh per run, no accumulation):
    ```bash
    notebooklm notebook delete "$NB_ID"
    ```

12. **Append to skill-usage-log** as usual.

### Scheduling (not a skill concern)

Wrap with `/schedule`:
```
/schedule add "research-videos-ai-recruiting" weekly "/research videos \"AI recruiting tools for large enterprises\""
```
The scheduler runs the command on the Mac Mini weekly. Output lands in the vault via Syncthing. Telegram notification fires from the Mac Mini using `send-telegram.sh`.

### Failure modes

- **NotebookLM research times out (1800s):** abort, log to run record, notify Keith "research timed out on: <query>".
- **JSON parse fails twice:** log raw answer, notify Keith "filter response wasn't parseable, see raw/research/".
- **Playlist add fails for a video:** log per-video in run record, continue with the others, include the failure count in the Telegram summary.
- **Zero suitable videos:** still write the run record, notify Keith "nothing new worth watching for: <query> (N candidates screened, all rejected -- see run record for why)".

### What NOT to do

- Do NOT cache NotebookLM notebooks across runs. Fresh per run.
- Do NOT dedupe across runs via a URL log. The filter prompt's preference tail handles this naturally once Keith rates videos.
- Do NOT ask Keith to confirm each add. The filter already decided. Keith's feedback comes after watching.
- Do NOT write to `wiki/context/video-preferences.md` from `/research videos`. Only `/review` appends there.

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

Use a more focused output format:

```markdown
---
tags: [research, deep-dive, {topic-tags}]
date: YYYY-MM-DD
type: research-briefing
origin: k2b-generate
source: "[Title](URL)"
up: "[[Home]]"
---

# Deep Dive: [Topic/Source Title]

## Source
[URL and brief description of what this is]

## Key Takeaways
1. [takeaway with context]
2. ...

## K2B Applicability
### What We Can Use
- [specific technique] -- could apply to [specific K2B area]

### What's Interesting But Not Actionable Yet
- [concept] -- relevant when [condition]

### Implementation Ideas
- [ ] [concrete next step]

## Linked Notes
[wikilinks to related vault notes]
```

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
