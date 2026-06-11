# A3 commit-proposed-first + attempt-budget recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a CAPITAL-PATH fix — every code step is red-test-first, and the whole plan gets an adversarial review BEFORE the first commit and a pre-commit review before shipping.

**Goal:** Make the orchestrator A3 ship flow commit the strategy as `proposed` FIRST (so K2Bi's `run_full_ship` only does the hook-allowed `proposed -> approved`), and add an auditable `reset-ship-attempts` recovery so the spent flight `2026-06-07-001` can finish — landing CDNS at `terminal_shipped`.

**Architecture:** Two new K2B-side store steps slotted into the existing A3 resume ladder. (1) `a1_commit_ship_repo_proposed` makes a `(new file) -> proposed` git commit of the already-authored, sha-bound strategy file, recorded as a new ladder rung between `author_strategy_to_repo` and `dispatch_ship`. (2) `a1_reset_ship_attempts` is an operator-attested (`--i-checked-the-log`) reset of `ship_attempt_count`, guarded by a live `clean_rollback` inspection. No K2Bi code changes — `run_full_ship` was already built to approve a pre-committed `proposed` file.

**Tech Stack:** Python 3 (stdlib only), SQLite via `scripts/lib/orchestrator_store.py`, `subprocess` git, pytest in `tests/orchestrator/`, the K2Bi git hooks (`.githooks/`, active via `core.hooksPath`).

---

## The capital-line decision (READ FIRST — this is the load-bearing judgement the plan-review must check)

**Claim:** The orchestrator committing the strategy as `proposed` does NOT cross the capital line, so it can be done K2B-side without violating "K2B never hand-commits the capital path."

**Why:**
1. The engine only consumes `status: approved` strategies. A committed `proposed` strategy is an inert draft — no paper order can result from it. The capital-affecting commit is the `proposed -> approved` transition, which `run_full_ship` continues to own (reviews, `handle_approve_strategy`, the `git commit --only`, and the rollback). The orchestrator never touches that commit.
2. `run_full_ship` was ALREADY designed for a pre-committed `proposed` file: it reads the existing file (`invest_orchestrator_adapters.py:376 original = strategy_path.read_bytes()`), and `_assert_ship_clean_preflight` runs `git status --porcelain --untracked-files=no` and passes on a fully-clean tree (`invest_orchestrator_adapters.py:1521-1561`). The ONLY thing missing is that the orchestrator never made the proposed commit. This is purely an orchestrator gap, not a K2Bi defect.
3. The K2Bi commit-msg hook ALLOWS `(new file) -> proposed` with **no trailers required** (`.githooks/commit-msg`: "draft creation, no trailers required"). The pre-commit hook's Check B requires a non-empty `## How This Works` — the A2-authored CDNS strategy already has one. Check D (immutability) does not apply to a new file.
4. Direct precedent: the A4 limits proposed-first commit (`43e7655`) was the same move, and the standing follow-up chip is "`propose_limits` should auto-commit the proposal as proposed." There is NO native K2Bi adapter that commits a proposed strategy (history shows it was always a manual `git commit`), so dispatching K2Bi to do it would mean net-new K2Bi adapter surface (a PR + heavier review) for zero added safety.

**Guard rails that keep this indisputably safe (all enforced inside `a1_commit_ship_repo_proposed`):**
- REFUSES unless the on-disk frontmatter `status` is exactly `proposed`. It can never commit an `approved` file — that capital commit stays with `run_full_ship`.
- Commits ONLY the single target path: `git add -- <relpath>` then `git commit --only -m <msg> -- <relpath>`. It cannot sweep a sibling session's staged files (the handoff's explicit warning) or an approved file.
- Re-checks the on-disk sha equals the `record-ship-repo-authored` bound sha (`ship_strategy_repo_sha256`) before staging.
- **TOCTOU close (Codex plan-review F2):** after `git add`, re-verifies the *staged blob* (`git show :<relpath>`) sha == the bound sha AND its frontmatter status == proposed BEFORE commit. `git commit --only` commits the index, so a sibling edit landing in the worktree after staging cannot change what is committed, and a sibling edit landing in the index is caught here. Commits exactly the bytes A2 backtested + the operator approved.
- Asserts the rest of the tree is clean-except-target before committing (mirrors `_assert_ship_clean_preflight`); refuses otherwise.
- Idempotent: if the file is already tracked-at-HEAD as `proposed` at the bound sha, it records HEAD and returns ok (no double-commit on resume).
- Fail-closed: a non-zero `git commit` (e.g. a hook rejection) returns `(False, reason)`, unstages the target, and leaves state unchanged, so the operator sees the hook error.
- (Considered + deferred: also taking K2Bi's per-strategy `_strategy_ship_lock`. Deferred because `commit --only` commits the verified index blob, not the worktree, so the staged-blob re-verify already closes the race without cross-repo lock coupling. Re-raise if the pre-commit review still wants the lock.)

**PR boundary:** The planned core fix is **100% K2B-side** (`scripts/lib/orchestrator_store.py`, the SKILL, K2B tests). No K2Bi code change. IF the plan-review or the live run surfaces a genuinely-required K2Bi change, THAT one ships via PR with Keith's go/no-go — but the plan does not anticipate one.

---

## File structure

| File | Change |
|---|---|
| `scripts/lib/orchestrator_store.py` | ADD `a1_commit_ship_repo_proposed()`, ADD `a1_reset_ship_attempts()`, ADD `_a3_git_commit_path()` helper, EDIT the A3 resume ladder (new `commit_strategy_proposed` rung), EDIT `a1_record_ship_repo_authored` clear-list (+ proposed-commit markers), ADD two CLI subparsers + dispatch. |
| `tests/orchestrator/test_orchestrator_a3.py` | ADD tests for the new commit step + reset helper; UPDATE the existing ladder/dispatch helpers + tests that assert `dispatch_ship` right after `record_ship_repo_authored`. |
| `.claude/skills/k2b-orchestrator/SKILL.md` | EDIT the A3 conductor: new ladder rung, the proposed-commit procedure step, the reset-ship-attempts recovery sequence. |

No new files. No K2Bi files.

---

## Resume ladder: before vs after

Current A3 ladder (`orchestrator_store.py:1143-1155`):
```
ship_authorized? no -> strategy_approved_await_ship
ship_verified -> terminal_shipped
ship_partial_detected_at -> ship_partial
ship_rolled_back_at -> ship_rolled_back
ship_repo_authored? no -> author_strategy_to_repo
ship_dispatch_started_at? no -> dispatch_ship
else -> verify_ship
```

After (one new rung inserted, GATED on not-yet-dispatched per Codex F1):
```
...
ship_repo_authored? no -> author_strategy_to_repo
(not ship_dispatch_started_at) and (not ship_proposed_commit_sha) -> commit_strategy_proposed   # NEW
ship_dispatch_started_at? no -> dispatch_ship
else -> verify_ship
```
The `not ship_dispatch_started_at` guard is essential: a LEGACY flight dispatched under the old code has `ship_dispatch_started_at` set but no `ship_proposed_commit_sha`. Without the guard it would route backward to `commit_strategy_proposed` and cancel its queued ship child as out-of-order. With the guard, a dispatched flight always resolves to `verify_ship`.

New flight payload keys: `ship_proposed_commit_sha`, `ship_proposed_committed_at`, `ship_attempts_reset_at`, `ship_attempts_reset_reason`.

---

## Task 1: `a1_commit_ship_repo_proposed` store step (the core fix)

**Files:**
- Modify: `scripts/lib/orchestrator_store.py` (add the function near `a1_record_ship_repo_authored`, ~line 3107; add `_a3_git_commit_path` helper near the other `_a3_git_*` helpers)
- Test: `tests/orchestrator/test_orchestrator_a3.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/orchestrator/test_orchestrator_a3.py` inside `class TestA3OracleAndHelpers`:

```python
    def test_commit_ship_repo_proposed_makes_proposed_commit(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        ok, reason = store.a1_authorize_ship(tid)
        assert ok, reason
        ok, reason = store.a1_record_ship_repo_authored(tid, str(proposed))
        assert ok, reason
        # The new ladder rung sits between author and dispatch.
        assert store.a1_resume_action(tid) == "commit_strategy_proposed"

        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert ok, reason
        # File is tracked at HEAD as proposed; payload records the proposed commit.
        assert _git(repo, "ls-files", "wiki/strategies/strategy_cdns.md").stdout.strip()
        head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["ship_proposed_commit_sha"] == head
        assert payload["ship_strategy_repo_sha256"] == _sha256(proposed)
        assert store.a1_resume_action(tid) == "dispatch_ship"

    def test_commit_ship_repo_proposed_is_idempotent(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        ok, _ = store.a1_commit_ship_repo_proposed(tid)
        assert ok
        head1 = _git(repo, "rev-parse", "HEAD").stdout.strip()
        # Second call is a no-op success (no new commit, no error).
        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert ok, reason
        assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head1

    def test_commit_ship_repo_proposed_refuses_non_proposed_status(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        strat = repo / "wiki" / "strategies" / "strategy_cdns.md"
        proposed = _strategy_file(strat)
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        # Flip the on-disk file to approved AFTER the sha bind -> the step must refuse.
        _strategy_file(strat, status="approved")
        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert not ok
        assert "proposed" in reason.lower()

    def test_commit_ship_repo_proposed_refuses_sha_drift_after_bind(self, store, tmp_path, monkeypatch):
        # A still-proposed but byte-changed file after the bind must refuse (commit exactly what A2 approved).
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        strat = repo / "wiki" / "strategies" / "strategy_cdns.md"
        proposed = _strategy_file(strat)
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        _strategy_file(strat, status="proposed", body="tampered-but-still-proposed")
        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert not ok
        assert "sha256" in reason.lower()
        assert _git(repo, "status", "--porcelain").stdout  # uncommitted (refused, file untracked)

    def test_commit_ship_repo_proposed_refuses_unrelated_dirty_tree(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        # A sibling-session dirty file elsewhere in the tree must block the proposed commit.
        (repo / "execution" / "validators" / "config.yaml").write_text("tampered\n", encoding="utf-8")
        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert not ok
        assert "clean" in reason.lower() or "unrelated" in reason.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k commit_ship_repo_proposed -v`
Expected: FAIL with `AttributeError: module 'scripts.lib.orchestrator_store' has no attribute 'a1_commit_ship_repo_proposed'`.

- [ ] **Step 3: Add the git helpers (correct argv + staged-blob read)**

Add near the other `_a3_git_*` helpers in `scripts/lib/orchestrator_store.py`:

```python
def _a3_git_staged_blob_sha(repo: Path, rel_path: str) -> tuple[str | None, bytes | None]:
    """Return (sha256, bytes) of the STAGED blob for rel_path, or (None, None)."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f":{rel_path}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return (None, None)
    blob = proc.stdout
    return (hashlib.sha256(blob).hexdigest(), blob)


def _a3_git_commit_only(repo: Path, rel_path: str, message: str) -> tuple[bool, str]:
    """Commit ONLY the already-staged rel_path. Correct argv: options before `--`."""
    commit = subprocess.run(
        ["git", "-C", str(repo), "commit", "--only", "-m", message, "--", rel_path],
        capture_output=True, text=True,
    )
    if commit.returncode != 0:
        return (False, (commit.stderr or commit.stdout).strip())
    return (True, (commit.stdout or "").strip())
```

Codex F2 argv fix: `git commit --only -m <message> -- <relpath>` — options BEFORE `--`, pathspec AFTER. The earlier draft put `-m` after `--`, which git would treat as a pathspec. `subprocess`/`hashlib` are already imported in this module.

- [ ] **Step 4: Add the store step**

Add after `a1_record_ship_repo_authored` (~line 3107):

```python
def a1_commit_ship_repo_proposed(task_id: str) -> tuple[bool, str]:
    """Commit the repo-authored strategy as `proposed` ((new file)->proposed).

    This is the strategy analog of the A4 commit-proposed-first fix: K2Bi's
    commit-msg hook forbids (new file)->approved, so the proposed draft must
    land tracked FIRST; run_full_ship then only does proposed->approved.
    Commits ONLY the target path; never an approved file; idempotent.
    """
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        action = a1_resume_action_locked(conn, task_id)
        if action != "commit_strategy_proposed":
            conn.close()
            return (False, f"proposed commit requires resume action commit_strategy_proposed (got {action!r})")
        raw_path = payload.get("ship_strategy_repo_path")
        if not raw_path:
            conn.close()
            return (False, "ship_strategy_repo_path missing")
        path = Path(str(raw_path)).expanduser()
        if not path.is_file():
            conn.close()
            return (False, f"repo strategy missing or not a file: {path}")
        recorded_sha = str(payload.get("ship_strategy_repo_sha256") or "").strip()
        if not recorded_sha:
            conn.close()
            return (False, "ship_strategy_repo_sha256 missing; record repo authorship first")
        current_sha = _sha256_file(path)
        if current_sha != recorded_sha:
            conn.close()
            return (False, f"repo strategy sha256 {current_sha} changed since repo authorship record {recorded_sha}")
        fm, perr = _parse_md_frontmatter(path)
        if fm is None:
            conn.close()
            return (False, f"repo strategy frontmatter invalid: {perr}")
        status_value = str(fm.get("status") or "").strip().lower()
        if status_value != "proposed":
            conn.close()
            return (False, f"repo strategy status must be 'proposed' to commit a draft, got {status_value!r}")
        repo = _a3_git_repo_root_for_path(path)
        if repo is None:
            conn.close()
            return (False, "cannot resolve K2Bi git repo for strategy path")
        try:
            rel_path = path.resolve(strict=False).relative_to(repo.resolve(strict=False)).as_posix()
        except ValueError:
            conn.close()
            return (False, f"strategy path is outside git repo: {path}")
        # Idempotent: already tracked at HEAD at the bound sha -> record HEAD, done.
        if _a3_git_path_tracked_at_head(repo, path) and _a3_git_status_for_path(repo, path) == "":
            head = _a3_git_head(repo)
            payload["ship_proposed_commit_sha"] = head
            payload.setdefault("ship_proposed_committed_at", now_iso())
            _update_payload_locked(conn, task_id, payload, status=task["status"])
            conn.commit()
            conn.close()
            return (True, "")
        # Clean-tree-except-target guard (mirror _assert_ship_clean_preflight intent):
        # refuse if any path OTHER than the target is dirty/untracked.
        status_all = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"],
            capture_output=True, text=True,
        )
        if status_all.returncode != 0:
            conn.close()
            return (False, f"git status failed: {(status_all.stderr or status_all.stdout).strip()}")
        dirty_other = [
            ln for ln in status_all.stdout.splitlines()
            if ln and ln[3:].strip() != rel_path
        ]
        if dirty_other:
            conn.close()
            return (False, f"K2Bi tree has unrelated changes; refuse proposed commit: {'; '.join(dirty_other[:5])}")
        # Stage ONLY the target, then re-verify the STAGED blob before commit (Codex F2 TOCTOU close).
        add = subprocess.run(["git", "-C", str(repo), "add", "--", rel_path], capture_output=True, text=True)
        if add.returncode != 0:
            conn.close()
            return (False, f"git add failed: {(add.stderr or add.stdout).strip()}")
        staged_sha, _staged_bytes = _a3_git_staged_blob_sha(repo, rel_path)
        # sha-identity to the bound proposed bytes is sufficient: the on-disk status==proposed was
        # already verified above, and recorded_sha is THAT proposed file's sha, so a staged-sha match
        # proves the staged blob is byte-identical to the verified proposed file (no status re-parse needed).
        if staged_sha != recorded_sha:
            subprocess.run(["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path], capture_output=True, text=True)
            conn.close()
            return (False, f"staged blob sha {staged_sha} != bound proposed sha {recorded_sha}; refusing commit (mid-flight mutation)")
        slug = _a3_strategy_slug_from_path(path)
        message = (
            f"feat(strategy): propose strategy_{slug} (status: proposed)\n\n"
            f"Orchestrator A3 proposed-first commit for flight {task_id}.\n"
            f"Strategy-Transition: (new file) -> proposed"
        )
        ok, out = _a3_git_commit_only(repo, rel_path, message)
        if not ok:
            subprocess.run(["git", "-C", str(repo), "reset", "-q", "HEAD", "--", rel_path], capture_output=True, text=True)
            conn.close()
            return (False, f"proposed commit refused: {out}")
        head = _a3_git_head(repo)
        payload["ship_proposed_commit_sha"] = head
        payload["ship_proposed_committed_at"] = now_iso()
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k commit_ship_repo_proposed -v`
Expected: 4 PASS. (The new rung assertion in test 1 will still fail until Task 2 — run Task 2 then re-run.)

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/orchestrator_store.py tests/orchestrator/test_orchestrator_a3.py
git commit -m "feat(orchestrator): a1_commit_ship_repo_proposed — A3 commit-proposed-first step"
```

---

## Task 2: New `commit_strategy_proposed` resume rung + reconcile re-author clear-list

**Files:**
- Modify: `scripts/lib/orchestrator_store.py:1151-1154` (ladder), `:3082-3102` (`a1_record_ship_repo_authored` clear-list)
- Test: covered by Task 1 test 1 (`assert ... == "commit_strategy_proposed"`) + a re-author reset test below

- [ ] **Step 1: Write the failing test**

Add to `class TestA3OracleAndHelpers`:

```python
    def test_reauthor_resets_proposed_commit_marker(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        store.a1_commit_ship_repo_proposed(tid)
        assert store.a1_resume_action(tid) == "dispatch_ship"
        # A fresh re-author must drop the proposed-commit marker so it re-runs.
        ok, reason = store.a1_record_ship_repo_authored(tid, str(proposed))
        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert "ship_proposed_commit_sha" not in payload
        assert store.a1_resume_action(tid) == "commit_strategy_proposed"

    def test_dispatched_legacy_flight_without_proposed_marker_routes_to_verify(self, store, tmp_path):
        # Codex F1: a flight already dispatched under the old code (no ship_proposed_commit_sha)
        # must resolve to verify_ship, NOT route backward to commit_strategy_proposed.
        tid = _a3_parent(
            store, tmp_path,
            payload_updates={
                "ship_authorized": True,
                "ship_repo_authored": True,
                "ship_strategy_repo_path": str(tmp_path / "strategy_cdns.md"),
                "ship_strategy_repo_sha256": "abc",
                "ship_dispatch_started_at": "2026-06-08T00:00:00+00:00",
            },
        )
        assert store.a1_resume_action(tid) == "verify_ship"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k reauthor_resets_proposed -v`
Expected: FAIL — resume returns `dispatch_ship` (marker not cleared) instead of `commit_strategy_proposed`.

- [ ] **Step 3: Insert the ladder rung (GATED on not-yet-dispatched — Codex F1)**

In `scripts/lib/orchestrator_store.py`, edit `a1_resume_action_locked` (after the `author_strategy_to_repo` rung, ~line 1152):

```python
    if not payload.get("ship_repo_authored"):
        return "author_strategy_to_repo"
    if not payload.get("ship_dispatch_started_at") and not payload.get("ship_proposed_commit_sha"):
        return "commit_strategy_proposed"
    if not payload.get("ship_dispatch_started_at"):
        return "dispatch_ship"
    return "verify_ship"
```

The `not ship_dispatch_started_at` guard prevents a legacy/in-flight dispatched flight (which lacks `ship_proposed_commit_sha`) from routing backward and cancelling its queued ship child.

- [ ] **Step 4: Add the markers to the re-author clear-list**

In `a1_record_ship_repo_authored`, add to the `for key in (...)` clear-list (~line 3082, alongside `ship_dispatch_started_at`):

```python
            "ship_proposed_commit_sha",
            "ship_proposed_committed_at",
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k "commit_ship_repo_proposed or reauthor_resets_proposed" -v`
Expected: all PASS (including test 1's `commit_strategy_proposed` assertion).

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/orchestrator_store.py tests/orchestrator/test_orchestrator_a3.py
git commit -m "feat(orchestrator): commit_strategy_proposed resume rung + re-author marker reset"
```

---

## Task 3: CLI subparser `commit-ship-repo-proposed`

**Files:**
- Modify: `scripts/lib/orchestrator_store.py` (subparser block ~line 4146 + dispatch block ~line 4529)
- Test: subprocess CLI smoke in the A3 test

- [ ] **Step 1: Write the failing test**

Add to `class TestA3OracleAndHelpers`:

```python
    def test_cli_commit_ship_repo_proposed(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        env = dict(os.environ)
        env["K2B_ORCH_DB"] = str(tmp_path / "orch.sqlite")
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.lib.orchestrator_store", "commit-ship-repo-proposed", tid],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        )
        assert proc.returncode == 0, proc.stderr
        assert store.a1_resume_action(tid) == "dispatch_ship"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k cli_commit_ship_repo_proposed -v`
Expected: FAIL — argparse exits non-zero (`invalid choice: 'commit-ship-repo-proposed'`).

- [ ] **Step 3: Add the subparser**

After the `record_ship_repo_p` subparser block (~line 4151):

```python
    commit_ship_proposed_p = sub.add_parser(
        "commit-ship-repo-proposed",
        help="Commit the A3 repo-authored strategy as proposed ((new file)->proposed)",
    )
    commit_ship_proposed_p.add_argument("id")
```

- [ ] **Step 4: Add the dispatch**

In the command dispatch chain (near the `record-ship-repo-authored` handler ~line 4529):

```python
    if args.cmd == "commit-ship-repo-proposed":
        ok, reason = a1_commit_ship_repo_proposed(args.id)
        if ok:
            print(f"commit-ship-repo-proposed {args.id}: proposed strategy committed")
            return 0
        print(f"commit-ship-repo-proposed rejected {args.id}: {reason}", file=sys.stderr)
        return 1
```

(Match the exact return/print idiom of the adjacent handlers — confirm the surrounding block uses `return 0/1` vs `sys.exit`.)

- [ ] **Step 5: Run to verify pass**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k cli_commit_ship_repo_proposed -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/orchestrator_store.py tests/orchestrator/test_orchestrator_a3.py
git commit -m "feat(orchestrator): commit-ship-repo-proposed CLI subcommand"
```

---

## Task 4: `a1_reset_ship_attempts` recovery step + CLI

**Files:**
- Modify: `scripts/lib/orchestrator_store.py` (add function near `a1_retry_ship_after_rollback` ~line 3380; subparser + dispatch)
- Test: `tests/orchestrator/test_orchestrator_a3.py`

- [ ] **Step 1: Write the failing tests**

```python
    def test_reset_ship_attempts_requires_checked_log(self, store, tmp_path, monkeypatch):
        repo, proposed, tid = _spent_rolled_back_flight(store, tmp_path, monkeypatch)
        ok, reason = store.a1_reset_ship_attempts(tid, checked_log=False, reason="flow bugs")
        assert not ok
        assert "i-checked-the-log" in reason.lower() or "checked" in reason.lower()

    def test_reset_ship_attempts_requires_clean_rollback(self, store, tmp_path, monkeypatch):
        # A partial (approved-uncommitted) flight must NEVER get its attempts reset.
        repo, proposed, tid = _dispatched_ship(store, tmp_path, monkeypatch)
        _strategy_file(proposed, status="approved")  # approved on disk, never committed -> partial
        store.a1_verify_ship(tid)
        # Force the attempt counter to the limit to exercise the guard.
        _force_payload(store, tid, {"ship_attempt_count": 3})
        ok, reason = store.a1_reset_ship_attempts(tid, checked_log=True, reason="x")
        assert not ok
        assert "clean_rollback" in reason

    def test_reset_ship_attempts_clears_limit_and_routes_to_recovery(self, store, tmp_path, monkeypatch):
        repo, proposed, tid = _spent_rolled_back_flight(store, tmp_path, monkeypatch)
        # At the limit with terminal_reason set, the oracle parks needs_human_terminal.
        assert store.a1_resume_action(tid) == "needs_human_terminal"
        ok, reason = store.a1_reset_ship_attempts(tid, checked_log=True, reason="3 attempts died on now-fixed flow bugs")
        assert ok, reason
        payload = json.loads(store.get_task(tid)["payload"])
        assert payload["ship_attempt_count"] == 0
        assert "terminal_reason" not in payload
        assert payload["ship_attempts_reset_reason"].startswith("3 attempts")
        # Falls through to ship_rolled_back -> operator runs retry-ship next.
        assert store.a1_resume_action(tid) == "ship_rolled_back"
```

Add these test helpers near `_dispatched_ship`:

```python
def _force_payload(store, tid, updates):
    with store._acquire_lock():
        conn = store.connect()
        store.init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        payload = json.loads(dict(row)["payload"])
        payload.update(updates)
        store._update_payload_locked(conn, tid, payload, status=dict(row)["status"])
        conn.commit()
        conn.close()


def _spent_rolled_back_flight(store, tmp_path, monkeypatch):
    """A flight at attempt 3/3, terminal_reason=ship_attempt_limit_exceeded, clean rollback."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
    proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
    tid = _a3_parent(
        store, tmp_path,
        payload_updates={
            "ship_authorized": True,
            "ship_repo_authored": True,
            "ship_strategy_repo_path": str(proposed),
            "ship_strategy_repo_sha256": _sha256(proposed),
            "strategy_artifact_sha256": _sha256(proposed),
            "ship_attempt_count": 3,
            "ship_rolled_back_at": "2026-06-10T00:00:00+00:00",
            "ship_rollback_clean": True,
            "terminal_reason": "ship_attempt_limit_exceeded",
            "terminal_reason_at": "2026-06-10T00:00:00+00:00",
        },
    )
    return repo, proposed, tid
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k reset_ship_attempts -v`
Expected: FAIL with `AttributeError: ... 'a1_reset_ship_attempts'`.

- [ ] **Step 3: Add the store function**

Add near `a1_retry_ship_after_rollback` (~line 3380):

```python
def a1_reset_ship_attempts(task_id: str, *, checked_log: bool, reason: str | None) -> tuple[bool, str]:
    """Operator-attested reset of the A3 ship attempt budget.

    Justified only when the spent attempts died on now-fixed FLOW bugs, not a
    flaky strategy or a partial ship. Guarded like the other --i-checked-the-log
    recoveries: requires a live clean_rollback inspection and an explicit ack.
    Resets ship_attempt_count and clears the ship_attempt_limit_exceeded terminal
    marker ONLY; leaves ship_rolled_back_at so the operator still runs retry-ship.
    """
    if not checked_log:
        return (False, "reset requires --i-checked-the-log (operator must confirm the spent attempts died on fixed flow bugs)")
    if not (reason and reason.strip()):
        return (False, "reset requires a --reason")
    with _acquire_lock():
        conn = connect()
        init_db(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            conn.close()
            return (False, f"task {task_id} not found")
        task = dict(row)
        if task["status"] in TERMINAL_STATUSES:
            conn.close()
            return (False, f"task {task_id} is terminal: {task['status']}")
        payload = _payload_dict(task.get("payload"))
        inspect = _a1_inspect_ship_state_from_payload(task_id, payload)
        if inspect.get("state") != "clean_rollback":
            conn.close()
            return (False, f"reset requires live inspect state clean_rollback (got {inspect.get('state')!r})")
        payload["ship_attempt_count"] = 0
        payload["ship_attempts_reset_at"] = now_iso()
        payload["ship_attempts_reset_reason"] = reason.strip()
        # Clear ONLY the attempt-limit terminalization; never touch an unrelated terminal_reason.
        if payload.get("terminal_reason") == "ship_attempt_limit_exceeded":
            payload.pop("terminal_reason", None)
            payload.pop("terminal_reason_at", None)
        _update_payload_locked(conn, task_id, payload, status=task["status"])
        conn.commit()
        conn.close()
    return (True, "")
```

- [ ] **Step 4: Add the CLI subparser + dispatch**

Subparser (after `retry_ship_p` ~line 4176):

```python
    reset_ship_attempts_p = sub.add_parser(
        "reset-ship-attempts",
        help="Operator-attested reset of the A3 ship attempt budget (clean_rollback only)",
    )
    reset_ship_attempts_p.add_argument("id")
    reset_ship_attempts_p.add_argument("--i-checked-the-log", action="store_true",
        help="Required ack that the spent attempts died on now-fixed flow bugs")
    reset_ship_attempts_p.add_argument("--reason", required=True)
```

Dispatch (near the `retry-ship` handler ~line 4564):

```python
    if args.cmd == "reset-ship-attempts":
        ok, reason = a1_reset_ship_attempts(args.id, checked_log=args.i_checked_the_log, reason=args.reason)
        if ok:
            print(f"reset-ship-attempts {args.id}: attempt budget reset to 0")
            return 0
        print(f"reset-ship-attempts rejected {args.id}: {reason}", file=sys.stderr)
        return 1
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k reset_ship_attempts -v`
Expected: 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/lib/orchestrator_store.py tests/orchestrator/test_orchestrator_a3.py
git commit -m "feat(orchestrator): reset-ship-attempts — operator-attested A3 attempt-budget recovery"
```

---

## Task 5: Reconcile existing A3 tests with the new ladder rung (regression)

The new rung changes the resume action immediately after `record_ship_repo_authored` from `dispatch_ship` to `commit_strategy_proposed`, and any flight built with `ship_repo_authored: True` but no `ship_proposed_commit_sha` now resolves to `commit_strategy_proposed`. The shared `_dispatched_ship` helper and several tests assume the old behavior.

**Files:**
- Modify: `tests/orchestrator/test_orchestrator_a3.py`

- [ ] **Step 1: Update `_dispatched_ship` to make the proposed commit**

Insert the proposed-commit step before `a1_mark_ship_dispatch_started` (line 198) so the helper produces a realistic post-proposed-commit flight:

```python
def _dispatched_ship(store, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
    proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")
    tid = _a3_parent(
        store, tmp_path,
        payload_updates={
            "ship_authorized": True,
            "ship_repo_authored": True,
            "ship_strategy_repo_path": str(proposed),
            "ship_strategy_repo_sha256": _sha256(proposed),
            "strategy_artifact_sha256": _sha256(proposed),
        },
    )
    ok, reason = store.a1_commit_ship_repo_proposed(tid)   # NEW: land proposed at HEAD first
    assert ok, reason
    ok, dispatch = store.a1_mark_ship_dispatch_started(tid)
    assert ok, dispatch
    return repo, proposed, tid
```

- [ ] **Step 2: Update `test_a3_ladder_to_terminal_shipped`**

Between the `record_ship_repo_authored` block (line 282-284) and the `mark_ship_dispatch_started` block (line 286), change the resume assertion and insert the commit step:

```python
        ok, reason = store.a1_record_ship_repo_authored(tid, str(proposed))
        assert ok, reason
        assert store.a1_resume_action(tid) == "commit_strategy_proposed"

        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert ok, reason
        assert store.a1_resume_action(tid) == "dispatch_ship"

        ok, dispatch = store.a1_mark_ship_dispatch_started(tid)
```

The later block (lines 292-295) that simulates the approved commit stays — but `head_before` there is now the PROPOSED commit, and the approved commit advances HEAD past it, which is exactly what `head_advanced` needs. Keep it. (`approved_commit_sha=head_before` in `_strategy_file` is cosmetic frontmatter; unaffected.)

- [ ] **Step 3: Audit the other direct-`mark_ship_dispatch_started` tests**

These build a flight with `ship_repo_authored: True` and call `a1_mark_ship_dispatch_started` directly:
`test_mark_ship_dispatch_started_bounds_attempts` (385), `_refuses_missing_recorded_repo_sha` (405), `_refuses_missing_git_repo_baseline` (424), `_refuses_unavailable_git_head_baseline` (448). With the new rung, their resume action is `commit_strategy_proposed`, so `mark_ship_dispatch_started` would refuse for the WRONG reason ("requires resume action dispatch_ship").

For EACH of these, seed `ship_proposed_commit_sha` in the `payload_updates` so the flight is at the dispatch rung:
```python
                "ship_proposed_commit_sha": "deadbeef",
```
(These tests assert behavior of `mark_ship_dispatch_started` itself — bounds, missing-sha, missing-git-baseline — none of which depend on a real proposed commit existing, so a sentinel marker is correct and minimal. `_refuses_missing_recorded_repo_sha` keeps NOT setting `ship_strategy_repo_sha256` — but note the new rung needs `ship_proposed_commit_sha`; setting only that sentinel still leaves the flight at `dispatch_ship` and the test still exercises the missing-recorded-sha refusal inside `mark_ship_dispatch_started`.)

`test_retry_ship_requires_live_clean_rollback` (550): it uses `_dispatched_ship` (now makes a real proposed commit), then simulates a rollback. After `retry-ship`, the flight keeps `ship_proposed_commit_sha`, so the post-retry assertion `== "dispatch_ship"` (line 574) stays correct. Verify it passes unchanged after the `_dispatched_ship` update.

- [ ] **Step 4: Run the full A3 suite**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -v`
Expected: all PASS (40 existing + 8 new). Fix any test whose assumption the new rung broke — by inserting the commit step or seeding the marker, never by weakening a capital guard.

- [ ] **Step 5: Commit**

```bash
git add tests/orchestrator/test_orchestrator_a3.py
git commit -m "test(orchestrator): reconcile A3 ladder tests with commit_strategy_proposed rung"
```

---

## Task 5b: Hook-fidelity integration test (Codex F3 — prove the LIVE hook path)

The unit tests use a temp repo with no `.githooks`, so they pass even though the real K2Bi pre-commit hook (Check B) rejects a proposed strategy with no `## How This Works`. A capital-path fix must prove the real hook accepts a valid proposed file and rejects an invalid one. This test runs the proposed commit through the REAL K2Bi hooks.

**Files:**
- Modify: `tests/orchestrator/test_orchestrator_a3.py`

- [ ] **Step 1: Add a hook-valid fixture and a hooks-installed repo helper**

The K2Bi pre-commit hook imports `scripts.lib.strategy_frontmatter`, so the temp repo must carry both `.githooks/` and the `scripts/lib/` helpers it depends on. Copy them from the real repo and point `core.hooksPath` at the copy:

```python
import shutil

def _strategy_file_hook_valid(path: Path, *, slug: str = "cdns", ticker: str = "CDNS", status: str = "proposed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {slug}\nticker: {ticker}\nstatus: {status}\n"
        "order:\n"
        f"  ticker: {ticker}\n"
        "---\n"
        f"# Strategy {slug}\n\n"
        "## How This Works\n\n"
        "Buy only while the operator-approved thesis holds; exit on a thesis break.\n",
        encoding="utf-8",
    )
    return path


def _init_git_repo_with_k2bi_hooks(repo: Path) -> bool:
    """Temp repo wired to the REAL K2Bi hooks. Returns False (skip) if the source repo is absent."""
    src = K2BI_REPO
    if not (src / ".githooks").is_dir() or not (src / "scripts" / "lib" / "strategy_frontmatter.py").is_file():
        return False
    _init_git_repo(repo)
    shutil.copytree(src / ".githooks", repo / ".githooks")
    (repo / "scripts" / "lib").mkdir(parents=True, exist_ok=True)
    (repo / "scripts" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "scripts" / "lib" / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(src / "scripts" / "lib" / "strategy_frontmatter.py", repo / "scripts" / "lib" / "strategy_frontmatter.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "hooks+frontmatter")
    _git(repo, "config", "core.hooksPath", ".githooks")
    return True
```

(If the hook references additional helpers beyond `strategy_frontmatter.py`, copy those too — discover them by running the test once and reading the import error. Do NOT stub the hook; the point is fidelity.)

- [ ] **Step 2: Write the integration tests**

```python
    def test_commit_ship_repo_proposed_passes_real_k2bi_hook(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        if not _init_git_repo_with_k2bi_hooks(repo):
            pytest.skip("K2Bi repo/hooks not available")
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        proposed = _strategy_file_hook_valid(repo / "wiki" / "strategies" / "strategy_cdns.md")
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert ok, reason  # real (new file)->proposed commit accepted by the live hook
        assert _git(repo, "ls-files", "wiki/strategies/strategy_cdns.md").stdout.strip()

    def test_commit_ship_repo_proposed_surfaces_hook_rejection(self, store, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        if not _init_git_repo_with_k2bi_hooks(repo):
            pytest.skip("K2Bi repo/hooks not available")
        monkeypatch.setenv("K2B_ORCH_K2BI_WORKSPACE", str(repo))
        # Missing '## How This Works' -> Check B must reject; the step must fail-closed, state unchanged.
        proposed = _strategy_file(repo / "wiki" / "strategies" / "strategy_cdns.md")  # no How This Works
        tid = _a3_parent(store, tmp_path, payload_updates={"strategy_artifact_sha256": _sha256(proposed)})
        store.a1_authorize_ship(tid)
        store.a1_record_ship_repo_authored(tid, str(proposed))
        ok, reason = store.a1_commit_ship_repo_proposed(tid)
        assert not ok
        assert "how this works" in reason.lower() or "check b" in reason.lower() or "refused" in reason.lower()
        payload = json.loads(store.get_task(tid)["payload"])
        assert "ship_proposed_commit_sha" not in payload  # state unchanged, fail-closed
        assert store.a1_resume_action(tid) == "commit_strategy_proposed"  # still at the rung
```

- [ ] **Step 3: Run the integration tests**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/test_orchestrator_a3.py -k "real_k2bi_hook or surfaces_hook_rejection" -v`
Expected: both PASS (or SKIP only if the K2Bi repo is genuinely absent — which must NOT be the case in this session). If the live hook rejects the valid fixture, the fixture is wrong (add the missing field the hook names); do NOT weaken the test.

- [ ] **Step 4: Commit**

```bash
git add tests/orchestrator/test_orchestrator_a3.py
git commit -m "test(orchestrator): prove A3 proposed commit against the real K2Bi pre-commit hook"
```

---

## Task 6: A3 conductor doc update (SKILL.md)

**Files:**
- Modify: `.claude/skills/k2b-orchestrator/SKILL.md`

- [ ] **Step 1: Update the A3 durable model resume ladder**

In the "A3 durable model additions" bullet about the resume ladder (~line 376), insert the new rung:
```
not repo-authored -> author_strategy_to_repo; repo-authored but not proposed-committed -> commit_strategy_proposed; not dispatched -> dispatch_ship; dispatched -> verify_ship.
```
Add `ship_proposed_commit_sha`/`ship_proposed_committed_at` to the parent-payload-flags bullet (~line 372).

- [ ] **Step 2: Add the proposed-commit procedure step**

In the A3 "### Procedure" (~line 446), between step 2 (Author to repo) and step 3 (Dispatch ship), insert:

```
2b. **Commit proposed first.** K2Bi's commit-msg hook forbids (new file)->approved; the strategy must land
    tracked as `proposed` before run_full_ship does proposed->approved. After `record-ship-repo-authored`,
    run `python3 -m scripts.lib.orchestrator_store commit-ship-repo-proposed <parent>`. This commits ONLY the
    strategy file as `(new file)->proposed` (no trailers needed), re-checks the bound sha, refuses a
    non-proposed file or an unrelated-dirty tree, and is idempotent. The resume action advances to dispatch_ship.
```
Renumber the old steps 3/4 to 3/4 (Dispatch/Verify) accordingly.

- [ ] **Step 3: Add the attempt-budget recovery to the recovery constraints**

In "### Capital preflight and recovery constraints" (~line 430), add:

```
- If a flight is parked needs_human_terminal with terminal_reason=ship_attempt_limit_exceeded AND a live
  `inspect-ship-state` returns clean_rollback AND the spent attempts died on now-fixed FLOW bugs (not a flaky
  strategy or a partial ship), recover with:
  `python3 -m scripts.lib.orchestrator_store reset-ship-attempts <parent> --i-checked-the-log --reason "<why>"`
  then `retry-ship`, which routes through commit_strategy_proposed -> dispatch_ship. NEVER reset on
  partial_approved_uncommitted.
```

- [ ] **Step 4: Verify the doc references match the code**

Grep the SKILL for the command names and confirm they match the CLI subparser strings exactly:
Run: `grep -n "commit-ship-repo-proposed\|reset-ship-attempts\|commit_strategy_proposed" .claude/skills/k2b-orchestrator/SKILL.md`
Expected: the new references present, spelled exactly as the subparsers.

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/k2b-orchestrator/SKILL.md
git commit -m "docs(orchestrator): A3 conductor — commit-proposed-first step + attempt-budget recovery"
```

---

## Task 7: Full suite + adversarial pre-commit review

- [ ] **Step 1: Run the entire orchestrator suite**

Run: `cd /Users/keithmbpm2/Projects/K2B && python3 -m pytest tests/orchestrator/ -q`
Expected: all green (prior 378 + the new A3 tests). Investigate ANY failure; do not proceed red.

- [ ] **Step 2: Adversarial pre-commit review (capital path — mandatory)**

Per CLAUDE.md Adversarial Review: Codex primary, Kimi fallback. Scope = the working tree diff.
Run the `/ship` Codex pre-commit pass (or `scripts/review.sh diff --files scripts/lib/orchestrator_store.py,tests/orchestrator/test_orchestrator_a3.py --focus "A3 capital-path commit-proposed-first: can the proposed commit ever commit an approved file, sweep unrelated changes, or break head_advanced? Is the attempt reset safe against partial ships?" --wait`). Fold every real finding with a regression test.

- [ ] **Step 3: Squash-or-keep + final commit**

The per-task commits above are fine to keep. Do NOT push to a K2Bi remote (no K2Bi change). This is K2B-side; ship via `/ship` (commit + DEVLOG + wiki/log + feature-note lane). Do NOT mark A3 gate-passed yet — that waits for the LIVE run below.

---

## LIVE RUN (operator-gated — AFTER code lands, separate from the code plan)

This is NOT code. It is the runbook for the actual CDNS ship on flight `2026-06-07-001`, with Keith in the loop. The attempt reset is operator-attested; the ship is the 4th human gate.

1. Confirm clean state: `inspect-ship-state 2026-06-07-001` returns `clean_rollback`; K2Bi HEAD `62d5258`; `strategy_cdns.md` untracked at sha `907d708...`; CDNS whitelisted.
2. Reset the budget (Keith attests the 3 attempts died on the flaky-review + hook-gate FLOW bugs):
   `python3 -m scripts.lib.orchestrator_store reset-ship-attempts 2026-06-07-001 --i-checked-the-log --reason "3 attempts spent on now-fixed flaky-review x2 + commit-proposed-first hook gate, not a flaky strategy"`
3. `retry-ship 2026-06-07-001` -> resume routes to `commit_strategy_proposed`.
4. `commit-ship-repo-proposed 2026-06-07-001` -> the real `(new file)->proposed` commit lands (passes Check A/B/D + commit-msg). Verify HEAD advanced by one proposed commit.
5. `mark-ship-dispatch-started 2026-06-07-001` -> mints the token, head_before = the proposed commit.
6. Dispatch `k2bi-run-full-ship` -> reviews advisory -> `handle_approve_strategy` proposed->approved -> commit (hook now allows it). `poll-once`.
7. `verify-ship 2026-06-07-001` -> inspector sees `committed` -> `terminal_shipped`, records `ship_commit_sha`.
8. MVP gate (binary `K2B-CANNOT-SHIP-THE-STRATEGY-TO-THE-ENGINE-AS-ONE-CONVERSATION`): positive `terminal_shipped` reached + the negative path still proven (a stale sha / dirty tree / non-proposed file refuses clean). On pass: mark A3 gate-passed — update `feature_k2b-orchestrator` (Updates + clear pending-action + tracker Stage 11 -> done), mirror the K2Bi Phase-4 second-strategy exit in the K2Bi Resume Card, then `/ship`.

---

## Self-review

**Spec coverage (handoff "The fix"):**
- (1) commit-proposed-first in the A3 author step -> Tasks 1-3 (store step + rung + CLI) + Task 6 conductor. ✓ Reconciles `record-ship-repo-authored` sha-bind (Task 1 re-checks the bound sha; commit doesn't change bytes), `_assert_ship_clean_preflight` (verified: passes on a clean tree), `verify-ship` head_advanced (head_before = the proposed commit, approved commit advances HEAD). ✓
- (2) attempt-budget recovery -> Task 4 (`reset-ship-attempts`, the handoff's preferred option (a), guarded like `--i-checked-the-log`). ✓
- (3) ship CDNS -> terminal_shipped -> the LIVE RUN runbook (operator-gated). ✓

**Placeholder scan:** every code step shows complete code; no TBD/"add error handling"/"similar to". ✓

**Type/name consistency:** `a1_commit_ship_repo_proposed`, `a1_reset_ship_attempts`, payload keys `ship_proposed_commit_sha`/`ship_proposed_committed_at`/`ship_attempts_reset_at`/`ship_attempts_reset_reason`, CLI `commit-ship-repo-proposed`/`reset-ship-attempts`, resume action `commit_strategy_proposed` — used identically across tasks, SKILL, and runbook. ✓

**Codex plan-review (job `8678c1`, NEEDS-ATTENTION) — all 3 findings folded:**
- F1 (resume-rung wedge): the new rung is gated on `not ship_dispatch_started_at`; regression test `test_dispatched_legacy_flight_without_proposed_marker_routes_to_verify`. ✓
- F2 (TOCTOU + argv): stage-then-verify-staged-blob-sha before commit; argv corrected to `commit --only -m <msg> -- <path>`; sha-drift test added. ✓
- F3 (tests don't prove the live hook): Task 5b runs the real K2Bi hooks against a hook-valid fixture + a negative missing-`How This Works` rejection test. ✓

**Capital-path risks the pre-commit reviewer should re-hammer:**
- Can `commit-ship-repo-proposed` ever commit an `approved` file? (Guarded: status must == proposed.) 
- Can it sweep unrelated/sibling-staged files? (Guarded: `--only -- <path>` + clean-tree-except-target refusal.)
- Does committing the proposed file before `mark-ship-dispatch-started` keep `head_advanced` correct on both fresh and retry paths? (Traced: yes — head_before is always the proposed commit.)
- Can `reset-ship-attempts` ever rescue a partial ship? (Guarded: requires live `clean_rollback`, refuses `partial_approved_uncommitted`.)
- Does clearing `terminal_reason` ever clobber an unrelated terminalization? (Guarded: only clears when == `ship_attempt_limit_exceeded`.)
