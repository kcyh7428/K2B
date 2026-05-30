---
name: k2b-portfolio
description: Surface K2Bi ticker pipeline state across all stages -- awaiting promotion, watchlist, active orchestrator flights, drafted theses, proposed strategies, open positions, recently closed. Use when Keith says /portfolio, "show me my portfolio", "what tickers are in flight", "where are my K2Bi positions", "what theses am I working on", "what orchestrator flights are running", or asks for an at-a-glance K2Bi status view. Complements /plate (K2B-side) -- this one covers K2Bi-side state only.
---

# K2B Portfolio

Read every canonical K2Bi pipeline source and emit a single dashboard so Keith knows where every ticker sits without opening K2Bi.

## When to Trigger

Keith says any of:
- `/portfolio`
- "show me my portfolio"
- "what tickers are in flight"
- "where are my K2Bi positions"
- "what theses am I working on"
- "K2Bi status"
- "portfolio view"

Do NOT auto-trigger on session start -- the session-start hook already surfaces review queue + observer items. `/portfolio` is for explicit "give me the K2Bi pipeline view."

## What it does

Runs `~/Projects/K2B/.claude/skills/k2b-portfolio/scripts/portfolio.sh` and shows the output verbatim. The script reads only canonical K2Bi vault paths -- no new data stores invented here.

## Canonical sources (read-only)

| Section | Read from | Filter | Row format hint |
|---|---|---|---|
| Awaiting promotion | `K2BI_VAULT_PATH/wiki/macro-themes/theme_*.md` | Files whose body lists candidate tickers AND no corresponding watchlist entry exists yet. Themes older than 30 days are skipped as stale. | Theme slug · `waiting_for_promotion` · ⚠ pick ticker · age since theme mtime |
| Watchlist (Stage 1-2) | `K2BI_VAULT_PATH/wiki/watchlist/<SYMBOL>.md` | Frontmatter `status:` field = `promoted` OR `screened`. Skip files where status is `dropped` or other terminal states. | symbol · status · auto (if `promoted` and Quick Score band exists) OR ⚠ enrich (if just `promoted` no Stage-2 yet) · age since file mtime |
| Active orchestrator flights | `K2B_VAULT_PATH/System/orchestrator/orchestrator.sqlite` (override path with `K2B_ORCH_DB`) | `tasks` rows with `status NOT IN ('done','failed','cancelled')` -- i.e. `ready`, `running`, `blocked`, `zombie`. Read-only via `mode=ro` URI (never checkpoints the WAL). Missing DB = `(none)`; unreadable DB = `⚠ orchestrator board unreachable`. | label (entity_key ticker, else command_key) · status · next-action (blocked → ⚠ unblock: reason; zombie → ⚠ needs reclaim; running → auto · running; ready+blocked_by → auto · waiting on `<task>`; ready → auto · queued) · age (heartbeat age for running/zombie, else created age -- timezone-offset-aware via Python `fromisoformat`, since the orchestrator stores UTC `+00:00` timestamps) |
| Theses drafted, awaiting review | `K2BI_VAULT_PATH/wiki/tickers/<SYMBOL>.md` | File body contains a `## Thesis` or `# Thesis` section heading AND frontmatter `thesis_approved_at:` is `null` or absent. | symbol · `thesis_drafted` · ⚠ review thesis · age since file mtime |
| Strategies proposed, awaiting ship | `K2BI_VAULT_PATH/wiki/strategies/strategy_*.md` | Frontmatter `status: proposed` AND `approved_at: null`. | strategy slug · `proposed` · ⚠ ship via /invest-ship · age since file mtime |
| Live strategies + open positions | `K2BI_VAULT_PATH/wiki/strategies/strategy_*.md` with `status: approved` AND `approved_at:` not null, joined with `K2BI_VAULT_PATH/raw/journal/<latest-date>.jsonl` events. | For each approved strategy: find the most recent `order_filled` BUY event for its ticker that has no matching SELL since. If a BUY-without-SELL exists, the position is open. | symbol · `position_open` · auto (engine holding) · age since fill OR `approved_no_fill_yet` if no BUY event |
| Recently closed (last 14 days) | `K2BI_VAULT_PATH/raw/journal/*.jsonl` AND `K2BI_VAULT_PATH/wiki/insights/<date>_*-retro.md` | Journal: `order_filled` SELL events with `ts` field within last 14 days. For each, lookup matching retro insight file by date proximity (same-day or +1 day). | symbol · `closed P&L $N.NN` · review retro (if retro file exists) OR ⚠ retro pending (if no retro yet) · age since SELL ts |

## How to invoke

```bash
bash ~/Projects/K2B/.claude/skills/k2b-portfolio/scripts/portfolio.sh
```

With a section filter:
```bash
bash ~/Projects/K2B/.claude/skills/k2b-portfolio/scripts/portfolio.sh strategies
```

Valid section args: `awaiting`, `watchlist`, `active`, `theses`, `strategies`, `positions`, `closed`. (`active` returns only the orchestrator-flights section -- the `/portfolio active` shortcut.)

Or with a sandbox vault for testing:
```bash
K2B_VAULT_PATH=/tmp/sandbox-vault \
K2BI_VAULT_PATH=/tmp/sandbox-k2bi \
bash ~/Projects/K2B/.claude/skills/k2b-portfolio/scripts/portfolio.sh
```

## Output format

The script (`portfolio.sh`) emits raw markdown. Each section produces the section heading plus `(none)` if empty -- never silently dropped.

Sections in fixed order:

1. **⚠ Awaiting promotion** -- themes with candidate tickers not yet on watchlist
2. **📋 Watchlist (Stage 1-2)** -- promoted / screened tickers awaiting enrichment or thesis
3. **🛫 Active orchestrator flights** -- in-flight orchestrator tasks (ready / running / blocked / zombie) with status + next-action + age
4. **📝 Theses drafted, awaiting review** -- ticker notes with an unapproved thesis block
5. **📊 Strategies proposed, awaiting ship** -- strategy specs awaiting `/invest-ship`
6. **💼 Live strategies + open positions** -- approved strategies with open journal positions or awaiting first fill
7. **✅ Recently closed (last 14 days)** -- SELL fills from journal with retro status

## Rendering convention (Hybrid)

The agent (Claude Code) does NOT show the raw script output verbatim by default. It renders into the **Hybrid format**, mirroring `k2b-plate`. The script stays the source of truth; the rendering is a display layer applied between the script's stdout and the user-facing message.

**Per-section rule:**

| Section | Treatment |
|---|---|
| ⚠ Awaiting promotion | **Full row verbatim** -- Keith needs to see which theme and how old it is to decide on promotion. |
| 📋 Watchlist | **Full row verbatim** -- decision-gate items (⚠ enrich) need full context. Auto rows may be compacted to one line. |
| 🛫 Active orchestrator flights | **Full row verbatim** -- ⚠ blocked/zombie flights need the unblock reason + age in one glance to act. Running/queued rows may be compacted. |
| 📝 Theses drafted | **Full row verbatim** -- Keith needs the ticker + age to prioritize review. |
| 📊 Strategies proposed | **Full row verbatim** -- the `/invest-ship` command and slug must be visible. |
| 💼 Live positions | **Full row verbatim** for open positions; `approved_no_fill_yet` rows may be summarized if many. |
| ✅ Recently closed | **Full row verbatim** for retro-pending items (⚠); completed retros may be compacted to a one-line summary. |

**Override -- "raw portfolio":** if Keith says "raw portfolio", "show me raw", "raw output", or "full portfolio", the agent skips the Hybrid rendering and dumps `portfolio.sh` stdout verbatim. Use when Keith needs the complete pipeline text or wants to audit what the script actually emits.

**Override -- "summarize portfolio":** if Keith says "summarize portfolio" or "tighter portfolio", compress further into a curated summary (1-line per section with decision items only).

**Default behavior:** Hybrid unless an override phrase appears in the same message.

## What this skill does NOT do

- **NOT a write tool.** Never promotes, approves, ships, or trades. It is strictly read-only.
- **NOT a /plate replacement.** `/plate` covers K2B project state; `/portfolio` covers K2Bi investment pipeline state. Run both for a full picture.
- **NOT a calculator.** Reads what K2Bi already wrote. P&L numbers come from journal events, not re-computed.
- **NOT live broker-data.** Position state is journal-derived, latest-as-of-last-event. Not a real-time broker query.
- **NOT runnable on Mac Mini.** K2Bi-Vault was nuked from Mac Mini during Phase 3.9 VPS migration (2026-04-25). The K2Bi engine + vault live on the Hostinger VPS in KL; the K2Bi PM workspace lives on Keith's MacBook only. Sending `/portfolio` to the Telegram bot (which runs on Mac Mini) will dispatch the script there and fail with `K2Bi vault unreachable at /Users/fastshower/Projects/K2Bi-Vault`. **Use `/portfolio` from K2B Claude Code on MacBook.** If the Telegram path is ever needed, the prerequisite is restoring K2Bi-Vault to Mac Mini via Syncthing read-only mirror (separate feature, not in this skill's scope).

## Implementation notes

- **Bash 3.2 + BSD awk compatible.** No `readarray`, `mapfile`, associative arrays, or `${var,,}`. Uses `index()` for literal-character matching to avoid BSD awk's `\|` illegal-primary error.
- **`set -uo pipefail` but NOT `set -e`.** The script handles missing files gracefully (a missing journal file for today is normal on weekends, not an error).
- **The script itself is read-only.** `portfolio.sh` performs zero writes to vault or memory state -- only `echo`/`awk`/`grep`/`sed`/`jq`/`find`/`cut` (all read operations).
- **Orchestrator SQLite read: `mode=ro` with bounded retry (no unlocked reads).** The Active flights section reads `orchestrator.sqlite` (WAL-mode) via `file:...?mode=ro` -- NOT the `-readonly` flag (fails CANTOPEN on a WAL DB needing to create the `-shm`), and NOT `immutable=1` (skips locking -> torn-read risk under a concurrent write). `mode=ro` takes a shared lock, never checkpoints, and cannot mutate state. WAL mode lets a `mode=ro` reader run fine alongside an active writer, so the ONLY failure is the brief window right after a writer exits, while its WAL index is torn down and a read-only open transiently can't build the `-shm` (CANTOPEN/error 14). That clears in well under a second, so the section **retries `mode=ro` up to 5 times with a 0.2s backoff** (happy path returns on the first try, no delay). Only if every retry fails does it print `⚠ orchestrator board unreachable`. Retrying rather than dropping to an unlocked `immutable=1` read keeps the locked-read guarantee intact, so a genuine concurrent write can never become a torn or stale read. Discovered live 2026-05-30 when a `/portfolio` immediately after a flight cancel hit the transient and showed "unreachable".
- **Stale-data guard (scoped).** If `K2BI_VAULT_PATH/` or `K2BI_VAULT_PATH/wiki/` does not exist, the script prints `K2Bi vault unreachable at <path>` to stderr and exits 1 -- EXCEPT for `/portfolio active`, which reads only the orchestrator SQLite (K2B vault) and must not be hidden by a K2Bi vault sync/mount issue. The guard is skipped when `SECTION=active`.
- **Defensive frontmatter parsing.** `fm_get_scalar` toggles on `^---$` and only emits matches within the first frontmatter block, preventing body-content leakage.
- **`*.sync-conflict-*` files are skipped** at every read step. Syncthing can create these on the vault during cross-machine writes.
- **Performance.** End-to-end runtime under 5 seconds on Keith's MacBook with the real K2Bi vault. Sections read sequentially; jq used for journal JSONL parsing.

## Error handling

- If `K2B_VAULT_PATH` is unset, defaults to `~/Projects/K2B-Vault/` (used only to locate the orchestrator SQLite for the Active flights section). Override the DB path directly with `K2B_ORCH_DB`.
- If `K2BI_VAULT_PATH` is unset, defaults to `~/Projects/K2Bi-Vault/`.
- If the orchestrator SQLite does not exist (orchestrator never run), the Active flights section emits `(none)`; if it exists but cannot be read, it emits `⚠ orchestrator board unreachable`.
- If a source file is missing (e.g., no journal files yet on a fresh vault), the corresponding section emits `(none)` and continues.
- If frontmatter is malformed in a strategy or ticker note, that file is skipped silently (parsing errors do not abort the whole portfolio).

## Usage logging

After completing the main task, log this skill invocation:

```bash
echo -e "$(date +%Y-%m-%d)\tk2b-portfolio\t$(echo $RANDOM | md5sum | head -c 8)\tsurfaced portfolio dashboard" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Notes

- This skill is the **consumer** in the K2Bi pipeline architecture. Producers are: K2Bi macro-theme extraction (writes `theme_*.md`), K2Bi watchlist promotion (writes `watchlist/<SYMBOL>.md`), K2Bi thesis generation (writes `tickers/<SYMBOL>.md`), K2Bi strategy proposal (writes `strategies/strategy_*.md`), K2Bi engine (writes `raw/journal/*.jsonl`), and K2Bi retro generation (writes `wiki/insights/<date>_*-retro.md`).
- If `/portfolio` output is missing something Keith expected to see, the bug is in the producer side (failed to write to the canonical home), not the reader. Fix at source per the Memory Layer Ownership doctrine in CLAUDE.md.
- This skill replaces nothing. It complements `/plate` (K2B project state) and the K2Bi dashboard (if one exists elsewhere).
