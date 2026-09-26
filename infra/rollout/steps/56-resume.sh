#!/usr/bin/env bash
# W10b (RB4-1): reopen the edge after 95-maintenance and 55-runtime-login - the installed
# release's drain.sh resume, which starts the runtime units, waits for both /readyz and only
# then serves. Unlike 91-abort.sh it checks nothing out: RELEASE stays installed. Needs RELEASE.
set -euo pipefail
: "${RELEASE:?the release commit}"
/root/infrx-deploy-"$RELEASE"/apps/infrx-api/deploy/drain.sh resume
