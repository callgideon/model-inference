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
