#!/usr/bin/env bash
# Pause or resume admission on the box (infra/README.md §7 steps 2 and 6, §8 rule 3):
#
#   sudo ./apps/infrx-api/deploy/drain.sh pause    # edge -> maintenance 503, then drain
#   sudo ./apps/infrx-api/deploy/drain.sh resume   # runtime up and ready, then edge back
#
# pause: the edge answers every request 503 `dependency_unavailable` + Retry-After first,
# so nothing new is admitted; then the worker stops - W3's drain: stop claiming, finish
# in-flight attempts within the unit's `docker stop -t`, fence and release the rest to
# the store's reaper, never lose them - and the gateway (in-flight
# requests get uvicorn's graceful window). Maintenance is the *active* Caddy site, so a
# Caddy restart keeps it. resume is the reverse, and the edge opens only after readiness.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$here/lib.sh"
mode=$(env_mode "$ROOT$ENV_FILE")
case "$mode" in pilot) runtime_units=$RUNTIME_UNITS_pilot ;; *) runtime_units=$RUNTIME_UNITS_dev ;; esac

case "${1:-}" in
  pause)
    [ -f "$CADDY_DIR/infrx/Caddyfile.maintenance" ] \
      || die "no maintenance site installed (install.sh installs it in pilot mode)" 2
    caddy_site Caddyfile.maintenance \
      || die "the edge did not reload into maintenance: it is still OPEN and admitting; nothing was stopped" 4
    # One call: systemd stops them in reverse start order (the worker is After= the
    # engine and the index, the gateway After= those), each within its TimeoutStopSec.
    systemctl stop $runtime_units
    echo "paused: the edge answers 503; stopped $runtime_units"
    ;;
  resume)
    systemctl start $runtime_units
    wait_ready "$mode" || die "the runtime is not ready; the edge stays in maintenance" 4
    caddy_site Caddyfile
    echo "resumed: $runtime_units ready; the edge serves"
    ;;
  *) die "usage: drain.sh pause|resume" 2 ;;
esac
