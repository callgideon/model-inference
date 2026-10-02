# Executed evidence — 2026-10-01

These files record the read/refusal audit, not completed deployment certification.

| File | What it records | Limits |
|---|---|---|
| [deployed-api-probe.json](deployed-api-probe.json) | 33 HTTP probes, completed 21:40:25 UTC; public catalog snapshot, authorized empty workspace reads, failed first-model registration and missing/unavailable families | No authenticated inference, weights upload, engine deployment, publication or judge call; `full_lifecycle=BLOCKED` |
| [host-observations.json](host-observations.json) | Two successful read-only SSM commands, 21:36–21:38 UTC; service/container identities, selected configuration presence and active E4C run | No service/configuration changes; sampled state, not a continuing monitor. Values of credentials/URLs/bucket names were not printed |

The authenticated bootstrap used an existing test identity through GoTrue plus the legacy `lab_provider_memberships` RPC. That RPC is deliberately recorded as a FastAPI gap. The report omits session tokens, membership IDs, email, private response bodies and model input/output. Public catalog IDs/metadata are retained as public evidence.

The probe now recognizes the existing `{refusal:reason}` response in addition to OpenAI-style errors. The initial report preserved HTTP statuses/field names but did not retain that reason value; no later source inference was retroactively inserted into the original response evidence. The original unauthenticated inference body used the legacy short `marlin2b` name; it was refused for missing authentication before model selection. The script now uses the canonical public ID for future probes. Neither run implies successful inference.

Focused source verification executed from the review checkout:

```sh
apps/infrx-api/.venv/bin/python -m pytest \
  apps/infrx-api/tests/l/control/test_operations.py \
  apps/infrx-api/tests/g/lab_control/test_lab_control.py \
  apps/infrx-api/tests/g/lab_auth/test_lab_auth.py -m 'not pg' -q
```

Result: **33 passed, 18 deselected**, two dependency deprecation warnings, 0.45 seconds reported by pytest. This is in-memory/source behavior; PostgreSQL cases were not run and local Docker was unavailable. Existing real-process/hosted gates are still required.

Documentation validation: existing plan graph remains 133 tasks with acyclic dependencies and preserved verdicts. Markdown links and reference JavaScript syntax were checked. No implementation package was marked complete by these checks. Previous UX preview images were not regenerated after the text-only onboarding correction; their evidence remains dated to the earlier visual review.

## The lifecycle runner (AP-11, from 2026-10-02)

`tests/integration/api_lifecycle/runner.py` is the acceptance runner (11a state/resume, 11b
stage contracts); its operation is [infra/runbooks/api-lifecycle.md](../../../../infra/runbooks/api-lifecycle.md).
Unlike `probe.py` it mutates, keeps a 0600 resumable state file separate from a 0600 secrets
file, and exits 0/1/3/4 on its verdict. An `isolated` run (`--world ap11`) is task-local
services with declared fixtures and the controlled engine: it proves the mounted consumer
routes (stage 09 PASS; 01/08/10/11 green on their mounted halves) and names every stage whose
API is absent as BLOCKED with its AP package. It is not a GPU, judge, hosted or
complete-lifecycle result; those are 11c-11e's live runs. Redacted verdicts are recorded in
`research/plan/evidence/w7/` by the lane that ran them.
