# K2B Migration Prompt

Paste this prompt into each Claude Project on claude.ai to extract data for your Obsidian vault.

---

## The Prompt

Copy everything below this line and paste it into the Claude Project chat:

---

I'm migrating data from this Claude Project into my Obsidian second brain (K2B). I need you to review ALL conversations in this project and extract a structured summary. Output it as a single markdown document with these exact sections:

```
# Migration Export: [Project Name]

## Project Summary
- **Name**: [short name, no spaces, use hyphens]
- **Domain**: [one of: sjm, signhub, talentsignals, agency-at-scale, personal, k2b]
- **Status**: [active, completed, paused, archived]
- **Description**: [2-3 sentences: what this project is, what it does, current state]

## Key Decisions
List every significant decision made across all conversations. For each:
- **Decision**: [what was decided]
- **Date**: [approximate if exact unknown]
- **Rationale**: [why this choice was made]
- **Alternatives considered**: [what was rejected and why]

## People Involved
For each person mentioned across conversations:
- **Name**: [Firstname Lastname]
- **Organization**: [company/entity]
- **Role**: [their role]
- **Relationship**: [colleague, partner, client, stakeholder, external]
- **Context**: [how they relate to this project]

## Active Action Items
Any open tasks, next steps, or unfinished work:
- [ ] [task description] -- [owner if known] -- [deadline if known]

## Completed Milestones
Key things that were accomplished:
- [what was done] -- [approximate date]

## Insights & Learnings
Non-obvious knowledge gained from this project. Things that would be useful to remember:
- **Insight**: [the learning]
- **Why it matters**: [context]

## Technical Architecture (if applicable)
- Tools/platforms used
- How components connect
- Key design decisions

## Content Seeds
Anything from this project that could become content (LinkedIn, YouTube):
- **Idea**: [topic]
- **Angle**: [what makes it interesting]
- **Source**: [which conversation or decision it comes from]

## Raw Context
Anything else important that doesn't fit above. Include specific details, numbers, URLs, configurations, or context that would be lost if this project were archived.
```

Be thorough. Go through every conversation in this project. I'd rather have too much than too little. Use real names, real details -- this is going into my private vault, not published anywhere.

---

## After Getting the Output

1. Copy Claude's entire response
2. Save it as a text file at: `/Users/keithmbpm2/Projects/K2B/migration-exports/[project-name].md`
   - Use lowercase, hyphens for spaces: `talentsignals.md`, `kira.md`, `agency-at-scale.md`
3. Repeat for each Claude Project
4. Once all exports are saved, tell K2B: "Process the migration exports"

## Projects to Export

- [ ] TalentSignals
- [ ] KIRA (old second brain)
- [ ] Agency at Scale
- [ ] Any other Claude Projects with useful context
