# K2B Phase 1 — Obsidian Vault + Core Skills

## Purpose

This document contains instructions for Claude Code to set up the K2B Obsidian vault and build the foundational skills. Feed this to a Claude Code session to execute.

## Prerequisites

- Obsidian installed on Mac (confirmed)
- Obsidian CLI enabled (Settings → General → bottom → CLI toggle ON)
- Claude Code installed and running
- This document accessible from Claude Code (either in project dir or referenced by path)

---

## Step 1: Locate and Configure Obsidian Vault

Before creating anything, find Keith's existing Obsidian vault or create a new one.

```
# Check if Obsidian CLI is available
obsidian --help

# List existing vaults
obsidian vault list

# If no K2B vault exists, create one:
obsidian vault create "K2B-Vault"

# Open the vault
obsidian vault open "K2B-Vault"
```

Get the vault path and store it. The vault path will be needed for all subsequent operations. Typical location: `~/Documents/K2B-Vault/` or similar.

## Step 2: Create Vault Directory Structure

Create the following folder structure inside the vault:

```
K2B-Vault/
├── 00-Inbox/
├── 01-Daily/
├── 02-Work/
│   ├── Meetings/
│   ├── Projects/
│   ├── People/
│   ├── Insights/
│   └── Decisions/
├── 03-Content/
│   ├── Ideas/
│   ├── Drafts/
│   ├── Published/
│   └── Calendar/
├── 04-Business/
│   ├── Signhub/
│   ├── BenAI/
│   └── AgencyAtScale/
├── 05-Knowledge/
│   ├── AI-Tools/
│   ├── Recruitment/
│   └── Resources/
├── 06-Personal/
└── Templates/
```

## Step 3: Create Note Templates

### Template: Daily Note
File: `Templates/daily-note.md`

```markdown
---
tags: [daily]
date: {{date}}
type: daily-note
---

# {{date}} — Daily Note

## Focus Today
- 

## Key Activities
- 

## Meetings
- 

## Insights & Observations
> Capture anything interesting — patterns, ideas, surprises

## Content Seeds
> Anything from today that could become a post, video, or teaching moment?

## End of Day
### What went well
- 

### What needs attention tomorrow
- 

### Open loops
- 
```

### Template: Meeting Note
File: `Templates/meeting-note.md`

```markdown
---
tags: [meeting]
date: {{date}}
type: meeting-note
project: 
participants: []
---

# Meeting: {{title}}

**Date**: {{date}}
**Participants**: 
**Context**: 

## Key Discussion Points
- 

## Decisions Made
- 

## Action Items
- [ ] 

## Insights
> What did I learn? What patterns do I see?

## Content Potential
> Could any of this become content? What angle?
```

### Template: Content Idea
File: `Templates/content-idea.md`

```markdown
---
tags: [content, idea]
date: {{date}}
type: content-idea
platform: [linkedin, youtube]
status: idea
source: 
---

# Content Idea: {{title}}

## Hook / Angle
> What makes this interesting? Why would someone stop scrolling?

## Core Insight
> The main point in 2-3 sentences

## Source Experience
> What real work experience triggered this idea?

## Key Talking Points
1. 
2. 
3. 

## Target Audience
> Who specifically benefits from this?

## Format Notes
> Short post? Long-form? Video? Thread?

## Draft Status
- [ ] Outline
- [ ] First draft
- [ ] Refined
- [ ] Ready to publish
```

### Template: Decision Log
File: `Templates/decision-log.md`

```markdown
---
tags: [decision]
date: {{date}}
type: decision
project: 
status: active
---

# Decision: {{title}}

**Date**: {{date}}
**Context**: 
**Stakeholders**: 

## The Decision
> What was decided?

## Why
> Rationale, constraints, factors considered

## Alternatives Considered
1. 
2. 

## Expected Outcome
> What should happen as a result?

## Review Date
> When to check if this was the right call?
```

### Template: Project Note
File: `Templates/project-note.md`

```markdown
---
tags: [project]
date: {{date}}
type: project
status: active
domain: sjm
---

# Project: {{title}}

## Objective
> What are we trying to achieve?

## Current Status
> Where are we now?

## Key Milestones
- [ ] 
- [ ] 

## Stakeholders
- 

## Open Questions
- 

## Related Notes
- 

## Updates
### {{date}}
- 
```

### Template: Person Note
File: `Templates/person-note.md`

```markdown
---
tags: [person]
type: person
organization: 
role: 
relationship: [colleague, stakeholder, report, external]
---

# {{name}}

**Organization**: 
**Role**: 
**Relationship**: 

## Context
> How do I know them? What's the working relationship?

## Communication Style
> How do they prefer to communicate? What matters to them?

## Key Interactions
### {{date}}
- 

## Notes
- 
```

## Step 4: Create the CLAUDE.md for K2B

This file should live in the K2B project root (wherever Claude Code sessions are started for K2B work). It tells Claude Code how to behave as K2B.

File: `CLAUDE.md` (in the Claude Code project root)

```markdown
# K2B — Keith's 2nd Brain

You are K2B, Keith's personal AI second brain. You run via Claude Code on Keith's Mac.

## Who Is Keith

Keith is the AVP Talent Acquisition at SJM Resorts (Macau). He also runs Signhub Tech Limited (HK), is Chief AI Officer in BenAI's Partner Network, and operates Agency at Scale. His content angle is showing how senior executives in traditional corporations use AI to 10x their effectiveness.

## Your Job

You help Keith with three things:
1. **Capture & organize** — daily work, meetings, insights into the Obsidian vault
2. **Surface & connect** — find patterns across notes, connect ideas, retrieve context
3. **Create & draft** — turn insights into content (LinkedIn posts, YouTube scripts, emails)

Execute. Don't explain what you're about to do. Just do it. If you need clarification, ask one short question.

## Your Environment

- **Obsidian vault**: [VAULT_PATH] (set during setup)
- All global Claude Code skills in ~/.claude/skills/
- MCP servers: Gmail, Google Calendar, Fireflies (when connected)
- Bash, file system, web search, all standard Claude Code tools

## Rules

- No em dashes. Ever.
- No AI cliches. No "Certainly!", "Great question!", "I'd be happy to", "As an AI".
- No sycophancy. No excessive apologies.
- Don't narrate. Don't explain your process. Just do the work.
- When creating Obsidian notes, always use the appropriate template structure.
- Always add YAML frontmatter with tags, date, and type.
- When capturing meeting notes, always extract action items and insights.
- When extracting insights, always flag potential content ideas.

## Slash Commands

### /daily
Generate today's daily note from the template. Check Google Calendar for meetings. Pre-populate what's known.

### /standup
Review active projects and recent daily notes. Produce a brief status across all work streams.

### /tldr
Summarize the current conversation. Extract key decisions, action items, and insights. Save as a note in the appropriate vault folder.

### /insight [topic]
Search vault notes for patterns related to [topic]. Synthesize what's been captured across meetings, decisions, and daily notes.

### /content
Review recent insights and daily notes from the past 7 days. Suggest content ideas based on interesting patterns, learnings, or experiences.

### /meeting [title]
Create a meeting note from template. If a Fireflies transcript is provided, process it: extract summary, decisions, action items, and insights.

## Email Safety

- NEVER send emails. Only draft.
- NEVER delete emails.
- Always confirm before creating any draft.
- Use specific search criteria.

## File Conventions

- Daily notes: `01-Daily/YYYY-MM-DD.md`
- Meeting notes: `02-Work/Meetings/YYYY-MM-DD_Meeting-Topic.md`
- Content ideas: `03-Content/Ideas/idea_short-slug.md`
- Projects: `02-Work/Projects/project_name.md`
- People: `02-Work/People/person_Firstname-Lastname.md`
- Decisions: `02-Work/Decisions/YYYY-MM-DD_decision-topic.md`
- Insights: `02-Work/Insights/insight_topic.md`
```

## Step 5: Create Core Skills

Skills should be placed in `~/.claude/skills/` for global access, or in the project's `.claude/skills/` directory.

### Skill: Daily Capture
File: `~/.claude/skills/k2b-daily-capture/SKILL.md`

```markdown
---
name: k2b-daily-capture
description: This skill should be used when Keith asks to create or update his daily note, do a daily review, or when starting a new work day. It handles the /daily slash command and daily note management in the K2B Obsidian vault.
---

# K2B Daily Capture

## Workflow

1. Read the daily note template from `[VAULT_PATH]/Templates/daily-note.md`
2. Check if today's daily note already exists at `[VAULT_PATH]/01-Daily/YYYY-MM-DD.md`
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

## Notes
- Always use YAML frontmatter with today's date and [daily] tag
- Keep the format clean and consistent
- Don't over-structure — Keith will fill in details naturally
```

### Skill: Meeting Processor
File: `~/.claude/skills/k2b-meeting-processor/SKILL.md`

```markdown
---
name: k2b-meeting-processor
description: This skill should be used when Keith provides a meeting transcript (from Fireflies or any other source), asks to process meeting notes, or uses the /meeting command. It extracts structured information from raw transcripts and creates organized Obsidian notes.
---

# K2B Meeting Processor

## Workflow

When a transcript is provided:

1. Read the meeting note template from `[VAULT_PATH]/Templates/meeting-note.md`
2. Analyze the transcript to extract:
   - **Participants**: Who was in the meeting
   - **Key Discussion Points**: Main topics covered (3-7 bullet points)
   - **Decisions Made**: Any explicit or implicit decisions
   - **Action Items**: Tasks with owners and deadlines where mentioned
   - **Insights**: Patterns, observations, or strategic points worth remembering
   - **Content Potential**: Anything that could become content (a teaching moment, a unique approach, a lesson learned)
3. Create the note at `[VAULT_PATH]/02-Work/Meetings/YYYY-MM-DD_Meeting-Topic.md`
4. If action items reference existing projects, create links: `[[project_name]]`
5. If people are mentioned who have existing person notes, create links: `[[person_Firstname-Lastname]]`
6. Update today's daily note to reference this meeting
7. If significant insights found, offer to create a standalone insight note

## Processing Guidelines
- Be concise. Keith wants signal, not noise.
- Action items should be specific and actionable, not vague.
- Insights should focus on patterns, not just facts.
- Always flag content potential — Keith's content comes from real work.
- If the transcript is from Fireflies, handle the Fireflies JSON format.

## Content Seed Detection
Flag as content potential if the meeting contains:
- A novel approach to a common recruitment challenge
- An AI tool or technique applied to traditional HR
- A lesson about organizational change or leadership
- A data point or metric that tells a story
- A moment where Keith's dual expertise (agency + in-house) created unique value
```

### Skill: Insight Extractor
File: `~/.claude/skills/k2b-insight-extractor/SKILL.md`

```markdown
---
name: k2b-insight-extractor
description: This skill should be used when Keith asks to find patterns, surface insights, review recent notes for themes, or uses the /insight command. It searches across the K2B Obsidian vault to synthesize connections and surface non-obvious patterns.
---

# K2B Insight Extractor

## Workflow

### For /insight [topic]:
1. Search the vault for notes related to [topic]:
   - Search file contents using `grep -r` or Obsidian CLI search
   - Look in Meetings, Insights, Daily notes, Projects, Decisions
2. Read the relevant notes
3. Synthesize:
   - What patterns appear across multiple notes?
   - What has changed over time?
   - What connections exist between different meetings/projects?
   - What is Keith repeatedly encountering?
4. Present findings as a brief synthesis (not a list of search results)
5. Offer to save as an insight note if the synthesis is valuable

### For /content (weekly content review):
1. Read all daily notes from the past 7 days
2. Read any new meeting notes from the past 7 days
3. Read any new insight notes
4. Identify:
   - Recurring themes (3+ mentions = pattern)
   - Interesting single events with teaching potential
   - Contrasts between AI approach and traditional approach
   - Metrics or results worth sharing
5. Generate 3-5 content ideas with:
   - Working title
   - Hook / angle
   - Source notes (linked)
   - Suggested platform (LinkedIn short post, LinkedIn long-form, YouTube)
6. Offer to create content idea notes for any Keith wants to pursue

## Insight Categories
When classifying insights, use these lenses:
- **Process**: How work gets done, workflow patterns
- **People**: Team dynamics, stakeholder management, leadership
- **Technology**: AI tools, automation, systems
- **Strategy**: Talent acquisition strategy, business direction
- **Culture**: Organizational culture, change management
- **Content**: Meta-observations about what resonates with audiences
```

### Skill: TLDR Capture
File: `~/.claude/skills/k2b-tldr/SKILL.md`

```markdown
---
name: k2b-tldr
description: This skill should be used when Keith uses the /tldr command to capture a summary of the current conversation. It extracts key decisions, action items, and insights from the conversation and saves them to the appropriate location in the K2B Obsidian vault.
---

# K2B TLDR Capture

## Workflow

1. Review the current conversation
2. Extract:
   - **Summary**: 3-5 bullet points of what was discussed/accomplished
   - **Decisions**: Any choices made
   - **Action Items**: Next steps with context
   - **Insights**: New understandings or connections discovered
   - **Content Seeds**: Anything worth turning into content
3. Determine the best save location:
   - If it was about a specific project → save in that project's folder
   - If it was a general brainstorm → save in 00-Inbox
   - If it was about content → save in 03-Content/Ideas
   - If it was about a meeting → append to the meeting note
4. Save with proper frontmatter and linking
5. Confirm what was saved and where

## Format

```yaml
---
tags: [tldr, {context-tags}]
date: YYYY-MM-DD
type: tldr
source: claude-code-session
---
```

## Notes
- Be ruthlessly concise. TLDR means TLDR.
- Action items should be copy-pasteable as tasks.
- Always link to related existing notes when possible.
```

## Step 6: Create Initial Vault Content

### Welcome/README note
File: `K2B-Vault/00-Inbox/Welcome to K2B.md`

```markdown
---
tags: [meta]
date: 2026-03-18
type: reference
---

# Welcome to K2B — Keith's 2nd Brain

This vault is managed by Claude Code with K2B skills.

## Quick Commands
- `/daily` — Create or update today's daily note
- `/standup` — Get a status briefing across all projects
- `/tldr` — Summarize this conversation and save it
- `/insight [topic]` — Surface patterns about a topic
- `/content` — Review recent work for content ideas
- `/meeting [title]` — Process a meeting transcript

## Vault Structure
- **00-Inbox**: Uncategorized captures
- **01-Daily**: Daily notes
- **02-Work**: SJM Resorts TA work (meetings, projects, people, insights, decisions)
- **03-Content**: Content pipeline (ideas, drafts, published, calendar)
- **04-Business**: Signhub, BenAI, Agency at Scale
- **05-Knowledge**: Reference material and learnings
- **06-Personal**: Personal notes
- **Templates**: Note templates

## How It Works
Everything in this vault is plain markdown. Claude Code reads and writes these files directly. Obsidian provides the visual layer — graph view, search, linking. Claude Code provides the intelligence — extraction, synthesis, creation.
```

## Step 7: Verify Setup

After creating everything:

1. Confirm vault structure exists: `find [VAULT_PATH] -type d | head -30`
2. Confirm templates exist: `ls [VAULT_PATH]/Templates/`
3. Confirm skills exist: `ls ~/.claude/skills/k2b-*/SKILL.md`
4. Open Obsidian and verify vault loads correctly
5. Test: Run `/daily` to create first daily note
6. Test: Run `/standup` to confirm it can read the vault

## What You Have After Phase 1

- A structured Obsidian vault tailored to Keith's role and workflow
- 5 note templates for consistent capture
- 4 Claude Code skills (daily capture, meeting processor, insight extractor, TLDR)
- A CLAUDE.md system prompt for K2B
- Slash commands for rapid interaction
- Ready to use from Claude Code terminal immediately
