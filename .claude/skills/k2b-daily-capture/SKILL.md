---
name: k2b-daily-capture
description: Start or end the day -- compiles today's captures from Telegram, vault, and TLDRs into a structured daily note through multi-turn conversation. Use when Keith says /daily, "today", "start the day", "end of day", "EOD", "what's on today", or anything about daily planning/review.
---

# K2B Daily Capture

## Core Model

`/daily` is a **multi-turn conversation**, not a one-shot generator. K2B harvests what it can find, presents a draft, asks about gaps, and Keith refines until it's right.

## Vault Path

`~/Projects/K2B-Vault`

## Workflow

### Step 1: Harvest Today's Captures

Gather from all available sources in parallel:

**a) Telegram messages from k2b-remote (Mac Mini):**
```bash
ssh macmini "sqlite3 ~/Projects/K2B/k2b-remote/store/k2b-remote.db \
  \"SELECT content, created_at FROM memories \
    WHERE chat_id='8394008217' \
    AND created_at >= $(date -u -d 'today 00:00' +%s)000 \
    ORDER BY created_at\""
```
On macOS the date command differs -- use:
```bash
ssh macmini "sqlite3 ~/Projects/K2B/k2b-remote/store/k2b-remote.db \
  \"SELECT content, created_at FROM memories \
    WHERE chat_id='8394008217' \
    AND created_at >= $(python3 -c 'import datetime; print(int(datetime.datetime.combine(datetime.date.today(), datetime.time.min).timestamp() * 1000))') \
    ORDER BY created_at\""
```
- Voice transcriptions (`[Voice transcribed]: ...`) are treated as normal text
- Messages are mixed across all contexts (SJM, Signhub, TalentSignals, personal) -- do NOT assume they're all about one topic

**b) Vault notes created or modified today:**
```bash
find ~/Projects/K2B-Vault/{Inbox,Notes,Daily} -name "*.md" -newer ~/Projects/K2B-Vault/Daily/$(date -v-1d +%Y-%m-%d).md 2>/dev/null
```
Or glob for today's date prefix in filenames.

**c) TLDR raw sources from today:**
Check for today's raw TLDRs in:
- `raw/tldrs/` for raw TLDR captures
- `raw/daily/` for any extracted insights or content seeds saved earlier today

**d) Yesterday's daily note:**
Read `~/Projects/K2B-Vault/Daily/$(date -v-1d +%Y-%m-%d).md` for open loops to carry forward.

### Step 2: Classify and Draft

Classify each captured item into the appropriate section:

- **SJM Work** -- recruitment, hiring, meetings, stakeholders, decisions
- **Signhub / TalentSignals / Agency at Scale** -- side venture activity
- **K2B Build** -- system building, features shipped, technical decisions (only from captures, NOT git log)
- **Insights** -- observations, patterns, things that surprised Keith
- **Content Seeds** -- anything that could become a LinkedIn post or video
- **Open Loops** -- unfinished items to carry forward to tomorrow

Rules:
- **Omit empty sections entirely.** If nothing fits a section, don't show it.
- A quiet day = a short note. That's fine.
- Use bullet points, not paragraphs.
- Don't hallucinate details. If a Telegram message is vague, include what's there and ask.
- Don't generate insights Keith didn't express. Use `> [!robot] K2B analysis` callout if surfacing a K2B-originated connection.
- Don't force Content Seeds if none naturally emerged.

### Step 3: Present Draft and Ask Questions

Show Keith the compiled draft, then ask targeted questions about gaps:

- "I see you mentioned [X] but no details -- what was the outcome?"
- "Nothing from SJM today -- quiet day or did I miss something?"
- "This message about [Y] -- is that SJM or TalentSignals?"
- "Any open loops to carry forward?"

Do NOT try to ask everything at once. Ask 2-3 questions max per round. Keith will fill in what matters.

### Step 4: Refine

Based on Keith's responses:
- Add, correct, or remove items
- Reclassify items if Keith says they belong elsewhere
- Show the updated draft
- Repeat until Keith confirms

### Step 5: Save

- Save to `~/Projects/K2B-Vault/Daily/YYYY-MM-DD.md` (auto-promote -- Daily/ notes never go through Inbox)
- If the file already exists (morning + evening use), **merge** new content into existing note rather than overwriting
- Use the k2b-vault-writer skill for the actual write
- After saving, append to `wiki/log.md` with cross-linked entities

**Channel-aware preview:**
- On Claude Code terminal: show the full note before saving
- On Telegram (k2b-remote): show compact summary (section headers + bullet counts), ask "Save? Or tell me what to change"

## Morning Mode

When Keith says `/daily` in the morning (or when no captures exist for today yet):

1. Pull yesterday's open loops
2. If any Telegram messages already sent today, include them
3. Otherwise: show open loops and say "Capture things as they happen today. /daily again tonight to compile."

Morning mode is brief. Don't prompt for a full daily plan.

## File Convention

Daily notes: `~/Projects/K2B-Vault/Daily/YYYY-MM-DD.md`

## Template

Use the daily-note template from `~/Projects/K2B-Vault/Templates/daily-note.md` for frontmatter structure. Sections are dynamic based on what has content.

## Frontmatter

```yaml
---
tags: [daily]
date: YYYY-MM-DD
type: daily-note
origin: keith
up: ""
---
```

## Cross-Linking

When creating or updating the daily note, add `[[wiki links]]`:

1. **People**: Link as `[[person_Firstname-Lastname]]`
2. **Projects**: Link as `[[project_name]]`
3. **Meetings**: If meeting notes exist for today, link as `[[YYYY-MM-DD_Meeting-Topic]]`
4. **Yesterday's note**: When carrying forward open loops, link as `[[YYYY-MM-DD]]`
5. **Linked Notes section**: At the bottom, collect all wiki links for graph visibility

Before linking, glob the vault to confirm the target exists. If a person or project doesn't have a note, create a stub.

## Usage Logging

After completing the main task:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-daily-capture\t$(echo $RANDOM | md5sum | head -c 8)\tcompiled daily note for YYYY-MM-DD" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Vault Redesign Awareness

- All captures go to raw/ subfolders first, then k2b-compile digests them into wiki/ pages.
- Meeting notes go to `raw/meetings/`, research to `raw/research/`, YouTube to `raw/youtube/`.
- TLDRs go to `raw/tldrs/` -- k2b-compile digests insights, content seeds, and action items into wiki pages.
- After the daily note is saved, any extracted insights or content seeds are saved to raw/daily/ and k2b-compile is triggered to digest them into wiki pages.
- When the daily note references items created today by other skills, link to wiki/ pages (the compiled output).

## Rules

- No em dashes. No AI cliches. No sycophancy.
- `origin: keith` always -- daily notes are Keith's own capture, K2B just organizes them.
- Keep it concise. Bullet points over paragraphs.
- A short daily note is better than a padded one.
- The conversation IS the skill. Don't rush to save -- iterate until Keith's satisfied.
- Use k2b-vault-writer conventions for all note creation and cross-linking.
