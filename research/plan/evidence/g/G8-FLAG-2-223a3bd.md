# G8-FLAG-2 — flag verb minors 0-G8FLAG-R2, 0-G8FLAG-R3, 1-G8FLAG-R6

- Base `44ba44a9`; code head `223a3bd` on `codex/g8-flag-2`; this evidence commit follows.
- Changed: `apps/infrx-api/infrx/operations/cli.py` (flag verb only), `tests/g/ops/{test_flag.py,test_flag_pg.py,mutants.py}` (+7 mutants).
  `transition.py` unchanged. No SQL, no other verb, no runbook. Docker g8 only (55447/55492); nothing hosted, no box, no AWS.
- Not in scope: 0-G8FLAG-R4 (write-then-audit crash window, shared with `credit-transition`).

## What changed
| finding | change |
|---|---|
| 0-G8FLAG-R2 | `test_flag.py` audited tuple and `test_flag_pg.py` audit read assert `after.operation == "flag"` |
| 0-G8FLAG-R3 | fake `FlagStore.set_flag` records `lock_timeout_s`; the default (5.0) and `--lock-timeout-s 0.25` are asserted at the writer; PG lock case kept |
| 1-G8FLAG-R6 | the `--on/--off` group is no longer argparse-required; `--dry-run` alone prints the row; `--dry-run` with a direction prints `{row…, enabled_after, changed}` and writes nothing (even given a key and reason); a write without a direction is `SystemExit("--on or --off is required unless --dry-run")` |

Dry-run contract (amends the G8-FLAG table's `--dry-run` row):
`flag --name <flag> [--on|--off] --dry-run` → `{name, enabled, updated_by, reason, updated_at}` plus, with a direction, `enabled_after` and `changed` (= `enabled != enabled_after`); exit 0, no operator key, no write; regime/unknown refused as before.

## Fails before / passes after
| cmd (in `apps/infrx-api`, `INFRX_D_TASK=g8`) | exit | result |
|---|---|---|
| new tests, base code: `pytest -q tests/g/ops/test_flag.py tests/g/ops/test_flag_pg.py` | 1 | 2 failed, 5 passed: `test_flag__the_dry_run_…` and `test_flag_pg__regime_unknown_and_dry_run_…` (`one of the arguments --on --off is required`, R6) |
| same, head | 0 | 7 passed |
| base code + base `test_flag.py`, the R2/R3 mutants: `python -m tests.g.ops.mutants flag_audits_as_transition flag_lock_bound_hardcoded flag_cli_lock_bound_constant` | 1 | 0/3 killed, all `survived` (R2/R3 fails-before) |
| same mutants, head | 0 | killed (below) |

## Mutants (G ops list; all killed)
| mutant | edit | fake kill (runner) | PG oracle (applied to the worktree, `test_flag_pg.py`) |
|---|---|---|---|
| `flag_audits_as_transition` (R2) | `op._once("flag", …` → `op._once("transition", …` | killed | killed (`off_off_on`) |
| `flag_lock_bound_hardcoded` (R3) | transition `lock_timeout_s=lock_timeout_s)` → `30.0)` | killed | killed (`lock`, 35 s) |
| `flag_cli_lock_bound_constant` (R3) | cli `lock_timeout_s=a.lock_timeout_s` → `5.0` | killed | killed (`lock`) |
| `flag_dry_run_requires_a_direction` (R6, declared `SystemExit`) | group `()` → `(required=True)` | killed | killed (`regime_unknown_and_dry_run`) |
| `flag_dry_run_writes` (R6) | `if a.dry_run:` → `if a.dry_run and getattr(a, "enabled", None) is None:` | killed | killed (`regime_unknown_and_dry_run`) |
| `flag_dry_run_change_misreported` (R6) | `"changed": row["enabled"] != a.enabled` → `True` | killed | survives (PG runs `--off` only; the fake's `--on` case kills it) |
| `flag_write_without_a_direction` (R6) | direction check → `if False:` | killed | n/a (fake only) |

The six G8-FLAG mutants stay killed (13/13 flag mutants). PG column: `scratchpad …/pgkill.py` applies one mutant, runs `test_flag_pg.py`, restores; `git status` clean afterwards.

## Commands (`INFRX_D_TASK=g8`, in `apps/infrx-api` unless noted)
| cmd | exit | result |
|---|---|---|
| `make api-env` (worktree root) | 0 | pinned env |
| `uv run --frozen --no-sync python -m tests.g.ops.mutants <13 flag mutants>` | 0 | 13/13 killed |
| `uv run --frozen --no-sync pytest -q tests/g/ops` | 0 | 94 passed (fake + PG) |
| `INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/g/ops/test_mutants.py` | 0 | 115 passed (109 mutants killed, was 102; list well-formed, every case covered, runner self-tests) |
| `uv run --frozen --no-sync ruff check infrx/operations tests/g/ops` | 0 | All checks passed |
| `python3 research/plan/scripts/validate_plan.py` (worktree root) | 0 | PASS (4 lines) |
| `git diff --stat 44ba44a9..223a3bd` | 0 | 4 files, owned paths only |

## Wiring requests
- None required. Optional doc follow-up (not owned): `infra/app/README.md` / operations docs could name `flag --name signup_grant --dry-run` as the read-only check; nothing asserts it today.

## Deviations
- The `--on`/`--off` requirement moved from argparse to `dispatch` (exit 1 message instead of argparse exit 2), so the dry run can omit it; the dry-run-with-direction output adds `enabled_after`/`changed` flat to the row.

## Estimate
Remaining (coordinator merge): optimistic 0.1 h, likely 0.2 h, pessimistic 0.5 h; confidence high; basis: 4 owned files, no wiring, checks green.
