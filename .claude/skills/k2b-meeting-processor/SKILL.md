---
name: k2b-meeting-processor
description: Capture meeting notes -- processes transcripts into structured vault notes with summary, decisions, action items, and insights. Use when Keith says /meeting, provides a transcript, or mentions "meeting", "Fireflies", "meeting notes", or "process this call".
---

# K2B Meeting Processor

## Vault Path

`~/Projects/K2B-Vault`

## Workflow

When a transcript is provided:

1. Read the meeting note template from `~/Projects/K2B-Vault/Templates/meeting-note.md`
2. Analyze the transcript to extract:
   - **Participants**: Who was in the meeting
   - **Key Discussion Points**: Main topics covered (3-7 bullet points)
   - **Decisions Made**: Any explicit or implicit decisions
   - **Action Items**: Tasks with owners and deadlines where mentioned
   - **Insights**: Patterns, observations, or strategic points worth remembering
   - **Content Potential**: Anything that could become content (a teaching moment, a unique approach, a lesson learned)
3. Create the note at `~/Projects/K2B-Vault/Notes/Work/YYYY-MM-DD_Meeting-Topic.md` (auto-promote -- bypasses Inbox)
4. **Run the vault-writer post-write pass** (see Post-Write Contract below)
5. Update today's daily note to add `[[YYYY-MM-DD_Meeting-Topic]]` in the Meetings section
6. **Update related project notes**: If the meeting is tied to a project, use the vault-writer update workflow to:
   - Append a dated entry to the project note's `## Updates` section summarizing what the meeting covered
   - Check off any milestones that were completed or confirmed
   - Add `[[YYYY-MM-DD_Meeting-Topic]]` to the project's `## Related Notes`
7. If significant insights found, offer to create a standalone insight note in `Notes/Insights/`

## Processing Guidelines
- Be concise. Keith wants signal, not noise.
- Action items should be specific and actionable, not vague.
- Insights should focus on patterns, not just facts.
- Always flag content potential -- Keith's content comes from real work.
- If the transcript is from Fireflies, handle the Fireflies JSON format.

## Content Seed Detection
Flag as content potential if the meeting contains:
- A novel approach to a common recruitment challenge
- An AI tool or technique applied to traditional HR
- A lesson about organizational change or leadership
- A data point or metric that tells a story
- A moment where Keith's dual expertise (agency + in-house) created unique value

## File Convention

Meeting notes go to: `~/Projects/K2B-Vault/Notes/Work/YYYY-MM-DD_Meeting-Topic.md` (auto-promote, bypasses Inbox)

## Post-Write Contract

After saving the meeting note, run the vault-writer cross-link pass:
1. **Cross-link**: Update person/project pages with backlinks (see Cross-Linking below)
2. **Index update**: Add/update a row in `Notes/Work/index.md` with `[[filename]]`, one-line summary, date
3. **System log**: Append entry to `System/log.md`:
   ```markdown
   ## [YYYY-MM-DD HH:MM] k2b-meeting-processor | Meeting Title
   - Created: Notes/Work/YYYY-MM-DD_Meeting-Topic.md
   - Cross-linked: [list of entity pages updated]
   - Index updated: Notes/Work/index.md
   ```

## Cross-Linking

When creating a meeting note, always add `[[wiki links]]`:

1. **Participants**: In the Participants line, list each person as `[[person_Firstname-Lastname]]`. Glob `Notes/People/person_*.md` to check existing person notes.
2. **Person stubs**: If a participant doesn't have a person note yet, create a stub from the person-note template at `Notes/People/person_Firstname-Lastname.md` with their organization and role pre-filled from the transcript.
3. **Projects**: Link referenced projects as `[[project_name]]` in the body and in the Project frontmatter field.
4. **Decisions**: If a decision is logged separately, link as `[[YYYY-MM-DD_decision-topic]]`.
5. **Daily note**: Add `[[YYYY-MM-DD_Meeting-Topic]]` to today's daily note Meetings section.
6. **Person note backlinks**: For each participant with a person note, append this meeting under their Key Interactions section as `### YYYY-MM-DD\n- [[YYYY-MM-DD_Meeting-Topic]]`.
7. **Linked Notes section**: Collect all wiki links at the bottom of the meeting note.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-meeting-processor\t$(echo $RANDOM | md5sum | head -c 8)\tprocessed meeting transcript: TITLE" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes
- No em dashes, no AI cliches, no sycophancy
- Always add YAML frontmatter with tags, date, type, origin, up, project, and participants
- Always set `origin: k2b-extract` in frontmatter (these notes are derived from Keith's meeting transcripts)
- Always set `up: "[[MOC_SJM-Work]]"` (or appropriate MOC based on meeting domain)
- Use the k2b-vault-writer skill conventions for all note creation and updates
