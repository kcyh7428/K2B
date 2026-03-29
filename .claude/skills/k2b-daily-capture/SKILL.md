---
name: k2b-daily-capture
description: Start or end the day -- creates/updates today's daily note with calendar, open loops, and progress. Use when Keith says /daily, "today", "start the day", "end of day", "EOD", "what's on today", or anything about daily planning/review.
---

# K2B Daily Capture

## Vault Path

`~/Projects/K2B-Vault`

## Workflow

1. Read the daily note template from `~/Projects/K2B-Vault/Templates/daily-note.md`
2. Check if today's daily note already exists at `~/Projects/K2B-Vault/Daily/YYYY-MM-DD.md`
3. If it exists, open it and offer to update. If not, create it.
4. When creating:
   a. Use the template structure
   b. Check Google Calendar MCP for today's meetings (if available)
   c. Pre-populate the Meetings section
   d. Check yesterday's daily note for any "attention tomorrow" or "open loops" items
   e. Carry forward unresolved items
5. When updating at end of day:
   a. Review what was captured
   b. Prompt for "What went well" and "What needs attention"
   c. Scan for any content seeds (interesting patterns or learnings)
   d. If content seeds found, offer to create content idea notes
   e. **Update related project notes**: If completed items reference a project, use the vault-writer update workflow to append progress to the project note's `## Updates` section and check off any completed milestones

## File Convention

Daily notes go to: `~/Projects/K2B-Vault/Daily/YYYY-MM-DD.md`

## Cross-Linking

When creating or updating the daily note, always add `[[wiki links]]`:

1. **Meetings**: List each meeting in the Meetings section as `[[YYYY-MM-DD_Meeting-Topic]]`. Glob for `Notes/YYYY-MM-DD_*.md` to find today's meeting notes.
2. **People**: When people are mentioned in activities or meetings, link them as `[[person_Firstname-Lastname]]`.
3. **Projects**: When projects are mentioned, link them as `[[project_name]]`.
4. **Linked Notes section**: At the bottom, collect all `[[wiki links]]` referenced anywhere in the note for easy graph visibility.
5. **Yesterday's note**: When carrying forward open loops, link to yesterday's note as `[[YYYY-MM-DD]]`.

Before linking, glob the vault to confirm the target note exists. If it doesn't exist and it's a person or project, create a stub note from the appropriate template.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-daily-capture\t$(echo $RANDOM | md5sum | head -c 8)\tcreated daily note for YYYY-MM-DD" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes
- Always use YAML frontmatter with today's date, [daily] tag, and `origin: keith` (daily notes are Keith's own capture)
- Keep the format clean and consistent
- Don't over-structure -- Keith will fill in details naturally
- No em dashes, no AI cliches, no sycophancy
