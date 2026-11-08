# Unified Video Extraction Implementation Plan

> For agentic workers: execute this plan task by task in one builder context,
> using executing-plans guidance and K2B's independent review rules. The builder
> owns tactical review iteration. Escalate structural conflicts, scope changes
> or genuine operator decisions to the architect.

**Goal:** Replace fragmented Facebook/YouTube extraction instructions with one
reviewed, deployed `k2b-video-extract` workflow on Home and SJM using the Mini.

**Architecture:** Mini native Chrome verifies and captures requested media;
NAS retains a verified source bundle. The requesting knowledge Mac processes
that bundle locally and the assistant reads the resulting speech and visuals.

**Tech stack:** Existing Python, yt-dlp with verified EJS/Deno, ffmpeg/ffprobe,
Groq speech processing, and a narrow Mac screen/system-audio recording component.
Prefer a small ScreenCaptureKit component over adding a cloud service. Test actual
Mac compatibility and permissions before adopting it; do not assume Linux parity.

**Spec:** `plans/2026-11-08_video-extraction-design.md`.

## Global constraints

- Read live Home `AGENTS.md` and all named task runbooks. Its current uncommitted
  provider and explain-system additions remain live instructions but are unrelated
  source changes; never accidentally ship them as part of this media feature.
- This worktree starts at `bd4a25b`. Use branch `codex/video-extract`; preserve
  the original checkout and SJM's local changes. No blanket add, reset or stash.
- OpenAI-built code requires approving Kimi review with no fallback before every
  commit. The recent audit's Kimi review timed out after 180 seconds. A timeout
  is not approval; the builder may diagnose and retry with the normal bounded
  review deadline, but cannot bypass independence or hide transport failure.
- Git carries code/skills to Home and SJM. The Mini receives only the reviewed,
  versioned media package. Credentials and local state never move with code.
- Initial platforms are Facebook and YouTube. Limits, ownership, privacy and
  acceptance are fixed by the spec. Other platform support requires new tests.
- No new daemon, schedule, cloud browser, OpenAI API spending, proxy change,
  general vault writing or account/permission change is bundled into this work.
- Persist progress, exact test results, review verdicts, deployment receipts and
  blockers in `plans/2026-11-08_video-extraction-delivery.md`. Do not put private
  media, credentials, browser state or unredacted delivery URLs in Git.

## Review focus

- Valid-looking URLs redirect to unrelated/private destinations: reject without
  leaking browser credentials or accepting a mismatched source (Tasks 1 and 2).
- A source plays but a recorder misses sound or the ending: no complete-content
  status without actual boundary/audio coverage evidence (Tasks 2 and 3).
- Concurrent Home/SJM processing or uncertain dispatch: no duplicate acquisition,
  overwrite or repeated successful ASR submission (Tasks 1, 3 and 5).
- SJM has no capture mount or provider credential: keep the block explicit and
  do not silently fall back through Home (Tasks 3 and 5).
- Dirty host instructions contain unrelated changes: merge only this reviewed
  feature and preserve existing adaptations and active bindings (Tasks 4 and 5).

## Task 1: Establish the shared contract and preserve Facebook

Files: create `scripts/lib/video_extract.py`, `scripts/video-extract.py`,
`tests/test_video_extract.py`; bring the installed v1 worker's reusable logic
under reviewed source control where needed. The exact library split may be
adjusted for reuse without expanding the external interface.

Interface: fixed actions `version`, `capture`, `record`, `process`, `status`,
`inventory`, `save-digest`, each with strict arguments. Run actions take a source
URL or validated run ID and independently supplied source manifest digest.
Only installation accepts a fixed host role; routine calls cannot change roots,
credentials, providers, executable paths or caller-supplied code.

- [ ] Inventory the installed Home/Mini v1 code and hashes. Snapshot it for review;
  do not edit the read-only installed package in place or restore retired Mini code.
- [ ] Add failing tests for platform IDs, source canonicalization, redirect/DNS
  restrictions, URL credentials, traversal, symlinks, malformed manifests and
  non-success receipts for incomplete stages. Preserve the verified Mini fake-IP
  exception only for its fixed approved media hosts, with normal TLS validation.
- [ ] Implement the common source/prepared bundle contract and platform adapters,
  preserving v1 originals, marker/readback checks, locks and explicit failures.
- [ ] Run the new focused tests and existing YouTube helper tests. Record actual
  results and any baseline failures; do not broaden unrelated suites unnecessarily.

## Task 2: Add YouTube acquisition and Mac recording

Files: `scripts/lib/video_extract.py`, a focused recorder under
`scripts/media/`, `tests/test_video_extract.py`, and
`tests/test_video_recording.py`.

Interface: both direct acquisition and recording produce the Task 1 source
bundle. The recorder takes only a verified selected source and fixed host output
root; it never accepts arbitrary shell commands, windows or destinations.

- [ ] Prove the Mini's actual available screen/system-audio APIs and permissions.
  Build a narrow recording component if needed. Microphone capture stays off.
  Any new permission prompt is an exact operator handoff, not an automatic grant.
- [ ] Pin and verify a suitable official yt-dlp plus EJS/Deno combination. Latest
  stable 2026.10.26 still failed public extraction of the original link in the
  audit. Test actual behavior rather than assuming a version bump solves access.
- [ ] Add tests for duration/bytes/timeouts, failed-direct to recorded-source
  transition, remaining-time checks, incomplete/silent audio and recording padding.
- [ ] Implement approved Mini-local session use without exporting credential
  material off Mini. Do not add an unreviewed token plugin or paid fallback.
- [ ] Live-test direct YouTube acquisition and a forced recording fallback on a
  known playable source. Retain and inspect actual audio and boundary frames.

## Task 3: Make preparation portable to Home and SJM

Files: common library, `scripts/video-extract.py`,
`tests/test_video_processing.py`, and host-specific installation settings.

Interface: `process RUN_ID SOURCE_MANIFEST_SHA256` returns a prepared-result
manifest binding the verified source digest, processor identity, timed transcript,
visual samples and explicit coverage/gaps. Repeated calls resume verified stages.

- [ ] Add tests for wrong mounts, independent expected digest, hash mismatch,
  isolated concurrent preparations, failed provider chunks, gaps, stale derived
  artifacts, interrupted NAS publication and reuse without duplicate ASR calls.
- [ ] Reuse existing Groq credential-loading and chunk handling on the owning
  requesting Mac. Check credentials without printing values. A missing credential
  blocks only that host's processing; do not copy another host's credential.
- [ ] Prepare complete timed speech and visual sampling with source-time maps.
  Preserve accurate observed frame times, final frame, crop checks and audio tails.
- [ ] Test the actual SJM direct capture-share binding and nonce/hash identity.
  Initial observation: SJM has only its Keith-Work mount, not the capture share.
  Establish the approved NAS route with its own existing authorization or report
  the exact login prompt. No Home relay can satisfy Home-independent acceptance.

## Task 4: Consolidate skills, authority and installation

Files: create `.agents/skills/k2b-video-extract/SKILL.md` and its focused
references, `scripts/install-video-extract.py`, installer tests; modify the
YouTube entry skill, research URL routing, affected sync/ship authority references
and the narrow SJM media exception in `AGENTS.md`.

- [ ] Make one maintained skill own video routing, commands, evidence and failure
  handling. Research delegates to it. Keep a thin legacy YouTube redirect only
  if existing callers need compatibility; no second competing capture procedure.
- [ ] Use the existing Facebook workflow reference as a migration input. Current
  Home contains uncommitted relevant hardening. Review intended media changes
  explicitly; do not assume earlier Kimi approval or ship unrelated research edits.
- [ ] Implement fixed, versioned, hash-bound role-specific installations outside
  writable source directories, with retained old versions and explicit rollback.
  Ordinary commands cannot install/update themselves or broaden permissions.
- [ ] Update authority narrowly for requested SJM media preparation on the capture
  share. Preserve Home-only semantic reconciliation and vault shared hubs.
- [ ] Run skill validation, authority verification, installer negative tests and
  Git whitespace checks. Mechanically scan exact circulated handoff/draft text
  under AGENTS.md, retaining command flags and Markdown exceptions with reasons.

## Task 5: Review, deliver and accept on the three hosts

Files: delivery record and exact intended feature files only.

- [ ] Self-review against every spec acceptance example; retain a concise evidence
  manifest. State which examples are fixtures and which are real host observations.
- [ ] Stage the exact intended set, get approving independent Kimi review using
  the K2B wrapper, resolve tactical findings in the builder context, and rerun
  invalidated focused tests. No commit while the verdict is missing or unresolved.
- [ ] Commit and push the reviewed feature branch. Preserve remote/local changes
  and use deliberate Git integration for Home and SJM; never force a dirty checkout.
- [ ] Install the reviewed Mini capture package and the requesting-Mac processors.
  Verify actual package hashes, settings, NAS identity and command invocation.
  Preserve the working v1 route until its replacement passes relevant live tests.
- [ ] Complete live acceptance from Home and SJM, including the original YouTube,
  Facebook canonical resolution, a longer Facebook video, no-caption speech,
  forced recording, source/audio/visual checks, replay and explicit failures.
- [ ] Verify representative source-backed K2B recall and actual vault bindings on
  affected knowledge hosts as required by ship rules. Do not claim broad recall
  completeness or sync from this bounded check.
- [ ] Retire competing capture instructions only after accepted replacement.
  Report prepared, tested, reviewed, committed, pushed, activated-home,
  activated-sjm, activated-mini and accepted separately, with exact blockers.

## Handoff context

Original dirty checkout: `/Users/keithmbpm2/Projects/K2B`.
Isolated implementation worktree: `/Users/keithmbpm2/.codex/worktrees/video-extract/K2B`.
Private audit: `output/video-extraction-audit-2026-11-08.md` in the original
checkout, including source research and Social Scout's critique.
Installed worker source: `/Users/keithmbpm2/.local/libexec/k2b-reel/e320dc76c3a1370e912c7b3c71ace1f778a3b1032cde2c3c9145dbd61924bbf2/worker.py`.
Mini native task: `01a17718-be0a-7121-bba3-7bc5e8c20810`, host
`remote-control:env_e_6add8d407cac83279a777291f2b46b8e`.
Authorized SSH aliases: `macmini-local` and `sjm-ai`. SSH is appropriate for
authorized package/deployment checks; native browser verification remains in the
Mini task using its actual computer-use tools, not SSH-driven UI automation.

Fresh observations: Home baseline `bd4a25b`; SJM baseline
`0ed2f76e0014b3034c687f817ae7576db1887093`, with local changes in the YouTube
skill and two eod-capture files. Both Macs' unrelated edits must be preserved.
Mini runs macOS 15.4 and has ffmpeg, Deno and Swift; the v1 helper and exact NAS
capture mount returned expected role/hash. These observations are dated preflight,
not acceptance of new code or a recording permission check.
