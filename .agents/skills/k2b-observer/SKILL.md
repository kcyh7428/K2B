---
name: k2b-observer
description: Dormant preference-analysis lane. Use only when Keith explicitly asks to inspect historical preference signals or run a bounded manual observation pass.
---

# K2B Observer

> **Host boundary:** On SJM, observation is read-only and must not write profiles, signals, candidates, or ledgers. Any explicitly requested synthesis runs on Home.

This lane is disabled by default in Stage 1. There is no lifecycle capture hook, background worker, schedule, automatic candidate surfacing, or automatic promotion. Do not enable one as part of an observation request.

## Authority and ownership

- `AGENTS.md` and `.agents/skills` define live behavior.
- Codex owns the interactive workflow; Kimi may perform a bounded analysis when explicitly requested.
- The home Mac is the only writer for shared vault ledgers and profiles.
- On SJM, perform read-only analysis only. Do not invent a pending profile or ledger mutation; rerun any write on Home.
- Treat historical observer output as evidence, not current runtime health.

## Manual commands

- `/observe profile` — read and summarize the current profile.
- `/observe signals` — report counts and freshness without writing.
- `/observe` — run a bounded manual harvest and synthesis after confirming healthy vault sync and home-writer ownership.
- `/observe reset` — destructive archival operation; requires Keith's explicit confirmation and must run on home.

## Inputs

- `wiki/context/preference-signals.jsonl` — append-only signal ledger.
- `wiki/context/preference-profile.md` — synthesized profile.
- `wiki/context/video-preferences.md` — explicit video feedback.
- `review/` outcomes and revision notes — source-backed behavior evidence.

No source is assumed fresh merely because it exists. Report its newest timestamp and whether it came from a retired background process.

## Manual synthesis contract

1. Verify Syncthing is healthy and the home writer is available before any shared-vault write.
2. Read the signal ledger in two passes: collect `grandfather-cutoff` and terminal `signal-processed` identifiers, then select unprocessed candidates.
3. Require at least three consistent observations before describing a pattern as high confidence.
4. Separate explicit feedback from inferred behavior and cite the underlying files or ledger rows.
5. Write the profile atomically on home with frontmatter containing `tags`, `date`, `type`, and `up`.
6. Present possible learnings to Keith. Never auto-promote a learning or policy rule.
7. Append through the owning helper when one exists; never rewrite an append-only ledger.

The profile should remain concise: evidence window, per-skill observations, general preferences, confidence, and candidate learnings. If evidence is stale or insufficient, say so and make no write.

## Reset safety

Only after explicit confirmation, archive the current signal ledger with a dated name, preserve the profile as historical context, and create a new empty ledger using an atomic move/write. Never delete historical signals.
