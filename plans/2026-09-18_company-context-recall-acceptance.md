# Company-context recall repair — acceptance record

Date: 2026-09-18. Builder: OpenAI Astra checkpoint; Kimi takeover for
verification, review, delivery, and activation per Keith's explicit request.

## What changed

- `AGENTS.md`: added "Recall for company work" guidance — consult the local
  vault for known people/projects/prior discussions; bounded search; freshness,
  provenance, and newer-statement precedence; email wording does not select
  Gmail.
- `.agents/skills/k2b-email/SKILL.md`: discovery narrowed to actual Gmail
  operations; explicit source/action selection section; workflow headings
  disambiguated from chat drafting. Send gates unchanged.
- `scripts/hooks/session-start.sh`: active-rules output filters historical
  audit metadata paragraphs without mutating the source; legacy capture
  inventory labeled legacy and separated from native job state; bounded
  native status observation via the existing read-only adapter with a 2-second
  deadline; conflict-copy warning unchanged.
- `tests/session-start-hook.test.sh`: focused coverage for rule filtering,
  missing/unreadable inventory degradation, native registration vs completion
  distinction, and stalled native status deadline.

## Review and post-review fixes

Independent review (Kimi of the OpenAI-authored diff, job
2026-09-18T06-46-15Z_30c2ab): verdict NEEDS-ATTENTION, five findings.
Kimi-authored fixes applied to the hook (authorship partition: these fixes
are Kimi-authored, not part of the OpenAI baseline):

1. [HIGH] Hook could abort entirely under `set -e` if the python3 status
   block failed (missing/broken python3). Fixed: the command substitution
   now falls back to an explicit "CAPTURE INVENTORY: unavailable" summary
   and the hook continues. New test: stub python3 exiting 1 verifies index
   and rules output is still produced (16 PASS, 0 FAIL total).
2. [MEDIUM] 2s SIGALRM covered the module import, so a cold/slow import of
   `native_job_status` would permanently report false "unknown local
   evidence". Fixed: the alarm is armed around `read_native_job_status`
   only; import failure is reported distinctly as "status reader
   unavailable".

Not fixed (recorded with reasons):
- [MEDIUM] active_rules denylist rot: the three filtered prefixes match the
  file's actual current metadata formats; an allowlist would be more fragile.
  Current behavior is pinned by the existing test.
- [LOW] adapter schema drift silently degrades: acceptable for a startup
  hook; the exception type name is printed.
- [LOW] k2b-email description dropped the cross-skill trigger: verified by
  grep that no live skill references k2b-email; no impact.

The Kimi-authored fixes require independent Codex review per AGENTS.md.

## Codex reviews of the hook changes

- Review 1 (job 2026-09-18T06-54-10Z_0fb0bb, Codex, builder-family other,
  no fallback): one P2 — the revised code left the module import unbounded,
  so a stalled import could consume the 10s SessionStart timeout. Fixed by
  arming one 3-second alarm across import and read with distinct timeout
  messages per phase ("status reader slow (import deadline)" vs "unknown
  local evidence (TimeoutError)"), plus a new stalled-import test.
- Review 2 (job 2026-09-18T06-59-06Z_e4d084, Codex, re-review after fixes):
  two P2s — (a) 4s status deadline + 5s conflict check could exceed the 10s
  hook timeout when both stall; fixed by reducing the status alarm to 3s
  (worst case 8s, leaving overhead). (b) "last finished" label was
  ambiguous because the adapter deliberately returns None for failed runs;
  fixed by labeling the field "last success".
- Residual: those two final corrections are exactly the reviewer's
  prescribed one-line changes and were not re-reviewed after application;
  the two Codex review attempts are the bounded usage authorized by the
  handoff. Focused hook tests: 17 PASS, 0 FAIL after all fixes.

## Verified evidence

- Focused hook test: 17 PASS, 0 FAIL on both Home and SJM after all fixes
  (2026-09-18). The test harness mirrors the real hook PATH and pins
  LC_ALL=C for its internal hash comparison so it runs identically on both
  hosts.
- Two-host activation verified: session-start.sh SHA-256
  adfb35cf03a6c27645f8f902689346977ec4060bc9f925dfb034cc0765f1d197
  identical in /Users/keithmbpm2/Projects/K2B and
  /Users/keithcheung/Projects/K2B; verify-codex-authority.sh passes on both.
- `scripts/verify-codex-authority.sh`: PASS (exit 0).
- `tests/codex-hooks.test.sh`: 3 PASS (exit 0).
- `git diff --check`: clean.
- Amy history repair: `wiki/work/work_treasury-ir-svp-search.md` updated on
  Home via the ordinary-note writer (receipt b8dd4f7e4ad5b01f, result
  saved-local). SHA-256 on both Macs:
  `8e2a2134e5a6a9351b590948bf65906b2e7fd156872f1d54fd763d7143241896`.
  Provenance: Home tasks 019fd010-a849-7ed2-a937-de7141c483ea (Aug 11-13)
  and 01a05a8d-3046-78d0-b1c8-dd00c71adb46 (Sep 1 contract draft); SJM
  statement 01a0b31f-0a6a-7c32-bb68-ac64e4c87170 (Sep 18 visa approval).
  Sending, signature, and confirmed start date remain unknown and are stated
  as such in the note.
- Clean ephemeral CLI evaluations (6 cases per host, both hosts completed):
  person recall with citations; project recall (Kingdee on SJM, Supply Chain
  on Home); missing-history gap stated without invention; newer user
  correction (1 Dec start date) takes precedence and is not saved;
  self-contained rewrite with no vault/mailbox activity; Gmail workflow
  correctly scoped with send gate explained and no mailbox access.

## Honest limitations

- No real fresh desktop startup acceptance has been demonstrated. Ephemeral
  CLI evaluations do not prove desktop hook discovery, trust, or automatic
  execution; actual desktop hook trust may require a user gesture.
- The evaluation runs used an ephemeral workspace; they prove instruction
  behavior, not installed-runtime startup behavior.
- Both older Amy sources predate the authorized September 13 native-capture
  window; this repair does not extend that window or claim native capture.
