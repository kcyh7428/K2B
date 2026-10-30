# Native capture and automation

Mandatory reading before any capture work, receipt work, native automatic-memory
job, schedule decision or publication of captured sources. This runbook carries the
authority of `AGENTS.md`; if it is missing or unreadable, stop the affected operation.

## Capture architecture

The existing dated exception below also overrides the scheduler skill's SJM
entrypoint refusal in Host and Registration Rule 2 and Step 2 validation, solely
for the approved SJM native automatic-memory job and its isolated synthetic
activation probes. It does not permit another unattended SJM workload or relax
any source, date, budget, provider, hold, receipt or activation check.

The reference to isolated synthetic activation probes preserves historical approval;
it authorizes no new invocation or recurring schedule. Before using that exception,
require the retained approval for the exact probe command, host, synthetic inputs,
allowed private state paths, receipt and cleanup bounds. Missing or unverifiable
evidence stops the probe. Never infer permission for an arbitrary Codex invocation,
Vault or shared-hub write from the word "probe".

Natural Codex conversations are discovered from each Mac's immutable `~/.codex/sessions/**/*.jsonl` source files. Capture must not depend on a manual hook call. Every reconciled source has provenance and a receipt binding source path, source hash, transcript hash, extraction hash, and reconciliation time.

Keith's 2026-09-13 activation approval extends the earlier discovery-only policy narrowly: the named native Codex jobs `K2B automatic memory Home` and `K2B automatic memory SJM` may follow `scripts/prompts/automatic-memory-native.md` under the signed-in Codex product, after their per-host probe and activation checks pass. This exception supersedes discover-only and deterministic-script-only wording in the scheduler skill for these two jobs and isolated synthetic activation probes only. It does not enable other background routines. Discovery remains provider-free. Interactive capture remains available. Kimi Code membership does not authorize scripted batch extraction. Legacy `job-a`/`catch-up` provider paths must not be scheduled or used for bulk backlog processing. No paid API route or fallback is enabled.

Keith's 2026-09-25 memory-repair approval replaces the initial one-turn limit for these same two jobs with a bounded batch every six hours: at most 12 completed turns, 240000 cumulative input-view bytes and 20 minutes per host per run, whichever is reached first. Select one turn at a time; a normal batch/time budget boundary leaves remaining work pending for the next run and is not an extraction failure. Record each boundary-stop turn as a deferred ID, and let a prepared-but-unaccounted turn make the receipt ambiguous rather than failed: a receipt must never mark unattempted work as a failed extraction, and a canonical receipt is written as durable failed evidence, never hand-authored, when command JSON is unreadable or the evidence is malformed. These runs use only the signed-in native Codex product, never DeepSeek or another paid API, with no paid fallback; the 2026-09-25 DeepSeek handoff was a bounded engineering lane for the repair itself, not a change to scheduled extraction. The inclusive completion-date window remains 2026-09-13 through the current HKT date. Do not widen the window or process earlier backlog automatically. Follow the native prompt's per-turn input-size and receipt limits; place extraction on the prompt's durable operator hold and report quota exhaustion, persistent failure or repeated selection without progress. Home's independently eligible drain/publication may continue while extraction is held. A six-hour trigger is not a six-hour freshness guarantee: report actual pending-turn counts and oldest unprocessed source time. Native runs may never modify live instructions, code, permissions, credentials, their schedule or publication authority themselves.

- Home is the only semantic reconciler and shared-hub writer.
- SJM may discover and stage its local sources but must not write shared hubs. Its pending machine-local operations live under `~/.local/state/k2b/`.
- Missed days are handled by explicit inclusive catch-up ranges, not yesterday-only scheduling.
- Provider, malformed-output, interruption, and offline-counterpart failures remain retryable and must not produce success receipts.
- Replaying an unchanged source must not duplicate knowledge.
- Capture status distinguishes discovered, extracted, reconciled, waiting, failed, skipped, disabled, and writer role.
- Reconciliation receipts are durable provenance and are retained with their matching extraction; they are not temporary scheduler receipts.

Dashboard intake is manual staging unless a verified worker is active. A present manifest alone is `staged`, never `processing`.

SJM corporate systems must not be connected to K2B through Outlook sync, email forwarding, or unapproved push integrations. Documents can enter through explicit Codex text, PDF extraction, approved OCR, or retained transcription paths.

Do not enable old observer, weave, YouTube, LinkedIn, alert, or dashboard-worker schedules merely because code remains. Disabled routines must be reported as disabled.
