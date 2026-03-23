---
name: k2b-learn
description: Capture a correction, preference, or best practice to make K2B smarter. Use when Keith says /learn, "remember that", "don't do that again", "next time do X", "you should know", or any variation of teaching K2B something. For errors use /k2b-error, for feature requests use /k2b-request, for reviewing all logs use /k2b-improve.
---

# K2B Learning Capture

## Memory Path

- Learnings: `/Users/keithmbpm2/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_learnings.md`

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
- **Date:** YYYY-MM-DD
```

ID format: `L-YYYY-MM-DD-NNN` where NNN auto-increments based on existing entries for that date.

## Behavioral Note

When Keith corrects K2B during normal conversation ("no, do it like this", "that's wrong", "next time..."), proactively offer: "Want me to /learn that?" -- but only offer, never auto-capture without confirmation.

## Notes

- No em dashes, no AI cliches, no sycophancy
- Keep confirmations to one line
- Don't over-explain the system to Keith, just capture and confirm
