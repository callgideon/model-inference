#!/usr/bin/env bash
# Coordinator, box, read-only: the newest certification run's outputs under the E4B output
# root (certify.py --box writes /opt/dlami/nvme/e4b/<UTC>): the directory listing with sizes,
# every *.json under it (bounded to HEAD_BYTES each) and the tail of every *.log. Names,
# verdicts, counts and timings only - the run writes no key, DSN or URL into these files, and
# the env file is never read. Needs nothing; E4B_ROOT, RUN, ONLY and HEAD_BYTES narrow it (SSM
# keeps 24,000 characters of output).
set -euo pipefail
root=${E4B_ROOT:-/opt/dlami/nvme/e4b}
head_bytes=${HEAD_BYTES:-24000}
only=${ONLY:-*.json}          # a glob on the file name; e.g. ONLY=report.json
[ -d "$root" ] || { echo "no $root"; exit 3; }
run=${RUN:-$(find "$root" -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort | tail -n 1)}
dir="$root/$run"
[ -d "$dir" ] || { echo "no run $run under $root"; exit 3; }
echo "== run $run"
find "$dir" -maxdepth 3 -type f -printf '%s\t%TY-%Tm-%TdT%TH:%TM:%TSZ\t%P\n' | sort -k3 | head -n 200
for f in $(find "$dir" -maxdepth 3 -type f -name "$only" | sort | head -n 20); do
  echo "== json ${f#$dir/}"; head -c "$head_bytes" "$f"; echo
done
for f in $(find "$dir" -maxdepth 3 -type f -name '*.log' | sort | head -n 10); do
  echo "== log tail ${f#$dir/}"; tail -n 40 "$f" | cut -c1-300
done
