#!/usr/bin/env bash
# LOCAL rehearsal of the deploy (I2B.b): the real install.sh, drain.sh, rollback.sh and
# migrate.py, the real unit files, the real runtime image, the pinned Valkey, Caddy and
# supabase/postgres images - on this developer host, never on the box.
#
#   ./apps/infrx-api/deploy/rehearse.sh [workdir]      # needs docker; ~2-5 min
#
# How the box is stood in for, and what that does not prove:
# * "the host" is a sandbox root (INFRX_ROOT) and one network namespace, the container
#   infrx-i2b-box (--network none): every `--network host` unit joins it, so 127.0.0.1 is
#   shared the way the box's is, and nothing listens on this machine's own interfaces.
# * `systemctl` is a mini-systemd that reads the installed unit files and runs their
#   ExecStartPre/ExecStart/ExecStop lines (`+` lines as root through a throwaway root
#   container); `docker` is a wrapper that renames containers/volumes to infrx-i2b-*,
#   maps host paths into the sandbox and joins the box; `curl` runs inside the box;
#   `aws` answers from a local parameter file. The scripts themselves are unmodified.
# * no GPU: the engine unit runs tests/integration/fake_vllm.py (E2's fake) in the runtime
#   image on 127.0.0.1:8000 instead of serve.sh. Nothing here measures the engine.
# * pilot is refused by the real preflight today (G2 composition, W3 entry points), so the
#   deploy rehearsed end to end is dev mode; pilot's refusal is rehearsed as a drill.
# Every container, network and volume it makes is named infrx-i2b-* and labelled
# ai.infrx.i2b.rehearsal=1; teardown removes exactly those and checks nothing is left.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
work=${1:-$(mktemp -d)}
mkdir -p "$work"
work=$(cd "$work" && pwd)
export INFRX_ROOT=$work/root REHEARSAL=$work
BIN=$work/bin
LABEL=ai.infrx.i2b.rehearsal=1
PG_IMAGE=supabase/postgres@sha256:7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd
KEY=infrx-i2b-rehearsal-legacy-key-0123456789     # a local literal, dev mode only
mkdir -p "$INFRX_ROOT/etc" "$BIN"
step() { printf '\n=== %s\n' "$*"; }
check() { if eval "$2"; then echo "PASS $1"; else echo "FAIL $1"; FAILED=1; fi; }
FAILED=0

teardown() {
  set +e
  ids=$(/usr/bin/docker ps -aq --filter "label=$LABEL")
  [ -z "$ids" ] || /usr/bin/docker rm -f $ids >/dev/null
  # files the containers wrote as their own uids
  [ -d "$work" ] && /usr/bin/docker run --rm --user 0 --network none -v "$work:$work" \
    --label "$LABEL" python@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9 \
    sh -c "rm -rf '$work/root'" >/dev/null 2>&1
  /usr/bin/docker network rm infrx-i2b-net >/dev/null 2>&1
  /usr/bin/docker volume ls -q --filter name=infrx-i2b- | xargs -r /usr/bin/docker volume rm >/dev/null
  left=$(/usr/bin/docker ps -aq --filter name=infrx-i2b-; /usr/bin/docker volume ls -q --filter name=infrx-i2b-)
  echo "teardown: $( [ -z "$left" ] && echo 'nothing infrx-i2b-* left' || echo "LEFT: $left")"
}
trap teardown EXIT

# --- the stubs ---------------------------------------------------------------------------
cat > "$BIN/docker" <<'PY'
#!/usr/bin/env python3
"""docker, as the box would see it: containers/volumes/image tags in the infrx-i2b-
namespace, host paths inside the sandbox root, host networking = the box's namespace."""
import os, sys
ROOT, P, REAL = os.environ["INFRX_ROOT"], "infrx-i2b-", "/usr/bin/docker"
HOST = ("/etc/", "/var/lib/infrx", "/var/backups/infrx", "/opt/dlami/nvme")
VALUED = {"--user", "--tmpfs", "--cap-drop", "--cap-add", "--security-opt", "--memory",
          "--cpus", "--pids-limit", "-e", "--env", "--label", "-p", "--gpus", "--entrypoint",
          "-w", "--workdir", "--format", "-t", "--network", "--name", "-v", "--volume",
          "--env-file", "--restart", "-f", "--filter"}
path = lambda p: ROOT + p if p.startswith(HOST) else p
def vol(spec):
    src, sep, rest = spec.partition(":")
    return (path(src) if src.startswith("/") else P + src) + sep + rest
args, out = sys.argv[1:], []
sub = args[0] if args else ""
if sub == "run":
    out, i = ["run", "--label", "ai.infrx.i2b.rehearsal=1"], 1
    while i < len(args):
        a = args[i]
        if a in ("--network",) and args[i + 1] == "host":
            out += ["--network", "container:infrx-i2b-box"]
        elif a == "--name":
            out += ["--name", P + args[i + 1]]
        elif a in ("-v", "--volume"):
            out += ["-v", vol(args[i + 1])]
        elif a == "--env-file":
            out += ["--env-file", path(args[i + 1])]
        elif a == "--restart":
            pass                                    # the rehearsal owns every lifetime
        elif a in VALUED:
            out += [a, args[i + 1]]
        elif a.startswith("-"):
            out.append(a); i += 1; continue
        else:
            out += args[i:]; break                  # the image, then its command
        i += 2
elif sub in ("stop", "rm", "inspect", "logs", "exec", "restart"):
    out, i, named = [sub], 1, False
    while i < len(args):
        a = args[i]
        if a in VALUED:
            out += [a, args[i + 1]]; i += 2; continue
        if a.startswith("-"):
            out.append(a); i += 1; continue
        if sub in ("exec", "inspect", "logs") and named:
            out += args[i:]; break                  # after the container: its command
        out.append(P + a); named = True; i += 1
elif sub in ("build", "image"):
    out = [a.replace("infrx-runtime:", P + "runtime:") for a in args]
else:
    out = args
os.execv(REAL, [REAL, *out])
PY

cat > "$BIN/systemctl" <<'PY'
#!/usr/bin/env python3
"""A mini-systemd for docker-run units: reads the INSTALLED unit files and runs their
Exec lines as systemd would (EnvironmentFile, ${VAR} expansion, `-` and `+` prefixes).
The engine unit's serve.sh is swapped for the fake engine (no GPU here)."""
import os, re, shlex, subprocess, sys, pathlib, time
ROOT, WORK = os.environ["INFRX_ROOT"], pathlib.Path(os.environ["REHEARSAL"])
UNITS = pathlib.Path(ROOT) / "etc/systemd/system"
with open(WORK / "systemctl.log", "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")

def unit(name):
    name = name if "." in name else name + ".service"
    text = re.sub(r"\\\n\s*", " ", (UNITS / name).read_text())
    keys = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith(("#", "[")):
            k, _, v = line.partition("=")
            keys.setdefault(k.strip(), []).append(v.strip())
    return name, keys

def environment(keys):
    env = {}
    for f in keys.get("EnvironmentFile", []):
        for line in pathlib.Path(ROOT + f.lstrip("-")).read_text().splitlines():
            k, _, v = line.partition("=")
            env[k] = v
    for e in keys.get("Environment", []):
        k, _, v = e.partition("=")
        env[k] = v
    return env

def argv_of(line, env):
    line = re.sub(r"\$\{(\w+)\}", lambda m: env.get(m.group(1), ""), line)
    return shlex.split(line)

def run(line, env, background=None):
    ignore, line = line.startswith("-"), line.lstrip("-")
    privileged, line = line.startswith("+"), line.lstrip("+")
    argv = argv_of(line, env)
    if argv[0] == "/usr/bin/docker":
        argv[0] = "docker"                              # the namespacing wrapper
    if argv[0].endswith("/serve.sh"):
        argv = shlex.split(os.environ["REHEARSAL_ENGINE"])
    if privileged:                                      # root, in a throwaway container
        target = argv[-1]
        argv = ["docker", "run", "--rm", "--user", "0", "--network", "none",
                "-v", f"{os.path.dirname(target)}:{os.path.dirname(target)}",
                os.environ["REHEARSAL_IMAGE"], *argv]
    if background:
        with open(background, "a") as out:
            subprocess.Popen(argv, stdout=out, stderr=out, start_new_session=True,
                             env={**os.environ, **env})
        return 0
    code = subprocess.run(argv, env={**os.environ, **env}).returncode
    return 0 if ignore else code

def start(name):
    name, keys = unit(name)
    if name.endswith(".timer"):
        return 0
    env = environment(keys)
    for line in keys.get("ExecStartPre", []):
        if run(line, env):
            return 1
    if keys.get("Type") == ["oneshot"]:
        code = run(keys["ExecStart"][0], env)
        for line in keys.get("ExecStopPost", []):
            run(line, env)
        return code
    run(keys["ExecStart"][0], env, background=WORK / f"{name}.log")
    time.sleep(1)
    return 0

def stop(name):
    name, keys = unit(name)
    if name.endswith(".timer") or not (UNITS / name).exists():
        return 0
    env = environment(keys)
    for line in keys.get("ExecStop", []) + keys.get("ExecStopPost", []):
        run(line, env)
    return 0

verb, names = sys.argv[1], [a for a in sys.argv[2:] if not a.startswith("-")]
if verb in ("daemon-reload", "enable"):
    sys.exit(0)
if verb == "disable":
    sys.exit(max([stop(n) for n in names] if "--now" in sys.argv else [0]))
if verb == "stop":
    sys.exit(max(stop(n) for n in names))
if verb == "start":
    sys.exit(max(start(n) for n in names))
if verb == "restart":
    sys.exit(max(stop(n) or start(n) for n in names))
sys.exit(f"mini-systemd: {verb} is not rehearsed")
PY

cat > "$BIN/curl" <<'SH'
#!/usr/bin/env bash
# curl -fsS -o /dev/null --max-time N URL, from inside the box's network namespace.
exec /usr/bin/docker exec infrx-i2b-box python -c '
import sys, urllib.request
try:
    urllib.request.urlopen(sys.argv[1], timeout=5)
except Exception:
    sys.exit(22)' "${@: -1}"
SH

cat > "$BIN/aws" <<'PY'
#!/usr/bin/env python3
import json, os, sys
spec = json.load(open(os.environ["REHEARSAL"] + "/params.json"))
entry = spec.get(sys.argv[sys.argv.index("--name") + 1])
if entry is None:
    sys.exit(sys.stderr.write("An error occurred (ParameterNotFound) when calling GetParameter\n") and 255)
if "error" in entry:
    sys.exit(sys.stderr.write(f"An error occurred ({entry['error']}) when calling GetParameter\n") and 255)
print(entry["value"])
PY

cat > "$BIN/chown" <<'SH'
#!/usr/bin/env bash
# chown OWNER DIR... as root, through a throwaway container (this host is not root).
owner=$1; shift
for d in "$@"; do
  /usr/bin/docker run --rm --user 0 --network none --label ai.infrx.i2b.rehearsal=1 \
    -v "$d:$d" "$REHEARSAL_IMAGE" chown "$owner" "$d"
done
SH
chmod +x "$BIN"/*
export PATH="$BIN:$PATH"
params() { printf '%s' "$1" > "$work/params.json"; }
params "{\"/model-inference/marlin2b_api_key\": {\"value\": \"$KEY\"}}"

# in-namespace HTTP: status, then body
http() {  # http METHOD URL [header-json] [body]
  /usr/bin/docker exec infrx-i2b-box python -c '
import json, sys, urllib.request
method, url = sys.argv[1], sys.argv[2]
headers = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
body = sys.argv[4].encode() if len(sys.argv) > 4 else None
req = urllib.request.Request(url, data=body, method=method, headers=headers)
try:
    r = urllib.request.urlopen(req, timeout=30); print(r.status); print(r.read().decode()[:300])
except urllib.error.HTTPError as e:
    print(e.code); print(e.read().decode()[:300])
except Exception as e:
    print("ERR", type(e).__name__)' "$@"
}
started() { /usr/bin/docker inspect --format '{{.State.StartedAt}}' "infrx-i2b-$1" 2>/dev/null || echo none; }
sha() { sha256sum "$1" | cut -c1-16; }

# --- the run -------------------------------------------------------------------------------
step "0. the box: pinned images, one network namespace"
cd "$repo"
RELEASE=$(git rev-parse HEAD)
[ -z "$(git status --porcelain)" ] || { echo "the checkout is dirty; commit first"; exit 2; }
docker build -q -f "$here/Dockerfile" -t "infrx-runtime:$RELEASE" "$repo/apps/infrx-api" >/dev/null
export REHEARSAL_IMAGE
REHEARSAL_IMAGE=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$RELEASE")
/usr/bin/docker run -d --name infrx-i2b-box --label "$LABEL" --network none \
  "$REHEARSAL_IMAGE" sleep infinity >/dev/null
export REHEARSAL_ENGINE="docker run --rm --name marlin2b-8000 --network host \
  -v $repo/apps/infrx-api/infrx:/x/apps/infrx-api/infrx:ro \
  -v $repo/tests/integration/fake_vllm.py:/x/tests/integration/fake_vllm.py:ro \
  -e INFRX_E2_REPO_ROOT=/x $REHEARSAL_IMAGE python /x/tests/integration/fake_vllm.py --port 8000"
echo "release $RELEASE"; echo "runtime image $REHEARSAL_IMAGE"
docker run --rm --network none "$REHEARSAL_IMAGE" python -c 'import os, sys; print("image python", sys.version.split()[0], "uid", os.geteuid())'

step "1. fresh dev deploy through install.sh (real preflight probe in the real image)"
set +e
INFRX_MODE=dev RELEASE=$RELEASE ENV_OWNER=$(id -un) READY_S=60 ENGINE_READY_S=60 \
  "$here/install.sh" 2>&1 | tail -n 8
code=${PIPESTATUS[0]}; set -e
check "install.sh dev exits 0 (got $code)" '[ "$code" = 0 ]'
env_file=$INFRX_ROOT/etc/marlin2b-gateway.env
echo "env keys: $(cut -d= -f1 "$env_file" | tr '\n' ' ')"
check "INFRX_IMAGE in the env file is the image the probe ran in" \
  "grep -qx 'INFRX_IMAGE=$REHEARSAL_IMAGE' '$env_file'"
first_env=$(sha "$env_file")
g=$(/usr/bin/docker inspect --format 'user={{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} capdrop={{.HostConfig.CapDrop}} secopt={{.HostConfig.SecurityOpt}} mem={{.HostConfig.Memory}} pids={{.HostConfig.PidsLimit}} image={{.Image}}' infrx-i2b-infrx-gateway)
echo "gateway container: $g"
check "the gateway runs the pinned image as 10001, read-only, no capabilities" \
  "[[ '$g' == *'user=10001:10000 ro=true capdrop=[ALL] secopt=[no-new-privileges]'*'image=$REHEARSAL_IMAGE'* ]]"

step "2. calls on the deployed path (loopback, legacy key: dev mode)"
r=$(http GET http://127.0.0.1:8001/v1/models | head -1); check "GET /v1/models 200 (got $r)" '[ "$r" = 200 ]'
body='{"model":"nemostation/marlin-2b","messages":[{"role":"user","content":"hi"}],"max_tokens":8}'
r=$(http POST http://127.0.0.1:8001/v1/chat/completions '{"Content-Type":"application/json"}' "$body" | head -1)
check "chat without a key is 401 (got $r)" '[ "$r" = 401 ]'
r=$(http POST http://127.0.0.1:8001/v1/chat/completions "{\"Content-Type\":\"application/json\",\"Authorization\":\"Bearer wrong-key-0123456789abcdef\"}" "$body" | head -1)
check "chat with a wrong key is 401 (got $r)" '[ "$r" = 401 ]'
r=$(http POST http://127.0.0.1:8001/v1/chat/completions "{\"Content-Type\":\"application/json\",\"Authorization\":\"Bearer $KEY\"}" "$body" | head -1)
check "chat with the key reaches the engine: 200 (got $r)" '[ "$r" = 200 ]'

step "3. fail-closed drills against the running deploy"
before_env=$(sha "$env_file"); before_start=$(started infrx-gateway)
units_before=$(cat "$INFRX_ROOT"/etc/systemd/system/* | sha256sum | cut -c1-16)
drill() {  # drill NAME EXPECTED-EXIT ENV...
  local name=$1 want=$2; shift 2
  set +e; env "$@" RELEASE="$RELEASE" ENV_OWNER="$(id -un)" "$here/install.sh" > "$work/drill.log" 2>&1
  local got=$?; set -e
  grep -E '^  - |refused|refusing' "$work/drill.log" | head -n 6
  check "$name: exit $want (got $got), env/units byte-identical, gateway not restarted" \
    "[ $got = $want ] && [ \"\$(sha '$env_file')\" = '$before_env' ] && [ \"\$(started infrx-gateway)\" = '$before_start' ] && [ \"\$(cat '$INFRX_ROOT'/etc/systemd/system/* | sha256sum | cut -c1-16)\" = '$units_before' ]"
}
params '{"/model-inference/marlin2b_api_key": {"error": "AccessDeniedException"}}'
drill "a denied SSM read" 2 INFRX_MODE=dev
params '{"/model-inference/marlin2b_api_key": {"error": "ThrottlingException"}}'
drill "a throttled SSM read" 2 INFRX_MODE=dev
params "{\"/model-inference/marlin2b_api_key\": {\"value\": \"$KEY\"}}"
drill "a mistyped tunable" 2 INFRX_MODE=dev INFRX_SET=MAX_ACTIVE_JOB=4
drill "a tunable the runtime cannot read" 2 INFRX_MODE=dev INFRX_SET=MAX_ACTIVE_JOBS=abc
params "{\"/model-inference/supabase_url\": {\"value\": \"https://example.supabase.co\"},
 \"/model-inference/supabase_service_role_key\": {\"value\": \"local-literal-service-role-0123456789\"},
 \"/model-inference/pg_journal_url\": {\"value\": \"postgresql://infrx@127.0.0.1:5432/infrx\"}}"
drill "the repository's pilot today (probe in the real image)" 2 INFRX_MODE=pilot
drill "no mode" 2 INFRX_MODE=
params "{\"/model-inference/marlin2b_api_key\": {\"value\": \"$KEY\"}}"

step "4. the pilot-only index unit (pinned Valkey, loopback, read-only, no persistence)"
systemctl start infrx-valkey
v=$(/usr/bin/docker exec infrx-i2b-infrx-valkey valkey-cli -h 127.0.0.1 ping 2>&1 || true)
check "valkey answers PONG on 127.0.0.1 (got $v)" '[ "$v" = PONG ]'
vi=$(/usr/bin/docker inspect --format 'user={{.Config.User}} ro={{.HostConfig.ReadonlyRootfs}} capdrop={{.HostConfig.CapDrop}}' infrx-i2b-infrx-valkey)
echo "valkey container: $vi"
check "valkey runs as 999, read-only, no capabilities" "[ '$vi' = 'user=999:1000 ro=true capdrop=[ALL]' ]"

step "5. the edge: real Caddyfile, pinned Caddy, in the box (plain HTTP address, no ACME)"
(export INFRX_SITE=http://:8080; . "$here/lib.sh"; edge_install "$here")
sleep 2
out=$(http GET http://127.0.0.1:8080/health); echo "$out"
check "public /health is exactly {\"ok\":true}" "[ \"\$(echo '$out' | tail -1)\" = '{\"ok\":true}' ]"
for p in /metrics /readyz /internal/x; do
  r=$(http GET "http://127.0.0.1:8080$p"); echo "$p -> $(echo "$r" | tr '\n' ' ')"
  check "$p is 404 not_found at the edge" "[[ '$(echo "$r" | head -1)' = 404 && '$r' == *'\"code\":\"not_found\"'* ]]"
done
r=$(http POST http://127.0.0.1:8080/v1/chat/completions "{\"Content-Type\":\"application/json\",\"Authorization\":\"Bearer $KEY\"}" "$body" | head -1)
check "an authenticated call through the edge is 200 (got $r)" '[ "$r" = 200 ]'
r=$(/usr/bin/docker exec infrx-i2b-box python -c '
import urllib.request
req = urllib.request.Request("http://127.0.0.1:8080/v1/chat/completions", data=b"x" * (97 * 2**20), method="POST")
try: urllib.request.urlopen(req, timeout=60); print(200)
except urllib.error.HTTPError as e: print(e.code, e.read().decode()[:120])
except Exception as e: print("ERR", type(e).__name__)')
echo "97 MiB body -> $r"
check "a body over MAX_REQUEST_BYTES is 413 request_too_large at the edge" "[[ '$r' == 413*request_too_large* ]]"

step "6. drain: maintenance at the edge first, then the runtime stops; resume after readiness"
"$here/drain.sh" pause
out=$(http POST http://127.0.0.1:8080/v1/chat/completions '{"Content-Type":"application/json"}' "$body")
echo "during maintenance: $(echo "$out" | tr '\n' ' ')"
check "maintenance answers 503 dependency_unavailable" "[[ '$(echo "$out" | head -1)' = 503 && '$out' == *dependency_unavailable* ]]"
check "the gateway container is gone" '[ "$(started infrx-gateway)" = none ]'
/usr/bin/docker restart infrx-i2b-caddy >/dev/null; sleep 2
r=$(http GET http://127.0.0.1:8080/health | tr '\n' ' ')
check "maintenance survives a Caddy restart (health: $r)" "[[ '$r' == 503* ]]"
"$here/drain.sh" resume
r=$(http GET http://127.0.0.1:8080/health | tr '\n' ' ')
check "resumed: public health up again ($r)" "[[ '$r' == '200 {\"ok\":true}'* ]]"

step "7. rollback: a second deploy (MAX_ACTIVE_JOBS=4), then rollback.sh to the first"
set +e
INFRX_MODE=dev RELEASE=$RELEASE ENV_OWNER=$(id -un) INFRX_SET=MAX_ACTIVE_JOBS=4 READY_S=60 \
  "$here/install.sh" > "$work/second.log" 2>&1; code=$?; set -e
check "second deploy exits 0 (got $code)" '[ "$code" = 0 ]'
check "the second env carries MAX_ACTIVE_JOBS=4" "grep -qx MAX_ACTIVE_JOBS=4 '$env_file'"
second_backup=$(ls -d "$INFRX_ROOT"/var/backups/infrx/* | tail -n1)
before_start=$(started infrx-gateway)
"$here/rollback.sh" "$second_backup" | tail -n 2
check "the env file is the first deploy's again" "[ \"\$(sha '$env_file')\" = '$first_env' ]"
check "the gateway restarted onto it and is ready" "[ \"\$(started infrx-gateway)\" != '$before_start' ] && curl -fsS -o /dev/null http://127.0.0.1:8001/health"

step "8. migrate.py against the pinned supabase/postgres (own network, no host port)"
/usr/bin/docker network create --label "$LABEL" infrx-i2b-net >/dev/null
/usr/bin/docker run -d --name infrx-i2b-postgres --label "$LABEL" --network infrx-i2b-net \
  -e POSTGRES_PASSWORD=infrx-i2b-local "$PG_IMAGE" >/dev/null
for _ in $(seq 60); do
  /usr/bin/docker exec infrx-i2b-postgres pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 2
done
sleep 3
# The Supabase CLI's history table, as a hosted project has it (I1B: 0001-0002 applied).
/usr/bin/docker exec -e PGPASSWORD=infrx-i2b-local infrx-i2b-postgres psql -q -h 127.0.0.1 -U postgres -c \
  "create schema if not exists supabase_migrations; create table if not exists supabase_migrations.schema_migrations (version text primary key, statements text[], name text);"
migrate() {  # migrate DIR ARGS...
  local dir=$1; shift
  /usr/bin/docker run --rm --label "$LABEL" --network infrx-i2b-net -v "$dir:/migrations:ro" \
    -e MIGRATE_DATABASE_URL -e PYTHONDONTWRITEBYTECODE=1 "$REHEARSAL_IMAGE" \
    python /app/deploy/migrate.py "$@" --dir /migrations
}
export MIGRATE_DATABASE_URL=postgresql://postgres:infrx-i2b-local@infrx-i2b-postgres:5432/postgres
mig=$repo/apps/app/supabase/migrations
first2=$work/first2; mkdir -p "$first2"; cp "$mig"/0001_*.sql "$mig"/0002_*.sql "$first2/"
d=$(migrate "$first2" plan | sed -n 's/^plan digest: //p')
migrate "$first2" apply --expect "$d"
out=$(migrate "$mig" plan); echo "$out"
digest=$(echo "$out" | sed -n 's/^plan digest: //p')
applied_versions() { /usr/bin/docker exec -e PGPASSWORD=infrx-i2b-local infrx-i2b-postgres psql -At -h 127.0.0.1 -U postgres -c "select string_agg(version, ',' order by version) from supabase_migrations.schema_migrations"; }
set +e
migrate "$mig" apply --expect "$(printf '0%.0s' $(seq 64))"; code=$?
check "apply with an unreviewed digest refuses: exit 2 (got $code), history 0001,0002" "[ $code = 2 ] && [ \"\$(applied_versions)\" = 0001,0002 ]"
broken=$work/broken; mkdir -p "$broken"; cp "$mig"/*.sql "$broken/"
printf 'create table infrx_i2b_never (x int);\nthis is not sql;\n' > "$broken/0010_broken.sql"
bd=$(migrate "$broken" plan | sed -n 's/^plan digest: //p')
migrate "$broken" apply --expect "$bd"; code=$?
check "a failing migration rolls the whole plan back: exit 3 (got $code), history 0001,0002" "[ $code = 3 ] && [ \"\$(applied_versions)\" = 0001,0002 ]"
t=$(/usr/bin/docker exec -e PGPASSWORD=infrx-i2b-local infrx-i2b-postgres psql -At -h 127.0.0.1 -U postgres -c "select to_regclass('infrx.jobs') is null and to_regclass('public.infrx_i2b_never') is null")
check "no table from 0003-0010 survived the rollback (got $t)" '[ "$t" = t ]'
gap=$work/gap; mkdir -p "$gap"; cp "$mig"/0001_*.sql "$mig"/0003_*.sql "$gap/"
migrate "$gap" plan; code=$?
check "a history the files cannot explain (0002 missing) refuses: exit 2 (got $code)" '[ $code = 2 ]'
migrate "$mig" apply --expect "$digest"; code=$?
set -e
check "the reviewed plan applies: exit 0 (got $code), history 0001..0009" "[ $code = 0 ] && [ \"\$(applied_versions)\" = 0001,0002,0003,0004,0005,0006,0007,0008,0009 ]"
out=$(migrate "$mig" plan | tail -1)
check "a second plan has nothing pending ($out)" '[ "$out" = "nothing pending" ]'

step "result"
[ "$FAILED" = 0 ] && echo "REHEARSAL PASSED" || echo "REHEARSAL FAILED"
exit "$FAILED"
