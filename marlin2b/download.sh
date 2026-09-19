#!/usr/bin/env bash
# Download this experiment's weights. Thin wrapper: the implementation is in
# common/download.sh so a fix lands once, not five times.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
exec "$here/../common/download.sh" "$here" "$@"
