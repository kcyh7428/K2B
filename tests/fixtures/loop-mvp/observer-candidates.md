# Observer Candidates

Last analysis: 2026-04-22 21:44
Observations analyzed: 13

## Summary
Fixture for loop-mvp test. Five candidates from the 2026-04-22 21:44 observer run, frozen for deterministic testing. Do NOT edit -- the loop-mvp binary test asserts exact content.

## Candidate Learnings (confirm with Keith)
- [high] workflow: Treat parse errors and silent failures as blocking invariants, not advisories -- fold before ship, freeze the shelf on parse error, refuse the status transition
  Evidence: WMM Commit 2 Codex pass 1 caught malformed-bullet silent delete; folded as "any parse error freezes the shelf" invariant
- [high] workflow: When an offline deadline looms, write a durable handoff note rather than commit partial state -- log-and-resume beats half-shipped
  Evidence: Session summary favoured durable handoff note over half-shipped state when given 1 min offline window
- [high] workflow: Shipping order is /ship first (wiki + admin lanes), /sync second (deploy lane) -- admin must complete before deploy
  Evidence: Session summary ship first, sync second when asked the ordering
- [medium] writing-style: When a data format choice affects retrieval quality, measure signal and switch formats empirically -- do not defend the canonical form if measurement says otherwise
  Evidence: On WMM Commit 2, canonical pipe-delimited shelf-row format drowned embedding signal when fed to sentence-transformers
- [medium] workflow: Accept multiple adversarial review passes when each pass produces a real finding -- gate quality beats shipping speed
  Evidence: On WMM Commit 2, four Codex adversarial review passes fired (pass 4 was approve); each of the first three passes produced a new HIGH that was real
