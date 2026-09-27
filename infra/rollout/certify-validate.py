"""Local, no request: bench --validate-only on every filled profile as the window runs it.
    python3 infra/rollout/certify-validate.py <release tree> <filled dir> <launcher plan file>
<launcher plan file> is `DRY_RUN=1 e1b-window.sh`'s output on a layout whose e4b/e4c holds the filled
copies. Stamped copies go to a fresh temporary directory. Exit 0 only when every cell is runnable
with no error and bench created no --out.
E4C: runbook §3's two lines as written, plus each certify cell stamped as certify.cell_profile
stamps it (envelope 0.5/1.0/2.0 x 135, soak 0.25 x 3600). E1B: every cell of the launcher's
DRY_RUN plan (its stamped copies), and WC-8 through dataset.check_profile (dataset.py has no
--validate-only)."""
import contextlib, hashlib, io, json, os, re, sys, tempfile

tree, filled, planfile = map(os.path.abspath, sys.argv[1:4])
os.chdir(tempfile.mkdtemp(prefix="certify-validate-"))
M = os.path.join(tree, "models", "marlin2b")
sys.path[:0] = [M]
for n in ("INFRX_API_KEY", "MARLIN_API_KEY", "INFRX_API_KEY_B"):
    os.environ.pop(n, None)
import bench, dataset  # noqa: E402

MANIFEST = os.path.join(M, "corpus", "manifest.json")
INV = os.path.join(filled, "keys-certify.json")
rows = []


def run(label, argv):
    before = set(os.listdir("."))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bench.main(argv + ["--validate-only"])
    v = json.loads(out.getvalue())
    created = set(os.listdir(".")) - before
    d = v["derived"]
    rows.append((label, code, v["runnable"], v["errors"], v["blocks"], v["warnings"],
                 d.get("projected_spend"), d.get("spend_currency"), d.get("scheduled_requests"),
                 d.get("profile_sha256"), sorted(created)))


def stamp(src, name, rate, version):          # certify.cell_profile, as written
    p = json.load(open(src))
    p["identity"]["run_id"] = f"{p['identity']['run_id']}-{name}"[:64]
    p["workload"].update(dataset_version=version, seed=20260922,
                         manifest_sha256=hashlib.sha256(open(MANIFEST, "rb").read()).hexdigest(),
                         forms=["video_b64"], max_tokens_mix=[128, 512, 1024])
    p["measurement"].update(arrival="open-loop", rate_per_s=rate)
    if name == "overload":
        p["measurement"]["profile_class"] = "P4"
    path = os.path.join(os.getcwd(), f"stamped-{name}.json")
    json.dump(p, open(path, "w"), indent=2)
    return path


C = ["--corpus", MANIFEST, "--subset", "full", "--target", "gateway", "--model",
     "nemostation/marlin-2b", "--seed", "20260922", "--forms", "video_b64", "--max-tokens",
     "128,512,1024", "--retries", "0", "--key-inventory", INV]
box, edge = os.path.join(filled, "E4C-box.json"), os.path.join(filled, "E4C-edge.json")
run("§3 box line (E4C-box.json, 0.5 x 3600)", C + ["--base-url", "http://127.0.0.1:8001/v1",
    "--rate", "0.5", "--requests", "3600", "--dataset-version", "e4c-1", "--profile", box])
run("§3 edge line (E4C-edge.json, 1000 x 32 burst 32)", C + ["--base-url",
    "https://marlin2b.callbill.ai/v1", "--rate", "1000.0", "--requests", "32", "--burst", "32",
    "--dataset-version", "e4c-1-overload", "--profile", edge])
for rate in (0.5, 1.0, 2.0):
    v = f"e4b-d3a99e0-x-r{rate}"
    run(f"certify envelope-r{rate} (stamped box, 135)", C + ["--base-url",
        "http://127.0.0.1:8001/v1", "--rate", str(rate), "--requests", "135",
        "--dataset-version", v, "--profile", stamp(box, f"envelope-r{rate}", rate, v)])
run("certify soak (stamped box, 0.25 x 3600)", C + ["--base-url", "http://127.0.0.1:8001/v1",
    "--rate", "0.25", "--requests", "3600", "--dataset-version", "e4b-d3a99e0-x-soak",
    "--profile", stamp(box, "soak", 0.25, "e4b-d3a99e0-x-soak")])
run("certify overload (stamped edge P4, 32)", C + ["--base-url",
    "https://marlin2b.callbill.ai/v1", "--rate", "1000.0", "--requests", "32", "--burst", "32",
    "--dataset-version", "e4b-d3a99e0-x-overload",
    "--profile", stamp(edge, "overload", 1000.0, "e4b-d3a99e0-x-overload")])

# E1B: the launcher's DRY_RUN plan (lines "plan <WC> <label> python <script> args...")
out_dir = re.search(r"^out=(\S+)$", open(planfile).read(), re.M).group(1)
box_root = os.path.dirname(os.path.dirname(out_dir))          # <root>/e4b/e1b-<utc>
swap = lambda a: (os.path.join(out_dir, a[5:]) if a.startswith("/out/") else
                  os.path.join(box_root, "e4b", "e4c", a[5:]) if a.startswith("/e4c/") else
                  os.path.join(tree, a) if a.startswith("models/") else a)
for line in open(planfile):
    if not line.startswith("plan "):
        continue
    _, wc, label, *argv = line.split()
    if label.endswith(("-resume", "-export")):
        continue
    if argv[1].endswith("dataset.py"):      # WC-8: media where /out/../corpus is, sized as the manifest says
        synth = json.load(open(os.path.join(M, "corpus-synth", "manifest.json")))
        root = os.path.join(os.getcwd(), "container")
        for c in synth["clips"]:
            os.makedirs(os.path.dirname(os.path.join(root, "corpus", c["file"])), exist_ok=True)
            with open(os.path.join(root, "corpus", c["file"]), "wb") as f:
                f.truncate(c["derived"]["bytes"])
        os.makedirs(os.path.join(root, "out"), exist_ok=True)
        with open(os.path.join(out_dir, "sop-incap.jsonl"), "rb") as s, \
                open(os.path.join(root, "out", "sop-incap.jsonl"), "wb") as d:
            d.write(s.read())
        a = dataset.parse_args([os.path.join(root, x[1:]) if x.startswith("/out/sop-incap")
                                else swap(x) for x in argv[2:]])
        why = dataset.check_profile(a)
        rows.append((f"{wc} {label} (dataset.check_profile)", 0 if why is None else 2,
                     why is None, why or [], [], [], None, "CREDIT", 9, None, []))
        continue
    run(f"{wc} {label}", [swap(x) for x in argv[2:]])

ok = True
for label, code, runnable, errors, blocks, warnings, spend, cur, n, psha, created in rows:
    ok &= runnable and not errors and not created
    print(f"{label}: exit {code} runnable {runnable} errors {errors} blocks {blocks} "
          f"warnings {warnings} requests {n} projected {spend} {cur} out-created {created}")
print("ALL RUNNABLE" if ok else "NOT ALL RUNNABLE")
sys.exit(0 if ok else 1)
