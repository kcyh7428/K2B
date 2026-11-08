# Unified K2B video extraction design

Date: 8 November 2026. Owner: Keith. Implementation authority: this chat's
instruction to prepare the plan and hand it off to develop and deploy.

## Agreed outcome

Keith pastes a Facebook or YouTube link into K2B on Home or SJM. One discoverable
skill, `k2b-video-extract`, obtains the actual content, explains it, and states
its evidence coverage. It replaces competing capture instructions, while
preserving the working Mini Facebook machinery as its starting point.

Example: Keith asks what ideas the Jack Roberts video gives. K2B verifies the
source, captures it, reads the transcript and relevant visual evidence, and
answers. A follow-up asking about a particular minute reads the retained run.
An inaccessible video produces an exact access failure, not an unlabelled
third-party transcript presented as a reviewed video.

## Components and ownership

- Home and SJM run the same reviewed skill and media-processing code locally.
  The requesting Mac verifies and processes its requested capture. SJM must not
  need Home to be awake for the accepted SJM path.
- The named Mini Codex task remains the browser and capture worker. It uses
  native Chrome controls, its own local session, and a fixed installed media
  helper. No old Mini K2B checkout, vault sync, Telegram runtime or scheduler is
  restored.
- The dedicated NAS capture share retains source media and derived artifacts.
  Both knowledge Macs need their own independently verified route to this share.
  NAS arrival is checked against a manifest digest independently returned by the
  Mini. A directory alone is not a mount.
- The assistant reads transcripts and images through its actual tools. Storage
  alone does not put them into the model's context. The assistant interprets
  content and chooses relevant moments. Code enforces supported sources,
  manifests, roles, budgets, deadlines, locks and validation.
- Media processing on SJM is a scoped new exception to the existing Home-only
  media rule. It applies only to requested media runs on the capture share, not
  vault raw captures, shared hubs, automatic memory or knowledge reconciliation.

## Acquisition decisions

1. Verify the requested source on the Mini through native Chrome. Retain the
   submitted and canonical URL, identity, publisher/title, duration and evidence
   of advancing playback. Resolve Facebook share/r paths from the actual page.
2. Use direct media extraction where it works. Preserve available timed native
   or automatic captions and their provenance. Verify and pin the official
   downloader and required JavaScript components; do not silently update code
   or fetch unreviewed plugins during ordinary requests.
3. Approved Mini-local session access is available for these on-demand requests
   when necessary. Cookies/tokens never move to Home, SJM, NAS, logs or another
   service. Use the selected Mini profile, not broad automatic profile discovery.
   A persistent browser session can expire. An actual login or security prompt
   remains an operator step; never automate a CAPTCHA or weaken protections.
4. When the source is playable but direct extraction fails, use a tested local
   screen/window and system-audio recorder. For a short social source already
   known to reject direct extraction, go straight to the accepted recorder.
   An absent official download button is not proof extraction will fail.
5. Recording is labelled playback recording, not original media. It runs at
   playback speed. The Mac implementation must be tested; Social Scout's Linux
   ffmpeg recorder is not a portable Mac recording implementation.

Use one maintained acquisition interface and one verified bundle format across
both methods. Do not add a cloud computer, proxy change, external transcript
subscription, new model provider, or new usage-billed OpenAI dependency.

Window-observation ruling, 8 November 2026: the actual CUA app inventory does not
expose comparable window IDs, so the initial CUA/SCK parity assumption is
invalidated. The architect accepted a narrow read-only ScreenCaptureKit query
for the approved Chrome app and visible source, refusing zero/multiple matches.
Native CUA freshly corroborates the requested URL, full page title and opening
picture in that unique window. Retain the actual SCK ID, app/process, full SCK
window title and geometry separately from the page title. The helper and recorder
independently recheck the same target immediately before capture; changed or
ambiguous targets stop. This changes the observation method within the approved
scope, without wider window access, permission grants or sound/coverage claims.
Actual system-audio and start/end acceptance remain required.

## Initial limits

Transport amendment confirmed on 8 November 2026: Keith answered the precise
Mini transport question with "Approve the narrow exception" in the source chat,
relayed by the architect to this builder. After actual route and TLS checks,
the existing Mini-only `198.18.0.0/15` allowance may cover the fixed supported
YouTube source hosts and `googlevideo.com` media hosts. Normal certificate and
hostname verification remain required. Every other private range stays blocked.
No proxy, DNS, router, fake-IP range or unrelated domain change is authorized.
The amendment and exact updated source hashes require independent review before
commit or activation. The existing reviewed snapshot and findings are preserved.

- Facebook maximum source duration: 1,200 seconds (20 minutes), explicitly
  requested by Keith after the proposed ten-minute limit.
- Facebook cumulative run-file budget: retain the current 256 MiB cap initially.
  The twenty-minute duration does not imply every bitrate fits. A source that
  cannot fit returns a clear size-limit failure, never a partial-success digest.
- YouTube maximum source duration: 3,600 seconds (60 minutes); cumulative
  source/derived run-file budget: 2 GiB, as proposed to Keith in the limits question.
  Treat the final user reply as the Facebook-duration correction to that proposal.
- Require at least twice the active byte budget free on Mini-local storage and
  its verified NAS destination before acquisition. Account for all run files,
  including failed partials, copies, audio and frame outputs. Recheck before writes.
- One direct acquisition and one recording attempt maximum in a request. An
  uncertain dispatch is collected, not resent. Provider failures do not trigger
  repeat downloads. No automatic limit expansion or automatic cleanup.
- The existing Facebook helper retains its ten-minute watchdog until the
  reviewed replacement is activated. For the replacement, acquisition deadline
  is 45 minutes for Facebook and 90 minutes for YouTube, covering preflight,
  direct attempt, recording and publication. Native source preflight is at most
  four minutes; direct extraction is at most ten minutes. Recording itself is
  bounded by verified source duration plus two minutes. All phases also obey
  the remaining acquisition deadline. Completion collection may add at most
  five minutes. No expired or late-delivered dispatch starts work.
- Processing stays separately bounded per command and audio chunk. Reuse the
  existing accepted Groq chunk/retry limits; do not increase retries or submit
  the same successful chunks again just because publication failed.

The duration and watchdog changes are named replacements for the older Reel
limits only after reviewed installation. Preserve old bounds on old commands.

## Evidence and recovery

Each run has a new platform-qualified ID, immutable source metadata, media,
caption provenance where available, timing/coverage information, file hashes,
random arrival marker, and a source manifest digest. Derived results bind the
source manifest and processor version. Do not persist signed delivery URLs,
browser cookies or tokens in the bundle.

Retain the existing exclusive Mini worker lock. Serialize capture through the
named Mini task. Requester processing uses host-local locks and separate,
content-addressed preparation directories so Home and SJM cannot overwrite one
another. Reuse unchanged, verified artifacts; source changes require fresh
acquisition. Do not invent distributed locks, always-on queues or new daemons.

NAS writes use unique partial paths, hash readback and atomic publication.
Interrupted or mismatched writes preserve originals and report unverified arrival.
Receipts never certify unattempted or incomplete work. Recovery resumes completed
stages. No automatic deletion, force overwrite, stale-lock clearing or credential
transfer is authorized.

For recordings, verify opening/ending frames and seams against the source
timeline, real audio content and beginning/ending speech, ads, loops and stalls.
An audio stream or volume reading alone is insufficient. Silence is not assumed
from an empty ASR result. Retain explicit audio gaps and inspect available visual
subtitles. Distinguish audio duration from video duration and recording padding.

Read the whole transcript and all prepared contact sheets, plus source frames
needed for demonstrations, uncertainty and crop checks. State the actual sampling
coverage, not every-frame viewing. Attribute separate media-model analysis to
that model. Extraction prompts stay neutral; fact-checking follows separately.

Saving an ordinary knowledge note remains a separate designated-writer action.
Media storage and an answer do not establish vault integration or cross-Mac recall.

## Standing request scope and deployment

An ordinary Facebook/YouTube video link submitted for analysis authorizes native
Mini dispatch, capture/recording, authorized existing speech processing and reading
the retained evidence within these limits after activation and acceptance. It
does not authorize account changes, publishing, bulk subscriptions, background
scraping, unrelated browsing, permission widening or a new paid fallback.

Keith authorized development and deployment. Ship reviewed Git commits, preserve
dirty host adaptations, and deliberately activate Home, SJM and the Mini package.
Every commit requires the independent reviewer family specified in AGENTS.md.
An unavailable reviewer, permission prompt, missing per-host credential or failed
live acceptance blocks the affected activation. Continue independent work and
report precise remaining state. Never call prepared code deployed.

## Acceptance examples

1. Original YouTube source `https://www.youtube.com/watch?v=bc7QaDmLmoA`:
   actual media, identity/duration, timed speech coverage and visual demonstration
   checks, without third-party text masquerading as video review.
2. Facebook share/r source and known Chinese-subtitled Reel: native canonical
   identity, capture, independent NAS arrival, subtitle/ASR comparison and ending.
3. At least one Facebook source longer than ten minutes, up to twenty minutes:
   complete acquisition and processing within the declared byte/time limits.
4. Source without accessible captions: full captured audio reaches existing
   Groq processing, with timed uncertainty and gaps retained.
5. Force direct acquisition failure for a playable known source: recording
   delivers both picture and actual source sound, with boundary checks.
6. From SJM, acquire/verify/process a requested source through its own NAS
   connection without using Home as a relay or processor. Read matching retained
   bytes from Home separately; verify relevant vault binding/recall per ship rules.
7. Reuse a verified run in a later active request without redundant transcription.
8. Expired login, unreachable Mini, unmounted/wrong NAS, exhausted budget,
   partial transfer and mismatched hash: precise bounded failure, no success
   receipt, no repeated uncertain dispatch and no lost evidence.

Fixtures can prove validation and recovery mechanics. They cannot replace the
real capture, audio permission, source review and host independence examples.
Record unavailable acceptance sources as untested, not passed. Retain working
Facebook v1 and its records until the replacement passes its relevant examples.
