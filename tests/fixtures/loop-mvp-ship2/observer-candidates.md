# Observer Candidates

Last analysis: 2026-04-24 20:02
Observations analyzed: 7

## Summary
Ship 2 fixture. Three candidate learnings from a simulated live observer run on 2026-04-24. Mimics the exact shape written by the pm2 k2b-observer-loop (including Summary + Candidate Learnings sections, `Last analysis` timestamp). The loop-mvp-ship2 binary test asserts this file can be routed end-to-end through the live path, not only the Ship 1 fixture path.

## Candidate Learnings (confirm with Keith)
- [high] workflow: When shipping touches more than one skill body, require Codex plan review at Checkpoint 1 before any code lands -- shared state deserves the second pair of eyes
  Evidence: 2026-04-24 Ship 2 scope touched three skill bodies; policy edit landed the same day without plan review and Keith flagged the gap
- [medium] preferences: Deferred review items without a visible ageing badge rot the same way candidates did before the loop -- show a count, archive on third defer
  Evidence: 2026-04-24 dashboard review/ section surfaced the same two items Keith has ignored for five sessions; no visible ageing signal
- [medium] vault: Auto-archive records deserve a dedicated JSONL per surface so audit queries can aggregate by surface kind
  Evidence: 2026-04-24 design review noted reviewer and observer archives shared one namespace; separate files lets /lint count by surface
