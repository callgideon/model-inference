"""E4C-runbook §3 and §5.0: fill the committed run-profile bases - FILL values only, nothing else.
    python3 infra/rollout/certify-fill.py <checkout> <dest dir> NAME=BASE [NAME=BASE ...]
77-e4c-profiles.sh carries this file verbatim (its heredoc; tests/i/test_rollout.py pins that).
Values come from the environment (the §2 reads): RELEASE, DEPLOYED_SHA, IMAGE_DIGEST,
CONFIG_VERSION, MIGRATION_VERSION, MAINTENANCE_WINDOW, and TENANT2_PREFIX for a two-tenant
base. weights_sha256 / processor_sha256 are computed from <checkout>'s serving-version.json
(R148 formula, runbook §2), and the base's tokenizer/template digests are checked against it.
Prints one line per filled field and the sha256 of every base and filled copy. Exit 2 on any
mismatch, before a file is written."""
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
