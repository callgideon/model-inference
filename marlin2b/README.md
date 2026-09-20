# marlin2b — NemoStation/Marlin-2B serving experiment

Video VLM (Qwen3.5-2B base, ~5.4 GB BF16): dense scene/event captions with
timestamps, and "find" (temporal grounding). Research:
[`research/models/marlin2b/`](../research/models/marlin2b/README.md).

## Dev box

AWS `g6e.2xlarge` (1× L40S 48 GB), Deep Learning AMI (Ubuntu 24.04, driver
595, PyTorch env at `/opt/pytorch`, docker + NVIDIA runtime). Instance store
NVMe is mounted at `/opt/dlami/nvme`; everything lives there:

| path | what |
|---|---|
| `/opt/dlami/nvme/marlin2b` | weights (`WEIGHTS_ROOT=/opt/dlami/nvme`) |
| `/opt/dlami/nvme/samples` | test clips (`sample-10s.mp4`, `Big_Buck_Bunny_360_10s_1MB.mp4`) |
| `/opt/dlami/nvme/logs` | download / serve / bench logs |
| `/home/ubuntu/model-inference` | this repo, branch `marlin2b` |

The HF token is in SSM Parameter Store as `/model-inference/hf_token`
(SecureString); the instance role can read it. Run remote commands with
`aws ssm send-command` from the admin host (no session-manager-plugin there).

```bash
# on the box
export PATH=/opt/pytorch/bin:$PATH WEIGHTS_ROOT=/opt/dlami/nvme HF_HOME=/opt/dlami/nvme/hf
export HF_TOKEN=$(aws ssm get-parameter --name /model-inference/hf_token --with-decryption --query Parameter.Value --output text)
./marlin2b/download.sh                                  # ~5.4 GB from Hugging Face
python marlin2b/reference.py /opt/dlami/nvme/samples/sample-10s.mp4        # transformers path (vendor-supported)
python marlin2b/reference.py --dump-prompts             # the canonical prompts the helpers use
./marlin2b/serve.sh                                     # vLLM nightly in docker, port 8000
python marlin2b/smoke.py /opt/dlami/nvme/samples/sample-10s.mp4            # one request, timed
python marlin2b/smoke.py video.mp4 --find "a person enters the room"
python marlin2b/bench.py video.mp4 -c 8 -n 32           # load test -> results/bench.jsonl
```

## Public endpoint (dev)

`https://marlin2b.callbill.ai` — OpenAI-compatible, TLS by Caddy (Let's
Encrypt), Elastic IP 100.57.145.167 (`eipalloc-037e19cc644820961`), Route 53
zone callbill.ai, security group `marlin2b-gateway` (80/443). Stack on the
box: `marlin2b-vllm.service` (docker, localhost:8000) → `gateway.py`
(`marlin2b-gateway.service`, localhost:8001) → Caddy (docker, :443).
Install or refresh with `sudo ./marlin2b/deploy/install.sh`.

API key: SSM `/model-inference/marlin2b_api_key` (SecureString). Model id on
the wire is `nemostation/marlin-2b`; usage is logged to
`/opt/dlami/nvme/logs/usage.jsonl` with the `Inference-Id` response header.

```bash
KEY=$(aws ssm get-parameter --name /model-inference/marlin2b_api_key --with-decryption --query Parameter.Value --output text)
curl https://marlin2b.callbill.ai/v1/chat/completions -H "authorization: Bearer $KEY" -H 'content-type: application/json' -d '{
  "model": "nemostation/marlin-2b", "max_tokens": 512, "temperature": 0,
  "messages": [{"role": "user", "content": [
    {"type": "video_url", "video_url": {"url": "https://download.samplelib.com/mp4/sample-10s.mp4"}},
    {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description."}]}]}'
```

The gateway sets the video budget per request (frames × 200,704 px), rejects
clips over 120 s or 64 MB, returns 429 above 16 in-flight, and strips the
leading `<think>`. Measured 2026-09-20 from another AWS host: a 10 s public
URL clip takes ~3.8 s end to end (TTFT ~3.2 s, dominated by downloading and
decoding the 5.5 MB source twice, gateway and vLLM); grounding returns in ~3.3 s.

## Why two paths

`transformers` with `trust_remote_code` is the only vendor-supported route
(the `.caption()`/`.find()` helpers live in `modeling_marlin.py`). vLLM does
not register `MarlinForConditionalGeneration`; `serve.sh` remaps it to the
native Qwen3.5 class with `--hf-overrides`, and `smoke.py`/`bench.py` send the
same canonical prompt text so outputs can be diffed against `reference.py`.
Parity between the two is the first thing to check on a new engine version.

## Video budget

Model defaults: 2 fps, 4–240 frames, 200,704 px/frame (~448×448). Each pair
of frames is one temporal patch of 196 tokens, so a 2-minute clip is ~23.5K
prompt tokens; `serve.sh` sets `--max-model-len 32768` for that reason.
Neither vLLM nor the vendor helper applies that budget by default on
transformers 5.17 (both spend ~12K tokens on a 10 s clip); `smoke.py` and
`bench.py` pass `--mm-kwargs auto`, which sets `size.longest_edge` to
frames × 200,704 and reproduces the training grid — see
[`results/notes.md`](results/notes.md).

## Results

`results/` holds `bench.jsonl` rows (one per `bench.py` run) and notes. Fill in
the table below from measurements, not estimates:

| GPU | engine | clip | concurrency | prompt tok | TTFT p50 | TPOT p50 | clips/s | out tok/s |
|---|---|---|---|---|---|---|---|---|
| L40S | vLLM nightly 2026-09-19 | sample-10s (1080p, 5.5 MB) | 1 | 2,061 | 0.77 s | 6 ms | 0.50 | 100 |
| L40S | vLLM nightly 2026-09-19 | sample-10s (1080p, 5.5 MB) | 8 | 2,061 | 3.35 s | 8 ms | 1.57 | 310 |
| L40S | vLLM nightly 2026-09-19 | sample-10s, processor default | 8 | 12,221 | 3.70 s | 8 ms | 1.47 | 290 |
| L40S | vLLM nightly 2026-09-19 | Big Buck Bunny (360p, 1 MB) | 8 | 1,928 | 0.66 s | 7 ms | 3.58 | 760 |

Measured 2026-09-19; details and caveats in [`results/notes.md`](results/notes.md).
