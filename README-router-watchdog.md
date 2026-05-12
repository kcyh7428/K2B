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
MIHOMO_API_BASE=http://192.168.50.1:9990
MIHOMO_API_SECRET=<secret>
MIHOMO_OPENAI_GROUP=🤖 OpenAI
EOF
chmod 600 ~/.k2b-router-watchdog.env

bash ~/Projects/K2B/scripts/router-watchdog/install.sh
```

The installer copies the source snapshot from `scripts/router-watchdog/bin/` into:

```text
~/Library/Application Support/k2b-router-watchdog/bin/
```

launchd runs that installed copy. Runtime state and logs live outside the repo:

```text
~/Library/Application Support/k2b-router-watchdog/state.json
~/Library/Application Support/k2b-router-watchdog/pending-partition-events.jsonl
~/Library/Application Support/k2b-router-watchdog/node-top3.json
~/Library/Logs/k2b-router-watchdog/health.jsonl
~/Library/Logs/k2b-router-watchdog/alerts.jsonl
~/Library/Logs/k2b-router-watchdog/node-score.jsonl
~/Library/Logs/k2b-router-watchdog/leaf-optimizer.jsonl
~/Library/Logs/k2b-router-watchdog/auto-switch.jsonl
~/Library/Logs/k2b-router-watchdog/install.log
```

The installer registers the leaf optimizer launchd job, but live mutation still requires the separate `~/.k2b-router-leafopt-enabled` sentinel. Leave that sentinel absent after install until the first live dry-run has been inspected.

## Operations

Check launchd:

```bash
launchctl print gui/$(id -u)/com.k2b.router-watchdog
launchctl print gui/$(id -u)/com.k2b.router-daily-rollup
launchctl print gui/$(id -u)/com.k2b.router-node-score
launchctl print gui/$(id -u)/com.k2b.router-leaf-optimizer
launchctl print gui/$(id -u)/com.k2b.router-digest
```

Run one dry tick:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/check.sh" --dry-run
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

Stop the watchdog:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-watchdog.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-daily-rollup.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-node-score.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-leaf-optimizer.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-digest.plist
```

Start it again:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-daily-rollup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-node-score.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-leaf-optimizer.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-digest.plist
```

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

## Alert Rules

- First failure: log only.
- Second failure: log only.
- Third consecutive failure: Telegram alert via direct `curl` to `api.telegram.org`.
- Re-alerts use `BACKOFF_SCHEDULE`, default `30m,2h,6h,24h`.
- Recovery after an alerted outage sends one recovery alert.
- A full network partition queues one "was offline, recovered now" alert after Telegram becomes reachable again.
- If `K2B_NETWORK_ALERT_CHAT_ID` is set, alerts go only to that chat/topic, not the main K2B chat.
