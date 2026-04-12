# K2B -- Keith's Second Brain

A personal AI operating system built on Claude Code + Obsidian. Captures work, surfaces patterns, drafts content, and gets smarter over time.

Inspired by [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) and his [autoresearch](https://github.com/karpathy/autoresearch) self-improvement loop.

## Inspiration & Credits

K2B's knowledge architecture is built on Karpathy's LLM Wiki idea (April 2026): instead of RAG-style retrieval on every query, an LLM incrementally builds and maintains a persistent wiki -- structured, interlinked markdown files that sit between you and raw sources. Knowledge is compiled once and kept current, not re-derived every time you ask a question.

The self-improvement system adapts Karpathy's autoresearch pattern: define ONE thing to change, ONE metric to measure, then loop (modify -> commit -> test -> if better keep, if worse revert -> repeat). In autoresearch this optimizes neural network training code. In K2B it optimizes skill instructions.

K2B extends the original pattern with:
- A **review queue** (human judgment layer between LLM suggestions and wiki promotion)
- A **commander/worker architecture** (Opus orchestrates, MiniMax M2.7 handles heavy extraction at 30-50x cost savings)
- A **background observer** that continuously learns from vault behavior patterns
- A **cross-link weaver** that proactively proposes semantic connections between wiki pages
- A **content pipeline** that turns captured knowledge into LinkedIn posts, emails, and media
- **Active rules** auto-promoted from learnings and injected every session (vs. hand-written program.md)

## What it does

**Capture** -- Daily notes, meeting transcripts, YouTube videos, emails, research, and conversation summaries flow into a structured Obsidian vault. Every capture lands in `raw/` as an immutable source, then gets compiled into `wiki/` knowledge pages with automatic cross-linking, frontmatter, and index updates.

**Think** -- Pattern recognition across notes, content idea extraction, deep research on external topics, review triage. The wiki is searchable through `index.md` (content catalog) and `log.md` (chronological record) -- no vector DB or RAG infrastructure needed. For complex multi-source research, `/research deep` offloads analysis to Google NotebookLM (Gemini) at zero token cost.

**Create** -- LinkedIn posts drafted from vault insights, multi-modal media generation (images, audio, video, music via MiniMax AI), email drafting via Google Workspace.

**Learn** -- K2B improves itself continuously (see Self-Improvement System below).

**Remote access** -- Always-on Mac Mini runs a Telegram bot (Anthropic Agent SDK) for K2B access from anywhere.

## Knowledge Architecture

Based on Karpathy's three-layer design, extended with a human judgment queue:

```
Layer 1: Raw Sources (immutable)          Layer 2: Wiki (LLM-owned)
raw/                                      wiki/
  youtube/    Video transcripts             people/       20 person pages
  meetings/   Meeting transcripts           projects/     11 project pages
  research/   Research briefings            work/         6 operational pages
  tldrs/      Conversation summaries        concepts/     12 concept/feature pages
  daily/      Daily note extracts           insights/     3 insight pages
                                            reference/    15 reference pages
                                            content-pipeline/  4 content ideas
                                            context/      21 operational configs

Layer 3: Review (human judgment)          Schema
review/                                   CLAUDE.md     System prompt + conventions
  Content ideas awaiting adoption           (co-evolved with K2B over time)
  Compile conflicts needing resolution    Active rules  7 behavioral rules injected
  Cross-link proposals from weave           every session, promoted from learnings
```

**How ingest works (the ripple effect):**

A single source (YouTube video, meeting transcript, research article) triggers a compile pass that can touch 10-15 wiki pages:

```
Raw source dropped in raw/
    |
k2b-compile calls MiniMax M2.7 for structured extraction
    |
MiniMax returns: pages_to_update, pages_to_create, content_seeds
    |
Opus applies changes atomically:
    +-- Creates/updates person pages (who was mentioned)
    +-- Creates/updates project pages (what was discussed)
    +-- Creates/updates concept pages (ideas and patterns)
    +-- Updates wiki subfolder index
    +-- Updates raw subfolder index
    +-- Updates master wiki/index.md counts
    +-- Appends to wiki/log.md
```

**Navigation (directly from Karpathy's pattern):**
- `wiki/index.md` -- content catalog, read FIRST on every query. Navigate to any page in 2 hops.
- `wiki/log.md` -- append-only record of all vault operations. Parseable with `grep "^## \[" log.md`.
- Per-subfolder `index.md` files for granular navigation within each wiki category.

## Deep Research with NotebookLM

K2B integrates Google NotebookLM as a third worker model for multi-source research. While MiniMax handles single-source extraction and Opus handles orchestration, NotebookLM (powered by Gemini) handles multi-document synthesis across 10-50 sources at zero token cost to K2B.

**Setup:** `pip install "notebooklm-py[browser]"` + `notebooklm login` + `notebooklm skill install` (MacBook only).

**The flow:**

```
/research deep "topic"
    |
Phase 1: Source Gathering
    +-- YouTube Data API (yt-search.py, 101 quota units/search)
    +-- Perplexity MCP (GitHub repos, Reddit, articles, tweets)
    +-- Vault grep (existing wiki notes on topic)
    |
Keith reviews and approves source list
    |
Phase 2: NotebookLM Setup
    +-- notebooklm create "K2B Research: <topic>"
    +-- notebooklm source add <url> (for each approved source, up to 50)
    +-- Wait for indexing
    |
Phase 3: Structured Research Queries (Gemini does ALL analysis -- FREE)
    +-- 5-8 targeted questions against the notebook
    +-- Landscape, architecture, comparison, risks, applicability
    +-- Citation-grounded answers, zero hallucination
    |
Phase 4: Optional Deliverables
    +-- Audio overview (podcast), mind map, infographic
    |
Phase 5: Synthesis (Opus -- only phase that costs tokens)
    +-- K2B-specific framing (Keith's context, vault architecture fit)
    +-- Save to raw/research/
    |
Phase 6: Compile into wiki pages
```

**Three-model architecture for research:**

| Role | Model | What it does | Cost |
|------|-------|-------------|------|
| Commander | Claude Opus | Source gathering, question design, K2B framing, vault integration | Standard |
| Worker 1 | Gemini (NotebookLM) | Multi-document analysis, cross-referencing, citation-grounded answers | Free |
| Worker 2 | MiniMax M2.7 | Single-source bulk extraction (when source > 10K chars) | ~30-50x cheaper than Opus |

The NotebookLM notebook persists on Google's servers. Keith can revisit it for follow-up queries anytime. Deep research runs on MacBook only (not via Telegram). Compiled wiki pages sync to Mac Mini via Syncthing.

## YouTube Knowledge Pipeline

K2B processes YouTube videos through 7 category playlists, each with domain-specific analysis:

| Playlist | Focus | Analysis Angle |
|----------|-------|---------------|
| K2B | AI tools, second brain systems | Architecture patterns, integration opportunities |
| K2B Claude | Claude Code, Anthropic ecosystem | Techniques to adopt, skill improvements |
| K2B Invest | Markets, trading, macro | Investment thesis extraction, risk signals |
| K2B Recruit | Talent acquisition, HR tech | Industry trends, competitive intelligence |
| K2B Content | Content creation, LinkedIn | Format ideas, engagement patterns |
| K2B Learn | General learning, productivity | Frameworks, mental models |
| K2B Screen | Pre-screened, high-signal content | Deep analysis (curator-approved) |

**The flow:**

```
YouTube playlist (saved by Keith)
    |
yt-playlist-poll.sh detects new videos
    |
Transcript extracted (YouTube API or Whisper for Chinese/unavailable)
    |
Playlist-specific analysis prompt generates raw/ capture
    |
k2b-compile digests into wiki/ (updates people, concepts, reference pages)
    |
Recommendation engine suggests related unwatched videos
    |
Preference learning tracks: watch/skip/screen/expire/comment signals
    |
youtube-preference-profile.md guides future recommendations
```

The recommendation engine learns from Keith's behavior -- which channels he trusts, which video lengths he prefers, which content pillars he engages with most. Videos that sit unwatched eventually expire. Screened videos (K2B Screen) get deeper analysis than bulk captures.

## Self-Improvement System

K2B gets smarter through three interconnected loops:

### 1. Observation and Pattern Detection

```
Keith uses K2B normally (capture, triage, create)
    |
Stop hook fires after every Claude response
    |
Detects vault file changes, guesses which skill was active
    |
Writes structured observation to observations.jsonl
    |
Background observer (MiniMax M2.7, pm2 on Mac Mini)
    +-- Batches 20+ observations
    +-- Detects patterns: what gets promoted vs. archived,
    |   revision patterns, content adoption rates
    +-- Writes findings to observer-candidates.md
    +-- Appends signals to preference-signals.jsonl
    |
Session start hook surfaces findings for Keith to review
```

Cost: ~$0.007 per analysis, ~$0.11/day for hourly checks during active hours.

### 2. Learning and Active Rules

```
Corrections, errors, and preferences accumulate:
    |
/learn captures a correction        -> self_improve_learnings.md
/error logs a failure with root cause -> self_improve_errors.md
/request logs a missing capability   -> self_improve_requests.md
    |
Learnings get reinforced (tracked count) on repeat observations
    |
Weekly audit promotes high-confidence learnings to active_rules.md
    (last audit: pruned 14 -> 7 rules, demoted 3, merged 2)
    |
Active rules injected into every session via startup hook
    |
Rules shape behavior -> new observations generated -> cycle continues
```

Current active rules (7): content identity boundaries, pipeline discipline, context vs. insight triage, deployment safety, poll-before-act, shipped-means-complete, compile index checklist.

### 3. Skill Optimization (Karpathy's Autoresearch, Adapted)

```
/autoresearch [skill-name]
    |
Phase 0: Preconditions (clean git, eval.json exists)
Phase 1: Review (read SKILL.md, results.tsv, git log, learnings)
Phase 2: Ideate (choose next change by priority)
Phase 3: ONE focused change to SKILL.md
Phase 4: git commit BEFORE testing (enables clean revert)
Phase 5: Run binary assertions from eval.json
Phase 6: If pass rate improved -> keep. If worse -> git reset
Phase 7: Log to eval/results.tsv
Phase 8: Update eval/learnings.md
    |
Repeat. Each iteration is one experiment. Git history is memory.
```

5 skills have eval infrastructure: daily-capture (91.7% pass rate), meeting-processor (80%), tldr, vault-writer, insight-extractor.

### 4. Cross-Link Weaver

```
Scheduled 3x/week (Sun/Tue/Thu at 04:00 HKT)
    |
k2b-weave reads all wiki pages, extracts existing links
    |
MiniMax M2.7 finds semantic gaps (missing cross-links)
    |
Top 10 proposals ranked by utility:
    +3 if target is orphan (zero inbound links)
    +2 if from/to cross wiki categories
    +1 if confidence > 0.75
    |
Digest note dropped in review/ for Keith's approval
    |
crosslink-ledger.jsonl tracks applied/rejected/deferred pairs
```

### 5. Vault Lint

`/lint` checks for: orphan pages, broken wikilinks, stale content, uncompiled raw sources, sparse articles, backlink gaps. `/lint deep` adds contradiction detection via MiniMax M2.7 across the full wiki.

## Commander/Worker Architecture

K2B uses a three-model pattern to balance capability, cost, and coverage:

| Role | Model | What it does | Cost |
|------|-------|-------------|------|
| **Commander** | Claude Opus (Claude Code) | Daily dialogue, orchestration, tool use, file changes | Standard Opus pricing |
| **Worker 1** | MiniMax M2.7 (minimaxi.com API) | Background analysis, compilation, contradiction detection, bulk extraction | ~30-50x cheaper than Opus |
| **Worker 2** | Gemini (via NotebookLM) | Multi-source deep research, cross-referencing, citation-grounded synthesis | Free (Google pays) |

Worker scripts in `scripts/`:
- `minimax-compile.sh` -- structured extraction for raw -> wiki compilation
- `minimax-weave.sh` -- semantic gap detection for cross-linking
- `minimax-lint-deep.sh` -- contradiction detection across wiki pages
- `minimax-research-extract.sh` -- extraction on long external sources
- `observer-loop.sh` -- background preference pattern detection

Pattern: Opus calls bash scripts that invoke MiniMax API, receives structured JSON, applies changes to the vault.

## Infrastructure

```
Telegram (mobile)             MacBook (interactive sessions)
    |                              |
Mac Mini (always-on)          Claude Code + Hooks
    |                              |
    +-- k2b-remote                 +-- SessionStart hook (review, triggers, observer)
    |   (Anthropic Agent SDK)      +-- Stop hook (observation capture)
    |                              |
    +-- k2b-observer-loop          +-- 22 Skills
    |   (MiniMax M2.7)             |     Capture: /daily, /meeting, /tldr, /youtube, /email, /compile
    |   Analyzes usage patterns    |     Think:   /review, /insight, /content, /research, /observe
    |   Writes observer-candidates |     Create:  /linkedin, /media
    |   Updates preference signals |     Teach:   /learn, /error, /request
    |                              |     System:  /ship, /schedule, /usage, /sync, /autoresearch,
    +-- k2b-weave (3x/week)       |             /lint, /improve
    |   Cross-link proposals       |
    |                              +-- Obsidian Vault (Syncthing-synced)
    +-- Syncthing (vault sync)     +-- Google Workspace (Gmail, Calendar, Drive)
                                   +-- MiniMax API (image, audio, video, music, text)
                                   +-- YouTube API, LinkedIn API, Airtable
```

## Skills (22)

| Category | Skills | What they do |
|----------|--------|-------------|
| Capture | daily-capture, meeting-processor, tldr, youtube-capture, email, compile | Ingest from calendar, transcripts, videos, Gmail, compile raw -> wiki |
| Think | review, insight-extractor, observer, research, improve, lint, weave | Triage, pattern detection, preference learning, vault health, cross-linking |
| Create | linkedin, media-generator, vault-writer | Draft posts, generate media, write vault notes |
| Teach K2B | feedback, usage-tracker, autoresearch | Corrections, usage stats, Karpathy-style self-improvement |
| System | ship, sync, scheduler | End-of-session shipping, deploy to Mac Mini, scheduled tasks |

Each skill is a Markdown file with YAML frontmatter in `.claude/skills/k2b-*/SKILL.md`.

## Directory structure

```
K2B/
  .claude/
    skills/k2b-*/      22 skills (capture, think, create, teach, system)
    settings.json       Project-level hooks configuration
  k2b-remote/           Telegram bot (Anthropic Agent SDK, runs on Mac Mini via pm2)
  scripts/
    hooks/              Claude Code hooks (session-start, stop-observe)
    observer-loop.sh    Background MiniMax observer (pm2 on Mac Mini)
    observer-prompt.md  Structured prompt for observer analysis
    minimax-*.sh        MiniMax API utilities (compile, weave, lint, research, image, speech)
    k2b-weave*.sh/.py   Cross-link weaver scripts
    yt-*.sh             YouTube API scripts (playlist poll, transcribe, auth)
    yt-search.py        YouTube Data API v3 search (topic-based video discovery)
    linkedin-*.sh       LinkedIn publishing scripts
    deploy-to-mini.sh   Deployment script for Mac Mini
    vault-query.sh      Vault search utility
  docs/                 Original planning and architecture documents
  tests/                Test fixtures (weave vault mock, etc.)
  CLAUDE.md             Live system prompt (source of truth for K2B behavior)
  DEVLOG.md             Development log
```

## Tech stack

| Component | Role |
|-----------|------|
| Claude Code (Opus) | Primary AI engine, interactive sessions, commander |
| MiniMax M2.7 | Worker model: compilation, observer, weave, lint deep, research extraction (~30-50x cheaper, 204K context) |
| Google NotebookLM (Gemini) | Deep research worker: multi-source synthesis, citation-grounded answers (free, via teng-lin/notebooklm-py) |
| Obsidian | Vault UI, graph view, cross-linking |
| Anthropic Agent SDK | Telegram bot (k2b-remote) |
| Google Workspace CLI | Gmail, Calendar, Drive integration |
| MiniMax API | Image, audio, video, music generation |
| YouTube Data API | Playlist polling, video metadata, OAuth-authenticated |
| Syncthing | Vault sync between MacBook and Mac Mini |
| pm2 | Process management (k2b-remote, k2b-observer-loop) |
| Claude Code Hooks | Automated session startup and observation capture |

## Portability

This architecture is domain-agnostic. The three-layer vault (raw/wiki/review), compile pipeline, observer, weave, autoresearch, lint, and active rules system can transfer to any knowledge domain by swapping:

1. The wiki subfolder categories (e.g. `wiki/tickers/` instead of `wiki/people/`)
2. The MiniMax extraction prompts (financial analysis instead of meeting notes)
3. The eval assertions (analysis quality instead of note formatting)
4. The active rules (trading discipline instead of content identity)

The commander/worker pattern (Opus + MiniMax + NotebookLM), observation loop, and self-improvement infrastructure remain the same.

## Note

This is a personal tool, not a framework or library. Built for one person's workflow. Architecture documented here for reference and potential adaptation to other domains.
