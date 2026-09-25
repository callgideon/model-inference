#!/usr/bin/env bash
# I8 slice 7, box: bounded cleanup of what the operations lane leaves behind, and nothing
# else. Allowlist - only these paths, only by these patterns:
#   /var/backups/infrx/<UTC>-<sha>/    install backups (they hold past env files: secrets);
#                                      the newest KEEP (default 3, minimum 2) are kept, and
#                                      so is any backup holding a release in known-good.json
#   /opt/dlami/nvme/restore-<id>/, /opt/dlami/nvme/marlin2b.pre-restore-<id>/,
#   /opt/dlami/nvme/marlin2b.failed-<id>/   81-restore-artifacts.sh leftovers, only for the
#                                      RESTORE_ID given (never a wildcard over ids)
# DRY_RUN=1 (the default) prints what it would remove; DRY_RUN=0 removes it.
set -euo pipefail
repo=${REPO:-/home/ubuntu/model-inference}
backups=${BACKUPS:-/var/backups/infrx}
nvme=${NVME:-/opt/dlami/nvme}
KEEP=${KEEP:-3}
DRY_RUN=${DRY_RUN:-1}
[[ $KEEP =~ ^[0-9]+$ ]] && [ "$KEEP" -ge 2 ] || { echo "KEEP is a number >= 2" >&2; exit 2; }
keep_releases=$(python3 -c 'import json,sys; print(" ".join(r["sha"] for r in json.load(open(sys.argv[1]))["releases"] if r.get("known_good")))' \
                "$repo/infra/rollout/known-good.json")
remove() { if [ "$DRY_RUN" = 0 ]; then rm -rf -- "$1"; echo "removed $1"; else echo "would remove $1"; fi; }
mapfile -t dirs < <(find "$backups" -mindepth 1 -maxdepth 1 -type d -regextype posix-extended \
                      -regex '.*/[0-9]{8}T[0-9]{6}(\.[0-9]+)?Z-[0-9a-f]{40}' | sort)
for ((i = 0; i < ${#dirs[@]} - KEEP; i++)); do
  dir=${dirs[$i]}
  held=$(tar -xOf "$dir/files.tar" etc/marlin2b-gateway.env 2>/dev/null | sed -n 's/^INFRX_RELEASE_SHA=//p' | tail -n1)
  case " $keep_releases " in *" ${held:-none} "*) echo "kept $dir (holds known-good $held)"; continue ;; esac
  remove "$dir"
done
if [ -n "${RESTORE_ID:-}" ]; then
  [[ $RESTORE_ID =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "RESTORE_ID is a restore's timestamp" >&2; exit 2; }
  for path in "$nvme/restore-$RESTORE_ID" "$nvme/marlin2b.pre-restore-$RESTORE_ID" "$nvme/marlin2b.failed-$RESTORE_ID"; do
    [ -e "$path" ] && remove "$path"
  done
fi
echo "cleanup done (DRY_RUN=$DRY_RUN, KEEP=$KEEP)"
