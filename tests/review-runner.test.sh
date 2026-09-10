#!/usr/bin/env bash
# tests/review-runner.test.sh
# Tests for scripts/lib/review_runner.py (Codex+Kimi fallback review runner).
#
# Architecture: each test builds a fresh temp git repo with:
#   - a dirty file (to satisfy the classifier's "something changed" requirement)
#   - a fake scripts/kimi-review.sh shim (inside the temp REPO_ROOT)
#   - a fake native Codex executable
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
  ((${#TMP_DIRS[@]})) || return 0
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

# Seed a fresh git repo with a dirty file, a fake Kimi shim, and a fake
# codex plugin. $1 = behavior for codex ("approve"|"hang"|"empty"|"error"),
# $2 = behavior for kimi ("approve"|"error"|"notfound").
seed_repo() {
  local d="$1"
  local codex_behavior="$2"
  local kimi_behavior="$3"

  cd "$d"
  git init -q
  git config user.email test@example.com
  git config user.name test

  # Gitignore the runner's archive dir + fake executable dir so their presence
  # doesn't trip the EISDIR guard (real K2B ships the same .gitignore entry
  # for .code-reviews/ via adaptation A4; /fake-bin/ is test-fixture only).
  cat > .gitignore <<EOF
/.code-reviews/
/fake-bin/
EOF

  # Seed scripts dir + Kimi shim (or leave scripts/kimi-review.sh missing if
  # behavior=notfound). The runner builds an absolute path to
  # scripts/kimi-review.sh via REPO_ROOT; the shim must exist at that path,
  # and the dir must be tracked by git so the EISDIR guard doesn't flag it.
  mkdir -p scripts
  case "$kimi_behavior" in
    approve)
      cat > scripts/kimi-review.sh <<'EOF'
#!/usr/bin/env bash
echo "# kimi-k2.7-code review -- APPROVE"
echo '{"verdict": "approve"}'
exit 0
EOF
      chmod +x scripts/kimi-review.sh
      ;;
    error)
      cat > scripts/kimi-review.sh <<'EOF'
#!/usr/bin/env bash
echo "kimi error" >&2
exit 1
EOF
      chmod +x scripts/kimi-review.sh
      ;;
    notfound)
      # Deliberately do NOT create the shim; add placeholder so scripts/
      # itself is tracked (otherwise untracked dir trips the EISDIR guard)
      echo "placeholder" > scripts/.placeholder
      ;;
  esac
  if [ "$kimi_behavior" != "notfound" ]; then
    cat > scripts/minimax-review.sh <<'EOF'
#!/usr/bin/env bash
echo "deprecated minimax alias should not be selected when scripts/kimi-review.sh exists" >&2
exit 1
EOF
    chmod +x scripts/minimax-review.sh
  fi

  # Commit the baseline so scripts/ and .gitignore are tracked. Only
  # target.py will be dirty when the runner scans.
  echo "dummy content" > target.py
  git add .gitignore scripts target.py
  git commit -q -m initial

  # Dirty file so the runner has something to review
  echo "dirty change" >> target.py

  # Fake native Codex executable (under /fake-bin/ which is gitignored).
  local plugin="$d/fake-bin/codex"
  mkdir -p "$d/fake-bin"
  case "$codex_behavior" in
    approve)
      cat > "$plugin" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "exec" ]; then cat >/dev/null; fi
printf '# Codex Review\nAPPROVE\n[codex] Review output captured.\n'
exit 0
EOF
      ;;
    hang)
      cat > "$plugin" <<'EOF'
#!/usr/bin/env bash
while :; do sleep 60; done
EOF
      ;;
    empty)
      cat > "$plugin" <<'EOF'
#!/usr/bin/env bash
printf 'Hello world (no verdict marker)\n'
exit 0
EOF
      ;;
    error)
      cat > "$plugin" <<'EOF'
#!/usr/bin/env bash
printf 'codex error\n' >&2
exit 1
EOF
      ;;
    missing)
      # Deliberately do NOT create the executable.
      :
      ;;
  esac
  [ "$codex_behavior" = "missing" ] || chmod +x "$plugin"

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
      --codex-executable "$plugin" --focus "test" 2>&1); then
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

# ---------- Test 2: Codex hang falls back to Kimi ----------
test_codex_hang_falls_back_to_kimi() {
  local t="test_codex_hang_falls_back_to_kimi"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" hang approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-executable "$plugin" --focus "test" \
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

  local attempts_codex attempts_kimi
  attempts_codex=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
for a in att:
    if a.get('reviewer')=='codex': print(a.get('result')); break
")
  attempts_kimi=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
att=d.get('reviewer_attempts', [])
for a in att:
    if a.get('reviewer')=='kimi': print(a.get('result')); break
")
  if [ "$attempts_codex" != "timed_out" ]; then
    fail "$t" "expected codex result=timed_out, got $attempts_codex"
    return
  fi
  if [ "$attempts_kimi" != "ok" ]; then
    fail "$t" "expected kimi result=ok, got $attempts_kimi"
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
      --codex-executable "$plugin" --focus "test" \
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
  # Use notfound for Kimi so fallback itself fails fast (avoids 10s grace blur).
  local plugin; plugin="$(seed_repo "$d" hang notfound)"

  cd "$d"
  local start_ts=$(date +%s)
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-executable "$plugin" --focus "test" \
      --deadline 2 --heartbeat-interval 1 2>&1)
  local rc=$?
  local end_ts=$(date +%s)
  local elapsed=$((end_ts - start_ts))

  # Runner should kill codex at deadline=2s, grace 10s = total <= 15s with fallback attempt
  if [ "$elapsed" -gt 30 ]; then
    fail "$t" "runner took ${elapsed}s, expected <=30s (deadline 2s + 10s grace + kimi spawn fail)"
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
      --codex-executable "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 after Kimi fallback approved, got rc=$rc. out=$out"
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
      --codex-executable "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 (Kimi approves), got rc=$rc. out=$out"
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
# Codex reviews the plan through native ephemeral, read-only `codex exec`, with
# the snapshotted prompt on stdin. Codex is primary; Kimi remains fallback.
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
      --codex-executable "$plugin" --focus "test" \
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
  # Codex must be invoked through the native ephemeral, read-only plan path.
  if ! grep -q "REVIEWER_START reviewer=codex" "$log_path"; then
    fail "$t" "expected codex reviewer to start. log=$(cat "$log_path")"
    return
  fi
  if ! grep -Eq "SPAWN argv=.*'exec'.*'--ephemeral'.*'--sandbox'.*'read-only'.*'-'" "$log_path"; then
    fail "$t" "expected native ephemeral read-only Codex plan review. log=$(cat "$log_path")"
    return
  fi
  if ! grep -q "# Codex Review" "$log_path"; then
    fail "$t" "expected Codex review output in log. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 7b: plan scope falls back to Kimi when Codex fails ----------
# Also proves the fallback actually receives plan SCOPE + the plan PATH (not a
# bare working-tree review): the shim below exits non-zero unless argv carries
# `--scope plan` and `--plan plans/tiny.md`, so a passing test guarantees Kimi
# reviews the plan file. (Without this argv guard the fake shim ignored argv and
# the regression guarantee was untested -- Codex plan-review P1 #4.)
test_plan_scope_codex_fails_falls_back_to_kimi() {
  local t="test_plan_scope_codex_fails_falls_back_to_kimi"
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
  cat > "$d/scripts/kimi-review.sh" <<'EOF'
#!/usr/bin/env bash
args="$*"
case "$args" in
  *"--scope plan"*"--plan plans/tiny.md"*) ;;
  *)
    echo "FALLBACK-ARGV-MISSING-PLAN-SCOPE: $args" >&2
    exit 1 ;;
esac
echo "# kimi-k2.7-code review -- APPROVE"
echo '{"verdict":"approve"}'
exit 0
EOF
  chmod +x "$d/scripts/kimi-review.sh"

  local out
  out=$(python3 "$RUNNER" plan --plan plans/tiny.md --wait \
      --codex-executable "$plugin" --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 after Kimi fallback, got rc=$rc. out=$out"
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
  if [ "$reviewers" != "codex,kimi" ]; then
    fail "$t" "expected attempts codex,kimi, got $reviewers. state=$(cat "$state_path")"
    return
  fi
  if [ "$fallback_used" != "True" ]; then
    fail "$t" "expected fallback_used True, got $fallback_used"
    return
  fi
  if ! grep -q "# kimi-k2.7-code review" "$log_path"; then
    fail "$t" "expected Kimi fallback output in log. log=$(cat "$log_path")"
    return
  fi
  pass "$t"
}

# ---------- Test 7c: plan-scope Codex is native, ephemeral, and read-only ----
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

  # Fake Codex records argv and the stdin prompt without exposing secrets.
  cat > "$plugin" <<'EOF'
#!/usr/bin/env bash
printf 'ARGV=%s\n' "$*"
prompt="$(cat)"
case "$prompt" in *BEGIN_PLAN_SNAPSHOT*END_PLAN_SNAPSHOT*) : ;; *) exit 9 ;; esac
printf 'CLAUDE_PLUGIN_ROOT=%s\n' "${CLAUDE_PLUGIN_ROOT:-UNSET}"
printf 'CLAUDE_PLUGIN_DATA=%s\n' "${CLAUDE_PLUGIN_DATA:-UNSET}"
printf '# Codex Review\nAPPROVE\n'
exit 0
EOF
  chmod +x "$plugin"

  local out
  out=$(python3 "$RUNNER" plan --plan plans/tiny.md --wait \
      --codex-executable "$plugin" --focus "test" \
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

  if ! grep -q -- 'ARGV=exec --ephemeral --sandbox read-only --cd' "$log_path"; then
    fail "$t" "native ephemeral read-only argv missing. log=$(cat "$log_path")"
    return
  fi
  grep -q 'CLAUDE_PLUGIN_ROOT=UNSET' "$log_path" || {
    fail "$t" "retired CLAUDE_PLUGIN_ROOT leaked to child"; return; }
  grep -q 'CLAUDE_PLUGIN_DATA=UNSET' "$log_path" || {
    fail "$t" "retired CLAUDE_PLUGIN_DATA leaked to child"; return; }
  pass "$t"
}

# ---------- Test A3: parent KIMI_API_KEY is inherited, not overwritten ----------
test_kimi_key_inherited_from_parent_env() {
  local t="test_kimi_key_inherited_from_parent_env"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  # Replace the standard Kimi shim with one that ECHOES the env var value
  # into its own stdout so we can verify inheritance.
  cat > "$d/scripts/kimi-review.sh" <<'EOF'
#!/usr/bin/env bash
echo "# kimi-k2.7-code review -- APPROVE"
echo "KEY-ECHO: [${KIMI_API_KEY:-UNSET}]"
exit 0
EOF
  chmod +x "$d/scripts/kimi-review.sh"

  cd "$d"
  local out
  out=$(KIMI_API_KEY="inherited-sentinel-xyz" python3 "$RUNNER" diff \
      --files target.py --wait --codex-executable "$plugin" \
      --primary kimi --focus "test" 2>&1)
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

# ---------- Test 10: primary Kimi diff requires explicit files ----------
test_primary_kimi_diff_requires_files_before_fallback() {
  local t="test_primary_kimi_diff_requires_files_before_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --wait \
      --codex-executable "$plugin" --primary kimi \
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
    fail "$t" "Codex fallback ran despite invalid Kimi diff args. out=$out"
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
      --codex-executable "$plugin" --primary kimi --no-fallback \
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

  # Strongest signal first: state file proves only Kimi attempted.
  local reviewers
  reviewers=$(python3 -c "
import json
d=json.loads(open('$state_path').read())
print(','.join(a.get('reviewer','') for a in d.get('reviewer_attempts', [])))
")
  if [ "$reviewers" != "kimi" ]; then
    fail "$t" "expected only kimi attempt, got reviewers=$reviewers state=$(cat "$state_path")"
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
      --codex-executable "$plugin" --primary codex \
      --builder-family openai --focus "test" 2>&1)
  local rc=$?

  if [ "$rc" -ne 2 ]; then
    fail "$t" "expected argv error rc=2, got rc=$rc. out=$out"
    return
  fi
  if ! echo "$out" | grep -q "builder-family openai requires --primary kimi --no-fallback"; then
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
test_openai_builder_accepts_kimi_no_fallback() {
  local t="test_openai_builder_accepts_kimi_no_fallback"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-executable "$plugin" --primary kimi --no-fallback \
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
  if [ "$reviewers" != "kimi" ]; then
    fail "$t" "expected only kimi attempt, got reviewers=$reviewers state=$(cat "$state_path")"
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
test_kimi_builder_rejects_kimi_primary() {
  local t="test_kimi_builder_rejects_kimi_primary"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-executable "$plugin" --primary kimi --no-fallback \
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
      --codex-executable "$plugin" --primary codex --no-fallback \
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
  if grep -q "REVIEWER_START reviewer=kimi" "$log_path"; then
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
      --codex-executable "$plugin" --primary codex \
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
      --codex-executable "$plugin" \
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
      --codex-executable "$plugin" --primary codex \
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
      --codex-executable "$plugin" --primary codex \
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
      --codex-executable "$plugin" --primary codex --no-fallback \
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
      --codex-executable "$plugin" --primary kimi \
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
      --codex-executable "$plugin" --primary kimi --no-fallback \
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
test_direct_kimi_reviewer_rejects_kimi_builder() {
  local t="test_direct_kimi_reviewer_rejects_kimi_builder"
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
test_direct_kimi_reviewer_openai_requires_no_fallback() {
  local t="test_direct_kimi_reviewer_openai_requires_no_fallback"
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
test_direct_kimi_reviewer_other_requires_reason() {
  local t="test_direct_kimi_reviewer_other_requires_reason"
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
      --codex-executable "$plugin" --primary codex \
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
test_anthropic_builder_accepts_kimi() {
  local t="test_anthropic_builder_accepts_kimi"
  local d; d="$(mktmp)"
  local plugin; plugin="$(seed_repo "$d" approve approve)"

  cd "$d"
  local out
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-executable "$plugin" --primary kimi \
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
  if [ "$reviewers" != "kimi" ]; then
    fail "$t" "expected only kimi attempt, got reviewers=$reviewers state=$(cat "$state_path")"
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
      --codex-executable "$plugin" --primary codex \
      --builder-family anthropic --focus "test" \
      --deadline 10 --heartbeat-interval 1 2>&1)
  local rc=$?

  if [ "$rc" -ne 0 ]; then
    fail "$t" "expected rc=0 after Kimi fallback, got rc=$rc. out=$out"
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
  if [ "$reviewers" != "codex,kimi" ]; then
    fail "$t" "expected attempts codex,kimi, got reviewers=$reviewers state=$(cat "$state_path")"
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

  # Slow the Kimi shim so the heartbeat thread runs at least once
  cat > "$d/scripts/kimi-review.sh" <<'EOF'
#!/usr/bin/env bash
sleep 2
echo "# kimi-k2.7-code review -- APPROVE"
echo '{"verdict":"approve"}'
exit 0
EOF
  chmod +x "$d/scripts/kimi-review.sh"

  cd "$d"
  local out
  # Use --primary kimi to skip Codex
  out=$(python3 "$RUNNER" diff --files target.py --wait \
      --codex-executable "$plugin" --primary kimi \
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
test_codex_hang_falls_back_to_kimi
test_both_fail_returns_exit_2
test_deadline_kill_after_n_seconds
test_quality_gate_no_verdict_forces_fallback
test_codex_unavailable_reason_eisdir
test_plan_scope_runs_codex_primary
test_plan_scope_codex_fails_falls_back_to_kimi
test_plan_scope_isolates_codex_state
test_watchdog_injects_heartbeat
test_kimi_key_inherited_from_parent_env
test_primary_kimi_diff_requires_files_before_fallback
test_no_fallback_stops_after_primary_failure
test_openai_builder_rejects_codex_primary
test_openai_builder_accepts_kimi_no_fallback
test_kimi_builder_rejects_kimi_primary
test_kimi_builder_accepts_codex_no_fallback
test_other_builder_requires_no_fallback
test_other_builder_requires_explicit_primary
test_other_builder_requires_reason
test_other_builder_accepts_reason
test_skip_codex_rejects_codex_primary
test_skip_codex_requires_no_fallback
test_skip_codex_records_reason
test_direct_kimi_reviewer_rejects_kimi_builder
test_direct_kimi_reviewer_openai_requires_no_fallback
test_direct_kimi_reviewer_other_requires_reason
test_anthropic_builder_accepts_codex
test_anthropic_builder_accepts_kimi
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
