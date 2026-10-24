# K2B assistant alignment checkpoint

Date: 24 October 2026, UTC+8.

## What is prepared

One local, provider-free startup renderer serves Codex SessionStart and the
explicit Kimi Work project entry. It identifies local authority, index, rules,
profile, preferences, provider policy, body implementation and native reader by
path/hash. Missing and older sources are labelled. Helper presence is separate
from verified session capability. Ordinary recall and saves reuse the existing
context and expected-hash writer contracts.

The candidate is in the attached managed worktree, based on e04a84a. It has not
been committed, pushed or activated on either Mac. The personal phone service
and personal cloud export remain deferred. No history extractor, memory job,
shared reconciliation authority, provider dependency or credential was changed.

## Automated acceptance

Python 3.12 final gate: **443 passed** across bootstrap, local save-to-reader,
bounded context, ordinary writer and native-job-status tests. Twenty-six of these
cover shared startup, including missing/unsafe sources, absent optional tools,
read-only behavior, source provenance, a completed 9.1-second body, a stalled
TERM-resistant process group, and both internal status timeouts firing together.

Passing shell gates:

- tests/session-start-hook.test.sh
- tests/codex-hooks.test.sh
- tests/verify-codex-authority.test.sh
- tests/vault-note-authority.test.sh
- scripts/verify-skills-parity.sh
- scripts/verify-codex-authority.sh
- git diff --check

The final gate used LC_ALL=C. An earlier note-authority attempt failed because
the inherited C.UTF-8 locale was invalid; the diagnosed environment retry
passed without changing its assertions or production code.

## Actual assistant and host checks

Kimi Work desktop on Home ran the separate task **Isolated K2B Alignment
Acceptance Test**, using only a synthetic vault and private state. It read the
candidate instructions and skill, ran bootstrap, gathered/validated/rendered a
selected running note, and distinguished the fictional current violet decision
from earlier amber. The packet contained one source and no recruiting record.

It then saved a new fictional ordinary note through the real writer, verified
the folder and master index, drafted from a local fictional company note and
performed the required temporary-file circulation check. It retrieved the
official public guide using its actual FetchURL and WebSearch tools. The draft
scan exited 1 and its temporary file was deleted. No mailbox operation occurred.

Work's receipt a3a431bf88393b49 reports saved-local and synchronization
unverified. The 488-byte source SHA-256 is
`a17e09ddd48a43bb73365761f89a7fd8da6d1682933e9d41dd8d93a768811ec8`.
A separate fresh Codex session gathered, validated and rendered that exact
Work-saved note and verified identical receipt, backup and source bytes.

Actual SJM separately ran its existing local writer and a fresh reader against
an isolated temporary fixture using Python 3.12.14. It returned saved-local,
one source, the fictional violet decision, and synchronization unverified. Its
temporary fixture was removed. No candidate code was copied to or activated in
the SJM checkout.

The fixture lacks published memory and a coverage receipt, so the assistant
packets correctly retain published_memory_absent, packet_partial and
coverage_receipt_unavailable labels. Missing fixture profile/rules/provider
notes were stated rather than invented.

## Review evidence

The independent reviewer is Kimi K2.7 Code, with no fallback.

- 2026-10-24T13-50-01Z_a06dbf: NEEDS-ATTENTION, followed by one consolidated
  correction for labels, provenance, cleanup and completed-body preservation.
- 2026-10-24T14-02-35Z_852a45: NEEDS-ATTENTION, exposing uninstrumented startup
  and the combined external/internal budget. The final supervisor and hook
  allowance close that family; seventeen bootstrap regressions pass.
- 2026-10-24T14-09-22Z_b7cbc7: no verdict. Completion budget 8,000 was exhausted
  with empty response text. This is retained as failed transport, not approval.
- 2026-10-24T14-13-20Z_90f7a5: NEEDS-ATTENTION on bounded output and missing-checker
  status. Four regressions reproduced the gaps and pass after correction.
  The supported fallback boundary and real conflict helper were also inspected.
- Findings-focused closure verdicts below complete the independent review.

Review 2026-10-24T14-22-19Z_ba3ce5 accepted the prior correction families and
reported three low residuals. They are closed with regressions: the final
post-KILL wait has a timeout and explicit incomplete-cleanup state; NUL-corrupted
text is unavailable instead of silently stripped; the historical ten-second
ledger statement is marked superseded. The final gate now passes 441 tests.

Review 2026-10-24T14-30-49Z_98b36b verified those three closures and identified
one low UTF-8 decoding residual. Both real-CLI corrupt-source regressions first
failed, then passed after the supervisor decoder and per-section validator
correction. Native iconv is present on both Macs. The final gate is 443 tests.
The exact remaining diff, before/after hashes, complete affected interfaces
and gate output are pinned in `.code-reviews/utf8-closure-review.md`.
The missing-packet attempt 2026-10-24T14-42-45Z_b01834 is retained as invalid
evidence. It is not an approval or a valid code assessment.

Final review 2026-10-24T14-44-09Z_c3f683 explicitly **APPROVE**, with no findings,
closes the last UTF-8 residual. The preceding accepted correction families plus
this final closure mark the implementation reviewed. The optional locale
observation needs no runtime change: the shared shell body already exports
LC_ALL=C before its byte-length checks. No runtime file changed after review.

Exact logs and private fixture receipts are retained under
`.code-reviews/alignment-evidence-20261024/`, with an evidence hash manifest.
The implementation ledger records the review dispositions and deadline design.

## Delivery and remaining acceptance

The local implementation and synthetic workflow checks are complete. Actual
SJM Kimi Work acceptance, a physical Home-off combined test, deliberate code
delivery and activation on each Mac remain open. A synthetic diagnostic SJM
role on Home is not actual SJM Work. A local save is not counterpart arrival.

Before delivery, preserve and reconcile Home's existing AGENTS/research edits
and SJM's parser/test/transcription and hook adaptations. Activate only the
reviewed changes, then verify startup in fresh conversations on each Mac.
The candidate changes the bounded hook allowance from ten to twenty seconds
so its twelve-second supervisor plus cleanup can finish before a platform kill.
No absolute cleanup guarantee is possible after an external SIGKILL or OS crash.

## Changed-file manifest

Runtime and authority:

- AGENTS.md
- .codex/hooks.json
- .agents/skills/k2b-research/SKILL.md
- .agents/skills/k2b-vault-writer/SKILL.md
- scripts/hooks/session-start.sh
- scripts/hooks/bootstrap-local.sh
- scripts/k2b-bootstrap.py
- scripts/lib/k2b_bootstrap.py
- scripts/verify-codex-authority.sh

Checks and records:

- tests/test_k2b_bootstrap.py
- tests/test_assistant_local_workflow.py
- tests/codex-hooks.test.sh
- tests/verify-codex-authority.test.sh
- tests/kimi-work-parity.md
- plans/2026-10-24_k2b-assistant-alignment.md
- plans/2026-10-24_k2b-assistant-alignment-acceptance.md
