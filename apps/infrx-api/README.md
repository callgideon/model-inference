# infrx-api — the inference gateway

**2026-09-22 sequence:** [Marlin inference backend first](../../research/plan/18-marlin-backend-first.md), consumer App next, provider Lab afterward. The backend has independent headless deployment/recovery/performance gates; frontend feature work is subsequent.

**Current execution:** [Marlin App-first complete plan](../../research/plan/12-complete-build-plan.md) and [fresh-session prompt](../../research/plan/16-fresh-session-handoff.md). Preserve the audited wave-2 work; actual product-v2 and runtime integration remain pending.

OpenAI-compatible gateway in front of vLLM, plus its deployment files and the
OpenRouter provider document. Runs on the GPU box as `marlin2b-gateway.service`
(uvicorn on localhost:8001) behind Caddy; vLLM stays on localhost:8000. Box
layout and the public endpoint: [`models/marlin2b/README.md`](../../models/marlin2b/README.md).
Requirements and data model: [`apps/README.md`](../README.md) §6–§7.

```
client ─▶ Caddy :443 ─▶ create_app :8001 ─▶ vLLM :8000
                          │  auth: api_keys (cache 60 s)
                          ├─▶ Supabase  usage_events (background queue)
                          └─▶ usage.jsonl (always) · usage_failed.jsonl (on failure)
```

| file | what |
|---|---|
| `infrx/gateway/` | the application factory (`uvicorn --factory infrx.gateway.app:create_app`) and routes |
| `infrx/auth/`, `infrx/media/`, `infrx/usage.py`, `infrx/config.py` | auth cache, safe media fetch and video budget, usage shipping, settings |
| `infrx/contracts/` | executable contracts v1: records, ports, fixtures, fakes and conformance suites ([README](infrx/contracts/README.md)) |
| `deploy/install.sh` | idempotent installer, run as root on the box |
| `deploy/*.service`, `deploy/Caddyfile` | systemd units and TLS |
| `deploy/replay_usage.py` | re-post rows from `usage_failed.jsonl` |
| `openrouter/provider-models.json` | served at `/v1/models` (see `openrouter/PLAN.md`) |
| `tests/<track>/` | one suite per track (`make api-test`) |
| `client_example.py` | reference client |

## Environment

`install.sh` writes `/etc/marlin2b-gateway.env` from SSM; the unit adds
`USAGE_LOG`. `INFRX_MODE` is required (an unset mode refuses to start, R44), and
`create_app` builds the pilot's stores from `DATABASE_URL` and its object store from
`S3_MEDIA_BUCKET`: M1-L2's `S3ObjectStore`, which must answer HeadBucket. Unset, or not
answering, the gateway refuses to start rather than stage media in process memory. Tests
inject the adapters.

| var | SSM parameter | meaning |
|---|---|---|
| `SUPABASE_URL` | `/model-inference/supabase_url` | project REST base, e.g. `https://fcbnscgsymzdykendbrc.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | `/model-inference/supabase_service_role_key` | service role; bypasses RLS, box only |
| `GATEWAY_API_KEY` | `/model-inference/marlin2b_api_key` | legacy single key, still accepted while it exists |
| `MODEL_ID` | — | id on the wire and in `usage_events.model_id` (`nemostation/marlin-2b`) |
| `UPSTREAM`, `MAX_INFLIGHT`, `MAX_VIDEO_SECONDS`, `MAX_VIDEO_MB` | — | vLLM address, 429 threshold, video limits |
| `FETCH_TIMEOUT_S`, `MAX_REDIRECTS`, `ALLOWED_VIDEO_MIME` | — | media fetch: total budget (30 s), redirect hops (3), content-type allowlist (`video/mp4,video/webm,video/quicktime`) |
| `USAGE_LOG`, `USAGE_FAILED_LOG` | — | `/opt/dlami/nvme/logs/usage.jsonl`, and `usage_failed.jsonl` beside it |
| `MODELS_DOC` | — | path to the provider document served by `/v1/models` |

## Auth

`Authorization: Bearer <key>` → `sha256(key).hexdigest()` → `GET
{SUPABASE_URL}/rest/v1/api_keys?key_hash=eq.<hash>&select=id,org_id,revoked_at`
with the service role key. Plain httpx; no supabase client library.

- cache keyed by hash: hits 60 s, misses 10 s, so a console-revoked key stops
  working within a minute;
- `revoked_at` set → 401;
- Supabase unreachable → keys already in the cache keep working (stale entries
  are served), unknown keys get **503**, not 401, so a caller retries instead
  of rotating a key that is fine;
- `last_used_at` is PATCHed at most once a minute per key, fire-and-forget;
- `GATEWAY_API_KEY`, if set, is accepted as before. It has no org, so those
  requests are written to `usage.jsonl` only and never reach `usage_events`.
  Drop it from SSM once every caller has a console key.

## Security: the media fetch (SSRF)

A `video_url` is a caller-controlled URL that the gateway dereferences from
inside the VPC, on an instance whose role can read SSM SecureStrings. It is
fetched **once, in the gateway**, and handed to vLLM inline as a base64
`data:` URL, so the engine never fetches from the internet — one download
instead of two, and no second SSRF surface behind ours.

Policy (`prepare_video` / `fetch_video` in `infrx/media/video.py`):

- **scheme**: `http`/`https` only; anything else is rejected before a socket
  is opened;
- **address**: every hostname is resolved here and **all** A/AAAA answers must
  be routable public addresses. Loopback, private (RFC1918), link-local
  (`169.254.0.0/16` — the EC2 metadata endpoint — and `fe80::/10`), CGNAT,
  multicast, reserved and unspecified are rejected, v4, v6 and v4-mapped v6;
- **redirects**: followed by hand, at most `MAX_REDIRECTS` (3) hops, with the
  address check re-run on **every** hop, so a 302 to `169.254.169.254` is
  rejected as the original URL would have been;
- **size**: `Content-Length` is checked up front and a byte counter aborts the
  stream the moment it passes `MAX_VIDEO_MB`, so the body is never buffered
  whole in RAM;
- **time**: the whole fetch, redirects included, lives inside `FETCH_TIMEOUT_S`
  (30 s), 5 s of it for connect;
- **type**: `ALLOWED_VIDEO_MIME` (mp4, webm, mov/quicktime); a server
  that says nothing or `application/octet-stream` falls back to the URL's
  extension, anything else is refused;
- **errors**: upstream exception text is never returned — it goes to the
  journal, the caller gets `400 could not fetch video (<class>)` where the
  class is one of `dns`, `blocked-address`, `too-large`, `timeout`,
  `unsupported-type`, `unsupported-scheme`, `too-many-redirects`,
  `http-<status>`, `fetch-failed`. That keeps the blind-SSRF oracle shut.

Not covered here, still worth having: egress rules denying the metadata range
and RFC1918 from the fetch path, and a per-tenant origin allowlist. Validation
and connection are two steps, so a DNS rebind between them remains
theoretically possible; pinning the resolved IP is the upgrade.

`data:` URLs are unchanged: decoded, size-capped, `ffprobe`d, forwarded as-is.

## Usage ingestion

Every request writes its `usage.jsonl` line (durable, unchanged). Authenticated
requests also queue an `usage_events` row — id = the `Inference-Id` header, so
inserts are idempotent — for a background task that POSTs it to
`{SUPABASE_URL}/rest/v1/usage_events` with `Prefer: return=minimal`. The queue
holds 10k rows; failures retry after 1 s, 3 s and 9 s and then append to
`usage_failed.jsonl`. Nothing about the request body or response is stored.

`cost_usd = prompt_tokens × input_usd_per_m / 1e6 + completion_tokens ×
output_usd_per_m / 1e6`, from `models` for `MODEL_ID`, re-read every 5 minutes.
If the prices cannot be read the row is written with `cost_usd = 0` and a
warning goes to the journal — the row is never dropped over a price.

Replay after an outage (the file is moved aside first, so the gateway can keep
appending; a 409 counts as already inserted):

```bash
set -a; . /etc/marlin2b-gateway.env; set +a
/opt/pytorch/bin/python ~/model-inference/apps/infrx-api/deploy/replay_usage.py
```

## Headless provisioning and the dataset client (G6B)

Everything a provisioned client needs, with both Next.js apps stopped. **Status:
implemented against in-memory fakes only** — `infrx.operations.cli` refuses to run
until the D1R/D5/A1 PostgreSQL adapters exist (it never runs on an in-memory store).

Operator prerequisites: the gateway environment installed by `deploy/preflight.py
apply --mode pilot` (no shared `GATEWAY_API_KEY`, R51), and an operator-audience key
row (bootstrap is a D-owned step). The operator secret comes from
`$INFRX_OPERATOR_KEY` or a no-echo prompt, never argv.

```bash
python -m infrx.operations.cli grant --user <user-uuid> --idempotency-key g-<user-uuid> --reason "..."
python -m infrx.operations.cli issue-key --user <user-uuid> --name sweep --secret-file ./sweep.key \
    --idempotency-key k-1 --reason "..."          # the secret is written once, 0600, never printed
python -m infrx.operations.cli publish-marlin --provider-org <uuid> \
    --created-at 2026-09-01T00:00:00+00:00 --effective-at <now, RFC 3339> --idempotency-key p-1 --reason "..."
# also: adjust, revoke-key, rotate-key, suspend [--code abuse|nonpayment|security|operator_request|other],
#       cancel --org --job, reconcile --org --request
```

- Keys are consumer keys for a **verified** individual's personal org, stored as
  `sha256` like every other key; a replayed issue returns the id and prefix only.
- Credit moves only through the A1 signup grant (once per user, 10,000 CREDIT) and D5
  adjustments; the tool never writes a balance. Every write needs an idempotency key
  and a reason and leaves one `infrx.audit_entries` row.
- The Marlin rate card is **provisional (P-01)** and labelled so in its version and
  approver until an operator-approved rate exists.

Client (`client_example.py`, reusing `models/marlin2b/bench.py`'s item identity and
key handling):

```bash
export INFRX_API_KEY="$(cat sweep.key)"
python client_example.py quickstart --base https://<host>/v1 --video https://<public>/clip.mp4
python client_example.py sweep --base https://<host>/v1 --manifest items.jsonl --state state.jsonl
```

One manifest line per ≤120 s segment; `Idempotency-Key: sop1.<item_key>` is derived
from the item, so re-running the same command resumes: finished items are skipped and
the rest are re-sent with the same key and payload (the server replays, never double
charges). At most 8 requests in flight per key; 429/5xx honour `Retry-After`; 400/409
are quarantined, 410 asks for a re-run, 401/402/403 stop the sweep. Served today: sync
JSON with `http(s)` or `data:` media. `--respond-async` (202, then the job's status at its
`Retry-After`, then its result) runs on G3's `/v1/jobs` routes, served once the cutover
mounts them. `--form upload` is **specified, not served** until G4U mounts `/v1/uploads`.
Research: `research/workloads/marlin-sop.md` §3.

## Deploy

```bash
cd ~/model-inference && git pull
sudo ./apps/infrx-api/deploy/install.sh          # reads SSM, writes the env file, restarts
journalctl -u marlin2b-gateway -f
```

Missing SSM parameters are a warning, not a failure: the gateway keeps running
with whatever is present. Add the Supabase parameters once, as SecureString:

```bash
aws ssm put-parameter --name /model-inference/supabase_url --type String --value https://<ref>.supabase.co --overwrite
aws ssm put-parameter --name /model-inference/supabase_service_role_key --type SecureString --value <key> --overwrite
```

## Tests

The environment is pinned (`pyproject.toml`, `uv.lock`, Python 3.12). From this
directory:

```bash
uv sync --frozen --all-extras      # creates ./.venv exactly as locked
uv run --frozen pytest -q          # the whole suite
uv run --frozen pytest -q tests/contracts -k dur_settle   # one oracle's cases
```

`make api-test` from the repository root runs the same command; `make check` adds
the console and benchmark targets. A track adding a dependency asks the
coordinator: nobody else edits `pyproject.toml` or `uv.lock`. Test files live in
`tests/<track>/`, discovered with `--import-mode=importlib` so same-named files in
different track directories do not collide.

`httpx.MockTransport` stands in for Supabase, the media origin and vLLM, so the
tests need no network and no env vars. `tests/contracts/` covers the shared
contracts: fixture round-trips, the money rules, the configuration names, and
every port's conformance suite run against the in-memory fakes.
