# Supervised knowledge integration delivery

Keith can now update current subject knowledge from exact retained evidence,
keep dated history and obtain a durable source-to-note completion receipt.
Tasks 3 and 4 are accepted for this bounded interactive workflow. Task 5's
fresh-desktop, final-version native-chain and seven-day gates remain open.
No unattended note writer, schedule change or wider capture scope was enabled.

## Code and review

Runtime commit `b10979e2b93bd156f735fae87a36ed726222b822` was pushed to
`origin/codex/knowledge-integration-20261026`, fast-forwarded into the Home
project and deliberately cherry-picked as SJM
`061469a06e61e42af80ab355da80e147a0a29f5f`.

DeepSeek supplied the initial integration prototype/core patches and the pure
raw classifier through the existing bounded engineering API connection. Only
curated code and synthetic fixtures were handed off. Codex repaired contracts,
implemented missing safety paths, wrote the real regression suite and reviewed
the live subject evidence locally. No vault, conversation history, credentials
or automatic extraction was sent to DeepSeek.

Kimi independently reviewed the mixed diff without fallback. Earlier findings
were fixed and rechecked: exact durable summary binding, supported asset/code
links, deterministic source-overlap blocking, pre-save Home ledger checks,
stdout-free replay with retained durable evidence, useful blocked actions,
reference/HTML link checks, footnote handling and index-leaf symlink refusal.
Final runtime review `2026-10-26T01-12-04Z_5d22df` returned NEEDS-ATTENTION only
for confirmation of the then-pending 189-test run. Its text found no code-level
blocker and explicitly permitted scoped shipping once that run passed. The
exact final run passed 189 tests before commit; this satisfied the conditional
ship gate. Do not relabel that review as an unconditional APPROVE verdict.

A one-file test portability correction followed as Home
`bfd21b083c704f8cf1723f2e87705acc38bf22dc` and SJM `4841a91`. Python patch
versions differ in JSON nesting tolerance: decoder refusal and rejection of a
non-object snapshot are both concise failures. An independent forced-recursion
test preserves specific decoder-error coverage. Kimi review
`2026-10-26T01-21-29Z_3ef0d5` returned APPROVE without fallback. Its residual
low-severity note is that the accepted invalid-schema substring could be more
specific. It is a test-precision limitation, with no runtime change.

Home's existing provider/instruction edits and SJM's transcription/parser
changes, plus both local AGENTS/hooks adaptations, were hash-preserved across
activation. SJM's assistant-only-turn parser delta was read separately and left
uncommitted, with its additional regression retained. Credentials and local
state were not transported.

## Installed verification

| Check | Home | SJM |
| --- | --- | --- |
| Ten focused capture/SQL/memory/context/integration/writer modules | 1,057 passed | 1,058 passed |
| Live authority verifier | Passed | Passed |
| Raw-helper compatibility shell test | Passed | Passed |
| Ordinary-writer authority shell test | Passed | Passed |
| Portable runtime hashes | Match | Match |

The count includes the seven-module audit baseline and three changed-flow
modules. SJM's extra pre-existing parser regression explains the one-test
difference. Tests use the existing local PyYAML-capable interpreters: Home
Python 3.12 and SJM's note-save environment. The default Home/SJM Python can
lack PyYAML, so direct Python commands must select the documented interpreter.
No dependencies were installed. An installed SJM compile-wrapper probe selected
its existing note-save interpreter with K2B_PYTHON unset and provider disabled,
and returned bounded context successfully.

## Actual subject integration

Three reviewed source-bound proposals covered four existing note targets:

| Topic | Result | Writer receipt IDs |
| --- | --- | --- |
| Amy onboarding | Current summary distinguishes end-October preference, intended HR support, historical mid-November target and unknown start/signature/completion | `965c6a3a546dbe29` |
| Kingdee/Pulse+ | Corrected Carmen chronology verified as already represented; business note bytes unchanged | `f8e36304b39329b1` |
| K2B operating model | Current Home/SJM authority replaces stale runtime summary; former deployment retained as labelled history | `6027b78a3a07a259`, `47e98a85edec9b9a` |

The Home private integration operation IDs end in `be388`, `cb4d1` and `ce8d`;
their complete bindings and before/after hashes are in the private engineering
acceptance receipt. Writer receipts live in note-write-receipts; integration
receipts live in digestion/integration-receipts. They reference the existing
evidence rather than creating another canonical fact store.

Replaying all four exact writer commands returned already-present. Replaying
the three record commands without transient stdout files returned
already-recorded. Note, index and integration-receipt bytes remained unchanged.
No raw capture metadata was modified. Published versions without receipts
remain unmeasured integration work, not asserted failures.

The protected lifecycle context was corrected through the approved Home
maintenance path: private mode-0600 before-copy, expected hash, no open editor
on either host, active-chat scope inspection and both Syncthing folders idle
with zero needed files/bytes/directories or errors immediately before apply.
Historical lifecycle text was preserved as a dated quotation. Authority passed
afterward.

Normal recall also selected the older multi-machine reference before the main
project note. Its current summary was corrected through the ordinary writer,
receipt `0ba7ba6892399a05`; the earlier capture-worker scope and measurements
remain quoted history. No retired host was contacted. This is a separate
operator-context maintenance save, not double coverage of a memory item.
An additional framing save, `a73f850cf5a3b75c`, places the superseded-history
label inside the retained quoted block and the retired-host boundary inside
the current summary, so bounded excerpts retain that distinction.

## Arrival, recall and limits

Both vaults reached idle with zero needed files/bytes/directories and errors.
The four subject targets, lifecycle context, folder/master indexes and published
snapshot were hash-compared across both Macs. Bounded normal recall brought
back Amy's newer qualified summary and Kingdee's corrected chronology. The
broad K2B query initially selected the operating-model reference; one explicit
topic-note follow-up included the main project. Packets remained partial under
the existing evidence/source/byte limits. Source inclusion and passage review
are not a general free-form answer-accuracy score.

Private complete records, before/draft copies, replay evidence, installation
manifests and review logs are under
`~/.local/state/k2b/engineering/task3-deepseek-20261026/` on Home. Machine-local
SJM installation preservation is under
`~/.local/state/k2b/engineering/task3-deployment-20261026/` there. These are
engineering evidence, not synchronized knowledge or a new memory runtime.

The actual capture backlog, one bounded interactive schema repair, observed
heartbeat dispatches, proposed schedule alignment and remaining gates are in
`plans/2026-10-24_k2b-knowledge-end-to-end-acceptance.md`. Capturing, publication,
subject integration and replication retain separate status and ownership.
