# K2B knowledge-status delivery

Prepared 25 October and completed 26 October 2026, UTC+8. Status: reviewed,
committed, pushed, activated-home, activated-sjm and accepted for the bounded
Task 2 report. The remaining improvement tasks retain their own done checks.

## What Keith will notice

Task 2 adds one manual, read-only CLI report for current source coverage and
stage freshness. It separates file-session coverage, SQL coverage, delivery,
publication, integration, native-job observations and replication. Unknowns
remain visible. It never sums overlapping file and SQL inventories.

Home remains the semantic reconciler. SJM reads its own local source and
delivery state. The report adds no Home dependency for SJM. No startup-hook
integration, cache, background job, provider setting or paid fallback is added.

## Reviewed delivery

Canonical runtime commit: `49ccd936ebb7f869c1e8f10be7d2a77c64b30c83`.
Branch: `codex/knowledge-status-20261025`.
The branch is pushed to `origin/codex/knowledge-status-20261025`.
Home fast-forwarded its maintained branch to this commit. SJM cherry-picked
the same patch onto its equivalent base as
`59534d419aadbf494b92b70785b2e7467d51e098`.

The subsequent test-only portability correction is canonical commit `b08952a`,
installed on Home and cherry-picked on SJM as `f3d63b5`. It pins synthetic test
dates across a real day rollover and checks ordinary and token-like temporary
paths without weakening secret redaction or unchanged-state assertions.

Independent Kimi K2.7 Code review `2026-10-25T15-38-43Z_059346` returned
**APPROVE**, no findings, for the exact runtime candidate. Test-only review
`2026-10-25T23-22-36Z_2b1fd1` also returned **APPROVE**, no findings. Both used
no fallback. The first review used complete new-file contents, identical to
the staged added-file diff, to avoid duplicating every new file in context.

DeepSeek supplied implementation and correction patches through the existing
protected K2Bi coding-worker connection. Codex applied the patches, corrected
adapter and regression issues, and ran verification. Kimi is independent of
both builders. The worker received selected code and synthetic tests, without
live vault contents, native history, SQL databases, outboxes or credentials.

## Verification

- Final candidate focused suite: 292 tests passed.
- Final candidate live smoke: completed in about four seconds, with no observed
  pipeline-state changes. Fresh observations no longer trigger clock-skew
  warnings. A short scan can legitimately report unknown or partial coverage.
- Installed Home focused suite after the portability correction: **293 passed**
  under Python 3.12.2.
- Installed SJM focused suite after the portability correction: **293 passed**,
  using its existing note-save Python 3.12.14 environment and normal pytest temporary
  root. No test dependency or production redactor change was needed.
- Installed live reports completed in about 4.2 seconds on Home and 1.4 seconds
  on SJM, with no observed pipeline-state changes. They retain unknown coverage
  when a short scan cannot establish the full inventory. SJM reports no local
  publication receipt, consistent with Home-only publication ownership.
- The runtime file hashes matched the reviewed candidate on both hosts.
  Existing dirty files, Home provider edits and SJM parser/transcription edits
  remained byte-identical. Each host's AGENTS.md and hook bytes were preserved.
- Both vaults reported idle, zero needed files/bytes/directories and zero errors
  after activation. Five representative note, published-memory and semantic
  shelf hashes matched across hosts. No vault content was copied through Git.

The regressions cover separate owner roots, canonical date arguments, relocated
Codex roots, widened-scope rejection before collection, role denial before
readers, unavailable versus zero counts, incomplete scans, stale observations,
offline peers, forged union/dispatch/artifact claims, bounded binary reads,
publication lag, and exact report-clock overrides.

Publication-event clock skew preserves the receipt binding while making lag
unknown and visible. Rejected receipts remain distinct from an empty folder.
Deeply nested local JSON degrades safely. Delivery backlog age comes from
validated envelope creation times; an incomplete scan labels its age as an
observation of inspected envelopes only.

## Usage and limits

From each installed project directory, use its Python 3.12 interpreter:

```bash
python3 scripts/k2b-knowledge-status.py report --writer-role home
python3 scripts/k2b-knowledge-status.py report --writer-role sjm-source-only
```

The first command is for Home and the second for SJM. File and SQL scans each
receive the configured scan budget. The report does not stage SQL sources or
change the approved K2B/K2Bi scope, which begins 13 September 2026 and ends on
the resolved current UTC+8 date. SQL selection remains `operator_only`.

Publication receipts describe successful snapshots. The current publisher
retains staged recovery journals across interrupted runs but has no measured
latest-attempt outcome. The report says that outcome is unmeasured. A staged
journal is not proof of failure, and a success receipt does not verify live
artifact hashes. Native registration and run receipts do not verify native
dispatch. Subject-note integration remains unmeasured. Idle replication does
not establish complete capture or integrated knowledge.

This delivery closes Task 2's report gate only. Tasks 3 to 5 retain the
supervised integration, guidance repair and full-path observation work.

Private worker, preservation and verification evidence:
`/Users/keithmbpm2/.local/state/k2b/engineering/task2-deepseek-20261025-5f75f0xx/`
and the SJM-local `~/.local/state/k2b/engineering/task2-deployment-20261025/`.
