---
name: k2b-research
description: Deep dive into external topics -- scan for new AI tools, techniques, and ideas; analyze URLs, YouTube videos, and GitHub repos. This skill should be used when Keith says /research, "look into this", "what's new in AI", or wants to deep-dive into a topic, URL, or repo. For internal system health, use /improve instead.
---

# K2B Research Agent

On-demand research that scans externally for new tools, techniques, and ideas, and deep dives into specific topics or URLs.

## Commands

- `/research` -- External scanning using research-topics.md
- `/research "topic"` -- Deep dive on a specific topic
- `/research <url>` -- Deep dive on a specific URL (YouTube, GitHub, article)

> For internal vault health and system auditing, use `/improve vault` instead.

## Vault & Skill Paths

- Vault: `~/Projects/K2B-Vault`
- Skills: `~/Projects/K2B/.claude/skills/`
- Research topics: `~/Projects/K2B-Vault/Notes/Context/research-topics.md`
- Output: `~/Projects/K2B-Vault/raw/research/`

## External Scanning

### Default Mode (no topic/URL)

1. Read `K2B-Vault/Notes/Context/research-topics.md` for the topic list
2. For each topic category, run 1-2 targeted web searches
3. For each finding:
   - Brief summary (2-3 sentences max)
   - K2B relevance: how could this be applied to K2B specifically?
   - Actionability: is this something Keith could use now, soon, or someday?
4. Prioritize findings by relevance to K2B

### Topic Mode (`/research "topic"`)

1. Run 3-5 targeted web searches on the specific topic
2. Read and synthesize findings
3. Produce a deep-dive analysis focused on K2B applicability
4. Include specific recommendations: "K2B could implement X by doing Y"

### URL Mode (`/research <url>`)

Detect URL type and handle accordingly:

**YouTube URLs:**
1. Fetch transcript using the YouTube Transcript MCP tool
2. Analyze the full transcript
3. Extract key concepts, techniques, tools mentioned
4. Map each to K2B applicability
5. Note timestamps for the most relevant segments

**GitHub repo URLs:**
1. Fetch and read the README
2. Explore the repo structure (key files, directory layout)
3. Assess: what patterns or code could K2B reuse?
4. Note specific files or techniques worth adopting

**Article/web page URLs:**
1. Fetch the page content
2. Extract key insights and techniques
3. Analyze through the lens of "what can K2B learn from this?"

**All URL types produce:**
- Source summary
- Key takeaways (5-10 bullet points)
- K2B applicability analysis
- Specific recommendations with implementation ideas

## Output Format

Save to `raw/research/YYYY-MM-DD_research-briefing.md` (or `raw/research/YYYY-MM-DD_research-[topic-slug].md` for focused research).

After saving to raw/research/, trigger k2b-compile to digest the raw source into wiki pages. k2b-compile reads the raw research note, shows Keith a summary of wiki pages to update, and on approval updates wiki pages, indexes, and wiki/log.md.

```markdown
---
tags: [research, k2b-system]
date: YYYY-MM-DD
type: reference
origin: k2b-generate
up: "[[MOC_K2B-System]]"
---

# Research Briefing -- YYYY-MM-DD

## External Findings
### [Finding 1 Title]
- **Source**: [URL or search]
- **Summary**: [2-3 sentences]
- **K2B Relevance**: [how this applies]
- **Action**: [what Keith could do with this]

### [Finding 2 Title]
...

## Recommendations
### New Ideas from Research
- [ ] [actionable idea with implementation sketch]

## Linked Notes
[wikilinks to related vault notes]
```

## For URL Deep Dives

Use a more focused output format:

```markdown
---
tags: [research, deep-dive, {topic-tags}]
date: YYYY-MM-DD
type: research-briefing
origin: k2b-generate
source: "[Title](URL)"
up: "[[MOC_K2B-System]]"
---

# Deep Dive: [Topic/Source Title]

## Source
[URL and brief description of what this is]

## Key Takeaways
1. [takeaway with context]
2. ...

## K2B Applicability
### What We Can Use
- [specific technique] -- could apply to [specific K2B area]

### What's Interesting But Not Actionable Yet
- [concept] -- relevant when [condition]

### Implementation Ideas
- [ ] [concrete next step]

## Linked Notes
[wikilinks to related vault notes]
```

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-research\t$(echo $RANDOM | md5sum | head -c 8)\tran research: FOCUS" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Be specific in recommendations -- "improve the meeting processor" is useless, "add an explicit instruction for formatting action items with owner names in brackets" is actionable
- External findings should be filtered for relevance -- don't dump every search result
- When scanning YouTube videos, use the transcript MCP tools
- When scanning GitHub repos, focus on README, key source files, and patterns
- Always cross-link findings to existing vault notes where relevant
