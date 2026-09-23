# watch.sh LOGFILE ENDREGEX: poll the box log every 150 s via SSM (ids logged), print new
# progress lines once, exit when ENDREGEX appears.
. /tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/box/helpers.sh
seen=$(mktemp)
while :; do
  id=$(ssm "grep -E '^(start=|cell=|level=|restored=|refused|args_diff|image_diff|capability_exit|parity_exit|probe=cancellation|candidate=|restore:|stopping|clip=|bench_exit|reference done|served done|done utc)' $1 | tail -60") || { sleep 60; continue; }
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$id" "poll $1 (watch)" >> "$LOG"
  sleep 6
  o=$(out "$id" 2>/dev/null) || o=""
  printf '%s\n' "$o" | while IFS= read -r line; do
    [ -n "$line" ] && ! grep -qxF -- "$line" "$seen" && { printf '%s\n' "$line" >> "$seen"; echo "$line" | cut -c1-300; }
  done
  printf '%s\n' "$o" | grep -Eq "$2" && exit 0
  sleep 150
done
