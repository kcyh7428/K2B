---
name: k2b-error
description: Log a failure, mistake, or unexpected behavior with root cause analysis. Use when Keith says /error, "that broke", "something went wrong", "that failed", or describes a problem K2B caused. Captures what happened, why, and the fix so the same mistake isn't repeated.
---

# K2B Error Logger

## Memory Path

- Errors: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_errors.md`
- Learnings: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_learnings.md`

## Command: /error [description]

Log a failure with root cause and fix.

1. If description provided, use it. If not, infer from the current conversation what went wrong.
2. Read `self_improve_errors.md`.
3. Append a new entry.
4. If the error reveals a generalizable learning, also add it to `self_improve_learnings.md` following the k2b-learn entry format.
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

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep confirmations to one line
- Don't over-explain, just capture and confirm
