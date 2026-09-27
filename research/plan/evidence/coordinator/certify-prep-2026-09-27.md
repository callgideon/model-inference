# E4C certify + E1B cells: the prepared inputs and commands ("test window on the side")

This was prepared read-only against dcfd9d2d. The tip has since moved to b5339c55, but only plan documents changed; none of the inputs below did. Nothing ran against docker, AWS, SSM, hosted, the box, Vercel or secrets. The only things run were local `bench.py --validate-only`, `dataset.check_profile`, and `infra/rollout/e1b-window.sh` with `DRY_RUN=1` on a scratch tree, with a docker shim that fails on any call. The shim was never called.

- RELEASE: `d3a99e01869b6b9c85173e25888abbbf9ed4dd29`.
- Files are under this scratchpad (`certify-prep/`):
  - `steps/76-e4c-prepare.sh`, `steps/77-e4c-profiles.sh` and `steps/wc0-scrape.sh`: new box steps. They are not committed.
  - `h6.sh`: the H6 inventory, run on the host.
  - `fill.py`: the §3 fill, the same code that 77 embeds. Use it on the host for §5.0.
  - `validate.py`: the local validator.
  - `local-e4c/`: the validation copies.

**Four preconditions that no committed step performs.** Without them, §4 fails or measures the wrong tree:

- **P-a.** The certify, E1B and WC-6 code runs from `/opt/dlami/nvme/w3-checkout` (e4c-certify.sh:46, e1b-window.sh:17, l8ref.sh:14, l8served.sh:8).
  - W8 moved only `/home/ubuntu/model-inference` (40-checkout.sh:10).
  - The last recorded w3-checkout commit is `4226315` (20-platform-handoff-2026-09-24.md:795).
  - Certify's `e4b.b.served-build` check compares the served revision with the `/repo` tree. It would FAIL with "the report's tree is 4226315", and certify.py itself would be an older copy.
- **P-b.** `infrx-certify:$RELEASE` must exist (e4c-certify.sh:20, e1b-window.sh:79). The only recipe is at E4B-9aa7ffe.md:430.
- **P-c.** `/opt/dlami/nvme/e4b/inventory.txt` dates from session 03 (the bda1586 install). W10 reinstalled the engine unit. Certify's `e4b.b.config-pin` reads the file (certify.py:932-948).
- **P-d.** WC-0 (the scrape sidecar) has no launcher. E4C-runbook.md:217 says it "starts with §4".

`steps/76-e4c-prepare.sh` covers P-a to P-c plus A1. `steps/wc0-scrape.sh` covers P-d.

## 1. The six filled profiles

These are filled from the committed bases at RELEASE (the bases are identical at the tip). Only the `FILL` values are replaced. Everything else, including every bound and spend cap, is the committed value (R133).

| Field | Value | Source |
|---|---|---|
| `identity.source_sha` | `d3a99e01869b6b9c85173e25888abbbf9ed4dd29` | RELEASE. Box read in 77: `git -C /opt/dlami/nvme/w3-checkout rev-parse HEAD` == RELEASE and clean (runbook §2) |
| `identity.deployed_sha` | `d3a99e01869b6b9c85173e25888abbbf9ed4dd29` | `infrx_build_info{revision}` on 127.0.0.1:8001 **and** :8002, read in 77. It must equal RELEASE (install sets `INFRX_RELEASE_SHA`; pilot.py:279) |
| `identity.image_digest` | `sha256:faa5b681878d441b2c4571409dcc73ead01ee957e3123e80517cdc9b8e9ccd5e` | The W10 image (RELEASE-d3a99e01.md W13 row). Box read in 77: `docker image inspect infrx-runtime:$RELEASE` .Id == `docker inspect infrx-gateway` .Image |
| `identity.weights_sha256` | `sha256:91a81b77f73b647ff84d32cad09ded838966b1dea605024d1e082ae7f3b20bfc` | The R148 shard-set digest. Recomputed here from RELEASE's `serving-version.json` (runbook §2 formula): equal |
| `identity.processor_sha256` | `sha256:d89ef49ce9cd37fbf510158e13c1ef063d9286411c1ec9049932dbe0487143b1` | `serving-version.json` `model.processor_config_digest` (P-06 measured 2026-09-26, SSM eb00392c) |
| `identity.migration_version` | `0026` | W7's post plan `applied: 0001 … 0026` (RELEASE record, 06:53Z). 77 and fill.py refuse anything below 0026 (R147) |
| `identity.config_version` | `sha256:<sha256sum /etc/marlin2b-gateway.env>` | **Box read only**, taken by 77 after W10b rewrote the env file at 07:31Z. It is unknown here. The local copies carry a `BOX-READ …` marker, and the box copies carry the real hash |
| `target.maintenance_window` | proposed `e4c-side-d3a99e01-<UTC start>`, for example `e4c-side-d3a99e01-20260927T1000Z` | The coordinator logs the window id (runbook §1 step 1). The same id is passed as 77's `MAINTENANCE_WINDOW` and recorded in freeze.json |
| `target.allowed_fault_targets[1..2]` (E4C-box only) | `infrx-worker`, `infrx-valkey` | The W10 units (apps/infrx-api/deploy/*.service). 77 checks that each is `active`. `[0]` stays the committed `marlin2b-vllm` |
| unchanged, committed | `engine_options {"max_num_seqs": 8, "max_video_seconds": 82}`, tokenizer `sha256:06b95093…e523`, template `sha256:273d8e0e…2d80` | Checked equal to `serving-version.json` by fill.py. The engine options digest `sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147` is **not** a profile field; it goes to freeze.json via `certify --hashes`, which 77 prints |
| unchanged, committed: the card and key | rates 400/1200 CREDIT (card `rc_marlin2b_20260925_launch`), `test_key_ids ["142c7d81"]`, `tenant_key_env ["INFRX_API_KEY"]` | the bases |
| unchanged, committed: spend caps (CREDIT) | E4C-box 50,000 · E4C-edge 433 · E1B-box and E1B-box-forms 1,824.768 · E1B-sop 121.6512 · E1B-direct null (engine direct, unmetered) | the bases, per E1B-protocol §7.2 and the runbook §3 projections |

Where each profile goes on the box (runbook §3) and its committed base sha256:

| Box path | From base | Base sha256 |
|---|---|---|
| `/opt/dlami/nvme/e4b/e4c/E4C-box.json` | `E4C-box.base.json` | `c0d4aa1b…d2ff` |
| `/opt/dlami/nvme/e4b/e4c/E4C-edge.json` | `E4C-edge.overload.base.json` | `7007d970…3689` |
| `/opt/dlami/nvme/e4b/e4c/E1B-direct.json` | `E1B-direct.base.json` | `80357ef7…52f5` |
| `/opt/dlami/nvme/e4b/e4c/E1B-box.json` | `E1B-box.base.json` | `91abe6db…b108` |
| `/opt/dlami/nvme/e4b/e4c/E1B-box-forms.json` | `E1B-box.forms.base.json` | `c6dda4d2…fcba` |
| `/opt/dlami/nvme/e4b/e4c/E1B-sop.json` | `E1B-sop.base.json` | `9784861c…6aba` |
| `/opt/dlami/nvme/e4b/e4c/keys-certify.json` | H6, via 77's args | — |

The real filled sha256 values come from 77's output, which feeds profiles.sha256. The local validation copies differ from the box copies only in `config_version`, and in `maintenance_window` if another id is chosen.

**Two-tenant profiles (E4C-two-tenant, E1B-two-tenant): not filled.** Their input, the tenant-2 key's **id** prefix, does not exist until §5.0. fill.py refuses without `TENANT2_PREFIX`, and a stand-in fills and passes. The §5.0 command is in A6.

### Local validation (all `runnable: true`)

`validate.py` runs bench with `INFRX_API_KEY`/`MARLIN_API_KEY` unset, the inventory `{"active_key_id_prefixes":["142c7d81"]}`, and the RELEASE tree. No `--out` was created.

| Cell (profile) | Requests | Projected CREDIT | Errors, blocks, warnings |
|---|---|---|---|
| §3 box line (E4C-box, 0.5/s) | 3600 | 48660.4800 | none |
| §3 edge line (E4C-edge, 1000/s, burst 32) | 32 | 432.5376 | none |
| certify envelope r0.5, r1.0 and r2.0 (box stamped as `cell_profile` does it) | 135 each | 1824.7680 each | none |
| certify soak (box, 0.25/s) | 3600 | 48660.4800 | none |
| certify overload (edge stamped P4) | 32 | 432.5376 | none |
| WC-1 L1-c1/c2/c4/c8 (E1B-direct) | 64 each | — | block "no approved rates…" plus warning "blocks do not apply to a local or fake target". This is **expected**: engine direct is unmetered (the test_profile.py oracle) |
| WC-2 pair-r0.5 and pair-r2.0 (E1B-direct) | 135 each | — | the same expected block and warning |
| WC-2 pair-c1 (E1B-box, tip launcher) | 64 | 865.0752 | none |
| WC-3 L3 (E1B-box) | 135 | 1824.7680 | none |
| WC-4 L5 (E1B-box) | 60 | 811.0080 | none |
| WC-5 forms (E1B-box-forms) | 135 | 1824.7680 | none |
| WC-8 sop (E1B-sop, `dataset.check_profile`; dataset.py has no `--validate-only`) | 9 | — | none (refusal None) |
| WC-7 cold (E1B-box) | 128 | 1730.1504 | none |

The launcher's DRY_RUN plans (default cells, then `CELLS=WC-7`) held the 12 cells §7.2 lists, plus the WC-8 resume and export lines. On the box, 77 re-validates the §3 lines and the three E1B bases in `infrx-certify:$RELEASE` with `--network none`.

## 2. Getting the profiles onto the box

There is no existing put-file step. ssm.sh passes only `NAME=VALUE` exports plus the step text (ssm.sh:24-29). release-bundle's fetch goes through S3. The smallest correct route is to fill **on the box** from the committed bases in the w3-checkout, which after 76 is byte-identical to RELEASE. That follows runbook §2: "every value is read from the served build, never typed from memory." Only the coordinator's inputs travel as args: the migration version, the window id and H6's inventory values. keys-certify.json is written from those args, so no file transfer is needed.

```bash
infra/rollout/ssm.sh <scratch>/certify-prep/steps/76-e4c-prepare.sh RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29
infra/rollout/ssm.sh <scratch>/certify-prep/steps/77-e4c-profiles.sh RELEASE=d3a99e01869b6b9c85173e25888abbbf9ed4dd29 \
  MIGRATION_VERSION=0026 MAINTENANCE_WINDOW=<window id> KEYS_TAKEN_AT=<h6 as_of> KEYS_SOURCE_SHA256=<h6 sha256> ACTIVE_PREFIXES=142c7d81
```

(To commit them later, put both under `infra/rollout/steps/` and extend test_rollout.py.)

### steps/76-e4c-prepare.sh

```bash
#!/usr/bin/env bash
# E4C preconditions on the box, as root (E4C-runbook §1 step 4, §2 source_sha, §4 image; E4B-9aa7ffe.md
# steps 2-3 and :430), none of which a committed step performs:
#   A1  the public edge answers 200 from the box (the P4 burst leaves the box and re-enters through Caddy)
#   1   the measurement checkout /opt/dlami/nvme/w3-checkout at RELEASE, clean and detached, from W1's
#       verified bundle (40-checkout.sh moves only /home/ubuntu/model-inference; certify, e1b-window.sh and
#       l8ref/l8served.sh run from w3-checkout)
#   2   the certify image infrx-certify:$RELEASE (the runtime image + git), built once
#   3   a fresh engine inventory /opt/dlami/nvme/e4b/inventory.txt (certify --inventory; W10 reinstalled the unit)
#   infra/rollout/ssm.sh <this file> RELEASE=<40-hex>
# Idempotent. Exit 2 before anything changes: edge not 200, a dirty checkout, a bundle that fails its sha256.
set -euo pipefail
: "${RELEASE:?the release commit}"
nvme=/opt/dlami/nvme; c=$nvme/w3-checkout; rel=$nvme/releases
g() { git -c safe.directory='*' -C "$c" "$@"; }
code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://marlin2b.callbill.ai/health || true)
echo "A1 edge /health from the box: $code"
[ "$code" = 200 ] || { echo "the edge is not open (maintenance?): stop" >&2; exit 2; }
if [ -n "$(g status --porcelain)" ]; then
  echo "w3-checkout is dirty (move the files aside as 2026-09-24's w3-untracked-<utc>/, then rerun):" >&2
  g status --porcelain | head -10 >&2; exit 2
fi
if [ "$(g rev-parse HEAD)" != "$RELEASE" ]; then
  echo "w3-checkout was at $(g rev-parse HEAD)"
  ( cd "$rel" && sha256sum -c "$RELEASE.sha256" ) || exit 2
  g fetch -q "$rel/$RELEASE.bundle" "+refs/infrx/release:refs/infrx/releases/$RELEASE"
  g checkout -q --detach "$RELEASE"
fi
[ "$(g rev-parse HEAD)" = "$RELEASE" ] && [ -z "$(g status --porcelain)" ]
echo "w3-checkout at $RELEASE (clean)"
docker image inspect "infrx-certify:$RELEASE" > /dev/null 2>&1 || \
  printf 'FROM infrx-runtime:%s\nUSER 0\nRUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n' \
    "$RELEASE" | docker build -q -t "infrx-certify:$RELEASE" - > /dev/null
echo "certify image infrx-certify:$RELEASE $(docker image inspect --format '{{.Id}}' "infrx-certify:$RELEASE")"
inv=$nvme/e4b/inventory.txt
CONTAINER=marlin2b-8000 ENGINE=http://127.0.0.1:8000 WEIGHTS=$nvme/marlin2b \
  bash "$c/models/marlin2b/measure/inventory.sh" > "$inv.new" 2>&1 || echo "inventory exit $?"
[ -s "$inv" ] && cp -p "$inv" "$nvme/e4b/inventory.$(date -u +%Y%m%dT%H%M%SZ).prev"
mv -f "$inv.new" "$inv"; chmod 644 "$inv"
echo "inventory $(sha256sum "$inv" | cut -d' ' -f1) $(wc -l < "$inv") lines"
grep -E '^(image_equals_pin|args)=' "$inv" | cut -c1-400
```

### steps/77-e4c-profiles.sh (the heredoc body is fill.py verbatim)

```bash
#!/usr/bin/env bash
# E4C-runbook §2/§3 + H6 on the box, as root: fill the six committed bases from the served build into
# /opt/dlami/nvme/e4b/e4c/ (FILL values only, nothing else; R133), write keys-certify.json from H6's
# dry run (prefixes only), validate with bench --validate-only in the certify image (no network, no
# key), and print certify --hashes from the checkout for freeze.json. Needs 76-e4c-prepare.sh first.
#   infra/rollout/ssm.sh <this file> RELEASE=<40-hex> MIGRATION_VERSION=0026 MAINTENANCE_WINDOW=<window id> \
#     KEYS_TAKEN_AT=<H6 dry-run as_of> KEYS_SOURCE_SHA256=<H6 dry-run JSON sha256> [ACTIVE_PREFIXES=142c7d81]
# Every identity is read here (deployed_sha from both /metrics, image from docker, config_version =
# sha256 of the env file - the hash only, never the file); exit 2 before a file is written when a read disagrees.
set -euo pipefail
: "${RELEASE:?}" "${MIGRATION_VERSION:?}" "${MAINTENANCE_WINDOW:?}" "${KEYS_TAKEN_AT:?}" "${KEYS_SOURCE_SHA256:?}"
ACTIVE_PREFIXES=${ACTIVE_PREFIXES:-142c7d81}
nvme=/opt/dlami/nvme; e4c=$nvme/e4b/e4c; repo=$nvme/w3-checkout; env_file=/etc/marlin2b-gateway.env
refuse() { echo "refused: $*" >&2; exit 2; }
g() { git -c safe.directory='*' -C "$repo" "$@"; }
[[ $KEYS_SOURCE_SHA256 =~ ^[0-9a-f]{64}$ ]] || refuse "KEYS_SOURCE_SHA256 is the dry-run JSON's 64-hex sha256"
[[ $ACTIVE_PREFIXES =~ ^[0-9a-f]{8,12}(,[0-9a-f]{8,12})*$ ]] || refuse "ACTIVE_PREFIXES: comma list of 8-12 hex id prefixes"
[ "$(g rev-parse HEAD)" = "$RELEASE" ] && [ -z "$(g status --porcelain)" ] || refuse "w3-checkout is not a clean $RELEASE (76-e4c-prepare.sh)"
rev() { curl -s -m 5 "127.0.0.1:$1/metrics" | sed -n 's/^infrx_build_info{.*revision="\([0-9a-f]*\)".*/\1/p' | head -n 1; }
gw=$(rev 8001); wk=$(rev 8002)
[ "$gw" = "$RELEASE" ] && [ "$wk" = "$RELEASE" ] || refuse "infrx_build_info revision: gateway '$gw', worker '$wk'"
img=$(docker image inspect --format '{{.Id}}' "infrx-runtime:$RELEASE")
[ "$(docker inspect --format '{{.Image}}' infrx-gateway)" = "$img" ] || refuse "infrx-gateway does not run infrx-runtime:$RELEASE"
for u in marlin2b-vllm infrx-worker infrx-valkey; do [ "$(systemctl is-active "$u")" = active ] || refuse "$u is not active"; done
docker image inspect "infrx-certify:$RELEASE" > /dev/null || refuse "no infrx-certify:$RELEASE (76-e4c-prepare.sh)"
export RELEASE DEPLOYED_SHA=$gw IMAGE_DIGEST=$img MIGRATION_VERSION MAINTENANCE_WINDOW
CONFIG_VERSION=sha256:$(sha256sum "$env_file" | cut -d' ' -f1); export CONFIG_VERSION
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
python3 - "$repo" "$tmp" E4C-box.json=E4C-box.base.json E4C-edge.json=E4C-edge.overload.base.json \
  E1B-direct.json=E1B-direct.base.json E1B-box.json=E1B-box.base.json \
  E1B-box-forms.json=E1B-box.forms.base.json E1B-sop.json=E1B-sop.base.json <<'PY'
import hashlib, json, os, re, sys

repo, dest, pairs = sys.argv[1], sys.argv[2], [a.split("=", 1) for a in sys.argv[3:]]
env = os.environ
sv = json.load(open(f"{repo}/models/marlin2b/serving-version.json"))["model"]
weights = "sha256:" + hashlib.sha256(json.dumps(sv["weight_shard_digests"],
                                                separators=(",", ":")).encode()).hexdigest()
identity = {"source_sha": env["RELEASE"], "deployed_sha": env["DEPLOYED_SHA"],
            "image_digest": env["IMAGE_DIGEST"], "weights_sha256": weights,
            "processor_sha256": sv["processor_config_digest"],
            "migration_version": env["MIGRATION_VERSION"], "config_version": env["CONFIG_VERSION"]}
faults = {"FILL worker unit": "infrx-worker", "FILL valkey unit": "infrx-valkey"}
problems = []
if env["DEPLOYED_SHA"] != env["RELEASE"]:
    problems.append(f"deployed_sha {env['DEPLOYED_SHA']} != RELEASE {env['RELEASE']}")
if not re.fullmatch(r"\d{4}", env["MIGRATION_VERSION"]) or int(env["MIGRATION_VERSION"]) < 26:
    problems.append("migration_version must be 0026 or newer (R147, runbook §2)")
if not env.get("MAINTENANCE_WINDOW", "").strip():
    problems.append("MAINTENANCE_WINDOW is empty")
out, log = {}, []
for name, base in pairs:
    path = f"{repo}/models/marlin2b/profiles/{base}"
    raw = open(path, "rb").read()
    p = json.loads(raw)
    ident, target = p["identity"], p["target"]
    for k, v in (("tokenizer_sha256", sv["tokenizer_digest"]),
                 ("template_sha256", sv["chat_template_digest"])):
        if ident[k] != v:
            problems.append(f"{base}: {k} {ident[k]} != serving-version.json {v}")
    for k, v in identity.items():
        if str(ident.get(k, "")).startswith("FILL"):
            ident[k] = v
            log.append(f"{name} identity.{k} = {v}")
    if str(target["maintenance_window"]).startswith("FILL"):
        target["maintenance_window"] = env["MAINTENANCE_WINDOW"]
        log.append(f"{name} target.maintenance_window = {env['MAINTENANCE_WINDOW']}")
    for i, v in enumerate(target["allowed_fault_targets"]):
        if v in faults:
            target["allowed_fault_targets"][i] = faults[v]
            log.append(f"{name} target.allowed_fault_targets[{i}] = {faults[v]}")
    for i, v in enumerate(target["test_key_ids"]):
        if v.startswith("FILL"):
            t2 = env.get("TENANT2_PREFIX", "")
            if not re.fullmatch(r"[0-9a-f]{8,12}", t2):
                problems.append(f"{base}: test_key_ids[{i}] needs TENANT2_PREFIX (8-12 hex, §5.0)")
            target["test_key_ids"][i] = t2
            log.append(f"{name} target.test_key_ids[{i}] = {t2}")
    if "FILL" in json.dumps(p):
        problems.append(f"{base}: a FILL remains")
    out[name] = (hashlib.sha256(raw).hexdigest(), base, json.dumps(p, indent=1) + "\n")
if problems:
    print("\n".join(f"refused: {x}" for x in problems), file=sys.stderr)
    sys.exit(2)
os.makedirs(dest, exist_ok=True)
for name, (base_sha, base, text) in out.items():
    with open(f"{dest}/{name}", "w") as f:
        f.write(text)
    print(f"base {base_sha} {base}")
    print(f"filled {hashlib.sha256(text.encode()).hexdigest()} {name}")
print("\n".join(log))
PY
python3 -c 'import json,sys; print(json.dumps({"active_key_id_prefixes": sys.argv[1].split(","), "taken_at": sys.argv[2], "source": "G8 credit-transition --dry-run sha256:" + sys.argv[3]}))' \
  "$ACTIVE_PREFIXES" "$KEYS_TAKEN_AT" "$KEYS_SOURCE_SHA256" > "$tmp/keys-certify.json"
mkdir -p "$e4c"; chmod 755 "$e4c"
for f in "$tmp"/*.json; do install -m 0644 "$f" "$e4c/"; done   # replaces only these seven names
( cd "$e4c" && sha256sum E4C-box.json E4C-edge.json E1B-direct.json E1B-box.json E1B-box-forms.json E1B-sop.json keys-certify.json )
v() {  # bench --validate-only in the certify image: no network, no key; prints the verdict's essentials
  docker run --rm --network none -v "$repo:/repo:ro" -v "$e4c:/e4c:ro" -w /repo "infrx-certify:$RELEASE" \
    python models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json --subset full --seed 20260922 \
      --max-tokens 128,512,1024 --retries 0 --key-inventory /e4c/keys-certify.json "$@" --validate-only \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("runnable", d["runnable"], "errors", d["errors"], "blocks", d["blocks"], "warnings", d["warnings"], "projected", d["derived"].get("projected_spend"))'
}
BOX="--target gateway --model nemostation/marlin-2b --base-url http://127.0.0.1:8001/v1"
echo "== E4C-box (§3, 0.5 x 3600)";  v $BOX --forms video_b64 --rate 0.5 --requests 3600 --dataset-version e4c-1 --profile /e4c/E4C-box.json
echo "== E4C-edge (§3, P4 burst 32)"; v --target gateway --model nemostation/marlin-2b --base-url https://marlin2b.callbill.ai/v1 --forms video_b64 --rate 1000.0 --requests 32 --burst 32 --dataset-version e4c-1-overload --profile /e4c/E4C-edge.json
echo "== E1B-box";       v $BOX --forms video_b64 --rate 0.5 --requests 135 --dataset-version e1b-w1-box --profile /e4c/E1B-box.json
echo "== E1B-box-forms"; v $BOX --forms upload,video_b64 --rate 0.5 --requests 135 --dataset-version e1b-w1-forms --profile /e4c/E1B-box-forms.json
echo "== E1B-direct (engine direct: one block + 'blocks do not apply to a local or fake target' expected)"
v --target direct --model marlin2b --base-url http://127.0.0.1:8000/v1 --forms video_b64 -c 1 -n 64 --dataset-version e1b-w1-direct --profile /e4c/E1B-direct.json
echo "== certify --hashes (freeze.json)"
docker run --rm --network none -v "$repo:/repo:ro" -w /repo "infrx-certify:$RELEASE" python tests/integration/backend/certify.py --hashes
```

## 3. H4, H5 and H6: exact commands (coordinator host, repo root, after `make api-env`)

Common setup: `L=~/infrx-e4c/$(date -u +%Y%m%dT%H%M%SZ); umask 077; mkdir -p "$L"; chmod 700 "$L"`, and `aws() { env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN aws --region us-east-1 "$@"; }`. `CLI` = `infra/rollout/operator-cli.sh`, which reads `pg_journal_url` and `operator_key` from SSM by name.

### The certify tenant's user uuid (needed by H5)

The repository holds it only truncated (`f997131f…`: 20-platform-handoff:851, session-02:889). No CLI verb maps an org_id or key_id to a user. The read path is the **`statement` verb** (cli.py:129-130,160-162). It authenticates with the tenant's own key file and returns `{"org_id","user_id","credit",…}` (service.py:167-175):

```bash
aws ssm get-parameter --with-decryption --name /model-inference/e4b_api_key --query Parameter.Value --output text > "$L/certify.key"
CLI statement --key-file "$L/certify.key" > "$L/h5-statement-before.json"; echo "exit $?"; shred -u "$L/certify.key"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["user_id"], d["org_id"], d["credit"]["available"])' "$L/h5-statement-before.json"
```

- Expected: `f997131f-…  15e766d0-8c4d-47a8-986f-22ed32f390c3  ~9999.9…`. That is 10,000 less the W12 CREDIT smoke debits after the 06:56Z cutover.
- **Stop** if `org_id` is not `15e766d0-8c4d-47a8-986f-22ed32f390c3`.
- SQL fallback (owner login, read-only; no repo tool wraps it): `select user_id, org_id, audience, revoked_at from public.api_keys where id::text like '142c7d81%';`.

### H4: tenant 2 (account only, **no key**)

```bash
export SUPABASE_URL=$(aws ssm get-parameter --name /model-inference/supabase_url --query Parameter.Value --output text)
export SUPABASE_SERVICE_ROLE_KEY=$(aws ssm get-parameter --with-decryption --name /model-inference/supabase_service_role_key --query Parameter.Value --output text)
openssl rand -base64 24 | tr -d '\n' > "$L/test2.password"; export INFRX_TEST_USER_PASSWORD=$(cat "$L/test2.password")
apps/infrx-api/.venv/bin/python infra/app/create-test-user.py --email rey+infrx-test2@callsofia.co --dry-run
apps/infrx-api/.venv/bin/python infra/app/create-test-user.py --email rey+infrx-test2@callsofia.co --json > "$L/test2-user.json"; echo "exit $?"
unset SUPABASE_SERVICE_ROLE_KEY INFRX_TEST_USER_PASSWORD
U2=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["user_id"])' "$L/test2-user.json")
CLI grant --user "$U2" --idempotency-key grant-tenant2-20260925 --reason "E4C second test tenant" | tee "$L/h4-grant.json"
CLI account --user "$U2" > "$L/h4-account.json"
```

- Expected from create-test-user: `confirmed: true, created: true, grant: "granted", available: "10000.00000000", unit: "CREDIT"`, exit 0.
  - The script already claims the grant through `claim_signup_grant`, the function the CLI `grant` also calls (state/signup.py:27,86-93). The runbook's H4 `grant` therefore answers `"replayed": true` with the same wallet and 10000.00000000. It is still worth running for the audited op row.
  - "Read `auth.users.email_confirmed_at`" is satisfied: the tool refuses unless GoTrue returns `email_confirmed_at` (create-test-user.py:103-104).
  - Exit 3 "address exists" means an earlier attempt created the user. Rerun with `--reset-existing`.
- `account` must show `verification_evidence_ref` non-null and `credit.available` "10000.00000000".
- **Do not** issue its key, and do not log in to the App as test1 or test2 during §4/§4a: the App console issues keys (apps/app/app/actions.ts:42).
- Keep `test2.password` (0600) only if an App login is wanted later; otherwise `shred -u`.

### H5: P-24 funding (+40,000 CREDIT to the certify tenant)

```bash
U1=<user_id from the statement>
CLI account --user "$U1" > "$L/h5-account-before.json"
CLI adjust --user "$U1" --amount 40000 --idempotency-key adj-e4c-20260925 --reason "E4C test allocation" | tee "$L/h5-adjust.json"
CLI account --user "$U1" > "$L/h5-account-after.json"
```

- Expected: `{"amount": "40000.00000000", "entry_id": …, "replayed": false, "wallet_id": "8404630c-…"}`, exit 0.
- Available after = before + 40000.00000000, so ~49,999.9 rather than exactly 50,000 (the runbook's "50,000" at :74 ignores the W12 debits).
- `replayed: true` means it had already run: record it, do not re-fund.

### H6: the certify key inventory (after H3 and H4; nothing issued until §4 ends)

```bash
bash <scratch>/certify-prep/h6.sh "$L" h6          # EXPECT defaults to 142c7d81
```

- Expected: `dry-run exit 0`, `blockers [] drift []`, and these active lines:
  - `142c7d81 consumer org 15e766d0`
  - `d554db80 operator org a349a382`
- Then an `INVENTORY {"active_key_id_prefixes": ["142c7d81"], "taken_at": "<as_of>", "source": "G8 credit-transition --dry-run sha256:<sha>"}` line and the `KEYS_TAKEN_AT=… KEYS_SOURCE_SHA256=… ACTIVE_PREFIXES=142c7d81` args for 77.
- **Which prefixes remain:**
  - Consumer (spending) keys only, which today means exactly `142c7d81`.
  - The **operator key d554db80 is excluded and stays active**. H5 and §5.0's `issue-key` need it. An operator credential cannot spend a wallet (contracts/v2/ports.py:94, "an operator credential spends nothing"). The P-24 rule is scoped to consumer keys ("Every active consumer prefix must appear in test_key_ids", 2026-09-25-inputs-decisions-draft.md:408). The runbook's example stops are all consumer keys (a pre-cutover key, an early tenant-2 key, a canary key; E4C-runbook.md:75).
  - Read literally, E4C-runbook.md:75 ("every prefix … whose revoked_at is null … must be exactly 142c7d81") would force revoking the operator key. That is defect 1 below; record the exclusion in the H6 record.
  - The revoked keys b5b74b8e, 04af08ec, 0fdbb31f and 77178e29 must show `revoked_at` set.
- The inventory uses `key_id[:8]`. The `keys` block's `prefix` field is the secret's first 17 characters (`sk-infrx-` + 8; service.py:44-46) and never goes into the inventory.
- Resulting box file `/opt/dlami/nvme/e4b/e4c/keys-certify.json`: `{"active_key_id_prefixes": ["142c7d81"], "taken_at": "<as_of>", "source": "G8 credit-transition --dry-run sha256:<sha>"}`.
- **Stop** (h6.sh exits 1 with `STOP:`) on any other active consumer or provider_dev key: the operator revokes it, then H6 is retaken.

## 4. The ordered commands (A1–A7)

All box steps run as `infra/rollout/ssm.sh <step> [NAME=VALUE …]` from the **tip checkout**. ssm.sh ships the local step text, so e1b-window.sh carries the tip's WC-2 c = 1 half (checklist OPEN 26). The box-side code it runs (bench, dataset, runprofile, the profiles) is identical between RELEASE and the tip (`git diff d3a99e01 HEAD` is empty for those paths). Record the launcher's sha256 in freeze.json. `R=d3a99e01869b6b9c85173e25888abbbf9ed4dd29`.

| # | Step | Command | Expected | Duration (est.) | Stop when |
|---|---|---|---|---|---|
| A0 | Open the side window | Log `MW=e4c-side-d3a99e01-<UTC>` as the lock/window id. Confirm "Allow new users to sign up" is still OFF and that no one uses the App console (key issuance) until A5 ends | — | 2 min | signup ON with real users (then H6 cannot hold) |
| A1 + prep | Edge check, w3-checkout, certify image, inventory | `ssm.sh steps/76-e4c-prepare.sh RELEASE=$R` | `A1 edge /health from the box: 200`; `w3-checkout at $R (clean)`; `certify image … sha256:…`; `inventory <sha> N lines`; `image_equals_pin=yes`; `args=[…"--max-num-seqs","8"…]` | 3–8 min | exit 2 (edge not 200, dirty checkout, bundle sha mismatch); `image_equals_pin=no` |
| A2 | Observe O3 | `ssm.sh infra/rollout/steps/71-pool-budget.sh` | exit 0, `PASS session` | 1 min | exit 1 (the pooler can be exhausted) |
| A2 | O4–O6 | **SKIPPED: alerts skipped by the user's decision (the SNS subscription is unconfirmed).** Record it in ops.md. If the monitor gauges are wanted anyway, run O4 without `P24_APPROVED`: `ssm.sh infra/rollout/steps/72-observe-install.sh RELEASE=$R CANARY_VIDEO=/opt/dlami/nvme/w3-corpus/sop-synth-v1/sop00-8s-640x360.mp4 CANARY_KEY_PARAM=<future canary param name> ALERT_SNS_TOPIC_ARN=arn:aws:sns:us-east-1:641134885443:infrx-pilot-alerts ALERT_OWNER=<who> ALERT_ESCALATION=<how>`, then `73-observe-status.sh` | `canary: BLOCKED (P-24) …`, timers listed; first cycle exit 0 or 3. A nonexistent `CANARY_KEY_PARAM` writes an **empty** `INFRX_CANARY_KEY` without failing (defect 4) | 3 min | — |
| A3 | H4 → H5 → H6 | §3 above (host) | as in §3 | 10 min | the H6 `STOP:` line; org mismatch |
| A3 | Fill, place, validate | `ssm.sh steps/77-e4c-profiles.sh RELEASE=$R MIGRATION_VERSION=0026 MAINTENANCE_WINDOW=$MW KEYS_TAKEN_AT=<…> KEYS_SOURCE_SHA256=<…> ACTIVE_PREFIXES=142c7d81` | Seven `base`/`filled` sha lines; per-field lines matching §1; five `runnable True` (E1B-direct with the expected block and warning); `certify --hashes` JSON whose `engine_options_digest` is `sha256:3c4bbfac…6147` and which equals RELEASE-d3a99e01.md except `git` | 1–2 min | `refused:` (any read disagrees); any `runnable False`. Never edit a filled copy (R133) |
| A3 | freeze.json | Record §2's identities, 76's inventory sha, 77's output (the sha lines are profiles.sha256), the release-bundle SHA256SUMS, the SSM ids, and the launcher and step sha256 values | — | 10 min | — |
| A4 | WC-0 start | `ssm.sh steps/wc0-scrape.sh ACTION=start` | `dir=/opt/dlami/nvme/e1b-logs/wc0-<utc> pids=… lines>0` | 1 min | lines=0 |
| A4 | §4 certify | `ssm.sh infra/rollout/e4c-certify.sh RELEASE=$R` | Returns after ~35 s: `out=/opt/dlami/nvme/e4b/<UTC>` plus the first `[ok …]` lines. The pre-checks refuse with exit 2 (key.env names exactly `INFRX_API_KEY`, no `MARLIN_API_KEY` in the env file, the three e4c files, `pg_journal_url` readable) | run 4.5–6 h (the soak is 14,400 s) | exit 2 at launch |
| A4 | Poll | `ssm.sh infra/rollout/steps/78-e4b-report.sh RUN=<that UTC> ONLY=report.json`. **Always pass RUN**: without it, 78 picks `e4c` (defect 3) | Cells `e4b.a.dataset-resume`, `e4b.b.envelope` (supported 0.5 only), `e4b.b.soak` (0.25 × 14,400), `e4b.b.overload` (edge P4: honest 429 + Retry-After) PASS; `target.max_video_seconds == 82.0`; the certify.log tail `exit 0` | every 15–30 min | any FAIL follows the 05 §7 fix loop (a rerun is a new run; criteria unchanged); overload PENDING on PROFILE |
| A4 | Fetch | A copy of session-03-tools/rollout/e4b-fetch3.sh with `O=/opt/dlami/nvme/e4b/<UTC>` (it hardcodes run3's dir at :4) → S3 `w4/e4b-box/<UTC>.tgz` → `models/marlin2b/results/E4C-box-d3a99e0/run1-<UTC>/` | uploaded line | 5 min | — |
| A4 | Closing inventory | `EXPECT=142c7d81 bash h6.sh "$L" h6-after-s4` | `OK: … ['142c7d81']` | 1 min | a new spending key whose created_at falls inside §4: the cells after it are INVALID (bench checks the file only at each cell's start) |
| A5 | Budget | `CLI account --user "$U1"` | `credit.available` ≥ 8,096.5632 (E1B §7.2) | 1 min | below that figure |
| A5 | WC-1…5, WC-8 | `TIMEOUT_S=7200 ssm.sh infra/rollout/e1b-window.sh RELEASE=$R` | `out=/opt/dlami/nvme/e4b/e1b-<utc>`, 13 `plan …` lines (10 bench cells + WC-8 run/resume/export), `cells.tsv` all `exit=0`, `WC-8 SIGINT after 3 done`, `done out=…` | 54–77 min of cells (+ transitions) | `refused before …` (engine not at max_num_seqs 8, a certify container live, a leftover container) |
| A5 | WC-6a | `ssm.sh models/marlin2b/e1b/l8ref.sh EUTC=<stamp>` | `restored=yes ready_s=<n>` and the `drill=wc6a-declared-engine-stop … ssm=FILL` line (FILL = this SSM id → drills.md). **Declared engine outage on the live edge** | 5–7 min | `restored=no`: the engine is down; restart.md § Engine, and the window stops |
| A5 | WC-7 (immediately after, no request in between) | `ssm.sh infra/rollout/e1b-window.sh RELEASE=$R CELLS=WC-7` | `WC-7 cold exit=0` | 5–6 min | a refusal |
| A5 | WC-6b | `ssm.sh models/marlin2b/e1b/l8served.sh EUTC=<same stamp>`, then compare: `printf 'R=/opt/dlami/nvme/w3-checkout; python3 $R/research/plan/evidence/w/box/box-lane/l8compare.py /opt/dlami/nvme/e1b-logs/L8-%s $R/models/marlin2b/measure\n' <stamp> > $L/wc6-compare.sh; ssm.sh $L/wc6-compare.sh` | 22 `clip=… budget=… exit=0` lines; a per-clip events comparison | 3–5 min | — |
| A5 | Closing inventory | `EXPECT=142c7d81 bash h6.sh "$L" h6-after-s4a` | OK | 1 min | as above |
| A6 | §5.0.1 tenant-2 key [operator-held] | `CLI issue-key --user "$U2" --name e4c-tenant2 --secret-file "$L/tenant2.key" --idempotency-key key-tenant2-20260925 --reason "E4C second test tenant"` | JSON `{key_id, org_id, prefix, replayed: false, secret_file}`. **`TENANT2_PREFIX` = `key_id[:8]`**, not the `prefix` field (defect 1) | 1 min | — |
| A6 | §5.0.2 fill both two-tenant profiles (host) | `git archive d3a99e01 models/marlin2b \| tar -x -C $L/rel` then `RELEASE=$R DEPLOYED_SHA=$R IMAGE_DIGEST=sha256:faa5b681… CONFIG_VERSION=<77's value> MIGRATION_VERSION=0026 MAINTENANCE_WINDOW=$MW TENANT2_PREFIX=<id8> python3 <scratch>/certify-prep/fill.py $L/rel ~/e4c E4C-two-tenant.json=E4C-box.two-tenant.base.json E1B-two-tenant.json=E1B-edge.two-tenant.base.json` | base/filled sha lines | 1 min | `refused:` |
| A6 | §5.0.3 journey inventory | `EXPECT=142c7d81,<id8> bash h6.sh "$L" h6-journey`, then write `~/e4c/keys-journey.json` from its `INVENTORY` line | OK | 1 min | `STOP:` |
| A6 | §5.0.4 `MEDIA_BASE_URL` | **BLOCKED**: unnamed (runbook :240-244) | — | — | — |
| A6 | §5.1 legs | SSE journey and replay: **BLOCKED** (the frozen journey forms include `video_url`, and changing them is a new profile version, R133). Runnable: sync block (runbook :284-296), async `INFRX_TEST_KEY=<each key> VIDEO_FILE=<in-cap clip> infra/rollout/verify-journey.sh` + `drift.py --request-id`, foreign-call block (:300-318), discovery `curl -s https://marlin2b.callbill.ai/v1/models` | `failures: 0` per tenant; sync 200 then replay (`Idempotency-Replayed: true`); foreign 404 `not_found` ×3 per tenant; owner job `succeeded` | 20–30 min | any FAIL |
| A6 | WC-9 | E1B-protocol §7.2's WC-9 line with `--profile ~/e4c/E1B-two-tenant.json --key-inventory ~/e4c/keys-journey.json --out ~/e4c/e1b-2t.jsonl --raw ~/e4c/e1b-2t-raw.jsonl`, first with `--validate-only`. Both keys `read -rs`; `MARLIN_API_KEY` unset | validate `runnable: true`, projected 1824.7680; then per-tenant accepted counts | 5–7 min | refusal |
| A6 | WC-0 stop | `ssm.sh steps/wc0-scrape.sh ACTION=stop` | listing of `wc0-*` | 1 min | — |
| A6 | Canary re-enable | **BLOCKED (P-24)**: no canary tenant, key, SSM name or spend approval. It stays off | — | — | — |
| A7 | §6 drills | Engine restart (restart.md § Engine; ≤ 300 s), worker SIGKILL (`systemctl kill -s KILL infrx-worker.service` then start; ≤ 30 s), Valkey index loss (index-loss.md; ≤ 30 s, only 503 `dependency_unavailable`; note the Q3 rebuild is PENDING there), DB/object-store stall (E3C s08; no box form exists), restore (restore.md hosted A-steps: use the W6 block, not bare A1–A6), known-good rollback (rollback.md steps 1-7 + `known-good.py --bundles` + `85-known-good-box.sh`). Each drill is one `drill=` line (runbook §6 format). **These are outages or a release swap on the live platform: confirm the user's go first** | per-drill PASS within the bound; correctness PASS | 1–3 h | a FAIL beyond the bound |
| A7 | Reconcile | `apps/infrx-api/.venv/bin/python infra/runbooks/drift.py --hours <window h>` after each drill and at the end | `wallet drift rows 0`, `credit-wallet drift rows 0` | 1 min each | any drift row |
| A7 | Spend | `CLI account --user "$U1"` before and after each cell (ledger debits) | ≤ 50,000 per cell; ~≤ 9,200 per certify run (est.) | — | — |
| A7 | Cleanup | `ssm.sh infra/rollout/steps/86-cleanup.sh` (dry run), log it, then `ssm.sh infra/rollout/steps/86-cleanup.sh DRY_RUN=0` | `would remove …` / `removed …` (allowlisted only) | 2 min | a path outside the allowlist |

After the window: `shred -u "$L/tenant2.key"` once the journey is over (or revoke the key). Revoke the tenant-2 key before any certify rerun, then retake H6 (runbook :199-200).

Total, est.: about 8–12 h, dominated by the 4 h soak, the 1.0–1.6 h of E1B cells and the drills.

## 5. What stays BLOCKED, and which P-17 checks it makes false

| Item | Blocked on | Effect |
|---|---|---|
| SSE journey + replay legs (§5.1) | `MEDIA_BASE_URL` unnamed (15-pending-inputs; runbook :240-244) | **P-17 check 5 false**, and so **check 1 false** (E4C test_id BACKEND-JOURNEY not PASS) |
| O6 alert delivery (`74-alert-test.sh`, the owner confirms the nonce) | SNS subscription unconfirmed; alerts skipped by the user's decision | **P-17 check 7 false** (it needs "74-alert-test.sh delivered to the P-25 destination", 03-operations-and-verification.md:106), and so **check 1 false** (OPS-CONTINUOUS) |
| Canary re-enable after WC-9 (§1 step 5; O4 with `P24_APPROVED`, O5 `infrx_canary_up`) | P-24: no canary tenant, key or `CANARY_KEY_PARAM`, no spend approval (288 req/day) | Runbook §1 step 5 and the ops.md record stay NOT RUN (part of OPS-CONTINUOUS). Leaving it off is correct for §4 and §4a |
| Restore / fresh-instance restore, MIRROR_URL (O9–O11) | P-25: the durable image store, prefix approval, PITR token read | Check 7's restore half stays open until run |
| WC-5's `video_url` form | `MEDIA_BASE_URL` | WC-5 runs upload+inline only (already its committed form) |

Checks this window **can** make true: 2 (freeze.json + profiles.sha256), 3 (CREDIT, the P-01 card, the P-02 dry-run exit 0: already recorded), 4 (if the envelope and soak PASS), 6 (drift 0 within the cap) and 10 (the cutover is recorded in the RELEASE record). **BACKEND-READY cannot be accepted from this window**: it stays PENDING, naming checks 1, 5 and 7.

## 6. Repository defects found (none edited)

1. **E4C-runbook.md:75 (H6) and :232-233 (§5.0 step 1): "prefix" is ambiguous in two ways.**
   - (a) The dry-run `keys` block's `prefix` and issue-key's `prefix` are the **secret's** first 17 characters (`sk-infrx-` + 8; service.py:44-46,275; transition.py:249). The inventory and `test_key_ids` need `key_id[:8]` (schema `^[0-9a-f]{8,12}$`). Copying the `prefix` field fails validation, and it puts secret-derived characters into evidence.
   - (b) The block lists every audience, so "every prefix whose revoked_at is null must be exactly 142c7d81" always stops on the operator key d554db80, which H5 and §5.0 still need. Operator credentials cannot spend (contracts/v2/ports.py:94), and the decision (inputs-draft :408) scopes the rule to consumer keys.
   - Fix: "key_id[:8] of each key with `revoked_at` null and audience ≠ operator". h6.sh implements this.
2. **No step moves `/opt/dlami/nvme/w3-checkout` to RELEASE** (40-checkout.sh:10 moves only `/home/ubuntu/model-inference`). Yet e4c-certify.sh:46, e1b-window.sh:17, l8ref.sh:14 and l8served.sh:8 run from it, and runbook §2 requires `source_sha` from it. The **certify image build** also exists only as prose (E4B-9aa7ffe.md:430), although e4c-certify.sh:20 requires it. The **certify `--inventory` refresh** after W10 is in no step either (runbook 0.2 names inventory.sh, not `/opt/dlami/nvme/e4b/inventory.txt`). 76-e4c-prepare.sh covers all three.
3. **infra/rollout/steps/78-e4b-report.sh:13** picks the default run by lexical `sort | tail -n 1` over every directory in `/opt/dlami/nvme/e4b`. Once §3 creates `e4c/` (and §4a creates `e1b-*`), the default is `e4c`, not the certify run. Workaround: always pass `RUN=<UTC>`. Fix: match `-regex '.*/[0-9]{8}T[0-9]{6}Z'`.
4. **infra/rollout/steps/72-observe-install.sh:49**: `write_env` runs `printf … "$(param …)"`, and `set -e` does not see a failing command substitution in an argument (verified locally). An unreadable or nonexistent SSM name writes `NAME=` empty and prints `wrote …`. Today's harm: O4 "succeeds" with a nonexistent `CANARY_KEY_PARAM`, and a later `P24_APPROVED` run sends the canary with an empty key. The same applies to `MONITOR_DSN_PARAM` and `ALERT_WEBHOOK_PARAM`.
5. **WC-0 has no launcher.** E1B-protocol §7.2 gives only a command sketch; E4C-runbook.md:217 says it "starts with §4", and no script starts or stops it (see steps/wc0-scrape.sh).
6. **session-03-tools/rollout/e4b-fetch3.sh:4** hardcodes run3's directory. E4C-runbook §7 says "as fetched by e4b-fetch3.sh", so a copy with the new `O=` is needed.
7. **E4C-runbook.md:48-49 isolation gap after go-live.** Safety "comes from the key inventory". But the live App issues consumer keys (apps/app/app/actions.ts:42) to test1 or any signup, and bench and certify read `keys-certify.json` only at each cell's start. A key created mid-soak is invisible. Proposed: a closing H6 after §4 and after §4a (added to A4 and A5 above).
8. Minor:
   - E4C-runbook.md:74 says "holds … 50,000" after H5; it will be 50,000 less the W12 smoke debits.
   - E4C-runbook §6 names the worker drill `docker kill --signal=KILL infrx-worker`, but restart.md § Worker says `systemctl kill -s KILL infrx-worker.service`.
   - The checklist's claim that "72 requires [the CANARY_KEY_PARAM parameter]" is true of the name only (see 4).
