---
name: k2b-media-generator
description: Generate K2B media assets through GPTsAPI and Higgsfield -- images, image edits, video, music, speech (TTS), audio transcription, and high-end editorial presentation decks (HTML to PDF + PPTX). Use when Keith wants an image, an existing image transformed, a video, music, a voiceover, a transcription, or a stunning / visual-led slide deck or presentation.
---

# K2B Media Generator

## Live K2B Authority

- `AGENTS.md` is the instruction authority and `.agents/skills` is the only live skill root.
- Codex is the interactive commander; Kimi K2.7 is the background text worker.
- OpenAI-built diffs use Kimi review with no fallback; Kimi-built diffs use Codex review.
- Scheduled work must be a registered host job with an observable receipt; failures go to the Operations Console attention queue.
- Capture enters through the dashboard or a vault drop, never Telegram.
- Canonical memory is `K2B-Vault/System/memory`; read Codex sessions only when explicitly required and never read Claude state.

Generate images, image edits, speech, audio transcriptions, video, and music. Two providers:

- **GPTsAPI** -- default for executive editorial images, typography, diagrams, quote cards, LinkedIn headers, clean business visuals (`gpt-image-2`), plus speech (`tts-1-hd`) and transcription (`whisper-1`, Groq fallback).
- **Higgsfield** (Plus plan, credit-based) -- premium image models (Nano Banana Pro, GPT Image 2, Flux 2, Grok), **video** (Veo 3.1, Kling 3.0, Seedance 2.0), image-edit / image-to-video, Soul character consistency, product-photoshoot, TTS/music. This is what unblocks `/media video` and `/media music`, both dead since the MiniMax subscription lapsed 2026-05-27.

Provider routing rubric is in the **Provider Routing** section below. MiniMax image fallback was retired 2026-05-27.

## Commands

- `/media image "prompt" [aspect] [slug]` -- Generate an image via GPTsAPI `gpt-image-2`
- `/media speech "text" [voice] [model] [slug]` -- Generate TTS audio via GPTsAPI `tts-1-hd`
- `/media transcribe <audio-file> [language] [slug]` -- Transcribe audio via GPTsAPI Whisper (Chinese/English/50+ languages); Groq Whisper available as fallback
- `/media video "prompt" [slug]` -- Generate video via Higgsfield (default `seedance_2_0`; use `veo3_1` or `kling3_0` for premium). **MacBook Codex sessions only.** See the **Higgsfield** section. (MiniMax video stays retired -- never call any MiniMax API.)
- `/media music "description" [slug]` -- Generate music via Higgsfield (`sonilo_music`). **MacBook Codex sessions only.** See the **Higgsfield** section. (MiniMax music stays retired.)

> **Surface scope.** Media requests enter through a MacBook Codex session, the dashboard, or a vault drop. Video and music run only from a MacBook Codex session that calls `scripts/higgsfield.sh` directly; there is no background intake path for either command.
- `/media for <idea-slug>` -- Auto-generate media for a content idea
- `/media voices` -- List available voices
- **Presentation decks** (stunning / editorial) -- not a one-shot command; see the **Presentation Decks** section below for the HTML -> headless Chrome -> PDF + image-PPTX build workflow
- **Single infographics with exact wording** -- use the [[k2b-infographic]] skill instead. It composites text-free `gpt-image-2` panels under a crisp HTML/CSS frame so labels, captions and brand colours are EXACTLY what Keith asked for. Trigger the moment wording must be controlled.

## Paths

- Scripts (image/VLM): `~/Projects/K2B/scripts/gptsapi-image.sh`, `~/Projects/K2B/scripts/gptsapi-vlm.sh`
- Scripts (speech + transcription): `~/Projects/K2B/scripts/gptsapi-speech.sh`, `~/Projects/K2B/scripts/gptsapi-transcribe.sh`
- **Retired (do NOT call):** the legacy MiniMax image and OCR wrapper scripts were deleted 2026-05-27. `scripts/minimax-speech.sh` deleted 2026-05-16. `mcp__minimax__text_to_audio` MCP tool still exists in the agent's tool list but its backend is dead (status_code 2049). `scripts/minimax-transcribe.sh` does not exist and MUST NOT be created -- MiniMax has no STT endpoint.
- Assets: `~/Projects/K2B-Vault/Assets/` (images/, audio/, video/)
- Vault: `~/Projects/K2B-Vault`
- This `.agents/skills` copy is the sole live skill. Validate it with `scripts/verify-codex-authority.sh` before delivery.

## Integration Method

**Image primary: GPTsAPI wrapper**
Use the bash wrapper for image generation:
```bash
./scripts/gptsapi-image.sh --prompt "prompt" --aspect-ratio 16:9 --slug slug
```

The wrapper submits an async `gpt-image-2` prediction, polls for completion for up to 120 seconds, downloads or decodes the returned image payload, and saves it to `K2B-Vault/Assets/images/`. Typical completion time is 30-45 seconds. Keep the active Codex session visibly updated during the wait.

Loading `GPTSAPI_KEY` (agent / CLI runs). The wrapper reads `GPTSAPI_KEY` from its environment and does not source any `.env` itself. An agent or terminal session is a non-interactive shell that does NOT source `~/.zshrc`, so the key may be absent and the script exits `missing_gptsapi_key`. Before calling any `gptsapi-*` script from an agent/CLI session, load it from the approved K2B environment:

```
if [ -z "${GPTSAPI_KEY:-}" ]; then set -a; . "$HOME/Projects/K2B/k2b-remote/.env" 2>/dev/null; set +a; fi
```

`[ -n "${GPTSAPI_KEY:-}" ] || { echo "GPTSAPI_KEY not in k2b-remote/.env"; exit 1; }`
Never echo the key. Same preamble for `gptsapi-speech.sh`, `gptsapi-transcribe.sh`, `gptsapi-vlm.sh`.

**Image-to-image edit: GPTsAPI direct endpoint (experimental)**
When Keith wants to transform, restyle, preserve, or improve an existing image, use this user-provided GPTsAPI image-edit endpoint shape as a reference until a maintained wrapper exists. This path is not equivalent to `/media image`: no K2B wrapper, polling, automatic asset save, dashboard delivery, or Obsidian embed handling exists yet.

Endpoint:
```text
POST https://api.gptsapi.net/api/v3/openai/gpt-image-2/image-edit
Authorization: Bearer <GPTSAPI_KEY_FROM_ENV>
Content-Type: application/json
```

Payload shape:
```json
{
  "prompt": "Transform this product image into a premium e-commerce poster style.",
  "input_urls": ["<VERIFIED_PUBLIC_HTTPS_IMAGE_URL>"],
  "aspect_ratio": "1:1",
  "resolution": "1K"
}
```
`input_urls` must be HTTPS URL strings reachable by GPTSAPI, not local file paths, `file://` URLs, localhost/private-network URLs, or plain `http://` URLs. K2B does not yet have a maintained local-image-to-public-URL path for this endpoint. If the source image is private or only in the local vault, pause and ask Keith for a public/presigned HTTPS URL or build an upload/presign wrapper first. Do not paste raw API keys into chat or scripts; read `GPTSAPI_KEY` from the environment.

Safety constraints for image-edit:
- Manual-only. Do not call from cron, scheduled jobs, batch workflows, or unattended pipelines.
- Do not paste or run raw one-off curl commands from chat. Build or extend a wrapper first, or create a reviewed one-off script that reads `GPTSAPI_KEY` from the environment and uses a file-based payload.
- Process one image-edit request at a time. Confirm with Keith before multiple edits because pricing/rate limits may differ from plain `gpt-image-2`.
- `GPTSAPI_KEY` must only appear in the HTTP `Authorization` header. Never embed it in request bodies, URL query parameters, or form fields.
- Never send placeholder URLs. Verify every `input_urls` value is the actual public HTTPS image URL before sending.
- Keep the response JSON outside the vault. Never move, copy, paste, or reference the temp JSON file inside any vault note.

Output handling for image-edit:
1. Save the raw provider response to a temp JSON file outside the vault.
2. Inspect the JSON. If it contains only a task ID or an unknown schema, stop and build/verify a wrapper before polling or updating the vault. Delete the temp JSON after inspection; do not store it in the vault.
3. If the response clearly contains a downloadable image URL or base64 image payload, save through a reviewed script that creates `K2B-Vault/Assets/images/`, verifies it is writable, writes to a temp file in that directory, and atomically moves the completed image to `YYYY-MM-DD_image_edit_<slug>.png`. The script must use `mktemp` plus `trap`/`finally` cleanup for temp JSON and image files on success, error, and interruption. Do not write partial downloads directly to the final vault path.
4. Verify the saved image exists and is non-empty before printing `![[Assets/images/YYYY-MM-DD_image_edit_<slug>.png]]` or updating any vault note.

**Other media: MiniMax MCP Server** (DEAD since 2026-05-27 -- subscription lapsed; these tools return status_code 2049. Listed for reference only; do NOT call.)
The MiniMax MCP server (`minimax-mcp-js`) exposes these tools, but its backend is dead:
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

## Higgsfield

Higgsfield is K2B's premium generation provider and the only working path for **video** and **music**. Keith is on the **Plus plan** (credit-based, ~1096 credits as of 2026-07-15). The CLI (`@higgsfield/cli`) is installed at `~/.npm-global/bin/higgsfield` and authenticated via OAuth (`higgsfield auth login`) -- no env key.

### Wrapper (use this)
```bash
./scripts/higgsfield.sh --type image --model nano_banana_pro --prompt "prompt" --slug slug
./scripts/higgsfield.sh --type video --model seedance_2_0    --prompt "prompt" --slug slug
./scripts/higgsfield.sh --type audio --model sonilo_music    --prompt "prompt" --slug slug
```
The wrapper runs `generate create <model> --prompt ... --wait`, downloads the finished `result_url` into `K2B-Vault/Assets/{images,video,audio}/YYYY-MM-DD_<type>_<slug>.<ext>`, and prints the Obsidian embed + absolute path. Flags after a literal `--` pass straight to the CLI (e.g. `--aspect-ratio 16:9`, `--image-references ./ref.png`, `--start-image ./a.png`, `--end-image ./b.png`); local paths are auto-uploaded. Exit codes: 2 = not authenticated (run `higgsfield auth login`), 3 = generation failed (check `higgsfield account status` for credits), 4 = download failed.

### Key models (`higgsfield model list [--video]`)
- **Image**: `nano_banana_pro` (premium default), `gpt_image_2`, `flux_2`, `flux_kontext` (edits), `grok_image`, `text2image_soul_v2` (Soul character consistency).
- **Video**: `seedance_2_0` (default), `seedance_2_0_mini` (drafts), `veo3_1` / `veo3_1_lite` (Google Veo), `kling3_0` / `kling3_0_turbo` (Kling), `wan2_7`, `sync_so` (lipsync), `video_upscale`.
- **Audio**: `sonilo_music` (music), `text2speech_v2` / `inworld_text_to_speech` (TTS), `seed_audio`.
- **Also**: `product-photoshoot` and `soul-id` are dedicated CLI subcommands (brand-quality product shots, trained character refs) -- call the CLI directly for those; the wrapper covers generic generate jobs.

### Cost & safety
- Always check cost first for anything beyond a quick image: `higgsfield generate cost <model> --prompt "..."` (free, prints credits). Nano Banana image ~1 credit; video costs more.
- Confirm with Keith before batch runs or premium video -- credits are finite.
- Video jobs can take minutes. Keep the active Codex session visibly updated. The wrapper uses `--wait-timeout 20m`.
- Higgsfield authentication is per machine. This skill authorizes the existing MacBook OAuth path only.

### Dashboard / vault delivery
After the wrapper prints the asset path, print the `![[...]]` embed, update any target note, and surface the result in the active Codex session or dashboard. Do not create a Telegram outbox item.

## Provider Routing

| Want | Provider / path |
|---|---|
| Executive editorial image, typography, diagram, quote card, LinkedIn header, clean business visual | **GPTsAPI** `gptsapi-image.sh` (default) |
| Premium / photoreal / stylized image, or when GPTsAPI quality falls short | **Higgsfield** `nano_banana_pro` / `flux_2` |
| Consistent recurring character across images | **Higgsfield** `text2image_soul_v2` + `soul-id` |
| Brand product photoshoot | **Higgsfield** `product-photoshoot` |
| Image edit / restyle / image-to-image | **Higgsfield** `flux_kontext` (preferred -- real wrapper, auto-upload) over the experimental GPTsAPI image-edit endpoint |
| Video (any) | **Higgsfield** `seedance_2_0` default, `veo3_1` / `kling3_0` premium |
| Music | **Higgsfield** `sonilo_music` |
| Speech / TTS | **GPTsAPI** `gptsapi-speech.sh` default; Higgsfield `text2speech_v2` if a specific voice is needed |
| Transcription (STT) | **GPTsAPI** Whisper (Groq fallback). Higgsfield has `speech2text` but GPTsAPI stays default |
| Infographic with exact wording | **[[k2b-infographic]]** skill (never let a model render the label text) |

## Image Generation

### Parameters
- **prompt**: Description of the image to generate
- **aspect**: GPTsAPI supports `1:1`, `16:9`, `9:16`, `4:3`, `3:4` (default: `16:9`).
- **slug**: Filename slug (auto-generated from prompt if omitted)
- Default model: GPTsAPI `gpt-image-2`

### Image-to-image edit parameters
- **endpoint**: `POST https://api.gptsapi.net/api/v3/openai/gpt-image-2/image-edit`
- **prompt**: Required, max 20,000 characters.
- **input_urls**: Required array of image URLs, max 16 images. URLs must be externally reachable by GPTsAPI.
- **aspect_ratio**: `auto`, `1:1`, `9:16`, `16:9`, `4:3`, or `3:4`.
- **resolution**: `1K`, `2K`, or `4K`.
- **limits**: `1:1` cannot convert to `4K`. If `aspect_ratio` is `auto` or omitted, output is only `1K`.
- **cost**: Check the GPTSAPI dashboard for current image-edit pricing. Do not batch image edits without explicit confirmation.

### Provider choice
- Default GPTsAPI for executive editorial images, typography, diagrams, quote cards, LinkedIn headers, and clean business visuals.
- TTS now routes through GPTsAPI by default (see Speech section below). MiniMax TTS retired 2026-05-14 -- the Token Plan tier allocates zero TTS quota and pay-per-call MiniMax requires a separate Standard API key with Credits topped up. GPTsAPI uses the existing `GPTSAPI_KEY` and same billing as image generation.

### Workflow
1. Default: run `scripts/gptsapi-image.sh --prompt "prompt" --aspect-ratio aspect --slug slug`
2. Asset saves to `K2B-Vault/Assets/images/YYYY-MM-DD_image_slug.png`
3. Surface the saved asset in the active Codex session or dashboard.
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
3. Surface the saved asset in the active Codex session or dashboard.
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
- Best used when GPTsAPI credit is tight; retained voice transcription continues to use Groq.

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

## Video Generation (Higgsfield)

Video runs on **Higgsfield** (see the **Higgsfield** section). MiniMax video stays retired -- never call MCP `generate_video` / `query_video_generation`.

### Workflow
1. Check cost: `higgsfield generate cost seedance_2_0 --prompt "..."` (free). Confirm with Keith for premium models or batches.
2. Generate: `./scripts/higgsfield.sh --type video --model seedance_2_0 --prompt "prompt" --slug slug`
   - Premium: `--model veo3_1` or `--model kling3_0`.
   - Image-to-video: append `-- --start-image ./ref.png` (local path auto-uploads).
3. Saves to `K2B-Vault/Assets/video/YYYY-MM-DD_video_slug.mp4` (the wrapper reserves a collision-free name, so a retry never overwrites an earlier paid clip). Surface the saved asset in the active Codex session or dashboard; do not create an outbox item.
4. Print the embed `![[Assets/video/YYYY-MM-DD_video_slug.mp4]]`.

### Prompt tips
- Camera movement reads well in brackets: `[Pan left]`, `[Zoom in]`, `[Static shot]`, `[Tilt down]`.

## Music Generation (Higgsfield)

Music runs on **Higgsfield** `sonilo_music`. MiniMax music stays retired.

### Workflow
1. `./scripts/higgsfield.sh --type audio --model sonilo_music --prompt "genre, mood, instruments, tempo" --slug slug`
2. Saves to `K2B-Vault/Assets/audio/YYYY-MM-DD_audio_slug.<ext>`; deliver + embed as with speech.

## `/media for <idea-slug>` -- Content Idea Media Generation

This is the high-value workflow. Reads a content idea and generates appropriate media.

### Workflow
1. Read `K2B-Vault/wiki/content-pipeline/content_<slug>.md`
2. Extract: title, hook, platform (linkedin/youtube), core insight
3. If the idea note contains frontmatter `image_edit: true`, frontmatter `image_source: existing`, an Obsidian image embed (`![[...]]`), or a Markdown image (`![...](...)`), STOP. Image-edit is experimental and manual-only, and is never invoked from `/media for` or any automated workflow regardless of confirmation. End the `/media for` run with a note to Keith; a separate manual request handles image-edit.
4. Based on platform:
   - **LinkedIn**: Generate a 16:9 header image using the hook + topic as prompt context. Add style: "professional, corporate, modern design, suitable for LinkedIn"
   - **YouTube**: Generate a 16:9 thumbnail. Add style: "bold, eye-catching, high contrast, YouTube thumbnail style"
5. Optionally generate TTS of the hook/summary (ask Keith first if he wants audio)
6. Update the idea note by adding a `## Generated Assets` section:
   ```markdown
   ## Generated Assets

   ![[Assets/images/YYYY-MM-DD_image_slug.png]]
   ```
7. Print confirmation with the embed path

## Presentation Decks (Stunning / Editorial)

For high-end, visual-led decks (board, MD, or leadership facing), do NOT draw native shapes with pptxgenjs. Hand-build **HTML/CSS, render through headless Chrome to PDF, rasterize to images, and wrap the images in a PPTX**. This produces far better visual quality than pptxgenjs shapes. Proven 2026-06-15 building the SJM AI-HR discussion paper -- the rendered reference deck lives at `K2B-Vault/Assets/decks/2026-06-15_SJM_AI_HR_Discussion-Paper.pdf` (look at it before starting, to set the quality bar).

### Which route to use

| Want | Route |
|---|---|
| Editorial / "stunning" / visual-led / leadership-facing deck | **HTML route** (this section) |
| Quick deck the user will edit the text in afterward | Native pptxgenjs -- use the **anthropic-skills:pptx** skill |
| Both (polished now, editable later) | Ship the HTML PDF/PPTX as the headline AND offer an editable pptxgenjs version |

The HTML route's PPTX is **flat images, not editable text**. Always say so on delivery, and offer the native pptxgenjs version if the user needs to change wording.

### Pipeline

Pick a shell-safe `SLUG` (lowercase, no spaces or special characters) and work in a fresh scratch dir. Quote `"$WORK"` everywhere below so a stray space can never split a path:

```bash
SLUG=sjm-ai-hr; WORK="/tmp/deckbuild/$SLUG"; mkdir -p "$WORK"
```

**1. One HTML file, one `<section class="slide">` per slide, 1280x720 canvas each.** Put a design-system CSS in a single `<style>` block:

```css
*{margin:0;padding:0;box-sizing:border-box;
  -webkit-print-color-adjust:exact;print-color-adjust:exact;}  /* REQUIRED: without this Chrome drops every background fill in the PDF */
@page{size:1280px 720px;margin:0;}
.slide{width:1280px;height:720px;position:relative;overflow:hidden;page-break-after:always;}
.slide:last-child{page-break-after:auto;}
:root{
  --serif:'Charter','Iowan Old Style',Palatino,Georgia,serif;  /* macOS system serif */
  --sans:-apple-system,'Helvetica Neue',Arial,sans-serif;      /* macOS system sans  */
  /* one near-black ink, ONE accent, a warm paper bg, a hairline rule color */
}
```

Quality comes from the design discipline, NOT the toolchain:
- Refined, restrained palette: a near-black ink, ONE accent color, a warm paper background, a hairline divider. Restraint over decoration.
- Serif display + sans body pairing (Charter/Iowan serif headings, -apple-system sans body). Generous margins (~74px top, ~96px sides).
- A running header and footer on every slide (brand left, doc tag right; thin footer line at the bottom).
- Build framework diagrams (process loops, phase timelines, pillar rows, 2- and 3-column grids) in pure CSS flex/grid. No images for diagrams.

**2. Render to PDF with headless Chrome (verbatim):**
```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
"$CHROME" \
  --headless=new --disable-gpu --no-pdf-header-footer \
  --run-all-compositor-stages-before-draw \
  --print-to-pdf="$WORK/deck.pdf" \
  "file://$WORK/deck.html" &
chrome_pid=$!
( sleep 30
  if kill -0 "$chrome_pid" 2>/dev/null; then
    kill "$chrome_pid" 2>/dev/null || true
    sleep 5
    kill -9 "$chrome_pid" 2>/dev/null || true
  fi
) &
watchdog_pid=$!
wait "$chrome_pid"; chrome_rc=$?
kill "$watchdog_pid" 2>/dev/null || true
wait "$watchdog_pid" 2>/dev/null || true
if [[ "$chrome_rc" -ne 0 ]]; then
  echo "Chrome PDF render failed or timed out after 30s" >&2
  exit "$chrome_rc"
fi
command -v pdfinfo >/dev/null || {
  echo "pdfinfo not found; install poppler first: brew install poppler" >&2
  exit 1
}
pdfinfo "$WORK/deck.pdf" >/dev/null
```
Pass an absolute `file://` path. `--run-all-compositor-stages-before-draw` forces Chrome to finish layout and font loading before it prints, so no slide comes out half-rendered. `--headless=new` is verified on Chrome 149 (bare `--headless` also works on current Chrome). The render is normally 1-2s; the 30s watchdog uses only macOS-default shell tools, escalates from TERM to KILL after 5s, and prevents a stuck Chrome render from blocking the session indefinitely. `pdfinfo` verifies Chrome produced a readable PDF before the pipeline proceeds. (Do NOT wrap this in `timeout`/`gtimeout` -- neither ships on macOS by default, so it would fail with "command not found" unless you first `brew install coreutils`.)

**3. Rasterize to crisp 16:9 PNGs (192 DPI -> 2560x1440):**
```bash
command -v pdftoppm >/dev/null || {
  echo "pdftoppm not found; install poppler first: brew install poppler" >&2
  exit 1
}
pdftoppm -png -r 192 "$WORK/deck.pdf" "$WORK/slide"
# -> slide-1.png, slide-2.png, ... (pdftoppm zero-pads to the page count: 10+ slides -> slide-01..slide-10)
```

**4. Visual QA with fresh eyes: Read the PNGs.** Open each slide image and look for overflow, cramped spacing, weak contrast, misalignment. Fix the CSS, re-render from step 2, repeat until clean. Reading the rendered pixels catches what reading the HTML does not.

**5. Deliver BOTH artifacts:**
- **Primary: the vector PDF** (`deck.pdf`) -- sharp at any zoom, this is the headline deliverable.
- **Also: an image-based PPTX** for people who expect PowerPoint. Assemble with pptxgenjs, one full-bleed image per slide (proven `assemble.js`, 2026-06-15):

```js
// assemble.js -- run from inside $WORK. Globs whatever PNGs pdftoppm produced.
// Do NOT reconstruct names with a fixed pad width: pdftoppm zero-pads to the
// TOTAL page count, so a 9-slide deck is slide-1..slide-9 but a 10+ slide deck
// is slide-01..slide-10. Globbing + numeric sort is correct for any count.
const fs = require("fs"), path = require("path");
const pptxgen = require("pptxgenjs");
const p = new pptxgen();
p.layout = "LAYOUT_WIDE";                 // 13.333 x 7.5 in = 16:9 (1280px / 96dpi = 13.333in, 720 / 96 = 7.5)
const slides = fs.readdirSync(__dirname)
  .filter(f => /^slide-\d+\.png$/.test(f))
  .sort((a, b) => a.match(/\d+/)[0] - b.match(/\d+/)[0]);   // numeric, pad-width agnostic
for (const f of slides) {
  // 2560x1440 PNGs share the 16:9 ratio of the 13.333x7.5 frame, so they
  // downscale into it crisply -- no stretching, no aspect distortion.
  p.addSlide().addImage({ path: path.join(__dirname, f), x: 0, y: 0, w: 13.333, h: 7.5 });
}
p.writeFile({ fileName: path.join(__dirname, "deck.pptx") });
```
```bash
cd "$WORK" && { [ -f package.json ] || npm init -y; } && npm i pptxgenjs && node assemble.js
```

Save both to the vault, then clear the scratch dir:
```bash
dest_pdf="$HOME/Projects/K2B-Vault/Assets/decks/$(date +%F)_deck_$SLUG.pdf"
dest_pptx="$HOME/Projects/K2B-Vault/Assets/decks/$(date +%F)_deck_$SLUG.pptx"
test -s "$WORK/deck.pdf" && test -s "$WORK/deck.pptx"
pdfinfo "$WORK/deck.pdf" >/dev/null
python3 -m zipfile -t "$WORK/deck.pptx" >/dev/null
cp "$WORK/deck.pdf" "$dest_pdf"
cp "$WORK/deck.pptx" "$dest_pptx"
rm -rf "$WORK"   # node_modules + PNGs; remove once both artifacts are saved
```
New decks follow `YYYY-MM-DD_deck_<slug>.{pdf,pptx}`. (The 2026-06-15 reference deck predates this convention and keeps its descriptive name `2026-06-15_SJM_AI_HR_Discussion-Paper.pdf`.)

### Common mistakes
- Background colors/fills are missing in the PDF -> you dropped `print-color-adjust:exact`.
- Slides look blurry in the PPTX -> rasterize at `-r 192` (2560x1440), not the default 150.
- Content is clipped at a slide edge -> a block overflowed the 720px height and `overflow:hidden` hid it; QA the PNG in step 4 and trim the content.
- Chrome renders a blank/old page -> use an ABSOLUTE `file://` path, not a relative one.
- PPTX build dies with `ENOENT ... slide-01.png` (or silently drops slides) -> `pdftoppm` zero-pads to the page count, so a sub-10-slide deck is `slide-1.png` not `slide-01.png`. Glob the real files (the `assemble.js` above does) instead of reconstructing names with a fixed width.
- Calling the image-PPTX "editable" -> it is NOT text-editable; offer the native pptxgenjs version when the user needs to edit copy.

## Asset Naming Convention

All generated files follow: `YYYY-MM-DD_type_slug.ext`

- Images: `Assets/images/2026-03-25_image_ai-recruiting.png`
- Speech: `Assets/audio/2026-03-25_speech_insight-summary.mp3`
- Music: `Assets/audio/2026-03-25_music_intro-theme.mp3`
- Video: `Assets/video/2026-03-25_video_youtube-intro.mp4`
- Decks: `Assets/decks/2026-06-15_deck_sjm-ai-hr.pdf` and `.pptx` (see the **Presentation Decks** section)

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
  - `/media image` (GPTsAPI default), `/media speech`, `/media transcribe` -- "Set `GPTSAPI_KEY` in your shell environment. Get it from gptsapi.net dashboard." For agent/CLI runs the key must be in `k2b-remote/.env`; load it with the preamble in Integration Method (non-interactive shells do not source `~/.zshrc`).
  - `/media video`, `/media music` -- now run on **Higgsfield** (see the Higgsfield section). If a call fails with exit 2, the CLI is not authenticated on this machine: run `higgsfield auth login`. Exit 3 usually means out of credits: check `higgsfield account status`. MiniMax stays dead -- never set `MINIMAX_API_KEY` or call any MiniMax API for these.
  - `/media transcribe` fallback to Groq -- "Set `GROQ_API_KEY` in `~/Projects/K2B/k2b-remote/.env`."
