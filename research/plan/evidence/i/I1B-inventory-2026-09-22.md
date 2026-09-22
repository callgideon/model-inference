# I1B — read-only live-state inventory (2026-09-22, ~15:55–16:20 UTC)

Coordinator-commissioned, read-only (SELECT/describe/status verbs only; secrets read into shell variables and never printed). Nothing on either system was changed by the inventory. Full command list is in the coordinator transcript; verbs: `aws ec2 describe-*`, `aws ssm send-command` (AWS-RunShellScript, read-only script) + `get-command-invocation`, `psql` under `default_transaction_read_only=on`, PostgREST/Auth `GET`s.

## GPU box `i-0e8449a4ffca29bab` (g6e.2xlarge, us-east-1d, 100.57.145.167)
| fact | value |
|---|---|
| state | running since 2026-09-19T22:46Z; uptime 2 d 17 h; load 0.15; Ubuntu 24.04.4, kernel 6.17.0-1020-aws |
| GPU | 1× L40S, driver 595.91.07, CUDA 13.2; 39,945 / 46,068 MiB used at idle (model resident), 0 % util |
| host | 8 vCPU, 61 GiB RAM; `/` 290 G (243 G free); `/opt/dlami/nvme` 412 G (386 G free) |
| durability (before this session) | one EBS volume `vol-091e45c92f7426291`, DeleteOnTermination=true; **0 snapshots, 0 AMIs** in the account; usage.jsonl on instance store |
| services | `marlin2b-vllm.service` active since 2026-09-20 01:14Z (`Restart=always`, `ExecStop=docker stop marlin2b-8000`, no `-t`/`TimeoutStopSec` → 10 s drain); `marlin2b-gateway.service` active since **2026-09-22 06:58Z** (restart with no attributable change), `uvicorn gateway:app --host 127.0.0.1 --port 8001 --workers 1`, interpreter /opt/pytorch/bin/python3.13 |
| containers | `caddy` (caddy:2, v2.11.4; single site marlin2b.callbill.ai → 127.0.0.1:8001, flush_interval -1, read_timeout 600 s; no rate limit); `marlin2b-8000` = `vllm/vllm-openai:nightly`, local image id `sha256:4cbfd34aac14…`, **no RepoDigest recorded** (upstream digest unrecoverable from the box) |
| vLLM args | `vllm serve /model --served-model-name marlin2b --hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}' --max-model-len 32768 --gpu-memory-utilization 0.90 --limit-mm-per-prompt '{"video":1,"image":4}' --dtype bfloat16 --max-num-seqs 32`; absent: --reasoning-parser, continuous_usage_stats, chunked prefill flags, kv-cache-dtype, api-key |
| HTTP | local :8000 and :8001 `/v1/models` 200; `/health` 200 `{"ok":true,"inflight":0}`; `/metrics` 404; `/readyz` 404. Public: `/v1/models` 200 (deliberately public), `POST /v1/chat/completions` without key 401, bogus key 401 |
| gateway env | `/etc/marlin2b-gateway.env` (0600 ubuntu:root): names MODEL_ID, MAX_INFLIGHT, GATEWAY_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY; unit sets USAGE_LOG |
| python | system 3.12.3; /opt/pytorch 3.13.15 with torch 2.12.1+cu130, transformers 5.17.0, fastapi 0.141.1, uvicorn 0.52.4, httpx 0.28.1, pydantic 2.13.4; no vllm/supabase packages; no lock; ffprobe 6.1.1 (distro) |
| repo | /home/ubuntu/model-inference on `main` at `8f1d2f6` (2026-09-20) — **480 commits behind** `f6fd3e8`; `apps/infrx-api/infrx/` absent — the deployed gateway is the pre-F1 monolith `gateway.py` (sha256 0bdb948…) |
| logs | usage.jsonl 19 lines (last 2026-09-20 08:32); usage_failed.jsonl absent; last inference 2026-09-20 08:32Z; caches primed (prefix 74.6 %, MM 88.2 %) |

### Marlin artifact on disk (`/opt/dlami/nvme/marlin2b`, 5.44 GB, 2 shards)
config.json: architectures `MarlinForConditionalGeneration`, model_type `qwen3_5`, bf16, transformers 5.7.0; tokens image 248056 / video 248057 / vision_start 248053 / vision_end 248054 / eos 248046 / pad 248044; text_config max_position_embeddings **262,144**, 24 layers, hidden 2048, 8 heads / 2 kv, head_dim 256, vocab 248320, **hybrid attention** (3× linear_attention then full_attention, interval 4 → 6 full-attention layers), mrope_section [11,11,10], `mtp_num_hidden_layers: 1` with no MTP tensors shipped; vision_config depth 24, hidden 1024, patch 16, spatial_merge 2, temporal_patch 2, num_position_embeddings 2304. generation_config eos_token_id **[248044, 248046]**, no sampling defaults. preprocessor_config size.longest_edge 16,777,216 / shortest_edge 65,536, Qwen3VLProcessor; **no video_preprocessor_config.json**. tokenizer_config model_max_length 262,144, audio tokens declared with no audio config. chat_template.jinja (7,755 B): ChatML; always emits a `<think>` block (`<think>\n\n</think>\n\n` when thinking is off); XML tool-call dialect (no parser configured → tools unsupported).

## Hosted Supabase `fcbnscgsymzdykendbrc` (us-east-2)
Reached via the session pooler `aws-0-us-east-2.pooler.supabase.com:5432` (direct host is IPv6-only from here).
| fact | value |
|---|---|
| versions | PostgreSQL 17.6; PostgREST 14.5 |
| migrations applied | `0001 init`, `0002 seed_models` — **0003–0005 NOT applied**; no `infrx` schema; no `infrx_*` roles; 0 views |
| tables | api_keys, credit_ledger, models, org_members, organizations, profiles, usage_events (RLS on all) |
| counts | auth.users 4 (3 confirmed); organizations 4; org_members 4 (all `owner`, no org with >1 member); api_keys 2 active / 0 revoked; usage_events 1 ($0.00019660); **credit_ledger 0 rows** |
| per-org USD balance | all four = **0.000000** |
| storage | no buckets |
| auth | disable_signup true; all external providers false; mailer_autoconfirm false |
| auth.uid() | reads **both** GUCs: `request.jwt.claim.sub` first, then `request.jwt.claims->>'sub'` (verified empirically under `begin read only … rollback`) |

## Consequences (coordinator)
- **P-02 resolved:** total legacy USD is $0.00; nothing to transition or convert; the four accounts are the 2026-09-20 dev/e2e test accounts (decision: keep; they receive no grant until verified under A1).
- **P-04 resolved:** this box is the I2B/E1B target. I2B is a first deployment of the refactored app, not an update; W3 must pin the engine digest from the registry.
- **P-03 resolved** (Docker on the coordinator host).
- Local harnesses must set both JWT claim forms (the pinned local image reads only the legacy GUC; hosted reads both, legacy first).
- Immediate action taken under authorization: EBS snapshot `snap-08732d3ac6376e850` of the root volume; DeleteOnTermination set to false. Recorded in the session-02 operation log.
