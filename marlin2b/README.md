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
Whether vLLM's Qwen3.5 processor applies the same fps/pixel defaults as the
custom code is **⚠️ to be verified** by comparing `prompt_tokens` from
`smoke.py` against the frame count `reference.py` produces.

## Results

`results/` holds `bench.jsonl` rows (one per `bench.py` run) and notes. Fill in
the table below from measurements, not estimates:

| GPU | engine | concurrency | prompt tok | TTFT p50 | TPOT p50 | out tok/s | notes |
|---|---|---|---|---|---|---|---|
| L40S | vLLM nightly | 1 | | | | | |
| L40S | vLLM nightly | 8 | | | | | |
