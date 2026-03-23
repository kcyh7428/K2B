---
name: k2b-request
description: Log a capability or feature that K2B doesn't have yet. Use when Keith says /request, "I wish you could", "can you do X" (and the answer is no), "feature request", "you should be able to", or identifies a gap in K2B's abilities.
---

# K2B Feature Request Logger

## Memory Path

- Requests: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_requests.md`

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

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep confirmations to one line
- Don't over-explain, just capture and confirm
