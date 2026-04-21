# Design: K2B Skill Topology & Reference Section

**Date:** 2026-04-19
**Status:** approved
**Owner:** Claude (k2b-vault-writer)
**Triggered by:** Keith -- "i need a full diagram as in how each skill is tie into the other and how each loop is connecting with each other. there are skills that i haven't explore because i don't know what does it tie to. example the eval - i don't know how to use it and what it use it."

## Problem

The K2B operation manual (CLAUDE.md + the existing `wiki/context/context_k2b-system.md`) describes WHAT each skill does in prose, but does not show:

1. **Per-skill connections** -- what each skill reads, writes, and which other skills consume its output or feed into it
2. **A navigable mega-diagram** -- one place Keith can scan and spot the unfamiliar node
3. **Lesser-explored skills** -- anything outside Keith's daily-driver set is opaque (his stated example: `eval` from `skill-creator`)

Result: Keith cannot discover capabilities that already exist in the system. The skills are installed, the integration is built, but the discovery surface is missing.

## Non-Goals

- Replacing CLAUDE.md or the existing system reference. We append to the existing reference, do not rewrite.
- Documenting every skill in the universe. Scope is K2B + plugin skills Keith actually invokes.
- Building a runtime introspection tool. This is a static markdown reference; if it goes stale, `/lint` adds a freshness check later (separate work).
- Generating the diagrams from code. NBLM-assisted authoring of Mermaid is fine; the diagrams are committed as markdown, not regenerated on each session.

## Solution

Append a new section to `K2B-Vault/wiki/context/context_k2b-system.md` titled **"Skill Topology & Reference"** with four subsections, in this order:

### 1. Mega-Topology Diagram

One Mermaid `flowchart` showing every in-scope skill as a node and every "writes-to" / "reads-from" relationship as a directed edge. Color-coded by category via `classDef`:

- Capture (blue)
- Think (green)
- Create (purple)
- Teach K2B (orange)
- System (red)
- Plugin (gray)

The diagram is the navigation chart. Scan it, spot the unfamiliar node, jump to the per-skill entry below.

**Authoring approach:** I draft a candidate Mermaid block by hand from the per-skill entries. If it renders unreadably dense in Obsidian, fall back to subgraph-per-category and color the cross-category edges. NBLM is available as a layout helper if hand-drafting hits a wall, but is not a hard dependency.

### 2. Three Scoped Loop Diagrams

Each is a small, focused Mermaid `flowchart` covering one closed loop. The existing reference already has 4 (Two Machines, Knowledge Ingestion, Continuous Learning Loop, Content Pipeline). We add three more to complete coverage:

- **Code & Ship loop** -- `/learn` -> `self_improve_learnings.md` -> `/ship` step 0 (auto-promotion to `active_rules.md`) -> `/ship` adversarial review (Codex / MiniMax) -> commit -> `/sync` -> Mac Mini pm2 restart -> deployed code re-loaded next session
- **Review & Triage loop** -- `/weave` proposals + `/lint` findings + `/research videos` notes + observer findings (k2b-observer-loop on Mini) all land in `review/` -> `/review` triages -> outcomes feed `preference-signals.jsonl` -> observer learns from outcomes
- **Plugin tie-ins** -- `/codex:adversarial-review` (Checkpoint 1, plan-time) -> `superpowers:brainstorming` -> `superpowers:writing-plans` -> `superpowers:executing-plans` (or direct implementation) -> `/ship` Checkpoint 2 (Codex `/codex:review` or MiniMax fallback). Separately: `skill-creator:eval` benchmark -> `k2b-autoresearch` improvement loop -> revised SKILL.md -> committed via `/ship`.

### 3. Per-Skill Reference Table

Alphabetized within each category. Six fields per entry:

| Field | What it captures |
|---|---|
| **Purpose** | One-line description of what the skill does |
| **Trigger phrases** | Slash command + natural-language phrases that fire it |
| **Reads** | File paths and upstream skill outputs the skill consumes |
| **Writes** | File paths the skill creates or modifies, and any append-only logs |
| **Connects to** | Named upstream/downstream skills (e.g. "feeds /ship step 13.5", "consumed by k2b-observer-loop") |
| **When NOT to use** | Disambiguation against adjacent skills (e.g. `/insight` vs `/research` vs `/observe`) |

**Coverage list (final):**

K2B skills (22, all `.claude/skills/k2b-*`):
- Capture: `/daily`, `/meeting`, `/tldr`, `/youtube`, `/email`, `/compile`
- Think: `/review`, `/insight`, `/content`, `/research`, `/observe`, `/improve`, `/lint`, `/weave`
- Create: `/linkedin`, `/media`
- Teach K2B: `k2b-feedback` (one entry covering `/learn`, `/error`, `/request` -- three trigger paths into one skill)
- System: `/ship`, `/schedule`, `/usage`, `/autoresearch`, `/sync`
- Internal-only (no slash, used by other skills): `k2b-vault-writer`

Codex plugin:
- `/codex:review`, `/codex:adversarial-review`, `codex:rescue`, `codex:setup`

Superpowers (relevant):
- `brainstorming`, `writing-plans`, `executing-plans`, `test-driven-development`, `verification-before-completion`, `using-git-worktrees`, `dispatching-parallel-agents`, `code-reviewer` (also surfaces as `superpowers:code-reviewer`)

Anthropic skills (relevant):
- `skill-creator` (with eval/benchmark sub-capability called out explicitly)
- `pdf`, `xlsx`, `docx`, `pptx`
- `internal-comms`
- `schedule`
- `consolidate-memory`

Total: ~36 entries (20 K2B-side surface entries -- 22 directories minus 2 collapsed for k2b-feedback's 3-trigger consolidation, with k2b-vault-writer kept as internal-only -- plus 4 codex + 8 superpowers + 8 anthropic-skills).

**Format:** Each entry is a Markdown subsection (`### /skill-name` or `### plugin:skill-name`) followed by the 6 fields as bold-prefixed lines. No nested tables -- flat fields read better in Obsidian and are easier to grep.

Example shape:

```markdown
### /weave
**Purpose:** Background MiniMax M2.7 cron job (3x/week on Mac Mini) that proposes missing wiki cross-links and drops them into the review queue.
**Trigger phrases:** `/weave`, "run weave", "find missing links", "propose cross-links"
**Reads:** `wiki/**/*.md`, `wiki/context/crosslink-ledger.jsonl`
**Writes:** `review/crosslink_*.md`, `wiki/context/crosslink-ledger.jsonl`, `wiki/context/weave-metrics.jsonl`, `wiki/context/weave-alerts.md`
**Connects to:** Consumed by `/review` (proposal triage). Findings feed `wiki/context/weave-alerts.md` which session-start hook surfaces.
**When NOT to use:** Manual cross-linking is faster than `/weave` for a single known link -- use `[[filename]]` directly. `/weave` is for batch discovery you wouldn't have done by hand.
```

### 4. "If You Want X, Use Y" Reverse Index

Short table at the end. Maps a goal phrased in user terms to the right skill, optimized for the case where Keith remembers what he wants to do but not which skill does it.

Sample rows (full table grows during authoring):

| If you want to... | Use this |
|---|---|
| Find a pattern across notes | `/insight` |
| Deep-dive into a topic from external sources | `/research` |
| Surface implicit preferences from your behavior | `/observe` |
| Test if a skill is actually improving | `skill-creator` (eval mode) |
| Iteratively self-improve a skill via test loop | `/autoresearch` |
| See which skills you haven't been using | `/usage` |
| Find missing cross-links across the wiki | `/weave` |
| Catch contradictions and orphans in the vault | `/lint deep` |
| Get a second-model adversarial review on a plan | `/codex:adversarial-review` |
| Get a second-model review on uncommitted code | `/codex:review` (or `/ship` runs it automatically) |
| Brainstorm a new feature properly | `superpowers:brainstorming` |
| Convert a brainstorm into an implementation plan | `superpowers:writing-plans` |
| Execute a plan with checkpoints | `superpowers:executing-plans` |
| Run an isolated experiment without polluting your branch | `superpowers:using-git-worktrees` |
| Build or modify a skill from scratch | `skill-creator` |
| Schedule a recurring agent | `/schedule` |
| Push project file changes to Mac Mini | `/sync` |

## Authoring Sequence

1. Read each in-scope SKILL.md body (or plugin equivalent) to extract Purpose / Reads / Writes / Connects to / When NOT to use accurately. **Reads/Writes especially must be grounded in the actual SKILL.md, not guessed** -- this is the highest-risk drift source.
2. Draft per-skill entries grouped by category.
3. Hand-draft the mega-topology Mermaid block from the entries (each "Writes" -> "Reads" pair becomes an edge). Verify it renders in Obsidian preview before committing. If unreadable, switch to subgraph-per-category layout.
4. Hand-draft the three scoped loop diagrams.
5. Build the reverse-index table from the entries -- for each "When NOT to use" disambiguation, generate at least one row.
6. Append all four subsections to `context_k2b-system.md` in the order specified above. Update the `## Related Pages` section at the bottom of the existing doc if needed.
7. Commit as a single doc-only change. No `/ship` flow needed (vault-only, syncs via Syncthing) but Keith may run `/ship` separately if he wants the entry in DEVLOG.

## Acceptance Criteria

- [ ] New "Skill Topology & Reference" section appended to `context_k2b-system.md`
- [ ] Mega-topology diagram renders in Obsidian without nodes overlapping illegibly
- [ ] All ~38 in-scope skills have a complete entry with all 6 fields populated
- [ ] No entry has a placeholder ("TBD", "TODO") in any field
- [ ] Three new scoped loop diagrams render correctly
- [ ] Reverse-index table has at least 15 rows covering every category
- [ ] Existing 239 lines of `context_k2b-system.md` are unchanged (only appended to)
- [ ] Spec self-review passes (placeholder scan, internal consistency, scope, ambiguity)
- [ ] Keith can spot a skill he doesn't recognize on the mega-diagram and find its entry within 10 seconds

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Mega-diagram unreadable due to ~38 nodes | Fall back to subgraph-per-category layout with cross-category edges colored separately; if still too dense, drop the unified view and keep only scoped diagrams |
| Per-skill Reads/Writes drift from actual SKILL.md content | Author by reading each SKILL.md; if a SKILL.md changes later, the doc goes stale. Acceptance criterion: capture the SKILL.md commit SHA in the appended section's intro paragraph so staleness is detectable |
| Doc grows from 239 -> ~1100 lines, becomes unwieldy in one file | Acceptable for now; if the file exceeds 1500 lines or scrolling becomes painful, split into `context_skill-topology.md` (option B from brainstorming) in a follow-up |
| Plugin SKILL.md files in cache directories may move on plugin updates | Skip filesystem path references to plugin SKILL.md files in the entries; describe behavior from the in-context skill description instead |
| New skills added later don't get added to the topology | Add to backlog: a `/lint` check that flags k2b-* skill directories without an entry in `context_k2b-system.md`. Out of scope for this spec |

## Out of Scope (Explicit)

- A `/lint` enforcement that the topology stays in sync with installed skills (separate spec)
- Auto-generation of the topology from skill metadata (separate spec, would need a frontmatter schema for connections)
- Documentation for skills outside the active workflow (n8n-*, frontend-design, web-design-guidelines, k2b-media-generator deep-dive)
- Splitting the doc into a separate file (only triggers if the file becomes unwieldy)
