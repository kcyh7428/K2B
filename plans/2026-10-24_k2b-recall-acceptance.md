# K2B recall repair checkpoint

The repair is now reviewed, committed, pushed, activated and accepted on both
Macs. See [the delivery record](2026-10-25_k2b-recall-delivery.md) for installed
versions, tests, preservation evidence and remaining scope.

The opening sections preserve the earlier checkpoint. The later passage and
answer gate at the end records the completed candidate tests and subsequent
delivery authorization. Installed-host acceptance must be evidenced separately.

25 October 2026, UTC+8. Prepared implementation and offline comparison, not a
live activation or full Task 1 acceptance. Phone service remains deferred.

## Scope and preservation

Keith authorized the plan update, recall repair and BM25/question-summary
comparison. Work uses the clean attached worktree at
`/Users/keithmbpm2/.codex/worktrees/k2b-assistant-alignment/K2B`, based on
`bfda581ae0ca2fdb3c8ca98f171069598fbf6c77`. Home's current runtime and SJM are
unchanged. The requested Home plan and checkpoint documentation are the only
live-checkout edits.

Before work, Home's tracked diff, status and 1,162 untracked file hashes were
saved privately. The original audit and improvement plan were copied before
editing. Existing provider/finance changes, credentials, capture, schedules,
shared memory and unrelated application work are outside this repair.

## Implemented candidate

- Score note candidates independently of index order. A discovered note can
  gain its stronger filename, title or declared-alias relevance.
- Weight distinct terms and corpus rarity. Repeated incidental prose cannot
  gain unlimited relevance. No date-based truth override is introduced.
- Prefer direct memory key/value matches over incidental evidence quotations.
- Spread the bounded note evidence allocation across relevant sources and keep
  useful body passages ahead of headings. Preserve correction units and the
  caller's explicit selected topic.
- Use word boundaries for Latin names and retain Chinese bigram matching.
  Long-passage windows use the same boundaries as discovery.
- Resolve supported vault-path, alias, heading and Markdown note links through
  one safe resolver, excluding index evidence and symlinked parents.

The packet schema and 48,000-byte, 20-source, 80-span limits are unchanged.
All emitted evidence remains an exact, source-linked passage or validated
memory item. Retrieval descriptions never become answer evidence.

## Comparison design

A private snapshot contains 479 files, 8,168,378 bytes, with a SHA-256 manifest.
It includes ordinary notes, their indexes, eligible human-capture raw trees,
the published memory snapshot and semantic shelf. Native history, authentication
and other machine-local application state were not copied.

Ten diagnostic questions and two controls reproduce the prior audit. Twenty
held-out questions were frozen before repair and scored separately: eighteen
source-grounded questions plus two unknown controls. Cases, source passages
and generated descriptions stay outside Git. The manifest hash is
`b4dc1e5341aad0460d5a5fa94582cfabfb535dc5545783e9f8066b660a86cd03`.

Five variants use identical snapshot, queries and packet bounds: unchanged
helper, repaired ranking, BM25, repaired ranking with descriptions, and BM25
with descriptions. BM25 uses an in-memory SQLite FTS5 index with bilingual
pre-tokenization; the measured cold gather includes index construction.

Descriptions are produced uniformly for all 286 eligible ordinary notes from
their opening source passages and title-based question templates. They are
query-independent and bound to exact source hashes. A preliminary hand-written
eight-topic draft is retained privately but excluded from the measured variants
to avoid selectively enriching expected subjects. This experiment does not
measure the benefit or cost of model-generated semantic paraphrases.

## Final fixed-code results

| Variant | Diagnostic expected notes | Held-out retrieval checks | Literal passage checks |
|---|---:|---:|---:|
| Unchanged helper | 2/10 | 5/20 | 2/18 |
| Repaired ranking | 10/10 | 20/20 | 11/18 |
| BM25 | 9/10 | 20/20 | 11/18 |
| Repaired with descriptions | 10/10 | 20/20 | 11/18 |
| BM25 with descriptions | 9/10 | 20/20 | 11/18 |

Both diagnostic controls pass in every variant. The twenty held-out retrieval
checks include correct empty results for two unknown queries. They are not
twenty correct model answers. All 160 final packets passed source, locator
and hash validation within their original byte limits.

Literal passage checks look for source-grounded answer markers in the expected
note's retained passages. They are deliberately reported separately from file
inclusion and from semantic answer quality. Some failures are formatting or
equivalent wording; others require an additional source read. The current
candidate can still retrieve a detailed historical paragraph before the current
summary. Finding the right file does not close this remaining gap.

Final report: private `comparison-after-review.json`, SHA-256
`960c5dd0272475bf2365a0d610b0fa6750573f5e54f3d885f6a9d0e63d1a6af4`.
The source snapshot and frozen cases/descriptions were rehashed after the run;
all remain unchanged. `final-evidence-manifest.json` binds the six implementation
and test file hashes, inputs and aggregate results. `accepted-packets/` contains
the 160 source-bound packets. An earlier rerun overlapped review fixes and
correctly aborted on code drift; it is not accepted evidence.

Cold median gather times on this Home snapshot were 294 ms for the unchanged
helper, 674 ms for repaired ranking, 693 ms for BM25, 715 ms for repaired ranking
with descriptions and 747 ms for BM25 with descriptions. These are descriptive
single-run observations, with other verification work on the host. They do not
establish a speed improvement or a production latency guarantee. Maximum packet
size across variants was 47,926 bytes.

## Decision and remaining acceptance

Keep repaired deterministic ranking as the implementation candidate. Neither
BM25 nor uniform extractive descriptions improves the observed retrieval checks;
BM25 loses one diagnostic source. Both stay in the offline comparison tool and
are not enabled by normal startup or recall. Richer semantic descriptions remain
an unproven possibility, requiring their own held-out evaluation.

Task 1 remains open for a full source-backed answer-quality assessment and
stronger first-packet passage selection, including bilingual historical/current
questions. The audit's target of eighteen correct, sufficiently sourced answers
out of twenty has not been established by source inclusion or marker checks.
Actual SJM candidate installation and two-host acceptance require reviewed Git
delivery and separate activation authority. No candidate code was copied to SJM.

The existing capture and subject-integration work in Tasks 2 to 5 remains
planned. Kimi history extraction, automatic summaries, background note writing,
new providers and phone service remain outside this checkpoint.

## Verification and review

The unmodified recall suite passed 120 tests. New regressions were observed
failing before the changes. One synthetic memory fixture was corrected to avoid
trailing whitespace in its hash-bound value; it then reproduced the intended
missing-second-subject failure against the saved baseline.

The previous Ann/Anna test expected both people for an Ann-only query. The new
boundary contract excludes Anna for that query and still retrieves both sources
when both names are explicitly requested. Name-only candidates whose source
body and title do not match remain outside emitted evidence.

The final focused recall suite passed 152 tests. The larger capture/memory run
passed 695 tests before the final regressions were added. The first full Python
run reported 1,692 passes and one environment failure: an orchestrator subprocess
selected Python without PyYAML. With the existing Python 3.12 directory on PATH,
the final post-fix full suite passes 1,702 tests and five subtests. No dependency
was installed and no orchestrator code was changed. Live authority and diff
whitespace checks pass.

Independent Kimi K2.7 Code review `2026-10-25T03-26-01Z_1969d2`, no fallback,
returned NEEDS-ATTENTION. The important findings were reproduced with nine RED
test cases and fixed in one GREEN pass: raw-byte CRLF description hashes,
ambiguous short-link handling, inconclusive freshness classification,
symlinked provisional scope and safe benchmark case IDs. There is no post-fix
approving verdict; commit and activation gates remain open.

The recheck retains its existing five-second bound and packet schema. A scan
that cannot complete now reports `candidate_recheck_inconclusive` and refuses
validation, rather than asserting that a source changed. Large custom-budget
gathers can still require a narrower query or retry. Incomplete gather inventories
remain explicitly partial. Invalid benchmark inputs abort without a success
aggregate, rather than silently presenting a partial comparison as complete.

One pre-existing minor remains: `test_explicit_raw_capture_adds_to_linked_captures`
is defined twice in the old test module, so the earlier definition is shadowed.
It is not counted as new coverage. New recall and review regressions have unique
names and were executed explicitly.

Preservation verification: Home's tracked patch is byte-identical; all 1,161
original untracked files other than the requested plan update are hash-identical
and present. Both Home and the worktree remain at the original Git head. SJM
was not modified. State: prepared, tested, independently reviewed with findings
fixed by regression tests, uncommitted, unpushed and not activated.

Private evidence root:
`/Users/keithmbpm2/.local/state/k2b/recall-repair-20261025/`.

## Passage and answer gate, 25 October 2026

Keith explicitly authorized finishing passage fixes and answer tests, obtaining
Kimi's final approval, then committing and activating on both Macs. Existing
changes must be preserved. The phone service remains deferred.

The candidate ranks exact body passages with distinct-term rarity, a length
penalty and section context. It keeps bounded current-summary passages for a
discovered subject, including cross-language questions, and retains relevant
historical labels. Original passage dates and locators remain intact. Leading
subjects receive answer and scope context before incidental sources use the
note reservation. The note reservation is twenty spans, capped at half the
caller's evidence budget; global packet limits remain unchanged. Correction
units retain their priority. No date-based truth override is added.

Five new passage/allocation tests were observed RED, then GREEN. The final full
Python suite passes **1,707 tests and five subtests**. The AST-identical duplicate
raw-link test is consolidated without altering its assertions. Authority and
diff whitespace checks pass.

The fixed-snapshot comparison was rerun after the passage changes:

| Variant | Diagnostic sources | Original twenty retrieval checks | Literal passages |
|---|---:|---:|---:|
| Unchanged helper | 2/10 | 5/20 | 2/18 |
| Repaired ranking and passages | 10/10 | 20/20 | 16/18 |
| BM25 | 9/10 | 20/20 | 15/18 |
| Repaired with descriptions | 10/10 | 20/20 | 17/18 |
| BM25 with descriptions | 9/10 | 20/20 | 16/18 |

All ten expected diagnostic subjects and both controls pass for the repaired
candidate. All 160 comparison packets bind successfully, at most 47,960 bytes.
The original twenty cases are now regression cases. Uniform descriptions add
one literal-passage hit but no source-inclusion gain; this does not establish
a semantic advantage or justify a new runtime dependency. Both optional
variants remain offline. Cold median gather times were 300, 753, 751, 790 and
791 ms respectively, descriptive single-run measurements under test load.
The report hash is
`508f1ef6de9e3a6d94bab2853b1828513912037be0987a06edebf5b329efef78`.

Twenty additional answer cases were frozen before passage tuning: eighteen
answerable cases and two unknown controls with common vocabulary. All eighteen
expected sources are included in the first packet. Sixteen contain the required
literal passages initially; two use one bounded follow-up each, selecting an
already discovered source and refining the query. Initial and final packets,
follow-up parameters and validation outcomes are retained privately. All twenty
final answer drafts pass source/locator/hash validation and render successfully.
Unknown names can retrieve generic material, so safe abstention is assessed
instead of incorrectly requiring the entire packet to be empty.

Independent Kimi K2.7 Code semantic review
`2026-10-25T05-25-50Z_a00488`, with no fallback, returned **APPROVE: twenty PASS,
zero FAIL**. It checked every claim against the emitted evidence, including
date qualifications, reported versus independently verified state, proposals,
historical corrections and bounded-scope abstention. Its optional follow-up
traceability suggestion is covered by retained initial/final packets and query
parameters. These are predeclared answer-support cases, not independently
generated free-form responses or a general answer-accuracy guarantee. The
review input preceded a private driver correction to preserve selected-topic
bindings; the evidence and answer text did not change, and all twenty final
bindings were separately validated afterwards.

Current runtime module SHA-256:
`aa2b25a4f750998e53d6c805e9cf0be8ac5e435e41cd7c88c2997333b848b780`.
The source snapshot and original cases/descriptions remain unchanged. SJM's
private evaluation snapshot was reconstructed from its own synchronized vault,
matching all 479 hashes and 8,168,378 bytes. Both vaults reported idle, zero
needed files/bytes and no errors, with five representative source hashes equal.

At this pre-commit record, final staged code approval and installed-host
acceptance remain to execute. Broader capture coverage, subject integration,
fresh-desktop and sustained end-to-end acceptance remain separate tasks.
Private evidence for this phase is under
`/Users/keithmbpm2/.local/state/k2b/recall-passage-delivery-20261025/`.
