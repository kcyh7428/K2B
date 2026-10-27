# Manual recovery execution binding

Prepared method only. Real source copies, NAS payload writes and restore execution remain held for the manager's exact release. This package provides observations and command plans, not a controller that freezes writers or performs recovery.

For proposed current/history/private-state classes outside the delivered semantic verifier, use the separately reviewed [one-time consumer contract](2026-10-27_manual-run-consumer-contract.md). Its frozen P2 component is preparation input until actually delivered and rebound. Do not route every broader path through this narrower helper, omit rejected evidence, or reuse this baseline verdict as approval of broader execution. The original `2975eb5` delivery/activation receipts remain immutable evidence of the earlier six-file package.

## Before release

1. Bind delivered P2 commit `c3849ee7186d27bbeda89f2981b8b4b2ac2014af`, approved by Kimi job `2026-10-26T23-25-32Z_5a0469`. Its CLI SHA-256 is `26f1201cabbcf4a275bf7d4d96a0b02295d83398cba55335bc4333ec729f8bbb`; module SHA-256 is `30d5d303baf021065537b60b55a17acdc94cd36e1e9a05a053d73378fc7fe03d`. The delivered `plans/2026-10-27_k2b-nas-manual-recovery-rehearsal.md` SHA-256 is `47cbeb4b00832893a72648e03beb93dccbb97e5571fa4a71f34335791ae8edc1`. Recheck these actual bytes before execution. Bind this orchestration commit and approving review separately.
2. Privately approve the exact selected-path list and exclusions. Generate the archive plan from that absolute list path. Retain its rule version, rule digest, NUL-list digest, full-plan digest, root mode, directory modes/mtimes, file metadata and total bytes. The full-plan digest binds canonical sorted compact ASCII JSON of every field except the digest itself. Retain the digest separately in the private release receipt; a self-contained digest detects corruption but does not authenticate an edited plan with a recomputed digest. Recompute against unchanged sources before use. Selection is not approval merely because the planner accepts it.
3. Bind the manager's dated private DSM Permission Inspector observation for the existing parent. Its record hash is `cbaee7ba86b17bf9ab94ae18c7077dc354ef7dc4951d9e34b9ffdfae42874f39`. It verifies the parent identity/owner, maintenance-user effective access and capture-worker effective denial. It is parent-only visual evidence; no standalone screenshot-byte hash or independently read administrator username is claimed. Changed parent identity/ACL invalidates it. A fresh child target needs post-creation checks after exact release. The failed privileged probe remains failed; do not retry it or put private knowledge on the capture share.
4. Establish private passphrase custody in the existing password manager. Use a random passphrase of at least 32 characters under the run's recovery record, then lock, reopen and retrieve it. Never put it in chat, environment variables, arguments, scripts or receipts.
5. Obtain fresh Home and SJM status observations and validate at receipt time. Explicitly establish native-job/staging inactivity and editor quiescence. Configured lock witnesses do not prove those conditions or hold a global freeze. Any source-copy controller must establish and retain ownership of the actual writer locks independently; this package does not provide it.

## Visible private-entry readiness

This gate remains pending. GnuPG 2.5.18 and pinentry-tty 1.3.2 are installed. Computer use refused access to Terminal, so no visible user-controlled prompt was verified. A background tool PTY is not readiness evidence. The manager must observe the following harmless test in a Terminal window Keith opens and controls, before requesting custody entry:

```sh
test -t 0 && test -t 1 && tty
printf 'SETDESC K2B readiness test. No secret needed.\nCONFIRM\nBYE\n' | /opt/homebrew/opt/pinentry/bin/pinentry-tty --ttyname "$(tty)" --ttytype "$TERM" --timeout 30
```

Keith confirms the visible non-secret prompt. Retain only the successful confirmation and terminal identity, with no screen recording or tool reading during later secret entry. After exact release, fresh separate mode-0700 GnuPG homes for encryption and decryption use that terminal and a per-home agent configuration pointing to the installed pinentry-tty. Set GPG_TTY to the non-secret current tty, use normal GnuPG agent prompting and disable symmetric-key cache. No global agent configuration change, installation, direct secret-returning pinentry protocol call, passphrase argument/environment value or secret trace is permitted. A successful readiness confirmation alone does not prove password-manager lock/reopen/retrieval or archive decryption.

## Evidence and refusal

The synthetic suite proves status refusal, deterministic selection, archive metadata preservation and fixed read-only NAS command construction. Real read-only NAS tool/stat probes and local status observations are separate private evidence. No payload has been uploaded by this package.

The tested deployment uses HTTP on loopback. HTTPS requires an already trusted certificate through the default trust store; self-signed or untrusted GUI certificates refuse. Do not disable verification or change Syncthing settings to make a probe pass. The observation timestamp precedes HTTP/status and lock sampling, after configuration parsing, so freshness bounds the oldest possible sampled state conservatively. Stamping after every sample would conceal the age of earlier samples.

The explicit lock contract is `configured_witnesses_role_v1`. Home requires all five witnesses clear. SJM requires note_write, compile_index and wiki_log clear; integration and publication must be `not_applicable`, reflecting the inspected Home-only owner guards and current authority. All five are still probed: unexpected busy Home-only writers on SJM refuse acceptance. A missing parent is unknown for every applicable witness. Only a genuinely absent parent of an inapplicable Home-only SJM witness can produce `not_applicable`; permission errors, unsafe ancestry and unknown existing paths remain unknown. Normal CLI/remote calls bind actor and home to the physical Unix account, ignoring USER/HOME assertions. Explicit synthetic home injection remains a library test dependency, absent from the CLI and fixed remote call. Recheck owner code and the private SJM role proof before execution if either role or implementation changes. Native-job/editor quiescence is still a separate gate.

Each observation includes the exact normalized `witness_specs`, including absolute paths, lock kinds and fallbacks. Relative paths, escaping components and control bytes refuse. Both CLI status routes validate before returning success. Before release, independently compare these specs with the actual writer launch/configuration environments on each host and retain the approved specs in the private release record. Both CLI routes accept the expected-witness-specs flag with an absolute private JSON path; they bind its exact byte hash and return `expected_specs_matched` after validation. Omission is explicitly `unbound_preflight`, never an execution receipt. The CLI status output wraps the observation and receipt time consistently with counterpart output. The library also accepts `expected_witness_specs`; a different absolute path or kind refuses. Never derive the authoritative expectation solely from the observer's own output. The current live receipt exposes configured specs but does not certify cross-process environment equality. This comparison is a mandatory execution gate, not evidence inferred from matching defaults. The XML reader and parser use the same strict UTF-8 text; non-UTF-8 declarations, NUL, DTD and entity definitions refuse before parsing. A complete status-route test confirms a valid UTF-8 XML declaration is accepted by the actual parser.

P2 shared-knowledge inspection has a 16 MiB per-file ceiling and excludes private state. The private metadata survey found 3,450 vault files, including history classes omitted by the older inventory, and one oversized file. It is a non-atomic planning inventory, not an approved selection or checkpoint. This planner caps a selection at 2,000 files and cannot certify the full survey. Keep the full inventory and owner dispositions private; historical/conflict content is not excluded merely because ordinary recall excludes it. Path acceptance does not inspect document contents for private information.

The separate supported oversized-evidence method is a whole-file streaming SHA-256 with Python 3.12 `hashlib.file_digest`, before/after six-field stat checks and a repeated source hash under the continuing quiet window. Use an explicit owner-approved NUL tar list containing that file and every selected ancestor, then the fixed BSD PAX create/extract builders. After actual NAS retrieval and decryption, stream-hash the restored whole file and compare size, POSIX bits and nanosecond mtime against the separately retained private source record. Never split or rewrite immutable evidence to fit P2. A 17 MiB synthetic whole-file cycle passed this method; it does not inspect or approve the real oversized file and is outside P2's semantic verifier. Empty directories, ACL/owner/xattr verification and private state still need owner-reviewed supported methods before full recovery acceptance.

The original SJM 1.5-second transport limit timed out and is retained as failed evidence. A diagnostic took 1.64 seconds. The revised bounded transport limit is explicitly five seconds, with a 0.75-second local HTTP timeout; the two-second observation freshness and 0.5-second future tolerance remain unchanged. An intermediate stricter probe refused the missing Home-only integration parent on SJM. The inspected role correction above resolves that applicability error without creating a path or treating an applicable unknown as clear. The final joint live observation passed in 1.66 seconds: Home's five witnesses clear, SJM's three applicable witnesses clear and two Home-only witnesses explicitly inapplicable. At joint validation Home's observation age was 1.661 seconds; SJM's receipt-time sample age was 0.142 seconds. This is partial point-in-time route evidence, not a two-host quiet window, source convergence, backup or native-job inactivity. Clock skew still refuses acceptance and requires clock correction, never a freshness bypass.

The five-second transport envelope includes SSH startup before the remote timestamp. The two-second freshness check begins from remote sampling, so total transport duration is not observation age. Keep these distinct bounds; do not inflate freshness to accommodate slow transport. SSH connection establishment remains capped at one second with no automatic retry. A slow connection refuses safely; a supervised retry requires fresh observations after the route recovers. The measured round-trip duration above is not a separately measured TCP connect latency.

Collect SJM first, then Home, and revalidate both at the joint receipt time using independently bound witness specs. This avoids aging Home's sample during SSH startup. The causal order is SSH connection, remote Python start, remote observed timestamp, remote GUI/lock sampling, local receipt. Synthetic regressions demonstrate that three-second and 4.5-second startup delays still accept a later remote sample aged 0.1 seconds. An actually old remote sample or excessive clock skew still refuses by design. The recorded 1.661-second age belongs to the earlier Home-first collection, not the SJM sample. The flock witness briefly takes and releases an exclusive lock; this can transiently deny another nonblocking writer. Treat such a writer refusal as contention evidence, wait for the real quiet window and collect fresh observations. Repeated probes do not create or prove that window.

A fresh SJM-first live collection completed in 1.797 seconds, with joint ages of 0.011 seconds for Home and 0.211 seconds for SJM. A separate diagnostic inserted a three-second delay before SJM's timestamp capture: total duration 4.675 seconds, SJM sample age 0.199 seconds, accepted under the unchanged five-second transport and two-second freshness limits. The diagnostic used a separately hashed in-memory payload; it is not the default or execution payload. These measurements disprove the claim that transport above two seconds necessarily produces stale remote state. They do not prove clock synchronization, writer-config equality or quietness.

The counterpart CLI requires the expected payload SHA-256, computed using the bound module and local Python version before dispatch. It checks that value before SSH and returns the executed payload digest with the validated observation. The reviewed candidate's Python 3.12 payload digest is `f120e0b1e8e530846b44bc815cfec2bf3f002b12aec75c83b2ecc126457a21d7`. Recompute for the final delivered code/runtime; a different value requires deliberate binding rather than substitution. The serializer selects named definitions/constants and fixed imports through Python's syntax tree, without a positional source split. Raw remote stderr is intentionally withheld because exceptions can contain private paths or configuration values. Private supervised diagnosis may classify a static failure without forwarding raw output.

## After exact release

Follow the delivered P2 runbook under the approved writer freeze and approved path scope. Bind source manifests and archive-list bytes before copying. The plan retains each selected file's P2-derived SHA-256, size, mode and nanosecond mtime, as well as directory metadata. Run `validate_source_plan` against the bound plan immediately before and after tar creation under the continuing quiet window; it rehashes files through the delivered P2 helper and refuses changed metadata or hashes. Equal scans do not replace the quiet window or establish an atomic snapshot. Use the actual delivered verifier for the decrypted restore, including all retained ancestor metadata. A planning result alone cannot authorize shell execution or source copying.

The tar builders are read-only and reject known symlink ancestry/leaves and existing output at planning time. Direct execution of the file-output argv is not the released method. After exact release, use the following held-input/exclusive-output procedure with inputs from the bound private run record and the bound `ro` library. Generate the NUL list privately with exclusive creation and mode 0600; retain its expected SHA-256 and total selected payload bytes separately in the release record. The procedure holds the input and output descriptors, checks the input before and after, uses a fixed stream argv without token rewriting, and publishes the final name without overwriting an existing name. Failed partials remain explicitly unaccepted. Even a final name is not evidence of success without a successful return and the bound private verification receipt. The private parent and continuing source quiet window remain required.

```python
def create_archive_exclusively(root, list_path, archive_path, expected_list_sha256, expected_payload_bytes):
    import hashlib
    import os
    import stat
    import subprocess
    import uuid
    from pathlib import Path
    archive_path = Path(archive_path)
    ro.validate_tar_create_paths(root, list_path, archive_path)
    if isinstance(expected_payload_bytes, bool) or not isinstance(expected_payload_bytes, int) or expected_payload_bytes < 0:
        raise RuntimeError("archive_expected_bytes_invalid")
    partial = archive_path.with_name(archive_path.name + ".partial-" + uuid.uuid4().hex)
    signature = lambda item: (item.st_dev, item.st_ino, item.st_size,
                              item.st_mtime_ns, item.st_ctime_ns, item.st_mode, item.st_nlink)
    list_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(list_path, list_flags), "rb", buffering=0) as listing:
        before = os.fstat(listing.fileno())
        named_list = os.lstat(list_path)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > 8 * 1024 * 1024
                or signature(before) != signature(named_list)):
            raise RuntimeError("archive_list_identity_invalid")
        if hashlib.file_digest(listing, "sha256").hexdigest() != expected_list_sha256:
            raise RuntimeError("archive_list_digest_invalid")
        listing.seek(0)
        argv = ro.tar_create_stream_argv(root, listing.fileno())
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        descriptor = os.open(partial, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            subprocess.run(argv, stdout=output, stderr=subprocess.PIPE, check=True,
                           pass_fds=(listing.fileno(),))
            output.flush()
            os.fsync(output.fileno())
            opened = os.fstat(output.fileno())
            named = os.lstat(partial)
            if (not stat.S_ISREG(named.st_mode) or opened.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                raise RuntimeError("archive_output_identity_changed")
            if opened.st_size < max(512, expected_payload_bytes):
                raise RuntimeError("archive_output_too_small")
            if (signature(before) != signature(os.fstat(listing.fileno()))
                    or signature(before) != signature(os.lstat(list_path))):
                raise RuntimeError("archive_list_changed")
            listing.seek(0)
            if hashlib.file_digest(listing, "sha256").hexdigest() != expected_list_sha256:
                raise RuntimeError("archive_list_changed")
            parent_fd = os.open(archive_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
                os.link(partial.name, archive_path.name, src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd, follow_symlinks=False)
                final = os.lstat(archive_path)
                if (not stat.S_ISREG(final.st_mode)
                        or (opened.st_dev, opened.st_ino) != (final.st_dev, final.st_ino)):
                    raise RuntimeError("archive_final_identity_changed")
                os.fsync(parent_fd)
                os.unlink(partial.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
    return archive_path
```

This exact reviewed fragment is exercised with synthetic files, including the 17 MiB whole-file route. The package CLI remains observation/planning only; no automatic executor or source-copy release is added.

The output-size check is a sanity gate for this uncompressed method, not restore verification. A refusal or durability error leaves unaccepted artifacts and requires a fresh run, never deletion of earlier points or acceptance by filename. After extraction into the fresh inactive destination, apply the retained source-root POSIX mode to that destination root and verify it; the archive list intentionally contains only descendants, so tar does not restore the root entry itself.

Fetch the encrypted archive back from its actual NAS location. Verify the fetched hash against the upload receipt, then decrypt that fetched file using a fresh GnuPG home and disabled symmetric-key cache, retrieving the passphrase from its reopened custody record. Verify restored bytes, modes and timestamps in a separate temporary destination. Retain private receipts and refusal evidence. Cached upload data or remembered passphrases do not establish NAS retrieval or custody recovery.

Report bounded manual rehearsal results separately from unattended backup, independent recovery and future NAS authority. Same-NAS snapshots and working replicas do not establish an independent backup. Office access requires a real off-site test. Mini database snapshots, account/ACL changes, schedules and backup activation remain separate held scope.
