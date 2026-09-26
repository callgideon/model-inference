# WAVE4B-UNION round 3: code head `05d63097`

Continues `wave4b-union-7418365.md` (rounds 1–2, accepted at `452b175e`). Same lane, branch,
worktree and isolation: `INFRX_D_TASK=union`, PostgreSQL 55458, Valkey 55454, MinIO
`infrx-union-s3` on 55455 (restarted for this round). The same two forced deviations apply:
`tests/q` on `INFRX_Q_VALKEY_PORT=55456`, and `tests/i`'s pooler harness on `infrx-i8-*`.
No hosted DB, pilot box, AWS or SSM. Nothing pushed; no rebase, reset, amend or stash.

## Commits (first-parent, after `452b175e`)

| # | Commit | What | Resolution |
|---|---|---|---|
| 1 | `b0492cde` | `merge --no-ff codex/w5-f5` at **1ea5047f** (code 7d28e115) | **hand-resolved** `tests/g/test_relay_readiness.py` import block (below) |
| 2 | `f145cd18` | WR-W5F5-2: `deploy/preflight.py` MANIFEST gains `MONITOR_DATABASE_URL` after `GATEWAY_API_KEY`, the exact entry from `W5-F5-3a6195b.md`. The evidence's diff has a bad hunk header (`git apply`: "corrupt patch at line 12"), so the 4 lines were inserted by an exact-string edit | own commit |
| 3 | `ae859eeb` | `merge --no-ff codex/p25-enact` at **a15393c5** (code 1c62eef8) | **hand-resolved** `infrx/config.py` and `tests/contracts/test_config_and_imports.py` (below). This conflict was not announced: both sides only added lines at the same spot, W5-F5's first |
| 4 | `2e6931ea` | WR-P25-1: gateway grace, with its proof and mutants (below) | own commit |
| 5 | `e49480f3` | WR-P25-2: `infra/runbooks/observe.md:189` becomes "`PROCESSING_CACHE_MAX_BYTES`, 50 GiB, P-25 decided 2026-09-25" | own commit |
| 6 | `cc49ae90` | WR-P25-3: `infra/alerts/operations.json` `threshold_status` wording only, for RetentionPendingDeleteOld, RetentionStale and ProcessingCacheRefusing. Thresholds unchanged; the `derived:` prefix kept. **Also includes a companion test edit** (below) | own commit |
| 7 | `367b5138` | WR-P25-4 (= WR-UNION-1 = F1): `KNOWN_UNPRODUCED` drops `UnsettleableJobs` | own commit |
| 8 | `07931439` | F2 doc fix: `infra/rollout/README.md`'s "Rollback target after migrations 0019+" row now says both recorded targets carry a `schema_proof` through 0023 (KNOWN-GOOD-PROOF), so the known-good rollback has a target up to 0023 and R3 is the fallback beyond it. One verification-log line added | own commit |
| 9 | `05d63097` | `merge --no-ff` `claude/consumer-v1` at **65445218**, as instructed | **hand-resolved** `tests/g/mutants.py`, union of both sides (below) |

Migrations: `git diff 74183655 05d63097 -- apps/app/supabase/migrations` is empty (0001–0023
unchanged since round 2).

`claude/consumer-v1` has since moved to **6badd4e1** (58 commits, APP-UNION round 2). From
65445218 that delta touches only `apps/app/**`, plan records and `Makefile` console targets;
nothing under `apps/infrx-api`, `infra` or `tests`. `git merge-tree` of `05d63097` with
`6badd4e1` is clean. It is not merged here.

### Hand resolutions

`tests/g/test_relay_readiness.py` (merge 1): union of the imports. w5-f5 brings
`builders as b`, `JobState, SettlementState, Usage`, `CREDIT as CREDIT_UNIT` and
`ERROR_EVENT`. The w5-merge delta brings `MediaStaging`, which the `M6_PHASE2` tripwire uses.
Checked right after: 25 passed, 2 xfailed. The 2 xfails are W5-F5's strict `0-W5F5-R2` probe
(WR-W5F5-4, a follow-up).

`infrx/config.py` / `tests/contracts/test_config_and_imports.py` (merge 3), `git show --cc ae859eeb`:
```diff
      journal_expire_interval_s: float = 300.0
 +    # E3C F-6: ... A credential: never in a repr or a log.
 +    monitor_database_url: str = field(default="", repr=False)
+     retention_grace_s: float = 3600.0
...
-     "PROCESSING_CACHE_MAX_BYTES": 64424509440, "RETENTION_INTERVAL_S": 300.0,
+     "PROCESSING_CACHE_MAX_BYTES": 53687091200, "RETENTION_INTERVAL_S": 300.0,
 +    # W5-F5 (E3C F-6): D10's read-only monitor login for the worker's reconciliation gauges
 +    "MONITOR_DATABASE_URL": "",
+     # P-25 (decided 2026-09-25): 50 GiB above, and the content collection grace
+     "RETENTION_GRACE_S": 3600.0,
```
`worker/__main__.py` auto-merged: P25's `lifecycle = PgLifecycle(connect, limits=limits,
grace_s=deployment.retention_grace_s)`, W5-F5's `reconciliation=reconciliation_reader(deployment)`,
and M6's housekeeping are all present.

`tests/g/mutants.py` (merge 9): kept both blocks, HEAD first. HEAD has the five W5 wiring-4
mutants and the two W5-F5 mutants; the tip adds `docs_resume_not_replayed`. `_layout`
auto-merged with the tip's `apps/app` symlink. The P25 mutation-layout change (copying
`preflight.py` and `operations.json`) is in `tests/w/worker_main_mutants.py`, not in
`tests/g`, so it did not conflict. `tests/g/test_mutants.py` (list health, default subset):
31 passed.

### WR-P25-1 (commit `2e6931ea`)

- `gateway/pilot.py`: `_pg_lifecycle(connect, settings)` returns
  `PgLifecycle(connect, limits=settings.pilot, grace_s=settings.deployment.retention_grace_s)`.
  The caller in `adapters_from_env` passes `settings`.
- `tests/w/test_worker_main.py`:
  - The lane's strict xfail `test_worker_main__the_gateways_content_grace_is_the_deployments`
    loses its mark and passes: `grace_s == 11.0` with `RETENTION_GRACE_S=11`.
  - New case `test_worker_main_pg__a_source_the_gateway_registers_is_eligible_after_p25s_grace`
    runs on a fresh catalog database with one organization. It registers a written source
    through the pilot's own `adapters_from_env(...)["lifecycle"]` (pool closed, so it uses
    the adapter's own connection, as a service role). It asserts
    `eligible_at - registered_at == 3600 s`. Both cases passed.
- `tests/w/worker_main_mutants.py`:
  - `gateway_grace_wired_unrecorded` is replaced by `gateway_grace_dropped` (memory list;
    `_pg_lifecycle` loses `grace_s=`): **killed** (1 failed).
  - `pg_gateway_grace_dropped` (PG list, same edit, names the new PG case): **killed**.
  - `tests/w/test_worker_main_mutants.py` default (anchors, coverage, baseline): 9 passed,
    1 skipped.
- Fails-before: each mutant is exactly the pre-WR-P25-1 source (the library's 604,800 s).

### WR-P25-3 companion edit: needs the coordinator's confirmation

`tests/i/test_observe.py::test_ops_retention__each_rule_fires_on_its_fault_and_nothing_fires_when_healthy`
asserted `"⚠️ TO BE VERIFIED (P-25)" in threshold_status or status.startswith("exact")` for
every M6 rule. So WR-P25-3's wording, which removes that marker from three rules, fails the case
(`AssertionError: RetentionPendingDeleteOld`).

The assertion is now `"(P-25" in threshold_status or ...startswith("exact")`: every non-exact
M6 threshold still has to name its P-25 basis, open or decided. The oracle still holds:
dropping "(P-25)" from ProcessingCacheRefusing makes the case fail (1 failed; restored after).
This edit is in the same commit as WR-P25-3. Drop both together if that wording is not wanted.

## F4: a pre-existing red on `codex/p25-enact`, not applied here (WIRING REQUEST WR-UNION-2)

P25-ENACT's `worker_main_mutants._layout` now creates `apps/infrx-api/deploy/` (it copies
`preflight.py` there). `prep_worker_mutants._layout` and `_pg_layout` build on that layout and
then `shutil.copytree(API_DIR / "deploy", api / "deploy")` fails with
`FileExistsError: … apps/infrx-api/deploy`. `w5_mutants` uses the same two layouts.

Effect: every prep-worker and W5 mutant run is a runner error. Reproduced on a `git archive` of
`a15393c5` alone (same traceback). The P25 lane ran only the worker-main list. Per the brief
this lane-owned test helper is not edited here. The fix, `tests/w/prep_worker_mutants.py`:

```diff
@@ def _layout(root: pathlib.Path) -> pathlib.Path:
     api = worker_main_mutants._layout(root)
-    shutil.copytree(API_DIR / "deploy", api / "deploy",
+    shutil.copytree(API_DIR / "deploy", api / "deploy", dirs_exist_ok=True,
                     ignore=shutil.ignore_patterns("__pycache__"))
@@ def _pg_layout(root: pathlib.Path) -> pathlib.Path:
     api = worker_main_mutants._pg_layout(root)
-    shutil.copytree(API_DIR / "deploy", api / "deploy",
+    shutil.copytree(API_DIR / "deploy", api / "deploy", dirs_exist_ok=True,
                     ignore=shutil.ignore_patterns("__pycache__"))
```

Proof on a `git archive` of `05d63097` plus this patch (a scratch copy; the worktree is untouched):

| Check | Result |
|---|---|
| `python -m tests.w.prep_worker_mutants` | **70/70 killed** |
| `pytest tests/w/test_prep_worker_mutants.py` (default) | 7 passed, 1 skipped |
| `python -m tests.w.w5_mutants` | **28/28 killed** |
| `pytest tests/w/test_w5_mutants.py` (default) | 6 passed, 1 skipped |
| `INFRX_MUTANTS=all` PG lists of both files, union block + MinIO | **15/15 killed** (prep 8, w5 7) |

Those are exactly the 5 `make api-test` failures below. With WR-UNION-2 the gate has 0 failed.

## Checks at `05d63097` (apps/infrx-api unless noted; `P = uv run --frozen --no-sync pytest -q -p no:cacheprovider`; union env + MinIO)

| Command | Exit | Result |
|---|---|---|
| `P tests/i/test_packaging.py` before WR-W5F5-2 / all four after | 1 / 0 | 1 failed, 22 passed / **49 passed** (packaging + envcheck + ops_steps + artifacts) |
| `P -rfEs tests/g` | 0 | **703 passed, 2 xfailed** (W5-F5's strict probe) |
| `P -rfEs tests/w` | 1 | **5 failed, 331 passed, 3 skipped**. The 5 are F4's `FileExistsError` (prep-worker pristine + 2 subset mutants, w5 pristine + 1). Every `_pg__`/MinIO worker case ran and passed, including P25's |
| `P -rfEs tests/m/test_retention.py` | 0 | **79 passed** |
| `P -rfEs tests/i` (full, with the i8 pooler block) | 0 | **236 passed, 1 xfailed**. F1 is closed |
| `P tests/contracts` | 0 | **1294 passed** |
| `P -rfEs tests/d/test_composition_pg.py` | 0 | **3 passed** |
| `python -m tests.g.mutants <the 94 relay.py mutants>` | 0 | **94/94 killed** (incl. w5_marker_skipped, w5_relay_expectation_unchecked, w5_f5_late_recheck_restored, w5_f5_legacy_recheck_dropped) |
| `python -m tests.w.worker_main_mutants` | 0 | **46/46 killed** (incl. W5-F5's and P25's) |
| `INFRX_MUTANTS=all P tests/w/test_worker_main_mutants.py tests/w/test_prep_worker_mutants.py -k test_pg_mutant_is_killed` | 1 | worker-main PG **7/7 killed** (incl. `pg_gateway_grace_dropped`, `pg_monitor_login_sets_a_role`, `pg_privilege_refusal_every_tick`); prep-worker PG 8 failed = F4 |
| `python -m tests.w.prep_worker_mutants` | 1 | F4 (`FileExistsError` in the pristine layout); 70/70 with WR-UNION-2 |
| `python tests/i/mutants.py known_good_proof_ignores_through known_good_record_unproven schema_proof_trusts_moved_statements` | 1 → 0 | First try: broken_runner, because another session's `tests/i/test_mutants.py` run held the shared `infrx-i8-*` pooler at the same moment (the baseline's pooler cases failed). Rerun with it free: **3/3 killed** |
| root: `known-good.py bda15866… --applied 0023 --set MAX_VIDEO_SECONDS=82 --set WORKER_CONCURRENCY=8` | 0 | **KNOWN-GOOD** |
| root: `validate_plan.py` | 0 | PASS |
| root: `make api-test` | 2 | **5 failed, 4505 passed, 5 skipped, 11 xfailed** (43m05s). The 5 are exactly F4 (10 `FileExistsError` lines); nothing else failed |

## Follow-ups (recorded, not done here)

- **F3 (per the coordinator: leave as merged).** `worker/__main__.py` builds two
  `PgLifecycle`s over one `connect`:
  - M6's `lifecycle`, which now carries P25's `grace_s`, is used for content registration
    and retention.
  - W5's `readiness=PgLifecycle(connect, limits=limits)` is given to `PreparationRunner` and
    has the library grace.
  - The readiness instance only claims (`claim_preparation_ready`) and reads `readiness`. It
    never registers or completes, so its grace is inert today.
  - Reusing `lifecycle` there would remove the trap. It needs W5's `main_readiness_not_wired`
    anchor re-pointed.
- WR-W5F5-1 (D10 grant for the monitor login) and WR-W5F5-4 (relay attach path; strict-xfail
  probe in place) are W5-F5's open requests.
- `infra/runbooks/observe.md:177` still says the retention thresholds assume a
  "300 s interval, ⚠️ TO BE VERIFIED (P-25)". WR-P25-2 named only line 189.
- WR-P25-5 (15-pending-inputs P-25 row) is the coordinator's.

## Remaining effort

WR-UNION-2 (the 2-line patch above) then one `make api-test` (about 45 min). After that come
the E3C final run and the coordinator's merge onto the tip (6badd4e1 merges clean). Optimistic
1 h, likely 1.5 h, pessimistic 3 h; confidence medium. Basis: every list is green here except
F4, whose fix is proven on a copy of this head.

## Verification log

- 2026-09-26: round 3 written at code head `05d63097`. Logs in the session scratchpad: `union/results.txt` (`r3a-*`, `r3b2-*`, `r3d-*`), `wr2-*.log`, `wr-union-2.patch`.
