# K2B Phase 3 — Content Pipeline

## Purpose

Build the skills that transform Keith's daily work insights into publishable content for LinkedIn and YouTube. This is the "content is a byproduct of work" layer.

## Prerequisites

- Phase 1 complete (vault populated with at least 1-2 weeks of daily notes, meetings, insights)
- Ideally Phase 2 running (so content ideas can be captured from anywhere)

## Content Strategy Context

### Keith's Content Angle
**"How a senior executive in traditional corporations uses AI to be effective"**

Target audience: Mid-to-senior professionals in traditional industries (hospitality, real estate, manufacturing) who are curious about AI but operate in environments where it hasn't penetrated yet.

### Content Pillars
1. **AI in Recruitment** — Practical applications of AI in talent acquisition (sourcing, screening, employer branding, analytics)
2. **Executive AI Adoption** — How to introduce AI tools in conservative corporate environments without triggering resistance
3. **Workflow Automation** — Turning repetitive corporate processes into AI-assisted workflows
4. **Building a Second Brain** — How Keith uses Claude Code + Obsidian as a senior executive (meta-content about this very system)
5. **Bridging Two Worlds** — Keith's unique perspective from agency-side and in-house recruitment

### Platform Strategy
- **LinkedIn**: Regular posts (2-3/week). Mix of short observations, longer thought pieces, and occasional frameworks/tools. Professional tone but authentic.
- **YouTube**: Weekly or bi-weekly videos. Deeper dives, tutorials, walkthroughs. Show, don't just tell. Screen recordings of actual AI usage.

---

## Skills to Build

### Skill: Brand Voice Generator
File: `~/.claude/skills/k2b-brand-voice/SKILL.md`

```markdown
---
name: k2b-brand-voice
description: This skill should be used when Keith wants to create or update his brand voice definition, or when any other content skill needs to reference his writing style. It manages the brand voice document that governs all content output.
---

# K2B Brand Voice

## Workflow

### Initial Setup (run once)
1. Guide Keith through a brand voice questionnaire:
   - How do you talk about your work when excited?
   - What phrases do you naturally use?
   - Who do you admire as a communicator? Why?
   - What tone turns you off in content?
   - Read 5-10 of Keith's existing posts/messages for style patterns
2. Generate a brand voice document
3. Save to `[VAULT_PATH]/05-Knowledge/Resources/brand-voice.md`

### Brand Voice Reference
When any content skill needs to check Keith's voice, read:
`[VAULT_PATH]/05-Knowledge/Resources/brand-voice.md`

## Keith's Voice (baseline — to be refined through questionnaire)
- **Tone**: Direct, practical, no-BS. Shares real experiences, not theory.
- **Perspective**: Senior executive who actually does the work, not an outside commentator.
- **Language**: Professional but conversational. Uses concrete examples over abstractions.
- **Avoid**: Buzzwords for their own sake, hype, "AI will replace everyone" fear-mongering, AI cliches.
- **Embrace**: Specific metrics, real stories, honest failures, practical "here's how" details.
- **Signature move**: Bridging the gap — showing how cutting-edge AI applies in traditional corporate reality.
```

### Skill: LinkedIn Post Drafter
File: `~/.claude/skills/k2b-linkedin/SKILL.md`

```markdown
---
name: k2b-linkedin
description: This skill should be used when Keith wants to create a LinkedIn post, or when the content review process identifies a content idea suitable for LinkedIn. It drafts posts in Keith's brand voice, drawing from vault insights and experiences.
---

# K2B LinkedIn Post Drafter

## Workflow

### From Content Idea Note
1. Read the content idea note
2. Read Keith's brand voice from `[VAULT_PATH]/05-Knowledge/Resources/brand-voice.md`
3. Read any linked source notes (meetings, insights, daily notes that inspired this)
4. Draft the post

### From Scratch
1. Ask Keith for the topic or insight
2. Search the vault for related notes to add depth
3. Read brand voice
4. Draft the post

### Post Structure Options

**Short Observation (< 300 words)**
- Hook line (pattern interrupt or bold statement)
- 3-5 sentences of context/story
- The insight or lesson
- Call to engagement (question, not "like and share")

**Thought Piece (300-600 words)**
- Hook line
- The situation/context
- What Keith actually did (specific, real)
- What happened / what he learned
- The principle or framework
- Application for the reader
- Closing question

**Framework/Tool Share (300-800 words)**
- Hook: "Here's exactly how I [result]"
- Brief context on why
- Step-by-step or framework breakdown
- Real results or outcomes
- Offer to share more detail

### Drafting Rules
- Write in first person
- Use line breaks for readability (LinkedIn formatting)
- No hashtag spam (3-5 relevant hashtags max, at the end)
- Include one specific detail that proves this is real experience, not theory
- End with a genuine question, not a CTA
- No emojis in the first line (algorithm doesn't reward it as much as people think)
- Draft 2 versions: one punchy, one more detailed. Let Keith pick.

### Save
- Save draft to `[VAULT_PATH]/03-Content/Drafts/linkedin_YYYY-MM-DD_topic.md`
- Update the content idea note status to "draft"
- Add to content calendar if one exists

## Quality Check
Before presenting the draft, verify:
- [ ] Does this sound like Keith, not like an AI?
- [ ] Is there a specific real-world detail?
- [ ] Would a senior HR/TA leader find this valuable?
- [ ] Is the hook genuinely interesting or just clickbait?
- [ ] Does it teach something or just state an opinion?
```

### Skill: YouTube Script Drafter
File: `~/.claude/skills/k2b-youtube/SKILL.md`

```markdown
---
name: k2b-youtube
description: This skill should be used when Keith wants to create a YouTube video script, plan a video, or develop a video concept from a content idea. It creates structured scripts with visual direction notes.
---

# K2B YouTube Script Drafter

## Workflow

1. Identify the video topic (from content idea note or Keith's request)
2. Read brand voice
3. Search vault for all related notes (this becomes the research base)
4. Web search for additional context if needed (competitor videos, current trends)
5. Create the script

## Script Structure

### Pre-Production Brief
```yaml
Title: 
Working Hook: 
Target Length: [5-8 min | 10-15 min | 15-20 min]
Target Audience: 
Key Takeaway: (one sentence)
Thumbnail Concept: 
```

### Script Format

**INTRO (30-60 seconds)**
- Hook: First sentence must create curiosity or state a bold claim
- Context: Why this matters to the viewer
- Promise: What they'll learn/gain by watching
- "Let's get into it" (no long intros)

**SECTIONS (2-4 sections)**
For each section:
```
## Section Title

[TALKING HEAD]
Script text here...

[SCREEN RECORDING: description of what to show]
Script for voiceover while showing screen...

[B-ROLL SUGGESTION: description]
Voiceover text...

KEY POINT: The one thing the viewer should remember from this section.
```

**OUTRO (30-60 seconds)**
- Recap key points (fast)
- One actionable next step for the viewer
- CTA (subscribe + relevant link)

### Visual Direction Notes
Include throughout:
- `[SCREEN]` — Show this on screen (tool, dashboard, etc.)
- `[DIAGRAM]` — Create a simple diagram showing this concept
- `[TEXT OVERLAY]` — Key stat or phrase to overlay
- `[CUT TO]` — Transition suggestion

### Save
- Save to `[VAULT_PATH]/03-Content/Drafts/youtube_YYYY-MM-DD_topic.md`
- Create a companion `youtube_YYYY-MM-DD_topic_notes.md` with raw research and source links

## Video Categories for Keith
1. **"How I Use AI to..."** — Practical walkthroughs of real work tasks
2. **"What Happened When..."** — Story-driven lessons from implementing AI at SJM
3. **"The [X] Framework"** — Structured approaches to common challenges
4. **"Behind the Build"** — Building K2B, showing the process (meta-content)
5. **"AI vs Traditional"** — Side-by-side comparisons of old way vs AI way
```

### Skill: Content Calendar
File: `~/.claude/skills/k2b-content-calendar/SKILL.md`

```markdown
---
name: k2b-content-calendar
description: This skill should be used when Keith wants to plan content, review his publishing schedule, or manage the content pipeline. It maintains a content calendar in the Obsidian vault and helps with scheduling and tracking.
---

# K2B Content Calendar

## Calendar File
Location: `[VAULT_PATH]/03-Content/Calendar/content-calendar.md`

## Format

```markdown
# Content Calendar

## This Week (YYYY-MM-DD to YYYY-MM-DD)

### Monday
- [ ] LinkedIn: [title] — Status: [idea/draft/ready/published]

### Wednesday  
- [ ] LinkedIn: [title] — Status: [idea/draft/ready/published]

### Thursday
- [ ] YouTube: [title] — Status: [scripted/recorded/edited/published]

## Pipeline (Next 2 Weeks)
| Date | Platform | Topic | Status | Source Note |
|------|----------|-------|--------|-------------|
| | | | | |

## Ideas Backlog
Sorted by potential impact:
1. [idea] — Source: [[note_link]]
2. ...
```

## Workflow

### Weekly Planning (suggest running every Sunday or Monday)
1. Read recent content ideas from `03-Content/Ideas/`
2. Read recent insights from `02-Work/Insights/`
3. Check what was published last week
4. Propose a content schedule for the coming week:
   - 2-3 LinkedIn posts
   - 0-1 YouTube video
5. Balance content across pillars (don't do 3 AI posts in a row)
6. Update the calendar file

### Status Tracking
When Keith publishes something:
1. Move from Drafts to Published: `03-Content/Published/`
2. Add publish date and platform link to the note
3. Update calendar status to "published"
4. Note engagement metrics if Keith provides them (for future content strategy)

### Content Recycling
Track published content. After 30+ days, revisit high-performing topics:
- Can this be expanded into a YouTube video?
- Can a YouTube topic become a LinkedIn series?
- Can old insights be updated with new data?
```

---

## Getting Started with Phase 3

### Step 1: Create Brand Voice (do this first)
```
Open Claude Code in your K2B project and say:
"Help me create my brand system using the k2b-brand-voice skill"
```
Answer the questionnaire. This creates the foundation all content skills reference.

### Step 2: Try a LinkedIn Post
```
After at least a week of daily notes and meeting captures, say:
"/content" to review recent insights, then:
"Draft a LinkedIn post from that first idea"
```

### Step 3: Plan First YouTube Video
```
"I want to plan my first YouTube video. The topic is: How I built a second brain 
using Claude Code and Obsidian as a senior TA executive. Use the youtube script skill."
```

### Step 4: Set Up Content Calendar
```
"Set up my content calendar. I want to publish 2 LinkedIn posts per week (Tuesday and Thursday) 
and 1 YouTube video every other week (Saturday release)."
```

## What You Have After Phase 3

- Brand voice codified and referenced by all content skills
- LinkedIn post drafting with 2-variant output
- YouTube script generation with visual direction
- Content calendar tracking pipeline from idea → published
- Weekly content review process (/content command)
- All content sourced from real work (not generated from nothing)
