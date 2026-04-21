---
title: Washing Machine Memory -- Ship 1 (v2, post-Codex rework)
date: 2026-04-21
status: designed
feature: feature_washing-machine-memory
ships-under: feature_washing-machine-memory
checkpoint-1: codex-plan-review-complete-rework-folded
checkpoint-2: at-pre-commit-per-commit
revision: v2
v1-review: Codex 2026-04-21 returned REWORK with 5 P1 + 6 P2 findings; all folded below
up: "[[plans/index]]"
---

# Plan: Washing Machine Memory Ship 1 v2 (post-Codex rework)

Implements Ship 1 of `wiki/concepts/feature_washing-machine-memory.md` -- the root-cause fix for the 2026-04-21 doctor-phone failure. Replaces the 2026-04-21 v1 plan after Codex plan review returned **REWORK**. See the "v1 → v2 delta" section below for what changed and why.

## Goal (updated)

The 2026-04-21 doctor-phone bug must be unrepeatable in **three modes**:

1. **Fresh image mode** (full pipeline): send Dr. Lo business card image → image OCR → classifier → shelf write → people page → retrieval works.
2. **Fresh text mode** (direct dictation): Keith types "Dr. Lo's phone is 2830 3709" → classifier → shelf write → retrieval works.
3. **Historical mode** (the actual April 21 incident state): Dr. Lo data already lives in `K2B-Vault/Daily/2025-04-11.md` + Mac Mini `memories/telegram-*.jsonl`. A migration script reads both, writes the semantic-shelf row + people page, and after Ship 1 a query resolves correctly **without manual backfill**.

All three modes pass the retrieval test:
- Ask "whats my doctor phone number" in a fresh session
- Memory-inject surfaces the row BEFORE agent generates
- Reply contains `2830 3709` with zero tool calls

Plus the synonym case: stored `Tel:` + query `phone` must resolve via embeddings (not classifier normalisation alone), and stored `Dr.` + query `doctor` must resolve.

If any mode fails, Ship 1 is not shipped.

## Architecture recap (unchanged from spec)

Four sub-components, in implementation order:

- **(b) Shelf writer** -- atomic append, flock-if-available + mkdir fallback, single-writer. Foundation.
- **(c) Embedding index + hybrid retriever** -- sentence-transformers on shelf rows. Cosine + BM25 + entity-link via reciprocal rank fusion.
- **(a) Normalization Gate** -- MiniMax-M2.7 text classifier + MiniMax-VL image-OCR stage + date resolver + entity extractor + pending-confirmation flow. Writes via (b).
- **(d) Memory inject** -- two stages: (d1) deterministic injector that replaces `buildMemoryContext`, and (d2) Research Agent loop (Plan → Search → Integrate → Reflect) on top. (d1) is the bug killer; (d2) is additional robustness.

Wire everything into `k2b-remote/src/bot.ts handleMessage` last. **Do NOT delete `buildMemoryContext` or `searchMemoriesFts` in Ship 1** -- just stop calling them. Dual-write to `memories_fts` continues for a 2-week bake period. Ship 4 removes the dead code + drops the table.

## v1 → v2 delta (why this rewrite exists)

Codex plan review 2026-04-21 returned REWORK. Every P1 is verified against the repo. Changes:

| Codex finding | v1 gap | v2 fix |
|---|---|---|
| P1.1 image ingest impossible | Plan classifier was text-only; photos come in as `[Photo attached at /path]` wrappers per `k2b-remote/src/media.ts:61-66` | New attachment-extraction stage in Commit 3. Image messages pass through MiniMax-VL (or Opus-vision fallback) before classifier. Classifier consumes normalized text, never the wrapper. |
| P1.2 pending-confirm orphaned | `washingMachineResume.ts` created but never wired into `bot.ts:368` text handler | Commit 5 adds explicit bot-level interceptor: before `normalizationGate`, check for active pending-confirmation for this chatId. If present AND reply matches resume rule (reply-to prompt OR sole pending), route through resume handler. Resume file now stores `chatId` + Telegram `messageId` of prompt. |
| P1.3 no historical migration | v1 MVP was fresh synthetic only; real Apr 21 state (JSONL + mis-dated Daily note) untouched | New Commit 0b: `scripts/washing-machine/migrate-historical.py` reads Mac Mini `memories/telegram-*.jsonl` + `K2B-Vault/Daily/2025-04-11.md`, extracts Dr. Lo record (and any other `fact | contact` candidates flagged by classifier review), writes to `wiki/context/shelves/semantic.md`. MVP test now runs against the real post-migration state, not fresh synthetic. |
| P1.4 rollback command wrong | `git revert <sha>..HEAD` excludes `<sha>` | Command corrected to `git revert --no-edit <first_ship_commit>^..HEAD`. Rollback protocol expanded with "restart k2b-remote via pm2 + re-run `syncMemoriesFromVault`" step. Dual-write means FTS table has full provenance so revert is actually clean for 2 weeks. |
| P1.5 corpus not durable | Plan referenced "this conversation" as corpus source | Commit 0 hard gate: `tests/washing-machine/calibration-corpus.md` + `tests/washing-machine/calibration-expected.json` must exist + be checked in before Commit 1. Corpus has 25 rows with human-readable text + expected classifier JSON per row. |
| P2.1 minimax-chat.sh missing | Plan preflight referenced non-existent script | All classifier + research-agent calls go through existing `scripts/minimax-json-job.sh`. No new chat wrapper. Preflight verifies `minimax-json-job.sh` reachable. |
| P2.2 Commit 5 zero tests | No bot-path coverage on the commit that touches production | New tests in Commit 5: `typecheck.test.sh` (tsc pass), `bot-boot.test.sh` (startup doesn't crash with mocked deps), `bot-path-text.test.sh`, `bot-path-photo.test.sh`, `bot-path-pending-reply.test.sh`. |
| P2.3 preflight doesn't match runtime | No WASHING_MACHINE_PYTHON pin, no Mini-FTS5 check with actual interpreter, flock assumption wrong on macOS | Preflight exports `WASHING_MACHINE_PYTHON=/path/to/venv/bin/python3` used by every new script. Preflight step 3 creates + queries an FTS5 virtual table via WASHING_MACHINE_PYTHON. Shelf writer uses flock-if-available + mkdir fallback (copied from `motivations-helper.sh`). |
| P2.4 reflection uses MCP that doesn't exist in TS | `mcp__obsidian__obsidian_simple_search` not available from k2b-remote/TS | Reflection step calls `scripts/vault-query.sh search "query"` via child_process. Integrate step now returns structured `{status: "found"\|"partial"\|"not_found", summary, source_ids[]}`. Reflection keys off `status`, not on substring match in free-form prose. |
| P2.5 classifier schema too loose + question rejection | No field confidence, no evidence spans, no question-rejection rule | Frozen schema v1.0: `{keep: bool, category: str, shelf: str, discard_reason: str?, entities: [{type, fields: {...}, field_confidence: {...}, evidence_spans: [...]}], timestamp_iso, date_confidence, needs_confirmation_reason: [str]}`. Prompt enforces `keep=false` for questions (ends with `?` OR starts with `what/who/where/when/how/why`), commands (starts with `/`), tool-echoes, and assistant turns. Prompt also enforces `never invent missing fields; use null + evidence_span pointing at the absence`. |
| P2.6 over-bundled | Single Commit 4 for both deterministic inject + Research Agent loop | Commit 4 split into 4a (deterministic injector -- bug killer, required for MVP) and 4b (Research Agent loop -- polish on top). If 4b hits trouble, hotfix ships 1-3+4a+5 without committing to a formal 1A/1B split upfront. |
| P3 embedding tests too weak | "5 rows → 5 × 384-dim" is near-tautology | Replaced with: row-hash idempotence test (same input → same index state), update-replaces-old test (row edited → old embedding gone + new stable), top-hit stability across rebuild test. |

### Codex v2 re-review (2026-04-21, tight pass) -- additional fixes folded

After v2 was reviewed a second time, Codex returned GO-WITH-FIXES confirming all 5 original P1s were closed but flagging 2 new blockers the rewrite introduced. Both folded below:

| Codex v2 finding | v2 gap | v2.1 fix |
|---|---|---|
| Preflight MiniMax smoke used a forbidden flag combo | `--prompt-stdin` + `--input -` is explicitly rejected at `scripts/minimax-json-job.sh:92-95` | Preflight step 4 now writes the trivial system prompt to `/tmp/preflight-prompt.txt` and calls `minimax-json-job.sh --prompt /tmp/preflight-prompt.txt --input -`. |
| Commit ordering broken: migration in 0b depended on classifier in Commit 3 | `migrate-historical.py` was placed in Commit 0b but needed the Commit 3 classifier to run. Chicken-and-egg. | Migration moved to a new **Commit 1b** (runs after the shelf writer in Commit 1 exists, before the classifier in Commit 3). Migration now uses a hardcoded Dr. Lo extractor for `Daily/2025-04-11.md` + known-template JSONL turns -- it's a one-time incident fix, not a general tool, so the classifier is not required. Commit 0b keeps only the calibration corpus + fixtures (pure file commits, no runtime deps). |

## Pre-flight (Commit 0 -- verification only, no feature code)

Before any feature code, prove the non-Node dependencies work on Mac Mini (production target).

File: `scripts/washing-machine/preflight.sh` (NEW, runs on Mac Mini).

Steps:
1. Create / confirm venv: `~/Projects/K2B/venv/washing-machine/`. If absent, `python3 -m venv <path> && source <path>/bin/activate && pip install sentence-transformers==3.0.0 numpy`. Export `WASHING_MACHINE_PYTHON=<venv>/bin/python3` to `~/.config/k2b/washing-machine.env` (sourced by every new script).
2. Verify sentence-transformers loads the model: `"$WASHING_MACHINE_PYTHON" -c "from sentence_transformers import SentenceTransformer; m=SentenceTransformer('all-MiniLM-L6-v2'); v=m.encode('test'); assert v.shape==(384,), v.shape; print('st ok')"`. On first run, this downloads the 22MB model to `~/.cache/torch/sentence_transformers/`.
3. Verify SQLite FTS5 with the pinned interpreter: `"$WASHING_MACHINE_PYTHON" -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); c.execute('INSERT INTO t VALUES(?)', ('doctor phone urology',)); r=c.execute('SELECT * FROM t WHERE t MATCH ?', ('doctor',)).fetchone(); assert r is not None; print('fts5 ok')"`.
4. Verify MiniMax chat: write trivial system prompt to `/tmp/preflight-prompt.txt` (`echo 'Return JSON {"ok": true}. No prose, no fences.' > /tmp/preflight-prompt.txt`), then `echo 'hello' | scripts/minimax-json-job.sh --prompt /tmp/preflight-prompt.txt --input - --job-name preflight-smoke --model MiniMax-M2.7`, confirm JSON in ≤ 10s. (Note: `--prompt-stdin` + `--input -` is explicitly rejected by the wrapper at `minimax-json-job.sh:92-95`; use a tempfile for the prompt.)
5. Verify MiniMax VLM endpoint. Endpoint **confirmed** via 2026-04-21 MiniMax Offload v2 handoff: `POST /v1/coding_plan/vlm` on `https://api.minimaxi.com` with headers `Authorization: Bearer ${MINIMAX_API_KEY}` + `MM-API-Source: K2B`. Body: `{prompt, image_url}` where `image_url` is a base64 data URL (`data:image/jpeg;base64,...`). Smoke test: base64-encode `tests/washing-machine/fixtures/images/test-128.png` (a 128×128 PNG with the word "TEST" rendered), POST to the endpoint with prompt `"Transcribe all visible text."`, expect response `content` field to contain "TEST" (case-insensitive). Parse `base_resp.status_code`; zero = success. On non-zero, preflight prints the decoded status code mapping (see Commit 3 error-handling table) and exits N.
6. Verify lock pattern: `flock --version 2>/dev/null || echo 'using mkdir fallback'`, then run both branches of the acquire_lock helper from `motivations-helper.sh` on a temp file.
7. Verify `scripts/vault-query.sh search "Dr Lo Hak Keung"` returns a non-empty result set (proves Obsidian REST is up + the Daily note is indexed).

Exit 0 = all 7 pass. Exit N = specific check failed, script prints which one and the fix.

**Checkpoint 0 gate**: `preflight.sh` exits 0 on Mac Mini. If not, fix dependency + re-run before Commit 0b.

## Commit 0b -- Calibration corpus + image fixtures (hard gate, no runtime deps)

Files:
- `tests/washing-machine/calibration-corpus.md` (NEW, ≥25 human-readable rows): each row is `### Row N\n<user message>\n\nExpected: <one-line summary>\n`. Rows cover: clean text captures, image captures (with OCR fixture), date-relative captures ("tomorrow", "next Friday"), date-contradiction cases (OCR 2025 + msg 2026), junk (tool output, questions, commands), preference captures, decision captures, multi-entity captures.
- `tests/washing-machine/calibration-expected.json` (NEW): machine-readable expected classifier output per row, keyed by row ID. Used by `classify.test.sh` in Commit 3.
- `tests/washing-machine/fixtures/images/` (NEW dir): PNG/JPEG/WebP fixtures for image-OCR test cases (VLM supports **JPEG/PNG/WebP only -- NOT GIF**): `dr-lo-card.png` (Dr. Lo card derived from Keith's actual image, watermark removed); `test-128.png` (128×128 dummy "TEST" image for preflight smoke); `invalid.gif` (intentional GIF to test error path); `corrupted.png` (for error-path coverage).

**Checkpoint 0b gate**: all three deliverables committed AND `calibration-expected.json` validates against a JSON Schema (also checked in as `tests/washing-machine/calibration-expected.schema.json`). No runtime deps: this is a pure "check in the test fixtures" commit, because the classifier and shelf writer don't exist yet -- they land in Commit 3 and Commit 1 respectively. The historical migration is deferred to Commit 1b (after the shelf writer exists).

## Commit 1 -- shelf writer + tests

Files:
- `scripts/washing-machine/shelf-writer.sh` (NEW): atomic append to `wiki/context/shelves/<shelf>.md`. Single writer per shelf. Uses `acquire_lock` from `motivations-helper.sh` (flock-if-available, mkdir fallback). Row format: `- <ISO-date> | <type> | <canonical-slug> | <attr:value> | <attr:value> | ...`. Increments frontmatter `row-count`. Atomic via mktemp + mv.
- `scripts/washing-machine/lib/shelf_rows.py` (NEW): pure parser/serialiser for the row format. Escapes `|` in values as `\|`. Consumed by writer + embedder + retriever.
- `tests/washing-machine/shelf-writer.test.sh` (NEW):
  - Empty shelf file → append 1 row, row-count=1, frontmatter preserved
  - Append to existing shelf → row-count increments, old rows intact
  - Two concurrent writers → serialised by lock, both rows present, no corruption
  - Row with pipe in value → escaped as `\|`, round-trips through parser
  - Rollback: temp-file write fails → no shelf mutation
  - Character encoding: UTF-8 Chinese text (e.g., `羅克強醫生`) round-trips cleanly

TDD: write tests, verify RED, implement, verify GREEN. Commit.

## Commit 1b -- historical migration (after shelf writer exists)

Files:
- `scripts/washing-machine/migrate-historical.py` (NEW): idempotent backfill. Reads `K2B-Vault/Daily/2025-04-11.md` (the mis-dated Dr. Lo Daily note -- has a known template with `Dr. Lo Hak Keung`, `Tel: 2830 3709`, `WhatsApp: 9861 9017`, address line) and Mac Mini `memories/telegram-*.jsonl` (all chat files, filter for the Apr 1 image-capture turns). Uses a **hardcoded extraction template** for the Dr. Lo record (no classifier dependency -- migration is a one-time fix for a known incident; we don't need a general classifier for it). Writes the record to `wiki/context/shelves/semantic.md` via the Commit 1 shelf writer. Idempotency: shelf row stores `source_hash = sha256(filepath + record_signature)`; re-run is no-op if hash present.
- `tests/washing-machine/migrate-historical.test.sh` (NEW):
  - Fixture Daily note + fixture JSONL → migration writes Dr. Lo row exactly once
  - Re-run on same state → zero-delta (idempotent)
  - Missing Daily note → clean skip (no crash, logs a warning)
  - Row already in shelf from Commit 3 ingest → migration skips (hash collision)
- Runs once on real vault at end of Commit 1b; result logged to `wiki/context/washing-machine-migration.log.md`.

**Checkpoint 1b gate**: Dr. Lo row exists in `wiki/context/shelves/semantic.md` after migration; re-run produces zero delta. Proceed to Commit 2.

Rationale for moving migration out of Commit 0b (fixed per Codex v2 review): the original plan had `migrate-historical.py` call "the Commit 3 classifier in dry-run mode" from Commit 0b, but the classifier doesn't exist until Commit 3. Chicken-and-egg. Solved by (a) deferring migration until shelf-writer exists, and (b) replacing the classifier-driven path with a hardcoded Dr. Lo extractor -- the migration is a one-time incident fix, not a general-purpose tool, so hardcoding is appropriate.

## Commit 2 -- embedder + hybrid retriever + tests

Files:
- `scripts/washing-machine/embed-index.py` (NEW): loads sentence-transformers `all-MiniLM-L6-v2` via `WASHING_MACHINE_PYTHON`, reads all shelf rows, builds or updates SQLite index at `wiki/context/shelves/index.db`. Schema: `rows(id INTEGER PRIMARY KEY, shelf TEXT, row_hash TEXT UNIQUE, row_text TEXT, embedding BLOB, entities_json TEXT, created_at INTEGER, updated_at INTEGER)` + `CREATE VIRTUAL TABLE rows_fts USING fts5(row_text, content='rows', content_rowid='id')` + triggers to sync FTS. Idempotent via row_hash.
- `scripts/washing-machine/retrieve.py` (NEW): CLI `retrieve.py "query" [--shelf semantic] [--k 10]`. Returns JSON array ranked by reciprocal rank fusion of (i) cosine on embedding, (ii) FTS5 BM25, (iii) entity-link (query entity candidates ∩ row entities). Weights default `α=0.5 β=0.3 γ=0.2`, overridable via env. Tuned during Commit 2; frozen in plan once green.
- `tests/washing-machine/embed-index.test.sh` (NEW):
  - Empty shelf → index has 0 rows, no crash
  - Add 5 rows → 5 index entries, each row_hash unique
  - Re-index same 5 rows → zero-delta (idempotent)
  - Edit one row's text → old index entry replaced (not duplicated)
  - Delete one row from shelf → index entry removed on next reindex
- `tests/washing-machine/retrieve.test.sh` (NEW):
  - **Doctor-phone gate** (binary): seed shelf with the Dr. Lo row exactly as Ship 1 writes it (`tel: 2830 3709`, role contains `Urology`). Query `doctor phone number` → top hit is Dr. Lo. Query `urology contact` → top hit Dr. Lo. Query `phone st pauls` → top hit Dr. Lo. **If any of these fail, Ship 1 is not viable -- escalate to Keith before continuing.**
  - Entity-link: row has entity `[[person_Dr-Lo-Hak-Keung]]`. Query containing that literal wikilink → row returned.
  - BM25 fallback: query `"2830 3709"` → Dr. Lo row top hit regardless of embedding cosine.
  - Zero-result: query for something not in shelf → empty array, not error.
  - Synonym stress: stored row uses `Tel:`, query uses `phone` → hit via embedding. Stored `Dr.`, query `doctor` → hit.

TDD: tests RED, implement, GREEN. Commit.

## Commit 3 -- Normalization Gate (text + image + dates + pending-confirm) + tests

Files:
- `scripts/washing-machine/extract-attachment.sh` (NEW): thin dispatcher. Takes `{type: "photo"|"document"|"text", path?, text?}`. Photo path delegates to `scripts/minimax-vlm.sh` (see below); document path uses `pdftotext` + text passthrough; text path pass-through. Returns `{normalized_text, attachment_type, source_path, ocr_confidence?, provider: "minimax-vlm"|"opus-vision"}`. Rejects GIF with a clear error message (VLM supports JPEG/PNG/WebP only).
- `scripts/minimax-vlm.sh` (NEW): reusable primitive wrapping the VLM REST endpoint, mirrors the `scripts/minimax-json-job.sh` pattern. CLI: `minimax-vlm.sh --image <path> --prompt <text> --job-name <label> [--fallback auto|never|always]`. Internals: base64-encode + MIME-type detect via `file -b --mime-type`, POST to `https://api.minimaxi.com/v1/coding_plan/vlm` with headers `Authorization: Bearer ${MINIMAX_API_KEY}`, `MM-API-Source: K2B`, `Content-Type: application/json`, body `{prompt, image_url: "data:${MIME};base64,${B64}"}`. Parse `base_resp.status_code`: `0`=ok, return `.content`; `1002`=rate limit → back off + retry; `1004`=auth/region mismatch → fatal, check `.com` vs `.io`; `1008`=quota exhausted → fall back to Opus; `2038`=real-name verification → fatal, escalate to Keith. On non-zero or HTTP error (when `--fallback auto`), invoke Opus vision via `claude -p --image "$PATH" "$PROMPT"`. Logs every invocation to `wiki/context/minimax-jobs.jsonl` via `log_job_invocation` (same pattern as `minimax-json-job.sh`). Rationale for factoring out: reusable by future Track B2/B3 work (screenshot analysis, receipt capture) without each consumer reimplementing base64+curl+error+log. See Design Decision D2 below.
- `scripts/washing-machine/normalize.py` (NEW): wraps existing `scripts/normalize-dates.py` (shipped 2026-04-19) for backward-relative dates. Adds forward relatives (`next Friday`, `tomorrow`) and OCR-contradiction detection (|ocr_date - msg_date| > 6 months triggers a `needs_confirmation_reason: ["date_mismatch"]`). Returns `{timestamp_iso, date_confidence, contradictions[]}`.
- `scripts/washing-machine/classify.sh` (NEW): calls `scripts/minimax-json-job.sh --prompt classifier-prompt.txt --input - --job-name washing-machine-classify`. Prompt v1.0 (see `prompts/washing-machine-classifier-v1.0.txt`, also new) enforces:
  - `keep=false` for questions (ends with `?` OR starts with `what/who/where/when/how/why/are/is/do/does/can/could/will/would`), commands (starts with `/`), assistant turns, tool-echoes (starts with `[`, matches known tool-output patterns)
  - Never invent missing fields; use null + evidence_span pointing at absence
  - Return structured schema v1.0 (see v1→v2 delta table above)
- `scripts/washing-machine/prompts/washing-machine-classifier-v1.0.txt` (NEW): the frozen prompt.
- `k2b-remote/src/washingMachine.ts` (NEW): `normalizationGate(msgText, msgTs, attachmentMeta?): Promise<GateResult>`. Flow: if attachment, call extract-attachment.sh first; pass normalized text through classify.sh; dispatch kept entities to shelf-writer.sh + people-page creator via `k2b-vault-writer`. On `needs_confirmation_reason`, write `wiki/context/shelves/.pending-confirmation/<uuid>.json` with `{chatId, promptMessageId, candidates, originalExtraction}` and post Telegram reply with numbered options.
- `k2b-remote/src/washingMachineResume.ts` (NEW): `resumePendingConfirmation(chatId, replyText, replyToMessageId): Promise<ResumeResult>`. Matches reply to the pending UUID by `replyToMessageId` first, or by sole-pending-for-chat otherwise. Finalises write, deletes pending file. On nonsense reply, keeps pending + returns "retry" status.
- `tests/washing-machine/classify.test.sh` (NEW): runs every row of `calibration-corpus.md` through classify.sh, asserts each matches `calibration-expected.json`. Individual row failures surfaced per ID. 25 rows × pass/fail.
- `tests/washing-machine/normalize.test.sh` (NEW):
  - `"next friday"` against 2026-04-01 (Wed) → 2026-04-03, confidence 0.9
  - `"next friday"` against 2026-04-21 (Tue) → 2026-04-24, confidence 0.9
  - OCR date 2025-04-11 + msg ts 2026-04-01 → contradiction flagged (|Δ| > 6mo)
  - Ambiguous `"earlier"` → confidence < 0.7, marked for confirm
- `tests/washing-machine/pending-confirm.test.sh` (NEW):
  - Write on contradiction → uuid JSON with `chatId`, `promptMessageId`, both candidate dates, original extraction
  - Reply `"1"` → finalises with first date, deletes pending, appends shelf row
  - Reply `"2026-04-01"` → finalises with typed date, deletes pending
  - Reply nonsense → keeps pending, emits "retry" response
  - Reply-to-prompt disambiguation: two pendings for same chat, reply_to_message_id resolves correctly
- `tests/washing-machine/extract-attachment.test.sh` (NEW):
  - `TEST` text image fixture → OCR contains "TEST"
  - **Dr. Lo card fixture (Chinese OCR accuracy gate)**: OCR must contain `2830 3709` AND `Urology` AND either `Lo` or `羅克強`. Extract 5 key fields (name, phone, whatsapp, specialty, hospital) and assert **≥ 4 of 5 match expected values** (80% field accuracy). If accuracy < 80%, test fails AND plan escalates: default provider switches to Opus-vision via config flag, Chinese OCR gap logged to `self_improve_requests.md`.
  - GIF file path → clean error "GIF not supported; convert to PNG/JPEG/WebP", no API call made
  - Missing file → clean error (no crash), no API call made
  - Non-image path → falls through to text passthrough
  - MiniMax returns non-zero `base_resp.status_code` → fallback to Opus vision, test passes with provider="opus-vision"
  - Opus vision also fails → test surfaces both errors, marks extraction failed, Gate returns `keep=false, discard_reason="ocr_failed"`
- `tests/washing-machine/reject-questions.test.sh` (NEW):
  - `"whats my doctor phone number"` → keep=false, discard_reason="question"
  - `"Dr. Lo phone is 2830 3709"` → keep=true, extracts contact
  - `"/tldr"` → keep=false, discard_reason="command"
  - `"[Tool: obsidian_simple_search] result: ..."` → keep=false, discard_reason="tool_echo"

**Checkpoint 3 gate**: all 25 calibration rows pass + all reject-question tests pass. Classifier prompt tuned (NOT the test) if any fail.

## Commit 4a -- Deterministic memory injector (bug killer)

Files:
- `k2b-remote/src/memoryInject.ts` (NEW): `injectMemoryFromShelves(userMsg): Promise<string>`. Deterministic flow:
  1. Extract query entity candidates via regex (phone patterns, wikilink tokens, proper-noun N-grams).
  2. Keyword-route: `phone|address|email|contact|doctor|appointment` → semantic shelf. `prefer|always|never|don't` → preference rows (Ship 3). Fall-through → semantic.
  3. Call `retrieve.py` with the routed shelf + user query + k=8.
  4. Return `[Memory context]\n<rows>\n\n` in the same slot `buildMemoryContext` uses.
- `tests/washing-machine/memory-inject.test.sh` (NEW):
  - Doctor-phone query → shelf rows returned include Dr. Lo
  - Query with no match → empty string (agent proceeds without memory context, not an error)
  - Query with multiple matches → top-k ordered by RRF score

## Commit 4b -- Research Agent loop (polish on top of 4a)

Files:
- `k2b-remote/src/researchAgent.ts` (NEW): `researchAgentInject(userMsg, msgTs): Promise<{factualSummary, sources, status}>`. Internally:
  1. Plan: `minimax-json-job.sh` with plan prompt → returns 1-3 search queries
  2. Search: each query runs `retrieve.py`, collects top-10 rows
  3. Integrate: `minimax-json-job.sh` synthesises ≤500-token structured `{status: "found"|"partial"|"not_found", summary, source_ids[]}`. **No free-form "I don't have it" strings.**
  4. Reflect: if `status == "not_found"` AND query pattern matches factual-retrieval shape, escalate to `scripts/vault-query.sh search "<expanded keywords>"`. Only return `not_found` if both passes empty.
- `tests/washing-machine/research-agent.test.sh` (NEW):
  - MVP doctor test: shelf seeded with Dr. Lo row, query `whats my doctor phone number`, summary contains `2830 3709`, status=found
  - Reflection test: shelf EMPTY, query `whats my doctor phone number`, first-pass status=not_found → escalation runs → either resolves via vault-query.sh OR returns not_found with both passes logged
  - Synonym test: shelf has `Tel:` not `phone`, query uses `phone`, summary still contains the number
  - Latency budget: full loop ≤ 2.0 s on typical Mac Mini load (advisory; record actual)

**Hotfix path**: if Commit 4b testing reveals latency > 3s or stability issues, Ship 1 defers 4b to Ship 1.5. In that case Commit 5 wires `injectMemoryFromShelves` (Commit 4a output) directly. Commit 4b becomes first task of Ship 1.5.

## Commit 5 -- wire bot.ts + smoke tests + retire old memory path

Files:
- `k2b-remote/src/bot.ts` (MODIFIED):
  - New text handler: before `handleMessage`, check for active pending-confirmation for this chatId (+ message's `reply_to_message_id`). If match, route through `resumePendingConfirmation` and return.
  - New photo handler: route through `extract-attachment.sh` → `normalizationGate` BEFORE calling `handleMessage` for the agent response. (The existing `buildPhotoMessage` wrapper remains for the agent call; the OCR is an independent capture path.)
  - In `handleMessage`: replace `const memCtx = await buildMemoryContext(chatId, userMessage)` with `const memCtx = await researchAgentInject(userMessage, Date.now())` (if Commit 4b green) OR `await injectMemoryFromShelves(userMessage)` (hotfix path).
- `k2b-remote/src/memory.ts` (MODIFIED, not DELETED): `buildMemoryContext` stays as dead code. `saveConversationTurn` keeps writing to BOTH vault JSONL AND SQLite `memories_fts` (dual-write for 2-week bake). The classifier writes to semantic shelf IN ADDITION.
- `k2b-remote/src/db.ts` (UNCHANGED): `searchMemoriesFts` stays, just uncalled. Virtual table keeps receiving writes.
- `k2b-remote/CLAUDE.md` (MODIFIED): Memory section describes both the live Washing Machine path AND the deprecated FTS path (with "retires in Ship 4" marker).
- `wiki/concepts/feature_washing-machine-memory.md` (MODIFIED): `status: in-progress` at /ship time (set by k2b-ship skill, not manually).
- New tests under `k2b-remote/tests/`:
  - `typecheck.test.sh`: `npm run typecheck` passes
  - `bot-boot.test.sh`: import + construct bot with mocked deps, no throw
  - `bot-path-text.test.sh`: text message → classifier → shelf write mocked, no crash
  - `bot-path-photo.test.sh`: photo message → extract-attachment mocked → classifier mocked, flow completes
  - `bot-path-pending-reply.test.sh`: pending file seeded, reply "1" → resume handler called, pending file deleted

**Checkpoint 5 gate**: typecheck green + all 5 smoke tests pass + k2b-remote builds successfully via `npm run build`.

## Commit 6 -- MVP gate + docs + /ship

Files:
- `tests/washing-machine/mvp-end-to-end.sh` (NEW): three modes, all must pass.
  - **Mode 1 (fresh image)**: stage synthetic business card fixture through the bot's photo handler, wait for shelf-write + people-page-write, query `whats my doctor phone number` in fresh session, assert reply contains `2830 3709` AND the agent ran with zero tool calls.
  - **Mode 2 (fresh text)**: same but starting from text message `"Dr. Lo's phone is 2830 3709, he's a urology specialist at St. Paul's"`.
  - **Mode 3 (historical)**: run `migrate-historical.py` on actual vault + Mini JSONL state, assert Dr. Lo row appears in shelf once, then query → reply contains `2830 3709`.
- `DEVLOG.md` (APPEND): session summary per k2b-ship.
- `wiki/log.md` (APPEND): vault activity per k2b-ship.
- `wiki/concepts/feature_washing-machine-memory.md` (UPDATED): add post-ship provenance note.

**Checkpoint 6 gate (the ship gate)**: `mvp-end-to-end.sh` exits 0 on Mac Mini across all three modes. If any mode fails, Ship 1 is not shipped -- revert to pre-Ship-1 state, debug, retry.

## Files changed (final tally for v2)

New:
- `scripts/washing-machine/preflight.sh`
- `scripts/washing-machine/shelf-writer.sh`
- `scripts/washing-machine/lib/shelf_rows.py`
- `scripts/washing-machine/embed-index.py`
- `scripts/washing-machine/retrieve.py`
- `scripts/washing-machine/extract-attachment.sh`
- `scripts/minimax-vlm.sh` (reusable VLM primitive; see Commit 3 body + D2)
- `scripts/washing-machine/normalize.py`
- `scripts/washing-machine/classify.sh`
- `scripts/washing-machine/migrate-historical.py`
- `scripts/washing-machine/prompts/washing-machine-classifier-v1.0.txt`
- `k2b-remote/src/washingMachine.ts`
- `k2b-remote/src/washingMachineResume.ts`
- `k2b-remote/src/memoryInject.ts`
- `k2b-remote/src/researchAgent.ts`
- `wiki/context/shelves/semantic.md` (built by `migrate-historical.py` at Commit 1b; receives incremental rows from Commit 3 classifier thereafter)
- `wiki/context/shelves/index.db` (built by embed-index.py at Commit 2)
- `wiki/context/shelves/.pending-confirmation/` (empty dir, .gitkeep)
- `wiki/context/washing-machine-migration.log.md` (append-only)
- `tests/washing-machine/calibration-corpus.md`
- `tests/washing-machine/calibration-expected.json`
- `tests/washing-machine/fixtures/images/*`
- 12 test scripts under `tests/washing-machine/`
- 5 test scripts under `k2b-remote/tests/` (Commit 5 smoke tests)

Modified:
- `k2b-remote/src/bot.ts` (pending-intercept + photo OCR route + inject wire)
- `k2b-remote/src/memory.ts` (dual-write kept; nothing deleted)
- `k2b-remote/src/db.ts` (unchanged, just uncalled)
- `k2b-remote/CLAUDE.md` (Memory section rewrite)
- `DEVLOG.md`, `wiki/log.md`
- `wiki/concepts/feature_washing-machine-memory.md` (status move via /ship)
- `wiki/concepts/index.md` (lane move via /ship)

Deleted: **NOTHING in Ship 1.** `buildMemoryContext` + `searchMemoriesFts` removal deferred to Ship 4 after 2-week bake.

## Test list (binary pass/fail, runnable)

Ship 1 gates:
1. preflight: all 7 steps pass on Mac Mini
2. migrate-historical: Dr. Lo row produced on real state, idempotent
3. shelf-writer: 6 unit tests (including UTF-8 Chinese)
4. embed-index: 5 tests (including row-hash idempotence + update-replaces-old)
5. retrieve: **3 doctor-phone variants top-hit** (the bug killer)
6. retrieve: entity-link + BM25 + zero-result + synonym stress
7. classify: **25 calibration rows** all pass
8. reject-questions: `what's my doctor phone number` NOT stored as fact
9. extract-attachment: Dr. Lo card fixture OCR works
10. normalize: forward relative dates + contradiction detection
11. pending-confirm: write + reply-1 + typed-date + nonsense + reply-to disambiguation
12. memory-inject: deterministic shelf routing
13. research-agent: structured-status integrate + reflection escalation
14. typecheck + bot-boot + 3 bot-path smoke tests
15. **mvp-end-to-end 3 modes: fresh-image, fresh-text, historical** -- the ship gate

Binary gates for shipping: #5 (doctor-phone retrieval), #7 (calibration), #8 (question rejection), #15 (MVP 3-mode).

## Checkpoints

- **Checkpoint 1 -- Codex plan review**: DONE 2026-04-21. v1 REWORK verdict → this v2 plan folds all P1s + P2s. Before any Commit 0 work, v2 must be re-reviewed by Codex. Accept verdicts: GO, GO-WITH-FIXES. REWORK → revise v3.
- **Checkpoint per commit**: before each commit, Codex pre-commit review on staged diff. Tier 2 for commits 0/0b/1/2, Tier 3 for commits 3/4a/4b/5 (cross-file, memory semantics), Tier 1 for commit 6 (docs + end-to-end script).
- **Checkpoint 6 -- MVP 3-mode gate**: `mvp-end-to-end.sh` green on Mac Mini. No /ship without green.

## Rollback protocol (v2)

Correct syntax: `git revert --no-edit <first_ship_commit>^..HEAD`. Replace `<first_ship_commit>` with the SHA of the first Ship 1 commit (preflight commit or Commit 0b if preflight has no staged changes).

After revert:
1. `pm2 restart k2b-remote` on Mac Mini.
2. k2b-remote auto-runs `syncMemoriesFromVault` on boot, which reads vault JSONL back into `memories_fts`. Because Ship 1 kept dual-write active, no data loss.
3. Shelf files + `index.db` + `.pending-confirmation/` survive on disk but are unused by reverted code (harmless orphans). Ship 2 removes if revert becomes permanent.
4. `memories_fts` virtual table has full provenance for the Ship 1 window; retrieval works against pre-Ship-1 path immediately.

Bake period: 2 weeks of dual-write. Ship 4 removes `buildMemoryContext` + `searchMemoriesFts` + drops `memories_fts` table only after bake is clean.

## Out-of-scope for Ship 1 (enforced)

- MacBook UserPromptSubmit/Stop hooks -- Ship 2
- Other 4 shelves (keyword, recent, insights, team) beyond stubs -- Ship 3
- Consolidation + decay + row-count warnings -- Ship 4
- FIFO message queue from `feature_pipeline-hardening` -- Ship 3
- Exfil guard, cost footer, PIN lock -- separate security feature
- Multi-agent Hive Mind, voice War Room -- not this feature
- Observer integration (insights-shelf population) -- Ship 3
- Deletion of `buildMemoryContext` + `searchMemoriesFts` -- Ship 4 after bake

If a sub-task is not required for MVP 3-mode test, it does NOT land in Ship 1.

## Effort estimate (revised from v1's 30h)

- Commit 0 preflight: 1h (includes pip install + vision provider smoke)
- Commit 0b corpus + fixtures: 2h (calibration rows + JSON schema + image fixtures only; migration moved to 1b)
- Commit 1 shelf writer: 3h
- Commit 1b historical migration: 2h (hardcoded Dr. Lo extractor + idempotency test, after shelf writer exists)
- Commit 2 embedder + retriever: 6h (the synonym-resilience tests are the critical path)
- Commit 3 Normalization Gate: 8h (grew from 6h due to attachment extraction + stricter schema + pending-confirm bot wiring)
- Commit 4a deterministic inject: 2h
- Commit 4b Research Agent loop: 4h
- Commit 5 bot wire + smoke tests + CLAUDE.md update: 3h (grew from 2h due to smoke tests)
- Commit 6 MVP + docs: 2h
- Codex reviews: ~3h total across the run

Total: **~36 hours = 4-6 working days**.

## Open items to resolve before Commit 0

1. **Vision provider for image OCR.** **RESOLVED 2026-04-21** via MiniMax Offload v2 handoff (`plans/2026-04-21_minimax-offload-v2-consolidated.md`). MiniMax-VL primary via confirmed REST endpoint `POST https://api.minimaxi.com/v1/coding_plan/vlm` with `Bearer ${MINIMAX_API_KEY}` + `MM-API-Source: K2B` headers, body `{prompt, image_url: base64-data-URL}`. Quota 150/5hrs ≈ 720/day. Supports JPEG/PNG/WebP only (not GIF). Opus vision (`claude -p --image`) is the automatic fallback triggered by non-zero `base_resp.status_code` OR Chinese OCR accuracy below 80% on the Dr. Lo fixture in Commit 3 testing. Preflight step 5 now does a direct REST smoke (no probe-and-fallback logic).
2. Reciprocal rank fusion weights (α/β/γ). Start `0.5/0.3/0.2`; tune during Commit 2 using doctor-phone test. Freeze in plan once green.
3. Where to store `washing-machine.env` (WASHING_MACHINE_PYTHON + MINIMAX_VISION_MODEL). Proposed: `~/.config/k2b/washing-machine.env`, sourced by every new script.
4. Calibration corpus extraction. Before Commit 0 closes, extract 25 rows from this session's discussion + past K2B conversations. Block Commit 1 until both corpus files exist.

## Design decisions log

Why the plan ended up shaped the way it is. Preserved here so future sessions can reconstruct the reasoning without the originating conversations.

### D1 -- Plan revision strategy (2026-04-21)

**Decision**: no full rewrite after the 2026-04-21 MiniMax Offload v2 handoff. Fold 4 concrete refinements (endpoint confirmation, error-code mapping, GIF correction, Chinese OCR accuracy gate) into v2.2 and proceed.

**Why**: the offload handoff and the Washing Machine plan are **parallel timelines** that intersect at one file (`scripts/minimax-vlm.sh`). The handoff explicitly says "you do NOT need to rescope" and lists architecture / corpus / 3-mode MVP / sentence-transformers / Groq Whisper as unchanged. Ship 1 savings: ~2-4 hours of Commit 0 + Commit 3 implementation work, plus prevents 2-3 specific bugs (GIF format, missing error-code handling, endpoint probing dead-end). Not an "unlock" -- a sharpening.

**Rejected alternative**: another full rewrite round. After v1 REWORK → v2 GO-WITH-FIXES → v2.1 + offload fold = v2.2, each round found less. Further paper-thinking without shipping code has diminishing returns; remaining risks (Chinese OCR quality, VLM latency on Mini, classifier prompt tuning) only surface when we call the API.

**Timeline structure** (for future sessions to understand scope):

```
Washing Machine Ship 1 (this plan) ──── Ship 2 ──── Ship 3 ──── Ship 4
                    │
                    └── produces: scripts/minimax-vlm.sh (D2 below)
                                         │
                                         ├── consumed by: Track B2 (screenshot analysis, future)
                                         ├── consumed by: Track B3 (receipt capture, future)
                                         └── consumed by: any future VLM work
```

Tracks B2/B3/C/D/TTS from the offload handoff each need their own ship plan + Codex review cycle. None are Ship 1 scope.

### D2 -- VLM call path: reusable primitive over inline curl or CLI or MCP (2026-04-21)

**Decision**: add `scripts/minimax-vlm.sh` in Commit 3 as a reusable primitive (Path 1.5). `extract-attachment.sh` calls it via one line. Mirrors `scripts/minimax-json-job.sh` pattern.

**Alternatives considered**:

| Path | Description | Lines | Dependencies | Observability | Rejected because |
|---|---|---|---|---|---|
| 1 | Inline curl + base64 + error parse in `extract-attachment.sh` | ~20 | curl, jq (already there) | Direct via `log_job_invocation` | Not reusable by future Track B work -- each consumer reimplements base64+curl+error pattern |
| 2 | `mmx vision $IMG --prompt "$P"` (official CLI) | ~5 | +Node npm global | Manual wrap | Adds Node dep on Mini + MacBook; flag-drift risk; loses fence-strip + retry-on-5xx patterns |
| 3 | MiniMax-Coding-Plan-MCP server via `@modelcontextprotocol/sdk` in k2b-remote | ~30-50 | +MCP SDK | Manual wrap | MCP is for LLM tool discovery ("agent picks which tool"); our OCR trigger is deterministic (photo arrived → OCR), so MCP abstraction adds weight with zero benefit |
| **1.5 (chosen)** | **New `scripts/minimax-vlm.sh` + thin caller in `extract-attachment.sh`** | **~5 in caller, ~20 in primitive (amortized)** | **curl, jq** | **Built-in via `log_job_invocation`** | **-- selected --** |

**When alternatives DO make sense**:
- **mmx-cli**: ad-hoc terminal use (`mmx quota` to check remaining at session start, one-off image inspections). Install as optional; not a pipeline dependency.
- **MCP**: future feature where an LLM picks dynamically between tools (e.g., "analyze this image OR search for it OR both"). Not today.

### D3 -- Vision provider: MiniMax-VL primary, Opus vision auto-fallback

**Decision**: `scripts/minimax-vlm.sh` tries MiniMax-VL first. Falls back to `claude -p --image` automatically on non-zero `base_resp.status_code` OR on Chinese OCR accuracy < 80% on the Dr. Lo fixture in Commit 3 testing.

**Why primary = MiniMax-VL**: quota on Keith's plan is 150 calls/5hrs ≈ 720/day; realistic volume is < 10 cards/day. Same quota bucket as other MiniMax calls (no separate billing). Chinese-native (MiniMax is a Chinese lab), though OCR quality is unbenchmarked publicly -- hence the 80% gate.

**Why fallback = Opus vision**: works today (Opus is multimodal), handles Chinese perfectly. Adds ~$0.30-1/day at Keith's realistic volume. Acceptable as safety net.

**Why not Tesseract**: fails on bilingual Chinese-English content (Dr. Lo card has both). Ruled out.

### D4 -- Rollback strategy: dual-write, not delete

**Decision**: Ship 1 keeps `memories_fts` writes + `buildMemoryContext` / `searchMemoriesFts` functions alive (as dead code). `bot.ts` stops CALLING them; the new shelf path is wired in parallel. After 2-week bake period, Ship 4 deletes.

**Why**: `git revert` during the bake window returns to a state where the old FTS path still has full provenance of everything captured during Ship 1 (via dual-write). Rollback is truly clean. No data loss, no stranded shelf-only facts.

**Rejected alternative** (original spec): delete `buildMemoryContext` in the same commit as `researchAgentInject` lands. Codex P1.4 exposed that this makes rollback non-clean -- shelf data doesn't round-trip into the old FTS table.

### D5 -- Branch strategy: main, not feature branch

**Decision**: all 10 commits land on `main`, commit-by-commit, each gated by Codex pre-commit review. No feature branch.

**Why**: matches K2B's established commit-on-main + `/ship` pattern (recent 10 commits all on main). The per-commit Codex gate catches issues that a feature-branch squash-merge might hide. Rollback is `git revert <first-ship-commit>^..HEAD` on main -- simpler than branch merge-back.

**Rejected alternative**: `ship/washing-machine-1` feature branch with fast-forward merge at end. Cleaner for a single blast-radius rollback but duplicates what `git revert` already does. Keith's call 2026-04-21.

### D6 -- Ship 1 bundled (4 sub-components), not split into 1A/1B

**Decision**: keep Ship 1 as a single ship that contains Normalization Gate + typed shelves + hybrid retrieval + deterministic injector + Research Agent loop. Inside Ship 1, split the old Commit 4 into 4a (deterministic injector -- bug killer) + 4b (Research Agent loop -- polish). If 4b hits trouble during testing, hotfix by shipping 1-3+4a+5 only, defer 4b to Ship 1.5.

**Why bundled**: the spec was explicit ("ALL four because cutting any one leaves the doctor bug half-fixed"), and Keith's stated rationale was avoiding the April 16 mistake of shipping cheap MVPs without the hard part.

**Why split 4a/4b**: Codex v1 P2.6 noted deterministic retrieval alone can kill the doctor-phone bug. Splitting Commit 4 gives us a hotfix path without committing to a formal 1A/1B split upfront.

## Linked notes

- **MiniMax Offload v2 handoff**: `plans/2026-04-21_minimax-offload-v2-consolidated.md` -- confirms VLM REST endpoint, error codes, host pinning, and recommends direct `curl` wrapper over `mmx vision`. Resolves this plan's Open Item 1.
- **Subscription snapshot**: `K2B-Vault/wiki/reference/2026-04-21_minimaxi-subscription-plan.md` -- quotas + endpoints. Updated 2026-04-21 to correct GIF support claim (VLM is JPEG/PNG/WebP only).
- Spec: `/Users/keithmbpm2/Projects/K2B-Vault/wiki/concepts/feature_washing-machine-memory.md`
- v1 plan review: Codex 2026-04-21 (full report preserved in session `cf2a476d-02f9-4019-afb5-8b7cbbcc3ec4` + this plan's v1→v2 delta)
- Calibration corpus target: `tests/washing-machine/calibration-corpus.md` (NEW, Commit 0b)
- Person page: `/Users/keithmbpm2/Projects/K2B-Vault/wiki/people/person_Dr-Lo-Hak-Keung.md` (manually backfilled 2026-04-21; Ship 1 must reproduce this automatically via Mode 1 or Mode 3)
- Historical sources: Mac Mini `memories/telegram-<chatId>.jsonl` + `K2B-Vault/Daily/2025-04-11.md` (migration targets)
- NotebookLM: notebook `880c1d36-33ea-437a-bc19-47b401403198`, conversation `cf2a476d-02f9-4019-afb5-8b7cbbcc3ec4`
- Parent research: `raw/research/2026-04-16_research_claudeclaw-v2-k2b-self-improvement.md`, `raw/research/2026-04-18_research_memory-architecture-patterns.md`, `raw/research/2026-04-19_research_memory-architecture-plan.md`
- Visual: `Assets/2026-04-16_claudeclaw-v2-visual-guide.pdf` pages 11 + 13
