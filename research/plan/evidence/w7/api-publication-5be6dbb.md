# api-publication (AP-06, 06a–06d) — evidence at 5be6dbb

Lane api-publication, wave 7 batch 2. Branch `codex/w7-api-publication`, worktree
`.claude/worktrees/codex-w7-api-publication`, base `b05eb6f4`, head `5be6dbba` (code), key `ap6`
(PostgreSQL 57558 only; never d1). Nothing is mounted by default: every route stands on
`rt.lab_publication`, which no composition sets until WR-AP06-1/2.

## What was built (no new domain)

| Slice | Delivered | Where |
|---|---|---|
| 06a | `POST/GET /lab/v1/control/endpoints/{id}/keys`, `DELETE …/keys/{key_id}`, `GET /lab/v1/control/dev-wallet` (R270, typed). Issue = `LabControl.issue_dev_key` on the endpoint's newest `ready_private` dev revision; membership + capability (`LabAccess.require`) BEFORE any receipt is recorded in the provider's scope; secret once, a replay names the key (`secret: null`, `secret_returned: false`). Listing/revocation/wallet reads are 503 `unavailable` until SR-AP06-1. Operator `POST /operator/v1/dev-wallet-grants` (`LabControl.fund`, the 0060 operation id = grant_credit's operation id) and `POST /operator/v1/deployments/{id}/dev-rate` (`LabControl.price_dev`). | `infrx/gateway/routes/lab_control.py` (`_dev_routes`), `operator_publication.py` |
| 06b | `GET /operator/v1/publication-proposals?provider_org_id=`, `GET …/{id}` (candidate, readiness receipt + availability, current listing + card), `POST …/{id}/approve` (`LabControl.approve` at the client's `expected_version`, gated on AP-05's readiness receipt of the source revision for this exact serving revision; none composed → 503), `POST …/{id}/reject` (`Operations.reject`). Operator = session actor with `operator` AND `profiles.is_operator` re-read by the store (`Operations.operator`); an operator API key is refused (AP-03's `console.actions.operator`). | `operator_publication.py` |
| 06c | `/v1/models` iterates the served model first (unchanged code path) then any other alias the catalog lists (`published_aliases`, duck-typed like `usd_price`); another alias is listed only with an operator-approved card and a route (`rt.routes.state(deployment, serving)`), owned by its provider slug; no route table → only Marlin (the relay's one upstream). | `infrx/gateway/routes/models.py` |
| 06d | `POST /operator/v1/listings/{model_id:path}/rollback` (`LabControl.rollback`, slash-carrying alias, fixed suffix); admitted jobs keep their pins (proved); a route down or a route table that cannot answer lists the alias explicitly `unavailable`, never hidden as healthy. | `operator_publication.py`, `models.py` |

Idempotency (R270 rule 2) for every mutation is `once()` over 0060's `ControlOps` (no second
store): key scoped to the actor's tenant + kind, bound to the body hash (other body → 409);
a finished first request answers again — its committed effect re-read from the domain
(succeeded) or its refusal (failed, same status); a request that died mid-way is reconciled
by re-reading the effect after its lease lapses, before any redo; a retryable refusal is not
recorded. Dev-key issue records `issued:<key_id>:<prefix>` as the operation's phase.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| seam red: `pytest -q tests/ap06` with base `lab_control.py`/`models.py` and no `operator_publication.py` | 2 | 2 collection errors (`operator_publication` absent); `test_catalog.py` alone on base `models.py`: 1 failed (routed alias), 3 passed (parity holds on base by construction) |
| `INFRX_D_TASK=ap6 pytest -q tests/ap06 tests/l/control tests/g/test_route_conformance.py tests/g/test_catalog_truth.py tests/contracts/test_openapi_export.py -k 'not mutant_is_killed and not false_kill'` | 1 | 136 passed, 1 failed = `tests/l/control/test_mutants.py::test_every_case_is_covered_by_a_mutant` — PRE-EXISTING at b05eb6f4 (uncovered `test_control_app__mounts_the_judge_family_only_with_lab_judge_api`, AP-08's), identical on the untouched base run |
| `tests/ap06` alone on ap6 | 0 | 26 cases: 23 unit + 3 PG (two-process race, 0060 replay + rollback + fund, dev key on PG) |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap6 pytest -q tests/ap06/test_mutants.py -k 'not pg_mutant'` | 0 | 40 passed: 36/36 unit mutants killed, list well-formed, every-case guard green, runner self-tests |
| `INFRX_MUTANTS=all INFRX_D_TASK=ap6 pytest -q tests/ap06/test_mutants.py -k pg_mutant` (own process) | 0 | 4/4 PG mutants killed (incl. `pg_lost_cas_rebased_on_the_fresh_version` by the two-process race) |
| `pytest -q tests/g tests/l tests/contracts -m 'not pg'` (no key) | 1 | 2305 passed; 24 failed = the unmarked `*_pg.py` suites refused by the d1 harness (`ForeignContainer: infrx-d1-postgres` owned by codex-w5-lab-rollout-4) + the pre-existing l/control guard; none touches this lane's code |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` / pyright JSON | 0 | 457 errors (baseline 458; −1: the models cache dict typed); lane files 0 new (models.py 91/101 pre-existing) |
| `tests/integration/test_makefile_mutant_lists.py` | 1 | expected until WR-AP06-5 (`tests/ap06/test_mutants.py` named 0 times) |
| catalog parity | — | `/v1/models` body bytes in legacy and CREDIT regimes == `tests/ap06/fixtures/models-document-b05eb6f4.json`, captured from base code before `models.py` changed (two captures `cmp`-identical) |

Failed-then-passed during the lane: `refusal_not_recorded` survived until the replayed-refusal
case advanced past the lease and used a distinct card (fixed the case, not the mutant);
`pg_lost_cas_answered_as_reconciled` survived because 0060's fence refuses a second finish
(the door was right) — replaced by the rebase-on-conflict defect, which the race kills.

## Wiring requests (coordinator; exact patches)

**WR-AP06-1** `apps/infrx-api/infrx/config.py`, `DeploymentSettings` after `lab_judge_api`:
```python
    # AP-06 (WR-AP06-1): the publication door on the Lab unit - `/operator/v1/publication-
    # proposals*`, `/operator/v1/listings/{alias}/rollback`, `/operator/v1/dev-wallet-grants`,
    # `/operator/v1/deployments/{id}/dev-rate`, `/lab/v1/control/endpoints/{id}/keys`,
    # `/lab/v1/control/dev-wallet` - over L3's LabControl and 0060's receipts. Off by default:
    # no such route exists. On, it needs AP-01's session actors on the unit.
    lab_publication: bool = False
```
**WR-AP06-2** `apps/infrx-api/infrx/lab/control/app.py` (`_compose`, before the return; the
unit's login already holds the `lab_control_*` and 0060 `control_op_*` EXECUTE grants):
```python
    publication = None
    if settings.deployment is not None and settings.deployment.lab_publication:
        from ...gateway.routes.operator_publication import Publication
        from ...state.control_ops import PgControlOps
        # readiness: AP-05's adapter when it merges; credentials: SR-AP06-1's (503 until then)
        publication = Publication(control.operations, PgControlOps(connect))
    return SimpleNamespace(..., lab_publication=publication, ...)
```
and in `create_app` add `operator_publication` to the imported families and to the
`for family in (...)` loop (`lab_control.register` already mounts the dev routes over
`rt.lab_publication`). With `actors=None` on the unit the routes answer 500 on every call:
compose AP-01's `SessionActors` there first (api-identity-2's Lab-unit wiring), or refuse to
start when `lab_publication` is on and `actors` is None. Composed test (add as
`tests/ap06/test_composed.py`):
```python
def test_composed__the_lab_unit_mounts_publication_only_with_lab_publication(monkeypatch):
    from fastapi.routing import APIRoute
    from infrx.contracts.openapi import export
    paths = lambda app: {r.path for r in app.routes if isinstance(r, APIRoute)}
    off = export._lab_control()
    assert not {p for p in paths(off) if p.startswith("/operator/v1/publication")}
    monkeypatch.setenv("LAB_PUBLICATION", "1")
    on = export._lab_control()                 # its env patch must pass LAB_PUBLICATION through
    assert "/operator/v1/publication-proposals/{proposal_id}/approve" in paths(on)
    assert "/lab/v1/control/endpoints/{endpoint_id}/keys" in paths(on)
```
**WR-AP06-3** `apps/infrx-api/infrx/contracts/openapi/export.py`: `_lab_control` env adds
`"LAB_PUBLICATION": "1"`; `FAMILIES` gains, before the `/lab/v1/` refusal entry,
`("/lab/v1/control/endpoints/", [{"SessionBearer": []}], "r270", "provider session")` and
`("/lab/v1/control/dev-wallet", [{"SessionBearer": []}], "r270", "provider session")`; then
`uv run --frozen python -m infrx.contracts.openapi.export` and `make api-client-test`
(every new route carries body + response schemas and an explicit operationId).
**WR-AP06-4 (with AP-05, batch 3)** a newly approved non-Marlin alias must not be admitted to
the Marlin engine: (a) `state/catalog.py` `PgCatalogDirectory.published_aliases()` =
`select l.public_model_id from infrx.catalog_listings l where l.version = (select max(v.version)
from infrx.catalog_listings v where v.public_model_id = l.public_model_id) order by 1`;
(b) the composition sets `rt.routes` (AP-05's endpoints: `async state(deployment, serving) ->
"ready" | "unavailable" | None`, ready only when the engine serves exactly that pinned
revision); (c) admission (`validate.py` → `catalog.resolve`) refuses 503 `unavailable` for a
non-served alias whose route is not ready, and the relay picks the route's upstream. Until
then approval stays impossible in practice: it needs a readiness receipt and no readiness port
is composed (503), so nothing but Marlin can be listed.
**WR-AP06-5** `Makefile` `api-mutants`, after the ap4 line:
```make
	# AP-06's list: fake world, then its PostgreSQL half (two-process race) in its own process; task-local key ap6
	cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=ap6 uv run --frozen pytest -q tests/ap06/test_mutants.py
```
Composed test: `tests/integration/test_makefile_mutant_lists.py`.

## Schema requests

**SR-AP06-1** (api-schema-2's 0066 or a later allocation), SECURITY DEFINER, EXECUTE to
`infrx_lab_control` only, each audited through `infrx.lab_control_audit`:
- `infrx.lab_control_dev_keys(p_args jsonb {provider_org_id, endpoint_id}) returns jsonb` →
  `[{key_id, endpoint_id, name, prefix, created_at, revoked_at}]`, the provider_dev keys of that
  provider's dev endpoint only (`not_found` for another provider's endpoint);
- `infrx.lab_control_revoke_dev_key(p_args jsonb {provider_org_id, endpoint_id, key_id, actor,
  idempotency_key}) returns jsonb` → the row, one-way (first `revoked_at` answers again; via
  `infrx.revoke_key`), `not_found` for a key of another provider/endpoint, action `lab_dev_key_revoke`;
- `infrx.lab_control_dev_wallet(p_args jsonb {provider_org_id}) returns jsonb` →
  `{opened boolean, balance numeric(20,8)}` (ledger total − reserved of the provider_dev
  wallet; `opened:false, balance:0` before the first allocation). The `PgDevCredentials`
  adapter (≈30 lines over `PgLabDataStore._call`) is this lane's follow-up once it lands.

## Protocols consumed by name (file with AP-05)

`Readiness.receipt(deployment_revision_id: str) -> ReadinessReceipt | None` with
`ReadinessReceipt{deployment_revision_id, serving_version_id, ready: bool, checked_at}` (the
newest receipt of that revision; `DependencyUnavailable` when the store is down);
`routes.state(deployment: DeploymentRevision, serving: ServingRevision) -> "ready" |
"unavailable" | None` (WR-AP06-4).

## Open issues

1. Operator proposal listing is per provider (`?provider_org_id=` required): no cross-provider
   proposals read exists; unpaged (a provider's few) with a `ponytail` note.
2. The detail has no benchmark policy or consumer-contract diff: no benchmark-policy store
   exists; it shows candidate vs current listing/card and the readiness receipt.
3. One approved profile (Marlin's `APPROVED`) gates every alias in `/v1/models`; a per-revision
   measured profile needs a store (contracts §6) — with AP-05/AP-10.
4. Pre-existing on the base, not this lane: `tests/l/control` every-case guard red
   (AP-08's `test_control_app__mounts_the_judge_family_only_with_lab_judge_api` has no mutant).

## Proposed rulings (unnumbered)

- A synchronous control mutation's Idempotency-Key is a 0060 receipt; its outcome is re-read
  from the domain's committed effect (never a stored copy of a secret), a refusal replays with
  its status, a retryable refusal is not recorded, and a died request is reconciled by read
  before any redo.
- `/v1/models` lists a non-served alias only with an operator-approved card and a ready engine
  route for exactly its pinned revision; the served model's entry is pinned by a byte golden.

## Estimate (remaining for AP-06)

optimistic 3 h / likely 5 h / pessimistic 9 h, confidence medium. Basis: the door, its PG
proofs and both mutant lists are done on ap6 (~6 h spent); remaining = review fixes, the
SR-AP06-1 adapter once the SQL lands, composing WR-AP06-1/2/3 with the composed test, and the
WR-AP06-4 route/admission join with AP-05 (pessimistic: the relay's upstream selection).
