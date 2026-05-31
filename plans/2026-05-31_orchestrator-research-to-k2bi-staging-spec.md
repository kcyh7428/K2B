# Orchestrator research -> K2Bi staging + /portfolio visibility -- Checkpoint-1 draft

Status: DRAFT for Keith's review, then Codex Checkpoint-1 BEFORE any build. Scope B (K2B
architecture). Companion to [[feature_k2b-orchestrator-v1]] and `feature_k2b-portfolio-view`.

## Named bug (what this kills)

> The orchestrator gathers clean, link-verified ticker research, then files it in a K2B *scratch*
> folder (`K2B-Vault/raw/orchestrator-results/`) -- not K2Bi's research home
> (`K2Bi-Vault/raw/research/`). So the research is invisible to Keith and to `/portfolio` (which
> reads K2Bi), and it quietly rots. (JOBY `2026-05-30-001` was the live instance: a 6.8 KB clean
> doc nobody could see until a sibling session grepped for it -- a recurrence of the
> "research-capture-file-but-never-deliver" cycle Keith named 2026-04-22.)

JOBY itself is already rescued by hand (`K2Bi-Vault/raw/research/2026-05-31_research_JOBY.md`,
`disposition: awaiting-promotion`). This spec is the SYSTEMIC fix so the next flight lands right.

## Grounding facts (verified 2026-05-31)

- `K2Bi-Vault/raw/research/` is the established research home. Its `index.md` declares the format
  `YYYY-MM-DD_research_<SYMBOL-or-theme>.md` and it ALREADY holds hand-filed Kimi-DR docs
  (`2026-05-04_kimi-dr_genpact-second-wave-beneficiaries.pdf`, the gpt-dr docs). So the artifact
  the orchestrator produces already has a conventional home there.
- Existing research-doc frontmatter convention: `type: research`, `origin`, `symbol`,
  `up: "[[index]]"`, `research_kind`, plus provenance fields.
- The orchestrator currently writes only to `K2B-Vault/raw/orchestrator-results/` (skill body lines
  ~106-115): raw paste + sha256 + clean doc all stay K2B-side.
- `/portfolio` sections today: Awaiting-promotion (reads K2Bi `macro-themes/`), Watchlist, Active
  flights (`status NOT IN done/failed/cancelled` -> excludes finished flights), Theses, Strategies,
  Positions, Closed. A *done* research flight's deliverable appears in NONE of them.

## The fix -- two parts

### Part A (producer): orchestrator finalize writes the clean deliverable to K2Bi research staging

At the orchestrator's `complete`/finalize step for a ticker-research flight (Chat 3; and Chat-1
flights whose entity is a ticker), write the CLEAN, link-verified doc to
`K2Bi-Vault/raw/research/<YYYY-MM-DD>_research_<SYMBOL>.md`, convention-compliant:

- Frontmatter: `type: research`, `origin: k2b-extract`, `symbol: <SYM>`, `up: "[[index]]"`,
  `research_kind: orchestrator-kimi-dr-verified`, `disposition: awaiting-promotion`, plus
  provenance (`provenance_flight`, `source_engine`, `verification`, and plain-path pointers back to
  the K2B raw paste + clean doc -- cross-vault, so plain paths not wikilinks).
- Append a row to `K2Bi-Vault/raw/research/index.md`.
- The RAW paste + sha256 STAY in `K2B-Vault/raw/orchestrator-results/` as the audit trail. Only the
  clean deliverable crosses.

### Part B (consumer): /portfolio surfaces it

Add a section **"🔬 Research gathered -- awaiting your call"** (right after Active flights). Reads
`K2Bi-Vault/raw/research/*.md` where `disposition: awaiting-promotion` AND no
`wiki/watchlist/<SYMBOL>.md` exists. Row: `SYMBOL · research-ready · ⚠ your call: promote or park ·
<doc> · <age>`. Derived from state -> cannot rot.

## The boundary (RESOLVED 2026-05-31 -- ownership-based, not location-based)

Keith's ruling (2026-05-31): the strict "K2B never writes K2Bi" framing does NOT apply here,
because **the orchestrator acts on K2Bi's behalf and this document is K2Bi-owned** (it is K2Bi
investment research, not K2B operational data). Filing a K2Bi-owned artifact into K2Bi's own
staging area is K2B doing its job, not crossing a line. **FULL version chosen** (Part A + Part B).

The real boundary is therefore restated as ownership/pipeline-based, not "any K2Bi write":

> **K2B may write K2Bi-*owned* artifacts into K2Bi *staging* areas (e.g. `raw/research/`). K2B must
> NEVER write K2Bi's *pipeline* (promote a ticker, create/mutate `wiki/watchlist/`, themes, theses,
> strategies, the journal, or any trading state). Pipeline entry is Keith's Stage-3 human gate.**

- The write lands ONLY in `raw/research/` (staging). It NEVER creates `wiki/watchlist/<SYMBOL>.md`,
  a theme, a thesis, or a strategy. No ticker enters the K2Bi analyst pipeline.
- **Promotion stays Keith's Stage-3 human gate.** `disposition: awaiting-promotion` is the signal
  that the research is parked at the door, not through it.
- Standing automated write granted: the orchestrator finalize step may write to
  `K2Bi-Vault/raw/research/` + its index on every qualifying flight. Codex's remaining job is to
  confirm the *mechanism* is safe (convention-compliant, preflighted, no pipeline leakage), not to
  re-litigate whether the write is allowed -- that is decided.

## Disposition lifecycle (derived-from-state, no rot)

- File created -> `disposition: awaiting-promotion` -> shows in /portfolio.
- Keith promotes -> `wiki/watchlist/<SYMBOL>.md` now exists -> /portfolio filter drops it (delivered).
- Keith skips it -> set `disposition: parked` -> /portfolio drops it.
- No separate store, no reminder to close. /portfolio reads reality each run.

## MVP binary test (write before code)

Named bug: "a finished orchestrator ticker-research flight leaves its deliverable in a K2B scratch
folder, invisible to K2Bi and /portfolio."

PASS if all hold:
1. A `done` ticker-research flight produces `K2Bi-Vault/raw/research/<date>_research_<SYMBOL>.md`,
   convention-compliant (frontmatter schema + index row), with `disposition: awaiting-promotion`.
2. NO `wiki/watchlist/<SYMBOL>.md`, theme, thesis, or strategy was created by the finalize step
   (verified absent) -- staging only.
3. `/portfolio` lists that symbol under "🔬 Research gathered -- awaiting your call".
4. Creating a `wiki/watchlist/<SYMBOL>.md` (simulated promotion) makes the row drop from /portfolio.
5. Setting `disposition: parked` also makes the row drop.
6. The raw paste + sha256 remain in `K2B-Vault/raw/orchestrator-results/` (audit trail intact).

FAIL if any pipeline artifact is created, if the doc violates K2Bi convention, or if a delivered/
parked item still shows.

## Open questions for Codex Checkpoint-1

1. Chat-1 (domain/trend) vs Chat-3 (ticker) routing: do domain-trend docs cross to
   `K2Bi-Vault/raw/macro/` (a slug), stay K2B-side, or also go to `raw/research/`? Lean: only
   ticker-entity flights cross; domain trends stay K2B-side until a ticker emerges.
2. Does the producer write need its own preflight (K2Bi vault writable, `raw/research/` + index
   exist, symbol resolvable)? Mirror the narrative preflight pattern?
3. Naming collision: reuse the K2Bi pipeline's `_2`/`_3` auto-version, or date-key is enough?
4. Index ownership: should the K2B orchestrator write the K2Bi `raw/research/index.md` row directly,
   or call a K2Bi-side helper (cleaner ownership, but adds a K2Bi dependency)?
5. ~~Is the standing automated K2Bi-vault write acceptable...~~ **RESOLVED 2026-05-31: FULL version.**
   The write is K2Bi-owned-artifact -> K2Bi-staging, allowed (ownership-based boundary above).
   Codex reviews the mechanism's safety, not the permission.

## Checkpoint-1 disposition (Codex, 2026-05-31 -- NEEDS-ATTENTION, all 4 ACCEPTED)

Codex (plan-scope, ran on Codex via the fixed `task --prompt-file` path) confirmed the permission is
settled and raised 4 mechanism findings -- all accepted; they revise the build, not the decision:

1. **Finalize must be a fail-closed transaction (ACCEPT).** Today `complete` is a plain DB flip to
   `done`. Part A must instead: (a) preflight K2Bi vault + `raw/research/` + index + write-perm +
   resolved symbol FIRST; (b) write doc + index row idempotently; (c) ONLY then mark `done`. On any
   write failure leave the flight `returned`/`blocked` -- never `done` with no visible K2Bi doc
   (that's the named bug returning). Touches `orchestrator_store.py:985` + SKILL.md finalize.
2. **"Delivered" = ACTIVE watchlist status, not file existence (ACCEPT).** `/portfolio` treats only
   `promoted`/`screened` watchlist rows as active. Filtering research on "watchlist file exists"
   would make a NEW research doc for a previously-DROPPED ticker silently vanish. Fix: delivered =
   active-status watchlist entry; add a dropped-watchlist regression test.
3. **Explicit ticker-type metadata, no casing inference (ACCEPT, resolves OQ#1+OQ#3).** Add
   `research_target_type=ticker` + `canonical_symbol` at flight creation, validated against the
   K2Bi canonical registry. Only that class gets Part-A finalize. An uppercase topic ("AI") must
   not be mistaken for a ticker; a suffixed ticker must not be missed. Do NOT infer from casing.
4. **K2Bi owns the staging write; K2B calls it (ACCEPT, resolves OQ#4).** K2Bi already locks
   watchlist-index writes; a raw read/modify/write index append can drop concurrent rows. So add a
   **K2Bi-side research-staging helper** (PR'd into K2Bi `scripts/lib/`, per the two-hat model + the
   ownership principle L-2026-05-31-001) that owns doc naming, `_2`/`_3` collision, index-row
   idempotency (by flight/symbol/date), and locking. K2B's orchestrator CALLS it -- it does not
   hand-append the index. MVP test snapshots the whole K2Bi pipeline surface and asserts the ONLY
   changed paths are `raw/research/<doc>` + `raw/research/index.md`.

**Net effect on build shape:** this is no longer a small K2B-only change. It now spans (a) a **K2Bi
PR** for the staging helper (K2B architect-proposes; K2Bi merges), (b) K2B orchestrator finalize +
flight metadata + preflight, (c) the `/portfolio` section. Re-spec the build split below after the
K2Bi helper's interface is agreed. The ownership principle L-2026-05-31-001 makes this clean: the
K2Bi-owned write logic lives in K2Bi; K2B invokes it.

## Build split (revised post-Checkpoint-1)

1. **K2Bi PR (architect-proposed):** `research_staging` helper -- convention-compliant doc write +
   locked, idempotent index update + `_2`/`_3` collision. Owns the K2Bi-side write entirely.
2. **K2B orchestrator:** flight metadata (`research_target_type`, `canonical_symbol` validated vs
   registry); fail-closed finalize transaction that preflights then calls the K2Bi helper.
3. **K2B `/portfolio`:** the "🔬 Research gathered" section, delivered = active-watchlist-status.

Each lands behind the mandated pre-commit review; the K2Bi PR follows K2Bi's own ship discipline.
