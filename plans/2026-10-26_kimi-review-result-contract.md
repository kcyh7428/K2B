# Kimi review result contract

The runner consumes a validated normalized result bound to one attempt and
the SHA256 of its exact reviewer command and retained reviewed-input bytes.
The redacted prompt is saved privately before provider execution. Its content
digest is rechecked when publishing and consuming the result. Consumers can
also supply an expected content digest. Human output, echoed markers and
watchdog diagnostics cannot authorize approval. Both the child and runner
validate the declared review contract. Unsupported schema forms fail closed.
Standard informational schema annotations are accepted without evaluation.
Finite integral JSON numbers are accepted as integers; finding line numbers
are normalized to integer values only after successful validation.

The shared client's legacy finish-reason default is preserved. Only the review
caller requests `strict_finish_reason=True`, which preserves absent provider
stop metadata as unknown and rejects it. That local conversion option is
never sent as a provider request parameter.

## Consumer behavior

`scripts/review.sh` directly executes `review_runner.py` and propagates its
exit code. With `--wait`, Kimi approval exits 0; a completed Kimi review with
`needs-attention` exits 4 without fallback. Its status remains `completed`,
which describes review completion, and its `review_verdict` remains
`needs-attention`. Other nonzero codes retain their existing error meanings.
Codex behavior is unchanged, including an undetermined normalized verdict.

Consumers must require an explicit `review_verdict` of `approve`, the exact
reviewed patch identity and the expected independent reviewer before shipping.
Exit 0 from background dispatch or a polling request is never approval. Poll
output includes the job's `exit_code`, verdict and retained result path.
Manual K2B shipping still requires reading the actual review findings.

## Evidence and retention

Result files are mode 0600 inside fresh mode-0700 attempt directories under
`.code-reviews/`. They are audit evidence retained with the existing job JSON
and log, including rejected and interrupted attempts. The state record binds
the exact result path and attempt ID. They are intentionally not temporary
scheduler files and are not automatically deleted at review completion.
Each directory also contains mode-0600 `attempt.json` linking its job ID,
attempt ID, reviewer, timestamp, state path and log path. The reviewed input
snapshot is mode 0600, limited to 16 MiB and retained with the result. No
model reasoning is stored. A pending attempt directory is distinct from a
validated result path; only the latter is advertised as `review_result_path`.

Archive and usage-log storage failures produce bounded errors before result
publication. The runner still requires a successful child exit. Kimi timeout
classification uses an actual watchdog deadline signal, rather than proximity
to the deadline. Codex's existing classification is unchanged.

No existing review-archive retention sweep was found in the inspected scripts.
This repair introduces no background cleanup job or arbitrary retention age.
An explicitly requested future cleanup must treat a job JSON, log, referenced
attempt directories and corresponding provider archive as one evidence group.
It must preserve pending or ambiguous attempts and any evidence still needed
for a shipping decision. Do not use a filename-only glob as proof of ownership.

Provider archives keep the existing `timestamp_scope.json` name whenever it
is free. A same-second collision gets a random suffix through exclusive
creation, preserving both records rather than overwriting either. Existing
archives are not renamed. Directory presence or archive transport success is
not an approving verdict.

## Verification scope

Offline tests run the actual Kimi shell wrapper and replace only the provider
boundary. They cover normalized findings, JSON output, trailing diagnostics,
watchdog-shaped interleaving, malformed or stale results, and exhaustion.
Separate live calls establish the configured provider path works at the time
tested. They do not establish sustained provider reliability.
