# K2B

A personal AI operating system built on Claude Code + Obsidian.

## What it does

- **Daily work companion** -- captures meetings, decisions, and insights into a structured Obsidian vault with automatic cross-linking and pattern recognition
- **Content engine** -- surfaces content ideas from daily work, drafts LinkedIn posts, generates media (images, audio, video) via MiniMax AI
- **Remote assistant via Telegram** -- always-on Mac Mini runs a Telegram bot powered by the Anthropic Agent SDK, providing K2B access from anywhere

## Architecture

```
Telegram (mobile)
    |
Mac Mini (always-on server)
    |
    +-- k2b-remote (Anthropic Agent SDK)
    |       |
    |       +-- Claude Code
    |               |
    |               +-- Skills (capture, think, create)
    |               +-- Obsidian Vault (synced via Syncthing)
    |               +-- Google Workspace (Gmail, Calendar, Drive)
    |               +-- MiniMax API (image, audio, video, music)
    |               +-- YouTube, LinkedIn, Airtable
```

## Directory structure

```
K2B/
  .claude/skills/   Skills -- K2B's capabilities (daily capture, meetings, research, content, etc.)
  k2b-remote/       Telegram bot -- Anthropic Agent SDK, runs on Mac Mini via pm2
  scripts/          Deployment and utility scripts (deploy, LinkedIn, MiniMax, YouTube)
  docs/             Original planning and architecture documents
  CLAUDE.md         Live system prompt -- source of truth for K2B behavior
  DEVLOG.md         Development log
```

## Tech stack

Claude Code, Obsidian, Anthropic Agent SDK, Google Workspace CLI, MiniMax API, Syncthing, pm2

## Note

This is a personal tool, not a framework or library. It's built for one person's workflow and published for reference only.
