# On-demand Facebook Reel capture and digest

Use when Keith sends a Facebook video/Reel link for digestion. Keith explicitly
authorized building, activating and setting one-time permissions for this route
on 27 October 2026. Routine source submissions within this scope authorize
messaging the Mini task and invoking the installed helpers. This is not background
scraping, publishing, credential transfer or authority to weaken other permissions.

## Native Mini route

Read the exact current Mini thread with its host ID before dispatch:

- Thread: `01a17718-be0a-7121-bba3-7bc5e8c20810`
- Host: `remote-control:env_e_6add8d407cac83279a777291f2b46b8e`

These IDs were verified on 27 October 2026. A missing thread, changed host ID or
unexpected visible source stops dispatch. Consult the vault operating-model note
for dated routing evidence; do not substitute another task or host without Keith's
authorization.

Verify the current source through native computer use on that Mini, then invoke:

```text
/Users/fastshower/.local/libexec/k2b-reel-v1 capture 'https://www.facebook.com/share/v/EXAMPLE/'
```

Send only the source, current question, fixed command and bounded task deadline.
Pass the URL as one safely quoted shell argument. Never paste an unquoted URL
with tracking parameters into shell command text or evaluate its contents as code.
The command owns preflight, download, validation, NAS copy and read-back. It
accepts supported HTTPS Facebook share/video paths, up to ten minutes and 256 MiB
of cumulative run files. Credentials are not needed for the tested public route.
The helper validates the source host/path and strips query parameters and fragments
before network access. The supported path already identifies the Reel or share;
tracking and redirect parameters are never forwarded.
NAS files are first copied under unique partial names and hash-verified before
atomic publication. Interrupted copies remain under `partials/`, and a retry uses
a new partial without touching the old one. A mismatch under a final published
name remains a blocking integrity failure. There is no automatic overwrite or
deletion of committed evidence.
Capture command deadline is ten minutes; allow a fifteen-minute dispatch envelope
for browser verification and result return. Two acquisition dispatches at most in
one active task. Preserve partials. Do not increase limits automatically.
The NAS IP and per-host SMB accounts are intentionally pinned to the verified
deployment. A changed NAS address, DNS-name mount or account is an operator
configuration change requiring renewed verification and reviewed installation.
The helper stops instead of accepting a similarly named unverified share.
The named Mini's existing route was verified on 27 October 2026: Facebook and
three allowed media hosts resolved into `198.18.0.0/15`, with successful normal
TLS connections. Mini role therefore permits that specific fake-IP transport
range as well as public addresses. Home retains public-only media checks.
Fixed Facebook/CDN hostname restrictions, HTTPS and normal TLS certificate and
hostname verification remain mandatory. LAN, loopback, link-local and arbitrary
private URLs remain rejected. This supports the observed Mini route, not every
proxy, and changes no VPN, DNS, router or proxy settings. `version` reports the mode.
Changes to the Mini's proxy, DNS mode or fake-IP range require renewed route
verification. Other private ranges remain blocked; never expand the allowance
automatically. Inspect the `version` transport mode when diagnosing a route change.

Read the completed JSON result. It must bind `run_id`, `manifest_sha256`, local
path and NAS path. A successful send is only dispatch. Use wait_threads for
completion and bounded read_thread fallback if waiting fails. Avoid duplicate
dispatch while a worker remains active. General inventory omission does not
establish disconnection. Screen Sharing is a supervised fallback; garbled input
there does not establish that native Mini browsing is unavailable.

## Home processing and assistant review

Invoke on Home using the independently returned source manifest digest:

```text
/Users/keithmbpm2/.local/libexec/k2b-reel-v1 process RUN_ID MANIFEST_SHA256
```

The command requires Home's independent NAS SMB mount and verifies every source
payload plus the random marker. It uses Home's existing Groq credential without
copying it, transcribes the entire audio in timestamped overlapping chunks and
prepares visual contact sheets. It persists the result to NAS before success.
The prepared result is evidence for review, not a finished digest. Home's existing
`~/.k2b-env` must be a regular file owned by Keith with mode 0600. A credential
failure blocks processing; do not copy or silently change credential files.

Read the entire transcript. Reconcile the labelled overlaps against the original
chunk responses. Review every returned contact sheet in the documented reading
order and its timestamp map, plus the final-frame image.
Each sheet records actual filter-observed sample timestamps, `frame_count` and
`padding_cells`. Only the first `frame_count` cells are evidence; ignore all
padding cells. A missing or incorrectly timed sample blocks preparation rather
than silently substituting one frame. The video stream duration is recorded
separately from any audio-only tail. Accurate seeking is explicit during image
transcoding; see [FFmpeg seeking documentation](https://www.ffmpeg.org/ffmpeg.html).
Compare uncertain words, classical quotations, names and the beginning/ending with
visible subtitles.
Transcription success does not establish word accuracy or perfect speech coverage.
Explicitly report silence, unclear speech, unreadable text and missing evidence.
Empty ASR chunks are retained as explicit `audio_gaps`; an empty result does not
prove silence or music. Inspect available subtitles and visuals for those ranges,
and state the unresolved speech coverage. Never describe such a result as a fully
verified spoken-content digest. The helper still prepares visual evidence so a
silent or music-led clip need not be abandoned or repeatedly submitted to ASR.
Blank or null text with absent, null or empty segments is recorded as an audio gap.
Gap entries retain neighboring timed word segments that overlap that chunk;
inspect these before describing the unresolved range. A blank chunk does not
invalidate words independently returned by its neighbor.
Malformed responses and non-empty text without timed segments remain blocking
validation failures. Empty-response tolerance is tested with fixtures; actual
silent-audio provider behavior is not claimed as live-verified.
Cropping is provisional and derived only from the first five seconds. When a crop
is present, inspect full-source frames at the beginning, middle and end before
relying on the cropped sheets, and inspect additional frames if the framing changes.
For text-heavy clips or demonstrations, inspect additional source frames using
normal read-only tools and label any remaining gaps. Do not generate a new
privileged per-Reel helper for ordinary work.

Produce a concise digest covering the progression of the entire argument,
examples, qualifications, conclusions and useful timestamps. Distinguish source
claims, assistant interpretation and Keith's stated reflection. Do not infer
adoption of the source's worldview. Describe coverage as full-audio processing
with one-second visual samples; never claim every-frame viewing.

For persistence, prepare the exact digest in Home's normal writable project:

```text
output/reel-digests/RUN_ID.md
```

This directory is ignored by Git. Never force-add private digests to code delivery.

Include these provenance lines near the top, substituting the actual values:

```text
Source run: RUN_ID
Source manifest: MANIFEST_SHA256
```

Apply AGENTS.md's draft scan when it is a circulation draft. Save via:

```text
/Users/keithmbpm2/.local/libexec/k2b-reel-v1 save-digest RUN_ID MANIFEST_SHA256
```

This writes a content-addressed digest under the verified NAS run. It does not
integrate a vault note. Ordinary knowledge saves retain the note-writer procedure.

## Permissions and recovery

Use the absolute installed commands directly. Saved rules authorize only their
role-specific actions. They do not authorize a Python interpreter alone, an
installer, arbitrary output paths, unknown providers or caller-supplied code.
If the tool nevertheless asks for approval, report no-prompt acceptance as failed;
inspect the actual rule/managed-policy result and stop before changing permission
scope. Never approve a request on Keith's behalf or bypass a rejection.
A blocked or failed installed route stops that task. Switching to the generic
manual capture procedure requires fresh explicit Keith authorization naming that
capture; routine source submission does not authorize such a fallback.

The installed source hash is verified on every run. Package updates need review
and explicit authorized installation. The installer is not a routine command.

`status RUN_ID` verifies NAS publication before returning durable completion
evidence from the owning host. A local preparation awaiting transfer reports
`home_publication_pending`, not completion. Replay
of unchanged source metadata reuses a verified prior capture for up to 24 hours.
After that the helper acquires a new capture. State the capture's
date when reusing; metadata agreement alone does not prove remote media bytes have
not changed. Corrupt evidence blocks reuse. Re-running processing resumes completed
audio chunks and retries a missing NAS publication without re-transcribing them.
Failure remains explicit, with source originals preserved. No later-session retry
or always-on queue is enabled. Mini sleep, expired login or unmounted NAS remain
operational blockers, not prompts to expand permissions.
Completed visual sheets have per-sheet hash checkpoints and resume independently.
Acquisition before its source manifest is committed is deliberately a bounded
whole-stage attempt. A failed stream or encode preserves that incomplete run;
an authorized retry starts a new run and may download the streams again. Byte-range
download resume is outside this version. Two failed acquisition attempts produce
an explicit blocked result, with no further autonomous retry or permission request.
This boundary does not affect verified post-acquisition NAS-transfer replay.
The installed command has an overall ten-minute signal watchdog as well as
per-command and per-copy checks, including NAS reads and publication while its
lock is held. A timeout returns an explicit blocked result and releases the lock.

Corrupted or mismatched evidence is a blocking integrity failure. Inspect the
originals and diagnostics before repair. With Keith's explicit approval naming
the exact run, use `recover-local RUN_ID MANIFEST_SHA256` on the owning host. This
action is deliberately excluded from the saved allow rules. It first verifies
the independent NAS original manifest, then moves local evidence into a private
quarantine without deletion. Re-run the ordinary command to rebuild locally;
rebuilt Home results use a new content-addressed NAS preparation directory so
previous prepared evidence is preserved without blocking the new publication.
Home may need fresh transcription for quarantined responses. If NAS verification
also fails, recovery stops and preserves everything for operator investigation.
This recovery exception does not authorize a manual capture fallback.

## Retention

The NAS is the retained source/result archive. Local captures and processing
copies remain recoverable until Keith explicitly authorizes cleanup for exact
paths. Empty metadata scratch directories are removed by the command. Completed
media, failed partials and NAS evidence are never silently deleted.

Use the installed `inventory` action to inspect exact local run paths, sizes and
dates without permission changes. The inventory also lists preserved NAS partials
when the owning host's exact
mount is verified. An unavailable NAS is reported separately from local results.
If free space approaches the required reserve,
report the inventory and propose exact local directories for operator cleanup.
Before Keith removes any local directory, independently verify its NAS originals,
prepared results and completed digest. Preserve failed or unverified captures.
An explicitly authorized local cleanup may use Finder's recoverable Trash; it is
outside the saved helper permissions. Keep NAS originals unless Keith separately
authorizes their removal. Local receipt loss means future replay may acquire a
new capture rather than pretend the removed local evidence still exists.

This gives an operator recovery path for eventual storage pressure, not a per-Reel
approval or a new cleanup schedule. Report archive growth when storage checks fail.

SJM can coordinate and return a digest during an active task, but transcription
and final NAS processing stay Home-owned in this version. Do not substitute SJM
credentials or copy Home private state. If Home is unavailable, report processing
waiting rather than claim a completed digest.
