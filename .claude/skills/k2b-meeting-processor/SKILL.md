---
name: k2b-meeting-processor
description: This skill should be used when Keith provides a meeting transcript (from Fireflies or any other source), asks to process meeting notes, or uses the /meeting command. It extracts structured information from raw transcripts and creates organized Obsidian notes. Use this whenever Keith mentions "meeting", "transcript", "Fireflies", "meeting notes", "process this call", or provides a transcript to summarize.
---

# K2B Meeting Processor

## Vault Path

`/Users/keithmbpm2/Projects/K2B-Vault`

## Workflow

When a transcript is provided:

1. Read the meeting note template from `/Users/keithmbpm2/Projects/K2B-Vault/Templates/meeting-note.md`
2. Analyze the transcript to extract:
   - **Participants**: Who was in the meeting
   - **Key Discussion Points**: Main topics covered (3-7 bullet points)
   - **Decisions Made**: Any explicit or implicit decisions
   - **Action Items**: Tasks with owners and deadlines where mentioned
   - **Insights**: Patterns, observations, or strategic points worth remembering
   - **Content Potential**: Anything that could become content (a teaching moment, a unique approach, a lesson learned)
3. Create the note at `/Users/keithmbpm2/Projects/K2B-Vault/Notes/YYYY-MM-DD_Meeting-Topic.md`
4. **Cross-link the note** (see Cross-Linking section below)
5. Update today's daily note to add `[[YYYY-MM-DD_Meeting-Topic]]` in the Meetings section
6. **Update related project notes**: If the meeting is tied to a project, use the vault-writer update workflow to:
   - Append a dated entry to the project note's `## Updates` section summarizing what the meeting covered
   - Check off any milestones that were completed or confirmed
   - Add `[[YYYY-MM-DD_Meeting-Topic]]` to the project's `## Related Notes`
7. If significant insights found, offer to create a standalone insight note

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

Meeting notes go to: `/Users/keithmbpm2/Projects/K2B-Vault/Notes/YYYY-MM-DD_Meeting-Topic.md`

## Cross-Linking

When creating a meeting note, always add `[[wiki links]]`:

1. **Participants**: In the Participants line, list each person as `[[person_Firstname-Lastname]]`. Glob `Notes/People/person_*.md` to check existing person notes.
2. **Person stubs**: If a participant doesn't have a person note yet, create a stub from the person-note template at `Notes/People/person_Firstname-Lastname.md` with their organization and role pre-filled from the transcript.
3. **Projects**: Link referenced projects as `[[project_name]]` in the body and in the Project frontmatter field.
4. **Decisions**: If a decision is logged separately, link as `[[YYYY-MM-DD_decision-topic]]`.
5. **Daily note**: Add `[[YYYY-MM-DD_Meeting-Topic]]` to today's daily note Meetings section.
6. **Person note backlinks**: For each participant with a person note, append this meeting under their Key Interactions section as `### YYYY-MM-DD\n- [[YYYY-MM-DD_Meeting-Topic]]`.
7. **Linked Notes section**: Collect all wiki links at the bottom of the meeting note.

## Notes
- No em dashes, no AI cliches, no sycophancy
- Always add YAML frontmatter with tags, date, type, project, and participants
