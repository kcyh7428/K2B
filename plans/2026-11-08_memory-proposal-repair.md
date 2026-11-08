# Memory proposal validation repair

Confirmed by Keith on 8 November 2026 in chat
`01a1b97e-ed3f-7dc0-a680-be5e4de40701`: "go ahead", responding to the
explained repair, independent review, SJM activation, held-turn retry,
capture resumption and Home delivery verification.

## Goal and information flow

The SJM memory worker extracts durable meaning from bounded conversation views.
The model identifies a decision and supplies the exact user quote plus the actual
assistant proposal and both event IDs. Code validates those links against retained
source evidence before queuing a private extraction for Home to retrieve.
Home alone reconciles and publishes shared memory.

Today, a Page-state event between an assistant proposal and "go ahead" makes
validation fail. The current hold blocks all subsequent SJM extraction. After
this repair, validation may look past a standalone, recognized Page-state event
to the immediately preceding conversational proposal. Original source bytes,
event identities, transcripts and provenance stay unchanged.

## Confirmed choice and limits

Use the existing validator, with a narrow recognition check for a user-role
`external_codex_apps_open_page` wrapper containing exactly one JSON `page_id`
whose value is null or a nonempty string. Ignore at most one complete standalone
wrapper when finding the proposal. Metadata cannot itself be the acceptance.
Do not filter source evidence or skip genuine
intervening user/assistant messages, malformed metadata, extra fields or prose.
The model still resolves the meaning; code enforces exact attribution and quotes.

A blanket event filter would alter historical source bindings. An operator
override or skipped decision would bypass evidence validation. Neither is used.
No schedule, budget, provider, date window, retry counter or writer authority
changes. Preserve the hold and failed attempt until the exact reviewed repair
passes and the held extraction is queued successfully under exclusive ownership.

Affected activation: SJM only. Keith explicitly answered "Keep activation limited
to SJM" on 8 November in this chat. Home code is not activated by this approval.
Home delivery verification is read-only and must distinguish outbox arrival,
reconciliation and publication. The operator initially inferred a Home mismatch
from its extraction validator. Investigation corrected that inference: native
Home pull calls `accept_memory_envelope` then `automatic_memory.accept_home_envelope`,
which validates the prepared delivery envelope, not SJM extraction receipts.
The exact candidate envelope generated from the held source passed Home's installed
`_validate_envelope` read-only with matching payload hash. No receipt or artifact
schema change is needed. Retained SJM receipt validation remains SJM-local.
Actual Home arrival still requires a matching acknowledgement or Home record.

## Acceptance examples

1. A proposal, standalone recognized Page-state event, and exact "go ahead"
   acceptance validate and retain both evidence identities.
2. Real intervening conversation, malformed/unknown Page-state shapes, extra
   prose, metadata cited as acceptance, multiple wrappers, invented proposal
   links, and missing proposals remain rejected.
3. A recorded extraction with Page-state context survives receipt validation
   and replay without changing source bytes or creating duplicates.
4. On SJM, the actual held 3 November turn queues through the installed helper,
   with the existing failed-attempt evidence retained. Release only that hold
   after the successful retry. The next native run must show actual extraction.
5. Read back the matching delivery/reconciliation evidence on Home. An
   unavailable or rejecting Home remains an explicit delivery gap.

## Implementation and verification

- [ ] Add regression cases and observe the positive case fail on old code.
- [ ] Add the narrow metadata recognizer in `scripts/lib/eod_capture.py`.
- [ ] Run focused source/receipt/capture suites and inspect the actual held input.
- [ ] Stage only this design, validator change and regression tests; obtain Kimi
      approval with OpenAI builder family and no fallback, then commit and push.
- [ ] Apply the reviewed commit to SJM preserving its unrelated local edits.
- [ ] Retry the held extraction under the permanent-chat batch lock, retain
      canonical run evidence, and release the matching hold only on success.
- [ ] Observe continued native capture and verify Home delivery separately.
