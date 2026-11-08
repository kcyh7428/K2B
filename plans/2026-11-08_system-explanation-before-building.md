# Explain system design before building

## Confirmed purpose and scope

On 8 November 2026 Keith requested a K2B communication rule so that requirements
lead to an understandable system design before implementation. He cited Minnie:
he expected a conversational agent with memory and tool calling, but discovered
that question-specific handlers had been built. This records that historical
mismatch, not a claim about Minnie's present implementation.

In the current K2B chat, Keith then explicitly authorized: "get it independently
reviewed, then commit and push it and apply the same rule on both Macs. verify
it's loaded on both."

The affected installations are Home and SJM. Only this rule and this design record
belong in the delivery. Preserve unrelated edits, host settings and credentials.
There is no bot, provider, storage, scheduler or production-system change.

## Design

Add the rule to AGENTS.md, the common instruction authority already read by K2B's
build, development, ship and handoff workflows. Reuse existing startup and delivery
mechanisms. No new skill, hook, service or approval system is needed.

Before implementation, explain user behaviour with examples, components and
information flow, meaningful choices and limitations, and how results will be
checked. For AI, distinguish retrieval, model-selected tools, predefined routing
and deterministic permission or validation controls.

Confirm unexplained material choices and acceptance examples together. Reuse
existing confirmation for routine implementation. Explain and confirm a material
change to the design rather than silently extending it. Honour explicitly scoped
delegation. At delivery, check behaviour and evidence against agreed examples;
a known gap needs explicit acceptance. Existing safety and delivery authority remain.

## Acceptance examples and evidence

- The committed diff contains only the new AGENTS.md rule and this record.
- Independent Kimi review approves the exact staged diff with no fallback.
- The added AGENTS.md section and this plan match byte-for-byte between canonical
  Home and SJM checkouts. Preserve each host's existing local adaptations outside
  that section; a conflicting adaptation blocks installation for reconciliation.
- Each Mac's registered SessionStart command succeeds and reports its actual
  updated AGENTS.md path and full-file hash as available authority.
- Both authority scans pass. Host hook files remain byte-identical to their
  private pre-update recovery copies, and unrelated work remains preserved.
- Report the pushed source commit and each host's installed commit separately.

The user-facing explanation and rollout were explicitly requested in this chat.
The concrete checks above implement that request. Startup source readback verifies
the installed instruction path and bytes. It does not demonstrate future model
compliance or replace instructions already held in an older open chat.

Retain review verdicts, staged diff, recovery copies and per-host startup evidence
under each host's private operations directory for system-explanation-rule-20261108.

## Pre-activation evidence supplied for review

The Home candidate passed `bash scripts/verify-codex-authority.sh` after the earlier
revisions on 8 November: `PASS: Codex live authority is independent of Claude state`.
Repeat that scan on Home's final candidate before review. Run SJM's authority scan
after installing the reviewed rule and before claiming activation verified there.
Both hosts' exact SMB mounts,
aliases and bounded index reads passed, and both legacy Syncthing vault folders were
observed paused. Private pre-update AGENTS.md and hook copies exist on both hosts.
The staged whitespace checks passed. Startup checks on the installed final rule are
post-activation checks, not evidence claimed to have passed before installation.
