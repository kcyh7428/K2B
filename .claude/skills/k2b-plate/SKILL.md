---
name: k2b-plate
description: Surface every pending item that needs Keith's attention across all canonical sources -- pending-action features, reminders, builder handoffs, K2Bi PM checkpoint, In Progress lanes, recently shipped, memory flags. Use when Keith says /plate, "what's on my plate", "what's outstanding", "what needs my attention", "what do I owe", "anything pending", "show me my plate".
---

# K2B Plate

Read every canonical pending-state source and emit a single dashboard so Keith knows what needs his attention without re-deriving it every session.

## When to Trigger

Keith says any of:
- `/plate`
- "what's on my plate"
- "what's outstanding"
- "what needs my attention"
- "what do I owe"
- "anything pending"
- "show me my plate"
- "what's left"

Do NOT auto-trigger on session start -- the session-start hook already surfaces review queue + observer items. `/plate` is for explicit "give me the strategic-overview view."

## What it does

Runs `~/Projects/K2B/.claude/skills/k2b-plate/scripts/plate.sh` and shows the output verbatim. The script reads only canonical sources defined by `feature_pending-discipline` (shipped 2026-05-16) -- no new data stores invented here.

## Canonical sources (read-only)

| Section | Source | Filter |
|---|---|---|
| Pending actions on in-progress features or active projects | `wiki/concepts/feature_*.md` + `wiki/concepts/project_*.md` + `wiki/projects/project_*.md` | frontmatter has `status: in-progress` (feature_*) OR `status: active` (project_*) AND `pending-action:` set |
| Open reminders | `wiki/context/reminders.md` | lines matching `^- \[open\]` |
| Recent open handoffs | `raw/sessions/*_handoff_*.md` | frontmatter `status: open`, mtime within last 7 days |
| K2Bi PM current checkpoint | `K2Bi-Vault/wiki/planning/index.md` Resume Card | first `## K2B PM checkpoint` blockquote (top = most recent) |
| In Progress lanes | `wiki/concepts/index.md` In Progress table | all rows |
| Recently shipped | `wiki/concepts/index.md` Shipped table | rows with date within last 7 days |
| Memory flags | `~/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory/self_improve_requests.md` + `self_improve_errors.md` | top open R-IDs, recent E-IDs |
| Backlog top 3 + Next Up | `wiki/concepts/index.md` Backlog + Next Up tables | first N rows |

## How to invoke

```bash
bash ~/Projects/K2B/.claude/skills/k2b-plate/scripts/plate.sh
```

Or with a sandbox vault for testing:
```bash
K2B_VAULT_PATH=/tmp/sandbox-vault \
K2BI_VAULT_PATH=/tmp/sandbox-k2bi \
bash ~/Projects/K2B/.claude/skills/k2b-plate/scripts/plate.sh
```

## Output format

The script (`plate.sh`) emits raw markdown. Each section produces zero lines if empty -- short on quiet days, longer on busy days.

Sections in fixed order:

1. **⚠ Needs your decision now** -- pending-actions + open reminders + recent open handoffs
2. **🎩 K2Bi PM current checkpoint** -- raw blockquote extract from Resume Card
3. **✅ Recently shipped (last 7 days)** -- from concepts/index.md (notes column truncated to 150 chars)
4. **🚧 In Progress lanes** -- from concepts/index.md
5. **🧠 Memory flags** -- open R-IDs + recent E-IDs
6. **📅 Next Up + Backlog top 3** -- from concepts/index.md

## Rendering convention (the Hybrid -- agent-side display layer)

The agent (Claude Code) does NOT show the raw script output verbatim by default. It renders into the **Hybrid format** below, established 2026-05-16 after Keith compared three options (Raw, Curated, Hybrid) and picked Hybrid. The script stays the source of truth; the rendering is a display layer applied between the script's stdout and the user-facing message.

**Per-section rule:**

| Section | Treatment |
|---|---|
| ⚠ Pending actions | **Full body verbatim** -- do NOT truncate or summarize. Keith needs the Close: condition and the "how to" in one glance to act on it. |
| 🔔 Open reminders | **Full body verbatim** -- same reason. |
| 🎩 K2Bi PM checkpoint | **One-paragraph summary** the agent extracts from the multi-paragraph blockquote. Capture: ship state, next concrete action, binding constraints. Skip the numbered conditional triggers + discipline list unless Keith asks. |
| ✅ Recently shipped | **Title + date + one-line tag** per item. Skip the long Shipped-table notes. |
| 🚧 In Progress | **2-column markdown table** (feature / updated date). No commentary column. |
| 📅 Pipeline | **Compact one-line** Next Up + Backlog top 3 (e.g. "Next Up: feature_X · Backlog top 3: A · B · C"). |
| 🧠 Memory flags | **Omit entirely when empty**. When populated, render as a 1-line summary per R-ID/E-ID. |

**Why this shape:** pending stuff is what Keith ACTS on -- needs full context. Lanes/snapshots are reference -- need to be visible but not loud. The compression on lanes/checkpoint/shipped is bias-managed (the script still has the full data; agent just narrows the display).

**Override -- "raw plate":** if Keith says "raw plate", "show me raw", "raw output", or "full plate", the agent skips the Hybrid rendering and dumps `plate.sh` stdout verbatim. Use when Keith needs the complete K2Bi Resume Card text, the full Shipped-table notes, or wants to audit what the script actually emits.

**Override -- "summarize plate":** if Keith says "summarize plate" or "tighter plate", compress further into Bulleted-Curated form (1-line per pending item, all references reduced to slug names, no K2Bi paragraph -- just one sentence).

**Default behavior:** Hybrid unless an override phrase appears in the same message.

## Implementation notes

- **Frontmatter-aware parsing required.** Naive `grep '^pending-action: '` matches YAML example blocks in feature note bodies (verified during pending-discipline smoke test). The script uses an awk parser that toggles on `^---$` and only emits matches within the first frontmatter block.
- **`*.sync-conflict-*` files are skipped** at every read step. Syncthing can create these on the vault during cross-machine writes.
- **Class-differentiated reading:** handoffs check status: open from frontmatter; reminders use the `[open]` text marker; pending-actions check that both `status: in-progress` AND `pending-action:` are set.
- **The script itself is read-only.** `plate.sh` performs zero writes to vault or memory state -- only `echo`/`awk`/`grep`/`sed`/`find`/`cut` (all read operations). The post-task usage logging in the "Usage logging" section below is an *agent-side convention* (Claude appends one line to `skill-usage-log.tsv` after running the skill), consistent with all other K2B skills (k2b-sync, k2b-ship, etc.) and not part of the script's own execution.
- **Defensive frontmatter parsing.** `fm_get_block` caps block output at 50 lines to prevent body-content leakage if a feature note has malformed frontmatter (missing closing `---`, unusual next-key formatting). Legitimate pending-action bodies are < 10 lines; the 50-line cap is belt-and-suspenders.
- **Debug mode.** Set `K2B_PLATE_DEBUG=1` to surface awk/grep errors that are otherwise suppressed. Use when a known pending item isn't appearing in the output and you need to see the parse errors.

## Error handling

- If `K2B_VAULT_PATH` is unset, defaults to `~/Projects/K2B-Vault/`.
- If a source file is missing (e.g. `reminders.md` not yet created on a fresh vault), the corresponding section emits zero lines and continues.
- If frontmatter is malformed in a feature note, that file is skipped silently (parsing errors do not abort the whole plate).
- If `K2Bi-Vault/wiki/planning/index.md` doesn't exist (running on a non-K2Bi machine), the K2Bi section emits zero lines.

## Usage logging

After completing the main task, log this skill invocation:

```bash
echo -e "$(date +%Y-%m-%d)\tk2b-plate\t$(echo $RANDOM | md5sum | head -c 8)\tsurfaced plate dashboard" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Notes

- This skill is the **consumer** in the pending-discipline architecture (shipped 2026-05-16 via [[wiki/concepts/Shipped/feature_pending-discipline]]). Producers are: K2B Claude Code sessions (set `pending-action:` on feature notes when shipping with deferred MVP), me (write `raw/sessions/*_handoff_*.md` when drafting paste-ins), Keith (manual edits to `reminders.md`), K2Bi PM hat (writes Resume Card checkpoint).
- If `/plate` output is missing something Keith expected to see, the bug is in the producer side (failed to write to the canonical home), not the reader. Fix at source per the Memory Layer Ownership doctrine in CLAUDE.md.
- This skill replaces nothing. It complements the existing loop dashboard (a N / r N / d N triage) and session-start hook (review queue + observer items).
