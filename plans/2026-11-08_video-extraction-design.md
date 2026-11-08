# Unified K2B content extraction design

Date: 8 November 2026. Owner: Keith. This revision records Keith's explicit
approval of content extraction as the default, relayed by the architect in the
active source chat after the concrete media-versus-content choice. Existing
develop-and-deploy authority covers reviewed Home/Mini delivery. SJM remains
explicitly deferred. This current revision supersedes the earlier media-first
YouTube and automatic-recording acceptance requirements. Their exact source and
review history remain in Git through 24ece38 and the delivery record.

## Goal and concrete behavior

Keith supplies a Facebook or YouTube URL and asks what ideas it offers. One
maintained k2b-video-extract skill establishes source identity on the named Mini,
obtains the supported content evidence, and lets Home read it to answer with
provenance and coverage limits. YouTube uses the actual native Show transcript
panel plus relevant native visual checks. Facebook uses its existing direct
Mini download and Home speech/visual processing. Neither site automatically
falls back to recording.

For the Jack Roberts YouTube source, the Mini reads the actual caption sequence,
retains its language/automatic label, timestamp facts and a source-linked
paraphrase, then checks selected demonstrations on the actual source. Home
independently verifies the returned NAS bundle digest and reads its analysis,
claims and visual observations. The answer says it is caption-and-visual
evidence, not a complete video/audio extraction or verbatim transcript.
A follow-up first reads retained timestamps and observations, then reopens a
relevant source moment if the retained coverage cannot support the answer.

## Components, decisions and roles

- The named Mini task owns native Chrome browsing and visual observations.
  It selects relevant moments from the user's question and actual captions.
  It interprets source content and produces a timestamped paraphrase, without
  inventing missing captions, hearing, complete speech or every-frame review.
- A fixed installed Python adapter validates allowed hosts, canonical source
  identity, typed evidence fields, file hashes, request deadlines, roles and
  byte limits. Code does not decide the ideas or hard-code answer paths.
- The existing NAS capture share retains hash-bound evidence bundles. The
  Mini independently returns the manifest digest; Home compares every file
  and random arrival marker through its own verified mount. Storage alone
  does not put evidence in the model: Home must actually read the files.
- Home validates and reads the immutable YouTube NAS bundle directly, without
  a derived caption copy or another prepared-publication transaction, ffprobe,
  audio, Groq or recorder acceptance. Its typed reader result exposes the source
  digest, provenance, coverage, actual source paths and reader version.
  For Facebook media it retains the accepted existing
  full-audio chunking, ASR uncertainty and observed visual preparation.
- Code and skills stay local through reviewed Git delivery. Mini receives
  only the versioned worker package, not a K2B checkout or vault. Credentials
  and local browser state remain on the owning host. No scheduler is added.
- SJM is deliberately untouched/deferred under Keith's explicit decision.
  Home output is Home evidence, never proof of completed SJM processing.

## Typed evidence contract

YouTube bundles use evidence_type caption_visual and method
native_captions_and_visual_checks. They contain source/request metadata,
original caption metadata, timestamped analysis/claims, retained earlier visual
notes, fresh relevant native visual observations, a random marker and manifest.
No video/audio file is required or represented as present.

Retain the actual caption label, language, auto/manual/unknown provenance,
caption count and ordered start timestamps, whether first/last exposed rows
were inspected, raw-caption retention status, unknown caption end times and
speech completeness. A worker paraphrase is identified as a paraphrase, not
caption text or a verbatim transcript. Retaining a summary does not prove the
correctness of a source's claims. Home and answer artifacts retain that distinction.

Native visual observations carry actual source clocks or explicit uncertainty,
actual observation timing and a description of what was seen. Prior notes retain
their original provenance and timing limits. Relevant-scene notes are not stored
video frames and are never called full visual viewing. Existing source evidence
is reused with its actual observation interval, not rewritten as fresh capture.

Facebook bundles remain evidence_type media, method direct_media. Source
media, timed speech, visual sampling, gaps and prepared hashes retain their
existing separate meaning. Existing v1 bytes and accepted artifacts remain intact.

The adapter stages a fixed caption draft under a UUID-bound private directory,
validates its independently supplied evidence digest, commits it once to a new
publication run and publishes through the existing NAS channel. A repeated
publication only resumes that exact committed digest; it never browses,
downloads, records or calls a provider. Home reads that retained source bundle
directly and revalidates its immutable digest. Facebook media preparation remains
isolated by processor/source digest and reuses unchanged successful artifacts.

## Limits, failures and exclusions

Facebook source duration is at most 1200 seconds, separate from the 256 MiB
active-operation local-plus-NAS cap. A twenty-minute source may still exceed
the byte cap, in which case stop rather than truncate. YouTube source duration
is at most 3600 seconds and its operation cap is 2 GiB in the same scope.
Retained archives are reported separately, without a global all-host ceiling.
Require twice the operation cap free and the exact owning SMB binding.
Requests retain their original deadlines: Facebook 45 minutes, YouTube 90 minutes.
Native evidence can be reused with explicitly dated observation provenance;
reuse never renews an expired publication request or pretends to be a fresh source.

Missing/incomplete inaccessible native captions, blocked Facebook direct
acquisition, requests requiring a complete actual media file, changed identity,
unsupported hosts, expired requests, wrong/unmounted NAS, exhausted budget or
hash mismatch produce a precise unsupported/deferred result. No hidden
provider, downloader retry, recorder fallback or complete-media claim is allowed.
A login/security/permission prompt stays an exact operator handoff.

The custom recorder, R13 recovery and failed YouTube media remain experimental
and unactivated. Default record/recover actions refuse explicitly. Do not
record again, repair, decode, transcribe or publish that failed media. No app,
service, provider, paid fallback, credential, network, permission or scheduler
change is part of this revision. The previously approved narrow Mini transport
exception and normal TLS checks remain unchanged for supported direct media.

## Observable acceptance

1. Original YouTube source bc7QaDmLmoA: actual caption panel identity,
  177 ordered starts from 0:00 to 22:39, explicit English United States auto-generated
   label, complete inspected exposed sequence, timestamped paraphrase and
   bounded relevant native demonstration checks. Retain actual timing, unknown
   caption ends, absent complete media and limited visual coverage.
2. The independently returned typed bundle digest, every payload and random
   marker match through Home's own capture mount. Home processing/status
   retain the same evidence type, provenance, uncertainty and no-media claim.
3. Accepted Facebook share-form/short and longer-than-ten-minute direct cases
   retain canonical identity, full supported speech/visual/boundary proof and
   the separate twenty-minute/256-MiB limits. No reacquisition is needed merely
   to replace code with identical proven algorithms.
4. Verified reuse preserves successful response bytes and performs no new
   browsing, acquisition or provider work. A follow-up reopens a scene only
   when the existing evidence is insufficient.
5. Missing captions, unsupported complete-media requests, blocked direct media,
   disabled recording, source/hash/mount/budget/deadline failures cannot issue
   a content-complete or accepted-media receipt or silently select another route.
6. Home and Mini role/package/NAS proof and bounded Home vault recall pass
   after activation; SJM deferral is recorded separately from passed checks.

Activation uses stable named capabilities, including Home vault binding/recall,
and a separate named SJM deferral scope. The current numbered Home/Mini acceptance
item is never waived by a historical SJM example number. Both SJM candidate
installation and activation are deferred. Repeat the affected storage/recall and
role/package checks after activation before calling the release accepted.

Every commit requires independent adversarial approval. Keith authorized the
DeepSeek desktop account exception after bounded Kimi attempts returned no usable
verdict; it remains opposite-family review, not a missing-verdict waiver.
Activation is distinct from commit/push and must preserve all unrelated dirty
Home changes, concurrent commits, local adaptations and working Facebook v1.
