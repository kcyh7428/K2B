# K2B Development Log

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
