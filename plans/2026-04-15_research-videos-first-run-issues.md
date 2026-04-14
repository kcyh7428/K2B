---
name: research-videos-first-run-issues
date: 2026-04-15
type: issue-log
related: [feature_research-videos-notebooklm, k2b-research]
---

# /research videos — first-run issues log (2026-04-15)

First live run of the v2 K2B-as-curator pipeline against
`"Claude agent skills production 2026"`. The run completed end-to-end and
produced 4 picks + 20 rejects that cleared the schema gate and landed in
K2B Watch + the review note + the run record. But several pipeline errors
had to be worked around inline. None of them are yet fixed in the committed
skill; each needs a real fix before the weekly `/schedule` can run
unattended.

**Severity legend:**

- **BLOCKER** — unattended run will fail silently or produce wrong output
- **HIGH** — unattended run will fail loudly but recoverably
- **MEDIUM** — unattended run degrades but completes
- **LOW** — cosmetic or operator-only issue

---

## BLOCKER-1 — yt-search.py progress line corrupts `$CANDIDATES` JSON

**Where:** `k2b-research/SKILL.md` Step 1 (yt-search candidate discovery) +
`scripts/yt-search.py`

**What happened:** Step 1 writes yt-search output straight to
`$CANDIDATES`:

```bash
python3 ~/Projects/K2B/scripts/yt-search.py "$QUERY" --count 25 --months 1 --json > "$CANDIDATES"
COUNT=$(jq '.count' "$CANDIDATES")
```

yt-search.py **prints a progress line before the JSON** even in `--json`
mode:

```
Searching YouTube for: "Claude agent skills production 2026" (top 25 results, last 1 months)...

{
  "query": "Claude agent skills production 2026",
  "count": 25,
  ...
}
```

The subsequent `jq '.count' "$CANDIDATES"` fails with
`parse error: Invalid numeric literal at line 1, column 10` and `COUNT`
becomes empty.

**Why this is a BLOCKER (not just HIGH):** the zero-candidate guard in
Step 1 compares `"$COUNT" == "0"`. Empty string is NOT equal to "0", so
the guard passes and the pipeline **proceeds to notebook creation** even
though `$CANDIDATES` is unparseable. Every downstream `jq` call on
`$CANDIDATES` then fails silently, leading to an empty
`SOURCE_IDS` array, which means zero sources added, which means the
ready-count threshold fails, which aborts — but only after a notebook was
created (cost) and the real failure reason (progress-line pollution) is
invisible in the run record.

**Fix options:**

1. **Fix the script**: make `yt-search.py --json` print JSON and nothing
   else. Progress line goes to stderr or disappears entirely in `--json`
   mode. This is the right long-term fix.
2. **Fix the skill**: change Step 1 to strip the progress line via
   `python3 ... --json 2>/dev/null | tail -n +3 > "$CANDIDATES"` or
   route through a Python helper that extracts the JSON portion.
   Brittle because it assumes a fixed header line count.
3. **Best**: do (1) AND tighten the zero-candidate guard to also fail
   loudly when `jq '.count'` returns empty or non-numeric.

**Fixed inline during run:** yes, via a Python helper that extracted the
JSON portion. The skill is still broken as written.

---

## HIGH-1 — NBLM JSON parse fails on literal newlines inside string values

**Where:** `k2b-research/SKILL.md` Step 6a (defensive NBLM parse)

**What happened:** Step 6a says the defensive parse does a citation-marker
strip and URL rejoin, then `json.loads`. In practice NBLM wraps long
string values (`what_it_covers`) with literal newlines inside the string
literals:

```json
{
  "what_it_covers": "The video explains what Claude Code skills are, defining 
them as text prompts that tell the AI how to perform specific tasks..."
}
```

Raw `json.loads()` rejects this with
`Invalid control character at: line 7 column 81`.

**Fix:** Step 6a needs to collapse raw `\n` inside unescaped string
literals before parsing. I used a character-walker that tracks `in_string`
and replaces `\n` with space when inside a string:

```python
out = []
in_string = False
escaped = False
for ch in json_text:
    if escaped: out.append(ch); escaped = False; continue
    if ch == "\\": out.append(ch); escaped = True; continue
    if ch == '"': in_string = not in_string; out.append(ch); continue
    if in_string and ch == "\n": out.append(" "); continue
    out.append(ch)
```

**Severity:** HIGH (not BLOCKER) because the skill's retry-with-stricter-
prompt path would probably trip the same bug on retry, but the partial
run record gets written correctly on final failure, so the audit trail is
preserved.

**Fixed inline during run:** yes, in `/tmp/k2b-parse-nblm.py`. The skill
is still broken as written.

---

## HIGH-2 — Citation-marker regex doesn't match dash ranges

**Where:** `k2b-research/SKILL.md` Step 6a

**What happened:** The skill's defensive parse says to strip
`r'\s*\[\d+(?:,\s*\d+)*\]'`. NBLM returned citation markers like
`[1-4]`, `[5-8]`, `[9-12]`, `[13-16]`, `[17-20]` — dash ranges, not
comma lists. The skill's regex doesn't match those, so the markers
survive into `json.loads` and cause syntax errors.

**Fix:** broaden the regex to cover ranges:
`r'\s*\[\d+(?:[-,\s]\d+)*\]'`

**Severity:** HIGH because it's a silent pre-condition of HIGH-1 — even
if the newline walker is fixed, unstripped citation markers still break
the parse.

**Fixed inline during run:** yes, in `/tmp/k2b-parse-nblm.py`. The skill
is still broken as written.

---

## HIGH-3 — 8 of 25 `source wait` failures with no retry, no backoff

**Where:** `k2b-research/SKILL.md` Step 3 (parallel `source wait` loop)

**What happened:** After `notebooklm source add` returned 25 source IDs
cleanly, the parallel `source wait` loop with `--timeout 600` ended with:

- `ready`: 17
- `fail` (empty status string, i.e. timeout or non-ready): 8

The 8 failures had no retry, no backoff, no indication whether they were
transient (worth retrying) or permanent (private/transcription-blocked).
Because the ≥5 threshold was met, the run proceeded with 17. But for an
unattended weekly run on a potentially flakier network, losing 32% of
sources without any retry is a recipe for silent degradation.

This is the issue that was previously tracked in the now-deleted
`plans/2026-04-14_phaseB-blockers.md` item 2 — "Add a per-URL retry with
2s backoff to the skill's bulk add loop before B11's weekly schedule goes
live."

**Fix (minimum viable):** in Step 3's per-source child, on fail, sleep
`2s` and retry once. On second fail, record the final status (with
reason: timeout / not-found / etc.) so the run record can categorize.

**Fix (better):** exponential backoff with 3 attempts (2s, 4s, 8s).

**Fix (best):** track source status via `notebooklm source list --json`
and distinguish transient indexing delays from permanent failures (e.g.
"Private video, no transcript available") — only retry transient ones.

**Fixed inline during run:** no. The 8 failures were tolerated because
the threshold was met.

---

## HIGH-4 — NBLM described sources that weren't in the `ready` set

**Where:** `k2b-research/SKILL.md` Step 3 + Step 6a (rejoin)

**What happened:** Source wait returned `ready_count = 17` but NBLM's
`ask` returned 24 descriptions (one source was missing from the NBLM
response entirely, but 7 descriptions corresponded to sources that my
`source wait` considered `fail`). This could mean:

1. `source wait` timed out waiting on sources that DID finish indexing
   in the background just after the timeout, so NBLM could see them
2. OR NBLM's internal notebook state is more forgiving than `source
   wait`'s status field
3. OR the ready-count tracking has a race between parallel children
   writing to `$READY_LOG`

**Why this matters:** if the ≥5 ready threshold is using a stale/low
ready count, it might abort perfectly-good runs unnecessarily. Inversely,
the rejoin logic would happily process whatever NBLM returns regardless
of its `ready/fail` classification — so the threshold is advisory only.

**Fix:** re-check `source list --json` after `source wait` has returned,
and re-count ready sources from the canonical state rather than the
timeout-races-aware `$READY_LOG`. Or document explicitly that the
threshold is a lower bound and the actual indexed count may be higher.

**Severity:** HIGH because the threshold inaccuracy could abort runs that
should have succeeded.

**Fixed inline during run:** no, just observed. The rejoin handled 24
entries gracefully.

---

## MEDIUM-1 — Telegram notification silently fails on MacBook

**Where:** `k2b-research/SKILL.md` Step 10 + `scripts/send-telegram.sh`

**What happened:** `send-telegram.sh` aborted with
`K2B_BOT_TOKEN / TELEGRAM_BOT_TOKEN env var not set`. The token is
configured on the Mac Mini for scheduled runs but not in MacBook
interactive sessions. The skill's Prerequisites line says
"`K2B_BOT_TOKEN` is set in env" but doesn't distinguish environments
or provide a graceful fallback.

**Why MEDIUM (not BLOCKER):** the run continued, playlist adds + review
note + run record all completed correctly, just no Telegram ping. For
the scheduled Mini run this works; for manual MacBook test runs the lack
of notification is annoying but not destructive.

**Fix options:**

1. Load the token from `~/.k2b/secrets` or a `.env` file if not in env
2. Detect "running on MacBook" vs "running on Mini" and skip Telegram
   with a visible warning on MacBook
3. Print the Telegram message to stdout so Keith can read it locally
   when the bot token is missing

**Fixed inline during run:** no, just observed. Picks are already in
the playlist so Keith sees them on youtube.com.

---

## MEDIUM-3 — `flock` unavailable on macOS (BSD)

**Where:** `k2b-review/SKILL.md` Video feedback Concurrency and atomicity section + `CLAUDE.md` Video Feedback via Telegram rule

**What happened:** The skill says to acquire a file lock via:

```bash
exec 9>/tmp/k2b-review-videos.lock
flock -x 9
```

This fails on macOS with `command not found: flock` because `flock(1)` is a Linux-only utility. macOS BSD has `lockf(1)` (different API) but not `flock(1)`.

**Why it matters:** The MacBook is a valid run environment for `/review` and the CLAUDE.md Telegram feedback path — both use the same lock. If `flock` silently fails (bash continues past a missing command in non-strict mode), the concurrency invariant the skill depends on is gone and `video-preferences.md` could get corrupted if two sessions race.

**Fix options:**

1. **Replace `flock` with `python3 -c "import fcntl; fcntl.flock(...)"`** — `fcntl` is cross-platform, works on both macOS and Linux. This is what I used in the live run via a helper script.
2. **Install util-linux via brew** (`brew install util-linux`) to get a `flock` binary on macOS. Requires a new dependency on every MacBook.
3. **Use `mkdir` for atomic locking** (classic Unix pattern): `mkdir /tmp/k2b-review-videos.lock` succeeds only if the directory doesn't exist. Portable across all Unixes.

**Recommended:** Option 1 — inline the lock acquisition via Python `fcntl.flock` since the skill already uses Python helpers elsewhere (YAML parsing, atomic write-rename), and it's the most portable.

**Severity:** MEDIUM — the single-session run tonight had no race condition so nothing corrupted, but on a multi-session / Telegram-racing workload the failure would be silent and destructive.

**Fixed inline during run:** yes, via `/tmp/k2b-review-apply.py` using `fcntl.flock`. The skill still says `flock -x 9` which is broken on macOS.

---

## MEDIUM-2 — `notebooklm delete` syntax gotcha

**Where:** `k2b-research/SKILL.md` Step 11

**What happened:** Correct syntax is `notebooklm delete -n <id> -y`. I
first tried `notebooklm delete "$NB_ID" -y` as a positional argument,
which fails with `Got unexpected extra argument`. The skill already has
the correct syntax in Step 11, so this was operator error on my part,
but it's a footgun — `notebooklm create` and `notebooklm use` take the
ID positionally but `delete` requires `-n`.

**Severity:** MEDIUM for the operator. The skill is correct as written.

**Fixed inline during run:** yes, I retried with the `-n` flag.

---

## LOW-1 — NBLM returns synthetic placeholder URLs

**Where:** NBLM's response to Step 5 ask prompt

**What happened:** NBLM returned `url` values like
`https://www.youtube.com/watch?v=ChaseAI_Skills` and
`https://www.youtube.com/watch?v=VaibhavSisinty_Skills2` instead of the
real video IDs. Expected behavior, already documented in the skill, and
the title-rejoin in Step 6a handled it correctly. Not a new bug, just
worth noting it reproduces reliably.

**Severity:** LOW — skill handles it.

**Fixed inline during run:** n/a, the title-rejoin worked as designed.

---

## LOW-2 — Duration field occasionally "unknown" in NBLM output

**Where:** NBLM's response to Step 5

**What happened:** Several entries had `"duration": "unknown"` in the
NBLM output even though `$CANDIDATES` had the real duration. Handled by
the rejoin (which pulls `real_duration` from yt-search, not from NBLM).

**Severity:** LOW — skill handles it.

---

## Summary table

| ID | Severity | Where | Fix status |
|----|----------|-------|------------|
| BLOCKER-1 | BLOCKER | yt-search.py + Step 1 | not fixed in skill |
| HIGH-1 | HIGH | Step 6a defensive parse | not fixed in skill |
| HIGH-2 | HIGH | Step 6a citation regex | not fixed in skill |
| HIGH-3 | HIGH | Step 3 source wait retry | not fixed in skill |
| HIGH-4 | HIGH | Step 3 ready-count accuracy | not fixed in skill |
| MEDIUM-1 | MEDIUM | Step 10 Telegram | not fixed in skill |
| MEDIUM-2 | MEDIUM | Step 11 syntax gotcha | skill is correct, operator error |
| MEDIUM-3 | MEDIUM | k2b-review flock on macOS | not fixed in skill |
| LOW-1 | LOW | NBLM synthetic URLs | handled by rejoin |
| LOW-2 | LOW | NBLM unknown duration | handled by rejoin |

## Next actions

1. **Do NOT fix anything yet** — Keith is reviewing the 4 picks tomorrow
   and will provide feedback. Fixing the skill mid-review would change
   the behavior between runs and muddy the signal.
2. **After Keith's feedback:** address BLOCKER-1 + HIGH-1,2,3,4 in a
   single commit with Codex review. The schedule cannot go live until
   at least BLOCKER-1 and HIGH-3 are fixed.
3. **MEDIUM-1 (Telegram)** can be deferred until Keith starts running
   tests from MacBook more frequently — it's not on the scheduled path.
4. **The v2 curator refactor itself is validated** — the rubric picked
   correctly, the preference tail vetoes landed, the recency veto was
   unused (all candidates were <30 days so no test of the 180-day cutoff
   yet), the schema gate caught nothing because the K2B output was
   well-formed, the playlist moves worked, the run note + run record
   were written via atomic rename. The logic is good. What needs
   hardening is the input-parsing resilience and the source-wait retry.
