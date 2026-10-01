# W6 api-L4 — A6, A7, A8, A13 (Lab routes) — evidence at f6cd2d2

Lane api-L4 of wave W6 (behaviour-preserving clean-up). Branch `codex/w6-api-L4`, base `08983639`,
code head `f6cd2d20` (commits: A6 `c6d76b4a`, A7 `61e7e289`, A8 `cb240473`, A13 `b859f48f` + `f6cd2d20`).
Task-local key `l4` only (port 57503). No migration, contract (`infrx/contracts/`), Makefile,
pyproject or composition-root edit.

## Changed paths

- `apps/infrx-api/infrx/lab/time.py` (new: `iso_z`; the coordinator's ruling: `infrx/contracts/` is frozen)
- `apps/infrx-api/infrx/gateway/lab_auth.py`
- `apps/infrx-api/infrx/gateway/routes/lab_{checkpoints,control,datasets,evaluations,pipelines,releases,traces}.py`
- `apps/infrx-api/tests/g/lab_{auth,control,datasets,evaluations,pipelines,releases}/` (cases + mutant lists)

`routes/lab_common.py` was NOT created: the helpers went into `lab_auth.py` (audit A8's own fix and wave rule 8:
the module that already owned authenticate/member/ok/refusal/guarded is extended, not a sibling added).

## A6 — one `...Z` instant

- Seam case first: `tests/g/lab_releases/test_lab_releases.py::test_lab_releases__a_lab_instant_is_utc_to_the_second`
  — RED at `08983639` (collection error: `infrx.lab.time` absent), GREEN after. It runs with `TZ=Asia/Kolkata`
  so a naive instant read as host-local time is visible (restored in `finally`).
- `iso_z(value: datetime | str)`: ISO text parsed, aware -> UTC, naive taken as UTC, whole seconds.
  Replaced: lab_datasets:190, lab_evaluations:227, lab_releases:117, lab_pipelines:519 (4 of the audit's 9).
  Behaviour: identical for UTC-aware and naive datetimes (what `db_now()`/`clock()` return); a non-UTC aware value
  is now converted instead of printed as wall time.
- Mutants (tests/g/lab_releases, file `lab/time.py`): `instant_kept_in_its_offset`, `naive_instant_read_as_local`,
  `instant_text_refused`, `instant_keeps_its_fraction` — 4/4 killed; `proposed_at_unset` and evaluations'
  `created_at_not_the_experiments` re-pointed to `iso_z(now)` — killed.
- The other five formatters are other owners' files: wiring requests WR-L4-1..4 below.

## A7 — one refusal table

- Seam case first: `test_lab_auth__refusals_are_the_lab_ports_reasons` + `(Gone, 410, gone)`, `(ResultExpired, 410, gone)`
  — RED (`assert (503, b'{"refusal":"unavailable"}') == (410, b'{"refusal":"gone"}')`), GREEN after.
- `lab_auth.REFUSALS` gains `(errors.Gone, 410, "gone")`; new `lab_auth.status_of(exc) -> (status, reason)` used by `refusal`.
- `lab_pipelines._guarded` deleted (9 lines + the JSONResponse import); the route uses `lab_auth.guarded` — the same
  `{"refusal":"gone"}` 410 no-store answer.
- `lab_datasets.STATUS` 8 kinds -> 2 (`RequestTooLarge` 413, `InvalidRequest` 400 — the backend's own `{detail}` shape);
  every other status is `lab_auth.status_of(error)[0]` (401/403/404/409/410/503 unchanged; ImportRejected is an
  InvalidRequest -> 400 as before).
- Gone reachability for the other families (evaluations, releases, control, checkpoints, traces): no backend they
  call raises `errors.Gone` (grep: only datasets.versions, pipelines.annotations, content fakes, relay/jobs/media),
  so their answers are unchanged.
- Mutants: pipelines `gone_is_unavailable`/`gone_cacheable` and datasets `unauthenticated_is_unavailable` re-pointed
  to lab_auth; added lab_auth `gone_is_unavailable`, datasets `statuses_not_lab_auths`, `invalid_body_is_a_422`.

## A8 — the session families' helpers in one module

- Seam case first: `test_lab_auth__every_lab_family_shares_one_actor` (fake + pg) — RED
  (`AttributeError: module 'infrx.gateway.lab_auth' has no attribute 'Actor'`, 2 failed), GREEN after.
- `Actor`, `lab_actor`, `require`, `held`, `lab_body`, `MAX_BODY_BYTES` moved from `routes/lab_evaluations.py` into
  `lab_auth.py`; lab_pipelines/lab_releases import them from lab_auth (no route->route import left);
  lab_control's local `actor`/`body` now delegate (kept as one-line closures because tests/l/control's
  `reject_body_before_operator` anchors `decision = await body(request, Rejection)`); `lab_control.Actor` re-exports
  lab_auth's (`infrx/lab/control/operations.py` imports it from there).
- Line counts (dedup, base -> A8 commit): lab_evaluations 326 -> 287, lab_control 215 -> 199, lab_pipelines 582 -> 572
  (A7), lab_auth 121 -> 185 (the moved helpers + status_of + docstring). Copies of the actor/body logic: 2 -> 1.
- Anchors re-pointed to lab_auth: lab_control x6 (session_skipped, actor_role_not_the_memberships, actor_cached,
  content_type_unchecked, body_bounded_by_the_chat_cap, invalid_body_escapes), lab_evaluations x6; lab_auth list +3
  (`actor_role_not_the_memberships`, `require_ignores_the_role`, `not_held_is_a_page`). Two test lines now read the
  bound from `lab_auth.MAX_BODY_BYTES`.
- Every anchor in any list naming a touched file checked by a scratch script (lists under apps/infrx-api/tests and
  tests/integration): 283 anchors, 0 misdeclared (incl. lab_local/lab_operate/lab_evaluate/lab_rollout/lab_observe).

## A13 — typing of the Lab route modules

- `register(app: FastAPI, rt: Any, x: Family | None = None) -> Family | None` on all seven; `access: LabAccess`
  (TYPE_CHECKING import) instead of `object` on six dataclasses; `LabCheckpoints.ledger: CheckpointLedger`;
  `LabTraces.retention: Retention`; `who: Actor` on the 22 pipelines/releases operations; `port() -> Any`;
  `...` bodies on 14 docstring-only Protocol methods; `guarded(handler: Handler) -> Handler`;
  `launch -> dict | None` (honest: `_experiment` is Optional); lab_traces' possibly-unbound `grant` removed (one
  comprehension over CATEGORIES, same order, same last grant); lab_datasets' copied routes typed via `cast`.
- pyright 1.1.414 (no config = standard mode) on `lab_auth.py routes/lab_*.py`: base 51 errors
  (29 InvalidTypeForm, 15 ReturnType, 5 ArgumentType, 1 CallIssue, 1 OptionalMemberAccess) -> 37
  (29 InvalidTypeForm, 5 ArgumentType, 1 CallIssue, 1 OptionalMemberAccess, 1 ReturnType). Remaining, by owner:
  29 `lab.RefOf(...)` annotations (api-L5's `reportInvalidTypeForm=false`); 3 `AccessStore` vs P1's `Members`
  Protocol (its methods are unannotated -> inferred `None`; WR-L4-6); 3 `lab_traces.PgServing` over
  `traces.ship.pins.pg_rows` (api-L3's typed `pg_rows`); 1 `REF_RE.fullmatch(...).group` (a stored ref; a guard would
  change the 503's logged type — left); 1 lab_datasets `methods=list(route.methods)` (APIRoute.methods is Optional in the stub; left as it was).

## Commands (exit codes, counts)

| command (from apps/infrx-api, INFRX_D_TASK=l4) | exit | result |
|---|---|---|
| `pytest tests/g/lab_* tests/i/lab_control` at base 08983639 | 0 | 212 passed, 11 skipped |
| same at b859f48f (f6cd2d20 only restores one line of lab_datasets; tests/g/lab_datasets + tests/i/lab_control rerun: 25 passed, datasets mutants 28 passed) | 0 | 215 passed, 11 skipped (the 3 new cases; skips: pg cases keyed b3/p1/p2/r2/p3, ClickHouse stack, an empty PG-mutant param) |
| `pytest tests/g tests/l` at b859f48f | 0 | 1095 passed, 17 skipped (incl. the error-envelope tests test_errors/test_route_conformance) |
| `INFRX_MUTANTS=all pytest tests/g/lab_auth/test_mutants.py` | 0 | 31 passed (0 survivors) |
| ... `tests/g/lab_control` / `lab_evaluations` / `lab_pipelines` | 0 | 30 / 51 / 74 passed |
| ... `tests/g/lab_releases` / `lab_datasets` / `lab_checkpoints` / `lab_traces` | 0 | 70 (+3 PG-param skips) / 28 / 13 / 28 passed |
| `INFRX_LAB_API_PG=1 INFRX_MUTANTS=all pytest tests/i/lab_control/test_mutants.py` (PG half on l4: releases 200 on both logins) | 0 | 24 passed |
| `INFRX_MUTANTS=all pytest tests/l/control/test_mutants.py` (lab_control anchors) | 0 | 117 passed |
| `uvx ruff check --select F,E9,I001` on the touched modules | 0 | clean (3 pre-existing E501 lines untouched) |

Note: `tests/i/lab_control/test_mutants.py` WITHOUT `INFRX_LAB_API_PG=1` reports `releases_read_no_experiments`
broken_runner (its only case is pg-marked; the fake-half copy collects nothing, pytest exit 5) — pre-existing, the
list's documented invocation is with the PG half.

## Isolation incident (reported)

One rerun of `tests/l/control/test_mutants.py` (INFRX_MUTANTS=all) was started WITHOUT `INFRX_D_TASK=l4`, so its PG
half defaulted to the shared `d1` harness (infrx-d1-postgres, 127.0.0.1:55432). Its pristine baseline failed three pg
cases (broken_runner, 29 mutants unrun). The code path is `tests/l/control/conftest.py` `pgharness.recreate(
"infrx_d1_l3tpl")` + per-case copies `infrx_d1_l3case`: scratch databases of that suite, not d1's own database; the
container was not recreated (still "Up 40 hours" afterwards). The list was rerun on l4: 117/117. The coordinator may
want to tell the d1 holder that `infrx_d1_l3tpl`/`infrx_d1_l3case` may have been dropped/recreated around
2026-10-01.

## Wiring requests

- WR-L4-1 (api-L1, `infrx/gateway/pilot.py`): replace `_z`'s body with the shared formatter, keeping the name the
  release read models and tests/g/lab_releases' anchors use:
  `-def _z(value) -> str: ... return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`
  `+from ..lab.time import iso_z as _z   # A6`; re-point tests/g/mutants.py:1161 (`RELEASES_C6`) to file
  `lab/time.py`, old `"    if at.tzinfo is not None:\n        at = at.astimezone(UTC)\n"`, new `""`. Behaviour: identical
  for aware text/datetimes; naive text is now UTC instead of host-local (the box runs UTC).
- WR-L4-2 (api-L1, `infrx/lab/workers/__main__.py:762`): `"decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")` ->
  `"decided_at": iso_z(now)` + `from ..time import iso_z`.
- WR-L4-3 (owner of `infrx/rollouts/control/__init__.py:227`): same edit (`from ...lab.time import iso_z`); re-point
  tests/r/control/mutants.py:177's old text to `iso_z(now)`.
- WR-L4-4 (owner of `infrx/evaluation/checkpoints/__init__.py:359`):
  `now = iso_z(await self.access.store.db_now())` + `from ...lab.time import iso_z`.
- WR-L4-5 (docs-state): none needed in CLAUDE.md; `infrx/lab/time.py` is self-describing.
- WR-L4-6 (owner of `infrx/pipelines/annotations/__init__.py`): annotate `Members.membership -> ProviderMembership | None`
  and `Members.db_now -> datetime` so `LabAccess.store` (an `AccessStore`) satisfies it (3 pyright errors in lab_pipelines).

## Estimate (remaining)

Optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence high — basis: the four tasks are done and green; what
remains is review, the five wiring requests (each a one-line edit + one anchor) and a rerun on the merged tip.

## Fix round (1-L4-RV-1, 2026-10-01)

Finding: the one `tests/l/control` mutant rerun without `INFRX_D_TASK` reached the d1 harness (55432). Re-examined
with read-only `docker inspect` only (d1 itself was not touched again):

- `infrx-d1-postgres` carries `ai.infrx.d1.checkout = .../worktrees/codex-w5-lab-rollout-4`, created
  2026-09-29T11:54:16Z, so the label at incident time was another checkout's (labels are fixed at create).
- `tests/l/control/conftest.py` `pg_template` calls `pgharness.ensure()` BEFORE `recreate()`; `ensure()` takes the
  port lock, then raises `ForeignContainer` for a container another checkout labelled ("this run will not start, stop or
  delete it"). `recreate()` and the per-case copy also go through `assert_ours()` before any `DROP DATABASE`.
  The three pristine pg failures of that run are this refusal. Conclusion: the run was refused at the gate, and
  `infrx_d1_l3tpl` / `infrx_d1_l3case` were most probably NOT dropped. The evidence's earlier "may have been dropped"
  overstated it. The coordinator should still tell the d1 holder (codex-w5-lab-rollout-4) that a refused attempt was
  made, so they can check.
- The rule was still broken: the run should never have aimed at 55432. Process fix for this lane: every PG/mutant command
  in this lane's evidence now runs with `INFRX_D_TASK=l4` exported up front (all the reruns below do).
- WR-L4-7 (owner of `tests/d/pgharness.py`): stop the silent `d1` default:
  `SERVICE = local_services(os.environ.get("INFRX_D_TASK", "d1"))` -> refuse (pytest.skip/raise) when
  `INFRX_D_TASK` is unset and `INFRX_MUTANTS` is set, or when the checkout is a `codex/w6-*` worktree. Proposed oracle:
  `env -u INFRX_D_TASK INFRX_MUTANTS=all pytest tests/l/control -m pg` skips with a "set INFRX_D_TASK" reason and
  never runs `docker`. Today the ownership gate prevents damage only while d1's container exists. If the container is
  absent, a stray run would create its own on 55432.

Reruns at f6cd2d20 code (no code changed in this round; all with `INFRX_D_TASK=l4`):

| Command | Exit | Result |
|---|---|---|
| `pytest tests/g/lab_auth tests/i/lab_control` | 0 | 41 passed |
| `INFRX_LAB_API_PG=1 INFRX_MUTANTS=all pytest tests/i/lab_control/test_mutants.py` | 0 | 24 passed (PG matrix, releases 200 on both logins) |
| `INFRX_MUTANTS=all pytest tests/l/control/test_mutants.py` | 0 | 117 passed (the lab_control anchors, PG half on l4) |

Contention note: earlier attempts in this round reported failures because another checkout
(`codex-w5-merge-75`, a coordinator merge run) used the same `l4` key at the same time. The harness refused with
`HarnessBusy` ("Nothing was altered"). Those numbers were discarded, and the reruns above started after it stopped.

## Merge (merge #79, coordinator lane `codex/w5-merge-79`)

Lane head `011fbe50` merged `--no-ff` onto `21aefded` (merge-tree clean, no conflict). Verdict ACCEPT_WITH_FIXES
(lenses ACCEPT_WITH_FIXES x2). Every PG and mutant run of this merge set `INFRX_D_TASK=l4`; nothing touched d1/55432.

Wirings applied in the one wiring commit:

- WR-L4-3 (`infrx/rollouts/control/__init__.py` `_decide`, unowned this wave): `"decided_at": iso_z(now)` +
  `from ...lab.time import iso_z`; `tests/r/control/mutants.py` `r2_decided_at` old text re-pointed to `iso_z(now)`.
- WR-L4-4 (`infrx/evaluation/checkpoints/__init__.py:359`): `now = iso_z(await self.access.store.db_now())` + the import.
  (The checkpoints suite and list are `tests/b/checkpoints`; there is no `tests/e`.)
- WR-L4-6 (`infrx/pipelines/annotations/__init__.py`): `Members.membership(...) -> ProviderMembership | None`,
  `Members.db_now() -> datetime` (+ `ProviderMembership` imported from `contracts.v2.records`).

Carried to api-L1 (its files; it dispatches after this merge), exact text:

- WR-L4-1 (`infrx/gateway/pilot.py`): replace `_z`'s body with the shared formatter, keeping the name:
  `-def _z(value) -> str: ... return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")`
  `+from ..lab.time import iso_z as _z   # A6`; re-point `tests/g/mutants.py:1161` (`RELEASES_C6`) to file
  `lab/time.py`, old `"    if at.tzinfo is not None:\n        at = at.astimezone(UTC)\n"`, new `""`.
- WR-L4-2 (`infrx/lab/workers/__main__.py:762`): `"decided_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")` ->
  `"decided_at": iso_z(now)` + `from ..time import iso_z`.

Lens minors:

- L4-R1: `tests/g/lab_datasets::test_lab_datasets__a_derived_version_and_an_expired_or_cancelled_export` - an
  export part reads 200 while live, then 410 with a `{detail}`-only body after `expires_at` (the module clock moved
  61 s past a 60 s TTL) and after cancel. Mutant `gone_dropped_from_the_datasets_path` (lab_auth's Gone row removed)
  killed.
- L4-R2: the same case asserts the derived manifest's `created_at` is `YYYY-MM-DDTHH:MM:SSZ`; mutant
  `derived_created_at_not_iso_z` (`clock().isoformat()`) killed. Pipelines: the approval case already pins
  `approved_at == "2026-09-27T12:00:00Z"`; mutant `approved_at_not_iso_z` (`now.isoformat()`) added, killed.
- L4-R4 / L4-RV-2: `lab_auth.py`'s two `contracts.v2.records` imports merged into one parenthesized import;
  `ruff check --select I001 apps/infrx-api/infrx/gateway/lab_auth.py` -> "All checks passed!" (exit 0).
- L4-R5: `infrx/lab/time.py`'s A6 docstring names every call site: the four routes, WR-L4-3/4 (applied here) and
  WR-L4-1/2 (through api-L1).
- L4-RV-3: `iso_z` reading a naive value as UTC (where `pilot._z`/`strftime` read naive text as host-local) is an
  accepted correction under wave rule 1: the box runs UTC, so no answer changes there.
- L4-RV-4: the A8 oracle (`test_lab_auth__every_lab_family_shares_one_actor`) also asserts no
  `intake.check_content_type` copy remains in lab_control / lab_evaluations / lab_pipelines / lab_releases.
- L4-R3 / L4-RV-1 (the isolation incident above): recorded as row 80 of
  `research/plan/consumer-v1/10-carried-work-register.md` with its log line: d1's scratch Lab databases recreated
  2026-10-01 by an api-L4 rerun; the d1 holder (the coordinator's default harness) is informed; no container
  recreated. No code change (WR-L4-7 stays a proposal to the pgharness owner).

No ruling is numbered at this merge. Pre-commit runs in the merge worktree (INFRX_D_TASK=l4, INFRX_MUTANTS=all):
lab_datasets 30, lab_pipelines 75, lab_auth 31, tests/r/control 81, tests/b/checkpoints 62, tests/p/annotations 79
passed (0 survivors); focused suites `tests/g/lab_auth tests/g/lab_datasets tests/g/lab_pipelines tests/r/control
tests/b/checkpoints tests/p`: 219 passed, 23 skipped. The merge's full check record (shared clone at the wiring
head) is the coordinator's merge #79 entry.

## Log

- 2026-10-01: written at code head f6cd2d20 by the api-L4 implementer.
- 2026-10-01: fix round for 1-L4-RV-1 appended (incident re-examined, WR-L4-7, reruns on l4).
- 2026-10-01: Merge section appended by the merge #79 coordinator lane (head 011fbe50; WR-L4-3/4/6 applied; WR-L4-1/2 carried to api-L1; the minors; the incident row).
