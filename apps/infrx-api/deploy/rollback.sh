#!/usr/bin/env bash
# Put the files an install.sh run replaced back, and restart onto them:
#
#   sudo ./apps/infrx-api/deploy/rollback.sh /var/backups/infrx/<UTC>-<sha>
#
# This is the unit-and-configuration revert. The whole-host revert is the root-volume
# EBS snapshot taken before the rollout (runbook, a coordinator AWS operation).
#
# infra/README.md §8 and §5.1 decide what a rollback may return to. Once a host serves
# INFRX_MODE=pilot it has admitted metered work, and a runtime without a pilot mode (the
# pre-I2B monolith, or dev) cannot reserve or settle it: that is not a rollback, it is an
# unmetered gateway. Such a restore is refused unless the operator states, in
# ROLLBACK_TO_UNMETERED, that no pilot request was ever accepted (a failed first cutover);
# otherwise the safe state is maintenance (drain.sh pause) until a compatible runtime
# exists. Every refusal happens before anything is stopped or written.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"
backup=${1:-}
[ -n "$backup" ] && [ -f "$backup/files.tar" ] && [ -f "$backup/absent" ] \
  || die "usage: rollback.sh <backup dir with files.tar and absent>" 2

current=$(env_mode "$ROOT$ENV_FILE")
restored=$(tar -xOf "$backup/files.tar" "${ENV_FILE#/}" 2>/dev/null | sed -n 's/^INFRX_MODE=//p' | tail -n1)
if [ "$current" = pilot ] && [ "$restored" != pilot ] \
   && [ "${ROLLBACK_TO_UNMETERED:-}" != "no-pilot-request-was-accepted" ]; then
  die "refused: this host serves INFRX_MODE=pilot and $backup restores '${restored:-no mode}',
which cannot settle metered work (infra/README.md §8). Use drain.sh pause (maintenance 503),
or set ROLLBACK_TO_UNMETERED=no-pilot-request-was-accepted if no pilot request was ever
accepted on this host." 2
fi

# Stop what the current configuration runs (the worker drains within its stop budget).
systemctl stop $RUNTIME_UNITS_pilot 2>/dev/null || systemctl stop marlin2b-gateway
tar -C "${ROOT:-/}" -xpf "$backup/files.tar"
while read -r path; do
  [ -n "$path" ] || continue
  case "$path" in etc/systemd/system/*) systemctl disable --now "$(basename "$path")" 2>/dev/null || true ;; esac
  rm -f "${ROOT:-}/$path"
done < "$backup/absent"
systemctl daemon-reload
case "$restored" in pilot) units=$RUNTIME_UNITS_pilot ;; *) units=$RUNTIME_UNITS_dev ;; esac
systemctl restart $units
wait_ready "${restored:-legacy}" || die "the restored runtime is not ready" 4
# The edge serves whatever site the backup had (none on a pre-pilot host).
if [ -f "$CADDY_DIR/Caddyfile" ] && docker inspect caddy >/dev/null 2>&1; then
  caddy_reload
fi
echo "rolled back to $backup (mode ${restored:-legacy}); restarted $units"
