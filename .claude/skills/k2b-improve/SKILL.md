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
- Preference Profile: `~/Projects/K2B-Vault/Notes/Context/preference-profile.md`
- Preference Signals: `~/Projects/K2B-Vault/Notes/Context/preference-signals.jsonl`
- Vault: `~/Projects/K2B-Vault`
- Skills: `~/Projects/K2B/.claude/skills/`

## Command: /improve

Generate the full system health report. Sections can be run individually for speed:

- `/improve` -- Full report (all sections)
- `/improve learnings` -- Self-improvement logs only
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

## Section 3: Vault Health

1. **Orphaned notes**: Glob all notes in `Notes/` and check for `up:` field in frontmatter. List any without an `up:` link.
2. **Stale Inbox items**: Check `Inbox/` for files older than 7 days. These should be processed or moved.
3. **MOC freshness**: Read each MOC file. Compare links in MOCs against actual files in vault. Flag notes that exist but aren't linked from any MOC.
4. **Broken wikilinks**: Sample 10-15 notes and check that `[[wikilinks]]` point to existing files.
5. **Vault metrics**: Count notes by folder, count total wikilinks, check daily note streak (consecutive days with a daily note).

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

### Preferences (from Observer)
- Last observed: YYYY-MM-DD | N signals
- Strongest signal: [skill with clearest pattern]
- Candidate learnings: N (review with /observe)

### Vault Health
- X notes | Y wikilinks | Z orphans | W stale inbox items
- Daily streak: N days
- Issues: [any broken links or missing MOC links]

### Skill Evals
[table above]
- Skills needing eval: [list]
- Skills needing attention: [declining pass rates]
```

## Usage Logging

After completing the main task:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-improve\t$(echo $RANDOM | md5sum | head -c 8)\treviewed system health" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep the report scannable -- Keith wants a dashboard, not an essay
- When suggesting actions, be specific: "promote L-2026-03-15-002 to CLAUDE.md" not "consider promoting some learnings"
- The vault health section replaces what /research internal used to do
