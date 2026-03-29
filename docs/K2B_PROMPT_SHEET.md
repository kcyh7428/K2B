# K2B — Claude Code Prompt Sheet

## How to Use This Document

This is your cheat sheet. Each section contains a prompt you can paste directly into a Claude Code session. The documents in this knowledge base (K2B_ARCHITECTURE.md, K2B_PHASE1, PHASE2, PHASE3) provide the detailed context — these prompts tell Claude Code what to do with that context.

---

## Phase 1 Prompts

### 1A: Set Up the Obsidian Vault

```
I'm building K2B — my personal second brain using Obsidian + Claude Code.

Read the build instructions at [PATH]/K2B_PHASE1_VAULT_AND_SKILLS.md

My Obsidian is already installed on this Mac. Start by:
1. Finding or creating my K2B vault
2. Creating the full directory structure
3. Creating all note templates
4. Creating the welcome note

Show me the vault path when you find/create it so I can confirm.
```

### 1B: Install K2B Skills

```
Now install the K2B skills. Read the skill definitions from [PATH]/K2B_PHASE1_VAULT_AND_SKILLS.md (Step 5 section).

Create these skills in ~/.claude/skills/:
- k2b-daily-capture
- k2b-meeting-processor  
- k2b-insight-extractor
- k2b-tldr

Use the vault path: [YOUR_VAULT_PATH]

Replace all [VAULT_PATH] placeholders in the skills with the actual path.
```

### 1C: Create the K2B CLAUDE.md

```
Create the K2B CLAUDE.md system prompt. Read the spec from [PATH]/K2B_PHASE1_VAULT_AND_SKILLS.md (Step 4).

My Obsidian vault is at: [YOUR_VAULT_PATH]

Place the CLAUDE.md in this project root so it loads automatically.
Replace all [VAULT_PATH] placeholders with the actual path.
```

### 1D: Test Everything

```
Let's test the K2B setup:
1. Run /daily — create today's daily note
2. Verify the note was created in the vault at the correct path
3. Run /standup — generate a status briefing
4. Run /tldr — capture a summary of this setup conversation
5. Confirm all skills are loaded and working
```

---

## Phase 2 Prompts

### 2A: Build ClaudeClaw for K2B

```
I want to build K2B-Remote — a Telegram bot that lets me interact with K2B from my phone.

Read:
1. The K2B Phase 2 spec at /K2B_PHASE2_CLAUDECLAW.md
2. The ClaudeClaw mega prompt at /ClaudeClaw_Complete_Setup_Plan.md

My choices:
- Platform: Telegram
- Voice: Groq STT (speech-to-text only)
- Memory: Full (semantic + episodic)
- Features: Scheduler + Background service (launchd)

My K2B vault is at: /K2B-Value
My K2B CLAUDE.md is at: /K2B

Build following the mega prompt spec with K2B adaptations from Phase 2 doc.
```

### 2B: Setup Wizard

```
Run the K2B-Remote setup wizard. I need to:
1. Get my Telegram bot token (walk me through @BotFather)
2. Get my Groq API key
3. Configure .env
4. Install as a background service
5. Get my chat ID
```

---

## Phase 3 Prompts

### 3A: Create Brand Voice

```
Help me create my brand system using the brand voice process described in [PATH]/K2B_PHASE3_CONTENT_PIPELINE.md.

My content angle: I'm a senior executive (AVP Talent Acquisition at SJM Resorts) showing how AI tools can be used effectively in traditional corporate environments. My unique edge is dual expertise — agency recruitment and in-house TA leadership.

Guide me through the questionnaire and generate my brand voice document. Save it to my Obsidian vault.
```

### 3B: Install Content Skills

```
Install the K2B content pipeline skills from [PATH]/K2B_PHASE3_CONTENT_PIPELINE.md.

Create these skills in ~/.claude/skills/:
- k2b-brand-voice
- k2b-linkedin
- k2b-youtube
- k2b-content-calendar

My vault path: [YOUR_VAULT_PATH]
Replace all [VAULT_PATH] placeholders with the actual path.
```

### 3C: First Content Review

```
/content

Review my vault notes from the past week and suggest content ideas. I want to start building my content pipeline for LinkedIn and YouTube.
```

### 3D: Set Up Content Calendar

```
Set up my content calendar. I want:
- LinkedIn: 2 posts per week (Tuesday and Thursday)
- YouTube: 1 video every 2 weeks (Saturday release)

Create the calendar file in my vault and populate it with ideas from recent notes.
```

---

## Everyday K2B Commands

These work once Phase 1 is set up (and remotely once Phase 2 is running):

```
/daily                    → Create/update today's daily note
/standup                  → Status briefing across all projects  
/tldr                     → Save conversation summary to vault
/insight hiring           → Surface patterns about hiring from vault
/insight ai-adoption      → Surface patterns about AI adoption
/content                  → Weekly content idea review
/meeting Q2 TA Planning   → Process a meeting transcript
```

---

## Troubleshooting Prompts

### Skills Not Loading
```
Check if my K2B skills are properly installed:
ls -la ~/.claude/skills/k2b-*/SKILL.md

Read each SKILL.md and verify the YAML frontmatter has correct name and description fields.
```

### Vault Path Issues
```
My Obsidian vault should be at [PATH]. Verify:
1. The path exists and is readable
2. The folder structure matches the K2B spec
3. Templates are in place
4. Run an obsidian CLI command to confirm access
```

### ClaudeClaw Won't Start
```
Debug K2B-Remote startup:
1. Check if another instance is running: cat store/claudeclaw.pid
2. Check .env is properly configured: cat .env (redact sensitive values)
3. Run npm run status for health check
4. Check logs: tail -50 /tmp/claudeclaw.log
```
