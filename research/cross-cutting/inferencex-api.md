# SemiAnalysis InferenceX API — endpoints, slug map, re-fetch recipe

**Research date: 2026-09-19.** Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md) — every
claim here was produced by a live request made this session, or is marked
**⚠️ TO BE VERIFIED**. Base URL throughout:
`https://inferencex.semianalysis.com/api/v1` [src](https://inferencex.semianalysis.com/api/v1/benchmarks?model=gpt-oss-120b).

> **Why this document exists.** Seven GPU documents quote InferenceX benchmark rows. A
> fact-checking pass on 2026-09-19 could re-fetch only the `gpt-oss-120b` rows and marked the
> rest ⚠️ TO BE VERIFIED, because every other slug returned `{"error":"Unknown model"}`. The
> cause is now identified and **all fifteen models are re-fetchable** (§3). This document does
> not edit the GPU documents; §7 says which of their tables the map unblocks.

---

## 0. Executive summary

1. **`model` is the only server-side filter.** `hardware`, `framework` and `precision` are
   silently ignored — a request with `&hardware=h100` returns every hardware. The published
   citation format `?model=<slug>&hardware=<slug>` is therefore *misleading but harmless*: the
   `hardware` term never did anything, and the repo's tables were built by filtering the full
   response client-side. Filter client-side and the numbers reproduce (§6, §7).
2. **The `model` value is the InferenceX *display name*, not a URL slug and not the DB key.**
   `gpt-oss-120b` worked all along because its display name happens to look like a slug. The
   others need `DeepSeek-V4.1-Flash`, `Llama-3.3-70B-Instruct-FP8`, `DeepSeek-R1-0528`, etc.
   Full map in §3. Neither the site route slug (`deepseek-v41-flash`) nor the DB key (`dsv41flash`)
   is accepted.
3. **One model maps to several DB keys.** `Kimi-K2.5` returns `kimik2.5` *and* `kimik2.6`;
   `GLM-5` returns `glm5` *and* `glm5.1`. Split on the row's own `model` field, not on the
   request.
4. **`/api/v1/availability` is the cheap index** — one small row per
   (model, isl, osl, precision, hardware, framework, spec_method, disagg, benchmark_type, date)
   with **DB keys**, no metrics. Use it to discover what exists, then `benchmarks` for numbers.
5. Both tables the fact-checker could not verify now re-fetch and **match**: the §9.3 MBU inputs
   and the §10.1 DeepSeek-V4.1-Flash table in `h100.md` (§7.1, §7.2).

---

## 1. Endpoints

All are `GET`, unauthenticated, JSON. Verified live 2026-09-19.

| Endpoint | Status | Shape | Notes |
|---|---|---|---|
| `/benchmarks?model=<display-name>` | 200 | list of run rows | The only one with metrics. `model` **required** — omit it and you get `400 {"error":"Unknown model"}` |
| `/benchmarks/history?model=&isl=&osl=` | 200 | list of run rows | Same row shape, every historical curve rather than the latest per config. ~12 MB for `gpt-oss-120b` at 1k/1k |
| `/availability` | 200, ~1.2 MB | 6 729 rows | Index of what exists; **DB keys**, no metrics |
| `/evaluations` | 200 | list | Accuracy evals per config |
| `/reliability` | 200 | list | `{hardware, date, n_success, total}` |
| `/submissions` | 200 | `{summary: [...]}` | Community submissions |
| `/framework-releases` | 200 | `{"vllm":"v0.29.0","sglang":"v0.5.20"}` | Pinned engine versions |
| `/latest-images` | 200 | list | Newest container image per (model, hardware, framework, precision, spec_method) |
| `/workflow-info?date=<YYYY-MM-DD>` | 200 | `{runs: [...]}` | GitHub Actions run metadata for that sweep |
| `/feedback/list` | 200 | `{rows: [...]}` | Site feedback; ciphertext bodies |
| `/models`, `/hardware`, `/metadata`, `/filters` | **404** | — | Do not exist. There is no slug-discovery endpoint |

Two further endpoints appear in the site bundle but were not exercised here —
`/collectivex/runs[/{id}]` and `/eval-samples[-live]`, plus `/resident-sequence-lengths?ids=`
and the non-versioned `/api/unofficial-run?runId=`. **⚠️ TO BE VERIFIED.**

### 1.1 Transport gotcha

Some responses come back **gzip-encoded even when `Accept-Encoding: identity` is sent**
(`DeepSeek-V4.1-Flash` does; `gpt-oss-120b` does not). `curl --compressed` handles it; a raw
`urllib` read does not, and fails with `UnicodeDecodeError: ... byte 0x8b in position 1`. The
snippet in §6 sniffs the gzip magic and decompresses.

---

## 2. Parameters of `/benchmarks`

Taken from `fetchBenchmarks` in the site's JS bundle and confirmed against the live endpoint:

```js
// _next/static/immutable/chunks/3y7hzww4juur-.js
"fetchBenchmarks", function (e, t, r, n, i, o, l) {
  let s = new URLSearchParams({ model: e });
  t && s.set("date", t);
  r && s.set("exact", "true");
  i && s.set("runId", i);
  o && s.set("exactRun", "true");
  l && (s.set("view", l.type), s.set("sequence", l.sequence));
  return a(`/api/v1/benchmarks?${s}`, n);
}
```

| Param | Required | Effect (measured) |
|---|---|---|
| `model` | **yes** | Display name (§3). Anything else → `400 {"error":"Unknown model"}` |
| `date` | no | **No effect on its own** — `?model=gpt-oss-120b&date=2026-06-05` still returns all 429 rows across all 13 dates |
| `exact` | no | `exact=true` makes `date` bite: `date=2026-06-05&exact=true` → 20 rows, `date=2026-05-17&exact=true` → 85 rows, all on that date. A date with no runs → `[]`, **not** an error |
| `runId` + `exactRun` | no | Filter to one workflow run. `runId=790&exactRun=true` returned `[]` for `gpt-oss-120b` — the id space is per-model. **⚠️ TO BE VERIFIED** which id it wants (`workflow_run_id` vs the Actions run id) |
| `view`, `sequence` | no | Accepted, **no observable effect** on the returned set (429 rows with and without). Presentation hints |
| `hardware`, `framework`, `precision` | — | **Not parameters.** Accepted and ignored. Filter client-side |

`/benchmarks/history` takes `model`, `isl`, `osl` (required) plus optional `benchmarkType`
and `view`.

### 2.1 Row shape

Flat run metadata plus a nested `metrics` object of **101 keys**. Metadata:
`id, hardware, framework, model, precision, spec_method, disagg, is_multinode,
prefill_tp/ep/dp_attention/num_workers, decode_tp/ep/dp_attention/num_workers,
num_prefill_gpu, num_decode_gpu, benchmark_type, offload_mode, isl, osl, conc, image,
recipe_fingerprint, workers, power_invalid_reasons, power_audit, date, workflow_run_id,
run_started_at, run_url, curve_date, curve_workflow_run_id, curve_run_started_at`.

The metrics the repo's tables use: `tput_per_gpu`, `output_tput_per_gpu`, `input_tput_per_gpu`,
`median_intvty`, `median_ttft`, `median_tpot`, `median_itl`, `median_e2el`,
`kv_cache_pool_tokens`; plus `mean_*`/`std_*`/`p75_*`/`p90_*`/`p95_*` variants, power
(`avg_total_gpu_power_w`, `joules_per_output_token`, `total_gpu_energy_j`, `power_valid`) and
cache-hit rates.

> **`isl` and `osl` are `null` on `benchmark_type: "agentic_traces"` rows** — the trace supplies
> the lengths. Client-side code that filters on `isl == 1024` silently drops every agentic row,
> including the whole DeepSeek-V4.1-Flash dataset.

---

## 3. Validated model slug map

Source of truth is `COMPARE_MODEL_SLUGS` in the site bundle
(`_next/static/immutable/chunks/3y7hzww4juur-.js`). Every row below was issued as a live
request on 2026-09-19; "rows" is what came back.

| `model=` (send this) | Site route slug (**not accepted**) | DB keys in `model` field | Rows | Hardware present |
|---|---|---|---|---|
| `DeepSeek-R1-0528` | `deepseek-r1` | `dsr1` | 1 310 | b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x |
| `DeepSeek-V4-Pro` | `deepseek-v4` | `dsv4` | 896 | b200, b300, gb200, gb300, h200, mi300x, mi325x, mi355x, vr200 |
| `DeepSeek-V4.1-Flash` | `deepseek-v41-flash` | `dsv41flash` | 98 | b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x |
| `GLM-5` | `glm-5-1` | `glm5`, `glm5.1` | 512 | b200, b300, gb200, gb300, h200, mi325x, mi355x |
| `GLM-5.2` | `glm-5-3` | `glm5.2` | 89 | b200, b300, gb200, gb300, h200, mi325x, mi355x |
| `Kimi-K2.5` | `kimi-k26` | `kimik2.5`, `kimik2.6`, (`kimik2.7-code`) | 372 | b200, b300, gb200, gb300, h200, mi300x, mi325x, mi355x |
| `Kimi-K3` | `kimi-k3` | `kimik3` | 68 | b200, b300, gb200, gb300, h200, mi355x |
| `Llama-3.3-70B-Instruct-FP8` | `llama-3-3-70b` | `llama70b` | 681 | b200, h100, h200, mi300x, mi325x, mi355x |
| `MiniMax-M2.5` | `minimax-m27` | `minimaxm2.5`, (`minimaxm2.7`) | 704 | b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x |
| `MiniMax-M3` | `minimax-m3` | `minimaxm3` | 1 269 | b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x |
| `Qwen-3.5-397B-A17B` | `qwen-3-5` | `qwen3.5` | 949 | b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x, **rtx6000pro** |
| `Qwen3.8-Flash-Next` | `qwen-3-8-flash-next` | `qwen3.8next` | 19 | b200, b300, h100, h200 |
| `gpt-oss-120b` | `gptoss-120b` | `gptoss120b` | 429 | b200, gb200, h100, h200, mi300x, mi325x, mi355x |
| `Qwen3.8-27B` | `qwen-3-8-27b` | `qwen3.827b` | **0** | — (slug valid, dataset empty) |
| `Qwen3.8-27B-Eager` | `qwen-3-8-27b-eager` | `qwen3.827beager` | **0** | — (slug valid, dataset empty) |

`Qwen3.8-27B` and `Qwen3.8-27B-Eager` return `200 []`: the model is registered but has no
published runs, and `qwen3.827b` appears nowhere in `/availability`. Any repo claim quoting
Qwen3.8-27B InferenceX *numbers* is **⚠️ TO BE VERIFIED** against some other source — this API
has none. `kimik2.7-code` and `minimaxm2.7` are likewise registered DB keys absent from
`/availability`.

**Things that do *not* work** (all `400 {"error":"Unknown model"}`, tested): the DB keys
(`dsr1`, `llama70b`, `gptoss120b`, `dsv41flash`, …); the site route slugs (`deepseek-r1`,
`gptoss-120b`, `llama-3-3-70b`, …); HuggingFace-style ids; and hyphen/dot variants
(`minimax-m2-5`, `deepseek-v4-1-flash`). There is no fuzzy matching — the string must be the
display name **exactly**, including case and dots.

### 3.1 Hardware, framework and precision values

Not request parameters — these are the values to filter rows on, enumerated from
`/availability` (the authoritative set, since `/benchmarks` only ever shows one model's slice):

- **hardware** — `b200`, `b300`, `gb200`, `gb300`, `h100`, `h200`, `mi300x`, `mi325x`,
  `mi355x`, `rtx6000pro`, `vr200`. No `a100` (consistent with `a100.md` §-note that A100 is not
  in the suite), no `mi350x`, no `h20`.
- **framework** — `vllm`, `sglang`, `trt`, `atom`, `tilert`, `vllm-disagg`, `atom-disagg`,
  `dynamo-vllm`, `dynamo-sglang`, `dynamo-trt`, `llmd-vllm`, `mori-sglang`, `mooncake-atom`.
- **precision** — `fp4`, `fp8`, `bf16`, `int4`.
- **spec_method** — `none`, `mtp`, `draft_model`. **benchmark_type** — `single_turn`,
  `agentic_traces`. **isl/osl** — `1024/1024`, `1024/8192`, `8192/1024`, or `null/null` for
  agentic traces.

The benchmark repo moved: `github.com/InferenceMAX/InferenceMAX` is now an empty stub reading
*"Repo moved to SemiAnalysisAI/InferenceX"*
[src](https://api.github.com/repos/InferenceMAX/InferenceMAX). The live repo
[`SemiAnalysisAI/InferenceX`](https://github.com/SemiAnalysisAI/InferenceX) (1 994 paths) lays
its recipes out as `benchmarks/multi_node/srt-slurm-recipes/<db-key>/<framework>/<hw>-<prec>/<isl><osl>/...`
— e.g. `dsr1/sglang/b200-fp4/1k1k/...`. Those directory names are the **DB keys**, so the repo
confirms §3's right-hand column but is *not* a source of API slugs.

---

## 4. Example response, one per hardware slug

Model: **DeepSeek-V4.1-Flash** (the repo's headline case; present on 9 of the 11 hardware
slugs). Request: `GET /api/v1/benchmarks?model=DeepSeek-V4.1-Flash` → 98 rows. Highest
`tput_per_gpu` row per hardware, 2026-09-19:

| hardware | `id` | framework | prec | spec | TP | conc | date | `tput_per_gpu` | `output_tput_per_gpu` | `median_intvty` |
|---|---|---|---|---|---|---|---|---|---|---|
| `b200` | 442167 | vllm | fp4 | mtp | 4 | 64 | 2026-09-15 | 92 073.4 | 669.6 | 98.1 |
| `b300` | 442379 | vllm | fp4 | mtp | 2 | 64 | 2026-09-16 | 155 834.5 | 1 158.1 | 75.5 |
| `gb200` | 442183 | vllm | fp4 | mtp | 4 | 64 | 2026-09-15 | 84 119.3 | 610.3 | 76.0 |
| `gb300` | 442262 | vllm | fp4 | mtp | 4 | 128 | 2026-09-16 | 102 318.2 | 887.1 | 39.8 |
| `h100` | 442505 | vllm | fp4 | mtp | 8 | 20 | 2026-09-18 | 12 673.2 | 89.8 | 109.8 |
| `h200` | 442212 | vllm | fp4 | mtp | 8 | 64 | 2026-09-16 | 22 637.9 | 186.4 | 33.8 |
| `mi300x` | 442496 | vllm | fp4 | mtp | 8 | 32 | 2026-09-18 | 12 124.4 | 85.9 | 41.3 |
| `mi325x` | 442426 | vllm | fp4 | mtp | 8 | 32 | 2026-09-18 | 15 287.6 | 103.1 | 55.1 |
| `mi355x` | 441920 | vllm | fp4 | mtp | 4 | 32 | 2026-09-13 | 40 919.4 | 259.1 | 89.8 |

`rtx6000pro` and `vr200` have **no** DeepSeek-V4.1-Flash rows. For a `rtx6000pro` example use
`model=Qwen-3.5-397B-A17B`; for `vr200` use `model=DeepSeek-V4-Pro` — those are the only models
present on each, per `/availability`.

These are **agentic-traces** rows, so `tput_per_gpu` counts cached prefill and is an order of
magnitude above the single-turn figures — do not compare across `benchmark_type`.

One full row verbatim (`id` 442505, the h100 row above, `metrics` elided to the used keys):

```json
{
  "id": "442505", "hardware": "h100", "framework": "vllm", "model": "dsv41flash",
  "precision": "fp4", "spec_method": "mtp", "disagg": false, "is_multinode": false,
  "prefill_tp": 8, "decode_tp": 8, "num_prefill_gpu": 8, "num_decode_gpu": 8,
  "benchmark_type": "agentic_traces", "offload_mode": "off",
  "isl": null, "osl": null, "conc": 20,
  "image": "vllm/vllm-openai:nightly-cd10ed6f9f6b37a8ace9cf380007e66fe12ec0c3",
  "recipe_fingerprint": "14b25a2cfc8d332059bddbe4aecf4e07019c25bc973734eca489c4db8a2bd717",
  "date": "2026-09-18", "workflow_run_id": "2509",
  "run_started_at": "2026-09-18 22:35:24+00",
  "run_url": "https://github.com/SemiAnalysisAI/InferenceX/actions/runs/35314892357/attempts/1",
  "metrics": {
    "tput_per_gpu": 12673.2, "output_tput_per_gpu": 89.8,
    "median_intvty": 109.8, "median_ttft": 0.358, "median_tpot": 0.0091
  }
}
```

---

## 5. Citation format to use from here on

The `&hardware=` term in the existing citations never filtered anything. When adding new
citations, either cite the request that was actually made:

```
[src](https://inferencex.semianalysis.com/api/v1/benchmarks?model=DeepSeek-V4.1-Flash)
```

and state the client-side filter in prose ("h100, vLLM, agentic traces, 2026-09-18"), or cite
the run directly via the row's own `run_url`. Existing `&hardware=` citations are not *wrong* —
the URL resolves and contains the row — merely over-specified. No GPU document was edited by
this work.

---

## 6. Fetch-and-tabulate snippet

```python
#!/usr/bin/env python3
"""Fetch + tabulate SemiAnalysis InferenceX benchmark rows."""
import gzip, json, urllib.request, urllib.parse

BASE = "https://inferencex.semianalysis.com/api/v1"

def fetch(model, **params):
    """model = InferenceX DISPLAY NAME, e.g. 'DeepSeek-V4.1-Flash'.
    The server filters on `model` only; `date` needs `exact="true"` to bite."""
    q = urllib.parse.urlencode({"model": model, **params})
    req = urllib.request.Request(f"{BASE}/benchmarks?{q}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":          # gzipped regardless of Accept-Encoding
        raw = gzip.decompress(raw)
    return json.loads(raw)

def rows(model, **where):
    """where: hardware=/framework=/precision=/isl=/conc=/date=... filtered CLIENT-SIDE.
    NB: agentic_traces rows carry isl=osl=None -- do not filter on isl for those."""
    return [r for r in fetch(model)
            if all(r.get(k) == v for k, v in where.items())]

def table(rs):
    hdr = ["isl", "osl", "tp", "conc", "tput/gpu", "out/gpu", "intvty", "ttft_s", "tpot_s"]
    print(" ".join(f"{h:>9}" for h in hdr))
    for r in sorted(rs, key=lambda r: (r["isl"] or 0, r["osl"] or 0,
                                       r["decode_tp"], r["conc"])):
        m = r["metrics"]
        for x in (r["isl"], r["osl"], r["decode_tp"], r["conc"],
                  m["tput_per_gpu"], m["output_tput_per_gpu"], m["median_intvty"],
                  m["median_ttft"], m["median_tpot"]):
            print(f"{x:>9.4g}" if isinstance(x, float) else f"{x!s:>9}", end=" ")
        print()

if __name__ == "__main__":
    # h100.md 10.1, gpt-oss-120b table
    table(rows("gpt-oss-120b", hardware="h100", framework="vllm", date="2026-05-17"))
    # h100.md 10.1, DeepSeek-V4.1-Flash agentic table
    table(rows("DeepSeek-V4.1-Flash", hardware="h100", date="2026-09-18"))
```

---

## 7. Which repo documents cite InferenceX, and are the rows re-fetchable?

Every model cited by a GPU document maps to a working slug, so **every InferenceX table in the
repo is now re-fetchable**. The column below records only whether the *slug* resolves — it is
not a claim that each table's cells were re-checked. Only the two tables in §7.1/§7.2 were.

| Document | InferenceX section(s) | Models cited | Slug resolves? | Cells re-checked here? |
|---|---|---|---|---|
| [`gpus/h100.md`](../gpus/h100.md) | §9.3, §10.1 (+ §5 image list, §8.3 TCO rates) | gpt-oss-120b, Llama-3.3-70B, DeepSeek-V4.1-Flash, MiniMax-M2.5, MiniMax-M3, DeepSeek-R1 | ✅ all six | ✅ §9.3 inputs + both §10.1 tables (§7.1, §7.2) |
| [`gpus/b200.md`](../gpus/b200.md) | §10.2 | gpt-oss-120b, DeepSeek-V4.1-Flash, DeepSeek-R1, Kimi-K3, MiniMax-M3, Llama-3.3-70B, Qwen3.8-27B | ✅ except **Qwen3.8-27B → 0 rows** | ❌ |
| [`gpus/b300.md`](../gpus/b300.md) | "SemiAnalysis InferenceX — B300" ×2 | DeepSeek-R1-0528, DeepSeek-V4-Pro, DeepSeek-V4.1-Flash, gpt-oss-120b | ✅ all four | ❌ |
| [`gpus/gb300.md`](../gpus/gb300.md) | §10.5 | DeepSeek-V4.1-Flash, DeepSeek-R1, gpt-oss-120b, Kimi-K3, Qwen3.8-27B | ✅ except **Qwen3.8-27B → 0 rows** | ❌ |
| [`gpus/h200.md`](../gpus/h200.md) | "InferenceMAX / InferenceX" | gpt-oss-120b, DeepSeek-V4.1-Flash, GLM-5, Kimi-K3, Llama-3.3-70B | ✅ all five | ❌ |
| [`gpus/mi355x.md`](../gpus/mi355x.md) | scattered | gpt-oss-120b, GLM-5.2, Kimi-K2.5/K2.6, DeepSeek-V4.1-Flash, DeepSeek-R1 | ✅ (K2.5 **and** K2.6 both arrive under `Kimi-K2.5`) | ❌ |
| [`gpus/rtx6000-pro.md`](../gpus/rtx6000-pro.md) | scattered | gpt-oss-120b, Qwen-3.5-397B-A17B, DeepSeek-V4.1-Flash, Kimi-K3, GLM-5.x, Qwen3.8-27B | ⚠️ slugs resolve, but **`rtx6000pro` has runs for `Qwen-3.5-397B-A17B` only** (2 rows) — any other model's RTX 6000 Pro row cannot come from this API | ❌ |
| [`gpus/a100.md`](../gpus/a100.md) | §-note only | — (states A100 is *absent* from the suite) | ✅ confirmed — no `a100` in `/availability` | ✅ (the absence claim) |
| [`cross-cutting/serving-optimizations.md`](serving-optimizations.md) | MI355X run #3058 | — | ⚠️ cites a run **id**, not a model slug | ❌ |
| [`cross-cutting/quantization-formats.md`](quantization-formats.md), [`inference-engines.md`](inference-engines.md), [`cloud-pricing.md`](cloud-pricing.md) | passing references | — | n/a | ❌ |

`mi355x.md` and `rtx6000-pro.md` contain no `inferencex.semianalysis.com/api` URL at all — they
reference the suite by name. **⚠️ TO BE VERIFIED** where their numeric rows came from.

### 7.1 Re-fetch of `h100.md` §9.3 — **MATCHES**

§9.3's MBU table is Llama-3.3-70B-Instruct-FP8, vLLM, ISL 1024 / OSL 8192, run 2025-10-29 — not
gpt-oss-120b. `rows("Llama-3.3-70B-Instruct-FP8", hardware="h100", isl=1024, osl=8192)` returns
**15 rows, all dated 2025-10-29**, TP ∈ {2,4,8} × conc ∈ {4,8,16,32,64}.

| §9.3 config | doc intvty | API `median_intvty` | doc TPOT | API `median_tpot` |
|---|---|---|---|---|
| TP2, conc 4 | 55.6 | 55.64 | 17.99 ms | 17.973 ms |
| TP2, conc 64 | 26.8 | 26.77 | 37.31 ms | 37.357 ms |
| TP4, conc 4 | 83.3 | 83.32 | 12.00 ms | 12.002 ms |
| TP4, conc 64 | 42.3 | 42.34 | 23.64 ms | 23.617 ms |
| TP8, conc 4 | 108.7 | 108.69 | 9.20 ms | 9.200 ms |
| TP8, conc 64 | 60.1 | 60.08 | 16.64 ms | 16.644 ms |

All six interactivity figures match to the printed precision. The ≤0.05 ms TPOT gaps are not a
discrepancy: the document derived TPOT as `1000 / intvty` from its **own rounded** interactivity
(`1/55.6 = 17.986 → 17.99`, `1/26.8 = 37.313 → 37.31`, `1/42.3 = 23.64`), which is internally
consistent and reproduces every printed cell. **The §9.3 ⚠️ TO BE VERIFIED marker can be
lifted** — its inputs re-fetch. Note the API *does* carry TP4 rows, so the document's remark
that "the TP4 rows have no counterpart in the §10.1 Llama table" is a gap in §10.1's coverage,
not evidence against the TP4 inputs.

### 7.2 Re-fetch of `h100.md` §10.1 DeepSeek-V4.1-Flash — **MATCHES, all cells**

`rows("DeepSeek-V4.1-Flash", hardware="h100")` → 12 rows, all `fp4` / `mtp` / `agentic_traces`
/ 2026-09-18 / TP8, on `vllm/vllm-openai:nightly-cd10ed6f…` and `lmsysorg/sglang:dev-dsv41` —
matching the heading exactly.

| Engine | conc | doc tput/GPU → API | doc out/GPU → API | doc intvty → API | doc TTFT → API | doc TPOT → API |
|---|---|---|---|---|---|---|
| vLLM MTP | 1 | 2 685 → 2 685.3 | 18.8 → 18.8 | 341 → 341.3 | 0.480 → 0.480 | 2.9 → 2.9 ms |
| vLLM MTP | 8 | 7 093 → 7 092.9 | 51.6 → 51.6 | 241 → 241.0 | 0.298 → 0.298 | 4.2 → 4.2 ms |
| vLLM MTP | 16 | 11 242 → 11 242.0 | 78.7 → 78.7 | 154 → 154.1 | 0.306 → 0.306 | 6.5 → 6.5 ms |
| vLLM MTP | **20** | 12 673 → 12 673.2 | 89.8 → 89.8 | 110 → 109.8 | 0.358 → 0.358 | 9.1 → 9.1 ms |
| vLLM MTP | 24 | 10 339 → 10 339.3 | 76.8 → 76.8 | 80 → 80.0 | 0.404 → 0.404 | 12.5 → 12.5 ms |
| vLLM MTP | 28 | 2 816 → 2 815.9 | 19.6 → 19.6 | 15 → 15.1 | 2.502 → 2.502 | 66.1 → 66.1 ms |
| SGLang MTP | 1 | 1 139 → 1 138.5 | 9.2 → 9.2 | 114 → 113.9 | 1.979 → 1.979 | 8.8 → 8.8 ms |
| SGLang MTP | 8 | 1 530 → 1 530.1 | 11.1 → 11.1 | 30 → 30.3 | 1.065 → 1.065 | 33.0 → 33.0 ms |

Every cell matches. The conc-24 → conc-28 cliff is real in the source data. The API also holds
**four rows the document omits** — vLLM conc 2 (2 290.8 / 19.7 / 266.7) and conc 4 (4 055.6 /
28.0 / 322.6), SGLang conc 2 (1 055.6 / 8.2 / 103.1) and conc 4 (630.9 / 4.8 / 41.0). The vLLM
conc-2 dip and the SGLang conc-4 dip are in the data, not transcription errors; the document's
smooth-looking curve is a consequence of the omission. Worth noting if that table is ever
re-cut. **No edit made here.**

### 7.3 Re-fetch of `h100.md` §10.1 gpt-oss-120b — **MATCHES** (unchanged from the earlier pass)

`rows("gpt-oss-120b", hardware="h100", framework="vllm", date="2026-05-17")` → 28 rows,
reproducing all eight 1k/1k and 8k/1k cells (e.g. 1k/1k TP2 c64 = 4 159.54 / 2 079.30 / 66.59 /
0.0871 / 0.01502; 8k/1k TP2 c64 = 10 284.14 / 1 140.94 / 36.66 / 0.4219 / 0.02727).

The document's **run-provenance correction is confirmed**: the 2026-05-17 sweep contains **no
1024/8192 rows at all**. The only H100 1k/8k gpt-oss-120b data is dated **2026-03-27** on
`vllm/vllm-openai:v0.18.0`, TP2 conc 64 = `tput_per_gpu` 2 442.65, `output_tput_per_gpu`
2 171.51, `median_intvty` 69.04, `median_ttft` 0.0736, `median_tpot` 0.01448 — exactly the
figures the correction gives.

---

## 8. Open items

- **⚠️ TO BE VERIFIED** — `runId` / `exactRun` semantics. `runId=790&exactRun=true` returned
  `[]` for `gpt-oss-120b` though `790` is a real `curve_workflow_run_id` on `mi300x` rows.
- **⚠️ TO BE VERIFIED** — `/collectivex/runs`, `/eval-samples`, `/eval-samples-live`,
  `/resident-sequence-lengths`, `/api/unofficial-run` were found in the site bundle but not
  exercised.
- **⚠️ TO BE VERIFIED** — whether the display-name slugs are stable. They are UI strings
  (`COMPARE_MODEL_SLUGS[].displayName`) with no versioning or `/models` endpoint behind them, so
  a site copy change could break every stored URL. Re-derive from the bundle if a slug starts
  returning `400`; the array is greppable as `slug:"…",displayName:"…",dbKeys:[…]`.
- **⚠️ TO BE VERIFIED** — the numeric InferenceX rows in `mi355x.md` and `rtx6000-pro.md`, which
  cite no API URL; and specifically whether `rtx6000-pro.md`'s non-Qwen-3.5 rows can come from
  this API at all (§7, they cannot).
- `Qwen3.8-27B` / `Qwen3.8-27B-Eager` are registered but empty. Repo claims quoting InferenceX
  numbers for them need another source.
