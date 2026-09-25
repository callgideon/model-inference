"""E3C s01 / s02 (BACKEND-JOURNEY): the headless journey and the two-gateway, two-tenant
drill, on the composed real stack. E3B's journey matrix (test_journey.py) is NOT repeated:
its mode contracts are imported and driven here through what E3B did not cover - an
individual provisioned only through the operator CLI, revocation, and two gateway processes
answering one logical request."""
from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import pilotbox                                         # noqa: E402
import stack                                            # noqa: E402
from test_journey import run_async, run_sse, run_sync   # noqa: E402  (E3B's mode contracts)

FOREIGN = (("GET", "/v1/jobs/{h}"), ("GET", "/v1/jobs/{h}/result"),
           ("GET", "/v1/jobs/{h}/events"), ("DELETE", "/v1/jobs/{h}"))
KEY_CACHE_BOUND_S = 75          # the gateway's key cache (config.Settings.key_ttl = 60) + slack


def foreign_404(trip, http, tenant, handle: str) -> None:
    """Another tenant's handle is `404 not_found` on every owned route (never 403, never 200)."""
    for method, path in FOREIGN:
        answer = http.request(method, path.format(h=handle), headers=trip.headers(tenant))
        assert (answer.status_code, world.code(answer)) == (404, "not_found"), \
            f"cross-tenant {method} {path}: {answer.status_code} {answer.text[:200]}"


def revoked_within(trip, tenant, handle: str, bound_s: float = KEY_CACHE_BOUND_S) -> float:
    """Revoke the key through the CLI; every call with it is 401 within the key-cache bound,
    and a refused inference admits nothing. Seconds it took."""
    status, out = world.cli(trip, "revoke-key", "--org", tenant.org_id, "--key-id",
                            tenant.key_id, "--idempotency-key", f"r-{tenant.name}",
                            "--reason", "e3c revoke")
    assert status == 0, out
    began = time.monotonic()
    try:
        world.wait_for(lambda: trip.http.get(f"/v1/jobs/{handle}", headers=trip.headers(
            tenant)).status_code == 401, bound_s, "a revoked key refused", every=2.0)
    except AssertionError:
        raise AssertionError(f"revocation not observed within {bound_s} s") from None
    jobs = trip.db("select count(*) from infrx.jobs where org_id = %s", tenant.org_id)
    refused = trip.send(tenant, "sync", world.TEXT, "e3c-after-revoke")
    assert (refused.status_code, world.code(refused)) == (401, "invalid_api_key"), refused.text
    assert trip.db("select count(*) from infrx.jobs where org_id = %s", tenant.org_id) == jobs
    return time.monotonic() - began


def test_s01_cli_identity_grant_key_modes_result_revoke(workdir, record_property):
    """Verified identity -> one CLI grant (a second, under another key, replays it) -> a CLI
    key -> text sync, uploaded video async, URL video SSE -> results -> exact settlement per
    job, conserved wallet, other tenants 404 -> CLI revoke -> 401."""
    with world.composed(workdir) as trip:
        gamma = world.individual(trip, "gamma", workdir)
        status, again = world.cli(trip, "grant", "--user", gamma.user_id, "--idempotency-key",
                                  "g-gamma-again", "--reason", "e3c replayed callback")
        assert status == 0 and again["replayed"] is True, again
        assert again["ledger_operation_id"] == gamma.grant["ledger_operation_id"], again
        assert trip.db("select count(*) from infrx.signup_entitlements where user_id = %s",
                       gamma.user_id) == [(1,)], "one entitlement per individual"
        assert trip.db("select count(*) from infrx.credit_ledger where wallet_id = %s and "
                       "kind = 'signup_grant'", gamma.wallet.wallet_id) == [(1,)]
        upload = trip.upload(gamma, pilotbox.clip())
        cells = (run_sync(trip, gamma, world.TEXT, "e3c-s01-text-sync"),
                 run_async(trip, gamma, world.video(upload), "e3c-s01-upload-async"),
                 run_sse(trip, gamma, world.video_url("s01"), "e3c-s01-url-sse"))
        for request_id, handle, _usage in cells:
            world.settled_once(trip, request_id)
            foreign_404(trip, trip.http, trip.world.alpha, handle)
        trip.conserved(gamma)
        took = revoked_within(trip, gamma, cells[0][1])
        record_property("revocation_s", round(took, 1))


def test_nc_journey_revoke__s01_detects_a_revocation_the_gateway_ignores(workdir):
    """Negative control: with revocation removed in the gateway (bypass `revoke-ignored`),
    s01's revocation oracle must report it."""
    with world.composed(workdir, start=("worker",)) as trip:
        trip.box.start("gateway", INFRX_E3C_BYPASS="revoke-ignored")
        gamma = world.individual(trip, "gamma", workdir)
        request_id, handle, _ = run_sync(trip, gamma, world.TEXT, "e3c-nc-revoke")
        with pytest.raises(AssertionError, match="revocation not observed"):
            revoked_within(trip, gamma, handle, bound_s=KEY_CACHE_BOUND_S)


def same_key_on_both(trip, gateways, tenant, key: str, body: dict) -> list:
    """The same keyed `POST /v1/jobs` through every gateway at the same instant."""
    start = threading.Barrier(len(gateways))

    def send(http):
        start.wait()
        return http.post("/v1/jobs", json=body, headers=trip.headers(tenant, key))
    with ThreadPoolExecutor(len(gateways)) as pool:
        return list(pool.map(send, gateways))


def test_s02_two_gateways_two_tenants_one_logical_request(workdir):
    """Two gateway processes over the same stores. One key sent through both at once, then
    retried through each: one job, one acceptance, the rest replays. Each tenant's handles
    are 404 to the other through either process. One terminal accounting per job. A1's
    signup callback repeated concurrently grants nothing twice; a lost upload acknowledgement
    retried finalizes to the same ref."""
    import httpx
    with world.composed(workdir) as trip:
        other = world.second_gateway(trip.box)
        other.start("gateway")
        b = httpx.Client(base_url=other.url, timeout=120.0)
        try:
            alpha, beta = trip.world.alpha, trip.world.beta
            body = {"model": stack.CREDIT_ALIAS, "messages": world.TEXT}
            first = same_key_on_both(trip, (trip.http, b), alpha, "e3c-s02-both", body)
            assert [a.status_code for a in first] == [202, 202], [a.text for a in first]
            handle, = {a.json()["job_handle"] for a in first}
            assert sorted(a.json()["idempotency_replayed"] for a in first) == [False, True]
            for http in (b, trip.http, b):
                again = http.post("/v1/jobs", json=body, headers=trip.headers(alpha, "e3c-s02-both"))
                assert (again.status_code, again.json()["job_handle"],
                        again.json()["idempotency_replayed"]) == (202, handle, True), again.text
            (request_id, _state), = world.job_of(trip, alpha.org_id, "e3c-s02-both")
            assert trip.until_terminal(alpha, handle)["state"] == "succeeded"
            results = [http.get(f"/v1/jobs/{handle}/result", headers=trip.headers(alpha))
                       for http in (trip.http, b)]
            assert [r.status_code for r in results] == [200, 200] and \
                results[0].json() == results[1].json()
            mine = b.post("/v1/jobs", json=body, headers=trip.headers(beta, "e3c-s02-beta"))
            assert mine.status_code == 202, mine.text
            for http in (trip.http, b):
                foreign_404(trip, http, beta, handle)
                foreign_404(trip, http, alpha, mine.json()["job_handle"])
            trip.until_terminal(beta, mine.json()["job_handle"])
            for tenant, key in ((alpha, "e3c-s02-both"), (beta, "e3c-s02-beta")):
                (rid, _), = world.job_of(trip, tenant.org_id, key)
                world.settled_once(trip, rid)
                trip.conserved(tenant)
            callbacks(trip, alpha)
            ref = trip.upload(alpha, pilotbox.clip()).removeprefix("infrx-upload:")
            done = [http.post(f"/v1/uploads/{ref}/complete", headers=trip.headers(alpha))
                    for http in (trip.http, trip.http)]
            assert [d.status_code for d in done] == [200, 200] and \
                done[0].json() == done[1].json(), [d.text for d in done]
        finally:
            b.close()
            other.stop("gateway")


def callbacks(trip, tenant, repeats: int = 6) -> None:
    """A1's signup callback (`public.claim_signup_grant`) delivered again, concurrently, for
    an individual already granted: every answer is the one grant, nothing is written."""
    import psycopg
    start = threading.Barrier(repeats)

    def claim(_):
        with psycopg.connect(stack.harness.pg_dsn(trip.world.database), autocommit=True) as c:
            start.wait()
            return c.execute("select status, wallet_id::text from public.claim_signup_grant("
                             "%s, '', null)", (tenant.user_id,)).fetchone()
    with ThreadPoolExecutor(repeats) as pool:
        answers = set(pool.map(claim, range(repeats)))
    assert len(answers) == 1 and next(iter(answers))[1] == tenant.wallet.wallet_id, answers
    assert trip.db("select count(*) from infrx.credit_ledger where wallet_id = %s and kind = "
                   "'signup_grant'", tenant.wallet.wallet_id) == [(1,)]


def test_nc_journey_tenant__s02_detects_a_gateway_that_serves_another_tenants_job(workdir):
    """Negative control: gateway B with tenant scoping removed on the owned read (bypass
    `tenant-blind`); s02's cross-tenant oracle must report it."""
    import httpx
    with world.composed(workdir) as trip:
        other = world.second_gateway(trip.box)
        other.start("gateway", INFRX_E3C_BYPASS="tenant-blind")
        try:
            with httpx.Client(base_url=other.url, timeout=60.0) as b:
                answer = trip.send(trip.world.alpha, "async", world.TEXT, "e3c-nc-tenant")
                handle = answer.json()["job_handle"]
                trip.until_terminal(trip.world.alpha, handle)
                with pytest.raises(AssertionError, match="cross-tenant"):
                    foreign_404(trip, b, trip.world.beta, handle)
        finally:
            other.stop("gateway")


DATASET = world.harness.REPO_ROOT / "models" / "marlin2b" / "dataset.py"


def test_s01_the_external_dataset_client_resumes_uploads_across_a_gateway_restart(workdir):
    """E1C's resumable dataset client (`models/marlin2b/dataset.py`, run as the external
    client it is - never copied) uploads and infers four items, is interrupted, the gateway
    is SIGKILLed and replaced, and the same command resumes: one job and one settlement per
    item, every result exported, the wallet conserved (UPLOAD-RESTART + BACKEND-JOURNEY)."""
    import json
    import os
    import signal
    import subprocess
    if not DATASET.exists():
        world.blocked("E1C", why="no resumable dataset client (models/marlin2b/dataset.py)")
    with world.composed(workdir) as trip:
        alpha = trip.world.alpha
        (workdir / "clip.mp4").write_bytes(pilotbox.clip())
        manifest = workdir / "items.jsonl"
        manifest.write_text("".join(json.dumps({
            "id": f"item-{n}", "video": "clip.mp4", "prompt": f"What happens? ({n})",
            "max_tokens": 64, "start_s": 0, "end_s": 8}) + "\n" for n in range(4)))
        state = workdir / "run.sqlite"
        state.unlink(missing_ok=True)
        argv = [sys.executable, str(DATASET), "run", "--manifest", str(manifest), "--state",
                str(state), "--dataset-version", "e3c-s01", "--base-url",
                f"{trip.box.url}/v1", "--model", stack.CREDIT_ALIAS, "--retain-output",
                "text", "--form", "upload", "--concurrency", "2"]
        env = {**os.environ, "INFRX_API_KEY": alpha.secret}
        trip.engine.control(delta_gap_s=0.05)
        first = subprocess.Popen(argv, env=env, cwd=str(workdir), stdout=subprocess.DEVNULL,
                                 stderr=subprocess.STDOUT, start_new_session=True)
        world.wait_for(lambda: trip.db("select count(*) from infrx.jobs where org_id = %s",
                                       alpha.org_id)[0][0] >= 1, 60, "the first item admitted")
        os.killpg(first.pid, signal.SIGINT)
        first.wait(timeout=60)
        trip.box.kill("gateway")
        trip.box.start("gateway")
        trip.engine.control(delta_gap_s=0.0)
        resumed = subprocess.run(argv, env=env, cwd=str(workdir), capture_output=True,
                                 text=True, timeout=600)
        assert resumed.returncode == 0, resumed.stdout[-1500:] + resumed.stderr[-1500:]
        results, failures = workdir / "results.jsonl", workdir / "failures.jsonl"
        exported = subprocess.run([sys.executable, str(DATASET), "export", "--state",
                                   str(state), "--results", str(results), "--failures",
                                   str(failures)], cwd=str(workdir), capture_output=True,
                                  text=True, timeout=60)
        assert exported.returncode == 0, exported.stderr[-1500:]
        rows = [json.loads(line) for line in results.read_text().splitlines() if line.strip()]
        assert len(rows) == 4 and not failures.read_text().strip(), (rows, failures.read_text())
        jobs = trip.db("select request_id::text, idempotency_key from infrx.jobs where "
                       "org_id = %s", alpha.org_id)
        assert len(jobs) == 4 and len({key for _, key in jobs}) == 4, jobs
        for request_id, _ in jobs:
            world.settled_once(trip, request_id)
        trip.conserved(alpha)
