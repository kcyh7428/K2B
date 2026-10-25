# K2B Conversation Knowledge Improvement Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task after Keith authorizes implementation. Follow AGENTS.md for independent review and delivery. This plan does not authorize commits, activation or new background work.

**Goal:** Make eligible conversations reliably discoverable, integrate reviewed knowledge into current subject notes, and return relevant, dated evidence for natural questions on both Macs.

**Architecture:** Retain native Codex extraction, existing private receipts, the Home reconciler, published memory JSON, Markdown notes and Syncthing. Repair deterministic retrieval first, expose stage-specific coverage, and add a supervised integration ledger referencing existing memory items and note-writer receipts. Consider bounded background integration only after the supervised path and two-host acceptance pass.

**Tech Stack:** Python 3.12, existing SQLite readers, PyYAML, Markdown, native Codex Automations and Syncthing. No new usage-billed API or model runtime.

**Spec:** `plans/2026-10-24_k2b-conversation-knowledge-audit.md`, especially Required behavior after improvement and Acceptance and limits.

**Current delivery, updated 26 October 2026:** Task 1's recall repair is reviewed, committed, pushed,
activated and accepted on Home and SJM. See
[the delivery record](2026-10-25_k2b-recall-delivery.md). Task 2's bounded status
report is also reviewed, delivered and accepted on both Macs, as recorded in
[its delivery record](2026-10-25_k2b-knowledge-status-delivery.md). On 26 October,
Keith authorized continuing development and reviewed deployment of the remaining
work. Tasks 3 to 5 are authorized but not yet accepted; their actual acceptance
evidence remains pending.
Phone service, wider source scope, automatic SQL selection, schedule changes
and background integration retain their separate authorization gates. The dated
checkpoints below preserve their original preparation-state observations.

## Approved recall work, 25 October 2026

Keith authorized updating this plan and starting Task 1, including a comparison
of the current helper, repaired ranking, BM25 and question-oriented retrieval
summaries. This is an implementation and evaluation checkpoint. It does not
authorize committing, pushing, updating either live checkout, new capture
sources or background jobs. Tasks 2 to 5 retain their separate gates. The phone
service and its cloud export remain deferred.

Keith subsequently authorized finishing passage selection and answer-quality
tests, obtaining Kimi's final approval, committing and activating the reviewed
repair on both Macs. That later instruction supersedes this section's original
implementation-only delivery boundary for Task 1. It does not activate Tasks 2
to 5 or the phone service. Preserve existing local changes on both hosts.

The additional ideas come from Nick Saraev's video, reviewed through a complete
audio transcription: https://www.youtube.com/watch?v=LP7bfT4vOlo. Treat its speed,
cost and reliability claims as the creator's experience, not K2B acceptance
evidence. SQLite FTS5 documents the BM25 baseline:
https://sqlite.org/fts5.html.

Compare on a fixed, private source snapshot and fixed cases. Keep the ten
diagnostic subjects and two controls separate from twenty held-out questions.
Record expected-source inclusion, reciprocal rank, unsupported-source hits,
packet size, latency and source-bound answer checks. Freeze the held-out cases
before tuning; report any later case change. A source hit alone is not a correct
answer. Keep private names, passages and generated summaries outside Git.

Question-oriented summaries are disposable discovery aids: brief descriptions
and likely future question phrasings tied to a vault-relative source and its
content hash. They must not introduce facts, become evidence themselves or
replace the source. Changed or missing sources invalidate the aid. Evaluate
lexical ranking and BM25 both with and without these aids to separate the
ranking effect from the description effect. Do not enable automatic summary
generation, add a new canonical memory store or change the packet schema merely
to run this experiment.

Source time, event time and ingestion time remain distinct. Relevance and
explicit correction relationships take precedence over an arbitrary recency
boost. Preserve historical answers, confirmed decisions and uncertainty about
drafts. Pruning a disposable index must never delete source evidence or history.
Benchmark results determine whether BM25 or descriptions merit later runtime
integration; neither is presumed superior.

## Global constraints

- Preserve K2B's current Home/SJM roles and all unrelated unfinished work. No retired-host contact or configuration.
- Automatic capture remains limited to the approved project/source scope and 13 September 2026 through the current HKT date until a separate scope approval.
- Retain native extraction limits: 12 completed turns, 240,000 cumulative input-view bytes, 20 minutes per run and 48,000 bytes per selected current-turn view.
- Retain evidence packet limits: 48,000 bytes, 20 sources and 80 spans. Never truncate the current source turn to manufacture extraction success.
- Raw captures, source snapshots and historical provenance receipts remain immutable. Compilation/integration completion belongs in private receipts.
- Use the ordinary-note writer, expected content hashes, conflict checks and durable recovery copies for permitted note saves. Shared hub writes keep their designated Home writers.
- The current ordinary-note exception covers active user-requested conversations. A background writer needs a separately reviewed authority change before activation.
- Preserve source time, original speaker and the distinction between draft, proposal, user decision, reported completion and independently verified behavior.
- Keep credentials and native history on their owning Mac. No new OpenAI API dependency, paid fallback, embeddings, local model or external notification channel.
- Never use em dashes or double-hyphen prose substitutes. Literal flags, code and Markdown delimiters retain their ordinary syntax.
- The original plan request alone did not authorize delivery. Keith's subsequent explicit Task 1 delivery instruction authorizes its reviewed commit and two-Mac activation. Each commit still needs the independent reviewer required by AGENTS.md; an OpenAI-built diff requires an approving Kimi verdict with no fallback.

## Review focus

- Short names, common words and mixed-language queries must not let index order or incidental quotations displace the named subject. Task 1 owns these tests.
- Identical conversation evidence represented in file and SQL forms must count once; missing images, deadlines and source revisions remain visible. Task 2 owns these tests.
- A partial multi-note save, a source revision or a real Syncthing conflict must not receive a successful integration receipt. Task 3 owns these tests.
- A source describing a draft or earlier project state must not silently become a completed action or current policy. Tasks 1, 3 and 4 own these tests.
- Offline peers, extraction holds, quota exhaustion and overlapping workers must not create healthy freshness claims or new paid work. Tasks 2 and 5 own these tests.

## Task 1 Repair evidence selection and establish real recall regressions

**Files:** Modify `scripts/lib/k2b_context.py` and `tests/test_k2b_context.py`. Create `tests/fixtures/k2b-context-ranking/` with synthetic minimal equivalents of the observed failure patterns, and `plans/2026-10-24_k2b-recall-acceptance.md` for the private-case specification and aggregate results. Do not check in copied private conversation text.

**Interfaces:** Preserve `gather(query: str, vault_root: Path, ...) -> dict`, the packet schema and existing validation semantics. Make ranked note candidates and memory evidence deterministic functions of the same source snapshot. Derive relevance rather than encoding the names used in the acceptance cases.

- [x] Add failing tests `test_named_subject_outranks_earlier_index_row`, `test_memory_key_value_match_outranks_incidental_quote`, `test_two_query_entities_survive_memory_pressure`, `test_current_subject_outranks_incidental_retired_feature`, and `test_topic_expansion_supports_vault_path_and_markdown_links`. Keep existing bare-link, alias and heading behavior.
- [x] Add tests for short-name word boundaries, Chinese/English aliases, no evidence for an unknown term, source/evidence coherence under byte trimming, and correction endpoints retained together. A historical query must still be able to retrieve historical notes.
- [x] Run the new tests and record the pre-change failures. Use redacted or synthetic fixtures with crowded index rows and memory records ordered before the desired subject alphabetically.
- [x] Refactor ranking so an index-discovered candidate can receive its strongest filename/title/alias match. Rank direct key/value and subject evidence ahead of incidental quote matches. Prefer distinct query-term coverage and explicit entity phrases before deterministic tie-breaking.
- [x] Allocate the existing packet budget across relevant entities and source types before filling it with lower-ranked matches. Preserve the selected topic anchor and accepted correction chains; do not fix this by raising global limits.
- [x] Reuse the safe note-link resolver for selected-topic expansion, keeping traversal bounded and excluding index/control paths and escapes.
- [x] Run `python3 -m pytest tests/test_k2b_context.py -q`. Repeat the twelve diagnostic CLI cases on each host against one recorded snapshot. All ten expected subject sources, the selected-topic control and unknown-term control must pass. Review twenty held-out questions for answer quality rather than merely matching filenames. The new twenty-case gate assesses predeclared answer support, with two recorded bounded follow-ups, rather than automatic free-form generation; see the delivery record for this limit.

**Checkpoint:** Relevant evidence is retained within the existing limits. Existing authorship, correction, freshness and citation validation remain intact. Prepare an isolated diff for the required reviewer; commit only after separate delivery authorization.

### Recall comparison additions

- [x] Preserve the Home dirty-state inventory and use the clean attached worktree. Save the unmodified retrieval implementation and a content-hash manifest of the evaluation snapshot privately.
- [x] Run the current helper on the fixed cases before changing production code. Add reproducing tests for ranking, multi-source retention, token boundaries, link resolution and evidence/source coherence; observe the failures before implementing fixes.
- [x] Compare the unchanged helper, repaired deterministic ranking, BM25, repaired ranking with question summaries, and BM25 with question summaries. Use identical snapshots, queries and bounds. Record descriptive timing without presenting a single run as a performance guarantee.
- [x] Add synthetic tests proving descriptions only affect discovery, stale description hashes are rejected, no-evidence questions stay empty, and original source passages remain mandatory. Preserve authorship, corrections, explicit topic selection and packet validation in every variant.
- [x] Keep BM25 and summary experimentation in the provider-free offline evaluation tool until its source binding and held-out results justify a reviewed runtime choice. Include Chinese tokenization and aliases in the comparison.
- [x] Record the selected implementation, rejected alternatives, aggregate results and remaining acceptance gaps in `plans/2026-10-24_k2b-recall-acceptance.md`. Obtain independent Kimi review of the complete proposed change with no fallback. Leave the result uncommitted for delivery review.

### Checkpoint, 25 October 2026

The isolated repaired candidate includes all ten expected diagnostic notes,
compared with two for the unchanged helper and nine for BM25. Uniform extractive
question descriptions add no source-inclusion gain in this experiment. The
eighteen answerable held-out cases all include their expected sources; two
unknown cases stay empty. Literal passage checks pass eleven of eighteen, so
source inclusion does not establish the separate answer-quality target.

The full post-review Python suite passes 1,702 tests and five subtests. Kimi's
independent no-fallback review returned NEEDS-ATTENTION; the important findings
were reproduced and fixed in one regression-tested pass. No post-fix approving
verdict is claimed. The five-second freshness recheck remains bounded and fails
explicitly as inconclusive when it cannot complete. A pre-existing shadowed
duplicate test remains a documented minor.

Keep the repaired deterministic ranker as the candidate. BM25 and descriptions
remain offline experiments. Stronger passage selection, sufficiently sourced
answer review and actual SJM candidate acceptance remain open. No code was
committed, pushed or activated; phone service remains deferred. See
`plans/2026-10-24_k2b-recall-acceptance.md` for the exact evidence and limits.

## Task 2 Expose complete scope and stage-specific freshness

**Files:** Create `scripts/lib/knowledge_status.py`, `scripts/k2b-knowledge-status.py` and `tests/test_knowledge_status.py`. Modify `scripts/hooks/session-start.sh` only to consume a bounded recent status summary; extend `tests/session-start-hook.test.sh` if that integration is approved. Reuse `scripts/lib/eod_capture.py`, `scripts/lib/codex_sql_memory.py` and `scripts/lib/native_job_status.py` as owning readers.

**Interfaces:** `knowledge_status.summarize(observations: dict, *, now: str) -> dict` is a pure stage-summary function. The CLI's read-only `report` command gathers bounded existing-reader observations and emits `observed_at`, `source_window`, `scope`, `source_coverage`, `delivery`, `publication`, `integration`, `replication` and `limitations`. Absent observations remain unknown. A private disposable status cache is derived evidence, never a new canonical memory store.

- [x] Add failing tests for overlapping file/SQL inventories, excluded projects, a SQL-only pending turn, unavailable attachments, a changed SQL revision, incomplete/time-limited scans, stale observations and offline peers. Include automation-origin dispatch versus a manually written run receipt.
- [x] Preserve adapter-specific counts when exact cross-format deduplication is unavailable; never sum overlapping aggregates. Use existing source/session/turn identities for any union that claims exactness. This release keeps the union unmeasured.
- [x] Implement reporting of pending count and oldest source time, actual run outcome and dispatch evidence, publication watermark/hash, delivery backlog, and unmeasured integration. Do not equate active registration, zero outbox or idle Syncthing with source completeness. Current owners cannot verify dispatch or the latest publication-attempt outcome, so those remain explicitly unmeasured.
- [x] Display the current K2B/K2Bi and date boundaries, plus metadata-only exclusions. Add an explicit `sql_selection: operator_only` limitation while the native selector remains file-backed. Do not automatically widen scope or stage SQL turns.
- [x] Keep the optional startup summary and local cache absent in this release. No hook deadline, startup scan or cross-host behavior changes.
- [x] Run `python3 -m pytest tests/test_knowledge_status.py tests/test_eod_capture_status.py -q`: final installed suite **293 passed on each Mac**, including the subsequent portable test correction. No hooks changed.

**Checkpoint:** One report explains actual coverage and freshness without hiding unknowns. Keith can then decide whether automatic SQL selection or any additional project scope is wanted. Those decisions are separate from this reporting task.

## Task 3 Add supervised integration with durable source-to-note accounting

**Files:** Create `scripts/lib/knowledge_integration.py`, `scripts/knowledge-digest.py` and `tests/test_knowledge_integration.py`. Extend `scripts/vault-note-write.py` and `tests/test_vault_note_write.py` only if the existing source reference and receipt fields cannot bind the integration operation. Update `.agents/skills/k2b-vault-writer/SKILL.md` and `.agents/skills/k2b-compile/SKILL.md` for the approved supervised path.

**Interfaces:** `build_queue(snapshot: dict, receipts: list[dict], *, raw_sources: list[dict] | None = None) -> list[dict]` derives outstanding work. Queue rows bind the source kind (`memory_item` or `raw_capture`), stable source/item identity, version/source hashes, kind, speaker and proposed topic. `validate_proposal(proposal: dict, *, vault_root: Path, snapshot: dict) -> dict` validates an operator/model-authored proposal without applying it. `record_integration(proposal: dict, note_receipts: list[dict], *, state_root: Path, vault_root: Path, snapshot: dict) -> dict` verifies saved outcomes and writes an atomic, idempotent private Home receipt. Use `~/.local/state/k2b/digestion/`, outside the native job's exclusive state root.

Receipt schema 1 contains an operation ID derived from source identities and intended target content hashes, source references and hashes, disposition, timestamp, and target paths with before/after hashes and ordinary-writer receipt IDs. A retained leaf has an explicit reason and no target edit. Receipts reference the existing knowledge and evidence rather than copying another memory store.

- [ ] Add failing tests for changed sources, stale note hashes, invented item IDs/quotes, unsafe or missing link targets, symlinks, real conflict copies, partial multi-note saves, duplicate replay, a source intentionally retained as a leaf, and a proposal presented as a completed business event.
- [ ] Derive queue states `needs_review`, `integrated`, `retained_leaf` and `blocked` from receipts. Do not infer failure from an absent legacy compiled flag. Every explicit terminal disposition needs a source identity and reason.
- [ ] Build a proposal containing existing target notes, expected note hashes, exact source references, replacement-draft hashes, a current-summary change, dated history and the reason for each relationship. The current interactive Codex conversation supplies synthesis; the helper makes no provider call.
- [ ] Apply authorized proposals through the ordinary-note writer. Re-read source and note hashes immediately before saving. Record success only after all designated writer receipts and resulting note hashes verify. Preserve partial outcomes for completion or manual resolution.
- [ ] Keep raw sources immutable. Extend the derived queue to classify human captures separately from orchestrator technical records, preserving existing skipped/deferred dispositions. Completion receipts replace new edits to raw compilation flags.
- [ ] Run `python3 -m pytest tests/test_knowledge_integration.py tests/test_vault_note_write.py -q` and `bash tests/vault-note-authority.test.sh`. Make authority checks pass after skill changes.
- [ ] Re-read and supervise three real topic proposals: Amy onboarding, Kingdee/Pulse+ and K2B's current operating model. Use only supported new facts and decisions. Preserve unknown sending, signature, acceptance and completion states. Replay the same proposals and verify zero duplicate edits or knowledge.

**Checkpoint:** New assertions have an auditable route into subject knowledge, including intentional leaves and unresolved work. There is still no new unattended writer.

## Task 4 Repair misleading guidance and temporal interpretation

**Files:** Modify `.agents/skills/k2b-compile/SKILL.md`, `.agents/skills/k2b-vault-writer/SKILL.md`, `scripts/minimax-compile.sh`, `scripts/raw-needs-compile.py`, `tests/raw-needs-compile.test.sh` and `tests/test_eod_capture_status.py` as needed by the accepted contracts. Use the ordinary-note writer for `wiki/projects/project_k2b.md` and `wiki/projects/project_k2b-always-on.md`. The context target `wiki/context/context_k2b-note-lifecycle.md` is outside that helper's allowlist: edit it on Home with `apply_patch` only after preserving a private before-copy, checking its expected hash, confirming exclusive editing ownership and verifying healthy idle Syncthing on both Macs. Update project-index rows through the owning helper rather than hand-editing them.

**Interfaces:** Keep the existing raw helper CLI compatible. Add explicit classification/receipt-backed status rather than treating all missing flags as a unified backlog. Preserve provenance and history while making the current operational description truthful.

- [ ] Add tests distinguishing technical records, human captures, missing flags, receipt-backed integration, skipped/deferred items and unreadable metadata. Raw bytes must remain unchanged throughout queue evaluation and completion accounting.
- [ ] Replace the compile worker's double-hyphen prose instruction with the live punctuation rule. For substantive updates, include bounded current target-note passages as well as index discovery, then have Codex validate the proposal against current source and note hashes. Avoid sending an unbounded vault to the worker.
- [ ] Permit reviewed current-summary updates while preserving dated history, rather than using append-only text as a substitute for resolving current state. Cross-key correction candidates require review through the existing correction workflow; never bulk-merge keys by name similarity.
- [ ] Correct the prominent K2B project and retirement descriptions, and reconcile the lifecycle reference with current authority. Preserve historical facts as dated history. Do not contact or create work for the retired host.
- [ ] Correct the SJM test's expectation of an unredacted full temporary path. Assert a stable receipt identifier and unchanged state while preserving secret redaction. Include both short and token-like temporary paths in the regression.
- [ ] Run focused changed-module tests, `bash tests/raw-needs-compile.test.sh`, `scripts/verify-codex-authority.sh` and applicable note/index writer tests. Repeat the seven-module baseline on both hosts; preserve and review SJM's existing parser delta separately from this work.

**Checkpoint:** Current guidance, writer ownership and queue semantics agree. Historical evidence remains available and is interpreted with source time and speaker attribution.

## Task 5 Verify the complete path and decide bounded automation

**Files:** Create `plans/2026-10-24_k2b-knowledge-end-to-end-acceptance.md`. Modify the native prompt, registered job configuration or writer authority only in a separately approved activation change. Any proposed scheduled integration also requires reviewed code, tests and explicit authorization for the ordinary writer's new background use.

**Interfaces:** Acceptance records bind deployed Git heads/file hashes, actual native dispatches, source-event identities, extraction/reconciliation/publication receipts, note-integration receipts, snapshot hashes and normal recall results on both hosts. Prepared, reviewed, committed, activated and accepted remain distinct states.

- [ ] Preserve both dirty checkouts and SJM adaptations. Obtain the required independent verdict for each implementation diff. After explicit delivery authorization, transfer only reviewed Git changes and verify the actual installed host versions.
- [ ] Use genuine completed user conversations, one per host, to verify the full path into published memory and normal recall on both Macs. Bind the evidence to native scheduler dispatch. Include replay, offline SJM/Home, a pending local delivery, a changed decision, a missing attachment, a quota/hold and a simultaneous-writer attempt in isolated tests or explicitly approved probes.
- [ ] Verify one fresh desktop session on each host actually receives the intended startup context and retrieves a known subject. A manual hook invocation or ephemeral CLI test does not satisfy this desktop check.
- [ ] Measure incoming turn sizes, completed extraction per cycle, deferred work and oldest pending time. Propose aligning Home reconciliation at least 30 minutes after SJM starts, keeping the six-hour cadence and existing limits, so a normal SJM extraction can reach that Home cycle. Change registration only after approval and read it back.
- [ ] Observe seven consecutive days of the final deployed version. Target ordinary source-to-shared recall within seven hours when both hosts are online and capacity is sufficient. Any item exceeding 24 hours needs an explicit reason and operator action. Do not claim acceptance from an empty run or temporary operator catch-up.
- [ ] If supervised integration and quality checks pass, present a concrete background pilot for separate approval: Home only; at most five already reviewed records and three existing allowlisted subject notes per run; ten minutes; initially K2B and Kingdee/Pulse+ topics. Use the signed-in native product, the existing writer with newly approved narrow background authority, private integration receipts and no paid fallback. New notes, ambiguous corrections, unsupported assistant reports, raw captures, policy and shared hubs stay outside automatic application.
- [ ] Keep capture and integration failures independent: an integration hold must not prevent safe capture/drain/publication. Pilot workers may never change code, policy, credentials, schedules or their own bounds. Verify idempotence, lock contention, failure recovery and an actual nonempty integration cycle before expansion.

**Checkpoint:** The process has measured source-to-answer behavior, a visible integration backlog and independently reviewed delivery. Background integration remains off unless its exact pilot is separately authorized and accepted.

## Plan self-review

The five tasks cover the audit's seven findings and all six required outcomes. Task 1 fixes the demonstrated user-facing failures without increasing context limits. Task 2 makes the existing capture contract observable before changing it. Task 3 adds integration receipts and supervised subject maintenance without creating another canonical memory store. Task 4 reconciles raw immutability, current guidance and portable verification. Task 5 owns installation, real desktop and unattended acceptance, schedule changes and any later background authority.

Task 1 implementation and the recall comparison were authorized on 25 October.
Task 1 delivery and activation were subsequently authorized explicitly. At that
preparation checkpoint, the remaining tasks were planned work and were not
implicitly activated by the recall repair. Keith's 26 October continuation
instruction subsequently authorized their development and reviewed deployment.
Their acceptance still requires the specific evidence in Tasks 3 to 5; wider
source scope, automatic SQL selection, schedule changes and background
integration retain their separate activation gates.

## Passage and answer gate, 25 October 2026

Exact source passages now compete by distinct-term relevance, passage length
and section context. Bounded current summaries can accompany a discovered
subject across languages. Historical labels, original locators, date handling
and correction endpoints remain preserved. The leading subjects retain answer
and scope context within the existing global packet limits.

Five new passage and allocation regressions were observed failing before the
fixes. The full final-candidate Python suite passes 1,707 tests and five subtests.
The old duplicate raw-link test definition was AST-identical and is consolidated
to one executed definition, without changing its assertions.

The final comparison retains all ten diagnostic subjects and both controls.
On the original twenty comparison cases, deterministic repair passes sixteen
of eighteen literal passage checks; uniform question descriptions pass seventeen.
These are now regression cases, not unseen holdouts. BM25 still loses one
diagnostic subject and remains an offline experiment with the descriptions.

Twenty additional answer cases were frozen before passage tuning: eighteen
answerable and two unknown controls using generic vocabulary. Sixteen of the
eighteen answerable cases have the required passages in the first packet; the
other two use one explicitly recorded bounded follow-up each. All twenty final
drafts validate and render with the actual packet bindings. Kimi's independent
semantic review returned APPROVE, twenty PASS and zero FAIL, on the emitted
evidence, including dates, proposals, historical corrections and safe abstention.
This tests support for predeclared answers, not automatic free-form generation
or a general accuracy guarantee.

The source snapshot was reconstructed on SJM from its own vault bytes, all 479
hashes matching the fixed Home manifest. Both vaults reported idle with zero
needed files/bytes and errors, and five representative source hashes matched.
Final staged code approval, reviewed Git delivery and installed-host verification
are the remaining delivery gates at this record's preparation. Record their
outcome separately after execution. Broader stage reporting, supervised topic
integration and sustained end-to-end acceptance remain Tasks 2 to 5.
