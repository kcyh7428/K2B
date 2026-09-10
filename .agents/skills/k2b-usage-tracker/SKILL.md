---
name: k2b-usage-tracker
description: Manually inspect or append K2B skill usage. Use when Keith says "check usage", "how often have I used", "usage stats", or when an explicitly invoked skill needs to record its invocation.
---

# K2B Usage Tracker

Track skill invocations and report threshold readiness. Stage 1 does not fire actions automatically.

## Files

- **Usage log**: `~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv`
- **Trigger rules**: `~/Projects/K2B-Vault/wiki/context/usage-triggers.md`

## Commands

- `/usage` -- Show usage stats summary
- `/usage log <skill-name> "<description>"` -- Manually log a skill use
- `/usage triggers` -- Show current trigger rules and status
- `/usage add-trigger <skill> <threshold> "<action>"` -- Add a new trigger rule
- `/usage remove-trigger <skill>` -- Remove a trigger rule
- `/usage check` -- Check all triggers and report any that are ready

## Usage Log Format

Tab-separated file. Each line:
```
date	skill	session	notes
2026-03-24	k2b-meeting-processor	abc123	processed fireflies transcript for hiring sync
2026-03-24	k2b-daily-capture	def456	morning daily note
```

### Logging a Use

Append a line to the TSV:
```bash
python3 "$HOME/Projects/K2B/scripts/k2b-shared-append.py" usage --skill <skill-name> --summary "<description>"
```

## Trigger Rules Format

The triggers file uses a simple markdown table:

```markdown
| Skill | Threshold | Window | Action | Last Fired |
|-------|-----------|--------|--------|------------|
| k2b-meeting-processor | 10 | rolling | Run /insight meetings to surface patterns | 2026-03-20 |
| k2b-insight-extractor | 5 | rolling | Run /content to generate ideas from insights | never |
| k2b-daily-capture | 30 | rolling | Run /improve vault for vault health check | never |
```

- **Threshold**: Number of uses since last fired (or since tracking began)
- **Window**: `rolling` means count since last fired. `total` means count all-time.
- **Action**: The prompt or slash command to execute
- **Last Fired**: Date when this trigger last executed (or `never`)

## Checking Triggers

When Keith explicitly calls `/usage check`:

1. Read `skill-usage-log.tsv`
2. Read `usage-triggers.md`
3. For each trigger:
   a. Count uses of that skill since `Last Fired` date (or all uses if `never`)
   b. If count >= threshold:
      - Notify Keith: "Trigger ready: [skill] has been used [N] times since [date]. Action: [action]"
      - Do not execute it automatically. Keith must explicitly request the action.
      - Update `Last Fired` only after the requested action actually completes.
4. If no triggers are ready, report "All triggers below threshold"

## Usage Stats

When `/usage` is called:

1. Read `skill-usage-log.tsv`
2. Calculate:
   - Total invocations per skill (all time)
   - Invocations per skill (last 7 days)
   - Invocations per skill (last 30 days)
   - Most active day of week
3. Display as a summary table:

```
## K2B Skill Usage -- Last 30 Days

| Skill | 7d | 30d | All Time | Trigger Status |
|-------|-----|------|----------|----------------|
| k2b-daily-capture | 5 | 22 | 22 | 8/30 to next |
| k2b-meeting-processor | 3 | 8 | 8 | 8/10 to next |
| k2b-research | 1 | 4 | 4 | -- |
```

## Integration with Other Skills

Every k2b-* skill should log its usage. The logging instruction is a single bash append at the end of the skill's execution. Skills should include this at the end of their workflow:

```
After completing the main task, log usage:
python3 "$HOME/Projects/K2B/scripts/k2b-shared-append.py" usage --skill <SKILL-NAME> --summary "<brief-description>"
```

## Notes

- No em dashes, no AI cliches, no sycophancy
- The usage log is append-only. Never delete or modify existing entries.
- Triggers should always ask Keith before firing (don't auto-execute)
- Keep the TSV clean: one line per invocation, no blank lines
- Session IDs are random 8-char hex strings for grouping within a session
