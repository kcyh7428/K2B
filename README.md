# K2B -- Keith's Second Brain

A personal AI operating system built on Claude Code + Obsidian. Captures work, surfaces patterns, drafts content, and gets smarter over time.

## What it does

**Capture** -- Daily notes, meeting transcripts, YouTube videos, emails, and conversation summaries flow into a structured Obsidian vault with automatic cross-linking, frontmatter, and MOC integration.

**Think** -- Pattern recognition across notes, content idea extraction, deep research on external topics, inbox triage with preference learning.

**Create** -- LinkedIn posts drafted from vault content, multi-modal media generation (images, audio, video, music via MiniMax AI), email drafting via Google Workspace.

**Learn** -- K2B improves itself continuously through three mechanisms:
- **Session hooks** automatically check inbox, usage triggers, and observer findings on every startup
- **Background observer** (MiniMax M2.7, running 24/7 on Mac Mini) analyzes usage patterns: what gets promoted vs. archived, revision patterns, content adoption rates
- **Confidence-scored learnings** accumulate from corrections and observations, auto-applying at high confidence

**Remote access** -- Always-on Mac Mini runs a Telegram bot (Anthropic Agent SDK) for K2B access from anywhere.

## Architecture

```
Telegram (mobile)             MacBook (interactive sessions)
    |                              |
Mac Mini (always-on)          Claude Code + Hooks
    |                              |
    +-- k2b-remote                 +-- SessionStart hook (inbox, triggers, observer findings)
    |   (Anthropic Agent SDK)      +-- Stop hook (observation capture)
    |                              |
    +-- k2b-observer-loop          +-- 20+ Skills
    |   (MiniMax M2.7)             |     Capture: /daily, /meeting, /tldr, /youtube, /email
    |   Analyzes usage patterns    |     Think:   /inbox, /insight, /content, /research, /observe
    |   Writes observer-candidates |     Create:  /linkedin, /media
    |   Updates preference signals |     Teach:   /learn, /error, /request
    |                              |     System:  /schedule, /usage, /sync, /autoresearch
    +-- Syncthing (vault sync)     |
                                   +-- Obsidian Vault
                                   +-- Google Workspace (Gmail, Calendar, Drive)
                                   +-- MiniMax API (image, audio, video, music, text)
                                   +-- YouTube, LinkedIn, Airtable
```

## Self-Improvement Loop

```
Keith uses K2B normally (capture, triage, create)
    |
Stop hook logs observations to observations.jsonl
    |
Background observer (MiniMax M2.7) detects patterns
    |
    +-- Writes observer-candidates.md (surfaced at next session start)
    +-- Appends to preference-signals.jsonl
    |
/observe synthesizes signals into preference-profile.md
    |
All skills read the profile before producing output
    |
/autoresearch optimizes skills against assertions + profile
```

Cost: ~$0.007 per analysis, ~$0.11/day for hourly checks during active hours.

## Skills (20+)

| Category | Skills | What they do |
|----------|--------|-------------|
| Capture | daily-capture, meeting-processor, tldr, youtube-capture, email | Ingest from calendar, transcripts, videos, Gmail |
| Think | inbox, insight-extractor, observer, research, improve | Triage, pattern detection, preference learning |
| Create | linkedin, media-generator, vault-writer | Draft posts, generate media, write vault notes |
| Teach K2B | feedback, usage-tracker, autoresearch | Corrections, usage stats, iterative self-improvement |
| System | sync, scheduler | Deploy to Mac Mini, persistent scheduled tasks |

Each skill is a Markdown file with YAML frontmatter in `.claude/skills/k2b-*/SKILL.md`.

## Directory structure

```
K2B/
  .claude/
    skills/k2b-*/     20+ skills (capture, think, create, system)
    settings.json      Project-level hooks configuration
  k2b-remote/          Telegram bot (Anthropic Agent SDK, runs on Mac Mini via pm2)
  scripts/
    hooks/             Claude Code hooks (session-start, stop-observe)
    observer-loop.sh   Background MiniMax observer (pm2 on Mac Mini)
    observer-prompt.md Structured prompt for observer analysis
    minimax-*.sh       MiniMax API utilities (image, common helpers)
    deploy-to-mini.sh  Deployment script for Mac Mini
  docs/                Original planning and architecture documents
  CLAUDE.md            Live system prompt (source of truth for K2B behavior)
  DEVLOG.md            Development log
```

## Tech stack

| Component | Role |
|-----------|------|
| Claude Code (Opus) | Primary AI engine, interactive sessions |
| MiniMax M2.7 | Worker model: background observer, compile, lint deep, research extraction (cheap, 204K context, minimaxi.com) |
| Obsidian | Vault UI, graph view, cross-linking |
| Anthropic Agent SDK | Telegram bot (k2b-remote) |
| Google Workspace CLI | Gmail, Calendar, Drive integration |
| MiniMax API | Image, audio, video, music generation |
| Syncthing | Vault sync between MacBook and Mac Mini |
| pm2 | Process management (k2b-remote + k2b-observer-loop) |
| Claude Code Hooks | Automated session startup and observation capture |

## Note

This is a personal tool, not a framework or library. Built for one person's workflow and published for reference.
