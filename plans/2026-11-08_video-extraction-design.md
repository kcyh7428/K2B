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

Audio-observation ruling, 8 November 2026: actual native CUA supplies pictures,
controls and indicators, without hearing source audio. The architect replaced
the invalid native-hearing premise with `unmuted_control_observed` as preflight
evidence. It binds only the actual source control, alongside fresh identity,
paused-at-zero, no-ad and exact window readiness. Unsupported older hearing
claims are rejected rather than converted. Actual sound acceptance follows
capture: retained audio must yield source speech, with first/last speech and
visible source or subtitles corroborated where available, explicit unclear
ranges/gaps, and attribution to ASR or the actual analysis tool. A volume value
or audio stream alone is insufficient. Microphone-off, one recording attempt,
all limits and actual source-boundary acceptance remain unchanged.

Recorder/controller checkpoint, 8 November 2026: the first repaired 55-second
component test retained real source speech but about 14.3 seconds of leading
paused picture exhausted the assumed ten-second padding. The verified source
ending was not established, so no recorder acceptance receipt was written.
The architect ruled that recording output must publish a request-bound ready
marker only from its actual successful-start callback, including recorder
identity, actual window/process/geometry and timing. Native CUA waits for that
marker, freshly confirms the same paused source and starts playback, retaining
an actual advancing-playback observation within 30 seconds of ready. Reserve
60 seconds of padding while keeping the source-plus-120-second ceiling,
original request deadline, byte cap and one recording attempt. Missing or late
ready/start evidence preserves partials and blocks acceptance. Native control
observes the ending and pauses the source to avoid unrelated autoplay. Retain
padding/loops and uncertainty instead of deriving an exact source offset from
a delayed tool call. Verify the first complete source episode's opening,
first/last speech and actual ending frames before accepting the recorder.

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
- Facebook active-operation file budget: retain the current 256 MiB cap initially,
  counting that operation's local files plus its NAS run files.
  The twenty-minute duration does not imply every bitrate fits. A source that
  cannot fit returns a clear size-limit failure, never a partial-success digest.
- YouTube maximum source duration: 3,600 seconds (60 minutes); active-operation
  local-plus-NAS source/derived run-file budget: 2 GiB, as proposed to Keith in the limits question.
  Treat the final user reply as the Facebook-duration correction to that proposal.
- Require at least twice the active byte budget free on Mini-local storage and
  its verified NAS destination before acquisition. Account for all run files,
  including failed partials, copies, audio and frame outputs. Recheck before writes.
  Keith explicitly confirmed this per-operation scope on 8 November 2026 after
  the cross-host boundary question. Retained archive size is reported separately;
  the cap is not a combined ceiling across every host's private retained copies.
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


Observation-protocol checkpoint, 8 November 2026: the new handshake case stayed
failed after a black concurrent native screenshot and a 40.18-second receipt-write
time. Actual retained frames at zero/five seconds showed the expected opening,
so neither the black screenshot nor later write time reliably describes what the
recorder captured. The architect ruled: preserve that failure without retrofit;
keep visible opening proof before capture; freshly recheck exact URL/title,
window, paused-zero/no-ad controls after ready; treat a black concurrent screenshot
as explicitly ambiguous picture evidence while identity must remain unambiguous.
Actual observation timing is captured inside the same native CUA call, with its
interval uncertainty retained separately from later persistence. Finished output
must establish the full first episode, opening, first/last speech and ending.
Native control must observe the end clock or wrap and pause. No timer widening,
permission/window expansion or automatic retry. Independently review this exact
protocol, then one fresh named short test; if it fails again, stop recording work
and deliver the accepted direct Facebook portion with the precise remaining
blocker for a new design decision. Original YouTube deadline stays immutable.


SJM release deferral confirmed by Keith on 8 November 2026 in the active user
conversation, relayed by the architect: leave SJM transcription repair for now
and use Home until a later fix. SJM processing and activation are deliberately
deferred, no longer required for this release's Home acceptance. Preserve its
candidate, retained failed chunk and dated verified-TLS HTTP 403 diagnosis.
No SJM provider, credential or network change is authorized. Home processing is
accepted as Home work under this explicit selection, never as completed SJM work.

Home/Mini delivery still requires the relevant actual examples 1, 2, 3, 4, 5, 7
and 8. Their activation receipts must list only genuinely passed examples and
separately record example 6 as Keith's dated SJM-processing/activation deferral.
The installer will recognize only this narrow named release exception for Home
or Mini; it cannot use it to activate SJM or waive recording/YouTube, source
boundaries, independent review, package binding or any other live check.


VFR and provider-uncertainty checkpoint, 8 November 2026: the final version2
short recording passed its actual callback/native-start protocol, full decode,
complete speech export and source publication. Its 111.464958-second container
has a 73.21-second variable-frame-rate video track, with a held opening and a
separate trailing audio span. The inherited per-sheet input truncation falsely
ended a sampling interval before the next actual VFR frame. The v2 correction
samples the full stream once into the same one-per-second 4x4 sheets, then reuses
v1 progress/hash/coverage verification. Preserve incomplete passes as partials.
Held decoded pictures between real frames may repeat in samples; do not call
those distinct source frames. Stop at the actual video extent, preserve the
actual last decoded PTS, and label the trailing audio as unpictured. Never clone
that tail into newly observed pictures. The source ending can be proved inside
the real track; the final paused-loop picture is not the first-episode ending.
No new recording, timer, access scope or broader architecture is introduced.

The architect also approved explicit provider uncertainty within the existing
gaps contract: only a structurally valid, finite, ordered interval outside its
actual submitted chunk becomes a timestamp anomaly. Preserve the retained raw
response bytes and exact raw segment index/text/times; exclude the entire
unsupported segment from normalized source-speech claims, retaining its actual
audio intersection as unverified. Never clip/redistribute words, infer silence
or automatically retry. Missing fields, malformed structures, nonfinite and
reversed intervals still hard-fail. Hash-bind anomaly and coverage records and
make preparation/status visibly unverified. A first episode can be accepted
only with actual opening, supported first/last speech, source pictures/subtitles
and ending, and no unresolved anomaly intersecting required source content.
An anomaly confined to retained padding or a later loop does not invalidate
separately proved source content. ASR timestamps spanning the paused lead are
not precise speech-onset evidence. This ruling was relayed in the active chat
by the architect before dependent implementation.
