---
name: k2b-youtube-capture
description: Batch-process YouTube videos saved to K2B category playlists (K2B, K2B Claude, K2B Invest, K2B Recruit, K2B Content, K2B Learn, K2B Screen) into raw/ vault notes with playlist-specific analysis. Use when Keith says /youtube, /youtube <playlist-name>, or wants to turn saved videos into notes. Video discovery from fresh queries runs through `/research videos "<query>"` (NotebookLM), not this skill.
---

# K2B YouTube Capture (Batch Playlist Processor)

Turn videos Keith saved to a K2B category playlist into `raw/youtube/` vault notes, analyzed with the playlist's specific `prompt_focus`.

## Scope

This skill is a pure **batch processor** for videos Keith has manually saved to category playlists in YouTube. Discovery of fresh videos from search queries runs through `/research videos "<query>"` (see [[feature_research-videos-notebooklm]]). Per-video feedback flows into `wiki/context/video-preferences.md` via `/review` and the Telegram feedback path.

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
2. Filter the list: skip any playlist where `url` is `PLACEHOLDER` or where `type: outbound` (K2B Watch is a destination for `/research videos` picks, not a capture source).
3. Iterate through ALL remaining playlists and run the processing pipeline below for each.

### `/youtube <playlist-name>` (one-playlist mode)

1. Read `~/Projects/K2B-Vault/wiki/context/youtube-playlists.md` as above.
2. Find the playlist whose `name` matches the argument case-insensitively. The argument can match the full name ("K2B Invest") or just the suffix ("invest", "Invest", "INVEST").
3. If no match: tell Keith which names are available (list them from the config) and stop. Do not run the pipeline.
4. If the match is an outbound playlist (K2B Watch): explain that K2B Watch is the destination for `/research videos` picks, not a capture source, and stop.
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

The cascade logic lives in `scripts/yt-transcript.sh` so the batch playlist flow (this skill) and the Telegram ad-hoc URL flow (k2b-remote) share one code path. Call it and read the method from stderr:

```bash
TRANSCRIPT=$(~/Projects/K2B/scripts/yt-transcript.sh "<video-url>" 2>/tmp/yt-transcript-err.txt)
METHOD=$(grep "^METHOD:" /tmp/yt-transcript-err.txt | tail -1 | awk '{print $2}')
```

The helper tries these tiers in order:

- **captions-en** -- `yt-dlp --write-auto-sub --sub-langs "en,en-.*"` (YouTube auto-captions)
- **captions-zh** -- same but Chinese
- **groq-whisper** -- downloads audio with `yt-playlist-poll.sh --extract-audio`, splits into 240s chunks if needed, sends each chunk to Groq Whisper `whisper-large-v3`
- **failed** -- all tiers exhausted

Exit 0 means a transcript is on stdout. Exit 1 means `METHOD: failed` and no transcript -- create a minimal note with `transcript_method: failed` and the video metadata only, and flag it for manual review.

Set `transcript_method:` in the note's frontmatter to the helper's reported method (`captions-en`, `captions-zh`, `groq-whisper`, or `failed`).

**Tier 2b -- OpenAI Whisper (paid fallback, only if Groq fails):**

The unified helper does not include OpenAI Whisper -- Groq has been reliable enough that the paid fallback hasn't been needed since 2026-04. If Groq starts failing, restore the fallback in `yt-transcript.sh` rather than duplicating it here:

```bash
WHISPER_KEY=$(grep OPENAI_API_KEY ~/.zshrc 2>/dev/null | head -1 | sed "s/export OPENAI_API_KEY=//;s/'//g")
curl -s --retry 1 https://api.openai.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $WHISPER_KEY" \
  -F "file=@<chunk-or-file>" -F "model=whisper-1" \
  -F "language=zh" -F "response_format=text"
```

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

For **K2B Content** playlist: also create `wiki/content-pipeline/content_<slug>.md` for each content seed found.

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

The following subcommands existed briefly and were all retired 2026-04-14 along with the YouTube conversational agent:

- **`/youtube recommend`** / **`/youtube morning`** / **`/youtube cleanup`** -- these belonged to the 6-hour agent loop in `k2b-remote`. The whole loop, the taste model, the channel affinity scoring, and the nudge pipeline were deleted. Fresh-video discovery is now `/research videos "<query>"` via NotebookLM.
- **`/youtube <url>`** (direct URL screening) -- the Telegram bot's `handleDirectYouTubeUrl` path was deleted in the same cleanup. To capture a single URL to the vault today, paste it into a K2B category playlist in YouTube and run `/youtube <playlist-name>`.

  For ad-hoc Q&A on a single URL (summarise, fact-check, explain), just send the URL to the Telegram bot directly. As of 2026-04-21 the bot pre-fetches the transcript via `scripts/yt-transcript.sh` before the agent runs, so the agent answers your question using the transcript without reinventing the fetch each time. See `wiki/concepts/feature_telegram-url-prefetch.md`. This path does NOT save to the vault -- it's disposable triage. For vault capture, still use the playlist flow.
- **`/youtube status`** -- check `wiki/context/youtube-processed.md` directly.

If Keith asks for any of these commands, point him at `/research videos "<query>"` (for discovery) or the batch `/youtube` flow (for saved videos). The retired feature's spec is at [[Shipped/2026-04-08_feature_youtube-agent]].

## Error Handling

- If yt-dlp is not installed: `brew install yt-dlp`
- If video is >2 hours: warn Keith and ask for confirmation before processing
- If audio file is >25MB (Whisper limit): warn Keith. For long videos, prefer YouTube Transcript MCP. If no captions, suggest splitting audio with ffmpeg.
- If Whisper API fails (key invalid, quota exceeded, model not found): try model cascade (whisper-1 → gpt-4o-transcribe → gpt-4o-mini-transcribe), then try key from ~/.zshrc. If all fail, create note with `transcript_method: failed` and flag.
- If playlist URL returns no results: skip silently and move to the next playlist.
- If `/youtube <name>` matches no playlist: list available names from `youtube-playlists.md` and stop.