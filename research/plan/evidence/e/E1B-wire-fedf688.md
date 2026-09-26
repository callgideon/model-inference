# E1B-WIRE — WR-1…WR-4 from E1B-PREP, wired (no measurement)

- Lane: E1B-WIRE (task E1B, window wiring; the cells run in the E4C window). Branch `codex/e1b-wire`.
- Base `387cac69` (E1B-PREP merged). Code head `fedf6888`; this file and the update file are committed on top of it.
- No GPU, no box, no AWS/SSM, no hosted Supabase, no paid call. Task-local docker `INFRX_D_TASK=w5` only.

## 1. Changed paths (all owned)

| Path | Wiring |
|---|---|
| `models/marlin2b/bench.py` (`_send`, 3 lines) | WR-3 |
| `models/marlin2b/tests/test_bench.py` | WR-3 case |
| `apps/infrx-api/infrx/worker/loop.py` (a `metrics` field + the `observe_phases` call) | WR-4 |
| `apps/infrx-api/tests/w/test_loop.py`, `tests/w/loop_mutants.py` | WR-4 case + 2 mutants |
| `models/marlin2b/profiles/E1B-{direct,box,box.forms,sop,edge.two-tenant}.base.json` (new) | WR-1 |
| `models/marlin2b/tests/test_profile.py` | WR-1/WR-2: §7.2 lines validated as written |
| `infra/rollout/e1b-window.sh` (new) | WR-2 |
| `apps/infrx-api/tests/i/test_rollout.py` | WR-2 flag and precondition pins |
| `models/marlin2b/results/E1B-protocol.md` §7.2 (2 lines) | WC-9's `--profile` names its filled file; one sentence naming the launcher |

## 2. Per wiring: failed before, passes after, mutants

**WR-3** (`bench.py _send`): `--target direct` adds `payload["stop_token_ids"] = [248044, 248046]`
(`infrx/worker/engine.py:158` `MODEL_EOS_TOKEN_IDS`, `serving-version.json` `eos_token_ids`); the
gateway payload carries neither (its parameter set is closed).
- Case `test_the_direct_leg_supplies_both_eos_ids_and_the_gateway_leg_neither` (reads the ids from
  `serving-version.json`; captures bodies with the fake gateway's `chat_override`).
- Before: FAILED, `assert [None, None] == [[248044, 248046], [248044, 248046]]` (direct). After: passed.
- Mutants through `models/marlin2b/tests/mutants.py` `run_one` (ad hoc, not added to its list: that
  file is outside this lane's paths; see WREQ-2): line dropped → killed; one id `[248046]` → killed;
  ids also on the gateway leg → killed (3/3, each "1 failed").

**WR-4** (`worker/loop.py`): after `result = await self.runner.run(...)`, a `WorkerLoop` holding the
worker `Registry` calls `metrics.observe_phases({k: v / 1000 for k, v in result.timings.items()})`
(attempt timings are ms; `infrx_phase_seconds` takes seconds). `metrics` defaults to `None`, so every
existing construction is unchanged.
- Case `test_ops_recover__the_loop_reports_each_attempts_phase_timings_in_seconds`: a clock that
  advances 1 ms per reading, the real adapter over the fake upstream, one candidate; the attempt times
  exactly `prefill, generate, journal, persist, settle`; each has `infrx_phase_seconds_count` 1 and a
  `_sum` equal to its ms / 1000.
- Before: FAILED, `TypeError` (WorkerLoop takes no `metrics`). After: passed; `tests/w/test_loop.py` 47 passed.
- Mutants in `tests/w/loop_mutants.py`: `phase_timings_never_observed` (call → `pass`) killed;
  `phase_timings_in_milliseconds` (`result.timings` unscaled) killed.
- **Not live until WREQ-1** (the composition root passes `metrics=rt.metrics`).

**WR-1** (profile bases, `models/marlin2b/profiles/`):
- `E1B-direct.base.json`: `direct-engine`, allowlist `127.0.0.1:8000`, closed-loop base (the launcher
  stamps `-c` 1/2/4/8 for WC-1 and the rates 0.5/2.0 for WC-2), `max_concurrency` 8 (the pinned
  `max_num_seqs`), 135 requests. **`expect_model`:** bench sets it from `identity.model_revision`,
  and the direct stream reports the engine's served name, so this base declares
  `model_revision: "marlin2b"` (`serve.sh --served-model-name marlin2b`); the test pins that equality.
  Spend: `currency` CREDIT with `rates`/`max_spend` null (engine direct, unmetered, 0 CREDIT); the
  loopback direct-engine target is local, so the block reads as a warning only.
- `E1B-box.base.json` (WC-3/4/7, forms `[video_b64]`) and `E1B-box.forms.base.json` (WC-5, forms
  `[upload, video_b64]`): `direct-gateway` `127.0.0.1:8001`, open-loop 0.5, `max_requests` 135,
  `max_spend` 1,824.768 CREDIT (= 135 × 13.5168).
- `E1B-sop.base.json` (WC-8): the 9 in-cap `sop-synth-v1` ids, closed-loop c = 1, forms `[upload]`,
  `max_tokens_mix` `[1024]`, `max_spend` 121.6512, `manifest_sha256`
  `5e2b71ba…d1f9` = the items JSONL the launcher writes (deterministic: sorted keys, `../corpus/`
  paths relative to `/out`).
- `E1B-edge.two-tenant.base.json` (WC-9): `public-edge` `marlin2b.callbill.ai`, `tenants` 2
  (`INFRX_API_KEY`, `INFRX_API_KEY_B`), open-loop 0.5, `max_requests` 135, `max_spend` 1,824.768
  CREDIT, P5, tenant-B prefix FILL.
- All five: `measurement.price` USD 2.24208/h citing `cloud-pricing.md` §3.1 (WR-1 c), spend in
  CREDIT only; identity FILLs as `E4C-box.base.json` (runbook §3 fills them).
- sha256: direct `80357ef7…52f5`, box `91abe6db…2b108`, box.forms `c6dda4d2…fcba`, sop
  `9784861c…6aba`, two-tenant `b3c0b2fc…2c7f`.

**WR-2** (`infra/rollout/e1b-window.sh`): strict bash, `RELEASE` required; cells run in the certify
image, one `--rm` container `infrx-e1b-<cell>` each, `-e CORPUS_CACHE` by name, repo/corpus/e4c
mounted read-only; the tenant key file only for gateway cells (never to the engine). Each cell's
filled base is copied and stamped from the cell's own command (run_id `<base>-<cell>`,
`dataset_version`, open-loop rate or closed-loop concurrency), as `certify.cell_profile` does.
Order: WC-1 → WC-2 → WC-3 → WC-4 → WC-5 → WC-8 (default `CELLS`); WC-7 only alone (`CELLS=WC-7`, right
after WC-6a's `restored=yes`). WC-6a/6b stay the l8 copies (§7.2). WC-8: writes the items JSONL, runs
`dataset.py`, sends SIGINT after 3 `done` (read from the SQLite journal), reruns the same command,
exports. Before every cell it refuses (exit 2, no container started) when the engine container's
args lack `"--max-num-seqs","<filled pin>"`, when any `infrx-certify:*` container other than a cell
exists (certify live), or when an `infrx-e1b-*` container is left. `DRY_RUN=1` stamps the copies and
prints the plan; docker is never called.

§7.2 changed only where a command must match the launcher: WC-9's `--profile <two-tenant perf base,
WR-1>` is now `--profile ~/e4c/E1B-two-tenant.json`, and one sentence says the launcher runs WC-1…5,
WC-8 and WC-7 alone and fills each line's `…` (`$O` = `/out`). No other §7 line changed; §1–§6 untouched.

Tests (new files and bases, so "before" = absent):
- `test_profile.py::test_every_section7_command_is_the_launchers_and_validates_against_its_filled_base`:
  parses §7.2 (unrolls the loops, expands `$BOX`/`$DIRECT`/`$M`/`$O`), runs the launcher `DRY_RUN=1`
  on filled bases, requires the launcher's 11 commands to equal §7.2's token for token (minus the
  `…` fills), validates each bench line `--validate-only` against its stamped copy (0 errors; gateway
  cells: no blocks/warnings, CREDIT projection = n × 13.5168 = §7.2's ceiling column; direct cells:
  `expect_model` = served name, no projection), validates WC-8 through `dataset.check_profile`
  (media stand-ins at `/out/../corpus` sizes), and WC-9's line as written against the filled
  two-tenant base with a two-prefix `keys-journey.json`. Every base: CREDIT spend, USD only in the price.
- `test_rollout.py`: `e1b-window.sh` added to the strict-bash/no-secret list;
  `test_e1b_window__cells_run_in_order_one_container_each_with_only_parser_flags`;
  `test_e1b_window__refuses_a_cell_that_would_overlap_or_run_off_the_pinned_engine`.
- Ad hoc mutants (edit, run, restore), 24/24 killed:
  - on the §7 test (13): direct identity = gateway revision; rate / concurrency / dataset_version not
    stamped; WC-2 without `--engine-state warm`; WC-5 on the video_b64 base; WC-8 out of the order;
    SOP item budget 512; box cap 1,824; box spend in USD; two-tenant base with 1 tenant; §7.2 WC-3
    burst 16; §7.2 WC-3 ceiling off.
  - on the rollout pins (11): no preflight before a cell; certify-live check dropped; left-container
    check dropped; engine pin not checked; key handed to the engine; `CORPUS_CACHE=/corpus` on argv;
    container not `--rm`; WC-7 allowed with other cells; WC-4 before WC-3; DRY_RUN calling docker;
    bench profile not the stamped copy.

## 3. Commands

| Command | Exit | Result |
|---|---|---|
| `make api-env` | 0 | pinned env synced |
| `cd apps/infrx-api && uv run --frozen --no-sync pytest -q ../../models/marlin2b/tests` | 0 | 118 passed (116 + 2 new; includes the protocol pin) |
| `INFRX_D_TASK=w5 uv run --frozen --no-sync pytest -q tests/w` | 0 | 331 passed, 10 skipped (341 collected; skips: local S3/MinIO or root-only cases) |
| `INFRX_D_TASK=w5 INFRX_MUTANTS=all uv run --frozen --no-sync pytest -q tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/w/test_w4_mutants.py tests/w/test_worker_main_mutants.py tests/w/test_prep_worker_mutants.py tests/w/test_w5_mutants.py` | 0 | 600 passed, 15 skipped in 2,806 s (skips: 7 + 8 PG+S3 mutants, no local S3 endpoint); the two new loop mutants killed |
| `uv run --frozen --no-sync pytest -q tests/i/test_rollout.py` | 0 | 14 passed |
| `bash -n infra/rollout/e1b-window.sh` | 0 | — |
| `DRY_RUN=1` on a scratch box tree (filled bases copied from the committed ones) | 0 | 12 plan lines (WC-1 ×4, WC-2 ×2, WC-3, WC-4, WC-5, WC-8 run/resume/export); `CELLS=WC-7`: 1 line; items JSONL sha256 = the SOP base's pin |
| `python3 research/plan/scripts/validate_plan.py` | 0 | all PASS (931 links, 273 documents) |
| `git diff --stat 387cac69..HEAD` | 0 | owned paths only |

## 4. Wiring requests (not applied)

- **WREQ-1 (worker composition root, `apps/infrx-api/infrx/worker/__main__.py:162`).** Without it WR-4
  is dead code on the box and journal/persist/settle stay `declared_missing`:
  ```diff
  -    loop = WorkerLoop(scheduler=scheduler, runner=runner, worker_id=worker_id, limits=limits)
  +    loop = WorkerLoop(scheduler=scheduler, runner=runner, worker_id=worker_id, limits=limits,
  +                      metrics=rt.metrics)
  ```
  Test (`tests/w/test_worker_main.py`): `service, _ = composed(environment(tmp_path)); assert
  service.loop.metrics is service.metrics` (the preparation loop keeps `metrics=None`: its runner
  has no attempt timings). Mutant for `worker_main_mutants.py`: drop `, metrics=rt.metrics`.
- **WREQ-2 (E1B mutation list, `models/marlin2b/tests/mutants.py`).** Add the three WR-3 mutants above
  (select `eos_ids`, case `test_the_direct_leg_supplies_both_eos_ids_and_the_gateway_leg_neither`).
- **WREQ-3 (E4C runbook owner, `models/marlin2b/results/E4C-runbook.md` §3/§5.0).** Fill
  `/opt/dlami/nvme/e4b/e4c/E1B-{direct,box,box-forms,sop}.json` from the four box bases with the
  §2 identity and `maintenance_window` (as `E4C-box.json`), and `~/e4c/E1B-two-tenant.json` from
  `E1B-edge.two-tenant.base.json` in §5.0 (tenant-B prefix). Run the launcher after §4:
  `TIMEOUT_S=7200 infra/rollout/ssm.sh infra/rollout/e1b-window.sh RELEASE=<sha>`, then WC-6a, then
  `… CELLS=WC-7`, then WC-6b. The certify image must carry `dataset.py` and `bench.py` at the release
  (it is the release checkout, mounted read-only at `/repo`).

## 5. Open issues

- Deviation: two box bases, not one. §7.1 rule 2 makes a different form set a new base, and the
  launcher stamps only run_id/dataset_version/rate/concurrency, so WC-5's `[upload, video_b64]` is
  `E1B-box.forms.base.json` beside `E1B-box.base.json` (WC-3/4/7).
- The WC-8 interrupt reads the SQLite journal from the host with `python3`; if the box's host
  python lacks `sqlite3`, the loop sees 0 done and the run completes uninterrupted (logged as "not
  interrupted"; the cell then shows no resume and MARLIN-SOP's interrupt half is NOT RUN).
- WC-0 (sidecar), WC-6a/6b (l8 copies) and WC-9 (coordinator host) are not in the launcher, as §7.2
  places them. The URL form of WC-5 stays BLOCKED on `MEDIA_BASE_URL`.
- The launcher runs in the foreground (about 45–60 min, `est.` from §7.2's wall column): SSM needs
  `TIMEOUT_S` ≥ 7200 and keeps only 24,000 characters; each cell's log is `$out/<cell>.log` and
  `$out/cells.tsv` records every exit.

## 6. Remaining effort (E1B through the window)

Optimistic 3 h, likely 5 h, pessimistic 9 h. Confidence: medium. Basis: WREQ-1..3 about 0.5 h; the
window cells 1.0–1.6 h of the E4C window (§7.2, schedule-derived `est.`); post-window analysis 2–4 h;
pessimistic adds one pair re-run and a launcher fix found on the box.
