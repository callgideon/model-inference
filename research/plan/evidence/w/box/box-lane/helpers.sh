# W3's helpers, verbatim (research/plan/evidence/w/W3-d8a7878.md "The measurement half"), plus
# st/err (status, stderr) and a local command-id log.
wrap() {  # $1 = script, $2 = settings (never a secret): decoded and run on the box
  printf 'echo %s | base64 -d | %s bash' "$(base64 -w0 "$1")" "$2"; }
ssm() {   # $1 = the command line; prints the command id
  env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws ssm send-command \
    --region us-east-1 --instance-ids i-0e8449a4ffca29bab --document-name AWS-RunShellScript \
    --parameters "$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]]}))' "$1")" \
    --query Command.CommandId --output text; }
out() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws ssm \
    get-command-invocation --region us-east-1 --instance-id i-0e8449a4ffca29bab \
    --command-id "$1" --query StandardOutputContent --output text; }
st() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws ssm \
    get-command-invocation --region us-east-1 --instance-id i-0e8449a4ffca29bab \
    --command-id "$1" --query '[Status,ResponseCode]' --output text; }
err() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws ssm \
    get-command-invocation --region us-east-1 --instance-id i-0e8449a4ffca29bab \
    --command-id "$1" --query StandardErrorContent --output text; }
LOG=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/box/ssm-log.tsv
run() {   # $1 = purpose, $2 = command line: send, log id, wait (<= $3 s, default 600), print status + stdout
  local id s i
  id=$(ssm "$2") || return 1
  printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$id" "$1" >> "$LOG"
  for i in $(seq 1 $(( ${3:-600} / 3 ))); do
    s=$(st "$id" 2>/dev/null) || s=Pending
    case "$s" in Success*|Failed*|Cancelled*|TimedOut*) break ;; esac
    sleep 3
  done
  echo "id=$id status=$s"
  out "$id"
  local e; e=$(err "$id"); [ -z "$e" ] || { echo "--- stderr"; echo "$e"; }
}
waitlog() {  # $1 = remote log, $2 = regex that ends the wait, $3 = max minutes; polls every 2 min
  local i id o
  for i in $(seq 1 $(( $3 / 2 ))); do
    sleep 120
    id=$(ssm "tail -c 4000 $1") || continue
    printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$id" "poll $1 (waitlog)" >> "$LOG"
    sleep 5
    o=$(out "$id" 2>/dev/null)
    if printf '%s' "$o" | grep -Eq "$2"; then echo "matched after poll $i ($id)"; printf '%s\n' "$o" | tail -40; return 0; fi
  done
  echo "no match after $3 min"; printf '%s\n' "$o" | tail -20; return 1
}
getrun() {  # $1 = tar base name, $2 = expected sha256, $3 = destination dir in the worktree
  local S=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/box
  local t=$S/$1.tar.gz x=$S/x-$1
  env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 s3 cp "s3://llm-bootcamp-641134885443/w4/$1.tar.gz" "$t" --only-show-errors || return 1
  [ "$(sha256sum < "$t" | cut -d' ' -f1)" = "$2" ] || { echo "sha256 mismatch for $1"; return 1; }
  rm -rf "$x"; mkdir -p "$x"; tar -C "$x" -xzf "$t" || return 1
  echo "fetched $1 sha256=$2 into $x"; find "$x" -maxdepth 2 | head; du -sh "$x"
}
waitfor() {  # $1 = remote log, $2 = end regex, $3 = max polls (150 s apart); prints progress lines
  local i id o
  for i in $(seq 1 "$3"); do
    id=$(ssm "grep -E '^(start=|cell=|level=|restored=|refused|args_diff|image_diff|capability_exit|parity_exit|candidate=|restore:|clip=|bench_exit|reference done|served done|done utc)' $1 | tail -40") || { sleep 30; continue; }
    printf '%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$id" "poll $1" >> "$LOG"
    sleep 6; o=$(out "$id" 2>/dev/null)
    if printf '%s' "$o" | grep -Eq "$2"; then echo "END $(date -u +%H:%M:%SZ) ($id)"; printf '%s\n' "$o" | cut -c1-400; return 0; fi
    [ "$i" -lt "$3" ] && sleep 144
  done
  echo "NOT YET $(date -u +%H:%M:%SZ) ($id)"; printf '%s\n' "$o" | tail -12 | cut -c1-300; return 1
}
