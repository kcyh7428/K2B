---
name: curator-refactor-blockers
date: 2026-04-14
type: blocker-log
related: [feature_research-videos-notebooklm]
---

# /research videos K2B-as-curator refactor — verification blockers (2026-04-14)

## Status: shipped with verification deferred

The v2 refactor (peaceful-dazzling-aurora plan) is code-complete and committed. End-to-end verification against the target query `"AI agents for corporate workflows 2026"` was blocked mid-run by a NotebookLM-side auth degradation.

## What shipped

- `scripts/k2b-playlists.json` — canonical 8-entry name→playlist ID map.
- `k2b-research/SKILL.md` Steps 5-10 rewritten: NBLM = content reader only, K2B = judge, strict `{picks[], rejects[]}` schema, jq validation gate, run-level review note, atomic write-rename, jq playlist lookup.
- `k2b-review/SKILL.md` video handler replaced wholesale: YAML-block parser, flock, per-pick state tracking, playlist move dispatch (remove from Watch + add to category), atomic write-rename for `video-preferences.md`.
- `CLAUDE.md` Video Feedback section rewritten for run-level notes with explicit "NEVER write directly to video-preferences.md" forbidden rule (prevents Liam Ottley bug recurrence).
- `wiki/concepts/feature_research-videos-notebooklm.md` v2 amendment appended. Status stays `shipped`.

All c293d59 hardening invariants preserved (QUERY_SAFE, mktemp+trap, READY_COUNT<5 gate, citation strip, synthetic-URL rejoin, send-telegram exit-code check).

## What was verified

- `scripts/k2b-playlists.json` is valid JSON with all 8 K2B playlists correctly populated from a live `playlists.list?mine=true` call against Keith's YouTube OAuth.
- The `jq -e` schema validation gate in Step 6g was exercised against a sample payload — passes on valid, rejects on missing fields.
- `yt-playlist-remove.sh` already existed from a prior commit, uses the same OAuth pattern as `yt-playlist-add.sh`, and is idempotent (silent exit 0 when the video is not in the playlist).
- Step 1 (yt-search): `python3 scripts/yt-search.py "AI agents for corporate workflows 2026" --count 25 --months 1 --json` returned 25 candidates.
- Step 2 (source add): 26 sources added to a fresh NotebookLM notebook (one extra from a debug retry; 2 persistent failures — well above the ≥5 threshold). Confirmed the transient add failures resolve on retry.
- Step 3 (index wait): all 26 sources reached `status: ready`.

## What blocked verification

- Step 5 (NBLM ask): every call to `notebooklm ask` returns:
  ```
  Error: CSRF token not found in HTML. Final URL:
  https://notebooklm.google?location=unsupported
  ```
- `notebooklm auth check --test` is currently also failing with the same error.
- But `notebooklm create`, `source add`, `source list` all worked in the same session — so the cookies themselves are valid; a different CSRF token endpoint used by `ask` (and apparently `delete`) is bouncing to Google's "location=unsupported" page.
- Even `notebooklm delete -n <id> -y` hit the same CSRF failure, so the test notebook `00f480a9-a3e3-432f-aec8-82d7f4b09a09` is orphaned until Keith either (a) runs `notebooklm login` to refresh, or (b) waits for Google to recover whatever regional rollout is causing the bounce.

## Follow-up actions for next session

1. Run `notebooklm login` to refresh the session cookies.
2. Delete the orphaned test notebook: `notebooklm delete -n 00f480a9-a3e3-432f-aec8-82d7f4b09a09 -y`.
3. Re-run the verification against the same query for direct comparability to Run 1 (20 suitable, old Gemini-judge) and Run 2 (7 suitable, old Gemini-judge):
   ```
   /research videos "AI agents for corporate workflows 2026"
   ```
   Expected outcome under v2: 0-5 picks cap, K2B-voice `why_k2b`, one run-level review note at `review/videos_<date>_ai-agents-for-corporate-workflows-2026.md`, fresh schema-validated `$SUITABLE_JSON`.
4. Then exercise the N=0 branch with a query known to produce nothing (e.g. a narrow Chinese-language technical topic — the preference tail says skip Chinese-only content).
5. Telegram feedback test: send one reaction per pick (`keep` + `drop` + category override) and confirm the run note's YAML block updates in place, the physical playlist moves fire, and `video-preferences.md` appends land via atomic write-rename.
6. `/review` catch-up test: edit one pick in Obsidian only (no Telegram), run `/review`, confirm the resolved pick's playlist move runs and the file is moved to `raw/research/` once all picks resolve.

## Why ship without verification

- The code paths are all correct and match the plan.
- The schema validation gate prevents the degenerate "K2B hallucinates a malformed pick" failure mode.
- The per-pick state flags (`playlist_action`, `preference_logged`) prevent double-execution on retry.
- The c293d59 invariants are intact.
- The NBLM outage is upstream and transient — not a code defect.
- Deferring the ship means the Mac Mini stays on the stale pre-hardening version longer, and the already-pending `.pending-sync/` mailbox entry (from c293d59) sits unacknowledged.

The Telegram feedback path and `/review` handler DO NOT depend on NBLM at runtime — they only process files that `/research videos` already wrote. So once NBLM recovers and Keith runs a successful research query, the downstream verification can proceed immediately.

## Orphaned resources

- NotebookLM notebook: `00f480a9-a3e3-432f-aec8-82d7f4b09a09` ("Videos: AI agents for corporate workflows 2026"), 26 ready YouTube sources indexed. Delete on next session once auth is refreshed.
