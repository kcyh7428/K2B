---
name: k2b-research
description: On-demand research agent that audits K2B vault health, reviews self-improvement status, scans for external ideas, and produces a structured research briefing note. Use when Keith says /research, "research", "improve K2B", "what's new in AI", "how can K2B be better", "scan for ideas", "look into this", or wants to deep-dive into a topic or URL for K2B-applicable insights.
---

# K2B Research Agent

On-demand research that finds what to improve -- both internally (vault health, skill quality) and externally (new tools, techniques, ideas).

## Commands

- `/research` -- Run both internal audit + external scanning
- `/research internal` -- Vault health and eval dashboard only
- `/research external` -- External scanning using research-topics.md
- `/research external "topic"` -- Deep dive on a specific topic
- `/research external <url>` -- Deep dive on a specific URL (YouTube, GitHub, article)

## Vault & Skill Paths

- Vault: `/Users/keithmbpm2/Projects/K2B-Vault`
- Skills: `/Users/keithmbpm2/Projects/K2B/.claude/skills/`
- Research topics: `/Users/keithmbpm2/Projects/K2B-Vault/Notes/Context/research-topics.md`
- Output: `/Users/keithmbpm2/Projects/K2B-Vault/Inbox/`

## Phase 1: Internal Audit

### Vault Health Check
1. **Orphaned notes**: Glob all notes in `Notes/` and check for `up:` field in frontmatter. List any without an `up:` link.
2. **Stale Inbox items**: Check `Inbox/` for files older than 7 days. These should be processed or moved.
3. **MOC freshness**: Read each MOC file. Compare links in MOCs against actual files in vault. Flag notes that exist but aren't linked from any MOC.
4. **Broken wikilinks**: Sample 10-15 notes and check that `[[wikilinks]]` point to existing files.
5. **Vault metrics**: Count notes by folder, count total wikilinks, check daily note streak (consecutive days with a daily note).

### Self-Improvement Status
1. Read `self_improve_learnings.md` -- surface top 5 most-reinforced learnings
2. Read `self_improve_errors.md` -- list any unresolved errors
3. Read `self_improve_requests.md` -- list open feature requests
4. Flag patterns: are errors recurring? Are requests accumulating in one area?

### Skill Eval Dashboard
1. For each skill in `.claude/skills/k2b-*/`:
   a. Check if `eval/eval.json` exists
   b. Check if `eval/results.tsv` exists and has entries
   c. Read latest pass rate from results.tsv
   d. Read best pass rate from results.tsv
   e. Count total iterations run
2. Present as a table showing which skills need attention

## Phase 2: External Scanning

### Default Mode (no topic/URL)

1. Read `K2B-Vault/Notes/Context/research-topics.md` for the topic list
2. For each topic category, run 1-2 targeted web searches
3. For each finding:
   - Brief summary (2-3 sentences max)
   - K2B relevance: how could this be applied to K2B specifically?
   - Actionability: is this something Keith could use now, soon, or someday?
4. Prioritize findings by relevance to K2B

### Topic Mode (`/research external "topic"`)

1. Run 3-5 targeted web searches on the specific topic
2. Read and synthesize findings
3. Produce a deep-dive analysis focused on K2B applicability
4. Include specific recommendations: "K2B could implement X by doing Y"

### URL Mode (`/research external <url>`)

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

Save to `Inbox/YYYY-MM-DD_research-briefing.md` (or `Inbox/YYYY-MM-DD_research-[topic-slug].md` for focused research).

Use the k2b-vault-writer conventions for frontmatter and cross-linking.

```markdown
---
tags: [research, k2b-system]
date: YYYY-MM-DD
type: research-briefing
up: "[[MOC_K2B-System]]"
---

# Research Briefing -- YYYY-MM-DD

## Vault Health
[metrics table + issues found]

## Self-Improvement Status
[learnings summary, errors, requests]

## Skill Eval Dashboard
| Skill | Has Eval | Assertions | Pass Rate | Best | Iterations |
|-------|----------|-----------|-----------|------|------------|
| k2b-meeting-processor | Yes | 15 | 80% | 86% | 12 |
| k2b-daily-capture | Yes | 12 | 92% | 92% | 5 |
| ... | ... | ... | ... | ... | ... |

## External Findings
### [Finding 1 Title]
- **Source**: [URL or search]
- **Summary**: [2-3 sentences]
- **K2B Relevance**: [how this applies]
- **Action**: [what Keith could do with this]

### [Finding 2 Title]
...

## Recommendations
### Vault Maintenance
- [ ] [specific action]

### Skill Improvements (requires /autoresearch)
- [ ] k2b-meeting-processor: [what to improve and why]

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

## Notes

- No em dashes, no AI cliches, no sycophancy
- Be specific in recommendations -- "improve the meeting processor" is useless, "add an explicit instruction for formatting action items with owner names in brackets" is actionable
- External findings should be filtered for relevance -- don't dump every search result
- When scanning YouTube videos, use the transcript MCP tools
- When scanning GitHub repos, focus on README, key source files, and patterns
- Always cross-link findings to existing vault notes where relevant
