# Active Motivations: Motivation-Aware Content Discovery

**Date:** 2026-04-16 (revised 2026-04-16 post-Codex)
**Status:** Designed, pending implementation
**Prerequisites:** feature_canonical-memory (shipped b5c77ce), feature_session-end-capture (shipped 996acaf)
**Codex plan review:** completed 2026-04-16, blockers addressed below

## Problem

K2B's `/research videos` pipeline knows WHAT Keith likes (topic, freshness, channel trust, expertise level) but not WHY he's interested. The judgment step uses a hardcoded "Keith framing" that describes his identity ("Senior TA leader running AI transformation") but not his current focus. Interest in "Claude Code skills" could mean "building self-improving k2b-remote" or "preparing a LinkedIn content series" -- the system can't distinguish.

The current pipeline deliberately blindfolds Gemini. But Gemini sits on 20M+ tokens of full video transcripts. If K2B tells Gemini what Keith is working on, Gemini can extract the specific segment that solves Keith's current problem, not just return a generic content description.

Keith's feedback (ClaudeClaw v2 research, 2026-04-16): "K2B should understand WHY Keith cares about specific content, not just WHAT the content says. Interest in a topic doesn't mean 'learn everything' -- it means 'learn the intersection with Keith's active concerns.'"

## Design (revised post-Codex)

### Two files, single-writer per file (solves Syncthing race structurally)

- `wiki/context/active-motivations.md` -- observer-owned. Contains Building + Emerging Interests. Observer on Mac Mini is the only writer. Written via atomic rename.
- `wiki/context/active-questions.md` -- Keith-owned. Append-only for new questions, Keith edits directly in Obsidian to remove/prune. Observer never writes this file.

Skills that consume motivations read BOTH files and concatenate at read time. Keith can browse either in Obsidian. This eliminates the cross-device race: each file has a single writer, Syncthing just moves bytes.

### Document structure

**active-motivations.md** (observer-owned):
```markdown
---
tags: [context, motivations, observer-owned]
type: context
origin: k2b-observer
up: "[[index]]"
last-observer-update: 2026-04-16T10:30:00Z
building-last-synced: 2026-04-16T10:30:00Z
---

# Active Motivations (observer-maintained)

Keith's active motivations as detected by the observer. Read by /research, /compile, /daily, NBLM prompts. For Keith's self-added questions see [[active-questions]].

## Building (auto-synced from wiki/concepts/index.md by Ship-1 bootstrap script + Ship-2 observer)

- **feature_mission-control-v3** -- Ship 1-of-4 in measurement, gate 2026-04-24 (in_progress)
- **project_minimax-offload** -- Phase 1 shipped-local, gate >= 2026-04-24 (in_progress)

## Emerging Interests (observer-detected, Ship 2+)

*(empty -- observer populates starting Ship 2)*

Each entry format:
- **topic** *(first_seen YYYY-MM-DD, last_seen YYYY-MM-DD, evidence: N signals)*
```

**active-questions.md** (Keith-owned):
```markdown
---
tags: [context, motivations, keith-owned]
type: context
origin: keith
up: "[[index]]"
---

# Active Questions (Keith-maintained)

Things Keith wants to learn more about. Append-only via inline "add X to my active questions"; Keith edits directly in Obsidian to remove. Observer never writes this file.

## Questions

- *(empty -- Keith adds via inline commands)*
```

### Ownership rules (crisp)

| Section | Writer | Reader | Write mechanism |
|---------|--------|--------|-----------------|
| active-motivations.md Building | bash bootstrap (Ship 1) + observer (Ship 2) | /research, /compile, /daily, observer prompt | atomic tmp+mv via helper |
| active-motivations.md Emerging | observer (Ship 2) | same as Building | atomic tmp+mv via helper |
| active-questions.md Questions | Keith inline via helper | same | append-only via helper (flock-protected) |

No writer ever crosses these lines. No cross-device race possible.

### Helper script (addresses CLAUDE.md ownership violation)

`scripts/motivations-helper.sh` owns the write procedure. CLAUDE.md only advertises intent routing.

```bash
# Commands:
motivations-helper.sh add-question "text"         # appends to active-questions.md under flock
motivations-helper.sh remove-question "pattern"   # Keith inline path (rare)
motivations-helper.sh sync-building               # reads concepts/index.md, writes Building section atomically
motivations-helper.sh read                        # outputs concatenated view for skills

# All writes go through atomic tmp+mv + flock.
```

CLAUDE.md rule: "When Keith says 'add X to my active questions', invoke `scripts/motivations-helper.sh add-question \"X\"`. Do not edit the file directly."

### NBLM prompt change (revised to preserve baseline coverage)

Current NBLM prompt (k2b-research SKILL.md Step 5, line ~387):
```
Each source in this notebook is a YouTube video. For each one, return an objective content description.
...
Do NOT judge whether a video is good, suitable, relevant, or recommended.
```

Revised prompt addition (after existing schema, before "Do NOT judge"):
```
Viewer context (use for EXTRACTION GUIDANCE only, not ranking):
${MOTIVATIONS}

For every video, maintain the baseline description quality defined above
(2-3 sentences in what_it_covers, all schema fields populated).

Additionally, when a video's content touches any viewer context area,
add these fields to that video's entry:
- "motivation_overlap": ["short phrase from context that connects"]
- "motivation_detail": "2-3 sentences describing specifically how the video
  addresses the matching motivation (which timestamps, which examples, which
  claims)"

Do NOT remove detail from videos that don't match viewer context. Do NOT use
this context to judge, rank, filter, or downgrade any video. Extraction is
equal for all videos; overlap videos get ADDITIONAL fields, never reduced
ones.
```

This directly addresses Codex blocker 3: baseline coverage is explicitly preserved, motivation detail is an additive field not a replacement.

### Observer integration (Ship 2, simpler than original plan)

Ship 2 does NOT ask MiniMax to parse concepts/index.md tables. Instead:

1. A deterministic bash script (`scripts/motivations-helper.sh sync-building`) reads concepts/index.md, extracts In Progress + Next Up rows with deterministic regex, writes the Building section atomically. This ships with Ship 1.
2. Ship 2 adds this script to observer-loop.sh as a pre-analysis step (runs every cycle, fast, no LLM).
3. Ship 2 additionally tells MiniMax (in observer-prompt.md) to detect Emerging Interests from session summaries only (not from concepts/index.md). Output: `emerging_interests[]` with `{topic, first_seen, last_seen, evidence_count, supporting_signals}`.
4. Observer-loop.sh processes emerging_interests[], merges with existing Emerging section (dedup by topic, update last_seen + evidence_count for resurfacing topics), filters decayed (last_seen > 14 days ago AND no current signals).

### Decay semantics (addresses Codex adjustment 4)

Each Emerging Interest carries `first_seen`, `last_seen`, `evidence_count`.
- **Resurfaces** = new session signal for same topic -> update `last_seen` to today, increment `evidence_count`. `first_seen` never changes.
- **Decays out** = `last_seen` > 14 days ago AND no signal in current observer cycle. Removed on next observer run.
- **Promoted to Active Questions** = Keith says "promote [topic]" -> K2B calls `motivations-helper.sh add-question` with the topic, then tells observer to remove the Emerging entry on next cycle (via a marker file, to avoid observer not knowing about Keith's promotion).

### Rollback (addresses Codex adjustment 7)

Motivation injection is controlled by env var `K2B_MOTIVATIONS_ENABLED` (default: true).
- `K2B_MOTIVATIONS_ENABLED=false` -> NBLM prompt reverts to current objective-only text. Judgment step ignores motivations file. Pipeline behaves exactly as today.
- This is a simple kill switch in bash: `if [[ "${K2B_MOTIVATIONS_ENABLED:-true}" == "true" && -f "$MOTIVATIONS_FILE" ]]; then ...`
- Enables clean experimentation without code rollback.

### observer-runs.jsonl logging (addresses Codex adjustment 5)

Ship 2 prerequisite: extend observer-loop.sh line ~265 to log `user_msg` alongside `prompt` (system) and `response`. Truncate to 8000 chars same as existing fields. This is useful beyond this feature (debugging observer drift generally).

### k2b-investment compatibility

Building section auto-syncs from concepts/index.md by the deterministic script. When investment features enter In Progress or Next Up, the script picks them up on next sync (observer cycle or Keith running /ship). `/research videos` sees investment context, Gemini extracts with investment motivation awareness. No special handling needed.

## Ship Sequence (revised)

### Ship 1 (MVP) -- self-contained, no Ship 2 dependency

Delivers:
- `scripts/motivations-helper.sh` (add-question, remove-question, sync-building, read)
- `wiki/context/active-motivations.md` with Building seeded from deterministic sync script (reflects real In Progress + Next Up lanes only)
- `wiki/context/active-questions.md` with empty Questions section
- `.claude/skills/k2b-research/SKILL.md` updated: motivation injection in Step 5 (revised prompt preserving baseline coverage), motivations loaded in Step 6b, `why_k2b` enrichment in Step 6d
- `CLAUDE.md` routing rule pointing to helper script
- `K2B_MOTIVATIONS_ENABLED` env var support
- Verification: coverage diff test (compare what_it_covers length across overlap vs non-overlap videos in single run)

### Ship 2 -- observer auto-maintenance

Delivers:
- Extended observer-runs.jsonl logging (user_msg field)
- observer-loop.sh runs `motivations-helper.sh sync-building` each cycle
- observer-prompt.md: Emerging Interests detection from session signals (not concepts/index.md)
- observer-loop.sh processes emerging_interests[] with first_seen/last_seen/evidence_count/decay

### Ship 3 -- wider integration

Delivers:
- /research videos zero-query mode (proposes queries from motivations)
- /compile reads motivations for prioritization
- /daily surfaces relevant motivations in Focus section

## Risks (revised)

| Risk | Mitigation |
|------|-----------|
| NBLM prompt reduces baseline coverage for non-overlap videos | Revised prompt explicitly requires baseline; Ship 1 verification diffs coverage; rollback via env var |
| Syncthing cross-device race | Structural: split into two files with single writers each |
| Concepts/index.md table parsing brittleness | Replaced MiniMax parsing with deterministic bash script |
| Active Questions pile up | Keith edits Obsidian directly; future: add last-mentioned timestamp for manual review |
| parse-nblm.py drops motivation_overlap | Verified: dict entries preserve all fields via `dict(nblm_entry)` copy on line 165 |
| Helper script failure mid-write | Atomic tmp+mv + flock; helper exits non-zero on lock contention; caller handles |
| Observer can't detect Keith's promotion of Emerging -> Active Questions | Marker file `.motivations-promoted` consumed on next observer cycle |
