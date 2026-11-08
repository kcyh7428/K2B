# Unified video evidence workflow

Deployment and acceptance record: `plans/2026-11-08_video-extraction-delivery.md`.
Design: `plans/2026-11-08_video-extraction-design.md`.

## Connection and source

Use only Mini task `01a17718-be0a-7121-bba3-7bc5e8c20810`, host
`remote-control:env_e_6add8d407cac83279a777291f2b46b8e`. Read its actual state
before dispatch. An uncertain send is collected, never resent. At most two
bounded connection read rechecks, separated by 10 to 20 seconds. No challenges,
login or permission prompts may be answered on Keith's behalf.

Native desktop Chrome must establish submitted and canonical URL, publisher,
title, duration, actual advancing playback and the visible source audio control.
Native CUA cannot hear source audio. Actual source speech must be established
from retained capture evidence afterward, not an audio-playing indicator. Verify the owning
Mini user and real capture mount. Facebook share/r or share/v links require
native canonical Reel identity before acquisition. Do not infer access from a
downloader, network reachability or dated evidence. No Home/SJM signed-in browser
is selected by this route.

The Mini task writes `~/.local/state/k2b/video-v2/native-source.json`, mode 0600,
with `submitted_url` (normalized input), `canonical_url`, `host` (`Macmini`),
`user` (`fastshower`), `control` (`native_chrome`), `title`, `publisher`,
`duration`, observed `playback_start` and `playback_end`, `verified_epoch` and
the original `deadline_epoch`. These are evidence of actual native observations,
not fields to invent. Do not persist browser storage or signed delivery URLs.
The requesting coordinator creates one UUID `request_id` at first preflight for
that source request. Keep it unchanged for direct capture, recording fallback,
native-evidence refresh and publication recovery. The first stored receipt binds
that ID to the canonical source and original deadline; changes are refused.
A new ID requires a new user-requested source request or a separately named
development acceptance case, never an automatic retry of a failed attempt.
Recording additionally requires observed `audio_state` equal to
`unmuted_control_observed`, `source_reset` equal to `paused_at_zero` and
`ad_state` equal to `source_without_ad`. These do not prove the later captured
sound or ending; the complete retained recording still needs content review.
Reject the unsupported older hearing claim instead of silently converting it.
After capture, retained audio must yield actual source speech. Corroborate the
first and last speech with the visible source/subtitles where available, retain
unclear ranges and gaps, and name ASR or the actual analysis tool as the basis.
Stream existence or volume alone is insufficient for sound acceptance.
Recording also requires `window`, a safe ScreenCaptureKit candidate record with
exactly `id`, `process_id`, `app`, `title`, `x`, `y`, `width`, `height`. The
read-only query is restricted to the approved Chrome app and requested visible
source title; zero or multiple candidates stop. Native CUA must freshly
corroborate that unique window's requested URL, full page title and visible
opening picture, then set `window_corroborated` and `opening_picture_verified`
true. Keep the full page title separately from the actual SCK window title,
which can contain display ellipses. Do not invent an ID or claim CUA/SCK ID
parity: the tested CUA inventory does not expose comparable window metadata.
The helper rechecks the actual SCK ID/process/title/geometry before recording,
and the recorder independently repeats that check immediately before capture.
The read-only probe initializes the CLI's WindowServer connection and constructs
the same source filter used by recording, without creating a capture stream.
A changed or ambiguous target stops. Output preserves the window aspect within
1280 by 720 and retains original/output geometry. This observation-method ruling
was supplied by the architect within the approved scope on 8 November 2026;
actual system sound and source-boundary acceptance are still required.

Set the absolute acquisition deadline at initial preflight: Facebook 45 minutes,
YouTube 90 minutes. Native preflight is at most four minutes, direct acquisition
ten minutes total, recording verified duration plus two minutes. Check current
UTC on receipt, before direct acquisition, and before recording. No late work.
Collection may add five minutes, with each wait at most forty seconds.
One direct acquisition and one recording attempt per request. Both commands
retain attempt receipts before work. Keep the original deadline when refreshing
native evidence; do not reset it to authorize another attempt.

## Host and storage checks

Exact live capture bindings at `/Volumes/k2b-captures`:

- Mini: `//k2b-capture-mini@192.168.50.67/k2b-captures`.
- Home: `//cheung@192.168.50.67/k2b-captures`.
- SJM: `//cheung@100.75.5.21/k2b-captures`.

A directory is insufficient. Reconnect only the exact existing share through
Finder's supported Connect to Server on the owning host. A credential or new
permission prompt is an exact operator handoff. SJM cannot use Home as a relay
for this route's acceptance. Verify a random Mini marker/hash through the
requesting Mac's own mount. Require twice the active run budget free locally
and on the verified NAS before capture; recheck cumulative source, derived and
failed partial bytes while working and before publication. Preserve partials.
These are per-operation active local-plus-NAS caps, explicitly confirmed by Keith
on 8 November. Report retained archives separately through inventory; another
host's private retained copies are not measured by this operation's ceiling.
Mount failure stops the operation without a shadow directory or fallback.

Keith approved the narrow Mini transport exception on 8 November 2026 after the
source/TLS question: existing `198.18.0.0/15` addresses are allowed for the fixed
supported YouTube source hosts and `googlevideo.com` media hosts on Mini only.
Source `www.youtube.com` and actual native Network hosts
`rr1---sn-npoldnes.googlevideo.com` and `rr3---sn-oji3bc-5m.googlevideo.com`
passed normal certificate/hostname-verified TLS on that route. No signed URLs
were retained. Home/SJM retain public-only transport checks, all other private
ranges stay blocked, and no proxy, DNS, router or unrelated domain change is
authorized. The transport delta requires independent review before installation.

## Acquisition and recording

Only after acceptance, routine commands use the installed fixed launcher:

```text
~/.local/libexec/k2b-video-v2 version
~/.local/libexec/k2b-video-v2 capture SOURCE_URL
~/.local/libexec/k2b-video-v2 record SOURCE_URL
```

Quote URLs with standard shell quoting. Never interpolate URL text as code.
The versioned installer pins package files and official yt-dlp 2026.10.26 with
bundled EJS plus the Mini's actual Deno bytes. Runtime updates are explicit,
reviewed installs. No downloaded token plugins or automatic version update.
Release identity includes the role and host pins (Deno, downloader, selected
profile and compiler identity), so changing a pin creates a new retained candidate
instead of overwriting an old release. Runtime verifies the configuration binding.
Anonymous direct acquisition is attempted first. Selected local profile use
requires a native `chrome://version` observation of the active directory basename,
recorded by the Mini task in its fixed private `browser-profile.json` receipt
(`control: native_chrome`, `basename: Default` or the observed Profile number).
The installer binds that one profile; no broad profile search or cookie export
is permitted. Session-assisted extraction must be live-tested before acceptance.

If direct extraction fails for a source that plays, refresh native evidence
within the original deadline and use only the separately accepted recorder.
For known short direct failures, start with that recorder after acceptance.
An absent download button is not a direct-failure test. The recorder captures
one uniquely identified visible Chrome window and its system sound, never a
microphone. Native CUA resets the source, excludes ads and unrelated sound,
uses this request-bound handshake and observes the ending. Recording takes
playback time. A stream or volume reading never proves source speech.

The component publishes `recording-started.json` in the request receipt's fixed
record-run directory only from ScreenCaptureKit's successful recording-output
start callback. Read the receipt's `by_method.record` value rather than choosing
another directory. Verify request ID, canonical URL, original deadline, source
duration, installed package and actual recorder hash, and the unique window's
actual SCK ID/process/title/geometry. A UI indicator or a returned start command
is insufficient. No callback within ten seconds blocks and preserves partials.

Keep the visibly verified source opening in native evidence before recording.
Once the marker exists, freshly corroborate the same exact source URL/title,
unique window, paused-zero control, no-ad state and visible unmuted control.
A visible source change or ambiguous identity stops before Play. A black native
video screenshot during simultaneous capture is explicitly ambiguous picture
evidence, not a verified opening or automatically a changed source identity.
The finished recording must establish the actual first-episode opening.

Start playback and observe an actually advancing positive source clock within
30 seconds of `ready_epoch`. In the same CUA call that obtains that advancing
observation, take `observation_call_started_epoch = Date.now()/1000` immediately
before the native observation and `observed_epoch = Date.now()/1000` immediately
afterward. Return both with the actual observed clock text. Persist those exact
returned timestamps later, retaining the call interval as uncertainty; never
substitute the later receipt-write time or infer an exact source offset.

Write fixed run-local `native-playback-started.json` with `version: 2`,
`request_id`, `canonical_url`, full source `title`, `ready_marker_sha256`,
`control: native_chrome`, exact `window`, `source_reset: paused_at_zero`,
`ad_state: source_without_ad`, `audio_state: unmuted_control_observed`,
`window_corroborated: true`, `source_identity_corroborated: true`,
`picture_observation` set honestly to `source_picture_visible` or
`ambiguous_black_during_capture`, `observation_clock_basis:
native_call_completion_epoch`, both returned native observation timestamps,
actual positive `playback_clock`, and actual later `receipt_written_epoch`.
The readiness fields describe the fresh check before Play, while the clock and
call timestamps describe the advancing observation after Play. Do not include
`opening_picture_verified` in this versioned post-ready record; the pre-record
opening remains separately required and the actual recorded opening remains
mandatory. Old failed receipts are never converted or retrospectively accepted.
Missing, changed or late identity/start evidence blocks publication, retaining
output for diagnosis. Native CUA never claims hearing.

The component reserves 60 seconds beyond verified source duration, with an
absolute source-plus-120-second command ceiling, original deadline and byte cap.
This allowance is not proof of complete capture. Native control observes the first source clock reaching its end or wrapping,
then pauses playback to prevent unrelated autoplay. It need not obtain the exact
ending image through the ambiguous concurrent screenshot surface; the actual
recorded ending is mandatory. Retain the
observed ending/pause, raw leading/trailing padding and any labelled loops in the
native task record. Acceptance requires actual opening/ending frames and first
and last source speech from the first complete episode, with source/subtitle
corroboration and explicit uncertainty or gaps.

Candidate recorder permission and real picture/speech/boundary checks precede
activation. A missing recorder acceptance receipt is a hard gate. A blocked recorder stops
without a manual tool substitution. Do not label playback recording as original
downloaded media. Retain recording padding and explicit source-time gaps.

## Independent preparation and review

Mini returns `run_id`, `manifest_sha256`, local and NAS paths in its completion
message, independent of NAS retrieval. The requester passes that digest:

```text
~/.local/libexec/k2b-video-v2 process RUN_ID SOURCE_MANIFEST_SHA256
~/.local/libexec/k2b-video-v2 status RUN_ID SOURCE_MANIFEST_SHA256
~/.local/libexec/k2b-video-v2 inventory
```

The source manifest, every payload and random marker must match. Preparation
is isolated by host, processor identity and source digest. Completed chunk
responses and visual checkpoints are reused; publication failure retries only
publication. A provider failure is not a reason to download again. Missing local
Groq credentials block that host and are never copied from another host.
The requester independently probes retained video and speech durations and checks
them against the declared source duration and allowed recording padding. Local
preparation plus NAS copies remain jointly counted throughout publication.
Container/video duration and audio duration are separate observations. The entire
audio track is chunked using its measured duration, mapped through its source
start offset, and compared with the original media audio-stream duration. Missing
audio before/after that track is an explicit media range, never assumed silence.
Prepared claims retain these measurements; a short exported audio prefix cannot
stand in for a longer original audio track. Recorder stdout JSON is kept separate
from benign framework stderr, while failure diagnostics retain precise errors.
Within the original request deadline, a repeated capture/record command can only
publish a hash-verified committed source stage. An incomplete attempt stays
blocked, rather than restarting acquisition. Recording timestamps include padding
until actual source-boundary review supplies the source-time interpretation.

Read the entire timed transcript, all valid cells in every contact sheet and
the final frame. Reconcile overlapping audio chunks. Empty ASR means unresolved
audio, not proven silence. Inspect native subtitles for uncertain names, quotes,
the opening and ending. Inspect full uncropped source frames at beginning,
middle and end when crop is provisional. Demonstrations need denser source
frames around relevant actions. State actual sampling coverage, never every-frame
viewing. Boundary/padding/ad/stall/loop checks are actual source comparisons,
not inferred from the file duration. Any media-model report is attributed to
that model; extraction prompts stay neutral and fact-checking follows separately.

`prepared_for_assistant_review` means prepared evidence, not completed content
review. Retain explicit gaps. A digest does not establish vault integration.
The installer does not create broad allow rules or approve a policy prompt.
Preserve Facebook v1 until relevant replacement acceptance. Releases and failed
partials remain until Keith authorizes exact-path cleanup. No stale-lock clearing,
force overwrite, background retries, daemon or new capture schedule is installed.


## Explicit SJM release deferral

Keith deferred SJM transcription repair and activation on 8 November 2026 and
selected Home processing until a later fix. Preserve SJM's installed candidate,
failed chunk and dated HTTP 403 access diagnosis; do not change its provider,
credentials, network or account. Home work remains Home evidence, never proof
that SJM processing passed. SJM rollout is deferred, not completed.

For this release Home/Mini acceptance still requires actual examples 1, 2, 3,
4, 5, 7 and 8 from the design. The activation receipt's `live_examples` lists
only those passed checks and `deferred_examples` separately contains exactly
`[{"example":6,"authority":"keith_2026-11-08","scope":"sjm_processing_and_activation"}]`.
This narrow installer exception is allowed only for Home/Mini, never SJM, and
cannot waive YouTube/recording, source boundaries, review or package binding.
Do not manufacture an example 6 pass or silently renew the original YouTube
request when it lacks the source-plus-120-second reserve before its deadline.
