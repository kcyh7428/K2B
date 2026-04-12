---
name: k2b-linkedin
description: Create LinkedIn posts -- draft, revise, generate images, and publish from vault content. Use when Keith says /linkedin, "draft a LinkedIn post", "post to LinkedIn", "publish on LinkedIn", "write a post about", or wants to check post performance.
---

# K2B LinkedIn

Draft LinkedIn posts from Keith's content pipeline, generate post images, and publish via the LinkedIn API.

## Vault Path

`~/Projects/K2B-Vault`

## Vault Redesign Note

LinkedIn drafts remain in `review/` as an exception to the content-ideas-only rule -- they need Keith's approval before publishing. This is tracked in k2b-inbox as an expected review item type alongside k2b-generate content ideas.

On publish, update `wiki/content-pipeline/index.md` and append to `wiki/log.md`.

## Key Paths

- Content Ideas: `wiki/content-pipeline/content_*.md`
- Insights: `wiki/insights/insight_*.md`
- Drafts: `review/linkedin_YYYY-MM-DD_slug.md` (stays in review/ until published -- exception to content-ideas-only rule)
- Brand Voice: `~/Projects/K2B/.claude/skills/k2b-linkedin/resource.md`
- Publish Script: `~/Projects/K2B/scripts/linkedin-publish.sh`
- Status Script: `~/Projects/K2B/scripts/linkedin-status.sh`
- Token: `~/.linkedin_token`
- Auth Script: `~/Projects/signhub-io/scripts/linkedin-auth.sh`
- Generated Images: `Assets/images/linkedin_YYYY-MM-DD_slug.png`

## Vault Query Tools

- **Dataview DQL** (structured frontmatter queries): `~/Projects/K2B/scripts/vault-query.sh dql '<TABLE query>'`
- **Full-text search**: `mcp__obsidian__search` MCP tool or `vault-query.sh search "<term>"`
- **Read file**: `mcp__obsidian__get_file_contents` or Read tool
- **List files**: `mcp__obsidian__list_files_in_dir`

Prefer DQL queries over Glob+Read+Filter when querying frontmatter across multiple files. Use `mcp__obsidian__search` instead of Grep for vault-wide content search.

## Commands

### /linkedin draft \<source\>
Draft a LinkedIn post. Source can be:
- A content idea slug: `/linkedin draft corporate-ai-restrictions`
- Any vault note path or slug: `/linkedin draft 2026-03-26` (daily note), `/linkedin draft insight_autoresearch-udit-review`
- A direct topic: `/linkedin draft "how I restructured my team using AI"`

### /linkedin draft
No arguments -- list adopted content ideas and let Keith pick.

### /linkedin publish
Publish the most recent draft via LinkedIn API. Requires Keith's confirmation.

### /linkedin revise \<draft\>
Revise an existing draft based on Keith's feedback. Skips the Socratic challenge.

### /linkedin image \<draft\>
Generate or regenerate a post image for an existing draft.

### /linkedin status
Show recent LinkedIn posts. Engagement metrics available after Community Management API approval.

---

## Workflow: /linkedin draft \<source\>

### Phase 1 -- Identify Source and Gather Material

1. **Determine source type:**
   - If slug matches `wiki/content-pipeline/content_*.md` -- content idea (richest source)
   - If slug matches `wiki/insights/insight_*.md` -- insight note
   - If slug matches `raw/meetings/YYYY-MM-DD_*.md` or `Daily/YYYY-MM-DD.md` -- meeting/daily note
   - If slug matches `wiki/context/*.md` -- context note
   - If quoted string -- direct topic from Keith (no vault source)

2. **Read the source note.** For content ideas, extract: Hook, Core Insight, Talking Points, Format Notes, Related links.

3. **Read the brand voice document** (`resource.md`).

4. **Search the vault for related material:**
   - Follow `[[wiki links]]` from the source note -- read linked notes
   - Use `mcp__obsidian__search` to find notes mentioning key terms from the source (searches across raw/, wiki/, Daily/, review/)
   - Look for meetings, insights, daily notes, context that mention the same topics
   - Collect Keith's actual words, specific details, numbers, anecdotes

5. **Check for existing drafts** of this source (glob `review/linkedin_*_slug.md`). If found, ask Keith: revise existing draft or start fresh?

### Phase 2 -- Extract Viewpoints

From gathered material, extract and present to Keith:

- 3-5 core viewpoints Keith has expressed about this topic
- Specific anecdotes with concrete details (names redacted if sensitive, but real situations)
- Contrasts between Keith's approach and the conventional approach
- The single strongest "scroll-stopper" angle

Present these as bullet points. **Do not draft yet.** Keith needs to see the raw material before the Socratic step.

### Phase 3 -- Socratic Challenge

Ask Keith 2-3 sharpening questions. Purpose: push past the obvious angle to find the post that only Keith can write.

Good Socratic questions:
- "You have 5 angles here. If you could only say ONE thing, which changes the reader's thinking most?"
- "Someone who disagrees would say [X]. What would you tell them?"
- "The hook mentions [Y]. Is that specific enough to stop someone scrolling past 50 other posts?"
- "This insight came from [meeting/experience]. What's the part you keep thinking about?"
- "Who specifically needs to hear this? What are they struggling with right now?"

Wait for Keith's responses. His answers are the draft fuel.

**Skip this phase** when:
- Keith says "just draft it" or uses `/linkedin revise`
- A draft already exists and Keith wants a revision
- Keith provides the angle directly ("write about X from the angle of Y")

### Phase 4 -- Draft

Using Keith's responses, the source material, and resource.md:

1. **Write the hook** (first 2 lines). Must be under 210 characters (LinkedIn's "see more" cutoff). Specific, surprising, or contrarian.

2. **Write the body** following the structure from resource.md:
   - Context (2-3 lines): the situation, constraint, or setup
   - Story/Detail (3-6 lines): what actually happened, with specifics
   - Insight (2-3 lines): the non-obvious takeaway

3. **Write the close** (1-2 lines): call to reflection or forward-looking thought. Not "follow for more."

4. **Add hashtags** if relevant (0-3, at the end, never inline).

5. **Run the quality checklist** (see below). Fix any failures before presenting.

### Phase 5 -- Present and Save

1. Show the draft to Keith in a code block
2. Show character count and hook length
3. Ask: approve, revise, or scrap?

**If approved:**
4. Save to `review/linkedin_YYYY-MM-DD_slug.md` with frontmatter (see Draft File Format below)
5. Include the full draft under `## Draft`
6. Include source material summary under `## Source Material`
7. Proceed to Phase 6 (image generation)

**If revise:** take Keith's feedback, redraft (skip Socratic), present again.
**If scrap:** done. No file saved.

### Phase 6 -- Generate Post Image

After text is approved:

1. Derive an image prompt from the post's hook + core insight. Go conceptual, not literal. Think editorial illustration, not stock photo.
2. Generate via MiniMax: `mcp__minimax__text_to_image` with aspect ratio `4:3` (LinkedIn feed optimal)
3. Save to `Assets/images/linkedin_YYYY-MM-DD_slug.png`
4. Show the image to Keith
5. Ask: approve, regenerate with different prompt, or skip image?
6. If approved, embed in draft note: `![[Assets/images/linkedin_YYYY-MM-DD_slug.png]]`

---

## Workflow: /linkedin draft (no arguments)

1. Query content ideas with LinkedIn platform:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE platform, status, source FROM "wiki/content-pipeline" WHERE contains(platform, "linkedin")'
   ```
2. Query insights with content potential:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE domain, content-potential FROM "wiki/insights" WHERE content-potential = true'
   ```
3. Show a numbered list:

```
Available content sources for LinkedIn:

Content Ideas:
  1. AI-Native Person Enters a Corporation (corporate-ai-restrictions) -- idea
  2. Loaded My Career Into AI in 10 Minutes (loaded-career-into-ai) -- idea

Insights with content potential:
  3. [insight title] -- insight
```

6. Keith picks a number. Proceed to draft workflow.

---

## Workflow: /linkedin publish

1. Find the most recent draft: glob `review/linkedin_*.md`, sort by date, take latest with `status: draft`
   - Or if Keith specifies a draft, use that one
2. Read the draft. Show Keith:
   - Full post text
   - Character count
   - Whether an image is attached
   - Source content idea
3. **Ask Keith to confirm**: "Publish this to LinkedIn? [text-only / with image]"
4. On confirmation:
   a. If image exists, run: `scripts/linkedin-publish.sh <draft-path> <image-path>`
   b. If text-only, run: `scripts/linkedin-publish.sh <draft-path>`
   c. On success:
      - Update draft frontmatter: `status: published`, `post-urn: <urn>`
      - Update source content idea: add `## Published Posts` section with link to draft
      - Log to resource.md Post Performance table (date, slug, chars, image y/n)
      - **Update `wiki/content-pipeline/index.md`** if source content idea was updated
      - **Append to `wiki/log.md`**: record publish action with post-urn
   d. On failure:
      - If 401: "Token expired. Run: `cd ~/Projects/signhub-io/scripts && ./linkedin-auth.sh`"
      - If rate limit: "LinkedIn rate limit hit. Try again later."
      - Other: show the error

---

## Workflow: /linkedin revise \<draft\>

1. Read the existing draft from `review/linkedin_*.md`
2. Read Keith's feedback (from conversation or `review-notes` frontmatter)
3. Read resource.md
4. **Skip Phases 2-3** (Socratic challenge already done)
5. Redraft based on feedback
6. Present revised draft + character count
7. If approved, update the existing file (Edit, not Write -- preserve frontmatter)

---

## Workflow: /linkedin image \<draft\>

1. Read the draft
2. Extract the hook and core insight
3. Generate image prompt (conceptual, editorial, not literal)
4. Generate via `mcp__minimax__text_to_image` with aspect ratio `4:3`
5. Save to `Assets/images/linkedin_YYYY-MM-DD_slug.png`
6. Show to Keith. Approve, regenerate, or skip.

---

## Workflow: /linkedin status

1. Run `scripts/linkedin-status.sh`
2. Parse and display results
3. If Community Management API is not yet approved, note:
   "Engagement metrics pending -- Community Management API under review."
4. Cross-reference posts with vault drafts (match by post-urn in frontmatter)

---

## Quality Checklist

Run these checks against every draft before presenting to Keith. Fix failures automatically:

- [ ] Hook lands in first 2 lines, under 210 characters
- [ ] Total length 400-1500 characters
- [ ] No em dashes (unicode U+2014 or U+2013)
- [ ] No AI cliches (check against resource.md avoid list)
- [ ] References a specific Keith experience (not generic advice anyone could give)
- [ ] 0-3 hashtags, at the end, never inline
- [ ] 0-2 emoji maximum, never as bullet points
- [ ] No external links in post body
- [ ] Clear closing takeaway or call to reflection
- [ ] Does not start with a question

---

## Draft File Format

```yaml
---
tags: [linkedin, draft, content]
date: YYYY-MM-DD
type: linkedin-draft
origin: k2b-extract
source: "[[content_slug]]"
status: draft
post-urn: ""
image: ""
review-action:
review-notes: ""
up: "[[MOC_Content-Pipeline]]"
---

# LinkedIn Draft: [Title]

## Draft

[The actual post text goes here. Plain text, no markdown formatting.
This is what gets extracted by linkedin-publish.sh and posted to LinkedIn.]

## Source Material

- Source idea: [[content_slug]]
- Related notes consulted: [[note1]], [[note2]]
- Keith's angle: [1-line summary of the angle chosen during Socratic challenge]

## Generated Image

![[Assets/images/linkedin_YYYY-MM-DD_slug.png]]
```

---

## Cross-Linking

- Every draft links back to its source note (`[[content_slug]]`, `[[insight_slug]]`, etc.)
- Every draft links to vault notes that informed it
- When published, update the source content idea with a link to the draft under `## Published Posts`
- Update `wiki/content-pipeline/index.md` with a link to the draft

---

## Content Pillars (from resource.md)

1. **AI in traditional corporations** -- flagship. How a senior exec uses AI daily without IT approval.
2. **Recruitment transformation** -- TA strategy, mass hiring, AI in recruiting.
3. **Building in public** -- K2B, TalentSignals, the autoresearch loop itself.

---

## Notes

- No em dashes. Ever.
- No AI cliches. No "Certainly!", "Great question!", sycophancy.
- Content ideas originate from Keith. K2B extracts and shapes, not generates from nothing.
- `origin: k2b-extract` for all drafts (derived from Keith's input).
- Keith must confirm before any post is published. Never auto-publish.
- If token is expired (401 errors), direct Keith to re-auth. Do not attempt to refresh automatically.

## Usage Logging

After completing the main task:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-linkedin\t$(echo $RANDOM | md5sum | head -c 8)\taction: DESCRIPTION" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```
