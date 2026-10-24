# K2B assistant alignment delivery

24 October 2026, UTC+8.

Local recall, public research, drafting from local context and ordinary note
saves are available through Codex and supervised Kimi Work on both activated
Macs. Open that Mac's local K2B project. Work follows the explicit AGENTS entry
and shared bootstrap; ordinary saves use the existing expected-hash writer.
The separate phone service and its personal cloud export remain deferred.

## Reviewed Git delivery

- Runtime commit: fb87d8f28b70bdd3562561f32529e81ba06a82f0.
- Pushed branch: origin/codex/assistant-alignment-20261024.
- Home runtime branch: codex/assistant-alignment-home-20261024, same commit.
- SJM runtime branch: codex/assistant-alignment-sjm-20261024, commit
  2cfeb2285673b5a1faa2292f0e06054d2bfd577d. It applies the same sixteen reviewed
  file hashes while retaining SJM's additional committed base.
- Subsequent documentation-only delivery commits do not change these runtime
  hashes. The current Git heads identify the final record revisions.

Independent Kimi reviews accepted the runtime and subsequent explicit research
host gates. Final scope closure 2026-10-24T15-01-27Z_03442c is APPROVE with no
findings and no fallback. The earlier staged review findings and failed
transport/test-harness attempts remain in the private review records.

## Concrete acceptance

The Python 3.12 implementation gate passed 443 tests. Actual SJM passed all 27
focused bootstrap and save-to-reader tests after activation. Both actual Macs
pass hook registration, SessionStart, ordinary-note authority, live instruction
authority and skills parity. The SJM note-authority shell gate uses its existing
PyYAML-enabled note-save interpreter; no dependency was installed.

The earlier Home Work UI fixture trial passed all four workflows and fresh
Codex recall of its saved note. Actual SJM Work then independently completed:

- Corrected running-only recall, with current violet distinguished from amber,
  validated/rendered source locators and unrelated records excluded.
- A fictional company email after reading its local source. The exact temporary
  subject/body file was mode 0600; rg exited 1 and trap cleanup removed it.
- Retrieval of the official Kimi Work guide using actual WebSearch and FetchURL.
- The ordinary note save and index update during the physical Home outage.

SJM Work ran in the existing desktop product process through its authenticated
loopback control interface, conversation 05394989-76b1-45e4-b03d-6e57cd5ceb0f.
Its machine-owned credential was used locally. Code CLI was not substituted
for Work. The separate task left existing conversations untouched.

Home Wi-Fi was physically confirmed Off at 23:37:29.276, and restoration began
at 23:38:14.287. Before the outage SJM reached Home through its existing SSH
configuration; during it two independent probes failed. Work saved the note
between 23:37:31.950 and 23:37:32.026. A fresh signed-in native Codex session
gathered/read it between 23:37:32.122 and 23:37:32.190. Both operations fall
strictly inside the measured outage. Wi-Fi restored automatically and its
independent restoration guard was cancelled after On was confirmed.

The fresh reader was not given the decision value. It correctly identified
cobalt as current, superseding violet and amber, with source locators at lines
23, 27 and 31 to 32. The 1,107-byte source, prepared draft, private recovery
backup, receipt and reader agree on SHA256:

`b15c0b4230fda3b0248ddd79f1b8daf80256627c42098e85be04be802e370983`

Receipt: 5e8bed75b23a3ca0, saved-local, synchronization unverified. The writer
updated the folder row and master count without hand-editing indexes.

## Preserved state and boundaries

Home's existing AGENTS/provider and research/finance edits were deliberately
merged back as uncommitted changes. Their merged bytes and all 893 pre-existing
untracked files were hash-verified. SJM's dirty transcription skill, parser and
parser tests remain byte-identical. Its AGENTS/hooks were saved and compared
before delivery; the actual portable-base comparison showed no current hook
divergence, and the twenty-second hook contract passes there.

Home had a pre-existing ignored retired project .claude tree. Its nine objects
were inventoried, copied with types/modes preserved, hash-verified and moved
outside the live checkout. Both original and verified copy remain in the private
activation archive. Personal application history was not accessed.

The source and helper checks use a disposable synthetic vault, outside Syncthing.
They prove local saving/recall while Home is unavailable, not counterpart arrival.
Missing fixture policy/lifecycle/template inputs remain explicitly reported.
The real profile and preference sources are labelled old by modification age;
this is not a factual-inaccuracy finding. A manual fresh CLI bootstrap check
does not claim observation of an automatic desktop SessionStart event.

Both Syncthing vault folders were idle with zero needed files/bytes and no errors
after reconnection. Shared-memory reconciliation stays Home-owned. Kimi history
is not automatically extracted, and neither native memory job was changed.

## Durable evidence

Home evidence root:
`/Users/keithmbpm2/.local/state/k2b/activation/assistant-alignment-20261024/`

Key files: physical-home-off.json, home-off-acceptance-verified.json,
reviewed-file-sha256.json, home-fresh-codex/result.md, home-before/ and
retired-project-archive/.

SJM evidence root:
`/Users/keithcheung/.local/state/k2b/activation/assistant-alignment-20261024/`

Key files under work-acceptance/: acceptance-prepared.md, acceptance-offline.md,
writer-execution.json, writer-result.json, writer-source-check.json,
fixture-writer-state/, and fresh-codex-offline-final2/packet.json,
reader-source-check.json and result.md. Earlier timeout and harness-failure
records are retained separately and are not counted as successful trials.
