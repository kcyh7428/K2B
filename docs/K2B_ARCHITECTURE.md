# K2B — Keith's 2nd Brain

## Project Vision

K2B is a personal AI operating system built on Claude Code + Obsidian + Skills. It replaces the previous KIRA architecture (which relied on AirTable + Supabase + n8n) with a simpler, local-first approach.

K2B serves three purposes:
1. **Daily Work Companion** — Capture, organize, and surface insights from Keith's role as AVP Talent Acquisition at SJM Resorts
2. **Content Engine** — Transform work insights and AI-usage patterns into content for YouTube and LinkedIn
3. **Remote Assistant** — Accessible from anywhere via Telegram using ClaudeClaw architecture

## Who Is Keith

Keith operates Signhub Tech Limited (Hong Kong), providing recruitment technology consultancy and AI-powered automation services. He has rare dual-sided recruitment expertise — agency-side and in-house Director of Talent Acquisition for major properties. He is now AVP Talent Acquisition at SJM Resorts in Macau.

His content angle: How a senior executive in traditional corporations uses AI to 10x effectiveness — bridging the gap between cutting-edge AI tools and real-world corporate talent acquisition.

Keith partners with Andrew on TalentSignals (AI automations for recruiting firms -- product design, ideas, prototypes) and runs Agency at Scale (AI-powered outreach automation).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    K2B SYSTEM                            │
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                   │
│  │   Telegram    │    │  Claude Code  │                  │
│  │  (Remote UI)  │───▶│  (Terminal)   │                  │
│  └──────────────┘    └──────┬───────┘                   │
│                             │                            │
│         ┌───────────────────┼───────────────────┐       │
│         │                   │                   │       │
│    ┌────▼─────┐      ┌─────▼──────┐     ┌─────▼─────┐ │
│    │  Skills   │      │  Obsidian   │     │   MCP     │ │
│    │ (Claude)  │      │  (Vault)    │     │ (Gmail,   │ │
│    │           │      │             │     │  GCal,    │ │
│    │ • capture │      │ • markdown  │     │  etc.)    │ │
│    │ • content │      │ • local     │     │           │ │
│    │ • meeting │      │ • graph     │     │           │ │
│    │ • insight │      │ • CLI       │     │           │ │
│    │ • schedule│      │             │     │           │ │
│    │ • usage   │      │             │     │           │ │
│    │ • media   │      │             │     │           │ │
│    └───────────┘      └────────────┘     └───────────┘ │
│                                                         │
│    ┌─────────────────────────────────────────────────┐  │
│    │  Scheduled Tasks (persistent, autonomous)       │  │
│    │  weekly-vault-health │ weekly-external-research  │  │
│    │  daily-review-check  │ friday-self-improvement   │  │
│    │  + usage-based triggers + one-time reminders    │  │
│    └─────────────────────────────────────────────────┘  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### What Changed from KIRA

| Component | KIRA (Old) | K2B (New) |
|-----------|-----------|-----------|
| Structured Data | AirTable (7 tables) | Obsidian vault (markdown) |
| Semantic Search | Supabase/pgvector | Claude Code file search + Obsidian CLI |
| Automation | n8n workflows | Claude Code skills + webhooks |
| Intelligence | Claude via MCP | Claude Code directly (local) |
| Remote Access | None (chat only) | ClaudeClaw via Telegram |
| Content Pipeline | Manual | Skills-driven (LinkedIn, YouTube) |

### What Stays

- Gmail MCP (search, draft — NEVER send or batch delete)
- Google Calendar MCP
- Fireflies for meeting transcripts (but ingested into Obsidian now)
- Claude Code as the primary intelligence layer

## Technology Stack

### Core (Phase 1)
- **Claude Code** — Primary agent, runs locally on Mac
- **Obsidian** — Local knowledge vault (markdown files + CLI)
- **Claude Code Skills** — Progressive disclosure capabilities

### Remote Access (Phase 2)
- **ClaudeClaw** — Telegram bot using Anthropic Agent SDK
- **SQLite** — Session persistence + memory
- **Groq Whisper** — Voice note transcription (free tier)
- **ElevenLabs** — Voice replies (optional)

### Content (Phase 3)
- **Brand Voice skill** — Keith's writing style codified
- **LinkedIn Post skill** — Draft posts from insights
- **YouTube Script skill** — Video scripts from accumulated knowledge
- **PowerPoint skill** — On-brand presentations

### Multi-Modal Media (Phase 6)
- **MiniMax API** (minimaxi.com) -- image, speech, video, music generation
- `k2b-media-generator` skill -- `/media` command for all modalities
- MCP server: `minimax-mcp-js` for direct tool access
- Bash scripts: `scripts/minimax-*.sh` as fallback/CLI interface
- Generated assets stored in `K2B-Vault/Assets/` (images/, audio/, video/)

### Integrations (via MCP, wrapped as Skills)
- Gmail (read + draft only)
- Google Calendar
- Fireflies (meeting transcripts)
- MiniMax (image generation, TTS, STT, video, music)

## Obsidian Vault Structure

Simplified flat structure (restructured 2026-03-23). Folders earn their place (10+ files). Links do the navigation, not folders.

```
K2B-Vault/
├── Inbox/                       # New captures, TLDRs, agent output (always land here first)
├── Daily/                       # Daily notes (YYYY-MM-DD.md)
├── Notes/                       # All processed notes
│   ├── People/                  # Person notes (18+)
│   ├── Projects/                # Project notes (13+)
│   ├── Content-Ideas/            # Adopted content ideas only (20+)
│   ├── Context/                 # Reference docs, research topics, usage tracking
│   └── (flat)                   # Insights, decisions, meetings, business overviews
├── Assets/                      # Generated media
│   ├── images/                  # AI-generated images (LinkedIn headers, thumbnails)
│   ├── audio/                   # TTS output, transcriptions, music
│   └── video/                   # Generated video clips
├── Templates/                   # Note templates
├── Home.md                      # Front door
└── MOC_*.md                     # Maps of Content at vault root
```

## Build Phases

### Phase 1: Foundation — Obsidian Vault + Core Skills
**Goal**: A working second brain Keith can use from day 1
**Effort**: 1–2 Claude Code sessions
**What gets built**:
- Obsidian vault structure with templates
- Core skills: daily capture, meeting notes, /tldr, insight extraction
- CLAUDE.md configured for K2B
- Connection to Gmail + GCal MCPs

→ See `K2B_PHASE1_VAULT_AND_SKILLS.md` for Claude Code build instructions.

### Phase 2: Remote Access — ClaudeClaw
**Goal**: Access K2B from Telegram on phone
**Effort**: 1 Claude Code session (mega prompt driven)
**What gets built**:
- Telegram bot via Anthropic Agent SDK
- Session persistence (SQLite)
- Memory system (semantic + episodic)
- Voice note support (Groq STT)
- Photo/document forwarding

→ See `K2B_PHASE2_CLAUDECLAW.md` for Claude Code build instructions.

### Phase 3: Content Pipeline
**Goal**: Turn daily work insights into publishable content
**Effort**: Iterative, skill-by-skill
**What gets built**:
- Brand voice definition
- LinkedIn post drafting skill
- YouTube script skill
- Content calendar management
- PowerPoint generation skill

→ See `K2B_PHASE3_CONTENT_PIPELINE.md` for Claude Code build instructions.

### Phase 4: Research Agent + Autoresearch (Complete)
**Goal**: Self-improving K2B through the Karpathy autoresearch pattern
**What gets built**:
- `k2b-research` skill -- on-demand research agent (internal audit + external scanning + URL deep dives)
- `k2b-autoresearch` skill -- the Karpathy loop for iterative skill improvement
- Eval infrastructure -- `eval/eval.json` + `eval/learnings.md` + `eval/results.tsv` per skill
- Binary assertion testing for all core skills

### Phase 5: Scheduling & Automation (Complete)
**Goal**: K2B runs tasks autonomously on schedules and reacts to usage patterns
**What gets built**:
- `k2b-scheduler` skill -- wraps Scheduled Tasks MCP for persistent recurring/one-time tasks
- `k2b-usage-tracker` skill -- tracks skill invocations, fires actions at configurable thresholds
- Usage logging across all 13 k2b-* skills (append-only TSV)
- Trigger rules system (`usage-triggers.md`) for threshold-based automation
- Session-start check for ready triggers (`scripts/check-usage-triggers.sh`)
- 4 seeded tasks: weekly vault health, weekly external research (Perplexity + YouTube + web), daily review check, Friday self-improvement review

**3-tier scheduling model**:
1. **Quick reminders** -- CronCreate / `/loop` (session-only, ephemeral)
2. **Persistent tasks** -- Scheduled Tasks MCP via `/schedule` (survives restarts, runs autonomously)
3. **Usage triggers** -- Threshold-based actions via `/usage` (e.g., after 10 meeting transcripts, auto-run insight extraction)

### Phase 6: Multi-Modal Media Generation (Complete)
**Goal**: Generate images, speech, and audio transcriptions using MiniMax AI (minimaxi.com)
**What gets built**:
- `k2b-media-generator` skill -- `/media` command wrapping image, speech, transcription, video, music
- MiniMax MCP server (`minimax-mcp-js`) in `.mcp.json` for direct Claude Code tool access
- Bash scripts: `minimax-common.sh`, `minimax-image.sh`, `minimax-speech.sh`, `minimax-transcribe.sh`
- `Assets/` folder in vault (images/, audio/, video/) with naming convention `YYYY-MM-DD_type_slug.ext`
- Image generation via `image-01` model (50/day on Plus tier)
- Text-to-speech via `speech-2.8-hd` (40 languages, Mandarin/Cantonese/English, 7 emotions)
- Audio transcription (Chinese/English STT) for meeting recordings
- Video generation (Hailuo 2.3) and music (Music 2.5+) ready in code, requires Max tier upgrade
- Keith's subscription: Plus (98 RMB/mo). Max tier (198 RMB/mo) unlocks video + music.

### Phase 7: YouTube Capture (Complete, partially retired 2026-04-14)
**Goal**: Process YouTube videos Keith saves to category playlists, with playlist-specific analysis.
**What remains live**:
- `k2b-youtube-capture` skill -- `/youtube` / `/youtube <playlist-name>` for batch playlist polling
- 7 YouTube playlists: K2B (general), K2B Claude, K2B Invest, K2B Recruit, K2B Content, K2B Learn, K2B Screen. K2B Watch is the destination for `/research videos` picks, not a capture source.
- Playlist config in `wiki/context/youtube-playlists.md`
- Transcript cascade: YouTube Transcript MCP (free) -> Groq Whisper (chunked for >4min) -> metadata-only fallback
- YouTube Data API v3 with OAuth for playlist writes (add/remove)
- Bash scripts: `yt-playlist-poll.sh`, `yt-playlist-add.sh`, `yt-playlist-remove.sh`, `yt-search.py`, `yt-auth.sh`
- Processed video tracking in `wiki/context/youtube-processed.md`
- Per-playlist `prompt_focus` drives different analysis for each playlist

**Retired 2026-04-14**: the YouTube conversational agent (6h background loop, taste model, channel affinity scoring, recommendation engine, `/youtube recommend`, `/youtube morning`, direct-URL screening, all 5 MCP tools, `youtube_agent_state` SQLite table) was deleted. Fresh-video discovery now runs through `/research videos "<query>"` via NotebookLM. See [[Shipped/2026-04-08_feature_youtube-agent]] and [[Shipped/feature_research-videos-notebooklm]].

**Future additions**:
- Voice cloning (upload Keith's voice sample for consistent narration)
- Webhook endpoint for direct transcript ingestion
- Vector search layer if Obsidian search proves insufficient
- WhatsApp bridge
- Conditional triggers beyond usage count (e.g., vault orphan detection)
- n8n integration for complex multi-step conditional workflows

## Key Design Principles

1. **Local-first**: Everything lives on Keith's Mac. No cloud dependencies for core function.
2. **Progressive disclosure**: Skills load context only when needed. Don't bloat the context window.
3. **Obsidian is the canvas**: All knowledge, all outputs, all organization happens in markdown.
4. **Claude Code is the workhorse**: It reads, writes, searches, and creates. Obsidian is passive storage.
5. **Content is a byproduct**: Daily work naturally feeds the content pipeline. Don't create content for its own sake.
6. **Start simple, iterate**: Each phase should be usable on its own before moving to the next.
7. **Validate before acting**: Every vault write checks frontmatter completeness, wikilink integrity, and folder placement before saving. Inspired by GStack's "careful" pattern.
8. **Completeness is cheap**: When the cost of doing the complete thing (all frontmatter fields, all cross-links, all MOC updates) is seconds, do the complete thing. Inspired by GStack's "boil the lake" principle.

## Skill Data Flow

Skills are organized by user intent: **Capture** (things come in), **Think** (K2B processes), **Create** (things go out).

```
CAPTURE                       THINK                          CREATE
-------                       -----                          ------
/daily   --> Daily/            /review  --> promotes to        /linkedin --> publishes
/meeting --> Notes/               Notes/ or Archive/          /media    --> Assets/
/tldr    --> Inbox/            /insight --> Notes/Insights/
/youtube --> Inbox/            /content --> Inbox/ (ideas)
/email   --> (read only)       /research --> Inbox/ (briefings)
                               /improve --> (surfaces patterns)

TEACH K2B                     SYSTEM
---------                     ------
/learn   --> learnings.md      /schedule --> Mac Mini cron
/error   --> errors.md         /usage    --> usage-log.tsv
/request --> requests.md       /autoresearch --> skill SKILL.md
```

### Inbox Write Contract

All notes saved to `Inbox/` MUST include `review-action:` and `review-notes: ""` in frontmatter. This is how Keith triages in Obsidian. Skills that write to Inbox (tldr, research, youtube, insight-extractor for /content) are required to include these fields. Vault-writer enforces this as a pre-write validation step.

### Skill I/O Contracts

| Skill | Reads From | Writes To | Also Updates |
|-------|-----------|----------|-------------|
| /daily | Google Calendar, open loops from yesterday | Daily/ | Project notes (status, milestones) |
| /meeting | Fireflies transcript or manual input | Notes/ | Project notes, person notes |
| /tldr | Current conversation context | Inbox/ | Project notes (progress) |
| /youtube | Playlist config, YouTube transcripts | Inbox/ | youtube-processed.md |
| /review | review/ notes with review-action set | Notes/, Archive/ | MOCs (after promote) |
| /insight | Vault-wide search | Notes/Insights/ | MOC_Content-Pipeline |
| /content | Recent daily + meeting + insight notes | Inbox/ | -- |
| /research | Vault health, web search, URLs | Inbox/ | -- |
| /linkedin | Notes/Content-Ideas/ | (external: LinkedIn API) | Content idea status |
| /media | Content ideas, direct prompts | Assets/ | Content idea (embeds) |

## File Naming Conventions

- Daily notes: `YYYY-MM-DD.md`
- Meeting notes: `YYYY-MM-DD_Meeting-Topic.md`
- Content ideas: `idea_short-slug.md`
- Projects: `project_name.md`
- People: `person_Firstname-Lastname.md`

## Tags System

Use YAML frontmatter tags for searchability:

```yaml
---
tags: [meeting, sjm, hiring-strategy]
date: 2026-03-18
type: meeting-note
project: graduate-program-2026
---
```

Core tag categories:
- **Type**: meeting, insight, decision, idea, draft, project, person
- **Domain**: sjm, signhub, benai, a-at-s, personal
- **Topic**: hiring, sourcing, employer-brand, ai-tools, automation, content
- **Status**: active, completed, archived, idea, draft, published
