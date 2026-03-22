# K2B — Migration from KIRA

## What's Being Sunset

### AirTable K.I.R.A. Base (appDe0hmbjYSFpjP0)
**Status**: To be archived, not deleted yet

Current tables:
- `Inputs` — Processing tracker for incoming data → **Replaced by**: Obsidian `00-Inbox/` + daily notes
- `Summaries` — LLM-generated summaries → **Replaced by**: Obsidian meeting notes + insight notes
- `Action Items` — Task list → **Replaced by**: Obsidian daily notes (action items section) + project notes
- `Projects & Work` — Work streams → **Replaced by**: Obsidian `02-Work/Projects/`
- `Automation Components` — n8n workflow configs → **Sunset**: No n8n layer in K2B
- `People` — Contacts/stakeholders → **Replaced by**: Obsidian `02-Work/People/`
- `Professional Achievements` — Keith's background → **Replaced by**: Obsidian `05-Knowledge/` + brand voice doc
- `Fireflies Integration` — Meeting transcript intake → **Replaced by**: K2B meeting processor skill
- `Summaries copy` — Backup table → **Sunset**

### Supabase (wlqgohlaltniaowprahl)
**Status**: Deferred. Not needed for MVP. Revisit after Phase 1 evaluation.

### n8n Workflows
**Status**: Sunset. Processing handled by Claude Code skills directly.

## Data Worth Migrating

Before archiving the AirTable base, consider extracting:

### From `Action Items` table
Any open/in-progress items should become entries in the Obsidian vault.
```
Prompt for Claude Code:
"Read the Action Items table from AirTable base appDe0hmbjYSFpjP0. 
Filter for Status = 'To Do' or 'In Progress'. 
For each item, create a task entry in today's daily note or the relevant project note in my Obsidian vault."
```

### From `Projects & Work` table
Active projects should have Obsidian project notes created.
```
Prompt for Claude Code:
"Read the Projects & Work table from AirTable base appDe0hmbjYSFpjP0.
Filter for Status = 'Active'.
For each project, create a project note in [VAULT_PATH]/02-Work/Projects/ using the project template."
```

### From `People` table
Key contacts should have person notes.
```
Prompt for Claude Code:
"Read the People table from AirTable base appDe0hmbjYSFpjP0.
For each person, create a person note in [VAULT_PATH]/02-Work/People/ using the person template."
```

### From `Professional Achievements` table
Keith's achievement history is valuable for content and brand voice.
```
Prompt for Claude Code:
"Read the Professional Achievements table from AirTable base appDe0hmbjYSFpjP0.
Create a summary document at [VAULT_PATH]/05-Knowledge/Resources/professional-background.md 
with all achievements organized chronologically."
```

## Migration Approach

**Don't migrate everything at once.** Instead:

1. Start fresh with the Obsidian vault (Phase 1)
2. Migrate only active/open items from AirTable
3. Let old completed items stay in AirTable as an archive
4. If you need to reference something old, you still have AirTable access
5. After 3 months of K2B usage, decide whether to fully archive AirTable

## What This Project Space Becomes

This Claude.ai project (formerly the KIRA project) becomes the **K2B Knowledge Base** — the meta-layer that stores:

- K2B architecture documentation
- Build instructions for each phase
- The ClaudeClaw mega prompt
- Claude Code prompt sheets
- Migration notes (this file)

When you start a Claude Code session to build or modify K2B, you reference these documents. They're the "instructions for building the builder."

## Updated Project Files

| Old File | New File | Purpose |
|----------|----------|---------|
| PLANNING.md - KIRA Architecture | K2B_ARCHITECTURE.md | Master plan |
| TASK.md - KIRA Project Tasks | K2B_PROMPT_SHEET.md | Actionable prompts |
| AirTable Schema Details | K2B_MIGRATION.md (this file) | Transition reference |
| ClaudeClaw Mega Prompt | ClaudeClaw___Mega_Prompt (keep as-is) | Phase 2 build spec |
| — (new) | K2B_PHASE1_VAULT_AND_SKILLS.md | Foundation build |
| — (new) | K2B_PHASE2_CLAUDECLAW.md | Remote access build |
| — (new) | K2B_PHASE3_CONTENT_PIPELINE.md | Content pipeline build |
