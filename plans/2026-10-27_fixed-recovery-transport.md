# Fixed recovery transport preparation

Status: local preparation only. No real SSH, NAS creation, upload or fetch has
run through this component. Real execution remains separately gated.

The existing P1 `nas_command` validates the exact target, personal parent and
run ID. The new client uses its fixed SSH prefix, preserving batch mode,
strict host-key checking and the connection timeout. Its sole remote command
is the existing Python interpreter executing the locally pinned
`recovery_nas_remote.py` source and one JSON control argument. There is no
arbitrary remote-command or runner option. Runtime/tool compatibility and the
authenticated host-key binding remain part of the private run admission.

The remote program verifies actual hostname, password-database username and
UID. It walks parent ancestors through no-follow directory descriptors and
binds the observed parent device/inode/owner/mode. All child namespace and run
directories are owned by that UID with mode 0700. An absent namespace is
exclusively created; an existing namespace requires its separately approved
exact identity. The run must always be absent before creation. Named and held
directory identities are checked after every phase. No remote partial is ever
deleted, adopted or overwritten by a retry.

Upload consumes an already-held local ciphertext descriptor. The client
requires a regular owned mode-0600 file with one link, measures its bytes and
eight-field signature, and checks the full signature before and after upload.
The remote exclusively creates either fixed `knowledge.tar.gpg` or
`control.json.gpg` with mode
0600, hashes the received stream, fsyncs it, then independently reopens and
hashes the actual stored bytes. It checks held and named file signatures and
returns those measurements. Available filesystem space must cover the cipher
plus 1 GiB before slot creation. This check does not establish user quota or
replace the manager's actual capacity/effective-access evidence.

Fetch opens that actual remote slot and requires the uploaded signature,
size and hash before streaming. It hashes the outgoing stream and rechecks
the full named/held signature afterwards. Cipher bytes alone go to stdout;
the successful remote measurement goes to stderr. The client redirects stdout
into a fresh exclusive local mode-0600 file already held by the foreground
parent under an owned mode-0700 parent. It measures that actual descriptor's size/hash and checks
both local and remote measurements against the original uploaded ciphertext.
It also checks local inode separation, named/held identity and the parent
identity after fetch. `prepare_fetch_output` runs before P1 forks. `fetch`
writes into that inherited descriptor and returns JSON-safe measured evidence.
After the real P1 supervisor has stopped the child group, the foreground parent
calls `accept_fetched` to rehash and recheck its still-held descriptor against
the successful return and upload chain. A descriptor number returned from a
dead child is never used as the parent's input. The retained artifact supplies
the actual new descriptor for fresh-cache decrypt. A local pathname or Boolean
cannot supply this fetch result. An actual P1-supervised local simulation pins
this process boundary, and a later mutation fails the parent's recheck.

The two artifact kinds are fixed enumerations, not arbitrary slot paths.
Archive and control uploads have separate original byte/hash/signature returns
and different artifact-bound context digests. Both objects require independent
remote readback and genuine fetch. An archive receipt cannot stand in for a
control fetch. The sealed private control JSON carries the admitted source
selections, provenance and software/runtime bindings; its original plaintext
digest must be retained with the independently reopened custody record before
transfer. After its genuine fetch and fresh-cache decrypt, verify that exact
digest before using its data. Neither ciphertext nor metadata in the same NAS
run can authenticate itself. The exact control schema, size bound, privacy
dispositions and trusted original digest remain part of P1's consumer binding
and manager admission. No real control content is included in provider packets.

Receipts include actual executed argv hash, pinned program hash, exit status,
elapsed time, a target/parent/run/binding context digest and measured local and
remote artifact evidence. Saved JSON is not self-authenticating: the private
operator must retain and bind the actual successful returns before handing
them to later phases. JSON parsing rejects duplicates, floats and nonfinite
values; fields use exact shapes and integer checks. `request_context` derives
the artifact context from independently admitted request inputs. Store that
digest in the immutable private run record before transfer. Fetch, parent
acceptance and crypto decrypt require this separate expected context; they
cannot derive their trust anchor from a pair of saved receipts. Changed contexts, unknown
program bytes, nonzero exits or mismatched measurements fail closed.

Child processes inherit P1's supervised group. Each client wait is bounded by
the remaining window. Timeout or interruption attempts child termination and
a bounded reap, then reports an unaccepted partial with unknown remote state.
It never claims an SSH disconnect proves remote work has ended. P1's outer
supervisor, calibrated nonrenewable phases, liveness checks and explicit
failure inspection remain mandatory. No automatic retry or remote cleanup is
provided.

Synthetic tests redirect the remote prefix only inside an explicit local
subprocess simulation. Production source remains pinned; the fixed remote
program operates on real descriptors and invented files in that simulation.
Tests cover actual create/upload/readback/fetch, missing remote data despite
an available cached upload, remote and fetched-byte mutation, changed source
signature, unknown namespace, existing run/slot, symlinks, capacity refusal,
wrong physical bindings, changed named run, unsafe local destinations,
context mismatch and strict JSON. These results do not prove NAS access,
NAS storage persistence, actual host identity or real retrieval.

Before real use, the one-time operator still needs approved source disposition,
actual writer/capture control, nonsync/nonindex stage and restore evidence,
actual runtime/tool calibration and quotas, effective maintenance access and
capture-worker denial, private custody/readiness and a bound actual GnuPG entry
route. Decrypt must measure the actual fresh
output against the original archive digest, then run P1 archive inspection,
the reviewed exact-name route, independent restore comparison and the separate
declared-alias phase. These gates and real-run release
are not supplied by these helpers alone. P1 must independently bind and
review the affected consumer before real use.

## Foreground encryption and fresh-cache decrypt

`recovery_foreground_crypto.py` supplies fixed GnuPG phases for the future
operator. It hashes and binds canonical installed GPG/GPGCONF tools, requires
a fresh empty owned mode-0700 home beneath a private parent, and holds that
home's identity through the phase. It passes no key in argv, environment,
files or logs. GPG uses ask-mode private entry, disables symmetric caching and
reads the actual held source descriptor. Encryption uses AES256 symmetric
mode. Decrypt requires the trusted fetch return to match the actual held
cipher descriptor, and requires the original captured archive digest as its
expected plaintext hash. A cached local upload with another inode cannot meet
that descriptor/receipt binding. Saved JSON alone is not trusted IPC evidence.

The output descriptor is created and held in the foreground parent before
P1's supervised fork. Only JSON-safe measurements return from the child. The
outer operator must recheck that actual output against the successful return
before upload or inspection. Input signatures and bytes are checked before
and after the tool. Output bytes and signature are measured independently.
GPG stderr is neither retained in receipts nor printed. Successful decrypt
does not establish archive admission, restore fidelity or application recovery.

The helper always attempts bounded GPGCONF cleanup for that same fresh home
after the GPG attempt, refusing cleanup through a replaced home identity.
Cleanup failure leaves the phase unaccepted. No global agent configuration is
changed. Parent death or a forced outer deadline can leave private agent state
unknown and requires explicit operator inspection; no retry or success is
inferred. Private failure evidence retains the separate GPG-phase and cleanup
reason codes when both fail. The cleanup bound does not renew P1's outer phase
allowance.

Crypto tests use harmless fixture executables and invented bytes. They test
command/descriptor protocol and the composed local fetch/decrypt path, not
cryptographic correctness, installed GPG compatibility, an actual key prompt,
custody retrieval or private-entry readiness. No real GPG agent or passphrase
was used in these tests. Those actual route/tool/entry/calibration proofs remain
part of the manager's separate private admission and execution release.

## Ambiguous-attempt inspection

The fixed remote helper also exposes a bounded read-only `inspect` action.
After any interrupted or failed create/upload, inspect the exact approved
namespace, run and two slot names before resolving the attempt. It verifies
the physical actor and bound personal parent, does not create anything or read
or hash slot payloads, and refuses symlink traversal. Its result is only
metadata observation with ownership unresolved. It cannot serve as a create,
upload or fetch receipt and cannot establish that a partial slot completed.

Retain the original failed attempt and inspection privately. Resolve actual
ownership and whether a remote process may still be active before proceeding.
Do not adopt identities from inspection or automatically retry, overwrite,
delete or reuse the failed run. A fresh run ID requires manual verification,
fresh manager binding/release and any required namespace ownership decision.
An unreachable inspect route remains an unresolved attempt. Existing P1
read-only stat/hash actions may be used only after their target ownership is
resolved; SSH authentication failure is never a storage-denial proof.
