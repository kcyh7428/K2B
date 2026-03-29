---
name: k2b-youtube-capture
description: Capture insights from YouTube videos -- processes playlists and direct URLs with playlist-specific analysis, handles Chinese transcription via Whisper, creates vault notes. Use when Keith says /youtube, pastes a YouTube URL, or wants to check playlists for new videos.
---

# K2B YouTube Capture

Process YouTube videos saved to playlists with playlist-specific analysis. Also recommends new videos for Keith's commute.

## Commands

- `/youtube` -- Poll all configured playlists for new videos and process them
- `/youtube <url>` -- Process a single YouTube video (standard analysis)
- `/youtube recommend` -- Run the recommendation engine (find new videos, add to K2B Watch)
- `/youtube status` -- Show processing stats and playlist config

## Paths

- Vault: `~/Projects/K2B-Vault`
- Scripts: `~/Projects/K2B/scripts/`
- Playlist config: `~/Projects/K2B-Vault/Notes/Context/youtube-playlists.md`
- Processed log: `~/Projects/K2B-Vault/Notes/Context/youtube-processed.md`
- Output notes: `~/Projects/K2B-Vault/Inbox/`
- YouTube token: `~/.config/k2b/youtube-token.json`
- OAuth client: `~/.config/gws/client_secret.json`

## Workflow: Process Playlists (`/youtube`)

### 1. Read Playlist Config

Read `~/Projects/K2B-Vault/Notes/Context/youtube-playlists.md`. Extract the YAML from the fenced code block. Parse into playlist objects.

Skip playlists where `url` is `PLACEHOLDER`. Skip playlists where `type: outbound` (those are for recommendations, not capture).

### 2. Poll Each Playlist

For each inbound playlist, run:
```bash
~/Projects/K2B/scripts/yt-playlist-poll.sh "<playlist-url>" "~/Projects/K2B-Vault/Notes/Context/youtube-processed.md" --max 3
```

This outputs tab-separated lines: `video_id\ttitle\tupload_date\turl`

If no output, the playlist has no new videos -- skip it.

### 3. Process Each New Video

For each new video found:

#### 3a. Get Metadata

Use `mcp__YouTube_Transcript_MCP_Server__get_video_info` to get title, channel, duration, description.

#### 3b. Get Transcript (Cascade)

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

#### 3c. Analyze with Playlist Focus

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

#### 3d. Create Vault Note

Save to `Inbox/YYYY-MM-DD_youtube_<video-slug>.md` using the k2b-vault-writer conventions.

Frontmatter (**MANDATORY: includes Inbox Write Contract fields**):
```yaml
---
tags: [youtube, video-capture, {playlist-specific-tags}]
date: YYYY-MM-DD
type: video-capture
origin: k2b-extract
source: "[{title}]({url})"
channel: {channel_name}
playlist: {playlist_name}
transcript_method: {youtube-api|openai-whisper|failed}
up: "{playlist up link}"
review-action:
review-notes: ""
---
```

Before saving, verify: review-action and review-notes are present. All Inbox notes require these for Keith's Obsidian review workflow.

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

For **K2B Content** playlist: also create `Notes/Content-Ideas/idea_<slug>.md` for each content seed found.

For **K2B Learn** playlist: append to or create `Notes/Context/learning-paths.md` linking this video in sequence.

#### 3e. Update Processed Log

Append to `~/Projects/K2B-Vault/Notes/Context/youtube-processed.md`:
```
{video_id} | {YYYY-MM-DD} | {playlist_name} | {title}
```

#### 3f. Log Usage

Append to skill-usage-log.tsv following the k2b-usage-tracker pattern.

### 4. Summary

After processing all videos, show Keith a summary:
- How many playlists checked
- How many new videos processed
- Brief one-liner for each video processed (title, playlist, transcript method)

## Workflow: Single URL (`/youtube <url>`)

1. Extract video ID from URL
2. Check if already in processed log -- if so, tell Keith and offer to reprocess
3. Run steps 3a-3f above with standard analysis (K2B default prompt_focus)
4. If transcript fails, ask Keith if he wants to provide the OpenAI API key

## Workflow: Recommend (`/youtube recommend`)

Find relevant YouTube content and add to Keith's K2B Watch playlist.

1. Read playlist config to get K2B Watch playlist ID (extract from URL)
2. Read recent vault context:
   - Last 5 daily notes
   - Active projects (glob Notes/Projects/)
   - Recent content ideas
3. Generate 3-5 search queries based on Keith's interests:
   - Content pillars: AI in Recruitment, Executive AI Adoption, Workflow Automation, Building a Second Brain
   - Active project topics
   - Recent learning gaps
4. For each query, run: `scripts/yt-search.sh "<query>" --max 10`
5. Deduplicate against youtube-processed.md
6. Score each video (0-10): relevance, recency, channel quality, duration fit (15-45min for commute)
7. Pick top 3-5 videos
8. Add each to K2B Watch playlist: `scripts/yt-playlist-add.sh <playlist-id> <video-id>`
9. Create `Inbox/YYYY-MM-DD_youtube-recommendations.md` with the picks and reasoning
10. Log each as `recommended` in youtube-processed.md

## Workflow: Status (`/youtube status`)

1. Read youtube-processed.md, count videos by playlist and by month
2. Read youtube-playlists.md, show which playlists are configured vs PLACEHOLDER
3. Show last 5 processed videos with dates

## Scheduled Task

For automated polling, set up via `/schedule`:
```
/schedule daily 10am "You are K2B. Working directory: ~/Projects/K2B. Vault: ~/Projects/K2B-Vault. Run the YouTube playlist capture workflow: read playlist config from Notes/Context/youtube-playlists.md, poll each playlist for new videos using scripts/yt-playlist-poll.sh, process each new video (get transcript, analyze with playlist focus, create vault note), update youtube-processed.md. Use the k2b-youtube-capture skill instructions."
```

## Error Handling

- If yt-dlp is not installed: `brew install yt-dlp`
- If video is >2 hours: warn Keith and ask for confirmation before processing
- If audio file is >25MB (Whisper limit): warn Keith. For long videos, prefer YouTube Transcript MCP. If no captions, suggest splitting audio with ffmpeg.
- If Whisper API fails (key invalid, quota exceeded, model not found): try model cascade (whisper-1 → gpt-4o-transcribe → gpt-4o-mini-transcribe), then try key from ~/.zshrc. If all fail, create note with `transcript_method: failed` and flag
- If session OPENAI_API_KEY differs from ~/.zshrc key: warn Keith to restart session or update env
- If playlist URL returns no results: skip silently
- If youtube-token.json is missing (for recommend): tell Keith to run `scripts/yt-auth.sh`
