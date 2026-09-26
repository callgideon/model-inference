#!/usr/bin/env bash
# E1B's window cells (models/marlin2b/results/E1B-protocol.md §7.2), on the box, as root, inside
# the E4C window: WC-1…WC-5 and WC-8 after runbook §4's report is fetched, then WC-7 ALONE right
# after WC-6a's restored=yes (no request may reach the engine in between):
#   TIMEOUT_S=7200 infra/rollout/ssm.sh infra/rollout/e1b-window.sh RELEASE=<sha>
#   infra/rollout/ssm.sh infra/rollout/e1b-window.sh RELEASE=<sha> CELLS=WC-7
# Each cell runs in the certify image as container infrx-e1b-<cell>, under a copy of its filled
# base (runbook §3 fills /opt/dlami/nvme/e4b/e4c/E1B-*.json from models/marlin2b/profiles/
# E1B-*.base.json) with run_id, dataset_version, rate and concurrency stamped from the cell's own
# command, as certify.cell_profile does. Before every cell it refuses (exit 2) when the engine is
# not at the filled max_num_seqs, when a certify container exists, or when a previous cell left
# its container. DRY_RUN=1 stamps the copies and prints the plan: no docker, no request.
# apps/infrx-api/tests/i/test_rollout.py pins its flags; models/marlin2b/tests/test_profile.py
# validates every planned command against §7.2 as written. Prints names, paths and ids only.
set -euo pipefail
: "${RELEASE:?the release commit}"
nvme=/opt/dlami/nvme; e4b=$nvme/e4b; e4c=$e4b/e4c; repo=$nvme/w3-checkout; M=models/marlin2b
ORDER="WC-1 WC-2 WC-3 WC-4 WC-5 WC-8 WC-7"                      # §7.2's order (WC-6a/b: l8 copies)
CELLS=${CELLS:-WC-1 WC-2 WC-3 WC-4 WC-5 WC-8}
for c in $CELLS; do
  case " $ORDER " in *" $c "*) ;; *) echo "unknown cell $c (this launcher runs: $ORDER)" >&2; exit 2 ;; esac
done
if [[ " $CELLS " == *" WC-7 "* && "$(echo $CELLS)" != WC-7 ]]; then
  echo "WC-7 runs alone, right after WC-6a's restored=yes (§7.2)" >&2; exit 2
fi
for f in E1B-direct.json E1B-box.json E1B-box-forms.json E1B-sop.json keys-certify.json; do
  test -s "$e4c/$f" || { echo "missing $e4c/$f (E1B-protocol §7.1 rule 2)" >&2; exit 2; }
done
seqs=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["identity"]["engine_options"]["max_num_seqs"])' "$e4c/E1B-direct.json")
out=$e4b/e1b-$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$out/profiles" "$out/raw"; chmod 777 "$out" "$out/raw"
echo "out=$out"
BOX="--corpus $M/corpus/manifest.json --subset full --target gateway --model nemostation/marlin-2b --seed 20260922 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --base-url http://127.0.0.1:8001/v1"
DIRECT="--corpus $M/corpus/manifest.json --subset full --target direct --model marlin2b --seed 20260922 --forms video_b64 --max-tokens 128,512,1024 --retries 0 --base-url http://127.0.0.1:8000/v1"

refuse() { echo "refused before $1: $2" >&2; exit 2; }
preflight() {                         # §7.1 rule 1: one engine, so nothing else may be running
  local names args
  names=$(docker ps -a --format '{{.Names}} {{.Image}}')
  if grep -q '^infrx-e1b-' <<< "$names"; then refuse "$1" "a previous cell left $(grep -o '^infrx-e1b-[^ ]*' <<< "$names" | tr '\n' ' ')"; fi
  if grep -v '^infrx-e1b-' <<< "$names" | grep -q ' infrx-certify:'; then refuse "$1" "a certify run is live"; fi
  args=$(docker inspect --format '{{json .Args}}' marlin2b-8000) || refuse "$1" "no engine container marlin2b-8000"
  grep -q "\"--max-num-seqs\",\"$seqs\"" <<< "$args" || refuse "$1" "the engine is not at the pinned max_num_seqs $seqs"
}
# The cell's filled base, stamped from its own command: label = dataset version minus e1b-w1-.
stamp() {
  python3 - "$e4c/$1" "$out/profiles" "${@:2}" <<'PY'
import json, sys
base, dest, argv = sys.argv[1], sys.argv[2], sys.argv[3:]
opt = lambda *names: next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a in names), None)
version, rate, conc = opt("--dataset-version"), opt("--rate"), opt("-c", "--concurrency")
label = version.removeprefix("e1b-w1-")
p = json.load(open(base))
p["identity"]["run_id"] = f"{p['identity']['run_id']}-{label.lower()}"[:64]
p["workload"]["dataset_version"] = version
p["measurement"].update(arrival="open-loop", rate_per_s=float(rate), concurrency=None) if rate else \
    p["measurement"].update(arrival="closed-loop", rate_per_s=None, concurrency=int(conc))
json.dump(p, open(f"{dest}/{label}.json", "w"), indent=1)
print(label)
PY
}
container() {                         # container <name> [docker run flags...] -- <argv...>
  local name=$1; shift
  docker run --rm --name "infrx-e1b-$name" --network host -v "$repo:/repo:ro" \
    -v "$nvme/w3-corpus:/corpus:ro" -v "$e4c:/e4c:ro" -v "$out:/out" -w /repo -e CORPUS_CACHE "$@"
}
export CORPUS_CACHE=/corpus
cell() {                              # cell <WC-n> <base> <bench args...>
  local wc=$1 base=$2 label rc=0 keys=(--env-file "$e4b/key.env"); shift 2
  label=$(stamp "$base" "$@")
  local argv=(python "$M/bench.py" "$@" --profile "/out/profiles/$label.json"
              --key-inventory /e4c/keys-certify.json --out "/out/$label.jsonl" --raw "/out/raw/$label.jsonl")
  [[ " $* " == *" --target direct "* ]] && keys=()            # the engine takes no key
  echo "plan $wc $label ${argv[*]}"
  [ "${DRY_RUN:-0}" = 1 ] && return 0
  preflight "$wc $label"
  container "$label" "${keys[@]}" "infrx-certify:$RELEASE" "${argv[@]}" > "$out/$label.log" 2>&1 || rc=$?
  echo "$wc $label exit=$rc" | tee -a "$out/cells.tsv"
}
sop() {                               # WC-8: dataset.py, interrupted after 3 done, resumed
  python3 - "$repo/$M/corpus-synth/manifest.json" > "$out/sop-incap.jsonl" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
text = {p["id"]: p["text"] for p in m["prompts"]}
for c in m["clips"]:
    if c["duration_s"] <= 82:        # sop00-sop08; the video path is relative to /out/
        print(json.dumps({"id": c["id"], "video": "../corpus/" + c["file"], "prompt": text[c["prompt"]],
                          "max_tokens": 1024, "start_s": 0, "end_s": c["duration_s"]}, sort_keys=True))
PY
  local label rc=0 n=0 run=(python "$M/dataset.py" run --manifest /out/sop-incap.jsonl --state /out/sop.sqlite
    --dataset-version e1b-w1-sop --base-url http://127.0.0.1:8001/v1 --model nemostation/marlin-2b
    --retain-output digest --form upload --concurrency 1)
  label=$(stamp E1B-sop.json "${run[@]}")
  run+=(--profile "/out/profiles/$label.json" --key-inventory /e4c/keys-certify.json)
  local export=(python "$M/dataset.py" export --state /out/sop.sqlite --results /out/sop-results.jsonl
                --failures /out/sop-failures.jsonl)
  echo "plan WC-8 $label ${run[*]}"; echo "plan WC-8 $label-resume ${run[*]}"; echo "plan WC-8 $label-export ${export[*]}"
  [ "${DRY_RUN:-0}" = 1 ] && return 0
  preflight "WC-8 $label"
  container "$label" -d --env-file "$e4b/key.env" "infrx-certify:$RELEASE" "${run[@]}" > /dev/null
  until [ "$n" -ge 3 ] || ! docker inspect "infrx-e1b-$label" > /dev/null 2>&1; do
    sleep 2
    n=$(python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("select count(*) from items where state=?", ("done",)).fetchone()[0])' "$out/sop.sqlite" 2>/dev/null || echo 0)
  done
  if docker kill --signal INT "infrx-e1b-$label" > /dev/null 2>&1; then echo "WC-8 SIGINT after $n done"
  else echo "WC-8 finished before 3 done: not interrupted"; fi
  while docker inspect "infrx-e1b-$label" > /dev/null 2>&1; do sleep 1; done   # exited and removed
  preflight "WC-8 $label-resume"
  container "$label" --env-file "$e4b/key.env" "infrx-certify:$RELEASE" "${run[@]}" > "$out/$label.log" 2>&1 || rc=$?
  echo "WC-8 $label exit=$rc" | tee -a "$out/cells.tsv"
  container "$label" "infrx-certify:$RELEASE" "${export[@]}" >> "$out/$label.log" 2>&1 || true
}

for wc in $ORDER; do
  [[ " $CELLS " == *" $wc "* ]] || continue
  case $wc in
    WC-1) for c in 1 2 4 8; do cell WC-1 E1B-direct.json $DIRECT -c $c -n 64 --engine-state warm --dataset-version e1b-w1-L1-c$c; done ;;
    WC-2) for r in 0.5 2.0; do cell WC-2 E1B-direct.json $DIRECT --rate $r --requests 135 --engine-state warm --dataset-version e1b-w1-pair-r$r; done ;;
    WC-3) cell WC-3 E1B-box.json $BOX --rate 0.5 --burst 8 --requests 135 --dataset-version e1b-w1-L3 ;;
    WC-4) cell WC-4 E1B-box.json $BOX --rate 0.5 --requests 60 --cancel-fraction 0.2 --cancel-after 2 --dataset-version e1b-w1-L5 ;;
    WC-5) cell WC-5 E1B-box-forms.json $BOX --forms upload,video_b64 --rate 0.5 --requests 135 --dataset-version e1b-w1-forms ;;
    WC-8) sop ;;
    WC-7) cell WC-7 E1B-box.json $BOX --rate 0.5 --requests 128 --engine-state restarted --dataset-version e1b-w1-cold ;;
  esac
done
echo "done out=$out"
