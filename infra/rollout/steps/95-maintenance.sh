#!/usr/bin/env bash
# infra/README.md §8 rule 3: no compatible metered runtime -> maintenance 503 until one
# exists. Admission stops at the edge; the runtime drains. Needs RELEASE (for the
# release's drain.sh extracted by 30-pause).
set -euo pipefail
: "${RELEASE:?the release commit}"
/root/infrx-deploy-"$RELEASE"/apps/infrx-api/deploy/drain.sh pause
