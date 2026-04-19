---
name: k2b-observer
description: Harvest implicit preference signals from vault behavior and synthesize a preference profile that other skills reference. This skill should be used when Keith says /observe, "what have you noticed", "check preferences", "review feedback", or on session-start/scheduled runs. It reads observer-loop analysis, review queue outcomes, video feedback (video-preferences.md), revision patterns, and adoption rates to learn what Keith actually wants without him having to say it explicitly.
---

# K2B Observer

Harvest implicit preference signals from Keith's vault behavior. Synthesize patterns into a preference profile that other K2B skills reference before producing output.

## Vault & Skill Paths

- Vault: `~/Projects/K2B-Vault`
- Preference signals log: `~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl`
- Preference profile: `~/Projects/K2B-Vault/wiki/context/preference-profile.md`
- Video preferences (NotebookLM filter tail): `~/Projects/K2B-Vault/wiki/context/video-preferences.md`
- Skills: `~/Projects/K2B/.claude/skills/`
- Learnings: `~/.claude/projects/*/memory/self_improve_learnings.md`

## Vault Query Tools

- **Dataview DQL** (structured frontmatter queries): `~/Projects/K2B/scripts/vault-query.sh dql '<TABLE query>'`
- **Full-text search**: `mcp__obsidian__search` MCP tool or `vault-query.sh search "<term>"`
- **Read file**: `mcp__obsidian__get_file_contents` or Read tool
- **List files**: `mcp__obsidian__list_files_in_dir`

Prefer DQL queries over Glob+Read+Filter when scanning multiple files for frontmatter fields.

## Commands

- `/observe` -- Run the full observation cycle (harvest + synthesize)
- `/observe harvest` -- Harvest new signals only (no synthesis)
- `/observe profile` -- Show the current preference profile
- `/observe signals` -- Show raw signal stats (counts by skill, action, etc.)
- `/observe reset` -- Archive current signals and start fresh (confirm with Keith first)

## Phase 1: Harvest Signals

### 1a. Preference Signal Sources

Read `preference-signals.jsonl`. This file has two signal sources:

1. **Observer-loop (primary, active)**: The background observer on Mac Mini analyzes vault behavior via MiniMax M2.7 and appends signals with schema: `{date, source, type, description, confidence, skill}`. This is the main source of signals today.
2. **Review queue outcomes (secondary)**: When k2b-review processes review/ items, it appends signals with schema: `{date, file, source_skill, type, action, days_in_inbox, has_feedback, feedback}`. This source activates as Keith uses /review more frequently.

If the file doesn't exist or is empty, tell Keith: "No preference signals yet. The observer-loop will start generating signals automatically, or process some review/ items with /review."

Then check if this is the first run (no preference-profile.md exists). If so, run the Bootstrapping procedure below.

### 1a-filter. Filter out processed signals (APPEND-cutoff reader)

Read `~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl` in **two passes** (signal-processed lines appear after the original signal, so a single top-to-bottom pass would surface signals before seeing their processed marker):

**Pass 1 -- collect filter state:** Walk the entire file. Track:
1. `cutoff_line` -- the line number of the `type: "grandfather-cutoff"` entry (0 if absent). All lines before it are grandfathered.
2. `processed_ids` set -- for every `type: "signal-processed"` line whose `action` is `confirmed` or `rejected`, add its `signal_id`. `action: watching` is intentionally EXCLUDED -- deferring a signal should resurface it next session, not silence it forever.

**Pass 2 -- collect candidates:** Walk the file again. A signal is filtered out when any of these is true:
- It appears before `cutoff_line` (grandfathered).
- Its `signal_id` is in `processed_ids`.
- It has no `signal_id` field at all (pre-Fix #6 historical; grandfathered by the cutoff).

Remaining signals flow into Phase 2 pattern detection and Phase 3 synthesis.

### 1b. Revision Detection

For items with action = "promote" in the signals log:

1. Parse `review-notes` feedback text for patterns:
   - "too long" / "shorten" / "verbose" -- length preference
   - "remove X" / "don't need X" / "skip X" -- section preference
   - "good" / "useful" / "keep doing this" -- positive reinforcement
   - "wrong" / "not what I meant" / "missed the point" -- quality issue

2. Categorize each feedback instance and store alongside the raw signal data for pattern detection.

### 1c. Adoption Rate by Skill

From the signals, calculate per-skill stats:
- Total items produced
- Promote rate (promoted / total)
- Archive rate (archived / total)
- Delete rate (deleted / total)
- Revise rate (revised / total)
- Average days in review before action
- Feedback rate (has_feedback = yes / total)

### 1d. Content Pipeline Signals

For content-specific tracking:
- How many content ideas (origin: k2b-generate) got adopted vs archived?
- How many LinkedIn drafts were published as-is vs revised vs scrapped?
- Which content pillars produce ideas Keith adopts?

### 1e. Video Feedback (from `/research videos`)

Video preferences are captured inline in `wiki/context/video-preferences.md` by `/review` or the Telegram feedback path. That file IS the NotebookLM preference tail -- `/research videos` reads it directly on every run. The observer does not need to re-synthesize a separate YouTube profile; it can surface recurring themes from `video-preferences.md` as patterns (e.g., "Keith consistently drops managed-agent content").

## Phase 2: Detect Patterns

A pattern requires a minimum of **3 occurrences** of the same behavior to be considered real. Below 3, it's noise.

### Pattern Types to Detect

**Skill-Level Patterns:**
- "k2b-youtube-capture: 70% archive rate from AI News Daily playlist" (low relevance signal)
- "k2b-tldr: Keith always provides review-notes mentioning 'too long'" (format preference)
- "k2b-research: average 5 days in review before action" (low urgency/relevance)
- "k2b-insight-extractor /content ideas: 20% adopt rate" (quality or relevance issue)

**Cross-Skill Patterns:**
- "Notes with type: video-capture have 40% promote rate vs 80% for type: tldr" (relative value)
- "Keith acts on items within 1 day when source_skill is k2b-meeting-processor" (high value signal)
- "Items with has_feedback = yes are 3x more likely to be promoted" (engagement signal)

**Revision Patterns (from review-notes parsing):**
- "Keith mentions 'too long' in 4 of 7 review-notes for k2b-tldr" (consistent length preference)
- "Keith removes Content Seeds section in feedback for k2b-tldr" (section preference)

### Pattern Confidence

Assign confidence based on:
- **High (3+ occurrences, consistent direction)**: Ready to include in preference profile
- **Medium (3+ occurrences, mixed signal)**: Note in profile with caveat
- **Low (<3 occurrences)**: Do not include in profile yet, track for future

## Phase 3: Synthesize Preference Profile

Write to `~/Projects/K2B-Vault/wiki/context/preference-profile.md`:

```yaml
---
tags: [k2b-system, preferences]
date: YYYY-MM-DD
type: reference
origin: k2b-generate
up: "[[MOC_K2B-System]]"
---
```

Body structure:

```markdown
# K2B Preference Profile

Last updated: YYYY-MM-DD
Based on: N feedback signals over N days

## Skill-Specific Preferences

### k2b-tldr
- **Adopt rate**: X% (N items)
- **Observed preferences**:
  - Keith prefers shorter summaries (N instances of "too long" feedback)
  - Content Seeds section is frequently removed (N instances)
- **Recommendation**: Keep summaries to 3-5 bullets. Consider making Content Seeds opt-in.

### k2b-youtube-capture
- **Adopt rate**: X% overall
  - By playlist: K2B Claude X%, K2B Invest X%, ...
- **Observed preferences**:
  - [patterns detected]
- **Recommendation**: [actionable suggestion]

### k2b-insight-extractor (/content)
- **Adopt rate**: X% of generated ideas adopted
- **Observed preferences**:
  - [patterns detected]
- **Recommendation**: [actionable suggestion]

### [other skills with signals]
...

## General Preferences

- **Response length**: [observed pattern across skills]
- **Review processing speed**: Keith acts fastest on [type], slowest on [type]
- **Content pipeline**: [observed adoption patterns]

## Candidate Learnings

These patterns are strong enough to consider promoting to k2b-feedback:

1. "[specific pattern]" -- Confidence: high (N occurrences)
   - Suggested /learn entry: "[what K2B should do differently]"
2. ...

## Signal Quality Notes

- Total signals: N
- Date range: YYYY-MM-DD to YYYY-MM-DD
- Signals per skill: [breakdown]
- Note: Patterns with <3 occurrences are excluded. More data improves accuracy.
```

## Phase 4: Candidate Learnings Promotion

When patterns reach high confidence, present them to Keith as candidate /learn entries:

"I've noticed you consistently [pattern]. Should I /learn this so it sticks?"

If Keith agrees, call the k2b-feedback workflow to capture it with proper dedup and reinforcement.

**Never auto-promote without Keith's confirmation.** The observer suggests, Keith decides.

## How Other Skills Use the Preference Profile (Planned)

**Status: Not yet implemented.** The design intent is documented here for when preference-based adaptation is added to downstream skills. Currently, no skill reads preference-profile.md before producing output.

**Planned integration** -- skills that produce output Keith reviews would read the preference profile before generating:

1. **k2b-tldr**: Read preference-profile.md section for k2b-tldr. Apply any length, section, or format preferences.
2. **k2b-youtube-capture**: Read preference-profile.md for playlist-specific preferences. Adjust analysis depth.
3. **k2b-linkedin**: Read preference-profile.md for draft preferences. Apply revision patterns.
4. **k2b-insight-extractor**: Read preference-profile.md for content idea adoption patterns.

**When implemented**: Each skill would add one line to its workflow: "Read `wiki/context/preference-profile.md` for skill-specific preferences. Apply any relevant preferences to output formatting."

The preference profile is a reference document, not an enforcement mechanism. Keith decides when to update skill instructions based on strong preferences.

## Bootstrapping (First Run)

On first `/observe`, if preference-signals.jsonl is empty or doesn't exist:

1. Query archived notes via DQL:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE type, origin, date FROM "Archive"'
   ```
   Archived items are implicit "not valuable enough to keep" signals.
2. Query adopted content ideas:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE type, origin, status FROM "wiki/content-pipeline"'
   ```
   These are implicit "this was valuable" signals.
3. Query stale review items:
   ```bash
   ~/Projects/K2B/scripts/vault-query.sh dql 'TABLE date, review-action AS "action" FROM "review" WHERE date <= date(today) - dur(7 days)'
   ```
   Items older than 7 days with no review-action = low urgency signal.
4. Generate an initial preference-signals.jsonl from this retrospective data
5. Run the full synthesis to produce the first preference-profile.md

Tell Keith: "Bootstrapped preference profile from N archived items, N adopted ideas, and N pending review items. This will get more accurate as the observer-loop and /review generate more signals."

## /observe reset

When Keith says `/observe reset`:
1. Confirm with Keith first: "This will archive current signals and start fresh. Continue?"
2. Move `preference-signals.jsonl` to `Archive/preference-signals-YYYY-MM-DD.jsonl`
3. Create a new empty `preference-signals.jsonl`
4. Keep `preference-profile.md` in place (it's still valid until a new synthesis runs)

## File Formats

### preference-signals.jsonl

One JSON object per line, append-only. Two signal sources produce different schemas:

**Observer-loop signals** (primary source, written by background MiniMax M2.7 analysis):
```json
{"date":"2026-04-08","source":"observer-loop","type":"vault-behavior","description":"Keith revised the daily note 3 times, shortening each section","confidence":"high","skill":"k2b-daily-capture"}
{"date":"2026-04-09","source":"observer-loop","type":"content-preference","description":"YouTube captures from AI channels archived without review","confidence":"medium","skill":"k2b-youtube-capture"}
```

**Review queue outcome signals** (secondary source, written by k2b-review when processing review/ items):
```json
{"date":"2026-03-28","file":"youtube_2026-03-25_ai-news.md","source_skill":"k2b-youtube-capture","type":"video-capture","action":"archive","days_in_inbox":3,"has_feedback":"no","feedback":""}
{"date":"2026-03-28","file":"tldr_2026-03-27_k2b-planning.md","source_skill":"k2b-tldr","type":"tldr","action":"promote","days_in_inbox":1,"has_feedback":"yes","feedback":"good summary but too long, trim to 3 bullets"}
```

When reading signals, check for `source` field (observer-loop) vs `source_skill` field (review queue) to distinguish the schemas.

### preference-profile.md

See Phase 3 above for full format. This is a vault note with frontmatter, readable by both Keith in Obsidian and K2B skills.

## Background Observer Loop

K2B runs a background observer on Mac Mini (`scripts/observer-loop.sh`, managed by pm2 as `k2b-observer`). This loop:

1. Captures observations via a Stop hook (`scripts/hooks/stop-observe.sh`) after every Claude response
2. When 20+ observations accumulate, calls MiniMax-M2.7 API to analyze patterns
3. Writes findings to `observer-candidates.md` (surfaced by session-start hook)
4. Appends detected patterns to `preference-signals.jsonl`
5. Archives processed observations

**How `/observe` relates to the background loop:**
- The background loop runs automatically and cheaply via MiniMax (~$0.007/analysis)
- `/observe` is Keith's manual command for on-demand analysis with full Claude reasoning
- `/observe` reads the same files (preference-signals.jsonl, observations.jsonl) and produces the same output (preference-profile.md)
- They complement each other: background loop catches patterns continuously, `/observe` does deep synthesis on demand
- `/observe` should read `observer-candidates.md` and incorporate any background findings

## Session-Start Inline Confirmation

When observer findings appear in the session-start hook output, act on them immediately -- do not wait for Keith to remember `/observe`. This collapses the old 3-step manual flow (`/observe` -> `/learn` -> wait for reinforcement) into one natural-language response from Keith. `/observe` remains available for deep synthesis but is no longer required for the loop to close.

### HIGH confidence findings

Present each HIGH finding with three options:

- **confirm** -- run `/learn` inline with the finding text. This auto-creates a policy ledger entry (the correction becomes an executable guardrail).
- **keep watching** -- do nothing. Let the finding accumulate more evidence before acting on it.
- **reject** -- note the rejection in `wiki/context/preference-signals.jsonl` so the observer learns what Keith does NOT endorse. Use the exact format below (one JSON object per line, trailing newline, atomic write):

```json
{"date":"YYYY-MM-DD","source":"session-start-reject","type":"rejection","description":"<finding text>","confidence":"high","skill":"k2b-observer"}
```

### MEDIUM confidence findings

Show MEDIUM findings as context. Do not prompt for action unless Keith asks. They exist to nudge Keith's awareness, not to force a decision.

### Post-action mark

After Keith answers y/n/skip, mark the signal as processed via the helper so both the session-start inline flow and `/observe` deep synthesis filter it out on the next read:

```bash
scripts/observer-mark-processed.sh <signal_id> <confirmed|rejected|watching> [L-ID]
```

Pass `confirmed` when Keith answered yes and a learning was created, `rejected` when he said no (do not surface again), `watching` when he deferred. Include the new L-ID as the third argument when the action produced a learning. `watching` is recorded but does NOT suppress the signal on subsequent reads -- deferred findings resurface next session.

### Idempotency

Once a finding is confirmed/kept/rejected inline, it is considered processed for this session. A subsequent `/observe` run streams `preference-signals.jsonl` using the Phase 1a-filter APPEND-cutoff reader: signals written before the `grandfather-cutoff` line are skipped, signals after the cutoff are filtered by `signal_id` against any `signal-processed` lines with `action: confirmed` or `action: rejected`. Keith is never asked the same question twice.

## Integration Map

```
Stop hook captures observations
    |
    +--> appends to observations.jsonl
    |
Background observer loop (MiniMax-M2.7, pm2)
    |
    +--> reads observations.jsonl periodically
    +--> calls MiniMax API for pattern detection
    +--> writes observer-candidates.md (for session-start hook)
    +--> appends to preference-signals.jsonl
    |
k2b-review processes review/ items
    |
    +--> appends to preference-signals.jsonl (review queue outcome schema)
    |
k2b-observer (/observe command) reads preference-signals.jsonl + observer-candidates.md
    |
    +--> detects patterns (deep synthesis)
    +--> writes preference-profile.md
    +--> suggests candidate /learn entries
    |
Session-start hook reads observer-candidates.md
    |
    +--> surfaces findings to Keith
    |
Other skills can read preference-profile.md before producing output (planned, not yet implemented)
    |
k2b-improve reviews preference-profile.md alongside learnings/errors/requests

Video Feedback Loop:
    |
/research videos filters via NotebookLM, drops per-video review notes in review/
    |
    +--> /review distills Keith's verdicts into wiki/context/video-preferences.md
    |
/research videos reads video-preferences.md on every run as the preference tail
```

## Usage Logging

After completing the main task:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-observer\t$(echo $RANDOM | md5sum | head -c 8)\tobserved: SUMMARY" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Never auto-promote learnings without Keith's confirmation
- The preference profile is a reference document, not an enforcement mechanism
- Patterns require 3+ occurrences to be considered real
- The observer suggests, Keith decides
- Keep the profile scannable -- bullet points, not essays
- preference-signals.jsonl is append-only. Never delete or modify existing entries.
- On /observe reset, move the current jsonl to Archive/ with a date suffix, don't delete
