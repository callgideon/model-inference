# L3-SQL — the L3 ControlStore seam over the registry (lab-sql, LW2; the D slice of L3)

- Base `eb0734d7` · first commit `5ad2ff59` (by the cut session) · reconciled `f189baf9` · code head `be40bda4` · branch `codex/w5-lab-sql-lw2` (continuation relaunch, 2026-09-28)
- Task-local only (`INFRX_D_TASK=dlab`, PG 57500). LOCAL-ONLY migration (R151), nothing applied hosted. Nothing composes `PgControlStore` (the L3 lane does); no composition root touched.

## What changed in the continuation (WR-L3-1)
`5ad2ff59` shipped `PgLabControlStore` (`move`, `approve`, `rollback(alias, revision)`, `open_dev_wallet`, `history`) - not the surface L3 was built on (research/plan/evidence/l/L3-8e092ae.md, WR-L3-1 and its delta). 0032 is lane-owned and unmerged, so it was **reshaped in place** (no second, dead RPC set) into L3's `infrx.lab.control.ControlStore`, method for method:

| ControlStore | here |
|---|---|
| `db_now()` | `select infrx.now()` |
| `model_provider(model_id)` | `public.models.provider_org_id` (a non-uuid id is None, no query) |
| `deployment(id)` | 0007's row via `operations._DEPLOYMENT` (non-uuid: None) |
| `endpoint(provider, name, env, actor)` | `lab_control_endpoint`: get or create |
| `transition(id, provider, expected, to, actor, reason)` | `lab_control_transition`: CAS on the expected state of the provider's **private** revision (a public one is `not_found`: publication is only `publish`'s); 0007's guard keeps the graph (`state_conflict`); `lab_transition` event before/after |
| `propose(proposal, source, actor)` | `lab_control_propose`: source must be the provider's dev/private/`ready_private` revision of the same serving version; the row is inserted `proposed_public`; `lab_propose {source}` |
| `issue_dev_key(...)` | `lab_control_dev_key`: the provider's **dev** endpoint only; `api_keys` row `audience=provider_dev`, `org_id = provider_org_id` (auth.context's rule) - the first key opens a workspace organization whose id is the provider's (slug `lab-<id>`, no members, 0003's zero legacy wallet, no grant; lab-sql's call per WR-L3-1); `user_id` stays null (0009's CHECK), the member is `created_by`; `lab_dev_key {endpoint_id, prefix}` - never the hash |
| `publish(alias, card, expected_version, ...)` | `lab_control_publish`: advisory lock per alias, CAS on the current version, card inserted, `proposed_public -> active`, listing version + 1, the previous revision left active; `lab_publish` before/after |
| `rollback(alias, to_version, expected_version, ...)` | `lab_control_rollback`: CAS; `1 <= to < current` (`not_found`); target revision `active` (`state_conflict`); version + 1 repeating the target's revision **and card** |
| `fund_dev_wallet(provider, amount, operation_id, ...)` | `lab_control_fund`: provider_dev wallet at 0 if absent, then D5's audited `grant_credit` operator_allocation (replay = same entry, one `lab_fund` event) |
| `events(provider)` | `lab_control_events`, oldest first; `ControlEvent`/`Listing` field for field L3's models |

## Commands (`apps/infrx-api`, `INFRX_D_TASK=dlab`)
| command | head | exit | result |
|---|---|---|---|
| `pytest -q tests/d/test_l3sql_control.py` (new seam test, old 0032) | pre-`f189baf9` | 2 | collection error: `ImportError: cannot import name 'PgControlStore'` |
| same, first run with the new 0032 | pre | 1 | 9 passed, 1 failed (the store check assumed listing v1; the committed race had moved it - made version-agnostic) |
| `pytest -q tests/d/test_l3sql_control.py tests/d/test_l3sql_units.py` | `f189baf9` | 0 | **10 + 2 passed** |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_l3sql.py` | `f189baf9` | 0 | **49 passed**: 38 SQL + 8 Python mutants killed, lists well-formed, no superseded anchor, every case named |
| **L3 integration probe**: L3's files from `codex/w5-lab-access-lw2@08a353e7` copied untracked into this worktree, `pytest -q tests/l/control/test_control.py` | `f189baf9` | 1 | 25 passed, 1 failed: `a_newer_unvalidated_revision_is_never_keyed_priced_or_served[pg]` = exactly WR-L3-5 (A3's `catalog.py _PRIVATE`), as L3 predicted |
| same with `catalog.py` WR-L3-5 applied (uncommitted probe, reverted, `git diff` empty after) | `f189baf9` | 0 | **26 passed (13 pg cases on real PG against PgControlStore)** |
| L3's `INFRX_MUTANTS=all tests/l/control/test_mutants.py -k pg_` with the probe | `f189baf9` | 1 | 20/21 PG mutants killed; `pg_dev_revision_of_any_environment` survives: the service's `_dev` environment check is ALSO enforced by this store (transition refuses public, propose needs a dev/private source) - defense in depth; L3's list should drop that one from `PG_MUTANTS` (WR below) |
| `ruff check` changed Python | be40bda4 | 0 | all checks passed |
| final sweep: see the D6J evidence (`make api-test`) | be40bda4 | | |

## Oracles
LAB-PUBLISH (SQL half): CAS'd transitions on own private revisions; proposal only from a validated dev source; publication and rollback CAS'd on the listing version, admitted jobs keep their revision/card pins through publish and rollback (real CREDIT admissions); two operators racing publish once (the loser is told the alias moved on - the lock, not a colliding insert); two rollbacks land once; dev revisions never published, listed or admitted by a consumer alias. LAB-ACCESS: foreign/public `not_found`; dev keys only on the provider's dev endpoint, in the provider's org, hash never audited; browser roles 42501 on the table and all 8 RPCs; the platform role cannot edit the audit. Dev wallet opens at 0, funded only by an audited allocation, replay-safe.

## Wiring requests
- WR-LSQ-1 `tests/integration/test_harness.py` migration pin (see the D6J evidence for the full list 0031-0040).
- WR-LSQ-2 `Makefile` api-mutants, own-process PG line: `tests/d/test_code_mutants_l3sql.py` (with the other lab-sql lists; see D6J evidence).
- WR-LSQ-8 (L3 lane, `tests/l/control/mutants.py`): drop `pg_dev_revision_of_any_environment` from `PG_MUTANTS` (the store also enforces it), or keep it memory-only.
- WR-L3-5 (A3, unchanged): `infrx/state/catalog.py _PRIVATE`: `d.state = 'ready_private'`.

## Open issues
- `lab_control_dev_key` opens a `public.organizations` row per provider on its first dev key (id = provider id). Nothing else refers to it; a ruling may prefer a separate provider-key table later.
- 0004's closed D6 stubs are untouched (see D6J).

## Estimate (remaining, L3-SQL): 0.5/1.5/3 h, confidence medium; basis: one review round (D10-0025 at 1/2/5) plus L3's merge-time pg run.
