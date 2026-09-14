# K2B Status and Context Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Kimi is the builder; do not spawn additional workers. Steps use checkbox syntax for tracking.

**Goal:** Make K2B's status and research context truthful without changing its working capture schedule.

**Architecture:** Read exact native job registrations and receipts through a small read-only adapter. Parse actual memory-log entry boundaries and statuses. Build motivation context at read time from the current index and explicit questions, excluding expired inferred interests.

**Tech Stack:** Python 3.12 standard library, Bash, existing pytest and shell fixtures.

**Spec:** `docs/superpowers/specs/2026-09-13-k2b-status-context.md`.

## Global constraints

The spec's fixed boundaries apply to every task. Builder edits only the nine product paths listed below. Manager owns plans, job, guard state, review and delivery records. Existing good capture work must be preserved. No provider calls in product tests. No live state or vault writes during implementation. No commits before the independent review gate.

Run shell tests with `PATH=/usr/local/bin:$PATH` so `python3` resolves to the already-installed Python with PyYAML. The initial plate baseline failed because `/opt/homebrew/bin/python3` lacked PyYAML, not because of a candidate change. Python capture-status baseline passed 163 tests. Do not install packages or change global Python to fix this environment selection.

## Task 1: Correct plate memory flags

**Files:** Modify `.agents/skills/k2b-plate/scripts/plate.sh`, `tests/k2b-plate.test.sh`; create `scripts/plate-memory-flags.py`.

**Interface:** `python3 scripts/plate-memory-flags.py --requests PATH --errors PATH --today YYYY-MM-DD` emits the existing Memory flags subsection body as Markdown, excluding its heading. It reads only; missing files produce a truthful unavailable/no-records line, not an exception dump. Dates use the supplied HKT calendar date. Requests use latest explicit status per ID; errors are limited to the last 30 days and labelled resolved/open/unspecified.

- [ ] Add fixture tests before implementation. Include this canonical request and a closed entry; assert only the open entry appears:

```markdown
### R-2026-09-12-001
- **Status:** open
- **Date:** 2026-09-12
### R-2026-09-12-002
- **Status:** closed
- **Date:** 2026-09-12
```

- [ ] Test `##` and `###`, a later resolved addendum for the same ID, missing status, malformed date, future date, empty file, absent file and an error outside the 30-day range. Assertions must check exact IDs and prevent closed/duplicate leakage.
- [ ] Run `bash tests/k2b-plate.test.sh`; record the new failure before changing production code.
- [ ] Implement the small parser and replace only the Memory flags extraction block with its CLI call. Preserve the plate's other sections and format.
- [ ] Rerun the focused shell test after the change. Leave history files untouched.

## Task 2: Observe native registration honestly

**Files:** Create `scripts/lib/native_job_status.py`, `tests/test_native_job_status.py`; modify `scripts/lib/eod_capture.py`, `tests/test_eod_capture_status.py`.

**Interface:** `native_job_status.read_native_job_status(*, writer_role: str, state_root: Path, automation_root: Path | None = None) -> dict`. Default automation root is the actual user's `.codex/automations`. `home` maps only to `k2b-automatic-memory-home`; `sjm-source-only` maps only to `k2b-automatic-memory-sjm`. Map existing source-only spelling deliberately after inspecting `_memory_writer_role`; unknown roles must not select Home. Tests always pass a temporary root.

**Returned fields:** `job_id`, `registration_state` (active/paused/missing/unknown), `configured_model`, `configured_reasoning`, `schedule`, `last_completed_at`, `last_run_outcome` (completed/failed/unknown), `extraction_hold` (present/absent/unknown), and `diagnostic` (sanitized short reason or null). A valid empty/held extraction is not a failed scheduler run; retain that distinction in the diagnostic/hold field. Read only expected fields and never return prompts, credentials or raw transcript data.

- [ ] Write a fixture test equivalent to:

```python
def test_active_is_not_completion(tmp_path):
    root = tmp_path / "automations"
    job = root / "k2b-automatic-memory-home"
    job.mkdir(parents=True)
    (job / "automation.toml").write_text(
        'id="k2b-automatic-memory-home"\nstatus="ACTIVE"\n')
    result = read_native_job_status(writer_role="home", state_root=tmp_path / "state", automation_root=root)
    assert result["registration_state"] == "active"
    assert result["last_run_outcome"] == "unknown"
```

- [ ] Add paused, absent, malformed TOML, wrong ID, invalid status/type, unsafe symlink, wrong role, successful receipt, failed receipt, malformed receipt, active hold and differently formatted receipt filename tests. Choose newest receipt by validated timestamps, never lexicographic filename. An invalid newest record must not be silently represented as current success. Inputs and directories must remain byte-for-byte unchanged.
- [ ] Run `/usr/local/bin/python3.12 -m pytest tests/test_native_job_status.py -q` and observe the expected failure.
- [ ] Implement the adapter with standard-library TOML/JSON/date parsing and bounded reads. Integrate the result into `memory_status`. Correct the legacy hardcoded labels to reflect observed registration, with unknown for unavailable evidence; preserve additive compatibility for existing count/stage fields. Do not touch extraction, cursor, queue or publisher semantics.
- [ ] Update assertions that encoded the old static disabled/unverified labels. Add a regression ensuring fixture status does not read real Home registration accidentally.
- [ ] Run `/usr/local/bin/python3.12 -m pytest tests/test_native_job_status.py tests/test_eod_capture_status.py -q`.

## Task 3: Current, write-free motivation context

**Files:** Modify `scripts/motivations-helper.sh`; create `tests/motivations-read.test.sh`.

**Interface:** Keep `scripts/motivations-helper.sh read` and all existing write commands. Reads use existing `K2B_MOTIVATIONS_FILE`, `K2B_QUESTIONS_FILE`, `K2B_CONCEPTS_INDEX` overrides. Add `K2B_MOTIVATIONS_TODAY` as a strict ISO date override for deterministic tests; production uses today's UTC date. Invalid override exits nonzero with a short diagnostic and emits no inferred context.

- [ ] Build temporary index/motivation/question fixtures. The index contains CURRENT_PROJECT, cached Building contains RETIRED_MINI_PLAN, and explicit questions contain KEITH_QUESTION. With an expired observer date, assert output contains CURRENT_PROJECT and KEITH_QUESTION, but not RETIRED_MINI_PLAN or an expired inferred interest. Hash all fixtures before/after and assert no writes.
- [ ] Cover fresh inferred interests, exact 30-day boundary, missing/invalid/future observer timestamp, absent index, absent questions, empty sections, disabled toggle, and an invalid date override. Explicit questions do not expire or get rewritten.
- [ ] Run `bash tests/motivations-read.test.sh` and record the failing reproduction.
- [ ] Reuse `extract_building_from_concepts` for a read-time Building view. Add a concise source label. Filter only inferred Emerging Interests by the declared timestamp rule. Do not call `sync-building`, providers or any writer from `read`.
- [ ] Run the focused shell test. Do not rewrite research's large skill merely to change its existing helper call.

## Task 4: Review, deliver and reconcile current records (manager)

- [ ] Check actual changed paths against the frozen manifest and inspect the diff. Reject out-of-scope edits without deleting valid work.
- [ ] Obtain Kimi's explicit plan review verdict before accepting its implementation: this spec/plan was drafted by OpenAI. Resolve any material ambiguity before edits, not through builder guesswork.
- [ ] Stage only the intended product paths and approved plan/spec. Run `git diff --cached --check`.
- [ ] Review Kimi's product diff independently through `scripts/review.sh diff --files "<exact staged path CSV>" --builder-family kimi --primary codex --no-fallback --wait`. Provide the Ship Card and focused evidence; plan/spec are reviewed requirements, not Kimi-authored product code. Require an explicit approving verdict, not transport success.
- [ ] One consolidated correction packet and findings-only re-review are allowed. No full rediscovery or unchanged test repetition.
- [ ] Canonical final gate, once on the stable candidate: `bash tests/k2b-plate.test.sh`, `bash tests/motivations-read.test.sh`, `/usr/local/bin/python3.12 -m pytest tests/test_native_job_status.py tests/test_eod_capture_status.py tests/test_automatic_memory.py tests/test_automatic_memory_publisher.py -q`, `scripts/verify-codex-authority.sh`, `git diff --cached --check`.
- [ ] Use `k2b-ship` for reviewed commit and push to `origin/codex/k2b-status-context-20260913`. Preserve unrelated Home files. Use `k2b-sync` for deliberate Home/SJM activation only after verifying safe fast-forward paths and preserving host adaptations. No force operations.
- [ ] On Home, read `k2b-vault-writer`, verify live Syncthing convergence and exclusive ownership, then update only `wiki/concepts/feature_codex-primary-migration.md`, `wiki/concepts/index.md`, `wiki/context/active-motivations.md`, plus required designated append-only logs. Preserve history and explicit questions. Record actual commit, active hourly settings, Mini retirement evidence, and remaining acceptance/cost work. Do not edit policy ledgers or rules.
- [ ] Verify installed focused tests on each host, exact Git identity, live native registration readback and matching shared-note hashes when SJM is online. No extra model extraction run merely for a timestamp.
- [ ] Report separately: prepared, reviewed, committed, pushed, activated-home, activated-sjm; seven-day accepted remains false until its actual evidence exists.

## Stop conditions

Stop for any outside-manifest code/interface, missing independent approval, unresolved dirty overlap, credential/paid API requirement, deployment divergence, repeated same failure after one informed fix, or inability to establish real Kimi model/session identity. Preserve evidence and the worktree. Do not expand scope or reset counters.

## Scope self-review

Every required product contract maps to Tasks 1-4. Capture batching, backup configuration, bulk knowledge compilation, new infrastructure and revived dormant jobs are explicitly deferred. The first package needs no new numeric AI-spend or cadence decision.
