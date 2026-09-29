#!/usr/bin/env python3
"""lab-sql LW9: `0058_lab_variant_identities.sql` on real PostgreSQL - R3's two revision
identities per optimization variant (WR-LW7-3a) and the release page's per-serving tally
(WR-C7-TALLY), composed with their stores; R251's grants to the Lab control login.

World: test_d7_lab_data's seed (NEMO, OTHER; D7's variant/report helpers, 0054's release
and job fixtures). Each `check_*` is the check a mutant in `tests/d/test_code_mutants_lw9.py`
must break.

    INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw9.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.state import migrations
from infrx.state.jobstore import connector

from ..d import checks_credit as cc
from ..d import pgharness
from ..d import test_code_mutants_live as lv
from ..d import test_d7_variant as v

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
DB = f"{pgharness.DATABASE}_lw9"
NEMO, OTHER, uid, ok, refusal = v.NEMO, v.OTHER, v.uid, v.ok, v.refusal
LOGIN = "infrx_lab_control"
#: R251: the route reads the control unit's releases/optimizations pages call.
ROUTE = ("lab_optimization_variant_listing", "lab_release_tally", "lab_release_live")
WRITE = "lab_put_variant_identities"             # R3's (a worker/CLI), never a route's
seed = v.seed


def identity(engine_version: str = "0.11.0", quantization: str = "bf16",
             capabilities=("text", "tools")) -> dict:
    """An R3 `Identity` as `model_dump(mode="json")` writes it."""
    return {"checkpoint_digest": "sha256:" + "1" * 64, "tokenizer_digest": "sha256:" + "2" * 64,
            "served_model": "nemo/marlin-2b", "engine": "vllm",
            "engine_version": engine_version, "quantization": quantization,
            "hardware": "g6e.2xlarge", "preprocessor": "p1", "capabilities": list(capabilities)}


BASE, NVFP4 = identity(), identity(quantization="nvfp4")


def put(ref: str, provider: str = NEMO, base=BASE, variant=NVFP4, actor="r3") -> dict:
    return {"variant_ref": ref, "provider_org_id": provider, "base": base, "variant": variant,
            "actor": actor}


def _executes(conn, role: str, name: str) -> bool:
    return conn.execute("select has_function_privilege(%s, %s, 'execute')",
                        (role, f"infrx.{name}(jsonb)")).fetchone()[0]


# ----------------------------------------------------------------------------- checks
@v.rolled_back
def check_browser_roles_reach_nothing(conn) -> str:
    """DUR-RLS / R237 / R251: no browser session executes 0058's functions or reads its table;
    the platform role does; the Lab control login executes the route reads (the listing, the
    tally and 0054's Live, which the releases page now calls) and never R3's write."""
    probes = [f"select infrx.{name}('{{}}'::jsonb)" for name in (*ROUTE[:2], WRITE)]
    probes.append("select count(*) from infrx.lab_variant_identities")
    reached = [f"{s}: {sql[:60]}" for s in cc.BROWSER for sql in probes
               if not (cc.refused_as(conn, s, sql) or "").startswith("42501")]
    assert not reached, f"browser sessions reached 0058: {reached}"
    got = cc.refused_as(conn, "service", "select infrx.lab_optimization_variant_listing("
                        f"'{{\"provider_org_id\": \"{NEMO}\"}}')")
    assert got is None, f"the platform role cannot read: {got}"
    unit = {name: _executes(conn, LOGIN, name) for name in (*ROUTE, WRITE)}
    assert unit == {**dict.fromkeys(ROUTE, True), WRITE: False}, unit
    return f"{len(cc.BROWSER)} browser sessions x {len(probes)} probes refused; {LOGIN}: routes only"


@v.rolled_back
def check_a_variant_lists_the_identities_r3_stored(conn) -> str:
    """WR-LW7-3a: 0055's listing, in its order, with each variant's stored base and variant
    identity (null when R3 has not stored them); they are written once per variant (the same
    again is the same answer, different ones `state_conflict`), only for the writer's own
    variant (another provider's or none: `not_found`, R227), and only shaped as the Lab reads
    them (engine, engine_version, hardware, quantization, capabilities: `invalid_request`);
    a stored pair is never edited. Publication order, not ref order: the second variant
    sorts BEFORE the first by ref (lw7's T2 tags)."""
    first = v.variant(conn, 0x71)
    v.t.advance(conn, 5)                                    # the seed's clock is frozen
    second = v.variant(conn, 0x70)
    assert second < first, (second, first)
    foreign = v.variant(conn, 0x93, OTHER)
    policy = v.t.publish(conn, lv.d9.policy(uid(1, 0x99)))  # a record, not a variant
    answer = ok(conn, WRITE, put(first))
    assert answer == {"variant_ref": first, "base": BASE, "variant": NVFP4}, answer
    assert ok(conn, WRITE, put(first)) == answer, "a replay is the same answer"
    assert refusal(conn, WRITE, put(first, variant=identity(quantization="fp8"))) == \
        "state_conflict"
    assert refusal(conn, WRITE, put(first, OTHER, variant=identity(quantization="int4"))) == \
        "not_found", "another provider wrote this provider's variant's identities"
    assert refusal(conn, WRITE, put(foreign)) == "not_found"
    assert refusal(conn, WRITE, put(policy)) == "not_found"
    edited = cc.attempt(conn, "update infrx.lab_variant_identities set stored_by = 'x' "
                        "where variant_ref = %s", (first,))
    assert edited is not None, "a stored identity pair was edited"
    assert refusal(conn, WRITE, put(uid(9, 0x91))) == "not_found"
    for wrong in ({**NVFP4, "engine": None}, {k: x for k, x in NVFP4.items() if k != "hardware"},
                  {**NVFP4, "capabilities": "text"}, {**NVFP4, "capabilities": [1]}, "vllm"):
        assert refusal(conn, WRITE, put(second, variant=wrong)) == "invalid_request", wrong
    assert refusal(conn, WRITE, put(second, actor=None)) == "invalid_request"
    listed = ok(conn, "lab_optimization_variant_listing", {"provider_org_id": NEMO})
    plain = ok(conn, "lab_optimization_variants", {"provider_org_id": NEMO})
    assert [r["variant_ref"] for r in listed] == [r["variant_ref"] for r in plain], listed
    assert [r["variant_ref"] for r in listed] == [first, second], listed   # never `foreign`
    by = {r["variant_ref"]: r for r in listed}
    assert (by[first]["base"], by[first]["variant"]) == (BASE, NVFP4), by[first]
    assert (by[second]["base"], by[second]["variant"]) == (None, None), by[second]
    assert {k: x for k, x in by[first].items() if k not in ("base", "variant")} == \
        next(r for r in plain if r["variant_ref"] == first), "0055's fields changed"
    other = ok(conn, "lab_optimization_variant_listing", {"provider_org_id": OTHER})
    assert [(r["variant_ref"], r["base"]) for r in other] == [(foreign, None)], other
    return "listing = 0055's + stored identities (null when absent); own variant, once, shaped"


@v.rolled_back
def check_the_tally_counts_each_servings_terminal_requests_by_pin(conn) -> str:
    """WR-C7-TALLY: one row per (serving_ref, pinned_by) of THIS revision's assignments over
    terminal jobs (a queued job is not yet a request), ordered by serving then pin, its
    requests summing to 0054's two arms; another revision's jobs never count; a revision
    with none (or an unknown one) is []."""
    candidate = lv.ref_of(conn, cc.DEV_DEPLOYMENT)
    ref, body = lv.launch(conn, 0x94, candidate)
    other, other_body = lv.launch(conn, 0x95, candidate)
    quiet, _ = lv.launch(conn, 0x96, candidate)
    base = body["baseline_ref"]
    for i, (serving, state) in enumerate(
            ((candidate, "succeeded"), (candidate, "failed"), (candidate, "succeeded"),
             (candidate, "queued"), (base, "cancelled"), (base, "expired"), (base, "queued")),
            start=1):
        lv.job(conn, ref, body, uid(i, 0x94c), serving, state)
    with conn.transaction():                            # one explicit pin (R1's `explicit`)
        conn.execute("set local session_replication_role = replica")
        conn.execute("update infrx.lab_rollout_assignments set pinned_by = 'explicit' "
                     "where request_id = %s", (uid(3, 0x94c),))
    lv.job(conn, other, other_body, uid(1, 0x95c), candidate, "succeeded")
    rows = ok(conn, "lab_release_tally", {"policy_ref": ref})
    expected = sorted([{"serving_ref": candidate, "pinned_by": "cohort", "requests": 2},
                       {"serving_ref": candidate, "pinned_by": "explicit", "requests": 1},
                       {"serving_ref": base, "pinned_by": "cohort", "requests": 2}],
                      key=lambda r: (r["serving_ref"], r["pinned_by"]))
    assert rows == expected, rows
    arms = sum(a["requests"] for a in lv.live(conn, ref))
    assert sum(r["requests"] for r in rows) == arms == 5, (rows, arms)
    assert ok(conn, "lab_release_tally", {"policy_ref": other}) == [
        {"serving_ref": candidate, "pinned_by": "cohort", "requests": 1}]
    assert ok(conn, "lab_release_tally", {"policy_ref": quiet}) == []
    unknown = lv.d9.BASE.replace("serving", "policy")
    assert ok(conn, "lab_release_tally", {"policy_ref": unknown}) == []
    return "per (serving, pin) over terminal jobs of this revision; sums to Live's arms"


def check_the_stores_compose(conn) -> str:
    """The ports' round trips on their own connections (commits; tags 0x97-0x98):
    `PgLabVariants.put_identities` then `.variants` (the listing with them) and
    `PgReleaseStore.tally`."""
    from infrx.state.lab_rollout import PgReleaseStore
    from infrx.state.lab_variants import PgLabVariants
    connect = connector(pgharness.dsn(conn.info.dbname))
    ref = v.variant(conn, 0x97)
    store = PgLabVariants(connect)
    asyncio.run(store.put_identities(ref, base=BASE, variant=NVFP4, provider_org_id=NEMO,
                                     actor="r3"))
    [row] = [r for r in asyncio.run(store.variants(NEMO)) if r["variant_ref"] == ref]
    assert (row["base"], row["variant"]) == (BASE, NVFP4), row
    candidate = lv.ref_of(conn, cc.DEV_DEPLOYMENT)
    release, body = lv.launch(conn, 0x98, candidate)
    lv.job(conn, release, body, uid(1, 0x98c), candidate, "succeeded")
    assert asyncio.run(PgReleaseStore(connect).tally(release)) == [
        {"serving_ref": candidate, "pinned_by": "cohort", "requests": 1}]
    return "put_identities/variants and tally round trips"


CHECKS = {c.__name__: c for c in (
    check_browser_roles_reach_nothing,
    check_a_variant_lists_the_identities_r3_stored,
    check_the_tally_counts_each_servings_terminal_requests_by_pin,
    check_the_stores_compose)}


# ----------------------------------------------------------------------------- tests
@pytest.fixture(scope="module")
def conn():
    pgharness.ensure()
    pgharness.recreate(DB)
    pgharness.apply(DB, migrations.sql_for(shim=pgharness.NEEDS_SHIM))
    with pgharness.connect(DB) as connection:
        seed(connection)
        yield connection


@pytest.mark.parametrize("name", list(CHECKS))
def test_lw9(conn, name) -> None:
    print(f"{name}: {CHECKS[name](conn)}")
