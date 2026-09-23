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
