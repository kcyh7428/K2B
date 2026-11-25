# K2B vault when the Macs change networks

Keith approved the permanent two-Mac Tailscale vault setup on 25 November
2026 in the Home vault diagnosis chat. Start with Home on the SJM office
network. The related Mac-swapping assessment is chat
`01a20844-d4a5-7f92-a20a-13b46337a0d9`.

On 26 November Keith approved starting the Home cutover independently and
handling SJM separately in the same diagnosis chat. Verify Home's live
Tailscale collection against the NAS server and prior Home publication
evidence before activating Home. Keep matching readbacks from both Macs and
the network-switch trials as acceptance checks for the two-Mac rollout.

Both Macs use `//cheung@100.75.5.21/Keith-Work`. Their vault shortcuts,
mount points and roles stay fixed to the machines. Home keeps
`~/Projects/K2B-NAS`; SJM keeps the Finder-managed `/Volumes/Keith-Work`.
The Home pending writer, publication preflight and local publication
authority must agree on the source. Keep the existing policy hash, writer
locks, managed paths, schedules and per-Mac credentials.

At the start of active K2B work that needs the NAS, run a bounded reconnect
command when the approved mount is missing. Use macOS NetFS and its saved
SMB sign-in, with a custom mount point on Home and the standard volume location
on SJM. The reconnect command compiles the small NetFS caller with the local
macOS command line tools only when a mount is needed. Confirm the exact mount and
resolved alias before reporting success. Refuse an occupied or mismatched
destination and tell Keith if NAS access needs attention. A task can use the
existing eligible local read path or ordinary waiting-edit route when the
NAS is unavailable. The SJM local read preference and Home fallback eligibility
rules retain their existing approvals.

Accept by verifying an authenticated Home mount from SJM, the normal alias and
a bounded index read on each Mac, and an authorized ordinary save from each
Mac with the resulting hash read back through both aliases. Confirm common
collection identity before regular writes and check Home publication readiness
without publishing test content. Test network changes in both placements,
then check the normal alias and an ordinary save again. Exercise the missing
mount and unavailable-server paths. A local remount drill tests the reconnect
command. A real network change remains a separate observation.

Before Home activation, confirm collection identity by comparing an existing
publication marker and hashes through the exact Home SMB mount and the NAS
server, and verify the marker and hashes against a retained LAN-era receipt.
At SJM activation, confirm the same collection through both exact SMB mounts.
Avoid triggering pending replay or publication during the Home cutover.
Preserve the original private Home
`publication-authority.json`, verify it names the exact prior binding and
the current ledger hash, then deploy the reviewed code and atomically change
only `storage.source` in the authority file in the same activation window.
First run the candidate publisher's SMB readiness check against its exact
Tailscale binding. It inspects writability without writing to NAS. Before
deploying, verify that the installed code and authority still name the former
LAN binding and record the authority hash. Apply the reviewed code and
change the authority in the same operator command. If the authority step
fails, keep publication waiting and repair the mismatch before running it.
After the atomic update, compare the saved authority storage object with
the installed `SMB_STORAGE` object and run `live_preflight` against the normal
Home alias. Record the before and after
authority bindings privately. The 81 existing Home LAN publication receipts
remain immutable and usable as history only after verified live Home
preflight. The read-only production inventory verified 81 exact pre-cutover
SMB bindings, 108 older alias-root receipts and no staged publication journals.
All 189 filenames matched their receipt identities. Repeat the inventory
immediately before activation, and stop if a LAN-bound receipt or journal
names a root other than the resolved Home NAS root or differs in binding.

Before the first new publication, rollback can restore Home's prior code and
local publication binding together. It requires a reachable home LAN. After
the first Tailscale-bound receipt, rollback to the former LAN publisher is
unsupported. Recover over Tailscale and carry both sets of immutable
receipts into any reviewed forward fix. If Tailscale is unavailable,
publication waits and local extraction can continue. Home's separate Tailscale Finder
mount at `/Volumes/Keith-Work` can stay mounted. If the former LAN mount is
still present at `~/Projects/K2B-NAS`, the operator must unmount it normally
before reconnecting to the new source. The reconnect command deliberately
refuses to displace any existing mount at the configured mount point.
