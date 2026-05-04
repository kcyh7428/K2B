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
~/Library/Logs/k2b-router-watchdog/auto-switch.jsonl
~/Library/Logs/k2b-router-watchdog/install.log
```

## Operations

Check launchd:

```bash
launchctl print gui/$(id -u)/com.k2b.router-watchdog
launchctl print gui/$(id -u)/com.k2b.router-daily-rollup
launchctl print gui/$(id -u)/com.k2b.router-node-score
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

Generate the recommendation digest without sending it:

```bash
bash "$HOME/Library/Application Support/k2b-router-watchdog/bin/digest.sh"
```

Stop the watchdog:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-watchdog.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-daily-rollup.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-node-score.plist
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-digest.plist
```

Start it again:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-watchdog.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-daily-rollup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.k2b.router-node-score.plist
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

## Alert Rules

- First failure: log only.
- Second failure: log only.
- Third consecutive failure: Telegram alert via direct `curl` to `api.telegram.org`.
- Re-alerts use `BACKOFF_SCHEDULE`, default `30m,2h,6h,24h`.
- Recovery after an alerted outage sends one recovery alert.
- A full network partition queues one "was offline, recovered now" alert after Telegram becomes reachable again.
- If `K2B_NETWORK_ALERT_CHAT_ID` is set, alerts go only to that chat/topic, not the main K2B chat.
