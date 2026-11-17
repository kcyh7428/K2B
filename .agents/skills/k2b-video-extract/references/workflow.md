# Content extraction workflow

Authority: the current content-first revision in
`plans/2026-11-08_video-extraction-design.md`, explicitly approved by Keith on
8 November 2026, supplemented by Keith's 17 November native browser-export
approval in `plans/2026-11-17_youtube-native-export.md`. The versioned Home and
Mini packages retain this procedure as hash-bound `workflow.md`. Read the
delivery record for actual installed/active state. Source edits do not install
runtime packages.

## Supported evidence

YouTube uses native captions and relevant native visual checks on the Mini.
Facebook uses direct Mini media acquisition and Home speech/visual processing.
Neither site automatically falls back to recording or another provider. SJM
processing and activation remain explicitly deferred; use Home for this release.

If a request requires a complete actual YouTube video/audio file, or native
captions remain absent/inaccessible/incomplete after checking the two supported
native routes below, report unsupported/deferred with
the exact reason. A worker paraphrase is not a verbatim transcript. Blocked
Facebook acquisition also stops. No audio download, transcript service, provider
switch, recording, renewed expired request or truncation is a hidden alternate.

## Mini source and native observations

Use only task `01a17718-be0a-7121-bba3-7bc5e8c20810`, host
`remote-control:env_e_6add8d407cac83279a777291f2b46b8e`. Read actual state
before dispatch. Collect uncertain sends, never resend them. At most two bounded
connection read rechecks, separated by 10 to 20 seconds. No login, CAPTCHA, security
or permission prompt may be answered on Keith's behalf. Native UI stays in that
task, never SSH UI scripting. Home is not a selected signed-in browser. Cookies,
credentials and signed delivery URLs never enter bundles.

Native Chrome establishes canonical URL, publisher/title and actual duration.
Resolve Facebook share/r or share/v forms from the actual page. Dated source
evidence remains dated, not proof of current playability. Reuse only under a
current request, retaining original observation time, provenance and uncertainty.

Read the actual native Show transcript sequence, including first/last exposed
rows. Retain count, ordered start times, language and the exact auto/manual/unknown
label. If this panel is inaccessible, use the native browser export below before
reporting caption acquisition blocked. Unknown caption end times
stay unknown; speech completeness is unverified. Preserve copyright limits and
explicit raw-caption retention status. Do not invent or translate missing rows.

### Alternate native caption route

On the same named Mini, verify that its current in-app browser documentation
actually exposes `tab.content.exportYouTubeTranscript()`. Open the same canonical
HTTPS YouTube `/watch` URL there and call that documented method. This is a
built-in browser export of source captions, not an audio transcription or
third-party transcript service. A method that is unavailable on the current
host is unavailable; do not infer it from Home's tools. An `ad_playing` result
is not a transcript: finish ordinary ad navigation and collect a bounded source
attempt within the original deadline. Never renew an expired request.

Read the returned private UTF-8 file on the Mini. Check its video URL/title
against native source identity, then inspect every exported timestamped row,
including first and last. Preserve actual language/automatic-caption provenance
or unknown; do not copy the Chrome label onto a different exported track without
evidence. Record SHA-256, byte count, row count, ordered starts, first/last starts,
the API and observation window. Empty output, source mismatch, malformed or
incomplete rows, an inaccessible export, or a security/login prompt stays a
precise blocked result. Source captions can omit speech even when all exported
rows are inspected. Inspect relevant native Chrome visuals as before.

Keep raw exports private on the Mini and publish only metadata and paraphrase.
Count the private file in the existing operation budget. Its digest binds the
worker's inspected bytes; Home's NAS payload checks do not independently rehash
the private raw export. Do not print full copyrighted transcripts in chat.

For this route set `caption_acquisition_method: native_browser_export`,
`caption_sequence_status: all_exported_rows_inspected`, and caption metadata
`control: mini_in_app_browser`, `first_exported_row_inspected: true`,
`last_exported_row_inspected: true`. Add metadata `native_export` with `api`
equal to `tab.content.exportYouTubeTranscript`, matching `source_url`, `sha256`,
`bytes` (1 through 2 MiB), `row_count`, `first_start_seconds` and
`last_start_seconds`. Raw retention is `private_export` while the raw file exists,
or the existing honest statuses if it does not. The top-level and visual control
remain `native_chrome` for source identity and visual observations only. Preserve
unknown caption ends, unverified speech completeness and sampled visuals.
New export caption and fresh visual observations must start at or after the
original request creation, finish within its original deadline and be no later
than actual validation time. A follow-up reuses the original verified bundle;
it cannot rebind old export observations to a new request. Earlier visual notes
remain dated in the separate prior-notes payload. Legacy panel timing is unchanged.

The original panel contract remains valid with default acquisition method
`native_panel`, caption control `native_chrome` and
`all_exposed_rows_inspected`. Never forge panel fields to satisfy an older
installed validator. Export bundles require the updated validator on Mini and
Home; until reviewed runtime delivery, retain private evidence and report
publication as pending, separately from successful caption acquisition.

The model selects scenes relevant to the question from actual caption timestamps.
Inspect them through native controls and screenshots. Retain actual visible
clocks or null with uncertainty, separately from requested clocks. Take call-start
and observation-completion epochs within that same CUA observation call. Pause
between bounded checks and keep autoplay off. Never claim hearing. A sampled
picture proves only what was actually visible, not hidden functionality, code
ownership or full visual coverage.

Write a source-linked timestamped paraphrase and claims, attributing assertions
and preserving uncertain names/figures. The model interprets content and selects
tools/moments; code enforces sources, types, hashes, roles and limits. Storage
alone does not put evidence in Home's model context.

## Caption draft and publication

Create one publication UUID and immutable deadline. Under Mini
`~/.local/state/k2b/video-v2/content-drafts/UUID/`, retain:

- `caption-metadata.json`: original native caption metadata.
- `analysis.md`: timestamped paraphrase, not raw captions.
- `claims.json`: source identity and timestamped claim groups/limits.
- `prior-visual-notes.json`: earlier observations with original timing limits.
- `visual-observations.json`: actual relevant native scene observations.
- `evidence.json`: typed source/request fields and hashes for those five files.

The paragraph below describes the original panel branch. The alternate native
export branch uses the distinct method, caption-control, sequence and retention
fields specified above. Source identity and visual control remain shared.

Structured payloads name the same canonical source URL. Keep originals unchanged;
do not rewrite old observations as fresh capture. Evidence version1 has
evidence_type caption_visual, UUID request_id, submitted/canonical URLs,
host Macmini, user fastshower, control native_chrome, title/publisher,
source_duration, request_created_epoch/deadline_epoch, dated
caption_observation_window (start_epoch/end_epoch/basis),
caption_sequence_status all_exposed_rows_inspected, caption_end_times not_exposed,
speech_completeness unverified, raw_caption_retention_status not_retained or
excerpts_only plus reason, caption_provenance_kind auto_generated/manual/unknown,
analysis_kind timestamped_paraphrase, complete_media_available false,
full_visual_review false, and exact five-file SHA-256 map under files.

Caption metadata has source_url/title/publisher/source_duration_seconds,
control native_chrome, caption_language/provenance_label_exact,
observed_caption_count, ordered caption_start_seconds_in_sequence and
first_caption_reached/last_caption_reached. Claims have source identity,
duration_seconds and claim entries with actual `m:ss-m:ss` ranges, claim and limits.
An explicit `whole source` interpretation is source-wide, not full visual coverage.
Prior notes retain full_visual_viewing false. Fresh visual notes have source
identity/duration, native control, full_visual_viewing false and observations
with requested_second, clock_seconds or null, observation_call_started_epoch,
observed_epoch, description and uncertainty. Notes are not retained video frames.

After independently obtaining evidence.json SHA-256, Mini runs:

```text
~/.local/libexec/k2b-video-v2 publish-content SOURCE_URL UUID EVIDENCE_SHA256
```

This stages/publishes validated evidence only. It never browses, downloads,
probes audio, transcribes or records. It pins one request/deadline and content
publication run. Retry publishes only the same committed digest; incomplete
staging remains preserved and blocked. Result content_evidence_published does
not mean captured media or accepted source interpretation.

## Facebook direct media

Use fixed native-source.json with submitted/canonical URL, title/publisher,
duration, actual advancing playback, Mini/native control, verified_epoch,
request_id and original deadline. Selected local session use requires the Mini's
native chrome://version profile observation. No browser storage is exported.
The pinned downloader/Deno retain normal TLS and the approved narrow Mini route.

```text
~/.local/libexec/k2b-video-v2 capture FACEBOOK_SOURCE_URL
```

One direct attempt per request. Collect failures/uncertain attempts, never restart
them automatically. Existing v1 stays preserved. Retain media, complete actual
audio chunking, raw ASR, gaps, visual samples/final PTS and hashes. Empty ASR is
not silence. Finite out-of-chunk provider segments remain raw and wholly excluded
from normalized words, with real intersecting audio unverified. Malformed,
nonfinite or reversed data still fails. No automatic provider retry. Compare
supported first/last speech with source/subtitles and actual boundaries. Report
unclear ranges, sampling limits and imprecise broad ASR onset honestly.

## Limits, NAS and Home

Facebook: source at most 1200 seconds, cap 256 MiB. YouTube: source at most 3600 seconds,
cap 2 GiB. Duration and size admission are separate. Caps count active-host-local
plus NAS operation files; archive totals are separate, without a global all-host
private-copy ceiling. Require twice the cap free. Request deadline is 45 minutes
for Facebook, 90 minutes for YouTube, pinned once and enforced during publication.
No automatic deletion, limit increase, stale-lock clearing, daemon or scheduler.

Verify the live SMB mount table, account/share and real path before NAS work:
Mini `//k2b-capture-mini@192.168.50.67/k2b-captures`, Home
`//cheung@192.168.50.67/k2b-captures`, at `/Volumes/k2b-captures`.
A directory alone is insufficient. Wrong/unavailable binding stops without a
shadow write. Reconnect/login prompts require the owning operator. Use unique
partials, hash readback and atomic publication; preserve failed partials. Return
run ID and source digest independently of NAS. Home checks every payload and
random marker through its own mount:

```text
~/.local/libexec/k2b-video-v2 process RUN_ID SOURCE_MANIFEST_SHA256
~/.local/libexec/k2b-video-v2 status RUN_ID SOURCE_MANIFEST_SHA256
~/.local/libexec/k2b-video-v2 inventory
```

The Home caption reader validates the immutable NAS bundle and points to its
actual analysis, claims, metadata and notes. It creates no Home-derived caption
copy, transcript or prepared-publication transaction. No media completeness,
ffprobe, STT or recorder acceptance is involved. Home must read the source files
before answering. Reader status retains the source digest/version, counts,
starts, raw-retention/end-time limits, uncertainties, visual notes and absent
complete media. Verified for reading does not mean completed interpretation.

Facebook keeps the accepted speech/visual algorithm and source/boundary checks.
Caches are processor/source isolated. Reuse verified successful artifacts; do
not resubmit successful ASR because publication failed. Missing owning credentials
block the host and are never copied. A follow-up reads retained timestamps and
analysis first, reopening a native moment only when coverage is insufficient.
Ordinary note saves remain a separate k2b-vault-writer action, not an implication
of content preparation or NAS storage.

## Experimental and deferred state

Default record/recover-recording refuse. Custom recorder, R13 and failed YouTube
media remain experimental/unactivated. No repair, failed-media use or further
recording is authorized. SJM stays deliberately deferred, not accepted; do not
contact/update it. Activation requires current typed acceptance, independent
approval and stable named Home/Mini checks, including Home vault binding/recall.
`deferred_scopes` separately names sjm_processing_and_activation with Keith's
8 November authority. It never waives a numbered Home check. SJM candidate
installation is deferred too. Repeat affected checks after activation.
