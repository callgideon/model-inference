# SOP benchmark (AP-10 10d)

The harness is `apps/infrx-api/infrx/lab/improve/sop.py`. It is a small quality run over fixed cases against one private candidate endpoint. It is not a load test (`../bench.py`) and not a resumable paid sweep (`../dataset.py`).

```sh
apps/infrx-api/.venv/bin/python -m infrx.lab.improve.sop \
  --manifest items.jsonl --dataset-version <version> \
  --base-url http://127.0.0.1:<port>/v1 --model <served name> \
  --serving-revision <AP-05 deployment revision id> --seed 7 --target candidate \
  [--sop sop.json --gold gold.json] --out report.json
```

- The manifest uses `../dataset.py`'s JSONL format. A key, if the endpoint needs one, comes only from `INFRX_API_KEY` and is never written.
- The report pins the following:
  - the dataset: its version, the manifest sha256 and a digest of the case ids;
  - the identity: the model requested, the models `/v1/models` lists, the model each answer names, and the serving revision;
  - the harness and parser versions;
  - the seed (every request uses `temperature` 0 and that seed).
- Every failure and every abstention is listed by item id:
  - `no_media`: the item was never sent;
  - `insufficient_evidence`: the answer had no timed event.
- **Quality** stays `BLOCKED` (P-07) until two inputs arrive:
  - the operator's SOP definition (`sop.json`: `sop_id`, `version`, `review_ref`, `tolerance_s`, `steps`);
  - a **human-reviewed** gold set of exactly this manifest (`gold.json`: `dataset_version`, `manifest_sha256`, `provenance: human_reviewed`, `reviewed_by`, `review_ref`, `labels: {item id: [{start_s, end_s}]}`).

  With both, the harness computes span agreement: matching is one-to-one and in order, and a match needs both the start and the end within `tolerance_s`. It reports precision and recall. Teacher or model labels are refused as ground truth.
- **Performance** is labelled `fake` when the endpoint answers the fake engine's `GET /_control` (`tests/integration/fake_vllm.py`, AP-05's local candidate engine), or when `--target fake` is given. It is labelled `meas.` only for a declared candidate that does not answer that probe.
  - p50 needs at least 6 samples and p95 at least 60; below that a percentile is refused, never guessed.

Running the harness on the real candidate (AP-05 on the box) takes a coordinator GPU window. Real SOP quality also needs P-07. Until then, the only runs are the local fake runs (`tests/ap10/test_sop_fake_engine.py`).
