---
title: MiniMax Offload v2 -- Consolidated Handoff
date: 2026-04-21
type: handoff
status: draft
feature: project_minimax-offload
supersedes-scope-of: "[[project_minimax-offload]]"
up: "[[plans/index]]"
companion: "[[plans/2026-04-21_washing-machine-ship-1]]"
---

# MiniMax Offload v2 -- Consolidated Handoff

Refreshed scope for the MiniMax offload project, written to be self-contained. Based on: (a) the 2026-04-21 `minimaxi.com` subscription dashboard capture, (b) a full scan of MiniMax's public API docs, (c) source-level reading of `MiniMax-AI/MiniMax-Coding-Plan-MCP` and `MiniMax-AI/cli`, (d) seven months of K2B's own offload history.

The primary reader is the in-flight Washing Machine Memory Ship 1 session. **Section "Handoff to Washing Machine Ship 1" at the end is the part that directly resolves their Open Item 1 (vision provider).** Everything before that is context.

## TL;DR

1. **Keith is on `Plus-极速版 月度套餐`** (minimaxi.com). Flat monthly fee. Quotas are generous: ~7200 text calls/day, ~720 vision calls/day untapped, ~720 web searches/day untapped, 9000 TTS chars/day near-zero use, 100 images/day in moderate use.
2. **The scope of "offload" broadens from text-only to four tracks**: text (existing), vision (new), web search (new), TTS (new). The original "cap at 2 offloads" rule is dropped now that real quota is known.
3. **Two new tools to adopt**: `mmx-cli` (official MiniMax CLI, `npm install -g mmx-cli`) for generation work; direct REST for K2B bash scripts that already have observability hooks.
4. **Washing Machine Memory Ship 1 unblocks today.** The VLM REST endpoint is confirmed: `POST /v1/coding_plan/vlm` with `{prompt, image_url}`. Their Open Item 1 is resolved.
5. **Retire `claude-minimaxi` for general use.** Keep the skill-level bake-ins (`k2b-compile batch mode` etc.) that already work. The Explore agent (Haiku 4.5) is the better default for bulk-read + classify, and `mmx-cli` covers the generation use cases without a whole Claude Code session.
6. **Dead ends confirmed** (don't wait for these): no embeddings API, no ASR/STT, no fine-tuning, no batch API, no hosted RAG/vector store, no callbacks/webhooks. Files API is TTS-source-only, not a RAG store.

## What is VLM

**Vision Language Model.** A model that takes an image plus a text instruction and returns text. Same concept as ChatGPT's image upload or Claude's vision: you show it a picture, ask a question, get a written answer.

Use cases that matter to K2B:
- **Business card to structured contact** (Washing Machine Ship 1, Dr. Lo)
- **Screenshot to text** (Telegram screenshot of a dashboard / error / chart -> extract content)
- **Whiteboard photo to notes** (scribbled diagram -> typed entities + relationships)
- **Receipt capture** (photo -> `{vendor, items, total, date}` JSON)
- **Scanned PDF to text** (page-by-page, when the PDF has no text layer)
- **Slide deck extraction** (photos of a pitched deck -> per-slide summary)

What MiniMax's VLM is NOT:
- Not grounded with bounding boxes. Just text output.
- Not multi-image. One image per call.
- Not GIF/SVG/PSD. JPEG + PNG + WebP only.

Quality: MiniMax publishes zero OCR benchmarks, but the product is Chinese-native and business cards are an easy OCR task. Fine for Washing Machine's use case; Benchmark Chinese-language accuracy in the Washing Machine preflight before trusting it on anything harder.

## Subscription snapshot (frozen 2026-04-21)

Source: minimaxi.com dashboard screenshot. Re-capture every 2-3 months.

| Service | Model | Quota | Window | Daily | Current K2B use |
|---|---|---|---|---|---|
| Text generation | MiniMax-M2.7-highspeed | 1500 | 5h | ~7200 | **Heavy** (compile, observer, review, research-extract, weave, claude-minimaxi) |
| Text-to-Speech HD | speech-2.8-hd / turbo | 9000 chars | 1d | 9000 | **Near-zero** |
| Image gen | image-01 | 100 | 1d | 100 | Moderate (k2b-linkedin, k2b-media-generator) |
| **Vision LM** | **coding-plan-vlm** | **150** | **5h** | **~720** | **ZERO -- untapped** |
| **Web search** | **coding-plan-search** | **150** | **5h** | **~720** | **ZERO -- untapped** |
| Music | music-2.6 | 100 | 1d | 100 | Zero |
| Music cover | music-cover | 100 | 1d | 100 | Zero |
| Lyrics | lyrics_generation | 100 | 1d | 100 | Zero |
| OpenClaw seats | -- | 1-2 | -- | -- | Zero (not a fit) |

Plan does NOT include: video generation (Hailuo is Max-tier), ASR/transcription (doesn't exist anywhere on MiniMax), embeddings (deprecated).

## Current K2B footprint

Working MiniMax integrations today:

| Layer | Model | Script / wrapper | Purpose |
|---|---|---|---|
| k2b-compile extraction | M2.7 | `scripts/minimax-compile.sh` | Wiki synthesis |
| k2b-lint deep | M2.7 | `scripts/minimax-lint-deep.sh` | Contradiction detection |
| Cross-link weaver | M2.7 | `scripts/minimax-weave.sh` | Bootstrap links |
| Observer loop (pm2 on Mac Mini) | M2.7 | `scripts/observer-loop.sh` | Background preference harvest |
| Adversarial reviewer (Codex fallback) | M2.7 | `scripts/minimax-review.sh` | Pre-commit review when Codex quota out |
| Research extract | M2.7 | `scripts/minimax-research-extract.sh` | Long-source digest for `/research` |
| Generic JSON job wrapper | M2.7 | `scripts/minimax-json-job.sh` | Factored boilerplate for all of the above |
| Image generation | image-01 | `scripts/minimax-image.sh` | k2b-linkedin images |
| TTS | speech-2.8 | `scripts/minimax-speech.sh` | Rarely invoked via `/media speech` |
| Claude Code backed by MiniMax | M2.7 | `scripts/claude-minimaxi.sh` | Interactive session delegation (UNDERUSED -- see below) |

Not yet integrated anywhere: **VLM, web search, music, voice clone, voice design, WebSocket TTS, `mmx-cli`, quota monitoring.**

## Confirmed API contracts

All calls use `Authorization: Bearer ${MINIMAX_API_KEY}` plus (new convention) `MM-API-Source: K2B` header so MiniMax's telemetry distinguishes us from the official MCP client. Host: `https://api.minimaxi.com` (Keith's China key) or `https://api.minimax.io` (international). Keith's key is `.com`-minted; the `.io` endpoint returns 401.

### Vision (coding-plan-vlm)

```
POST /v1/coding_plan/vlm
Content-Type: application/json
Authorization: Bearer $MINIMAX_API_KEY
MM-API-Source: K2B

{
  "prompt": "Extract contact info as JSON: name, phone, email, specialty, clinic.",
  "image_url": "data:image/jpeg;base64,<base64-data>"
}
```

Response:

```json
{
  "content": "{\"name\": \"Dr. Lo Hak Keung\", \"phone\": \"2830 3709\", ...}",
  "base_resp": { "status_code": 0, "status_msg": "success" }
}
```

Notes:
- Formats: **JPEG, PNG, WebP ONLY**. NOT GIF, PDF, SVG, PSD. (Keith's vault note at line 67 says GIF is supported -- that is wrong and should be corrected.)
- One image per call. No multi-image batching.
- URL inputs: the MCP client fetches and base64-encodes locally. The REST API itself only accepts base64 data URLs.
- No bounding boxes. Free-text answer. If you want structured output, ask for JSON in the prompt and parse it.

### Web search (coding-plan-search)

```
POST /v1/coding_plan/search
{
  "q": "your search query"
}
```

Response:

```json
{
  "organic": [
    { "title": "...", "link": "...", "snippet": "...", "date": "..." }
  ],
  "related_searches": [ { "query": "..." } ],
  "base_resp": { "status_code": 0, "status_msg": "success" }
}
```

Notes:
- Returns a **Google-style SERP**, not an LLM answer. No streaming, no summarization.
- Only one parameter: `q`. No region, language, freshness, result-count, or site-restriction knobs.
- Not a drop-in replacement for Brave Search API or Perplexity's `sonar-online`. Fine for "quick first-pass lookup" (Level 0 of `/research`), not for situations needing regional Macau/HK ranking or date filtering.

### Error codes (all endpoints)

Parse `base_resp.status_code`, not just HTTP status:

| Code | Meaning | Fix |
|---|---|---|
| 0 | success | -- |
| 1002 | rate limit | back off, retry in window |
| 1004 | auth / region mismatch | check key vs host (`.com` vs `.io`) |
| 1008 | out of balance | quota exhausted |
| 2038 | real-name verification required | China-specific, check account in minimaxi.com portal |

## New tool: `mmx-cli`

Official MiniMax CLI, https://github.com/MiniMax-AI/cli. Dual-region aware. Wraps every endpoint we care about under one unified binary.

Install:

```bash
npm install -g mmx-cli
mmx auth login --api-key "$MINIMAX_API_KEY"
mmx config set --key region --value cn   # Keith's key is china-minted
mmx quota                                 # verify plan + remaining quota
```

Commands relevant to K2B:

| `mmx` command | What it does | Current K2B equivalent |
|---|---|---|
| `mmx text chat --model MiniMax-M2.7-highspeed --system <prompt> --messages-file -` | Structured text with system + user messages, JSON out | `scripts/minimax-json-job.sh` (keep -- has our observability hooks) |
| `mmx vision path-or-url --prompt "..."` | VLM in one line | doesn't exist -- **this unblocks Washing Machine** |
| `mmx search "query" --output json` | Web search | doesn't exist |
| `mmx speech synthesize --text "..." --voice English_magnetic_voiced_man --out file.mp3` | TTS to file | `scripts/minimax-speech.sh` (used rarely) |
| `mmx music generate --prompt "..." --lyrics "..."` | Music | doesn't exist |
| `mmx quota` | **Live quota visibility** | doesn't exist -- **net-new capability** |

Adoption recommendation: use `mmx` for NEW capabilities (vision, search, quota monitoring, music). Keep battle-tested bash scripts (`minimax-json-job.sh`, `minimax-review.sh`, compile, observer, weave) as-is because they have:
- Observability logging to `wiki/context/minimax-jobs.jsonl`
- Fence stripping (MiniMax sometimes wraps JSON in ```json fences)
- Strict `jq -e` validation (the CLI probably doesn't)
- Retry on 502/503/504/529 (added 2026-04-21 in `minimax-review.sh`)

Migrate opportunistically, not wholesale.

## What we're NOT pursuing

Confirmed via docs scan. Stop waiting for these to come back:

- **Embeddings API** -- was legacy, now gone. K2B's Washing Machine uses local `sentence-transformers all-MiniLM-L6-v2` and that is the right call.
- **Audio transcription / ASR** -- MiniMax has no speech-to-text product at any tier. Keep Groq Whisper in `scripts/yt-transcript.sh`.
- **Fine-tuning** -- deprecated.
- **Batch API** -- deprecated. Use parallel M2.7 calls within the rolling 5-hour quota.
- **Hosted RAG / vector store / Assistants API** -- all deprecated.
- **Translation** -- no dedicated endpoint; use M2.7 text with a translation prompt.
- **Webhooks / callbacks** -- not supported on any async job. Poll `query` endpoints.
- **Files API as RAG store** -- file uploads are scoped to `voice_clone | prompt_audio | t2a_async_input` only. Not a reusable context store.
- **MiniMax-M2-her** (roleplay character model) -- strictly worse for K2B's extraction work (2k output cap, no prompt caching).
- **Video Agent** -- template-based virality videos (face-swap, pet outdoors). Off-brand for Keith's senior-exec content angle.

## Claude-minimaxi: retire for general use

Keith's feedback (2026-04-21): "I couldn't get it to search in the vault then I'm not using it much."

Diagnosis:
- The wrapper does NOT pass `--add-dir ~/Projects/K2B-Vault` by default, so MiniMax literally cannot see the vault.
- It does NOT load the Obsidian MCP config, so even if it could see the vault files, it wouldn't know to call `obsidian_simple_search`.
- Compounded by MiniMax-M2.7 being weaker than Opus at tool-selection (knowing when to invoke which MCP tool).

Decision: **retire `claude-minimaxi` as a general-purpose tool, keep the skill-level bake-ins.**

Reasons:
1. The Explore agent (Haiku 4.5, built into Claude Code) is the better default for "cheap vault read + classify". Faster startup, better tool-use instincts, no API-key plumbing.
2. `mmx-cli` covers the generation-side use cases without needing a whole Claude Code session.
3. The bake-ins that work already work. `k2b-compile batch mode` dispatches to `claude-minimaxi` at 3+ sources and that path is tested. Skills decide at their own thresholds; removing the general-purpose wrapper does not break those.

If a future use case genuinely needs MiniMax-backed interactive agent work with vault + MCP access, the fix is a 2-line edit to inject `--add-dir "$K2B_VAULT" --mcp-config ~/.claude/.mcp.json`. Not worth doing pre-emptively.

Action: update `wiki/context/context_claude-minimaxi-routing.md` "Skills That Opt In" section to be the canonical entry point, and mark general-use dispatch as deprecated. Keep the script on disk so existing skill bake-ins continue to work.

## Proposed rescope: four tracks

The project evolves from "sequential 6-phase Opus->M2.7 offload of text skills" to a four-track plan. Tracks can run in parallel; ships within a track stay sequential with phase gates.

### Track A -- Text offload (existing, cap removed)

Original Phases 1-6. Status unchanged from `project_minimax-offload.md` except:
- Phase 2a (generic wrapper) **already done** as side-effect of Washing Machine prep. `scripts/minimax-json-job.sh` exists and is battle-tested. Mark complete.
- The "hard stop after 2 offloads" rule is retired now that real quotas are known to be generous.
- Phase 2b (`/observe` data prep) remains next in queue, ready to start after the 2026-04-24 measurement window gate.
- Phases 3-6 (insight-extractor, meeting processor, daily clustering, YouTube) stay on the original guardrails (provenance, durable-memory gates, fail-closed for meetings/daily).

### Track B -- Vision (new)

**Primary consumer: Washing Machine Memory Ship 1** (already designed, in-flight).

Ships within track:
1. **Ship B1 -- VLM primitive**: `scripts/minimax-vlm.sh` wrapping the REST endpoint, OR adopt `mmx vision` directly from the Washing Machine commit. **Delivered as part of Washing Machine Ship 1 Commit 3.**
2. **Ship B2 -- Screenshot analysis** (future): Telegram users send dashboard / error / UI screenshots, K2B extracts content. Natural extension of B1.
3. **Ship B3 -- Whiteboard / receipt capture** (future): structured extraction from less-structured photos.

Green-light metric (from Washing Machine plan): >= 90% extraction accuracy on the Dr. Lo business card fixture (name + phone + specialty + clinic).

### Track C -- Web search (new)

**Primary consumer: `/research` Level 0** (quick first-pass lookups that don't warrant NotebookLM or WebFetch).

Ships within track:
1. **Ship C1 -- Search primitive**: either `scripts/minimax-search.sh` or `mmx search` in a bash one-liner. Deliberately thin wrapper; the endpoint has only one parameter.
2. **Ship C2 -- `/research` Level 0 integration**: before invoking WebFetch or NotebookLM, `/research` tries `mmx search` for 3-5 snippet results. If snippets answer the question, done. If not, fall through.
3. **Ship C3 -- Background fact-checking** (future): observer or compile does a quick check on factual claims before promoting raw -> wiki. Low-priority, advisory-only.

Limitations to document in the skill body:
- No region / language / freshness / site-restriction knobs.
- Returns snippets, not full page content. Does not replace WebFetch for specific-URL analysis.
- Fall through to Brave Search API (paid, controllable) or Tavily for queries where coding-plan-search is insufficient.

Green-light metric: on a 20-query evaluation set, coding-plan-search returns a correct-enough first-pass answer on at least 60% without needing fall-through.

### Track D -- Text-to-Speech (new)

**Primary consumer: end-of-day audio daily digest.**

Ships within track:
1. **Ship D1 -- Audio daily digest**: end-of-day, K2B generates a 2-3 min voice summary of today's daily note. Auto-saves to `K2B-Vault/Assets/audio/`. Sent to Keith's Telegram as a voice note.
2. **Ship D2 -- LinkedIn audio companion**: each published LinkedIn post auto-generates a 30-60s voice version (voice + punchy hook). Dropped in Telegram for Keith's approval before attaching to LinkedIn.
3. **Ship D3 (request)** -- Telegram voice-reply mode over WebSocket TTS (`/v1/t2a_v2_ws`). Shaves 1-3s perceived latency on long summaries by streaming audio chunks. Park as an entry in `self_improve_requests.md` until the voice-reply feature gets prioritized; do NOT build until then.

Green-light metric: daily digest lands in Telegram by 22:00 HKT for 5 consecutive days, Keith listens to at least 3 of 5 (measured via "heard" receipts on Telegram).

## Handoff to Washing Machine Ship 1

**This section is written for the in-flight Washing Machine Memory session.** It resolves your Open Item 1 from `plans/2026-04-21_washing-machine-ship-1.md` ("Vision provider for image OCR").

### Decision: MiniMax VLM via direct REST, with `mmx vision` as an optional simpler path

Your v2 plan already decided on MiniMax-VL primary with Opus-vision fallback. That decision stands. The endpoint probe scheduled for Commit 0 preflight step 5 is no longer needed -- **the REST path is confirmed and documented above**. Save the preflight a call.

### Confirmed integration facts

1. **Endpoint**: `POST /v1/coding_plan/vlm` on `https://api.minimaxi.com` (Keith's china-minted key). International host `api.minimax.io` returns 401 on this key; pin `api.minimaxi.com`.
2. **Payload**: `{prompt: string, image_url: data-URL-string}`. One image per call.
3. **Response**: `{content: string, base_resp: {...}}`. Free text; parse with your own regex / JSON coercion if you prompt for JSON.
4. **Auth**: `Authorization: Bearer ${MINIMAX_API_KEY}` plus optional `MM-API-Source: K2B` for telemetry separation.
5. **Formats**: JPEG, PNG, WebP. **Correct the calibration corpus plan**: your `tests/washing-machine/fixtures/images/` cannot contain GIF test cases. The vault note at `wiki/reference/2026-04-21_minimaxi-subscription-plan.md:67` also says GIF is supported and is wrong -- worth patching as part of your commit.
6. **Quota**: 150 calls per 5-hour window = ~720/day. At realistic <10 cards/day volume, quota is a non-issue.
7. **Error handling**: check `base_resp.status_code`. `1002` = rate limit, `1008` = quota exhausted, `1004` = region mismatch, `2038` = real-name verification needed (China). Do not rely on HTTP status alone.
8. **Chinese OCR quality**: unbenchmarked publicly. The Dr. Lo card is English + a Chinese name token. Test explicitly in Commit 3's `extract-attachment.test.sh`. If accuracy is below 90% on the fixture, escalate to Opus-vision fallback immediately rather than tuning the prompt.

### Two paths to actually call it (pick one)

**Path 1 (recommended for Washing Machine): direct `curl` wrapper in `scripts/washing-machine/extract-attachment.sh`.**

Keeps washing-machine ownership of the call, logs to `wiki/context/minimax-jobs.jsonl` via `log_job_invocation` for provenance, no new Node runtime dependency.

```bash
#!/usr/bin/env bash
# Inside scripts/washing-machine/extract-attachment.sh
IMAGE_PATH="$1"
PROMPT="$2"

BASE64=$(base64 < "$IMAGE_PATH" | tr -d '\n')
MIME=$(file -b --mime-type "$IMAGE_PATH")
DATA_URL="data:${MIME};base64,${BASE64}"

RESPONSE=$(curl -sS "https://api.minimaxi.com/v1/coding_plan/vlm" \
  -H "Authorization: Bearer ${MINIMAX_API_KEY}" \
  -H "MM-API-Source: K2B" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg p "$PROMPT" --arg img "$DATA_URL" '{prompt: $p, image_url: $img}')")

STATUS=$(echo "$RESPONSE" | jq -r '.base_resp.status_code')
if [[ "$STATUS" != "0" ]]; then
  echo "VLM error: $(echo "$RESPONSE" | jq -r '.base_resp.status_msg')" >&2
  exit 1
fi

echo "$RESPONSE" | jq -r '.content'
```

**Path 2 (simpler, adds a Node dependency): `mmx vision`.**

```bash
mmx vision "$IMAGE_PATH" --prompt "Extract contact info as JSON..."
```

One line. But the CLI doesn't currently log to `minimax-jobs.jsonl`, so you lose the provenance contract the washing-machine plan requires. If you go this route, wrap `mmx vision` in a shell script that also calls `log_job_invocation`.

**Recommendation**: Path 1 for Washing Machine. The curl wrapper is 20 lines, uses existing `scripts/minimax-common.sh` helpers, and keeps the observability + retry patterns you already have. Path 2 is for ad-hoc terminal use.

### Fallback plan (unchanged)

If MiniMax VLM fails the Commit 3 fixture tests:
- Error from endpoint -> fall back to Opus vision via `claude -p --image "$PATH" "$PROMPT"`. Keep the call site behind a flag so Keith can force one side for debugging.
- Accuracy below 90% on the fixture -> switch default to Opus vision, file the Chinese-OCR gap as a `/request`, revisit after MiniMax publishes a VL benchmark.

### What NOT to change in your v2 plan

- Your architecture (4 sub-components: shelf writer, embedder, normalization gate, memory inject) is correct.
- Your calibration corpus requirement stands.
- Your 3-mode MVP gate (fresh image / fresh text / historical) stands.
- The `sentence-transformers` local embedding choice is correct (MiniMax has no embeddings API).
- The Groq Whisper dependency in `yt-transcript.sh` stays (MiniMax has no ASR).

You do NOT need to rescope the Washing Machine plan because of this doc. You only need:
1. Resolve Open Item 1 in your own plan by pasting the confirmed endpoint details above.
2. Drop Commit 0 preflight step 5's "first try direct REST ... fall back to Opus vision if REST is unavailable" branch -- just do direct REST straight up.
3. Fix the GIF line in the calibration corpus and in the subscription reference note.

## Parked / `/request` items

Items worth logging but not building now. Use `/request` to capture them into `self_improve_requests.md`:

1. **WebSocket TTS voice-reply mode** (Ship D3, on the TTS track). Endpoint `/v1/t2a_v2_ws`. Streams audio chunks at same $60/M chars turbo pricing. Shaves 1-3s latency on long Telegram replies. Build when K2B adds a voice-reply mode.
2. **Voice clone cost probe**. Voice cloning is not in the subscription free quota -- pricing is pay-as-you-go and not publicly listed. Run one clone creation to measure actual cost before committing to a "Keith-voice daily digest" ship.
3. **`mmx quota` daily check in session start**. Surface remaining VLM + search + text quota at session-start if any is below 20%. Low priority, nice-to-have.
4. **Retire claude-minimaxi for general use** (code change): update `wiki/context/context_claude-minimaxi-routing.md` to mark general dispatch deprecated, keep skill-level bake-ins.
5. **Correct `wiki/reference/2026-04-21_minimaxi-subscription-plan.md` line 67**: GIF is NOT supported for VLM. Update to "JPEG, PNG, WebP only."

## Linked notes

- **Project**: [[../K2B-Vault/wiki/projects/project_minimax-offload]] -- history, phase-gate structure, Codex review findings. This handoff augments it, does not replace it.
- **Subscription snapshot**: [[../K2B-Vault/wiki/reference/2026-04-21_minimaxi-subscription-plan.md]] -- frozen dashboard capture. Correct the GIF line before citing.
- **Routing**: [[../K2B-Vault/wiki/context/context_claude-minimaxi-routing.md]] -- Opus vs Explore vs claude-minimaxi decision tree. Add "deprecated for general use" note.
- **Feature spec**: [[../K2B-Vault/wiki/concepts/feature_washing-machine-memory]] -- memory v2 design doc.
- **Washing Machine plan v2**: [[2026-04-21_washing-machine-ship-1]] -- implementation plan that this handoff directly informs.
- **claude-minimaxi setup walkthrough**: [[../K2B-Vault/raw/tldrs/2026-04-19_tldr-claude-minimaxi-setup]] -- gotchas encountered during first setup; still accurate for the skill-level bake-ins that remain.
- **mmx-cli**: https://github.com/MiniMax-AI/cli -- official CLI repo.
- **MiniMax Coding Plan MCP**: https://github.com/MiniMax-AI/MiniMax-Coding-Plan-MCP -- the MCP server whose source code exposed the VLM + search endpoints.

## Changelog

- **2026-04-21** -- Created by Opus session after: (a) capture of minimaxi.com subscription dashboard, (b) full catalog scan via two research-agent passes, (c) source-level read of `mmx-cli` + `MiniMax-Coding-Plan-MCP`, (d) Keith's guidance to retire claude-minimaxi for general use and skip M2.5 / content-safety flags. Intended as a handoff to the in-flight Washing Machine Ship 1 session.
