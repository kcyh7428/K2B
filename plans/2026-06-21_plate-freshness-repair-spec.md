# Plate Freshness Repair Spec

Date: 2026-06-21
Owner: Codex
Repo: `/Users/keithmbpm2/Projects/K2B`

## Objective

Keep `/plate` current after K2B/K2Bi orchestrator ships by fixing the shipped-row reader and adding a stale-status audit for the cross-repo notes that humans actually read next.

## Exact MVP Behavior

1. `/plate` "Recently shipped" reads every recent row in `K2B-Vault/wiki/concepts/index.md` under `## Shipped`, including both inline live rows like `[[feature_x]]` and archived rows like `[[Shipped/feature_x|feature_x]]`.
2. A stale-status audit fails when a feature already listed in the Shipped lane is still named as current/next work in the K2Bi PM Resume Card or the orchestrator full-scope tracker.
3. The current A5 stale state is repaired:
   - K2Bi Resume Card no longer says A5 is current or next.
   - `feature_k2b-orchestrator.md` no longer says A5 is the next separate feature.
4. `/ship` closeout guidance tells agents to run the stale-status audit for K2B/K2Bi/orchestrator ships before claiming the plate is current.

Binary done check: after the fix, a shipped A5 row shows in `/plate`, the stale audit passes on the live vaults, and the K2Bi Resume Card points to a fresh next PM decision instead of the already-shipped A5 gate.

## Source Of Truth

- Shipped status: `K2B-Vault/wiki/concepts/index.md`, `## Shipped`.
- Human next-action summary: `K2Bi-Vault/wiki/planning/index.md`, Resume Card section.
- Orchestrator full-loop summary: `K2B-Vault/wiki/concepts/feature_k2b-orchestrator.md`, "Full scope tracker" and "Where we are now".

## Files And Modules Likely Touched

- `.agents/skills/k2b-plate/scripts/plate.sh`
- `.claude/skills/k2b-plate/scripts/plate.sh`
- `scripts/audit-plate-freshness.py`
- `tests/k2b-plate.test.sh`
- `.agents/skills/k2b-ship/SKILL.md`
- `.claude/skills/k2b-ship/SKILL.md`
- `K2B-Vault/wiki/concepts/feature_k2b-orchestrator.md`
- `K2Bi-Vault/wiki/planning/index.md`
- `K2Bi-Vault/archive/checkpoints/...`

## Tests To Add First

1. Plate shipped parser fixture:
   - Build a temporary vault with a `## Shipped` table containing one inline `[[feature_recent-root]]` row and one archived `[[Shipped/feature_recent-archived|feature_recent-archived]]` row.
   - Run `.agents/skills/k2b-plate/scripts/plate.sh` against the fixture.
   - Assert both shipped rows appear and an old shipped row outside the 7-day window does not.
2. Stale audit fixture:
   - Build temporary K2B/K2Bi vaults where `feature_orchestrator-deploy-gate` is shipped but the K2Bi Resume Card and orchestrator tracker still name A5 as current/next.
   - Assert `scripts/audit-plate-freshness.py` exits non-zero and names both stale files.
   - Replace fixture text with shipped/current wording and assert the audit exits zero.

## Verification Commands

```bash
bash tests/k2b-plate.test.sh
python3 -m py_compile scripts/audit-plate-freshness.py
bash scripts/verify-skills-parity.sh
python3 scripts/audit-plate-freshness.py
bash .agents/skills/k2b-plate/scripts/plate.sh
git diff --check
```

## Explicit Non-Goals

- No live broker mutation.
- No K2Bi production deploy.
- No engine restart.
- No strategy, validator, whitelist, kill-switch, or broker-state change.
- No automatic source edits by `/plate`; it remains a read-only dashboard.
- No attempt to infer arbitrary stale prose with broad NLP. The MVP catches the concrete shipped-feature-current/next drift class that made A5 look stale.
