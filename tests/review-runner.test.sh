#!/usr/bin/env bash
# tests/review-runner.test.sh
# Tests for scripts/lib/review_runner.py (Codex+MiniMax fallback review runner).
#
# Architecture: each test builds a fresh temp git repo with:
#   - a dirty file (to satisfy the classifier's "something changed" requirement)
#   - a fake scripts/minimax-review.sh shim (inside the temp REPO_ROOT)
#   - a fake codex plugin tree with a fake codex-companion.mjs (real .mjs)
# then invokes the real runner at its actual K2B location.
#
# REPO_ROOT in the runner is computed from `git rev-parse --show-toplevel`
# against the runner's cwd, so we `cd` into the temp dir before invoking.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$REPO_ROOT/scripts/lib/review_runner.py"

TMP_DIRS=()
cleanup() {
  local d
  for d in "${TMP_DIRS[@]}"; do
    [ -n "$d" ] && [ -d "$d" ] && rm -rf "$d"
  done
}
trap cleanup EXIT

PASS=0
FAIL=0
FAIL_NAMES=()

pass() {
  PASS=$((PASS + 1))
  echo "PASS: $1"
}

fail() {
  FAIL=$((FAIL + 1))
  FAIL_NAMES+=("$1")
  echo "FAIL: $1 -- $2" >&2
}

mktmp() {
  local d
  d="$(mktemp -d)"
  TMP_DIRS+=("$d")
  echo "$d"
}

# Seed a fresh git repo with a dirty file, a fake minimax shim, and a fake
# codex plugin. $1 = behavior for codex ("approve"|"hang"|"empty"|"error"),
# $2 = behavior for minimax ("approve"|"error"|"notfound").
seed_repo() {
  local d="$1"
  local codex_behavior="$2"
  local minimax_behavior="$3"

  cd "$d"
  git init -q
  git config user.email test@example.com
  git config user.name test

  # Gitignore the runner's archive dir + fake plugin dir so their presence
  # doesn't trip the EISDIR guard (real K2B ships the same .gitignore entry
  # for .code-reviews/ via adaptation A4; /plugins/ is test-fixture only).
  cat > .gitignore <<EOF
/.code-reviews/
/plugins/
EOF

  # Seed scripts dir + minimax shim (or leave scripts/minimax-review.sh
  # missing if behavior=notfound). The runner builds an absolute path to
  # scripts/minimax-review.sh via REPO_ROOT; the shim must exist at that
  # path, and the dir must be tracked by git so the EISDIR guard doesn't
  # flag it.
  mkdir -p scripts
  case "$minimax_behavior" in
    approve)
      cat > scripts/minimax-review.sh <<'EOF'
#!/usr/bin/env bash
echo "# MiniMax MiniMax-M2.7 review -- APPROVE"
echo '{"verdict": "approve"}'
exit 0
EOF
      chmod +x scripts/minimax-review.sh
      ;;
    error)
      cat > scripts/minimax-review.sh <<'EOF'
#!/usr/bin/env bash
echo "minimax error" >&2
exit 1
EOF
      chmod +x scripts/minimax-review.sh
      ;;
    notfound)
      # Deliberately do NOT create the shim; add placeholder so scripts/
      # itself is tracked (otherwise untracked dir trips the EISDIR guard)
      echo "placeholder" > scripts/.placeholder
      ;;
  esac

  # Commit the baseline so scripts/ and .gitignore are tracked. Only
  # target.py will be dirty when the runner scans.
  echo "dummy content" > target.py
  git add .gitignore scripts target.py
  git commit -q -m initial

  # Dirty file so the runner has something to review
  echo "dirty change" >> target.py

  # Fake codex plugin tree (under /plugins/ which is gitignored)
  local plugin="$d/plugins/codex"
  mkdir -p "$plugin/scripts"
  case "$codex_behavior" in
    approve)
      cat > "$plugin/scripts/codex-companion.mjs" <<'EOF'
process.stdout.write("# Codex Review\n");
process.stdout.write("APPROVE\n");
process.stdout.write("[codex] Review output captured.\n");
process.exit(0);
EOF
      ;;
    hang)
      cat > "$plugin/scripts/codex-companion.mjs" <<'EOF'
// Sleep forever; the runner's deadline must kill us.
setInterval(() => {}, 60000);
EOF
      ;;
    empty)
      cat > "$plugin/scripts/codex-companion.mjs" <<'EOF'
process.stdout.write("Hello world (no verdict marker)\n");
process.exit(0);
EOF
      ;;
    error)
      cat > "$plugin/scripts/codex-companion.mjs" <<'EOF'
process.stderr.write("codex error\n");
process.exit(1);
EOF
      ;;
    missing)
      # Deliberately do NOT create the .mjs
      :
      ;;
  esac

  echo "$plugin"
}

# ---------- Test 1: primary Codex approves ----------
test_primary_codex_approves() {
  local t="test_primary_codex_approves_short_path"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  if ! out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --focus "test" 2>&1); then
    fail "$t" "runner exited non-zero: $out"
    return
  fi

  local log_path
  log_path=$(echo "$out" | python3 -c \
    'import json,sys
for line in sys.stdin.read().splitlines():
    if line.startswith("{"):
        # concatenate rest if multiline
        break
import json, sys; data=sys.stdin
' 2>&1) || true

  # Simpler: runner prints JSON on final line group. Extract log_path via jq-free python.
  local log
  log=$(python3 -c '
import json, sys
text = """'"$out"'"""
# find the JSON object -- runner prints it as pretty-printed multi-line
start = text.find("{")
end = text.rfind("}")
if start < 0 or end < 0:
    print("NO_JSON")
    sys.exit(0)
try:
    data = json.loads(text[start:end+1])
    print(data.get("log_path", "NO_LOG_PATH"))
except Exception as e:
    print(f"PARSE_ERROR: {e}")
')

  if [ ! -f "$log" ]; then
    fail "$t" "expected log file at $log, not found. out=$out"
    return
  fi
  if ! grep -q "# Codex Review" "$log"; then
    fail "$t" "log $log does not contain # Codex Review. contents:\n$(cat "$log")"
    return
  fi
  pass "$t"
}

# ---------- Test 2: Codex hang falls back to MiniMax ----------
test_codex_hang_falls_back_to_minimax() {
  local t="test_codex_hang_falls_back_to_minimax"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" hang approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 3 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    fail "$t" "runner rc=$rc, expected 0. out=$out"
    return
  fi

  # Check state file reports fallback
  local state_path
  state_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", "").replace(".log", ".json"))
')

  if [ ! -f "$state_path" ]; then
    fail "$t" "state file not found at $state_path"
    return
  fi

  local fallback_used
  fallback_used=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('fallback_used'))
")
  if [ "$fallback_used" != "True" ]; then
    fail "$t" "expected fallback_used=True, got $fallback_used. state=$(cat "$state_path")"
    return
  fi

  local attempts_codex attempts_minimax
  attempts_codex=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
for a in att:
    if a.get('reviewer')=='codex': print(a.get('result')); break
")
  attempts_minimax=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
for a in att:
    if a.get('reviewer')=='minimax': print(a.get('result')); break
")
  if [ "$attempts_codex" != "timed_out" ]; then
    fail "$t" "expected codex result=timed_out, got $attempts_codex"
    return
  fi
  if [ "$attempts_minimax" != "ok" ]; then
    fail "$t" "expected minimax result=ok, got $attempts_minimax"
    return
  fi

  pass "$t"
}

# ---------- Test 3: both fail returns exit 2 ----------
test_both_fail_returns_exit_2() {
  local t="test_both_fail_returns_exit_2"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" error error)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 5 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected rc=2, got rc=$rc. out=$out"
    return
  fi
  pass "$t"
}

# ---------- Test 4: deadline kill after N seconds ----------
test_deadline_kill_after_n_seconds() {
  local t="test_deadline_kill_after_n_seconds"
  local d; d="$(mktmp)"
  # Use notfound for minimax so fallback itself fails fast (avoids 10s grace blur).
  local plugin; plugin="$(seed_repo "$d" hang notfound)"

  cd "$d"
  local start_ts=$(date +%s)
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 2 --heartbeat-interval 1 2>&1)
  local rc=$?
  local end_ts=$(date +%s)
  local elapsed=$((end_ts - start_ts))

  # Runner should kill codex at deadline=2s, grace 10s = total <= 15s with fallback attempt
  if [ "$elapsed" -gt 30 ]; then
    fail "$t" "runner took ${elapsed}s, expected <=30s (deadline 2s + 10s grace + minimax spawn fail)"
    return
  fi

  # Extract state file
  local log_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')
  if ! grep -q "HARD_DEADLINE" "$log_path"; then
    fail "$t" "log did not contain HARD_DEADLINE marker. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 5: quality gate forces fallback on silent rc=0 ----------
test_quality_gate_no_verdict_forces_fallback() {
  local t="test_quality_gate_no_verdict_forces_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" empty approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 after MiniMax fallback approved, got rc=$rc. out=$out"
    return
  fi

  local log_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')
  if ! grep -q "QUALITY_GATE_FAIL" "$log_path"; then
    fail "$t" "log did not contain QUALITY_GATE_FAIL marker. log=$(cat "$log_path")"
    return
  fi
  local state_path="${log_path%.log}.json"
  local fallback_used
  fallback_used=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('fallback_used'))
")
  if [ "$fallback_used" != "True" ]; then
    fail "$t" "expected fallback_used=True after quality-gate fail, got $fallback_used"
    return
  fi
  pass "$t"
}

# ---------- Test 6: Codex EISDIR guard pre-skips on untracked dir ----------
test_codex_unavailable_reason_eisdir() {
  local t="test_codex_unavailable_reason_eisdir"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  # Seed an untracked directory -- Codex would EISDIR on this
  mkdir -p seed_dir
  echo "x" > seed_dir/x.py

  local out
  out=$(python3 "$RUNNER" working-tree --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 (MiniMax approves), got rc=$rc. out=$out"
    return
  fi

  local log_path state_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')
  state_path="${log_path%.log}.json"

  if ! grep -q "REVIEWER_SKIP" "$log_path"; then
    fail "$t" "log did not contain REVIEWER_SKIP for codex. log=$(cat "$log_path")"
    return
  fi
  if ! grep -q "EISDIR" "$log_path"; then
    fail "$t" "log did not contain EISDIR reason. log=$(cat "$log_path")"
    return
  fi
  # First reviewer attempt should be codex-unavailable
  local first_result
  first_result=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
for a in att:
    if a.get('reviewer')=='codex': print(a.get('result')); break
")
  if [ "$first_result" != "unavailable" ]; then
    fail "$t" "expected codex result=unavailable, got $first_result"
    return
  fi
  pass "$t"
}

# ---------- Test 7: plan scope always routes to MiniMax ----------
test_plan_scope_always_routes_to_minimax() {
  local t="test_plan_scope_always_routes_to_minimax"
  local d; d="$(mktmp)"
  # NOTE: clean working tree (no EISDIR hazards beyond what seed_repo creates).
  # seed_repo puts target.py dirty; remove it for a clean tree.
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  git checkout -q -- target.py
  # Now no EISDIR hazards. If Codex were eligible it would approve. But plan
  # scope must still route to MiniMax because Codex doesn't support plan files.

  # Create a plan file inside the repo
  mkdir -p plans
  cat > plans/tiny.md <<'EOF'
# Tiny plan

Just a placeholder for plan-scope routing test.
EOF

  local out
  out=$(python3 "$RUNNER" plan --plan plans/tiny.md --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0, got rc=$rc. out=$out"
    return
  fi

  local log_path state_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')
  state_path="${log_path%.log}.json"

  local first_result first_reason
  first_result=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
print(att[0].get('result')) if att else print('NONE')
")
  first_reason=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
print(att[0].get('reason')) if att else print('NONE')
")

  if [ "$first_result" != "unavailable" ]; then
    fail "$t" "expected first attempt unavailable, got $first_result"
    return
  fi
  case "$first_reason" in
    *plan*|*Plan*) : ;;
    *)
      fail "$t" "expected reason to mention plan scope, got: $first_reason"
      return ;;
  esac
  pass "$t"
}

# ---------- Test A3: parent MINIMAX_API_KEY is inherited, not overwritten ----------
test_minimax_key_inherited_from_parent_env() {
  local t="test_minimax_key_inherited_from_parent_env"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  # Replace the standard minimax shim with one that ECHOES the env var value
  # into its own stdout so we can verify inheritance.
  cat > "$d/scripts/minimax-review.sh" <<'EOF'
#!/usr/bin/env bash
echo "# MiniMax MiniMax-M2.7 review -- APPROVE"
echo "KEY-ECHO: [${MINIMAX_API_KEY:-UNSET}]"
exit 0
EOF
  chmod +x "$d/scripts/minimax-review.sh"

  cd "$d"
  local out
  out=$(MINIMAX_API_KEY="inherited-sentinel-xyz" python3 "$RUNNER" diff \
      --files target.py --wait --codex-plugin "$plugin" \
      --primary minimax --focus "test" 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "rc=$rc, out=$out"
    return
  fi

  local log_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')

  if ! grep -q "KEY-ECHO: \[inherited-sentinel-xyz\]" "$log_path"; then
    fail "$t" "expected KEY-ECHO: [inherited-sentinel-xyz] in log, but got: $(grep KEY-ECHO "$log_path" || echo NONE)"
    return
  fi
  pass "$t"
}

# ---------- Test 9: poll unknown job returns 1 ----------
test_poll_unknown_job_returns_1() {
  local t="test_poll_unknown_job_returns_1"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"
  cd "$d"

  local out
  out=$(python3 "$RUNNER" --poll nonexistent-job 2>&1)
  local rc=$?
  if [ "$rc" -ne 1 ]; then
    fail "$t" "expected rc=1, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "unknown_job_id"; then
    fail "$t" "expected unknown_job_id in output, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 8 (bonus): watchdog injects HEARTBEAT ----------
test_watchdog_injects_heartbeat() {
  local t="test_watchdog_injects_heartbeat"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  # Slow the minimax shim so the heartbeat thread runs at least once
  cat > "$d/scripts/minimax-review.sh" <<'EOF'
#!/usr/bin/env bash
sleep 2
echo "# MiniMax MiniMax-M2.7 review -- APPROVE"
echo '{"verdict":"approve"}'
exit 0
EOF
  chmod +x "$d/scripts/minimax-review.sh"

  cd "$d"
  local out
  # Use --primary minimax to skip Codex
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "rc=$rc, out=$out"
    return
  fi

  local log_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')

  local heartbeat_count
  heartbeat_count=$(grep -c "HEARTBEAT" "$log_path" || true)
  if [ "$heartbeat_count" -lt 1 ]; then
    fail "$t" "expected >=1 HEARTBEAT line, got $heartbeat_count. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Run all ----------
echo "Running review-runner tests..."
echo "Runner: $RUNNER"
echo

test_primary_codex_approves
test_codex_hang_falls_back_to_minimax
test_both_fail_returns_exit_2
test_deadline_kill_after_n_seconds
test_quality_gate_no_verdict_forces_fallback
test_codex_unavailable_reason_eisdir
test_plan_scope_always_routes_to_minimax
test_watchdog_injects_heartbeat
test_minimax_key_inherited_from_parent_env
test_poll_unknown_job_returns_1

echo
echo "======================================"
echo "Passed: $PASS    Failed: $FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "Failed tests:"
  for n in "${FAIL_NAMES[@]}"; do
    echo "  - $n"
  done
  exit 1
fi
echo "ALL TESTS PASS"
