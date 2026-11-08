# Bounded Qwen review with standard Kimi escalation

**Goal:** enforce the tested Qwen operating limit and deploy the same reviewed workflow to Home and SJM.

**Authority and confirmation:** Keith explicitly requested implementation and deployment on both Macs in the current Codex chat on 8 November 2026, after accepting the proposed 12,000-input-token limit for the entire assembled prompt, 120-second local deadline, standard Kimi 2.8 alternative and strict response validation. This supersedes the older Kimi-only review rule within this review workflow. No Highspeed, K3.1, new paid API dependency, model download, scheduler or service installation is authorized here.

**Evidence:** Home private operation `~/.local/state/k2b/operations/reviewer-size-sweep-20261108T054817Z/`: report.md, protocol.md, final-verification.json and routing-recommendation.json. Two real modules with controlled correct and defective patches were evaluated with growing actual source context. Four cases near 12.2K tokens passed twice. Four near 23.35K passed in 90 to 112 seconds. One 48.7K case was correct but took 163 seconds; remaining local cases there were not attempted. First-pass Kimi had four response-contract failures. The operating limit is provisional and covers focused patches, not arbitrary broad feature changes.

The experiment sizes are Qwen-counted input tokens, as report.md explicitly states, verified against generation input usage. They include all instructions, schema and context. They do not include generated output. The same tested 8,192-token output ceiling remains separate; no combined input/output envelope was tested or approved.

## System and confirmed behavior

The agent chooses relevant files and identifies the code author. The existing review command gathers and redacts context once, constructs one complete prompt, and retains its exact bytes with a digest. Code counts that exact prompt through the existing Qwen count endpoint using the tested profile. At most 12,000 tokens may reach local generation. Counting failure, over-limit input, unavailable endpoint, deadline, truncation, model mismatch or invalid review output selects standard Kimi 2.8 on the identical packet. A valid needs-attention review is returned to the builder without escalation. The model identifies defects; code enforces routing, time, independence, validation and result binding.

Home uses existing localhost:18081/v1 SSH forwarding to SJM over Tailscale. SJM uses its own localhost:8081/v1 server. The model ID is the existing SJM Qwen3.8-27B-4bit path. Kimi uses each machine's existing credential and the coding membership endpoint, model kimi-for-coding verified as K2.8 Preview. Credentials never move between hosts. Machine configuration may select only a loopback Qwen endpoint; it cannot raise limits or select another cloud provider/model. Malformed configuration stops rather than silently falling back.

Use `--primary auto` as the normal independent workflow, with `--primary kimi` for deliberate broader or higher-consequence review and reviewing this routing implementation. Both are normalized, attempt-bound reviewers. `--no-fallback` prohibits leaving the selected approved strategy; auto's internal Qwen-to-Kimi switch is authorized. Existing Codex review stays available for Kimi-built code. Add explicit Qwen and DeepSeek author families. Qwen-built code must bypass Qwen. Kimi-built code must use an independent Codex path, never the Kimi fallback.

The tested generation profile is temperature 1, low effort and an 8192-token output ceiling. A subprocess wall deadline enforces 120 seconds for the complete local attempt, including preflight/counting. Kimi is bounded by the remaining outer job budget. No HTTP retry loop or extra provider fallback. A local worker killed at its deadline cannot later publish approval. The existing server/tunnel remain independently owned and are not restarted or reconfigured.

Only a whole-answer JSON fence may be normalized. Reject duplicate keys, NaN/Infinity, prose, multiple JSON objects, extra fields including $schema, missing fields, invalid schema values, missing or unsuccessful finish metadata, missing/wrong model identity and inconsistent verdict/findings. Verify streamed completion. Preserve the existing result/input/attempt/command digest binding and private evidence. No dispatch exit, heartbeat, textual APPROVE marker or invalid provider response can approve. Counted and reported local input usage must agree. Record selected reviewer/model, token count, routing reason and failures.

Do not silently cut code or auto-repack an oversized packet. It goes to Kimi; if Kimi cannot return a valid review within the job budget, stop and report the actual failure. Kimi also requires strict validation. The agent prepares bounded packets with complete coverage for broad work, rather than treating the advertised context window as a reliable review size.

## Acceptance examples

1. Complete packets counted at exactly 12,000 tokens can use Qwen; 12,001 never invokes local generation and selects standard Kimi with identical prompt bytes.
2. Instructions, schema and all context are counted. A changed packet cannot use an earlier count or result. Counter absence, invalid counts and usage mismatch cannot authorize Qwen approval.
3. A valid local approve or needs-attention review finishes without a Kimi call. Findings exit with the existing corrections-required code 4.
4. Local unavailability, invalid schema, echoed schema, wrong model, missing finish reason and a trickling stream exceeding the wall deadline trigger the authorized Kimi route. Invalid Kimi output fails closed.
5. Review results with altered input bytes, stale attempt/scope identity or invalid routing evidence are refused by the parent.
6. Explicit author-family checks prohibit Qwen self-review and Kimi self-review through the alternative. No Codex fallback for OpenAI-built changes.
7. On each installed host: real small clean and defective reviews identify Qwen and retain input/route evidence; a real above-limit packet and unavailable-local fixture identify Kimi 2.8. Focused fake-HTTP tests verify malformed responses and deadlines without quota consumption.
8. Both actual checkouts retain unrelated changes and machine-local adaptations. Git carries the reviewed change; installed code hashes, authority checks and relevant source-backed recall/storage bindings are verified on both hosts. No deployment claim until host checks pass.

## Implementation plan

**Architecture:** reuse context gathering and attempt result publication; add a bounded routing/client module and internal shell entry point. Extend the central runner for the auto strategy and verified actual reviewer metadata. Keep legacy text-worker provider defaults outside this review scope unchanged.

**Tech stack:** Python 3.12 standard library, existing JSON-schema validator and pytest/unittest fixtures. Use the installed Python 3.12 test interpreter; the worktree's default Python 3.14 lacks pytest.

### Task 1: Routing and validation

- Files: add `scripts/lib/review_routing.py`, `tests/test_review_routing.py`.
- Interface: `route_review(prompt, mode, builder_family, endpoints, budget_end)` returns a validated provider envelope plus routing evidence; raises a bounded failure with evidence on exhaustion. Supporting strict parser and evidence validator are shared with the result consumer.
- Write failing boundary, strict-schema, independence, same-byte, transport and subprocess-deadline tests first. Exercise the real HTTP client against local fixture servers, using fake credentials only.
- Implement minimal local and standard Kimi clients, private machine configuration and bounded worker execution. No configurable upward token or time limit.
- Expected: focused routing tests pass, with no live provider calls in unit tests.

### Task 2: Existing command and authority integration

- Files: `scripts/lib/minimax_review.py`, `scripts/lib/review_runner.py`, `scripts/lib/review_result.py`, `scripts/review.sh`, new internal `scripts/routed-review.sh`, focused integration tests.
- Keep one retained prompt for count, both providers and publication. Bind routing evidence into the normalized result and verify it again when consumed. Surface actual reviewer and routing reason in final/poll state.
- Preserve legacy explicit review compatibility where it does not bypass the official auto strategy. Pin the official forced Kimi path to standard K2.8. Disable reviewer trials on the new strategy.
- Update AGENTS.md, ship/research instruction references and adversarial prompt to the approved scope, accurate NAS/host contract and an unambiguous result-object example. Do not relax the schema to accept echoed annotations.
- Expected: existing focused review suites and authority scan pass; simulated routed CLI checks demonstrate non-approval on invalid evidence.

### Task 3: Independent review and delivery

- Stage only intended files. Run diff checks and an independent forced Kimi review using `scripts/review.sh ... --builder-family openai --primary kimi --no-fallback --wait`. Retain exact reviewed bytes and actual approving verdict, fix material findings and re-review invalidated scope. Do not count this builder's Qwen routing path as its independent approval.
- Commit and push the reviewed branch. Each commit requires independent Kimi approval under the project rule, overriding generic skill suggestions about per-task commits or self-review.
- Integrate through Git into both actual checkouts, preserving Home's unrelated AGENTS insertion and SJM's newer commits/local changes. Use private backups and expected hashes for narrow instruction reconciliation. Never blanket stash/reset/overwrite other work.
- Install or verify host-owned loopback-only endpoint configuration. Run acceptance examples and authority verification on both actual checkouts, then retain delivery evidence and report exact activation states.

## Progress

- Preflight: Home root cf98fcc with unrelated dirty files including an AGENTS insertion; SJM 196ca9c with unrelated dirty YouTube/capture files. Preserve all. Managed worktree is `/Users/keithmbpm2/.codex/worktrees/qwen-review-routing/K2B`, branch `codex/qwen-review-routing-20261108`.
- Environment: default worktree Python 3.14 has no pytest; installed `/Library/Frameworks/Python.framework/Versions/3.12/bin/python3` has pytest 9.0.3. No dependency installation required.
- RED: the new routing suite failed collection because the implementation module did not exist. Baseline and subsequent focused review suites passed under Python 3.12.
- Implemented: disposable network workers, exact-packet counting, immutable local bounds, standard Kimi catalogue/model pin, strict response parsing, parent routing evidence checks, independent builder matrix and central CLI integration.
- Worktree verification: 151 focused Python tests plus 16 subtests passed before the final added routing cases; the expanded routing suite then passed all 39 cases. Shell runner suite passed 31 cases. Authority scan passed. Live small Qwen review used 2,234 counted/reported tokens in 21.45 seconds; forced Kimi used kimi-for-coding after checking K2.8 Preview catalogue identity.
- Delivery checkpoint: independent forced Kimi review and installed-host acceptance remain required. Final activation evidence will be retained privately under the host operations directory to avoid changing already reviewed commit bytes.
- The first independent request bundled all changed full files into a 494,268-character packet. Kimi returned an incomplete completion after 222 seconds. The strict gate rejected it and no approval was recorded. Independent review is therefore prepared as explicit smaller file groups with full source per group. Their union must cover the exact committed path set and bytes. This is deliberate review preparation after a reported failure, never silent truncation or an automatic retry.
- Review follow-up: reproduced and fixed auto timeout classification and contradictory exit-4/approve state. Rejected the suggestion to restore eager runner credential/trial injection: the new Kimi worker calls the same per-machine credential loader directly, while local reviews intentionally need no cloud credential and trial model overrides are explicitly excluded. Clarified that all measured size bands count complete prompt input, not output reservation. Added missing routed-entry regression coverage.
- Further review disposition: routed CLI calls now require central attempt binding, so every successful routed review retains exact input and normalized result, including token usage and routing evidence. Text markers and legacy trial/archive modes are intentionally outside this internal path. Invalid machine configuration stops even for forced cloud, as approved; unreachable valid local endpoints escalate. The two verified host users, ports and exact served model ID are deliberately pinned, with private endpoint configs installed on activation. Do not relax model identity or add aliases based on hypothetical other hosts. Kimi uses the existing coding membership endpoint, never a new paid platform. Added a finite 350-second ceiling to cloud workers even for a malformed deadline-unit value, and bind config reads to the checked file descriptor.
