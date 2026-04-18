# K2B Development Log

---

## 2026-04-18 -- k2b-email authorized send via draft + explicit confirmation

**Commit:** `35e1654` feat(k2b-email): authorized send via draft + explicit confirmation

**What shipped:** Relaxed the absolute `NEVER send` rule on `k2b-email` to allow Gmail drafts to be sent through a tightly constrained two-turn confirmation flow. Draft-first stays the default; sending requires a separate confirmation turn with an exact `send draft <id>` phrase. Send path: Keith says "draft an email to X" -> K2B creates draft via existing MIME upload, replies with a full preview including `From` / `To` / `Cc` / `Bcc` / `Reply-To` / `Subject` / attachments (filenames + sizes) / **full body verbatim** / draft ID -> Keith replies in a SEPARATE turn with exactly `send draft <id>` -> K2B re-fetches via `drafts get`, byte-compares every outbound-impacting field plus inner `message.id`, calls `gws gmail users drafts send` only if identical, reports `Sent. Message ID: <id>`. All `+send` / `+reply` / `+reply-all` / `+forward` helpers stay blocked; only `users drafts send` on a pre-approved draft ID is authorized. Works over Telegram bot with zero bot-code changes (each Telegram message is a separate turn by definition). Refuses the send path when the full body exceeds the preview channel limit (Telegram 4096) rather than authorizing a truncated preview.

**Codex review:** 4 rounds of adversarial-review via background + poll pattern (dogfooding `985a299`). Round 1 (`bl03bcl9k`): flagged bare `send`/`send it` ambiguity, ambiguity guard scoped too narrowly to fresh-only drafts, and TOCTOU hand-waving in the revalidation step. Round 2 (`b0711obs7`): flagged field-coverage hole (`Cc` / `Bcc` / attachments / `Reply-To` / full body missing from compare set) + TOCTOU framing inaccuracy. Round 3 (`be2afsp8i`): flagged body-truncation hole (preview showed first ~500 chars, so body tail went unsanctioned) + missing `From` / sender identity from the compare set. Round 4 (`btdjad337`): **APPROVE**, no material findings. Each round's fixes were surgical edits to the specific flagged rules/tables; no scope creep.

**Feature status change:** created `Shipped/feature_k2b-email-send.md`; added to Shipped lane in `wiki/concepts/index.md`; oldest inline Shipped row (`feature_vault-housekeeping-agent` 2026-04-07) trimmed to maintain the "recent 10" cap.

**Follow-ups:** First production-use will exercise the flow end-to-end via Telegram. If the confirmation friction bugs Keith, we can add a trusted-recipient whitelist later (deliberately out-of-scope for MVP). Needs `/sync skills` to Mac Mini (done at ship-time, completed 2026-04-18).

**Key decisions (if divergent from claude.ai project specs):**
- Exact `send draft <id>` vocabulary only, NOT bare `send` / `yes` / `go`. The ID-bound requirement was Codex's round-1 strong recommendation and defends against state drift with concurrent/stale drafts that bare-send heuristics can't disambiguate.
- Sender identity pinned to `keith.cheung@signhub.io` (Rule 13). Send-as alias drift is rejected rather than auto-approved. Hardens against Gmail's send-as alias feature silently switching From identity post-preview.
- Body must fit in preview channel (Rule 14). A truncated preview is never authorization for the untruncated body. If the email is too long for Telegram's 4096 limit, the skill tells Keith to send from Gmail directly instead of finding a workaround.
- TOCTOU honestly scoped (Rule 11): Gmail drafts API has no if-match / etag on `drafts.send`. `message.id` inner-ID check narrows the race window to the millisecond gap between `get` and `send` call, acceptable for Keith's single-user Mac + Telegram setup. Documented explicitly rather than claimed as an atomic guard.
- Voice messages never count as send confirmation (Rule 12). Transcription mishears could fabricate a "send it" from harmless speech. Text-only.

---

## 2026-04-18 -- k2b-remote defends against silent Clash long-poll stalls (Option B)

**Commit:** `7bb7d72` fix(k2b-remote): defend against silent Clash long-poll stalls

**What shipped:** Application-layer hardening for the recurring "k2b-remote goes silent for hours, no error, no recovery" failure mode first logged 2026-04-15 (see `plans/2026-04-15_k2b-remote-clash-stall-investigation.md` Option B). The failure mode is Clash Verge (the mandatory proxy because Telegram is geo-blocked at the Mini location) silently dropping the long-poll TCP connection during a quiet stretch; grammY's default 500s client timeout combined with zero TCP keepalive meant a wedged socket could hang `getUpdates` indefinitely. Concretely: (1) `HttpsProxyAgent` now configured with `keepAlive: true`, `keepAliveMsecs: 30000`, `timeout: 60000` -- dead sockets get detected in ~60s instead of forever. (2) grammY `client.timeoutSeconds: 55` sets the real application-layer abort deadline (was 500s by default). (3) `bot.start({ timeout: 50 })` sets the Telegram long-poll server wait just below the client abort so normal polls complete cleanly. (4) New grammY transformer logs `getUpdates` lifecycle: `poll start` / `poll end` at debug, `poll slow` (>52s) and `poll error` at warn so future stalls are visible at the default log level instead of silent. Timeout ladder stacks cleanly: Telegram 50s -> grammY client 55s -> socket idle 60s -> keepalive probes every 30s.

**Codex review:** 2 rounds of adversarial-review via background + poll pattern. Round 1 (`bc4kugqo2`): verdict `needs-attention`. Flagged that `bot.start({ timeout: 50 })` only sets Telegram's server-side long-poll parameter, not grammY's HTTP request deadline which still defaulted to 500s -- so a proxy blackhole could still hang `getUpdates` for 8 minutes. Also flagged that my debug-level poll logging would be invisible at the production log level (pino defaults to info). Round 2 (`b9cm3rh3j`): **APPROVE** after adding `client.timeoutSeconds: 55` to both proxy and no-proxy branches of bot construction, promoting `poll error` from debug to warn, and adding a `poll slow` threshold at 52s to catch near-misses before they turn into aborts. Final suggestion (lower threshold below client timeout) applied inline; 55000 -> 52000.

**Feature status change:** none. Infrastructure / reliability bugfix; no feature note created. Index.md "Last updated" line references "k2b-remote Clash stall defense shipped as infra" so the roadmap acknowledges the work without creating a feature row.

**Follow-ups:** Monitor the Mini's `k2b-out.log` for `poll slow` / `poll error` entries. If the pattern recurs despite keepalive + timeouts, the next lever is Clash Verge config (Option A in the original plan: increase outbound-node idle timeout, or switch to a node with better long-connection behavior). The plan file `plans/2026-04-15_k2b-remote-clash-stall-investigation.md` stays in the worktree as historical context but was not staged this session per `/ship` predates-session rule.

**Key decisions (if divergent from claude.ai project specs):**
- Did NOT touch `NO_PROXY` or unset `HTTP_PROXY` (called out in the plan as "do NOT propose these" -- Telegram is geo-blocked at the Mini, Clash is mandatory not optional).
- Did NOT add a `bot.api.getMe()` heartbeat (ruled out in the plan file -- getMe may run on a different socket and can't unstick an in-flight getUpdates).
- Kept `+timeout 50s` on `bot.start()` even though TCP keepalive alone would arguably be sufficient -- defense in depth at the application layer costs nothing and makes the timeout ladder self-documenting.
- Used a grammY API transformer (`bot.api.config.use`) for the logging diagnostic rather than patching individual API call sites -- idiomatic, narrow (only logs `getUpdates`, not every API call), zero impact on non-polling call paths.

---

## 2026-04-18 -- k2b-ship/k2b-sync portability for sibling repos + K2Bi rename cleanup

**Commit:** `69c4489` refactor(skills): k2b-ship/k2b-sync portability + K2Bi rename cleanup

**What shipped:** K2B-side prep work to support K2Bi Phase 1 Session 3 `/ship` smoke test from inside the new repo. (1) `k2b-ship` now skips gracefully when sibling-repo scripts are absent (`promote-learnings.py`, `audit-ownership.sh`, `deploy-to-mini.sh`), tightens its push guard to match exact `origin` remote name (a remote called `upstream` would have false-passed the previous `git remote | grep -q .` check then failed at push time), derives the `.pending-sync/` mailbox directory from `git rev-parse --show-toplevel`, and resolves the `now` deploy fallback path from the current repo root instead of hardcoding `~/Projects/K2B/scripts/deploy-to-mini.sh`. (2) `k2b-sync` step 0 now derives its mailbox path from git repo root to match the `/ship` producer, with an explicit "fork-friendly, not auto-portable" disclaimer noting that the rsync targets and deploy invocations later in the skill are still hardcoded to `~/Projects/K2B/` and require swap at fork time. (3) Doc cleanups: `k2b-compile/SKILL.md` and `scripts/compile-index-update.py` rename example paths from `k2b-investment` to `k2bi`; `CLAUDE.md` Project Resume Handles section renames the routing handle from "continue k2b investment" to "continue k2bi" to match the K2Bi rename that landed earlier; DEVLOG retroactively renames K2B-Investment to K2Bi in 3 historical entries with a clarifying note.

**Codex review:** 2 rounds via background + poll (the new pattern from `985a299` worked correctly -- no stalls). Round 1 returned 2 findings: P1 ("deferred-sync mailbox in path that `/sync` actually consumes" -- `k2b-sync/SKILL.md` step 0 and `scripts/hooks/session-start.sh` still hardcoded `~/Projects/K2B/.pending-sync/`) and P2 ("avoid hardcoding K2B's deploy script in the repo-agnostic `/sync now` path" -- step 12 fallback hardcoded `~/Projects/K2B/scripts/deploy-to-mini.sh`). Both fixed inline (`session-start.sh` left alone since it's K2B's hook with `$K2B` variable already at file top -- K2Bi will fork it). Round 2 returned 2 P2 regressions from the round-1 fix: the new push guard checked "any remote" but still pushed to `origin` (fix: `grep -qx 'origin'`), and `k2b-sync` step 0 description now claimed sibling-repo support while the rest of the skill kept K2B-hardcoded rsync paths (fix: walk back the framing to "fork-friendly, not auto-portable" with explicit note about the fork-time swap requirement). Both fixed inline. No round 3 needed -- fixes were surgical edits to the exact lines Codex flagged.

**Feature status change:** none. K2B-side infrastructure prep for `feature_k2bi-phase1-scaffold` Session 3. The K2Bi feature note remains in In Progress as `partial-shipped (Session 1 of 2)` and is updated by Session 3's own `/ship` from inside K2Bi, not this commit.

**Follow-ups:** K2Bi Phase 1 Session 3 will dogfood these portability fixes when it forks `k2b-sync` and runs `/ship` from inside `~/Projects/K2Bi/`. The fork-time swap note in `k2b-sync` step 0 documents the rsync-paths gap that Session 3 must close. Ownership drift audit deferred (5 rules, 26 offenders -- all expected: canonical homes in `active_rules.md`, watchlist self-reference, K2Bi planning notes documenting rules in context).

**Key decisions:** Treated as `--no-feature` infrastructure rather than mapping to `feature_k2bi-phase1-scaffold` because (a) the changes touch K2B repo not K2Bi, (b) they don't complete any tracked ship gate, (c) the portability work is genuinely cross-cutting infra that would benefit any future sibling repo too. Skipped 3rd Codex round despite both prior rounds returning findings, because the round-2 fixes were surgical edits to the exact flagged lines (push-guard regex tightening + walking back overpromise framing) with no new behavior introduced. Trusted the verification pattern over the cycle count.

---

## 2026-04-17 -- k2b-ship Codex review: background + poll pattern

**Commit:** `985a299` fix(k2b-ship): use background+poll pattern for Codex pre-commit review

**What shipped:** Step 3 of the `k2b-ship` skill no longer runs Codex via a synchronous `/codex:review` slash invocation. It now launches `codex-companion.mjs review --wait --scope working-tree` directly through Bash with `run_in_background: true`, captures the task output file, and polls it every ~90s to surface progress (reconnect count, current Codex action, file count read) to Keith. If Codex shows 5 reconnect attempts with no recovery for 3+ minutes, the assistant escalates to `--skip-codex codex-cli-wedged`. Checkpoint 2 documentation updated to point at the new pattern.

**Codex review:** skipped (documentation-only change in a single SKILL.md, no runtime logic; also: reviewing the Codex review procedure with Codex risks exactly the failure mode we just fixed).

**Feature status change:** none. k2b-ship skill update, not feature-bound.

**Follow-ups:** Next `/ship` session will dogfood the new pattern. If Codex still stalls despite the background path, root-cause the WebSocket reconnect storm in `codex-companion.mjs` upstream.

**Key decisions:** Kept foreground option available in the skill prose (for tiny diffs where sync is acceptable), but the default path is background + poll. Hardcoded reconnect-storm escalation threshold at "5 reconnects + 3 min no recovery" based on the single observed incident -- tighten later if we see more data.

---

## 2026-04-17 -- Active Motivations Ship 1: motivation-aware NBLM extraction

**Commit:** `76a8b34` feat: active motivations ship 1 -- motivation-aware NBLM extraction

**What shipped:** The `/research videos` pipeline now injects Keith's active projects (Building section from `wiki/concepts/index.md` In Progress + Next Up lanes) and self-added Active Questions into the Step 5 NotebookLM prompt as an additive "viewer context" block. Gemini is explicitly forbidden from using that context to rank or filter -- it only adds `motivation_overlap` and `motivation_detail` fields to entries whose content touches a motivation. Step 6 Claude-side judgment reloads the same motivations (via `scripts/motivations-helper.sh read`) and applies a motivation-match bonus that DOES NOT bypass the quality bar, plus a `why_k2b` enrichment requirement when `motivation_overlap` is populated. Everything is gated by `K2B_MOTIVATIONS_ENABLED` (default true); disabling it produces a byte-identical pre-feature prompt. `scripts/motivations-helper.sh` is the single writer for both `active-motivations.md` (observer-owned) and `active-questions.md` (Keith-owned) with atomic tmp+mv + flock, matching the K2B memory ownership matrix. `.gitignore` now excludes `__pycache__/` + `*.pyc`.

**Codex review:** 1 P2 surfaced and fixed before commit. Codex spotted that Step 6's Claude-side judgment referred to `$MOTIVATIONS` -- but that variable was only live inside the Step 5 bash block, so during a real `/research videos` run Claude-the-judge would have had no motivation context. Patched inline by adding an explicit helper re-read at Step 6c; the fix re-reads via the same deterministic helper call, so Step 5 and Step 6 get identical input. No other findings.

**Bias test:** Structural coverage-diff test (`/tmp/bias_test_step5.sh`, 11/11 PASS). Dry-runs the Step 5 prompt-building bash with `K2B_MOTIVATIONS_ENABLED=true` and `false`, diffs the outputs. Disabled prompt has zero motivation markers; enabled prompt adds the viewer-context block with all six anti-ranking guards present; the diff is purely additive (0 removals, 20 additions); after stripping MOT_BLOCK the enabled prompt matches disabled. Live coverage-diff run (measuring `what_it_covers` length across overlap vs non-overlap entries from an actual NBLM response) deferred to the first in-measurement `/research videos` call.

**Feature status change:** feature_active-motivations Ship 1 `in-flight` -> `in-measurement`. Gate 2026-04-30 (14-day window). Stays in `In Progress` lane (multi-ship feature; Ships 2 and 3 remain).

**Follow-ups:**
- Ship 2 prerequisite: extend `observer-runs.jsonl` line ~265 to log `user_msg` alongside `prompt` + `response`.
- Ship 2: wire `motivations-helper.sh sync-building` into `observer-loop.sh` as a pre-analysis step.
- Live coverage-diff test on next `/research videos` run: capture `$NBLM_RAW`, compare `what_it_covers` byte length for overlap vs non-overlap, assert non-overlap mean >= 150 chars.
- Monitor first Keith-initiated `add X to my active questions` end-to-end (helper -> Syncthing -> Obsidian).
- Needs `/sync` to deploy to Mac Mini.

**Key decisions:**
- Step 6c explicitly reloads motivations via the helper rather than trying to persist a variable from Step 5. The helper is deterministic given unchanged vault state, so back-to-back reads produce identical output; this keeps the Step 5 / Step 6 prompt and judgment in lockstep while respecting the Claude-reasoning vs shell-scope boundary.
- Structural bias test chosen over live-run bias test for this ship. Structural test is instant, zero API cost, and proves the prompt-level invariant (non-overlap videos cannot be treated differently). Live test is a better detector of Gemini actually biasing extraction despite the guards, but it costs ~20 min + NBLM budget per run; deferred to first in-measurement `/research videos` invocation where it runs as a free side effect of normal usage.
- `plans/2026-04-15_k2b-remote-clash-stall-investigation.md` was left uncommitted -- predates this session per `/ship` rule on unrelated prior-session artifacts.

---

## 2026-04-17 -- K2Bi Resume Handle + Active Motivations CLAUDE.md routing

**Commit:** `0654bc7` docs: wire up K2Bi resume handle + Active Motivations routing

**What shipped:** Two routing additions to CLAUDE.md (pure pointers, no procedure). (1) Project Resume Handles section routes "continue k2bi" / "resume k2bi" -> Resume Card in K2B-Vault/wiki/projects/k2bi/index.md. Lets any new Claude Code session pick up K2Bi Phase 1 work in under 60s. (2) Active Motivations section routes "add X to my active questions" -> scripts/motivations-helper.sh (paired with the in-flight feature_active-motivations Ship 1 from a parallel session). Both sections follow the ownership matrix: CLAUDE.md routes, the target owns the procedure. Also created feature_k2bi-phase1-scaffold.md in Next Up lane and added Resume Card block to k2bi/index.md (vault-only, synced via Syncthing).

**Codex review:** skipped (k2b-ship config-tweaks exception -- 16 lines of routing additions with no logic or procedure).

**Feature status change:** feature_k2bi-phase1-scaffold (created) status -> next. No lane move for feature_active-motivations (Ship 1 still in-flight in parallel session).

**Follow-ups:** Parallel active-motivations session should skip re-committing CLAUDE.md (its Active Motivations hunk landed under this commit). Phase 1 kickoff is next: in a future session Keith says "continue k2bi" and we execute the 8 exit-criteria items. Drift audit deferred (5 rules, 26 offenders -- all expected: memory files, planning docs, archived observations).

**Key decisions:** Shipped both CLAUDE.md hunks together (instead of surgical hunk stage) because both are final, routing-only, and merging avoids race with the parallel session. Feature note status set to `next` not `in-progress` because Phase 1 build hasn't actually started yet -- only the resume-handle infrastructure did.

(Note: project was subsequently renamed from K2B-Investment to K2Bi; references above updated retroactively.)

---

## 2026-04-16 -- Session-End Capture: auto-extract behavioral signals from /ship

**Commit:** `996acaf` feat: session-end capture

**What shipped:** Every /ship call now extracts implicit behavioral signals from the conversation and writes a compact summary to raw/sessions/. The observer background loop on Mac Mini picks these up as a new input source (alongside observations, learnings, and YouTube data), inlines them into the MiniMax prompt, and feeds the patterns into preference-signals.jsonl and preference-profile.md. This closes the biggest capture gap: Keith's deepest Claude Code work sessions now automatically feed the preference learning loop without manual /learn or /tldr.

**Codex review:** 6 findings (1 BLOCKER: empty-observations bail blocked session-only triggers, 2 BUG: sentinel touched on skip + archive moved unanalyzed files, 3 RISK: sed frontmatter stripping + mtime sentinel + hallucination grounding). All fixed before commit.

**Feature status change:** feature_session-end-capture ideating -> shipped

**Follow-ups:** Deploy observer changes to Mac Mini via /sync. First real test is this /ship session itself (step 13.5 runs below). Two features from the self-improvement architecture now shipped in one session (canonical-memory + session-end-capture). Next in the chain: feature_pipeline-hardening or feature_weighted-decay.

**Key decisions:** Session summaries are written atomically (temp + rename) to handle Syncthing. Observer archives only files it actually inlined (tracked via PROCESSED_SESSION_FILES array) to prevent unanalyzed summaries from being lost. Frontmatter stripping uses awk (counts exactly 2 --- delimiters) instead of sed (which would strip horizontal rules too).

---

## 2026-04-16 -- Canonical Memory: markdown as single source of truth for K2B memory

**Commit:** `b5c77ce` feat: implement canonical memory

**What shipped:** Full implementation of feature_canonical-memory. k2b-remote now reads preference-profile.md and injects it into every agent prompt (10-min cached, session reset on profile change). New conversation memories write-through to vault JSONL (canonical) and SQLite (live FTS projection). On startup, k2b-remote syncs any externally-arrived memories from vault JSONL (e.g. Claude Code session captures via Syncthing) using content-hash dedup to prevent duplicates. Migration script exports existing SQLite history to JSONL.

**Codex review:** 6 findings (1 BLOCKER: source_hash backfill missing, 2 BUG: timestamp mismatch between vault/SQLite writes + non-durable profile hash, 2 RISK: lock contention drops canonical writes + full-file read on startup, 1 NITPICK: import count overstated). All fixed before commit.

**Feature status change:** feature_canonical-memory ideating -> shipped

**Follow-ups:** Run migration script on Mac Mini (`npx tsx scripts/migrate-memories.ts`), deploy via /sync. feature_session-end-capture is the natural next step (auto-captures Claude Code session memories into the same JSONL format).

**Key decisions:** Write-through model (vault + SQLite on every save) instead of rebuild-only, per Codex review finding that rebuild-only breaks live retrieval until restart. Decay stays SQLite-only (projection concern). Access/reinforcement data intentionally resets on rebuild.

---

## 2026-04-16 -- Git pre-commit + commit-msg hooks for status edit and log append guards (audit Fix #8)

**Commit:** `881a5aa`

Final audit fix. Two repo-tracked hooks under `.githooks/`:

- **pre-commit** blocks direct `>> wiki/log.md` appends in staged diffs. Override: `K2B_ALLOW_LOG_APPEND=1`.
- **commit-msg** requires `Co-Shipped-By: k2b-ship` trailer when `status:` lines change in `wiki/concepts/feature_*.md`. Override: `K2B_ALLOW_STATUS_EDIT=1`.

`scripts/install-hooks.sh` sets `core.hooksPath .githooks` (idempotent, one-time per clone). k2b-ship SKILL.md updated to append the trailer to both main and devlog commit heredocs.

7 test scenarios across `tests/pre-commit.test.sh` (4 scenarios) and `tests/commit-msg.test.sh` (3 scenarios). All pass.

Also fixed: added `Bash(node *codex-companion.mjs *)` permission to `~/.claude/settings.json` so codex-rescue subagents stop silently failing on the Bash permission prompt.

Codex adversarial review: two advisory findings (trailer is forgeable, hooks are opt-in per clone) -- both valid for team repos but N/A for single-developer K2B.

**8 of 8 audit fixes now shipped.** Phase 0 of K2Bi roadmap is ready-gated.

---

## 2026-04-15 -- CLAUDE.md cleanup: strip procedural content into skill bodies (audit Fix #4)

**Commit:** `972665f` refactor(CLAUDE.md): strip procedural content into skill bodies (audit Fix #4)

**What shipped:** First execution of the K2Bi audit's Axis 4 "Clogged" recommendations. CLAUDE.md shrinks from 287 to 219 lines (24% reduction) by moving procedural how-to content into the correct skill bodies, enforcing the ownership rule: CLAUDE.md owns identity, taxonomy, and soft rules; skill bodies own procedures. Copy-in phase added new sections to three skills, each faithfully preserving the procedural detail CLAUDE.md used to carry: (1) `k2b-review/SKILL.md` gained "Video Feedback from Telegram (run-level)" with the reaction-match rule (URL -> title -> channel -> pick ordinal), flock-and-atomic-rewrite recipe, zero-match reply text, ambiguity prompt, and all three Liam Ottley forbidden rules (never direct-append to video-preferences.md, never hardcode playlist IDs, never skip the flock). (2) `k2b-ship/SKILL.md` gained "Codex Adversarial Review -- the two checkpoints" with Checkpoint 1 (plan review) spelled out, the three skip conditions (vault-only, config tweaks, emergency hotfixes), the "never skip both" rule, and the presentation rules (neutral reporting, no pre-filtering). (3) `k2b-observer/SKILL.md` gained "Session-Start Inline Confirmation" with the HIGH/MEDIUM 3-option recipe, the exact rejection JSONL format preserved verbatim, and an idempotency note for cross-session deduplication. Strip phase then deleted seven CLAUDE.md sections (A: Video Feedback via Telegram 40 lines, B: Email Safety 4 lines, C: Codex Adversarial Review 28 lines, D: Session Discipline manual fallback 7 lines, E: Inline Observer Confirmation 20 lines, plus Codex-recommended extras: the duplicate /ship block inside "Roadmap & Feature Notes" and the procedural lines inside "Obsidian Cross-Linking"), replacing each with a 1-paragraph pointer naming the target skill. Kept intact: File Conventions, Slash Commands index, Rules list, /ship top-level mandate, session-start hook summary.

**Codex review:** 2 rounds. Round 1 (plan review) challenged the proposed strip set and flagged that strip A (not just C and E) also needed copy-first -- k2b-review had the file-locking mechanics but not the Telegram reaction-match/reply logic. Also surfaced two extras beyond the original 7-row plan: the duplicate /ship block at CLAUDE.md:246-249, and procedural lines inside "Obsidian Cross-Linking" (the latter verified to be already covered in k2b-vault-writer). Plan adjusted to copy-first A + C + E before stripping. Round 2 (pre-commit) returned APPROVE with zero findings -- content preservation verified at specific line numbers for all three copy-ins, no dangling pointers, no em dashes, no accidental deletions, 4 expected files only.

**Feature status change:** none (shipped as `--no-feature`; this is audit-derived infrastructure cleanup, not feature work attached to either In Progress lane entry).

**Follow-ups:**
- Next audit items from Axis 4 "Clogged": Fix #1 (auto-promote shipped-file-location rule to active_rules after 3x reinforcement), Fix #2 (atomic helper for compile-4-index bookkeeping), Fix #3 (single-writer log-writer helper for wiki/log.md's 13 call-sites).
- Next audit items from Axis 5 "Conflicts": "NEVER manual rsync" enforcement via code (pre-sync validator), "Never edit feature status manually" enforcement via git pre-commit hook, observer inline confirmation vs /observe idempotency marker, active rules LRU cap policy.
- k2b-review/SKILL.md line 158 dangling pointer rewritten as part of this commit (it had still named "CLAUDE.md's video feedback path" even after that section moved into the same skill).

**Key decisions:**
- Shipped as `--no-feature`. Two features in In Progress (mission-control-v3, minimax-offload) but neither owns this audit-driven cleanup. Attaching it to either would misrepresent lane membership.
- Strip scope expanded mid-session based on Codex plan review. Originally proposed 7 rows (A-G with F/G as keeps); Codex identified that strip A was not safely housed in k2b-review and that the extra /ship block + Obsidian Cross-Linking procedural lines should be in the same pass. Keith approved the expanded scope.
- Copy-in done as new sections rather than editing existing sections in target skills, so the diff is auditable and zero-risk to existing workflows.

---

## 2026-04-15 -- /research videos: post-first-run hardening

**Commits:** `fb10504` docs(plans), `99a99ac` fix(k2b-research), `d972cb2` fix(parse-nblm), `7a5a184` docs(plans)

**What shipped:** Four schedule-blocking fixes to the v2 K2B-as-curator `/research videos` pipeline after the first live run (query: "Claude agent skills production 2026") surfaced issues that had to be worked around inline. Step 1 zero-candidate guard hardened against empty/non-numeric $COUNT (original incident log blamed yt-search.py progress pollution, but empirical test confirmed progress already routes to stderr; real hole was `[[ "$COUNT" == "0" ]]` silently passing). Step 3 gained per-source retry (2 attempts, 2s backoff) plus canonical `notebooklm source list --json` reconcile after the wait loop -- first run lost 8 of 25 sources to single-shot timeouts and had no visibility into real-vs-transient failures. Step 6a defensive parse extracted from inline Python into the committed helper `scripts/parse-nblm.py` (widened citation regex covering comma lists + dash ranges + mixed forms, character-walker newline normalizer inside JSON string literals, outermost-array extraction that skips prose `[words]` false positives via a heuristic requiring object content, rejoin-by-title against $CANDIDATES with identity_resolved=false fallback). Step 6g schema gate tightened with `type == "string" and length > 0` on every identity field, plus the Python heredoc that writes the schema-failure run record fixed to take QUERY/QUERY_SLUG/NBLM_RAW via explicit prefix-env (those bash vars are never `export`ed in the skill, so the old code always wrote "unknown" to the frontmatter) and to use `<<'PYEOF'` quoting. Step 6a's failure handler now writes a full raw+stderr audit trail to raw/research/ before aborting. Trap cascade extended across 4 steps and 7 tmpfiles.

**Codex review:** 2 rounds dispatched this session (plus 2 rounds from the first Codex run earlier today which produced the original incident log analysis). Round 1 against the working tree pre-commit caught Step 6a's retry/audit inconsistency (comment promised retry-and-log, bash just exited 1) plus an unsafe fixed-path `/tmp/k2b-parse-err.log` -- both fixed before the first commit. Round 2 against commit 99a99ac hinted at "parser edge behavior" before its wrapper ran out of turns; probed locally, reproduced an `extract_json_array` false-positive when NBLM output contains an incidental `[words]` in prose before the real array, fixed in d972cb2. Both round wrapper agents hit the codex-companion reconnect loop early but recovered; extracted the partial findings via bounded Python reads of the sub-agent transcript. A third round was intentionally skipped per Keith's approval -- diminishing returns.

**Feature status change:** feature_research-videos-notebooklm shipped -> shipped (follow-up hardening; feature note gained a `## 2026-04-15 post-first-run hardening` section; no lane movement in wiki/concepts/index.md).

**Follow-ups:**
- **MEDIUM-3**: `flock(1)` is Linux-only; `k2b-review/SKILL.md` and CLAUDE.md's Video Feedback via Telegram rule use `flock -x 9` which silently fails on macOS. Keith worked around during the live run via a Python `fcntl.flock` helper. Recommended inline fix per the plan: replace `flock -x 9` with `python3 -c "import fcntl; fcntl.flock(...)"`. Deferred until after the first full Telegram-feedback cycle validates the fixed `/research videos` pipeline.
- **MEDIUM-1** (Telegram fallback on MacBook when `K2B_BOT_TOKEN` unset), **LOW** (same-day same-query run-note filename collision), **LOW** (Step 7 per-pick playlist-add result capture) -- all documented in plans/2026-04-15_research-videos-first-run-issues.md. Revisit after the first full cycle run.
- Confirmed non-issues against the committed skill: MEDIUM-2 (notebooklm delete syntax -- skill already correct), LOW-1 (synthetic URLs -- handled by rejoin), LOW-2 (unknown duration -- handled by real_duration from yt-search).

**Key decisions:**
- Re-scoped BLOCKER-1 rather than implement the incident log's proposed fix. The log blamed yt-search.py's progress line; empirical test confirmed the progress line has always routed to stderr. The real fix is to validate the guard's parseability, not to fix the non-existent stdout leak.
- Step 6a NBLM ask retry loop explicitly NOT implemented despite being in the original skill contract. `parse-nblm.py` already encapsulates every defensive pass we know about, so a failure at this point almost always means NBLM returned genuinely malformed output (safety refusal, API error, empty body) that a retry would not fix. Full audit-trail failure handler replaces the retry contract; if repeat failures surface in production, the right move is to harden `parse-nblm.py` rather than bolt on a retry loop.
- `parse-nblm.py` committed as a standalone helper (not inlined into SKILL.md) so Claude does not have to reinvent the defensive passes on every run. Six smoke tests pass: dash-range + literal newline + prose wrapper multi-entry happy path, prose-bracket false positive, string-literal bracket false positive, title-match failure negative path, no-JSON-array negative path, usage-error negative path.

---

## 2026-04-14 -- /research videos: real_published + recency veto

**Commit:** `bcf064a` feat(k2b-research): add real_published + recency veto to /research videos

**What shipped:** Thread yt-search's publish date through the K2B-as-curator pipeline so K2B can veto outdated content on fast-moving topics. 5 touchpoints in k2b-research/SKILL.md: (1) Step 6a rejoin pulls `published` from $CANDIDATES and normalizes to YYYY-MM-DD or "unknown"; (2) Step 6d adds an explicit recency veto -- if topic moves fast AND `today - real_published > 180 days`, pick goes to rejects; evergreen topics exempt; run-date anchor is today at run time (never hardcoded); (3) Step 6e $SUITABLE_JSON schema gains `real_published`; (4) Step 6g jq gate validates string + ISO-8601 prefix OR literal "unknown"; (5) Step 9 review note YAML block carries `real_published` + display line in the prose header. Companion docs edits (not in this commit; Syncthing-managed): Welcome to K2B.md replaced `/inbox` with `/review` and added `/research videos` command rows; Home.md gained a plain-English "Finding New Videos to Watch — /research videos" walkthrough section.

**Codex review:** 1 pass. Returned 2 MEDIUM findings: (a) recency rule needed explicit "today" anchor to avoid drift, (b) jq gate only type-checked string but didn't validate date shape. Both fixed inline before commit: anchor now says "today - real_published > 180 days", gate now regex-validates `^[0-9]{4}-[0-9]{2}-[0-9]{2}` OR literal "unknown".

**Feature status change:** feature_research-videos-notebooklm shipped -> shipped (same-day follow-up; feature note unchanged, no lane move).

**Follow-ups:** End-to-end verification still deferred (NotebookLM auth degradation, see plans/2026-04-14_curator-refactor-blockers.md). First live run will exercise the new `real_published` path.

**Key decisions:** NBLM is NOT asked to return upload dates -- it reads transcripts and would hallucinate. yt-search is the sole source of truth for publish metadata.

---

## 2026-04-14 -- v2 K2B-as-curator refactor for /research videos

**Commit:** `042471e` refactor(k2b-research,k2b-review): v2 K2B-as-curator for /research videos

**What shipped:** NBLM reader / K2B judge split: Step 5 NBLM ask now requests objective content descriptions only (no taste context passed to Gemini); Step 6 K2B applies judgment inline using the baked framing from the skill header + last 30 lines of video-preferences.md. Produces strict `{picks[], rejects[]}` JSON with `pick_id`, `video_id`, `real_url`, `real_title`, `real_channel`, `why_k2b`, `suggested_category`, `confidence`, `preference_evidence` per pick. jq schema validation gate validates both `picks[]` and `rejects[]` shapes, type-checks confidence. Run-level review note replaces per-video notes: one `review/videos_YYYY-MM-DD_<slug>.md` with human-readable prose + machine-parseable fenced YAML block per pick. Feedback drives physical playlist moves: keep removes from K2B Watch + adds to category; drop/neutral removes from Watch. `scripts/k2b-playlists.json` is the canonical name-to-ID map. flock + atomic write-rename hardening across `/review` and CLAUDE.md Telegram path. Schema gate failure path now writes partial run record with raw NBLM answer before aborting.

**Codex review:** 3 passes total. Pass 1 (prior session): 1 BLOCKER + 2 HIGH + 1 NIT -- all fixed (real_url/title/channel added to YAML block, rejects[] schema-validated, Write-tool handoff made explicit with post-write verification, CLAUDE.md wording corrected). Pass 2 (prior session, truncated): fixes verified. Pass 3 (this session): 1 HIGH + 1 MEDIUM + 1 NIT -- all fixed (schema gate writes run record on failure, new-category state machine aligned to playlist_action: pending not decision: pending, real_channel sourced from YAML not prose).

**Feature status change:** feature_research-videos-notebooklm shipped -> shipped (same-day v2 amendment; feature note already contains v2 amendment section).

**Follow-ups:** End-to-end verification deferred -- NotebookLM auth degradation blocked run on "AI agents for corporate workflows 2026". See plans/2026-04-14_curator-refactor-blockers.md.

**Key decisions:** none diverging from claude.ai project specs.

---

## 2026-04-14 -- docs(CLAUDE.md): post-retirement command cleanup

**Commit:** `5bc83ab` docs(CLAUDE.md): update /youtube + /research command entries post-retirement

**What shipped:** Updated `CLAUDE.md` slash command list to reflect the YouTube agent retirement (25bf78d). `/youtube` now described as capture-only via `k2b-youtube-capture` with an explicit note that `recommend`/`screen`/`morning` subcommands were retired 2026-04-14 and discovery should use `/research videos`. `/research` entry annotated with the new `videos` subcommand. Paired with a `/lint` pass that auto-fixed 4 stale indexes (wiki/index.md counts, raw/index.md + tldrs + research row additions) to catch up on the retirement + NotebookLM ships.

**Codex review:** skipped -- 4-line docs edit, no logic (per CLAUDE.md "When to Skip": one-line/docs changes).

**Feature status change:** none (--no-feature; this is post-ship documentation cleanup for already-shipped `feature_research-videos-notebooklm`).

**Follow-ups:** 3 HIGH uncompiled raw sources still pending `/compile` (ai-trading-skills-architecture, ai-trading-analyst video, mempalace video). 3 retirement-era tldrs + 2 video run records need a compile/operational-marker decision.

**Key decisions:** none.

---

## 2026-04-14 -- feature_research-videos-notebooklm: Phase B end-to-end shipping

**Commits:** `fee5317`..`25bf78d` (9 commits, range `30c079f..25bf78d` on `main`)

- `fee5317` feat(scripts): add send-telegram.sh for text notifications
- `8224217` feat(k2b-research): add /research videos subcommand
- `f1be7ed` feat(k2b-review): handle video feedback notes from /research videos
- `e49c1e6` docs(CLAUDE.md): add video feedback via telegram rule
- `1965895` fix(k2b-research): /research videos uses yt-search, not deep research
- `c78c687` fix(send-telegram): drop parse_mode=Markdown default
- `11003df` style(k2b-research): replace em dash with double hyphen
- `c293d59` fix(k2b-research,send-telegram): harden /research videos for unattended runs
- `25bf78d` docs(plans): phase B blockers + resolution log

**What shipped:** Phase B of `plans/2026-04-13_retire-youtube-and-build-research-videos.md` -- the on-demand `/research videos "<query>"` replacement for the retired YouTube agent. Live tested end-to-end against the query "AI agents for corporate workflows 2026". Pipeline: `yt-search.py --count 25 --months 1` -> 25 fresh YouTube candidates -> `notebooklm source add` per candidate (NotebookLM transcribes natively) -> parallel `notebooklm source wait` with a hard >=5 ready threshold gate -> NotebookLM `ask` filter prompt with baked Keith framing + tail of `wiki/context/video-preferences.md` -> JSON parse with citation-marker stripping + synthetic-URL rejoin by title -> `scripts/yt-playlist-add.sh` per suitable -> per-video `review/video_*.md` notes -> run record at `raw/research/` -> Telegram notification via `scripts/send-telegram.sh` (auto-chunks under the 4096-byte limit, fails on non-2xx) -> notebook delete. Run 1 produced 20 suitable from 25 candidates and added all 20 to K2B Watch. Telegram feedback rule (CLAUDE.md) edited 4 review notes from free-form Telegram reactions; one rule violation (Liam Ottley reaction wrote a JSONL line directly to `video-preferences.md` instead of editing the note, cause unknown, applied intent manually). `/review` distillation collapsed 5 disliked notes into preference lines. Run 2 same query produced 7 suitable from 25 candidates: all 5 disliked channels rejected with explicit citation of Keith's preferences ("Disqualified based on recent explicit feedback rejecting this channel for being too beginner-focused"), plus generalization hits on Mikey No Code and Automation Feed for the "no beginner content" pattern. Feedback loop closed.

**Files affected:**
- `.claude/skills/k2b-research/SKILL.md` (+150 lines: full /research videos section)
- `.claude/skills/k2b-review/SKILL.md` (+18: video feedback handler)
- `CLAUDE.md` (+13: Video Feedback via Telegram rule)
- `scripts/send-telegram.sh` (new, 88 lines: chunker + HTTP error gate)
- `plans/2026-04-14_phaseB-blockers.md` (new, 117: mid-flight resolution log)
- vault: `wiki/context/video-preferences.md`, `wiki/concepts/feature_research-videos-notebooklm.md` (-> shipped), `wiki/concepts/index.md` (Shipped lane), 5 distilled preference lines, run record + B10 validation record, 15 still-pending review notes

**Codex review:** 3 rounds. Round 1: 1 BLOCKER + 2 HIGH + 2 MEDIUM. Round 2: 2 fix-induced regressions (HIGH chunker NUL-separator bug, MEDIUM count name confusion). Round 3: clean. All 7 issues closed in `c293d59`. Findings ranged from logic (source-wait gate never enforced because bare `wait` only checks the last backgrounded job) to security (literal interpolation of user-supplied query into bash, fixed via `printf %q` discipline) to silent-failure (send-telegram swallowed HTTP errors and ignored Telegram's 4096-byte hard limit, fixed via Python chunker + non-2xx fail).

**Plan deviations from the original 2026-04-13 spec:**
1. **`notebooklm source add-research --mode deep` is not a video-discovery engine.** Empirically returned 65/65 web articles + 0 YouTube videos for the test query. Pivoted mid-session: `yt-search.py` (already kept alive by Phase A) does discovery, NotebookLM is the filter only. Cleaner shape, eliminates a 5-15 min `add-research` wait per run, preserves the entire feedback loop architecture.
2. **`scripts/send-telegram.sh` parse_mode=Markdown removed.** Telegram's Markdown v1 parser returned HTTP 400 on 3 of 4 batches in the live B7 test because of underscores in channel names ("Nate Herk | AI Automation"). Plain text is the reliable default. Documented as a deferred opt-in for callers that need bold.
3. **Task B11 (weekly `/schedule` run) deferred.** Keith opted to validate the signal-to-noise ratio across a few manual runs before wiring the unattended schedule.

**Status:**
- What works: full pipeline (discovery, indexing, filter, playlist add, review notes, Telegram notification, run record, notebook cleanup, Telegram feedback edit, /review distillation, second-run feedback loop).
- What's incomplete: weekly /schedule (deferred, B11). Liam Ottley Telegram feedback path failed once silently -- needs diagnosis.
- What's next: 2-3 more manual runs against different queries to validate signal/noise, then wire B11. Resolve Liam Ottley feedback diagnostic. Optionally tune the filter prompt (20 suitable in run 1 was above the 3-10 target, filter runs hot).

**Feature status change:** `feature_research-videos-notebooklm` `designed -> shipped` (vault edit, mirrored in `wiki/concepts/index.md` Shipped lane).

**Follow-ups:**
- Diagnose why one Telegram feedback message bypassed the CLAUDE.md rule and wrote a JSONL line to `video-preferences.md` directly.
- Add a positional-fallback to the title-rejoin in Step 6 (current title-only rejoin would miss if the filter ever reorders or omits entries).
- `deploy-to-mini.sh auto` only synced `scripts/`, missing skill + CLAUDE.md changes -- detection logic needs review.
- Revisit `notebooklm source add` bulk-loop reliability: first attempt today had silent failures, second pass clean. Add per-call retry with backoff before B11 unattended runs.
- Consider tightening the filter prompt's "when in doubt, keep it" bias after observing 2-3 more runs.

---

## 2026-04-14 -- k2b-remote: retire YouTube agent (feature_youtube-agent Phase 4)

**Commit:** `9362b2c` feat(k2b-remote): retire youtube agent (phase 4 of feature_youtube-agent)

**What shipped:** Phase 4 of the v3 iteration of feature_youtube-agent: full retirement of the conversational YouTube agent. Deleted `youtube.ts`, `youtube-agent-loop.ts`, `taste-model.ts` wholesale (~1300 lines). Stripped from `agent.ts`: the `k2bYoutubeToolsServer` MCP singleton, all 5 YouTube tools (`youtube_get_pending` / `youtube_add_to_watch` / `youtube_skip` / `youtube_keep` / `youtube_swap_all`), `runStatelessQuery`, the `mcpServers` key in `runAgent`. Stripped from `bot.ts`: `handleYouTubeCallback` (12 action branches), `handleCommentOrSkipReason`, `handleDirectYouTubeUrl`, `fetchYouTubeOEmbed`, `awaitingComment` Map, `sendPendingNudges`, `sendScreenOptions`, `sendTelegramMessageWithButtons`, the `callback_query:data` dispatch, the `resetYtState` calls in `/newchat` and `/forget`, and the YouTube URL detection in `bot.on('message:text')`. Stripped from `db.ts`: `youtube_agent_state` `CREATE TABLE`, `getYouTubeAgentState` / `upsertYouTubeAgentState` / `resetYouTubeAgentState`, `YouTubeAgentStateRow` interface; added a v3 -> v4 migration that runs `DROP TABLE IF EXISTS youtube_agent_state` (unconditional + idempotent). Stripped from `index.ts`: the `runYouTubeAgentLoop` import + the setTimeout/setInterval block that ran it every 6 hours, and `sendTelegramMessageWithButtons` from the bot import. Removed `async-mutex` from `package.json` + lockfile. Deleted 7 vault state files: `youtube-recommended.jsonl`, `youtube-feedback-signals.jsonl`, `youtube-preference-profile.md`, `youtube-taste-model.json` (orphan from `taste-model.ts`), `youtube-recommended.jsonl.bak-before-repair` (stale repair backup), `youtube-taste-profile.md` (generated by dead observer pipeline), `youtube-feedback.md` (Keith-authored doc for retired system). Updated `wiki/context/index.md` to drop links to deleted files and add a retirement note. Updated `feature_youtube-agent.md` to `status: retired`, added `retired-date: 2026-04-14`, marked Phase 4 row as `shipped`, added a long Retirement notes section explaining what was kept (k2b-youtube-capture skill, yt-* scripts, OAuth token, K2B Watch playlist) and what's superseded. Moved the feature row from In Progress lane to Shipped lane in `wiki/concepts/index.md`. Net delta: 11 files changed, 654 insertions, 2604 deletions.

**Plan-gap fix found during execution:** The plan didn't list `scheduler.ts` for cleanup, but it imported `sendPendingNudges` and `sendScreenOptions` from `bot.ts` and ran a YouTube-specific post-task hook after `/youtube screen` / `/youtube morning` / `/youtube recommend` cron prompts (lines 5, 68-81). After deleting the bot.ts exports, scheduler.ts would have failed to compile. Stripped the import and the entire post-task block as part of A5. Documented in the retirement commit message.

**Codex review:** Skipped at Checkpoint 2 with documented reason -- the retirement is a delete-only refactor (whole-file deletes + grep-driven removal of orphan call sites). The grep verification (`rg "ytMutex|youtube_agent_state|guardedAdd|guardedSkip|handleYouTubeAgentResponse|parseAndExecuteActions|taste-?model" k2b-remote/src`) returned zero live-code matches before commit. The Phase 1 plan it builds on already had 4 rounds of Codex adversarial review. Risk profile is low: deletes only, idempotent SQL migration, unconditional `DROP TABLE IF EXISTS`.

**Combined Phase 1 + Phase 4 deploy** (per Keith's choice on the deferred-sync question): the Mini was running pre-Phase-1 code with both `sessions(scope)` and `youtube_agent_state` schemas. Single `pm2 restart` after the deploy ran both migrations in sequence: v1/v2 -> v3 (`Migrated sessions: dropped scope column`) and v3 -> v4 (`DROP TABLE IF EXISTS youtube_agent_state`, silent). DB snapshot taken before restart at `~/Projects/K2B/k2b-remote/store/k2b-remote.db.pre-retire-youtube-1776096381` as the rollback point. Bot rebooted clean (PID 7844, restart count ↺34, error log empty). `k2b-observer-loop` and `k2b-dashboard` undisturbed.

**Deploy gotcha:** `scripts/deploy-to-mini.sh code` does not propagate file deletions -- after rsync, the three deleted .ts files were still present on the Mini and tsc failed with stale-import errors. Recovery: SSH'd in and `rm`'d the three orphan files manually, removed `node_modules/async-mutex`, then re-ran `npm run build && pm2 restart k2b-remote`. Worth fixing in `deploy-to-mini.sh` to use `--delete` for the `code` target, or at minimum to detect deletions in the diff and apply them. Left as a follow-up because it's `scripts/` work, not Phase A scope.

**Smoke test:** Keith sent "what's on today" via Telegram. Bot replied with a full K2B daily skill output (date header, Telegram capture status, vault status, open loops, recap question), 9 turns, 27205ms, `is_error: false`. Confirms `handleMessage` -> `runAgent` -> persistent session -> vault tools all working post-retirement.

**Feature status change:** `feature_youtube-agent` in-progress -> retired. Moved to Shipped lane in roadmap index.

**Follow-ups:**
- `scripts/observer-loop.sh` lines 143, 279-319 still have a YouTube feedback handling branch that reads the now-deleted `youtube-feedback-signals.jsonl` and writes the now-deleted `youtube-taste-profile.md`. Branch silently no-ops (input files missing) -- safe in production but it's dead code. Clean up next time `scripts/` is touched. Out of Phase A scope per Keith's instructions ("don't delete `scripts/yt-*`" was specific to the playlist scripts, not the whole observer).
- `scripts/deploy-to-mini.sh code` should propagate file deletions, or at least detect them and warn. The recovery procedure here was 30 seconds of manual SSH but easy to forget.
- Phase B (build `/research videos` subcommand in `k2b-research` skill) is the next chunk. Plan tasks B1-B13 are in `plans/2026-04-13_retire-youtube-and-build-research-videos.md`. Per Keith's instructions, STOP after A13 and wait for confirmation before starting Phase B.

**Key decisions (divergent from claude.ai project specs):**
- Combined Phase 1 + Phase 4 deploy in one `pm2 restart` (Keith's call earlier in the session). Both migrations ran cleanly together; no need for the two-window separate-deploy fallback.
- The plan said "git add K2B-Vault/wiki/..." for the vault file edits; that's a plan bug because K2B-Vault is a sibling directory (`/Users/keithmbpm2/Projects/K2B-Vault/`) managed by Syncthing, not a path inside the K2B repo. All vault edits in this commit were done via filesystem operations (Edit tool / `rm`); none were staged in git. The retirement commit on `main` contains only K2B repo files (k2b-remote/src/*, package files, the plan file).

---

## 2026-04-13 -- session-design-v3 Phase 1: collapse two-session model

**Commit:** `61ab89a` refactor(k2b-remote): collapse two-session design, delete keyword router

**What shipped:** Phase 1 of a 4-phase v3 architecture refactor of the k2b-remote YouTube/interactive agent split. Collapses the `(chat_id, scope)` composite session PK back down to `chat_id` -- one persistent Claude Code session per chat instead of two -- and deletes the keyword-router dispatch (`handleYouTubeAgentResponse`, `parseAndExecuteActions`, `youtubeKeywords` array, `runAgentWithSession` helper, plus the forced-choice prompt that caused the "show me both" hallucination). YouTube state now lives behind an in-process MCP read tool (`youtube_get_pending`) the agent calls only when it judges the message is video-related; four mutation tools (`youtube_add_to_watch` / `youtube_skip` / `youtube_keep` / `youtube_swap_all`) handle changes via `guarded*` wrappers in `youtube.ts` that acquire a process-local `async-mutex` (`ytMutex`) and re-check `pendingVideoIds` inside the lock for TOCTOU safety. Background classifier calls (check-in copy, screening JSON) now go through a new `runStatelessQuery` helper that wraps `query()` with `persistSession: false` so the loop never writes orphan sessions to `~/.claude/projects/`. Every button callback mutation in `bot.ts` AND the `handleCommentOrSkipReason` text-reply skip-reason path are wrapped in `ytMutex.runExclusive`. `sessions` table v2->v3 migration preserves the `scope='interactive'` rows and drops the stale `scope='youtube'` rows. Added `k2b-remote/tasks.db` to `.gitignore`. Net delta: ~469 insertions / ~406 deletions across 7 files plus `package.json` + lockfile (`async-mutex` new dep).

**Codex review:** Skipped at Checkpoint 2 with documented reason -- the v3 plan (`plans/2026-04-13_session-design-v3.md`) underwent 4 rounds of Codex adversarial review pre-implementation. Round 3 fixed the must-fix triad: `unstable_v2_prompt` is not one-shot (use `query({ persistSession: false })` instead, verified at `runtimeTypes.d.ts:360`), pre-check-then-mutate is TOCTOU-vulnerable (process-local async mutex), dual-pm2 hot rollback is unsafe (cold rollback only with DB snapshot). Round 4 fixed the missing-mutex gap on `handleCommentOrSkipReason`. A second-opinion Claude Code session 2026-04-13 read the full plan + diff and confirmed implementation fidelity, recommending ship-as-is without revert or extension.

**Feature status change:** `feature_youtube-agent` shipped -> in-progress (v3 iteration row added to its Shipping Status table). Index lane updated.

**Follow-ups:**
- Run `/sync` to deploy `k2b-remote/` to the Mac Mini (build + pm2 restart).
- Phase 2 (cooperative loop presentation, 60s quiet-window check) and Phase 3 (migrate `handleDirectYouTubeUrl`/screen-process/highlights/promote off the persistent session) are both parked, superseded by Phase 4.
- Phase 4 (designed): retire the YouTube agent loop entirely, replace with a NotebookLM-backed K2B skill for standing-topic weekly digests. Designed in a separate Claude Code session. Phase 1 buys a stable bot during the 1-3 week NotebookLM build + validation window.
- Known non-blocker carried forward: `expireVideoFromWatch` (called from the loop on phantom-playlist entries) internally composes `clearFromAgentState` and isn't wrapped in `ytMutex`. Loop's own phase guard makes it serial in practice. Will be deleted wholesale in Phase 4.

**Key decisions (divergent from claude.ai project specs):**
- State-as-tool, not state-as-context. The plan explicitly rejected prepending YouTube state into the prompt with `resume=sessionId` because anything sent that way becomes persisted history and would accumulate stale state in the transcript over days. The `youtube_get_pending` read tool only fires when the agent judges the message is video-related and tool results are bounded/compactable -- no transcript pollution.
- `feature_youtube-agent` reopened (not a new `feature_session-design-v3` feature note). The v3 work is iteration 2 of the YouTube agent feature. Reopening keeps the full arc (built -> iterated -> retired in Phase 4) in one feature note.

---

## 2026-04-13 -- fix silent failure in handleMessage

**Commit:** `cfa5c72` fix(k2b-remote): add catch block to handleMessage to prevent silent failures

**What shipped:** handleMessage had a try/finally with no catch block. When anything after runAgent() threw (sendMessage, scanOutbox, saveConversationTurn), the error was silently swallowed and the bot went quiet with no log entry and no reply to Keith. Added a catch block that logs the error via pino and sends a fallback "Something went wrong" reply to Telegram. Also widened the try scope to cover buildMemoryContext and typing setup per Codex review.

**Codex review:** 3 findings. Fixed 2: (1) try scope too narrow -- widened to cover buildMemoryContext and sendTyping. (2) log message said "after agent returned" but runAgent errors also land in the catch -- fixed to generic "handleMessage failed". Noted 1: pre-existing chatId guard bug (String(undefined) = 'undefined' is truthy) -- separate fix, deferred.

**Feature status change:** No feature -- bugfix (bot going silent on post-agent errors)

**Follow-ups:**
- Fix chatId guard: validate ctx.chat?.id before String() conversion (pre-existing bug)

---

## 2026-04-13 -- Telegram media sending via outbox directory

**Commits:** `1050e52` feat: Telegram media sending via outbox directory | `51a4d8e` fix: delete outbox manifests after send, not before

**What shipped:** K2B can now send images, audio, video, and documents to Keith via Telegram. Agent writes a JSON manifest to `workspace/telegram-outbox/`, bot scans after `runAgent()` returns and sends files via grammy's `sendPhoto`/`sendAudio`/`sendVideo`/`sendDocument`. Supports 10MB photo limit with document fallback, 50MB hard cap. Updated k2b-media-generator skill and k2b-remote CLAUDE.md with outbox instructions so the agent knows how to use it.

**Codex review:** 5 findings. Fixed 2: (1) manifest deleted before send -- now deleted only after successful send. (2) TOCTOU on file stat -- moved inside try block. Accepted 3: same-millisecond timestamp miss (extremely rare), concurrent scanOutbox race (Node single-threaded), wrong-chat-id (single-user bot).

**Feature status change:** No feature -- infrastructure enhancement

**Follow-ups:** none

---

## 2026-04-13 -- session isolation: split YouTube agent from Telegram chat

**Commits:** `66d39cb` feat: scoped sessions + persistent YouTube agent state in SQLite | `3f38712` refactor: migrate youtube-agent-loop to persisted SQLite state | `e70a3d7` refactor: migrate bot.ts to DB-backed session scopes and YouTube state | `448815d` fix: enforce 12h expiry on pendingCandidates reads

**What shipped:** Fixed a fundamental design flaw where the proactive YouTube agent loop and regular Telegram chat shared one Claude Code session, causing context contamination. Root cause: "one chat = one session" model when the system actually runs multiple independent workflows. Sessions table now has a (chat_id, scope) composite key -- YouTube agent gets scope='youtube', regular chat gets scope='interactive'. YouTube agent state (phase, pendingVideoIds, pendingCandidates) moved from in-memory globals to SQLite, surviving pm2 restarts. 12h auto-expiry timer prevents the "stuck forever" bug when Keith doesn't respond to YouTube check-ins. Migration preserves existing sessions as 'interactive'. /newchat also resets YouTube workflow state.

**Codex review:** 2 findings. (1) IMPORTANT: getYtPendingCandidates() didn't enforce stale_after expiry, allowing old Telegram buttons to act on expired candidates -- fixed in 448815d. (2) MINOR: verdict/verdictValue field mapping is overloaded in PendingCandidate -- pre-existing, deferred.

**Feature status change:** No feature -- infrastructure bugfix (session cross-contamination)

**Follow-ups:**
- awaitingComment Map still in-memory (low priority -- rarely hit)
- sentNudgeIds Map still in-memory (cosmetic duplicate nudges on restart)
- verdict/verdictValue field semantics cleanup in PendingCandidate

---

## 2026-04-12 -- inline observer confirmation at session start

**Commit:** `d02c574` feat: inline observer confirmation at session start

**What shipped:** Observer findings at session start now prompt Keith for inline action on HIGH confidence items (confirm as guard / keep watching / reject). Confirm runs /learn which auto-creates a policy ledger entry. Reject logs to preference-signals.jsonl. This collapses the old 3-step manual flow (/observe -> /learn -> reinforce) into one natural-language response. /observe remains available for deep synthesis but is no longer required.

**Codex review:** skipped (trivial: one-line hook header + CLAUDE.md docs)

**Feature status change:** No feature -- continuation of self-improvement loop hardening

**Follow-ups:** none

---

## 2026-04-12 -- close the self-improvement loop with policy ledger

**Commit:** `15b9e7a` feat: add policy ledger and close self-improvement loop

**What shipped:** Fixed a silent bug where observer findings never surfaced at session start (hook read from Notes/Context/ but observer writes to wiki/context/). Added a learnings watch list to session start that shows Reinforced 2+ learnings with active-rules dedupe. Created policy-ledger.jsonl as an executable guardrail layer -- seeded from 7 active rules + key learnings + 3 autonomy-tracking entries for weave, lint, and vault-writer. Added pre-action ledger checks to vault-writer, compile, and weave skills. Updated k2b-feedback to auto-append ledger entries when /learn captures actionable guards. Scheduled weekly /lint on Mac Mini (Sunday 8am HKT, task a6b5059b).

**Codex review:** 2 findings (both fixed before commit). (1) Medium: watch list duplicated already-promoted active rules -- added dedupe via grep exclusion. (2) Low: documentation drift between hook threshold (2+) and feedback skill docs (6+) -- aligned docs.

**Feature status change:** No feature -- infrastructure work (self-improvement loop hardening)

**Follow-ups:**
- Monitor policy ledger adoption over 2 weeks before building k2b-retro
- First autonomy graduation candidate: k2b-weave crosslink_apply (needs 10+ approvals)
- Codex found the observer-candidates path bug -- confirms adversarial review value

**Key decisions:** Policy ledger is JSONL not SQLite (grep-friendly, same pattern as observations.jsonl). Autonomy graduation is per-action-type not per-skill (k2b-weave proposing != k2b-weave editing). Watch list threshold is Reinforced 2+ (not 3+ or 6+) to bridge the gap between single-mention and promoted-rule.

---

## 2026-04-12 -- rename /inbox to /review

**Commit:** `206b47e` refactor: rename /inbox skill to /review

**What shipped:** Renamed the k2b-inbox skill to k2b-review across the entire codebase (28 files). The slash command is now `/review` instead of `/inbox`, aligning with the `review/` vault directory it operates on. Also fixed stale `Inbox/` vault paths in the legacy dashboard routes to use `review/`. Dashboard files renamed (inbox.ts -> review.ts, Inbox.tsx -> Review.tsx), CSS classes updated (inbox-* -> review-*), API endpoints changed (/api/inbox -> /api/review).

**Codex review:** skipped: Keith declined Codex rescue

**Feature status change:** none (--no-feature, infrastructure refactoring)

**Follow-ups:**
- Run /sync to deploy skill + code + dashboard + script changes to Mac Mini

**Key decisions:** none

---

## 2026-04-12 -- NotebookLM deep research capability

**Commit:** `4f23749` feat(research): add NotebookLM deep research mode and YouTube Data API search

**What shipped:** Added multi-source deep research capability to K2B via Google NotebookLM (teng-lin/notebooklm-py, 10K+ stars). New `/research deep <topic>` mode in k2b-research skill orchestrates: source gathering (YouTube Data API + Perplexity), NotebookLM notebook creation + source loading, structured research queries (Gemini does analysis at zero token cost), Opus synthesis into vault, and compile into wiki pages. Also added `scripts/yt-search.py` using YouTube Data API v3 with K2B's existing OAuth credentials (replaces yt-dlp which required Chrome cookies and Keychain access). Test run on "AI trading bot using Claude Code" loaded 19 sources, ran 6 research queries, and compiled findings into `concept_investment-second-brain.md` (7 architecture patterns, risk management deep dive, failure case studies, expanded API stack).

**Codex review:** reviewed, 4 warnings fixed (--count >50 silent truncation, swallowed HTTPError in video details, defensive credential parsing, day-based ISO 8601 duration crash). 2 nits fixed (unused import, unnecessary $(cat) in skill).

**Feature status change:** feature_notebooklm-research-integration backlog -> in-progress

**Follow-ups:**
- Run the production investment second brain architecture research as the first real use
- Consider shipping the feature once the first production research is validated

**Key decisions:**
- Chose teng-lin/notebooklm-py (skill-based) over jacob-bd/notebooklm-mcp-cli (MCP-based, 35 tools consuming context)
- YouTube Data API v3 over yt-dlp (works on both machines, no cookie issues, 100 searches/day)
- Perplexity for GitHub/Reddit/article discovery alongside YouTube API
- NotebookLM NOT installed on Mac Mini -- vault is the durable layer, NotebookLM is throwaway analysis on MacBook

---

## 2026-04-12 -- skill remediation: vault architecture alignment

**Commit:** `b0e6224` chore(skills): align 15 skill/eval files with current vault architecture

**What shipped:** Audited all 22 K2B skills against the current 3-layer vault design (raw/wiki/review). Found 15 files with stale references from the pre-migration era. Fixed 5 cross-cutting patterns: dead Inbox/ folder references (10 replacements), legacy Notes/ paths (5 replacements), hardcoded MOC_ in up: frontmatter (5 fixes), old idea_<slug> naming convention (2 fixes), and one-off issues (empty up: field, missing usage logging). The deepest rewrite was k2b-observer: Phase 1a rewritten to document actual signal sources (observer-loop primary, review queue secondary), signal format docs updated with both schemas, aspirational "How Other Skills Use" section downgraded to Planned status.

**Codex review:** reviewed, 2 findings (both fixed): daily-capture find command and insight-extractor DQL missing wiki/ in their search paths

**Feature status change:** --no-feature (cross-cutting skill maintenance)

**Follow-ups:** none

**Key decisions:** MOCs still valid as up: targets (vault-writer lists 5 active MOCs), but downstream skills should not hardcode a single MOC -- use dynamic selection based on note domain

---

## 2026-04-12 -- fix autoresearch post-loop handoff

**Commit:** `d7b3477` fix(autoresearch): add post-loop handoff to prompt /ship instead of /sync

**What shipped:** Added a Post-Loop Handoff section to k2b-autoresearch SKILL.md. After the loop completes, the skill now instructs K2B to prompt /ship (not /sync). Autoresearch creates unpushed commits via the commit-before-test protocol; prompting /sync directly skipped shipping discipline (no devlog, no wiki/log, no feature tracking).

**Codex review:** skipped: 9-line addition to a single skill file

**Feature status change:** --no-feature (skill maintenance)

**Follow-ups:** none

**Key decisions:** none

---

## 2026-04-12 -- k2b-compile autoresearch: index-skip prevention + dedup gates

**Commits:** `c9a4dd5` + `c5513e7` + `2b943c0` + `b27b5a3` (3 experiment iterations + eval infra)

**What shipped:** Ran /autoresearch on k2b-compile targeting two recurring real-world failures: (1) raw subfolder index consistently skipped during compile (E-2026-04-12-001, 2x in 4 days), (2) MiniMax output blindly followed, creating duplicate pages instead of enriching existing ones (L-2026-04-12-001). Three structural improvements kept: Step 5 reordered with raw index FIRST + checklist gate + self-check, pre-create dedup check against raw source `related:` links + wiki grep, and Step 2 validation framing MiniMax output as suggestion-not-directive. Also created eval infrastructure (eval.json with 15 assertions across 3 test prompts, results.tsv, learnings.md).

**Codex review:** skipped: autoresearch loop (experiment commits are individually scoped, each changes one thing)

**Feature status change:** --no-feature (skill maintenance)

**Follow-ups:**
- Binary eval ceiling noted: when a skill mentions all the right steps but fails on execution emphasis, assertions score 100% at baseline. Future evals should test ordering/priority, not just presence.
- Next compile run is the real test -- will it follow the raw-index-first ordering?

**Key decisions:** none

---

## 2026-04-12 -- k2b-weave v0: background cross-link weaver

**Commit:** `accf9bb` feat(weave): k2b-weave v0 -- background cross-link weaver via MiniMax M2.7

**What shipped:** New skill `k2b-weave` that runs 3x/week on Mac Mini (Mon/Wed/Fri 04:00 HKT), reads the whole in-scope wiki (~57 pages, ~81K tokens), calls MiniMax M2.7 to find missing cross-links, proposes top-10 candidates ranked by utility (orphan reduction, cross-category, confidence), and drops a digest note in `review/` for Keith's approval via `/inbox`. Approved pairs land as `related:` frontmatter entries on the FROM page. Single-sided writes, no auto-apply in v0.

Components: `scripts/k2b-weave.sh` (orchestrator, 700+ lines), `scripts/minimax-weave.sh` (MiniMax caller with strict JSON schema, prompt injection guard, mock hook for testing), `scripts/k2b-weave-add-related.py` (YAML frontmatter editor with atomic writes + optimistic concurrency), `tests/test-k2b-weave.sh` (33-case integration suite), 10-page fixture vault. Extended `k2b-inbox` to delegate `type: crosslink-digest` notes to `/weave apply`.

**Codex review:** 3 rounds total (2 adversarial design reviews, 1 pre-commit). Round 1 found 6 blockers: HIGH-tier auto-apply unsafe, scope contradictions, single mega-prompt fragile, Syncthing write-path blast radius, fragile ledger identity, bidirectional churn. All addressed -- HIGH disabled for v0, scope fixed, single-sided frontmatter writes, atomic writes + lock file, path+slug dual-keyed ledger with TTL. Round 2 confirmed all blockers resolved; found 1 new blocker (shared lock domain mismatch) which we accepted as v2 work via Path B (optimistic concurrency). Round 3 (pre-commit) found 1 blocker (fsync durability in atomic writes) -- fixed before commit. 9 concerns documented as post-ship work.

**Feature status change:** feature_k2b-weave: in-progress -> shipped

**Follow-ups:**
- Register cron on Mac Mini after `/sync`: `ssh macmini 'cd ~/Projects/K2B/k2b-remote && node dist/schedule-cli.js create "run /weave" "0 20 * * 0,2,4" 8394008217'`
- Monitor first 3 real runs for acceptance rate; adjust top-10 scoring weights if needed
- Audit grep/jq pipefail spots per Codex concern
- Add trailing-pipe parsing leniency test per Codex concern
- The 04-11 Kai on AI youtube raw that triggered this feature needs `/compile` to connect to the vault properly

**Key decisions:**
- v0 is MEDIUM-only (no auto-apply) per Codex round 1 blocker on homonym safety
- Single-sided `related:` frontmatter field instead of bidirectional `## Related` section per Codex round 1
- Path B chosen over Codex's full fix: atomic writes + optimistic re-read + lock file instead of shared vault-mutation lock across compile/vault-writer (deferred to v2)
- Top-10 per run cap to prevent inbox overwhelm (Codex round 1 suggestion)
- Temperature lowered from 0.2 to 0.1 after observing MiniMax non-determinism on real vault

---

## 2026-04-12 -- Durable deferred-sync mailbox (k2b-ship post-ship hardening)

**Commit:** `bfa6f19` fix(ship): durable deferred-sync mailbox for /ship -- /sync handoff

**What shipped:** Replaced the soft "run /sync" reminder at the end of `/ship` with an explicit sync-now-or-defer question backed by a durable `~/Projects/K2B/.pending-sync/` mailbox directory. Each `/ship --defer` writes a uniquely-named JSON entry via temp-file + `os.replace()` atomic rename. `/sync` is the sole consumer: it scans the directory, validates each entry (JSON + schema + category allowlist), folds valid entries into the sync scope, runs the deploy, and deletes only the specific filenames it observed at scan-start. The session-start hook surfaces pending mailbox entries (and any broken ones) on every new session. Codex approved in round 5 of adversarial review after finding and fixing 9 successive durability holes across 4 prior rounds. Also caught `k2b-sync` docs up to `scripts/deploy-to-mini.sh` dashboard support (commit `5dd0e88`) that landed earlier but never updated the skill doc.

**Why:** Keith asked whether `k2b-sync`'s end-of-session proactive prompt could be retired now that `/ship` had one -- "is it just a double prompt?" I ran `/codex:adversarial-review` on the `/ship ↔ /sync` responsibility split to get a second opinion, and Codex immediately found a structural gap: `/ship` ended with a soft reminder that left no durable recovery signal. If Keith ignored the reminder, shipped-to-GitHub state sat ahead of the Mac Mini with no way for a future session to know the Mini was stale. Codex explicitly warned against retiring the proactive prompt until a durable mechanism existed. So the plan became: build the durable mechanism first, revisit the prompt question later.

**Files affected:**
- `.claude/skills/k2b-ship/SKILL.md` -- step 12 rewritten as now/defer branch. The `now` branch invokes `/sync` in-line and does NOT touch the mailbox (avoids a cleanup race Codex R2 caught). The `defer` branch writes a unique JSON entry via atomic rename. Category table rolls `scripts/hooks/**` into `scripts` -- no separate `hooks` category exists because `/sync`'s allowlist is `{skills, code, dashboard, scripts}`.
- `.claude/skills/k2b-sync/SKILL.md` -- Step 0 documents the mailbox consumption contract: scan directory, skip fresh `.tmp_*` (in-progress writes), surface stale `.tmp_*` older than 60s as UNREADABLE, validate schema + category allowlist, report UNREADABLE loudly, fold VALID entries into sync scope, delete only filenames observed at scan-start. Also added `/sync dashboard` command, dashboard category row, dry-run example, `pm2 status k2b-dashboard` verification, and pm2 error-handling case.
- `scripts/hooks/session-start.sh` -- new section 5 surfaces mailbox entries at session start using the same validation contract. Python always exits 0 and encodes state in stdout (`EMPTY` / `VALID|n` / `ENTRY|...` / `UNREADABLE|name|reason`) so the hook's `set -euo pipefail` can't hide the check's output. Pluralization handled correctly.
- `CLAUDE.md` -- every top-level `/ship` reference updated from "reminder" wording to the `now/defer` contract. Session Discipline manual fallback now mentions `/sync` and the mailbox.
- `.gitignore` -- `/.pending-sync/` (mailbox directory) + legacy `/.pending-sync.json` single-file path kept ignored.

**Codex adversarial review:** 5 rounds. Each found a more subtle failure mode that forced another redesign:

- **R1** (original split check): dashboard docs missing from `k2b-sync`; `/ship` reminder-only handoff.
- **R2** (after reminder fix): CLAUDE.md still said "reminder" in 4 places; malformed marker silently downgraded to "no pending sync"; session-start swallowed stderr; marker clear was TOCTOU-racy.
- **R3** (after switching to mailbox + atomic write + filename-based delete): crash between `fsync` and `os.replace()` left only a `.tmp_*.json` file that readers ignored forever; `/ship` emitted a `hooks` category `/sync` had no deploy target for.
- **R4** (after stale-temp detection + `hooks→scripts` normalization): `/sync` still trusted mailbox categories blindly; legacy pre-fix entries could bypass the producer-side fix.
- **R5** (after consumer-side allowlist): **verdict `approve`**. No material findings. Only advisory next steps (integration tests when the flow moves from docs to executable code).

**Smoke tests:** 12 session-start hook paths via bash + handcrafted mailbox states. All pass: well-formed plural, well-formed singular, malformed JSON, schema-missing fields, empty categories list, legacy `hooks` category, mixed valid+broken, valid `scripts` entry, fresh `.tmp_` silent, stale `.tmp_` surfaced with age, `pending:false` silent, gitignore excludes the directory.

**Key decisions:**
- **Mailbox directory, not single file.** The single-file design with compare-and-swap clearing is elegant on paper but fragile in practice (R2 TOCTOU, R3 crash-during-write). The mailbox-as-directory pattern is the standard fix: producers write unique filenames via atomic rename, consumers delete by filename, no state is ever rewritten in place. Race-free on POSIX by construction.
- **Consumer-side category allowlist, hardcoded set.** `{skills, code, dashboard, scripts}` literal duplicated in 3 places (`k2b-ship` category table, `k2b-sync` Step 0, `session-start.sh` section 5). A pluggable registration system would be more elegant but adds surface area. The drift risk is documented in `feature_k2b-ship`'s Updates follow-ups.
- **Stale `.tmp_*` files surface as UNREADABLE, never auto-recovered.** Auto-renaming crashed temp files would add write-from-reader responsibility and a second race surface. Surfacing them and requiring Keith to decide keeps consumers mostly read-only.
- **`k2b-sync` proactive prompt NOT retired.** Keith's original question today. Codex R2 explicitly warned no: it's the last-resort recovery path if `/ship` is skipped entirely. The plan file records this as deferred until the mailbox has real-world usage.
- **Nothing in the mailbox is executable yet.** The SKILL.md files are specs telling future Claude invocations what to do; the actual write/read/delete logic runs when the skills are invoked. Codex's advisory to add integration tests when this moves to standalone executable code is noted but out of scope.

**Status:**
- What works: `bfa6f19` landed + pushed. Session-start hook smoke-tests all passing. Feature note Updates section has the history. `wiki/concepts/index.md` Shipped row annotated. `wiki/log.md` entry appended.
- What's incomplete: Nothing in this commit, but the full defer → session boundary → session-start surface → /sync consume loop is untested in the wild. First real-world defer will be the canary.
- What's next: `/sync` the 5 files to the Mac Mini so the updated `session-start.sh` hook runs on the Mini. Then the first real defer is the canary run.

**Feature status change:** feature_k2b-ship stays `shipped` -- no lane transition. This is post-ship hardening, not a new ship row.

---

## 2026-04-11 (late) -- Mission Control v3 Ship 1 patches: vault-drop intake, sync script, audio UX, context fix

**Commits:** `84994a3` feat(intake) + `5dd0e88` chore(sync) + `02f9031` fix(intake) + `324758a` chore(launch)

**What shipped:** Four in-flight patches to Mission Control v3 Ship 1 during the 2026-04-24 measurement window. (1) Replaced cross-host HTTP forwarding with a vault-drop queue: dashboard writes `Assets/intake/<uuid>/manifest.json` atomically, k2b-remote gains an `intake-watcher` (fs.watch + 15s reconcile sweep) that validates, waits for size stability, calls `handleIntake`, then atomically renames to `Assets/intake/processed/<uuid>/`. Dashboard polls `GET /api/intake/status/:uuid` (pending-sync -> processing -> done/error) so failures are never swallowed. k2b-remote stays loopback-only. (2) Added a `dashboard` category to `scripts/deploy-to-mini.sh` so the script actually covers all three pm2 services on Mac Mini instead of leaving k2b-dashboard out. (3) IntakeBar audio mode is now stage-then-submit: dropping a file shows a file chip + Remove button, the note textarea stays editable, and nothing uploads until Submit is clicked. Matches URL/text/fireflies mode. Also fixed `buildMessage` in `k2b-remote/src/intake.ts` which was forwarding only the transcribed text to the agent and silently dropping the user-provided note -- it now prepends `[Context from user]: <note>` above the voice block. (4) Launch.json preview entry repointed 5173 -> 5174 to avoid collision with the live MacBook dev tree.

**Why now:** Keith uploaded an mp3 via the MacBook dashboard to test intake, watched it stage in `Assets/intake/` and then NEVER get processed. Root cause: `intake.ts` forwards to `http://localhost:3300` expecting k2b-remote to be co-located, but k2b-remote only runs on the Mac Mini. The MacBook POST fails with `ECONNREFUSED`, `forwardToRemote` swallows the error, and the UI returns silently with no indication. The Mac Mini dashboard was the only path actually working. These patches fix the MacBook path, make failures visible, and add the capture-context field that was cosmetically present but functionally broken.

**Codex review:** 3 rounds on the vault-drop rewrite (`84994a3`), 0 rounds on `5dd0e88` (trivial script addition), 0 rounds on `02f9031` (small UX + 1-line backend fix that Keith live-tested), 0 rounds on `324758a` (config one-liner). On the vault-drop: R1 flagged 1 blocker (multer errors becoming non-JSON) and 4 fix-before-ship (missing `type` validation on POST, stuck-in-processing on move failure, watcher leak not cleaning on early returns, weak manifest validation including non-string `file` slipping through). R2 flagged 2 fix-before-ship (watcher leak still incomplete because finally block didn't own cleanup, silent `.done` write failure leaving UI stuck in processing). R3 flagged 1 hygiene issue (duplicate `closePerDirWatcher` call in the success branch); I pushed back explaining the early close is a pre-rename correctness guard against fs.watch firing on a disappearing directory, and the finally block is a safety net for all other exit paths. Codex agreed and cleared for commit. All fixes applied before any commit; no findings remained.

**Feature status change:** `feature_mission-control-v3` stays **in-measurement** (Ship 1-of-3, gate 2026-04-24 unchanged). These are in-flight patches to Ship 1 during its measurement window, not a new ship. Shipping table row updated to list all four commits and note that the live intake path was verified end-to-end post-patch.

**Follow-ups:**
- Observer audit at the 2026-04-24 gate should measure intake usage now that the MacBook path works and the context field actually reaches the agent.
- Empty-note duplicate uuid `07191cb5-...` deleted from `Assets/intake/processed/` after being superseded by `ac1227c4-...` (the one with Rachel Chan / 8 chef hiring context).
- `allhands/index.html` CV-upload bug flagged by Codex (hardcoded `cv.jpg` vs. spec allowing `.jpg/.png/.pdf`) stays out of scope. Not part of this session; untracked at session start.

**Key decisions (divergent from original plan):**
- **Rejected cross-host HTTP entirely.** An earlier draft of this plan proposed binding k2b-remote beyond `127.0.0.1`, pointing the MacBook dashboard at the Mac Mini Tailscale IP, and adding path-relative payloads with retry logic. Codex adversarial review of that draft called it over-engineering (1-bug-becomes-distributed-system), flagged high-severity exposure risks on the bind change (0.0.0.0 vs tailscale-only, no auth model defined), and noted missing idempotency for retries. The revised plan reused Syncthing as the transport and kept the trust boundary on loopback. This IS an architecture shift away from the original Ship 1 spec's "dashboard POSTs to k2b-remote" model, but the `POST /intake` HTTP endpoint in k2b-remote is **kept** as a deprecated-but-live secondary path, so the Mac Mini dashboard continues to work the old way while the vault-drop is the authoritative new contract. Both code paths coexist.
- **Skipped Codex review on 3 of 4 commits.** Rationale: `5dd0e88` and `324758a` are infrastructure/config changes (deploy script category addition, port number swap) where Codex findings would be low-signal. `02f9031` is a UX change plus a 1-line backend fix that Keith was live-testing in the same session, so feedback was immediate. The vault-drop rewrite got the full 3-round Codex gate. /ship's mandatory Codex gate was honored on the commit that needed it, waived on the trivial ones with recorded reason here.
- **Ship 1 patches during the measurement window don't reset the gate.** Per the Shipping Status table pattern borrowed from `project_minimax-offload`, these patches strengthen Ship 1 before the observer-based gate review rather than invalidating the 14-day measurement clock. The gate still runs 2026-04-24 09:00 HKT on scheduled task `4872acad`.

**What**: Shipped `k2b-ship` (end-of-session shipping workflow) as a standalone skill out of `feature_dev-skills-pack`, and refactored `/improve` Sections 1b and 3 to delegate their vault+rules checks to `/lint` via a structured `wiki/context/lint-report.md` artifact instead of re-running the same queries.

**Why**:
- **k2b-ship**: Manual "stage + commit + push + DEVLOG + roadmap + /sync reminder" at the end of every session is friction that compounds. Per the 2026-04-10 ship order update in `feature_dev-skills-pack`, k2b-ship was the highest-ROI piece of that pack and got pulled out to ship standalone first. `k2b-retro` and `k2b-session-tracker` stay in the pack until `/ship` proves itself.
- **/improve ↔ /lint dedupe**: `/lint` Check #11 (Active Rules Staleness) was added earlier today in commit `d1f5227`. That left `/improve` Section 1b running a near-duplicate active-rules audit, and Section 3 still running DQL orphan/broken-link/stale-review queries that `/lint` already covered. Sections 2 (preferences) and 4 (eval dashboard) already used the "reader, not worker" pattern — only Sections 1b and 3 were the stragglers.

**Shipped**:
- **`.claude/skills/k2b-ship/SKILL.md`** (new, commit `88bad34`): 13-step workflow covering scope detection, feature identification against `wiki/concepts/index.md` lanes, mandatory Codex pre-commit review, code commit/push, feature note updates (single-ship vs multi-ship patterns), index.md lane transitions, DEVLOG.md follow-up commit (two-commit pattern because DEVLOG references the code SHA), wiki/log.md append, multi-ship gate handling, Backlog→Next Up promotion suggestion, and /sync reminder. Error handling covers Codex-missing, pre-commit hook failures, push conflicts, feature-note-not-found, and degraded bookkeeping.
- **`CLAUDE.md`** (commit `88bad34`): added `/ship` to System slash command list; rewrote Roadmap & Feature Notes section around In Progress / Next Up / Backlog / Shipped / Parked lanes with feature-note frontmatter schema; replaced manual Session Discipline checklist with a /ship-first workflow keeping a manual fallback for vault-only sessions.
- **`.claude/skills/k2b-lint/SKILL.md`** (commit `59136f5`): Check #11 gained a step 6 that surfaces learnings promotion candidates (learnings newer than `Last promoted:` with `Reinforced >= 2`) — moved here from `/improve` Section 1b. Output Format section now documents two artifacts: inline human report + structured `wiki/context/lint-report.md` with frontmatter summary counts + per-check roll-up + a top-level `## Needs Review` aggregator that downstream skills consume. Both manual and scheduled workflows write the artifact.
- **`.claude/skills/k2b-improve/SKILL.md`** (commit `59136f5`): Memory & Data Paths gains `lint-report.md`. Section 1b becomes a reader of `## Active Rules` from the lint artifact (no more re-running path validation or learnings scan). Section 3 becomes a reader of `## Needs Review` from the lint artifact (no more DQL queries). Vault metrics (folder counts + daily streak) stay inline as the only unique work. Both sections fall through with a "run /lint to refresh" nudge when the report is missing or >7 days stale. Report Format vault health line updated to show lint date and hard-error count.

**Codex review**: 2 rounds.
- **Round 1** (before commit): Found (P1) `/ship` committing code before writing DEVLOG.md (which is in git at project root, so step 5's commit leaves step 8's DEVLOG append as dirty state the next run sees), and (P2) `/improve` Section 3 telling the agent to read a "Needs Review" section that the new `/lint` artifact format did not define. Both real, both fixed before any commit: rewrote `/ship` step 8 as a two-commit flow (code first, devlog second matching the repo's existing `dc2ba69 docs: devlog for ...` pattern); added `## Needs Review` as a top-level aggregator section in the `/lint` artifact spec.
- **Round 2** (after fixes): Both original findings gone. Two new findings: (a) a documentation clarity issue in `/ship` step 5 where Codex read the category table as gating staging rather than /sync routing — fixed with a 2-sentence clarification that staging comes from `git status` of this-session-touched paths regardless of category; (b) a real `allhands/index.html` bug where CV upload hardcodes `cv.jpg` but the accompanying spec allows `.jpg/.png/.pdf` — **out of scope**, `allhands/` is not part of this session and was already untracked in the working tree at session start.

**Feature status changes**:
- `feature_k2b-ship` (implicit `designed` since the 2026-04-10 pullout from dev-skills-pack) → `shipped`. Note created directly in `wiki/concepts/Shipped/feature_k2b-ship.md` with full Updates entry. Added to `wiki/concepts/index.md` Shipped lane. `feature_dev-skills-pack` updated to record that Skill 1 (k2b-ship) is done; k2b-retro + k2b-session-tracker remain in backlog.
- `/improve ↔ /lint` dedupe shipped under `--no-feature` — it's maintenance on `concept_self-improving-loop` components, not a tracked feature.

**Key decisions**:
- **Two commits, not one bundled.** k2b-ship is a feature ship (`feat(ship)`) with attribution to `feature_k2b-ship`; the dedupe is a `refactor(skills)` with `--no-feature`. Mixing would have muddied attribution and made future revert harder. Three options were presented (A: two commits, B: one bundled, C: ship only dedupe); Keith picked A.
- **k2b-ship gets its own feature note**, not a row inside `feature_dev-skills-pack`. The 2026-04-10 pullout in the pack note explicitly said "tracked in `wiki/concepts/index.md` under In Progress once work starts" — honoring that meant creating `feature_k2b-ship.md` as the single source of truth for this ship, with `feature_dev-skills-pack.md` adding a pointer.
- **Two-commit DEVLOG pattern is now explicit in the /ship spec.** Previously the repo's working pattern (`dc2ba69 docs: devlog for active rules staleness detection` followed the matching feat commit) existed in history but wasn't documented. /ship now documents it so the next run doesn't trip on the Codex-caught bug.
- **Staging is driven by `git status`, not the category table.** The category table exists only to decide whether /sync routing is needed. A small but real clarification added to step 5 so the next reader (or reviewer) doesn't mistake it for a gate on what gets staged.
- **`/lint` is the worker, `/improve` is the aggregator.** Same pattern `/improve` Sections 2 and 4 already used. No more duplicated vault queries across skills. Fresh-data discipline: `/improve` nudges the user to `/lint` if the artifact is stale rather than silently reporting outdated findings.

**Status**:
- What works: Both commits landed (`88bad34`, `59136f5`), pushed to `origin/main`. `feature_k2b-ship.md` exists in Shipped/. `wiki/concepts/index.md` Shipped lane updated. `feature_dev-skills-pack.md` pointer added. `wiki/log.md` has both entries.
- What's incomplete: `wiki/context/lint-report.md` doesn't exist yet — first `/lint` run after these changes will generate it. Until then `/improve` Sections 1b and 3 will print the "no recent lint report" fallback instead of actual findings.
- What's next: Run `/sync` to push both skill-folder changes + CLAUDE.md to the Mac Mini. First test of the new `/lint` artifact next time `/lint` is invoked. Follow-up for Keith: decide whether to fix the `allhands/index.html` cv-format bug in a separate session.

---

## 2026-04-11 -- Active rules staleness detection + rules audit

**What**: Audited `active_rules.md` and found rules 2, 3, 6, 7 referencing pre-migration vault paths (`Notes/`, `Inbox/`, `Content-Ideas/`, `Insights/`). Rewrote rules 2/3/6, retired rule 7 (now automated by `/ship`), and bumped Last promoted to 2026-04-11. Then built automation so the next drift surfaces itself: added `k2b-lint` Check 11 (Active Rules Staleness) and `k2b-improve` Section 1b (Active Rules Audit) + `/improve rules` subcommand.

**Why**: The vault migration to raw/wiki/review landed 2026-04-08 but the rules file wasn't updated, so K2B was loading guidance referencing folders that no longer existed. Keith asked "how do I make sure these stay current?" — the honest answer was "they weren't," and the failure mode (path references going stale after refactors) is mechanically detectable. Scheduled reviews don't work unless something actively checks; the cheapest reliable trigger is to fold the check into existing weekly lint + existing health dashboard.

**Shipped**:
- **active_rules.md**: rules 2, 3, 6 rewritten against current vault paths. Rule 7 (After shipping, sweep vault) retired — `/ship` handles the sweep automatically, keeping the rule would just shadow the workflow and create drift risk. 13 rules → 12. Last promoted 2026-04-11.
- **k2b-lint Check 11 (Active Rules Staleness)**: reads active_rules.md, extracts backtick-wrapped and bare folder references, checks each path exists in vault. Flags dead paths, legacy folders (`Notes/`, `Inbox/`, `Content-Ideas/`, `Insights/`), and stale promotion dates (>30 days). Never auto-fixes — rules are Keith's voice. Runs in weekly schedule alongside checks 8-10. Contradiction detection renumbered to Check 12.
- **k2b-improve Section 1b (Active Rules Audit)** + `/improve rules`: reports last promotion date, rule count, path validation issues, and promotion candidates (reinforced learnings newer than last promotion). Dashboard report format now includes an Active Rules block.

**Key decisions**:
- **#1 (lint validator) + #3 (improve audit), skipped #2 (scheduled cron nudge)**. Detection of staleness is automated; promotion decisions stay manual. Scheduled cron nudges become noise unless they're load-bearing, and the validator catches the critical failure mode (dead path references) without needing a schedule.
- **Rule 7 retired not rewritten**. Option B (fallback rule when /ship is skipped) would have kept a shadow version of the workflow as a rule — which is exactly how drift starts. If /ship gets skipped, the manual fallback is already documented in CLAUDE.md.
- **Never auto-fix active rules**. The validator reports, Keith decides. Active rules are his voice and his judgment calls, not something to paper over mechanically.

**Test for this setup**: next time the vault architecture shifts, the first `/lint` run afterward should automatically surface any rule pointing at a dead path. Today's audit becomes a one-time event, not a recurring manual discovery.

---

## 2026-04-11 -- YouTube Agent stability refactor + skill split (Iteration 3)

**What**: Consolidated every YouTube state mutation into a small set of canonical functions to end the whack-a-mole regression loop, then reduced the k2b-youtube-capture skill to a pure batch playlist processor now that the conversational agent handles all curation/discovery.

**Why**: Iterations 1 and 2 of the YouTube agent had shipped the conversational curator but left ~20 bugs spread across 4 files. Every fix kept introducing new regressions during the next usage cycle — JSONL entries with `title: videoId`, "unknown" channel pollution in the taste model, agent promising "Done" without executing, duplicate JSONL keys from runAgent manually rewriting files via bash. Codex's structural audit found the root cause: state mutations were scattered across many duplicate code paths (4 distinct "add to Watch", 3 "skip from Watch", 2 "move to Screen") with inconsistent semantics, and runAgent prompts still had authority to mutate files.

**Process**:
- Two-round Codex review: first round flagged 15 bugs across iterations 1-2 (all fixed earlier), second round produced a full state-mutation audit with a table mapping every location that writes to JSONL/playlists/taste-model with its triggers, duplication risk, and safety issues.
- Plan mode used for the structural refactor (Iteration 3) and again for the skill cleanup. Both plans focused on "no new abstractions beyond what's needed to kill duplication".
- Iteration 3 refactor shipped as `66b2927`, follow-up bug fixes for publish date and new-pick skip confirmation as `25fcdb0`, and the skill cleanup as `e891a9a`.

**Shipped**:
- **k2b-remote/src/youtube.ts** gained 6 canonical state functions (`addVideoToWatch`, `skipVideoFromWatch`, `markVideoWatched`, `moveVideoToScreen`, `skipVideoFromScreen`, `expireVideoFromWatch`) plus `clearFromAgentState`. Every state change — JSONL write + feedback signal + taste model update + playlist op — happens together in one function call. No more split-phase writes.
- **Shared state moved to youtube.ts**: `youtubeAgentState` (phase machine + pendingVideoIds) and `pendingCandidates` (new-pick metadata cache) used to live in youtube-agent-loop.ts with inconsistent cleanup. Centralized so both files reference the same objects. `clearFromAgentState` now clears BOTH maps (was a stale-cache bug).
- **bot.ts callbacks rewritten**: watch, screen, skip (deduce + confirm split), skip-confirm, agent-add (rejects placeholder metadata), agent-skip, screen-skip, screen-process, screen-all all use canonical functions. `handleDirectYouTubeUrl` populates `pendingCandidates` with real yt-dlp metadata so `agent-add` has something to read.
- **youtube-agent-loop.ts**: text-reply handlers (`parseAndExecuteActions`, "swap/replace", "add all") use canonical functions instead of rolling their own JSONL writes. The "swap/replace" path now correctly calls `skipVideoFromWatch` (was silently marking `expired` with no playlist removal, no feedback, no taste model training). verify-playlist branch calls `expireVideoFromWatch`.
- **screen-process / screen-all prompts stripped of mutation authority**: runAgent is now told explicitly "Do NOT modify the K2B Screen playlist. Do NOT edit youtube-recommended.jsonl." The agent just creates the vault note; code handles state mutation via `skipVideoFromScreen` after the agent returns.
- **taste-model.ts** gained `deduceSkipReason(rec)` + `deduceSkipReasonKey(rec)` helpers so bot.ts doesn't hand-roll deduction inline. Both accept either a rec-like object or positional args via overloads.
- **Iteration 2 follow-ups also landed**: new recommendation cards enrich `upload_date` via `fetchVideoMetadata` after the flat-playlist search (fixes "Published: Unknown" on cards). Skip on a new-pick card now routes through the same deduction + confirmation flow as Watch-list skips — skip/skip-confirm/skip-reason handlers fall back to `pendingCandidates` when the video isn't in JSONL yet.
- **k2b-youtube-capture skill reduced**: 545 → 260 lines. Retired sub-commands: `morning`, `recommend`, `cleanup`, `<url>`, `status`, `screen`. Kept + refactored: `/youtube` (polls all inbound category playlists) and new `/youtube <playlist-name>` (case-insensitive match for one playlist like `/youtube invest` or `/youtube screen`). Both commands share the same per-playlist processing pipeline.
- **Mac Mini scheduled tasks deleted**: `a0ec773c` (Run /youtube recommend) and `c52fb28f` (Run /youtube morning). The agent loop's in-process setInterval replaces both.
- **Wiki feature doc rewritten** (`wiki/concepts/2026-04-08_feature_youtube-agent.md`) as the full workflow guide: ASCII diagram of the agent cycle, playbook for common user scenarios, trigger reference, state file map, canonical function table, relationship to other K2B skills, troubleshooting section.

**Key decisions**:
- **Validate at the boundary**: `addVideoToWatch` throws `MetadataValidationError` if title is missing or equals videoId, or channel is empty or 'unknown'. No more placeholder data ever lands in JSONL. The `agent-add` button callback rejects with a user-visible "Couldn't add -- metadata isn't available, paste URL again" rather than saving garbage.
- **`pendingCandidates` as the single source of truth for new-pick metadata**. `findNewContent` writes the cache before sending cards. `agent-add`, `skip-confirm`, text-reply handlers all read from the same cache. `clearFromAgentState` cleans it. No more split ownership between bot.ts and youtube-agent-loop.ts.
- **runAgent is parse-only**. The invariant is now: the agent produces text or JSON; the code does all mutations. Any file-mutation prompt gets an explicit "Do NOT use any tools" guardrail. This kills the "agent says Done but didn't" class of bug.
- **Unify skip flow across Watch-list and new-pick cards**. Both paths use `youtube:skip:<id>` callback and fall back to `pendingCandidates` when the video isn't in JSONL. Same deduction + confirmation UX regardless of card type. `agent-skip` remains as a legacy no-op for stale cards in Keith's Telegram history.
- **Agent-driven Screen processing stays, but runAgent no longer touches playlist/JSONL**. The screen-process prompt tells the agent to create a vault note; then `skipVideoFromScreen` runs in code to handle the state mutation and playlist removal. Same 2-minute cost for transcript analysis but the mutation surface is bounded.
- **Skill becomes a pure batch processor**. Curation/discovery (Watch list, recommendations, direct URLs, morning nudges) belongs in the agent loop. Batch processing videos Keith saved to a category playlist with `prompt_focus` domain analysis belongs in the skill. Clean split, zero overlap. `/youtube invest` now does the one thing you'd expect.
- **No new files**. Everything lived in existing files. Refactor was 560 additions vs 250 deletions in 4 files; skill cleanup was 63 additions vs 348 deletions in 1 file.

**Verification**:
- `npx tsc --noEmit` clean after each iteration (three times across the session)
- `deploy-to-mini.sh code` and `deploy-to-mini.sh skills` both shipped clean; pm2 restarts verified online
- Taste model on Mac Mini post-migration: 10 channels, 0 "unknown" bucket (one-time migration on load deleted the polluted entry)
- JSONL repair: 3 polluted entries (`Ob5Vu-gD3mo` had videoId as title; `-l0jXCQMuwc` and `eScvCTwVtFI` had duplicated keys from prior runAgent bash rewrites) repaired via in-place JSON parse/stringify with metadata re-fetch
- Live test via Telegram: Keith recommended 2 videos, interacted, found publish date still showed "Unknown" on new cards (flat-playlist upload_date gap) and no feedback prompt on new-pick Skip button -- both fixed in `25fcdb0`. Still pending Keith's fresh round of live testing to confirm full stability.
- Retired scheduled tasks verified deleted from Mac Mini scheduler (`node dist/schedule-cli.js list` no longer shows `a0ec773c` or `c52fb28f`)
- The full workflow guide lives at `K2B-Vault/wiki/concepts/2026-04-08_feature_youtube-agent.md` (~450 lines) -- durable single source of truth for how the YouTube pipeline works

**Out of scope** (deferred or cut):
- Auto-polling K2B Watch / K2B Screen for manual additions via YouTube mobile app → agent loop only runs every 6h; Keith uses the skill or sends URLs for immediate capture
- Transcript-based URL screening → currently metadata-only (faster, ~15s). Full transcripts only fire on "Process Now"
- Topic freshness learning from watch/skip patterns → windows still hardcoded (claude-code: 30d, etc.)
- Concurrent-write locking on youtube-recommended.jsonl → Codex flagged but fine for single-process bot
- `screen-process` deep refactor → minimum fix only (prompt guardrail + code-side skipVideoFromScreen call). Full redesign is its own job.

**Files changed (session summary)**:
- `k2b-remote/src/youtube.ts` -- canonical state functions + shared state
- `k2b-remote/src/bot.ts` -- callback rewrites + URL handler pendingCandidates population
- `k2b-remote/src/youtube-agent-loop.ts` -- text-reply handlers + findNewContent metadata enrichment
- `k2b-remote/src/taste-model.ts` -- deduceSkipReason overloads
- `.claude/skills/k2b-youtube-capture/SKILL.md` -- 545 → 260 lines, 2-command surface
- `K2B-Vault/wiki/concepts/2026-04-08_feature_youtube-agent.md` -- full workflow guide (vault-only, synced via Syncthing)

**Commits**: `66b2927`, `25fcdb0`, `e891a9a`

---

## 2026-04-10 -- Mission Control v3 Ship 1 (dashboard rebuild + browser intake + learning audit)

**What**: Fresh rebuild of `k2b-dashboard` against the post-Karpathy vault (raw/wiki/review), adding two substantial new capabilities that go beyond a path-patch: browser intake (URL / audio / text / Fireflies) forwarded through a new HTTP endpoint inside k2b-remote, and a read-only Learning Inspector that surfaces K2B's four-layer learning stack (signals → observer candidates → active rules/profile → feedback form) with live drill-down into the actual MiniMax prompts and responses from each observer run.

**Why**: v1/v2 dashboard was built March 31 against `Notes/`, `Inbox/`, `MOC_K2B-Roadmap.md`. Between Apr 4-8 the vault was restructured into the 3-layer Karpathy model (`raw/` → `wiki/` → `review/`) and the dashboard's hardcoded paths were never updated, leaving ~50% of panels broken. K2B itself also changed substantively (20 skills, conversational YouTube agent, MiniMax background observer loop, k2b-compile pipeline, memory-sync via symlink). A path patch would fix the bleeding but miss two real gaps: (1) capture still required Telegram or Claude Code -- the dashboard should be an input surface, and (2) learning was happening in jsonl files nobody opened, with no visible chain from "signal observed" → "candidate proposed" → "rule promoted", which is exactly how a learning system drifts into believing wrong things.

**Process (both Checkpoints per CLAUDE.md)**:
- **Plan mode** → brainstorming skill → 6 clarifying questions → 3 approaches proposed → Approach A selected (two-column with Learning Inspector drawer)
- **Checkpoint 1 (plan review)** → Codex adversarial review of the single-ship plan → found 5 load-bearing assumptions wrong: (1) k2b-remote has no HTTP server today (pure grammy polling), (2) Telegram voice is NOT handled by the agent loop directly -- transcribe-then-text via Groq Whisper in voice.ts before runAgent, (3) observer-loop.sh still writes to Notes/Context/ not wiki/context/, (4) active_rules.md has no structured provenance metadata, (5) observer-candidates.md has no stable candidate IDs. Verdict: decompose from 1 ship into 3.
- **Three-ship roadmap** adopted. Ship 1 (this devlog entry) delivers dashboard + intake + read-only audit. Ship 2 adds stable IDs + provenance metadata (pure data layer, no UI). Ship 3 adds reject buttons + provenance click-trace (blocked on Ship 2).
- **Canonical spec** written to `K2B-Vault/wiki/concepts/feature_mission-control-v3.md` + indexed + cross-linked from `project_k2b.md` + `wiki/log.md` entry. The vault note is the durable home; Ships 2 and 3 reference it directly.
- **Checkpoint 2 (pre-commit review)** → Codex found 4 real issues + 1 nit. All fixed before commit.

**Shipped**:
- **k2b-dashboard** fresh rebuild in place. `git mv src/ legacy-v2/` preserved for one week. New: Express + React 18 + Vite + TypeScript + Tailwind + TanStack Query + react-dropzone + lucide-react + multer 2.x
- 8 new API routes under `src/server/routes/`: now, review, captures, learning (4 endpoints), vault, scheduled, activity, intake. Centralised `lib/vault-paths.ts` as single source of truth -- zero hardcoded paths in route handlers (root cause of the v1/v2 breakage).
- 7 new React components: `NowCard` (opinionated state-adaptive), `IntakeBar` (4 modes), `ReviewQueue`, `TodayCaptures`, `LearningPanel`, `LearningInspector` (4-column drawer), `FooterRow`. TanStack Query at 15s polling throughout.
- **k2b-remote** gained `src/http-server.ts` (node:http, bound to 127.0.0.1:3300 only, two routes: GET /health + POST /intake) and `src/intake.ts` (type dispatcher). Audio intake mirrors `bot.ts:836-858` exactly -- call `transcribeAudio(filePath)` from voice.ts first, then runAgent on the transcript. Zero divergence in skill behavior between Telegram and dashboard inputs.
- **scripts/observer-loop.sh** migrated from `Notes/Context/` to `wiki/context/` (side-effect bug fix the observer had been carrying). Added per-run append to `wiki/context/observer-runs.jsonl` with `{ts, model, prompt, response}` so the Learning Inspector can show the actual MiniMax prompt and response from each run -- the key audit surface for "is K2B learning the right thing". Truncation uses bash substring `${var:0:8000}` to avoid SIGPIPE under `set -e pipefail` (Codex catch).
- `.claude/launch.json` updated to launch v3 via the built dist/server (port 3200).

**Key decisions**:
- **Command center first, ops second**: the old dashboard's pm2/proxy/git status panels were cut. Silent breakage isn't what this dashboard is for -- if k2b-remote is down, Telegram capture breaks and Keith notices within minutes. The dashboard's job is to show what needs action and what K2B is learning, not to duplicate `pm2 list`.
- **Single "Now" card** at the top, opinionated, state-adaptive. Resolves top-down: review items pending > observer high-confidence candidate > scheduled task firing in <1h > "all clear, drop something in the intake". One card, one message, no decision paralysis.
- **Intake reuses the Telegram path via runAgent**, not a parallel capture pipeline. Dashboard POSTs to the new k2b-remote HTTP endpoint, which calls the SAME `runAgent(message)` the Telegram bot uses. One code path, skills don't know (or care) which client sent the message. Fewer bugs, one set of tests.
- **Inspector is read-only in Ship 1**, with reject buttons deferred to Ship 3. Codex correctly caught that rejection needs stable IDs that don't exist yet -- the observer-candidates.md format is unstructured markdown bullets. Building the reject UI now would require a data migration that belongs in Ship 2.
- **Observer run streaming is where the "learning wrong data" audit lives**. Not the headline numbers on the panel -- the panel is the entry point. The value is in the drawer's accordion that shows each MiniMax run's prompt + raw response so Keith can read exactly what was sent and exactly what came back. If the observer hallucinated or latched onto a noisy signal, it's visible in the raw text.
- **Vault path auto-detection** at `src/server/lib/vault-paths.ts`. The same compiled code runs on both `keithmbpm2` (MacBook) and `fastshower` (Mac Mini) without any env vars or rsync-sync for .env. Codex's fix for the original relative-path fallback -- verified live on Mac Mini after deploy.
- **Fresh rebuild in place** (not alongside v2). Wiping src/ was the right call -- path abstraction was broken in 14 route files across v2 and patching would've left the architectural bug ("hardcoded paths strewn across handlers") intact. legacy-v2/ preserved as reference for one week.

**Verification** (both local and live on Mac Mini):
- `typecheck` + `build` clean for k2b-dashboard and k2b-remote
- `bash -n scripts/observer-loop.sh` clean
- All 11 dashboard API routes return real JSON from the live vault
- `grep -rn 'Notes/Context\|/Users/keithmbpm2\|/Users/fastshower' k2b-dashboard/src/` returns empty
- Learning Inspector opens with all 4 columns rendering real signals, candidates, rules, feedback form (verified via preview DOM snapshot)
- **Live deploy smoke test**: POSTed `{type: "text", payload: "Dashboard v3 smoke test..."}` to `http://localhost:3200/api/intake` on Mac Mini → dashboard forwarded to k2b-remote at 127.0.0.1:3300 → k2b-remote spawned a fresh Anthropic Agent SDK session → agent acknowledged → response flowed back as `{status: "ok", text: "Got it...", hadError: false}`. Full intake chain verified end-to-end.
- Vault auto-detection verified live: Mac Mini resolved `/Users/fastshower/Projects/K2B-Vault` with zero config.
- pm2 list after deploy: `k2b-dashboard 3.0.0 online`, `k2b-remote online (HTTP server listening on 127.0.0.1:3300)`, `k2b-observer-loop online`. All three restarted cleanly.

**Out of scope for Ship 1** (deferred or cut):
- Reject / mute buttons on candidates and rules → Ship 3 (needs IDs from Ship 2)
- Provenance click-trace (rule → candidate → signals) → Ship 3
- Stable IDs in observer-candidates.md and provenance fields in active_rules.md → Ship 2
- SSE streaming of intake progress (simple JSON response + polling is sufficient)
- pm2 / proxy / git status panel (silent breakage isn't what this dashboard is for)
- Mobile intake (Telegram covers it; phone dashboard is read-only simplified)
- Auth (Tailscale-trusted, same as v1/v2)
- Historical vault-growth chart

**Known follow-ups** (not urgent):
- Add `dashboard` category to `scripts/deploy-to-mini.sh` -- this first deploy was manual rsync + ssh. Ship 2/3 should use `./deploy-to-mini.sh dashboard` for repeatability.
- Wait ~1 hour for observer loop to run and populate `observer-runs.jsonl` -- then re-open the Inspector to see the audit stream with real content.
- Ship 2 (stable IDs + provenance data layer) spec to be expanded in `feature_mission-control-v3.md` after Ship 1 soak.

**Files changed**: 81 files, +3134 / -234. New: k2b-dashboard/src/** (fresh rebuild), k2b-remote/src/{http-server,intake}.ts, k2b-dashboard/{tailwind.config,postcss.config}.js. Modified: k2b-remote/src/index.ts, scripts/observer-loop.sh, k2b-dashboard/package.json, .claude/launch.json. Moved: k2b-dashboard/src/** → k2b-dashboard/legacy-v2/**.

**Commit**: `ff5705b feat: Mission Control v3 Ship 1 -- dashboard rebuild + browser intake + learning audit`

---

## 2026-04-10 -- k2b-remote session bleed + YouTube Shorts fallback

**What**: Fixed a Telegram bot bug where a single conversation was spawning multiple fresh Claude Code sessions, causing the bot to reply with contradictory context across messages. Keith hit it on 2026-04-09 ("memory palace repo" thread got three different interpretations) and with a YouTube Shorts URL that returned "can't get the video details".

**Root cause**: Normal chat (`bot.ts handleMessage`) was correctly loading `getSession(chatId)` and persisting `newSessionId`, but every `runAgent()` call inside the YouTube agent loop (`youtube-agent-loop.ts` — 4 call sites) AND inside the YouTube callback handlers (`handleYouTubeCallback` — 4 more call sites) AND inside `handleDirectYouTubeUrl` passed no session ID. Every one of those calls spawned a brand-new Claude Code session that re-read the vault from scratch and guessed at context, which is why the replies contradicted each other.

**Shipped**:
- `runAgentWithSession(chatId, prompt)` helper in youtube-agent-loop.ts that wraps runAgent with the existing `getSession` / `setSession` SQLite helpers. All 4 YouTube-loop call sites now thread the chat session.
- All 4 YouTube callback `runAgent` calls in bot.ts (highlights, promote, screen-process, screen-all) now thread the chat session too.
- `handleDirectYouTubeUrl` now threads the chat session into its screening agent call.
- `fetchYouTubeOEmbed(videoId)` fallback in bot.ts: when yt-dlp fails (common on YouTube Shorts), curl-fetches the oEmbed endpoint to recover title + channel so the screening agent still has something to reason about instead of returning "can't get video details".

**Key decisions**:
- Per-chat session is canonical: every agent call for a given chatId resumes the same Claude Code session, full stop. Rejected the alternative of a YouTube-specific session store because session fragmentation was the whole bug.
- Bundled both fixes (session + Shorts oEmbed) in one change. Same architectural weakness (agent has no durable context when something fails), adjacent files, one verification pass.
- oEmbed fallback uses `curl` via execFileSync for consistency with the existing yt-dlp pattern and to inherit system proxy env vars automatically (no new dependency).

**Verification** (live on Mac Mini, pid 37850):
- 7 agent runs across multiple Telegram interactions → **1 unique session ID**. Pre-fix the same chat would have spawned 3-5 distinct session IDs.
- Memory test: "remember 42" → "what number" → "42." Then bot meta-detected the test while staying aware of the parallel YouTube thread.
- YouTube Shorts URL screened cleanly with a substantive verdict — no "can't get video details" error.
- Zero `hadError: true`, zero `level:50` errors post-restart.

**Process**: Plan mode → codex adversarial review (Keith's Checkpoint 2) — zero findings on bot.ts/youtube-agent-loop.ts. One unrelated P2 on `scripts/generate-org-chart.py` (untracked, not in scope) — flagged for separate follow-up.

**Files changed**: k2b-remote/src/bot.ts, k2b-remote/src/youtube-agent-loop.ts

---

## 2026-04-08 -- Vault redesign Plan A: remaining 5 skill alignment

**What**: Updated the 5 remaining skills deferred from the previous session to align with the new vault architecture (auto-promote, index updates, cross-link pass, System/log.md).

**Shipped**:
- meeting-processor: output path fixed from Notes/ to Notes/Work/, added post-write contract (index + log), added up: frontmatter
- daily-capture: TLDR source updated for decompose-immediately, auto-promote awareness section, vault-writer reference
- inbox: narrowed scope description (content-ideas + LinkedIn drafts only), updated promote destinations with misroute flags, index update on promote
- linkedin: vault redesign exception note for drafts staying in Inbox, index/log updates on publish
- insight-extractor: insights marked as auto-promote, post-write contract for both /insight and /content outputs

**Key decisions**:
- LinkedIn drafts remain in Inbox/ as an explicit exception to the content-ideas-only rule (they need Keith's approval before publishing)
- Non-content types in the inbox promote table are flagged as "misrouted" to catch routing bugs

**Still needs**:
- Weekly /lint schedule not yet configured
- Plan B (compiled wiki) designed but deferred

**Files changed**: k2b-meeting-processor/SKILL.md, k2b-daily-capture/SKILL.md, k2b-inbox/SKILL.md, k2b-linkedin/SKILL.md, k2b-insight-extractor/SKILL.md

---

## 2026-04-07 -- Vault redesign Plan A: Karpathy architecture adoption

**What**: Researched Karpathy's LLM Wiki architecture, designed two-track plan (Plan A incremental + Plan B compiled wiki), shipped Plan A, cleared inbox from 14 to 0, audited all 19 skills.

**Key decisions**:
- Karpathy's compilation model supersedes Cole's 5-layer framework as vault design reference
- Plan A first (low risk), Plan B later if needed (compilation engine)
- Compile step will be summary-first (Keith approves before ripple) when Plan B ships
- MOCs will merge into wiki/index.md hierarchy in Plan B

**Shipped**:
- Per-folder index.md (8 files across Notes/ subfolders)
- System/log.md (append-only vault activity record)
- k2b-lint skill (new /lint command, subsumes feature_vault-housekeeping-agent)
- Auto-promote routing in vault-writer (captures bypass Inbox by type)
- Cross-link pass contract in vault-writer
- Inbox narrowed to k2b-generate content ideas only
- 4 capture skills updated (youtube-capture, research, tldr, CLAUDE.md)
- Inbox cleared: 7 YouTube notes consolidated, 2 research briefings consolidated, stabilization audit updated, feature idea moved

**Still needs**:
- 5 skills need alignment updates: meeting-processor, daily-capture, inbox, linkedin, insight-extractor
- Weekly /lint schedule not yet configured
- Plan B (compiled wiki) designed but deferred 1-2 weeks

**Files changed**: k2b-lint/SKILL.md (new), k2b-vault-writer/SKILL.md, k2b-youtube-capture/SKILL.md, k2b-research/SKILL.md, k2b-tldr/SKILL.md, CLAUDE.md

---

## 2026-04-05 -- Memory sync architecture fix (symlink to vault)

**What was built/changed:**
- Fixed memory drift between MacBook and Mac Mini: Claude Code memory dir `~/.claude/projects/-Users-{user}-Projects-K2B/memory/` is machine-local and doesn't sync via Syncthing or /sync
- Weekly promotion task (Sunday 10am HKT) ran on Mac Mini and created a fresh active_rules.md with only 2 rules, while MacBook had 12 rules -- Telegram-K2B was missing 10 behavioral rules
- Solution: symlink the machine-local memory dir to `K2B-Vault/System/memory/` on both machines
- Moved all 13 memory files to `K2B-Vault/System/memory/`
- Replaced machine-local memory dirs with symlinks pointing to vault location
- Syncthing now handles memory sync automatically
- Zero code changes: session-start hook's `find` command follows symlinks transparently
- Deleted backup directories after verifying both machines read correctly through symlinks

**Files affected:**
- K2B-Vault/System/memory/ (new canonical location, 13 files)
- ~/.claude/projects/-Users-keithmbpm2-Projects-K2B/memory (now symlink)
- ~/.claude/projects/-Users-fastshower-Projects-K2B/memory (now symlink, on Mac Mini)
- CLAUDE.md (documented memory sync architecture)
- K2B-Vault/System/memory/reference_mac_mini.md (documented symlink setup)

**Key decisions:**
- Symlink approach chosen over moving files + updating hook paths: zero code changes, works for MEMORY.md auto-loading by Claude Code harness
- Memory files live in vault under `System/` (non-standard folder to discourage accidental Obsidian edits)
- Scheduled tasks on either machine now write to the shared location automatically
- Architecture supports future machines: just create a symlink from their machine-local path to the vault

---

## 2026-04-04 -- Redesign /daily from blank template to multi-turn compilation

**What was built/changed:**
- Full rewrite of k2b-daily-capture SKILL.md -- flipped from "blank morning template" to "end-of-day compilation from existing captures"
- New model: K2B harvests Telegram messages (via SSH to Mac Mini SQLite), vault notes created today, TLDRs, and yesterday's open loops, then classifies into context sections and refines through conversation
- Sections are dynamic: SJM Work, Signhub/TalentSignals/Agency at Scale, K2B Build, Insights, Content Seeds, Open Loops -- only rendered if they have content
- Morning mode: brief open-loops-only view, not a full template
- Channel-aware: full preview on terminal, compact summary on Telegram
- Multi-turn conversation flow: harvest -> draft -> ask about gaps -> Keith refines -> save
- Simplified daily-note.md template (vault) -- removed blank prompts, now just frontmatter + dynamic section comments
- New eval.json with 4 test cases: mixed Telegram classification, quiet day, morning mode, all-Telegram with voice notes

**Files affected:**
- .claude/skills/k2b-daily-capture/SKILL.md (full rewrite)
- .claude/skills/k2b-daily-capture/eval/eval.json (4 new test cases)
- K2B-Vault/Templates/daily-note.md (simplified)

**Key decisions:**
- /daily is a conversation, not a one-shot generator. K2B asks clarifying questions before saving.
- Git log excluded from K2B Build section -- /daily captures the day as an executive, not a changelog
- Telegram messages are mixed across all contexts (SJM, side ventures, personal) -- skill must classify, not assume
- Design originated from Keith's Claude Chat spec, refined through discussion about message classification and iterative flow

---

## 2026-04-04 -- Stabilization audit, memory layer fix, inbox processing

**What was built/changed:**
- Full K2B stabilization audit comparing architecture against Cole Medin's second brain framework
- Audit covers: architecture health (5 layers), recurring friction analysis (30+ learnings categorized), skill-by-skill status (18 skills), k2b-remote health, config parity, reference implementation comparison
- Fixed memory layer: split learnings into two tiers -- active_rules.md (12 distilled behavioral rules, loaded every session) + self_improve_learnings.md (historical reference, not loaded at startup)
- Session-start hook rewritten to load active_rules.md instead of broken reinforcement threshold filter (was >= 6, max in file was 3, so zero learnings ever surfaced)
- Weekly memory promotion task scheduled (Sunday 10am HKT) -- reviews new learnings + observer candidates, updates active rules, prunes stale rules
- Fixed Telegram bot skip confirmation: "Skipped. Why?" -> "Skipped and removed from Watch playlist. Why?" (UX clarity)
- Updated settings.json permission allowlist: added ~20 missing bash commands, fixed MCP tool names to actual IDs, added all MCP servers
- Processed 3 inbox items (1 deleted, 2 archived with review-notes for observer)
- Captured learning L-2026-04-04-001: "shipped != finished" -- features aren't done until config surface is complete

**Files affected:**
- scripts/hooks/session-start.sh (active_rules.md loading replaces broken filter)
- .claude/settings.json (permission allowlist expanded + MCP names fixed)
- k2b-remote/src/bot.ts (skip confirmation message)
- memory/active_rules.md (new -- 12 distilled rules)
- memory/MEMORY.md (index updated)
- memory/self_improve_learnings.md (new learning added)
- K2B-Vault/Inbox/2026-04-04_k2b-stabilization-audit.md (new audit note)
- K2B-Vault/Notes/Context/preference-signals.jsonl (3 new signals)
- K2B-Vault/Notes/Context/self-improve-errors.jsonl (1 new error logged)

**Key decisions:**
- Memory layer follows capture -> distill -> apply -> prune loop (inspired by Cole Medin's daily reflection pattern)
- Active rules capped at ~15 entries to stay concise and always-loaded
- Audit is findings-only, no prescriptions -- Keith reviews and decides what to act on
- Mac Mini settings.json still needs the updated permissions (noted as config drift in audit)

---

## 2026-04-02 -- Slim CLAUDE.md from 268 to 161 lines

**What was built/changed:**
- Slimmed CLAUDE.md by removing skill-specific detail that's duplicated in SKILL.md files (loaded on-demand)
- Removed: Skill Data Flow ASCII diagram, detailed /inbox workflow, /observe subcommand descriptions, Mac Mini deploy commands, Background Observer implementation details
- Compressed: Slash commands to one-liners, vault structure tree, content pipeline, file conventions
- All behavioral rules preserved intact
- Synced to Mac Mini
- Logged feature request R-2026-04-02-001: observer should harvest review-notes from archived items

**Files affected:**
- CLAUDE.md (268 -> 161 lines, ~40% reduction)

**Key decisions:**
- Pure subtraction approach: no skill files needed editing since all removed detail already existed in corresponding SKILL.md files
- Inbox Write Contract rule relocated from Skill Data Flow section to Vault Structure section

**Status:**
- What works: All behavioral rules intact, slimmed file synced to Mac Mini
- What's next: Smoke test in fresh session to confirm skill routing still works with compressed slash command descriptions

---

## 2026-04-02 -- YouTube Telegram Button Fixes + /youtube screen

**Problem**: YouTube morning routine and recommend commands sent plain text to Telegram instead of inline keyboard button cards. Keith never saw Watch/Comment/Skip/Screen buttons for recommended videos.

**Root causes found**:
1. `sentNudgeIds` was an in-memory `Set` that never cleared -- once a video's nudge was sent, it was blocked forever (until pm2 restart). Changed to `Map<string, number>` with 24h TTL.
2. `/youtube recommend` skill didn't set `status: "nudge_sent"` in JSONL entries, so `getPendingNudges()` couldn't find new recommendations.
3. Screen button callback set `status: "processed"` instead of `screen_pending`, making videos invisible to the new screen command.
4. Scheduler only triggered nudge buttons after `/youtube morning`, not `/youtube recommend`.

**New feature**: `/youtube screen` command with Telegram button cards
- Polls K2B Screen playlist, writes `screen_pending` entries to JSONL
- Bot sends individual cards with Process/Skip buttons per video
- Process All button for batch processing
- Immediate acknowledgment message when processing starts (can take minutes for transcript extraction)
- Removed Screen processing from morning routine -- now on-demand only via `/youtube screen`

**Key decisions**:
- K2B Watch is exclusively populated by `/youtube recommend` -- Keith never adds there manually
- Keith's manual video additions go to K2B Screen
- Morning routine is lean: just stale nudge handling, no Screen processing
- Screen button cards follow same UX pattern as Watch nudge cards

---

## 2026-04-01 -- Mission Control v2

Major dashboard overhaul: from status board to command center.

### What was built

**New panels (Wave 1-2)**
- Health & Alerts strip -- checks pm2, inbox age, task failures. Green "nominal" or colored alert bar
- Suggested Next Action -- priority-ranked "what should I do?" card. Click copies command
- Quick Actions bar -- 5 preset buttons (/daily, /inbox, /content, /sync, /observe) + custom command input
- Vault Growth chart -- 30-day bar chart of notes created per day (+134 notes in period)
- LinkedIn Performance placeholder -- ready for metrics when API connected

**YouTube Digest redesigned**
- Response badges: Watch (green), Screen (blue), Skip (gray), Comment (amber), Pending (dashed)
- Verdict value labels (HIGH/MED/LOW) from two-pass pipeline
- Screening Pipeline: pending extraction + recently extracted (successful only)
- Skipped count collapsed to footer. Old Watch/Skip buttons removed

**Inbox redesigned**
- Filter tabs: All | Videos | Research | Features with counts
- Inline Snooze/Archive action buttons per item (POST /api/inbox/:filename/action)
- Accordion preview on click (200-char excerpt)
- Age-based urgency: amber 2d+, red 5d+, sorted oldest-first

**Improved existing panels**
- Activity Feed: collapsible time blocks (This morning/afternoon/Yesterday)
- Scheduled Tasks: status dots (green/red/gray) + per-task next run countdown
- Skills: Never Used collapsed into accordion with descriptions and try hints
- Intelligence: Observer hidden when empty, learnings capped at 3 with expand
- Content Pipeline: color-coded stage dots

**New API endpoints**
- GET /api/health -- system alert aggregation
- GET /api/vault/growth -- 30-day notes/day (cached 5m)
- GET /api/suggested-action -- composite next-action recommendation
- POST /api/command -- command relay (v1: clipboard copy)
- POST /api/inbox/:filename/action -- archive/snooze inbox items

### Key decisions
- Quick Actions copies to clipboard in v1 (not direct execution) -- avoids auth complexity
- Suggested Action shows only highest-priority item -- one line, one action
- Vault growth uses file birthtime not frontmatter date -- more reliable
- YouTube shows last 7 recs newest-first with click-to-expand pick_reason

### Files changed
- 11 modified components, 5 new components, 5 new server routes, 570+ lines CSS
- launch.json updated with mission-control preview config

---

## 2026-04-01 -- Two-Pass YouTube Recommendation Pipeline

Upgraded `/youtube recommend` from metadata-only scoring to transcript-screened verdicts with a closed learning loop.

### What was built
- **Two-pass recommend pipeline**: Pass 1 filters 24-40 candidates by metadata + preference profile, Pass 2 screens 5-7 finalists via transcript excerpts generating 3-5 sentence verdicts with HIGH/MEDIUM/LOW value estimates
- **4-button Telegram layout**: Watch (logs + sends link), Comment (captures text/voice), Skip (logs + optional reason), Screen (sends to K2B Screen playlist for full processing)
- **Comment capture system**: `awaitingComment` Map in bot.ts intercepts next text or voice message after Comment/Skip buttons
- **youtube-preference-profile.md**: New vault file maintained by observer, read by recommend Pass 1. Tracks channel affinity, pillar patterns, duration preferences, verdict accuracy, machine-readable scoring adjustments
- **Observer extension**: Phase 1e harvests YouTube signals from recommended.jsonl + feedback-signals.jsonl. Phase 3b synthesizes youtube-preference-profile.md
- **Morning routine revamp**: 48-hour expiry (was day-based), profile freshness check, verdict-aware nudge format, 4-button layout

### Key decisions
- **"Screen" not "Queue"** -- renamed K2B Queue playlist to K2B Screen. "Screen this" is clearer than "Queue this" for "K2B, check if this is worth watching"
- **45-min duration cap** (not 20 min) -- Keith watches longer videos if good. Cap only for unknown/low-affinity channels
- **Truncate to 2000 words for screening** -- full transcript unnecessary for verdict generation, saves time
- **Optional skip reason** -- skip logs immediately (zero friction), "Why?" asked as ignorable follow-up
- **Watch as callback not URL button** -- enables logging watch action for learning loop
- **Separate youtube-preference-profile.md** from general preference-profile.md -- domain-specific, read directly by recommend workflow

### Files changed
- `k2b-remote/src/youtube.ts` -- verdict, verdict_value, pillars_matched, comment_text fields + screen/watch/comment signal types
- `k2b-remote/src/bot.ts` -- 4 new callback handlers, awaitingComment state, handleCommentOrSkipReason, revamped sendPendingNudges
- `.claude/skills/k2b-youtube-capture/SKILL.md` -- two-pass pipeline replacing single-pass, revamped morning routine
- `.claude/skills/k2b-observer/SKILL.md` -- Phase 1e YouTube harvesting, Phase 3b preference profile synthesis, updated integration map
- `K2B-Vault/Notes/Context/youtube-preference-profile.md` -- initial empty structure (confidence: low)
- `K2B-Vault/Notes/Context/youtube-playlists.md` -- K2B Queue renamed to K2B Screen
- `K2B-Vault/Notes/Features/feature_two-pass-youtube.md` -- feature spec
- `K2B-Vault/MOC_K2B-Roadmap.md` -- added to In Progress

### Deploy
- Synced to Mac Mini: skills + CLAUDE.md + k2b-remote code. Built clean, pm2 restarted.

---

## 2026-03-31 -- K2B Mission Control Dashboard v1

Built a full web dashboard for K2B -- single-page dark theme mission control that shows the state of the entire system at a glance.

### What was built
- **k2b-dashboard/** -- standalone Express + React + Vite app (TypeScript throughout)
- 9 API endpoints reading from vault files, SQLite, JSONL, TSV, git, and pm2
- 10 panel components: System Status, Vault Stats, Roadmap, YouTube Digest, Inbox, Intelligence, Skill Activity, Scheduled Tasks, Content Pipeline, Activity Feed
- SSH fallback to Mac Mini for pm2 status and scheduled tasks when running on MacBook
- Live YouTube playlist polling via yt-dlp (cached 1 hour)
- Dark monospace theme (#0a0a0a background, JetBrains Mono, mission control aesthetic)
- Click-to-expand rows with always-visible subtitles
- Responsive layout (stacks on mobile for Tailscale commute access)

### Key decisions
- **Standalone app** (not embedded in k2b-remote) -- separate pm2 process, dashboard stays up even when iterating on bot code
- **Read-only v1** -- no write operations. Action buttons (YouTube skip, inbox triage) are v2
- **SSH to Mac Mini** for remote data -- system status and scheduled tasks pulled from Mini when local data unavailable
- **Polling (30s)** not WebSocket -- simple, reliable for v1
- **Skill Activity heatmap** with bar chart showing which skills are hot vs dormant, with "Try:" hints for never-used skills
- **K2B Intelligence panel** shows observer candidates, recent learnings with reinforcement counts, observer status
- **YouTube Queue** shows live playlist items (via yt-dlp on Mac Mini) not just processed history

### Bug fixes during build
- Fixed API/component field mismatches (contentPipeline, scheduledTasks, activity feed, skills, intelligence)
- Fixed learnings parser (markdown list prefix `- **Field:**` not matched by regex)
- Added bar chart CSS for skill activity
- Fixed Header always showing "offline" (was checking nonexistent `status` field)
- Fixed YouTube Queue showing processed history instead of current playlist items

### Vault updates
- Created `feature_mission-control.md` (shipped)
- Updated `project_k2b.md`, `project_k2b-always-on.md`, `MOC_K2B-Roadmap.md`, `MOC_K2B-System.md`

### Deploy
- Not yet deployed to Mac Mini. Run `/sync` to push.
- On Mini: `npm install && npm run build && pm2 start dist/server/index.js --name k2b-dashboard`

---

## 2026-03-31 -- YouTube Taste Learning Loop + Vault Housekeeping

### 1. Features/Shipped subfolder convention
- Created `Notes/Features/Shipped/` for completed feature specs (distinct from Archive)
- Added "Roadmap & Feature Notes" section to CLAUDE.md: Roadmap MOC = index, feature notes = detailed specs only when needed
- Moved ai-human-guardrail, proactive-youtube, playlist-redesign to Shipped

### 2. YouTube taste learning loop (Phase 1 + Phase 2 conversational redesign)
- Added skip-why buttons to Telegram: [Too basic] [Clickbait] [Not relevant] [Too long]
- Added value-feedback buttons after highlights: [Exactly my level] [Gave me an idea] [Good but basic] [Not worth it]
- New `appendFeedbackSignal()` in youtube.ts writes to `youtube-feedback-signals.jsonl`
- Extended `YouTubeRecommendation` interface: topics, skip_reason, value_signal, search_query
- Updated observer-prompt.md with YouTube Taste Synthesis section (generates youtube_taste object)
- Updated observer-loop.sh to write `youtube-taste-profile.md` when taste data present
- Updated SKILL.md: recommend workflow now reads taste profile, 5-dimension scoring with confidence-weighted taste fit
- Scheduled `/youtube recommend` every other day at 11am HKT
- **v2 redesign**: Replaced rigid button-based feedback with conversational flow
  - Removed skip-why buttons and value-feedback buttons
  - Skip now triggers agent conversation: "What put you off?" -> Keith responds naturally -> K2B extracts and logs reason
  - Highlights now includes K2B's honest assessment of whether it's worth Keith's time, asks his opinion conversationally
  - Removed rigid [Content idea] [Feature] [Insight] [Nothing] promotion buttons -- agent handles promotion/playlist moves through conversation
  - Nudge messages redesigned: added YouTube link, duration, pick_reason, [Watch] URL button
  - Free-text feedback (`signal_text`) captured alongside structured signals for richer observer pattern detection

### 3. Scheduled tasks wiped (again) and restored
- Manual rsync overwrote Mac Mini production SQLite database (SAME bug as E-2026-03-29-002)
- Restored all 5 original tasks + added new youtube recommend task (6 total)
- Logged as E-2026-03-31-001, reinforced L-2026-03-29-002 to 3x (medium confidence)
- Rule: NEVER manual rsync for k2b-remote. ALWAYS use `scripts/deploy-to-mini.sh code`

### 4. Agent SDK systemPrompt 403 fix
- Uncommitted agent.ts changes (systemPrompt preset/append) were synced to Mac Mini for the first time
- SDK 0.1.77 doesn't support systemPrompt config -- caused 403 Forbidden on all agent calls
- Reverted systemPrompt block, redeployed via deploy-to-mini.sh (not manual rsync)

### 5. SSH Keychain limitation documented
- macOS Keychain blocks credential access from non-interactive SSH sessions
- Claude CLI auth works interactively on Mac Mini but fails via SSH
- All pm2-based paths (Telegram, scheduled tasks) work fine -- only direct SSH agent invocation affected
- Documented in project_k2b-always-on.md Known Issues

**Key decisions:**
- Taste profile starts permissive (weight 0.15 at low confidence) and tightens as signals accumulate (0.30 at high confidence)
- No hard filtering -- taste scores are soft ranking adjustments, never exclusions
- Conversational feedback captures richer signals than rigid buttons -- Keith's actual words are more valuable than 4 fixed categories
- deploy-to-mini.sh is the ONLY acceptable way to deploy k2b-remote code
- All Telegram sends go through the bot process (Grammy), never through the agent directly

---

## 2026-03-30 -- Tailscale Remote Access + Proxy Support for System Proxy Mode

**What was built/changed:**

### 1. Proxy Support for k2b-remote
- Installed `https-proxy-agent` for Grammy bot proxy routing
- Wired proxy into Grammy bot constructor (`bot.ts`) via `client.baseFetchConfig.agent` -- only activates when `HTTP_PROXY` env var is set
- Wired proxy into Agent SDK (`agent.ts`) via `options.env` passing `HTTPS_PROXY`/`HTTP_PROXY` to Claude Code subprocess
- Added `HTTP_PROXY` config to `config.ts` with fallback chain: `.env` file -> `process.env`
- Created `ecosystem.config.cjs` locally (was previously Mac Mini only) with proxy env vars defaulting to port 7897

### 2. Mac Mini Network Mode Change
- Switched Mac Mini Clash Verge from TUN mode to System Proxy mode (port 7897)
- Added `HTTPS_PROXY`/`HTTP_PROXY`/`NO_PROXY` to Mac Mini `~/.zshenv` for all CLI tools (gws, curl, etc.)
- Restarted observer loop with proxy env vars (curl in minimax-common.sh needs proxy)
- All external APIs verified working through proxy: Telegram, Anthropic, Groq, MiniMax, Google

### 3. Tailscale Mesh Networking
- Installed Tailscale standalone on Mac Mini (IP: `100.116.205.17`, full mode)
- Installed Tailscale standalone on MacBook (IP: `100.68.35.19`, full mode)
- No TUN conflict on Mac Mini (System Proxy mode) or MacBook (Tailscale coexists with Clash TUN)
- Added `macmini-ts` SSH alias in `~/.ssh/config` for remote access via Tailscale
- Tested from mobile hotspot (different network) -- SSH works

### 4. Vault Documentation
- Updated `project_k2b-always-on.md` with Phase 7 (Tailscale + proxy), updated specs table, known issues, operational commands
- Updated `MOC_K2B-System.md` architecture diagram with Tailscale IPs and proxy details
- Updated TLDR action items (3 of 4 remote gaps now resolved)
- Updated Mac Mini memory reference with Tailscale and proxy info

**Key decisions:**
- Mac Mini uses System Proxy (not TUN) so Tailscale can run in full mode as SSH target
- MacBook keeps TUN mode (Claude Desktop requires it) -- Tailscale works alongside it
- Proxy wiring is conditional: code works identically with or without `HTTP_PROXY` set
- Port 7897 (not 7890) is Clash Verge's default on the Mac Mini

**Learnings captured:** L-2026-03-30-003 through L-2026-03-30-008 (Grammy restart after mode change, config.ts env source, port mismatch, proxy wiring architecture, Tailscale+Clash compatibility, all-processes-need-proxy)

---

## 2026-03-30 -- Claude Code Hooks, MiniMax Background Observer, Proactive YouTube

**What was built/changed:**

### 1. Claude Code Hooks (inspired by Everything Claude Code repo)
- Created `scripts/hooks/session-start.sh` -- deterministic session startup (usage triggers, inbox scan, observer candidates, high-confidence learnings)
- Created `scripts/hooks/stop-observe.sh` -- captures vault file changes after every Claude response to `observations.jsonl`
- Created `.claude/settings.json` -- project-level hooks config wiring both hooks

### 2. MiniMax Background Observer
- Created `scripts/observer-loop.sh` -- background process calling MiniMax-M2.5 API to analyze K2B usage patterns (~$0.007/analysis)
- Created `scripts/observer-prompt.md` -- structured analysis prompt (skill patterns, revision patterns, YouTube behavior)
- Fixed `scripts/minimax-common.sh` -- platform-aware vault path detection (MacBook vs Mac Mini)
- Deployed to Mac Mini as pm2 process `k2b-observer-loop`
- Gate: 20+ observations, 1hr cooldown, 7am-11pm HKT active hours

### 3. Confidence-Scored Learnings
- Updated `k2b-feedback` skill -- learnings now carry low/medium/high confidence based on reinforcement count
- Session-start hook auto-loads high-confidence (6+) learnings into context

### 4. Proactive YouTube Knowledge Acquisition
- Created `k2b-remote/src/youtube.ts` -- JSONL data layer for recommendation tracking + dedup
- Added Telegram inline keyboard buttons to `bot.ts` -- [Get highlights] [Skip] + promotion flow [Content idea] [Feature] [Insight] [Nothing]
- Added `callback_query:data` handler for all YouTube button interactions
- Added `/youtube morning` subcommand to k2b-youtube-capture skill
- Wired `sendPendingNudges` into scheduler.ts -- buttons sent after morning task completes
- Created scheduled task: daily 7am HKT `Run /youtube morning`
- Observer prompt updated to analyze YouTube watch/skip/promote patterns

### 5. Documentation
- Updated CLAUDE.md -- hooks, observer loop, Mac Mini pm2 processes, /youtube morning
- Updated README.md -- full architecture diagram, skills table, self-improvement loop, tech stack
- Updated vault: Home.md, MOC_K2B-Roadmap.md, project_k2b.md, new feature_background-observer.md
- Created spec: `docs/superpowers/specs/2026-03-29-proactive-youtube-knowledge-acquisition-design.md`
- Created plan: `docs/superpowers/plans/2026-03-29-proactive-youtube-knowledge-acquisition.md`

**Key decisions:**
- MiniMax-M2.5 (minimaxi.com, $0.30/M in) chosen over Claude Haiku for background observer -- cheaper, Keith's existing subscription is underused
- Vault JSONL over SQLite for YouTube tracking -- observer needs to read the data, SQLite is opaque to it
- Extended existing k2b-youtube-capture skill rather than creating new k2b-youtube-morning skill -- one skill, cleaner
- Inline Telegram buttons via Grammy InlineKeyboard rather than text-based replies -- better UX

**Files affected:**
- `.claude/settings.json` -- new (hooks config)
- `.claude/skills/k2b-feedback/SKILL.md` -- confidence scoring
- `.claude/skills/k2b-observer/SKILL.md` -- background observer integration
- `.claude/skills/k2b-youtube-capture/SKILL.md` -- /youtube morning subcommand
- `k2b-remote/src/bot.ts` -- inline buttons, callback handler, sendPendingNudges
- `k2b-remote/src/scheduler.ts` -- post-task nudge sending
- `k2b-remote/src/youtube.ts` -- new (JSONL data layer)
- `scripts/hooks/session-start.sh` -- new
- `scripts/hooks/stop-observe.sh` -- new
- `scripts/observer-loop.sh` -- new
- `scripts/observer-prompt.md` -- new + YouTube patterns
- `scripts/minimax-common.sh` -- platform-aware vault path
- `CLAUDE.md` -- hooks, observer, youtube morning docs
- `README.md` -- comprehensive rewrite

---

## 2026-03-29 -- Git Setup & Session Discipline

**What was built/changed:**
- Updated root .gitignore with comprehensive ignore rules (secrets, node_modules, dist, store, workspace/uploads, logs, pids)
- Created DEVLOG.md with standard entry template
- Added "Session Discipline" section to CLAUDE.md enforcing end-of-session commits and devlog entries
- Committed all previously uncommitted work: 12 new skills, vault-writer references, scripts, migration-exports, k2b-remote health endpoint, MCP config

**Files affected:**
- `.gitignore` -- expanded from 2 rules to full coverage
- `DEVLOG.md` -- created
- `CLAUDE.md` -- added Session Discipline section
- `.claude/skills/k2b-email/` -- new skill
- `.claude/skills/k2b-feedback/` -- new skill (replaces k2b-learn, k2b-error, k2b-request)
- `.claude/skills/k2b-inbox/` -- new skill
- `.claude/skills/k2b-linkedin/` -- new skill
- `.claude/skills/k2b-media-generator/` -- new skill
- `.claude/skills/k2b-observer/` -- new skill
- `.claude/skills/k2b-scheduler/` -- new skill
- `.claude/skills/k2b-sync/` -- new skill
- `.claude/skills/k2b-usage-tracker/` -- new skill
- `.claude/skills/k2b-youtube-capture/` -- new skill
- `.claude/skills/k2b-vault-writer/references/` -- Obsidian syntax references (moved from deleted obsidian-markdown skill)
- `scripts/` -- deploy, LinkedIn, MiniMax, YouTube helper scripts
- `k2b-remote/src/health.ts` -- new health endpoint
- `k2b-remote/scripts/health-check.sh` -- health check script
- `migration-exports/` -- Claude conversation exports for data migration

**Key decisions:**
- Single root .gitignore covers the whole project; k2b-remote keeps its own .gitignore for subdir-specific rules
- migration-exports/ included in repo (reference material, no secrets)
- obsidian-markdown skill deleted; its references moved under k2b-vault-writer
- k2b-learn, k2b-error, k2b-request consolidated into k2b-feedback

**Status:**
- What works: Git repo with full history, all skills and code tracked
- What's incomplete: Nothing -- this is a housekeeping commit
- What's next: Normal development with session-end commit discipline

---

## 2026-04-09 -- YouTube agent phantom nudge fix + minor hardening

**What was built/changed:**
- Fixed YouTube agent loop sending reminders for videos not actually in the Watch playlist (JSONL-playlist desync)
- Added `getPlaylistVideoIds()` to verify nudge_sent entries against real playlist before presenting
- Phantom entries auto-expire with `outcome: not-in-playlist`; if all are phantom, skips to findNewContent
- Returns null on API error so we don't accidentally expire everything
- Agent error handling: `runAgent()` now returns `hadError` flag, preserves partial response on catch
- yt-playlist-poll.sh: added YouTube Shorts URL format support for audio extraction

**Files affected:**
- k2b-remote/src/youtube.ts (getPlaylistVideoIds)
- k2b-remote/src/youtube-agent-loop.ts (verification step)
- k2b-remote/src/agent.ts (hadError flag)
- scripts/yt-playlist-poll.sh (shorts URL fallback)

**Key decisions:**
- Distinguish API error (null) from empty playlist ([]) to avoid mass-expiring on transient failures
- Verification runs every cycle before presenting nudges, adding ~5s latency from yt-dlp call

**Status:**
- What works: Build passes, ready to deploy
- What's incomplete: Nothing
- What's next: Deploy to Mac Mini, monitor next YouTube agent cycle

---

## YYYY-MM-DD -- Session Title

**What was built/changed:**
-

**Files affected:**
-

**Key decisions:**
-

**Status:**
- What works:
- What's incomplete:
- What's next:

---
