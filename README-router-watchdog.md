# K2B Router Watchdog

The watchdog observes router/Mihomo/network health, scores proxy nodes, alerts on failures, and can optionally auto-switch the OpenAI proxy group. Auto-switch is off unless the sentinel file exists.

## Install

On the Mac Mini:

```bash
cat > ~/.k2b-router-watchdog.env <<'EOF'
TELEGRAM_BOT_TOKEN=<token>
KEITH_CHAT_ID=8394008217
K2B_NETWORK_ALERT_CHAT_ID=-1003966532428
K2B_NETWORK_ALERT_THREAD_ID=6
MIHOMO_API_BASE=http://192.168.9.1:9090
MIHOMO_API_SECRET=<secret>
MIHOMO_OPENAI_GROUP=🤖 OpenAI
K2B_PRIVATE_VPN_ROUTE_GROUP=🎯 总模式
K2B_PRIVATE_VPN_PRIVATE_GROUP=🔒 私有线路
K2B_PRIVATE_VPN_PRIMARY=🇭🇰 K2B-VPS-HK
K2B_PRIVATE_VPN_FAILOVER=🇹🇼 K2B-VPS-TW
K2B_PRIVATE_VPN_EMERGENCY=🇲🇾 K2B-VPS-KL
K2B_PRIVATE_VPN_FAIL_THRESHOLD=2
K2B_PRIVATE_VPN_RECOVERY_THRESHOLD=5
K2B_PRIVATE_VPN_INCIDENT_KEEP=50
K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS=30
K2B_PRIVATE_VPN_ALERT_RECOVERY_MAX_AGE_HOURS=168
K2B_PRIVATE_VPN_AWS_PROFILE=k2b-aws-signhubdev-hk
K2B_PRIVATE_VPN_AWS_REGION=ap-east-1
K2B_PRIVATE_VPN_AWS_INSTANCE=Ubuntu-1
K2B_PRIVATE_VPN_ROUTER_SSH_TARGET=root@192.168.9.1
K2B_PRIVATE_VPN_ROUTER_SSH_STRICT_HOST_KEY_CHECKING=yes
K2B_PRIVATE_VPN_ROUTER_LAN_IP=192.168.9.1
K2B_PRIVATE_VPN_UPSTREAM_GATEWAY=192.168.1.1
K2B_PRIVATE_VPN_HK_SSH_TARGET=ubuntu@<hk-server-ip>
# Optional: if not using ssh-agent, this must be a non-symlink key under ~/.ssh with 0600 or stricter permissions.
# K2B_PRIVATE_VPN_HK_SSH_KEY=~/.ssh/<key>
EOF
chmod 600 ~/.k2b-router-watchdog.env

bash ~/Projects/K2B/scripts/router-watchdog/install.sh
```

With the ASUS now in AP mode, point the watchdog at the R5C router (`192.168.9.1`) for both the Mihomo API and router SSH trace. Do not point `K2B_PRIVATE_VPN_ROUTER_SSH_TARGET` at the ASUS AP.

Before the first watchdog install on a new Mac Mini, seed the router host key once so strict checking can stay enabled:

```bash
ssh root@192.168.9.1 true
```

If the router host key is intentionally rotated later, refresh the pinned `known_hosts` entry before re-running the watchdog.

The installer copies the source snapshot from `scripts/router-watchdog/bin/` into:

```text
~/Library/Application Support/k2b-router-watchdog/bin/
```

launchd runs that installed copy. Runtime state and logs live outside the repo:

```text
~/Library/Application Support/k2b-router-watchdog/state.json
~/Library/Application Support/k2b-router-watchdog/private-vpn-state.json
~/Library/Application Support/k2b-router-watchdog/r5c-autorecovery-state.json
~/Library/Application Support/k2b-router-watchdog/pending-partition-events.jsonl
~/Library/Application Support/k2b-router-watchdog/node-top3.json
~/Library/Logs/k2b-router-watchdog/health.jsonl
~/Library/Logs/k2b-router-watchdog/private-vpn-health.jsonl
~/Library/Logs/k2b-router-watchdog/alerts.jsonl
~/Library/Logs/k2b-router-watchdog/private-vpn-alerts.jsonl
~/Library/Logs/k2b-router-watchdog/node-score.jsonl
~/Library/Logs/k2b-router-watchdog/leaf-optimizer.jsonl
~/Library/Logs/k2b-router-watchdog/auto-switch.jsonl
~/Library/Logs/k2b-router-watchdog/incidents/
~/Library/Logs/k2b-router-watchdog/r5c-autorecovery.jsonl
~/Library/Logs/k2b-router-watchdog/r5c-autorecovery/
~/Library/Logs/k2b-router-watchdog/install.log
```

The installer registers the leaf optimizer launchd job, but live mutation still requires the separate `~/.k2b-router-leafopt-enabled` sentinel. Leave that sentinel absent after install until the first live dry-run has been inspected.

## Operations

Check launchd:

```bash
launchctl print gui/$(id -u)/com.k2b.router-watchdog
launchctl print gui/$(id -u)/com.k2b.router-private-vpn-watchdog
launchctl print gui/$(id -u)/com.k2b.router-daily-rollup
launchctl print gui/$(id -u)/com.k2b.router-node-score
launchctl print gui/$(id -u)/com.k2b.router-leaf-optimizer
launchctl print gui/$(id -u)/com.k2b.router-digest
launchctl print gui/$(id -u)/com.k2b.router-r5c-autorecovery
```

Run one dry tick:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/check.sh" --dry-run
```

Run one private VPN tick:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/private-vpn-watchdog.sh"
```

Run the daily rollup manually:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/rollup.sh"
```

Score proxy nodes manually:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/score-nodes.sh"
```

Dry-run the manual-selector leaf optimizer:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/optimize-leaves.sh" --dry-run
```

Generate the recommendation digest without sending it:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/digest.sh"
```

When private VPN health rows exist, the digest starts with a combined worst-status line, then prints the private route section before the general router watchdog section. This keeps HK/TW route health visible first without hiding a failing general router watchdog section in Telegram previews.

Stop the watchdog:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-watchdog.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-private-vpn-watchdog.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-daily-rollup.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-node-score.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-leaf-optimizer.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-digest.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-r5c-autorecovery.plist
```

Start it again:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-private-vpn-watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-daily-rollup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-node-score.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-leaf-optimizer.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-digest.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-r5c-autorecovery.plist
```

## Private VPN Watchdog

`com.k2b.router-private-vpn-watchdog` runs every 60 seconds from the Mac Mini. It does not install software on the router and it does not run AWS CLI or SSH during green checks.

The intended private route is:

```text
🎯 总模式 -> 🔒 私有线路 -> 🇭🇰 K2B-VPS-HK -> 🇹🇼 K2B-VPS-TW -> 🇲🇾 K2B-VPS-KL
```

HK is primary, TW is the real fallback, and KL is emergency/monitor-only. Each minute the job reads the Mihomo selector chain and measures HK, TW, KL, and DIRECT against `http://www.gstatic.com/generate_204` and Apple's success URL. Two consecutive HK failures create an incident bundle and Telegram alert. Five consecutive HK successes after an alerted outage create the recovery alert. The expensive checks are limited to incident transitions:

- AWS Lightsail state for HK
- HK server SSH service/socket check
- router SSH log tail
- Mihomo selector/history snapshot

Incident JSON is written under:

```text
~/Library/Logs/k2b-router-watchdog/incidents/
```

The watcher keeps the newest `K2B_PRIVATE_VPN_INCIDENT_KEEP` incident bundles and also prunes bundles older than `K2B_PRIVATE_VPN_INCIDENT_MAX_AGE_DAYS`.

Recovery alerts normally rely on `private-vpn-state.json`. If that state file is deleted while the route is recovering, the watcher can still infer an unresolved outage from `private-vpn-alerts.jsonl` and send one recovery alert when HK is stable again. `K2B_PRIVATE_VPN_ALERT_RECOVERY_MAX_AGE_HOURS` caps that alert-log lookback; the default is 168 hours.

The selector chain and recovery alert wording are snapshots from the start of that tick. The private VPN watchdog never mutates Mihomo selectors, so a simultaneous manual dashboard change or another watchdog mutation can make the snapshot best-effort.

## Auto-Switch

Auto-switch is disabled unless this file exists:

```text
~/.k2b-router-autoswitch-enabled
```

Enable:

```bash
touch ~/.k2b-router-autoswitch-enabled
```

Disable:

```bash
rm -f ~/.k2b-router-autoswitch-enabled
```

When enabled, the only router mutation allowed is:

```text
PUT /proxies/<MIHOMO_OPENAI_GROUP>
{"name":"<known-good-candidate>"}
```

It does not change DNS, mode, provider subscriptions, router config, or Mihomo service state. It only switches the configured `MIHOMO_OPENAI_GROUP` after repeated target failures, only to a recent non-quarantined manual selector candidate, and with a 30-minute switch cooldown.

On a live failure trigger, `check.sh` runs a fresh `score-nodes.sh` pass before auto-switching. The switch is allowed when either:

- the current candidate is scored bad/quarantined, or
- the best healthy manual selector is clearly better than the current candidate by `K2B_AUTOSWITCH_MIN_SCORE_IMPROVEMENT` (default `0.05` score points).

If auto-switch is triggered but blocked, the watchdog emits an `auto_switch_blocked` alert explaining the reason and best candidate, subject to the same Telegram reachability limits as all other alerts.

## Leaf Optimizer

The leaf optimizer is a separate 6-hour automation. It keeps configured inner `♻️ 手动切换*` selector pools ready while leaving outer failover to `auto-switch.py`. Profiles live in:

```text
~/Library/Application Support/k2b-router-watchdog/bin/leaf-optimizer-profiles.json
```

The shipped `ai` profile reads `MIHOMO_OPENAI_GROUP`, scores against ChatGPT, Claude, AI Studio, NotebookLM, and the Gemini API endpoint, excludes HK leaves, and excludes synthetic/meta leaf names matching `^🌏自动最优线路`.

Live leaf changes are disabled unless this file exists:

```text
~/.k2b-router-leafopt-enabled
```

When the sentinel is absent, the scheduled job logs `sentinel_missing` and does not call Mihomo `PUT`. This sentinel is separate from `~/.k2b-router-autoswitch-enabled` because leaf optimization can change several manual selectors in one run, while outer auto-switch only changes `🤖 OpenAI` after failures.

Allowed mutation:

```text
PUT /proxies/♻️ 手动切换N
{"name":"<known-good-non-HK-leaf>"}
```

It preserves diversity so the manual selectors do not all collapse onto the same fastest leaf. It does not mutate `🤖 OpenAI`, Google, YouTube, media groups, DNS, mode, provider subscriptions, or YAML rules.

To avoid route churn, live changes require a 12-hour dwell per selector and two consecutive runs where the same replacement wins. Clearly invalid current leaves, including HK or failing AI leaves, may be replaced immediately unless that selector changed in the last 60 minutes. Optimizer state is stored at:

```text
~/Library/Application Support/k2b-router-watchdog/leaf-optimizer-state.json
```

The optimizer also takes a singleton lock at:

```text
~/Library/Application Support/k2b-router-watchdog/leaf-optimizer.lock
```

Router selector mutation is serialized across the leaf optimizer and the outer auto-switcher with:

```text
~/Library/Application Support/k2b-router-watchdog/mihomo-mutation.lock
```

The leaf optimizer holds this shared lock for the whole mutation pass after scoring. Each selector is guarded with a fresh read immediately before `PUT` and a verify read immediately after `PUT`. Mihomo does not expose a conditional selector update in this setup, so manual dashboard changes outside the watchdog remain a best-effort race; watchdog-owned mutations are serialized.

The scheduled job includes a 300-second launchd `ThrottleInterval` so repeated API or scope failures do not create a rapid restart loop.

Use `K2B_LEAF_OPTIMIZER_CANDIDATE_REGEX` only if selector discovery needs to change outside profile config. The final mutation scope is still hard-locked to literal `♻️ 手动切换*` selector names.

`optimize-leaves.sh` parses `MIHOMO_*` and `K2B_LEAF_OPTIMIZER_*` keys from the watchdog env file without sourcing it. It accepts `KEY=value`, `export KEY=value`, and matching outer quotes, so the existing unquoted `MIHOMO_OPENAI_GROUP=🤖 OpenAI` format remains valid.

To add a future optimizer profile, add a disabled profile to `leaf-optimizer-profiles.json`, set its `group_env_var`, `selector_regex`, targets, sentinel/state/log paths, HK policy, and any `exclude_leaf_regex`; add the matching `MIHOMO_*_GROUP` key to `~/.k2b-router-watchdog.env`; run `install.sh`; then inspect `optimize-leaves.sh --profile <name> --dry-run`. Only enable the profile and consider a sentinel after that dry-run proves it owns a manual-selector parent group. Direct leaf groups such as current `Ⓜ️ 延迟最低` are explicit no-ops because they do not contain `♻️ 手动切换*` children.

## R5C Auto-Recovery

`com.k2b.router-r5c-autorecovery` runs every 60 seconds from the Mac Mini. It is intentionally separate from the 10-minute general health cadence and the private VPN watchdog. It attempts to recover the house network when the R5C is truly hard down by rebooting the ASUS AP, which power-cycles the R5C.

Live recovery is **disabled** unless this sentinel file exists:

```text
~/.k2b-r5c-autorecovery-enabled
```

Enable (only after a supervised live dry-run):

```bash
touch ~/.k2b-r5c-autorecovery-enabled
```

Disable:

```bash
rm -f ~/.k2b-r5c-autorecovery-enabled
```

Run one dry tick:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/r5c-autorecovery.sh"
```

### Fire contract

The helper may attempt an ASUS reboot only when all are true:

1. Three consecutive 60-second checks observe R5C LAN reachability down, cheap router HTTP down, and Mihomo API down.
2. ASUS management path (`admin@192.168.9.2`) is reachable.
3. The live sentinel exists.
4. No cooldown is active.
5. Evidence has been written successfully.
6. A final pre-fire confirmation still sees R5C down and ASUS reachable.

It does **not** fire on `all_private_udp_down`, `hk_only_down`, or `wan_or_wifi_flap` when R5C LAN is still reachable.

### Configuration

Operational values can be overridden with environment variables:

- `K2B_R5C_AUTORECOVERY_R5C_LAN_IP` default `192.168.9.1`
- `K2B_R5C_AUTORECOVERY_MIHOMO_API_BASE` default from `MIHOMO_API_BASE` or `http://192.168.9.1:9090`
- `K2B_R5C_AUTORECOVERY_ASUS_HOST` default `192.168.9.2`
- `K2B_R5C_AUTORECOVERY_ASUS_SSH_TARGET` default `admin@192.168.9.2`
- `K2B_R5C_AUTORECOVERY_ASUS_SSH_KEY` default `~/.ssh/router_id_ed25519`; blank, missing, or unsafe keys fail closed. The helper invokes ASUS SSH with this validated key only.
- `K2B_R5C_AUTORECOVERY_THRESHOLD` default `3`
- `K2B_R5C_AUTORECOVERY_COOLDOWN_MINUTES` default `30`

Probe execution is pinned to trusted `/sbin/ping` and `/usr/bin/curl`; ASUS SSH execution is pinned to trusted `/usr/bin/ssh`. The runtime env file cannot replace these executables.

State, logs, and evidence live outside the repo:

```text
~/Library/Application Support/k2b-router-watchdog/r5c-autorecovery-state.json
~/Library/Logs/k2b-router-watchdog/r5c-autorecovery.jsonl
~/Library/Logs/k2b-router-watchdog/r5c-autorecovery-alerts.jsonl
~/Library/Logs/k2b-router-watchdog/r5c-autorecovery/
~/Library/Logs/k2b-router-watchdog/partition-suppressions.jsonl
```

### Telegram alerting

The helper writes structured JSONL decision rows and evidence files first, then appends R5C alert events to `r5c-autorecovery-alerts.jsonl` and sends them through the installed `send-alert.sh` when available. Alert delivery failure does not block logging, evidence capture, cooldown state, or the ASUS reboot decision. Delivery failures are also counted in the R5C state file; a later successful delivery clears the stale error fields. If `send-alert.sh` is missing or not executable, the wrapper writes an `alerting_disabled` row to `r5c-autorecovery-alerts.jsonl`.

## Alert Rules

- First hard-down sample: one `r5c_hard_down_suspected` alert, no ASUS reboot yet.
- Second hard-down sample: log/evidence only.
- Third consecutive hard-down sample: one `r5c_autorecovery_fired` alert and an ASUS reboot attempt when the sentinel, ASUS SSH, and final confirmation gates pass.
- If the threshold is reached but the sentinel, ASUS SSH, or final confirmation gate blocks action, one `r5c_autorecovery_blocked` alert is emitted for that blocking action.
- Recovery after an R5C-owned incident sends one R5C summary alert: `r5c_autorecovery_recovered` if an ASUS reboot was attempted, otherwise `r5c_autorecovery_cleared`.
- While an R5C incident owns the outage, private-VPN degraded/recovery alerts are folded into the R5C narrative. Completed network-partition recoveries that overlap a closed R5C incident are suppressed; open or malformed R5C state fails open so the Mini does not silently drop the only partition-recovery signal. General watchdog per-check alerts keep their existing thresholds; the current unification is scoped to the alert streams that overlapped in the 2026-06-22 incident.
- Suppressed network-partition recoveries are written to `partition-suppressions.jsonl` with both the partition window and R5C incident window, so the queue drain remains auditable without sending another Telegram message.
- A full network partition queues one "was offline, recovered now" alert after Telegram becomes reachable again.
- If `K2B_NETWORK_ALERT_CHAT_ID` is set, alerts go only to that chat/topic, not the main K2B chat.
