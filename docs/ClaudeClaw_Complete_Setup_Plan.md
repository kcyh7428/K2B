# ClaudeClaw — Complete Weekend Setup Plan

**For:** Keith — AVP, Talent Acquisition & HRIS, SJM Resorts, Macao
**Created:** March 10, 2026
**Purpose:** Set up a personal AI agent accessible from Telegram on your duty iPhone, running Claude Code on your Mac at home. Includes an Obsidian vault for structured knowledge capture.

---

## What You're Building (The Big Picture)

```
┌─────────────────────┐     ┌──────────────────────────────────────────────┐
│  DUTY iPHONE (SJM)  │     │           YOUR MAC AT HOME                   │
│                     │     │                                              │
│  ┌───────────────┐  │     │  ┌──────────────┐    ┌───────────────────┐  │
│  │   Telegram    │──┼─────┼─>│  ClaudeClaw   │───>│   Claude Code     │  │
│  │   App         │<─┼─────┼──│  (bridge)     │<───│   CLI + Skills    │  │
│  └───────────────┘  │     │  └──────┬───────┘    └───────────────────┘  │
│                     │     │         │                                    │
│  You dictate a      │     │         ▼                                    │
│  voice note or      │     │  ┌──────────────┐    ┌───────────────────┐  │
│  type a message     │     │  │   SQLite DB   │    │   Obsidian Vault  │  │
│  between meetings   │     │  │  (memory +    │    │  (structured      │  │
│                     │     │  │   sessions)   │    │   knowledge base) │  │
│                     │     │  └──────────────┘    └───────────────────┘  │
└─────────────────────┘     └──────────────────────────────────────────────┘
```

**The workflow:** You observe something at SJM → open Telegram on your duty iPhone → type or dictate it → ClaudeClaw on your Mac processes it with Claude Code → responds back to Telegram AND writes structured notes into your Obsidian vault → over time you build a searchable knowledge base that feeds both your SJM work and YouTube content pipeline.

---

## Your Confirmed Configuration

| Setting | Choice | Notes |
|---------|--------|-------|
| Platform | Telegram | Already installed on duty iPhone |
| Voice | Text only (for now) | Can add Groq STT later with one config change |
| Memory | Simple SQLite | Last N turns for conversational continuity |
| Knowledge base | Obsidian vault | Structured notes, daily logs, content ideas |
| Scheduler | Yes (cron) | Daily briefings, reminders |
| Auto-start service | Add after initial setup works | Don't want to debug two things at once |
| Video analysis | Skip | Not needed for Phase 1 |
| WhatsApp bridge | Skip | Telegram is the primary channel |

---

## Pre-Flight Checklist (Do This Before Saturday)

Complete these before you sit down for the main build:

### 1. Check Node.js on your Mac

Open Terminal and run:

```bash
node --version
```

- If it says `v20.x.x` or higher → you're good
- If it says something lower → upgrade: `brew install node` (if you have Homebrew) or download from https://nodejs.org
- If "command not found" → install from https://nodejs.org (download the LTS version)

### 2. Check if Claude Code CLI is installed

```bash
claude --version
```

- If it returns a version → you're good
- If "command not found" → install it:

```bash
npm install -g @anthropic-ai/claude-code
```

Then authenticate:

```bash
claude
```

This opens a browser window. Sign in with your Max account. Once done, test it:

```bash
claude "Say hello"
```

If you get a response, you're ready.

### 3. Download Obsidian

Go to https://obsidian.md and download for Mac. Install it but don't configure it yet — we'll set up the vault during the build.

### 4. Decide on a name for your assistant

The CLAUDE.md file needs a name for your AI assistant. Examples: "Claw", "Atlas", "Friday", "Jarvis", or just "ClaudeClaw". Pick whatever feels right — you can change it later.

### 5. Have the mega prompt ready

The file `REBUILD_PROMPT.md` is already in your Claude uploads. You'll need it on your Mac's filesystem. Either:
- Download it from this conversation, or
- Copy it to your Mac manually before starting

---

## Phase 1 — Create Project Folder & Obsidian Vault (15 mins)

### Step 1.1 — Create the ClaudeClaw project directory

```bash
mkdir -p ~/Projects/claudeclaw
cd ~/Projects/claudeclaw
```

### Step 1.2 — Create the Obsidian vault

```bash
mkdir -p ~/TheVault
```

Open Obsidian → "Open folder as vault" → select `~/TheVault`

### Step 1.3 — Create the vault structure

Inside your vault, create these folders (you can do this in Obsidian or Terminal):

```bash
mkdir -p ~/TheVault/daily-notes
mkdir -p ~/TheVault/sjm
mkdir -p ~/TheVault/sjm/team
mkdir -p ~/TheVault/sjm/stakeholders
mkdir -p ~/TheVault/sjm/processes
mkdir -p ~/TheVault/sjm/meetings
mkdir -p ~/TheVault/content-pipeline
mkdir -p ~/TheVault/content-pipeline/insight-log
mkdir -p ~/TheVault/content-pipeline/episode-ideas
mkdir -p ~/TheVault/projects
mkdir -p ~/TheVault/people
mkdir -p ~/TheVault/inbox
```

**What each folder is for:**

- `daily-notes/` — Auto-generated daily logs from your ClaudeClaw interactions
- `sjm/` — Everything related to your SJM role (team assessments, meeting notes, process observations)
- `content-pipeline/` — YouTube content ideas, insight logs, episode scripts
- `projects/` — Ongoing projects (TalentSignals, team restructure, etc.)
- `people/` — Key people notes (team members, stakeholders, contacts)
- `inbox/` — Dump zone for quick captures that haven't been organized yet

### Step 1.4 — Create a seed CLAUDE.md for Obsidian context

This goes inside the ClaudeClaw project (not the vault). It tells Claude Code how to interact with your vault:

```bash
cd ~/Projects/claudeclaw
```

Create a file called `CLAUDE.md` with this content (we'll refine it during the build, but having a starter helps):

```markdown
# [YOUR ASSISTANT NAME]

You are Keith's personal AI assistant, accessible via Telegram.
You run as a persistent service on his Mac.

## Who Is Keith

Keith is the AVP for Talent Acquisition & HRIS at SJM Resorts in Macao.
He manages a team of ~40 people. He recently joined and is inheriting
processes that need major overhaul.

His priorities:
- Team assessment and restructuring
- Recruitment strategy and pipeline building
- Stakeholder management and internal comms to leadership
- Capturing insights for a YouTube channel about how executives use AI

## Your Job

Execute. Don't explain what you're about to do — just do it.
When Keith sends a message, he wants output, not a plan.
If you need clarification, ask one short question.

## Obsidian Vault

Keith's knowledge base lives at: ~/TheVault

All markdown files must follow Obsidian conventions:
- Use [[double brackets]] for internal links
- Use YAML frontmatter for metadata
- Use tags with # prefix

When Keith shares observations, meeting notes, or insights:
1. Save a properly formatted note to the appropriate vault folder
2. Link it to related existing notes
3. If it contains a content/YouTube insight, also create or append to
   a note in content-pipeline/insight-log/

### Vault Structure

- daily-notes/ — Daily interaction logs (format: YYYY-MM-DD.md)
- sjm/team/ — Team member assessments, org structure notes
- sjm/stakeholders/ — Notes on key stakeholders, relationship mapping
- sjm/processes/ — Process observations, improvement ideas
- sjm/meetings/ — Meeting notes and action items
- content-pipeline/insight-log/ — AI transformation insights for YouTube
- content-pipeline/episode-ideas/ — Potential YouTube episode concepts
- projects/ — Ongoing projects
- people/ — Key people profiles
- inbox/ — Quick captures to be organized later

### Daily Note Template

When creating a daily note, use this structure:

---
date: YYYY-MM-DD
tags: [daily]
---

# YYYY-MM-DD

## Observations
[What Keith shared about his day]

## Decisions & Actions
[Any decisions mentioned or action items]

## Content Ideas
[Any AI-transformation angles worth noting]

## Raw Notes
[Unprocessed captures from the day]

## Message Format

- Keep Telegram responses concise
- Use plain text over heavy markdown
- For long outputs: summary first, offer to expand
- When saving to Obsidian, confirm with a brief message like:
  "Noted. Saved to sjm/meetings/[filename]. Linked to [[related note]]."

## SJM Context

- SJM internal comms are very formal/corporate
- If Keith asks you to draft something for SJM leadership, use formal register
- Keith's team is ~40 people across TA and HRIS functions
- He is inheriting existing processes that need major overhaul
```

**Note:** This file gets refined during the wizard setup. You'll personalize it with your assistant's name and any additional context.

---

## Phase 2 — Create Telegram Bot (10 mins)

### Step 2.1 — Create the bot via BotFather

1. Open Telegram on your Mac (or phone)
2. Search for `@BotFather`
3. Send `/newbot`
4. Choose a display name (e.g., `Keith's Assistant`)
5. Choose a username (must end in `bot`, e.g., `keith_claw_bot`)
6. **Save the bot token** BotFather gives you — you'll need this in Phase 3

The token looks like: `7123456789:AAHxyz-abc123def456...`

### Step 2.2 — Get your Telegram user ID

1. Search for `@userinfobot` in Telegram
2. Send it any message
3. It replies with your numeric user ID (e.g., `123456789`)
4. **Save this number** — it's used to restrict the bot so only you can use it

### Step 2.3 — Verify

Search for your new bot's username in Telegram. Open the chat. It won't respond yet — that's expected.

---

## Phase 3 — Build ClaudeClaw (60-90 mins)

This is the main event. Claude Code builds the project from the mega prompt.

### Step 3.1 — Copy the mega prompt to your project

Make sure `REBUILD_PROMPT.md` is in `~/Projects/claudeclaw/`

### Step 3.2 — Start Claude Code in the project directory

```bash
cd ~/Projects/claudeclaw
claude
```

### Step 3.3 — Feed the mega prompt

Once Claude Code is running, type:

```
@REBUILD_PROMPT.md — Read this and help me set up ClaudeClaw.
```

Claude Code will read the mega prompt and start the interactive wizard.

### Step 3.4 — Answer the wizard's four questions

When the wizard asks, here are your answers:

**Q1 — Platform:**
→ `telegram`

**Q2 — Voice:**
→ `none` (text only for now)

**Q3 — Memory:**
→ `simple` (last N turns in SQLite)

**Q4 — Optional features:**
→ Select `scheduler` only

### Step 3.5 — Provide your credentials when asked

The wizard will ask for:
- **Telegram bot token** — paste the token from Phase 2
- **Your Telegram chat ID** — the number from Phase 2 (or it may tell you to get it after first run via `/chatid`)

### Step 3.6 — Personalize CLAUDE.md

The wizard will open CLAUDE.md in your editor. Replace the starter content with the version we prepared in Phase 1, Step 1.4, filling in:
- `[YOUR ASSISTANT NAME]` → your chosen name
- `[YOUR NAME]` → Keith
- The Obsidian vault path → `~/TheVault`

### Step 3.7 — Let it build

Claude Code will now create all the source files (~14 files), install dependencies, and compile. This takes 10-30 minutes. Watch for errors. If something fails, tell Claude Code what happened — it's designed to debug with you.

### Step 3.8 — What gets built

When done, you should have:

```
~/Projects/claudeclaw/
├── src/
│   ├── index.ts          — Entry point, lifecycle
│   ├── agent.ts          — Claude Code SDK bridge
│   ├── bot.ts            — Telegram bot handlers
│   ├── db.ts             — SQLite schema + queries
│   ├── config.ts         — Environment config
│   ├── env.ts            — .env parser
│   ├── logger.ts         — Logging setup
│   ├── memory.ts         — Simple memory (last N turns)
│   ├── scheduler.ts      — Cron task runner
│   └── schedule-cli.ts   — CLI for managing scheduled tasks
├── scripts/
│   ├── setup.ts          — Setup wizard
│   ├── status.ts         — Health check
│   └── notify.sh         — Shell notification helper
├── store/                — Runtime data (SQLite DB lives here)
├── CLAUDE.md             — Your assistant's system prompt
├── .env                  — Your config (tokens, IDs)
├── package.json
├── tsconfig.json
└── .gitignore
```

---

## Phase 4 — Test (20 mins)

### Step 4.1 — Start the bot

If the wizard didn't start it automatically:

```bash
cd ~/Projects/claudeclaw
npm run dev
```

You should see output indicating the bot is running.

### Step 4.2 — Basic text test

From Telegram on your duty iPhone, send your bot:

```
Hello, can you hear me?
```

Watch your Mac's terminal — you should see Claude Code activate, process the message, and send a response back to Telegram within 5-30 seconds.

### Step 4.3 — Test the /chatid command

Send `/chatid` in Telegram. The bot should reply with your numeric chat ID. Verify it matches what you configured.

### Step 4.4 — Test /newchat

Send `/newchat`. The bot should confirm it's starting a fresh session.

### Step 4.5 — Test Obsidian integration

Send this message to your bot:

```
I had my first team meeting today at SJM. Key observations:
the TA team seems to have low morale, the HRIS system is outdated
(they're using an old version of SAP), and my direct report Maria
seems sharp — she could be a strong ally. Save this to my vault.
```

Then check your Obsidian vault — you should see a new note appear in the appropriate folder (likely `sjm/meetings/` or `daily-notes/`).

### Step 4.6 — Test the scheduler

From your Mac terminal (separate from the running bot):

```bash
cd ~/Projects/claudeclaw
node dist/schedule-cli.js create "Give me a summary of everything I captured today. Format it as a daily briefing." "0 21 * * *" YOUR_CHAT_ID
```

This creates a 9pm daily briefing. To verify:

```bash
node dist/schedule-cli.js list
```

---

## Phase 5 — Configure for Your SJM Workflow (30 mins)

Once the basics work, send these messages to your bot from Telegram to establish your context:

### Message 1 — Establish identity

```
My name is Keith. I recently joined SJM Resorts in Macao as AVP for
Talent Acquisition & HRIS. I manage a team of about 40 people. I'm
inheriting existing processes that need major overhaul. My priorities
are: team assessment & restructuring, recruitment strategy, and
stakeholder management.
```

### Message 2 — Set up the content pipeline

```
I'm building a YouTube channel about how corporate executives use AI
to transform their work. My SJM experience is the live case study.
Whenever I share a work challenge or insight, I want you to also
capture the "AI transformation angle" — how AI helped, what the
takeaway is, and whether it could be a YouTube episode concept.
Save these to content-pipeline/insight-log/ in my vault.
```

### Message 3 — Set up daily note behavior

```
At the end of each day when I message you with observations, create
or update the daily note in daily-notes/YYYY-MM-DD.md. Organize what
I've shared into: Observations, Decisions & Actions, Content Ideas,
and Raw Notes. Link any people mentioned to notes in people/ and
any SJM topics to the relevant sjm/ subfolder.
```

### Message 4 — Set up the scheduled daily briefing

You already created the 9pm briefing in Phase 4. Now create a morning prompt:

From Terminal:
```bash
node dist/schedule-cli.js create "Good morning Keith. Here's what's on your plate today based on yesterday's notes and any open action items. Check my vault for context." "0 8 * * 1-5" YOUR_CHAT_ID
```

This gives you a weekday 8am briefing based on your accumulated notes.

---

## Phase 6 — Keep It Running (15 mins)

### Step 6.1 — Prevent Mac from sleeping

System Settings → Energy Saver (or Battery → Options on laptops):
- "Prevent automatic sleeping when the display is off" → ON

Or from Terminal:
```bash
caffeinate -d &
```

### Step 6.2 — Install as background service (optional but recommended)

Once everything is tested and working:

```bash
cd ~/Projects/claudeclaw
npm run setup
```

The setup wizard includes a step to install as a macOS Launch Agent. This means ClaudeClaw starts automatically when your Mac boots, and restarts if it crashes.

Alternatively, if you prefer manual control, just keep a Terminal tab open with `npm run dev` running.

### Step 6.3 — Verify persistence

Close the terminal. Send a message from Telegram on your phone. If the bot responds, the background service is working. If not, reopen terminal and run `npm run dev`.

---

## Troubleshooting Quick Reference

| Problem | Likely Cause | Fix |
|---------|-------------|-----|
| Bot doesn't respond | Process not running | Check Terminal. Run `npm run dev` |
| "Build failed" during setup | TypeScript errors | Read the error message and tell Claude Code what happened |
| "Cannot find module" | Dependencies not installed | Run `npm install` then `npm run build` |
| Telegram says "bot is not responding" | Token issue or process crashed | Check `.env` has correct `TELEGRAM_BOT_TOKEN` |
| No notes appearing in Obsidian | CLAUDE.md vault path wrong | Verify the path in CLAUDE.md matches your actual vault location |
| Scheduler not firing | Wrong time zone or process not running | Check cron expression; ensure process is running |
| "Session not found" errors | SQLite DB issue | Try `/newchat` to start fresh; check `store/` directory exists |
| Bot responds to random people | ALLOWED_CHAT_ID not set | Run `/chatid` and add your ID to `.env` |
| Mac went to sleep | Energy settings | Re-enable caffeinate; check Energy Saver settings |
| "Permission denied" on claude | CLI not authenticated | Run `claude` in Terminal and complete OAuth login |

**General rule:** If you hit a wall for more than 15 minutes, come to this Claude.ai project and describe what's happening. I can help debug in real-time.

---

## What You'll Have When Done

A personal AI agent accessible from Telegram on your duty iPhone that:

1. **Captures observations in real-time** — Type a quick note between SJM meetings, it gets processed and filed
2. **Builds a structured knowledge base** — Obsidian vault grows with linked notes about your team, stakeholders, processes
3. **Maintains conversational memory** — SQLite remembers your recent context across sessions
4. **Sends daily briefings** — Morning prep and evening summaries via scheduled tasks
5. **Feeds your YouTube pipeline** — Every AI-assisted work moment is automatically logged as content material
6. **Runs on your own hardware** — No corporate IT involvement, no MDM violations, no third-party cloud storage
7. **Costs nothing extra** — Your Max subscription covers Claude Code; everything else is free tier

---

## Cost Summary

| Item | Cost | Status |
|------|------|--------|
| Claude Max subscription | $100/mo | Already paying |
| Telegram | Free | Already installed |
| Obsidian | Free | Download before Saturday |
| SQLite | Free | Built into the project |
| Node.js | Free | Check version pre-flight |
| **Total additional cost** | **$0** | |

---

## Future Upgrades (When You're Ready)

These can be added later without rebuilding — each is a config change or small addition:

| Upgrade | What it adds | Effort |
|---------|-------------|--------|
| **Voice notes (Groq STT)** | Dictate from iPhone instead of typing | 10 min — get free API key from console.groq.com, add to .env |
| **Voice replies (ElevenLabs)** | Bot responds with audio | 15 min — get API key, choose a voice, add to .env |
| **Auto-start service** | Survives reboots automatically | 5 min — run `npm run setup` and select service install |
| **Video analysis (Gemini)** | Send photos/videos for analysis | 10 min — get free Google API key, add to .env |
| **Plaud AI integration** | Meeting transcripts → Obsidian | Custom — pipe Plaud output through Zapier to ClaudeClaw |
| **WhatsApp bridge** | Read/reply to WhatsApp from Telegram | 30 min — more complex setup with QR code auth |

---

## Timeline Summary

| Phase | Time | What |
|-------|------|------|
| Pre-flight | 15 min | Check Node, Claude CLI, download Obsidian |
| Phase 1 | 15 min | Create folders, vault structure, seed CLAUDE.md |
| Phase 2 | 10 min | Create Telegram bot via BotFather |
| Phase 3 | 60-90 min | Claude Code builds the project from mega prompt |
| Phase 4 | 20 min | Test everything end-to-end |
| Phase 5 | 30 min | Configure for SJM workflow |
| Phase 6 | 15 min | Keep it running |
| **Total** | **~2.5-3 hours** | |

---

## One More Thing: The Plaud AI Integration

You mentioned you use a Plaud AI recorder for meeting capture. Once ClaudeClaw is running, there's a natural pipeline:

```
Plaud records meeting (Cantonese)
    → Plaud transcribes (Cantonese + speaker labels)
    → Zapier triggers on new transcript
    → Sends transcript to ClaudeClaw via Telegram
    → Claude Code processes into structured meeting notes
    → Saves to Obsidian vault: sjm/meetings/YYYY-MM-DD-[topic].md
    → Links to relevant people, stakeholders, action items
```

This is a Phase 2 project — get ClaudeClaw working first, then we'll build the Plaud pipeline.

---

*Have fun this weekend, Keith. When you're done, you'll have a personal AI operating system that works around every SJM IT restriction while building your YouTube content library automatically.*
