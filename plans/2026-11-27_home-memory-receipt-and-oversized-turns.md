# Home memory receipt and oversized turns

Keith approved both Home fixes on 27 November 2026 in Codex thread
`01a2030e-0540-7f53-8fff-9110f3557ab2`. Keep both extraction holds in place.
Home remains the shared publisher. SJM and the Mac mini receive no changes.

The Home test published to the NAS, but the run recorder refused the valid
240,477-byte receipt at its 64 KiB read limit. Verify the saved publication
receipt with a larger finite limit specific to this evidence. Keep the existing
receipt format, identity, binding and time checks. Verify valid and invalid
large receipts and a held run that records verified publication as part of a
blocked outcome.

Home's scheduled worklist measures the complete current turn before selecting
it. If that required input exceeds 48,000 bytes, keep a private review record
bound to the source. Leave the turn pending without an input view, extraction
attempt or receipt. Count it in the worklist's attention summary and consider
the next turn within the existing scan and batch budgets. Reuse an exact
review record for the same source and input limit. Measure again and refresh
an outdated readable record. A readable record is retired once the turn fits
or has a valid extraction receipt. An unreadable review record stays in place
and is reported separately without stopping selection of other turns.
A worklist counts review results encountered up to its selection or time
boundary. The read-only backlog reports the broader pending-turn count.
A worklist with only flagged turns still reports attention. Keep the hold
gate and the worker's independent input check. The scheduled prompt already
uses the worklist that performs this check.

Before reporting readiness, run isolated tests covering an oversized first turn
followed by eligible work, retries, an existing hold and the SJM selection
path. Verify both fixes on the Home checkout used by the registered heartbeat.
The later scheduled acceptance test requires explicit release of Home's hold.
Verify one pending turn, the completed run receipt, a newer last-success time,
NAS hash readback and a source-backed recall. SJM stays held.
