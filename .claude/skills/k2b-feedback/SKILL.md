---
name: k2b-feedback
description: Capture learnings, errors, and feature requests to make K2B smarter over time. This skill should be used when Keith says /learn, /error, /request, /feedback, "remember that", "don't do that again", "next time do X", "that broke", "something went wrong", "I wish you could", "can you do X" (and the answer is no), or any variation of teaching K2B, reporting a failure, or identifying a capability gap. For reviewing all captured feedback, use k2b-improve.
---

# K2B Feedback Capture

Capture corrections, errors, and feature requests in a single unified skill. Three subcommands, three files, one skill.

## Memory Paths

- Learnings: `~/.claude/projects/*/memory/self_improve_learnings.md`
- Errors: `~/.claude/projects/*/memory/self_improve_errors.md`
- Requests: `~/.claude/projects/*/memory/self_improve_requests.md`

## Quick Reference

| Situation | Command | File |
|-----------|---------|------|
| Keith corrects K2B or teaches a preference | `/learn [description]` | self_improve_learnings.md |
| Something broke or failed | `/error [description]` | self_improve_errors.md |
| Keith wants something K2B can't do | `/request [description]` | self_improve_requests.md |
| Auto-detect from context | `/feedback [description]` | Routes automatically |

## Auto-Routing (/feedback)

When Keith says `/feedback` without specifying type, read the conversation context and route:

- Corrections, preferences, best practices --> learn
- Failures, bugs, unexpected behavior --> error
- Missing capabilities, "I wish you could" --> request
- If ambiguous, ask Keith: "Is this a learning, an error, or a feature request?"

## Command: /learn [description]

Capture a correction, preference, or best practice.

1. If description provided, use it. If not, infer the learning from the current conversation context.
2. Read `self_improve_learnings.md`.
3. Check if a similar learning already exists (match on topic/area). If yes, increment `Reinforced` count and update the entry with any new context. If no, append a new entry.
4. Write the updated file.
5. Confirm with one line: what was captured.

Entry format:
```markdown
### L-YYYY-MM-DD-NNN
- **Area:** [preferences | workflow | knowledge | tools | writing-style | vault]
- **Learning:** [what K2B should do differently]
- **Context:** [what triggered this learning]
- **Reinforced:** 1
- **Confidence:** low
- **Date:** YYYY-MM-DD
```

ID format: `L-YYYY-MM-DD-NNN` where NNN auto-increments based on existing entries for that date.

### Confidence Scoring

Confidence is derived from the Reinforced count:

| Reinforced | Confidence | Behavior |
|------------|------------|----------|
| 1-2 | low | Suggest but don't enforce. Mention when relevant. |
| 3-5 | medium | Apply when relevant. Can be overridden without comment. |
| 6+ | high | Treat as core behavior. Auto-apply. Loaded at session start. |

When updating `Reinforced`, always recalculate and update `Confidence`:
- Set `low` for 1-2
- Set `medium` for 3-5
- Set `high` for 6+

When confidence reaches `high`, the session-start hook automatically surfaces the learning so all skills apply it.

## Command: /error [description]

Log a failure with root cause and fix.

1. If description provided, use it. If not, infer from the current conversation what went wrong.
2. Read `self_improve_errors.md`.
3. Append a new entry.
4. If the error reveals a generalizable learning, also add it to `self_improve_learnings.md` following the /learn entry format.
5. Confirm with one line.

Entry format:
```markdown
### E-YYYY-MM-DD-NNN
- **What happened:** [description of failure]
- **Root cause:** [why it failed]
- **Fix:** [what resolved it or what to do next time]
- **Date:** YYYY-MM-DD
```

ID format: `E-YYYY-MM-DD-NNN` where NNN auto-increments based on existing entries for that date.

## Command: /request [description]

Log a capability K2B doesn't have yet.

1. If description provided, use it. If not, infer from the current conversation what was requested.
2. Read `self_improve_requests.md`.
3. Check for duplicates. If a similar request exists, note it was requested again and update the entry.
4. Append new entry or update existing.
5. Confirm with one line.

Entry format:
```markdown
### R-YYYY-MM-DD-NNN
- **Request:** [what Keith wanted]
- **Why needed:** [context for the request]
- **Status:** open
- **Date:** YYYY-MM-DD
```

ID format: `R-YYYY-MM-DD-NNN` where NNN auto-increments based on existing entries for that date.

## Behavioral Note

When Keith corrects K2B during normal conversation ("no, do it like this", "that's wrong", "next time..."), proactively offer: "Want me to /learn that?" -- but only offer, never auto-capture without confirmation.

## Usage Logging

After completing the main task, log this skill invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-feedback\t$(echo $RANDOM | md5sum | head -c 8)\tcaptured TYPE: DESCRIPTION" >> ~/Projects/K2B-Vault/Notes/Context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep confirmations to one line
- Don't over-explain the system to Keith, just capture and confirm
- The three subcommands maintain backward compatibility -- /learn, /error, /request still work as before
