# Plan v3: One Conversation Session, State-as-Tool, Stateless Classifiers

**Date:** 2026-04-13
**Supersedes:** v1 (single-session-collapse), v2 (session-design-v2)
**Status:** Revised 2026-04-13 after Codex round 3. Ready to execute.

## Revision notes (round 3)

Codex round 3 caught three must-fix issues that this revision addresses:

1. **`unstable_v2_prompt` is NOT one-shot** — verified, it creates a persisted session via `SessionImpl`. **Fix:** use `query()` with `persistSession: false` (verified to exist at [runtimeTypes.d.ts:360](k2b-remote/node_modules/@anthropic-ai/claude-agent-sdk/entrypoints/sdk/runtimeTypes.d.ts#L360)) for classifier calls. Drop `unstable_v2_prompt` from the plan entirely.

2. **Pre-check-then-mutate is still TOCTOU-vulnerable** — a button callback can clear state between the agent tool's state check and `playlistAdd`, causing double-add. **Fix:** process-local async mutex around all YouTube mutations (both button callbacks AND agent tool handlers). Pre-check stays as a short-circuit but the mutex is the correctness guarantee.

3. **Dual-pm2 rollback is unsafe** — two processes sharing one SQLite DB would double-run loops and stomp state. **Fix:** cold rollback only (stop new, checkout previous SHA, rebuild, restart). No hot dual-running.

Plus refinements:
- Mutation tools refuse if `phase === 'idle'` OR `videoId ∉ pendingVideoIds`; batch actions require ALL target ids pending
- Deploy requires `tsc` build success before `pm2 restart`, not just git commit atomicity
- Claims about "stateless" are scoped to the background loop classifiers, NOT to the interactive session's direct-URL/highlights/promote flows which still pollute transcript (acceptable, out of scope)

## Architecture at a glance

```
Telegram text
  └──> bot.ts handleMessage (SERIAL per chat via grammy)
        └──> runAgent(rawUserText, interactiveSessionId)   ← ONE persistent session
              ├── SDK MCP server registered with youtube_* tools
              ├── Agent reads state on demand:  youtube_get_pending()
              ├── Agent mutates via tool:       youtube_add_to_watch(id)
              │                                 youtube_skip(id)
              │                                 youtube_keep(id)
              │                                 youtube_swap_all()
              └── Each mutation tool re-reads state FIRST and no-ops
                  if the video is already gone (button-race safe)

Telegram button click
  └──> direct state mutation (unchanged, bot.ts:370-398)
        └──> clearFromAgentState + playlist API

Background YouTube loop
  └──> classification work → runStatelessQuery() → query({ persistSession: false, resume: undefined })
  └──> presentation → direct bot.telegram.sendPhoto (unchanged, already does this)
```

**No routing keyword list. No scope column. No forced-choice prompt in the text path. No orphan persistent sessions.**

## Why this specific shape

Codex adversarial review (v2 round) established three technical constraints I had gotten wrong:

1. **`query({ resume: undefined })` is NOT one-shot by default.** It starts a new persistent session unless you explicitly set `persistSession: false`. That Options field exists at [runtimeTypes.d.ts:360](k2b-remote/node_modules/@anthropic-ai/claude-agent-sdk/entrypoints/sdk/runtimeTypes.d.ts#L360) documented as "When false, disables session persistence to disk. Sessions will not be saved to ~/.claude/projects/ and cannot be resumed later." Solution: `runStatelessQuery` wraps `query()` with `{ persistSession: false, resume: undefined, mcpServers: {} }` for all classifier calls. (We do NOT use `unstable_v2_prompt` — Codex verified it has no `persistSession` escape hatch.)

2. **Prepending state as prompt text is NOT ephemeral.** Anything we send with `resume=sessionId` becomes persisted history. Over days the interactive session would accumulate stale YouTube state. Solution: state lives behind a **read tool**, not in the prompt. The agent calls `youtube_get_pending()` only when it judges the message might be about videos. Tool results are bounded and compactable.

3. **Button vs agent-tool race is real.** Button click mutates state immediately; an in-flight agent turn could call `youtube_add_to_watch(same_id)` against a playlist that was already touched. `addVideoToWatch` dedupes JSONL rows but not YouTube playlist API calls. Solution: every mutation tool re-reads `pendingVideoIds` before acting; no-op if the video is already gone.

## File-level changes

### A. agent.ts — wrap SDK with tool registration
Current: [agent.ts:5-70](k2b-remote/src/agent.ts#L5-L70) passes `{ resume?, cwd, env }` to `query()`.

Add:
- Import `createSdkMcpServer` + `tool` from `@anthropic-ai/claude-agent-sdk`
- Create a **module-singleton** in-process MCP server `k2bYoutubeTools` with the five mutation tools + `youtube_get_pending` read tool (singleton, not per-call)
- Pass `mcpServers: { 'k2b-youtube': k2bYoutubeTools }` to `query()` options on every `runAgent` call (only for the interactive conversational path; classifiers don't need tools)
- Add `runStatelessQuery(prompt: string)` that calls `query()` with `{ persistSession: false, resume: undefined, mcpServers: {} }`. Verified: `persistSession: false` is a real Options field at [runtimeTypes.d.ts:360](k2b-remote/node_modules/@anthropic-ai/claude-agent-sdk/entrypoints/sdk/runtimeTypes.d.ts#L360). Classifier calls will NOT write to `~/.claude/projects/`.

The tool handlers call the new guarded mutation functions in `youtube.ts` — all state and playlist side effects happen under a process-local mutex (see section B).

### B. youtube.ts — mutex-protected mutation path
Current: `addVideoToWatch`, `skipVideoFromWatch`, `clearFromAgentState` exist at [youtube.ts:276-341](k2b-remote/src/youtube.ts#L276-L341). They are NOT atomic — each does multiple operations (playlist API call, JSONL write, state clear) with no shared lock.

Add:
1. **Process-local async mutex** (use `async-mutex` npm package or small hand-rolled Promise queue). One mutex per k2b-remote process; all YouTube mutations acquire it.
2. **`guardedAddVideoToWatch(videoId, source)`** wrapper:
   ```typescript
   return mutex.runExclusive(async () => {
     const state = getYtState()
     if (state.phase === 'idle') return { status: 'idle' }
     if (!state.pendingVideoIds.includes(videoId)) return { status: 'already_handled' }
     // Inside lock: playlist API + JSONL + state clear all happen atomically
     // from the perspective of any OTHER mutation attempt
     await addVideoToWatch(meta, source)
     return { status: 'done', videoId, title: meta.title }
   })
   ```
3. Same pattern for `guardedSkipVideo`, `guardedKeepVideo`, `guardedSwapAll`.
4. **Button callback handlers in `bot.ts` must also acquire the same mutex** before calling the raw mutations. This is the critical TOCTOU fix — both paths (agent tool + button click) serialize through one lock, so whichever grabs the lock first wins and the other gets `already_handled`.

The mutex is process-local, not DB-level. That's sufficient because there's only ever one k2b-remote process on the Mac Mini (pm2 ensures singleton). If we ever run two bot processes, this breaks — and that's why rollback is cold, not hot (see Rollback section).

Raw mutations (`addVideoToWatch`, `skipVideoFromWatch`, etc.) stay in place as the underlying operations. Nothing outside the mutex should call them directly after this change.

### C. bot.ts — delete the router, inject nothing
Current structure to delete:
- [bot.ts:864-883](k2b-remote/src/bot.ts#L864-L883) — `handleYouTubeAgentResponse` call
- [bot.ts:216-232](k2b-remote/src/bot.ts#L216-L232) — auto-nudge on "youtube" keyword in text (Codex flagged this as a second side path)
- Any other implicit YouTube routing in `handleMessage`

New: `handleMessage` becomes the simple path:
```typescript
async function handleMessage(chatId, text) {
  const sessionId = getSession(chatId)              // no scope
  const { text: reply, newSessionId } = await runAgent(text, sessionId)
  if (newSessionId) setSession(chatId, newSessionId) // no scope
  await sendOutboxIfAny(chatId)
  await sendMessage(chatId, reply)
}
```

No YouTube state injected. No keyword check. No routing. The agent has tools and decides.

**Mutex-wrapped callsites in bot.ts** — every direct mutation call MUST run inside `ytMutex.runExclusive()`. Codex grep found these:
- Button callback "Add to Watch" around [bot.ts:354](k2b-remote/src/bot.ts#L354) — wrap the `addVideoToWatch` call
- Button callback "Skip" around [bot.ts:400](k2b-remote/src/bot.ts#L400) — wrap the `skipVideoFromWatch` call
- **`handleCommentOrSkipReason` at [bot.ts:591](k2b-remote/src/bot.ts#L591)** — the text reply path for skip-reason capture. Wraps `skipVideoFromWatch` and `clearFromAgentState`. Codex flagged this as the one missing-mutex gap in v3 round 4. Wrap the entire if/else mutation block (lines ~583-602) inside the mutex.

All three callsites use the **same module-singleton mutex** exported from `youtube.ts` (or a new `mutex.ts` helper). The rule: if a function touches `addVideoToWatch`, `skipVideoFromWatch`, `clearFromAgentState`, or the YouTube playlist API in any code path, it runs inside the mutex. Grep confirmation before Phase 1 deploy:
```bash
rg "addVideoToWatch|skipVideoFromWatch|clearFromAgentState" k2b-remote/src --type ts -n
```
Every match should either be (a) the raw definition in `youtube.ts`, (b) inside a `mutex.runExclusive` block, or (c) deleted by this Phase 1 (e.g., `parseAndExecuteActions` callsites).

### D. youtube-agent-loop.ts — stateless classifiers, delete parser
- Delete `runAgentWithSession(chatId, prompt)` helper and its use at [youtube-agent-loop.ts:39-47](k2b-remote/src/youtube-agent-loop.ts#L39-L47)
- All loop tasks (check-in copy, screening JSON, candidate verdicts) call `runStatelessQuery(prompt)` instead — guaranteed no persisted session because `persistSession: false` is set
- Delete `handleYouTubeAgentResponse` entire function (436-543)
- Delete `parseAndExecuteActions` entire function (561-693) — no text-routing caller left
- Delete `youtubeKeywords` array at :454-456
- Keep direct card presentation at :214-250, :368-423

**Codex verified** that the three loop prompts (check-in copy [youtube-agent-loop.ts:190-208](k2b-remote/src/youtube-agent-loop.ts#L190-L208), screening JSON [:339-341](k2b-remote/src/youtube-agent-loop.ts#L339-L341), parse prompt [:595-623](k2b-remote/src/youtube-agent-loop.ts#L595-L623)) all explicitly say "Do NOT use tools / read files / run commands" and receive precomputed text inputs. None of them need vault/web/MCP access. Safe to convert to stateless.

### E. db.ts — sessions table rebuild
Current schema at [db.ts:24-31](k2b-remote/src/db.ts#L24-L31):
```sql
CREATE TABLE sessions (
  chat_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  session_id TEXT,
  updated_at INTEGER,
  PRIMARY KEY (chat_id, scope)
)
```

Migration (inside the existing pattern at [db.ts:22-54](k2b-remote/src/db.ts#L22-L54)):
```sql
BEGIN TRANSACTION;
CREATE TABLE sessions_v3 (
  chat_id TEXT PRIMARY KEY,
  session_id TEXT,
  updated_at INTEGER
);
INSERT INTO sessions_v3 (chat_id, session_id, updated_at)
  SELECT chat_id, session_id, updated_at FROM sessions WHERE scope = 'interactive';
DROP TABLE sessions;
ALTER TABLE sessions_v3 RENAME TO sessions;
COMMIT;
```

Writers block during rebuild — fine for single-user low-traffic. Youtube scope rows dropped (stale state, not needed).

`getSession(chatId)` / `setSession(chatId, sessionId)` lose the scope parameter.

### F. Other YouTube flows already on interactive session
Codex noted `handleDirectYouTubeUrl`, screen-process, highlights, promote currently run on the **interactive** session and pollute its transcript. Codex was concerned deleting the YouTube session makes this worse.

Decision: leave these on the interactive session for this change. They're intentionally conversational ("Keith just sent a URL, Claude screens it and reports back"). If transcript pollution becomes an issue later, migrate them to stateless `unstable_v2_prompt` calls — but only when we have evidence it's a real problem. Not in scope for v3.

## What stays (unchanged)

- `youtube_agent_state` table — state, not conversation
- Button callback handlers at [bot.ts:370-398](k2b-remote/src/bot.ts#L370-L398)
- Media outbox mechanism
- Background loop schedule, presentation cards, `sendPhoto` path
- pm2 setup, deploy flow
- All `gws`, MCP, and MiniMax integrations

## What goes (deleted)

- `sessions.scope` column
- `scope='youtube'` rows
- `handleYouTubeAgentResponse` entire function
- `parseAndExecuteActions` entire function
- `youtubeKeywords` array
- `runAgentWithSession` helper
- Text-message "youtube" keyword side path in `handleMessage`
- The two-session mental model

## Race-condition story (revised)

**Tool/button race** — ALL YouTube mutations (agent tool calls AND button callbacks) acquire the same process-local async mutex before touching state or calling playlist API. Inside the mutex, each mutation re-checks `pendingVideoIds` and no-ops with `already_handled` if the video is gone. Whichever caller grabs the lock first wins; the other sees the cleared state and no-ops. No double-add possible.

**Background loop / interactive session** — no shared session. Loop only uses `runStatelessQuery` (which calls `query()` with `persistSession: false`, so no `~/.claude/projects/` writes, no resume pollution) and direct `sendPhoto`. Zero session contention possible.

**Multiple text messages from Keith in flight** — grammy serializes per chat; `handleMessage` is already single-threaded per chat_id. No change needed.

**Background loop presentation during active conversation** — Phase 2: before sending pre-notification, read `sessions.updated_at` for the chat. If activity within last 60s, defer presentation by one cycle. Pure UX courtesy, not a correctness requirement, not blocking Phase 1.

**Tool-level safeguards on mutation tools** — each tool refuses unless:
- `getYtState().phase !== 'idle'`, AND
- `videoId ∈ getYtState().pendingVideoIds`

For batch tools (`youtube_swap_all`), ALL target IDs must currently be pending or the whole call fails with `stale_state`. No partial-apply. This is state-based refusal, not text heuristics — the agent cannot damage state just by calling the wrong tool, because the tool will refuse.

## Phase plan

**Phase 1 — foundation (single deploy, atomic)**
Must ship together because router removal requires tools to exist. Build order within the commit is bottom-up so `tsc` succeeds at every save:

1. A: `youtube.ts` — add mutex + guarded mutation wrappers (underlying raw functions untouched)
2. B: `agent.ts` — register singleton MCP server with YouTube tools, add `runStatelessQuery` using `query()` + `persistSession: false`
3. C: `db.ts` — sessions table rebuild migration
4. D: `bot.ts` — delete router + "youtube" keyword side path, simplify `handleMessage`, wrap button callbacks in the same mutex
5. E: `youtube-agent-loop.ts` — convert to stateless classifiers, delete `runAgentWithSession` + `handleYouTubeAgentResponse` + `parseAndExecuteActions` + `youtubeKeywords`

**Before pm2 restart:** `npm run build` MUST pass. Codex flagged that git-atomic is not deploy-atomic — if tsc fails, pm2 restart on a half-built tree crashes the bot. Deploy check:
```bash
cd /Users/fastshower/Projects/K2B/k2b-remote && npm run build && pm2 restart k2b-remote
```
If `npm run build` fails, abort deploy and fix locally.

**Install `async-mutex`:** `npm install async-mutex` is a new dependency. Commit package.json + package-lock.json in the same atomic commit.

Expected delta: ~500-700 lines removed, ~250-350 added (mutex wrappers add some). Net reduction.

Deploy window: single commit, db snapshot taken BEFORE pm2 restart, migration runs on first start, stale YouTube state wiped. ~5 min total.

**Phase 2 — cooperative loop presentation (separate deploy)**
1. Background loop reads `sessions.updated_at` before user-facing notification
2. 60-second quiet-window check, defer one cycle if active

**Phase 3 — optional, later** (not in scope now)
- Migrate `handleDirectYouTubeUrl` etc. to stateless if transcript pollution proves real
- Add per-tool telemetry for button/agent race visibility

## Testing checklist

Before deploy:
- [ ] `handleMessage("show me both")` with pending YouTube state → agent does NOT call youtube tools, responds conversationally
- [ ] `handleMessage("add both")` with 2 pending videos → agent calls `youtube_add_to_watch` twice, state clears
- [ ] `handleMessage("add both")` with 0 pending videos → agent asks for clarification
- [ ] Button click "Add to Watch" while agent mid-turn → no double-add at playlist API (guarded tool no-ops)
- [ ] Background loop scoring 5 candidates → no persistent session created (check `.claude/projects` session count before/after)
- [ ] Interactive session history does not contain YouTube state text after 10 turns (grep session JSON)
- [ ] Existing `handleDirectYouTubeUrl` flow still works (unchanged path)
- [ ] Existing button callbacks still mutate state correctly
- [ ] Migration: existing interactive session rows preserved, youtube rows dropped, schema matches

After deploy (live on Mac Mini):
- [ ] Send "show me the investment infographic" with YouTube non-idle → routes to interactive, responds correctly
- [ ] Send "add both" with real pending picks → videos added
- [ ] Send ambiguous "both" in unrelated context → agent asks what "both" refers to

## Rollback plan (revised — cold only)

**Dual-pm2 hot rollback is UNSAFE.** Two k2b-remote processes sharing the same SQLite DB, JSONL files, and playlist API would double-run background loops and stomp session rows. Codex explicitly flagged this.

**Cold rollback procedure:**
1. Capture the pre-deploy commit SHA in DEVLOG before shipping
2. If rollback needed: `pm2 stop k2b-remote`
3. `git checkout <pre-deploy-sha>` in `/Users/fastshower/Projects/K2B/k2b-remote/`
4. `npm run build` (tsc must succeed)
5. `pm2 restart k2b-remote`
6. Any in-flight agent turns are lost — acceptable cost for a bad deploy

**The sessions table migration is NOT automatically reversible.** Rollback uses the pre-deploy DB snapshot, not schema reconstruction.

**Pre-deploy safety procedure (mandatory):**
1. Before first pm2 restart with v3 code, snapshot the live DB:
   ```bash
   cp /Users/fastshower/Projects/K2B/k2b-remote/tasks.db /Users/fastshower/Projects/K2B/k2b-remote/.pre-v3-tasks.db
   ```
2. Record the snapshot timestamp and pre-deploy git SHA in `DEVLOG.md`.

**Rollback procedure (cold):**
1. `pm2 stop k2b-remote`
2. `cp /Users/fastshower/Projects/K2B/k2b-remote/.pre-v3-tasks.db /Users/fastshower/Projects/K2B/k2b-remote/tasks.db` (restore snapshot — this reverts the schema to the v1 `sessions(chat_id, scope)` shape)
3. `git checkout <pre-deploy-sha>` in the k2b-remote directory
4. `npm run build` (tsc must succeed)
5. `pm2 start k2b-remote`
6. Any in-flight agent turns and any state changes between deploy and rollback are lost — acceptable cost for a bad deploy

We do NOT reconstruct the schema with SQL. We restore the snapshot file wholesale. Simpler, atomic, and avoids the risk of an incomplete `INSERT ... SELECT` statement.

Rollback window: accept ~10-15 minutes of bot downtime. Not zero-downtime. Acceptable for single-user bot.

## Effort estimate

- Phase 1 code: 6-8h
- Phase 1 testing: 2-3h
- Phase 1 deploy + monitor: 1-2h
- Phase 2: 1-2h later

Total before ship-ready: ~10-12h of focused work. Can be split across 2 sessions if needed.

## Go/no-go confirmation

Keith approved direction 2026-04-13. Codex approved approach (verdict: proceed on v3 direction, v2 had technical gaps now fixed). Next step: start Phase 1 execution.
