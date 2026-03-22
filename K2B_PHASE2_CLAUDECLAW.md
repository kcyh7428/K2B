# K2B Phase 2 — ClaudeClaw (Remote Access via Telegram)

## Purpose

Build a Telegram bot that bridges to Claude Code on Keith's Mac, so K2B is accessible from his phone anywhere. This is based on the ClaudeClaw architecture by Mark Kashef, adapted for K2B.

## Prerequisites

- Phase 1 complete (Obsidian vault + skills working)
- Node.js 20+
- Claude Code CLI installed and authenticated
- Telegram account (to create a bot via @BotFather)

## What Gets Built

A TypeScript project that:
1. Runs as a background service on Keith's Mac
2. Receives messages from Telegram
3. Passes them to Claude Code via Anthropic Agent SDK
4. Returns responses to Telegram
5. Persists sessions in SQLite (conversation continuity)
6. Has a memory system (semantic + episodic)
7. Handles voice notes (Groq Whisper transcription)
8. Handles photos and documents
9. Auto-starts on boot (launchd)

## Build Instructions for Claude Code

### Important Context

The ClaudeClaw mega prompt (stored separately in this knowledge base as `ClaudeClaw___Mega_Prompt`) contains the complete, detailed specification for building this system. This document provides the K2B-specific adaptations.

### K2B Adaptations to ClaudeClaw

When building from the mega prompt, apply these modifications:

1. **Project name**: `k2b-remote` (not `claudeclaw`)
2. **CLAUDE.md**: Use the K2B CLAUDE.md from Phase 1, not the generic ClaudeClaw template
3. **Vault integration**: The agent should have access to the Obsidian vault path so slash commands work remotely
4. **Memory sweet spot**: Use `full` memory (semantic + episodic with decay) — Keith will be sending messages throughout the day about work
5. **Voice**: Enable `stt_groq` (voice-to-text via Groq Whisper). TTS is optional for later.
6. **Platform**: Telegram
7. **Optional features**: Enable `scheduler` and `service` (background auto-start). Skip WhatsApp and multiuser for now.

### The Build Prompt

Feed the following to Claude Code in a new session:

```
I want to build K2B-Remote — a Telegram bot that lets me interact with Claude Code from my phone. This is based on the ClaudeClaw architecture.

Read the ClaudeClaw mega prompt at [PATH_TO_MEGA_PROMPT] for the complete technical specification.

My choices:
- Platform: Telegram
- Voice: Groq STT (speech-to-text via Groq Whisper API)
- Memory: Full (semantic + episodic with decay in SQLite)
- Features: Scheduler + Background service (launchd on macOS)

Modifications from the standard ClaudeClaw:
1. Project name is "k2b-remote"
2. Use my existing K2B CLAUDE.md at [VAULT_PATH]/CLAUDE.md as the system prompt
3. The agent must be able to read/write to my Obsidian vault at [VAULT_PATH]
4. All K2B skills in ~/.claude/skills/k2b-* should be available
5. When I send /daily, /standup, /tldr, /insight, /content, /meeting — those should work through the bot just like they do in the terminal

Build everything following the mega prompt specification. Ask me the setup questions it specifies.
```

### Getting Your Telegram Bot Token

1. Open Telegram and search for `@BotFather`
2. Send `/newbot`
3. Choose a name: `K2B` (display name)
4. Choose a username: `k2b_keith_bot` (must end in `bot`)
5. BotFather gives you a token like `7123456789:AAHxxxxxxxxxxxxxxxx`
6. Save this as `TELEGRAM_BOT_TOKEN` in the `.env` file

### Getting Your Chat ID

1. Start the bot: `npm run dev`
2. Open Telegram and send `/chatid` to your bot
3. It replies with your numeric chat ID
4. Save this as `ALLOWED_CHAT_ID` in `.env`
5. Restart the bot

### Getting a Groq API Key

1. Go to `console.groq.com`
2. Sign up (free)
3. Create an API key
4. Save as `GROQ_API_KEY` in `.env`

### Project Structure (Expected)

```
k2b-remote/
├── src/
│   ├── index.ts          # Entry point
│   ├── agent.ts          # Claude Code SDK wrapper
│   ├── bot.ts            # Telegram bot (grammy)
│   ├── db.ts             # SQLite (sessions + memory)
│   ├── memory.ts         # Semantic + episodic memory
│   ├── voice.ts          # Groq Whisper STT
│   ├── media.ts          # Photo/document handling
│   ├── scheduler.ts      # Cron task runner
│   ├── config.ts         # Environment config
│   ├── env.ts            # .env parser
│   └── logger.ts         # Pino logger
├── scripts/
│   ├── setup.ts          # Interactive setup wizard
│   └── status.ts         # Health check
├── CLAUDE.md             # → Symlink or copy of K2B CLAUDE.md
├── package.json
├── tsconfig.json
├── .env
└── .env.example
```

### Key Technical Details

**Agent SDK** — The core is `@anthropic-ai/claude-agent-sdk`. It spawns a `claude` subprocess with:
- `cwd`: pointed at the K2B project root (where CLAUDE.md lives)
- `resume`: session ID from SQLite (conversation continuity)
- `settingSources: ['project', 'user']` (loads CLAUDE.md + global skills)
- `permissionMode: 'bypassPermissions'` (no terminal approval needed)

**Memory** — SQLite with two sectors:
- Semantic: long-term facts ("my team has 5 recruiters", "SJM's grad program runs in Q3")
- Episodic: conversation context that decays over time
- FTS5 for full-text search across memories
- Salience decay: 2%/day, auto-delete below 0.1

**Session Persistence** — Each Telegram chat maps to a Claude Code session ID. Messages continue the same conversation thread. `/newchat` resets.

### Testing Checklist

After build:
- [ ] Bot starts without errors
- [ ] `/chatid` returns correct ID
- [ ] Text message → Claude response works
- [ ] `/daily` creates a daily note in Obsidian vault
- [ ] `/newchat` clears session
- [ ] Voice note → transcription → response works
- [ ] Photo → description works
- [ ] Memory persists across messages
- [ ] `/memory` shows recent memories
- [ ] Service starts on boot (launchd)
- [ ] `npm run status` passes all checks

## What You Have After Phase 2

- K2B accessible from Telegram on your phone
- Full Claude Code capabilities from anywhere
- Voice note support (speak → transcribe → process)
- Photo/document forwarding
- Persistent memory across conversations
- Session continuity (pick up where you left off)
- Scheduled tasks capability
- Auto-starts when Mac boots

## Important Safety Notes

- Only YOUR chat ID can interact with the bot
- `bypassPermissions` is safe because it's your personal machine
- The bot uses your existing Claude Code subscription — no separate API costs
- All data stays local on your Mac (SQLite, Obsidian vault)
