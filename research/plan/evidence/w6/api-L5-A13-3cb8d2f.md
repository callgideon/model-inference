# W6 api-L5 — A13: pyright configuration, baseline, worker/service typing

Lane api-L5, branch `codex/w6-api-L5`, base `2add8e0a`, commit `3cb8d2fc`.

## Config (`apps/infrx-api/pyproject.toml` `[tool.pyright]`)

`include = ["infrx", "deploy"]`, `pythonVersion = "3.12"`, `typeCheckingMode = "basic"`,
`venvPath = "."`, `venv = ".venv"`, `reportInvalidTypeForm = false` (pydantic `RefOf(...)`
call annotations). Runner: `uvx pyright@1.1.414` (no lockfile change).

## Invocations and memory (this host, 16 cores, load ~30 from parallel lanes)

| invocation (from `apps/infrx-api`) | exit | wall | max RSS | result |
|---|---|---|---|---|
| `uvx pyright@1.1.414` (whole tree, the config's `include`) | 1 | 26 s | 0.74 GB | 508 errors, 3 warnings — no OOM |
| `uvx pyright@1.1.414 infrx/<pkg>` per package (20 runs, below) | 0/1 | 4–21 s each | — | counts below |
| `make -f <wiring lines> api-typecheck` (the requested target) | 0 | 26 s | — | `pyright: 508 errors (baseline 508)`; with `API_PYRIGHT_BASELINE=507` make exits 2 |

The exit-250 OOM the audit recorded came from a run without this config (no `include`, so
node walks `.venv`); with the config the whole tree fits, so the requested target is one run
gated on the total. A package is still checkable alone with `uvx pyright@1.1.414 infrx/<pkg>`.

## Baseline (errors, basic mode)

| package | before (2add8e0a + config) | after (3cb8d2fc) |
|---|---|---|
| infrx/auth | 0 | 0 |
| infrx/content | 3 | 3 |
| infrx/contracts (frozen) | 182 | 182 |
| infrx/datasets | 6 | 6 |
| infrx/evaluation | 14 | 14 |
| infrx/gateway | 71 | 71 |
| infrx/harnesses | 2 | 2 |
| infrx/judge | 11 | 11 |
| infrx/lab | 52 | 52 |
| infrx/media | 26 | 26 |
| infrx/observe | 26 | 26 |
| infrx/operations | 13 | 13 |
| infrx/pipelines | 11 | 11 |
| infrx/rollouts | 7 | 7 |
| infrx/scheduling | 0 | 0 |
| infrx/state | 39 | 39 |
| infrx/traces | 16 | 16 |
| infrx/worker | 36 | **17** |
| infrx/*.py | 2 | 2 |
| deploy | 10 | 10 |
| **total** | **527** | **508** |

(The audit's figures — gateway 101, state 41 — were measured without the config's
`reportInvalidTypeForm=false` and venv.) api-L3 (state) and api-L4 (lab routes) lower their
rows this wave; the coordinator lowers `API_PYRIGHT_BASELINE` at merge.

## The worker fixes (19 errors; annotation-only or provably equivalent)

- `service.py`: `jobs: ReapedStore` (`ports.JobStore` + the sweep's `unsettleable`, a
  `Protocol` declared under `TYPE_CHECKING`), `engine: Engine`, `metrics: Registry | None`,
  `pool: Any | None`, `reconciliation: Callable[[], Awaitable[tuple[int, int]]] | None`,
  `housekeeping: dict[str, Callable[[], Coroutine[Any, Any, None]]]`,
  `_housekeeping: list[asyncio.Task]`. All typing imports are `TYPE_CHECKING`-only and the
  module has `from __future__ import annotations`: nothing new runs.
- `service.py` `serve()`: `pool, reaper, preparation` read once after `start()` with
  `assert pool is not None and reaper is not None` (start() creates both or raises); the
  preparation wait reads `preparation is not None` beside `self._preparing is not None`
  (set together in `start()`); `_preparation_tasks()` returns `()` when either is None.
- `engine.py`: the three async generators return `AsyncGenerator[EngineEvent, None]`
  (their `aclose()` is now typed).
- Left in the worker baseline (17): `__main__.py` 6 and `loop.py` 3 (api-L1's / unowned),
  `service.py:157` (`loop.scheduler` is `object` in `loop.py`), `engine.py:952-955` and
  `engine_wire.py:251-253` (narrowing inside mutation-anchored usage checks; a rewrite there
  is a logic edit this behaviour-preserving wave does not need), `spool.py:724-775` (the
  writer's `segment.fd` Optional, same reason).

Anchors: six W3/prep-worker mutants named `self._pool`/`self._reaper` in `serve()`; each
re-pointed to the same edit on the narrowed names (`serve_ignores_a_dead_reaper`,
`serve_waits_only_for_a_signal`, `serve_waits_for_the_whole_pool`, `runner_death_unlogged`,
`pool_death_unlogged`, prep `_WAIT`); checker 677/0 bad; the lists ran green (A12 file).

No new test: the typing changes add no decision (the assert is unreachable after a
successful `start()`), so the oracle is pyright's count plus the unchanged suites
(`tests/w/test_service.py test_prep_worker.py test_worker_main.py` on w5: 119 passed,
7 skipped).

## Wiring request: `Makefile` (owner makefile-pins)

Append after the `api-mutants` recipes (tabs in recipes; `API := apps/infrx-api` exists):

```make
# A14/A13 (W6 api-L5): ruff and pyright pinned through uvx; config in $(API)/pyproject.toml.
# The pyright gate is the 2026-10-01 baseline: lower it as errors are fixed, never raise it.
API_PYRIGHT_BASELINE := 508

api-lint:
	@grep -q '^\[tool.ruff\]' $(API)/pyproject.toml || { echo "api-lint: no [tool.ruff] in $(API)/pyproject.toml yet (api-L5)"; exit 0; }
	cd $(API) && uvx ruff@0.15.12 check

api-typecheck:
	@grep -q '^\[tool.pyright\]' $(API)/pyproject.toml || { echo "api-typecheck: no [tool.pyright] in $(API)/pyproject.toml yet (api-L5)"; exit 0; }
	cd $(API) && n=$$(uvx pyright@1.1.414 --outputjson | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["summary"]["errorCount"])') && echo "pyright: $$n errors (baseline $(API_PYRIGHT_BASELINE))" && [ "$$n" -le $(API_PYRIGHT_BASELINE) ]
```

and wire them in: `.PHONY` gains `api-lint api-typecheck`; line 160 becomes
`check: api-test api-mutants api-lint api-typecheck console-test …` (the rest unchanged).
Proof: these lines in a scratch makefile from the repo root — `api-lint` exit 0 ("All
checks passed!"), `api-typecheck` exit 0 ("pyright: 508 errors (baseline 508)"), and exit 2
with `API_PYRIGHT_BASELINE=507`. The grep guard keeps both targets a visible no-op on a tree
without the config.

## Estimate (remaining for A13)

0 / 0.5 / 2 h (confidence medium): the baseline number moves with the other lanes' merges.

## Fix round (2026-10-01, finding 1-L5-INT-1; head e751e42d)

Finding: merged onto claude/consumer-v1, the api-lint/api-typecheck recipes that makefile-pins
landed there (3fdd62e0, 945139f3) switch on with this lane's config and fail: `uv run --frozen
ruff|pyright` (neither is in uv.lock), a per-package pyright loop with no baseline gate, and a
UP031 in `tests/i/test_rollout_host.py` (added after 2add8e0a).

Owned-path fix (e751e42d): `[tool.ruff.lint.per-file-ignores]` gains
`"tests/i/test_rollout_host.py" = ["UP031"]` (same baseline treatment as the other 98 files).
Makefile and uv.lock are not this lane's (LANE-RULES 2): the recipe fix is the wiring request
below, now written as a diff against the landed recipes (the if/else guard kept).

Merged-tree oracle: `git merge-tree --write-tree claude/consumer-v1(db2f3445) 9ef4aa93` =
995aae2e (clean), `git archive` of apps/infrx-api + Makefile into the scratchpad, lane .venv
symlinked.

| command (merged tree, from apps/infrx-api unless noted) | exit | result |
|---|---|---|
| `uvx ruff@0.15.12 check` with 9ef4aa93's pyproject | 1 | RED: `tests/i/test_rollout_host.py:98:17 UP031` (the only finding) |
| same with e751e42d's pyproject | 0 | All checks passed! |
| `uvx ruff@0.15.12 check` in the lane tree (e751e42d) | 0 | All checks passed! |
| `uvx pyright@1.1.414 -p pyproject.toml --outputjson` | 1 | 182 files, 508 errors, 3 warnings; 13.5 s, 0.77 GB RSS (consumer-v1 added no infrx/deploy file since the base: baseline 508 holds) |
| root: `make api-lint` (merged Makefile + the diff below) | 0 | All checks passed! |
| root: `make api-typecheck` | 0 | `pyright: 508 errors (baseline 508)` |
| root: `make api-typecheck API_PYRIGHT_BASELINE=507` | 2 | `pyright: 508 errors (baseline 507)`, Error 1 — the gate bites |

`-p pyproject.toml` is new versus the first request: without it pyright picks up a
`pyrightconfig.json` in any parent directory (it did in the scratchpad, exit 1, empty JSON).

Affected suites: the change is a ruff-config line only (no runtime/test code); the ruff and
pyright runs above are the affected checks. Mutant lists: unaffected (no anchor names a
pyproject line); last green run is in the A12 file.

### Wiring request (replaces the first one): `Makefile` (owner makefile-pins / coordinator at merge)

Against claude/consumer-v1's Makefile (db2f3445); `check` and `.PHONY` already name both targets.

```diff
--- a/Makefile
+++ b/Makefile
@@ -12,20 +12,24 @@
 api-test:
 	cd $(API) && uv run --frozen pytest -q
 
-# W6 (A12/A14): ruff and pyright over apps/infrx-api, enabled by api-L5's config. Until it lands
-# each reports "not run" rather than a pass, as bench-test does. pyright runs one package
-# per process (then the top-level infrx/*.py modules in one): a whole-tree run OOMs node here
-# (exit 250, audit A13).
+# W6 (A12/A14): ruff and pyright over apps/infrx-api, enabled by api-L5's config, pinned through
+# uvx (neither is in uv.lock). Until the config lands each reports "not run" rather than a pass.
+# pyright runs once over the config's `include` (infrx, deploy; ~0.8 GB RSS): the exit-250 OOM
+# (audit A13) was an unscoped run walking .venv. The gate is the 2026-10-01 error baseline:
+# lower it as errors are fixed, never raise it.
+API_PYRIGHT_BASELINE := 508
+
 api-lint:
 	@if [ -f $(API)/ruff.toml ] || grep -q '^\[tool\.ruff' $(API)/pyproject.toml; then \
-		cd $(API) && uv run --frozen ruff check infrx deploy tests; \
+		cd $(API) && uvx ruff@0.15.12 check infrx deploy tests; \
 	else \
 		echo "api-lint: not run - no ruff config in $(API) yet (api-L5 owns it)"; \
 	fi
 
 api-typecheck:
 	@if [ -f $(API)/pyrightconfig.json ] || grep -q '^\[tool\.pyright' $(API)/pyproject.toml; then \
-		cd $(API) && rc=0; for pkg in infrx/*/; do [ "$$pkg" = infrx/__pycache__/ ] && continue; uv run --frozen pyright "$$pkg" || rc=1; done; uv run --frozen pyright infrx/*.py || rc=1; exit $$rc; \
+		cd $(API) && n=$$(uvx pyright@1.1.414 -p pyproject.toml --outputjson | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["summary"]["errorCount"])') && \
+		echo "pyright: $$n errors (baseline $(API_PYRIGHT_BASELINE))" && [ "$$n" -le $(API_PYRIGHT_BASELINE) ]; \
 	else \
 		echo "api-typecheck: not run - no pyright config in $(API) yet (api-L5 owns it)"; \
 	fi
```

Alternative (uv.lock owner): ruff==0.15.12 and pyright==1.1.414 in the dev group, then
`uv run --frozen` in place of `uvx …@…`; the baseline gate is needed either way.

Estimate (remaining for the lane): 0 / 0.25 / 1 h (confidence medium): applying the diff at
merge; the baseline moves only with other lanes' merges.

Correction (coordinator, merge #82, 0-L5-CORR-1): the pinned invocation is `uvx pyright@1.1.414 -p pyproject.toml` (from `apps/infrx-api`) everywhere this file says `uvx pyright@1.1.414`; without `-p` a parent directory's `pyrightconfig.json` hijacks the run. The `[tool.pyright]` comment in `apps/infrx-api/pyproject.toml` names the same form.

Correction (coordinator, merge #82, 0-L5-CORR-2): the `API_PYRIGHT_BASELINE` gate is on the total error count, so it allows trades between packages (one package's new errors hidden by another's fixes); the coordinator lowers `API_PYRIGHT_BASELINE` at every merge that lowers the count.
