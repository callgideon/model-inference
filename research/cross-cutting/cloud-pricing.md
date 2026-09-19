# GPU Cloud Pricing and TCO — reference as of 2026-09-19

Companion to [`research/METHODOLOGY.md`](../METHODOLOGY.md). This document supplies the
`price_per_gpu_hour` inputs to METHODOLOGY §6 (`cost_per_hour = n_gpus ×
price_per_gpu_hour`) and the capital/power inputs for on-prem comparisons.

**Research date: 2026-09-19.** Every price row carries a fetch date and a source
link. Where a vendor publishes no price, the row says so explicitly rather than
guessing. Estimates carry **⚠️ TO BE VERIFIED** with the estimation method stated
inline, per the METHODOLOGY legend.

**Reading rules used throughout**

| Convention | Meaning |
|---|---|
| `$/GPU-hr` | Instance price ÷ GPUs per instance. Includes the bundled CPU, RAM, local NVMe and NIC — it is *not* a GPU-only price. |
| SXM vs PCIe | Never mixed. Every row names the form factor. |
| Dense vs sparse | Never mixed. Marketing sheets usually quote sparse; all normalised math below uses **dense**. |
| "Hyperscaler" | AWS, GCP, Azure, OCI list price, public, self-serve (subject to quota). |
| "Neocloud" | CoreWeave, Lambda, Nebius, Crusoe, Together, Hyperstack, Voltage Park, DataCrunch/Verda, Fluidstack, SF Compute, TensorWave, Hot Aisle, Vultr, DigitalOcean, Scaleway. |
| "Marketplace" | RunPod Community, Vast.ai — host-set prices, no SLA. |
| ⚠️ TO BE VERIFIED | No primary source found as of 2026-09-19, or sources conflict. |

---

## 1. Data provenance and what could not be sourced

### 1.1 Primary machine-readable sources used

These were pulled as raw JSON/HTML on 2026-09-19, not read off a blog:

| Source | What it gave | Endpoint |
|---|---|---|
| AWS public price sheet (gzipped JSON) | Exact on-demand $/hr for every P/G instance in us-east-1, us-east-2, us-west-2 | [`b0.p.awsstatic.com/.../ec2-ondemand-without-sec-sel/US East (N. Virginia)/Linux/index.json`](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) |
| Azure Retail Prices API | On-demand, spot, low-priority, and 1y/3y/5y reservation totals per VM SKU per region | [`prices.azure.com/api/retail/prices`](https://prices.azure.com/api/retail/prices) |
| Oracle OCI public price-list API | Per-GPU-hour list price for every OCI GPU SKU incl. B300, GB300, MI355X, RTX PRO 6000 | [`apexapps.oracle.com/pls/apex/cetools/api/v1/products/`](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD) |
| GCP accelerator-optimized price sheet (server-rendered HTML tables) | On-demand, DWS Flex-start, DWS Calendar, Spot, Resource CUD 1y/3y for A2/A3/A4/G2/G4 | [`cloud.google.com/products/compute/pricing/accelerator-optimized`](https://cloud.google.com/products/compute/pricing/accelerator-optimized) |

Everything else came from vendor pricing pages fetched the same day.

### 1.2 What is genuinely unavailable as of 2026-09-19

| Item | Status |
|---|---|
| AWS standard Reserved Instance rates for p5en, p6-b200, p6-b300, p6e-gb200, g7e | **Not offered.** Vantage shows `N/A` for 1y and 3y RI on all of them ([p6-b200](https://instances.vantage.sh/aws/ec2/p6-b200.48xlarge), [p6-b300](https://instances.vantage.sh/aws/ec2/p6-b300.48xlarge), [p5en](https://instances.vantage.sh/aws/ec2/p5en.48xlarge), [g7e](https://instances.vantage.sh/aws/ec2/g7e.48xlarge)). AWS sells this capacity through **Capacity Blocks for ML** and Savings Plans instead. |
| GCP on-demand list price for `a4-highgpu-8g` (B200) | Published as **N/A** on Google's own price sheet — only DWS Flex-start, DWS Calendar, Spot and CUD prices exist ([GCP](https://cloud.google.com/products/compute/pricing/accelerator-optimized)). |
| GCP A4X (GB200) and A4X Max (GB300) prices | **Absent from the public accelerator-optimized price sheet entirely.** The docs say A4X/A4X Max require attached reservations ([GCP docs](https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines)). ⚠️ TO BE VERIFIED — quote-only. |
| Azure ND B200 v6 / ND GB300 v6 retail price | **No SKU returned** by the Retail Prices API for `contains(armSkuName,'B200')` other than `Standard_ND128isr_NDR_GB200_v6`, and zero rows for `contains(armSkuName,'GB300')`, checked 2026-09-19. Azure routes GB300 to "request a quote". |
| CoreWeave GB300 NVL72 and HGX B300 on-demand | Listed as **"Contact sales"**; only a spot rate is published for HGX B300 ([CoreWeave](https://www.coreweave.com/pricing)). |
| Crusoe B200 / GB200 / MI355X | **"Contact sales"** on Crusoe's own page ([Crusoe](https://www.crusoe.ai/cloud/pricing)). |
| Fluidstack public rate card | `fluidstack.io/pricing` returns **HTTP 404** (2026-09-19). Fluidstack is quote-only. ⚠️ TO BE VERIFIED |
| Vast.ai live per-GPU medians | `console.vast.ai/api/v0/bundles/` returned 404 for both GET and PUT from this environment on 2026-09-19. Ranges below come from a third-party snapshot and are labelled as such. |
| TensorWave rate card | `tensorwave.com/pricing` returns **HTTP 404**; the site routes to "Talk to Sales" ([TensorWave](https://tensorwave.com/)). Quoted rates below are third-party. |
| All hardware purchase prices | **No vendor publishes list prices for HGX/DGX/NVL72 systems.** Every number in §7 is trade-press or integrator-quote derived and is labelled as such. |
| Colocation $/kW-month | Broker/analyst ranges only; CBRE's raw H2-2025 figure reaches us second-hand. |

---

## 2. Hardware reference used for normalisation

All per-GPU. **Dense** figures are what §10 normalises against. Where a vendor
page only publishes the sparse number, the dense value is shown as *half* and the
derivation is stated — that is an inference, not a datasheet value, and is flagged.

### 2.1 NVIDIA

| GPU | Form factor | HBM (GB) | BW (TB/s) | Dense BF16 (TFLOPS) | Dense FP8 (TFLOPS) | Dense FP4 (TFLOPS) | TDP (W) | Source |
|---|---|---|---|---|---|---|---|---|
| A100 40GB | SXM4 | 40 | 1.555 | 312 | — (no FP8) | — | 400 | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) |
| A100 40GB | PCIe | 40 | 1.555 | 312 | — | — | 250 | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) |
| A100 80GB | SXM4 | 80 | 2.039 | 312 | — | — | 400 | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) |
| A100 80GB | PCIe | 80 | 1.935 | 312 | — | — | 300 | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) |
| H100 | SXM5 | 80 | 3.35 | 989.5 | 1,979 | — | 700 | [NVIDIA H100](https://www.nvidia.com/en-us/data-center/h100/) — page quotes 3,958 TFLOPS FP8 **with sparsity**; dense = ÷2 |
| H100 NVL | PCIe dual | 94 | 3.9 | 835.5 | 1,670.5 | — | 350–400 | [NVIDIA H100](https://www.nvidia.com/en-us/data-center/h100/) — 3,341 sparse ÷2 |
| H200 | SXM5 | 141 | 4.8 | 989.5 | 1,979 | — | up to 700 | [NVIDIA H200](https://www.nvidia.com/en-us/data-center/h200/) — 3,958 sparse ÷2 |
| H200 NVL | PCIe dual | 141 | 4.8 | 835.5 | 1,670.5 | — | up to 600 | [NVIDIA H200](https://www.nvidia.com/en-us/data-center/h200/) |
| B200 | SXM6 (HGX) | 180 | 8.0 | 2,250 | 4,500 | 9,000 | ~1,000 ⚠️ | [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/): HGX B200 8-GPU = 144 PFLOPS FP4 sparse / **72 dense**, 72 PFLOPS FP8 sparse. ÷8 per GPU. |
| B200 | GB200 Superchip | 186 | 8.0 | 2,500 | 5,000 | 10,000 | 1,200 ⚠️ | [NVIDIA GB200 NVL72](https://www.nvidia.com/en-us/data-center/gb200-nvl72/): 720 PFLOPS FP4 dense / 360 PFLOPS FP8 dense across 72 GPUs |
| B300 | SXM (HGX B300 / DGX B300 / AWS p6-b300) | **268** (2,144 GB per 8-GPU node) | 8.0 | 2,250 | 4,500 | 13,500 | 1,100–1,300 ⚠️ | As-deployed figure pinned by [METHODOLOGY §8](../METHODOLOGY.md) / [gpus/b300.md](../gpus/b300.md), corroborated by [AWS p6-b300 launch blog](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) ("2144GB HBM3e", 268 GB/GPU). [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) prints 2.1 TB total (÷8 = 262.5, rounded-down nameplate) and 108 PFLOPS FP4 dense ÷8. |
| B300 | GB300 Superchip (NVL72) | 288 (≈ 279 usable) | 8.0 | 2,500 | 5,000 | 15,000 | **1,100** | [Lenovo GB300 NVL72](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) (288 GB/GPU, 18 PFLOPS FP4 *sparse* per GPU, **"1100W total graphics power per GPU"**); [NVIDIA DGX GB300](https://www.nvidia.com/en-us/data-center/dgx-gb300/) (1,080 PFLOPS FP4 dense ÷72 = 15). Dense BF16/FP8 pinned by [METHODOLOGY §8](../METHODOLOGY.md) / [gpus/gb300.md](../gpus/gb300.md). |
| RTX PRO 6000 Blackwell | Server Edition, PCIe Gen5 | 96 (GDDR7) | 1.597 | 480 `est.` | 960 `est.` | **1,920 `est.` (≈ 2,000)** | up to 600 | [NVIDIA RTX PRO 6000 SE](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) prints 4 PFLOPS FP4 / 2 PFLOPS FP8 / 1 PFLOP FP16 with **no sparsity footnote anywhere on the page** (re-fetched 2026-09-19, zero occurrences of "sparsit"). [gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md) reconciles those against the SM FLOP/clk rate and concludes they are the **sparse** figures; dense = ÷2. METHODOLOGY §8 pins FP4 ≈ 2,000 dense / 4,000 sparse. |

**The B300 memory number — settled by METHODOLOGY §8, not by this document.**
Vendor rate cards disagree, and it matters for KV-cache sizing (METHODOLOGY §3).
What the sources actually say:

| Source | B300 HBM per GPU |
|---|---|
| **[METHODOLOGY §8](../METHODOLOGY.md) / [gpus/b300.md](../gpus/b300.md) — pinned as-deployed** | **268 GB (2,144 GB per 8-GPU node)** |
| [AWS p6-b300 launch blog](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) — "2144GB HBM3e" ÷ 8 | 268 GB |
| [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) — HGX B300 total 2.1 TB ÷ 8 (rounded-down nameplate) | 262.5 GB |
| [AWS p6-b300.48xlarge](https://instances.vantage.sh/aws/ec2/p6-b300.48xlarge) — 2,100 GiB ÷ 8 | 262.5 GiB |
| [CoreWeave](https://www.coreweave.com/pricing) HGX B300 instance spec (re-fetched 2026-09-19) | 270 GB |
| [Together AI](https://www.together.ai/gpu-clusters) HGX B300 | 270 GB |
| [RunPod](https://www.runpod.io/pricing) B300 pod | 288 GB |
| [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) B300 | 288 GB |
| [Scaleway](https://www.scaleway.com/en/pricing/gpu/) `B300-SXM-8-288G` shape name | 288 GB |

**Plan HGX / DGX / p6-b300 at 268 GB per GPU = 2,144 GB per 8-GPU node** (METHODOLOGY §8).
The 288 GB a rate card prints is the die nameplate; 262.5 GB is NVIDIA's
rounded-down 2.1 TB board figure; 270 GB is CoreWeave's own reported slice. Every
normalised table in §10 uses 268. Confirm with `nvidia-smi` on the actual SKU
before committing a fit calculation, and quote the SKU-specific figure where a
specific SKU is at stake.

**GB300 NVL72 is a different part and its figures are never merged with HGX B300's.**
METHODOLOGY §8 pins **288 GB per GPU, ≈ 279 GB usable**.
[Lenovo](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai)
says "288 GB HBM3e per GPU" (72 × 288 = 20.7 TB);
[NVIDIA's DGX GB300 page](https://www.nvidia.com/en-us/data-center/dgx-gb300/)
states total GPU memory of **20 TB** (÷72 = **277.8 GB/GPU**) and
[CoreWeave](https://www.coreweave.com/pricing) lists its GB300 NVL72 slice at
**279 GB/GPU** — those are the usable-after-reservation figures for the same
288 GB nameplate. §10.1 uses 288 and states the 279-GB alternative inline.

**GB300 sparse FP4 — dense only, as always.** [NVIDIA DGX GB300](https://www.nvidia.com/en-us/data-center/dgx-gb300/)
gives 1,440 PFLOPS sparse / 1,080 PFLOPS dense (ratio 1.33, not 2.0), while
[Lenovo](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai)
gives 18 PFLOPS sparse per GPU (= 1,296 PFLOPS per rack). The dense 15 PFLOPS/GPU
figure is consistent across both and is what METHODOLOGY §8 pins; **use dense only.**

### 2.2 AMD

| GPU | HBM (GB) | BW (TB/s) | Dense BF16 (TFLOPS) | Dense FP8 (TFLOPS) | Dense FP4 (TFLOPS) | TBP (W) | Source |
|---|---|---|---|---|---|---|---|
| MI300X (OAM) | 192 (HBM3) | 5.3 | 1,300 | 2,610 (OCP-FP8) | — (no FP4) | 750 peak | [AMD MI300X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html) ✅ re-confirmed 2026-09-19 |
| MI325X (OAM) | 256 (HBM3E) | 6.0 | 1,300 | 2,610 | — | 1,000 peak | [AMD MI325X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html) ✅ re-confirmed 2026-09-19 |
| MI355X (OAM) | 288 (HBM3E) | 8.0 | 2,500 | 5,000 (MXFP8 & OCP-FP8) | 10,100 (MXFP4 **and** MXFP6) | 1,400 | [AMD MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html) ✅ re-confirmed 2026-09-19 |

**§2.2 re-pull, 2026-09-19 — all three pages now confirmed.** The earlier pass
recorded `amd.com` as timing out on four attempts and flagged the whole section
⚠️ TO BE VERIFIED. Re-fetched with a browser user-agent
(`curl -A "Mozilla/5.0 …" --max-time 60`): **HTTP 200 on all three product
pages.** Verbatim from the spec tables:

| Page | Confirmed verbatim |
|---|---|
| MI300X | Dedicated Memory Size **192 GB** (HBM3); Peak Memory Bandwidth **5.3 TB/s**; FP16 **1.3 PFLOPs** dense / 2.61 with structured sparsity; FP8 (E5M2, E4M3) **2.61 PFLOPs** dense / 5.22 sparse; INT8 2.6 POPs / 5.22 sparse; TBP **750 W Peak**. No FP4 row exists. |
| MI325X | Dedicated Memory Size **256 GB** (HBM3E); Peak Memory Bandwidth **6 TB/s**; FP16 **1.3 PFLOPs** dense; FP8 **2.61 PFLOPs** dense; TBP **1000 W Peak**. No FP4 row exists. |
| MI355X | Dedicated Memory Size **288 GB** (HBM3E); Peak Memory Bandwidth **8 TB/s**; BF16 Matrix **2.5 PFLOPs** dense / 5 sparse; OCP-FP8 Matrix **5 PFLOPs** dense / 10.1 sparse; MXFP8 Matrix **5 PFLOPs**; **MXFP4 Matrix 10.1 PFLOPs**; **MXFP6 Matrix 10.1 PFLOPs**; INT8 Matrix 5 POPs / 10.1 sparse; TBP **1400 W**. |

AMD lists sparsity variants as separate, explicitly labelled rows, so every figure
above is unambiguously dense. **The "no sparsity row published for MXFP4" claim is
confirmed, not suspect:** the MI355X page carries `… with Structured Sparsity`
rows for FP16, OCP-FP8 and INT8 but *none* for MXFP4 or MXFP6. The previous pass's
guess that "an FP4 20.1 PFLOPs sparse row probably does exist" is withdrawn — it
does not appear on the page. Plan MI355X FP4 at **10,100 TFLOPs dense**, matching
METHODOLOGY §8.
MI355X's **MXFP6 at 10.1 PFLOPs** — the same rate as MXFP4 — is unique to CDNA4
and relevant if a model ships MXFP6 weights.

---

## 3. Hyperscaler pricing

### 3.1 AWS EC2

On-demand prices are the **exact values from AWS's own price sheet**, us-east-1,
Linux, fetched 2026-09-19. Spot figures are from Vantage the same day (Vantage
reads the AWS spot price history; spot moves continuously, treat as a snapshot).

| Instance | GPUs | GPU | On-demand $/inst-hr | **$/GPU-hr** | Spot $/GPU-hr | RI 1y $/GPU-hr | RI 3y $/GPU-hr | Capacity Block $/accel-hr |
|---|---|---|---|---|---|---|---|---|
| `p4d.24xlarge` | 8 | A100 40GB SXM | [$21.9576](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$2.745** | [$2.083](https://instances.vantage.sh/aws/ec2/p4d.24xlarge) | [$1.740](https://instances.vantage.sh/aws/ec2/p4d.24xlarge) | [$1.172](https://instances.vantage.sh/aws/ec2/p4d.24xlarge) | [$1.475](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p4de.24xlarge` | 8 | A100 80GB SXM | [$27.4471](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$3.431** | ⚠️ n/a | ⚠️ n/a | ⚠️ n/a | [$2.214](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p5.4xlarge` | 1 | H100 80GB SXM | [$6.88](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$6.880** | ⚠️ n/a | ⚠️ n/a | ⚠️ n/a | [$4.72–5.191](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p5.48xlarge` | 8 | H100 80GB SXM | [$55.04](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$6.880** | [$2.598](https://instances.vantage.sh/aws/ec2/p5.48xlarge) | N/A | [$2.972](https://instances.vantage.sh/aws/ec2/p5.48xlarge) | [$5.191](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p5e.48xlarge` | 8 | H200 141GB SXM | **not listed in us-east-1** ⚠️ | ⚠️ | [$3.350](https://instances.vantage.sh/aws/ec2/p5e.48xlarge) | N/A | N/A | [$5.97](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p5en.48xlarge` | 8 | H200 141GB SXM | [$63.296](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$7.912** | [$3.398](https://instances.vantage.sh/aws/ec2/p5en.48xlarge) | N/A | N/A | [$6.241–6.865](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p6-b200.48xlarge` | 8 | B200 180GB SXM6 | [$113.9328](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$14.242** | [$5.261](https://instances.vantage.sh/aws/ec2/p6-b200.48xlarge) | N/A | N/A | [$12.355](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `p6-b300.48xlarge` | 8 | B300 SXM | [$142.416](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20West%20(Oregon)/Linux/index.json) | **$17.802** | [$5.591](https://instances.vantage.sh/aws/ec2/p6-b300.48xlarge) | N/A | N/A | [$14.04](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `u-p6e-gb200x36` UltraServer | 36 | GB200 (B200 186GB) | not on-demand | — | — | — | — | [$10.582](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| `u-p6e-gb200x72` UltraServer | 72 | GB200 (B200 186GB) | not on-demand | — | — | — | — | [$10.582](https://aws.amazon.com/ec2/capacityblocks/pricing/) ($761.904/UltraServer-hr) |
| `g7e.48xlarge` | 8 | RTX PRO 6000 Blackwell SE | [$33.1443](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$4.143** | [$2.506](https://instances.vantage.sh/aws/ec2/g7e.48xlarge) | N/A | N/A | not offered |
| `g7e.2xlarge` | 1 | RTX PRO 6000 Blackwell SE | [$3.3631](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | **$3.363** | — | — | — | — |

✅ **Re-pulled 2026-09-19 in the sweep** (us-east-1 and us-west-2 sheets, HTTP 200):
`p4d` $21.9576, `p4de` $27.44705, `p5.4xlarge` $6.88, `p5.48xlarge` $55.04,
`p5en` $63.296, `p6-b200` $113.9328, `p6-b300` $142.416, `g7e.48xlarge` $33.14432,
`g7e.2xlarge` $3.36312 — all identical in both regions and identical to the table.
No `p5e` row exists in either sheet, confirming the ⚠️ above.

Notes:
- **Prices are identical in us-east-1, us-east-2 and us-west-2** for every P/G
  instance checked — verified by pulling all three region sheets on 2026-09-19.
  `p6-b300.48xlarge` is present in us-west-2 and us-east-1 (both at $142.416) but
  not us-east-2; `p4de.24xlarge` is present in us-east-1 and us-west-2 only.
- **AWS raised Capacity Block prices effective 2026-07-01** (the current sheet
  says prices next update October 2026). The p6-b200 Capacity Block rate of
  $12.355/accel-hr is *below* the on-demand $14.242 — on AWS, Capacity Blocks
  are the cheap path for Blackwell, not the expensive one.
- **AWS has no MI300X/MI325X/MI355X instance family** as of 2026-09-19. No AMD
  Instinct SKU appears in the price sheet.
- No p6e-gb200 on-demand SKU exists; it is Capacity-Block-only and confined to
  the **US East (Dallas) Local Zone** per the capacity-blocks page.

### 3.2 Google Cloud

From GCP's own accelerator-optimized price sheet, fetched 2026-09-19. Column
semantics are GCP's: *Price* = on-demand; *DWS Flex-start* = Dynamic Workload
Scheduler queued short jobs; *DWS Calendar* = future-dated reservation;
*Spot*; *Compute Resource CUD 1y/3y*.

| Machine type | GPUs | GPU | On-demand $/inst-hr | **$/GPU-hr** | Spot $/GPU-hr | DWS Flex $/GPU-hr | CUD 1y $/GPU-hr | CUD 3y $/GPU-hr |
|---|---|---|---|---|---|---|---|---|
| `a2-highgpu-1g` | 1 | A100 40GB SXM | $3.673385 | **$3.673** | $2.204 | $2.000 | $2.314 | $1.286 |
| `a2-highgpu-8g` | 8 | A100 40GB SXM | $29.38708 | **$3.673** | $2.204 | $2.000 | $2.314 | $1.286 |
| `a2-megagpu-16g` | 16 | A100 40GB SXM | $55.739504 | **$3.484** | $2.090 | N/A | $2.195 | $1.219 |
| `a2-ultragpu-1g` | 1 | A100 80GB SXM | $5.06879789 | **$5.069** | $3.041 | $2.400 | $4.200 | $3.500 |
| `a2-ultragpu-8g` | 8 | A100 80GB SXM | $40.550383 | **$5.069** | $3.041 | $2.400 | N/A | N/A |
| `a3-highgpu-8g` | 8 | H100 80GB SXM | $88.490000 | **$11.061** | $6.620 | $4.790 | $7.673 | $4.858 |
| `a3-megagpu-8g` | 8 | H100 80GB SXM | $93.400713 | **$11.675** | $6.987 | $5.040 | $8.026 | $5.081 |
| `a3-ultragpu-8g` | 8 | H200 141GB SXM | $84.806908 | **$10.601** | $6.359 | $5.300 | $7.309 | $4.651 |
| `a4-highgpu-8g` | 8 | B200 180GB SXM6 | **N/A (not sold on-demand)** | — | $4.954 | $8.055 | $11.116 | $7.088 |
| `g4-standard-48` | 1 | RTX PRO 6000 Blackwell | $4.49993 | **$4.500** | $1.743 | $2.250 | $3.105 | $1.979 |
| `g4-standard-384` | 8 | RTX PRO 6000 Blackwell | $35.99944 | **$4.500** | $1.743 | $2.250 | $3.105 | $1.979 |
| `g2-standard-48` | 4 | L40S… (L4) | $4.001665 | **$1.000** | $0.600 | N/A | $0.630 | $0.450 |
| `a4x-highgpu-*` (GB200) | 4 | GB200 | **absent from price sheet** ⚠️ | — | — | — | — | — |
| `a4x-maxgpu-*` (GB300) | 4 | GB300 | **absent from price sheet** ⚠️ | — | — | — | — | — |

Source for the whole table: [GCP accelerator-optimized pricing](https://cloud.google.com/products/compute/pricing/accelerator-optimized), fetched 2026-09-19.

⚠️ **TO BE VERIFIED — no cell in this table could be re-confirmed in the
2026-09-19 adversarial pass.** The GCP price page is large enough that the fetch
tool truncated it before reaching the A2/A3/A4/G4 tables on two attempts, and
`docs.cloud.google.com/compute/docs/gpus/gpu-pricing` returns HTTP 404. Every
other provider table in §3 and §4 was re-pulled and matched; this one is the
single unverified provider block. Re-pull via the Cloud Billing Catalog API
(authenticated) before relying on the GCP rows, and note that the A4 CUD-1y >
DWS-Flex inversion called out below rests entirely on an unverified figure.

Notes:
- GCP's sheet states plainly: *"These machine types are not eligible for sustained
  use discounts or flexible committed use discounts."* Only **Resource** CUDs apply
  to A2/A3/A4, and those require an attached reservation
  ([docs](https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines)).
- **G4 (RTX PRO 6000) is the exception** — it takes Compute *Flexible* CUDs
  (1y $3.780/GPU-hr, 3y $2.610/GPU-hr) *and* Resource CUDs, and is the only
  Blackwell-generation GCP SKU with a normal on-demand price.
- The A4 (B200) CUD-1y rate of **$11.116/GPU-hr is higher than the DWS Flex-start
  rate of $8.055** — an inversion worth checking before signing a 1-year B200 CUD.
- GCP publishes **no AMD Instinct machine family**.

### 3.3 Microsoft Azure

From the Azure Retail Prices API, fetched 2026-09-19. Reservation figures are
Azure's *total prepay for the term*; the $/GPU-hr column is
`total ÷ (years × 8760) ÷ GPUs`.

| VM size | GPUs | GPU | Region | PAYG $/inst-hr | **$/GPU-hr** | Spot $/GPU-hr | Low-priority $/GPU-hr | 1y res. $/GPU-hr | 3y res. $/GPU-hr |
|---|---|---|---|---|---|---|---|---|---|
| `ND96asr_A100_v4` | 8 | A100 40GB SXM | eastus | $31.613 | **$3.952** | $0.748 | $1.581 | $2.354 | $1.360 |
| `ND96amsr_A100_v4` | 8 | A100 80GB SXM | eastus | $32.770 | **$4.096** | $1.055 | $1.859 | $2.622 | $1.802 |
| `ND96ams_A100_v4` | 8 | A100 80GB SXM | eastus | $37.186 | **$4.648** | $1.055 | $1.859 | $2.622 | $1.802 |
| `ND96is_H100_v5` | 8 | H100 80GB SXM | eastus | $88.488 | **$11.061** | $2.146 | $2.212 | $7.079 | $4.855 |
| `ND96isr_H100_v5` | 8 | H100 80GB SXM | eastus | $98.320 | **$12.290** | $2.373 | $2.458 | $7.866 | $5.395 |
| `ND96isr_H200_v5` | 8 | H200 141GB SXM | eastus2 / westus3 | $84.800 | **$10.600** | $10.600 ⚠️ | $4.461 | $5.815 | $5.278 |
| `ND96isr_H200_v5` | 8 | H200 141GB SXM | swedencentral | $114.688 | **$14.336** | $14.336 ⚠️ | ⚠️ n/a | ⚠️ n/a | ⚠️ n/a |
| `ND128isr_NDR_GB200_v6` | 4 | GB200 (B200; Azure prints "192 GiB", see note) | eastus | $108.160 | **$27.040** | $27.040 ⚠️ | — | $17.306 | $11.898 |
| `ND128isr_NDR_GB200_v6` | 4 | GB200 | eastus2 / westus3 | $114.048 | **$28.512** | $27.040 ⚠️ | — | $17.306 | $11.898 |
| `ND96is_MI300X_v5` | 8 | MI300X 192GB OAM | eastus2 / westus3 | $48.000 | **$6.000** | $1.109 | $1.200–2.621 | $3.840 | $2.634 |
| `ND96is_MI300X_v5` | 8 | MI300X | swedencentral | $67.200 | **$8.400** | $1.654 | $1.680 | ⚠️ n/a | ⚠️ n/a |
| `ND B200 v6` | — | B200 | — | **no retail SKU returned** ⚠️ | — | — | — | — | — |
| `ND GB300 v6` | — | GB300 | — | **no retail SKU returned** ⚠️ | — | — | — | — | — |

Source: [Azure Retail Prices API](https://prices.azure.com/api/retail/prices), filter
`serviceName eq 'Virtual Machines'`, queried 2026-09-19.
**Correction 2026-09-19 (re-pull):** the two H100 PAYG rows previously read
$102.736 and $92.904 per instance-hour (→ $12.842 and $11.613 per GPU-hr). A fresh
query of the Retail Prices API filtered to `armRegionName eq 'eastus' and
contains(armSkuName,'H100')` returns Consumption meters of **$98.32** and
**$88.488** — each of the old figures was exactly $4.416/hr too high, which looks
like a second meter having been summed in. The reservation totals were re-verified
and are unchanged ($551,221 1y / $1,134,310 3y / $1,722,566 5y on `ND96isr`;
$496,099 / $1,020,879 on `ND96is`), so only the PAYG column moved.
([Azure Retail Prices API](https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Virtual%20Machines%27%20and%20armRegionName%20eq%20%27eastus%27%20and%20contains%28armSkuName%2C%27H100%27%29))

⚠️ **TO BE VERIFIED — the H200, GB200 and MI300X rows above were *not* re-pulled
in the 2026-09-19 verification pass**; only the H100 filter was re-run. Given that
both H100 PAYG figures were wrong by an identical offset, treat the other Azure
PAYG cells as unconfirmed until the same filter is run per SKU family.

`ND GB200 v6` GPU count and the per-accelerator memory figure are confirmed against
[Microsoft Learn — ND GB200-v6 series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series)
(18 such VMs form one GB200 NVL72 rack). ⚠️ **Unit slip in Azure's own docs:**
Microsoft writes "192 GiB" per accelerator, which would be 206 GB. NVIDIA's
[GB200 NVL72](https://www.nvidia.com/en-us/data-center/gb200-nvl72/) page gives
13.4 TB across 72 GPUs = **186 GB/GPU**, and CoreWeave lists its GB200 slice at
186 GB. Azure is quoting a **192 GB** nameplate mislabelled as GiB. §10.1 uses
**186 GB** for GB200; do not size KV cache against 192 GiB.

⚠️ **Azure spot pricing for H200 and GB200 is quoted by the API at the same value
as on-demand.** That is what the API returns; it means either no spot discount is
currently offered on those SKUs or the meter is a placeholder. Treat as
unavailable, not as a real spot price.

Azure publishes a **5-year reservation** for H100 (`ND96isr_H100_v5`: $1,722,566
total = **$4.916/GPU-hr**) but not for H200, GB200 or MI300X.

### 3.4 Oracle Cloud Infrastructure

OCI publishes a flat per-GPU-hour list price for every shape. This is the only
hyperscaler with a **public on-demand price for B300, GB300 and MI355X**.

| SKU | Shape | GPU | **$/GPU-hr list** | Source |
|---|---|---|---|---|
| B95907 | `BM.GPU.A100-v2.8` | A100 80GB SXM ×8 | **$4.00** | [OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD) |
| B98415 | `BM.GPU.H100.8` | H100 80GB SXM ×8 | **$10.00** | same |
| B109480 | H100T | H100 (Tenancy variant) | **$10.75** | same |
| B110519 | `BM.GPU.H200.8` | H200 141GB SXM ×8 | **$10.00** | same |
| B110978 | `BM.GPU.B200.8` | B200 180GB SXM6 ×8 | **$14.00** | same |
| B112237 | `BM.GPU.B300.8` | B300 SXM ×8 | **$15.00** | same |
| B110979 | `BM.GPU.GB200.4` | GB200 NVL72 (4-GPU slice) | **$16.00** | same |
| B112140 | `BM.GPU.GB300.4` | GB300 NVL72 (4-GPU slice) | **$18.00** | same |
| B109485 | `BM.GPU.MI300X.8` | MI300X ×8 | **$6.00** | same |
| B111758 | `BM.GPU.MI355X.8` | MI355X ×8 | **$8.60** | same |
| B112613 | RTX PRO 6000 | RTX PRO 6000 Blackwell | **$4.50** | same |
| B109479 | `BM.GPU.L40S.4` / `.NC.4` | L40S ×4 | **$3.50** | same |
| B95909 | `BM.GPU.A10.4` | A10 ×4 | **$2.00** | same |

✅ **Re-pulled 2026-09-19 in the sweep** (HTTP 200, 192 KB of JSON): every part
number and price above reproduced exactly — B112237 B300 **$15.00**, B112140
GB300 **$18.00**, B111758 MI355X **$8.60**, B110978 B200 $14.00, B110979 GB200
$16.00, B110519 H200 $10.00, B98415 H100 $10.00, B109480 H100T $10.75, B109485
MI300X $6.00, B112613 RTX PRO 6000 $4.50, B109479 L40S $3.50, B95907 A100-v2
$4.00, B95909 A10 $2.00. **This is the canonical source for the three prices the
other research docs get wrong: GB300 is $18.00 (not $7.40, which is Hyperstack's
HGX B300 rate), B300 is $15.00, and MI355X is $8.60 (not $3.45, which is Crusoe's
MI300X rate).**

Shape names cross-checked against the rendered [OCI price list page](https://www.oracle.com/cloud/price-list/)
(which lists `BM.GPU.B200.8`, `BM.GPU.GB200.4`, `BM.GPU.GB300.4`, `BM.GPU.B300.8`,
`BM.GPU.MI300X.8`, `BM.GPU.MI355X.8`), fetched 2026-09-19.

Additional OCI line items (software, **not** compute — they stack on top):
NVIDIA AI Enterprise surcharges of $2.50/GPU-hr on H100 and H200, $3.50 on B200,
$4.00 on GB200, $1.00 on A100 80GB, $0.76 on A100 40GB
([same API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD)).
**If your image needs NVAIE, add that to every OCI row above.**

⚠️ **TO BE VERIFIED — OCI reserved/committed rates.** OCI publishes only
pay-as-you-go in the public API; Annual Universal Credits and Oracle Support
Rewards discounts are contract-specific and not published. OCI's own marketing
positions its list price as already reflecting a discount to AWS/Azure list.
⚠️ **No A100 40GB (`BM.GPU4.8`) SKU appears in the current API** — likely retired
from the price list; only the A100-v2 (80GB) SKU remains.

---

## 4. Neocloud / specialist provider pricing

All fetched 2026-09-19 unless the vendor page states its own "as of" date.

### 4.1 CoreWeave

| Instance | GPUs | GPU | VRAM (GB) | On-demand $/inst-hr | **$/GPU-hr** | Spot $/GPU-hr | "Inference" $/GPU-hr |
|---|---|---|---|---|---|---|---|
| GB300 NVL72 slice | 4 | GB300 | 279 ‡ | Contact sales | — | — | Contact sales |
| GB200 NVL72 slice | 4 | GB200 | 186 | $42.00 | **$10.50** | — | $10.50 |
| HGX B300 | 8 | B300 | 270 ‡ | Contact sales | — | **$4.480** | Contact sales |
| HGX B200 | 8 | B200 | 180 | $68.80 | **$8.600** | $4.264 | $8.60 |
| HGX H200 | 8 | H200 | 141 | $50.44 | **$6.305** | $2.616 | $6.31 |
| HGX H100 | 8 | H100 | 80 | $49.24 | **$6.155** | $2.464 | $6.16 |
| A100 | 8 | A100 80GB | 80 | $21.60 | **$2.700** | $1.206 | $2.70 |
| RTX PRO 6000 (High Memory) | 8 | RTX PRO 6000 | 96 | $20.00 | **$2.500** | $1.386 | $2.50 |
| RTX PRO 6000 (Standard) | 8 | RTX PRO 6000 | 96 | Contact sales | — | $1.195 | Contact sales |

Source: [CoreWeave pricing](https://www.coreweave.com/pricing) (North America), fetched 2026-09-19 and **re-fetched 2026-09-19 in the sweep** — every cell above matched.
CoreWeave states "**up to 60% discounts over On-Demand for committed usage**" but
publishes no reserved rate card ⚠️ TO BE VERIFIED.

‡ VRAM column is **CoreWeave's own listed figure**, not the planning capacity.
METHODOLOGY §8 pins HGX B300 at **268 GB/GPU** and GB300 NVL72 at **288 GB
nameplate (≈ 279 usable)** — CoreWeave's 270 and 279 are its reported slices. §10
normalises on the METHODOLOGY figures. Note also that CoreWeave's non-NA region
prices HGX B300 spot at **$36.70/node-hr ($4.588/GPU-hr)**, not $35.84.

CoreWeave also still runs a **Classic** price list for older SKUs
([CoreWeave Classic](https://www.coreweave.com/pricing/classic)): H100 PCIe $4.25/hr,
HGX H100 $4.76/hr, A100 80GB PCIe/NVLINK $2.21/hr, A100 40GB $2.06/hr,
RTX A6000 $1.28/hr, A40 $1.28/hr — these are **per-GPU** and are cheaper than
the modern rate card, but Classic instances have different networking.

### 4.2 Lambda

| Config | GPU | VRAM | **$/GPU-hr** |
|---|---|---|---|
| 8× | B200 SXM6 | 180 GB | **$6.69** |
| 4× / 2× / 1× | B200 SXM6 | 180 GB | $6.79 / $6.89 / $6.99 |
| 8× | H100 SXM | 80 GB | **$3.99** |
| 4× / 2× / 1× | H100 SXM | 80 GB | $4.09 / $4.19 / $4.29 |
| 1× | H100 PCIe | 80 GB | $3.29 |
| 8× | A100 SXM | 80 GB | **$2.79** |
| 8× | A100 SXM | 40 GB | **$1.99** |
| 1× | A100 PCIe | 40 GB | $1.99 |
| 1× | GH200 | 96 GB | $2.29 |

**1-Click Clusters** (InfiniBand included, 2 weeks – 1 year):

| GPU | 16 GPUs | 64 GPUs | 256+ GPUs |
|---|---|---|---|
| HGX B200 | $9.86 | $9.36 | $8.87 |
| H100 | $6.16 | $5.85 | $5.54 |

Source: [Lambda pricing](https://lambda.ai/pricing), fetched 2026-09-19.

⚠️ **Lambda's cluster prices are higher than its single-instance prices**
($9.86 vs $6.69 for B200). That is the published rate card; the cluster SKU
bundles non-blocking IB fabric and dedicated capacity. If you only need 8 GPUs,
the on-demand instance is the cheaper Lambda product.
⚠️ **Lambda publishes no H200, B300, GB200, GB300 or MI300X price** as of 2026-09-19.

### 4.3 Nebius

| GPU | vCPU | RAM (GB) | Preemptible $/GPU-hr | **On-demand $/GPU-hr** |
|---|---|---|---|---|
| HGX B300 | 24 | 346 | $4.30 | **$7.85** |
| HGX B200 | 20 | 224 | $3.95 | **$7.15** |
| HGX H200 | 16 | 200 | $2.45 | **$4.50** |
| HGX H100 | 16 | 200 | $2.15 | **$3.85** |
| RTX PRO 6000 | 24 | 218 | $0.95 | **$1.80** |
| L40S (Intel) | 16–192 | 96–1152 | from $0.90 | from $1.82 |
| L40S (AMD) | 8–40 | 32–160 | from $0.74 | from $1.55 |

Source: [Nebius prices](https://nebius.com/prices), fetched 2026-09-19.
GB300 and GB200 NVL72 are **contact-sales**. Nebius advertises "up to 35% less
than on-demand" on commitments but publishes no term rate card ⚠️ TO BE VERIFIED.

### 4.4 RunPod (Secure Cloud & Community Cloud)

Vendor page states it was updated **2026-09-13**.

| GPU | VRAM | **Secure $/GPU-hr** | **Community $/GPU-hr** |
|---|---|---|---|
| B300 | 288 GB (nameplate; plan 268) | $7.89 | $6.94 |
| B200 | 180 GB | $6.79 | $5.98 |
| H200 | 141 GB | $4.59 | $3.59 |
| H100 SXM | 80 GB | $3.49 | $2.69 |
| H100 NVL | 94 GB | $3.19 | $2.59 |
| H100 PCIe | 80 GB | $2.89 | $1.99 |
| A100 SXM | 80 GB | $1.59 | $1.39 |
| A100 PCIe | 80 GB | $1.59 | $1.19 |
| RTX PRO 6000 | 96 GB | $2.09 | $1.69 |
| L40S | 48 GB | $1.09 | $0.79 |
| RTX 5090 | 32 GB | $0.99 | $0.69 |
| RTX 4090 | 24 GB | $0.74 | $0.34 |

Source: [RunPod pricing](https://www.runpod.io/pricing), fetched 2026-09-19.
**No MI300X / MI325X / MI355X and no A100 40GB** on the current rate card.
Billing is per second. Community Cloud = third-party hosts, no SLA, no guarantee
the node stays up — do not size a production replica against it.

### 4.5 Hyperstack

| GPU | VRAM | **On-demand $/GPU-hr** | **Reserved $/GPU-hr** |
|---|---|---|---|
| B300 | 288 GB* (nameplate; plan 268) | $7.40 | not listed ⚠️ |
| B200 | 192 GB* | $6.00 | $5.10 |
| H200 SXM | 141 GB | $3.99 | $2.79 |
| H100 SXM | 80 GB | $3.20 | $2.72 |
| H100 NVLink | 80 GB | $2.60 | $1.82 |
| H100 PCIe | 80 GB | $2.50 | $1.75 |
| RTX PRO 6000 SE | 96 GB | $1.85 | $1.30 |
| A100 SXM | 80 GB | $1.60 | $1.36 |
| A100 NVLink | 80 GB | $1.40 | $0.98 |
| A100 PCIe | 80 GB | $1.35 | $0.95 |
| L40 | 48 GB | $1.00 | $0.70 |

Source: [Hyperstack GPU pricing](https://www.hyperstack.cloud/gpu-pricing), fetched 2026-09-19 and **re-fetched 2026-09-19 in the sweep** — every cell above matched
(B300 $7.40 with no reserved rate listed; B200 $6.00/$5.10; H200 $3.99/$2.79;
H100 SXM $3.20/$2.72; RTX PRO 6000 SE $1.85/$1.30; A100 $1.60/$1.40/$1.35).
\* Hyperstack lists B200 as 192 GB and B300 as 288 GB — both are **die
nameplates**. METHODOLOGY §8 pins B200 at **180 GB** (NVIDIA HGX 1.4 TB ÷ 8) and
HGX B300 at **268 GB** (2,144 GB ÷ 8). Plan with those.
Hyperstack's lineup now also lists **GB200 NVL72 and GB300 NVL72**, but neither
appears in any published price block — contact-sales, like everywhere else. ⚠️
**Hyperstack is the cheapest published on-demand price for H100, H200, B200 and
B300 among reputable non-marketplace providers** in this survey.

### 4.6 Together AI

| GPU | VRAM | On-demand $/GPU-hr | Reserved $/GPU-hr | Min. scale |
|---|---|---|---|---|
| H100 SXM | 80 GB | $3.99 | $3.19 | 8 GPUs |
| H200 SXM | 140 GB | $5.99 | $3.99 | 256 GPUs |
| HGX B200 | 180 GB | $8.19 | $6.79 | 256 GPUs |
| HGX B300 | 270 GB (vendor-listed; plan 268) | Contact sales | Contact sales | — |
| GB200 NVL72 | 186 GB | — | Contact sales | 512 GPUs |
| GB300 NVL72 | 288 GB (≈ 279 usable) | — | Contact sales | — |

Together's B200 term ladder (page dated **July 2026**):
on-demand $8.19 → 7–30 d $7.99 → 31–90 d $7.79 → 91–180 d $6.79 → 181 d+ contact sales.

Sources: [Together GPU clusters](https://www.together.ai/gpu-clusters),
[Together HGX B200](https://www.together.ai/gpu/nvidia-hgx-b200), fetched 2026-09-19.
Note the **256-GPU minimum** on H200 and B200 — Together's headline rate is not
reachable for an 8-GPU inference replica.

### 4.7 Crusoe

| GPU | Memory | Config | **On-demand $/GPU-hr** |
|---|---|---|---|
| GB200 | 186 GB | NVL72 | Contact sales |
| B200 | 180 GB | HGX | Contact sales |
| H200 | 141 GB | HGX | **$4.29** |
| H100 | 80 GB | HGX | **$3.90** |
| A100 | 80 GB | SXM | **$2.30** |
| A100 | 80 GB | PCIe | **$2.00** |
| L40S | 48 GB | — | **$1.50** |
| MI355X | 288 GB | — | Contact sales |
| MI300X | 192 GB | — | **$3.45** |

Source: [Crusoe pricing](https://www.crusoe.ai/cloud/pricing), fetched 2026-09-19.
Every spot column on Crusoe's sheet reads "Contact sales". No reserved rate card
published ⚠️ TO BE VERIFIED.

### 4.8 DigitalOcean (GPU Droplets)

Vendor page states **"New pricing effective as of August 1, 2026"**.

| GPU | GPUs/Droplet | VRAM | **On-demand $/GPU-hr** | **12-mo reserved $/GPU-hr** |
|---|---|---|---|---|
| HGX B300 | 1 or 8 | 270–288 GB listed (plan **268**) | not offered on-demand | **$7.94** |
| HGX H200 | 1 or 8 | 141 GB | **$4.47** | **$3.40** |
| HGX H100 | 1 or 8 | 80 GB | **$4.41** | **$3.26** |
| MI350X | 1 or 8 | 288 GB | not offered on-demand | **$4.76** |
| MI325X | 1 or 8 | 256 GB | **$3.80** | **$2.88** |
| MI300X | 1 or 8 | 192 GB | **$2.59** | **$1.91** |
| RTX 6000 Ada | 1 | 48 GB | $1.57 | — |
| L40S | 1 | 48 GB | $1.57 | — |
| RTX 4000 Ada | 1 | 20 GB | $0.76 | — |

Source: [DigitalOcean GPU Droplets pricing](https://www.digitalocean.com/pricing/gpu-droplets), fetched 2026-09-19.
Billed per second, 5-minute minimum. **DigitalOcean is the cheapest published
on-demand MI325X** in this survey. Note DO lists **MI350X**, not MI355X — a
lower-clocked 1,000 W air-cooled part; it is *not* the same SKU as MI355X ⚠️.

### 4.9 Hot Aisle

| Config | GPU | **$/GPU-hr** | Terms |
|---|---|---|---|
| VM (1×, 2×, 4×) | MI300X | **$2.99** | per-minute, no commitment |
| Bare metal 8× | MI300X | **$3.39** | one-month minimum |
| legacy (grandfathered) | MI300X | $1.99 | existing customers only |
| — | MI355X | not published ⚠️ | — |

Source: [Hot Aisle pricing](https://hotaisle.xyz/pricing), fetched 2026-09-19.

### 4.10 Scaleway

Scaleway publishes **EUR only**. No FX rate is sourced here, so USD conversion is
⚠️ TO BE VERIFIED — do not convert without a dated rate.

| Instance | GPUs | GPU | vCPU | RAM | **€/hr (instance)** | **€/GPU-hr** |
|---|---|---|---|---|---|---|
| `L4-1-24G` | 1 | L4 24 GB | 8 | 48 GB | €0.79 | €0.79 |
| `L4-8-24G` | 8 | L4 24 GB | 64 | 384 GB | €6.30 | €0.7875 |
| `H100-SXM-8-80G` | 8 | H100 SXM 80 GB | — | — | listed, price not rendered ⚠️ | — |
| `B300-SXM-8-288G` | 8 | B300 SXM 288 GB | 224 | 3,840 GB | listed, price not rendered ⚠️ | — |

Source: [Scaleway GPU pricing](https://www.scaleway.com/en/pricing/gpu/), fetched 2026-09-19.
Scaleway does list a **B300-SXM-8-288G** shape and H100 SXM. The `288G` in the
shape name is the die nameplate, not the as-deployed capacity — plan B300 at
**268 GB** (METHODOLOGY §8). PAR-1 shows both shapes unavailable and the price
cells did not render server-side. ⚠️ TO BE VERIFIED via the Scaleway console.
Third-party trackers put Scaleway H100 SXM at $2.87–3.31/GPU-hr
([Spheron](https://www.spheron.network/blog/scaleway-h100-pricing-2026/)) — secondary,
unverified.

### 4.11 Voltage Park (now Lightning AI)

| GPU | **$/GPU-hr** | Notes |
|---|---|---|
| H100 (Ethernet, 1–1016 GPUs) | **from $1.99** | on-demand |
| H100 (3200 Gbps InfiniBand, 8–1016 GPUs) | Contact for pricing | |
| H100 long-term reserve (32–8000+ GPUs) | Contact for pricing | 6-month minimum |
| H200 / B200 / B300 | not published ⚠️ | |

Source: [Voltage Park pricing](https://www.voltagepark.com/pricing), fetched 2026-09-19.
Voltage Park and Lightning AI **merged 2026-01-21**; third-party trackers report
post-merger rates of H100 from $3.75, H200 from $4.93, B200 from $7.95/GPU-hr and
say B200/B300 are long-term-contract only
([Spheron](https://www.spheron.network/blog/voltage-park-is-now-lightning-ai-gpu-pricing-2026/)).
The $1.99 on the Voltage Park page and the $3.75 third-party figure conflict —
⚠️ TO BE VERIFIED; the $1.99 is a "starting at" headline and probably requires
Ethernet-only, single-GPU, spot-like terms.

### 4.12 TensorWave

TensorWave's site advertises MI455X (new), MI355X, MI325X and MI300X but
`tensorwave.com/pricing` is **404** as of 2026-09-19 — all rates are quote-based
([TensorWave](https://tensorwave.com/)).

| GPU | third-party reported $/GPU-hr | Confidence |
|---|---|---|
| MI300X | $1.71 | ⚠️ secondary, "starting from", quote-dependent ([Spheron, checked 16 Aug 2026](https://www.spheron.network/blog/amd-mi300x-mi355x-pricing-2026/)) |
| MI355X | $2.95 | ⚠️ secondary, quote-based ([same](https://www.spheron.network/blog/amd-mi300x-mi355x-pricing-2026/)) |
| MI325X | not reported ⚠️ | |

### 4.13 SF Compute

SF Compute is a **market**, not a rate card: you buy contracts at the clearing
price and can resell unused capacity.

| Metric | Value | Source |
|---|---|---|
| Headline "average gpu/hr" shown on site | **$2.07** | [sfcompute.com](https://sfcompute.com/), fetched 2026-09-19 |
| Worked example reserved rate (3-month contract) | $3.00/GPU-hr with resale at 1.5×–4.5× | same |
| Hardware offered | H100, B300, GB300 clusters | same |
| Per-GPU-model clearing price | **not published** ⚠️ | — |

The $2.07 is not attributable to a specific GPU model; it is a blended platform
average. Do **not** use it as an H100 or B300 price. ⚠️ TO BE VERIFIED per model.

### 4.14 Vultr

`vultr.com/pricing` returns **HTTP 403** to non-browser clients (verified twice on
2026-09-19), so no primary extraction was possible. Third-party snapshots,
September 2026:

| GPU | reported $/GPU-hr | Confidence |
|---|---|---|
| B200 (cloud GPU) | $8.50 | ⚠️ secondary ([Thunder Compute](https://www.thundercompute.com/blog/nvidia-b200-pricing)) |
| B200 (bare metal 8×192 GB) | from $3.50 | ⚠️ secondary, conflicts with the above |
| MI325X | $4.62 | ⚠️ secondary ([gpuperhour](https://gpuperhour.com/rent/mi325x)) |
| MI300X | $3.99 on-demand / $1.85 preemptible | ⚠️ secondary ([Spheron, 16 Aug 2026](https://www.spheron.network/blog/amd-mi300x-mi355x-pricing-2026/)) |

⚠️ TO BE VERIFIED — all Vultr rows. The $8.50 vs $3.50 B200 spread is unresolved;
it is likely cloud-GPU vs bare-metal-with-commitment.

### 4.15 DataCrunch / Verda

`datacrunch.io` **301-redirects to `verda.com`**, and `verda.com/products` returns
**HTTP 404** (2026-09-19). DataCrunch rebranded to Verda; no rate card could be
retrieved. ⚠️ TO BE VERIFIED — check `verda.com` directly.

### 4.16 Fluidstack

`fluidstack.io/pricing` returns **HTTP 404** (2026-09-19). Fluidstack is
enterprise/quote-only for H100, H200, GB200 NVL72 and GB300 NVL72.
⚠️ TO BE VERIFIED — no public rate card exists.

### 4.17 Vast.ai (marketplace)

Vast is a host-set spot market: *"Prices are set by the market, not by Vast."*
Three purchase modes — On-Demand (per-second, guaranteed), Interruptible ("50%+
cheaper"), Reserved ("up to 50% off", 1/3/6 months)
([Vast.ai pricing](https://vast.ai/pricing), fetched 2026-09-19).

The live bundles API (`console.vast.ai/api/v0/bundles/`) returned **404** from
this environment on 2026-09-19, so no first-party medians could be computed.
Third-party snapshot (July 2026), per GPU:

| GPU | reported range $/GPU-hr | Confidence |
|---|---|---|
| H100 | $1.045 – $2.747 (verified-datacenter hosts $1.50–$2.27) | ⚠️ secondary ([Spheron](https://www.spheron.network/blog/vastai-pricing-2026/)) |
| H200 | $1.936 – $3.816 | ⚠️ secondary (same) |
| B200 | $6.752 – $6.877 | ⚠️ secondary (same) |

⚠️ Do not plan capacity on Vast. Hosts disappear, disks are not durable, and the
price you see is not the price you keep.

---

## 5. Per-GPU consolidated tables

One table per GPU. All values are **$/GPU-hour**. Fetch date = 2026-09-19 for
every row; where the vendor page carries its own "as of" date it is noted.
"—" = provider does not offer that GPU. "CS" = contact sales.

### 5.1 A100 40GB

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| AWS | `p4d.24xlarge` (SXM) | 8 | **$2.745** | $1.740 | $1.172 | $2.083 |
| GCP | `a2-highgpu-8g` (SXM) | 8 | **$3.673** | $2.314 | $1.286 | $2.204 |
| GCP | `a2-megagpu-16g` (SXM) | 16 | $3.484 | $2.195 | $1.219 | $2.090 |
| Azure | `ND96asr_A100_v4` (SXM) | 8 | **$3.952** | $2.354 | $1.360 | $0.748 |
| Oracle OCI | — (retired from price list) | — | ⚠️ | — | — | — |
| CoreWeave | A100 40GB PCIe/NVLINK (Classic) | 1 | $2.06 | CS | CS | — |
| Lambda | 8× A100 SXM 40GB | 8 | **$1.99** | — | — | — |
| Lambda | 1×/2×/4× A100 PCIe 40GB | 1–4 | $1.99 | — | — | — |
| RunPod | — | — | — | — | — | — |
| Others | — | — | — | — | — | — |

**Cheapest reputable on-demand: Lambda $1.99. Cheapest hyperscaler: AWS $2.745.
Cheapest committed: AWS 3-yr RI $1.172 / GCP 3-yr CUD $1.286.**

### 5.2 A100 80GB

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| AWS | `p4de.24xlarge` (SXM) | 8 | **$3.431** | ⚠️ n/a | ⚠️ n/a | ⚠️ n/a |
| GCP | `a2-ultragpu-8g` (SXM) | 8 | **$5.069** | n/a (8g) | n/a (8g) | $3.041 |
| GCP | `a2-ultragpu-1g` (SXM) | 1 | $5.069 | $4.200 | $3.500 | $3.041 |
| Azure | `ND96amsr_A100_v4` (SXM) | 8 | **$4.096** | $2.622 | $1.802 | $1.055 |
| Oracle OCI | `BM.GPU.A100-v2.8` (SXM) | 8 | **$4.00** | ⚠️ | ⚠️ | — |
| CoreWeave | A100 (modern rate card) | 8 | **$2.700** | CS (≤60% off) | CS | $1.206 |
| CoreWeave Classic | A100 80GB PCIe/NVLINK | 1 | $2.21 | — | — | — |
| Lambda | 8× A100 SXM 80GB | 8 | **$2.79** | — | — | — |
| RunPod Secure | A100 SXM / PCIe 80GB | 1 | **$1.59** | — | — | — |
| RunPod Community | A100 SXM / PCIe 80GB | 1 | $1.39 / $1.19 | — | — | — |
| Hyperstack | A100 SXM / NVLink / PCIe | 1–8 | $1.60 / $1.40 / **$1.35** | $1.36 / $0.98 / $0.95 | — | — |
| Crusoe | A100 SXM / PCIe | 8 | $2.30 / $2.00 | CS | CS | CS |

**Cheapest reputable on-demand: Hyperstack A100 PCIe $1.35 (SXM $1.60);
RunPod Secure SXM $1.59. Cheapest hyperscaler: AWS $3.431. Cheapest committed:
Azure 3-yr $1.802 / Hyperstack reserved $0.95.**

### 5.3 H100 SXM

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| AWS | `p5.48xlarge` | 8 | **$6.880** | N/A | $2.972 | $2.598 |
| AWS | `p5.4xlarge` | 1 | $6.880 | N/A | ⚠️ | — |
| AWS Capacity Block | `p5.48xlarge` | 8 | — | — | — | $5.191 (block) |
| GCP | `a3-highgpu-8g` | 8 | **$11.061** | $7.673 | $4.858 | $6.620 |
| GCP | `a3-megagpu-8g` | 8 | $11.675 | $8.026 | $5.081 | $6.987 |
| Azure | `ND96isr_H100_v5` | 8 | **$12.290** | $7.866 | $5.395 (5-yr $4.916) | $2.373 |
| Azure | `ND96is_H100_v5` (no IB) | 8 | $11.061 | $7.079 | $4.855 | $2.146 |
| Oracle OCI | `BM.GPU.H100.8` | 8 | **$10.00** | ⚠️ | ⚠️ | — |
| CoreWeave | HGX H100 | 8 | **$6.155** | CS | CS | $2.464 |
| Lambda | 8× H100 SXM | 8 | **$3.99** | — | — | — |
| Lambda 1-Click Cluster | 16 / 64 / 256 GPUs | 16+ | $6.16 / $5.85 / $5.54 | CS | — | — |
| Nebius | HGX H100 | 8 | **$3.85** | ⚠️ (≤35% off) | ⚠️ | $2.15 |
| RunPod Secure | H100 SXM | 1 | **$3.49** | — | — | — |
| RunPod Community | H100 SXM | 1 | $2.69 | — | — | — |
| Hyperstack | H100 SXM | 1–8 | **$3.20** | $2.72 | — | — |
| Together | H100 SXM (min 8) | 8 | **$3.99** | $3.19 | — | — |
| Crusoe | HGX H100 | 8 | **$3.90** | CS | CS | CS |
| DigitalOcean | HGX H100 | 1 or 8 | **$4.41** | $3.26 (12-mo) | — | — |
| Voltage Park / Lightning | H100 Ethernet | 1–1016 | from **$1.99** ⚠️ | CS | CS | — |
| SF Compute | H100 cluster | var. | market (~$2.07 blended ⚠️) | market | market | resale market |
| Vast.ai | H100 (marketplace) | 1 | $1.05–$2.75 ⚠️ | ≤50% off ⚠️ | — | interruptible ⚠️ |

**Cheapest reputable on-demand: Hyperstack $3.20 (Nebius $3.85, Lambda/Together
$3.99). Cheapest hyperscaler: AWS $6.880. Cheapest committed: Azure 5-yr $4.916,
AWS 3-yr RI $2.972, Hyperstack reserved $2.72. Cheapest spot: AWS $2.598 /
Nebius preemptible $2.15.**

### 5.4 H100 PCIe / NVL

| Provider | Variant | On-demand | Reserved | Source |
|---|---|---|---|---|
| Lambda | H100 PCIe 80GB (1×) | $3.29 | — | [Lambda](https://lambda.ai/pricing) |
| RunPod Secure | H100 PCIe 80GB | $2.89 | — | [RunPod](https://www.runpod.io/pricing) |
| RunPod Community | H100 PCIe 80GB | $1.99 | — | same |
| RunPod Secure | H100 NVL 94GB | $3.19 | — | same |
| RunPod Community | H100 NVL 94GB | $2.59 | — | same |
| Hyperstack | H100 PCIe 80GB | $2.50 | $1.75 | [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) |
| Hyperstack | H100 NVLink 80GB | $2.60 | $1.82 | same |
| CoreWeave Classic | H100 PCIe 80GB | $4.25 | — | [CoreWeave Classic](https://www.coreweave.com/pricing/classic) |

**H100 PCIe is ~22% cheaper than SXM on the same provider but carries 2.0 TB/s
instead of 3.35 TB/s and 350–400 W instead of 700 W.** For decode-bound inference
(METHODOLOGY §4) that is a ~40% bandwidth cut for a ~22% price cut — SXM wins on
$/(TB/s)-hour. PCIe only wins when the model fits on one card and you are
prefill-heavy.

### 5.5 H200 SXM

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| AWS | `p5en.48xlarge` | 8 | **$7.912** | N/A | N/A | $3.398 |
| AWS | `p5e.48xlarge` | 8 | ⚠️ not in us-east-1 sheet | N/A | N/A | $3.350 |
| AWS Capacity Block | `p5e` / `p5en` | 8 | — | — | — | $5.97 / $6.241–6.865 |
| GCP | `a3-ultragpu-8g` | 8 | **$10.601** | $7.309 | $4.651 | $6.359 |
| Azure | `ND96isr_H200_v5` (eastus2/westus3) | 8 | **$10.600** | $5.815 | $5.278 | none ⚠️ |
| Azure | `ND96isr_H200_v5` (swedencentral) | 8 | $14.336 | ⚠️ | ⚠️ | none ⚠️ |
| Oracle OCI | `BM.GPU.H200.8` | 8 | **$10.00** | ⚠️ | ⚠️ | — |
| CoreWeave | HGX H200 | 8 | **$6.305** | CS | CS | $2.616 |
| Nebius | HGX H200 | 8 | **$4.50** | ⚠️ | ⚠️ | $2.45 |
| RunPod Secure | H200 | 1 | **$4.59** | — | — | — |
| RunPod Community | H200 | 1 | $3.59 | — | — | — |
| Hyperstack | H200 SXM | 1–8 | **$3.99** | $2.79 | — | — |
| Together | H200 (min 256 GPUs) | 256 | **$5.99** | $3.99 | — | — |
| Crusoe | HGX H200 | 8 | **$4.29** | CS | CS | CS |
| DigitalOcean | HGX H200 | 1 or 8 | **$4.47** | $3.40 (12-mo) | — | — |
| Voltage Park / Lightning | H200 | var. | from $4.93 ⚠️ secondary | CS | CS | — |
| Lambda | — (not offered) | — | — | — | — | — |
| Vast.ai | H200 (marketplace) | 1 | $1.94–$3.82 ⚠️ | — | — | — |

**Cheapest reputable on-demand: Hyperstack $3.99 (Crusoe $4.29, DigitalOcean $4.47,
Nebius $4.50). Cheapest hyperscaler: AWS p5en $7.912 / OCI $10.00.
Cheapest committed: GCP 3-yr CUD $4.651 / Azure 3-yr $5.278 / Hyperstack $2.79.**

### 5.6 B200

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| AWS | `p6-b200.48xlarge` | 8 | **$14.242** | N/A | N/A | $5.261 |
| AWS Capacity Block | `p6-b200.48xlarge` | 8 | — | — | — | $12.355 (block) |
| GCP | `a4-highgpu-8g` | 8 | **N/A — no on-demand** | $11.116 | $7.088 | $4.954 (DWS Flex $8.055) |
| Azure | ND B200 v6 | — | ⚠️ no retail SKU | — | — | — |
| Oracle OCI | `BM.GPU.B200.8` | 8 | **$14.00** | ⚠️ | ⚠️ | — |
| CoreWeave | HGX B200 | 8 | **$8.600** | CS | CS | $4.264 |
| Lambda | 8× B200 SXM6 | 8 | **$6.69** | — | — | — |
| Lambda 1-Click Cluster | 16 / 64 / 256+ | 16+ | $9.86 / $9.36 / $8.87 | CS | — | — |
| Nebius | HGX B200 | 8 | **$7.15** | ⚠️ | ⚠️ | $3.95 |
| RunPod Secure | B200 | 1 | **$6.79** | — | — | — |
| RunPod Community | B200 | 1 | $5.98 | — | — | — |
| Hyperstack | B200 | 1–8 | **$6.00** | $5.10 | — | — |
| Together | HGX B200 (min 256) | 256 | **$8.19** | $6.79 (91–180 d) | CS | — |
| Crusoe | HGX B200 | 8 | CS | CS | CS | CS |
| DigitalOcean | — | — | — | — | — | — |
| Vultr | B200 | 8 | $8.50 ⚠️ / BM from $3.50 ⚠️ | — | — | — |
| Voltage Park / Lightning | B200 | var. | from $7.95 ⚠️, contract-only | CS | CS | — |
| Vast.ai | B200 (marketplace) | 1 | $6.75–$6.88 ⚠️ | — | — | — |
| Scaleway | — | — | — | — | — | — |

**Cheapest reputable on-demand: Hyperstack $6.00 (Lambda $6.69, RunPod Secure $6.79,
Nebius $7.15). Cheapest hyperscaler: AWS $14.242 / OCI $14.00 — a 2.4× premium
over Hyperstack. Cheapest committed: GCP 3-yr CUD $7.088. Cheapest spot:
GCP $4.954 / CoreWeave $4.264.**

### 5.7 B300 (HGX)

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| AWS | `p6-b300.48xlarge` | 8 | **$17.802** | N/A | N/A | $5.591 |
| AWS Capacity Block | `p6-b300.48xlarge` | 8 | — | — | — | $14.04 (block) |
| GCP | — (no B300 SKU) | — | — | — | — | — |
| Azure | — (no retail SKU) | — | ⚠️ | — | — | — |
| Oracle OCI | `BM.GPU.B300.8` | 8 | **$15.00** | ⚠️ | ⚠️ | — |
| CoreWeave | HGX B300 | 8 | CS | CS | CS | **$4.480** |
| Nebius | HGX B300 | 8 | **$7.85** | ⚠️ | ⚠️ | $4.30 |
| RunPod Secure | B300 | 1 | **$7.89** | — | — | — |
| RunPod Community | B300 | 1 | $6.94 | — | — | — |
| Hyperstack | B300 | 1–8 | **$7.40** | not listed ⚠️ | — | — |
| Together | HGX B300 | 256+ | CS | CS | CS | — |
| DigitalOcean | HGX B300 | 1 or 8 | not on-demand | **$7.94** (12-mo) | — | — |
| Scaleway | `B300-SXM-8-288G` | 8 | listed, price not rendered ⚠️ | — | — | — |
| SF Compute | B300 cluster | var. | market ⚠️ | market | market | resale |

**Cheapest reputable on-demand: Hyperstack $7.40 (Nebius $7.85, RunPod $7.89).
Cheapest hyperscaler: OCI $15.00. Cheapest committed: DigitalOcean 12-mo $7.94.
Cheapest spot: CoreWeave $4.480 / Nebius $4.30.**

⚠️ Note the **OCI B300 ($15.00) is only 7% above OCI B200 ($14.00)** while
carrying 1.5× the dense FP4 and ~1.5× the memory. On OCI, B300 is strictly the
better buy for FP4 inference.

### 5.8 GB200 NVL72

| Provider | Instance | GPUs/unit | On-demand | 1-yr | 3-yr | Spot |
|---|---|---|---|---|---|---|
| AWS | `u-p6e-gb200x36` / `x72` UltraServer | 36 / 72 | Capacity Blocks only | — | — | **$10.582/accel-hr** |
| GCP | `a4x-*` | 4 | ⚠️ absent from price sheet; reservation-required | ⚠️ | ⚠️ | ⚠️ |
| Azure | `ND128isr_NDR_GB200_v6` | 4 | **$27.040** (eastus; $28.512 eastus2/westus3) | $17.306 | $11.898 | none ⚠️ |
| Oracle OCI | `BM.GPU.GB200.4` | 4 | **$16.00** | ⚠️ | ⚠️ | — |
| CoreWeave | GB200 NVL72 slice | 4 | **$10.50** ($42.00/instance) | CS (≤60% off) | CS | — |
| Nebius | GB200 NVL72 | 72 | CS | CS | CS | CS |
| Together | GB200 NVL72 (min 512 GPUs) | 512+ | — | CS | CS | — |
| Crusoe | GB200 NVL72 | 72 | CS | CS | CS | CS |
| Fluidstack | GB200 NVL72 | — | quote-only ⚠️ | — | — | — |
| Lambda | — | — | — | — | — | — |

**Cheapest published: CoreWeave $10.50 and AWS Capacity Blocks $10.582 — nearly
identical. OCI $16.00. Azure on-demand $27.04 is a 2.6× premium over CoreWeave;
Azure's 3-yr reservation at $11.898 brings it back in line.**

### 5.9 GB300 NVL72

| Provider | Instance | GPUs/unit | On-demand | 1-yr | 3-yr | Spot |
|---|---|---|---|---|---|---|
| Oracle OCI | `BM.GPU.GB300.4` | 4 | **$18.00** | ⚠️ | ⚠️ | — |
| CoreWeave | GB300 NVL72 slice (279 GB listed) | 4 | CS | CS | CS | CS |
| AWS | — (no p6e-gb300 SKU as of 2026-09-19) | — | — | — | — | — |
| GCP | `a4x-max*` | 4 | ⚠️ absent from price sheet; reservation-required | ⚠️ | ⚠️ | ⚠️ |
| Azure | ND GB300 v6 | — | ⚠️ no retail SKU; "request a quote" | — | — | — |
| Together | GB300 NVL72 | — | — | CS | CS | — |
| Nebius | GB300 | — | CS | CS | CS | CS |
| SF Compute | GB300 cluster | var. | market ⚠️ | market | market | resale |
| Fluidstack | GB300 NVL72 | — | quote-only ⚠️ | — | — | — |

**OCI's $18.00 is the only published on-demand GB300 price anywhere in this
survey.** Everything else is quote-only. That single price is therefore doing a
lot of work in §10 and should be treated with appropriate suspicion.

### 5.10 RTX PRO 6000 Blackwell (Server Edition)

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot |
|---|---|---|---|---|---|---|
| AWS | `g7e.48xlarge` | 8 | **$4.143** | N/A | N/A | $2.506 |
| AWS | `g7e.2xlarge` | 1 | $3.363 | N/A | N/A | — |
| GCP | `g4-standard-384` | 8 | **$4.500** | $3.105 (resource) / $3.780 (flex) | $1.979 (resource) / $2.610 (flex) | $1.743 |
| GCP | `g4-standard-48` | 1 | $4.500 | $3.105 | $1.979 | $1.743 |
| Azure | — (no SKU) | — | — | — | — | — |
| Oracle OCI | RTX PRO 6000 | — | **$4.50** | ⚠️ | ⚠️ | — |
| CoreWeave | RTX PRO 6000 (High Memory) | 8 | **$2.500** | CS | CS | $1.386 |
| CoreWeave | RTX PRO 6000 (Standard) | 8 | CS | CS | CS | $1.195 |
| Nebius | RTX PRO 6000 | 1–8 | **$1.80** | ⚠️ | ⚠️ | $0.95 |
| RunPod Secure | RTX PRO 6000 | 1 | **$2.09** | — | — | — |
| RunPod Community | RTX PRO 6000 | 1 | $1.69 | — | — | — |
| Hyperstack | RTX PRO 6000 SE | 1–8 | **$1.85** | $1.30 | — | — |

**Cheapest reputable on-demand: Nebius $1.80 (Hyperstack $1.85, RunPod $2.09,
CoreWeave $2.50). Cheapest hyperscaler: AWS $4.143. Cheapest committed: GCP 3-yr
resource CUD $1.979 / Hyperstack $1.30. Cheapest spot: Nebius $0.95 / GCP $1.743.**

This is the only Blackwell-generation GPU with a normal, self-serve, on-demand
price at *every* hyperscaler that carries it, and the only one where GCP offers
Flexible CUDs.

### 5.11 MI300X

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot/preempt. |
|---|---|---|---|---|---|---|
| Azure | `ND96is_MI300X_v5` | 8 | **$6.000** | $3.840 | $2.634 | $1.109 |
| Azure (swedencentral) | `ND96is_MI300X_v5` | 8 | $8.400 | ⚠️ | ⚠️ | $1.654 |
| Oracle OCI | `BM.GPU.MI300X.8` | 8 | **$6.00** | ⚠️ | ⚠️ | — |
| AWS | — (no AMD family) | — | — | — | — | — |
| GCP | — (no AMD family) | — | — | — | — | — |
| Crusoe | MI300X | 8 | **$3.45** | CS | CS | CS |
| DigitalOcean | MI300X | 1 or 8 | **$2.59** | $1.91 (12-mo) | — | — |
| Hot Aisle | MI300X VM | 1/2/4 | **$2.99** | — | — | — |
| Hot Aisle | MI300X bare metal | 8 | $3.39 | 1-month min | — | — |
| RunPod | — (not listed) | — | — | — | — | — |
| TensorWave | MI300X | 8 | $1.71 ⚠️ quote-based | CS | CS | — |
| Vultr | MI300X | — | $3.99 ⚠️ secondary | — | — | $1.85 ⚠️ |
| Cirrascale | MI300X | — | $3.85 ⚠️ secondary | — | — | — |

**Cheapest reputable on-demand: DigitalOcean $2.59 (Hot Aisle $2.99, Crusoe $3.45).
TensorWave's $1.71 is quote-based, not a published rate. Cheapest hyperscaler:
Azure / OCI $6.00. Cheapest committed: Azure 3-yr $2.634 / DigitalOcean 12-mo $1.91.
Cheapest spot: Azure $1.109.**

MI300X on Azure spot at **$1.109/GPU-hr with 192 GB HBM** is the cheapest
$/GB-hour in this entire survey ($0.0058/GB-hr).

### 5.12 MI325X

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot |
|---|---|---|---|---|---|---|
| DigitalOcean | MI325X | 1 or 8 | **$3.80** | $2.88 (12-mo) | — | — |
| Vultr | MI325X | — | $4.62 ⚠️ secondary | — | — | — |
| TensorWave | MI325X | 8 | not published ⚠️ | CS | CS | — |
| Azure / AWS / GCP / OCI | — | — | no SKU | — | — | — |
| Crusoe / CoreWeave / Nebius / Lambda / RunPod / Hyperstack | — | — | no SKU | — | — | — |

MI325X is the thinnest market in this survey: **one published on-demand price
(DigitalOcean $3.80)** and one secondary figure. If your plan depends on MI325X,
get a quote; do not plan against a list.

### 5.13 MI355X

| Provider | Instance | GPUs | On-demand | 1-yr | 3-yr | Spot |
|---|---|---|---|---|---|---|
| Oracle OCI | `BM.GPU.MI355X.8` | 8 | **$8.60** | ⚠️ | ⚠️ | — |
| Crusoe | MI355X | 8 | CS | CS | CS | CS |
| TensorWave | MI355X | 8 | $2.95 ⚠️ quote-based | CS | CS | — |
| DigitalOcean | MI350X (≠ MI355X) | 1 or 8 | not on-demand | **$4.76** (12-mo) | — | $4.50 ⚠️ |
| Vultr | MI355X | — | — | — | — | $2.59 preemptible ⚠️ |
| AWS / GCP / Azure | — | — | no SKU | — | — | — |

**The only published on-demand MI355X price is OCI's $8.60.** TensorWave's $2.95
is a quote-based number reported by a third party and, if real, would make MI355X
the cheapest FP4-capable GPU-hour on the market by a wide margin — that gap is
large enough that it must be verified before it is used in any plan.

⚠️ **DigitalOcean's "MI350X" is a distinct SKU** (air-cooled, lower TBP) from
MI355X (1,400 W, liquid). Do not substitute its price for MI355X.

### 5.14 Planning prices — the three rows every other doc cites

**This is the table to cite.** The per-(model, GPU) docs under
`research/models/<exp>/<gpu>.md` and the GPU docs under `research/gpus/<gpu>.md`
must take `price_per_gpu_hour` (METHODOLOGY §6) from here **by name** — never by
re-deriving it from §3–§5, and never from a neighbouring GPU's row.

Cite as: **`planning price · <slug> · low`**, **`· high`**, **`· res1y`**.

| GPU (slug) | **low** = cheapest reputable on-demand | **high** = cheapest hyperscaler on-demand | **res1y** = cheapest published 1-year commitment |
|---|---|---|---|
| `a100-40` A100 40GB SXM | **$1.99** (Lambda 8×) | **$2.745** (AWS `p4d`) | **$1.740** (AWS RI 1y) |
| `a100` A100 80GB SXM | **$1.59** (RunPod Secure) | **$3.431** (AWS `p4de`) | **$1.36** (Hyperstack reserved SXM) |
| `h100` H100 SXM5 | **$3.20** (Hyperstack) | **$6.880** (AWS `p5`) | **$2.72** (Hyperstack reserved) |
| `h200` H200 SXM | **$3.99** (Hyperstack) | **$7.912** (AWS `p5en`) | **$2.79** (Hyperstack reserved) |
| `b200` B200 HGX | **$6.00** (Hyperstack) | **$14.00** (OCI `BM.GPU.B200.8`) | **$5.10** (Hyperstack reserved) |
| `b300` B300 HGX | **$7.40** (Hyperstack) | **$15.00** (OCI `BM.GPU.B300.8`) | **$7.94** (DigitalOcean 12-mo) |
| `gb200` GB200 NVL72 | **$10.50** (CoreWeave slice) | **$16.00** (OCI `BM.GPU.GB200.4`) | **$17.31** (Azure 1y) ⚠️ *above* on-demand |
| `gb300` GB300 NVL72 | **$18.00** (OCI — the only published rate anywhere) | **$18.00** (OCI `BM.GPU.GB300.4`) | ⚠️ **none published** — use `high` |
| `rtx6000-pro` RTX PRO 6000 SE | **$1.80** (Nebius) | **$4.143** (AWS `g7e.48xlarge`) | **$1.30** (Hyperstack reserved) |
| `mi300x` MI300X | **$2.59** (DigitalOcean) | **$6.00** (Azure / OCI) | **$1.91** (DigitalOcean 12-mo) |
| `mi325x` MI325X | **$3.80** (DigitalOcean) | ⚠️ **no hyperscaler SKU** — use `low` | **$2.88** (DigitalOcean 12-mo) |
| `mi355x` MI355X | **$8.60** (OCI — the only published rate) | **$8.60** (OCI `BM.GPU.MI355X.8`) | ⚠️ **none published** — use `high` |

Rules for using this table:
- **Reputable** excludes marketplaces (Vast.ai, RunPod Community), quote-only
  third-party figures (TensorWave MI300X $1.71 / MI355X $2.95, Voltage Park
  $1.99) and SF Compute's blended $2.07. Those appear in §5 and are never a
  planning price.
- `a100-40`, `gb200`, `mi300x` and `mi325x` have no METHODOLOGY §8 GPU row; they
  are priced here for completeness and are not part of the pinned GPU set.
- **`low` and `high` are the same number for `gb300` and `mi355x`** because OCI is
  the only published seller. Any cost model for those two rests on one price —
  say so wherever you use it.
- Where `res1y` exceeds `low` (`gb200`), the commitment is *not* a saving; it buys
  guaranteed capacity at a hyperscaler. Use `low` for the cost floor.
- Sensitivity: quote `low` and `high` as a band rather than a point whenever the
  spread exceeds 2×. `high ÷ low`, computed in `python3`: `b200` 2.33×,
  `mi300x` 2.32×, `rtx6000-pro` 2.30×, `a100` 2.16×, `h100` 2.15×, `b300` 2.03×,
  `h200` 1.98×, `gb200` 1.52×, `a100-40` 1.38×, `gb300` and `mi355x` 1.00×.

---

## 6. What the prices are per *node*

Useful when the parallelism plan (METHODOLOGY §3) pins you to whole nodes.

| Node | GPUs | Cheapest reputable $/node-hr | Hyperscaler $/node-hr |
|---|---|---|---|
| 8× A100 80GB | 8 | $12.80 (Hyperstack SXM $1.60×8); $10.80 if PCIe $1.35×8 | $27.45 (AWS p4de) |
| 8× H100 SXM | 8 | $25.60 (Hyperstack) | $55.04 (AWS p5) |
| 8× H200 SXM | 8 | $31.92 (Hyperstack) | $63.30 (AWS p5en) |
| 8× B200 | 8 | $48.00 (Hyperstack) | $113.93 (AWS p6-b200) |
| 8× B300 | 8 | $59.20 (Hyperstack) | $142.42 (AWS p6-b300) |
| 8× RTX PRO 6000 | 8 | $14.40 (Nebius) | $33.14 (AWS g7e) |
| 8× MI300X | 8 | $20.72 (DigitalOcean) | $48.00 (Azure/OCI) |
| 8× MI355X | 8 | $68.80 (OCI) | $68.80 (OCI) |
| GB200 NVL72 full rack | 72 | $756.00 (CoreWeave @ $10.50) | $1,152.00 (OCI @ $16.00) / $1,946.88 (Azure @ $27.04) |
| GB300 NVL72 full rack | 72 | $1,296.00 (OCI @ $18.00) | $1,296.00 (OCI) |

---

## 7. Purchase prices

**No vendor publishes list prices for HGX baseboards, DGX systems or NVL72
racks.** Every figure below is trade-press or integrator-quote derived and is
labelled with its source and date. Treat all of §7 as **⚠️ indicative, not
quotable**.

### 7.1 Per GPU

| GPU | Price per GPU | Date | Source | Confidence |
|---|---|---|---|---|
| A100 80GB | $10,000–$15,000 (new) | 2026 H1 | [IntuitionLabs](https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026) citing CloudZero | ⚠️ secondary |
| H100 SXM5 80GB | $25,000–$30,000 | verified 2026-05-04 | [Mercatus](https://www.mercatus-ai.com/blog/h100-gpu-cost) | ⚠️ trade press, Supermicro/Dell/HPE quotes |
| H100 PCIe 80GB | $22,000–$27,000 | 2026-05-04 | [Mercatus](https://www.mercatus-ai.com/blog/h100-gpu-cost) | ⚠️ |
| H100 refurb / decommissioned | $18,000–$22,000 | 2026 | [Mercatus](https://www.mercatus-ai.com/blog/h100-gpu-cost) | ⚠️ |
| H100 secondary market (wide) | $6,000–$22,000 | mid-2026 | [IntuitionLabs](https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026) citing CloudZero | ⚠️ |
| H200 SXM5 141GB | $32,000–$40,000 | verified 2026-05-04 | [Mercatus](https://www.mercatus-ai.com/blog/h200-price) | ⚠️ |
| H200 PCIe/NVL | $28,000–$34,000 | 2026-05-04 | [Mercatus](https://www.mercatus-ai.com/blog/h200-price) | ⚠️ |
| B200 | $45,000–$55,000 | 2026-02-13 | [gpu.fm](https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026) | ⚠️ |
| B300 | **⚠️ TO BE VERIFIED** — no source found. Method: scale B200 by the OCI cloud price ratio ($15.00/$14.00 = 1.07) → ~$48,000–$59,000, or by the HGX BOM ratio (2.1 TB vs 1.4 TB HBM, +50% FP4) → ~$55,000–$70,000 | — | — | est. |
| GB200 (in NVL72) | ~$41,700/GPU implied by rack price | 2026 | derived, see §7.2 | est. |
| GB300 (in NVL72) | ⚠️ TO BE VERIFIED | — | — | est. |
| RTX PRO 6000 Blackwell | NVIDIA list **$13,250**, raised (+55% vs launch MSRP ~$8,548) | 2026 (Tom's Hardware) | [Tom's Hardware](https://www.tomshardware.com/pc-components/gpus/nvidia-raises-rtx-pro-6000-blackwell-gpu-pricing-to-usd13-250-55-percent-increase-over-msrp-in-a-years-time) | ⚠️ trade press |
| RTX PRO 6000 Blackwell — street | avg $14,758; Newegg $14,999; Amazon last-tracked $19,999 | Sept 2026 | [Thunder Compute](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) | ⚠️ retail, volatile |
| RTX PRO 6000 96GB (earlier quote) | $9,450–$9,800 | March 2026 | [IntuitionLabs](https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026) citing ElectroPages | ⚠️ superseded |
| MI300X / MI325X / MI355X | **⚠️ TO BE VERIFIED** — no purchase price found from any source. AMD does not publish, and no integrator quote surfaced. | — | — | — |

### 7.2 Per system

| System | Price | Per GPU | Date | Source |
|---|---|---|---|---|
| 8-GPU HGX A100 80GB server | ⚠️ TO BE VERIFIED (est. $120k–$170k by scaling H100 server $/GPU by the GPU price ratio) | ~$15k–21k | — | est. |
| 8-GPU HGX H100 server | $250,000–$320,000 (typ. $285,000) | $31,000–$40,000 | 2026-05-04 | [Mercatus](https://www.mercatus-ai.com/blog/h100-gpu-cost) |
| DGX H100 | $300,000–$460,000 | $37,500–$57,500 | 2026 H1 | [IntuitionLabs](https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026) citing DeployBase |
| 8-GPU HGX H200 server | $320,000–$420,000 (typ. ~$370,000) | $40,000–$52,500 | 2026-05-04 | [Mercatus](https://www.mercatus-ai.com/blog/h200-price) |
| DGX H200 | $400,000–$500,000 | $50,000–$62,500 | 2026 H1 | [IntuitionLabs](https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026) |
| 8× B200 HGX baseboard | $380,000–$450,000 | $47,500–$56,250 | 2026-02-13 | [gpu.fm](https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026) |
| DGX B200 (complete 8-GPU system) | **~$515,000** | ~$64,375 | 2026-02-13 | [gpu.fm](https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026) |
| 8-GPU HGX B300 server | ⚠️ TO BE VERIFIED (est. $560k–$660k = DGX B200 scaled by 1.1–1.3× for HBM and TBP) | ~$70k–82k | — | est. |
| GB200 NVL72 rack | **~$3,000,000** | ~$41,700 | 2026-02-13 | [gpu.fm](https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026) |
| GB200 NVL72 rack (range) | $2,000,000–$3,000,000 | $27,800–$41,700 | 2026 | [IntuitionLabs](https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026) citing Spheron |
| GB300 NVL72 rack | **⚠️ TO BE VERIFIED** — est. $3.3M–$3.8M. Method: GB200 NVL72 $3.0M × the OCI cloud-price ratio ($18.00/$16.00 = 1.125) = $3.375M; cross-check via +50% dense FP4 and +55% HBM → upper bound $3.8M | ~$46k–53k | — | est. |
| 8-GPU MI300X / MI325X / MI355X server | ⚠️ TO BE VERIFIED — no source | — | — | — |
| 8-GPU RTX PRO 6000 server | ⚠️ TO BE VERIFIED — est. $135k–$155k (8 × $13,250 GPU + ~$30k dual-socket platform, NIC, chassis) | ~$17k–19k | — | est. |

### 7.3 Sanity check: purchase vs. rent

Break-even months for 8× H100 at typical 2026 prices:

```
server capex      = $285,000  (8 × H100 SXM, HGX)        [Mercatus]
cheapest rent     = $3.20/GPU-hr × 8 = $25.60/node-hr    [Hyperstack]
hours to break even (capex only) = 285000 / 25.60 = 11,133 h = 15.3 months @ 100% duty
hyperscaler rent  = $6.88/GPU-hr × 8 = $55.04/node-hr    [AWS p5]
hours to break even = 285000 / 55.04 = 5,178 h = 7.1 months @ 100% duty
```
Adding colo power at $180/kW-month × 10.2 kW = $1,836/month pushes the
Hyperstack break-even to **~17.2 months** and the AWS break-even to **~7.5 months**.
Arithmetic run in `python3`; inputs cited above.

⚠️ Note the two halves of this block use **different hours-per-month constants**:
11,133 h ÷ 15.3 months implies 730 h/month (8760÷12), while the colo-adjusted
17.2 and 7.5 figures only reproduce at 720 h/month. At a consistent 730.5 h/month
the colo-adjusted numbers are **16.9 months** (Hyperstack) and **7.4 months**
(AWS). Re-verified with `python3` on 2026-09-19; the ±0.3-month spread does not
change any conclusion but the constant should be unified.

---

## 8. Power and colocation

### 8.1 Board and system power

| Unit | Power | Source |
|---|---|---|
| A100 40GB SXM / PCIe | 400 W / 250 W | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) |
| A100 80GB SXM / PCIe | 400 W / 300 W | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) |
| H100 SXM | up to 700 W | [NVIDIA H100](https://www.nvidia.com/en-us/data-center/h100/) |
| H100 NVL (PCIe) | 350–400 W | [NVIDIA H100](https://www.nvidia.com/en-us/data-center/h100/) |
| H200 SXM | up to 700 W (configurable) | [NVIDIA H200](https://www.nvidia.com/en-us/data-center/h200/) |
| H200 NVL (PCIe) | up to 600 W (configurable) | [NVIDIA H200](https://www.nvidia.com/en-us/data-center/h200/) |
| B200 (HGX) | ~1,000 W ⚠️ (derived: DGX B200 14.3 kW system ÷ 8 ≈ 1.79 kW/GPU incl. CPU/fans/PSU losses; board TDP not published on the HGX page) | [NVIDIA DGX B200](https://www.nvidia.com/en-us/data-center/dgx-b200/) |
| B300 (GB300 NVL72) | **1,100 W per GPU** (corrected 2026-09-19; the cited Lenovo page says "1100W total graphics power per GPU", not 1,400 W. 72 × 1.1 kW = 79.2 kW of GPU inside a 135 kW rack, which is what leaves room for 36 Grace CPUs + NVSwitch + losses) | [Lenovo GB300 NVL72](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) |
| B300 (HGX B300 SXM, air/liquid 8-GPU board) | 1,100–1,400 W ⚠️ TO BE VERIFIED — NVIDIA publishes no HGX B300 board TDP; the 1,400 W figure circulating for "B300" is the standalone SXM part, not the GB300 NVL72 configuration | [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) |
| RTX PRO 6000 Blackwell SE | up to 600 W (configurable) | [NVIDIA RTX PRO 6000 SE](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) |
| MI300X | 750 W peak (TBP) | [AMD MI300X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html) |
| MI325X | 1,000 W peak (TBP) | [AMD MI325X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html) |
| MI355X | 1,400 W (TBP) | [AMD MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html) |

### 8.2 System / rack draw

| System | Draw | Source |
|---|---|---|
| DGX B200 (8 GPU) | **~14.3 kW max** | [NVIDIA DGX B200](https://www.nvidia.com/en-us/data-center/dgx-b200/) |
| DGX H100 / H200 (8 GPU) | ~10.2 kW ⚠️ TO BE VERIFIED — widely cited, not confirmed on an NVIDIA page in this pass. Method for planning: 8 × 700 W GPU + ~2 kW host/fans/PSU loss ≈ 7.6 kW … 10.2 kW peak | est. |
| HGX B300 (8 GPU) | ⚠️ TO BE VERIFIED — est. **15–16 kW** from 8 × ~1,200 W + ~5 kW host overhead, or by scaling DGX B200's 14.3 kW by the TBP ratio | est. |
| GB200 NVL72 rack | ⚠️ TO BE VERIFIED — est. **~120 kW** (72 × 1.2 kW GPU + 36 Grace + NVSwitch + losses). No primary source found. | est. |
| GB300 NVL72 rack | **135 kW TDP, up to 155 kW peak** (EDPp); ~90% liquid / 10% air; water inlet up to 45 °C | [Lenovo GB300 NVL72](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) |
| 8× MI355X server | ⚠️ TO BE VERIFIED — est. **~14 kW** (8 × 1.4 kW + host) | est. |
| 8× RTX PRO 6000 server | ⚠️ TO BE VERIFIED — est. **~6.5 kW** (8 × 600 W + ~1.7 kW host) | est. |

Rack-power implication: **one GB300 NVL72 needs 135–155 kW in a single rack with
mandatory direct-to-chip liquid cooling.** That is above what most retail colo
halls provision per rack, and is the single biggest constraint on on-prem
Blackwell-Ultra deployment.

### 8.3 Colocation $/kW-month

All figures are broker/analyst aggregates — **no primary CBRE or datacenterHawk
document was retrievable** (datacenterHawk returned HTTP 403 on 2026-09-19).

| Segment | $/kW-month | Date | Source |
|---|---|---|---|
| US wholesale colocation, 250–500 kW | **~$195.94** | H2 2025 (CBRE, cited second-hand) | [Encor Advisors](https://encoradvisors.com/data-center-colocation-pricing/) (article dated 2026-06-01) citing Brightlio |
| Retail colo, Tier 1 markets | $120–$180 | 2026 | [Encor Advisors](https://encoradvisors.com/data-center-colocation-pricing/) ⚠️ secondary |
| Wholesale 1 MW+ | $80–$130 | 2026 | [Encor Advisors](https://encoradvisors.com/data-center-colocation-pricing/) ⚠️ secondary |
| **GPU-density colo (liquid-cooled)** | **$150–$250** | 2026 | [Encor Advisors](https://encoradvisors.com/data-center-colocation-pricing/) ⚠️ secondary; stated as 30–50% premium over standard |
| High-density rack (10–30 kW) | $3,000–$6,000/rack/month | 2026 | [Encor Advisors](https://encoradvisors.com/data-center-colocation-pricing/) |

**Planning value used below: $180/kW-month** (mid of the GPU-density band).
Sensitivity at $120 and $250 is shown.

⚠️ Colo is billed on **provisioned** kW, not consumed kW. A 135 kW GB300 rack
with a 155 kW EDPp usually has to be provisioned at ~155 kW, not 135 kW. The
numbers below use nameplate TDP and therefore **understate** colo cost by
roughly the EDPp/TDP margin (~15% on GB300).

---

## 9. Amortised on-prem $/GPU-hour

### 9.1 Formula

```
amortised_$/GPU-hr = capex_per_GPU / (life_years × 8760 × U)
                   + (kW_per_GPU × colo_$/kW-month × 12) / (8760 × U)

capex_per_GPU = system_price / n_GPUs        (all-in: GPUs, CPU, RAM, NVMe, NIC, chassis)
kW_per_GPU    = system_draw_kW / n_GPUs      (the full facility draw the colo bills)
U             = fraction of wall-clock hours the GPU is actually serving
```

Both terms are divided by `U` because the denominator is *useful* GPU-hours.
Colo bills for 8,760 h regardless; at U < 1 the idle hours reallocate onto the
billable ones. Excluded from this model and **not small**: network fabric
(IB/Ethernet switches, cables, optics — commonly 8–15% of cluster capex),
storage, remote hands, software licences (e.g. NVIDIA AI Enterprise, which OCI
charges $2.50–$4.00/GPU-hr for), spares, staff, and cost of capital. **Add 20–35%
to every number in §9.2 for a realistic fully-loaded figure.** ⚠️

### 9.2 Results — colo $180/kW-month, U = 0.90

| GPU | capex/GPU | kW/GPU | **3-yr $/GPU-hr** | of which capex | of which power | **5-yr $/GPU-hr** |
|---|---|---|---|---|---|---|
| A100 80GB SXM (8-GPU HGX, $164k est ⚠️) | $20,500 | 0.800 | **$1.086** | $0.867 | $0.219 | **$0.739** |
| H100 SXM (8-GPU HGX @ $285k) | $35,625 | 1.275 | **$1.856** | $1.506 | $0.349 | **$1.253** |
| H200 SXM (8-GPU HGX @ $370k) | $46,250 | 1.275 | **$2.305** | $1.955 | $0.349 | **$1.523** |
| B200 (DGX B200 @ $515k) | $64,375 | 1.788 | **$3.211** | $2.722 | $0.490 | **$2.123** |
| B300 (HGX B300 @ $600k est ⚠️) | $75,000 | 1.938 | **$3.702** | $3.171 | $0.531 | **$2.433** |
| GB200 NVL72 (@ $3.0M, 120 kW est ⚠️) | $41,667 | 1.667 | **$2.218** | $1.762 | $0.457 | **$1.514** |
| GB300 NVL72 (@ $3.5M est ⚠️, 135 kW) | $48,611 | 1.875 | **$2.569** | $2.055 | $0.514 | **$1.747** |
| RTX PRO 6000 SE (8-GPU @ $140k est ⚠️) | $17,500 | 0.813 | **$0.962** | $0.740 | $0.223 | **$0.667** |
| MI300X (8-GPU @ $160k est ⚠️) | $20,000 | 1.125 | **$1.154** | $0.846 | $0.308 | **$0.816** |
| MI355X (8-GPU @ $290k est ⚠️) | $36,250 | 1.750 | **$2.012** | $1.533 | $0.479 | **$1.399** |

Every row whose capex is marked ⚠️ inherits that uncertainty — the MI300X,
MI355X and RTX PRO 6000 rows in particular are **estimates on estimates**.

Worked example (H100, 3 yr):
```
capex term = 35,625 / (3 × 8760 × 0.90) = 35,625 / 23,652 = $1.506/GPU-hr
power term = (1.275 kW × $180/kW-mo × 12) / (8760 × 0.90) = 2,754 / 7,884 = $0.349/GPU-hr
total      = $1.856/GPU-hr
```

### 9.3 Sensitivity to colo price (3 yr, U = 0.90)

| GPU | $120/kW-mo | $180/kW-mo | $250/kW-mo |
|---|---|---|---|
| H100 SXM | $1.739 | $1.856 | $1.991 |
| GB300 NVL72 | $2.398 | $2.569 | $2.769 |

Colo price is a **second-order** lever: a 2× swing in $/kW-month moves the
all-in H100 number by 14%. Capex and utilisation dominate. At U = 0.50 instead of
0.90, every number in §9.2 nearly doubles.

### 9.4 On-prem vs. rent

| GPU | On-prem 3-yr | On-prem 5-yr | Cheapest reputable cloud OD | Cheapest committed cloud | Cheapest hyperscaler OD |
|---|---|---|---|---|---|
| A100 80GB | $1.09 ⚠️ | $0.74 ⚠️ | $1.35 (Hyperstack PCIe) | $0.95 (Hyperstack res.) | $3.43 (AWS) |
| H100 SXM | $1.86 | $1.25 | $3.20 (Hyperstack) | $2.72 (Hyperstack res.) | $6.88 (AWS) |
| H200 SXM | $2.31 | $1.52 | $3.99 (Hyperstack) | $2.79 (Hyperstack res.) | $7.91 (AWS) |
| B200 | $3.21 | $2.12 | $6.00 (Hyperstack) | $5.10 (Hyperstack res.) | $14.00 (OCI) |
| B300 | $3.70 ⚠️ | $2.43 ⚠️ | $7.40 (Hyperstack) | $7.94 (DO 12-mo) | $15.00 (OCI) |
| GB200 NVL72 | $2.22 ⚠️ | $1.51 ⚠️ | $10.50 (CoreWeave) | $11.90 (Azure 3-yr) | $16.00 (OCI) |
| GB300 NVL72 | $2.57 ⚠️ | $1.75 ⚠️ | $18.00 (OCI) | ⚠️ none published | $18.00 (OCI) |
| RTX PRO 6000 | $0.96 ⚠️ | $0.67 ⚠️ | $1.80 (Nebius) | $1.30 (Hyperstack res.) | $4.14 (AWS) |
| MI300X | $1.15 ⚠️ | $0.82 ⚠️ | $2.59 (DigitalOcean) | $1.91 (DO 12-mo) | $6.00 (Azure/OCI) |
| MI355X | $2.01 ⚠️ | $1.40 ⚠️ | $8.60 (OCI) | ⚠️ none published | $8.60 (OCI) |

The consistent pattern: **on-prem at 90% utilisation is roughly half the cheapest
neocloud on-demand price and one-third to one-quarter of hyperscaler list** — before
the 20–35% of unmodelled cost in §9.1 and before anyone's salary. The gap is
widest on rack-scale NVL72 systems, precisely because cloud GB200/GB300 supply
is scarce and priced accordingly.

---

## 10. Normalised comparison

Dense TFLOPS from §2. `chp` = cheapest reputable on-demand = **`planning price ·
<slug> · low`** (§5.14); `hyp` = cheapest hyperscaler on-demand = **`· high`**.

**Capacity basis (recomputed 2026-09-19):** every row below uses the
**as-deployed** capacity pinned in [METHODOLOGY §8](../METHODOLOGY.md) — B200
**180 GB**, HGX B300 **268 GB**, GB300 NVL72 **288 GB**, MI355X 288 GB, RTX PRO
6000 SE 96 GB — not a vendor rate card's nameplate. Dense FLOPS likewise come
from METHODOLOGY §8, with RTX PRO 6000 SE's dense values taken from
[gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md) (960 FP8 / 1,920 FP4, the
page's printed 2 / 4 PFLOPS being sparse). All three tables were re-derived with
`python3`; the changed cells are listed in the sweep log.

### 10.1 $/GB-hour (HBM capacity)

| GPU | HBM GB | chp $/GPU-hr | **chp $/GB-hr** | hyp $/GPU-hr | **hyp $/GB-hr** |
|---|---|---|---|---|---|
| MI300X | 192 | $2.59 | **$0.0135** | $6.00 | $0.0313 |
| MI325X | 256 | $3.80 | **$0.0148** | — | — |
| RTX PRO 6000 SE | 96 | $1.80 | **$0.0188** | $4.14 | $0.0431 |
| A100 80GB | 80 | $1.59 | **$0.0199** | $3.43 | $0.0429 |
| B300 HGX | 268 | $7.40 | **$0.0276** | $15.00 | $0.0560 |
| H200 SXM | 141 | $3.99 | **$0.0283** | $7.91 | $0.0561 |
| MI355X | 288 | $8.60 | **$0.0299** | $8.60 | $0.0299 |
| B200 HGX | 180 | $6.00 | **$0.0333** | $14.00 | $0.0778 |
| H100 SXM | 80 | $3.20 | **$0.0400** | $6.88 | $0.0860 |
| A100 40GB | 40 | $1.99 | **$0.0498** | $2.745 | $0.0686 |
| GB200 NVL72 | 186 | $10.50 | **$0.0565** | $16.00 | $0.0860 |
| GB300 NVL72 | 288 | $18.00 | **$0.0625** | $18.00 | $0.0625 |

This is the metric that matters for **KV-cache-bound** serving (METHODOLOGY §3:
`max_concurrency` scales with `kv_budget`). MI300X and MI325X are the cheapest
HBM in the market by a wide margin; MI300X on Azure spot ($1.109/GPU-hr ÷ 192 GB
= **$0.0058/GB-hr**) is cheaper still by another 2.3×. GB300 is the most
expensive HBM per hour despite having the most of it.

Capacity basis, per METHODOLOGY §8: **GB300 NVL72 = 288 GB/GPU nameplate**, which
is what this row uses; at the ≈ 279 GB usable figure that CoreWeave and NVIDIA's
DGX GB300 page imply (see §2.1) it becomes **$0.0645/GB-hr**. **HGX B300 = 268 GB
as deployed** (2,144 GB per 8-GPU node), which is the figure this row now uses —
previously 270 GB, giving $0.0274/$0.0556. The two rows are on their respective
pinned bases and are **not** interchangeable: HGX B300 and GB300 NVL72 are
different parts with different memory, clocks and dense FLOPS.

### 10.2 $/(TB/s)-hour (memory bandwidth)

Decode throughput at moderate batch is bandwidth-bound (METHODOLOGY §4), so this
is the single best first-order predictor of tokens/s/$ for interactive serving.

| GPU | BW TB/s | chp $/GPU-hr | **chp $/(TB/s)-hr** |
|---|---|---|---|
| MI300X | 5.3 | $2.59 | **$0.489** |
| MI325X | 6.0 | $3.80 | **$0.633** |
| B200 HGX | 8.0 | $6.00 | **$0.750** |
| A100 80GB | 2.039 | $1.59 | **$0.780** |
| H200 SXM | 4.8 | $3.99 | **$0.831** |
| B300 HGX | 8.0 | $7.40 | **$0.925** |
| H100 SXM | 3.35 | $3.20 | **$0.955** |
| MI355X | 8.0 | $8.60 | **$1.075** |
| RTX PRO 6000 SE | 1.597 | $1.80 | **$1.127** |
| A100 40GB | 1.555 | $1.99 | **$1.280** |
| GB200 NVL72 | 8.0 | $10.50 | **$1.313** |
| GB300 NVL72 | 8.0 | $18.00 | **$2.250** |

Two things fall out. **MI300X is 1.5–2.7× cheaper per unit of bandwidth than
anything NVIDIA sells** — whether that converts into tokens/s depends entirely on
whether the model and engine hit AMD's MBU (METHODOLOGY §4 plans 0.4–0.6 on
ROCm vs 0.6–0.8 on Hopper, which claws back most but not all of the gap).
And **RTX PRO 6000's GDDR7 at 1.597 TB/s makes it a poor decode engine** despite
the attractive $/GB-hr — it is a prefill and small-model card.
⚠️ **1,597 GB/s is the Server Edition**, which is the part every provider in §5.10
rents (AWS `g7e`, GCP `g4`, OCI, CoreWeave, Nebius, RunPod, Hyperstack all name
"Server Edition" or "SE"). The **1,792 GB/s** figure that circulates for
"RTX PRO 6000 Blackwell" is the **Workstation Edition** — a different SKU, not
sold by any provider in this survey. Do not substitute it; it would overstate
decode throughput by 12 % and understate $/(TB/s)-hr to $1.004.

### 10.3 $/PFLOP-hour (dense)

| GPU | dense FP8 TFLOPS | **chp $/PFLOP-hr FP8** | **hyp $/PFLOP-hr FP8** | dense FP4 TFLOPS | **chp $/PFLOP-hr FP4** |
|---|---|---|---|---|---|
| MI300X | 2,610 | **$0.992** | $2.299 | — | — |
| B200 HGX | 4,500 | **$1.333** | $3.111 | 9,000 | **$0.667** |
| MI325X | 2,610 | **$1.456** | — | — | — |
| H100 SXM | 1,979 | **$1.617** | $3.477 | — | — |
| B300 HGX | 4,500 | **$1.644** | $3.333 | 13,500 | **$0.548** |
| MI355X | 5,000 | **$1.720** | $1.720 | 10,100 | **$0.851** |
| RTX PRO 6000 SE | 960 | **$1.875** | $4.316 | 1,920 | **$0.938** |
| H200 SXM | 1,979 | **$2.016** | $3.998 | — | — |
| GB200 NVL72 | 5,000 | **$2.100** | $3.200 | 10,000 | **$1.050** |
| GB300 NVL72 | 5,000 | **$3.600** | $3.600 | 15,000 | **$1.200** |

The RTX PRO 6000 row is no longer flagged. Its page prints 4 PFLOPS FP4 /
2 PFLOPS FP8 / 1 PFLOP FP16 with **no sparsity footnote at all** (re-fetched
2026-09-19: zero occurrences of the string "sparsit" on the page), and
[gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md) reconciles them against the
SM FLOP/clock rate to show they are the sparse figures. Dense = ÷2, so the row
above uses **960 FP8 / 1,920 FP4** (METHODOLOGY §8: "FP4 ≈ 2,000 dense, 4,000 is
sparse"). It does **not** become the cheapest FP4 FLOP — at $0.938/PFLOP-hr it
sits above B300 ($0.548), B200 ($0.667) and MI355X ($0.851).

The GB300 dense FP8 of 5,000 TFLOPS is likewise no longer flagged: it is pinned
by METHODOLOGY §8 and by [gpus/gb300.md](../gpus/gb300.md).

**FP4 is where the money is.** B300 at $0.548/PFLOP-hr dense FP4 is 3× cheaper
per FLOP than H100 is per FP8 FLOP, and nothing in the Hopper generation can run
NVFP4 at all. If a model ships an NVFP4 checkpoint, Blackwell/Blackwell-Ultra
economics are not incrementally better — they are categorically different.

### 10.4 Composite view

| GPU | chp $/GPU-hr | $/GB-hr | $/(TB/s)-hr | $/PF-hr FP8 | $/PF-hr FP4 | Best at |
|---|---|---|---|---|---|---|
| MI300X | $2.59 | $0.0135 | $0.489 | $0.992 | — | Cheapest HBM, cheapest bandwidth, cheapest FP8 FLOP. No FP4. |
| MI325X | $3.80 | $0.0148 | $0.633 | $1.456 | — | Big-KV workloads; thin market. |
| B300 HGX | $7.40 | $0.0276 | $0.925 | $1.644 | **$0.548** | Cheapest dense FP4. |
| B200 HGX | $6.00 | $0.0333 | $0.750 | $1.333 | $0.667 | Best all-round Blackwell. |
| H200 SXM | $3.99 | $0.0283 | $0.831 | $2.016 | — | Cheapest Hopper with real KV headroom. |
| H100 SXM | $3.20 | $0.0400 | $0.955 | $1.617 | — | Deepest supply, most mature kernels. |
| MI355X | $8.60 | $0.0299 | $1.075 | $1.720 | $0.851 | Only AMD part with FP4/FP6; price is OCI-only. |
| RTX PRO 6000 | $1.80 | $0.0188 | $1.127 | $1.875 | $0.938 | Cheapest GPU-hour with ≥96 GB; bad for decode. |
| A100 80GB | $1.59 | $0.0199 | $0.780 | — | — | Cheapest absolute GPU-hour; no FP8. |
| GB200 NVL72 | $10.50 | $0.0565 | $1.313 | $2.100 | $1.050 | 72-GPU NVLink domain (wide-EP, huge MoE). |
| GB300 NVL72 | $18.00 | $0.0625 | $2.250 | $3.600 | $1.200 | Largest NVLink domain + 288 GB/GPU (≈ 279 usable). Priced at a scarcity premium. |

**The NVL72 premium is real and is not about FLOPS.** GB200 costs 1.75× a B200 per
hour for 1.11× the dense FP4. You pay for the 72-GPU NVLink domain — which buys
wide expert parallelism, DeepEP-style MoE routing and disaggregated
prefill/decode without leaving NVLink (METHODOLOGY §5.6). If your model fits in
8 GPUs, NVL72 is a bad trade.

---

## 11. Availability and quota realities

Price is necessary but not sufficient. As of 2026-09-19:

| Reality | Evidence |
|---|---|
| **GB300 has essentially one published on-demand price worldwide (OCI $18.00).** AWS has no GB300 SKU; Azure returns no GB300 meter; GCP's A4X Max is absent from the public price sheet; CoreWeave and Together say "contact sales". | [OCI API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD), [Azure API](https://prices.azure.com/api/retail/prices), [GCP](https://cloud.google.com/products/compute/pricing/accelerator-optimized), [CoreWeave](https://www.coreweave.com/pricing), [Together](https://www.together.ai/gpu-clusters) |
| **GCP will not sell B200 on demand at all** — `a4-highgpu-8g` on-demand is published as N/A. Your options are Spot, DWS Flex-start, DWS Calendar, or a CUD with an attached reservation. | [GCP](https://cloud.google.com/products/compute/pricing/accelerator-optimized) |
| **GCP A4/A4X/A3-Ultra take no sustained-use and no flexible CUDs.** Resource CUDs require an attached reservation, which is itself capacity-gated. | [GCP docs](https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines) |
| **AWS Blackwell is Capacity-Block-first.** No standard RIs exist for p6-b200/p6-b300/p5en/p6e-gb200; Capacity Blocks are prepaid, fixed-window, and were repriced upward effective 2026-07-01. | [AWS Capacity Blocks](https://aws.amazon.com/ec2/capacityblocks/pricing/), [Vantage](https://instances.vantage.sh/aws/ec2/p6-b200.48xlarge) |
| **AWS GB200 is confined to one Local Zone** (US East / Dallas) and sold only as 36- or 72-accelerator UltraServers. | [AWS Capacity Blocks](https://aws.amazon.com/ec2/capacityblocks/pricing/) |
| **Together's H200 and B200 headline prices require 256 GPUs minimum**; GB200 requires 512. An 8-GPU inference replica cannot buy at those rates. | [Together](https://www.together.ai/gpu-clusters) |
| **Lambda's cluster price exceeds its instance price** ($9.86 vs $6.69 for B200). Cheap Lambda B200 means 1–8 GPUs on shared fabric, not a cluster. | [Lambda](https://lambda.ai/pricing) |
| **CoreWeave was "largely sold out" of 2026 capacity** while raising list prices across generations; B300-class shows a spot rate but routes on-demand buyers to sales. | [CloudZero](https://www.cloudzero.com/blog/coreweave-pricing/), [Spheron](https://www.spheron.network/blog/coreweave-gpu-pricing-2026/), corroborated by the "Contact sales" cells on [CoreWeave's own page](https://www.coreweave.com/pricing) |
| **MI355X is a two-supplier market** (OCI published, Crusoe/TensorWave quote-only). MI325X is effectively a one-supplier market (DigitalOcean). | §5.12, §5.13 |
| **Azure spot on H200 and GB200 is priced at parity with on-demand** — i.e. there is no working spot discount on those SKUs. | [Azure API](https://prices.azure.com/api/retail/prices) |
| **Regional price spread is large on Azure**: H200 is $84.80/node-hr in eastus2/westus3 but $114.688 in swedencentral (+35%); MI300X is $48.00 vs $67.20 (+40%). | [Azure API](https://prices.azure.com/api/retail/prices) |
| **AWS regional spread is zero** for P/G instances across us-east-1/us-east-2/us-west-2 — verified by pulling all three sheets. SKU *availability* still differs (p6-b300 not in us-east-2; p4de not in us-east-2). | AWS price sheets, 2026-09-19 |
| **Marketplace supply (Vast, RunPod Community) is not plannable.** Vast's own page says prices are set by hosts, not by Vast. | [Vast.ai](https://vast.ai/pricing) |

---

## 12. How to use this with METHODOLOGY §6

```
cost_per_hour             = n_gpus × price_per_gpu_hour          ← §5.14 of this doc
cost_per_1M_output_tokens = cost_per_hour / (agg_output_tok_s × 3600) × 1e6
cost_per_1M_input_tokens  = cost_per_hour / (agg_prefill_tok_s × 3600) × 1e6
```

Which `price_per_gpu_hour` to use, by scenario:

| Scenario | Use | Why |
|---|---|---|
| "What does this cost if we ship it tomorrow?" | **`planning price · <slug> · low`** (§5.14) | Real, self-serve, no commitment |
| "What does this cost on our existing cloud contract?" | **`planning price · <slug> · high`** (§5.14) | You are quota-bound to one provider |
| "What is the floor?" | On-prem 3-yr (§9.2) **+ 25%** | Covers the fabric/storage/staff gap in §9.1 |
| "What is the risk-adjusted number?" | **`planning price · <slug> · res1y`** (§5.14) | Survives a price move; still cancellable-ish |
| Batch/offline | Spot (§5) | Preemption is tolerable; 40–80% discount |
| Never | Vast.ai / RunPod Community / SF Compute blended average | Not plannable, not per-model |

**Cached input (METHODOLOGY §6).** For *our own* serving cost a prefix-cache hit
skips prefill compute and costs only the KV load — assume **10 % of the uncached
prefill cost** unless measured, and say so. For *vendor API comparisons* use the
vendor's own published cached-input ratio, never a generic 10 %: DeepSeek ≈ 2.1 %,
Anthropic ≈ 2.5 %, Alibaba 10–25 % — the full table is in
[serving-optimizations.md §1.5](serving-optimizations.md).

**Standard scenarios (METHODOLOGY §6).** Cost tables built on this document report
at **S1** (4K in / 512 out, TPOT ≤ 50 ms), **S2** (32K in / 1K out, TPOT ≤ 50 ms),
**S3** (128K in / 2K out, no SLO), **S4** (4K in / 512 out, max-throughput), and
**blended** (75 % input of which 50 % cached / 25 % output, at the S1 operating
point).

Sanity anchors when you compute $/1M tokens: the cheapest reputable H100-hour is
**$3.20**, the cheapest B200-hour is **$6.00**, and the cheapest dense-FP4 PFLOP-hour
is **$0.548** (B300 at Hyperstack). Vendor API list prices to sanity-check
against, per 1M tokens ([serving-optimizations.md §1.5](serving-optimizations.md)):
**DeepSeek-V4-Flash $0.14 in / $0.0030 cached / $0.60 out** (the output figure is
**$0.14 input, not $0.15** — a $0.15 input price appears in some drafts and is
wrong), DeepSeek-V4-Pro $0.660 / $0.0220 / $1.98, Kimi K3 $3.00 / $0.30 / $15.00.
If a cost model lands more than ~3× away from what a model vendor charges per 1M
tokens on their public API, the throughput assumption is wrong, not the price.

**Where the per-(model, GPU) numbers live.** This document deliberately contains
**no fit, throughput or $/1M-token table for any specific model**. Those belong in
`research/models/<exp>/<gpu>.md` — e.g. `research/models/deepseek41f/b300.md`,
`research/models/kimik3/gb300.md` — and are written in the next phase. Do not add
them here or to any `research/gpus/*.md`; those docs cite §5.14 and stop.

**Price movement caveat:** neocloud rates moved materially during 2026 (SF
Compute's platform average sits near $2.07; H100 medians around $3.25 with H200
around $4.40 and B200 around $6.52 per third-party trackers in September 2026 —
[gpuperhour](https://gpuperhour.com/)). Anything in this document is a
2026-09-19 snapshot; re-pull §3's machine-readable endpoints before any decision
with a dollar sign on it.

---

## Open questions

Consolidated ⚠️ TO BE VERIFIED items.

**Pricing gaps**
1. **GCP A4X (GB200) and A4X Max (GB300) prices** — absent from the public accelerator-optimized price sheet entirely. Quote-only. Need a sales quote or a Cloud Billing Catalog API pull with credentials.
2. **Azure ND B200 v6 and ND GB300 v6** — no retail SKU returned by the Retail Prices API on 2026-09-19. Are these GA under a different SKU name, or genuinely quote-only?
3. **AWS `p5e.48xlarge` on-demand price** — absent from the us-east-1/us-east-2/us-west-2 price sheets. Vantage reports `$1.843/hr`, which is implausible for 8× H200 (it is below the spot price it reports on the same page, $26.797). Which regions still carry p5e, and at what price?
4. **OCI reserved / Annual Universal Credit rates** — OCI publishes only pay-as-you-go. What is the actual committed discount curve?
5. **CoreWeave reserved rate card** — "up to 60% off" is published; no term table is.
6. **Nebius commitment rates** — "up to 35% less"; no term table.
7. **Crusoe** — every spot and reserved cell reads "Contact sales"; B200, GB200 and MI355X have no on-demand price either.
8. **DataCrunch / Verda rate card** — `datacrunch.io` 301s to `verda.com`, `verda.com/products` is 404. Where is the current rate card?
9. **Fluidstack rate card** — `fluidstack.io/pricing` is 404. Quote-only confirmed?
10. **TensorWave rate card** — `tensorwave.com/pricing` is 404. Is MI355X at $2.95/GPU-hr real, and under what commitment? If real it is the cheapest FP4-capable GPU-hour in the market by 3×, which would change §10 materially.
11. **Vultr** — `vultr.com/pricing` returns HTTP 403 to non-browser clients. The B200 cloud ($8.50) vs bare-metal (from $3.50) spread is unexplained.
12. **Voltage Park / Lightning AI** — Voltage Park's page says H100 from $1.99; third-party post-merger reporting says from $3.75. Which is the current self-serve rate, and are H200/B200/B300 really contract-only?
13. **Vast.ai live medians** — `console.vast.ai/api/v0/bundles/` returned 404 from this environment (both GET and PUT). Need working API access for real per-model medians.
14. **SF Compute per-model clearing prices** — only a blended $2.07 platform average is published. What do H100, B300 and GB300 clear at?
15. **Scaleway H100 SXM and B300-SXM-8-288G prices** — shapes are listed but price cells did not render server-side; only L4 prices were retrievable. Also need a dated EUR/USD rate before any conversion.
16. **Hyperstack B300 reserved rate** — on-demand $7.40 published, reserved not listed ("reserved private clusters in Q4").
17. **RunPod MI300X/MI325X/MI355X and A100 40GB** — absent from the rate card. Discontinued or never offered?

**Hardware / spec gaps**
18. ~~**B300 HBM per GPU: 262.5 vs 270 vs 288 GB.**~~ **RESOLVED 2026-09-19** — METHODOLOGY §8 and [gpus/b300.md](../gpus/b300.md) pin the as-deployed figure at **268 GB/GPU = 2,144 GB per 8-GPU node**, corroborated by the AWS p6-b300 launch blog. 288 is the die nameplate, 262.5 is NVIDIA's rounded-down 2.1 TB board figure, 270 is CoreWeave's reported slice. §2.1 and §10 now use 268.
19. **B200 board TDP.** Not published on the NVIDIA HGX page; derived from DGX B200's 14.3 kW system draw. Need the HGX B200 datasheet figure.
20. **HGX B300 system power.** Estimated 15–16 kW; no primary source.
21. **DGX H100 / H200 system power.** The widely cited 10.2 kW was not confirmed on an NVIDIA page in this pass.
22. **GB200 NVL72 rack power.** Estimated ~120 kW; only GB300's 135 kW/155 kW peak is primary-sourced (Lenovo).
23. ~~**GB300 dense FP8.**~~ **RESOLVED** — METHODOLOGY §8 and [gpus/gb300.md](../gpus/gb300.md) pin GB300 NVL72 at **2,500 BF16 / 5,000 FP8 / 15,000 FP4 dense** per GPU. NVIDIA's 720 PFLOPS FP8 across 72 GPUs (= 10 PFLOPS/GPU) is the sparse figure. ⚠️ remains on the underlying NVIDIA page's ambiguity, not on the planning number.
24. **GB300 sparse FP4 ratio.** NVIDIA gives 1,440 sparse / 1,080 dense (1.33×, not 2×); Lenovo gives 18 PFLOPS/GPU sparse (= 1,296 per rack). Three numbers, unresolved — but **only the sparse side is unresolved**; dense 15,000/GPU is consistent across both and is what §10.3 uses.
25. ~~**RTX PRO 6000 Blackwell sparsity.**~~ **RESOLVED 2026-09-19** — the [Server Edition page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) was re-fetched and contains **zero occurrences of "sparsit"**, and [gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md) reconciles the printed 4 / 2 / 1 PFLOPS against the SM FLOP/clock rate to show they are sparse. Dense = **480 BF16 / 960 FP8 / 1,920 FP4** (METHODOLOGY §8: FP4 ≈ 2,000 dense). §2.1 and §10.3 corrected; the $/PFLOP-hr did **not** halve — it rose, because §2.1 had been carrying the sparse figures in dense columns.
25b. **RTX PRO 6000 Server vs Workstation bandwidth.** The Server Edition is **1,597 GB/s** (confirmed on the SE page, 2026-09-19). The 1,792 GB/s figure belongs to the **Workstation Edition**, which no provider in §5.10 rents. Several sibling docs cited a NVIDIA model card for a bandwidth figure the card does not carry — always cite the SE product page.
26. **DigitalOcean "MI350X" vs MI355X.** Different SKU (air-cooled, lower TBP). Need the MI350X spec (HBM, BW, TBP, FP4) to place its $4.76 12-month price in §10.

**Cost-model gaps**
27. **All purchase prices in §7** are trade-press/integrator-quote derived. No vendor list price exists for any HGX/DGX/NVL72 system.
28. **B300 and GB300 purchase prices** have no source at all; §7 uses cloud-price-ratio scaling, which is circular (cloud prices reflect scarcity, not BOM).
29. **AMD Instinct purchase prices (MI300X, MI325X, MI355X)** — none found from any source. Every AMD row in §9.2 rests on an estimate. (The AMD *specs* in §2.2 are no longer a gap — all three product pages were re-fetched successfully on 2026-09-19; see §2.2.)
30. **Colocation $/kW-month** — no primary CBRE or datacenterHawk document retrieved (datacenterHawk returned HTTP 403). All figures are second-hand from a broker article.
31. **Colo provisioned-kW vs nameplate-TDP.** §9 uses TDP; GB300's EDPp is 155 kW vs 135 kW TDP, so colo cost is understated ~15% for that row. Need real provisioning practice per facility.
32. **Network fabric, storage, licences, staff and cost of capital** are excluded from §9. The "+20–35%" adjustment is an estimate, not a measurement.
33. **Fully-loaded cost of an NVL72 deployment** — liquid-cooling CDU capex, 45 °C facility water availability, and the rack-power provisioning premium are all unmodelled.

---

## Sources

Primary, machine-readable (pulled 2026-09-19):
- https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json
- https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20West%20(Oregon)/Linux/index.json
- https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(Ohio)/Linux/index.json
- https://prices.azure.com/api/retail/prices
- https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD
- https://cloud.google.com/products/compute/pricing/accelerator-optimized

Vendor pricing pages:
- https://aws.amazon.com/ec2/capacityblocks/pricing/
- https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances — 2,144 GB per node / 268 GB per GPU
- https://instances.vantage.sh/aws/ec2/p4d.24xlarge
- https://instances.vantage.sh/aws/ec2/p5.48xlarge
- https://instances.vantage.sh/aws/ec2/p5e.48xlarge
- https://instances.vantage.sh/aws/ec2/p5en.48xlarge
- https://instances.vantage.sh/aws/ec2/p6-b200.48xlarge
- https://instances.vantage.sh/aws/ec2/p6-b300.48xlarge
- https://instances.vantage.sh/aws/ec2/g7e.48xlarge
- https://docs.cloud.google.com/compute/docs/accelerator-optimized-machines
- https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series
- https://www.oracle.com/cloud/price-list/
- https://www.coreweave.com/pricing
- https://www.coreweave.com/pricing/classic
- https://lambda.ai/pricing
- https://nebius.com/prices
- https://www.runpod.io/pricing
- https://www.hyperstack.cloud/gpu-pricing
- https://www.together.ai/gpu-clusters
- https://www.together.ai/gpu/nvidia-hgx-b200
- https://www.crusoe.ai/cloud/pricing
- https://www.digitalocean.com/pricing/gpu-droplets
- https://www.scaleway.com/en/pricing/gpu/
- https://hotaisle.xyz/pricing
- https://www.voltagepark.com/pricing
- https://tensorwave.com/
- https://vast.ai/pricing
- https://sfcompute.com/
- https://www.vultr.com/pricing/ (HTTP 403)
- https://verda.com/products (HTTP 404)
- https://www.fluidstack.io/pricing (HTTP 404)
- https://tensorwave.com/pricing (HTTP 404)
- https://console.vast.ai/api/v0/bundles/ (HTTP 404)

Hardware specifications:
- https://www.nvidia.com/en-us/data-center/a100/
- https://www.nvidia.com/en-us/data-center/h100/
- https://www.nvidia.com/en-us/data-center/h200/
- https://www.nvidia.com/en-us/data-center/hgx/
- https://www.nvidia.com/en-us/data-center/dgx-b200/
- https://www.nvidia.com/en-us/data-center/gb200-nvl72/
- https://www.nvidia.com/en-us/data-center/gb300-nvl72/
- https://www.nvidia.com/en-us/data-center/dgx-gb300/
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/
- https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html
- https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html
- https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html
- https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai

Purchase price, colocation and market context (all secondary — trade press and analyst aggregates):
- https://www.mercatus-ai.com/blog/h100-gpu-cost
- https://www.mercatus-ai.com/blog/h200-price
- https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026
- https://intuitionlabs.ai/articles/data-center-gpu-pricing-2026
- https://www.tomshardware.com/pc-components/gpus/nvidia-raises-rtx-pro-6000-blackwell-gpu-pricing-to-usd13-250-55-percent-increase-over-msrp-in-a-years-time
- https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing
- https://www.thundercompute.com/blog/nvidia-b200-pricing
- https://encoradvisors.com/data-center-colocation-pricing/
- https://datacenterhawk.com/resources/fundamentals/colocation-data-center-pricing-a-2026-beginner-s-guide (HTTP 403)
- https://www.cloudzero.com/blog/coreweave-pricing/
- https://www.spheron.network/blog/coreweave-gpu-pricing-2026/
- https://www.spheron.network/blog/amd-mi300x-mi355x-pricing-2026/
- https://www.spheron.network/blog/nvidia-b300-blackwell-ultra-guide/
- https://www.spheron.network/blog/vastai-pricing-2026/
- https://www.spheron.network/blog/voltage-park-is-now-lightning-ai-gpu-pricing-2026/
- https://www.spheron.network/blog/scaleway-h100-pricing-2026/
- https://gpuperhour.com/
- https://gpuperhour.com/rent/mi325x
- https://inferencex.semianalysis.com/
- https://getdeploying.com/gpus (HTTP 403 on sub-pages)

---

## Verification log (2026-09-19)

Adversarial fact-check pass. Every claim below was re-derived from a source
opened independently of the document's own citation, or recomputed with
`python3` from METHODOLOGY.md. **28 claims checked: 22 CONFIRMED, 5 CORRECTED,
1 UNVERIFIABLE**, plus three provider/vendor blocks that could not be reached at
all and are now flagged inline.

### CORRECTED

| # | Claim | Old → New | Source |
|---|---|---|---|
| 1 | Azure `ND96isr_H100_v5` PAYG, eastus (§3.3, §5.3) | **$102.736/inst-hr → $98.320**; $12.842 → **$12.290**/GPU-hr | [Azure Retail Prices API, `armRegionName eq 'eastus' and contains(armSkuName,'H100')`](https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Virtual%20Machines%27%20and%20armRegionName%20eq%20%27eastus%27%20and%20contains%28armSkuName%2C%27H100%27%29) |
| 2 | Azure `ND96is_H100_v5` PAYG, eastus (§3.3, §5.3) | **$92.904/inst-hr → $88.488**; $11.613 → **$11.061**/GPU-hr | [same API query](https://prices.azure.com/api/retail/prices) — both old figures were exactly $4.416/hr high, so a second meter was almost certainly summed in |
| 3 | GB300 per-GPU power (§2.1, §8.1) | **1,400 W → 1,100 W** | [Lenovo GB300 NVL72](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) — the page the document already cited says verbatim "1100W total graphics power per GPU". The 1,400 W figure was attributed to a source that does not contain it. |
| 4 | §10.3 MI300X cheapest-provider $/PFLOP-hr FP8 (§10.3, §10.4) | **$0.916 → $0.992** | Arithmetic: $2.59 ÷ 2.610 PFLOPS = $0.9923. The hyperscaler cell ($6.00 ÷ 2.610 = $2.299) was right, so only the `chp` column was wrong. MI300X still ranks first. |
| 5 | §6 node price, 8× A100 80GB | **$10.80 labelled "Hyperstack SXM $1.60×8" → $12.80** ($10.80 is the PCIe $1.35×8 figure; label and value disagreed) | [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) |

### UNVERIFIABLE

| # | Claim | Why |
|---|---|---|
| 6 | Entire §3.2 GCP table (A2/A3/A4/G4 on-demand, Spot, DWS, CUD) | [cloud.google.com price page](https://cloud.google.com/products/compute/pricing/accelerator-optimized) truncated before the tables on two fetches; `docs.cloud.google.com/compute/docs/gpus/gpu-pricing` returns HTTP 404. Flagged inline in §3.2. |

Also unreachable, now flagged inline rather than silently trusted: **all of
§2.2 (AMD MI300X / MI325X / MI355X specs)** — `amd.com` timed out on four
separate attempts; and **Azure's H200 / GB200 / MI300X PAYG rows**, which were
not re-queried and sit next to two H100 rows that proved wrong.

### CONFIRMED

| # | Claim | Source opened |
|---|---|---|
| 7 | H100 SXM 80 GB, 3.35 TB/s, 1,979 TFLOPS BF16 **with sparsity** → 989.5 dense; 3,958 FP8 sparse → 1,979 dense; up to 700 W | [NVIDIA H100](https://www.nvidia.com/en-us/data-center/h100/) — "* With sparsity" footnote present, so the document's ÷2 is correct |
| 8 | H100 NVL 94 GB, 3.9 TB/s, 1,671 sparse → 835.5 dense BF16, 3,341 → 1,670.5 dense FP8, 350–400 W | [NVIDIA H100](https://www.nvidia.com/en-us/data-center/h100/) |
| 9 | H200 SXM 141 GB, 4.8 TB/s, 989.5 dense BF16, 1,979 dense FP8, up to 700 W; H200 NVL 835.5 / 1,670.5, up to 600 W | [NVIDIA H200](https://www.nvidia.com/en-us/data-center/h200/) |
| 10 | A100 80 GB SXM 2,039 GB/s / 400 W; A100 80 GB PCIe 1,935 GB/s / 300 W; 312 TFLOPS dense BF16 (624 with sparsity) | [NVIDIA A100](https://www.nvidia.com/en-us/data-center/a100/) — note the page no longer carries the **40 GB** rows the §2.1 table cites it for; those numbers come from the A100 datasheet PDF |
| 11 | HGX B200: 1.4 TB ÷ 8 = **180 GB/GPU**; FP4 "144 PFLOPS \| 72 PFLOPS" sparse\|dense ÷8 = **9,000 dense**; FP8/FP6 72 PFLOPS sparse ÷8÷2 = 4,500 dense | [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) |
| 12 | HGX B300: 2.1 TB ÷ 8 = **262.5 GB/GPU**; FP4 "144 \| 108 PFLOPS" ÷8 = **13,500 dense** | [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) |
| 13 | GB200 NVL72: 13.4 TB ÷ 72 = **186 GB/GPU**; NVFP4 "1,440 \| 720 PFLOPS" ÷72 = **10,000 dense**; FP8 360 dense ÷72 = 5,000 | [NVIDIA GB200 NVL72](https://www.nvidia.com/en-us/data-center/gb200-nvl72/) — the page's "372 GB HBM3E" is per *Superchip* (2 GPUs), not per GPU |
| 14 | GB300 dense FP4 15,000/GPU: DGX GB300 "1440 PFLOPS \| 1080 PFLOPS" sparse\|dense ÷72 = 15 | [NVIDIA DGX GB300](https://www.nvidia.com/en-us/data-center/dgx-gb300/) — also confirms the §2.1 warning that the sparse:dense ratio is 1.33, not 2.0 |
| 15 | GB300 NVL72 rack: **135 kW TDP, up to 155 kW peak (EDPp), ~90% liquid / 10% air, water inlet up to 45 °C**, 288 GB HBM3e/GPU, 18,000 TFLOPS FP4 *with sparsity* | [Lenovo GB300 NVL72](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) |
| 16 | RTX PRO 6000 Blackwell SE: 96 GB GDDR7, **1,597 GB/s**, 4 PFLOPS FP4 / 2 PFLOPS FP8 / 1 PFLOP FP16, up to 600 W — **and the page carries no sparsity footnote**, exactly as §2.1 claims | [NVIDIA RTX PRO 6000 SE](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) |
| 17 | OCI list prices: B300 **$15.00**, GB300 **$18.00**, B200 **$14.00**, GB200 **$16.00**, H200 **$10.00**, H100T **$10.75**, MI300X **$6.00**, MI355X **$8.60**, RTX PRO 6000 **$4.50**, L40S **$3.50** | [OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD) — part numbers B112237, B112140, B110978, B110979, B110519, B109480, B109485, B111758, B112613, B109479 all match |
| 18 | OCI NVIDIA AI Enterprise surcharges: $2.50 H100, $2.50 H200, $3.50 B200, $4.00 GB200, $1.00 A100 80, $0.76 A100 40 | [same API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD) — B111824/26/27/28/29/30/31 |
| 19 | AWS Capacity Blocks $/accel-hr: p4d $1.475, p4de $2.214, p5 $5.191, p5e $5.97, p5en $6.865 (US) / $6.241 (EU-APAC), p6-b200 $12.355, p6-b300 $14.04, u-p6e-gb200x36 and x72 both $10.582; "prices next updated **October 2026**"; GB200 UltraServers in **US East (Dallas) Local Zone** only | [AWS Capacity Blocks](https://aws.amazon.com/ec2/capacityblocks/pricing/) — note $6.241/$6.865 is a *regional split*, not a range |
| 20 | AWS `p6-b200.48xlarge` on-demand **$113.933** → $14.242/GPU-hr; spot $42.092 → $5.261; RI 1y/3y **N/A**; 180 GiB/GPU | [Vantage](https://instances.vantage.sh/aws/ec2/p6-b200.48xlarge) |
| 21 | The §"Open questions" #3 claim that Vantage's p5e on-demand is implausible: it shows **$1.8432/hr on-demand against $26.799 spot** | [Vantage](https://instances.vantage.sh/aws/ec2/p5e.48xlarge) — confirmed nonsense, correctly flagged |
| 22 | CoreWeave full rate card: GB200 $42.00/4 = $10.50; HGX B200 $68.80 → $8.600 (spot $34.11 → $4.264); HGX H200 $50.44 → $6.305 (spot $20.93 → $2.616); HGX H100 $49.24 → $6.155 (spot $19.71 → $2.464); A100 $21.60 → $2.700 (spot $9.65 → $1.206); RTX PRO 6000 HM $20.00 → $2.500 (spot $11.09 → $1.386), Standard spot $9.56 → $1.195; HGX B300 spot $35.84 → **$4.480**; GB300 and HGX B300 on-demand "Contact sales" | [CoreWeave](https://www.coreweave.com/pricing) — every cell matched. Also confirms **GB300 slice listed at 279 GB/GPU and HGX B300 at 270 GB/GPU** |
| 23 | Lambda: B200 $6.69/$6.79/$6.89/$6.99, H100 SXM $3.99–4.29, H100 PCIe $3.29, A100 80 $2.79, A100 40 $1.99, GH200 $2.29; clusters B200 $9.86/$9.36/$8.87 and H100 $6.16/$5.85/$5.54; **no H200, B300, GB200 or GB300 listed** | [Lambda](https://lambda.ai/pricing) — including the counter-intuitive cluster-above-instance pricing |
| 24 | Hyperstack: B300 $7.40 (reserved not listed, "Q4"), B200 $6.00/$5.10 listed at **192 GB**, H200 $3.99/$2.79, H100 SXM $3.20/$2.72, H100 NVLink $2.60/$1.82, H100 PCIe $2.50/$1.75, RTX PRO 6000 SE $1.85/$1.30, A100 $1.60/$1.40/$1.35 | [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) |
| 25 | Nebius: B300 $7.85/$4.30, B200 $7.15/$3.95, H200 $4.50/$2.45, H100 $3.85/$2.15, RTX PRO 6000 $1.80/$0.95; GB200/GB300 contact sales | [Nebius](https://nebius.com/prices) |
| 26 | RunPod: page dated **September 13, 2026**; B300 $7.89/$6.94 at **288 GB**, B200 $6.79/$5.98 at 180 GB, H200 $4.59/$3.59, H100 SXM $3.49/$2.69, H100 NVL $3.19/$2.59, H100 PCIe $2.89/$1.99, A100 $1.59, RTX PRO 6000 $2.09/$1.69; **no MI300X/MI325X/MI355X** | [RunPod](https://www.runpod.io/pricing) |
| 27 | DigitalOcean: effective **August 1, 2026**; HGX B300 reserved-only $7.94, H200 $4.47/$3.40, H100 $4.41/$3.26, MI350X reserved-only $4.76, MI325X $3.80/$2.88, MI300X $2.59/$1.91 | [DigitalOcean](https://www.digitalocean.com/pricing/gpu-droplets) |
| 28 | All of §9.2, §9.3 and §10.1–10.2 arithmetic, plus every `$/inst-hr ÷ GPUs` division in §3 and §5, and the §9.1 formula's agreement with METHODOLOGY §6 | Recomputed in `python3`. Every figure reproduces to the last published digit except the five listed under CORRECTED. |

### Cross-document notes

- **METHODOLOGY §1–§5 has no counterpart in this document.** This file supplies
  only the `price_per_gpu_hour` input to METHODOLOGY §6. Parameter counts, KV
  bytes/token, per-sequence state and the `cost_per_1M_tokens` derivations they
  feed live in `research/models/*` and `research/gpus/*`; none of them could be
  recomputed here because this document contains no model configs. The
  `research/matrix/` directory is **empty** — the per-(model, GPU) cost tables
  METHODOLOGY §6 asks for do not exist yet anywhere in the repo.
  **This is not a gap in this document.** Per-(model, GPU) fit / throughput / cost
  tables belong in **`research/models/<exp>/<gpu>.md`** (e.g.
  `research/models/deepseek41f/b300.md`) and are written in the next phase;
  cross-cutting and GPU docs cite §5.14 for price and stop there. See §12.
- **HGX B300's INT8 rate is not in §2.** The NVIDIA HGX page gives HGX B300 **3
  POPS INT8** against HGX B200's **72 POPS** — a ~24× cut. That is a first-order
  fact for any INT8/W8A8 quantization plan on B300 and belongs in
  `research/cross-cutting/quantization-formats.md`. ⚠️ TO BE VERIFIED (single
  source, and the magnitude is surprising enough to want a second one).

---

## Sweep log (2026-09-19)

Systemic correction pass against the amended `research/METHODOLOGY.md` (§1 units,
§8 pinned GPU capacities and dense FLOPS, §6 cached-token rule and scenarios
S1–S4). Recomputations were run in `python3`; every external source below was
re-fetched in this pass. Nothing corrected by the earlier fact-checker was undone.

### Capacities — as deployed (METHODOLOGY §8)

| Section | Old → New | Reason | Source |
|---|---|---|---|
| §2.1 B300 row | HBM `262.5–288 ⚠️` → **`268` (2,144 GB per 8-GPU node)**; row retitled "HGX B300 / DGX B300 / AWS p6-b300" | METHODOLOGY §8 pins the as-deployed capacity; the doc was carrying an unresolved three-way spread | [METHODOLOGY §8](../METHODOLOGY.md), [gpus/b300.md](../gpus/b300.md), [AWS p6-b300 launch blog](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) ("2144GB HBM3e") |
| §2.1 B300 memory block | "**Plan HGX B300 at 270 GB and GB300 NVL72 at 288 GB**" → "**Plan HGX / DGX / p6-b300 at 268 GB = 2,144 GB per 8-GPU node**"; `⚠️ TO BE VERIFIED` heading dropped; source table gains the AWS-blog row and the pinned row | Dispute is settled upstream, not open | as above |
| §2.1 GB300 block | "the consistent planning number for GB300 is **~279 GB, not 288 GB**" → **288 GB nameplate, ≈ 279 GB usable**, with an explicit "HGX B300 and GB300 NVL72 figures are never merged" | METHODOLOGY §8 pins 288 (≈279 usable); the two parts must not be conflated | [METHODOLOGY §8](../METHODOLOGY.md), [gpus/gb300.md](../gpus/gb300.md) |
| §2.1 GB300 row | `288 ⚠️` → `288 (≈ 279 usable)` | ⚠️ no longer warranted | as above |
| §3.3 Azure GB200 row + note | "GB200 (B200 **192 GiB**)" → "(Azure prints '192 GiB', see note)" + new note pinning **186 GB/GPU** | GB/GiB slip in Azure's own docs: 192 GiB = 206 GB; NVIDIA gives 13.4 TB ÷ 72 = 186 GB | [NVIDIA GB200 NVL72](https://www.nvidia.com/en-us/data-center/gb200-nvl72/), [MS Learn ND GB200-v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series) |
| §4.1 CoreWeave VRAM column | `279` / `270` → `279 ‡` / `270 ‡` + footnote | Vendor-listed slice ≠ planning capacity | [CoreWeave](https://www.coreweave.com/pricing) re-fetched |
| §4.4 RunPod B300 | `288 GB` → `288 GB (nameplate; plan 268)` | as-deployed rule | METHODOLOGY §8 |
| §4.5 Hyperstack B300 / footnote | `288 GB` → `288 GB* (nameplate; plan 268)`; footnote now pins **B200 180 / B300 268** | as-deployed rule | METHODOLOGY §8 |
| §4.6 Together B300 / GB300 | `270 GB` → `270 GB (vendor-listed; plan 268)`; `288 GB` → `288 GB (≈ 279 usable)` | as-deployed rule; no HGX/NVL72 merging | METHODOLOGY §8 |
| §4.8 DigitalOcean B300 | `270–288 GB` → `270–288 GB listed (plan **268**)` | as-deployed rule | METHODOLOGY §8 |
| §4.10 Scaleway | "`B300-SXM-8-288G` (288 GB/GPU — a data point for the §2 memory dispute)" → shape name is the die nameplate; plan 268 | dispute resolved; shape names are not capacity evidence | METHODOLOGY §8 |
| §10.1 B300 HGX row | `270 GB` → **`268 GB`**; chp `$0.0274` → **`$0.0276`**; hyp `$0.0556` → **`$0.0560`** | recomputed on the as-deployed capacity | `python3`: 7.40/268, 15.00/268 |
| §10.1 closing note | "the B300 HGX row's 270 GB is likewise the usable rather than nameplate figure — the two rows are not on the same basis" → each row is on its own pinned basis; HGX B300 and GB300 NVL72 are different parts | removes a false equivalence | METHODOLOGY §8 |
| §10.4 B300 `$/GB-hr` | `$0.0274` → **`$0.0276`** | follows §10.1 | `python3` |
| §10.4 GB300 "Best at" | "288 GB/GPU" → "288 GB/GPU (≈ 279 usable)" | consistency with §2.1 | METHODOLOGY §8 |

### Dense FLOPS — never sparse silently (METHODOLOGY §8)

| Section | Old → New | Reason | Source |
|---|---|---|---|
| §2.1 RTX PRO 6000 SE row | dense BF16 / FP8 / FP4 `1,000 ⚠️ / 2,000 ⚠️ / 4,000 ⚠️` → **`480 / 960 / 1,920 (≈ 2,000)` `est.`** | The row was printing NVIDIA's **sparse** headline figures in dense columns, contradicting §10.3 of the same document, which already divided by 2 | SE page re-fetched 2026-09-19 — prints 4 / 2 / 1 PFLOPS and contains **zero occurrences of "sparsit"**; [gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md) reconciles them to sparse against the SM FLOP/clk rate; METHODOLOGY §8 "FP4 ≈ 2,000 dense (4,000 is sparse)" |
| §2.1 GB300 row | dense BF16 `2,500 ⚠️`, FP8 `5,000 ⚠️` → **`2,500` / `5,000`** | pinned values; ⚠️ no longer warranted | [METHODOLOGY §8](../METHODOLOGY.md), [gpus/gb300.md](../gpus/gb300.md) |
| §2.1 GB300 sparse-FP4 block | `⚠️ TO BE VERIFIED — GB300 sparse FP4` → retitled "dense only, as always"; dense 15,000/GPU stated as pinned | only the *sparse* side is unresolved; the dense figure is consistent across NVIDIA and Lenovo | [DGX GB300](https://www.nvidia.com/en-us/data-center/dgx-gb300/), [Lenovo](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) |
| §2.2 MI355X FP4 cell | `10,100 (MXFP4)` → `10,100 (MXFP4 **and** MXFP6)` | both rates are 10.1 PFLOPs on the product page | [AMD MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html) |
| §10.3 RTX PRO 6000 SE row | FP8 `1,000 ⚠️` → **`960`**; chp `$1.800 ⚠️` → **`$1.875`**; hyp `$4.143 ⚠️` → **`$4.316`**; FP4 `2,000 ⚠️` → **`1,920`**; chp FP4 `$0.900 ⚠️` → **`$0.938`** | recomputed on the dense values from gpus/rtx6000-pro.md §3c | `python3`: 1.80/0.960, 4.143/0.960, 1.80/1.920 |
| §10.3 GB300 FP8 | `5,000 ⚠️`, `$3.600 ⚠️` → `5,000`, `$3.600` | pinned | METHODOLOGY §8 |
| §10.3 closing note | "If they are already dense, that row's $/PFLOP-hr halves and RTX PRO 6000 becomes the cheapest FP4 FLOP on the market" → **removed**; replaced with the resolved finding that it is *not* the cheapest FP4 FLOP ($0.938 vs B300 $0.548, B200 $0.667, MI355X $0.851) | the speculation is now settled and was pointing the wrong way | as above |
| §10.4 RTX PRO 6000 row | `$1.800 ⚠️` → **`$1.875`**; `$0.900 ⚠️` → **`$0.938`** | follows §10.3 | `python3` |
| §10.4 GB300 FP8 | `$3.600 ⚠️` → `$3.600` | pinned | METHODOLOGY §8 |
| §10 preamble | — → added an explicit capacity-and-FLOPS basis paragraph (B200 180, B300 268, GB300 288; dense from METHODOLOGY §8) | makes the normalisation basis auditable | METHODOLOGY §8 |

### AMD specs — §2.2 re-pull (document-specific finding)

| Section | Old → New | Reason | Source |
|---|---|---|---|
| §2.2 body | "⚠️ TO BE VERIFIED — `amd.com` timed out on four separate fetch attempts … **no row in §2.2 was re-confirmed against a primary AMD page in this pass** … Re-pull the MI300X, MI325X and MI355X product pages before using §2.2 anywhere" → **all three pages re-fetched HTTP 200**, with a verbatim confirmation table added | `curl -A "Mozilla/5.0 …" --max-time 60` succeeded where the plain fetch had timed out | [MI300X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html), [MI325X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html), [MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html) |
| §2.2 body | "the 'no sparsity row published' claim for MXFP4 is specifically suspect … an FP4 20.1 PFLOPs sparse row probably does exist" → **withdrawn; the claim is confirmed** — the MI355X page carries `… with Structured Sparsity` rows for FP16, OCP-FP8 and INT8 but **none** for MXFP4 or MXFP6 | speculation contradicted by the primary page | [AMD MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html) |
| §2.2 rows | all three rows gain ✅ re-confirmed 2026-09-19 | every value (192/256/288 GB, 5.3/6/8 TB/s, 1,300/1,300/2,500 BF16, 2,610/2,610/5,000 FP8, 10,100 MXFP4, 750/1,000/1,400 W) reproduced verbatim | as above |
| Open questions #29 | — → notes that the AMD **spec** gap is closed; only the AMD **purchase-price** gap remains | keeps the open-questions list honest | — |

### Prices (METHODOLOGY §8: cloud-pricing.md rows only)

| Section | Old → New | Reason | Source |
|---|---|---|---|
| §5.14 | — → **NEW: "Planning prices — the three rows every other doc cites"**, a named `low` / `high` / `res1y` row per GPU slug, plus usage rules and the `high ÷ low` spread | the pair docs need one citable price per GPU instead of re-deriving from §3–§5 and drifting | §3–§5 of this document |
| §3.4 | — → added an OCI API re-pull block stating verbatim that **GB300 = $18.00 (not $7.40, which is Hyperstack's HGX B300 rate)** and **MI355X = $8.60 (not $3.45, which is Crusoe's MI300X rate)**, plus B300 $15.00 | these are the three prices sibling docs get wrong; naming the confusions inline stops the copy | [OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD) re-pulled, HTTP 200 — part numbers B112140, B111758, B112237 all match |
| §3.1 | — → added an AWS re-pull block listing all nine P/G on-demand prices from both us-east-1 and us-west-2 | primary-source confirmation of the most load-bearing price table | [AWS price sheets](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json), HTTP 200 |
| §3.1 note | "present in us-west-2 and us-east-1 but not us-east-2" → "(both at $142.416)" added | the two-region equality is now verified, not asserted | as above |
| §4.1 | — → added CoreWeave's **non-NA HGX B300 spot rate $36.70/node-hr = $4.588/GPU-hr**, distinct from the NA $35.84 = $4.480 | the page carries two regional spot rates; only one was recorded | [CoreWeave](https://www.coreweave.com/pricing) re-fetched |
| §4.5 | — → Hyperstack now lists **GB200 NVL72 and GB300 NVL72** in its lineup with no published price (contact-sales) | new lineup entries since the original pull; relevant to the GB300 price-scarcity claim in §5.9 and §11 | [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) re-fetched |
| §12 | "Cheapest reputable on-demand (§10.4 column 2)" / "Hyperscaler on-demand (§5)" / "Committed 1-yr (§5)" → **`planning price · <slug> · low` / `· high` / `· res1y`** | binds the cost model to the named §5.14 rows | §5.14 |
| §12 | — → added vendor API sanity anchors, with **DeepSeek-V4-Flash at $0.14/M input** (and an explicit note that a $0.15 figure appearing in some drafts is wrong), $0.0030 cached, $0.60 output | METHODOLOGY §6 asks for an API cross-check; the price must come from the sourced table | [serving-optimizations.md §1.5](serving-optimizations.md) citing [DeepSeek pricing](https://deepseek.ai/pricing) |

### Bandwidth, units, scope, citations

| Section | Old → New | Reason | Source |
|---|---|---|---|
| §10.2 note | — → added: **1,597 GB/s is the Server Edition**; the 1,792 GB/s figure is the **Workstation Edition**, which no provider in §5.10 rents; substituting it overstates decode by 12 % and understates $/(TB/s)-hr to $1.004 | a sibling doc cited an NVIDIA model card for an RTX PRO 6000 bandwidth figure the card does not contain; this pins the right number and the right page | [RTX PRO 6000 SE page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) re-fetched — "Memory Bandwidth 1597 GB/s" verbatim |
| §12 | — → added the **cached-input rule** (10 % of uncached prefill for own serving; vendor's own ratio for API comparisons — DeepSeek ≈ 2.1 %, Anthropic ≈ 2.5 %, Alibaba 10–25 %) and the **S1–S4 + blended** scenario definitions | METHODOLOGY §6 was amended with both | [METHODOLOGY §6](../METHODOLOGY.md), [serving-optimizations.md §1.5](serving-optimizations.md) |
| §12 | — → added "Where the per-(model, GPU) numbers live": `research/models/<exp>/<gpu>.md`, written in the next phase; not added here or to `research/gpus/*.md` | scope rule — no per-(model, GPU) fit/throughput/cost tables in cross-cutting or GPU docs | — |
| Verification log → Cross-document notes | "the per-(model, GPU) cost tables METHODOLOGY §6 asks for do not exist yet anywhere in the repo" → same, plus **"This is not a gap in this document"** and a one-line pointer to `research/models/<exp>/<gpu>.md` | the log flagged a missing section that belongs elsewhere | — |
| Open questions #18 | open → **RESOLVED** (B300 = 268 GB as deployed) | settled by METHODOLOGY §8 | [gpus/b300.md](../gpus/b300.md) |
| Open questions #23 | open → **RESOLVED** (GB300 dense FP8 = 5,000/GPU pinned) | settled by METHODOLOGY §8 | [gpus/gb300.md](../gpus/gb300.md) |
| Open questions #24 | "Three numbers, unresolved" → narrowed: only the **sparse** side is unresolved; dense 15,000/GPU is consistent | dense is what §10.3 uses | as above |
| Open questions #25 | open → **RESOLVED** (SE figures are sparse; dense 480 / 960 / 1,920) — and notes the $/PFLOP-hr *rose* rather than halved, because §2.1 had been carrying sparse figures in dense columns | settled by the re-fetch + gpus/rtx6000-pro.md §3c | as above |
| Open questions #25b | — → **NEW**: Server Edition 1,597 GB/s vs Workstation 1,792 GB/s, with the warning about the miscited model card | prevents the wrong-SKU bandwidth from spreading further | as above |
| Sources | — → added the AWS p6-b300 launch blog (2,144 GB / 268 GB per GPU) | new primary citation introduced in §2.1 | [AWS blog](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) |

### Checklist items that did not apply to this document

KV-cache arithmetic and per-sequence state (§5 of the checklist), weight-byte and
checkpoint reconciliation (§4), batch-feasibility rewrites (§6), the B200-vs-H200
DeepSeek-R1 gap (§9), TokenSpeed's DeepSeek-V4.1-Flash recipe (§10), the DSpark
3.51 acceptance-length label (§11), engine release versions (§12) and the
DeepSeek-V4.1-Flash MXFP4-vs-NVFP4 expert format (§14) have no counterpart here —
this document contains no model configs, no throughput tables and no engine
versions. They are handled in `research/models/*`,
`research/cross-cutting/inference-engines.md`,
`research/cross-cutting/quantization-formats.md` and `research/gpus/*`.

### Citation integrity — the five most load-bearing citations, re-fetched

| # | Citation | Claim it carries | Verdict |
|---|---|---|---|
| 1 | [OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD) | Every OCI row in §3.4, §5.6–§5.13, §6, §10 — and the only published GB300 ($18) and MI355X ($8.60) prices in the survey | ✅ **CONFIRMED** — HTTP 200; all 13 GPU part numbers and prices reproduce exactly |
| 2 | [AWS on-demand price sheets](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json) | Every §3.1 on-demand cell and the `high` column of §5.14 for A100/H100/H200/RTX | ✅ **CONFIRMED** — HTTP 200 on both us-east-1 and us-west-2; all nine instances match to the cent; `p5e` genuinely absent |
| 3 | [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) | B200 180 GB / 9,000 dense FP4; B300 2.1 TB / 13,500 dense FP4; the B300 INT8 cliff | ✅ **CONFIRMED** — verbatim "144 PFLOPS \| 108 PFLOPS" (B300 FP4 sparse\|dense), "144 \| 72" (B200), Total Memory "2.1 TB" / "1.4 TB", INT8 "3 POPS" (B300) vs "72 POPS" (B200) |
| 4 | [CoreWeave pricing](https://www.coreweave.com/pricing) | §4.1 in full, the GB200 `low` ($10.50) and B300 spot ($4.480) in §5.14 / §10 | ✅ **CONFIRMED** — every cell matched; GB300 slice 279 GB, HGX B300 270 GB, HGX B200 180 GB, GB200 186 GB all present verbatim |
| 5 | [NVIDIA RTX PRO 6000 SE](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) | The 1.597 TB/s in §2.1 and §10.2, and the FLOPS the §10.3 row is built on | ✅ **CONFIRMED for bandwidth** ("Memory Bandwidth 1597 GB/s", "Up to 600W (configurable)"). ⚠️ **The page does not resolve sparsity** — it prints 4 / 2 / 1 PFLOPS with no sparsity footnote anywhere (0 occurrences of "sparsit"). The dense reading comes from [gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md) and METHODOLOGY §8, and §2.1 now says so rather than implying the page is the source |

Bonus, from the document-specific finding: all three **AMD** product pages
([MI300X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi300x.html),
[MI325X](https://www.amd.com/en/products/accelerators/instinct/mi300/mi325x.html),
[MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html))
and [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) — ✅ **CONFIRMED**, HTTP 200.

---

**Follow-up (2026-09-19) — §8.1's GB300 correction is now propagated.** §8.1's **1,100 W per
GPU in a GB300 NVL72** (verification row 3 above) disagreed with `research/gpus/gb300.md`
§1.1's 1.4 kW until today. The Lenovo page was re-fetched and again reads verbatim "1100W
total graphics power per GPU" [src](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai); `gpus/gb300.md` §1.1 and `gpus/b300.md` §1 were
corrected to match, and the inline "both docs disagree" notes in `models/deepseek41f/gb300.md`,
`models/kimik3/gb300.md`, `models/deepseek41fnvfp4/gb300.md` and `matrix/pairs.json` were
replaced with references to the resolved figure. **Nothing in this document changed** — §8.1,
§8.2 (135 kW / 155 kW rack) and the §9 on-prem $/GPU-hour model were already correct and are
untouched.
