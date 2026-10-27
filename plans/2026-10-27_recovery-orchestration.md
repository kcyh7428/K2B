# Bounded recovery orchestration implementation

Manager-released on 27 October 2026. Build only machine-checkable status observations, deterministic archive planning and fixed remote command contracts. Existing automatic backup deferral and all payload/permission execution holds remain. No P2 recovery helper/runbook edits.

Files: scripts/lib/recovery_orchestration.py, scripts/k2b-recovery-orchestration.py, tests/test_recovery_orchestration.py, tests/test_recovery_orchestration_contract.py, plans/2026-10-27_manual-recovery-execution.md and this plan. DeepSeek constructs substantive code from curated repository excerpts and synthetic fixtures; Codex applies/tests and supplies independent contract checks; Kimi independently reviews the exact patch. No subagents or additional tasks.

## Binary done checks

- Synthetic local status server proves pending files/bytes, errors, malformed/missing fields, stale/future observations, unavailable endpoints and unknown/busy configured locks refuse acceptance. Only configured loopback API endpoints receive host-local credentials; no redirects/proxies or credential output.
- A fixed SSH route can execute the exact read-only probe in SJM's Python without installing or writing a file there. Its credentials stay on SJM. Live route proof is distinct from synthetic pending/refusal tests; do not mutate the live vault or Syncthing settings.
- Archive planner uses existing P2 manifest validation on synthetic files, enumerates every required ancestor, captures modes and mtimes, emits unique ancestors before children in deterministic depth/UTF-8 order followed by UTF-8 ordered files, and returns the exact NUL-list digest/rule version. No unrelated or empty-directory payloads. A local temporary BSD tar cycle verifies exact content, modes and representable timestamps. No source-copy CLI is provided.
- NAS builder allows only named read-only operations using absolute probed tools and fixed recovery-root paths. Local subprocess receives an argv list; SSH's remote-shell command is shlex.join of validated tokens. No arbitrary shell, sudo, mutation, user-supplied command string or secret logging. Tests include quotes/metacharacters, traversal, wrong roots and missing tools/output formats.
- Observe the original note-write then compile mkdir lock protocol, wiki-log's selected flock/mkdir protocol and integration file-lock protocol using read-only existing file descriptors. Never create/remove a live lock while probing. Lock witnesses are explicitly known configured locks, not proof of a global freeze or native batch inactivity. Those remain separate execution gates.
- Preparation copies only synthetic fixture bytes. The product CLI remains observation/planning only; the reviewed manual archive procedure is held until exact execution release. No real source copies, NAS payloads, global freeze, ACL/user/job changes or backup activation occur in this preparation. The later rehearsal still needs manager release, fresh target checks, final delivered P2 pins and private passphrase recovery.

## Build and review

Apply tests first and observe missing-module failure, then apply implementation. Independent coordinator contract tests reached that expected failure before any module was applied. The baseline publisher suite passed 24 tests. Use the existing Python 3.12 test environment, no dependency installation. Run the focused new tests and affected publisher tests. Stage only the intended paths in this package. Review as builder-family other, primary Kimi, no fallback, with the independence reason recording DeepSeek construction and Codex validation. Require actual approving normalized verdict on the exact staged patch before commit/push. Shipping code is separate from activation or permission to execute a recovery run.

## Live proof and execution binding

Keep concrete live endpoint addresses, local configuration, private inventory and tool/ACL receipts in the private task directory. Reviewed source contains the fixed SJM SSH alias and physical role bindings; test endpoints are synthetic. Live probes may read local Syncthing configuration for its own API authentication but never return its key/configuration content. A generic in-memory probe sent through existing SJM SSH is temporary read-only observation, not code activation.

After reviewed delivery, final execution binding must use P2's actually delivered helper revision, CLI/module hashes, approving verdict and declared schema/runbook coverage. Current observed or unshipped copies do not qualify. No recipe or code verdict certifies missing DSM rights or key custody.
