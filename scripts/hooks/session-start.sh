#!/bin/bash
# K2B Session Start Hook
# Fires on every Claude Code session start. Outputs context to stdout.
# Deterministic replacement for the "Session Start" section in CLAUDE.md.

set -euo pipefail

VAULT="$HOME/Projects/K2B-Vault"
K2B="$HOME/Projects/K2B"
CONTEXT_DIR="$VAULT/wiki/context"

output=""

# --- 1. Check usage triggers ---
trigger_result=$("$K2B/scripts/check-usage-triggers.sh" 2>/dev/null || true)
if echo "$trigger_result" | grep -q "USAGE TRIGGERS READY"; then
  output+="$trigger_result"$'\n\n'
fi

# --- 2. Scan review/ queue for items needing action ---
review_ready_count=0
review_reviewed_count=0

# Check review/Ready/ for items Keith dragged there
if [ -d "$VAULT/review/Ready" ]; then
  review_ready_count=$(find "$VAULT/review/Ready" -name "*.md" -type f 2>/dev/null | wc -l | tr -d ' ')
fi

# Check review/ for items with review-action set in frontmatter
if [ -d "$VAULT/review" ]; then
  for f in "$VAULT/review"/*.md; do
    [ -f "$f" ] || continue
    [[ "$(basename "$f")" == "index.md" ]] && continue
    if head -30 "$f" | grep -q "^review-action:" 2>/dev/null; then
      action=$(head -30 "$f" | grep "^review-action:" | head -1 | sed 's/review-action: *//')
      if [ -n "$action" ] && [ "$action" != '""' ] && [ "$action" != "''" ]; then
        review_reviewed_count=$((review_reviewed_count + 1))
      fi
    fi
  done
fi

total_review=$((review_ready_count + review_reviewed_count))
if [ "$total_review" -gt 0 ]; then
  output+="REVIEW QUEUE: $total_review items ready to process"
  [ "$review_ready_count" -gt 0 ] && output+=" ($review_ready_count in Ready/)"
  [ "$review_reviewed_count" -gt 0 ] && output+=" ($review_reviewed_count with review-action set)"
  output+=". Run /review to process them."$'\n\n'
fi

# --- 2.5 Inject wiki index summary ---
wiki_index="$VAULT/wiki/index.md"
if [ -f "$wiki_index" ]; then
  output+="WIKI INDEX (vault knowledge catalog):"$'\n'
  output+="$(cat "$wiki_index")"$'\n\n'
fi

# --- 3. K2B loop dashboard (observer candidates + review + research-delivery) ---
# Replaces the bare observer-candidates dump. The dashboard numbers observer
# candidates with stable content-hash IDs so Claude can route one-word
# accept/reject/defer tokens back to scripts/loop/loop-apply.sh before the
# first prompt response. Review/research items appear for awareness only in
# Ship 1 (routing is observer-only; Ship 2 extends routing).
# Silent degradation is forbidden per L-2026-04-22-001: if the renderer
# errors, surface the failure loudly instead of hiding it.
dashboard_stderr="$(mktemp)"
dashboard_output="$("$K2B/scripts/loop/loop-render-dashboard.sh" 2>"$dashboard_stderr")" || dashboard_exit=$?
dashboard_exit="${dashboard_exit:-0}"

if [ "$dashboard_exit" -ne 0 ]; then
  output+="## K2B LOOP DASHBOARD -- HOOK DEGRADED"$'\n'
  output+="Renderer exited $dashboard_exit. Observer candidates, review queue, and stale research are not being surfaced."$'\n'
  output+="stderr:"$'\n'
  output+="$(cat "$dashboard_stderr" 2>/dev/null || true)"$'\n\n'
elif [ -n "$dashboard_output" ]; then
  output+="$dashboard_output"$'\n\n'
  output+="LOOP ROUTING INSTRUCTION (Ship 1): observer candidates only are routable."$'\n'
  output+="If Keith's first message contains tokens matching the grammar above (a N / r N / d N),"$'\n'
  output+="call scripts/loop/loop-apply.sh with the translated --accept N / --reject N / --defer N"$'\n'
  output+="flags before any other action. Review/research items listed below the observer section"$'\n'
  output+="are informational in Ship 1; process them with /review or /lint, not the loop grammar."$'\n\n'
fi
rm -f "$dashboard_stderr"

# --- 4. Load active rules ---
active_rules=$(find -L ~/.claude/projects/ -name "active_rules.md" -type f 2>/dev/null | head -1)
if [ -f "$active_rules" 2>/dev/null ]; then
  output+="ACTIVE RULES (follow these every session):"$'\n'
  output+="$(cat "$active_rules")"$'\n\n'
fi

# --- 4.5 Load reinforced learnings as watch list ---
# Learnings with Reinforced >= 2 are surfaced as guidance (not full rules).
# This bridges the gap between "captured once" and "promoted to active rule".
# Dedupe: exclude learnings whose IDs already appear in active_rules.md.
learnings_file=$(find -L ~/.claude/projects/ -name "self_improve_learnings.md" -type f 2>/dev/null | head -1)
if [ -f "$learnings_file" 2>/dev/null ]; then
  # Build exclusion list from active rules (they reference learning IDs like L-2026-03-26-001)
  active_ids=""
  if [ -f "$active_rules" 2>/dev/null ]; then
    # Pipefail + set -e would treat a no-match grep as fatal and silently kill
    # the whole hook (pre-existing bug: observer/review output never reached
    # Keith because the hook exited 1 on an empty active_rules grep).
    active_ids=$( { grep -oE 'L-[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]+' "$active_rules" 2>/dev/null || true; } | sort -u | tr '\n' '|')
    active_ids="${active_ids%|}"  # strip trailing pipe
  fi
  # Extract learning blocks with Reinforced >= 2, excluding already-promoted ones
  watch_items=$(awk -v exclude="$active_ids" '
    /^### L-/ { id=$2; learning=""; area="" }
    /^\- \*\*Area:\*\*/ { gsub(/^- \*\*Area:\*\* */, ""); area=$0 }
    /^\- \*\*Learning:\*\*/ { gsub(/^- \*\*Learning:\*\* */, ""); learning=$0 }
    /^\- \*\*Reinforced:\*\*/ {
      gsub(/^- \*\*Reinforced:\*\* */, "");
      count=int($0);
      if (count >= 2 && learning != "") {
        # Skip if this learning ID is already in active rules
        if (exclude != "" && id ~ exclude) next;
        printf "- [%s] (%s) %s\n", id, area, learning
      }
    }
  ' "$learnings_file")
  if [ -n "$watch_items" ]; then
    output+="LEARNINGS WATCH LIST (reinforced 2+, not yet promoted to active rules):"$'\n'
    output+="$watch_items"$'\n\n'
  fi
fi

# --- 5. Surface pending-sync mailbox entries ---
# Written by /ship --defer. Each entry is a separate JSON file in the mailbox
# directory, so concurrent defers never race. Durable across session
# boundaries. The python below ALWAYS exits 0 and encodes state in stdout --
# `set -e` at the top of this script would otherwise abort the whole hook on
# a python non-zero exit, silently hiding the condition we want to surface.
pending_mailbox="$K2B/.pending-sync"
if [ -d "$pending_mailbox" ]; then
  pending_status=$(python3 <<PYEOF
import json, os, sys
MAILBOX = "$pending_mailbox"
entries = []
unreadable = []
try:
    names = sorted(os.listdir(MAILBOX))
except OSError as e:
    print(f"DIR_UNREADABLE|{e}")
    sys.exit(0)

import time
STALE_TMP_THRESHOLD = 60  # seconds; real writes take milliseconds
now = time.time()
for name in names:
    if name.startswith(".tmp_"):
        # In-progress atomic write by /ship (producer is between fdopen+fsync
        # and os.replace). Usually we skip these. BUT: if a producer crashed
        # between fsync and replace, the only durable artifact is the .tmp_
        # file and we would lose the defer signal forever. Surface any .tmp_
        # older than STALE_TMP_THRESHOLD as UNREADABLE so Keith can recover.
        try:
            age = now - os.stat(os.path.join(MAILBOX, name)).st_mtime
        except OSError:
            continue
        if age > STALE_TMP_THRESHOLD:
            unreadable.append((name, f"stale-temp:{int(age)}s old, likely crashed producer -- inspect and rename to .json if JSON is complete, else delete"))
        continue
    if not name.endswith(".json"):
        continue
    path = os.path.join(MAILBOX, name)
    try:
        with open(path) as f:
            d = json.load(f)
    except json.JSONDecodeError as e:
        unreadable.append((name, f"json:{e.msg}"))
        continue
    except OSError as e:
        unreadable.append((name, f"io:{e}"))
        continue
    if not isinstance(d, dict):
        unreadable.append((name, "schema:top-level not object"))
        continue
    if not d.get("pending", False):
        continue  # already-processed straggler, skip silently
    required = ("set_at", "set_by_commit", "categories", "files", "entry_id")
    missing = [k for k in required if k not in d]
    if missing:
        unreadable.append((name, f"schema:missing {','.join(missing)}"))
        continue
    # Category allowlist: only values /sync has a deploy target for.
    # Legacy "hooks" entries from pre-fix defers land here and must NOT be
    # silently consumed -- /sync has no deploy path for them.
    VALID_CATEGORIES = {"skills", "code", "dashboard", "scripts"}
    cats = d.get("categories", [])
    if not isinstance(cats, list) or not cats:
        unreadable.append((name, "schema:categories must be non-empty list"))
        continue
    bad_cats = [c for c in cats if c not in VALID_CATEGORIES]
    if bad_cats:
        unreadable.append((name, f"category:unknown {','.join(bad_cats)} (expected subset of {sorted(VALID_CATEGORIES)})"))
        continue
    entries.append(d)

if not entries and not unreadable:
    print("EMPTY")
    sys.exit(0)

# Summary format: one VALID line (may be empty count) + one UNREADABLE line
# per bad file. Both go through stdout so the bash side can branch.
print(f"VALID|{len(entries)}")
for d in entries:
    cats = ",".join(d.get("categories", [])) or "unknown"
    sha = str(d.get("set_by_commit", "unknown"))[:7]
    when = d.get("set_at", "unknown")
    nfiles = len(d.get("files", []))
    eid = d.get("entry_id", "unknown")
    print(f"ENTRY|{when}|{sha}|{cats}|{nfiles}|{eid}")
for name, reason in unreadable:
    print(f"UNREADABLE|{name}|{reason}")
PYEOF
)
  if [ -n "$pending_status" ]; then
    # Parse valid entries and unreadable entries
    valid_count=0
    unreadable_count=0
    entry_lines=""
    unreadable_lines=""
    while IFS= read -r line; do
      case "$line" in
        EMPTY) : ;;  # nothing
        VALID\|*) valid_count="${line#VALID|}" ;;
        ENTRY\|*)
          entry_lines+="  - ${line#ENTRY|}"$'\n'
          ;;
        UNREADABLE\|*)
          unreadable_count=$((unreadable_count + 1))
          unreadable_lines+="  - ${line#UNREADABLE|}"$'\n'
          ;;
        DIR_UNREADABLE\|*)
          unreadable_lines+="  - (mailbox directory unreadable) ${line#DIR_UNREADABLE|}"$'\n'
          unreadable_count=$((unreadable_count + 1))
          ;;
      esac
    done <<< "$pending_status"

    if [ "$valid_count" != "0" ] && [ -n "$valid_count" ]; then
      if [ "$valid_count" = "1" ]; then
        output+="PENDING SYNC: 1 mailbox entry"$'\n'
      else
        output+="PENDING SYNC: $valid_count mailbox entries"$'\n'
      fi
      output+="$entry_lines"
      output+="Format: when | commit | categories | file-count | entry-id"$'\n'
      output+="The Mac Mini is stale. Run /sync to consume the mailbox and catch it up."$'\n\n'
    fi
    if [ "$unreadable_count" -gt 0 ]; then
      if [ "$unreadable_count" = "1" ]; then
        output+="PENDING SYNC MAILBOX has 1 UNREADABLE entry"$'\n'
      else
        output+="PENDING SYNC MAILBOX has $unreadable_count UNREADABLE entries"$'\n'
      fi
      output+="$unreadable_lines"
      output+="The durable deferred-sync signal is broken for these entries. Inspect, fix, or delete them in $pending_mailbox and re-run /sync."$'\n\n'
    fi
  fi
fi

# --- Output ---
if [ -n "$output" ]; then
  echo "$output"
fi

exit 0
