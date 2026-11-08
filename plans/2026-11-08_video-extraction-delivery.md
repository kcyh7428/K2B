# Video extraction delivery

Design: `plans/2026-11-08_video-extraction-design.md`.
Implementation: `plans/2026-11-08_video-extraction-implementation.md`.
Builder: OpenAI, isolated `codex/video-extract` from `bd4a25b`.
Authority: Keith's develop and deploy request in the source chat, supplied in the builder handoff on 8 November 2026. Facebook correction: 1,200 source seconds; each active operation's local plus NAS files remain 256 MiB. YouTube: 3,600 source seconds, 2 GiB for the same per-operation scope. Keith explicitly confirmed this storage scope on 8 November; retained archives are reported separately, without an all-host private-copy ceiling.

## Current content-first checkpoint

The latest approved/pushed code checkpoint is24ece38. The current content-first
candidate is uncommitted and not installed/activated. R14 returned needs_attention
with four material corrections and two smaller gaps; all are corrected with
failure-first regressions. The corrected suite passes156tests in24.48seconds;
renewed exact review is next. Current acceptance uses stable named capabilities,
separate named SJM deferral and Home vault binding/recall, not historical example
numbers. The architect clarified that Home reads the immutable caption NAS bundle
directly, without another derived-caption publication transaction. Facebook keeps
its accepted media preparation. All sections below retain dated development
history; previous example numbers and media/recording requirements are historical,
superseded by the current design. No failed media is used or recording activated.

## Historical R9 checkpoint

- R9 SJM-deferral reconciliation independently approved in 42 seconds and
  committed/pushed as `b875d66`. The VFR/provider-uncertainty correction is
  prepared with 105 focused tests passing in 20.15 seconds, authority/skill/
  whitespace checks passed; exact independent review is next. No new recording.
- R8 observation correction independently approved by DeepSeek in an uninterrupted
  1 minute 25 seconds, no findings. Commit `db9966c` is pushed to
  `origin/codex/video-extract`. Earlier approved commits remain `e00a1ad`,
  `8f07e30`, `e94043e` and `082f8dd`.
- Current supervised candidates: Home
  `b505ebf9911b342c382081a21e5897fc3967e727b2d86f058a88e90b27a3696b`,
  Mini `a4027401f8b37a511c0f07f55fb29fcbcb3b3ff4c3691be61c9c2a6672155e5e`.
  SJM deliberately retains R7 candidate
  `e9ba8a7dab58b861140f7004c1e70d1bf863b5abb92fd014d1c471bbdd130b28`.
  No active launcher or live checkout update on any host.
- Keith explicitly deferred SJM transcription repair, processing and activation
  for this release and selected Home until a later fix. Its own failed chunk
  and dated HTTP 403 access/egress diagnosis remain retained. This is a user
  deferral, never completed SJM processing. No key/provider/network change.
- Short Chinese Facebook source: fresh direct run
  `facebook-2372450313602638-20261108T063408Z-1ef39758`, source digest
  `dfe4179364567698684dcf7acee5c1b3618f79288013e5a8ea86efc54e6f0a62`,
  actual source 44.837 seconds, 4,337,894 payload bytes. Home real Groq and all
  three sheets, final/uncropped frames and speech passed bounded review; reuse
  preserved actual response hash and nanosecond modification time. The exact
  real share/r/1MFtSe4XWL/ freshly resolved to the same source, publisher, title,
  45-second duration and advancing playback, without duplicate acquisition.
- Long Facebook source: fresh run
  `facebook-2177961246267847-20261108T063703Z-609c655c`, source digest
  `c15829a9a642b9414da34c2db8a80102b91875a7c971f5dd000574f4d453bb85`,
  688.865 source seconds, 86,572,984 payload bytes under unchanged 256 MiB cap.
  Home real Groq, full timed transcript, all 44 sheets, ending and uncropped
  frames were read; seam/crop/ASR-subtitle corrections are explicit. R7 processor
  preparation digest `0e27673b39e231fc84e28f5606bfb4e6209e8e252232dee3c5c0c1a02eed951a`
  reproduced all content/ASR segments and visual bytes. R8 changes recording
  observation validation, not ASR/visual processing algorithms. No SJM success
  is inferred from this Home evidence.
- Prior recorder attempts remain failed: initial CLI filter initialization abort,
  then speech retained without ending proof, then an actual ready marker with a
  black native screenshot and delayed write-time receipt. No failed case was
  retrospectively accepted. The AppKit and actual callback repairs are reviewed.
- The one final newly named version2 short case is in progress. Its actual
  native advancing-clock call completed 27.106353 seconds after ready, inside
  30 seconds, with returned 0:08 over a roughly 0.145-second call interval and
  a separate later persistence time. This is a protocol milestone, not source
  speech/ending acceptance. Actual output validation and Home first-episode
  opening, first/last speech and ending review remain pending.
- Original YouTube source identity and 22:42 native playback were freshly seen;
  its one direct attempt timed out without a manifest. Its unchanged deadline
  is 08:09:38 UTC, and after 07:44:56 it lacks the required source-plus-120-second
  admission reserve. No fallback or refreshed request was silently dispatched.
- Live wrong digest and failed recording/deadline/provider cases produced no
  success; other failure classes have focused fixture coverage. Retained archive
  totals are separate inventory snapshots, not an all-host private-copy ceiling.
- Home/Mini activation-receipt reconciliation for Keith's explicit SJM deferral
  is prepared, not reviewed or committed. It lists genuine passed relevant
  examples and separately defers the old media-first example6 (SJM). Those
  historical numbers are not the current typed acceptance contract.
  Latest focused suite: 92 passed in 15.03 seconds; authority/whitespace passed.

Prepared: yes. Tested: fixtures plus bounded live evidence above. Reviewed,
committed and pushed: through `b875d66`. Current VFR/provider-uncertainty
correction awaits independent review. Activated-home: no. Activated-sjm: deferred, no activation.
Activated-mini: no. Accepted: direct Facebook Home evidence only, not the full
unified release or recording/YouTube route.

## Acceptance matrix

| Example | Current evidence | State |
| --- | --- | --- |
| 1. Original YouTube | Native identity/playback verified; direct timed out; unchanged request no longer has admission reserve | Not accepted; no silent deadline refresh |
| 2. Facebook share/r and Chinese Reel | Fresh Chinese Reel captured and Home reviewed; exact share/r/1MFtSe4XWL/ freshly resolved natively to the same canonical Reel, identity, 45 seconds and advancing playback | Home passed |
| 3. Facebook over ten minutes | Fresh 688.865-second source, complete acquisition and Home preparation within unchanged cap | Home passed; SJM deliberately deferred |
| 4. Missing accessible captions | Both new Facebook sources reported no caption tracks; complete actual audio reached Home Groq | Home passed |
| 5. Recording after direct failure | Version2 protocol, decode/export/source publication passed; Home VFR/anomaly correction prepared | Same-source reprocessing and actual speech/ending review pending |
| 6. SJM independence | Own NAS verification passed, own STT403 retained; Keith explicitly selected Home until later SJM fix | Deferred by Keith, not passed |
| 7. Reuse | Short Home preparation reused with identical response hash and mtime | Home passed |
| 8. Precise failures | Live wrong digest refused; direct deadline and SJM HTTP 403 did not produce success; other classes have fixture coverage | Partial |

## Initial evidence

- Preserved installed v1 source at content-addressed release `e320dc76c3a1370e912c7b3c71ace1f778a3b1032cde2c3c9145dbd61924bbf2`, 714 lines. No installed v1 changes.
- Mini SSH reports `Macmini` / `fastshower`, exact capture mount `//k2b-capture-mini@192.168.50.67/k2b-captures`, yt-dlp 2026.03.17, Deno 2.7.14, Swift 6.1, arm64 macOS 15.
- SJM reports `GLPs-MacBook-Pro.local` / `keithcheung`, only Keith-Work mounted. Capture share missing. Existing YouTube/eod changes and untracked files preserved. Credential file exists, owner Keith, mode 0600; value not read or copied.
- Home capture mount matches its approved source. No new route accepted from mount evidence alone.
- Named Mini task received bounded native-source and existing recording-permission preflight. No permission grant, recording or install authorized by that preflight message.

## Progress

Candidate implementation prepared for review. Shared interfaces: capture and recording produce one source bundle; process consumes an independent source digest; installation pins code and host role. No interface conflict identified.

- First contract test failed because the unified module was absent, then passed with the implementation. Attempt-ledger and role-installer tests likewise failed before those functions existed.
- Focused tests: 32 passed, including actual two-second ffmpeg media preparation, timed chunk/visual output, independent manifest/hash checks, NAS publication and repeat preparation without another provider submission. Provider response in this fixture is synthetic; it is not speech recognition acceptance.
- Both existing YouTube helper shell suites passed, including modified official-asset rejection and provider error/empty-response boundaries. Full logs are private in `/tmp/k2b-video-yt-tests.log`.
- New skill frontmatter validation passed. `scripts/verify-codex-authority.sh` passed. Git whitespace check passed.
- Home Swift compilation failed from a pre-existing compiler/SDK mismatch and duplicate SwiftBridging module. No toolchain settings changed. Mini Swift 6.1 successfully typechecked the recorder source through stdin, without installing or running it. This proves API/compiler compatibility only.
- Native Mini verified original YouTube `bc7QaDmLmoA`, Jack Roberts, title `I Tested Uncensored AI... It Should Probably Be Illegal…`, duration 22:42, source playback 0:00 to 0:02 after a preroll advertisement, Audio playing indicator. No login blocker. Standalone recorder sound and permission are still unverified.
- Native selected Chrome profile directory basename is `Profile 9`, observed through chrome://version. Profile discovery is not needed or authorized.
- Mini freshly verified Facebook `2177961246267847`, publisher Lê Hồng Nhung, duration 11:29 (689 seconds), playback 0:02 to 0:11 muted, Chinese subtitles. This qualifies for the requested longer-source input only; actual acquisition and byte-budget fit remain untested.
- Chinese Reel `2372450313602638` freshly showed 0:08 / 0:45 and publisher 燃情追光者, with changing interview frames and Chinese subtitles. Capture and complete content review remain untested for the replacement.
- SJM's exact capture share was connected through Finder using its existing authorization. Actual mount is now `//cheung@100.75.5.21/k2b-captures` at `/Volumes/k2b-captures`. Home and SJM independently read the prior source manifest `a0fa24e7f43f6277c507bc0bf92bbd45c3ef8915d0292d105758d45dd2cfe942` and random marker `06ecf128575e26bd5f195a690bbedbbc30ca9f121b1d7ada5b9bbe5f62cb5d4d`. No credential transfer or Home relay. This proves current collection binding, not replacement processing acceptance.
- Latest host baselines changed during this task: Home `8efe30c`, SJM `a2c01b4`, both titled Explain system design before K2B implementation. The video branch remains based on `bd4a25b`. Preserve those newer host instructions and all dirty files during deliberate integration.
- Whole-candidate independent Kimi review `2026-11-08T04-51-48Z_25da0f` reached its 360-second hard deadline, no verdict, no fallback. No commit or installation followed.
- A bounded transport diagnostic of the eight-line launcher, `2026-11-08T04-58-01Z_64b28f`, returned an approving Kimi verdict. This establishes transport availability for a small scope only and cannot approve the feature. Retry using explicitly covered smaller review scopes with cross-component focus.
- Two additional failing tests exposed absent recording-source readiness and committed-source recovery gates. Both now pass: muted/unreset/ad playback is rejected, and publication-only recovery requires the retained source digest and unchanged payloads. Focused suite is now 34 tests.
- Core Kimi review `2026-11-08T04-59-30Z_81e8bd` returned needs-attention. Valid findings were repaired with failing regression tests: processing has duration/chunk/sheet-derived overall budget while individual commands stay bounded, caption transport/validation failures are explicit auxiliary gaps, preparation inventory includes nested host-local results, and digest arguments validate before directory creation. Source/status manifests explicitly bind processor and source digests.
- Reviewer recommendations to extend the approved ten-minute direct cap, repeat an uncommitted direct attempt, or add a new recovery/quarantine CLI action were not adopted. They conflict with the approved limits/interface. Conformant h264/AAC media now uses stream copy to reduce work inside the existing deadline. Failed attempts and exact-path cleanup remain explicit operator work, without deletion or stale-lock clearing.
- The inventory regex finding overlooked that configure() already replaces the v1 regex. Nested preparation inventory was a real omission and is fixed. A root symlink was already rejected by child(); an explicit root check was added for clarity.
- Installation/recording Kimi review `2026-11-08T05-01-21Z_e54a8f` timed out at 360 seconds without a verdict. Keith explicitly authorized DeepSeek when Kimi returns no verdict. Existing Home DeepSeek Harness headless configuration reports provider `deepseek-official`, model `deepseek-flash`, independently of OpenAI. No profile, provider, credential or permission changes. The scoped fallback must approve exact content before any commit or installation.
- The headless full review and bounded health request failed with structured `MISSING_CREDENTIAL` for its default official API route. No verdict, profile change or key copy. The already configured desktop uses `deepseek-account`; native UI identifies `DeepSeek-V4.1-Flash`, High reasoning. Keith's exception permits a fresh read-only desktop review instead.
- Expanded actual ffmpeg fixtures now cover two seconds and 260 seconds, including two speech chunks, all visual sheets and replay without repeat provider calls. A low-frame-rate ending failure was observed and repaired by selecting the actual final decoded frame PTS, retaining that timestamp and explicitly choosing the JPEG pixel format. The original v1 snapshot remains unchanged. Final focused suite: 39 passed in 6.60 seconds. Provider responses remain synthetic in fixtures, not live speech evidence.
- Transport amendment: Keith explicitly answered "Approve the narrow exception" on 8 November 2026, as relayed from the source chat. Actual Mini `www.youtube.com` resolved to `198.18.0.21` and passed normal TLS 1.3 certificate/hostname verification. Native Chrome Network Domain column then exposed `rr1---sn-npoldnes.googlevideo.com` (`198.18.4.55`) and `rr3---sn-oji3bc-5m.googlevideo.com` (`198.18.38.219`); both passed normal TLS 1.3 at 05:22:07Z. Their individual stream roles were not established. No signed URLs, cookies or headers retained. The approved fixed-host/domain, Mini-only allowance is implemented with failing-to-passing regression coverage; Home/SJM, suffix boundaries and other private ranges stay blocked. No network settings changed. This delta remains outside the preserved running review snapshot and requires renewed exact-content review.

Research: official [yt-dlp 2026.10.26 release](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26), [EJS requirements](https://github.com/yt-dlp/yt-dlp/wiki/EJS), and [Apple recording API](https://developer.apple.com/documentation/screencapturekit/screcordingoutputconfiguration). The official downloader pin is the audit-verified zip artifact, not an automatic update. Deno remains machine-local and hash-bound at install.

Ruling: keep existing v1 helper and instructions available until replacement acceptance. The new route is not activated merely because a package is prepared.

## Verification and delivery states

### Independent review corrections and current checkpoint

DeepSeek desktop completed the first full review in 15 minutes 19 seconds with
`needs_attention`. It independently verified packet
`4201f3fbd9bb03c4ae863687b4e7a86428f952d159a9546219076a7131fa4d64`
and full staged diff
`d8f47c5072ebbacf397c60f3cb0f72f1bb0643315dde7dae330b3000926645c6`,
read 2,553 packet lines and reviewed all 19 diffs. It resolved the single framing
newline discrepancy itself. The immutable first snapshot and finding index are
retained privately under `~/.local/state/k2b/operations/video-extract-20261108/`;
the full exact JSON remains in the fresh read-only DeepSeek desktop session and
the source Codex CUA transcript. No approving verdict exists yet.

All ten findings now have corrections awaiting renewed exact-content review:

1. A stable source-request UUID binds its first canonical URL and deadline.
   Refreshing evidence cannot reset the attempt ledger or original deadline.
2. Publication-only recovery compares retained and current native source identity,
   title, publisher and duration, plus manifest run/platform/video binding.
3. The requesting Mac independently probes video and full speech duration, checks
   direct-source agreement or bounded recording padding, and retains source duration.
4. Publication continues counting local preparation together with source/NAS copies.
   A regression proves it refuses before copying when their combined budget is full.
5. Release identity includes role and host pins. Pin changes create a new retained
   candidate; runtime verifies configuration identity and cannot silently change profile.
6. The recorder binds actual SCK ID/process/title/geometry, preserves aspect, and
   rechecks the exact source window immediately before capture. See ruling below.
7. Caption discovery covers other language keys and common timed formats, records
   unsupported/bounded omissions, and preserves failed writes only under partials.
8. Safe recorder errors reach stderr/retained diagnostics and the worker returns
   precise short error codes, including recorder screen/system-audio permission.
9. Status compares its receipt with hash-bound prepared claims, including a tamper
   regression using matching altered local and NAS receipts.
10. Inventory scans bounded v2 NAS source directories independently of local runs
    and itemizes failure receipts, including NAS-only failed partials.

Window feasibility: CUA inventory omitted window metadata, invalidating the ID-parity
premise. The architect ruled that a limited SCK query plus fresh native corroboration
is acceptable within the existing scope. Actual candidate ID 1544, Chrome PID 59196,
geometry x487/y36/1301x959 was uniquely observed. Native Chrome corroborated the exact
Jack Roberts URL, full page title/publisher and opening picture (chained Claude and
DeepSeek icons after preroll). SCK window titles contain display ellipses, so they
are bound separately from full page titles. This proves observation feasibility,
not recording, actual source sound or start/end acceptance. No permission requested.

Latest test checkpoint before renewed review: 52 focused tests passed in 8.57 seconds,
including the real-window-title regression. Updated Swift recorder typechecked on
Mini; authority verification and Git whitespace checks passed. Original v1 source hash remains
`e320dc76c3a1370e912c7b3c71ace1f778a3b1032cde2c3c9145dbd61924bbf2`.

R2 completed in 7 minutes 28 seconds after the six-minute conclusion request was
delivered/steered. Verdict remained `needs_attention`. The reviewer verified the
R2 packet/delta/manifest, all ten changed snapshots and all nine unchanged R1
files. It accepted all R1 corrections except a preflight-inventory residual.
R2 full-diff artifact was initially missing despite all file hashes being supplied;
the exact full patch is now retained under the R2 review directory with SHA-256
`e4bbbce351858869d0c7eab05c606977174d23b349de28a2d378a991fe997239`.

The three R2 findings are corrected for a bounded R3 review:

- Successful recorder JSON no longer mixes stderr into stdout. A real subprocess
  fixture emits benign stderr plus valid JSON and now parses successfully; the
  precise permission-error regression still passes.
- Audio is independently compared with the original audio stream, not forced to
  equal container/video length. Full measured audio is processed and source-offset
  mapped. Claims retain media/video/speech duration and explicit absent-audio
  ranges, without calling those ranges silence. A real four-second video with a
  two-second audio track prepares successfully, while a truncated audio export
  against a longer original audio track is refused.
- Recorder preflight directories and their failure receipts now appear in inventory.

Focused suite after these fixes: 56 passed in 9.23 seconds. No approving verdict,
commit, push, candidate installation or activation has occurred. Per-host private
code/adaptation backups were retained at 06:03:18Z. Fresh baselines are Home
`cf98fccb74fdb1794f64c4b215a8940e12f715dc` and SJM
`196ca9c1bf6a783fcb2a35091e736a0a3459616b`; both legacy Syncthing folders were
live-API verified paused. No credentials were copied or printed.

R3 candidate also pins the original request binding at read-only preflight entry,
before an actual recording attempt. Its regression prevents a deadline refresh
after a failed prerequisite from silently replacing the original request window.
Latest focused checkpoint: 57 passed in 9.28 seconds. R3 supplies an exact full
staged-diff artifact as well as its correction delta and current file hashes.

R3 concluded in 2 minutes 28 seconds with `needs_attention`, verifying the exact
19-file candidate and closing all R1/R2 findings. Two residuals were identified:
request binding still occurred after source prerequisites, and missing optional
video-stream duration metadata caused unnecessary rejection. The earlier R3 claim
that a failed native prerequisite was covered was incorrect: its test called only
the receipt helper, not the failing native-source path.

R4 moves syntactically valid request binding immediately after reading native
source evidence, before submitted identity, playback, freshness or deadline checks.
Three path-level regressions fail real native-source prerequisites, then prove the
same UUID cannot refresh its deadline. Optional video-stream duration is retained
as unknown when absent; container duration and independently measured full audio
still enforce source and audio completeness. A real media preparation regression
removes only video-stream duration metadata and verifies successful preparation,
reuse and status. Focused suite: 61 passed in 10.67 seconds. R4 exact-content review
is pending. No installation or activation has occurred.

R4 approved the complete exact 19-file candidate in 2 minutes 8 seconds, no
findings. Actual reviewer: DeepSeek desktop account, DeepSeek-V4.1-Flash High,
Read Only, under Keith's explicit exception after bounded Kimi no-verdict runs.
Full reviewed diff SHA-256:
`55dd308dd1f7394e00fe1e6a29561ddb69fdd4944dc08542f87daaf18cd9adb5`.
Packet SHA-256:
`f65415b08beb50f4b2377af1e84c31e9b30081f436765a044e14fd9311adcc51`.
All R1/R2/R3 findings were closed. Full verdict remains in the preserved desktop
session and native CUA transcript; private index binds all exact hashes.

Reviewed feature committed and pushed as
`e00a1ad0a7ef066aa0c9ec40190765786127bb53` on `codex/video-extract`.
SJM fetched that commit and installed candidate
`9ddd00f63acb5a8da254f4fff7eec1017e824d7a94cf9183ebe944f73ab62104`
from a private Git archive, preserving its dirty active checkout. No launcher
was activated. Home and Mini candidate installation reached frozen staging but
failed EACCES at directory rename. An isolated read-only-mode rename reproduced
that platform behavior. Partial candidates remain retained, never counted as
successful installations. The Mini compiled the actual recorder successfully.

Installer publication-order correction: retain owner-write on the private staging
root through its atomic rename, then freeze the published root to mode 0500.
Children and files remain frozen before publication. An installer integration
regression enforces the macOS rename requirement, checks all installed source
hashes, root mode, idempotent reuse and absence of an active launcher. Six
focused installer tests passed in 0.03 seconds. This small correction is pending
renewed independent review before any new commit or candidate installation.

Fresh native Mini preflight again observed the 45-second Chinese-subtitled Reel,
clock 0:00 to 0:11, and actual Profile 9 through chrome://version. The fixed private
profile receipt was saved mode 0600. Source was muted; an indicator did not prove
sound. No login, challenge or permission prompt, installation or recording occurred.

Prepared: candidate. Tested: 61 focused tests plus installer correction regression.
Reviewed: e00a1ad approved; publication-order correction pending. Committed: e00a1ad.
Pushed: e00a1ad. Candidate-installed-sjm: yes. Candidate-installed-home: failed.
Candidate-installed-mini: failed. Activated-home: no. Activated-sjm: no.
Activated-mini: no. Accepted: no.

Live acceptance is partial as itemized in the current matrix. Fixtures cannot substitute for actual recording audio, source boundaries or independent SJM processing.

## Recorder startup correction research

The actual Mini crash report identifies `SCContentFilter.init(desktopIndependentWindow:)`
as the first capture-framework caller above the `CGS_REQUIRE_INIT` assertion. The
[Apple NSApplication.shared documentation](https://developer.apple.com/documentation/appkit/nsapplication/shared)
explains that the shared application initialization connects a standalone program
to WindowServer and should occur early in main. A restricted native Mini test
confirmed this exact constructor succeeds after that initialization. The proposed
fix changes startup and probe depth only, preserving unique Chrome-window scope,
exact binding recheck, system audio, microphone off, source budgets and all live
acceptance gates. This is the first recorder repair after the real bootstrap
failure. A repeat of the same behavior after it requires the architect checkpoint;
no display capture or alternate recorder is introduced.

## Outstanding reconciliation at 06:59 UTC

The architect independently read the owning provider-context note after fresh
Home NAS/alias preflight. Its 15 September health snapshot has no SJM-specific
Groq route resolving the current HTTP 403, and it still describes the older
single-URL text helper and a broad retired-Mini line. Current AGENTS and Keith's
confirmed video design take precedence. No context/control note was changed
through a raw write or ordinary-note writer. The reviewed project workflow is
the new operating reference; the dated context limitation remains explicit.

A physical long-run check found Mini-local 86,573,420 bytes, Home-local prepared
43,513,287 bytes and NAS source/prepared 130,085,790 bytes: total 260,172,497,
below 268,435,456 but with only 8,262,959 bytes headroom. The worker monitors
active-host local stages plus NAS run files, including failed partials and NAS
preparations by other hosts; it cannot currently observe their private retained
local stages. A second full long-run SJM preparation could exceed an all-host
physical total despite passing that current operation check. No SJM long
preparation was attempted. This was the evidence behind the storage question;
Keith subsequently confirmed the per-operation boundary below. No cap increase, deletion,
new shared reservation protocol or cross-host state access was introduced.

The native Share dialog exposed only loading placeholders, so no share token
was invented and no duplicate capture dispatched. The architect then supplied
the actual dated source `https://www.facebook.com/share/r/1MFtSe4XWL/`, which
resolved to the known short Reel on 6 November. Fresh native resolution subsequently passed: exact canonical Reel, publisher,
caption, 45-second duration and advancing playback matched the retained source.
This tests URL resolution without repeating acquisition or ASR.

## Recorder checkpoint and storage confirmation

The prior 55-second bootstrap is retained as run `facebook-2372450313602638-20261108T065822Z-d444928f`, source `ca20c737579283a90c6dd8b2f0686728b4ea15745e2f47b935d2acd6e2562943`, Home prepared `5299d7fe8563423195621aa3a6847cc90009f062cd3c1d5868c68aa84797a32e`. Source speech was established by actual Groq ASR and visible Chinese subtitles. It did not prove the source ending. Native CUA never supplies a hearing observation. Keep raw padding, loops and uncertainty. A new named acceptance case is permitted only after the corrected artifact is independently reviewed.

The architect relayed Keith's explicit approval of per-operation limits: Facebook 256 MiB and YouTube 2 GiB for active local plus NAS operation files. Separately retained archive sizes are reported without claiming a combined ceiling over every host's private copies. The earlier cross-host question is resolved, not a reason to change numeric limits.

Implementation checkpoint: the compiled recorder now hashes its own executable, validates the worker context and writes a fixed private ready marker only in `recordingOutputDidStartRecording`. It binds request, URL, deadline, duration, package, actual binary hash, window/process/geometry and callback wall/uptime. The worker requires the corresponding fresh native start observation within 30 seconds and rejects old hearing claims. The component reserves 60 seconds and remains under the original source-plus-120 ceiling. Inventory labels the operation scope and reports existing local/NAS v2 archive bytes separately, with other-host private copies explicitly unmeasured. No protocol result is source-speech or ending acceptance.

## R7 delivery checkpoint

DeepSeek-V4.1-Flash High, Read Only, approved the exact seven-file correction in 1 minute 50 seconds, no findings. Packet `1d699a4f03f4416972c7ce0fec3469c7db88846a41167ee70b7e0c04d5204d10`, diff `2cd17d706a4ad9dec8ff0e76d1575604ef8575db58659e800b7fc631f3c4790d`, manifest `f28e95a0a5c8485080dd3a20182f7bc21d1eeb86d5dab7d01e89c8dc30b308bd`. Commit `082f8dd` was pushed to `origin/codex/video-extract`. Exact staged hashes were checked immediately before commit.

New supervised candidate packages: Home `8ccbba85ddcb57bfe297a6892ab3b2bae571272f9c99de915fbff78b42b64473`; SJM `e9ba8a7dab58b861140f7004c1e70d1bf863b5abb92fd014d1c471bbdd130b28`; Mini `75bc1fd7cd7785940c8c284cae7e8df1088347798279c6bfa368762ef34ea4ba`. All role/version readbacks report processor `62a675b795cced107a4442d4`, unchanged limits and explicit active-operation local-plus-NAS scope. No launcher or live checkout activation. The new processor identity intentionally retains older preparation archives rather than copying their cache into the new version.

Final-version long-source Home preparation also completed: processor `62a675b795cced107a4442d4`, prepared digest `0e27673b39e231fc84e28f5606bfb4e6209e8e252232dee3c5c0c1a02eed951a`, three new actual Groq responses, 374 segments and no empty-word gaps. Every ASR segment list, complete timed transcript, all 44 sheets and the final frame are identical to the already fully read retained preparation; only provider x_groq response metadata differs. Thus the prior content/subtitle/seam/crop/boundary review carries to these exact matching bytes. Older archives remain retained, not copied into the new cache.

## Failed handshake case and bounded diagnosis

Run `facebook-2372450313602638-20261108T072735Z-a9e82b71` completed its single 105-second component invocation and retained 17,618,998-byte `source.mp4`. The request/source/deadline/package/actual-binary/window-bound callback marker validated. The native start file marked opening-picture verification false because its post-ready paused-zero screenshot was reported black, and its timestamp was explicitly the later receipt-write time, 40.180173 seconds after ready. The worker stopped with `native_recording_opening_picture_unverified`; no decode/export, source manifest, NAS transfer or acceptance receipt exists. No retry was sent.

Native clocks later included 0:30, 0:36, 0:41 and wrap to 0:02. The exact last frame was missed. A pause action was rejected for an external Chrome change; final pause at 0:13 of the loop was subsequently confirmed stable. These remain observations with raw padding/loop, not source-boundary acceptance.

Read-only diagnostic extraction from the retained partial at 0 and 5 seconds shows the expected opening interview, doorway artwork and Chinese title; 20 seconds shows the male speaker and early subtitle. Thus those actual output frames are not black. This does not replace the failed native preflight or establish precise playback timing. No actual observation timestamp was retroactively reconstructed. The architect received the repeated delayed-start checkpoint and this distinction before any further controller change.

Separately reported retained v2 archives at this checkpoint: Home local runs/preflights 92,310,722 bytes; SJM local 1,711,899; Mini local 123,825,091; shared NAS v2 runs 198,504,927, independently read through each host's checked mount. The NAS number is the same shared archive, not three copies. Other-host private copies remain unmeasured by any single host's inventory. These are inventory snapshots, not a global all-host budget claim. No cleanup occurred.


## Observation-protocol checkpoint correction

The architect ruled that the prior case remains failed, without receipt retrofit.
Keep visible opening proof before capture, then freshly recheck exact source
URL/title, same window, paused-zero/no-ad and unmuted controls after the callback.
A black native screenshot during simultaneous capture is ambiguous picture
evidence, not a falsely verified image or automatically a changed source identity.
Source-identity ambiguity still stops before Play. The finished recording must
prove its actual first-episode opening, first/last speech, full episode and ending.
Native control observes the end clock or wrap and pauses, without needing an exact
last image from the ambiguous concurrent screenshot surface.

Native observation protocol version 2 captures call-start and call-completion
epochs inside the same CUA call that obtains positive advancing playback, then
persists those returned values later with a separately labelled write epoch.
Both native call bounds must remain within the existing 30-second ready window;
call uncertainty is retained, not converted to a precise source-clock offset.
The pre-record visible opening remains enforced separately. Old or unversioned
start records, write-time substitutes, late calls and unverified identity/opening
are rejected. The actual failed screenshot was re-inspected by its native task:
its source area was black, supporting that screenshot observation only. No native
observation timestamp was captured in that attempt and none was backfilled.

The new focused regression failed before implementation, and 36 recording tests
now pass. Full focused suite: 81 passed in 17.89 seconds. Authority and whitespace checks
passed. Renewed independent review is next.
No timer, window scope, permission, provider, attempt count or deadline changes.
After exact independent approval, one fresh named short case is authorized. If it
fails again, stop recording work and deliver the accepted direct Facebook portion
with the precise remaining blocker for a new design decision. Routine YouTube
fallback cannot start before that short case passes; its original deadline is
immutable even if it expires during this work.


## R8 approval and explicit SJM release deferral

The earlier short review windows did not establish a service failure. The
architect required one uninterrupted final attempt, bounded to fifteen minutes,
with no conclusion prompts or restart. It completed substantive approval in
1 minute 25 seconds, no findings. DeepSeek-V4.1-Flash High, Read Only, verified
all five current snapshots and exact packet/diff/manifest hashes. R8 packet
`7785ee989a61a9843cc28823bf534a67401c4cbbfa739b49e2667b3449eb6bf6`, diff
`e8e48b94b2149d9c0ef5b23ce85766b970b1081def9dad8380b46599077e6004`, manifest
`4b4130642e553c59a8a4e63a35448ef45c525f73fe32e1e2c10eb402442f2310`.
Commit `db9966c` is pushed. Home candidate is now
`b505ebf9911b342c382081a21e5897fc3967e727b2d86f058a88e90b27a3696b`;
Mini `a4027401f8b37a511c0f07f55fb29fcbcb3b3ff4c3691be61c9c2a6672155e5e`.
No activation. The one final newly named version2 short test is in progress;
its failure would stop recording work for a new design decision.

Keith explicitly deferred SJM processing and activation for this release and
selected Home until a later fix. This is a deliberate user deferral, not SJM
acceptance. Its prior R7 candidate and failed chunk remain preserved. Verified-TLS
unauthenticated models requests independently returned Home HTTP 401 with
invalid_request_error/invalid_api_key via 198.18.15.214, versus SJM generic HTTP
403 with null structured type/code via 104.18.38.236. Both showed the same service
edge and Google Trust Services WE1 issuer. This supports an access/egress-policy
distinction before STT, without proving a country rule or account defect. No key
replacement, provider switch or network/account/credential change is justified.

A narrow activation-receipt correction is being prepared so Home/Mini list only
passed relevant examples, with the historical media-first SJM example explicitly
deferred by Keith's dated instruction. Those earlier recording/media gates were
later superseded by the approved content-first design. The original YouTube
request now lacks its source-plus-120-second admission reserve before its
immutable deadline; it has not been silently refreshed or retried.


## R9 approval and final recording preparation correction

R9 independently approved the exact six-file SJM-deferral reconciliation in
42 seconds, no findings. Packet `179008d2a38e5ffdf6aba8c68af6ca49ba289b69cbcac2426f57afb83be02098`,
diff `567ff14b59829d65c51b1eddfb279a4c1846caa7e52a78fcbffee1f603907751`,
manifest `f532b814b181089b0332845834b75422989ac0968cbb0c1dfdd8f436de913d00`.
Commit `b875d66` is pushed; no activation.

The final short source bundle is
`facebook-2372450313602638-20261108T075420Z-768c0f84`, source digest
`9a10b2e7b7678fbc9e1851e87d46b0864f4e58c57755c20979a1464cb8dbe653`.
Root independently verified all 11 payloads, 17,547,814 bytes, after fresh Home
vault-alias/capture-mount checks. Native source 45 seconds, actual container 111.464958 seconds,
video 73.21 seconds; actual callback/native call passed. No source acceptance yet.
Home real Groq output was retained before preparation stopped with
`visual_sample_count_or_time_mismatch`. Its sparse opening cannot be sampled
correctly using artificial per-16-second input EOF boundaries.

A real 18-second fixture with blue and red frames reproduced that failure. The
v2 full-stream sheet pass now retains blue at 15 and 16 seconds and red only at 17 seconds,
uses the unchanged v1 progress/hash/coverage validator, and preserves interrupted
output bytes as partials. Four real fixtures cover both interruption states and
18-second video with 18- or 30-second audio. The longer audio never generates images
after 17 seconds. A read-only actual-source regression produced five sheets and 74 samples,
actual final PTS 73.168333 and unpictured container tail 38.254958 seconds. These
are private diagnostic outputs, not a final prepared receipt or NAS publication.

Root and builder inspected all five diagnostic sheets, final picture and full retained
transcript. The opening is held; samples at 16 to 18 seconds are black, playback then follows
the source progression before a loop around 64 seconds. The final picture at 73.168333 seconds
belongs to the paused loop. First-episode ending still needs subsecond proof.
The first ASR interval 0.02 to 20.24 seconds spans the paused lead, so its returned words can
be corroborated but its start is not precise speech onset. One raw Groq segment
89.58 to 119.56 seconds exceeds the actual 111.422688-second submitted audio and contains an
unsupported generic call-to-action. It remains raw and excluded from normalized
claims by the approved anomaly rule; no clipping, silence inference or retry.

Read-only classification of that unchanged response yields 39 supported segments
and one anomaly. All 374 segments in the three previously reviewed long-source raw
responses remain within their actual submitted chunk bounds, with zero anomalies.
Raw response, anomaly record, timeline and visual coverage are all bound by the
new prepared manifest. Malformed parse cases still block; episode intersection
checks block an anomaly inside required content but permit a separately labelled
padding anomaly. Focused/full checks and exact independent review precede new
candidate installation and reprocessing this same retained source. No new
recording attempt, provider/network change or SJM activation occurred.

Final processing-correction verification: 105 focused tests passed in 20.15 seconds. Authority, skill validation and whitespace checks passed. The sparse fixture and new classifier cases failed before implementation. A real preparation/status fixture proves raw-response hash/mtime stability, one provider call across reuse, anomaly visibility and prepared-manifest integrity refusal after anomaly-record corruption. No source-episode acceptance or actual first speech-onset precision is inferred from these tests.


## R10 approval and interruption-order correction

DeepSeek independently approved the exact five-file R10 correction, no findings,
with packet `73e70f06a1d1c3b9f002575cb8461f17a756757a427a456884d7033ce6119309`,
diff `4562fb74e3f0673c9755f29eb1fba58f14bad15a4e43a639e63237beb48a5a83`,
manifest `de706d5b4e5790dbfeac519521473d2d4020945c1a2efd02de07cd9c5f71e0d0`.
It completed around 08:30 UTC after the uninterrupted 08:23 UTC start. Approval
was not used to install or commit a subsequently discovered recovery edge.

The builder's additional real interruption probe found a narrow ordering issue:
when a prior complete output pass had committed only its first sheet's progress,
an interruption between moving old sheets preserved the bytes but left progress
pointing at a moved file. Retry then held `visual_progress_hash_mismatch`.
The new regression failed with that precise reason. The correction validates
all old files, atomically clears their committed progress before any move, then
preserves them as partials. Interruption between moves is now retryable without
losing old bytes or claiming completion. Targeted regression passes; full checks
and exact renewed review precede commit or installation. No new recording.

Mini's retained `a4027401f8b37a511c0f07f55fb29fcbcb3b3ff4c3691be61c9c2a6672155e5e`
runtime was read back and all four files match reviewed commit `b875d66` exactly.
The R9 media-only bundle and installer can therefore deliver that unchanged,
live-tested capture package while Home receives the processing-only correction.
The source protocol is unchanged. This preserves actual capture-binary evidence
without claiming an untested binary/package was recorded. SJM remains untouched.

Final interruption-order verification: 106 focused tests passed in 21.66 seconds; authority and whitespace checks passed. No source reacquisition or recording retry.


## R11 finding and verified stale-state recovery

R11 returned needs-attention in 1 minute 14 seconds. It correctly distinguished
preventing future interruptions from recovering an already stale checkpoint:
a completed sheet entry might already point at a sheet preserved by an old pass.
The builder retained the exact R11 packet and did not commit or install it.

The new regression reproduced that old state and failed before correction.
Recovery is now proof-bound: only a missing completed root sheet with its exact
declared SHA-256 under the same run's bounded partial directories can cause a
new verified generation pass. That preserved file is not promoted as completed
output; the whole pass is regenerated from the verified source. Missing bytes,
modified partials and a mismatched existing root sheet remain integrity holds.
No old cache or ASR response is copied across processor versions. Positive and
negative regressions prove all original valid bytes remain preserved and no
corrupt state is silently cleared. No new recording, provider retry or activation.

Verified stale-state recovery checks: 110 focused tests passed in 23.19 seconds. Authority, skill validation and whitespace passed. The matching-old-partial case failed before correction; missing/modified partials and mismatched current files remain blocked.


## Reviewed Home processing and bounded short-source acceptance

R12 approved the complete corrected candidate in 1 minute 17 seconds, no findings.
Packet `c076df1144270d031dba1e2121cac0d73d4fa8d65771799b73a15b2e9e5a2904`,
delta `9ccea704a6cedc26f1c7d9e475ae8194276af54b957cb040adf1367f98bbfcda`,
full diff `72333fce080d79cb3dc6914206cb4aff258044eed6c61c99ce8eae059c611a94`,
manifest `80af748a5be505e2d4c9dfd96c1ea9860c68de595a1c1b9777df31a704f44403`.
Commit `4156277` is pushed; Home candidate is
`c68e32aecc7e194cddb008382e25421e042c3636f0029e534905e0a6789e8a2f`, processor
`ecf81537354bebb476606978`. Mini retains exact reviewed R9/a402 capture runtime.

Same-source preparation completed and published, digest
`f535af6e94ec62c2222b6061760e0d5c9b5336bcbfa3b12e9830b0592548d828`.
All five sheets and final image byte-match the already fully inspected diagnostics.
The new real Groq response's segment list matches the fully read retained response:
39 supported segments, one impossible tail segment retained raw and excluded.
Source/prepared/status verification passed; root independently verified all 16
prepared payloads locally and on NAS and read the normalized transcript/anomaly
record. Actual opening/subtitle frames and cleared ending before the loop match
the retained direct reference. Conservative episode bounds 18.703333 to 63.711667
have no intersecting unverified range. First ASR onset remains imprecise, later
loop/padding remain labelled, and the final paused-loop image is not the source
ending. This establishes bounded first-source-episode picture/speech/boundary
acceptance, not complete transcription or every-frame viewing. The private
hash-bound review receipt is being finalized before enabling the next validation.

The architect explicitly authorized one fresh post-repair recording-only YouTube
deployment validation under Keith's existing delivery authority. The old failed
direct request remains closed and unchanged. No new identity/deadline is created
until candidates and recorder receipt are ready; no second direct attempt or
further routine recording retry is permitted. Source/host/method/provider/limits
are unchanged. The new case will be announced and both request records retained.
No Home/Mini launcher or checkout activation, and SJM remains deferred.

## Recorder receipt and fresh YouTube case dispatched

Private short-source review SHA-256 `bb6914a19d8f274b2b349fdb175c1e9e9a8370bd520e45f19c1c1dd3f448fe7a`; Mini receipt SHA-256 `c426441992dd04ba2b63c027393d9f1180312d5531f8139384fcac7315317559`. Mini independently verified the source and Home prepared payloads, actual capture package/binary and old YouTube direct-only request ledger before storing the receipt. Version readback now reports recorder_accepted true; no routine launcher exists. Final Home processor reuse preserved the successful raw response hash and nanosecond mtime.

Fresh recording-only YouTube deployment-validation request `e7a2c9fd-666f-4a81-92fc-0ffd972a09c9` was announced and dispatched promptly after all factual gates passed. Created `2026-11-08T09:03:11.541629+00:00`, immutable deadline `2026-11-08T10:33:11.541629+00:00`. Previous request `5958d65b-7728-42d4-af5e-b8da77eb430f` remains closed with its original deadline and sole failed direct attempt. No second direct attempt or further routine recording retry is allowed. The exact prompt and both linked request records remain private operation evidence. Source, hosts, method, duration/byte caps and provider remain unchanged. Current case is in progress, with no YouTube or overall release acceptance yet.


## Confirmed late-confirmation warning and exact retained-run recovery

Keith explicitly approved warning plus full-content proof on 8 November 2026,
including conditional recovery of the current retained YouTube recording. Its
request e7a2c9fd-666f-4a81-92fc-0ffd972a09c9, original 10:33:11.541629 UTC
deadline, consumed recording attempt and hard source-plus-120-second/2-GiB
per-operation limits stay unchanged. Native observation interval 29.522480 to
30.217480 seconds after ready straddled the existing 30-second target. The
original failed predicate remains false and retained, without rounding or retrofit.
A structurally valid late confirmation becomes a visible warning, never proof
of complete capture. Missing readiness, changed source/window, invalid timing
structure/sound route and hard limits remain blockers.

Recovery is restricted to youtube-bc7QaDmLmoA-20261108T090447Z-d57543ed and
its exact old a402 package. It requires independently returned raw/context
hashes, unchanged existing ledger and original command failure, bound native
source/startup observations and pinned capture code/binary. Old control-flow
evidence proves only earlier successful command/JSON/window checks. Discarded
final stdout is explicitly unavailable, not reconstructed. Fresh full decode,
actual media/audio validation and original deadline precede normal NAS staging
and Home processing. New records say pending_content_verification and expose
the original failed predicate/timing warning in prepared/status metadata.
No accepted capture receipt or new recording is permitted. Final linked recovery
acceptance requires complete identity/opening, supported first/last speech, actual
ending and full-episode coverage, with explicit ads/loops/padding/stalls and
unresolved ranges. Exact code/docs/tests require independent review before install.
SJM remains deliberately deferred and untouched.

Recovery verification checkpoint: 125 focused tests passed in 26.29 seconds.
Authority and whitespace checks passed. New regressions first failed before
implementation. Tests preserve the measured straddling interval and failed target,
reject changed raw/context/native evidence, wrong run/ledger, expired deadline
and a different original failure, and publish/reuse a pending bundle without
calling the recorder or mutating the original files/attempt ledger. A real
media preparation/status case retains the warning and original failed predicate
through hash-bound local and NAS preparation. The single YouTube capture remains
in progress; no recovery installation, new recording or activation occurred.


## R13 checkpoint and distinct hard-timeout outcome

R13 returned explicit approve in 12 minutes 24 seconds, no findings, for all six
exact files. Manifest 8bdad1233d72b08a4a60a9eb67a5f21f31aebc8efade24887396d367927eac6b,
packet 0c46ce2d681d189b93519dd122951b2b4b5a33956fae64c59f1ac91786fe5229,
diff b70c24a53fcaf93ce4a701dfe4bd1f99deea4829c00ae65695f88f6217d5bd5b.
Approval covers commit and supervised Home processing-candidate update only;
it does not approve new Mini installation, capture/content acceptance or activation.
The exact reviewed checkpoint is committed as 24ece38 and pushed on codex/video-extract.
No candidate or live launcher was changed after this review.

The one YouTube recording instead ended with exit 2 and exact result
{"status":"blocked","reason":"run_deadline"} after 1,482,410 milliseconds.
Completion was observed at epoch 1794130169.788. This is a hard command-duration
failure, not the late-confirmation predicate; Keith's late-only recovery approval
does not admit it. Recording work is paused. No retry, repaired container, decode,
provider transcription, source manifest, successful completion receipt or NAS
publication has occurred. The original request/deadline/consumed attempt remain
unchanged. No recorder process remains.

Raw source.mp4 remains on Mini, 586,567,498 bytes, SHA-256
3131a108c51a8a6dcad3d08761e73e7c3496c9a0855eb70e02ff62a8efbbceac.
Original source/ready/start/progress/final-ending/result/duration evidence remains
mode 0600 and independently read back. Private evidence record
youtube-postrepair-hard-failure.json has SHA-256
41e04d18c96e5effbebde716516fed9608cfe1d1bf285f63b0c7524b70d5fcae.
Native Chrome did reach 22:42/Replay with autoplay off. A muted source control
was observed around source12:00 and subsequently restored, with unknown onset
and recorded-speech effect. Repeated/decreased AX clocks and an external Chrome
change notice are retained as uncertainty, without assigning an unproved cause.
Native ending is not proof of a finalized recording or full recorded content.

Swift sleeps source-plus60, awaits stopCapture, then polls recording completion
for up to ten seconds before emitting final stdout. The parent monitors the hard
source-plus120 deadline and kills a still-live child on timeout. No stop-phase
marker exists, so this result does not isolate sleep, stop await or finalization
as the exact stalled phase. No timer or recording architecture was changed.

The architect separately authorized read-only native Show transcript research on
the same page for the original content question. It uses no failed media, download,
recording or provider transcription. Actual caption language/provenance, first/last
coverage and observed native visual moments will be retained with limits. This
is caption research, not accepted extraction or full visual review.

Current delivery state: prepared and tested (125 focused tests); reviewed,
committed and pushed through 24ece38. Home candidate c68e32ae remains installed
but not activated; Mini a402 remains installed with the accepted short-recorder
receipt but no routine launcher activation. SJM is explicitly deferred and
untouched. Direct short/share-form/long Facebook and the bounded short first
recorded episode have accepted evidence. YouTube and the full release remain
unaccepted. Home integration is private/read-only pending a concrete partial/full
release decision. All unrelated dirty host state and v1 remain preserved.


## Native caption research handoff, separate from extraction acceptance

The named Mini task inspected all 177 exposed native caption rows, from 0:00
to 22:39 across 27 chapters, with both endpoints visually reached. The actual
label is English (United States) (auto-generated). Caption start times are ordered
and distinct; caption end times and completeness against actual source speech
remain unverified. The full verbatim third-party transcript was not retained
because of copyright limits. No failed media or provider transcription was used.

Home private operation evidence byte-matches the independently returned Mini hashes:

- native-caption-research-metadata.json: 0a24b7bca3e276839ad52740c07690c6d66bf824f1c7e3b8c1b19d06452dfb13.
- native-caption-research-visual-notes.json: 9883893d764c8c3f8f6d7cedebb215b10cce7a453e2bbd1ec669c9060f23cf0a.
- native-caption-research-summary.md: 37b45889c85d06d2997c87e774e3f73b8f1be728b354e114da2ed093d1d3e592.
- native-caption-research-claims.json: 6078c9ee232c11497ec98481d859b494c11d959f18d05a93798502bdb8112c3b.

The builder read the complete 2,339-word transformative paraphrase and handed its
verified Home paths to the architect for the original content explanation. It
contains 14 timestamped claim groups, separates presenter claims from researcher
interpretation, and preserves uncertain captioned model names, prices and technical
terms. Visual corroboration is limited to the actual native moments listed in
the companion note; no hearing, full visual/audio viewing, accepted recording or
new extraction is claimed. No installation, new capture, provider call or SJM
contact occurred. The sole uncommitted file is this factual delivery append;
all code remains at independently approved and pushed 24ece38.


## Confirmed content-first release revision

Keith explicitly chose content extraction as the default after the concrete
choice was explained: YouTube native captions plus relevant native visual
checks, Facebook direct Mini download plus Home processing, truthful retained
timestamps/provenance/coverage, no complete YouTube media claim and precise
unsupported results. This supersedes the earlier default recorder/media examples,
not the hard limits or preservation obligations. SJM remains deferred. The
current written design and implementation plan were rewritten before new code.
Existing native caption research will retain its actual observation time and
provenance; a separate bounded native scene check will improve relevant visual
coverage. The custom recorder/R13/failed media stay unactivated and unused.
Home integration remains preservation-aware and no unrelated work is included.

Content adapter checkpoint: 146 focused tests passed in 23.81 seconds; authority,
both changed video skills and whitespace checks passed. Tests first failed for
the absent typed path, then proved actual file publication, Home validation,
preparation/status and reuse with probe/STT/recorder calls forbidden. They also
cover source/type/claim/clock/deadline/hash failures, incomplete caption sequence,
byte-limit preservation, default recording refusal and typed activation gates.
Default Mini installation no longer compiles or installs the recorder executable.
The frozen v1 library remains SHA-256 e320dc76c3a1370e912c7b3c71ace1f778a3b1032cde2c3c9145dbd61924bbf2;
Swift source remains unchanged and experimental.

All actual retained caption/summary/claim/prior-visual bytes and seven fresh native
scene notes validate through the candidate adapter. Caption count is177, starts
0to1359seconds, source1362seconds. Actual scene clocks are219,714,853,1038,1272;
the requested370/585samples retain null clocks and explicit uncertainty. Scene
file SHA-256 beaa3672959de5c2c1ce3cb01b56f3ace2d213b32edcf0164f9ce9e6b74a1473.
The caption observation interval remains the actual earlier native research turn,
1794130496to1794130761, not a fresh media observation. Validation is private and
read-only: no publication UUID, installation or activation has been created yet.
Independent exact review precedes all such delivery.

## R14 findings and corrected direct caption reader

R14 returned needs_attention after11minutes20seconds. Its complete15-file
snapshot and findings remain retained; nothing from it was committed/installed.
The corrected contract uses named deferred_scopes without historical example
numbers and requires Home vault binding/recall explicitly. SJM candidate
installation now refuses before filesystem mutation. Expired first requests pin
their original deadline and creation time before validation. A remaining research
offload reference to the retired YouTube helper was removed.

The architect clarified that the Mini bundle already contains the actual analysis
and visual observations. Home now verifies and reads that immutable NAS bundle
directly, returning its source digest/paths, reader version, identity, provenance
and coverage. It creates no copied caption cache, transcript or derived-publication
transaction. This removes the reported interrupted Home publication state and
the separately reproduced post-validation copy race. Facebook media preparation
and publication remain unchanged.

Two further reproduced source-binding defects are fixed: the staging context is
parsed from the exact bounded bytes matching the independently supplied digest,
then rechecked after copying; Home process/status compare the inner canonical
source with the outer run and independently revalidate bytes after reading.
Both hosts enforce the1MiB context cap. All new regressions first failed before
correction. The final156-test suite passed in24.48seconds; earlier authority,
skill and whitespace gates passed, and the frozen v1/Swift bytes remain unchanged.
Actual retained source validation still passes for177caption rows and7native
scene observations. The final bundle will label raw retention excerpts_only:
brief endpoint excerpts exist, but no full verbatim transcript is retained.
No live publication UUID, installation, activation or SJM contact has occurred.
