---
name: k2b-improve
description: Review all self-improvement logs and surface patterns. Use when Keith says /improve, /improve review, "review learnings", "what have you learned", "show me errors", "open requests", or wants to audit how K2B has been improving over time.
---

# K2B Self-Improvement Review

## Memory Paths

- Learnings: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_learnings.md`
- Errors: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_errors.md`
- Requests: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_requests.md`

## Command: /improve review

Review all self-improvement logs.

1. Read all three memory files.
2. Report:
   - Total learnings count
   - Top 5 most-reinforced learnings
   - Recent errors (last 30 days)
   - Open feature requests
3. Suggest any learnings with `Reinforced` >= 3 for promotion to standalone memory files.
4. Flag any recurring error patterns.
5. If any learnings are older than 90 days and reinforced only once, suggest pruning (confirm before deleting).

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep the report scannable, use bullet points
- Don't over-explain, just present the data
