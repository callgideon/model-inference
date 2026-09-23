# W-new — Consumed request parameters are neither refused nor forwarded

| Field | Value |
|---|---|
| Task | W-new (track W), G2 integration request 2, oracles API-STREAM / API-MODES |
| Status | **implemented** (fakes and `httpx.MockTransport` only: no engine, no GPU, no network, no Docker) |
| Owner / session | Claude Opus 5.5, worktree `.claude/worktrees/codex-wnew` |
| Base SHA | `0b10a54` (`claude/backend-impl`) |
| Implementation SHA | `e87fa69` (branch `codex/w-consumed-parameters`) |
| Integrated SHA | none — the coordinator integrates |

## What and why

`VllmEngine.check_parameters` (`apps/infrx-api/infrx/worker/engine.py`) refused `stream`,
`max_tokens` and `max_completion_tokens`. The frozen fixture
`infrx/contracts/fixtures/v1/normalized_request.json` carries `max_tokens` and `stream` in
`parameters`, and G1R's validator (`infrx/gateway/routes/validate.py`, `normalize`) copies
every `SUPPORTED` body key except `model`/`messages` into `parameters`. So every validated
streaming or capped request raised `UnsupportedParameter` at the engine and settled
`platform_error`. Reproduced at `0b10a54`: the fixture through `prepared_request` +
`upstream_body` raised `unsupported_parameter: max_tokens is not supported`.

Fix, as G2 proposed in `research/plan/evidence/g/G2-e5e7d3a.md` (request 2):
`CONSUMED_PARAMETERS = {"stream", "max_tokens", "max_completion_tokens"}`. The loop skips them,
so they are never refused and never forwarded. They are removed from `REFUSED_PARAMETERS`.
`stream_options` and `model` stay refused.

## Where the cap reaches the engine

These keys are safe to consume because the record already carries their effect in typed fields:

1. **Validator.** `Ingress.ceilings` takes `max_tokens` or `max_completion_tokens`, refusing a
   disagreement between them. The ceiling is `min(limits.max_output_tokens, deployment.max_output_tokens)`,
   where `MAX_OUTPUT_TOKENS` is 2048 (08 §5). A value outside `1..ceiling` is refused as
   `invalid_request` (it is not clamped). An absent value defaults to the ceiling. The result
   becomes `NormalizedRequest.max_output_tokens`. `stream` becomes `execution_mode` (`execution_mode()`).
2. **Admission.** `admit` range-checks `1 ≤ max_output_tokens ≤ MAX_OUTPUT_TOKENS` (r1 R55).
3. **Worker.** `prepared_request` copies `request.max_output_tokens` into
   `PreparedRequest.max_output_tokens`. `output_ceiling` validates it against
   `1..limits.max_output_tokens` and the context. `upstream_body` sends it as the engine's
   `"max_tokens": ceiling` next to `"stream": True`. The adapter enforces it on the way back
   (`stream.deltas > ceiling`, `usage.completion_tokens > ceiling`: `EngineProtocolViolation`).

The body is built as `{..., "max_tokens": ceiling, "stream": True, ..., **forwarded}`. A
forwarded consumed key would therefore **override** the record's ceiling and the worker's
streaming mode. That is why these keys are skipped and not passed through. For accepted
requests, the engine's `max_tokens` equals the caller's `max_tokens`, which is at most
`min(MAX_OUTPUT_TOKENS, deployment)`, through the record.

## Tests and mutants

- `test_api_stream__unsupported_options_are_refused_explicitly`: the refused set no longer
  contains `max_tokens`/`stream`, and `stream_options` is added. `{stream: False, max_tokens: 4096,
  max_completion_tokens: 4096}` with a record ceiling of 256 is accepted. The body gets
  `stream is True`, `max_tokens == 256` and no `max_completion_tokens`.
- New `test_api_stream__the_frozen_normalized_request_reaches_the_engine_capped`: the frozen
  v1 fixture is run through `prepared_request` and `upstream_body` with an M-prepared ref for its
  upload. It is accepted with `max_tokens == 256 == parameters.max_tokens == max_output_tokens`,
  `stream is True`, `temperature == 0.2` and `n == 1`. With the record's ceiling lowered to 64, the
  engine gets 64.
- Mutants added to `tests/w/mutants.py` (W1's list, owner of `worker/engine.py`):
  - `consumed_parameters_refused`: drops `or name in CONSUMED_PARAMETERS`. Killed by the two
    cases above. Both use `accepted()`, so the kill is an assertion and needs no `dies_by`.
  - `consumed_parameters_forwarded`: forwards the consumed keys. Killed by the options case,
    through the record's ceiling and `stream=True`.

## Runs (from `apps/infrx-api`, at `e87fa69`)

```
$ uv run --frozen python -m tests.w.mutants consumed_parameters_refused consumed_parameters_forwarded unknown_parameters_forwarded
[killed       ] unknown_parameters_forwarded: 1 failed, 50 deselected in 1.42s
[killed       ] consumed_parameters_refused: 2 failed, 49 deselected in 1.55s
[killed       ] consumed_parameters_forwarded: 1 failed, 50 deselected in 1.44s
3/3 killed

2026-09-23T10:08:21Z
$ uv run --frozen pytest -q -p no:cacheprovider tests/w tests/g
525 passed, 2 warnings in 285.91s (0:04:45)
exit=0

$ uv run --frozen pytest -q -p no:cacheprovider tests/contracts
3 failed, 1048 passed in 126.77s (0:02:06)
exit=1
```

The three failures are all in `tests/contracts/v2/test_v1_projection_pg.py`, which needs
the Docker PostgreSQL harness. Each one raised `tests.d.pgharness.HarnessBusy`: another run
(`codex-e3b2`, pid 993396) held `/tmp/infrx-d1-postgres-55432.lock`. Nothing was altered, and
this lane does not use Docker. They are unrelated to `worker/engine.py`. Rerun without that file:

```
2026-09-23T10:15:28Z
$ uv run --frozen pytest -q -p no:cacheprovider tests/contracts --ignore=tests/contracts/v2/test_v1_projection_pg.py
1048 passed in 150.70s (0:02:30)
exit=0
```

```
2026-09-23T10:15:16Z
$ INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py
320 passed in 1221.91s (0:20:21)
exit=0
2026-09-23T10:35:39Z
```

## Left for others

- G2: replace `engine=RecordKeysDropped(upstream.engine())` with `engine=upstream.engine()`
  in `tests/g/relay_support.py` and delete the shim. That file is only on G2's branch.
- Not run: `tests/d`, `tests/q` and `test_v1_projection_pg.py` (Docker).

## Verification log

- 2026-09-23: created at `e87fa69`; runs above.
