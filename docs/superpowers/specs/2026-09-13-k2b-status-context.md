# K2B trustworthy status and current context

Keith requested a plan and Kimi handoff to ship the improvements identified in the September 13 diagnosis. This is the first bounded delivery: truthful status and current context, not a new automation architecture.

## Required outcome

When Keith asks what is outstanding or researches a topic, K2B must distinguish actual running jobs, successful processing, unresolved work and historical context. Existing capture continues unchanged.

## Verified baseline

- Home and SJM have `1ab106fda20ab5dc447daa0839b3d9571b5b7609` installed. Both named native memory jobs are ACTIVE, Sol-low, hourly, limit one turn. The latest observed receipts at 2026-09-13 13:30/13:34 UTC completed without holds; Home accepted both contributions and published.
- `.agents/skills/k2b-plate/scripts/plate.sh:514,523` matches `## R-`/`## E-`; actual logs use `###`. The request log contains 23 entries marked open, including old and unrelated items. Matching headings alone must not make closed requests appear open.
- `scripts/lib/eod_capture.py:4159` hardcodes runtime unverified and activation disabled. These are not evidence of native registration state.
- `scripts/motivations-helper.sh read` emits cached Building and Emerging Interests without freshness checks. Research invokes it by default. Building was last synced July 27; inferred observer context dates April 17.
- Weave and observer have no active jobs across Home/SJM/Mini. Mini K2B processes and legacy daily jobs were retired with local recovery evidence. Do not revisit the Mini.

## Product contract

1. Plate understands both two- and three-level entry headings, selects explicitly open requests, respects the latest explicit status in an entry, reports recent dated errors, and does not turn malformed or missing status into an open request. Handle duplicate/addendum IDs by latest explicit dated status rather than counting the same request twice. Preserve all historical log files unchanged.
2. Native registration is observed read-only from the exact host-role job TOML. Show ACTIVE, PAUSED, missing or unknown separately from last completed receipt and from hold/error status. Never claim successful processing from ACTIVE alone. Missing, malformed or wrong-host registration is unknown/unavailable, not falsely disabled. Preserve all existing source, outbox, reconciliation and publication data and APIs except additive status fields and corrected runtime labels.
3. Motivation reads remain provider-free and write-free. Derive Building from the current concepts index using the existing parser, never from cached Building prose. Label the source as the index, not live deployment truth. Keep Keith's explicit questions, including undated ones, unchanged. Omit inferred Emerging Interests whose observer timestamp is missing, invalid, future-dated or more than 30 days old. The existing disabled toggle emits no content. Missing inputs must not fall back to stale Building or invent interests.
4. After verified delivery, the Home manager updates only the migration feature, concepts index and active-motivations note to reflect current activation/retirement and the new read-time contract. Preserve update history and the distinction between shipped repair and seven-day acceptance. No new policy or preference promotion.

## Fixed boundaries

- Python 3.12 and Bash; no new dependencies, paid API, provider fallback, credentials, daemon, schedule, database migration or dashboard redesign.
- Home is the shared writer. SJM remains source-only/read-only for the vault. Keep the current capture cadence and limits unchanged.
- No weave/observer activation, bulk compilation, local model work, K2Bi, Talent Radar, Service Motion or Mini work.
- Historical raw captures, request/error logs, credentials, publication authority and source receipts are never rewritten.
- Git delivery requires Kimi-built code to receive independent Codex approval. Activation uses the existing K2B shipping/sync adapters.

## Later delivery: capture efficiency (not authorized for implementation by this packet)

Prepare a separate bounded plan after measuring pending count, oldest eligible turn and actual per-run usage. It should offer daily multi-turn batches, a cheap empty-queue gate, per-host run limits and an explicit daily usage budget, while retaining provenance, corrections, idempotence and offline recovery. Numeric budget/cadence acceptance must be agreed before activation. Do not silently convert current jobs to daily one-turn processing.

Other follow-ups remain optional: current read-only lint and request triage; verification of existing backups and a restore test; paid video/music checks. Old roadmap items must be triaged, not treated as automatic implementation orders.
