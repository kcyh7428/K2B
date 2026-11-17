# YouTube native caption export

Date: 17 November 2026. Source chat: 01a1e998-11cd-77a2-9220-edb799de07bb.
Keith explicitly approved trying the Mini's built-in browser transcript export
and updating the workflow if it works. This supplements the 8 November design.

## Behavior and information flow

Try the native Chrome caption panel first. If that panel is inaccessible, the
named Mini worker may use its documented in-app browser
`tab.content.exportYouTubeTranscript()` on the same canonical YouTube video.
The model reads the exported captions, selects relevant source moments and
produces a timestamped paraphrase. Native Chrome still establishes source
identity/duration and provides actual visual observations. The export's own
source identity must match. Code validates the distinct acquisition method,
export digest metadata, row coverage, source identity and existing limits.

Keep the raw export private on the Mini. Publish only metadata, paraphrase,
claims and visual notes through the existing typed NAS channel. A private
export digest records the inspected source bytes; it is not a claim that Home
has independently rehashed the private raw transcript. Home verifies the
published metadata and analysis payloads using the existing NAS reader.

The export does not prove complete speech, caption end times, source accuracy
or full visual viewing. Preserve unknown language/provenance as unknown.
An unavailable exporter or empty, mismatched or incomplete output stops with
the exact reason. No third-party service, recording, audio download, paid
provider, new host or expired-request renewal is introduced. Facebook is
unchanged. SJM remains deferred and untouched.

## Confirmed examples and scope

Keith's confirmation: "then try the other method and see if it works. if it
does, we should update the workflow so it doesn't stop like this next time".
The preceding explanation identified this as the Mini's built-in browser export
after panel failure, with source/timestamp verification and relevant visuals.

Acceptance examples:

1. Video xNwZwrhqqNQ: the inaccessible Chrome panel does not end the attempt;
   the Mini export returns the same source's timestamped captions, inspected
   through first and last returned rows, with actual route and coverage stated.
2. An export for another video, missing coverage, an invalid digest or falsely
   claimed native-panel inspection fails validation.
3. Existing native-panel bundles remain valid. Export publication and Home
   reading invoke no audio/media/provider work.
4. Existing role, hash, mount, deadline and budget refusals remain in effect.

Keith subsequently authorized review, commit and deployment on Home and Mini
in this same chat on 17 November: "go ahead and review, commit and deploy the
workflow update on Home and the Mini so it works next time". The reviewed
versioned packages will include this procedure as hash-bound `workflow.md`.
The installer additionally requires named `youtube_native_browser_export`
acceptance. Preserve unrelated dirty source changes and old installed releases.
Deployment states remain separately reported and SJM remains excluded.
Mini delivery is a reviewed Git archive containing only the installer, the four
runtime source files and this workflow's source path under `.agents/skills`.
It is a temporary installer input, not a restored Mini K2B checkout. Missing
workflow input stops before release directories are created. New packages bind
the workflow hash; historical four-file packages retain their original integrity
checks and remain loadable for historical validation.

## Actual evidence and verification

The Mini's built-in export succeeded for xNwZwrhqqNQ, duration 584 seconds:
249 timestamped English auto-generated rows, first start 1 second, last start
576 seconds. The worker read all exported rows and inspected eight native
visual samples. Caption ends, the final eight seconds and complete speech
remain unverified. The exported file is 11,167 bytes, SHA-256
`bde43fe322860998a239a945b9fc23804da022a613a956a45240f33a5f00ac18`.

Private raw export remains on Mini under request
`ad8d2e69-a826-4322-bd09-5ef1d3f4eead`; its original deadline
1794925993.729 was preserved. Home read and verified the independently returned
analysis and visual-note hashes through the existing SSH route. This is private
exception evidence, not typed NAS publication or runtime deployment.

Home evidence is under
`~/.local/state/k2b/operations/youtube-native-export-20261117/`.
`candidate-validation.json` records successful validation of the actual 249-row,
eight-visual source metadata against the changed source validator, using the
original request/deadline. New export observations must begin within that
original request; validation rejects pre-request and future observations and
does not renew the deadline. Follow-ups reuse the verified original bundle.

The new acceptance test failed first on the old raw-retention/panel-only gate.
A separate actual-timing test failed first on the old observation-before-request
assumption. Both pass after the narrow export-specific changes. The five video
test modules now pass 177 tests, including the packaging and review corrections;
the authority check and whitespace check pass.
Local instruction review identified the timing and generic-schema conflicts;
both were corrected. This is not opposite-family commit approval.

The first opposite-family review returned needs-attention. Its timing lower
bound, historical-package compatibility, missing workflow diagnostic and test
count findings were corrected. A live old-panel read also exposed exact
historical coverage incompatibility; the panel result retains its old field set.
The corrected Home candidate successfully read the original 177-row NAS bundle
`youtube-bc7QaDmLmoA-20261108T104928Z-2b619b3f`, digest
`12af699680b5ad468d84585e6bbf3d5c4c7c49f272a4236c7c5cf37c102548c4`.
Actual typed export draft SHA-256 from Mini is
`0bfb10bdff7256799b608db4c5cf7e20c723ddfae11dbb53a841f159cded674f`.

The second review identified the Home reader's default observation upper bound.
The export reader now bounds observations by actual current time as well as the
original deadline, while allowing later reads of correctly dated expired bundles.
A test first reproduced the missing check, then passed after correction.

The reviewed workflow SHA-256 is
`0ef6fc78e8e862371b2bcde1062a1de62ac3309cb6d92d84f066b8809d8320d2`.
Before each Home or Mini candidate install and activation, the operator must
compare all installer/runtime/workflow input bytes against the reviewed Git
commit, and compare `workflow.md` to this recorded hash. Reject drift before
invoking the installer. The Mini archive's full digest is also checked after
transfer. Record this preflight and candidate-package hashes in the private
deployment receipt. This is an operator gate binding installation to review,
not an assertion that hashing arbitrary working bytes proves they were reviewed.

Prepared and tested: yes. Git commit/push: no. New runtime installation on Home
or Mini: no. SJM contact/change: no. NAS export publication: no, because the
installed packages still implement the earlier panel-only schema.
This is the pre-deployment checkpoint; the following authorized deployment
will record review and per-host acceptance in its delivery evidence.
