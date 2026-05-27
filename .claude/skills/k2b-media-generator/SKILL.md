# K2B Media Generator

Generate images, speech, audio transcriptions, video, and music. Images and speech default to GPTsAPI (`gpt-image-2`, `tts-1-hd`). Transcription defaults to GPTsAPI Whisper (`whisper-1`) with Groq Whisper as fallback. MiniMax image fallback was retired on 2026-05-27.

## Commands

- `/media image "prompt" [aspect] [slug]` -- Generate an image via GPTsAPI `gpt-image-2`
- `/media speech "text" [voice] [model] [slug]` -- Generate TTS audio via GPTsAPI `tts-1-hd`
- `/media transcribe <audio-file> [language] [slug]` -- Transcribe audio via GPTsAPI Whisper (Chinese/English/50+ languages); Groq Whisper available as fallback
- `/media video "prompt" [slug]` -- Generate video clip (requires Max tier; MiniMax)
- `/media music "description" [slug]` -- Generate music track (requires Max tier; MiniMax)
- `/media for <idea-slug>` -- Auto-generate media for a content idea
- `/media voices` -- List available voices

## Paths

- Scripts (image/VLM): `~/Projects/K2B/scripts/gptsapi-image.sh`, `~/Projects/K2B/scripts/gptsapi-vlm.sh`
- Scripts (speech + transcription): `~/Projects/K2B/scripts/gptsapi-speech.sh`, `~/Projects/K2B/scripts/gptsapi-transcribe.sh`
- **Retired (do NOT call):** the legacy MiniMax image and OCR wrapper scripts were deleted 2026-05-27. `scripts/minimax-speech.sh` deleted 2026-05-16. `mcp__minimax__text_to_audio` MCP tool still exists in the agent's tool list but its backend is dead (status_code 2049). `scripts/minimax-transcribe.sh` does not exist and MUST NOT be created -- MiniMax has no STT endpoint.
- Assets: `~/Projects/K2B-Vault/Assets/` (images/, audio/, video/)
- Vault: `~/Projects/K2B-Vault`

## Integration Method

**Image primary: GPTsAPI wrapper**
Use the bash wrapper for image generation:
```bash
./scripts/gptsapi-image.sh --prompt "prompt" --aspect-ratio 16:9 --slug slug
```

The wrapper submits an async `gpt-image-2` prediction, polls for completion for up to 120 seconds, downloads or decodes the returned image payload, and saves it to `K2B-Vault/Assets/images/`. Typical completion time is 30-45 seconds. In Telegram, `/media image` sends a progress message first so Keith does not see a silent wait.

**Other media: MiniMax MCP Server** (when available in session)
The MiniMax MCP server (`minimax-mcp-js`) provides direct tools:
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
./scripts/gptsapi-image.sh --prompt "prompt" --aspect-ratio 16:9 --slug slug
./scripts/gptsapi-speech.sh "text" [voice] [model] [slug]
./scripts/gptsapi-transcribe.sh <audio-file> [language]
```

## Image Generation

### Parameters
- **prompt**: Description of the image to generate
- **aspect**: GPTsAPI supports `1:1`, `16:9`, `9:16`, `4:3`, `3:4` (default: `16:9`).
- **slug**: Filename slug (auto-generated from prompt if omitted)
- Default model: GPTsAPI `gpt-image-2`

### Provider choice
- Default GPTsAPI for executive editorial images, typography, diagrams, quote cards, LinkedIn headers, and clean business visuals.
- TTS now routes through GPTsAPI by default (see Speech section below). MiniMax TTS retired 2026-05-14 -- the Token Plan tier allocates zero TTS quota and pay-per-call MiniMax requires a separate Standard API key with Credits topped up. GPTsAPI uses the existing `GPTSAPI_KEY` and same billing as image generation.

### Workflow
1. Default: run `scripts/gptsapi-image.sh --prompt "prompt" --aspect-ratio aspect --slug slug`
2. Asset saves to `K2B-Vault/Assets/images/YYYY-MM-DD_image_slug.png`
3. **Send to Telegram**:
   - If running through k2b-remote `/media image`, the bot sends a progress message, waits for the wrapper, then sends the photo directly.
   - If running inside an agent session, write an outbox manifest so the bot delivers the image to Keith:
   ```bash
   ~/Projects/K2B/scripts/telegram-outbox-write.sh photo \
     "$HOME/Projects/K2B-Vault/Assets/images/YYYY-MM-DD_image_slug.png" \
     "description"
   ```
4. Print the Obsidian embed: `![[Assets/images/YYYY-MM-DD_image_slug.png]]`
5. If generating for a vault note, update that note with the embed link

### Style Tips for Prompts
- LinkedIn headers: "professional, corporate, modern, clean design"
- YouTube thumbnails: "bold, eye-catching, high contrast, text-friendly composition"
- Add context from the content idea to make it relevant

## Speech (TTS)

### Parameters
- **text**: Text to convert to speech (up to ~4,000 chars per call; longer text may need chunking)
- **voice**: One of `alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer` (default: `onyx`)
- **model**: `tts-1` (faster, cheaper) or `tts-1-hd` (higher quality, default)
- **slug**: Filename slug

### Workflow
1. Run `scripts/gptsapi-speech.sh "text" [voice] [model] [slug]`
2. Asset saves to `K2B-Vault/Assets/audio/YYYY-MM-DD_speech_slug.mp3`
3. **Send to Telegram** (if running via k2b-remote): write an outbox manifest:
   ```bash
   jq -n --arg path "$HOME/Projects/K2B-Vault/Assets/audio/YYYY-MM-DD_speech_slug.mp3" --arg caption "description" \
     '{type:"audio", path:$path, caption:$caption}' > ~/Projects/K2B/k2b-remote/workspace/telegram-outbox/$(date +%s)_$RANDOM.json
   ```
4. Print embed: `![[Assets/audio/YYYY-MM-DD_speech_slug.mp3]]`

### Language Support
GPTsAPI `tts-1` and `tts-1-hd` (OpenAI's TTS) handle 50+ languages including Mandarin, Cantonese, English, Japanese, Korean, and most European languages. Language is auto-detected from the input text; no `languageBoost` parameter needed.

### MiniMax TTS retired
`scripts/minimax-speech.sh` was **deleted 2026-05-16** after the 2026-05-15 agent-path incident, where the agent (responding to a natural-language voice request rather than `/media speech`) fell through to MiniMax TTS via `mcp__minimax__text_to_audio` and then `scripts/minimax-speech.sh`, both of which failed with `status_code 2049 invalid api key`. The MiniMax Token Plan allocates zero TTS character quota despite the marketing copy listing voice generation. Pay-per-call MiniMax TTS requires a separate Standard API key with Credits topped up. The 2026-05-14 audit confirmed GPTsAPI `tts-1-hd` covers all K2B voice needs using the existing `GPTSAPI_KEY`. **The `mcp__minimax__text_to_audio` MCP tool is still in the agent's tool list (the MCP server exposes all its tools by default), but its backend is dead — never call it. Always invoke `scripts/gptsapi-speech.sh` via Bash for ANY TTS request, regardless of how the user phrases it.** Do not propose restoring MiniMax for TTS without a specific quality or feature reason.

## Audio Transcription (STT)

### Parameters
- **audio-file**: Path to audio file (mp3, wav, m4a, oga, ogg, flac, webm, mp4)
- **language**: Optional ISO-639-1 hint (e.g. `en`, `zh`, `yue` for Cantonese). Auto-detected if omitted.
- **slug**: Output filename slug

Supports: Mandarin, Cantonese, English, and 50+ languages via OpenAI Whisper (`whisper-1` through GPTsAPI).

### Transcription Procedure

**Default: GPTsAPI Whisper. Groq Whisper available as fallback for high-volume / free-tier needs.**

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

#### Step 4: Transcribe via GPTsAPI Whisper (primary)
```bash
# For each chunk (or single file):
~/Projects/K2B/scripts/gptsapi-transcribe.sh <chunk-file> [language]
```
- Model: `whisper-1` (OpenAI Whisper, exposed via GPTsAPI)
- Uses existing `GPTSAPI_KEY` -- same billing account as image generation
- For Cantonese/Mandarin: pass `zh` (or `yue` for Cantonese specifically) as the language argument
- Concatenate chunk results in order with a blank line between

#### Step 5: Fallback to Groq Whisper (high-volume / cost-sensitive)
```bash
GROQ_KEY=$(grep GROQ_API_KEY ~/Projects/K2B/k2b-remote/.env | cut -d= -f2)

curl -s --retry 2 --retry-delay 3 \
  https://api.groq.com/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer $GROQ_KEY" \
  -F "file=@<chunk-file>" \
  -F "model=whisper-large-v3" \
  -F "response_format=text"
```
- Model: `whisper-large-v3` (Groq free tier, high quality)
- Use `--retry 2` to handle intermittent SSL resets (curl exit 35)
- Set `transcript_method: groq-whisper`
- Best used when GPTsAPI credit is tight or for k2b-remote Telegram voice memos (currently still on Groq)

> **Note:** MiniMax does NOT have STT/transcription. Their audio APIs are TTS, voice cloning, and voice design only. Do not use `minimax-transcribe.sh` -- it calls a non-existent endpoint.

#### Step 6: Clean up
```bash
rm -f /tmp/k2b-transcribe-input.mp3 /tmp/k2b-transcribe-chunk_*.mp3
```

### API Limits
- **GPTsAPI**: `whisper-1`, 25MB per request, billed against GPTsAPI balance. Primary path.
- **Groq**: `whisper-large-v3`, ~25MB per request, free tier. Fallback / high-volume path.

### Workflow (after transcription)
1. Transcription saves to `K2B-Vault/raw/daily/YYYY-MM-DD_transcription_slug.md`
2. The output note includes frontmatter, the full transcript, and an embed of the source audio
3. Keith can then process the transcription (compile to wiki/, link to meetings, extract insights)
4. Set `transcript_method: gptsapi-whisper` (default) or `groq-whisper` (fallback) in frontmatter. MiniMax does not have transcription; do not use `minimax`.

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
1. Read `K2B-Vault/wiki/content-pipeline/content_<slug>.md`
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
- GPTsAPI image cost: see the current GPTsAPI dashboard for `gpt-image-2`.
- For batch generation, spread across days rather than burning quota in one session
- Always print the Obsidian embed path so Keith can paste it into notes
- API key error guidance, by command:
  - `/media image` (GPTsAPI default), `/media speech`, `/media transcribe` -- "Set `GPTSAPI_KEY` in your shell environment. Get it from gptsapi.net dashboard."
  - `/media video`, `/media music` (MiniMax-only modalities) -- "Set `MINIMAX_API_KEY` in your shell environment. Get it from minimaxi.com dashboard." Current MiniMax subscription state may still make these fail with `status_code 2049`.
  - `/media transcribe` fallback to Groq -- "Set `GROQ_API_KEY` in `~/Projects/K2B/k2b-remote/.env`."
