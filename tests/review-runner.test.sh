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

# ---------- Test 7: plan scope runs Codex as PRIMARY (regression fix 2026-05-31) ----------
# Codex reviews the plan file via the `task` subcommand (read-only sandbox,
# --prompt-file). The old code hard-skipped plan scope to MiniMax claiming
# codex-companion.mjs needs a --path flag it "dropped" -- but no companion
# version ever had --path, and `task` does not need it. Codex is primary again;
# MiniMax stays the fallback (see test 7b).
test_plan_scope_runs_codex_primary() {
  local t="test_plan_scope_runs_codex_primary"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  git checkout -q -- target.py   # clean tree (plan scope does not walk it)
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

  # First reviewer attempt must be codex with result ok (Codex is primary).
  local first_reviewer first_result fallback_used
  first_reviewer=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
print(att[0].get('reviewer')) if att else print('NONE')
")
  first_result=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
print(att[0].get('result')) if att else print('NONE')
")
  fallback_used=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('fallback_used'))
")
  if [ "$first_reviewer" != "codex" ]; then
    fail "$t" "expected first reviewer codex, got $first_reviewer. state=$(cat "$state_path")"
    return
  fi
  if [ "$first_result" != "ok" ]; then
    fail "$t" "expected codex result ok, got $first_result. state=$(cat "$state_path")"
    return
  fi
  if [ "$fallback_used" != "False" ]; then
    fail "$t" "expected fallback_used False (Codex primary handled it), got $fallback_used"
    return
  fi
  # Codex must be invoked via the `task` subcommand with a prompt file -- the
  # only path that lets Codex review an arbitrary plan markdown file.
  if ! grep -q "REVIEWER_START reviewer=codex" "$log_path"; then
    fail "$t" "expected codex reviewer to start. log=$(cat "$log_path")"
    return
  fi
  if ! grep -Eq "SPAWN argv=.*'task'.*'--prompt-file'" "$log_path"; then
    fail "$t" "expected Codex spawned via task --prompt-file. log=$(cat "$log_path")"
    return
  fi
  if ! grep -q "# Codex Review" "$log_path"; then
    fail "$t" "expected Codex review output in log. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 7b: plan scope falls back to MiniMax when Codex fails ----------
# Also proves the fallback actually receives plan SCOPE + the plan PATH (not a
# bare working-tree review): the shim below exits non-zero unless argv carries
# `--scope plan` and `--plan plans/tiny.md`, so a passing test guarantees Kimi
# reviews the plan file. (Without this argv guard the fake shim ignored argv and
# the regression guarantee was untested -- Codex plan-review P1 #4.)
test_plan_scope_codex_fails_falls_back_to_minimax() {
  local t="test_plan_scope_codex_fails_falls_back_to_minimax"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" error approve)"

  cd "$d"
  git checkout -q -- target.py   # clean tree
  mkdir -p plans
  cat > plans/tiny.md <<'EOF'
# Tiny plan

Placeholder for plan-scope fallback test.
EOF

  # Replace the standard shim with one that ASSERTS plan scope + plan path.
  cat > "$d/scripts/minimax-review.sh" <<'EOF'
#!/usr/bin/env bash
args="$*"
case "$args" in
  *"--scope plan"*"--plan plans/tiny.md"*) ;;
  *)
    echo "FALLBACK-ARGV-MISSING-PLAN-SCOPE: $args" >&2
    exit 1 ;;
esac
echo "# MiniMax MiniMax-M2.7 review -- APPROVE"
echo '{"verdict":"approve"}'
exit 0
EOF
  chmod +x "$d/scripts/minimax-review.sh"

  local out
  out=$(python3 "$RUNNER" plan --plan plans/tiny.md --wait \
      --codex-plugin "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 after MiniMax fallback, got rc=$rc. out=$out"
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

  local reviewers fallback_used
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  fallback_used=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('fallback_used'))
")
  if [ "$reviewers" != "codex,minimax" ]; then
    fail "$t" "expected attempts codex,minimax, got $reviewers. state=$(cat "$state_path")"
    return
  fi
  if [ "$fallback_used" != "True" ]; then
    fail "$t" "expected fallback_used True, got $fallback_used"
    return
  fi
  if ! grep -q "# MiniMax" "$log_path"; then
    fail "$t" "expected MiniMax fallback output in log. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 7c: plan-scope Codex runs with ISOLATED task state ----------
# Proves the runner relocates the companion's job store via CLAUDE_PLUGIN_DATA to
# a per-job dir, removing the PRIMARY resume-discovery path (the job store) for
# the plan-review `task`. This does NOT cover the documented residual where the
# app-server thread is still findable by name prefix; see build_codex_cmd in
# review_runner.py (Codex plan-review rounds 2-3 #2).
test_plan_scope_isolates_codex_state() {
  local t="test_plan_scope_isolates_codex_state"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  git checkout -q -- target.py   # clean tree
  mkdir -p plans
  cat > plans/tiny.md <<'EOF'
# Tiny plan

Placeholder for plan-scope state-isolation test.
EOF

  # Fake codex echoes the CLAUDE_PLUGIN_DATA it was spawned with.
  cat > "$plugin/scripts/codex-companion.mjs" <<'EOF'
process.stdout.write("# Codex Review\n");
process.stdout.write("CLAUDE_PLUGIN_DATA=[" + (process.env.CLAUDE_PLUGIN_DATA || "UNSET") + "]\n");
process.stdout.write("APPROVE\n");
process.exit(0);
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

  local log_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
data = json.loads(text[start:end+1])
print(data.get("log_path", ""))
')

  if ! grep -q "CODEX_PLAN_ISOLATED_STATE" "$log_path"; then
    fail "$t" "no CODEX_PLAN_ISOLATED_STATE marker. log=$(cat "$log_path")"
    return
  fi
  # The env the child actually saw must point at the per-job isolated dir,
  # not the default/unset store.
  local seen
  seen=$(grep -o 'CLAUDE_PLUGIN_DATA=\[[^]]*\]' "$log_path" | head -1)
  case "$seen" in
    *UNSET*)
      fail "$t" "codex spawned without isolated CLAUDE_PLUGIN_DATA: $seen"
      return ;;
    *.code-reviews/*codex-plan-state*) : ;;
    *)
      fail "$t" "CLAUDE_PLUGIN_DATA not isolated per-job: $seen"
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

# ---------- Test 10: primary MiniMax diff requires explicit files ----------
test_primary_minimax_diff_requires_files_before_fallback() {
  local t="test_primary_minimax_diff_requires_files_before_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --wait \
      --codex-plugin "$plugin" --primary minimax \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2 before fallback, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q -- "--files"; then
    fail "$t" "expected missing --files message, got: $out"
    return
  fi
  if echo "$out" | grep -q "# Codex Review"; then
    fail "$t" "Codex fallback ran despite invalid MiniMax diff args. out=$out"
    return
  fi
  # Stronger negative check: prove no reviewer process spawned at all by
  # confirming the runner did not create any state file or log archive.
  # If any reviewer spawned, .code-reviews/<job>.json + .log would exist.
  local archive_count
  archive_count=$(find "$d/.code-reviews" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$archive_count" != "0" ]; then
    fail "$t" "expected 0 archive files (no spawn), got $archive_count. archive=$(ls "$d/.code-reviews" 2>/dev/null)"
    return
  fi
  pass "$t"
}

# ---------- Test 11: --no-fallback prevents same-family fallback ----------
test_no_fallback_stops_after_primary_failure() {
  local t="test_no_fallback_stops_after_primary_failure"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve error)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax --no-fallback \
      --focus "test" --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected rc=2 with primary failure and no fallback, got rc=$rc. out=$out"
    return
  fi

  local log_path state_path
  log_path=$(python3 -c '
import json, sys
text = """'"$out"'"""
start = text.find("{")
end = text.rfind("}")
if start < 0 or end < 0:
    print("")
else:
    data = json.loads(text[start:end+1])
    print(data.get("log_path", ""))
')
  if [ -z "$log_path" ] || [ ! -f "$log_path" ]; then
    fail "$t" "expected runner JSON with log_path, got: $out"
    return
  fi
  state_path="${log_path%.log}.json"

  # Strongest signal first: state file proves only minimax attempted.
  local reviewers
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  if [ "$reviewers" != "minimax" ]; then
    fail "$t" "expected only minimax attempt, got reviewers=$reviewers state=$(cat "$state_path")"
    return
  fi
  # Negative SPAWN check: log must NOT contain any spawn for codex.
  if grep -qE 'SPAWN argv=.*codex|REVIEWER_START reviewer=codex' "$log_path"; then
    fail "$t" "Codex was spawned despite --no-fallback. log=$(cat "$log_path")"
    return
  fi
  if grep -q "# Codex Review" "$log_path"; then
    fail "$t" "Codex review marker found despite --no-fallback. log=$(cat "$log_path")"
    return
  fi
  # Confirm NO_FALLBACK log line was emitted (observability contract).
  if ! grep -q "NO_FALLBACK primary_failed" "$log_path"; then
    fail "$t" "expected NO_FALLBACK log line, got: $(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 12: OpenAI-built diffs cannot use Codex reviewer ----------
test_openai_builder_rejects_codex_primary() {
  local t="test_openai_builder_rejects_codex_primary"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex \
      --builder-family openai --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family openai requires --primary minimax --no-fallback"; then
    fail "$t" "expected openai matrix error, got: $out"
    return
  fi

  local archive_count
  archive_count=$(find "$d/.code-reviews" -maxdepth 1 -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  if [ "$archive_count" != "0" ]; then
    fail "$t" "expected 0 archive files (no reviewer spawn), got $archive_count"
    return
  fi
  pass "$t"
}

# ---------- Test 13: OpenAI-built diffs route to Kimi with no Codex fallback ----------
test_openai_builder_accepts_minimax_no_fallback() {
  local t="test_openai_builder_accepts_minimax_no_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax --no-fallback \
      --builder-family openai --focus "test" \
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
  if [ -z "$log_path" ] || [ ! -f "$log_path" ]; then
    fail "$t" "expected runner JSON with log_path, got: $out"
    return
  fi
  state_path="${log_path%.log}.json"

  local reviewers builder_family
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  builder_family=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('builder_family'))
")
  if [ "$reviewers" != "minimax" ]; then
    fail "$t" "expected only minimax attempt, got reviewers=$reviewers state=$(cat "$state_path")"
    return
  fi
  if [ "$builder_family" != "openai" ]; then
    fail "$t" "expected builder_family=openai, got $builder_family"
    return
  fi
  if grep -qE 'SPAWN argv=.*codex|REVIEWER_START reviewer=codex|# Codex Review' "$log_path"; then
    fail "$t" "Codex appeared despite OpenAI builder no-fallback matrix. log=$(cat "$log_path")"
    return
  fi
  if ! grep -q "builder_family=openai" "$log_path"; then
    fail "$t" "expected JOB_START builder_family=openai, got: $(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 14: Kimi-built diffs cannot use Kimi reviewer ----------
test_kimi_builder_rejects_minimax_primary() {
  local t="test_kimi_builder_rejects_minimax_primary"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax --no-fallback \
      --builder-family kimi --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family kimi requires --primary codex --no-fallback"; then
    fail "$t" "expected kimi matrix error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 15: Kimi-built diffs route to Codex with no Kimi fallback ----------
test_kimi_builder_accepts_codex_no_fallback() {
  local t="test_kimi_builder_accepts_codex_no_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex --no-fallback \
      --builder-family kimi --focus "test" \
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
  if [ -z "$log_path" ] || [ ! -f "$log_path" ]; then
    fail "$t" "expected runner JSON with log_path, got: $out"
    return
  fi
  state_path="${log_path%.log}.json"

  local reviewers builder_family
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  builder_family=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('builder_family'))
")
  if [ "$reviewers" != "codex" ]; then
    fail "$t" "expected only codex attempt, got reviewers=$reviewers state=$(cat "$state_path")"
    return
  fi
  if [ "$builder_family" != "kimi" ]; then
    fail "$t" "expected builder_family=kimi, got $builder_family"
    return
  fi
  if grep -q "REVIEWER_START reviewer=minimax" "$log_path"; then
    fail "$t" "Kimi reviewer appeared despite Kimi builder no-fallback matrix. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 16: other builder family requires an explicit no-fallback reviewer ----------
test_other_builder_requires_no_fallback() {
  local t="test_other_builder_requires_no_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex \
      --builder-family other --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family other requires --no-fallback"; then
    fail "$t" "expected other matrix error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 17: other builder family requires an explicit reviewer ----------
test_other_builder_requires_explicit_primary() {
  local t="test_other_builder_requires_explicit_primary"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" \
      --builder-family other --no-fallback --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family other requires explicit --primary"; then
    fail "$t" "expected explicit primary matrix error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 18: other builder family requires an independence reason ----------
test_other_builder_requires_reason() {
  local t="test_other_builder_requires_reason"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex \
      --builder-family other --no-fallback --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family other requires --other-reviewer-reason"; then
    fail "$t" "expected other reviewer reason error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 19: other builder family records independence reason ----------
test_other_builder_accepts_reason() {
  local t="test_other_builder_accepts_reason"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex \
      --builder-family other --no-fallback \
      --other-reviewer-reason "human-built diff, Codex independent" \
      --focus "test" --deadline 10 --heartbeat-interval 1 2>&1)
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

  local reason
  reason=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('other_reviewer_reason'))
")
  if [ "$reason" != "human-built diff, Codex independent" ]; then
    fail "$t" "expected recorded reason, got $reason. state=$(cat "$state_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 20: --skip-codex cannot run Codex as primary ----------
test_skip_codex_rejects_codex_primary() {
  local t="test_skip_codex_rejects_codex_primary"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex --no-fallback \
      --builder-family kimi --skip-codex "codex unavailable" \
      --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q -- "--skip-codex conflicts with --primary codex"; then
    fail "$t" "expected skip-codex/codex conflict, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 21: --skip-codex cannot leave Codex as fallback ----------
test_skip_codex_requires_no_fallback() {
  local t="test_skip_codex_requires_no_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax \
      --builder-family anthropic --skip-codex "codex unavailable" \
      --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q -- "--skip-codex requires --no-fallback"; then
    fail "$t" "expected skip-codex no-fallback error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 22: --skip-codex is recorded when builder-family-clean ----------
test_skip_codex_records_reason() {
  local t="test_skip_codex_records_reason"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax --no-fallback \
      --builder-family anthropic --skip-codex "codex unavailable" \
      --focus "test" --deadline 10 --heartbeat-interval 1 2>&1)
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

  local skip_reason
  skip_reason=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('skip_codex'))
")
  if [ "$skip_reason" != "codex unavailable" ]; then
    fail "$t" "expected skip_codex reason, got $skip_reason. state=$(cat "$state_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 23: direct Kimi reviewer rejects Kimi-built diffs ----------
test_direct_minimax_rejects_kimi_builder() {
  local t="test_direct_minimax_rejects_kimi_builder"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$REPO_ROOT/scripts/lib/minimax_review.py" \
      --scope diff --files target.py --builder-family kimi --json 2>&1)
  local rc=$?

  if [ "$rc" -ne 1 ]; then
    fail "$t" "expected rc=1, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family kimi cannot be reviewed by Kimi"; then
    fail "$t" "expected direct Kimi builder-family error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 24: direct Kimi reviewer requires no-fallback for OpenAI-built diffs ----------
test_direct_minimax_openai_requires_no_fallback() {
  local t="test_direct_minimax_openai_requires_no_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$REPO_ROOT/scripts/lib/minimax_review.py" \
      --scope diff --files target.py --builder-family openai --json 2>&1)
  local rc=$?

  if [ "$rc" -ne 1 ]; then
    fail "$t" "expected rc=1, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family openai requires --no-fallback"; then
    fail "$t" "expected direct Kimi no-fallback error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 25: direct Kimi reviewer requires reason for other builder ----------
test_direct_minimax_other_requires_reason() {
  local t="test_direct_minimax_other_requires_reason"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$REPO_ROOT/scripts/lib/minimax_review.py" \
      --scope diff --files target.py --builder-family other --no-fallback --json 2>&1)
  local rc=$?

  if [ "$rc" -ne 1 ]; then
    fail "$t" "expected rc=1, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family other requires --other-reviewer-reason"; then
    fail "$t" "expected direct Kimi other-reviewer reason error, got: $out"
    return
  fi
  pass "$t"
}

# ---------- Test 26: Anthropic-built diffs may use Codex ----------
test_anthropic_builder_accepts_codex() {
  local t="test_anthropic_builder_accepts_codex"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex \
      --builder-family anthropic --focus "test" \
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

  local reviewers builder_family
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  builder_family=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('builder_family'))
")
  if [ "$reviewers" != "codex" ]; then
    fail "$t" "expected only codex attempt, got reviewers=$reviewers state=$(cat "$state_path")"
    return
  fi
  if [ "$builder_family" != "anthropic" ]; then
    fail "$t" "expected builder_family=anthropic, got $builder_family"
    return
  fi
  pass "$t"
}

# ---------- Test 27: Anthropic-built diffs may use Kimi ----------
test_anthropic_builder_accepts_minimax() {
  local t="test_anthropic_builder_accepts_minimax"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary minimax \
      --builder-family anthropic --focus "test" \
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

  local reviewers builder_family
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  builder_family=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('builder_family'))
")
  if [ "$reviewers" != "minimax" ]; then
    fail "$t" "expected only minimax attempt, got reviewers=$reviewers state=$(cat "$state_path")"
    return
  fi
  if [ "$builder_family" != "anthropic" ]; then
    fail "$t" "expected builder_family=anthropic, got $builder_family"
    return
  fi
  pass "$t"
}

# ---------- Test 28: Anthropic-built diffs may fall back across independent reviewers ----------
test_anthropic_builder_allows_fallback() {
  local t="test_anthropic_builder_allows_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" error approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-plugin "$plugin" --primary codex \
      --builder-family anthropic --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 after minimax fallback, got rc=$rc. out=$out"
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

  local reviewers fallback_used
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  fallback_used=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(d.get('fallback_used'))
")
  if [ "$reviewers" != "codex,minimax" ]; then
    fail "$t" "expected attempts codex,minimax, got reviewers=$reviewers state=$(cat "$state_path")"
    return
  fi
  if [ "$fallback_used" != "True" ]; then
    fail "$t" "expected fallback_used=True, got $fallback_used"
    return
  fi
  pass "$t"
}

# ---------- Test 29: poll unknown job returns 1 ----------
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
test_plan_scope_runs_codex_primary
test_plan_scope_codex_fails_falls_back_to_minimax
test_plan_scope_isolates_codex_state
test_watchdog_injects_heartbeat
test_minimax_key_inherited_from_parent_env
test_primary_minimax_diff_requires_files_before_fallback
test_no_fallback_stops_after_primary_failure
test_openai_builder_rejects_codex_primary
test_openai_builder_accepts_minimax_no_fallback
test_kimi_builder_rejects_minimax_primary
test_kimi_builder_accepts_codex_no_fallback
test_other_builder_requires_no_fallback
test_other_builder_requires_explicit_primary
test_other_builder_requires_reason
test_other_builder_accepts_reason
test_skip_codex_rejects_codex_primary
test_skip_codex_requires_no_fallback
test_skip_codex_records_reason
test_direct_minimax_rejects_kimi_builder
test_direct_minimax_openai_requires_no_fallback
test_direct_minimax_other_requires_reason
test_anthropic_builder_accepts_codex
test_anthropic_builder_accepts_minimax
test_anthropic_builder_allows_fallback
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
