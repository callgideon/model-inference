# D7 follow-up — review minors, WR-B-2, WR-B-7, H1 DatasetSources (cut session) and WR-R3-2 variant comparisons (lab-sql, LW2)

- Base `eb0734d7` · follow-up `9b8cf423` (cut session, 0034) · WR-R3-2 `be40bda4` (0040) · code head `be40bda4` · branch `codex/w5-lab-sql-lw2`
- Task-local only (`INFRX_D_TASK=dlab`). LOCAL-ONLY migrations (R151). Nothing composed; no composition root touched.

## Changed paths (all owned)
- `0034_lab_eval_followup.sql` (cut session; reviewed, unchanged) and `0040_lab_variant_comparisons.sql`.
- `infrx/state/lab_data.py` (cut session: `finish(error=)`, `release`, `run_results`, `put_evaluator`/`evaluator`, `put_eval_report`/`eval_report`, `uses`; now + `put_variant_comparison`, `variant_comparisons`).
- `tests/d/test_d7_followup.py` (14), `test_d7_variant.py` (4), `test_d7_units.py` (8), `code_mutants_d7.py` (`FOLLOWUP` 36 + `VARIANT` 11 SQL, +3 Python), `test_code_mutants_d7.py`, `test_upgrade_lab.py`.

## The cut session's items (0034), as recorded then
F3 reaper SKIP LOCKED observed; F4 all-failed run fails; F5 a sample without its source refused; F6 revocation stops leasing mid-run; F7 per-provider source/checkpoint ids; RSI-2 rollback note corrected; RSI-3 cost unit of the run's budgets; WR-B-2 (a) error code (b) registered evaluators (c) results read (d) **402 lease release `lab_release_attempt`** (the next lease is the same attempt number and key); WR-B-7 write-once reports by `report_digest`; H1 `lab_dataset_uses` = the captured grant versions' scope (fail-closed), `PgLabDataStore.uses` composes with `LabAccess` (R172). Seam first: 14 errors without 0034 (`UndefinedFunction infrx.lab_put_evaluator`) -> 14 passed.

## WR-R3-2 (0040)
`lab_variant_comparisons`: one row per `sha256` of the stored RFC 8785 bytes of an `infrx.variant_comparison.1`; `variant_ref` must be a published `lab:variant` record of the provider and `report_digest` the provider's B2 report (`not_found`); `optimization_claimed` only with `equivalent` (CHECK); immutable; `lab_variant_comparisons {provider_org_id, report_digest}` reads them beside the report.

## Commands (`apps/infrx-api`, `INFRX_D_TASK=dlab`)
| command | head | exit | result |
|---|---|---|---|
| `pytest -q tests/d/test_d7_variant.py` WITHOUT 0040 (seam first) | pre-`be40bda4` | 1 | **4 failed** |
| `pytest -q tests/d/test_d7_variant.py tests/d/test_upgrade_lab.py` | `be40bda4` | 0 | **4 + 3 passed** (the upgrade: 0001-0026 history -> every Lab file; + 0027-0035 Lab rows -> 0036-0040 unchanged and re-runnable) |
| `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_d7.py` | `be40bda4` | 0 | **148 passed** (run with D6F's list: 214 passed in 359 s): 67 + 36 + 11 SQL and 31 Python mutants killed, 3 list checks |

## Wiring
- WR-LSQ-1 (migration pin, D6J evidence). `tests/d/test_code_mutants_d7.py` is already on api-mutants line 1.
- WR-LSQ-6 (rollout-control lane, at merge): R3 stores its result with `PgLabDataStore.put_variant_comparison(result, provider_org_id=, actor=)` after B2's `put_eval_report`.

## Estimate (remaining, D7 follow-ups): 0.5/1.5/3 h, confidence medium; one review round.

## Fix round: 1-LSQ-INT-1 (code head `a330241b`)
Reproduced on the merged tree (`git merge-tree --write-tree claude/consumer-v1 a330241b`, clean; scratch worktree, scratch-only edit so tests/b PG suites accept `INFRX_D_TASK=dlab`): `tests/b/runner/test_runner_pg.py tests/b/checkpoints/test_checkpoints_pg.py` **10 failed** (`not_found: no such evaluator for this provider`; B3 turns it into `skipped`).

Two causes, not one:
1. **R167 registered evaluators (0034 WR-B-2(b)).** Kept - it is the requested upgrade - and filed as a wiring request below; B1's `evaluator_ref` and `lab_put_evaluator`'s ref are the same RFC 8785 sha256, so registering in `runner.freeze` is one call. `checkpoints.subscribe` and I5's drills go through `runner.freeze`, so the same call covers B3 and I5.
2. **F6 and F4 vs B1's revoked-data drills.** F6 refused the *lease* once a grant was revoked, so B1's worker never got to end the case `revoked` and the run wedged: fixed in this lane (`a330241b`) - F6 now refuses a `succeeded` finish (results) instead; leases go out, cases end `failed`/revoked. F4 (D7 review minor: a run whose every case failed ends `failed`) contradicts B1's assertion `report["state"] == "succeeded"` for an all-revoked run: kept (reviewed D7 minor), B1's two assertions and its fake follow in the wiring request.

**WR-LSQ-7 (eval-runner lane B1/B3, same merge batch as 0034; I5 reruns):**
```diff
diff --git a/apps/infrx-api/infrx/evaluation/runner/__init__.py b/apps/infrx-api/infrx/evaluation/runner/__init__.py
index 0d8cf88e..e89ab059 100644
--- a/apps/infrx-api/infrx/evaluation/runner/__init__.py
+++ b/apps/infrx-api/infrx/evaluation/runner/__init__.py
@@ -157,6 +157,8 @@ async def freeze(store, payload: dict[str, Any], *, evaluator: dict[str, Any],
     run = lab.parse(payload)
     limit = _checked(run, evaluator)
     await may_schedule(access, user_id=user_id, provider_org_id=provider_org_id)
+    await store.put_evaluator(evaluator, provider_org_id=provider_org_id, actor=user_id,
+                              evaluator_id=lab.REF_RE.fullmatch(run.evaluator_ref).group(3))
     run_ref = await store.publish(payload, provider_org_id=provider_org_id, actor=user_id)
     await store.create_run(run_ref, provider_org_id=provider_org_id)
     return await _pinned(store, run, run_ref, evaluator, limit)
diff --git a/apps/infrx-api/tests/b/runner/test_runner.py b/apps/infrx-api/tests/b/runner/test_runner.py
index f94b54b7..3a7024d7 100644
--- a/apps/infrx-api/tests/b/runner/test_runner.py
+++ b/apps/infrx-api/tests/b/runner/test_runner.py
@@ -364,7 +364,7 @@ def test_b1_a_created_run_resumes_after_a_revocation_and_ends_revoked() -> None:
     assert again == frozen
     report = run(w.runner(worker="w2").run(again))
     assert report["failures"] == {i: "revoked" for i in w.ids} and len(w.wallet.calls) == 1
-    assert report["cases"] == {"failed": 3} and report["state"] == "succeeded"
+    assert report["cases"] == {"failed": 3} and report["state"] == "failed"
     with pytest.raises(errors.NotFound):
         resume(provider=OTHER)
     with pytest.raises(errors.InvalidRequest, match="evaluator"):
diff --git a/apps/infrx-api/tests/b/runner/test_runner_pg.py b/apps/infrx-api/tests/b/runner/test_runner_pg.py
index 431aec72..7c318808 100644
--- a/apps/infrx-api/tests/b/runner/test_runner_pg.py
+++ b/apps/infrx-api/tests/b/runner/test_runner_pg.py
@@ -292,7 +292,7 @@ def test_b1_pg_a_created_run_resumes_after_a_revocation_and_ends_revoked(world,
             c.frozen.run_ref, c.frozen.cases, c.frozen.limit)
         report = run(c.runner("w2").run(again))
         assert report["failures"] == {i: "revoked" for i in c.ids}
-        assert report["cases"] == {"failed": N} and report["state"] == "succeeded"
+        assert report["cases"] == {"failed": N} and report["state"] == "failed"
         assert len(c.wallet.calls) == 1 and c.results() == []
     finally:
         restore(c.conn)
diff --git a/apps/infrx-api/tests/b/runner/world.py b/apps/infrx-api/tests/b/runner/world.py
index 1b100083..cc56dfe9 100644
--- a/apps/infrx-api/tests/b/runner/world.py
+++ b/apps/infrx-api/tests/b/runner/world.py
@@ -136,6 +136,9 @@ class FakeEvalStore(FakeLabStore):
             raise errors.NotFound("no such run for this provider")
         return run
 
+    async def put_evaluator(self, spec, *, provider_org_id, evaluator_id, actor) -> None:
+        """D7's lab_put_evaluator (0034, R167): content-addressed, so the fake keeps nothing."""
+
     async def create_run(self, run_ref, *, provider_org_id):
         record = self.catalog.resolve(run_ref, provider_org_id=provider_org_id)
         dataset = self.catalog.resolve(record.dataset_ref, provider_org_id=provider_org_id)
@@ -220,9 +223,9 @@ class FakeEvalStore(FakeLabStore):
         a.update(state=outcome, digest=digest, cost=cost)
         self.cases[key[:2]]["state"] = "done" if outcome == "succeeded" else "failed"
         run = self.runs[key[0]]
-        if not any(c["state"] in ("pending", "leased") for k, c in self.cases.items()
-                   if k[0] == key[0]):
-            run["state"] = "succeeded"
+        mine = [c["state"] for k, c in self.cases.items() if k[0] == key[0]]
+        if not any(s in ("pending", "leased") for s in mine):
+            run["state"] = "succeeded" if "done" in mine else "failed"   # 0034 F4
         return lease
 
     async def release(self, lease):
```

| command (merged scratch tree, `apps/infrx-api`, `INFRX_D_TASK=dlab`) | exit | result |
|---|---|---|
| `pytest -q tests/b/runner/test_runner_pg.py tests/b/checkpoints/test_checkpoints_pg.py` (no WR-LSQ-7) | 1 | 10 failed |
| same, with `runner.freeze` registering only | 1 | 9 passed, 1 failed (`...resumes_after_a_revocation_and_ends_revoked`: state `failed` - F4) |
| same, with all of WR-LSQ-7 | 0 | **10 passed** (incl. the WR-B-2(d) 402 case, no longer xfail: `PgLabDataStore.release` exists) |
| `pytest -q tests/b` (with WR-LSQ-7) | 0 | 65 passed, 10 skipped |
| `INFRX_MUTANTS=all pytest -q tests/b/runner/test_mutants.py tests/b/checkpoints/test_mutants.py tests/b/reports/test_mutants.py tests/i/lab_eval/test_mutants.py` (with WR-LSQ-7) | 0 | 200 passed |
| lane: `INFRX_MUTANTS=all pytest -q tests/d/test_code_mutants_d7.py` (+ d6j) at `a330241b` | 0 | 264 passed; fix-round mutants `d7f_results_ignore_revocation`, `d7f_result_rights_off`, `d7f_rights_refuse_leases` killed |

Not run: `tests/i/lab_eval/test_drills_pg.py` (needs I5's S3); it calls `runner.freeze`, so WR-LSQ-7 covers it - I5 reruns it at merge.
