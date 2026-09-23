"""E3B.a: BACKEND-JOURNEY - two provisioned tenants, every supported input x mode, no App.

E3B phase 3: the bodies, against the MOUNTED gateway (the cutover lane's `ROUTERS = (health,
models, ingress, uploads, jobs)`), as a process of its own over this stack
(`pilotbox.py`: `create_app()` from the pilot environment, uvicorn, real HTTP). Every
injection and emulation in that composition is named in `pilotbox.py` and in the evidence;
none is the target state.

The matrix (18 §E3B.a, 04 BACKEND-JOURNEY): inputs text / video by URL / video by upload,
modes sync / SSE / explicit async, two tenants (`stack.provision_two_tenants()`). Per cell:
the mode's own contract (below), the same-key replay in the same mode is the same job, the
same key in either other mode is `409 idempotency_conflict` and writes nothing (R94), the
other tenant's status/result/events/cancel of the handle are 404, and after terminal: ONE
CREDIT debit at the admitted card x usage (half up), the hold settled, reserved back to its
prior value, one usage projection, the job pinned as `quote()` pinned it, the USD books
unmoved, both tenants' wallets conserved.

Pending: the three `video_upload` cells, on the owner reference `M3-U1` (stack.OWNERS): the
real media staging refuses an `infrx-upload:` reference today.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stack                                            # noqa: E402

INPUTS = ("text", "video_url", "video_upload")
MODES = ("sync", "sse", "async")


@pytest.fixture(scope="module")
def trip(tmp_path_factory):
    """The journey stack, once for the module: two tenants, PostgREST over their clone, the
    fake engine, the pilot box."""
    if not stack.has_stack():
        pytest.skip(f"no {stack.harness.PROJECT} stack: run `tests/integration/run.py "
                    f"--layer 3`")
    import pilotbox
    with pilotbox.journey(tmp_path_factory.mktemp("pilotbox")) as journey:
        yield journey


def messages_for(trip, tenant, input_kind: str) -> list:
    import pilotbox
    if input_kind == "text":
        return [{"role": "user", "content": "Describe the van."}]
    if input_kind == "video_url":
        source = f"https://{pilotbox.MEDIA_HOST}/clip-{tenant.name}.mp4"
    else:
        source = trip.upload(tenant, pilotbox.clip())
    return [{"role": "user", "content": [{"type": "text", "text": "What happens?"},
                                         {"type": "video_url", "video_url": {"url": source}}]}]


def error_code(response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def run_sync(trip, tenant, messages, key):
    """Sync: 200 only after the terminal commit, with the result and usage; the job named by
    `Inference-Id`. The same key again: the same job, `Idempotency-Replayed: true`."""
    first = trip.send(tenant, "sync", messages, key)
    assert first.status_code == 200, first.text
    body = first.json()
    request_id = first.headers["inference-id"]
    assert body["object"] == "chat.completion" and body["id"] == f"chatcmpl-{request_id}"
    assert body["choices"][0]["message"]["content"], body
    assert "idempotency-replayed" not in first.headers
    before = trip.untouched()
    again = trip.send(tenant, "sync", messages, key)
    assert (again.status_code, again.headers["inference-id"],
            again.headers.get("idempotency-replayed")) == (200, request_id, "true"), again.text
    assert again.json() == body, (again.json(), body)            # review J12: the same answer
    assert trip.untouched() == looked_up(before), "R91: the replay prepared or staged something"
    return request_id, trip.handle_of(request_id), body["usage"]


def resumed(frames: list[str]) -> list[str]:
    """What a resumed stream sends for `frames`: the same bytes, except that the relay starts
    every response's first delta with the assistant role (G2: "`role` on the first") - so a
    stream resumed after the original first delta carries the role once more."""
    import json

    import pilotbox
    out, first = [], True
    for frame in frames:
        data = pilotbox.frame_data(frame)
        if first and isinstance(data, dict) and data.get("choices") \
                and "content" in data["choices"][0].get("delta", {}):
            first = False
            if "role" not in data["choices"][0]["delta"]:
                data["choices"][0]["delta"]["role"] = "assistant"
                data["choices"][0]["delta"] = dict(sorted(data["choices"][0]["delta"].items()))
                frame = "\n".join(line if not line.startswith("data: ") else
                                  "data: " + json.dumps(data, separators=(",", ":"))
                                  for line in frame.splitlines())
        out.append(frame)
    return out


def run_sse(trip, tenant, messages, key):
    """SSE (G2's mode contract): the identity frame first (job handle and request id, no
    id), `id: <g>-<s>` model frames, a usage frame, `id: <terminal>` `data: [DONE]`. Replay
    from the journal (G3's events route over D4's `read_owned`) is byte-equal from EVERY
    issued cursor; a cursor past the head is 400 `invalid_cursor`. The same key again: the
    same job, replayed."""
    import pilotbox
    first = trip.send(tenant, "sse", messages, key)
    assert first.status_code == 200, first.text
    assert first.headers["content-type"].startswith("text/event-stream")
    sent = pilotbox.frames(first.text)
    identity = pilotbox.frame_data(sent[0])
    request_id = first.headers["inference-id"]
    assert pilotbox.frame_id(sent[0]) is None and "event: infrx.progress" in sent[0]
    assert (identity["phase"], identity["request_id"]) == ("accepted", request_id), identity
    handle = identity["job_handle"]
    assert pilotbox.frame_data(sent[-1]) == "[DONE]" and pilotbox.frame_id(sent[-1]), sent[-1]
    usage = pilotbox.frame_data(sent[-2])
    assert pilotbox.frame_id(sent[-2]) is None and usage["usage"]["total_tokens"] > 0, sent[-2]
    deltas = "".join(choice["delta"].get("content", "") for frame in sent[1:-2]
                     for choice in (pilotbox.frame_data(frame) or {}).get("choices", []))
    assert deltas, sent
    issued = [pilotbox.frame_id(frame) for frame in sent if pilotbox.frame_id(frame)]
    for cursor in issued:
        replay = trip.http.get(f"/v1/jobs/{handle}/events",
                               headers=trip.headers(tenant, **{"Last-Event-ID": cursor}))
        assert replay.status_code == 200, (cursor, replay.text)
        after = sent[[pilotbox.frame_id(frame) for frame in sent].index(cursor) + 1:]
        if cursor == issued[-1]:
            # From the terminal cursor itself nothing is replayed, and the answer still ends
            # the way every stream ends: the usage frame and a `[DONE]` (with no new id).
            after = [sent[-2], "data: [DONE]"]
        assert pilotbox.frames(replay.text)[1:] == resumed(after), (cursor, replay.text)
    generation = issued[-1].split("-")[0]
    past = trip.http.get(f"/v1/jobs/{handle}/events", headers=trip.headers(
        tenant, **{"Last-Event-ID": f"{generation}-{10 ** 6}"}))
    assert (past.status_code, error_code(past)) == (400, "invalid_cursor"), past.text
    before = trip.untouched()
    again = trip.send(tenant, "sse", messages, key)
    assert trip.untouched() == looked_up(before), "R91: the replay prepared or staged something"
    assert (again.status_code, again.headers["inference-id"],
            again.headers.get("idempotency-replayed")) == (200, request_id, "true"), again.text
    assert pilotbox.frame_data(pilotbox.frames(again.text)[0])["job_handle"] == handle
    return request_id, handle, usage["usage"]


def run_async(trip, tenant, messages, key):
    """Async (G3 request (g)): 202 with the handle, `Location`, `Retry-After: 2`,
    `Inference-Id`; status to terminal; the result with its response and usage. The same
    key again: 202, the same job, `Idempotency-Replayed: true`. `stream: true` on
    `POST /v1/jobs` is 400 on `stream`."""
    first = trip.send(tenant, "async", messages, key)
    assert first.status_code == 202, first.text
    body = first.json()
    handle, request_id = body["job_handle"], body["request_id"]
    assert set(body) == {"job_handle", "request_id", "state", "execution_mode", "created_at",
                         "deadline_at", "idempotency_replayed"}, body
    assert (body["execution_mode"], body["idempotency_replayed"]) == ("async", False), body
    assert (first.headers["location"], first.headers["retry-after"],
            first.headers["inference-id"]) == (f"/v1/jobs/{handle}", "2", request_id)
    status = trip.until_terminal(tenant, handle)
    assert (status["state"], status["cause"], status["result_available"]) == (
        "succeeded", "completed", True), status
    result = trip.http.get(f"/v1/jobs/{handle}/result", headers=trip.headers(tenant))
    assert result.status_code == 200, result.text
    assert result.json()["response"]["usage"] == status["usage"], result.text
    before = trip.untouched()
    again = trip.send(tenant, "async", messages, key)
    assert trip.untouched() == looked_up(before), "R91: the replay prepared or staged something"
    assert (again.status_code, again.json()["job_handle"], again.json()["idempotency_replayed"],
            again.headers.get("idempotency-replayed")) == (202, handle, True, "true"), again.text
    streamed = trip.send(tenant, "async", messages, None, stream=True)
    assert (streamed.status_code, error_code(streamed),
            streamed.json()["error"].get("param")) == (400, "invalid_request", "stream")
    return request_id, handle, status["usage"]


RUN = {"sync": run_sync, "sse": run_sse, "async": run_async}


def looked_up(before: tuple) -> tuple:
    """`Journey.untouched` after one lookup and nothing else."""
    return before[0] + 1, before[1]


def settled_once(trip, tenant, request_id: str, usage: dict, before: tuple) -> Decimal:
    """After terminal: ONE inference debit on the tenant's CREDIT wallet = the admitted card x
    usage (half up), the hold settled, reserved back to its prior value, one usage
    projection, one CREDIT usage row, the job pinned as `quote()` pinned it."""
    from infrx.contracts.v2 import records as v2
    doc, = trip.one("select infrx.job_admission(%s)", request_id)
    assert (doc["state"], doc["outcome"]["settlement_state"]) == ("succeeded", "settled"), doc
    pins = {name: str(value) for name, value in tenant.pins.model_dump(mode="json").items()
            if name not in ("schema_version", "requested_model")}
    assert {name: doc["pins"][name] for name in pins} == pins, (doc["pins"], pins)
    assert doc["pins"]["requested_model"] == stack.CREDIT_ALIAS
    card = v2.RateCardSnapshot.model_validate(doc["rate_card"])
    assert card.rate_card_version == tenant.pins.rate_card_version
    charged = card.debit(usage["prompt_tokens"], usage["completion_tokens"]).raw("CREDIT")
    assert charged > 0
    assert trip.db("select amount, kind from infrx.credit_ledger where request_id = %s",
                   request_id) == [(-charged, "inference_debit")], "not ONE debit at the card"
    assert trip.db("select wallet_id::text, state from infrx.credit_wallet_holds where "
                   "request_id = %s", request_id) == [(tenant.wallet.wallet_id, "settled")]
    assert trip.db("select accounting_regime, charged_credits from public.usage_events "
                   "where id = %s", request_id) == [("credit", charged)]
    assert trip.db("select count(*) from infrx.outbox where aggregate_id = %s and kind = "
                   "'usage_projection'", request_id) == [(1,)]
    ledger, reserved = trip.wallet(tenant)
    assert (ledger, reserved) == (before[0] - charged, before[1]), (before, ledger, reserved)
    return charged


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("input_kind", INPUTS)
def test_backend_journey(trip, input_kind, mode):
    """Two tenants call the mounted gateway: the mode's contract, the same-mode replay, the
    R94 cross-mode conflict that writes nothing, the other tenant's 404s, the settlement
    once at the admitted card, the USD books unmoved, both wallets conserved."""
    if input_kind == "video_upload" and "M3-U1" in stack.OWNERS:
        # review H-N1: the pending fails the day its blocker is gone (a structural probe).
        if not stack.upload_refs_refused():
            pytest.fail("M3-U1 is fixed: M's real staging resolves an infrx-upload: reference - "
                        "delete stack.OWNERS['M3-U1'] and run the video_upload cells")
        stack.pending("M3-U1", why="the real media staging refuses a chat naming a finalized "
                                   "infrx-upload: reference (MediaStaging.materialize)")
    alpha, beta = trip.world.alpha, trip.world.beta
    before = {tenant.name: (trip.wallet(tenant), trip.usd(tenant)) for tenant in (alpha, beta)}
    messages = messages_for(trip, alpha, input_kind)
    key = f"e3b3-{input_kind}-{mode}"
    request_id, handle, usage = RUN[mode](trip, alpha, messages, key)
    for other in MODES:
        if other == mode:
            continue
        mark = trip.untouched()
        crossed = trip.send(alpha, other, messages, key)
        assert (crossed.status_code, error_code(crossed)) == (409, "idempotency_conflict"), \
            f"R94: {mode} key reused in {other}: {crossed.status_code} {crossed.text}"
        assert trip.untouched() == looked_up(mark), \
            f"R91/R94: the {other} conflict was not answered by the lookup alone"
    foreign = trip.headers(beta)
    for method, path in (("GET", f"/v1/jobs/{handle}"), ("GET", f"/v1/jobs/{handle}/result"),
                         ("GET", f"/v1/jobs/{handle}/events"), ("DELETE", f"/v1/jobs/{handle}")):
        answer = trip.http.request(method, path, headers=foreign)
        assert (answer.status_code, error_code(answer)) == (404, "not_found"), \
            (method, path, answer.status_code, answer.text)
    settled_once(trip, alpha, request_id, usage, before["alpha"][0])
    assert trip.usd(alpha) == before["alpha"][1], "alpha's legacy USD books moved"
    assert (trip.wallet(beta), trip.usd(beta)) == before["beta"], "beta's books moved"
    if input_kind != "text":
        media, prepared = trip.one("select request_record->'media', prepared_refs from "
                                   "infrx.jobs where request_id = %s", request_id)
        kind = "url" if input_kind == "video_url" else "upload"
        assert [(ref["kind"], ref["mime"], ref["duration_s"]) for ref in media] == [
            (kind, "video/mp4", 10.0)], media                  # fetched (or uploaded), probed
        assert len(prepared) == 1, prepared                   # and prepared for the worker
    if input_kind == "video_upload":
        stolen = trip.send(beta, "sync", messages, f"{key}-foreign")
        assert (stolen.status_code, error_code(stolen)) == (404, "not_found"), stolen.text
    for tenant in (alpha, beta):
        trip.conserved(tenant)


def test_backend_journey__video_url_on_a_separate_worker_process(tmp_path):
    """The TARGET composition for video (review J2): the worker is a process of its own - what
    I2B-R4's `python -m infrx.worker` composes - not the gateway's. Today a video job fails
    there (`platform_error`): M's attach and processing-cache index are process memory, so the
    worker cannot resolve the file the gateway prepared (M3-U2). The case measures exactly that
    failure and pends on `M3-U2`; the day the request succeeds it fails, asking for the owner
    reference to go and the matrix to run this way. Nothing about it is ever a pass."""
    import pilotbox
    if not stack.has_stack():
        pytest.skip(f"no {stack.harness.PROJECT} stack: run `tests/integration/run.py "
                    f"--layer 3`")
    with pilotbox.journey(tmp_path, embedded=False) as trip:
        alpha = trip.world.alpha
        answer = trip.send(alpha, "sync", messages_for(trip, alpha, "video_url"), "m3u2-sync")
        request_id = answer.headers.get("inference-id")
        state = trip.db("select state, outcome_cause from infrx.jobs where request_id = %s",
                        request_id) if request_id else []
        assert (answer.status_code, error_code(answer), state) == (
            500, "internal_error", [("failed", "platform_error")]), \
            f"M3-U2 looks fixed ({answer.status_code} {state}): delete stack.OWNERS['M3-U2'] " \
            f"and run the video cells on a separate worker process"
        trip.conserved(alpha)
    stack.pending("M3-U2", why="a worker process cannot resolve media the gateway process "
                               "prepared (M's attach and cache index are process memory)")
    pytest.fail("a video job failed on a separate worker process and nothing pended")


def test_backend_journey__dataset_client_resume(trip, tmp_path, record_property):
    """04 BACKEND-JOURNEY / MARLIN-SOP: E1B's bench client (models/marlin2b/bench.py, the
    dataset client, `--target gateway`, text form) runs 8 items against the mounted gateway
    and is interrupted (SIGINT) mid-run; `--resume` with its raw file re-sends every item
    that is not terminal under the SAME key. Jobs = items (one per key), one hold per job,
    at most one debit per job and exactly one for each that succeeded, the wallet's total =
    the sum of the per-item debits, and no item accepted twice."""
    import os
    import signal
    import subprocess
    import time

    import pilotbox
    beta = trip.world.beta
    before = trip.wallet(beta)
    jobs_before, = trip.one("select count(*) from infrx.jobs where org_id = %s", beta.org_id)
    clip = tmp_path / "clip.mp4"          # the client insists on one; the text form sends none
    clip.write_bytes(pilotbox.clip())

    def bench(raw: str, *resume: str) -> list[str]:
        # The COPY's client under the mutation runner (the tree beside this file), so a
        # mutant of bench.py is what runs; the checkout's otherwise.
        return [sys.executable, str(Path(__file__).resolve().parents[3] / "models" /
                                    "marlin2b" / "bench.py"),
                "--target", "gateway", "--base-url", f"{trip.box.url}/v1",
                "--model", stack.CREDIT_ALIAS, "--forms", "text", "--requests", "8",
                "--concurrency", "2", "--seed", "7", "--dataset-version", "e3b3-resume",
                "--no-warmup", "--max-tokens", "64", "--out", str(tmp_path / "bench.jsonl"),
                "--raw", str(tmp_path / raw), "--prompt", "Describe the van.", *resume,
                str(clip)]
    env = {**os.environ, "INFRX_API_KEY": beta.secret}
    trip.engine.control(delta_gap_s=0.05)            # a generation long enough to interrupt
    try:
        # SIGINT back to its default in the client: a runner started in the background can
        # hand its children SIGINT ignored, and then the ctrl-c below would be no interruption.
        first = subprocess.Popen(bench("raw-1.jsonl"), env=env, cwd=str(tmp_path),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                 start_new_session=True, preexec_fn=lambda: signal.signal(
                                     signal.SIGINT, signal.SIG_DFL))
        end, raw = time.monotonic() + 60, tmp_path / "raw-1.jsonl"

        def recorded() -> int:            # the client flushes one row per finished attempt
            return raw.read_text().count('"item_key"') if raw.exists() else 0
        while recorded() < 2 and time.monotonic() < end and first.poll() is None:
            time.sleep(0.02)
        assert first.poll() is None, "the first run ended before it could be interrupted"
        os.killpg(first.pid, signal.SIGINT)               # the operator's ctrl-c
        first.wait(timeout=60)
        resumed = subprocess.run(bench("raw-2.jsonl", "--resume", str(tmp_path / "raw-1.jsonl")),
                                 env=env, cwd=str(tmp_path), capture_output=True, text=True,
                                 timeout=300)
        assert resumed.returncode == 0, resumed.stdout[-2000:] + resumed.stderr[-2000:]
    finally:
        trip.engine.control(delta_gap_s=0.0)
    runs = [[json.loads(line) for line in (tmp_path / name).open() if '"item_key"' in line]
            for name in ("raw-1.jsonl", "raw-2.jsonl")]
    keys = {row["item_key"] for rows in runs for row in rows}
    assert len(keys) == 8 and runs[0] and runs[1], (len(keys), [len(rows) for rows in runs])
    assert len(runs[0]) < 8, "the first run was not interrupted"
    jobs = trip.db("select request_id::text, idempotency_key, state from infrx.jobs "
                   "where org_id = %s order by admitted_at", beta.org_id)[jobs_before:]
    assert sorted(key for _, key, _ in jobs) == sorted(f"sop1.{item}" for item in keys), jobs
    debits, succeeded = [], set()
    for request_id, key, state in jobs:
        holds = trip.db("select state from infrx.credit_wallet_holds where request_id = %s",
                        request_id)
        ledger = trip.db("select amount from infrx.credit_ledger where request_id = %s",
                         request_id)
        assert len(holds) == 1 and len(ledger) <= 1, (request_id, holds, ledger)
        if state == "succeeded":
            assert holds == [("settled",)] and len(ledger) == 1, (request_id, holds, ledger)
            succeeded.add(key.removeprefix("sop1."))
        else:
            assert ledger == [], (request_id, state, ledger)
        debits += [-amount for amount, in ledger]
    accepted = [row["item_key"] for rows in runs for row in rows if row["outcome"] == "accepted"]
    assert len(accepted) == len(set(accepted)), f"an item was accepted twice: {accepted}"
    assert set(accepted) == succeeded and succeeded, (sorted(accepted), sorted(succeeded))
    ledger, _reserved = trip.wallet(beta)
    assert ledger == before[0] - sum(debits), (before, ledger, debits)
    trip.conserved(beta)
    record_property("resume", {"rows": [len(rows) for rows in runs],
                               "accepted": len(accepted), "states": sorted(
                                   state for _, _, state in jobs)})


# ------------------------------------------------------------------ provisioning fixture

def test_two_tenants_are_provisioned_with_their_own_resolved_wallets_and_pins(caplog):
    """API-OPS (R66, R71, R72, R85): the fixture the journeys use, through G6B's `Operations`
    on the real store - distinct users, orgs and keys; A1's real grant (10,000 CREDIT) read
    back through `tenant(secret).balance()`; each wallet RESOLVED from the credential and the
    other tenant's refused; an operator key spends no wallet; both pin the same published
    deployment; a replayed grant under another idempotency key is the same grant; no secret
    reaches a log line or an argv."""
    import logging

    from infrx.contracts import errors
    from infrx.contracts.v2 import ports
    from infrx.operations import cli

    caplog.set_level(logging.DEBUG)
    world = stack.provision_two_tenants()
    alpha, beta = world.alpha, world.beta
    assert len({alpha.user_id, beta.user_id}) == len({alpha.org_id, beta.org_id}) == 2
    assert alpha.key_id != beta.key_id
    assert (alpha.wallet.owner_user_id, alpha.wallet.personal_org_id) == (alpha.user_id,
                                                                          alpha.org_id)
    with pytest.raises(errors.Forbidden):
        ports.resolve_wallet(alpha.auth, beta.wallet)
    assert alpha.pins == beta.pins
    assert "real: IdentityDirectory=PgSignup" in alpha.provisioned_by
    assert "TenantStore=PgTenantStore" in alpha.provisioned_by   # E3B3: D5's adapters, no fake
    assert "fake" not in alpha.provisioned_by

    async def checks():
        for tenant in (alpha, beta):
            balance = await (await world.ops.tenant(tenant.secret)).balance()
            assert (str(balance.ledger_total), str(balance.reserved_total)) == (
                "10000.00000000", "0.00000000"), balance
        with pytest.raises(errors.Forbidden):
            await world.ops.tenant(world.operator_secret)
        return await assert_grant_replays(world, "alpha")
    stack.asyncio.run(checks())
    secrets = (alpha.secret, beta.secret, world.operator_secret)
    assert all(secrets) and not any(secret in caplog.text for secret in secrets)
    assert not any(secret in repr(tenant) for tenant in (alpha, beta) for secret in secrets)
    for secret in secrets:
        with pytest.raises(SystemExit, match="refusing a key on the command line"):
            cli.refuse_secret_argv(["grant", "--user", alpha.user_id, secret])


async def assert_grant_replays(world, name):
    """R71: a second grant for the same individual under ANOTHER idempotency key reaches the
    database (the operator's audit dedupe cannot answer it) and is the same grant."""
    tenant = getattr(world, name)
    operator = await world.ops.operator(world.operator_secret)
    again = await operator.grant_initial(tenant.user_id, idempotency_key=f"e3b2-regrant-{name}",
                                         reason="E3B2 replay drill")
    balance = await (await world.ops.tenant(tenant.secret)).balance()
    first = world.grants[name]
    assert (again["replayed"], again["ledger_operation_id"], str(balance.ledger_total)) == (
        True, first["ledger_operation_id"], "10000.00000000"), f"R71: not one grant: {again}"


def _signup_grant_not_unique() -> None:
    """The grant's uniqueness dropped, all four layers of it: the entitlement's key, the
    one-signup-grant-per-wallet ledger index, the replay answer of `claim_signup_grant` and
    the once-only guard of `grant_signup_credit`."""
    claim = stack.function_source("public.claim_signup_grant", "uuid, text, uuid")
    grant = stack.function_source("infrx.grant_signup_credit", "uuid, text, text, uuid")
    replay = ("if exists (select 1 from infrx.signup_entitlements e\n"
              "             where e.user_id = p_user_id and e.entitlement = "
              "'initial_signup_grant') then")
    once = ("if not exists (select 1 from infrx.signup_entitlements e\n"
            "                 where e.user_id = p_user_id and e.entitlement = "
            "'initial_signup_grant') then")
    assert claim.count(replay) == 1 and grant.count(once) == 1, "0006/0015 moved: stale drill"
    stack.defect("alter table infrx.signup_entitlements "
                 "drop constraint signup_entitlements_pkey")
    stack.defect("drop index infrx.credit_ledger_one_signup_grant_per_wallet")
    stack.defect(claim.replace(replay, "if false then"))
    stack.defect(grant.replace(once, "if true then"))


def test_e3b_db09_detects_a_signup_grant_that_is_not_unique():
    """Intentional defect on the REAL store (API-OPS, R71): with the grant's uniqueness
    dropped on the provisioning clone, the replay case must report a second grant."""
    world = stack.provision_two_tenants()
    _signup_grant_not_unique()
    with pytest.raises(AssertionError, match="R71: not one grant"):
        stack.asyncio.run(assert_grant_replays(world, "alpha"))


def test_an_unknown_pending_id_is_refused():
    """The pending vocabulary is closed: a typo cannot invent a new way to not run."""
    with pytest.raises(AssertionError):
        stack.pending("G9Z", why="not a task")


# ------------------------------------------------------------------ PostgREST (layer 3)

def _postgrest_or_skip():
    if stack.postgrest_owner() != "ours":
        pytest.skip(f"no {stack.POSTGREST} of this checkout: run "
                    "`tests/integration/run.py --layer 3`")
    import httpx
    return httpx.Client(base_url=stack.postgrest_url(), timeout=5.0)


def test_postgrest_refuses_anon_on_the_tenant_tables():
    """R59-4 through the real HTTP layer: `anon` has no grant on `organizations` after 0004,
    so PostgREST answers a permission error, not an empty 200 list."""
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"})
    assert answer.status_code in (401, 403), (answer.status_code, answer.text[:200])
    assert answer.json().get("code") == "42501", answer.text[:200]


def test_postgrest_service_role_reads_every_tenant():
    """The service role is what the gateway's PostgREST path uses; it bypasses RLS
    (E2R Limits 4), so tenant safety there is route-side - this measures that it does."""
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"},
                            headers={"Authorization": f"Bearer {stack.jwt('service_role')}"})
    assert answer.status_code == 200, answer.text[:200]
    assert len(answer.json()) >= 2


def test_postgrest_member_session_is_nobody_on_the_pinned_pairing():
    """A MEASUREMENT of the pinned pairing, over real HTTP: a member's JWT through PostgREST
    13.0.4 to the pinned `supabase/postgres` 17.6.1.173 is answered 200 with NO rows - the
    image's `auth.uid()` reads only the legacy `request.jwt.claim.sub` GUC and PostgREST 13
    sets only the JSON `request.jwt.claims` (E2R's SQL-level measurement, reproduced end to
    end). Hosted `auth.uid()` reads both forms (I1B), so the pinned image is the odd one out;
    bumping it is E's integration request. The case fails the day the pairing changes, so the
    assertion is updated deliberately rather than drifting."""
    import harness
    state = harness.load_state() or {}
    owner = (state.get("fixtures") or {}).get("principals", {}).get("owner_alpha")
    if not owner:
        pytest.skip("no seeded fixtures: run `tests/integration/run.py --layer 3`")
    with _postgrest_or_skip() as client:
        answer = client.get("/organizations", params={"select": "id"},
                            headers={"Authorization":
                                     f"Bearer {stack.jwt('authenticated', owner['user_id'])}"})
    assert (answer.status_code, answer.json()) == (200, []), answer.text[:200]
