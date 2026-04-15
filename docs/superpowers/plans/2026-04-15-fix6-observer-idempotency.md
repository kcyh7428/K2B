# K2B Audit Fix #6 — Observer Idempotency Marker

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Stop double-processing observer findings between session-start inline confirmation and `/observe` deep synthesis. Every signal gets a content-hash id; every action writes a `signal-processed` line; both readers filter out processed signals before acting.

**Architecture:** Append-only jsonl is preserved. New signals written by `scripts/observer-loop.sh` get a `signal_id` (sha256 of date + source + description + time-hh-mm-ss, first 8 hex chars -- time component prevents rerun collisions). Actions taken against a signal (confirmed/rejected/watching) append a **new** line of `type:"signal-processed"` via a thin helper `scripts/observer-mark-processed.sh`. Both the session-start inline flow and `/observe` deep synthesis stream the jsonl, bucket by `signal_id`, and filter out any signal that has a matching `signal-processed` line with `action: confirmed` or `action: rejected`. `action: watching` is tracked but does NOT filter the signal out -- deferred signals resurface next session. Existing historical signals are grandfathered via a one-shot cutoff marker APPENDED at the end of the current file (preserving append-only semantics) -- no backfill.

**Tech stack:** Bash, `jq`, `sha256sum`, flock/mkdir lock (Fix #1 pattern).

**Spec source:** `docs/superpowers/specs/2026-04-15-k2b-audit-fixes-design.md` Fix #6 (lines 296–375).

**Dependencies:**
- **Fix #1 helper pattern** (lock fallback) is the model; this fix duplicates the pattern but does NOT reuse the same lockfile.
- No hard dependency on any other fix — can land in parallel once Fix #1 exists as a reference.

---

## File Structure

**Create:**
- `scripts/observer-mark-processed.sh` — the single writer of `signal-processed` lines (~40 lines).
- `tests/observer-mark-processed.test.sh` — smoke + concurrency test (~50 lines).

**Modify:**
- `scripts/observer-loop.sh` — add `signal_id` (date + source + description + HH:MM:SS, sha256 first 8 hex) to every new signal written via the existing `jq -nc` expression.
- `.claude/skills/k2b-observer/SKILL.md` — add "Phase 1a: filter processed signals" + wire the post-action mark to the helper.
- `~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl` — **append** (not prepend) the grandfather-cutoff line. Reader walks top-to-bottom tracking a `past_cutoff` boolean; signals before cutoff are treated as already processed; signals after cutoff require `signal_id` dedup.

---

## Task 1: Verify current observer-loop signal schema

- [ ] **Step 1.1: Read `scripts/observer-loop.sh`**

```bash
cat scripts/observer-loop.sh
```

Record:
- The exact line(s) that append to `preference-signals.jsonl`.
- The current JSON fields written per signal.
- Whether `signal_id` is already present (spec says no).

- [ ] **Step 1.2: Read the current jsonl (a handful of lines)**

```bash
tail -20 ~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl 2>/dev/null || echo "not found"
```

Record the field set (likely: `type`, `date`, `source`, `description`, maybe others). Confirm no `signal_id` appears on existing lines.

- [ ] **Step 1.3: Read `k2b-observer/SKILL.md` Phase 1 / 1a region**

```bash
grep -n 'Phase\|signal\|preference-signals' .claude/skills/k2b-observer/SKILL.md
```

Record the line number where Phase 1 starts and whether a "filter" step already exists.

---

## Task 2: Grandfather existing signals

**Files:**
- Modify: `~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl` — **append** a cutoff marker line at the end (preserves append-only semantics; no mutation of prior signals).

- [ ] **Step 2.1: Append the cutoff line**

The append-only file must remain append-only. The cutoff marker goes at the END of the current file; the Phase 1a reader walks top-to-bottom and tracks a `past_cutoff` boolean. Signals WRITTEN BEFORE the cutoff line are implicitly grandfathered (never surfaced for action). Signals WRITTEN AFTER the cutoff line carry `signal_id` and are subject to dedup against `signal-processed` lines.

```bash
JSONL=~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl
if [ -f "$JSONL" ]; then
  CUTOFF='{"type":"grandfather-cutoff","at":"2026-04-15","note":"signals WRITTEN BEFORE this line are grandfathered (treat as processed); signals AFTER this line are subject to signal_id dedup"}'
  printf '%s\n' "$CUTOFF" >> "$JSONL"
  echo "appended cutoff to $JSONL"
else
  echo "no jsonl yet -- cutoff unneeded"
fi
```

- [ ] **Step 2.2: Verify the file still parses as valid jsonl**

```bash
python3 - <<'PY'
import json
for i, line in enumerate(open("/Users/keithmbpm2/Projects/K2B-Vault/wiki/context/preference-signals.jsonl")):
    line = line.strip()
    if not line:
        continue
    try:
        json.loads(line)
    except Exception as e:
        raise SystemExit(f"line {i+1} malformed: {e}\n{line}")
print("ok")
PY
```

- [ ] **Step 2.3: Commit in vault repo if versioned**

The K2B vault is not a git repo; this step is a no-op. Skip if `~/Projects/K2B-Vault/.git` does not exist.

```bash
if [ -d ~/Projects/K2B-Vault/.git ]; then
  cd ~/Projects/K2B-Vault
  git add wiki/context/preference-signals.jsonl
  git commit -m "chore(observer): append grandfather-cutoff marker for Fix #6 idempotency"
fi
```

---

## Task 3: Write `scripts/observer-mark-processed.sh`

**Files:**
- Create: `scripts/observer-mark-processed.sh`

- [ ] **Step 3.1: Write the script**

```bash
#!/usr/bin/env bash
# scripts/observer-mark-processed.sh
# Append a signal-processed line to preference-signals.jsonl under a lock.
#
# Usage: observer-mark-processed.sh <signal_id> <action> [learn_id]
#   signal_id:  8-hex content hash from the signal being acted on
#   action:     confirmed | rejected | watching
#   learn_id:   optional L-ID when the action produced a learning

set -euo pipefail

SIG="${1:?observer-mark-processed: signal_id required}"
ACTION="${2:?observer-mark-processed: action required}"
LEARN="${3:-}"

case "$ACTION" in
  confirmed|rejected|watching) ;;
  *) echo "observer-mark-processed: action must be one of confirmed|rejected|watching" >&2; exit 2 ;;
esac

JSONL="${K2B_PREFERENCE_SIGNALS:-$HOME/Projects/K2B-Vault/wiki/context/preference-signals.jsonl}"
LOCK="${K2B_PREFERENCE_SIGNALS_LOCK:-/tmp/k2b-preference-signals.lock}"
TS="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

if [ ! -f "$JSONL" ]; then
  echo "observer-mark-processed: jsonl not found: $JSONL" >&2
  exit 3
fi

# Build the JSON line.
if [ -n "$LEARN" ]; then
  LINE=$(printf '{"type":"signal-processed","signal_id":"%s","at":"%s","by":"session-start-inline","action":"%s","learn_id":"%s"}' \
    "$SIG" "$TS" "$ACTION" "$LEARN")
else
  LINE=$(printf '{"type":"signal-processed","signal_id":"%s","at":"%s","by":"session-start-inline","action":"%s"}' \
    "$SIG" "$TS" "$ACTION")
fi

# Acquire lock (same pattern as Fix #1 wiki-log-append).
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK"
  flock -x 9
  printf '%s\n' "$LINE" >> "$JSONL"
else
  LOCK_DIR="${LOCK}.d"
  TRIES=0
  while ! mkdir "$LOCK_DIR" 2>/dev/null; do
    TRIES=$((TRIES + 1))
    if [ "$TRIES" -gt 200 ]; then
      echo "observer-mark-processed: could not acquire $LOCK_DIR after 10s" >&2
      exit 4
    fi
    sleep 0.05
  done
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
  printf '%s\n' "$LINE" >> "$JSONL"
fi

echo "observer-mark-processed: marked $SIG as $ACTION"
```

- [ ] **Step 3.2: Syntax check + chmod**

```bash
bash -n scripts/observer-mark-processed.sh
chmod 755 scripts/observer-mark-processed.sh
```

- [ ] **Step 3.3: Commit**

```bash
git add scripts/observer-mark-processed.sh
git commit -m "feat(scripts): observer-mark-processed.sh signal dedup helper (Fix #6)"
```

---

## Task 4: Write tests

**Files:**
- Create: `tests/observer-mark-processed.test.sh`

- [ ] **Step 4.1: Write the test**

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/scripts/observer-mark-processed.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP" /tmp/k2b-preference-signals-test.lock.d 2>/dev/null || true' EXIT

JSONL="$TMP/signals.jsonl"
touch "$JSONL"

export K2B_PREFERENCE_SIGNALS="$JSONL"
export K2B_PREFERENCE_SIGNALS_LOCK=/tmp/k2b-preference-signals-test.lock

fail() { echo "FAIL: $*" >&2; exit 1; }

# --- Test 1: happy path without learn_id ---
"$HELPER" a3f7b2c1 confirmed
LAST=$(tail -1 "$JSONL")
echo "$LAST" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["signal_id"]=="a3f7b2c1" and d["action"]=="confirmed" and "learn_id" not in d' \
  || fail "line 1 schema mismatch: $LAST"

# --- Test 2: happy path with learn_id ---
"$HELPER" b4e8c3d2 rejected L-2026-04-15-003
LAST=$(tail -1 "$JSONL")
echo "$LAST" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert d["signal_id"]=="b4e8c3d2" and d["action"]=="rejected" and d["learn_id"]=="L-2026-04-15-003"' \
  || fail "line 2 schema mismatch: $LAST"

# --- Test 3: invalid action rejected ---
if "$HELPER" c5d9e4f3 bogus 2>/dev/null; then fail "expected failure on invalid action"; fi

# --- Test 4: parallel writers, all land, no interleaving ---
: > "$JSONL"
for i in $(seq 1 20); do
  "$HELPER" "hash$(printf '%02d' "$i")" watching &
done
wait
LINES=$(wc -l < "$JSONL" | tr -d ' ')
[ "$LINES" = "20" ] || fail "expected 20 parallel lines, got $LINES"
python3 - <<'PY' "$JSONL"
import json, sys
ids = set()
for line in open(sys.argv[1]):
    line = line.strip()
    if not line: continue
    d = json.loads(line)  # raises on any interleaved corruption
    ids.add(d["signal_id"])
assert len(ids) == 20, f"expected 20 unique ids, got {len(ids)}"
print("parallel ok")
PY

echo "observer-mark-processed.test.sh: all tests passed"
```

- [ ] **Step 4.2: Run the test**

```bash
chmod 755 tests/observer-mark-processed.test.sh
bash tests/observer-mark-processed.test.sh
```
Expected: `all tests passed`.

- [ ] **Step 4.3: Commit**

```bash
git add tests/observer-mark-processed.test.sh
git commit -m "test(scripts): observer-mark-processed concurrency test (Fix #6)"
```

---

## Task 5: Update `scripts/observer-loop.sh` to write `signal_id`

**Files:**
- Modify: `scripts/observer-loop.sh`

- [ ] **Step 5.1: Find the current signal-append line**

```bash
grep -n 'preference-signals\.jsonl\|>> .*signals' scripts/observer-loop.sh
```

Record the jq expression (or heredoc) used to build the signal object today.

- [ ] **Step 5.2: Add `signal_id` to the existing `jq -nc` expression**

The real file uses `jq -nc '{ ... }'` piping `.patterns[]?`. Compute `signal_id` BEFORE the jq call as `sha256(date + source + description + HH:MM:SS)[:8]` (time component prevents rerun collisions when the same pattern surfaces twice). Use `shasum -a 256` (macOS default), not `sha256sum` (Linux).

Because the existing jq expression iterates over `.patterns[]?`, the signal_id has to be computed INSIDE jq so each pattern in the batch gets its own id. Two options:
- Compute in jq using `now | strftime("%H:%M:%S")` plus string concat, then call a shasum sub-shell -- awkward.
- Compute per-pattern in a small while-loop after extracting patterns.

Cleanest: use jq to compute the id from `(date + source + description + time)` but since jq doesn't ship sha256, run the patterns through a two-step pipe. Simpler: use jq to embed all fields except signal_id, then a second stream that reads each line and injects signal_id computed via shasum.

For the patch, use this shape (drop into the existing `# Append patterns to preference-signals.jsonl` block):

```bash
# Append patterns to preference-signals.jsonl (each pattern gets a signal_id)
echo "$json_content" | jq -c '
    .patterns[]? |
    {
      date: (now | strftime("%Y-%m-%d")),
      time: (now | strftime("%H:%M:%S")),
      source: "observer-loop",
      type: .type,
      description: .description,
      confidence: .confidence,
      skill: .skill
    }
  ' 2>/dev/null | while IFS= read -r line; do
    [ -z "$line" ] && continue
    d=$(printf '%s' "$line" | jq -r '.date')
    t=$(printf '%s' "$line" | jq -r '.time')
    s=$(printf '%s' "$line" | jq -r '.source')
    desc=$(printf '%s' "$line" | jq -r '.description')
    sigid=$(printf '%s%s%s%s' "$d" "$s" "$desc" "$t" | shasum -a 256 | cut -c1-8)
    printf '%s' "$line" | jq -c --arg sigid "$sigid" '. + {signal_id: $sigid}' >> "$SIGNALS_FILE"
  done || true
```

This preserves the original jq shape (so the field set is unchanged except for the added `signal_id` and internal `time` field) and computes the hash per-pattern using macOS `shasum -a 256`.

**Locking:** the original loop does not lock the signals file append, and `observer-mark-processed.sh` acquires `/tmp/k2b-preference-signals.lock` locally on the Mac. This is an accepted risk per finding 3: observer-loop.sh runs on Mac Mini, the helper runs on Mac; filesystem locks do not cross Syncthing. Cross-machine conflicts manifest as Syncthing `*.sync-conflict*` files; not fixed in this plan.

- [ ] **Step 5.3: Syntax check**

```bash
bash -n scripts/observer-loop.sh
```

- [ ] **Step 5.4: Run once against the live loop if Keith approves**

This step requires Keith in-session because `observer-loop.sh` runs as a pm2 process on the Mini. Safer: deploy via `/sync` after commit and watch the next signal have a `signal_id`.

```bash
# local smoke only
bash scripts/observer-loop.sh --oneshot 2>&1 | tail -5 || true
tail -1 ~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl | python3 -c 'import json,sys; d=json.loads(sys.stdin.read()); assert "signal_id" in d, d'
```

- [ ] **Step 5.5: Commit**

```bash
git add scripts/observer-loop.sh
git commit -m "feat(observer-loop): write signal_id on every new signal (Fix #6)"
```

---

## Task 6: Update `k2b-observer/SKILL.md` with filter + mark steps

**Files:**
- Modify: `.claude/skills/k2b-observer/SKILL.md`

- [ ] **Step 6.1: Insert Phase 1a filter step**

Find the section where Phase 1 loads signals. Add immediately after:

```markdown
### Phase 1a. Filter out processed signals (APPEND-cutoff reader)

Walk `~/Projects/K2B-Vault/wiki/context/preference-signals.jsonl` top-to-bottom. Track two things:

1. `past_cutoff` boolean, starting False. When a line with `type: "grandfather-cutoff"` is seen, set `past_cutoff = True`. All lines BEFORE the cutoff are implicitly grandfathered (treated as already processed, never surfaced). All lines AFTER the cutoff are subject to signal_id dedup.
2. `processed_ids` set. For every line with `type: "signal-processed"` whose `action` is `confirmed` or `rejected`, add its `signal_id` to the set. `action: watching` is intentionally EXCLUDED -- deferring a signal should resurface it next session, not silence it forever.

A signal is filtered out (not surfaced) when any of these is true:
- It appears before the grandfather-cutoff line (i.e. `past_cutoff == False` when the signal is read).
- Its `signal_id` is in `processed_ids`.
- It has no `signal_id` at all (pre-Fix #6 historical, grandfathered by the cutoff).

Remaining signals flow into Phase 2 pattern detection and Phase 3 synthesis.
```

- [ ] **Step 6.2: Update the "Session-Start Inline Confirmation" section**

Find the section introduced by Fix #4 strip E (should already exist per recent commit `d02c574`). Add a step after Keith answers y/n/skip:

```markdown
After Keith answers, mark the signal as processed via the helper:

```bash
scripts/observer-mark-processed.sh <signal_id> <confirmed|rejected|watching> [L-ID]
```

Pass `confirmed` when Keith answered yes and a learning was created, `rejected` when he said no (do not surface again), `watching` when he deferred. Include the new L-ID when the action produced a learning.
```

- [ ] **Step 6.3: Commit**

```bash
git add .claude/skills/k2b-observer/SKILL.md
git commit -m "feat(k2b-observer): Phase 1a filter + post-action mark (Fix #6)"
```

---

## Task 7: Integration check

- [ ] **Step 7.1: Manual end-to-end walk**

1. Confirm the cutoff line is the LAST line of the live jsonl at the time of this task (subsequent observer-loop signals append after it).
2. Run `scripts/observer-loop.sh --oneshot` (or wait for the next background tick). Confirm a new signal has `signal_id`.
3. Simulate session-start inline: call `scripts/observer-mark-processed.sh <that-signal_id> confirmed L-test-001`.
4. Re-read the jsonl and confirm both the original signal and the processed line coexist.
5. Run the Phase 1a filter logic (a small Python one-liner or by hand) and confirm the signal no longer appears in "to surface" output.

```bash
python3 - <<'PY'
import json
path = "/Users/keithmbpm2/Projects/K2B-Vault/wiki/context/preference-signals.jsonl"
signals = []
processed = set()
past_cutoff = False
for line in open(path):
    line = line.strip()
    if not line: continue
    d = json.loads(line)
    if d.get("type") == "grandfather-cutoff":
        past_cutoff = True
        continue
    if d.get("type") == "signal-processed":
        # watching does NOT filter; only confirmed/rejected do
        if d.get("action") in ("confirmed", "rejected"):
            processed.add(d["signal_id"])
        continue
    if not past_cutoff:
        continue  # pre-cutoff, grandfathered
    signals.append(d)

unprocessed = [s for s in signals if s.get("signal_id") and s.get("signal_id") not in processed]
print(f"total={len(signals)} unprocessed={len(unprocessed)} processed={len(processed)}")
PY
```

Expected: unprocessed count drops whenever you mark a signal confirmed/rejected/watching.

---

## Self-review checklist

- [ ] Cutoff line is APPENDED at the end of the jsonl (append-only preserved).
- [ ] Every new signal from `observer-loop.sh` has a `signal_id` field with time component in its hash.
- [ ] `observer-mark-processed.sh` rejects invalid actions and accepts valid ones.
- [ ] Parallel writes with the SAME signal_id still produce 20 intact lines (finding 6 concurrency test).
- [ ] k2b-observer skill body has both Phase 1a filter (APPEND-cutoff, `watching` excluded from dedup) AND post-action mark step.
- [ ] No backfill of historical signals -- cutoff marker handles them.
- [ ] Cross-machine lock limitation documented in `observer-mark-processed.sh` header comment.

## Notes for the reviewing agent

- `sha256(date + source + description + HH:MM:SS)[:8]` has ~4.3B unique values and the time component prevents rerun collisions when the same pattern surfaces multiple times in a day.
- The `by: "session-start-inline"` field is hardcoded in the helper. If `/observe` deep synthesis also needs to mark signals, either (a) add a second helper, or (b) accept a `--by` flag. For this plan: `/observe` deep synthesis uses the same helper and accepts the hardcoded `by` -- it's cosmetic metadata.
- If the current `observer-loop.sh` uses a different vault path or runs on Mini only, adjust the `K2B_PREFERENCE_SIGNALS` default accordingly. The env-var override is for testing only.
- Cross-machine lock limitation: observer-loop.sh runs on Mac Mini (pm2) and observer-mark-processed.sh runs on the Mac. Both write to the Syncthing-synced jsonl. Filesystem locks DO NOT cross machines. Cross-machine conflicts manifest as Syncthing `*.sync-conflict*` files. Accepted risk; documented in the helper's header comment.
- `action: watching` deliberately does NOT dedup. When Keith defers a finding, the signal should resurface in the next session to give him another chance to act on it.
