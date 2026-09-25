---
name: k2b-youtube-transcribe
description: Transcribe or closely summarize one YouTube video from a URL during an active Codex conversation. Use when Keith asks for spoken instructions, a transcript, subtitles, or what a video says. Start without YouTube login; recover public audio with a temporary current yt-dlp and Groq Whisper when captions or the installed downloader fail. This is not the dormant playlist or vault-capture lane.
---

# K2B YouTube transcription

Use this for an explicitly supplied single video. Work on the current authorized Home or SJM Mac. Do not create a vault note, run a playlist job, or activate a background routine unless Keith separately requests it. The YouTube Data API login used by `yt-search.py` and playlist operations is not needed to transcribe a public URL.

## Public-first command

From the active K2B checkout, run:

```bash
ERRFILE=$(mktemp -t k2b-yt-transcript.XXXXXX)
OUTFILE=$(mktemp -t k2b-yt-transcript.XXXXXX)
trap 'rm -f "$ERRFILE" "$OUTFILE"' EXIT
trap 'exit 130' INT TERM
if K2B_YT_COOKIES_FILE=none YT_DLP_COOKIE_BROWSER=none \
   K2B_YT_CURRENT_DLP_FALLBACK=1 \
   scripts/yt-transcript.sh "<youtube-url>" --language zh \
   >"$OUTFILE" 2>"$ERRFILE"; then
  cat "$OUTFILE"
  awk '$1 == "METHOD:" { method = $2 } END { print "METHOD: " method }' "$ERRFILE" >&2
else
  cat "$ERRFILE" >&2
  exit 1
fi
```

Omit `--language zh` when the spoken language is unknown, or use its actual language code. Read `METHOD:` in stderr and check that stdout contains a plausible transcript. The normal helper tries captions first, then its installed-downloader/Groq route. When that fails, the explicit fallback downloads the pinned official yt-dlp 2026.08.19 release into a private temporary directory and verifies its SHA256 before execution. Its bundled EJS component runs with Deno; remote component fetching is disabled. It downloads public audio **without cookies** and sends the local file to the existing Groq Whisper helper. It removes the temporary downloader and audio after the run. This does not update the installed yt-dlp or change YouTube credentials. The download defaults to a 900-second limit and the full recovery to 1200 seconds; a longer video may need an explicit bounded override or another source. If a future YouTube change breaks the pinned release, verify a new official version and digest before updating the pin.

Keep the transcript in a temporary file if it is long; read it in bounded passages. The output is speech recognition, not an exact subtitle source. Cross-check names, commands, model versions, and paths against the creator's linked material and primary project documentation. Cite the video and any corroborating sources in the answer; label words that remain uncertain. For a long video, transcribe in sequential chunks if the normal helper hits an API or size limit. Do not call a failed or partial chunk a complete transcript.

If the public-first command fails, report the concrete failure: no accessible captions, current downloader network/403, Deno missing, Groq authorization/quota, or other observed error. A video that actually requires a signed-in account may need the existing local cookie fallback, but do not read or export cookies just because public download failed. Do not promise access to private, region-restricted, age-gated, DRM-protected, or unavailable videos. A transcript connector or YouTube's visible transcript is a read-only last resort when the local route fails.

After use, remove temporary transcript and error files when no longer needed. Do not place credentials or private cookie files in the repository, and do not enable paid OpenAI API transcription.
