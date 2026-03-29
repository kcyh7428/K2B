# Proactive YouTube Knowledge Acquisition

## Context

K2B's YouTube capture is currently manual -- Keith runs `/youtube` to poll playlists or `/youtube recommend` to get video suggestions. This means knowledge acquisition only happens when Keith remembers to ask. Videos sit unwatched, recommendations go stale, and the pipeline from "interesting video" to "content idea" or "feature to ship" requires multiple manual steps.

This design makes K2B proactive: it checks for new content every morning, nudges Keith about unwatched videos via Telegram with inline buttons, and connects video outcomes to the content pipeline and observer learning loop.

## Data Layer

### youtube-recommended.jsonl

**Path:** `Notes/Context/youtube-recommended.jsonl`

Single source of truth for all YouTube recommendations. Serves three purposes:
1. **Deduplication** -- never recommend the same video twice
2. **Nudge state** -- track which videos need reminders
3. **Observer signals** -- feed behavioral patterns into the preference learning loop

Each line is one JSON object:

```json
{
  "ts": "2026-03-30T07:00:00+08:00",
  "video_id": "abc123",
  "title": "How AI Will Transform Recruiting",
  "channel": "HR Leaders",
  "playlist": "K2B Recruit",
  "recommended_date": "2026-03-30",
  "status": "pending",
  "nudge_sent": false,
  "nudge_date": null,
  "outcome": null,
  "rating": null,
  "promoted_to": null,
  "vault_note": null
}
```

**Status lifecycle:**

```
pending          Video added to Watch or newly recommended
  |
nudge_sent       Telegram nudge sent, awaiting Keith's tap
  |
  +---> watched           Keith watched it (tapped highlights or confirmed watched)
  |       +---> promoted   Keith promoted to content-idea / feature / insight
  |       +---> (null)     Watched but not promoted
  |
  +---> highlights_sent   K2B sent transcript summary
  |       +---> promoted   Keith promoted after reading highlights
  |       +---> (null)     Read highlights but didn't promote
  |
  +---> skipped           Keith tapped Skip, removed from Watch playlist
  |
  +---> expired           Video sat for 2+ days with no response, auto-removed
  |
processed        Inbound playlist video auto-processed (not a recommendation, no nudge)
```

**Dedup rule:** Before recommending any video, grep `youtube-recommended.jsonl` for the `video_id`. If found in any status, skip it. This prevents re-recommendation regardless of how old the entry is.

## Components

### 1. /youtube morning (new subcommand in k2b-youtube-capture)

**Trigger:** Scheduled task, daily at 7am HKT. Can also be run manually via `/youtube morning`.

**Step 1 -- Handle stale nudges:**
- Read `youtube-recommended.jsonl` for entries with `status: "nudge_sent"`
- If `nudge_date` was yesterday: send one re-nudge via Telegram ("Still in your Watch list: [title]. Highlights or skip?")
- If `nudge_date` was 2+ days ago and no response: mark `status: "expired"`, remove from K2B Watch playlist via `yt-playlist-remove.sh`

**Step 2 -- Check K2B Watch for new additions:**
- Poll K2B Watch playlist via `yt-playlist-poll.sh`
- Filter out any `video_id` already in `youtube-recommended.jsonl`
- For each new video:
  - Append entry to JSONL with `status: "pending"`
  - Send Telegram nudge with inline buttons: `[Get highlights]` `[Skip]`
  - Update entry: `status: "nudge_sent"`, `nudge_sent: true`, `nudge_date: today`

**Step 3 -- Poll inbound playlists for new content:**
- Poll all inbound playlists (K2B, K2B Claude, K2B Recruit, K2B Content, K2B Invest, K2B Learn)
- Filter out any `video_id` already in `youtube-recommended.jsonl` or `youtube-processed.md`
- For each new video:
  - Process via existing youtube-capture skill (transcript, analysis, vault note)
  - Append entry to JSONL with `status: "processed"`, `vault_note: filename`
  - Send Telegram notification: "New video processed: [title]. Note created in Inbox."

### 2. Inline button support (k2b-remote/src/bot.ts)

**New capabilities needed:**

**Sending inline keyboards:**
Add a utility function that wraps Grammy's `InlineKeyboard` builder. Accepts an array of button definitions (label + callback data) and attaches to the message.

**Callback query handler:**
Register `bot.on('callback_query:data')` handler. Parse the callback data to determine action:

```
youtube:highlights:VIDEO_ID    Keith tapped "Get highlights"
youtube:skip:VIDEO_ID          Keith tapped "Skip"
youtube:promote:TYPE:VIDEO_ID  Keith tapped a promotion option (content-idea / feature / insight / nothing)
```

**Handler logic:**

`highlights` callback:
1. Answer the callback query (removes spinner)
2. Send typing indicator
3. Fetch transcript via `yt-dlp` (or youtube-transcript MCP)
4. Run transcript through Claude with playlist-specific `prompt_focus`
5. Send formatted summary to Telegram
6. Update JSONL: `status: "highlights_sent"`, `outcome: "highlights"`
7. Send promotion buttons: `[Content idea]` `[Feature]` `[Insight]` `[Nothing]`

`skip` callback:
1. Answer the callback query
2. Remove from K2B Watch playlist via `yt-playlist-remove.sh`
3. Update JSONL: `status: "skipped"`, `outcome: "skipped"`
4. Confirm: "Skipped and removed from Watch."

`promote` callback:
1. Answer the callback query
2. If type is "nothing": update JSONL `promoted_to: null`, done
3. Create vault note via agent (runs Claude Code with vault-writer):
   - `content-idea` -> `Inbox/content_slug.md` with content-idea template
   - `feature` -> `Inbox/feature_slug.md` with feature template
   - `insight` -> `Inbox/insight_slug.md` with insight template
4. Update JSONL: `promoted_to: type`, `vault_note: filename`
5. Confirm: "Saved to Inbox as [type]: [title]"

### 3. Observer prompt update (scripts/observer-prompt.md)

Add a section to the observer's analysis prompt:

```
### YouTube Behavior Patterns
Read Notes/Context/youtube-recommended.jsonl for:
- Watch rate by playlist: which playlists have the highest watched/total ratio?
- Watch rate by channel: which channels does Keith consistently watch?
- Promotion rate: what percentage of watched videos get promoted?
- Promotion type by playlist: K2B Claude -> features? K2B Recruit -> content ideas?
- Skip rate: which playlists or channels get consistently skipped?
- Highlight request rate: does Keith prefer full watch or quick highlights?
- Time to action: how quickly does Keith respond to nudges?
- Expiry rate: how many videos expire without action? (too many = wrong recommendations)
```

These patterns flow into `observer-candidates.md` and eventually the preference profile.

### 4. Scheduled task configuration

Morning task added via K2B's scheduler system, runs on Mac Mini:

- **Name:** `youtube-morning`
- **Schedule:** Daily at 7:00 HKT (23:00 UTC previous day)
- **Prompt:** "Run /youtube morning"

## Telegram Message Formats

### Nudge (new Watch video)

```
New in your Watch list:

How AI Will Transform Recruiting
HR Leaders | 18 min
Playlist: K2B Recruit

[Get highlights]  [Skip]
```

### Re-nudge (day 2)

```
Still in your Watch list (added yesterday):

How AI Will Transform Recruiting
HR Leaders | 18 min

[Get highlights]  [Skip]
```

### Highlights delivered

```
Highlights: How AI Will Transform Recruiting

[formatted summary with key takeaways,
action items, notable quotes]

---
What do you want to do with this?

[Content idea]  [Feature]  [Insight]  [Nothing]
```

### New inbound video processed

```
New video processed from K2B Claude:

Building Claude Code Skills
Cole Medin | 22 min

Note created: Inbox/2026-03-30_youtube_building-claude-code-skills.md
```

## Flow Diagram

```
Every morning at 7am HKT
    |
    ├── Check stale nudges
    │   youtube-recommended.jsonl (status=nudge_sent)
    │   ├── nudge_date = yesterday → re-nudge via Telegram
    │   └── nudge_date = 2+ days → expire, remove from Watch
    │
    ├── Check K2B Watch playlist
    │   yt-playlist-poll.sh → filter against youtube-recommended.jsonl
    │   └── New videos → append JSONL + Telegram nudge with buttons
    │
    └── Poll inbound playlists
        yt-playlist-poll.sh per playlist → filter against JSONL + processed log
        └── New videos → process (transcript + analysis) → vault note → Inbox

Keith taps button in Telegram
    |
    ├── [Get highlights]
    │   → Fetch transcript → Claude analysis → send summary
    │   → Update JSONL (highlights_sent)
    │   → Show promotion buttons
    │       ├── [Content idea] → create vault note → Inbox
    │       ├── [Feature] → create vault note → Inbox
    │       ├── [Insight] → create vault note → Inbox
    │       └── [Nothing] → done
    │
    └── [Skip]
        → Remove from Watch playlist
        → Update JSONL (skipped)

Observer loop (background, MiniMax M2.5)
    |
    └── Reads youtube-recommended.jsonl
        → Detects patterns (watch rate, promotion rate, channel affinity)
        → Writes to observer-candidates.md
        → Feeds into preference-profile.md
        → Future recommendations improve over time
```

## Files to Create

| File | Type | Purpose |
|------|------|---------|
| `Notes/Context/youtube-recommended.jsonl` | New data file | Recommendation tracking + dedup + observer signals |

## Files to Modify

| File | Change |
|------|--------|
| `.claude/skills/k2b-youtube-capture/SKILL.md` | Add `/youtube morning` subcommand with full morning automation logic |
| `k2b-remote/src/bot.ts` | Add inline keyboard builder, callback_query handler, promotion flow |
| `scripts/observer-prompt.md` | Add YouTube behavior pattern analysis section |
| `CLAUDE.md` | Document `/youtube morning`, scheduled task |

## What This Does NOT Do

- **No YouTube watch history API** -- removed from API in 2016. K2B Watch playlist is the proxy.
- **No automatic recommendation generation** -- morning run processes what Keith saved to Watch and what arrived in inbound playlists. The `/youtube recommend` command remains for on-demand discovery.
- **No auto-processing of Watch videos** -- Keith controls when highlights are generated. K2B nudges, Keith decides.
- **No new skill** -- `/youtube morning` is a subcommand within the existing k2b-youtube-capture skill, keeping all YouTube logic in one place.

## Success Criteria

1. Keith wakes up to a Telegram message showing his unwatched videos with tappable buttons
2. Tapping "Get highlights" delivers a summary within 60 seconds
3. Videos from yesterday that Keith hasn't acted on get one re-nudge, then expire
4. No video is ever recommended twice
5. The observer learns which playlists/channels Keith engages with over 2+ weeks of data
6. Promoted videos flow into the content pipeline or feature backlog as vault notes
