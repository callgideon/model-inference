# API lifecycle runner — running, resuming and cleaning up (AP-11)

The acceptance runner for [verification.md](../../research/plan/api-lifecycle/verification.md)'s
18 steps: `tests/integration/api_lifecycle/runner.py`. Every product step is an HTTP call to
FastAPI; SQL, the operator CLI and Supabase RPCs never count as a pass. The bounded gap audit
`research/plan/api-lifecycle/probe.py` is separate and is never called from here.

## Files

| File | Who writes it | Mode | Holds |
|---|---|---|---|
| state (`--state`) | the runner, atomically after every change | 0600 | run id, target, each mutation's Idempotency-Key + canonical request hash + `pending`/`done` outcome, stage checkpoints (status, safe outputs, redacted evidence), resources created and how to clean each, request counters |
| secrets (`--secrets`) | the operator (or `--world ap11`) | 0600, read only | credentials by name: identity-provider sessions, the operator's bootstrap credential, isolated-mode fixture keys |
| minted keys (`<state>.keys`) | the runner, when the key API answers (stages 08, 12) | 0600 | the secrets of the keys this run created, by secret name; the state holds only their ids. Reloaded on resume; a lost secret is never re-revealed: that key is revoked and one more is minted |
| config (`--config`) | the operator | any | target, origins (`gateway`, `lab`), identities (audience + secret name; `<holder>` a consumer key, `<holder>_web` the same individual's session), model + `model_uuid`, `composed` (the AP packages the target serves), `traces`, `judge` (`live` / `dry_run`), artifact dir, media clip, fixtures, budget, `max_requests` |
| `<out>/verdict.json` | the runner | redacted | per stage PASS/FAIL/BLOCKED/NOT RUN, reasons, evidence; the gate; pins |

Keep the state and secrets files outside the repository, in a 0700 directory. A group- or
world-readable state/secrets file is refused (INVALID, exit 4) before any request.

## Modes

```sh
PY=apps/infrx-api/.venv/bin/python; R=tests/integration/api_lifecycle/runner.py
$PY $R --mode inspect  --config c.json --secrets s.json --state /priv/ap11/state.json --out OUT
$PY $R --mode isolated --world ap11 --state /priv/ap11/state.json --out OUT
$PY $R --mode live     --config live.json --secrets s.json --state /priv/live/state.json --out OUT
$PY $R --mode cleanup  --config c.json --secrets s.json --state /priv/live/state.json --out OUT
```

- **inspect**: read-only stages only (01); any non-GET is refused before it leaves.
- **isolated**: `--world ap11` composes ap11's own stack (PostgreSQL 57567, Valkey 57568, MinIO
  57569, ClickHouse 57570 once WR-AP11C-1 gives ap11 that port, the gateway and worker processes
  with the wave-7 switches on, the Lab control unit with LAB_JUDGE_API/LAB_ARTIFACTS,
  `tests/integration/fake_vllm.py`) and tears it down after. Fixtures it seeds are declared in
  the verdict; keys, grants, data use, judge configs/budgets/runs and reviews go through the
  APIs. The judge runs as a **dry run**: stages 14-15 PASS carry the label `dry-run...`
  (accepted and accounted, nothing sent or spent, nothing scored) - never a judge result. Not
  GPU, live-judge or hosted evidence.
- **live**: refused (INVALID) unless the config names origins, identities, target, an exact
  `budget` (`{amount, unit}`, unit CREDIT/USD/PROVIDER_USD) and `max_requests` 1..6, and
  declares no fixture. Consumer keys must then come from the key API (stage 08, AP-03).
  Coordinator-serialized; never overlaps an E4C window; zero extra GPU allocations unless the
  config names the isolated target.
- **cleanup**: removes only the resources the state file records, newest first; 404/410 is
  `gone`, anything else non-2xx fails the cleanup.

## Exit codes and verdicts

0 PASS · 1 FAIL · 3 BLOCKED or NOT RUN · 4 INVALID (tests/integration/ENVIRONMENT.md). The gate
is the worst selected stage. A stage whose routes the target does not serve (config `composed`)
is BLOCKED naming the AP package and route (`BLOCKED[AP-05] POST /lab/v1/control/deployments,
...: not served by the target`) and is never called; a route the target answers with the
framework's own 404/405 is `BLOCKED[<AP>] <route>: not mounted`; a stage that asserted
nothing is never PASS; a PASS on a stand-in carries its `label`. `complete_lifecycle` is true only for a live run with all 18
stages PASS. API-boundary, quality and operations verdicts are reported NOT RUN by this runner.

## Interrupted runs (exit 3, a stage NOT RUN "interrupted")

The outcome of the last request is unknown. Do not start a new state file. Rerun the same
command with the same `--state` (and, for live, the same config):

1. stages already PASS are not repeated;
2. a mutation recorded `done` is confirmed by a GET (a vanished resource is a FAIL), never
   re-sent;
3. a mutation recorded `pending` is retried with its ORIGINAL Idempotency-Key and body (the
   server replays it); it does not count against the request budget again;
4. a recorded mutation whose body would now differ (an edited config) is INVALID: start a new
   state file for a new request. A version read before a compare-and-set write (consent,
   grant) is pinned in the state, so a resume re-sends the identical body.

An isolated world is rebuilt per invocation, so its state cannot resume against the next
world (the target differs: INVALID). Resume applies to live and to externally composed targets.

## Evidence

Commit only the redacted `verdict.json` (or a summary) under `research/plan/evidence/`;
never the state or secrets file, never box logs that might carry customer content.

## Verification log

- 2026-10-02: created with AP-11 slices 11a/11b (lane api-lifecycle, wave 7).
- 2026-10-02: AP-11 11c (lane api-lifecycle-2): `composed`, minted-key file, pinned CAS
  versions, the Lab unit and trace storage in the isolated world, the dry-run label, cleanup
  rows that fail alone.
