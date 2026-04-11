---
name: k2b-youtube-capture
description: Batch-process YouTube videos saved to K2B category playlists (K2B, K2B Claude, K2B Invest, K2B Recruit, K2B Content, K2B Learn, K2B Screen) into raw/ vault notes with playlist-specific analysis. Use when Keith says /youtube, /youtube <playlist-name>, or wants to turn saved videos into notes. Direct YouTube URLs and Watch-list recommendations are handled by the Telegram bot's agent loop, NOT this skill.
---

# K2B YouTube Capture (Batch Playlist Processor)

Turn videos Keith saved to a K2B category playlist into `raw/youtube/` vault notes, analyzed with the playlist's specific `prompt_focus`.

## Scope

This skill is now a pure **batch processor**. Curation, discovery, Watch-list management, and direct URL screening all moved to the YouTube Agent (k2b-remote's `youtube-agent-loop.ts` + `handleDirectYouTubeUrl`). See `wiki/concepts/2026-04-08_feature_youtube-agent.md` for the full workflow split.

## Commands

- `/youtube` -- Poll ALL inbound category playlists for new videos and process them
- `/youtube <playlist-name>` -- Poll ONE specific inbound playlist by name. Name is matched case-insensitively (e.g. `/youtube invest` matches "K2B Invest", `/youtube screen` matches "K2B Screen")

## Paths

- Vault: `~/Projects/K2B-Vault`
- Scripts: `~/Projects/K2B/scripts/`
- Playlist config: `~/Projects/K2B-Vault/wiki/context/youtube-playlists.md`
- Processed log: `~/Projects/K2B-Vault/wiki/context/youtube-processed.md`
- Output notes: `~/Projects/K2B-Vault/raw/youtube/`

## Entry point: decide what to poll

Before running the per-playlist pipeline, determine the scope based on how the command was invoked.

### `/youtube` (no args)

1. Read `~/Projects/K2B-Vault/wiki/context/youtube-playlists.md`. Extract the YAML from the fenced code block.
2. Filter the list: skip any playlist where `url` is `PLACEHOLDER` or where `type: outbound` (those are for the agent loop, not capture).
3. Iterate through ALL remaining playlists and run the processing pipeline below for each.

### `/youtube <playlist-name>` (one-playlist mode)

1. Read `~/Projects/K2B-Vault/wiki/context/youtube-playlists.md` as above.
2. Find the playlist whose `name` matches the argument case-insensitively. The argument can match the full name ("K2B Invest") or just the suffix ("invest", "Invest", "INVEST").
3. If no match: tell Keith which names are available (list them from the config) and stop. Do not run the pipeline.
4. If the match is an outbound playlist (K2B Watch): explain that outbound playlists are managed by the agent loop, not this skill, and stop.
5. If the match is inbound: run the processing pipeline below for JUST that playlist.

## Workflow: Per-Playlist Processing Pipeline

Once the outer loop has chosen which playlist(s) to process, each playlist runs through this identical pipeline.

### 1. Poll the playlist

```bash
~/Projects/K2B/scripts/yt-playlist-poll.sh "<playlist-url>" "~/Projects/K2B-Vault/wiki/context/youtube-processed.md" --max 3
```

Outputs tab-separated lines: `video_id\ttitle\tupload_date\turl`. If no output, the playlist has no new videos -- skip it and move on.

### 2. Process each new video

For each video returned by the poll:

#### 2a. Get Metadata

Use `mcp__YouTube_Transcript_MCP_Server__get_video_info` to get title, channel, duration, description.

#### 2b. Get Transcript (Cascade)

**Tier 1 -- YouTube Transcript MCP (free, fast):**
Try `mcp__YouTube_Transcript_MCP_Server__get_transcript` with `lang: "en"`.
If that fails, try with `lang: "zh"`.
If either succeeds and returns meaningful text (>100 chars), use it. Set `transcript_method: youtube-api`.

**Tier 2 -- Groq Whisper (free, reliable):**
If Tier 1 fails:

1. **Extract audio**: `scripts/yt-playlist-poll.sh --extract-audio "<video-url>" /tmp/k2b-yt-audio/`

2. **Check duration and pre-split if needed:**
   ```bash
   DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "<audio-path>")
   # If >240 seconds (4 min), split into chunks:
   ffmpeg -i "<audio-path>" -f segment -segment_time 240 -c copy /tmp/k2b-yt-chunk_%02d.mp3 -y
   ```
   If under 4 minutes, convert to mp3 if needed and use directly.

3. **Transcribe via Groq:**
   ```bash
   GROQ_KEY=$(grep GROQ_API_KEY ~/Projects/K2B/k2b-remote/.env | cut -d= -f2)

   curl -s --retry 2 --retry-delay 3 \
     https://api.groq.com/openai/v1/audio/transcriptions \
     -H "Authorization: Bearer $GROQ_KEY" \
     -F "file=@<chunk-or-file>" \
     -F "model=whisper-large-v3" \
     -F "response_format=text" \
     -F "language=zh"
   ```
   - Use `language=zh` for Chinese videos, omit for English
   - Concatenate chunk results in order
   - Set `transcript_method: groq-whisper`

4. Clean up: `rm <audio-path> /tmp/k2b-yt-chunk_*.mp3`

**Tier 2b -- OpenAI Whisper (paid fallback, only if Groq fails):**
```bash
WHISPER_KEY=$(grep OPENAI_API_KEY ~/.zshrc 2>/dev/null | head -1 | sed "s/export OPENAI_API_KEY=//;s/'//g")

curl -s --retry 1 \
  https://api.openai.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $WHISPER_KEY" \
  -F "file=@<chunk-or-file>" \
  -F "model=whisper-1" \
  -F "language=zh" \
  -F "response_format=text"
```
Same pre-split logic applies. Set `transcript_method: openai-whisper`.

**Tier 3 -- No transcript:**
If all tiers fail, create a minimal note with `transcript_method: failed` and the video metadata only. Flag for manual review.

#### 2c. Dedup Check

Before analyzing, check if `video_id` already exists in the processed log:
```bash
grep -cF "{video_id}" ~/Projects/K2B-Vault/wiki/context/youtube-processed.md
```

- If **already exists**: log a warning ("Video {video_id} already processed from another playlist -- skipping"). Skip steps 2d-2e (no analysis, no vault note). Still append to processed log in 2f for audit trail, then continue to next video.
- If **not found**: proceed with analysis below.

#### 2d. Analyze with Playlist Focus

Use the playlist's `prompt_focus` to guide analysis. Build this prompt:

```
You are processing a YouTube video for Keith's knowledge vault.

Video: {title} by {channel}
Language: {detected or assumed language}
Transcript source: {transcript_method}
Playlist: {playlist_name}

Transcript:
{transcript_text}

Analysis focus (from playlist config):
{prompt_focus}

Produce the following in English (even if the video is in another language):

1. SUMMARY: 3-5 sentences capturing the core message.

2. PLAYLIST-SPECIFIC ANALYSIS: Follow the analysis focus above. Be specific and actionable.

3. KEY TAKEAWAYS: 3-5 specific, actionable insights. Not vague observations.

4. ACTION ITEMS: Things Keith should follow up on -- tools to try, people to research, ideas to explore, techniques to test.

5. NOTABLE MOMENTS: 2-3 specific quotes or demonstrations worth bookmarking.
```

#### 2e. Create Vault Note

Save to `raw/youtube/YYYY-MM-DD_youtube_<video-slug>.md`.

Frontmatter:
```yaml
---
tags: [youtube, video-capture, {playlist-specific-tags}]
date: YYYY-MM-DD
type: reference
origin: k2b-extract
source: "[{title}]({url})"
channel: {channel_name}
playlist: {playlist_name}
transcript_method: {youtube-api|groq-whisper|openai-whisper|failed}
up: "{playlist up link}"
---
```

Body sections:
```markdown
# Video: {Title}

**Channel**: {channel} | **Duration**: {duration} | **Published**: {upload_date}
**Playlist**: {playlist_name} | **Transcript**: {transcript_method}

## Summary
{summary}

## Analysis
{playlist-specific analysis}

## Key Takeaways
1. {takeaway}
2. ...

## Action Items
- [ ] {action item}

## Notable Moments
{quotes and timestamps}

## Linked Notes
{wikilinks to related vault notes -- search vault for relevant connections}
```

For **K2B Content** playlist: also create `wiki/content-pipeline/idea_<slug>.md` for each content seed found.

For **K2B Learn** playlist: append to or create `wiki/context/learning-paths.md` linking this video in sequence.

For **K2B Screen** playlist: after processing, the video should also be removed from the K2B Screen playlist via `scripts/yt-playlist-remove.sh` since Screen is a triage queue. All other category playlists are persistent archives and should NOT be modified.

#### 2f. Trigger k2b-compile

After saving the note to raw/youtube/, trigger k2b-compile to digest the raw source into wiki pages:
1. k2b-compile reads the raw YouTube note + wiki/index.md
2. Shows Keith a summary of wiki pages to update (people, projects, reference entries)
3. On approval: updates wiki pages, indexes, wiki/log.md
4. Marks raw source as compiled

#### 2g. Update Processed Log

Append to `~/Projects/K2B-Vault/wiki/context/youtube-processed.md`:
```
{video_id} | {YYYY-MM-DD} | {playlist_name} | {title}
```

(Always append, even for duplicates caught in 2c -- the audit trail shows cross-playlist appearances.)

#### 2h. Log Usage

Append to skill-usage-log.tsv following the k2b-usage-tracker pattern.

### 3. Summary

After processing all videos in all polled playlists, show Keith a summary:
- How many playlists checked
- How many new videos processed
- Brief one-liner for each video processed (title, playlist, transcript method)

## What This Skill Does NOT Do

These used to live in this skill and were retired when the YouTube Agent shipped:

- **`/youtube recommend`** -- the agent loop in `k2b-remote` now discovers and screens new content every 6 hours via its own search + taste-model pipeline
- **`/youtube morning`** -- the agent loop checks in with Keith conversationally during its cycle instead of firing a scheduled nudge
- **`/youtube <url>`** -- the Telegram bot detects YouTube URLs and runs `handleDirectYouTubeUrl` for instant screening + vault add
- **`/youtube cleanup`** -- the agent loop's verify-playlist step automatically expires Watch list rows that don't match the actual K2B Watch playlist
- **`/youtube status`** -- reporting removed; check `wiki/context/youtube-processed.md` or the taste model JSON directly if needed

If Keith asks for any of these commands, tell him the capability is in the YouTube Agent now and point him at either the Telegram bot or `wiki/concepts/2026-04-08_feature_youtube-agent.md`.

## Error Handling

- If yt-dlp is not installed: `brew install yt-dlp`
- If video is >2 hours: warn Keith and ask for confirmation before processing
- If audio file is >25MB (Whisper limit): warn Keith. For long videos, prefer YouTube Transcript MCP. If no captions, suggest splitting audio with ffmpeg.
- If Whisper API fails (key invalid, quota exceeded, model not found): try model cascade (whisper-1 → gpt-4o-transcribe → gpt-4o-mini-transcribe), then try key from ~/.zshrc. If all fail, create note with `transcript_method: failed` and flag.
- If playlist URL returns no results: skip silently and move to the next playlist.
- If `/youtube <name>` matches no playlist: list available names from `youtube-playlists.md` and stop.