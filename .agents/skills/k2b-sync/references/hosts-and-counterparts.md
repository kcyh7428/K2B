# Hosts and counterparts

Mandatory reading before cross-Mac status claims, SJM configuration work, Mini
dispatch or capture-completion reporting, and code transfer between Macs. This runbook
carries the authority of `AGENTS.md`; if it is missing or unreadable, stop the affected
operation.

## Mini routes and Facebook evidence

The former Mac Mini deployment topology remains retired. Keith separately
authorized the Mini as a supervised signed-in browser and media-capture worker
on 25 October 2026 and reaffirmed that scope on 26 October 2026. Facebook
browsing belongs on the Mini under that narrow authorization. Home and SJM
remain the knowledge hosts; the NAS capture share stores media and results.
Do not restore the Mini's old K2B checkout, vault replication, Telegram runtime
or background jobs. Do not interpret the old deployment retirement as revoking
the newer browser-worker authorization.

From SJM, use the existing Home SSH connection as a relay when necessary;
credentials stay on their owning machines. Mini browser control must use an
actually available computer-use connection, with Screen Sharing as a supervised
route. Read the local vault's
`wiki/reference/2026-10-25_k2b-multi-machine-operating-model.md` for tested routes
and dated limits. Confirm input and page results before claiming it works. A reachable
host, installed app, public download or copied result does not prove a native
Mini Codex browser connection or automatic task routing. Use Home or SJM for
signed-in browsing only when Keith explicitly selects that host for the task.
Before dispatch or capture-completion reporting, follow the Facebook evidence
procedure in `k2b-research`: retain pre-transfer source hashes and provenance,
verify the real Mini NAS mount and read-back, and report failed or unverified
arrival as Mini-local and unintegrated. Never treat failed partials as evidence.

## SJM local adaptations

SJM intentionally has local `AGENTS.md` and `.codex/hooks.json` adaptations. Preserve and reconcile those changes deliberately before updating its checkout. Never blind-pull, overwrite, or copy home configuration onto SJM.

## SJM workstation configuration

When this checkout runs on `GLPs-MacBook-Pro` as `keithcheung`:

- Project: `/Users/keithcheung/Projects/K2B`. Vault: `/Users/keithcheung/Projects/K2B-Vault`.
- Translate home-Mac example paths to these local paths. Use local file tools and ripgrep for Vault search; Obsidian is optional.
- Both Macs read the same online NAS vault through their approved aliases above. The former Syncthing vault link connects SJM only to Home and its selected folder stays paused; it is not the current online transport. The Mini's separately authorized supervised browser-worker role does not restore its former K2B deployment.
- The Git remote named `home` is the home MacBook source. Code does not travel through Syncthing.
- SJM is read-only for the shared `K2B-Vault` except for one bounded path: ordinary notes under `wiki/work`, `wiki/people`, `wiki/projects`, `wiki/concepts`, `wiki/insights`, `wiki/reference` may be saved during active user-requested conversations through the local `scripts/vault-note-write.py`, targeting the verified online NAS alias. It takes the locks, checks hashes, preserves conflict copies and updates the folder/master indexes. For every other vault write, a skill must stop and tell Keith to run the write on Home; do not invent a general pending-write format. Only purpose-built source exports, discovery receipts, and small records routed by `scripts/k2b-shared-append.py` may be queued under `~/.local/state/k2b/`.
- SJM unattended work is restricted to local conversation discovery and the explicitly approved native automatic-memory job/probe in `.agents/skills/k2b-scheduler/references/native-capture-and-automation.md`. The native worker may extract the bounded local batch defined in that native-capture runbook into its private outbox; it must not invoke Kimi, reconcile Home memory, edit the Vault, or start other dormant routines.
- Scheduled discovery runs `scripts/codex-discovery-job.sh sjm-source-only`; home uses the same entrypoint with role `home`. Receipts and failure state remain under `~/.local/state/k2b/`.
- The project hook remains SessionStart-only. Before an SJM code update, preserve its existing `AGENTS.md` and `.codex/hooks.json` adaptations and compare them with the incoming portable versions. The shared hook resolves paths from `$HOME`; remove a local divergence only after confirming the incoming file preserves the required SJM behavior.
- In online mode verify the actual SMB target and matching counterpart readback before claiming cross-Mac visibility. For explicitly selected legacy replication, wait for Up to Date before convergence claims or authorized Home shared-note edits. Neither route supplies a distributed file lock.
- Service credentials are per-machine. Never copy the home `.env`, Codex authentication, SSH private keys, or MCP secrets.
- Keep Service Motion footage, results, and any later local models outside K2B and its Vault.
