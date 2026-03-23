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
│    └───────────┘      └────────────┘     └───────────┘ │
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

### Integrations (via MCP, wrapped as Skills)
- Gmail (read + draft only)
- Google Calendar
- Fireflies (meeting transcripts)

## Obsidian Vault Structure

```
K2B-Vault/
├── 00-Inbox/                    # Unsorted captures, quick notes
├── 01-Daily/                    # Daily notes (auto-generated)
│   └── 2026-03-18.md
├── 02-Work/                     # SJM Resorts TA role
│   ├── Meetings/                # Meeting notes & transcripts
│   ├── Projects/                # Active TA projects
│   ├── People/                  # Key stakeholders, team notes
│   ├── Insights/                # Observations, patterns, learnings
│   └── Decisions/               # Decision log with rationale
├── 03-Content/                  # Content pipeline
│   ├── Ideas/                   # Raw content ideas
│   ├── Drafts/                  # Work-in-progress content
│   ├── Published/               # Archive of published content
│   └── Calendar/                # Content schedule
├── 04-Business/                 # Signhub, TalentSignals, Agency at Scale
│   ├── Signhub/
│   ├── TalentSignals/
│   └── AgencyAtScale/
├── 05-Knowledge/                # Reference material, learnings
│   ├── AI-Tools/                # AI tool notes, configs, learnings
│   ├── Recruitment/             # Domain expertise
│   └── Resources/               # Bookmarks, references
├── 06-Personal/                 # Personal notes, goals
├── Templates/                   # Note templates
│   ├── daily-note.md
│   ├── meeting-note.md
│   ├── content-idea.md
│   ├── decision-log.md
│   └── project-note.md
└── .obsidian/                   # Obsidian config
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

### Phase 4: Research Agent + Autoresearch (In Progress)
**Goal**: Self-improving K2B through the Karpathy autoresearch pattern
**What gets built**:
- `k2b-research` skill -- on-demand research agent (internal audit + external scanning + URL deep dives)
- `k2b-autoresearch` skill -- the Karpathy loop for iterative skill improvement
- Eval infrastructure -- `eval/eval.json` + `eval/learnings.md` + `eval/results.tsv` per skill
- Binary assertion testing for all core skills
- Schedulable for overnight autonomous improvement runs (via CronCreate or ClaudeClaw)

**Future additions**:
- Webhook endpoint for direct transcript ingestion
- Vector search layer if Obsidian search proves insufficient
- WhatsApp bridge
- Voice interface (ElevenLabs TTS)

## Key Design Principles

1. **Local-first**: Everything lives on Keith's Mac. No cloud dependencies for core function.
2. **Progressive disclosure**: Skills load context only when needed. Don't bloat the context window.
3. **Obsidian is the canvas**: All knowledge, all outputs, all organization happens in markdown.
4. **Claude Code is the workhorse**: It reads, writes, searches, and creates. Obsidian is passive storage.
5. **Content is a byproduct**: Daily work naturally feeds the content pipeline. Don't create content for its own sake.
6. **Start simple, iterate**: Each phase should be usable on its own before moving to the next.

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
