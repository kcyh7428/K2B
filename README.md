# K2B — Keith's Second Brain

K2B is Keith's personal knowledge and workflow system. Codex is the primary interface and sole live instruction authority. Kimi K2.7 Code is the retained cloud worker for bounded extraction, analysis, and independent review of OpenAI-built changes.

Stage 1 uses two Macs:

- **Home MacBook Pro** — owns shared-vault writes and reconciliation.
- **SJM MacBook Pro** — captures local Codex sessions and reads the synchronized vault; shared write operations are queued for the home writer.

Git carries project code. Syncthing replicates `K2B-Vault` directly between the two Macs. Syncthing is transport, not a lock or backup. Credentials remain per-machine.

## Daily experience

Most K2B work is intentionally conversational and manual:

1. Talk with Codex on either Mac.
2. Local discovery finds eligible Codex session files directly and leaves the source immutable.
3. During a requested session, Codex processes queued conversations with retained Kimi assistance within its interactive entitlement.
4. The home writer commits accepted knowledge to the vault.
5. Syncthing makes the result available on the other Mac for later recall.

Missed days are handled by range-based discovery rather than a yesterday-only schedule. Replaying an already reconciled source is idempotent. `python3 scripts/eod-capture.py status --since 2026-07-26` reports source/receipt evidence without claiming that another host is healthy.

Keith selected **automatic discovery, interactive capture with Codex and Kimi**. The unattended-safe entrypoint is `scripts/codex-discovery-job.sh home` (use `sjm-source-only` on SJM). It runs local discovery only, writes private queue/status plus an observable receipt under `~/.local/state/k2b/`, and never calls a provider or edits the vault. Schedule registration is an activation step; see the dated acceptance record for actual installation state.

Scheduler receipts retain the latest 90 runs. Vault reconciliation receipts are durable provenance and remain paired with their extraction artifact; a missing or changed extraction makes status retryable rather than silently trusting an orphan receipt.

During a requested Codex session, review a selected source and produce a JSON object with an explicit `items` list plus its `raw_source_sha256` and `transcript_sha256`. On home, `stage-reviewed --date YYYY-MM-DD --session /absolute/source.jsonl --reviewed-json /private/reviewed.json` validates provenance, source scope, and evidence before staging; `job-b --date YYYY-MM-DD` performs locked reconciliation after healthy vault sync. Missing, malformed, or stale reviewed output must not be treated as an empty successful extraction. The extractor schema and evidence rules remain in `scripts/prompts/eod-capture-extract.md`.

For an SJM source, run `export-source --source-host sjm --session /absolute/source.jsonl --output ~/.local/state/k2b/capture-bundles/<id>.json` on SJM during the requested session. It accepts only an exact K2B/K2Bi Codex source, removes tool/system/developer content, redacts credential patterns, binds the immutable raw-source and dialogue hashes, and writes mode 0600. Transfer only that selected bundle over the existing authorized SSH connection; never synchronize the complete Codex session tree. Home stages it with `stage-reviewed --source-bundle /private/bundle.json`. Bundles and reviewed JSON stay machine-local, outside Git and Syncthing.

Kimi Code membership is for interactive use, not scripted batch processing. Do not schedule legacy `job-a`, `catch-up`, or the compatibility cron wrapper. No paid API fallback is enabled. Backlog sources stay waiting until actually reviewed and reconciled.

Optional Obsidian MCP reads the owning machine's private Local REST API plugin credential through `scripts/run-obsidian-mcp.sh`. That plugin's `data.json` must be mode 0600 and excluded from Syncthing on each Mac. No API key belongs in Git or in a copied configuration.

The dashboard intake route is optional manual staging. A present manifest is reported as `staged`, never `processing`, unless a verified worker exists. Stage 1 does not require a continuously running dashboard.

## Knowledge layout

The vault remains plain Markdown:

```text
K2B-Vault/
  raw/                 immutable source captures
  wiki/                compiled, source-backed knowledge
  review/              items awaiting Keith's judgment
  System/memory/       active rules and memory ledgers
```

Important shared hubs have one writer on the home Mac. Use their owning helper rather than editing concurrently:

- `wiki/log.md` → `scripts/wiki-log-append.sh`
- compile indexes → `scripts/compile-index-update.py`
- observer candidates/defers and policy or usage ledgers → their owning scripts

SJM-side pending operations belong in `~/.local/state/k2b/`, outside the synchronized vault. Wait for healthy, up-to-date Syncthing state before applying a queued shared-vault change.

## Providers

- **Codex** — commander, implementation, and normal desktop interface.
- **Kimi K2.7 Code** — extraction/analysis worker and independent reviewer. The integration uses the Kimi Code membership service at `https://api.kimi.com/coding`; it must not silently switch to a metered Moonshot Open Platform endpoint.
- **NotebookLM/Gemini** — optional specialist for explicitly requested multi-source synthesis.
- **GPTsAPI/Groq/Higgsfield** — retained media, OCR, transcription, and generation routes where their specific skills apply.

Provider credentials are stored per machine, outside Git. The K2B scripts read the required variables from the environment or `~/.k2b-env`. Never copy credentials between Macs.

## Active and disabled lanes

Active Stage 1 behavior is deliberately small:

- automatic Codex conversation discovery; interactive Codex/Kimi capture and receipt-backed home reconciliation
- manual vault reading, writing, compile, review, and research when Keith requests them
- Git code synchronization and two-Mac Syncthing vault replication
- truthful local status and authority checks

The following routines are disabled unless Keith explicitly starts a separate task for them: daily capture, meeting processing, observer, weave, YouTube batch capture, LinkedIn publishing, reminders, alerts, and broad background automation. Disabled means no scheduled worker should be enabled and no dashboard should describe one as healthy.

Tracked Claude compatibility material and the K2B Telegram bot are removed by Stage 1. The obsolete Mini router-watchdog jobs, legacy observer writers, and the old local sentence-transformer preflight/index/retrieval lane are also removed; local AI returns only as a separately designed Stage 2. Personal Claude/Telegram application data and historical captures are not part of this cleanup. Historical records are evidence, never live instructions.

The former Mac Mini topology is outside Stage 1. Do not contact, inspect, configure, deploy to, or create a pending obligation for it. Local-model installation or evaluation is Stage 2 and requires separate acceptance and authorization.

## Repository map

```text
K2B/
  AGENTS.md                    live Codex authority
  .agents/skills/k2b-*/        live K2B skills
  .codex/hooks.json            bounded Codex hooks
  k2b-dashboard/               optional manual intake/status UI
  scripts/                     capture, provider, vault, and verification tools
  tests/                       focused regression and safety tests
  plans/                       dated implementation and acceptance evidence
```

## Focused verification

Start with the checks relevant to a change. Stage 1's core gates include:

```bash
scripts/verify-codex-authority.sh
bash tests/codex-discovery-job.test.sh
bash tests/eod-capture-cron.test.sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest tests/test_eod_capture.py tests/test_eod_capture_status.py
python3 -m pytest tests/test_export_claude_history.py
bash tests/inventory-retired-runtime.test.sh
```

Dashboard changes also require its focused test, typecheck, and build. Provider changes require the Kimi wrapper tests and harmless authenticated probes on both Macs.

## Delivery

Implementation and delivery are separate decisions. Every eventual commit requires independent Kimi review when OpenAI built the change. Without explicit commit/push/ship wording, keep the verified work uncommitted and report what is implemented, tested, activated, and still blocked.
