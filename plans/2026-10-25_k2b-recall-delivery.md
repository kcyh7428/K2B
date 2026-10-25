# K2B recall repair delivery

25 October 2026, UTC+8. Status: reviewed, committed, pushed, activated-home,
activated-sjm and accepted for this bounded recall repair. Phone service remains
deferred. The remaining knowledge-improvement tasks keep their separate gates.

## What Keith will notice

Ordinary local recall on either Mac finds the relevant subject more reliably
and retains useful answer passages plus current and historical context. Latin
names respect word boundaries, Chinese aliases remain supported, and explicit
topic links use the safe resolver. Answers still need source reading and may
require a bounded follow-up. Headings and relevance scores never declare truth.
Home remains the shared reconciler; these local reads add no Home dependency.

The runtime retains the existing packet schema and limits of 48,000 bytes,
twenty sources and eighty spans. Source hashes, exact quotes, passage dates,
authorship and correction-unit validation remain mandatory. BM25 and uniformly
generated question descriptions remain provider-free offline experiments.

## Reviewed Git delivery

Canonical runtime commit: `0a461637043ba781258ae5b7d72b02e1e350b561`.
Pushed to `origin/codex/knowledge-recall-20261025`.

Home fast-forwarded its maintained branch
`codex/assistant-alignment-home-20261024` from `bfda581` to that reviewed commit.
SJM deliberately cherry-picked it onto its equivalent divergent base, producing
`cc8bf0ce3a3a26b95d1f197ffd2667e6450f3b51` on
`codex/assistant-alignment-sjm-20261024`. The stable patch ID is identical:
`419649d40a9b41c852247cfbfb7effaecf71f16a`.

At runtime activation, all ten reviewed file hashes matched the staged manifest
on both installed hosts.
The runtime module hash is
`aa2b25a4f750998e53d6c805e9cf0be8ac5e435e41cd7c88c2997333b848b780`.
This record is a subsequent documentation change; the installed runtime checks
below bind the runtime commits above.

Independent Kimi K2.7 Code final review
`2026-10-25T05-30-38Z_2e62d1`, with no fallback, returned **APPROVE**, no findings.
It verified the earlier material corrections and the complete current runtime.
Its non-blocking tuning observation is retained: index-discovery boosting could
eventually weight the actually matched term more precisely. This does not change
the accepted implementation or create a new automation obligation.

## Verification

- Home final-candidate full Python suite: **1,707 tests and five subtests passed**.
- SJM installed recall, experiment, bootstrap and local-workflow suite:
  **184 tests passed**.
- Both installed CLIs: **17 of 17** gather, validate and render checks passed on
  each Mac. Twelve use the same fixed snapshot: ten diagnostic subjects, a
  selected-topic control and an unknown-term control. Five use each actual local
  vault, including a Chinese question and an unknown term. Each host's installed
  seventeen-check run has a maximum packet size of 47,708 bytes, as recorded in
  `home-installed-summary.json` and `sjm-installed-summary.json`.
- Both actual local bootstrap renderers completed with their correct host roles.
  Live authority verification passed on each Mac. Candidate note-authority,
  SessionStart and hook shell regressions also passed.
- The 160-packet fixed-snapshot comparison passed all source, locator and hash
  bindings, with at most 47,960 bytes per packet. Deterministic repair found all
  ten diagnostic subjects; BM25 found nine. On the original eighteen answerable
  comparison questions, literal passage checks are sixteen for repaired
  retrieval, seventeen with descriptions, fifteen for BM25 and sixteen for
  BM25 with descriptions. These original cases are now regressions. This separate
  comparison measurement is retained in `comparison-final.json`, SHA-256
  `508f1ef6de9e3a6d94bab2853b1828513912037be0987a06edebf5b329efef78`.

Twenty additional answer cases were frozen before passage tuning. All eighteen
answerable cases found their expected source in the first packet. Sixteen had
the required passages initially; two used one recorded bounded follow-up each
to an already discovered source. All twenty final drafts validated and rendered.
The two unknown cases safely abstained despite incidental generic material.

Independent semantic review `2026-10-25T05-25-50Z_a00488` returned **APPROVE:
twenty PASS and zero FAIL**, checking every factual claim, temporal qualification,
proposal status, historical correction and abstention against emitted evidence.
These are predeclared answer-support tests, not a benchmark of automatic
free-form answer generation or a general accuracy guarantee. Initial/final
packets and explicit follow-up parameters are retained privately. The final
code review also received the corrected twenty-case binding package.

## Preservation and vault observations

Home's provider/finance edits to `AGENTS.md` and the research skill are unchanged.
The original 1,163 untracked files are hash-accounted: the three authorized plan
documents that collided with the incoming tracked files were preserved under
the private `home-plan-collisions/` directory, and all other original files
remain byte-identical in the checkout. The original tracked patch and staged
state are unchanged.

SJM's transcription-skill, capture-parser and parser-test edits were narrowly
stashed, then restored byte for byte. Its 52 original untracked files are
unchanged. Its local `AGENTS.md` and hook bytes match the preserved copies.
A recovery stash is retained privately by reference. Credentials, native
application history and machine-local runtime state remain on their own Mac.

Before and after activation, both vault folders reported idle, zero needed
files/bytes/directories and zero errors. Five representative current note,
published-memory and semantic-shelf hashes matched across hosts. SJM rebuilt
the private evaluation snapshot from its own vault, matching all 479 fixed
manifest hashes and 8,168,378 bytes. Code moved through Git; vault content was
not copied as a code-deployment step. The reconstruction receipt is
`sjm-snapshot-preparation.json` in the private Home evidence root below; it
records `snapshot_ready: true`, 479 files and 8,168,378 bytes after verifying
each source against the fixed manifest.

## Limits and next work

This closes Task 1's bounded runtime and evidence-support gate. It does not
establish complete historical capture, current subject-note integration,
scheduled freshness or sustained end-to-end reliability. Tasks 2 to 5 retain
the reporting, supervised integration, guidance repair and full-path acceptance
work. Fresh desktop UI sessions and a new physical Home-off trial were not
repeated for this pure local retrieval change; their prior alignment acceptance
is historical evidence, and the current installed checks are CLI evidence.

No new capture source, background job, automatic summary writer, provider
fallback, local model or phone service is activated by this delivery.

Private receipts and preservation records:
`/Users/keithmbpm2/.local/state/k2b/recall-passage-delivery-20261025/` and the
corresponding SJM-local directory. The earlier comparison snapshot and failure
history remain under Home's `recall-repair-20261025/` directory.
