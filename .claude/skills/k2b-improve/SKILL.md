---
name: k2b-improve
description: Full K2B system health dashboard -- reviews self-improvement logs, preference profile, vault health, and skill eval status. This skill should be used when Keith says /improve, "how is K2B doing", "review learnings", "system health", "vault health", "show me errors", "open requests", or wants to audit K2B's overall state and improvement trajectory.
---

# K2B Self-Improvement Dashboard

Single command for the full picture of how K2B is doing -- learnings, errors, requests, preferences, vault health, and skill eval status.

## Memory & Data Paths

- Learnings: `~/.claude/projects/*/memory/self_improve_learnings.md`
- Errors: `~/.claude/projects/*/memory/self_improve_errors.md`
- Requests: `~/.claude/projects/*/memory/self_improve_requests.md`
- Active Rules: `~/Projects/K2B-Vault/System/memory/active_rules.md`
- Preference Profile: `~/Projects/K2B-Vault/wiki/context/preference-profile.md`
- Preference Signals: `~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl`
- Lint Report: `~/Projects/K2B-Vault/wiki/context/lint-report.md`
- Vault: `~/Projects/K2B-Vault`
- Skills: `~/Projects/K2B/.claude/skills/`

## Vault Query Tools

- **Dataview DQL** (structured frontmatter queries): `~/Projects/K2B/scripts/vault-query.sh dql '<TABLE query>'`
- **Full-text search**: `mcp__obsidian__search` MCP tool or `vault-query.sh search "<term>"`
- **Read file**: `mcp__obsidian__get_file_contents` or Read tool
- **List files**: `mcp__obsidian__list_files_in_dir`

Prefer DQL queries over Glob+Read+Filter for vault health checks.

## Command: /improve

Generate the full system health report. Sections can be run individually for speed:

- `/improve` -- Full report (all sections)
- `/improve learnings` -- Self-improvement logs only
- `/improve rules` -- Active rules audit only
- `/improve vault` -- Vault health only
- `/improve evals` -- Skill eval dashboard only
- `/improve preferences` -- Preference profile review only

## Section 1: Self-Improvement Status

1. Read all three memory files (learnings, errors, requests).
2. Report:
   - Total learnings count and total reinforcements
   - Top 5 most-reinforced learnings
   - Recent errors (last 30 days) -- any unresolved?
   - Open feature requests
3. Suggest any learnings with `Reinforced` >= 3 for promotion to standalone memory files or CLAUDE.md.
4. Flag any recurring error patterns.
5. If any learnings are older than 90 days and reinforced only once, suggest pruning (confirm before deleting).

## Section 1b: Active Rules Audit (from /lint)

Active rules staleness and path validation is done by `/lint` Check #11. This section reads the latest lint report rather than re-running the check.

1. Read `~/Projects/K2B-Vault/wiki/context/lint-report.md`.
2. If the file doesn't exist or its frontmatter `date:` is older than 7 days, say: "No recent lint report. Run /lint to refresh active rules audit." Skip to next section.
3. Otherwise, extract from the lint report frontmatter and the Active Rules section:
   - Total rule count (count rules in `active_rules.md` directly)
   - `rules-last-promoted` and days since (flag if >30 days)
   - `rules-dead-paths` count and the specific rule/path pairs from the Active Rules section body
   - `rules-legacy-folders` count
   - `rules-promotion-candidates` count and learning IDs
4. If any dead paths, legacy folders, or >=1 promotion candidates exist, ask Keith: "N path issues and M promotion candidates in the latest lint. Want to review?"
5. Never auto-edit `active_rules.md` -- rules are Keith's voice; he decides what to rewrite, retire, or promote.

## Section 2: Preference Profile

1. Read `preference-profile.md`.
2. If it exists, report:
   - Skills with lowest adopt rates (potential quality issues)
   - Skills with highest adopt rates (what's working well)
   - Any candidate learnings from the observer that haven't been promoted yet
   - Date of last observer run
   - Total signal count and date range
3. If it doesn't exist, note: "No preference profile yet. Run /observe to generate one."
4. If candidate learnings exist, ask Keith: "The observer found N candidate learnings. Want to review and promote any?"

## Section 3: Vault Health (from /lint)

Vault structural health (orphans, broken wikilinks, stale review items, uncompiled raw, sparse wiki pages, index drift) is checked by `/lint`. This section reads the latest lint report rather than re-running queries. Vault metrics (counts + daily streak) are computed inline since they're cheap and not structural checks.

1. Read `~/Projects/K2B-Vault/wiki/context/lint-report.md`.
2. If the file doesn't exist or its frontmatter `date:` is older than 7 days, say: "No recent lint report. Run /lint to refresh vault health." Skip structural findings and jump to step 4 (metrics).
3. Otherwise, extract and report from the lint frontmatter and body:
   - **Summary line**: `checks-run`, `auto-fixed`, `needs-review`, `clean`, `hard-errors`
   - **Top findings**: up to 5 items from the "Needs Review" section of the lint report body, prioritized by hard-errors first, then broken links, then orphans, then stale content
   - **Counts**: `vault-orphans`, `vault-broken-links`, `review-stale-items`, `uncompiled-raw`, `sparse-wiki-pages`
4. **Vault metrics** (inline, always runs regardless of lint freshness):
   - Note counts by top-level folder (`raw/`, `wiki/`, `review/`, `Daily/`) via Glob
   - Daily note streak: consecutive days ending today with a file at `Daily/YYYY-MM-DD.md`
5. If the lint report is >3 days old, append a nudge: "Lint last ran N days ago. Consider /lint."

## Section 4: Skill Eval Dashboard

1. For each skill in `.claude/skills/k2b-*/`:
   a. Check if `eval/eval.json` exists
   b. Check if `eval/results.tsv` exists and has entries
   c. Read latest pass rate from results.tsv
   d. Read best pass rate from results.tsv
   e. Count total iterations run
2. Present as a table:

```
| Skill                  | Has Eval | Assertions | Pass Rate | Best | Iterations | Last Run   |
|------------------------|----------|-----------|-----------|------|------------|------------|
| k2b-meeting-processor  | Yes      | 15        | 80.0%     | 86.7%| 12         | 2026-03-24 |
| k2b-daily-capture      | No       | --        | --        | --   | --         | --         |
```

3. Highlight skills that have no eval.json (candidates for /autoresearch setup).
4. Highlight skills with declining pass rates (need attention).

## Report Format

Present the full report as a structured summary Keith can scan in 30 seconds:

```
## K2B System Health -- YYYY-MM-DD

### Learnings & Feedback
- X learnings (Y reinforcements), Z errors, W open requests
- Top reinforced: [top 3]
- Action needed: [any patterns or promotions]

### Active Rules
- N rules | Last promoted: YYYY-MM-DD (X days ago)
- Path issues: N (dead paths or legacy folders)
- Promotion candidates: N learnings since last promotion

### Preferences (from Observer)
- Last observed: YYYY-MM-DD | N signals
- Strongest signal: [skill with clearest pattern]
- Candidate learnings: N (review with /observe)

### Vault Health (lint YYYY-MM-DD)
- X notes across raw/wiki/review | Daily streak: N days
- Lint: N auto-fixed, N needs review, N hard errors
- Top issues: [up to 5 from lint "Needs Review"]

### Skill Evals
[table above]
- Skills needing eval: [list]
- Skills needing attention: [declining pass rates]
```

## Usage Logging

After completing the main task:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-improve\t$(echo $RANDOM | md5sum | head -c 8)\treviewed system health" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep the report scannable -- Keith wants a dashboard, not an essay
- When suggesting actions, be specific: "promote L-2026-03-15-002 to CLAUDE.md" not "consider promoting some learnings"
- The vault health section replaces what /research internal used to do
