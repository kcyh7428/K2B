# K2B assistant alignment, 24 October 2026

Source: Task 4 in the 23 October K2B Anywhere plan, narrowed by Keith's
24 October request to keep the separate phone service deferred.

## Scope and acceptance

Prepare one provider-free startup packet for Codex and Kimi Work, using local
authority, profile, rules, knowledge index and truthful local status. Reuse
the bounded context helper and ordinary note writer. Do not copy a skill tree,
extract Kimi history, publish personal snapshots or change either memory job.
Code and configuration activation on either Mac requires separate delivery.

1. Preserve the existing SessionStart behavior and degradation guarantees.
2. Add a shared bootstrap CLI and source manifest with Home/SJM path handling,
   missing/stale context labels and session capability checks.
3. Add a Kimi Work project entry procedure using the same current AGENTS.md,
   renderer and live skills. Codex remains the default and instruction authority.
4. Verify local startup, bounded project recall and ordinary saves with Home
   unavailable. Keep synthetic evidence outside the real vault.
5. Exercise available fresh assistant sessions and record tool or host gaps.
6. Run focused bootstrap, context, writer, hooks and authority gates, then
   independent Kimi review. Leave a tested uncommitted checkpoint.

## Protected state

Base e04a84a. Implementation is isolated from the dirty Home checkout. Existing
AGENTS.md and research-skill edits there belong to other work and are preserved.
SJM has local parser, parser-test and transcription-skill adaptations. No live
SJM update, credential transfer, schedule change, commit or push is authorized.

## Execution ledger

- Plan accepted for implementation by Keith in this conversation.
- Ruling: personal publishing from the old Task 4 is deferred with the phone
  service because it serves that service and is unnecessary for local parity.
- Baseline: existing SessionStart suite running before implementation.
- Implementation approach: retain the proven read-only startup body and have
  both entry points use one renderer. Keep its shell fallback for broken Python.
- Baseline SessionStart suite passed before edits. Ten new CLI behavior checks
  first failed because the renderer was absent, then passed after implementation.
- Focused bootstrap/context/writer/native-status gate: 426 passed on Python 3.12.
  The additional real-CLI save-to-fresh-reader check passed with a fictional note,
  updated local index and exact source hash; unrelated recruiting evidence absent.
- Existing startup, hook-registration, authority and note-authority checks pass.
  Shell logs retain an existing locale warning; no assertion was weakened.
- Home runtime: current packet loads local sources and marks older profile and
  preference sources stale by modification age. This is advisory source age,
  not a claim that the profile's facts are wrong.
- Live Work UI test is pending: Kimi has an active user draft in the running
  conversation. It was observed read-only and left untouched.
- Fresh Codex fixture behavior test dispatched separately. No actual SJM
  activation or physically disconnected Home acceptance is claimed.

## Review correction record

Kimi review 2026-10-24T13-50-01Z_a06dbf returned NEEDS-ATTENTION with two medium
and four low findings. Transport success was not treated as approval.

- Removed the competing nine-second subprocess deadline. The proven body's
  serial native-status and conflict-scan deadlines remain binding; Codex's
  registered ten-second hook timeout was unchanged at that checkpoint, later
  superseded by the twenty-second contract below. A 9.1-second completed-body
  regression first failed, then passed without discarding the status.
- Helper presence now reports present_unverified without inferring writer
  dependency or session capability from the renderer's interpreter. A runtime
  path is shown explicitly. No automatic dependency install or probe with write
  side effects was added.
- Added EXIT/INT/TERM cleanup for the conflict child and its temporary file.
  The interrupted-scan regression first demonstrated the leaked file, then
  passed. Removing the competing outer kill also removes that orphan source.
- Added a healthy-hook regression proving the renderer actually ran. Keep the
  explicit fallback warning and suppress arbitrary stderr rather than create a
  new startup log or potentially inject private exception text.
- Unknown diagnostic host deliberately stays unknown and cannot select a
  different job through username fallback. The role is now the explicit string
  unknown instead of None. This is a fail-closed choice, not writer authority.
- Added exact body path/hash, native-reader source path/hash, implementation
  root and Python runtime to the manifest. Fixture cross-tree reads are visible
  instead of blending unlabelled implementation and source trees.

Fifteen bootstrap tests pass after this correction, including preserved shell
fallback behavior. The final combined gate and findings-focused Kimi review
are the remaining code gates. Real Kimi Work acceptance is running separately
with Keith's explicit permission in a test project and synthetic vault.

### Final deadline-family correction

Re-review 2026-10-24T14-02-35Z_852a45 exposed a remaining uninstrumented startup
path and asked for the two internal timeouts to be tested together. The body
can block before its Python alarm starts, so its internal deadlines alone are
insufficient for a standalone Kimi Work invocation. A regression observed an
uninstrumented body take thirty seconds before correction.

The final candidate now supervises the body in a dedicated process group with
a twelve-second limit, one second of TERM cleanup and group KILL if needed.
Codex's hook allowance is twenty seconds, preserving margin for that supervisor
instead of allowing the platform to kill it first. The conflict loop polls at
0.1 seconds and uses a templated OS temporary file; allocation failure reports
unknown and preserves the rest of startup. Catchable-signal cleanup is tested.
An external SIGKILL or OS crash cannot run a shell trap, so no absolute cleanup
guarantee or unapproved sweeper is claimed.

Seventeen bootstrap tests pass, including a TERM-resistant process group,
a 9.1-second successful body and simultaneous native-import/conflict timeouts.
The updated hook contract test passes with the twenty-second allowance. This
candidate-only configuration change must be reconciled with SJM's local hook
adaptations during separately authorized delivery.

Actual Kimi Work completed local recall, ordinary save, index verification,
fictional company drafting with the required draft scan, and public retrieval
with FetchURL/WebSearch. A separate fresh Codex session recalled the actual
Work-saved note with matching source and receipt hashes. These are synthetic
Home tests with SJM diagnostic role; the actual SJM helper save/read test also
passed separately. Actual SJM Work and physical Home-off acceptance remain open.

### Bounded output and degraded-checker closure

Review 2026-10-24T14-13-20Z_90f7a5 accepted the supervisor budget but identified
legacy-body output and absent-checker gaps. Four additional regressions first
failed: oversized index/rules injection, missing-checker silence, loss of rules
read before a later interpreter stall, and unbounded legacy status input.

The final body caps index/rules at 65,536 bytes, skips oversized sections with
an explicit warning, and emits completed sections immediately. The supervisor
therefore retains the already-read rules and index when a later phase stalls.
Legacy status rejects oversized/non-regular/symlink sources before parsing.
A missing conflict helper now reports unknown, and dotfile fallback discovery
has a bounded depth.

The fallback scope is explicit: only the registered Codex adapter invokes the
shell fallback, under its twenty-second platform allowance. Kimi Work uses the
supervised Python CLI and reports a missing runtime rather than calling the
internal shell body. No unbounded standalone shell Work entry is supported.
The real conflict helper is a pure-Python os.walk with a two-second internal
budget, twenty-thousand-file bound and no subprocess/TERM override. The outer
supervisor still bounds an unexpectedly stalled or TERM-resistant helper group.
No guarantee is claimed for an external SIGKILL or OS crash.

The final Python gate passes 438 tests, including 21 shared-startup checks.
Home application was dry-run checked using a separate merged AGENTS preview
that preserves the pre-existing provider paragraph. The live checkout was
not edited. The candidate-only patch is not blindly applied over Home's dirty
AGENTS.md. Actual SJM hook adaptations require the same deliberate reconciliation.

### Final low-finding closure

Review 2026-10-24T14-22-19Z_ba3ce5 accepted the architecture, bounded output,
deadline family, unknown states and supported fallback boundary. Three low
findings were closed: the final post-KILL wait is bounded and reports incomplete
cleanup with partial evidence if an OS child cannot yet be reaped; corrupted
NUL-containing profile/index/rules sources are explicitly unavailable; and the
old ten-second ledger statement is marked superseded by twenty seconds.

The NUL/profile and bounded-final-wait regressions first failed, then passed.
Final combined gate: 441 Python tests, including 24 bootstrap cases, plus all
listed hook, authority, writer and skill shell gates. No code has been activated.

### UTF-8 residual closure

Review 2026-10-24T14-30-49Z_98b36b verified those three low-finding closures and
identified invalid UTF-8 as the remaining corrupted-source case. The supervisor
now explicitly decodes with replacement for unexpected diagnostics, while the
body rejects an invalid UTF-8 index/rules section before emission. The native
iconv validator was verified on both Macs; a missing validator is explicit.
Two real-CLI regressions first failed and now preserve the other completed
sections and conflict state without injecting corrupt rules. Final gate:
443 Python tests, including 26 bootstrap cases, and all required shell checks.

One review packet assembly attempt failed before supplying code and is retained
as invalid evidence. The corrected findings-only packet pins before/after hashes,
the exact final diff, affected interfaces and literal gate output. No candidate
runtime file changes after that packet are permitted without invalidating it.

Final review 2026-10-24T14-44-09Z_c3f683 explicitly APPROVE with no findings.
It closes the remaining UTF-8 residual; the preceding review chain accepted
the other correction families. Its optional locale observation is already
covered by the shell body's LC_ALL=C export before byte-length checks.
Implementation state: prepared, tested, reviewed, uncommitted, unpushed and
not activated on either Mac. Real SJM Work and physical Home-off acceptance
remain open for the separately authorized delivery phase.

### Authorized delivery follow-up

Keith authorized commit, activation on both Macs and actual SJM Work/Home-off
acceptance. Staged review 2026-10-24T14-53-15Z_411b63 accepted the runtime but
identified a medium research-skill boundary ambiguity and the planned SJM hook
reconciliation check. The research steps now gate raw captures, asset downloads,
compile, extraction ledgers and retained notebook/video workflows explicitly.
The current SJM AGENTS/hooks were inspected and match Home's portable base;
SJM's additional CapCut commit and dirty parser/transcription work are preserved.
This skill-only correction requires a closure verdict before commit. No core
bootstrap runtime file changed from the previously approved checkpoint.
