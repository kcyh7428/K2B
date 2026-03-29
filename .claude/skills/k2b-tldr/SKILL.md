---
name: k2b-tldr
description: Capture a conversation summary -- extracts decisions, action items, insights, and content seeds into a vault note. Use when Keith says /tldr, "summarize this", "capture this", "save this conversation", or wants to save the key points from a session.
---

# K2B TLDR Capture

## Vault Path

`~/Projects/K2B-Vault`

## Workflow

1. Review the current conversation
2. Extract:
   - **Summary**: 3-5 bullet points of what was discussed/accomplished
   - **Decisions**: Any choices made
   - **Action Items**: Next steps with context
   - **Insights**: Technical/operational things learned during the session (e.g., "auth codes expire in 30 seconds"). These inform K2B's behavior and future sessions.
   - **Content Seeds**: Raw angles from the session that could become public content (e.g., "built a full publishing stack in 3 hours"). These are loose sparks, not fully formed ideas. They stay in the TLDR -- do NOT create separate content idea notes from them. Keith reviews content seeds during collective inbox review and promotes the ones worth developing.
3. Always save to `Inbox/` with filename format `YYYY-MM-DD_tldr-topic-slug.md`
4. Save with proper frontmatter and linking
5. **Update related project notes**: If the conversation involved progress on a project, use the vault-writer update workflow to:
   - Append a dated entry to the project note's `## Updates` section
   - Update `## Current Status` if the status meaningfully changed
   - Check off any completed milestones in `## Key Milestones`
   - Add new `[[wikilinks]]` to `## Related Notes` if new notes were created
6. Confirm what was saved and where

## Frontmatter Format

**MANDATORY: All TLDR notes go to Inbox/ and MUST include review-action and review-notes. See vault-writer Inbox Write Contract.**

```yaml
---
tags: [tldr, {context-tags}]
date: YYYY-MM-DD
type: tldr
origin: k2b-extract
source: claude-code-session
up: "[[MOC_K2B-System]]"
review-action:
review-notes: ""
---
```

Before saving, verify: review-action and review-notes are present. If they're missing, the note is broken and Keith can't triage it in Obsidian.

## Cross-Linking

When saving a TLDR note, always add `[[wiki links]]`:

1. **People**: Link any people mentioned as `[[person_Firstname-Lastname]]`.
2. **Projects**: Link any projects discussed as `[[project_name]]`.
3. **Meetings**: If the conversation was about a specific meeting, link as `[[YYYY-MM-DD_Meeting-Topic]]`.
4. **Source notes**: If referencing existing vault notes, link them directly.
5. Glob the vault before linking to confirm targets exist.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-tldr\t$(echo $RANDOM | md5sum | head -c 8)\tcaptured tldr for conversation" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Section Guidance

### Insights vs Content Seeds
These are different things serving different purposes:
- **Insights** = what K2B learned (technical, operational, process). Inform future K2B behavior. Example: "LinkedIn API requires --data-urlencode for secrets with base64 padding"
- **Content Seeds** = what Keith could publish about. Raw angles for his content pipeline. Example: "Built an entire AI-powered publishing stack in one 3-hour session"

A single session event can produce both: the insight is the technical learning, the content seed is the public-facing angle on the same event.

### Content Seeds Rules
- Content seeds live ONLY inside the TLDR note. Never auto-create separate content idea notes.
- Keep them as one-liners with just enough context to spark memory later.
- Not every session has content seeds. Don't force them.
- When Keith reviews inbox and says "promote this", the content seed gets extracted into a proper `content_*.md` note in `Notes/Content-Ideas/`.
- Some seeds won't resonate with Keith and that's fine -- they still help ideation by being visible during review.

## Notes
- Be ruthlessly concise. TLDR means TLDR.
- Action items should be copy-pasteable as tasks.
- Always link to related existing notes when possible.
- No em dashes, no AI cliches, no sycophancy
