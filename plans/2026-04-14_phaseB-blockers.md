# Phase B Blockers -- 2026-04-14

## Blocker 1: NotebookLM deep research does not surface YouTube videos (B7) -- RESOLVED 2026-04-14

### Resolution

Keith approved Option B with two refinements: (1) drop `add-research --mode deep` entirely, not alongside yt-search; (2) use `yt-search.py --count 25 --months 1` for higher recall on fresh candidates; (3) tighten the filter prompt opening line from "list every YouTube video" to "Each source in this notebook is a YouTube video. Classify each one." Skill patch committed as `1965895`.

Re-ran B7 end-to-end against the same query `"AI agents for corporate workflows 2026"`:

- yt-search.py returned 25 fresh candidates in <3s (~101 quota units used).
- `notebooklm source add` for each URL → 25 source IDs captured. First bulk loop had spurious failures (cause unclear -- possibly rate-limit or background-task termination); inline retry with per-source raw capture produced 25/25 clean adds on the second pass. **Follow-up: add a per-call retry with 2s backoff to the skill's bulk add loop before scheduling the weekly run** -- the weekly version won't have a human re-runner.
- Parallel `source wait --timeout 600` across all 25 → all `status=ready`, no failures.
- Filter prompt via `notebooklm ask --json` → 5.7MB response, `.answer` contained a clean JSON array of 25 classifications (citation markers `[1, 2]` stripped with a regex before parse). 20 suitable, 5 rejected (Hindi tutorial, shallow "7 steps" listicle, labs video, Simplilearn, Programming Hobby generic guide). Rejections were reasonable per the baked framing.
- Gemini used synthetic `v=1..v=25` URLs in its JSON response -- the filter doesn't see real URLs, only source indices. Rejoin to real URLs done by matching on normalized title, 25/25 matched cleanly.
- `scripts/yt-playlist-add.sh` → 20/20 adds to `PLg0PUkz5itjwIXWVuSlvxud0ZR2JBsacX`.
- 20 review notes dropped into `K2B-Vault/review/video_2026-04-14_*.md` with correct frontmatter.
- Run record: `K2B-Vault/raw/research/2026-04-14_videos_ai-agents-corporate-workflows-2026.md`.
- Telegram notification: batched 20 videos into 4 messages of 5. **Batches 1, 3, 4 initially returned HTTP 400** because `send-telegram.sh` defaulted to `parse_mode=Markdown` and Telegram's legacy Markdown parser chokes on underscores in channel names like "Nate Herk | AI Automation". Fixed by dropping `parse_mode=Markdown` entirely (commit `c78c687`) -- plain text is the reliable default. Resent batches 1/3/4, all rc=0.
- Notebook deleted: `5a3adfb1-5861-4507-bb1e-9c70bc389199`.
- Skill usage logged.

### Diff that fixed it

```
.claude/skills/k2b-research/SKILL.md  | 23 +++++++++++++++++++----
scripts/send-telegram.sh              |  1 -
```

Plus new run record + 20 review notes in the vault (Syncthing, not git).

### Unresolved follow-ups (not blockers)

1. **Filter URL rewriting.** Gemini returns `v=1..N` in the `url` field instead of real URLs. Skill currently relies on title-matching back to `candidates.json`. Works, but if the filter ever reorders items or omits entries, title-matching could miss. Add a defensive fallback: if any filter item can't be title-matched, log to run record + fall back to positional index.
2. **Bulk add retry.** The first 25-URL bulk loop had silent failures (background task terminated early). Second attempt was clean. Add a per-URL retry with 2s backoff in the skill's loop before B11's weekly schedule goes live -- this runs unattended.
3. **20 suitable is above the 3-10 target.** Filter's "when in doubt, keep it" bias produces high recall. Consider tightening to "suitable=true only if you would bet money Keith will finish the video." Minor, can observe for 2-3 runs first.

### State before B8

Skill, script, and one retro doc commit will land on main. Four commits since Phase A: `fee5317`, `8224217`, `f1be7ed`, `e49c1e6`, `1965895`, `c78c687`. Review notes exist for B8 to target.

---

## Original blocker report (preserved for audit)

## Blocker 1 (original): NotebookLM deep research does not surface YouTube videos (B7)

### What happened

Executed Task B7 of `plans/2026-04-13_retire-youtube-and-build-research-videos.md` against the plan's test query: `"AI agents for corporate workflows 2026"`.

1. `notebooklm create "Videos: AI agents for corporate workflows 2026"` → notebook `ea2017fc-90da-4cb9-be3f-5a1fb5a3dc4b` (cleaned up).
2. `notebooklm source add-research "AI agents for corporate workflows 2026" --mode deep` → research task `ed004127-c257-4c26-9017-b08f4e0b90be`, completed successfully.
3. `notebooklm research wait` returned 66 sources: **65 web articles + 1 synthesis report**. Zero YouTube videos.
4. `notebooklm source list` returned empty `sources: []` (deep-research sources live in the research report, not as indexable notebook sources).
5. Grepped the 41,711-char synthesis report_markdown for YouTube URLs → zero matches.

### Why this blocks the plan

The plan's Step 5 asks NotebookLM: *"From the sources in this notebook, list every YouTube video and classify it."* With zero YouTube sources in the notebook, this returns an empty JSON array. Steps 7–10 (playlist adds, review notes, Telegram notification) have nothing to act on, and the feedback loop (B9, B10) has no way to close because there's no video to rate.

This isn't a query problem -- Deloitte Insights, Kaiwäehner, Gartner-style blog posts, and vendor whitepapers are exactly what you'd expect a research query about corporate agentic AI to surface. The deep-research mode appears to prioritize long-form text analysis over video content, which is the opposite of what `/research videos` needs.

### The design gap

The plan assumed `notebooklm source add-research --mode deep` would surface YouTube videos as part of its source gathering. Empirically, it does not (at least not for this query). The plan's flow treats NotebookLM as both the **discovery engine** and the **filter**. In practice, only the filter half works -- NotebookLM is good at classifying sources you hand it, not good at finding YouTube videos from a query.

### Proposed fix (Option B -- recommended)

Split discovery from filtering. Use `scripts/yt-search.py` (already kept in Phase A) for candidate discovery, then use NotebookLM only as the filter with explicit YouTube URLs added as sources.

**Revised flow:**

1. `python3 scripts/yt-search.py "<query>" --count 20 --months 6` → 20 candidate video URLs, titles, channels, durations, view counts. Cost: ~101 YouTube API quota units out of 10k/day.
2. `notebooklm create "Videos: <query>"`
3. For each candidate URL: `notebooklm source add "<youtube-url>" -n "$NB_ID"`. notebooklm-py supports YouTube URLs natively and pulls the transcript as the source.
4. `notebooklm source wait` (all sources indexed).
5. Read the preference tail (unchanged).
6. Run the existing JSON filter prompt (unchanged -- the prompt itself is good, it just needs real YouTube sources in the notebook).
7. Parse JSON, playlist adds, review notes, run record, Telegram notification, notebook delete (all unchanged).

**Pros:**
- Eliminates the `add-research --mode deep` dependency entirely (which also saves 5–15 min wall-clock per run).
- yt-search gives us channel + duration + publish date up front, so we can pre-filter obviously-bad candidates (e.g., under 3 minutes) before spending NotebookLM credit on them.
- Matches Keith's stated model: *"NotebookLM reads full video content via Gemini at zero token cost, filters by Keith's stated preferences."* NotebookLM was always the filter, never the discovery engine.

**Cons:**
- Requires a small edit to `.claude/skills/k2b-research/SKILL.md` replacing Steps 1–3 of the current flow with the yt-search + source-add sequence.
- yt-search.py is YouTube-only, so the "discovery" surface shrinks to what YouTube Search API returns (no Reddit threads, no blog-linked videos). For this use case that's fine -- we want videos.

### Option A (simpler, less good)

Skip `--mode deep` and use `--mode research` (or plain source add on the query). This is closer to the plan's current shape but relies on NotebookLM's lighter research mode surfacing videos, which I haven't tested. Gamble.

### Option C (scope change)

Reframe the feature as *"NotebookLM finds articles on a query, filters them by Keith's framing, notifies Keith."* Drops YouTube entirely. This is a different feature than what Keith brainstormed and the name `/research videos` no longer fits. Not recommended without a re-brainstorm.

### My recommendation

Go with **Option B**. It's a ~20-minute skill edit, preserves the entire feedback loop design (baked framing, preference tail, review notes, Telegram, `/review` distillation), and deletes a brittle assumption. I need Keith's approval before touching the skill -- this is a plan deviation, and per the feedback memory at `feedback_subagent_vs_main_session.md` and the stop condition in the execution prompt, plan deviations stop and ask.

### State at time of stop

- Phase B commits through B6 are on `main` (`fee5317`, `8224217`, `f1be7ed`, `e49c1e6`) and pushed.
- `K2B-Vault/wiki/context/video-preferences.md` seeded (vault-only, Syncthing).
- Test notebook `ea2017fc-90da-4cb9-be3f-5a1fb5a3dc4b` deleted.
- `notebooklm auth check --test`: all green (17 cookies, SID + token fetch pass).
- Nothing deployed to Mac Mini yet (B13 hasn't run).
- `/research videos` skill section as committed will not work end-to-end until this blocker is resolved.

### Next move

Waiting on Keith:
1. Approve Option B and let me rewrite the `/research videos` flow section of `k2b-research/SKILL.md`, then re-run B7, or
2. Pick a different option, or
3. Defer the live test entirely, ship the not-yet-working skill with a known-broken flag, and let a future session fix it.
