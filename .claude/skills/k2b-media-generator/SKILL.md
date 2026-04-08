# K2B Media Generator

Generate images, speech, audio transcriptions, video, and music using the MiniMax API (minimaxi.com). Powered by Keith's Plus subscription.

## Commands

- `/media image "prompt" [aspect] [slug]` -- Generate an image
- `/media speech "text" [voice] [emotion] [slug]` -- Generate TTS audio
- `/media transcribe <audio-file> [slug]` -- Transcribe audio via Groq Whisper (Chinese/English/50+ languages)
- `/media video "prompt" [slug]` -- Generate video clip (requires Max tier)
- `/media music "description" [slug]` -- Generate music track (requires Max tier)
- `/media for <idea-slug>` -- Auto-generate media for a content idea
- `/media voices` -- List available voices

## Paths

- Scripts: `~/Projects/K2B/scripts/minimax-*.sh`
- Assets: `~/Projects/K2B-Vault/Assets/` (images/, audio/, video/)
- Vault: `~/Projects/K2B-Vault`

## Integration Method

**Primary: MCP Server** (when available in session)
The MiniMax MCP server (`minimax-mcp-js`) provides direct tools:
- `text_to_image` -- image generation
- `text_to_audio` -- TTS
- `generate_video` -- video generation (Max tier)
- `query_video_generation` -- poll async video task
- `music_generation` -- music (Max tier)
- `voice_clone` -- clone a voice from audio sample
- `voice_design` -- generate custom voice from description
- `list_voices` -- show available voices

**Fallback: Bash Scripts**
If MCP tools are unavailable, use the bash scripts:
```bash
./scripts/minimax-image.sh "prompt" [aspect] [slug]
./scripts/minimax-speech.sh "text" [voice] [emotion] [slug]
./scripts/minimax-transcribe.sh <audio-file> [slug]
```

## Image Generation

### Parameters
- **prompt**: Description of the image to generate
- **aspect**: `1:1`, `16:9`, `4:3`, `3:2`, `2:3`, `3:4`, `9:16`, `21:9` (default: `16:9`)
- **slug**: Filename slug (auto-generated from prompt if omitted)
- Model: `image-01`

### Workflow
1. If using MCP: call `text_to_image` with prompt and aspect_ratio
2. If using bash: run `scripts/minimax-image.sh "prompt" aspect slug`
3. Asset saves to `K2B-Vault/Assets/images/YYYY-MM-DD_image_slug.png`
4. Print the Obsidian embed: `![[Assets/images/YYYY-MM-DD_image_slug.png]]`
5. If generating for a vault note, update that note with the embed link

### Style Tips for Prompts
- LinkedIn headers: "professional, corporate, modern, clean design"
- YouTube thumbnails: "bold, eye-catching, high contrast, text-friendly composition"
- Add context from the content idea to make it relevant

## Speech (TTS)

### Parameters
- **text**: Text to convert to speech (up to 10,000 chars; daily limit ~4,000 chars on Plus tier)
- **voice**: Voice ID (default: `male-qn-qingse`). Use `/media voices` or MCP `list_voices` to see options
- **emotion**: `neutral`, `happy`, `sad`, `angry`, `fearful`, `disgusted`, `surprised` (default: `neutral`)
- **slug**: Filename slug
- Model: `speech-2.8-hd`

### Workflow
1. If using MCP: call `text_to_audio` with text, voiceId, emotion
2. If using bash: run `scripts/minimax-speech.sh "text" voice emotion slug`
3. Asset saves to `K2B-Vault/Assets/audio/YYYY-MM-DD_speech_slug.mp3`
4. Print embed: `![[Assets/audio/YYYY-MM-DD_speech_slug.mp3]]`

### Language Support
40 languages including Mandarin, Cantonese, English. Set `languageBoost` to the primary language for best results, or `auto` for mixed-language text.

## Audio Transcription (STT)

### Parameters
- **audio-file**: Path to audio file (mp3, wav, m4a, oga, ogg, etc.)
- **slug**: Output filename slug
- Supports: Mandarin, Cantonese, English, and 50+ languages via Groq Whisper

### Transcription Procedure

**Always follow this procedure. No exceptions. No trying OpenAI first.**

#### Step 1: Check duration and size
```bash
DURATION=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "<audio-file>")
SIZE=$(stat -f%z "<audio-file>" 2>/dev/null || stat -c%s "<audio-file>")
echo "Duration: ${DURATION}s | Size: $((SIZE / 1024 / 1024))MB"
```

#### Step 2: Convert format if needed
m4a, oga, ogg, wav files must be converted to mp3 before sending to API:
```bash
ffmpeg -i "<audio-file>" -acodec libmp3lame -ab 128k /tmp/k2b-transcribe-input.mp3 -y
```
If already mp3, copy to `/tmp/k2b-transcribe-input.mp3`.

#### Step 3: Pre-split if >4 minutes or >20MB
```bash
# Split into 4-minute (240s) chunks
ffmpeg -i /tmp/k2b-transcribe-input.mp3 -f segment -segment_time 240 -c copy /tmp/k2b-transcribe-chunk_%02d.mp3 -y
```
If file is under 4 minutes AND under 20MB, skip splitting -- use the single file directly.

#### Step 4: Transcribe via Groq Whisper (primary)
```bash
GROQ_KEY=$(grep GROQ_API_KEY ~/Projects/K2B/k2b-remote/.env | cut -d= -f2)

# For each chunk (or single file):
curl -s --retry 2 --retry-delay 3 \
  https://api.groq.com/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer $GROQ_KEY" \
  -F "file=@<chunk-file>" \
  -F "model=whisper-large-v3" \
  -F "response_format=text"
```
- Model: `whisper-large-v3` (free tier, high quality)
- Use `--retry 2` to handle intermittent SSL resets (curl exit 35)
- For Cantonese/Mandarin: add `-F "language=zh"` for better accuracy
- Concatenate chunk results in order with a blank line between

#### Step 5: Fallback to OpenAI Whisper (only if Groq fails)
```bash
WHISPER_KEY=$(grep OPENAI_API_KEY ~/.zshrc 2>/dev/null | head -1 | sed "s/export OPENAI_API_KEY=//;s/'//g")

curl -s --retry 1 \
  https://api.openai.com/v1/audio/transcriptions \
  -H "Authorization: Bearer $WHISPER_KEY" \
  -F "file=@<chunk-or-file>" \
  -F "model=whisper-1" \
  -F "response_format=text"
```
Same pre-split logic applies. Set `transcript_method: openai-whisper`.

> **Note:** MiniMax does NOT have STT/transcription. Their audio APIs are TTS, voice cloning, and voice design only. Do not use `minimax-transcribe.sh` -- it calls a non-existent endpoint.

#### Step 6: Clean up
```bash
rm -f /tmp/k2b-transcribe-input.mp3 /tmp/k2b-transcribe-chunk_*.mp3
```

### API Limits
- **Groq**: ~25MB per request, free tier. Best reliability under 4 minutes per chunk.
- **OpenAI**: Paid fallback. Model `whisper-1`. 25MB file limit.

### Workflow (after transcription)
1. Transcription saves to `K2B-Vault/raw/daily/YYYY-MM-DD_transcription_slug.md`
2. The output note includes frontmatter, the full transcript, and an embed of the source audio
3. Keith can then process the transcription (move to Notes/, link to meetings, extract insights)
4. Set `transcript_method: groq-whisper` or `minimax` in frontmatter

### Use Cases
- Transcribe Mandarin/Cantonese meetings that Fireflies might miss
- Process voice memos from phone
- Transcribe interview recordings for recruitment notes

## Video Generation (Max Tier Required)

If Keith is on Plus tier, show this message:
> Video generation requires the Max tier (198 RMB/mo). Current tier: Plus. Use `/media image` for static visuals instead.

### Parameters (when available)
- **prompt**: Video description. Use camera movements in brackets: `[Pan left]`, `[Zoom in]`, `[Static shot]`, `[Pedestal up]`, `[Tilt down]`
- **slug**: Filename slug
- Model: `MiniMax-Hailuo-2.3` (1080p, 6s) or `MiniMax-Hailuo-2.3-Fast` (drafts)

### Workflow
1. Call MCP `generate_video` with prompt and model
2. Video generation is async -- call `query_video_generation` with the task_id to poll
3. When complete, download to `K2B-Vault/Assets/video/YYYY-MM-DD_video_slug.mp4`

## Music Generation (Max Tier Required)

If Keith is on Plus tier, show the same upgrade message as video.

### Parameters (when available)
- **description**: Music description (genre, mood, instruments, tempo)
- Model: `music-2.5+`

## `/media for <idea-slug>` -- Content Idea Media Generation

This is the high-value workflow. Reads a content idea and generates appropriate media.

### Workflow
1. Read `K2B-Vault/wiki/content-pipeline/idea_<slug>.md`
2. Extract: title, hook, platform (linkedin/youtube), core insight
3. Based on platform:
   - **LinkedIn**: Generate a 16:9 header image using the hook + topic as prompt context. Add style: "professional, corporate, modern design, suitable for LinkedIn"
   - **YouTube**: Generate a 16:9 thumbnail. Add style: "bold, eye-catching, high contrast, YouTube thumbnail style"
4. Optionally generate TTS of the hook/summary (ask Keith first if he wants audio)
5. Update the idea note by adding a `## Generated Assets` section:
   ```markdown
   ## Generated Assets

   ![[Assets/images/YYYY-MM-DD_image_slug.png]]
   ```
6. Print confirmation with the embed path

## Asset Naming Convention

All generated files follow: `YYYY-MM-DD_type_slug.ext`

- Images: `Assets/images/2026-03-25_image_ai-recruiting.png`
- Speech: `Assets/audio/2026-03-25_speech_insight-summary.mp3`
- Music: `Assets/audio/2026-03-25_music_intro-theme.mp3`
- Video: `Assets/video/2026-03-25_video_youtube-intro.mp4`

## Voice Cloning (Future)

Keith can clone his own voice for narration:
1. Record a 1-2 minute voice sample (clear, no background noise)
2. Use MCP `voice_clone` to upload the sample and create a custom voice ID
3. Use that voice ID as the default for all future `/media speech` calls
4. Store the voice ID in a K2B memory or config file

## Usage Logging

After completing the main task, log the invocation:
```bash
echo -e "$(date +%Y-%m-%d)\tk2b-media-generator\t$(echo $RANDOM | md5sum | head -c 8)\tgenerated TYPE: DESCRIPTION" >> ~/Projects/K2B-Vault/wiki/context/skill-usage-log.tsv
```

## Notes

- No em dashes, no AI cliches
- Always confirm with Keith before generating multiple assets (API quota awareness)
- Plus tier daily limits: ~50 images, ~4,000 chars speech. Plan accordingly.
- For batch generation, spread across days rather than burning quota in one session
- Always print the Obsidian embed path so Keith can paste it into notes
- If API key is not set, tell Keith: "Set MINIMAX_API_KEY in your shell environment. Get it from minimaxi.com dashboard."
