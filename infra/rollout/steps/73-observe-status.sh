#!/usr/bin/env bash
# Read-only: what the monitor sees now (observe.md). Prints metric samples (counts, ages,
# 0/1 gauges with closed labels) and journal tails, which carry no key, DSN or URL.
set -euo pipefail
systemctl list-timers 'infrx-*' --no-pager || true
systemctl --failed --no-pager || true
ls -l --time-style=+%FT%TZ /var/lib/infrx/metrics/ || true
for f in host durable canary; do echo "== $f.prom"; cat "/var/lib/infrx/metrics/$f.prom" 2>/dev/null || echo "(none)"; done
echo "== undelivered (last 5)"; tail -n 5 /var/lib/infrx/metrics/undelivered.jsonl 2>/dev/null || echo "(none)"
journalctl -u infrx-observe -u infrx-canary --since -30min --no-pager | tail -n 40
