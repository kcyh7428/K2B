# K2B knowledge acceptance

Prepared 26 October 2026. This is an evidence register, not a claim of sustained
acceptance. The current task authorizes development and reviewed deployment of
Tasks 3 and 4. Schedule changes, wider source scope, automatic SQL selection
and background subject-note writing retain their separate activation gates.

## Acceptance boundaries

| Gate | Required evidence | Current state |
| --- | --- | --- |
| Installed runtime | Reviewed commit, both installed heads and file hashes | Home b10979e plus bfd21b0; SJM 061469a plus 4841a91; portable eight-file runtime manifest matched, local parser delta separately recorded below |
| Subject integration | Three real reviewed topics, writer/ledger receipts, replay and counterpart hashes | Three operations, four targets, durable receipts and unchanged replay verified |
| Native source-to-recall | Genuine nonempty scheduled source on each host, extraction, reconciliation, publication and normal recall | Historical partial evidence, final-version chain remains open |
| SJM parser compatibility | A genuine item produced with the installed SJM parser delta passes Home validation, reconciliation, publication and recall | Open Task 5 check; portable runtime equality and isolated tests do not establish this chain |
| Desktop entry | Fresh desktop SessionStart and known-subject recall on each host | Not observed for this final version |
| Seven-day operation | Seven consecutive actual days, nonempty run on both hosts, latency and failure evidence | Not yet elapsed |
| Schedule alignment | Explicit approval, supported update and readback | Proposed only |
| Background integration | Separate exact approval, reviewed narrow writer authority and nonempty pilot evidence | Disabled |

Manual hook execution, CLI tests and synthetic fixtures do not satisfy the
fresh-desktop gate. Registration and a canonical receipt alone do not prove
scheduler origin or source completeness. A manual repair remains a manual
repair even when it uses the same canonical receipt helper.

## Observations before final deployment

The app supplied actual heartbeat turns in the existing permanent chats:

- Home: chat `01a170dd-3f84-7263-abbb-14d29400917e`, completed heartbeat turn
  `01a17589-b277-7853-9311-5058dbdd98b4`, starting 26 October at 00:08:40 UTC.
- SJM: chat `01a170dd-4f95-7911-aa3a-8f2eddcd4280`, completed heartbeat turn
  `01a1759e-6f56-7a91-993b-db210f95aad5`, starting at 00:31:19 UTC.

These establish observed native dispatches before final Task 3/4 activation.
The SJM run was empty, so it is insufficient for nonempty acceptance. Home's
`native-run-receipts/20261026T001406632185Z.json` recorded seven completed
extractions and one retryable validation failure, with publication of eligible
work. It must remain failed historical evidence.

The failed item had an unsupported speaker label, user, instead of keith. An
interactive source review read the complete existing bounded input, corrected
only that label in a new derived reviewed-extraction JSON artifact and checked
the exact source binding through the owning validator. The immutable source
bundle and original reviewed artifact were not rewritten; raw-source,
transcript, cursor and completed-prefix bindings remained identical. The
existing commands then queued, reconciled and published it.
`native-run-receipts/20261026T010254483691Z.json` records one completed item,
zero failures and no budget deferral. This is an interactive operator repair,
not another scheduled dispatch. No source dates, retry counters, schedules,
holds, publication authority or provider defaults were changed.

Private integrity evidence is capture-repair/source-integrity.json under the
Home task3 engineering directory. The original reviewed artifact SHA-256 is
`a731f32aa941977ec41e0ccf3dbc70c09766fec7bbe7c589b09c0e0e4723a71f`;
the derived corrected artifact is
`9904ae682b6f44b5ff2e9d83d3c4409da811e8d14bbd9d2ed2345e6808c7ebe6`.
The source bundle hash is
`2b08487e6a2521e9d1016adb0b624b045ca9b3a63a4caef250629640edf5c33a`.
The only object-level difference is items[0].speaker_source. This is not a
claim that a live session file cannot naturally append later dialogue.

At 01:02:56 UTC, the complete Home file-backed scan reported 192 eligible turns,
148 extracted and 44 pending, with oldest pending source 25 October at
09:44:12 UTC. At 00:56:17 UTC, SJM reported 76 eligible, 75 extracted and one
pending, oldest 26 October at 00:53:36 UTC. These are adapter-specific file
counts. They exclude SQL-only coverage and must not be presented as a deduped
global inventory. The approved window remains 13 September through the current
HKT date, with existing project exclusions.

## Installed manifest scope and local adaptations

The matching portable runtime manifest covers scripts/lib/knowledge_integration.py,
scripts/knowledge-digest.py, scripts/compile-context.py,
scripts/lib/raw_source_status.py, scripts/raw-needs-compile.py,
scripts/minimax-compile.sh and the compile/vault-writer SKILL.md files.
Complete hashes are in delivery/final-home.json and delivery/final-sjm.json in
the Home task3 engineering evidence directory. Home installed head is
`bfd21b083c704f8cf1723f2e87705acc38bf22dc`; SJM installed head is
`4841a9115f4923b9dca5a66e1f486c3d1c5b3fa2`, before the documentation-only
delivery commit. Runtime parent commits are the two IDs in the gate table.

SJM's preserved uncommitted assistant-only-turn parser change is installed and
executed, and is excluded from that eight-file portable manifest. Its
scripts/lib/eod_capture.py SHA-256 is
`74a883fb7346c2f5e0038b9c3e7ed20f8a91171865e18376d0907b9a64cb4eed`;
its additional tests/test_eod_capture.py hash is
`e611a38bb99829a4e5f5e1bfa5c30d1a80e0c6546551158d7bf090e6046cb4d7`.
The local delta was inspected separately, left uncommitted and included in the
1,058-test SJM run. No equality claim is made for that parser file or for the
intentional local AGENTS/hooks adaptations. Home passed 1,057 tests.

The installed-state claim assumes these preserved SJM adaptations remain
present. Private SJM evidence is
`~/.local/state/k2b/engineering/task3-deployment-20261026/preservation-integrity.json`,
with per-file hashes and mode-0600 snapshots of the five preserved files,
the pre-activation tracked.patch hash and installed head. RESTORE.md in that
directory specifies manual recovery: preserve current state, compare the
owning portable base and hashes, inspect the patch, use git apply with its
check option first, and reconcile newer edits and AGENTS/hooks deliberately.
It authorizes no automatic restore, reset, clean or blind rule overwrite.
No restoration was performed. These backups stay on SJM; credentials do not
move with them. The final-version cross-host parser chain remains the named
Task 5 check above, rather than an inferred result of the subject-note tests.
The integrity receipt SHA-256 at verification is
`b60377f92ee397e7f9f46f46bd978487e3c861d9e53958963ce6698f6a68df2a`.

## Cadence and capacity proposal

The current Home job uses minute 8 and SJM minute 29 on their six-hour cadence.
Home can therefore reconcile before the matching SJM extraction has started.
Proposed exact change: move only Home to minute 59, keeping SJM at minute 29,
both existing permanent chats, six-hour cadence, full prompts, holds, model
settings and all turn/byte/time/provider/writer bounds unchanged. Use the
supported automation tool and read back the exact result only after approval.
Do not edit scheduler TOML or databases directly.
The offset changes extraction-start/reconciliation ordering only. Actual
drain, publication, offline recovery and Syncthing transport latency remain
unmeasured until the per-cycle observations below; 30 minutes is not a bound
on end-to-end delivery or a freshness promise.

The nominal ceiling is 48 turns per day per host before byte/time limits. Four
recent Home scheduled receipts completed 12, 10, 12 and seven turns; the second
included one budget deferral and the last included the schema failure above.
Those runs do not establish adequate steady-state capacity. Record incoming
turn counts and sizes, actual completed/deferred work and oldest pending time
per cycle. An item over 24 hours requires an explicit reason and operator action.
Do not promise seven-hour recall while catch-up, capacity or offline conditions
prevent it. Schedule alignment alone will not remove an existing backlog.

## Final-version observation protocol

Record final runtime hashes, deployed heads, notes and snapshot hashes on both
hosts. Bind one genuine source from each host to actual heartbeat metadata,
canonical extraction and reconciliation/publication evidence, then verify its
ordinary recall on both hosts. Retain failed and empty runs as such. Never
invent a conversation, nonempty run or elapsed observation day.

For each of seven actual consecutive days, record source-window/scope, adapter
coverage completeness, incoming sizes, pending count/oldest time, holds,
deferred IDs, completed extractions, publication watermark, counterpart arrival
and recall latency. A changed deployed version restarts observation for the
affected gate. Capture/drain/publication must remain independently operable if
subject integration is held. No observation schedule was created by this file.

Existing focused tests cover replay, partial saves, stale hashes, changed source
versions, real conflict copies, durable-evidence tampering, role boundaries and
writer locks. These are isolated proofs, not live outage or unattended acceptance.

## Background pilot specification, inactive

Only after supervised integration succeeds, a separate approval can authorize a
Home-only pilot: at most five already reviewed records, three existing
allowlisted subject notes and ten minutes per run; initially K2B and
Kingdee/Pulse+ only. Use the signed-in native product and the existing note
writer with newly approved narrow background authority, source-bound private
receipts and no paid fallback. New notes, raw captures, ambiguous corrections,
unsupported assistant reports, policy and shared hubs stay excluded.

The pilot must pass exact-hash replay, contention, partial-failure recovery and
one actual nonempty cycle before expansion. Workers may never change code,
policy, credentials, schedules or their own bounds. This specification does not
activate a worker, schedule, new source scope or background writer permission.
