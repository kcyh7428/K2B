# Skill Topology & Reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Append a "Skill Topology & Reference" section to `K2B-Vault/wiki/context/context_k2b-system.md` so Keith can discover capabilities he is not using and trace how every skill connects to every other skill.

**Architecture:** Pure documentation work. Read each in-scope SKILL.md to extract Reads / Writes / Connects-to truth. Compose four subsections: mega-topology Mermaid diagram, three scoped loop diagrams, per-skill reference table (~36 entries x 6 fields), reverse-index lookup table. Append in order; do not modify the existing 239 lines.

**Tech Stack:** Markdown + Mermaid (already renders in Obsidian). Vault file syncs to Mac Mini via Syncthing automatically. No git commit required (K2B-Vault is Syncthing-only, no git). No code, no tests, no /ship handoff.

**Spec:** [docs/superpowers/specs/2026-04-19-skill-topology-design.md](../specs/2026-04-19-skill-topology-design.md)

---

## File Structure

**Modify:** `/Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md`
- Append a new top-level section `## Skill Topology & Reference` after line 144 (after the existing "Assistant Layer (22 Skills)" section, before "Continuous Learning Loop"). Actually, append at the END of the existing file, after `## Related Pages` and before EOF, so the new section sits at the bottom and existing flow is undisturbed.
- Update `## Related Pages` to point at the new section anchor.

**Read (truth source for entries):**
- All `/Users/keithmbpm2/Projects/K2B/.claude/skills/k2b-*/SKILL.md` (22 directories)
- Codex plugin: `/Users/keithmbpm2/.claude/plugins/marketplaces/openai-codex/plugins/codex/skills/*/SKILL.md` (4 skills: review, adversarial-review, rescue, setup -- exact paths confirmed in Task 4)
- Superpowers: `/Users/keithmbpm2/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/skills/*/SKILL.md` (8 in-scope: brainstorming, writing-plans, executing-plans, test-driven-development, verification-before-completion, using-git-worktrees, dispatching-parallel-agents, the code-reviewer marketplace skill -- path confirmed in Task 5)
- Anthropic-skills: marketplace cache (8 in-scope: skill-creator, pdf, xlsx, docx, pptx, internal-comms, schedule, consolidate-memory -- path confirmed in Task 6)

**Working scratch:** No intermediate files. Build entries directly into the final markdown as we go.

---

## Task 1: Locate exact plugin SKILL.md paths

**Files:**
- Read: filesystem (no edits)

- [ ] **Step 1: Locate Codex plugin skill paths**

Run:
```bash
ls /Users/keithmbpm2/.claude/plugins/marketplaces/openai-codex/plugins/codex/skills/ 2>/dev/null && \
ls /Users/keithmbpm2/.claude/plugins/cache/claude-plugins-official/superpowers/5.0.7/skills/ 2>/dev/null && \
find /Users/keithmbpm2/.claude/plugins -path '*anthropic-skills*' -name 'SKILL.md' 2>/dev/null | head -20
```

Expected: lists of skill directory names. Capture the exact base paths into a working notepad in your head -- they get reused in Tasks 4-6.

- [ ] **Step 2: Verify all 4 categories of in-scope skills are reachable**

Cross-check against the spec coverage list. If any plugin path is missing or returns empty:
- Codex missing -> mark those 4 entries as "TODO -- plugin path unresolved" and continue (do not block)
- Superpowers missing -> same
- Anthropic-skills missing -> same

Record the resolved base paths somewhere persistent. Suggested: write a one-line comment at the top of the working draft section.

---

## Task 2: Read K2B Capture + Create skills (8 SKILL.md files), draft entries

**Files:**
- Read: `.claude/skills/k2b-{daily-capture,meeting-processor,tldr,youtube-capture,email,compile,linkedin,media-generator}/SKILL.md`
- Working draft: keep entries in conversation context

- [ ] **Step 1: Read all 8 SKILL.md files in parallel**

Use the Read tool 8 times in parallel (single message, multiple Read calls). Skim each for: Purpose (one line), Trigger phrases (from frontmatter description + body), Reads (file paths the skill ingests), Writes (file paths the skill creates/modifies), Connects to (named upstream/downstream skills mentioned in the body), When NOT to use (explicit exclusions in the body).

- [ ] **Step 2: Draft entries in this exact format for each skill**

```markdown
### /daily
**Purpose:** Start or end the day -- compiles today's captures from Telegram, vault, and TLDRs into a structured daily note through multi-turn conversation.
**Trigger phrases:** `/daily`, "today", "start the day", "end of day", "EOD", "what's on today"
**Reads:** Telegram captures via k2b-remote, `K2B-Vault/raw/tldrs/*`, prior `Daily/YYYY-MM-DD.md` notes
**Writes:** `K2B-Vault/Daily/YYYY-MM-DD.md`, `K2B-Vault/raw/daily/YYYY-MM-DD_daily-extract.md`
**Connects to:** Consumed by `/compile` for wiki digestion. Daily extract feeds k2b-observer-loop pattern detection.
**When NOT to use:** For one-off thought capture mid-day, use `/tldr` (single-conversation) instead.
```

8 entries total. Order alphabetically within Capture, then Create.

- [ ] **Step 3: Verify completeness**

Each entry has all 6 bold-prefixed fields. No "TBD" or "TODO". No em dashes (run `grep '—'` mentally on the draft).

---

## Task 3: Read K2B Think skills (8 SKILL.md files), draft entries

**Files:**
- Read: `.claude/skills/k2b-{review,insight-extractor,research,observer,improve,lint,weave}/SKILL.md` plus the `/content` skill (likely lives in `k2b-insight-extractor` or its own dir -- verify in Step 1)

- [ ] **Step 1: Verify which skill owns `/content`**

Run:
```bash
grep -l '/content' /Users/keithmbpm2/Projects/K2B/.claude/skills/k2b-*/SKILL.md | head -5
```
Expected: identifies the owning skill directory. Add to the read list.

- [ ] **Step 2: Read all Think SKILL.md files in parallel**

Same parallel-Read pattern as Task 2. 8 files total (or 7 if `/content` shares a skill dir with another).

- [ ] **Step 3: Draft entries using the same 6-field format**

Pay extra attention to:
- `/observe` -> writes `preference-profile.md`, reads `observations.jsonl` -- connects to k2b-observer-loop and active rules pipeline
- `/weave` -> writes `crosslink-ledger.jsonl`, `weave-alerts.md`, drops proposals into `review/` -- connects to `/review`
- `/lint` -> reads vault structure, writes `lint-report.md` -- `/lint deep` invokes MiniMax for contradiction detection
- `/research videos` is a sub-mode that connects to NotebookLM CLI and `video-preferences.md` (the filter tail)
- `/improve` -> reads self-improvement logs, NOT the same as `/lint`

- [ ] **Step 4: Verify completeness** (same checks as Task 2.3)

---

## Task 4: Read K2B Teach + System skills (6 entries), draft entries

**Files:**
- Read: `.claude/skills/k2b-{feedback,ship,scheduler,usage-tracker,autoresearch,sync,vault-writer}/SKILL.md` (7 files, but k2b-feedback collapses to 1 entry covering 3 trigger phrases)

- [ ] **Step 1: Read all 7 SKILL.md files in parallel**

- [ ] **Step 2: Draft entries**

Special handling:
- **k2b-feedback:** ONE entry titled `### k2b-feedback (/learn, /error, /request)`. Trigger phrases lists all three. Purpose covers all three. Writes lists `self_improve_learnings.md` (from /learn), `self_improve_errors.md` (from /error), `self_improve_requests.md` (from /request), `policy-ledger.jsonl` (from /learn guard appends).
- **k2b-vault-writer:** entry titled `### k2b-vault-writer (internal)`. Note in Purpose that this has no slash command -- it's invoked by other skills (k2b-daily-capture, k2b-meeting-processor, k2b-tldr, k2b-insight-extractor) when they need to create or update a linked note.
- **/ship:** Connects-to field is the longest -- it ties to almost everything. Be explicit: upstream from `/learn` (auto-promotion in step 0), `/codex:review` (Checkpoint 2), MiniMax adversarial review (fallback), `/sync` (handoff in step 12). Downstream: writes DEVLOG.md, `wiki/log.md`, `wiki/concepts/index.md`, feature notes, `raw/sessions/`, `.pending-sync/` mailbox.
- **/sync:** consumes `.pending-sync/` mailbox written by `/ship --defer`. Reads diff against Mac Mini state. Writes nothing in the vault but rsyncs to Mini.

6 entries total. Combined with prior tasks: 8 + 8 + 6 = 22 K2B entries. Wait -- the spec says 20 K2B-side surface entries. Re-check: 6 Capture (Task 2 first half) + 2 Create (Task 2 second half) + 8 Think (Task 3) + 1 Teach (Task 4) + 5 System (Task 4) + 1 internal vault-writer (Task 4) = 23. The spec said "20 K2B-side surface entries" -- that count was approximate. Use the actual count, not the approximation.

- [ ] **Step 3: Verify completeness**

---

## Task 5: Read Codex plugin skills (4 SKILL.md files), draft entries

**Files:**
- Read: paths from Task 1 Step 1 output

- [ ] **Step 1: Read all 4 Codex skill files in parallel**

Skills: `/codex:review`, `/codex:adversarial-review`, `codex:rescue`, `codex:setup`.

If a path is unresolved (Task 1 marked it TODO), use the in-context skill description from the system reminder to draft the entry instead. Note in the entry "Source: in-context skill description (SKILL.md path unresolved at write time)" so future readers know.

- [ ] **Step 2: Draft entries with 6-field format**

Special handling:
- `/codex:adversarial-review` -> Connects-to: feeds Checkpoint 1 of `/ship` adversarial review (plan-time, before code). Reads plan files from `~/.claude/plans/` or `<repo>/plans/`.
- `/codex:review` -> Connects-to: Checkpoint 2 of `/ship` (pre-commit). Invoked by `/ship` step 3c via background+poll pattern.
- `codex:rescue` -> Connects-to: invoked when Claude is stuck or wants a second implementation pass. Not directly tied into `/ship`.
- `codex:setup` -> Connects-to: prerequisite for both review skills. Run once when plugin is first installed.

- [ ] **Step 3: Verify completeness**

---

## Task 6: Read Superpowers skills (8 SKILL.md files), draft entries

**Files:**
- Read: paths from Task 1 Step 1 output

- [ ] **Step 1: Read all 8 Superpowers skill files in parallel**

Skills: brainstorming, writing-plans, executing-plans, test-driven-development, verification-before-completion, using-git-worktrees, dispatching-parallel-agents, code-reviewer (also exposed as superpowers:code-reviewer subagent).

- [ ] **Step 2: Draft entries with 6-field format**

Special handling:
- `superpowers:brainstorming` -> Connects-to: terminal state is invoking `superpowers:writing-plans`. Triggered before any creative work (build features, write components, modify behavior).
- `superpowers:writing-plans` -> Connects-to: invoked after `brainstorming`. Terminal state is offering execution choice between `subagent-driven-development` and `executing-plans`.
- `superpowers:executing-plans` -> Connects-to: consumes plan from `writing-plans`. Used for inline execution with checkpoints.
- `superpowers:test-driven-development` -> Connects-to: invoked from inside any task that writes implementation code. RED-GREEN-REFACTOR cycle.
- `superpowers:verification-before-completion` -> Connects-to: invoked before claiming work complete or committing. Required by `/ship`.
- `superpowers:using-git-worktrees` -> Connects-to: invoked at the start of feature work that needs isolation. Optional, recommended for risky changes.

- [ ] **Step 3: Verify completeness**

---

## Task 7: Read Anthropic-skills (8 SKILL.md files), draft entries

**Files:**
- Read: paths from Task 1 Step 1 output

- [ ] **Step 1: Read all 8 Anthropic-skills files in parallel**

Skills: skill-creator, pdf, xlsx, docx, pptx, internal-comms, schedule, consolidate-memory.

- [ ] **Step 2: Draft entries with 6-field format**

Special handling:
- `anthropic-skills:skill-creator` -> THIS IS WHERE EVAL LIVES. Purpose must explicitly call out the three sub-capabilities: (1) create new skills from scratch, (2) edit/improve existing skills, (3) **run evals to test skill performance with variance analysis and benchmark description-triggering accuracy**. Connects-to: `k2b-autoresearch` consumes the eval output to drive iterative self-improvement loops.
- `anthropic-skills:consolidate-memory` -> Connects-to: K2B routes /learn-style facts to `self_improve_learnings.md`, but this skill is the canonical Anthropic auto-memory consolidation (merge duplicates, fix stale, prune index). Could be invoked manually but K2B's /learn covers most of it.
- `anthropic-skills:pdf`, `xlsx`, `docx`, `pptx` -> file-format skills, briefly note triggering (any mention of file type or a path with that extension).
- `anthropic-skills:internal-comms` -> for company comms templates (status reports, leadership updates). Note Keith's SJM context where this could apply.
- `anthropic-skills:schedule` -> overlaps with K2B's `/schedule`; note the difference (Anthropic = generic scheduled-tasks MCP; K2B = persistent cron triggers via Mac Mini).

- [ ] **Step 3: Verify completeness**

---

## Task 8: Draft three scoped loop diagrams

**Files:**
- Working draft: keep in conversation context

- [ ] **Step 1: Draft the Code & Ship loop diagram**

```mermaid
flowchart LR
    K[Keith corrects K2B] -->|/learn| LH[self_improve_learnings.md]
    LH -->|reinforced 3x| SHIP0[/ship step 0<br/>auto-promote]
    SHIP0 -->|y/n/skip| AR[active_rules.md]
    AR -->|loaded session-start| NEXT[Next session behavior]
    SHIP0 --> SHIP3[/ship step 3<br/>adversarial review]
    SHIP3 -->|tier-detect| CDX[Codex /codex:review]
    SHIP3 -->|fallback| MX[MiniMax-M2.7 review]
    CDX -->|approved| COMMIT[git commit + push]
    MX -->|approved| COMMIT
    COMMIT -->|step 12| Q{sync now or defer?}
    Q -->|now| SYNC[/sync runs]
    Q -->|defer| MAIL[.pending-sync/ mailbox]
    SYNC --> RSYNC[deploy-to-mini.sh rsync]
    MAIL -.->|next /sync consumes| SYNC
    RSYNC --> PM2[pm2 restart on Mac Mini]
    PM2 --> RUNNING[k2b-remote + observer-loop running new code]
```

- [ ] **Step 2: Draft the Review & Triage loop diagram**

```mermaid
flowchart LR
    W[/weave cron 3x/wk] -->|proposals| RV[review/]
    L[/lint deep] -->|contradictions| RV
    RES[/research videos] -->|per-video notes| RV
    OBS[k2b-observer-loop on Mini] -->|findings| OC[observer-candidates.md]
    OC -->|session-start hook| K[Keith confirm/watch/reject]
    RV -->|/review triages| OUT[Decisions: promote/archive/delete]
    OUT -->|outcomes| PS[preference-signals.jsonl]
    PS -->|fed back| OBS
    K -->|confirm| FB[k2b-feedback /learn]
```

- [ ] **Step 3: Draft the Plugin tie-ins diagram**

```mermaid
flowchart LR
    K[Keith asks for new feature] --> BRAIN[superpowers:brainstorming]
    BRAIN -->|spec written| ARV[/codex:adversarial-review<br/>Checkpoint 1 plan-time]
    ARV -->|spec approved| WP[superpowers:writing-plans]
    WP -->|plan written| EXEC[superpowers:executing-plans<br/>or subagent-driven-development]
    EXEC -->|implementation done| SHIP[/ship Checkpoint 2 pre-commit]
    SHIP -->|tier 2/3| CDX[/codex:review background+poll]
    SHIP -->|tier 1 or fallback| MX[MiniMax-M2.7 review]

    SC[skill-creator eval] -->|benchmark variance| AUTORES[/autoresearch loop]
    AUTORES -->|revised SKILL.md| SHIP
    SC -.->|create or edit skill| SKILLDIR[.claude/skills/k2b-*/SKILL.md]
```

- [ ] **Step 4: Verify all three diagrams**

- All edges have a verb on the arrow (or no label, but never a noun-only edge)
- No em dashes inside diagram labels (replace with `--`)
- Mermaid syntax: opening ` ```mermaid ` and closing ` ``` ` fences match
- Node IDs are unique within each diagram
- Labels are short enough to render legibly

---

## Task 9: Draft the mega-topology diagram

**Files:**
- Working draft: keep in conversation context

- [ ] **Step 1: Build the node list from all per-skill entries**

Every entry from Tasks 2-7 becomes a node. Group nodes by category using Mermaid `subgraph`:
- `subgraph CAP[Capture]`
- `subgraph THINK[Think]`
- `subgraph CREATE[Create]`
- `subgraph TEACH[Teach K2B]`
- `subgraph SYS[System]`
- `subgraph PLUGIN[Plugin]`

- [ ] **Step 2: Build the edge list from per-skill Connects-to fields**

For each entry, every "Connects to" mention becomes a directed edge. Examples:
- `/daily --> /compile` (extract feeds compile)
- `/learn --> /ship` (auto-promotion)
- `/ship --> /sync` (handoff)
- `superpowers:brainstorming --> superpowers:writing-plans`
- `superpowers:writing-plans --> superpowers:executing-plans`
- `skill-creator --> /autoresearch`
- `/codex:adversarial-review --> /ship` (Checkpoint 1 result feeds Checkpoint 2 decisions)

Cross-category edges (e.g. PLUGIN -> SYS) are highlighted differently (use a stroke color via classDef).

- [ ] **Step 3: Assemble the Mermaid block**

```mermaid
flowchart LR
    classDef cap fill:#cce5ff,stroke:#0066cc
    classDef think fill:#d4edda,stroke:#28a745
    classDef create fill:#e2d4f0,stroke:#6f42c1
    classDef teach fill:#ffe5cc,stroke:#fd7e14
    classDef sys fill:#f8d7da,stroke:#dc3545
    classDef plugin fill:#e9ecef,stroke:#6c757d

    subgraph CAP[Capture]
        DAILY[/daily]
        MTG[/meeting]
        TLDR[/tldr]
        YT[/youtube]
        EMAIL[/email]
        COMPILE[/compile]
    end
    subgraph THINK[Think]
        REVIEW[/review]
        INSIGHT[/insight]
        CONTENT[/content]
        RESEARCH[/research]
        OBSERVE[/observe]
        IMPROVE[/improve]
        LINT[/lint]
        WEAVE[/weave]
    end
    subgraph CREATE[Create]
        LI[/linkedin]
        MEDIA[/media]
    end
    subgraph TEACH[Teach K2B]
        FEEDBACK[k2b-feedback]
    end
    subgraph SYS[System]
        SHIP[/ship]
        SCHED[/schedule]
        USAGE[/usage]
        AUTORES[/autoresearch]
        SYNC[/sync]
        VW[k2b-vault-writer]
    end
    subgraph PLUGIN[Plugin]
        CDXR[codex:review]
        CDXAR[codex:adversarial-review]
        CDXRE[codex:rescue]
        CDXSE[codex:setup]
        SPB[sup:brainstorming]
        SPWP[sup:writing-plans]
        SPEX[sup:executing-plans]
        SPTDD[sup:tdd]
        SPVER[sup:verify]
        SPGW[sup:worktrees]
        SPDA[sup:dispatch-agents]
        SPCR[sup:code-reviewer]
        SC[skill-creator]
        PDF[pdf]
        XLSX[xlsx]
        DOCX[docx]
        PPTX[pptx]
        IC[internal-comms]
        ASCH[a-skills:schedule]
        CM[consolidate-memory]
    end

    DAILY --> COMPILE
    MTG --> COMPILE
    TLDR --> COMPILE
    YT --> COMPILE
    RESEARCH --> COMPILE
    COMPILE --> INSIGHT
    INSIGHT --> CONTENT
    CONTENT --> REVIEW
    WEAVE --> REVIEW
    LINT --> REVIEW
    OBSERVE --> REVIEW
    REVIEW --> LI
    LI --> MEDIA
    FEEDBACK --> SHIP
    SHIP --> SYNC
    SHIP --> CDXR
    SHIP --> AUTORES
    AUTORES --> SC
    SC --> SHIP
    SPB --> SPWP
    SPWP --> SPEX
    SPEX --> SHIP
    CDXAR --> SHIP
    CDXSE --> CDXR
    CDXSE --> CDXAR
    SCHED --> SHIP
    USAGE -.->|signals| FEEDBACK
    DAILY -.invokes.-> VW
    MTG -.invokes.-> VW
    TLDR -.invokes.-> VW
    INSIGHT -.invokes.-> VW

    class DAILY,MTG,TLDR,YT,EMAIL,COMPILE cap
    class REVIEW,INSIGHT,CONTENT,RESEARCH,OBSERVE,IMPROVE,LINT,WEAVE think
    class LI,MEDIA create
    class FEEDBACK teach
    class SHIP,SCHED,USAGE,AUTORES,SYNC,VW sys
    class CDXR,CDXAR,CDXRE,CDXSE,SPB,SPWP,SPEX,SPTDD,SPVER,SPGW,SPDA,SPCR,SC,PDF,XLSX,DOCX,PPTX,IC,ASCH,CM plugin
```

- [ ] **Step 4: Render-test mentally**

Walk through each subgraph, check labels are short, check no orphan nodes, check at least one cross-category edge per category (proves the diagram captures the topology, not just within-group flow). If a node has zero edges, double-check the Connects-to field of its entry -- either the diagram is missing an edge or the entry was under-specified.

If the visual density is too high, fall back to the subgraph-only layout (already done above) and accept that opening the diagram in Obsidian's Mermaid preview will give pan/zoom.

---

## Task 10: Draft reverse-index "If you want X, use Y" table

**Files:**
- Working draft: keep in conversation context

- [ ] **Step 1: Generate one row per "When NOT to use" disambiguation from Tasks 2-7**

For every entry where "When NOT to use" mentions another skill, that's a candidate row.

- [ ] **Step 2: Add capability-discovery rows for lesser-explored skills**

Required rows (from spec sample):
- Find a pattern across notes -> `/insight`
- Deep-dive into a topic from external sources -> `/research`
- Surface implicit preferences from your behavior -> `/observe`
- Test if a skill is actually improving -> `skill-creator` (eval mode)
- Iteratively self-improve a skill via test loop -> `/autoresearch`
- See which skills you haven't been using -> `/usage`
- Find missing cross-links across the wiki -> `/weave`
- Catch contradictions and orphans in the vault -> `/lint deep`
- Get a second-model adversarial review on a plan -> `/codex:adversarial-review`
- Get a second-model review on uncommitted code -> `/codex:review` (or `/ship` runs it automatically)
- Brainstorm a new feature properly -> `superpowers:brainstorming`
- Convert a brainstorm into an implementation plan -> `superpowers:writing-plans`
- Execute a plan with checkpoints -> `superpowers:executing-plans`
- Run an isolated experiment without polluting your branch -> `superpowers:using-git-worktrees`
- Build or modify a skill from scratch -> `skill-creator`
- Schedule a recurring agent -> `/schedule`
- Push project file changes to Mac Mini -> `/sync`

- [ ] **Step 3: Add 5+ Keith-specific workflow rows**

From your knowledge of K2B usage patterns:
- Capture a meeting transcript I just got from Fireflies -> `/meeting`
- Save the key points from this conversation -> `/tldr`
- Draft a LinkedIn post from a recent insight -> `/linkedin`
- Review the K2B system health -> `/improve`
- Process pending review queue items -> `/review`
- Triage today's email inbox -> `/email`

- [ ] **Step 4: Verify the table has 20+ rows minimum**

Acceptance bar from spec: "at least 15 rows covering every category". Aim for 20-25.

---

## Task 11: Compose final section + append to context_k2b-system.md

**Files:**
- Modify: `/Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md`

- [ ] **Step 1: Read current EOF state of the target file**

Run:
```bash
wc -l /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
```
Expected: 239 lines (matches what we measured at brainstorming time). If higher, someone else edited it -- reconcile before appending.

Read the last 20 lines via the Read tool to confirm `## Related Pages` is still the final section.

- [ ] **Step 2: Compose the new section in this exact order**

Section structure (build into a single string in conversation context, then write):

```markdown

## Skill Topology & Reference

> Built 2026-04-19. Each entry's Reads / Writes / Connects-to was extracted from the relevant SKILL.md at that date. If a skill SKILL.md changes, refresh the entry. If a new skill is added, add an entry. Acceptance check is a future `/lint` rule (see backlog).

### Mega-Topology Overview

<the mega Mermaid block from Task 9>

### Loop Diagrams

#### Code & Ship Loop

<Task 8 Step 1 block>

#### Review & Triage Loop

<Task 8 Step 2 block>

#### Plugin Tie-Ins (Brainstorm -> Plan -> Execute -> Ship)

<Task 8 Step 3 block>

### Per-Skill Reference

#### Capture

<Task 2 entries, alphabetical>

#### Think

<Task 3 entries, alphabetical>

#### Create

<Task 2 second-half entries, alphabetical>

#### Teach K2B

<Task 4 k2b-feedback entry>

#### System

<Task 4 system entries, alphabetical>

#### Plugin: Codex

<Task 5 entries, alphabetical>

#### Plugin: Superpowers

<Task 6 entries, alphabetical>

#### Plugin: Anthropic Skills

<Task 7 entries, alphabetical>

### "If You Want X, Use Y" Reverse Index

<Task 10 table>

```

- [ ] **Step 3: Append to the file**

Use the Edit tool with the `Related Pages` section's first line as `old_string` and prefix the new section before it. Or use Edit with the END-of-file pattern to append after `## Related Pages` body. Pick whichever produces fewer Edit-tool retries.

If the Edit tool has trouble with large blocks, fall back to: Read the existing file fully, compose new full-file content (existing + appended), Write it back. The Write tool will overwrite -- which is fine because the existing content is preserved verbatim by inclusion.

- [ ] **Step 4: Verify file extended**

Run:
```bash
wc -l /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
```
Expected: significantly larger (~1100-1300 lines). If still 239, the append didn't land -- diagnose.

---

## Task 12: Verification

**Files:**
- Read: `/Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md`

- [ ] **Step 1: Em dash scan**

Run:
```bash
grep -n '—' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
```
Expected: only the 17 pre-existing em dashes from the original section (lines below 240). Any em dash in lines past 240 = our violation. Fix immediately by replacing with `--`.

- [ ] **Step 2: Field completeness check**

Run:
```bash
grep -c '^\*\*Purpose:\*\*' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
grep -c '^\*\*Trigger phrases:\*\*' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
grep -c '^\*\*Reads:\*\*' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
grep -c '^\*\*Writes:\*\*' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
grep -c '^\*\*Connects to:\*\*' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
grep -c '^\*\*When NOT to use:\*\*' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
```
Expected: all six counts equal to the entry count (~36). If any count is short, an entry is missing that field -- find and fix.

- [ ] **Step 3: Mermaid fence balance**

Run:
```bash
grep -c '^```mermaid$' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
grep -c '^```$' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
```
Expected: opening-fence count == 8 (4 existing + 4 new: 1 mega + 3 scoped). Closing-fence count >= opening (other code blocks add to closing count too). If opening != 8, a mermaid block is missing or duplicated.

- [ ] **Step 4: TBD/TODO scan**

Run:
```bash
grep -n -E 'TBD|TODO|fill in|implement later' /Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md
```
Expected: zero results past line 239 (the original section may have legitimate uses; the new section must have none).

- [ ] **Step 5: Open in Obsidian preview**

Optional but recommended. Tell Keith: "Open `K2B-Vault/wiki/context/context_k2b-system.md` in Obsidian, scroll to the new section, sanity-check the diagrams render and the per-skill table reads cleanly." Wait for his thumbs-up.

---

## Self-Review

After writing this plan, fresh eyes on the spec:

**Spec coverage:**
- [x] Mega-topology diagram -> Task 9
- [x] Three scoped loop diagrams -> Task 8
- [x] Per-skill reference table (~36 entries x 6 fields) -> Tasks 2-7
- [x] Reverse-index table -> Task 10
- [x] Authoring requires reading each in-scope SKILL.md -> Tasks 2-7 step 1
- [x] No em dashes ever -> Task 12 step 1
- [x] Vault file, no /ship needed -> noted in plan header
- [x] Append-only, do not modify existing 239 lines -> Task 11 step 3

**Placeholder scan:** None found.

**Type/name consistency:**
- "Per-skill entry" structure (6 fields) consistent across Tasks 2-7. Field names: Purpose, Trigger phrases, Reads, Writes, Connects to, When NOT to use. All match spec.
- File path `/Users/keithmbpm2/Projects/K2B-Vault/wiki/context/context_k2b-system.md` is consistent across Tasks 11-12.
- Skill name conventions: `/k2b-name` for K2B slash skills, `plugin:skill-name` for plugin skills. Consistent.

No issues found.
