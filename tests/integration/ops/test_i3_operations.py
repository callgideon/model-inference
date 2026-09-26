"""I3: the App operations runbook is executable where it can be, and stays that way. Layer 1.

* the cutover prerequisites derived from the tree: migration numbering contiguous, the
  rollback tool accepting the tree at its own newest;
* every cutover step names its check and its abort; every combined check names its pass;
* the App alert rule names a section of operations.md that exists, merges with the backend
  rule sets without a clash (WR-I3-3 composed) and fires on its metric;
* the canary's App probe (WR-I3-2 composed) writes 1 only for a production identity;
* the scenario register: every `offline` row names tests that exist, every `NOT RUN` row a
  reason; links and anchors resolve; bash blocks parse; a verification log is kept.
Mirrors tests/integration/backend/recovery/test_runbooks.py's discipline.
"""
from __future__ import annotations

import http.server
import json
import re
import runpy
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
OPS = REPO / "infra" / "app" / "operations.md"
ALERTS = REPO / "infra" / "alerts"
OBSERVE = REPO / "infra" / "observe"
MIGRATIONS = REPO / "apps" / "app" / "supabase" / "migrations"
sys.path.insert(0, str(REPO / "apps" / "infrx-api"))

from infrx.observe import alerts as evaluator            # noqa: E402

TEXT = OPS.read_text()

# WR-I3-3, exactly as handed to I8's owner: infra/observe/rules.py merges app.json when present.
RULES_OLD = '''    for rule in ops["rules"]:
        if rule["name"] in rules:
            raise SystemExit(f"rule defined twice: {rule['name']}")
        rules[rule["name"]] = rule
    return {"version": f"a{alerts['version']}+o{ops['version']}", "rules": list(rules.values())}
'''
RULES_NEW = '''    # I3 (WR-I3-3): the App's rules, when present; same shape, same duplicate refusal.
    app = json.loads((directory / "app.json").read_text()) if (directory / "app.json").is_file() else None
    for rule in ops["rules"] + (app["rules"] if app else []):
        if rule["name"] in rules:
            raise SystemExit(f"rule defined twice: {rule['name']}")
        rules[rule["name"]] = rule
    version = f"a{alerts['version']}+o{ops['version']}" + (f"+p{app['version']}" if app else "")
    return {"version": version, "rules": list(rules.values())}
'''
# WR-I3-2, exactly as handed to I8's owner: inserted after this line of infra/observe/canary.sh.
CANARY_AFTER = 'publish() { chmod 0644 "$prom"; mv -f "$prom" "$OUT"; }\n'
CANARY_PROBE = '''
# I3 (WR-I3-2): the App's public release identity, anonymous (WR-I2A-1): no key, no body.
# 1 only for a 200 naming production and a full commit. Before the key check: needs no key.
APP=${APP:-https://app.callbill.ai}
app_up=0
if curl -sf --max-time 15 -o "$work/app" "$APP/api/version" \\
   && grep -q '"environment":"production"' "$work/app" \\
   && grep -Eq '"commit":"[0-9a-f]{40}"' "$work/app"; then app_up=1; fi
m infrx_app_up "$app_up"
echo "canary app up=$app_up"
'''
# WR-I3-5, exactly as handed to I2A's owner: infra/app/README.md section 5 states rollback.py's rule.
README_OLD = """  schema: its tree's newest migration ≤ the hosted applied migration (migrations are additive).
"""
README_NEW = """  schema: its tree's newest migration = the hosted applied migration, or below it only with a
  recorded schema proof (`infra/app/rollback.py --schema-proof`; not every migration is
  additive, [operations.md](operations.md#app-rollback)).
"""


def anchors(path: Path) -> set[str]:
    """GitHub's heading slugs, as test_runbooks.py computes them."""
    return {re.sub(r"[^a-z0-9 -]", "", line.lstrip("#").strip().lower()).replace(" ", "-")
            for line in path.read_text().splitlines() if re.match(r"#{1,6} ", line)}


def section(title: str) -> str:
    return re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## )", TEXT, re.S | re.M).group(1)


def rows(text: str, first: str) -> list[list[str]]:
    return [[c.strip() for c in line.strip().strip("|").split("|")]
            for line in text.splitlines() if re.match(rf"\| {first}\d+ \|", line)]


def merged_rules(tmp_path: Path) -> dict:
    """rules.py with WR-I3-3 (as is once applied), over the real alert files."""
    source = (OBSERVE / "rules.py").read_text()
    if "app.json" not in source:
        assert source.count(RULES_OLD) == 1, "rules.py moved: refresh WR-I3-3's hunk"
        source = source.replace(RULES_OLD, RULES_NEW)
    patched = tmp_path / "rules.py"
    patched.write_text(source)
    return runpy.run_path(str(patched))["merge"](ALERTS)


def test_i3_ops01_links_anchors_bash_and_log():
    """Catches: a runbook step pointing at a section or file that moved, a step block that
    cannot run, a runbook without its log."""
    assert "## Verification log" in TEXT
    for target, anchor in re.findall(r"\]\(([\w./-]+\.(?:md|py|json))(?:#([\w-]+))?\)", TEXT):
        path = (OPS.parent / target).resolve()
        assert path.is_file(), target
        if anchor:
            assert anchor in anchors(path), (target, anchor)
    for anchor in re.findall(r"\]\(#([\w-]+)\)", TEXT):
        assert anchor in anchors(OPS), anchor
    blocks = re.findall(r"```bash\n(.*?)```", TEXT, re.S)
    assert blocks
    for block in blocks:
        done = subprocess.run(["bash", "-n"], input=block, capture_output=True, text=True)
        assert done.returncode == 0, (done.stderr, block)
    readme = (REPO / "infra" / "app" / "README.md").read_text()
    assert "operations.md" in readme, "infra/app/README.md does not link the operations runbook"
    assert "../app/operations.md" in (REPO / "infra" / "runbooks" / "README.md").read_text()


def test_i3_ops02_migration_numbering_is_contiguous_and_the_tool_accepts_the_tree():
    """Catches (cutover X1): a gap or a duplicate number, which makes 'applied through NNNN'
    ambiguous for the operator and the tool."""
    numbers = [p.name[:4] for p in sorted(MIGRATIONS.glob("*.sql"))]
    assert all(re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", p.name) for p in MIGRATIONS.glob("*.sql"))
    assert numbers == [f"{i:04d}" for i in range(1, len(numbers) + 1)], numbers
    done = subprocess.run([sys.executable, str(REPO / "infra" / "app" / "rollback.py"), "HEAD",
                           "--applied", numbers[-1], "--gateway", "HEAD"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout
    assert json.loads(done.stdout)["migrations"] == numbers


def test_i3_ops03_every_cutover_step_names_its_check_and_abort():
    """Catches: an operator step with no way back (the abort column empty or a dash), a
    renumbered or missing step, a combined check without its pass condition."""
    steps = rows(section("Auth and credit cutover"), "X")
    assert [s[0] for s in steps] == [f"X{i}" for i in range(10)], [s[0] for s in steps]
    for number, step, check, abort in steps:
        assert "[OP]" in step or "Offline" in step, number
        assert check and check not in ("-", "—"), number
        assert abort and abort not in ("-", "—"), f"{number} has no abort/rollback"
    # README section 1 item 2: the App's hosted inputs (section 3 variables, section 4 P-05
    # settings) are set before the App release is deployed (review 1-I3R-2).
    deploy = next(i for i, s in enumerate(steps) if "Deploy the App release" in s[1])
    for i, (number, step, _check, _abort) in enumerate(steps):
        if "P-05" in step or "App variables" in step:
            assert i < deploy, f"{number} sets a hosted App input after the deploy ({steps[deploy][0]})"
    checks = rows(section("Combined checks"), "C")
    assert [c[0] for c in checks] == ["C1", "C2", "C3", "C4", "C5"]
    assert all(len(c) == 3 and c[2] for c in checks)
    rollback = section("Auth and credit cutover").split("### Cutover rollback", 1)[1]
    assert "Not rolled back" in rollback and "money" in rollback and "users" in rollback


def test_i3_ops04_the_app_rule_names_a_section_merges_and_fires(tmp_path):
    """Catches: AppDown linking a missing section, clashing with a backend rule name, a shape
    the evaluator cannot read, or not firing on a down App (WR-I3-3 composed)."""
    app = json.loads((ALERTS / "app.json").read_text())
    shape = set().union(*(r.keys() for r in json.loads((ALERTS / "operations.json").read_text())["rules"]))
    for rule in app["rules"]:
        assert set(rule) <= shape, set(rule) - shape
        assert rule["severity"] in ("page", "ticket") and rule["op"] in evaluator.OPS
        assert rule["threshold_status"].startswith(("exact", "derived")) or "TO BE VERIFIED" in rule["threshold_status"]
        document, _, anchor = rule["runbook"].partition("#")
        assert document == "infra/app/operations.md" and anchor in anchors(OPS), rule["name"]
    merged = merged_rules(tmp_path)
    names = [r["name"] for r in merged["rules"]]
    assert len(names) == len(set(names)) and "AppDown" in names
    assert merged["version"].endswith(f"+p{app['version']}")
    down = evaluator.evaluate(merged["rules"], evaluator.parse('infrx_app_up{process="canary"} 0\n'))
    assert [a["alert"] for a in down] == ["AppDown"]
    assert evaluator.evaluate(merged["rules"], evaluator.parse('infrx_app_up{process="canary"} 1\n')) == []
    # a duplicate App rule name is refused by the same merge
    clash = tmp_path / "clash"
    clash.mkdir()
    for name in ("alerts.json", "operations.json"):
        (clash / name).write_text((ALERTS / name).read_text())
    (clash / "app.json").write_text(json.dumps({**app, "rules": app["rules"] + [{**app["rules"][0], "name": "ComponentDown"}]}))
    source = (tmp_path / "rules.py")
    with pytest.raises(SystemExit, match="defined twice"):
        runpy.run_path(str(source))["merge"](clash)
    # the pending producer is exactly what WR-I3-2 adds, and has not silently landed
    canary = (OBSERVE / "canary.sh").read_text()
    for metric in {r["metric"] for r in app["rules"]}:
        assert metric in app.get("pending_producers", {}) or metric in canary, metric
    for metric in app.get("pending_producers", {}):
        assert metric not in canary, f"{metric} landed in canary.sh: drop it from app.json pending_producers"


class _App(http.server.BaseHTTPRequestHandler):
    status, body = 200, ""

    def do_GET(self):                                   # noqa: N802 - http.server's name
        self.send_response(_App.status if self.path == "/api/version" else 404)
        self.end_headers()
        self.wfile.write(_App.body.encode())

    def log_message(self, *args):
        pass


@pytest.fixture
def app_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.mark.parametrize("status, body, up", [
    (200, json.dumps({"commit": "a" * 40, "builtAt": "unknown", "deployment": "dpl_X",
                      "environment": "production", "apiOrigin": "https://api.example.test"},
                     separators=(",", ":")), 1),
    (200, json.dumps({"commit": "unknown", "environment": "production"}, separators=(",", ":")), 0),
    (200, json.dumps({"commit": "a" * 40, "environment": "preview"}, separators=(",", ":")), 0),
    (500, "", 0),
])
def test_i3_ops05_the_canary_app_probe_writes_up_only_for_a_production_identity(
        tmp_path, app_server, status, body, up):
    """Catches (WR-I3-2 composed): an App probe that reads a refused, unidentified or
    mis-scoped App as up, or that needs the canary key (it runs with none here)."""
    source = (OBSERVE / "canary.sh").read_text()
    if "infrx_app_up" not in source:
        assert source.count(CANARY_AFTER) == 1, "canary.sh moved: refresh WR-I3-2's hunk"
        source = source.replace(CANARY_AFTER, CANARY_AFTER + CANARY_PROBE)
    _App.status, _App.body = status, body
    out = tmp_path / "canary.prom"
    done = subprocess.run(["bash", "-c", source], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "OUT": str(out), "APP": app_server})
    assert done.returncode == 3 and "BLOCKED" in done.stderr     # no key: the canary itself stops
    samples = evaluator.parse(out.read_text())
    assert samples[("infrx_app_up", (("process", "canary"),))] == up, (done.stdout, done.stderr)


def test_i3_ops06_the_scenario_register_names_real_tests_or_a_reason():
    """Catches: a register row claiming an offline check nobody runs, or a NOT RUN row with
    no reason (OPS-RECOVER's App half must be honest about what ran)."""
    register = rows(section("Scenario register"), "OPS-APP-")
    assert len(register) >= 10
    sources = "".join(p.read_text() for p in (REPO / "tests" / "integration" / "ops").glob("test_*.py"))
    sources += "".join(p.read_text() for p in (REPO / "apps" / "app" / "tests" / "i3").glob("*.test.ts"))
    for number, _scenario, status in register:
        if status.startswith("offline:"):
            for test_id in re.split(r",\s*", status.removeprefix("offline:").strip()):
                assert re.search(rf"(def {test_id}_|\"{test_id} )", sources), (number, test_id)
        else:
            assert re.match(r"NOT RUN: \S.{10,}", status), (number, status)
    assert "DUR-OUTBOX" in section("Scenario register")
    assert (REPO / "research/plan/evidence/i/I3B-32f94b3.md").is_file()


def test_i3_ops07_the_release_runbook_states_the_same_migration_rule():
    """Catches (review 1-I3R-4; WR-I3-5 composed): infra/app/README.md section 5 and this
    runbook disagreeing on a hosted schema ahead of the App target (README: assumed additive;
    rollback.py and operations.md: proven, as infra/rollout/known-good.py)."""
    readme = (REPO / "infra" / "app" / "README.md").read_text()
    if "--schema-proof" not in readme:
        assert readme.count(README_OLD) == 1, "README section 5 moved: refresh WR-I3-5's hunk"
        readme = readme.replace(README_OLD, README_NEW)
    rule = re.search(r"\*\*Known-good App release\*\*.*?(?=\n\n)", readme, re.S).group(0)
    assert "additive)" not in rule and "--schema-proof" in rule, rule
    anchor = re.search(r"\]\(operations\.md#([\w-]+)\)", rule).group(1)
    assert anchor in anchors(OPS) and "--schema-proof" in section("App rollback")
    assert "--schema-proof" in (REPO / "infra" / "app" / "rollback.py").read_text()
