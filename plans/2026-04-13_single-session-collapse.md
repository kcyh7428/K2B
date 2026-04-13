# Plan: Collapse k2b-remote to Single Session Per Chat

**Date:** 2026-04-13
**Author:** Claude (for Keith's review)
**Status:** Draft — pending Codex adversarial review

## Problem

k2b-remote currently runs **two Claude Code sessions per Telegram chat**: one `'interactive'`, one `'youtube'`. A keyword-matching router in front of them decides which session gets Keith's message. When YouTube `phase != idle`, the router intercepts anything matching a broad keyword list (`all`, `both`, `first`, `old`, `video`, `watch`, etc.). Result: regular chat messages get swallowed by the YouTube handler and the bot appears broken.

Yesterday's "session isolation" fix (commits `66d39cb`, `3f38712`, `e70a3d7`, `448815d`, `e5a7917`) made the state survive restarts, which made the stuck-in-YouTube-mode problem *stickier*, not better.

## Diagnosis

We partitioned the wrong thing. The leak we feared was **context leak** (YouTube curator persona bleeding into regular chat). The leak we actually have is **routing leak** (router misclassifies messages before either session sees them). Two sessions + a router is strictly more surface area than one session + injected state, and the router is where the bugs live.

## Target Architecture

**One Claude Code session per chat.** Every Telegram text message flows to the same `runAgent()` call with the same resumed session. The agent sees current YouTube state (if non-idle) as context injected into the prompt for that turn, and decides itself whether Keith's message is about YouTube or something else.

```
Telegram message
  -> handleMessage(chatId)
  -> session = getSession(chatId)   // no scope
  -> ytState = getYtState(chatId)    // read, not route
  -> prompt = buildPrompt(message, ytState)  // inject context if non-idle
  -> runAgent(prompt, session)
  -> scanOutbox(chatId)
  -> reply
```

The YouTube background loop still runs on schedule to detect new picks, but when it has something to say, it either:
- Posts video cards directly via `bot.telegram.sendPhoto` (no agent call), or
- Calls `runAgent()` on the *same* interactive session with a system-authored "you just woke up, there are 2 new picks, present them" prompt.

Either way, there is one session per chat, period.

## Concrete Changes

### Database
- `sessions` table: drop `scope` column, revert PK to `chat_id` alone
- `youtube_agent_state` table: **keep as is** — it's state, not a conversation. This is the single source of truth for YouTube phase + pending videos.
- Migration: `DELETE FROM sessions WHERE scope = 'youtube'; ALTER TABLE sessions DROP COLUMN scope;` (or SQLite equivalent: rebuild table)

### [k2b-remote/src/bot.ts](k2b-remote/src/bot.ts)
- Remove the `handleYouTubeAgentResponse()` call at line ~864-883 from the text-message path
- In `handleMessage()`, before calling `runAgent()`:
  - Read `getYtState(chatId)`
  - If `phase !== 'idle'` and `Date.now() < stale_after`, prepend YouTube context block to the prompt: "Current YouTube screening state: phase=X, 2 pending videos: [title1, title2]. If the user's message is about these videos, act on it; otherwise answer normally and leave YouTube state untouched."
- Keep button callback handlers (`Add to Watch`, `Skip`, etc.) — they still mutate YouTube state directly without going through the agent
- `getSession(chatId)` loses its scope parameter everywhere

### [k2b-remote/src/youtube-agent-loop.ts](k2b-remote/src/youtube-agent-loop.ts)
- Delete `handleYouTubeAgentResponse()` and `parseAndExecuteActions()` (or neuter to empty stubs during transition)
- The loop's presentation path (when it finds new videos to screen) uses `runAgent()` against the **interactive session** with a system-authored prompt, OR posts cards directly via `bot.telegram.sendPhoto` with no agent call
- Decision: prefer direct `sendPhoto` with buttons for presentation, because it's simpler and avoids burning agent tokens on a "here are 2 videos" summary Keith doesn't need
- When Keith types a free-text response to a presented card, it goes to the normal `handleMessage()` path — the agent sees the YouTube state context and decides

### [k2b-remote/src/youtube.ts](k2b-remote/src/youtube.ts)
- `getYtState`/`setYtState`/`resetYtState` keep their signatures
- No `scope` anywhere

### Optional Phase 2: YouTube state as SDK tools
- Instead of the agent parsing free text and calling `parseAndExecuteActions`, expose tools: `youtube_add_to_watch(video_id)`, `youtube_skip(video_id)`, `youtube_screen(video_id)`, `youtube_mark_watched(video_id)`, `youtube_reset()`
- The agent calls these when Keith says "add both" or "skip the first one"
- This replaces the entire `parseAndExecuteActions` pattern-matching layer with clean tool calls
- **Defer to Phase 2 unless Phase 1 proves it's needed** — start by just injecting state and letting the agent describe what to do in text, mutate state in bot.ts based on agent's structured reply

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Context leak: YouTube "curator persona" bleeds into interactive chat | Don't set a persistent YouTube system prompt. State is injected per-turn as plain context, not persona |
| Race: background loop fires while Keith is typing | Serialize `runAgent` calls per chat with a mutex (current design has this risk too; not new) |
| Lost parsing: `parseAndExecuteActions` had rich logic for "add both", "skip the first" | Phase 1 relies on the agent to handle natural language (it's better at this than regex). Phase 2 adds tools if needed |
| Single session context grows unbounded | Claude Code sessions already handle compaction; same limit either way |
| Migration breaks existing pending YouTube state | Accept: wipe `youtube_agent_state` on deploy, Keith re-triggers screening. Low cost |

## What Stays

- `youtube_agent_state` table (state, not conversation)
- Button callbacks and their direct state mutations
- Media outbox mechanism
- 12h staleness on YouTube state
- Background YouTube loop schedule

## What Goes

- `scope` column on `sessions` table
- `'youtube'` session rows
- `handleYouTubeAgentResponse()` text-message gate
- `parseAndExecuteActions()` free-text parser (Phase 2 decision)
- The keyword list at youtube-agent-loop.ts:454-456
- The whole mental model of "which agent gets this message"

## Phase Breakdown

**Phase 1A — schema + bot.ts (1-2h)**
1. Migration to drop `scope` column
2. Update `getSession`/`setSession` to take only `chatId`
3. Update `handleMessage()` to inject YouTube state as context before `runAgent`
4. Remove `handleYouTubeAgentResponse()` from text path (leave button handlers alone)
5. Deploy, test with Keith

**Phase 1B — YouTube loop presentation (1h)**
1. Rewrite loop's "present picks" path to send Telegram cards directly
2. Remove old `runAgentWithSession` internal call
3. Deploy, test

**Phase 2 (optional, later) — SDK tools**
1. Add youtube_* tools to the agent SDK config
2. Delete `parseAndExecuteActions`
3. Agent uses tools instead of text parsing

## Success Criteria

- Keith types "show me the investment infographic" while YouTube phase = `presenting-picks` → regular chat handles it, no interception
- Keith types "add both" while YouTube phase = `presenting-picks` → agent sees state, adds both videos
- `sessions` table has one row per chat
- Bot restart clears or preserves state predictably (explicit choice, not accidental)
- No keyword router exists anywhere in the codebase

## Open Questions for Codex

1. Is injecting YouTube state as per-turn context reliable enough, or is the tools approach (Phase 2) worth doing upfront?
2. Is there a failure mode where the agent, seeing "2 pending videos" in context, tries to mutate state when the user's message is unrelated?
3. Should the background YouTube loop present cards via direct `sendPhoto` or via an agent call? Tradeoffs?
4. Migration: is there a safer path than dropping the scope column, given SQLite's limited ALTER support?
5. Any blind spot — is there a reason the 2-session design was actually correct that we're missing?
