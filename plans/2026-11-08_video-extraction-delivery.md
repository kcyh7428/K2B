# Video extraction delivery

Design: `plans/2026-11-08_video-extraction-design.md`.
Implementation: `plans/2026-11-08_video-extraction-implementation.md`.
Builder: OpenAI, isolated `codex/video-extract` from `bd4a25b`.
Authority: Keith's develop and deploy request in the source chat, supplied in the builder handoff on 8 November 2026. Facebook correction: 1,200 source seconds; cumulative run files remain 256 MiB. YouTube: 3,600 source seconds, 2 GiB cumulative run files.

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

Prepared: candidate. Tested: focused fixtures and Mini source/API preflights only. Reviewed: pending. Committed: no. Pushed: no. Activated-home: no. Activated-sjm: no. Activated-mini: no. Accepted: no.

Live acceptance examples 1 through 8: untested. Fixtures cannot substitute for actual audio, source boundaries or independent SJM processing.
