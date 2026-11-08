---
name: k2b-video-extract
description: Explain a requested YouTube or Facebook source using native YouTube captions and relevant visual checks, or direct Facebook media, through the supervised Mini and Home.
---

# Video extraction

Read [the workflow](references/workflow.md) before dispatch or processing.
This is the single maintained procedure. Its delivery record and installed
version identify actual acceptance/activation; never infer deployment from code.

Keith explicitly approved content extraction as the default on 8 November 2026.
YouTube uses the actual native caption panel and relevant native visual checks,
retaining a timestamped paraphrase, caption provenance and limited observations.
It does not promise a complete video/audio file, verbatim transcript, complete
speech or every-frame review. Missing captions or complete-media requirements
stop clearly. Facebook uses direct Mini download plus Home processing; a blocked
direct acquisition stops. There is no automatic recording/provider fallback.
The preserved custom recorder and failed media remain experimental/unactivated.
Facebook source duration
is at most 1,200 seconds, with a separate active-operation local-plus-NAS 256 MiB cap.
YouTube duration is at most 3,600 seconds, with the same per-operation scope and a 2 GiB cap. Retained archives are reported separately. A source
can fit the duration limit and fail the byte limit. Never claim a twenty-minute
Facebook guarantee or truncate to fit.

Home validates the independently returned typed NAS bundle and reads its actual
analysis, claims and visual observations before answering. The model selects
relevant scenes and interprets content; code enforces source identity, allowed
hosts, evidence types, hashes, roles and limits. Follow-ups use retained timestamps
and reopen a native source moment when coverage is insufficient.
Keith explicitly deferred SJM processing and activation for this release and
selected Home until a later fix. Preserve SJM's
candidate and failed chunk; Home output is not completed SJM processing. Browser
login and source playback belong to the Mini. No credential transfer, automatic
retry, provider/network change, paid fallback or scheduled capture is enabled.
Saving an ordinary knowledge note is a separate `k2b-vault-writer` action.
