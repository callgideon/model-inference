"""E3C s03 (UPLOAD-RESTART, RV-02): an upload's create / PUT / complete / resolve each in a
different gateway process, or across a gateway SIGKILL, on the real object store and
database. The same durable outcomes as F2C-L's lifecycle conformance (`UploadRepository`:
a ticket survives `reopen()`; wrong owner is not_found; a refused upload stays refused),
asserted through the mounted routes of real processes. Red on a tree whose upload state
lives in `MediaUploads.uploads` (RV-02); green needs D10's durable adapter composed by M5."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import world                                            # noqa: E402

import pilotbox                                         # noqa: E402
import stack                                            # noqa: E402

STEPS = ("create", "put", "complete")


def create(http, trip, tenant, data: bytes, **extra):
    return http.post("/v1/uploads", headers=trip.headers(tenant), json={
        "bytes": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        "accepted_mime": ["video/mp4"], **extra})


def put(http, trip, tenant, handle: str, data: bytes):
    return http.put(f"/v1/uploads/{handle}", content=data,
                    headers=trip.headers(tenant, **{"content-type": "video/mp4"}))


def complete(http, trip, tenant, handle: str):
    return http.post(f"/v1/uploads/{handle}/complete", headers=trip.headers(tenant))


def runs_on(trip, http, tenant, ref: str, key: str) -> str:
    """An async job naming the finalized ref, through `http`, to success; its request id."""
    import stack as _stack
    answer = http.post("/v1/jobs", json={"model": _stack.CREDIT_ALIAS,
                                         "messages": world.video(ref)},
                       headers=trip.headers(tenant, key))
    assert answer.status_code == 202, f"the finalized upload did not admit: {answer.text[:300]}"
    status = trip.until_terminal(tenant, answer.json()["job_handle"])
    assert status["state"] == "succeeded", status
    request_id = answer.json()["request_id"]
    world.settled_once(trip, request_id)
    return request_id


def two_gateways(trip):
    import httpx
    other = world.second_gateway(trip.box)
    other.start("gateway")
    return other, httpx.Client(base_url=other.url, timeout=120.0)


def cross_process(trip, a, b, key: str) -> None:
    """create on A, PUT on B, complete on A, a retried complete on B, the job on B: one
    durable ticket, one ref, one job that runs on the uploaded bytes."""
    alpha, data = trip.world.alpha, pilotbox.clip()
    ticket = create(a, trip, alpha, data)
    assert ticket.status_code == 201, ticket.text
    handle = ticket.json()["upload_handle"]
    stored = put(b, trip, alpha, handle, data)
    assert stored.status_code == 204, \
        f"PUT on the other gateway: {stored.status_code} {stored.text[:200]}"
    done = complete(a, trip, alpha, handle)
    assert done.status_code == 200, done.text
    again = complete(b, trip, alpha, handle)
    assert (again.status_code, again.json()) == (200, done.json()), \
        f"a retried completion on the other gateway: {again.status_code} {again.text[:200]}"
    runs_on(trip, b, alpha, ticket.json()["destination_ref"], key)


def test_s03_create_put_complete_resolve_each_on_another_gateway(workdir):
    with world.composed(workdir) as trip:
        other, b = two_gateways(trip)
        try:
            cross_process(trip, trip.http, b, "e3c-s03-cross")
        finally:
            b.close()
            other.stop("gateway")


@pytest.mark.parametrize("after", STEPS)
def test_s03_a_gateway_sigkill_after_each_step_loses_nothing(workdir, after):
    """The one gateway SIGKILLed and restarted after `after`; the remaining steps and the job
    run on the replacement process."""
    with world.composed(workdir) as trip:
        alpha, data = trip.world.alpha, pilotbox.clip()
        ticket = create(trip.http, trip, alpha, data)
        assert ticket.status_code == 201, ticket.text
        handle, ref = ticket.json()["upload_handle"], ticket.json()["destination_ref"]
        for step in STEPS[1:]:
            if STEPS[STEPS.index(step) - 1] == after:
                trip.box.kill("gateway")
                trip.box.start("gateway")
            answer = (put(trip.http, trip, alpha, handle, data) if step == "put"
                      else complete(trip.http, trip, alpha, handle))
            assert answer.status_code in (200, 204), \
                f"{step} after a restart at {after}: {answer.status_code} {answer.text[:200]}"
        if after == "complete":
            trip.box.kill("gateway")
            trip.box.start("gateway")
        runs_on(trip, trip.http, alpha, ref, f"e3c-s03-restart-{after}")


def test_s03_wrong_owner_and_wrong_digest_are_refused_on_every_process(workdir):
    """Another tenant's handle is not_found on both processes (never forbidden, never used);
    bytes that do not match the declared digest are refused, and the refusal is final on the
    other process too."""
    with world.composed(workdir) as trip:
        other, b = two_gateways(trip)
        try:
            alpha, beta, data = trip.world.alpha, trip.world.beta, pilotbox.clip()
            ticket = create(trip.http, trip, alpha, data)
            handle = ticket.json()["upload_handle"]
            assert put(trip.http, trip, alpha, handle, data).status_code == 204
            for http in (trip.http, b):
                for answer in (put(http, trip, beta, handle, data),
                               complete(http, trip, beta, handle)):
                    assert (answer.status_code, world.code(answer)) == (404, "not_found"), \
                        answer.text
            assert complete(trip.http, trip, alpha, handle).status_code == 200
            stolen = b.post("/v1/jobs", json={"model": stack.CREDIT_ALIAS, "messages": world.video(
                ticket.json()["destination_ref"])}, headers=trip.headers(beta, "e3c-s03-stolen"))
            assert (stolen.status_code, world.code(stolen)) == (404, "not_found"), stolen.text
            bad = create(trip.http, trip, alpha, data)          # declares the clip's digest
            bad_handle = bad.json()["upload_handle"]
            other_bytes = data[:-1] + bytes([data[-1] ^ 1])
            assert put(b, trip, alpha, bad_handle, other_bytes).status_code == 204, \
                "PUT on the other gateway"
            refused = complete(trip.http, trip, alpha, bad_handle)
            assert refused.status_code in (400, 415), refused.text
            again = complete(b, trip, alpha, bad_handle)
            assert again.status_code in (400, 409, 415) and again.status_code != 200, \
                f"a refused upload completed on the other gateway: {again.text[:200]}"
            assert trip.db("select count(*) from infrx.jobs where org_id = %s",
                           beta.org_id) == [(0,)]
        finally:
            b.close()
            other.stop("gateway")


def test_nc_upload_restart__s03_detects_process_local_upload_state(workdir):
    """Negative control: both gateways with the durable upload state removed (bypass
    `upload-local`: a handle is known only to the process that created it); s03's
    cross-process oracle must report it."""
    import httpx
    with world.composed(workdir, start=("worker",)) as trip:
        trip.box.start("gateway", INFRX_E3C_BYPASS="upload-local")
        other = world.second_gateway(trip.box)
        other.start("gateway", INFRX_E3C_BYPASS="upload-local")
        try:
            with httpx.Client(base_url=other.url, timeout=60.0) as b:
                with pytest.raises(AssertionError, match="on the other gateway"):
                    cross_process(trip, trip.http, b, "e3c-nc-upload")
        finally:
            other.stop("gateway")
