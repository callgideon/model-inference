# api-lifecycle-3 (LW7 batch 3) — AP-11 11c close-out + 11d/11e preparation — evidence at c28f8a2

Lane api-lifecycle-3, branch `codex/w7-api-lifecycle-3`, worktree
`.claude/worktrees/codex-w7-api-lifecycle-3`, base `49114933`, code head `c28f8a2c`. Key `ap11`
(PostgreSQL 57567, Valkey 57568, MinIO 57569, ClickHouse 57570/57571). Nothing hosted, no box,
no AWS/SSM/S3, no Vercel, no secrets; never d1/55432; live mode never run; P-10 stays BLOCKED.

## Commits

| Commit | Slice |
|---|---|
| 4aae941b | (row 97, stage 14) `lab.s14` pins the newest *reviewed* rubric (criteria + output schema); only api-judge-2's `definition_pending` P-07 skeleton (or nothing) = `BLOCKED[P-07]`, never FAIL; the dry-run label is set only once the stage proceeds |
| 39a5f6ce | (row 97, stage 02) `world.py` starts AP-04's worker `python -m infrx.lab.workers artifacts` as a fourth owned process (own env: `LAB_DATABASE_URL`, `LAB_WORKER_HEALTH_PORT`, the unit's Lab objects); AP-04 is `composed` only when the unit mounts it AND the worker is ready, else config `uncomposed` names the cause and the runner prints it (AP-06's cause named too). (11d) `--mode live` is BLOCKED before any request naming each missing operator input: `BLOCKED[P-10]` (judge `live` + `judge_secret_ref` an `ssm:/` parameter name), `BLOCKED[GPU-TARGET]` (`live_target`), `BLOCKED[WINDOW]` (an existing `window_record`) |
| c28f8a2c | (11e) `--mode hosted` prints `HOSTED_PLAN` (release identity; OpenAPI export + generated clients; the coordinator's deploy via the rollout runbook / `lab-release.sh`; hosted smoke in inspect mode; the R271 thin-boundary tests; consumer regression; rollback via `90-revert.sh`) and reports NOT RUN (exit 3): nothing composed, sent or written. Runner `BASE` = 49114933 |

Changed paths (all owned): `tests/integration/api_lifecycle/{runner,world,conftest,test_runner,mutants,test_mutants}.py`, `stages/lab.py`. `verification.md`'s stage table needed no change (the 18 rows still match). The `hosting` and `judge` worker roles are deliberately not started: `hosting` needs a `HOSTING_*` slot on a GPU box, and the dry-run judge sends nothing (named in the verdict's `pins.lab_workers`).

## Commands, exit codes, counts

| Command (repo root) | Exit | Result |
|---|---|---|
| `runner.py --mode isolated --world ap11` at the base 49114933 | 1 | **RED (row 97 reproduced)**: 02 FAIL (verification operation `queued`), 14 FAIL (rubric v2 `definition_pending` has no output schema); 01, 08–13 PASS. `AP11-isolated-4911493-base-verdict.json` |
| `pytest tests/integration/api_lifecycle/test_runner.py` with the fake listing the base's rubrics (v1 reviewed + v2 pending, `/judge/configs` 409 on a pending version), before the fix | 1 | **RED: 12 failed, 87 passed** |
| new cases before their implementation (`could_not_compose`, `live_is_blocked…`, `hosted_mode…`) | 1 | each red, then green |
| `pytest tests/integration/api_lifecycle/test_runner.py` at c28f8a2c | 0 | **102 passed** (42 case functions) |
| `runner.py --mode isolated --world ap11 --out <scratch>` at c28f8a2c (clean tree, `pins.dirty: false`) | 3 | **BLOCKED, no FAIL**: 01, 02, 08–17 PASS (14–15 labelled dry-run); 03 BLOCKED[artifact] (the toy upload verifies; a serving revision of it is the API's actionable `unsupported`: missing_file ×4, unsupported_architecture/model_type/dtype - needs the measured Marlin bytes); 04 BLOCKED needs 03; 05, 07 BLOCKED needs 04/05 + AP-06 named with its cause; 06 needs 05; 18 BLOCKED[AP-06] + no runner (11d). 109 requests, 6 inference, 59 s. `AP11-isolated-c28f8a2-verdict.json` |
| `make api-lifecycle` at c28f8a2c | make 2 (recipe exit **3**, `Error 3`) | same verdict, every stage PASS or BLOCKED[named]; `pins.dirty: true` only because the target writes its own output dir into the tree (moved out, not committed: it holds service logs) |
| `runner.py --mode hosted` | 3 | NOT RUN, the 7-step plan printed, no state file written. `AP11-hosted-plan-c28f8a2-verdict.json` |
| `runner.py --mode live` with a well-formed config and none of the three inputs | 3 | BLOCKED[P-10], BLOCKED[GPU-TARGET], BLOCKED[WINDOW]; no request, no stage. `AP11-live-unconfigured-c28f8a2-verdict.json` |
| `INFRX_MUTANTS=all pytest tests/integration/api_lifecycle/test_mutants.py` | 0 | **152 passed** in 882 s: **148/148 mutants killed** (133 carried + 15 new), list well-formed, `test_every_case_is_covered_by_a_mutant` green (42 cases), both self-tests (no false kill) |
| `make api-lint` | 0 | All checks passed (ruff 0.15.12 also clean on `tests/integration/api_lifecycle`) |
| `make api-typecheck` | 0 | `pyright: 457 errors (baseline 458)` (the package is outside the pyright project) |
| `pytest tests/integration/test_makefile_mutant_lists.py tests/integration/test_lab_package_isolation.py` | 0 | 9 passed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (916 links, 501 documents) |
| grep `sk-infrx-` / `eyJ` / `Bearer` / `X-Amz-Signature` / the MinIO and ClickHouse local passwords in the four committed verdicts | - | 0 matches |

New mutants (15; 148 in the list): `pending_rubric_pinned`, `no_reviewed_rubric_fails`, `oldest_reviewed_rubric`, `label_before_rubric` (+ `label_before_outputs` re-anchored), `uncomposed_cause_dropped`, `live_inputs_skipped`, `live_judge_mode_unchecked`, `ssm_name_unchecked`, `live_target_unchecked`, `window_record_unread`, `hosted_mode_runs_stages`, `hosted_plan_unprinted`, `hosted_plan_without_boundary`, `hosted_plan_without_rollback`. `world.py` has no layer-1 case (its proof is the isolated verdict, as before).

## What the isolated run is and is not

Real: PostgreSQL (Supabase image, migrations 0001..0067 on the base), Valkey, MinIO, ClickHouse,
the gateway and worker in pilot mode, the Lab control unit, AP-04's artifacts worker, `fake_vllm.py`,
real HTTP. Declared fixtures as before (verified sign-up + edge sessions, operator flags, the
NemoStation membership, the seeded listing, the judge's dry run). Not GPU, live-judge, hosted or
a complete-lifecycle result.

## Wiring / schema requests

None. (No composition root, Makefile, tasklocal or migration change is needed: `make api-lifecycle`
and the mutant line run this code unchanged; ap11's ClickHouse block is on the base.)

## Open items (proposed, for the coordinator)

- `infra/runbooks/api-lifecycle.md` (api-lifecycle-2's, not this lane's path) should name the two
  new modes: `--mode live` needs `judge: live`, `judge_secret_ref: ssm:/...`, `live_target`,
  `window_record`; `--mode hosted` is a print-only plan.
- `make api-lifecycle` writes its verdict dir (with the service logs) into the tree; a
  `$${TMPDIR}` out dir (or a `.gitignore` line) would keep `pins.dirty` honest.
- 11d with a live target: stages 05–07 bodies follow AP-06's merged schemas (`operator_publication.py`
  on the Lab unit behind `LAB_PUBLICATION`, operator routes on origin `lab`), and stage 18 needs a
  runner; both only become runnable once stage 04 has a candidate engine (GPU window).
- Proposed ruling (unnumbered): "An AP-11 live run is BLOCKED, before any request, until the
  config names P-10's secret by SSM parameter name, the approved live target and an existing
  operator window record; `--mode hosted` only prints its plan and is never a pass."

## Estimate (remaining AP-11 work)

11c close-out (this lane): 0.5 / 1 / 2 h for review fixes. 11d/11e execution (coordinator-serialized,
live target): 8 / 14 / 26 h, confidence low - AP-06 body alignment for 05–07 and a stage-18 runner
(~1–2 h per stage on this framework), plus P-10, a GPU window, the R151 window for 0060+, the
hosted smoke and cleanup; each waits on an operator input.
