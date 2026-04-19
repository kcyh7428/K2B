# k2b-remote: 4-min Telegram delivery stall (Clash Verge proxy)

**Date logged:** 2026-04-15
**Status:** PARKED -- Keith collecting more evidence by messaging the bot through the workday before deciding on a fix
**Owner:** Keith
**Priority:** medium (one observed incident, recoverable, but blind to recurrence until logging is added)

## Incident

- **2026-04-15 10:58 HKT** Keith sent a Telegram message to `k2b-remote` after the bot had been idle since 08:53:26 (≈2h 9min of zero inbound traffic).
- Bot did NOT process the message until **11:02:08** -- a **4-minute gap** between Keith's send and the bot's `Running agent` log line.
- Subsequent messages processed instantly. No pm2 restart in the window. `k2b-error.log` empty. Zero log lines in the gap.
- Eventually the bot replied normally at 11:03:47 with the correct video-feedback summary.

## Root cause (high-confidence after Codex review + bot.ts inspection)

`k2b-remote` routes ALL Telegram traffic through Clash Verge:

1. Mac Mini shell env: `HTTP_PROXY=http://127.0.0.1:7897` and `HTTPS_PROXY=http://127.0.0.1:7897` (Clash Verge local proxy port).
2. `NO_PROXY=localhost,127.0.0.1` -- does NOT exclude `api.telegram.org`.
3. `k2b-remote/src/config.ts:26` reads `HTTP_PROXY` from env.
4. `k2b-remote/src/bot.ts:208` wires the proxy into grammY via `HttpsProxyAgent` for ALL Telegram API calls.

**Telegram is geo-blocked at the Mac Mini's location** -- Clash Verge is mandatory, not optional. There is NO bypass option. Any fix must work WITH Clash, not around it.

The 4-min gap is most likely Clash Verge (or its upstream node) silently dropping the long-poll TCP connection after a long idle period, and the bot taking that long to detect + reconnect because there's no application-layer timeout or keepalive.

## What was ruled out

- **grammY long-poll "stale socket" hypothesis (original guess):** wrong. grammY 1.30.0's `getUpdates` is a repeating request with finite poll timeout, not one socket sitting idle for 2h. (Codex correction.)
- **`bot.api.getMe()` heartbeat fix (original proposed remedy):** would not work. `getMe()` is just another API request that may run on a different socket. It cannot unstick an in-flight `getUpdates`. (Codex correction.)
- **pm2 restart, App Nap, getUpdates offset desync:** none fit the evidence (single delayed message followed by normal delivery).
- **Code bug in `handleMessage`:** none. `bot.ts:122` has no debounce, queue, or setTimeout. Each Telegram update calls `runAgent` immediately. The bot code is fine; it just isn't defensive against an unreliable transport.

## Fix options (parked -- pick after Keith's evidence-gathering today)

### Option A -- Clash Verge config tweak (transport layer)
- TCP keepalive on the active outbound node
- Increase the proxy's idle timeout so it stops killing long-poll connections
- Switch to a different outbound node with better long-connection behavior
- **Pros:** one place, applies to all apps using Clash
- **Cons:** fragile (depends on node + provider behavior), can silently regress when Keith changes nodes, hard to test

### Option B -- bot.ts hardening (application layer) [RECOMMENDED by Codex]
1. `HttpsProxyAgent` options: `keepAlive: true`, `keepAliveMsecs: 30000`, `timeout: 60000`
2. Configure grammY polling timeout so a wedged `getUpdates` aborts and the loop reconnects within seconds
3. Add `logger.debug` around each `getUpdates` start/end/error so the next stall is diagnosable

- **Pros:** works regardless of which Clash node/config Keith uses, version-controlled, defensive against ANY proxy/network hiccup
- **Cons:** ~10 lines of code in `bot.ts`; doesn't help other apps using Clash
- **Patch size:** small -- HttpsProxyAgent options, one grammY polling option, ~5 lines of debug logging

### Option C -- Both
Belt and suspenders. A first as a quick experiment (no code change), B if A doesn't fully solve it. Or B alone if Keith wants application-layer defense regardless.

## Today's experiment (Keith's plan)

- Keep the bot AS-IS while Keith messages it through the workday from his phone
- Watch for any further multi-minute gaps between Keith's send time and the bot's `Running agent` log line
- If no further stalls today: lower priority, may not need a fix at all (the 10:58 incident might have been a one-off Clash hiccup)
- If it stalls again: revisit, almost certainly need Option B at minimum

## What's NOT a fix (do NOT propose these)

- `NO_PROXY=api.telegram.org` -- breaks the bot, Telegram is geo-blocked at this location
- Unsetting `HTTP_PROXY`/`HTTPS_PROXY` in `ecosystem.config.cjs` -- same reason
- `getMe()` heartbeat -- ineffective, see "ruled out" above
- Switching to webhooks instead of long-polling -- requires public endpoint, much more invasive

## Related: CLAUDE.md / SKILL.md duplication (separate, lower priority)

While investigating, found that the "Video Feedback via Telegram" rule in `~/Projects/K2B/CLAUDE.md` (lines 159-181) duplicates the SHARED CORE of `.claude/skills/k2b-review/SKILL.md` (lines 156-214) -- specifically the flock command, playlist scripts, and atomic write-rename helper. NOT a verbatim duplicate (Codex was right to push back) -- the CLAUDE.md rule has Telegram-specific wrapper logic (fuzzy pick matching, per-pick reply, ambiguity handling, bulk reactions) that the skill doesn't have.

The shared core is the part where MEDIUM-3 (`flock` doesn't exist on macOS) lives in two files. The clean refactor is to extract the shared core into a helper script that BOTH `/review` and the Telegram inline rule call. Defer until after MEDIUM-3 is being fixed -- they pair naturally.

**Wrong refactor**: collapsing the inline rule to "just invoke /review skill" -- would break Telegram-specific behaviors and process the entire queue per reaction.

## Files to touch (when work resumes)

- `k2b-remote/src/bot.ts:208` -- HttpsProxyAgent options
- `k2b-remote/src/bot.ts` (handleMessage / createBot scope) -- add poll-loop logging
- `k2b-remote/src/index.ts:110` -- possibly grammY polling timeout config in `bot.start({...})`
- `k2b-remote/.env` or `ecosystem.config.cjs` -- NO new env vars needed; existing HTTP_PROXY stays
