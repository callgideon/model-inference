"""AP-07 W4: the data-use -> capture -> Lab trace proof on E5L's composed box (o11).

The ap7 lane proof (`apps/infrx-api/tests/ap07/test_trace_stack.py`) hands each answer to the
capture hook in-process; this one runs the box's own gateway and worker processes (E3C's
composed clone, namespace e5l) with `CONSOLE_DATA_USE` + `IDENTITY_API` + `LAB_TRACES` +
`TRACE_PUMPS` on, so every decision crosses the real HTTP surface:

1. alpha's owner reads `/console/v1/data-use` and opts alpha's key in (`PUT .../capture` full)
   with a verified session (the e5l session door answers `/auth/v1/user`); beta's owner
   decides its key `off` the same way;
2. alpha's sync, SSE and async requests are answered and shipped by the box (one trace each,
   mode full); beta's sync and async requests are answered and leave no envelope;
3. A's developer lists alpha's requests on `/lab/v1/traces` under the seed's sharing grant
   (`access_state` content) and reads the content;
4. alpha's owner revokes that grant through `DELETE /console/v1/data-grants/{id}`: the next
   Lab read is `revoked`, no content, no organization.

Lost acknowledgements, duplicate delivery and the projection outage are E5L's o07/o09 and the
ap7 stack's cases; not repeated here.

Needs the e5l stack (tasklocal block 57100-57199): under E5L's runner once the coordinator
registers it as scenario o11 (wiring request WR-AP07B-1), or by hand on a kept stack:

    apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --out <dir> --keep --only o01
    INFRX_E2_NAMESPACE=e5l apps/infrx-api/.venv/bin/python -m pytest -q \\
        --import-mode=prepend tests/integration/lab_observe/test_ap07_box_variant.py

Outside the e5l namespace the case is BLOCKED (a skip naming the prerequisite), never a pass.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BOX = os.environ.get("INFRX_E2_NAMESPACE") == "e5l"
pytestmark = pytest.mark.skipif(not BOX, reason=(
    "BLOCKED[e5l stack]: the composed box needs E5L's stack (block 57100-57199); rerun with "
    "INFRX_E2_NAMESPACE=e5l after `runner.py --keep` (this module's docstring)"))



def box_env(ow, trip, door, spool: Path) -> dict[str, str]:
    """Capture on (gateway and worker on one spool), the Lab traces route, the session actors
    and the data-use routes - every switch the variant needs, nothing else."""
    return {"TRACE_PUMPS": "1", "TRACE_SPOOL_DIR": str(spool), **ow.lab_traces_env(trip, door),
            "IDENTITY_API": "1", "CONSOLE_DATA_USE": "1"}


def test_o11_data_use_decided_on_the_box_reaches_capture_and_the_lab(workdir):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import observe_world as ow                      # the e5l harness loads only here
    world, run = ow.world, ow.run
    with ow.observe_trip(workdir, start=(), trace_prefix="infrx/") as trip, \
            ow.supabase_door(trip.box.env["SUPABASE_URL"]) as door:
        alpha, beta = trip.world.alpha, trip.world.beta
        env = box_env(ow, trip, door, workdir / "trace-spool")
        trip.box.start("worker", **{k: env[k] for k in ("TRACE_PUMPS", "TRACE_SPOOL_DIR",
                                                          "CLICKHOUSE_URL", "S3_TRACE_BUCKET")})
        trip.box.start("gateway", **env)

        def console(tenant, method: str, path: str, **kw):
            return trip.http.request(method, path, headers={
                "authorization": f"Bearer {ow.session(tenant.user_id)}"}, **kw)

        for tenant, mode in ((alpha, "full"), (beta, "off")):
            doc = console(tenant, "GET", "/console/v1/data-use")
            assert doc.status_code == 200, doc.text
            assert doc.headers["cache-control"] == "no-store"
            decided = console(tenant, "PUT", f"/console/v1/keys/{tenant.key_id}/capture",
                              json={"mode": mode, "consent_version": doc.json()["consent"]["version"]})
            assert decided.status_code == 200, decided.text
            [key] = [k for k in decided.json()["keys"] if k["key_id"] == tenant.key_id]
            assert key["effective_mode"] == mode, key
        assert trip.http.get("/console/v1/data-use", headers={
            "authorization": f"Bearer {alpha.secret}"}).status_code == 401, "an API key decided"

        sync = trip.send(alpha, "sync", world.TEXT, None)
        assert sync.status_code == 200, sync.text[:200]
        sse = trip.send(alpha, "sse", world.TEXT, None)
        assert sse.status_code == 200 and "data: [DONE]" in sse.text, sse.text[-200:]
        captured = {sync.headers["inference-id"], sse.headers["inference-id"],
                    ow.served(trip, alpha, "o11-on")}
        quiet_sync = trip.send(beta, "sync", world.TEXT, None)
        assert quiet_sync.status_code == 200, quiet_sync.text[:200]
        quiet = {quiet_sync.headers["inference-id"], ow.served(trip, beta, "o11-off")}
        retention = trip.traces.retention
        world.wait_for(lambda: all(run(retention.find_traces(alpha.org_id, r)) for r in captured),
                       60.0, "the gateway's ship pass")
        for request_id in captured:
            [row] = run(retention.find_traces(alpha.org_id, request_id))
            assert row.mode == "full" and row.content_stored, row

        def lab(path: str = ""):
            got = trip.http.get("/lab/v1/traces" + path, params={"provider_org_id": ow.PROVIDER},
                                headers={"authorization": f"Bearer {ow.session(ow.DEV)}"})
            assert got.status_code == 200, got.text
            return got.json()
        listed = {i["request_id"]: i for i in lab()["data"]}
        assert captured <= set(listed) and not quiet & set(listed), sorted(listed)
        assert {listed[r]["access_state"] for r in captured} == {"content"}
        one = sync.headers["inference-id"]
        assert "Describe the van." in lab("/" + one)["content"]

        grants = console(alpha, "GET", "/console/v1/data-grants").json()["data"]
        [grant] = [g for g in grants if g["provider_org_id"] == ow.PROVIDER]
        revoked = console(alpha, "DELETE", f"/console/v1/data-grants/{grant['grant_id']}")
        assert revoked.status_code == 200 and revoked.json()["state"] == "revoked", revoked.text
        after = lab("/" + one)
        assert after["access_state"] == "revoked" and not {"content", "grantor_org_id"} & set(
            after), after
        assert all(trip.traces.rows("trace_envelopes", r) == 0 for r in quiet), \
            "a key decided off left a trace"
        assert door.seen and all(door.seen), "a session was refused or never verified"
