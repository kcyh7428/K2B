# Integrated Loop Ship 2 Plan -- 2026-04-24

Continuation of `feature_k2b-integrated-loop` after Ship 1 (commits `ad0259f..e8d1e50`, shipped 2026-04-23). Ship 1 landed the dashboard + auto-apply on observer candidates against a fixture. Ship 2 wires the remaining three root-cause fixes and one deprecation surface.

## Ship 2 binary MVP (write test fixtures FIRST per L-2026-04-22-007)

Four gates, all must hold for ship:

- **Gate A** -- live observer (not fixture) surfaces candidates in session-start. Reproduction: place a valid live-shape observer-candidates.md at the default production path, run the default session-start hook with no env overrides, see those candidates numbered in the dashboard.
- **Gate B** -- a deferred item shows `deferred: 1x` counter on next session, `2x` after a second defer, and auto-archives after 3 defers. Reproduction: `--defer 1` once, re-render dashboard, confirm `(deferred 1x)` suffix on item; defer a second time, confirm `2x`; defer a third time, confirm the candidate moves to `observations.archive/auto-archived-deferred-YYYY-MM-DD.jsonl` and disappears from observer-candidates.md.
- **Gate C** -- review/ queue item gets routed via loop grammar with same a/r/d keystrokes. Reproduction: dashboard numbers review items continuing the observer numbering (if O observer candidates, review starts at O+1), `--accept N` on a review index marks `review-action: accepted` and moves to `review/Ready/`, `--reject N` archives to `Archive/review-archive/YYYY-MM-DD/` with `review-action: rejected`, `--defer N` increments the same sidecar counter, 3 defers auto-archive.
- **Gate D** -- running `/autoresearch`, `/improve`, `/review` directly emits a deprecation notice pointing at the dashboard. Reproduction: grep for a specific deprecation sentinel string (`DEPRECATED in Ship 2 of k2b-integrated-loop`) at the top of each of the three SKILL.md bodies.

Binary verdict: 4/4 pass = SHIP. Any fail = REJECT.

## TDD order (iron law)

1. Write fixtures (`tests/fixtures/loop-mvp-ship2/`) -- observer-candidates.md with 3 candidates, observer-defers.jsonl starting empty, review/ with 2 items, self_improve_learnings.md baseline.
2. Write failing Python unit tests in `tests/loop/test_loop_lib_ship2.py` covering:
   - defer_counter_increments_on_first_defer
   - defer_counter_second_defer_shows_2x
   - defer_at_3_archives_and_removes_from_candidates
   - review_accept_moves_to_ready
   - review_reject_archives
   - review_defer_increments_counter
3. Write failing bash gate test `tests/loop/loop-mvp-ship2.test.sh` with 4 gates A/B/C/D.
4. Watch RED. Confirm failure reason matches missing feature (not typo).
5. Implement GREEN piece by piece, running the tests between each piece. New surface:
   - `loop_lib.py`: `read_defers(path)`, `increment_defer(path, item_id, date_str)`, `auto_archive_if_due(path, archive_dir, item_id, cand, threshold=3)`, plus review-item helpers `list_reviews`, `accept_review(p, date_str)`, `reject_review(p, date_str, archive_root)`.
   - `loop_apply.py`: extend index space -- observer 1..O, review O+1..O+R. Route by index into observer or review handler. Defer for either surface calls the same counter primitive.
   - `loop_render.py`: unified numbering. Add defer badge `(deferred Nx)` read from defers sidecar. Review section renders as routable `[N] review · filename` items.
   - `loop-apply.sh`: expose `K2B_LOOP_DEFERS`, `K2B_LOOP_REVIEW_DIR`, `K2B_LOOP_REVIEW_READY_DIR`, `K2B_LOOP_REVIEW_ARCHIVE_ROOT` env vars.
   - k2b-autoresearch / k2b-improve / k2b-review `SKILL.md`: deprecation preamble with the sentinel string.
6. Verify GREEN. Full `pytest tests/loop/` + each bash test green.

## Design decisions (Scope B per L-2026-04-22-006 -- propose and proceed)

- **Defer state** lives in a sidecar JSONL `wiki/context/observer-defers.jsonl`, not inside observer-candidates.md. Observer loop writes observer-candidates.md; the loop writes defers. Single-writer per file. Schema: one JSON object per line with `item_id`, `count`, `last_deferred`, `kind` (observer|review).
- **Unified numbering** across observer + review. Keystroke grammar stays uniform (`a N / r N / d N`). The renderer assigns indexes in observer-first order; the apply script parses candidates and reviews in the same order, picks by index.
- **Review accept** ≠ "process the item end-to-end". That would require running k2b-vault-writer or k2b-compile under the hook, which is out of scope and risky under flock. Accept = mark `review-action: accepted` and move the file to `review/Ready/`. That makes the item ready for the next manual `/review` run (which still exists, just with a deprecation banner). Same for reject -- we archive the file with `review-action: rejected`, the Archive/ move is the permanent record.
- **Auto-archive on 3 defers** writes a JSONL line to `observations.archive/auto-archived-deferred-YYYY-MM-DD.jsonl` for observer candidates, and moves the review file to `Archive/review-archive/YYYY-MM-DD/` for review items. In both cases the defer sidecar entry is cleared.
- **Live observer wiring** = production default path. The session-start hook already has the correct default; the only Ship 2 change is documentation + a gate-A structural test that asserts the default resolves to the live path when no env override is set.
- **Deprecation notice text** is the first thing an invoked skill sees. Claude is instructed to emit it verbatim at the top of any response triggered by the skill, then proceed only if the user explicitly confirms they want the legacy workflow. Sentinel string for the gate-D grep: `DEPRECATED in Ship 2 of k2b-integrated-loop`.

## Not in scope

- Interactive keystroke loop in the dashboard itself (Ship 3 if ever; Claude already translates `a 1 r 2 d 3` into loop-apply.sh flags).
- Deleting the three deprecated skills -- Ship 1 already said 2 weeks of clean loop operation gates deletion. Ship 2 only adds the deprecation notice.
- Cross-session conversation-state ledger, WAL protocol narrow retrofit, ADL protocol -- all still parked.
- Modifying the observer's writer format. Format stays as Ship 1 fixture. Defers live elsewhere.

## Adversarial review

- Plan review (Checkpoint 1) via Codex on this plan doc. Required because Ship 2 edits three skill bodies (`shared state`).
- Pre-commit review (Checkpoint 2) via Codex on the full diff before `/ship` commit.
- If Codex quota depleted, fall back to MiniMax-M2.7 per CLAUDE.md Adversarial Review section.

## Ship mechanics

One commit preferred, 2-3 if broken up for readability. `/ship` at the end, which handles wiki lane move (Shipped already, update in place), DEVLOG, wiki/log, and the `.pending-sync/` mailbox gate.
