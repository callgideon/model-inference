"""LAB-LOCAL scenarios (the E4-ON regression), over ONE composition with every switch ON
(`lab_world.composition`), run only through runner.py (never collected by `pytest
tests/integration`). A process that refuses to start is a FAIL naming its refusal - a real
finding, recorded and filed, never patched here.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_world as lw                                  # noqa: E402

operate, world, stack = lw.operate, lw.world, lw.stack
A = operate.PROVIDER_A
#: One GET per Lab route family, as the Lab web's adapters call it (provider A).
FAMILIES = {
    "control": f"/lab/v1/control/deployments?provider_org_id={A}",
    "traces": f"/lab/v1/traces?provider_org_id={A}",
    "evals": f"/lab/v1/evaluations/runs?provider_org_id={A}",
    "pipelines": f"/lab/v1/pipelines/training-runs?provider_org_id={A}",
    "teacher-batches": f"/lab/v1/pipelines/teacher-batches?provider_org_id={A}",
    "releases": f"/lab/v1/releases?provider_org_id={A}",
    "optimizations": f"/lab/v1/optimizations?provider_org_id={A}",
    "datasets": f"/lab/v1/providers/{A}/datasets/versions",
}
#: Families the composition documents as a typed 503 until their lane lands (pilot._lab_2:
#: "the ports whose tables are not merged ... answer 503"; the Lab web: no HTTP control
#: adapter). Each stays NOT RUN, naming its lane, until it answers 200.
PENDING_FAMILIES = {
    "evals": "WR-B4-2 (experiments, catalog and B3 ledger ports; composition-5)",
    "pipelines": "WR-LAB2-4 (the run listings)",
    "teacher-batches": "WR-P4B-1 (composition-4, 6bffa2e8, merge batch #36)",
    "releases": "WR-R4-1 lab-sql half / WR-R4-2 (release read models, proposal store)",
    "optimizations": "WR-R4-1 lab-sql half (R3 variants)",
    "control-web": "WR-E3L-J (apps/lab has no HTTP control adapter; lab-app-control lane)",
}
#: The Lab web's page families (apps/lab/app/(provider)/*), each read signed in as A's admin.
PAGES = {"/overview": "control-web", "/models": "control-web", "/deployments": "control-web",
         "/requests": "traces", "/evaluations": "evals", "/evaluations/checkpoints": "evals",
         "/annotations": "pipelines", "/training": "pipelines", "/judge": "judge",
         "/releases": "releases", "/optimizations": "optimizations", "/datasets": "datasets",
         "/settings": "session"}
UNAVAILABLE = '{"refusal":"unavailable"}'   # lw.UNAVAILABLE without the status


def split_pending(wrong: dict[str, str], family_of) -> tuple[dict, dict]:
    """(unexpected, pending): a pending family's typed unavailability is NOT RUN, not FAIL."""
    pending = {k: v for k, v in wrong.items() if family_of(k) in PENDING_FAMILIES
               and (v.startswith("503 " + UNAVAILABLE) or v.startswith("200 "))}
    return {k: v for k, v in wrong.items() if k not in pending}, pending


def not_run_pending(pending: dict) -> None:
    if pending:
        pytest.skip("NOT RUN[" + ",".join(sorted({PENDING_FAMILIES[f] .split(" ")[0]
                                                   for f in pending})) + "] typed "
                    f"unavailable until the lane lands: {sorted(pending)}")

@pytest.fixture(scope="module")
def on(tmp_path_factory):
    import os
    out = os.environ.get("INFRX_LAB_LOCAL_OUT")
    workdir = Path(out) / "cases" / "composition" if out else tmp_path_factory.mktemp("on")
    workdir.mkdir(parents=True, exist_ok=True)
    probe = lambda url: _families(url, operate.session(operate.ADMIN_A), lambda s: s == 200)  # noqa: E731
    with lw.composition(workdir, login_probe=probe) as composed:
        yield composed


def need(on, role: str) -> None:
    if role in on.refused:
        pytest.skip(f"BLOCKED[{role}] it did not start (its own case records the refusal)")


def test_o01_the_gateway_is_ready_with_every_switch_on(on):
    assert "gateway" not in on.refused, f"the gateway refused: {on.refused.get('gateway')}"
    env = on.trip.box.env
    assert all(env.get(name) == "true" for name in lw.GATEWAY_SWITCHES), "premise: every switch ON"
    assert env["DATABASE_URL"].startswith("postgresql://infrx_runtime:"), "premise: runtime login"


def test_o02_the_consumer_worker_is_ready_with_its_switches_on(on):
    assert "worker" not in on.refused, f"the worker refused: {on.refused.get('worker')}"
    assert on.trip.box.env.get("TRACE_PUMPS") == "true", "premise: the trace pumps ON"


def test_o02_the_consumer_workers_lab_seam_refuses_by_name(on):
    """R198: with LAB_EVAL_WORKER ON the consumer worker refuses (exit 2) naming its missing
    sources, so Lab work never runs in a consumer process (the eval role runs it)."""
    assert on.seam != "started", "the consumer worker ran Lab evaluation (R198)"
    assert "exited 2" in on.seam and lw.EVAL_SEAM_REFUSAL in on.seam, on.seam


def _role(on, role: str) -> None:
    name = f"lab-{role}"
    why = on.refused.get(name)
    if why is None:
        return
    pending = lw.PENDING_ROLES.get(role)
    if pending and why.startswith("exited 2:") and pending[1] in why:
        pytest.skip(f"NOT RUN[{pending[0]}] {name} refuses by name until its work source "
                    f"lands (R198/R211): {why[-240:]}")
    raise AssertionError(f"{name} did not start: {why}")


def test_o03_the_eval_role_starts(on):
    _role(on, "eval")

def test_o03_the_checkpoints_role_starts(on):
    _role(on, "checkpoints")

def test_o03_the_judge_role_starts(on):
    _role(on, "judge")

def test_o03_the_annotation_role_starts(on):
    _role(on, "annotation")

def test_o03_the_training_role_starts(on):
    _role(on, "training")

def test_o03_the_rollout_role_starts(on):
    _role(on, "rollout")

def test_o03_the_datasets_role_starts(on):
    _role(on, "datasets")


def test_o04_the_control_factory_serves_a_lab_session(on):
    assert "lab-control" not in on.refused, f"the control factory refused: {on.refused.get('lab-control')}"
    answer = on.control.call("GET", "deployments", operate.session(operate.DEV_A))
    assert answer.status_code == 200, f"{answer.status_code} {answer.text[:300]}"
    refused = on.control.call("GET", "deployments", operate.session(operate.DEV_B))
    assert refused.status_code in (403, 404), "B's developer reads nothing of A's"


def test_o04_the_control_factory_is_ready_on_its_own_login(on):
    """LDP-R4: the factory on 0043's `infrx_lab_control` (the box's L5 login), not the owner,
    answers /readyz off the transaction pooler (LDP-F7 fixed by lab-control-routes: no
    `set role` on the Lab login)."""
    assert "lab-control-login" not in on.refused, \
        f"the control factory on {lw.CONTROL_LOGIN}: {on.refused.get('lab-control-login')}"


def _client(url: str):
    import httpx
    return httpx.Client(base_url=url, timeout=30.0)


def _families(url: str, bearer: str | None, ok) -> dict:
    """Each family's answer that `ok(status)` rejects, by family (errors included)."""
    import httpx
    wrong = {}
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    with _client(url) as http:
        for family, path in FAMILIES.items():
            try:
                answer = http.get(path, headers=headers)
            except httpx.HTTPError as failed:
                wrong[family] = f"{type(failed).__name__}: {failed}"[:200]
                continue
            if not ok(answer.status_code):
                wrong[family] = f"{answer.status_code} {answer.text[:160]}"
    return wrong


def test_o05_every_lab_route_family_answers_a_lab_session(on):
    """On the gateway with EVERY switch ON (the dedicated runtime login ROLLOUT_ROUTING needs)."""
    need(on, "gateway")
    wrong = _families(on.trip.box.url, operate.session(operate.ADMIN_A), lambda s: s == 200)
    assert not wrong, f"route families not serving a Lab session (all switches ON): {wrong}"


def test_o05_the_lab_routes_gateway_serves_every_family(on):
    """ROLLOUT_ROUTING off, owner login, every other switch ON: each family and the signed
    checkpoint receiver (mounted = a refusal of an unsigned post, never 404/405/5xx)."""
    need(on, "lab-gateway")
    import httpx
    found = _families(on.labgw.url, operate.session(operate.ADMIN_A), lambda s: s == 200)
    wrong, pending = split_pending(found, lambda family: family)
    try:
        with _client(on.labgw.url) as http:
            unsigned = http.post("/lab/v1/checkpoints", json={})
        if unsigned.status_code in (404, 405) or unsigned.status_code >= 500:
            wrong["checkpoints"] = f"{unsigned.status_code} {unsigned.text[:160]}"
    except httpx.HTTPError as failed:
        wrong["checkpoints"] = type(failed).__name__
    assert not wrong, f"route families not serving a Lab session: {wrong}"
    not_run_pending({family: PENDING_FAMILIES[family] for family in pending})


def test_o05_the_control_factory_serves_every_family_on_its_own_login(on):
    """WR-LDP-2 / R245 / R251 (WR-LW8-2): the control factory on 0043's `infrx_lab_control`
    (the box's only /lab/v1/* server) answers every family exactly as the same factory on the
    owner login (0056 grants the login its routes' functions: no NOT RUN[SR-LCR-1] any more),
    and a family that is not 200 only as its typed 503 - NOT RUN naming its pending ports -
    never a 401/404/500. Traces is judged apart (its ClickHouse backend is not in this env)."""
    need(on, "lab-control-login")
    need(on, "lab-control")
    if on.login_families is None:
        pytest.skip("BLOCKED[lab-control-login] the factory on its own login was not probed")
    owner = _families(str(on.control.http.base_url), operate.session(operate.ADMIN_A),
                      lambda s: s == 200)
    (on.workdir / "o05-control-families.json").write_text(__import__("json").dumps(
        {"infrx_lab_control": on.login_families, "owner": owner}, indent=1))
    apart = lambda found: {f: v for f, v in found.items() if f != "traces"}   # noqa: E731
    wrong, typed = lw.judge_login(apart(on.login_families), apart(owner), PENDING_FAMILIES)
    assert not wrong, f"families on {lw.CONTROL_LOGIN} not serving as on the owner: {wrong}"
    not_run_pending(typed)


def test_o05_a_consumer_key_is_no_lab_session_on_any_family(on):
    need(on, "lab-gateway")
    served = _families(on.labgw.url, on.trip.world.alpha.secret, lambda s: s in (401, 403))
    assert not served, f"a consumer key was not refused: {served}"


def test_o06_the_consumer_path_serves_and_settles_once(on):
    need(on, "gateway")
    need(on, "worker")
    trip, alpha = on.trip, on.trip.world.alpha
    sync = trip.send(alpha, "sync", world.TEXT, None)
    assert sync.status_code == 200, f"sync {sync.status_code} {sync.text[:300]}"
    accepted = trip.send(alpha, "async", world.TEXT, "lab-local-o06")
    assert accepted.status_code == 202, f"async {accepted.status_code} {accepted.text[:300]}"
    request_id = accepted.json()["request_id"]
    assert world.terminal(trip, request_id, timeout=90.0) == "succeeded"
    assert world.attempts(trip, request_id) == 1
    world.settled_once(trip, request_id)
    trip.conserved(alpha)


def test_o07_the_lab_web_renders_every_page_family_signed_in(on):
    import httpx
    need(on, "lab-gateway")
    with lw.lab_web(on.workdir, on.labgw.url, on.standin.url,
                    str(on.control.http.base_url).rstrip("/")) as web:
        assert web.why is None, f"the Lab web did not start: {web.why}"
        cookie = (f"{lw.session_cookie(operate.ADMIN_A)}; "
                  f"infrx-lab-workspace={A}")
        found = {}
        with httpx.Client(base_url=web.origin, verify=False, timeout=60.0) as http:
            for page in PAGES:
                answer = http.get(page, headers={"Cookie": cookie})
                alert = re.search(r'role="alert"[^>]*>([^<]{0,120})', answer.text)
                if ">Sign out<" not in answer.text:        # the layout's signed-in form
                    found[page] = f"{answer.status_code} not signed in"
                elif answer.status_code != 200 or alert:
                    found[page] = f"{answer.status_code} {alert.group(1) if alert else ''}"
            anonymous = http.get("/datasets")         # the session is what signs them in
        assert ">Sign out<" not in anonymous.text, "signed out, yet the page is signed in"
        (on.workdir / "o07-pages.json").write_text(__import__("json").dumps(found, indent=1))
        wrong, pending = split_pending(found, PAGES.get)
        assert not wrong, f"Lab pages not serving signed in: {wrong}"
        not_run_pending({PAGES[page]: PENDING_FAMILIES[PAGES[page]] for page in pending})
