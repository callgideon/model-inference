# E4C-RUNBOOK: edge and two-tenant profiles, E4P-V7/V8, the E4C window runbook

- **Base:** `b0cc5090`.
- **Branch:** `codex/e4c-runbook`.
- **Commits:**
  - `9d1ee515`: profiles, certify, tests and mutants.
  - `f2a245c0`: the runbook.
  - The evidence commit follows them.
- **Not touched:** no box, AWS, SSM, hosted DB, docker or secret. No key value was read or printed.
- **Decisions applied:** P-01, P-02, P-05, P-06, P-17, P-18, P-24 and P-25 (15 "Decisions 2026-09-25"), draft §P-17/§P-18/§P-24, R133, and E4C-PREP findings E4P-V7, E4P-V8, E4P-V9 and open issue 4.

## Changed paths

| path | change |
|---|---|
| `models/marlin2b/profiles/E4C-edge.overload.base.json` | new: the P4 public-edge overload profile |
| `models/marlin2b/profiles/E4C-box.two-tenant.base.json` | new: the P-17 check 5 two-tenant journey profile |
| `tests/integration/backend/certify.py` | E4P-V7 (`edge_host`, PENDING on PROFILE) and E4P-V8 (any remote target) in `load_cells` only |
| `tests/integration/backend/test_certify.py` | 3 new tests; `_edge_profile` helper; the two tiny-scale remote tests (E1C validity, CW-V1) now pass `--overload-profile` so they keep exercising the burst client (V8 blocks it otherwise) |
| `tests/integration/backend/e4b_mutants.py` | 3 new mutants; `box_burst_unblocked` re-anchored on the new gate line |
| `models/marlin2b/tests/test_profile.py` | 1 new test (two-tenant) |
| `models/marlin2b/results/E4C-runbook.md` | new: the runbook |

- **Schema:** unchanged. It already has a multi-tenant shape: `target.tenant_key_env` is a list, and `workload.tenants` is checked against bench's `--tenant-keys` (runprofile.py:148-153). So there is no schema change, schema test or runprofile mutant.
- **`E4C-box.base.json`:** byte-identical.

## sha256 of every profile (at f2a245c0)

```
c0d4aa1b9ef3f467ebb17b5d8bf6367a45507bf02685f2ebe8ddd9d279a0d2ff  E4C-box.base.json   (unchanged, as required)
886bb02bdbc2230e94c28f1c23d10d1aa1793a1f983a2ccc0de559f819e5be8b  E4C-box.two-tenant.base.json
7007d970321a7022d90a4c918f7183fdbfbe303f766d0a9f69ec9b7cbc213689  E4C-edge.overload.base.json
fd2d8241cbc351eb8c27b93caff10ef6048f06780e5417c4c9654a9651c799fd  P0-hosted-smoke.template.json
24f796d1315562c4e126c7b65cea7ed29b4dd700743c33fe1dbbce990fbda448  run-profile.v1.schema.json
3a2c05f5928cb53b9441037c193cc40c61c0d348a05ae924edda8677ed153581  smoke-corpus.json
```

## 1. The P4 public-edge overload profile

**What it is.** A copy of the base with only these fields changed:

- `run_id`: `e4c-edge-1`
- `target.path`: `public-edge`
- `allowlist`: `["marlin2b.callbill.ai"]`
  - This is the repository's public host: `apps/infrx-api/deploy/Caddyfile:12` `{$INFRX_SITE:marlin2b.callbill.ai}`, also `infra/rollout/verify-external.sh:12` and `verify-journey.sh:20`.
- `allowed_fault_targets`: `[]`. A burst injects no fault.
- `profile_class`: P4.
- The burst shape certify stamps: open-loop, `rate_per_s` 1000.0, 32 requests with `--burst 32` (`MATRIX["box"]["overload"]`), and `dataset_version` `e4c-1-overload`.
- `min_samples`: 32.
- `thresholds`: null (overload has no latency threshold).
- `client.location`: says the traffic goes through the edge.

**Bounds.**

| bound | value |
|---|---|
| `max_requests` | 32 |
| `max_duration_s` | 900 |
| `max_input_bytes` | 210,000,000. This is at least the whole 64-clip corpus (206,105,764 B). A 32-burst schedules 32 distinct clips; this seed's burst measures 119,745,973 B |
| `max_output_tokens` | 32,768 (32 × 1,024) |
| `max_concurrency` | 32 |
| per request | `max_input_tokens_per_request` 30,720 and `max_output_tokens_per_request` 1,024, the same as the base |
| `max_drain_s` | 600 |

**Spend (CREDIT; rates 400 / 1,200 per 1M from the P-01 card).**
- Per request: (30,720 × 400 + 1,024 × 1,200) / 10^6 = **13.5168**.
- Burst: 32 × 13.5168 = **432.5376**.
- Cap: `max_spend` **433** (the ceiling, rounded up to a whole CREDIT). Holds are 0.

**The FILL identities are the base's:**
- the 7 identity FILLs
- `maintenance_window`

**Validate-only**, with the exact stamped burst argv and `MARLIN_API_KEY` and `INFRX_API_KEY` unset:

```
bench.py --corpus models/marlin2b/corpus/manifest.json --subset full --base-url https://marlin2b.callbill.ai/v1 --target gateway --model nemostation/marlin-2b --rate 1000.0 --requests 32 --burst 32 --seed 20260922 --dataset-version e4c-1-overload --forms video_b64 --max-tokens 128,512,1024 --retries 0 --key-inventory <{"active_key_id_prefixes":["142c7d81"]}> --profile <p> --validate-only
```

- **As committed:** exit 2, with 13 errors, all on FILL fields:
  - 5 schema pattern errors, on `source_sha`, `deployed_sha`, `image_digest`, `weights_sha256` and `processor_sha256`
  - 8 FILL paths: those 5, plus `migration_version`, `config_version` and `maintenance_window`
- **Fully filled:** exit 0, `runnable`, no errors, blocks or warnings.
  - `profile_class` P4
  - `spend_currency` CREDIT
  - `projected_spend` "432.5376"
  - `media_bytes` 119,745,973
  - `output_token_ceiling` 17,280
- **Through certify** (the oracle is the new `test_e4c_the_committed_edge_profile_is_the_burst_certify_runs_once_frozen`, built on E4C-PREP's edge test):
  - The committed profile, stamped by `certify.bench_argv`, is refused only on FILL paths.
  - Frozen and passed as `--overload-profile` on the box target:
    - The overload cell runs at `https://marlin2b.callbill.ai/v1` and PASSes on the fake's honest refusals.
    - bench `--validate-only` on the exact burst argv gives exit 0 and runnable, P4, CREDIT, 432.5376 ≤ 433.

## 2. The two-tenant journey profile (P-17 check 5)

**Shape.** No schema change is needed (see Changed paths).
- `tenant_key_env`: `["INFRX_API_KEY", "INFRX_API_KEY_B"]`
- `workload.tenants`: 2
- `test_key_ids`: `["142c7d81", "FILL 8-12 hex: …the P-05 second-tenant key…"]`
  - The FILL is refused by the schema pattern and by the FILL rule.
- `path`: `public-edge`, `allowlist` `marlin2b.callbill.ai`. The journey is P-17's *external* client.
- `profile_class`: P0 (the smoke/journey class of 05 §4).
- Closed loop, concurrency 2.
- `forms`: `upload, upload, video_b64, video_b64`. With `tenant = i % 2`, each tenant gets both forms.
- `max_tokens_mix`: [128]
- `dataset_version`: `e4c-journey-1`

**Bounds.**

| bound | value |
|---|---|
| `max_requests` | 8 (4 per tenant) |
| `max_duration_s` | 900 |
| `max_input_bytes` | 210,000,000 (measured 50,356,386) |
| per request | 30,720 in / 128 out |
| `max_output_tokens` | 1,024 |
| `max_concurrency` | 2 |

**Spend (CREDIT).**
- Per request: (30,720 × 400 + 128 × 1,200) / 10^6 = **12.4416**.
- 8 requests: 8 × 12.4416 = **99.5328**.
- Cap: `max_spend` **100**. Holds are 0.
- Tenant B's worst case is 4 × 12.4416 = 49.7664 of its one 10,000 grant.

**Validate-only** (runbook §5 command, `--tenant-keys INFRX_API_KEY,INFRX_API_KEY_B`, inventory `["142c7d81", <B>]`):
- **As committed:** exit 2. Every error is on a FILL path: the 7 identity FILLs, `test_key_ids[1]` and `maintenance_window`. Six of them also have their pattern error.
- **Filled:** exit 0, runnable, no warnings, `projected_spend` "99.5328", `media_bytes` 50,356,386.
- **Negative controls** (test):
  - The run without `--tenant-keys` is refused: "workload.tenants is 2, the run uses 1".
  - With tenant B's prefix active but not in `test_key_ids`, it is refused: "key inventory: 1 active key(s) outside target.test_key_ids".

## 3/4. E4P-V7 and E4P-V8 (certify `load_cells`, the overload branch)

- **V8:** the gate is now `if not local:`.
  - Any remote target without `--overload-profile` reports `e4b.b.overload` PENDING on PROFILE: "BLOCKED: a remote overload burst is P4 … pass --overload-profile …".
  - The burst never starts.
  - This applies at every scale, not only the box.
- **V7:** a new `edge_host(path)` returns the first `target.allowlist` string, or None. It returns None when the profile has:
  - no `target` block
  - a non-object `target`
  - no `allowlist`, or an empty one
  - a non-string first entry
- A None host reports PENDING on PROFILE: "BLOCKED: --overload-profile names no public-edge host: it needs a target block whose target.allowlist is a non-empty list of hosts (E4P-V7)". There is no KeyError and no burst.
- Unreadable or wrong-schema edge profiles are still `profile_blocked`'s BLOCKED, as before.

## Fails-before

Method: the new tests run against the base `b0cc5090` code, from a `git archive` copy plus the new test files. For V7, the worktree was used before the fix.

| test | before | after |
|---|---|---|
| `test_e4c_the_committed_edge_profile_is_the_burst_certify_runs_once_frozen` | FAIL `FileNotFoundError` (no edge profile) | pass |
| `test_e4c_an_overload_profile_with_no_edge_host_is_blocked_never_a_crash` | FAIL `KeyError: 'target'` at certify.py:1380 (the crash E4P-V7 names); with the base code and the profile present, the same | pass |
| `test_e4c_any_remote_burst_without_an_edge_profile_is_blocked_not_run` | FAIL `AssertionError: {'stage': 'e4b.b.overload', 'status': 'PASS', 'detail': 'honest refusals'…}`: the tiny remote target ran the burst through its direct-gateway profile | pass |
| `test_the_e4c_two_tenant_profile_refuses_until_frozen_then_bounds_the_journey` | FAIL `FileNotFoundError` (no two-tenant profile) | pass |

## Mutants (new; all killed by assertion)

| mutant | edit | killed by |
|---|---|---|
| `remote_burst_unblocked_off_the_box` (V8) | gate back to `if not local and (edge or target["scale"] == "box"):` | E4P-V8 test |
| `edge_host_unchecked` (V7) | `edge_host` returns `str(hosts)`, so a missing host becomes "None" and the burst is sent to it | E4P-V7 test |
| `edge_cap_below_the_burst` (JSON) | `"max_spend": 433` becomes 432, which the 432.5376 burst exceeds | committed-edge test |
| `box_burst_unblocked` (re-anchored) | `if not local:` becomes `if not local and edge:` | E4C-PREP burst test |

A targeted run of these four plus `burst_off_the_edge` and `p4_stamp_dropped`: **6/6 killed**.

## Commands (at 9d1ee515 / f2a245c0)

| cmd | exit | result |
|---|---|---|
| `make api-env` | 0 | env synced |
| `make bench-test` | 0 | **115 passed** (114 + 1 new) |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_certify.py tests/integration/test_run.py` | 0 | **102 passed** (99 + 3 new) |
| `apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_e4b_mutants.py` (default subset) | 0 | 5 passed. The list is well formed and every case is covered, including the 3 new ones |
| `e4b_mutants.py box_burst_unblocked remote_burst_unblocked_off_the_box edge_host_unchecked edge_cap_below_the_burst burst_off_the_edge p4_stamp_dropped` | 0 | 6/6 killed |
| `INFRX_MUTANTS=all apps/infrx-api/.venv/bin/python -m pytest -q tests/integration/backend/test_e4b_mutants.py` (detached, setsid nohup, log `/tmp/claude-1000/e4c/e4b-mutants.log`) | running | at handback about 56 % done with 0 failures (every mutant so far killed); the final count was not captured before handback. The coordinator reruns it or reads the log |
| `apps/infrx-api/.venv/bin/python models/marlin2b/tests/mutants.py` | 0 | 116 mutants: 113 killed, 3 controls survived, 0 not killed, 0 problems (unchanged list) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (4 lines; 932 local links) |
| bench `--validate-only` edge, as committed / filled | 2 / 0 | 13 FILL-only errors / runnable, 432.5376 CREDIT |
| bench `--validate-only` two-tenant, as committed / filled | 2 / 0 | FILL-only errors / runnable, 99.5328 CREDIT |
| `sha256sum models/marlin2b/profiles/*.json` | 0 | the base is `c0d4aa1b…d2ff`, unchanged |

## 5. The runbook

`models/marlin2b/results/E4C-runbook.md`:
- the rules, carried verbatim
- the ordered steps before the window: P-18 timestamp, P-06, P-01, the P-02 dry-run, the operator-held revocation, the P-05 tenant, the +40,000 adjust `adj-e4c-20260925`, and the key inventory
- the window: rollout.md W1–W13, the edge open, the box-to-edge health check, O3–O6
- the freeze, with each FILL field and where its value comes from
- the fill and validate commands
- the one certify invocation, carrying `--run-profile`, `--key-inventory` and `--overload-profile`, and the cell table
- the two-tenant journey legs
- the drill record format, with each measured time next to its §5 bound and PASS/FAIL
- the evidence layout
- the P-17 ten-check tick-off with where each proof lives

Every operator-held step is marked.

## Wiring requests

**WR-E4C-RB-1.** `research/plan/consumer-v1/03-operations-and-verification.md` is coordinator-owned. Append one line after the existing "Runbook: the box shell…" line at the end of `### Acceptance checklist (P-17, decided 2026-09-25)`:

```
Window runbook: [models/marlin2b/results/E4C-runbook.md](../../../models/marlin2b/results/E4C-runbook.md) (freeze, FILL sources, key inventory, the certify and two-tenant journey invocations with `E4C-edge.overload.base.json` and `E4C-box.two-tenant.base.json`, drill record, evidence layout, P-17 tick-off).
```

Proof: `python3 research/plan/scripts/validate_plan.py` checks local links, and the target exists at f2a245c0.

## Open issues (for the coordinator)

1. **`weights_sha256` "shard-set digest".** The base's FILL text names it, but no repository code defines it. The runbook defines it as `sha256:` plus the sha256 of the compact JSON list `serving-version.json` `model.weight_shard_digests`, in file order. Ratify it, or name another derivation, before the freeze.
2. **Sync (non-stream) leg of the journey.** No external client in the repository runs it against the box; `test_journey.py` covers it locally. The runbook asks for a one-off header-file curl per tenant, or NOT RUN with that reason.
3. **Box to edge hairpin.** The P4 burst from the certify container (`--network host`) reaches `https://marlin2b.callbill.ai` through the Elastic IP. The runbook makes a box-side `curl …/health` = 200 a precondition. It was not measured here.
4. **Gateway env file key names.** The launcher passes `--env-file /etc/marlin2b-gateway.env`. The runbook pre-checks that it holds no `MARLIN_API_KEY` name (rule 1), which was not verified here.
5. **Coordinator updates overlay.** No `updates/E4C-*.json` was written, because it is outside this brief's owned paths.

## Estimate

- Remaining for this lane: 0 h optimistic, 0.5 h likely (a review fix round), 1.5 h pessimistic.
- Confidence: high.
- Basis: all five deliverables are done and checked; the full e4b mutant run was about 56 % through with no survivor at handback.
