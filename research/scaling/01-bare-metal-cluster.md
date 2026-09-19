# Bare-metal GPU cluster foundation

> **Scope.** Everything *below* the inference engine: silicon layout, fabric, OS/driver
> stack, orchestrator, weight delivery, telemetry, tenancy, and a costed bill of
> materials. It stops where the engine process starts. Which engine, which
> quantization, which attention kernel, how many GPUs a model needs and what a token
> costs are already answered in this repo and are **linked, never re-derived**:
> [`METHODOLOGY.md`](../METHODOLOGY.md) (formulas + §8 pinned GPU/model inputs),
> [`gpus/b300.md`](../gpus/b300.md), [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md),
> [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md),
> [`matrix/fit-matrix.md`](../matrix/fit-matrix.md), [`matrix/recommendations.md`](../matrix/recommendations.md).
>
> **Research date: 2026-09-19.** Every version below is what was current on that date.
> Infrastructure software in this space moves on a ~3-month cadence; re-pin before you buy.
>
> **Legend** is [`METHODOLOGY.md` §Legend](../METHODOLOGY.md#legend): `[src]` = primary
> source, **⚠️ TO BE VERIFIED** = estimate with the method stated inline, `est.` =
> derived, `meas.` = published measurement.

**The five workloads this cluster exists to serve** (all figures pinned in
[`METHODOLOGY.md` §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)):

| Model | Checkpoint on disk | Min GPUs on B300 | Shape | What it demands of the *cluster* |
|---|---:|---:|---|---|
| [Kimi-K3](../models/kimik3/README.md) | **1,560.9 GB** | 8 | full 8×B300 node, 195 GB/GPU | whole-node gang scheduling; the weight-distribution problem in §5 |
| [DeepSeek-V4.1-Flash](../models/deepseek41f/README.md) | 510.29 GB | 2 | TP4–TP8, EP, DSpark γ=5 | NVLink-local EP; PD-disaggregation over RDMA (§4.6) |
| [DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/README.md) | 527.27 GB | 4 | same | same |
| [Qwen3.8-27B](../models/qwen3827b/README.md) | 30.87 GB FP8 | 1 | single GPU | high replica count; fast cold start; fractional-GPU question (§2.6) |
| [Marlin-2B](../models/marlin2b/README.md) | 5.444 GB BF16 | 1 | single GPU, video VLM | many replicas, bursty; cheapest cold start |

Sum of one copy of each (Qwen at FP8): **2,634.77 GB = 2,453.8 GiB = 2.40 TiB** `est.` — the
number that sets NVMe sizing per node in §5 and §8. *(Corrected 2026-09-19: earlier drafts
printed 2.45 TiB; 2,634.774 × 10⁹ B ÷ 2⁴⁰ = 2.396 TiB.)*

---

## 1. Hardware and topology

### 1.1 The node

This repo's unit of capacity is an **8×B300 HGX node**. The GPU-level numbers are
pinned in [`METHODOLOGY.md` §8](../METHODOLOGY.md#gpus) and detailed in
[`gpus/b300.md`](../gpus/b300.md); repeated here only as far as the *cluster* design
depends on them.

| Property | Value | Source |
|---|---|---|
| GPUs per node | 8 × B300 SXM (`sm_103`) | [`gpus/b300.md`](../gpus/b300.md) |
| HBM per GPU **as deployed** | **268 GB** (2,144 GB/node) | [METHODOLOGY §8](../METHODOLOGY.md#gpus) — pinned |
| HBM bandwidth | 8.0 TB/s per GPU, 64 TB/s per node | ibid. |
| NVLink per GPU | 1.8 TB/s bidirectional; **14.4 TB/s aggregate** per baseboard | [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) via [`gpus/b300.md` §NVLink](../gpus/b300.md) |
| NVLink domain | **8 GPUs** (2 × NVSwitch on the baseboard) | ibid. |
| Host link | PCIe 6.x @ 128 GB/s | [`gpus/b300.md`](../gpus/b300.md) |
| East-west NICs | **8 × ConnectX-8 SuperNIC, up to 800 Gb/s each** | "Eight NVIDIA® ConnectX-8 SuperNICs per NVIDIA HGX B300 baseboard. Up to 800 Gbps per adapter" [src](https://docs.nvidia.com/enterprise-reference-architectures/hgx-ai-factory/latest/components.html) |
| North-south | 1 × BlueField-3 DPU (B3240 recommended for B300) | ibid. |
| CPU | "Minimum of 48 physical CPU cores per socket" | ibid. |
| System memory | "Minimum of 2TB system memory" | ibid. |
| Local NVMe | RA floor: "Minimum 2 TB NVMe drive per CPU socket" (plus a "1 TB NVMe boot drive") | ibid. — **too small for this repo; see §5.4** |
| System memory bandwidth | RA floor: "Minimum of 500GB/s memory bandwidth" | ibid. |
| CPU (recommended) | "Recommendation of 56 physical CPU cores per socket" | ibid. |

> **⚠️ Documented disagreement, stated rather than silently resolved.** NVIDIA's HGX AI
> Factory reference architecture prints "288GB HBM3e" per B300 and "2.30TB HBM3e" per node
> [src](https://docs.nvidia.com/enterprise-reference-architectures/hgx-ai-factory/latest/components.html).
> [METHODOLOGY §8](../METHODOLOGY.md#gpus) pins **268 GB / 2,144 GB** as deployed, from
> AWS p6-b300's reported figure, with NVIDIA's own HGX page at 2.1 TB/node and OCI at
> 263 GB/GPU. The RA figure is the *physical* stack capacity; every sizing number in this
> tree uses the as-deployed pin. Full reconciliation and the unresolved 288→279→268→262.5
> arithmetic: [`gpus/b300.md` §2](../gpus/b300.md).

### 1.2 The NVLink domain is the scheduling atom

Decode on an 8-GPU baseboard is memory-bandwidth bound ([METHODOLOGY §4](../METHODOLOGY.md#4-throughput-and-latency-roofline));
tensor- and expert-parallel collectives ride NVLink at 1.8 TB/s/GPU. The moment a
parallel group spans two baseboards, those collectives drop onto InfiniBand at ~800 Gb/s
= 0.1 TB/s per NIC — a **~18× per-GPU bandwidth cliff** `est.`, which METHODOLOGY's
planning band prices at "20–40 % comm overhead for multi-node TP over InfiniBand"
versus "~5–15 % for TP=8 over NVLink" ([METHODOLOGY §4](../METHODOLOGY.md#4-throughput-and-latency-roofline)).

**Decision rule.** Keep any TP or EP group inside one NVLink domain. Consequences for
this repo:

| Model | Parallel group | Fits one 8-GPU domain? | Scheduling requirement |
|---|---|---|---|
| Kimi-K3 | **TP8 + DCP8**, 195.1 GB/GPU ([`matrix/fit-matrix.md`](../matrix/fit-matrix.md); *corrected 2026-09-19 from "TP8 / EP8" — the B300 recommendation is decode-context-parallel, not expert-parallel*) | exactly | **whole-node gang**, no co-tenant |
| DeepSeek-V4.1-Flash | TP4 or TP8 + EP | yes | 4- or 8-GPU gang, NVLink-local |
| DeepSeek-V4.1-Flash-NVFP4 | TP4–TP8 | yes | same |
| Qwen3.8-27B | TP1 | trivially | 1 GPU, no gang needed |
| Marlin-2B | TP1 | trivially | 1 GPU, no gang needed |

Two of five workloads therefore need **all-or-nothing placement**, which is the whole
argument for gang scheduling in §3.8. A Kimi-K3 replica that gets 6 of 8 GPUs does not
run slower — it does not run, and it holds 6 GPUs hostage while it waits.

### 1.3 Inter-node fabric: InfiniBand vs RoCEv2

| | InfiniBand XDR (Quantum-X800) | RoCEv2 over Ethernet (Spectrum-X) |
|---|---|---|
| Per-port speed | **800 Gb/s per direction** [src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html) | 800 Gb/s (SN5610/SN5600D: "64 port 800 Gbps") [src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html) |
| Switch | Quantum-X800 **Q3400-RA** | Spectrum-4 SN5610 / SN5600D |
| Congestion control | credit-based link-level flow control, native | requires PFC + ECN tuning; lossless Ethernet is a configuration project |
| Routing | adaptive routing, SHARP in-network reduction | adaptive routing (Spectrum-X) |
| Subnet management | needs an SM (UFM: "NVIDIA Unified Fabric Manager 3.5 Appliance, Enterprise Edition") [src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html) | standard Ethernet management |
| Ops familiarity | HPC-specific skillset | reuses existing network team |

**Decision rule for an inference cluster.**

- **≤ 4 nodes and no multi-node model:** neither fabric's advanced features matter. The
  fabric carries PD-disaggregation KV traffic (§4.6) and weight pulls (§5), not TP
  collectives. **RoCEv2 is sufficient and cheaper**, and you already have Ethernet staff.
- **≥ 8 nodes, or any multi-node TP/EP, or a PD-disaggregated deployment with a high
  prefill:decode node ratio:** the tail latency of a badly-tuned PFC domain shows up
  directly as p99 TTFT. **InfiniBand XDR**, because the flow control is not something you
  have to get right yourself.
- **Trade-off you are accepting with InfiniBand:** a second, unfamiliar fabric to
  operate, an SM to keep highly available, and vendor lock to the NVIDIA networking stack.
  With RoCEv2 you are accepting that a PFC misconfiguration anywhere in the domain can
  produce head-of-line blocking that looks, from the serving metrics, like a model
  regression.

None of this repo's five models *requires* multi-node parallelism on B300 — Kimi-K3, the
largest, fits one node ([`matrix/fit-matrix.md`](../matrix/fit-matrix.md)). The fabric is
therefore an **inference-traffic** fabric, not a training fabric, which is why §8's 4-node
BOM offers a RoCE option that the 16-node BOM does not.

### 1.4 Rail-optimized topology

NVIDIA's B300 SuperPOD RA specifies a **"rail-optimized, full-fat tree topology"** on
Quantum-X800 Q3400-RA 800 Gbps InfiniBand switches, with a Scalable Unit of **72 DGX B300
systems** [src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html).
*(Corrected 2026-09-19: earlier drafts quoted "Rail-optimized, non-blocking, twin-plane, fat
tree topology"; that wording is not on the B300-XDR RA components page and was not found on
its architecture page either — the twin-plane phrasing belongs to an earlier RA generation.
Treat "twin-plane" as **⚠️ TO BE VERIFIED** against whichever RA revision you build to.)*

Rail-optimized means: NIC *i* of every node connects to leaf switch *i*. GPU *i* on node A
reaches GPU *i* on node B in one switch hop; reaching GPU *j≠i* costs a spine traversal or
an intra-node NVLink hop first. Collectives that are rank-aligned (ring/tree all-reduce
across the same GPU index) therefore never leave their rail.

This is why `NCCL_CROSS_NIC` exists and why its default is wrong for a rail-optimized
fabric in one direction and right in the other:

> `NCCL_CROSS_NIC` — "0: Always use the same NIC for the same ring/tree, to avoid crossing
> network rails. Suited for networks with per NIC switches (rails), with a slow inter-rail
> connection… 2: (Default) Try to use the same NIC for the same ring/tree, but still allow
> for the use of different NICs if it would result in a better performance."
> [src](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html) (NCCL 2.31.2)

**Decision rule:** on a rail-optimized fabric leave `NCCL_CROSS_NIC=2` (the default lets
NCCL exploit the rails it detects); set `NCCL_CROSS_NIC=0` explicitly only if you have
measured spine oversubscription hurting you. Setting it to 0 on a *non*-rail-optimized
fabric costs you bandwidth for no benefit. **⚠️ TO BE VERIFIED for this repo:** no
measurement of either setting exists here; the rule above is read off the NCCL
documentation's own description, not from a benchmark.

### 1.5 GPUDirect RDMA

GPUDirect RDMA lets a NIC DMA straight into GPU HBM, skipping a bounce buffer in host
memory. For inference it matters in exactly two places: **PD-disaggregated KV transfer**
(§4.6) and **weight loading from a remote filesystem** (§5, via GPUDirect Storage).

Two implementations, and the choice is a real one:

| | DMA-BUF (modern) | `nvidia-peermem` (legacy) |
|---|---|---|
| GPU driver | **open kernel module required** | any supported driver |
| CUDA | **11.7 or higher** | no minimum |
| Linux kernel | **5.12 or higher** | no minimum |
| Network drivers | MLNX_OFED / DOCA-OFED **optional** | **required** |

[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html)

Enabling it via the GPU Operator, quoted from the doc:

```bash
# GPUDirect RDMA with DMA-BUF (nothing extra needed beyond the open module)
helm install --wait --generate-name -n gpu-operator --create-namespace \
  nvidia/gpu-operator --version=v26.7.0

# legacy nvidia-peermem path
#   add: --set driver.rdma.enabled=true
# use host-installed OFED instead of the container's:
#   add: --set driver.rdma.useHostMofed=true
# force the open kernel module on pre-R570 drivers:
#   add: --set driver.kernelModuleType=open
```
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html)

**Decision rule:** on a 2026 greenfield B300 cluster take **DMA-BUF**. Blackwell requires
the open kernel module anyway, and DMA-BUF drops the hard dependency on a matching OFED
build inside the driver container — which is the single most common cause of a GPU
Operator upgrade breaking RDMA.

### 1.6 NCCL topology awareness

NCCL discovers PCIe/NVLink/NIC topology at init. The knobs that matter on this node shape:

| Variable | Documented behaviour / default | When to set it |
|---|---|---|
| `NCCL_IB_HCA` | "Define to filter IB Verbs interfaces… each entry follows the form `<hca>[:<port>[:<rail>[:<plane>]]]`" | Always, in a container: pin the 8 HCAs the pod is allowed to use, so NCCL does not pick a management NIC |
| `NCCL_SOCKET_IFNAME` | "Define to a list of prefixes to filter interfaces to be used by NCCL" | Always in Kubernetes — the bootstrap socket must not land on the CNI overlay if that overlay is slow |
| `NCCL_CROSS_NIC` | default **2** | §1.4 |
| `NCCL_IB_GID_INDEX` | "The default value is -1" | **RoCEv2 only** — you must select the RoCEv2 GID; IB ignores it |
| `NCCL_IB_TC` | "The default value is 0" | **RoCEv2 only** — set to the DSCP-mapped traffic class your PFC config protects |
| `NCCL_NET_GDR_LEVEL` | values `LOC, PIX, PXB, PHB, SYS` (or legacy 0–4) | Raise if GPUDirect RDMA is silently disabled because the NIC is further from the GPU than the default cutoff |
| `NCCL_P2P_LEVEL` | "a short string representing the path type… topographical cutoff for using the P2P transport" | Rarely; NVSwitch makes all 8 GPUs `NVL` |
| `NCCL_TOPO_FILE` | "By default, NCCL will load `/var/run/nvidia-topologyd/virtualTopology.xml` if present" | When the container cannot see host PCIe topology and auto-detection picks a bad ring |

All quotes [src](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html) (NCCL
2.31.2, the version that page documents as of 2026-09-19).

The `NCCL_TOPO_FILE` default path is worth noting: it is the hook the GPU Operator's
`nvidia-topologyd` uses to hand a container the host's real topology. If you run a driver
container *without* topologyd, NCCL inside a pod on an overlay CNI can build a ring that
ignores NVSwitch entirely. That failure is silent — it shows up only as ~5–10× slower
collectives. **⚠️ TO BE VERIFIED:** no measurement of the magnitude on this node shape is
published here; the mechanism is documented, the number is not.

### 1.7 NVL72 as a scale-up domain

GB300 NVL72 replaces the 8-GPU NVLink domain with a **72-GPU** one: 130 TB/s total NVLink,
288 GB/GPU physical (≈279 usable), 36 Grace CPUs + 72 Blackwell Ultra GPUs in one liquid-
cooled MGX rack ([`gpus/gb300.md`](../gpus/gb300.md), [`gpus/b300.md` §fabric](../gpus/b300.md)).

What that buys an inference cluster is **EP width**. On 8×B300 an expert-parallel group is
capped at EP8; on NVL72 it is EP72 without leaving NVLink. For a 2.8 T-parameter MoE like
Kimi-K3 or the 552 B-backbone DeepSeek-V4.1-Flash, wide-EP is the lever that raises
per-GPU throughput ([`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) on
DeepEP/EPLB). This repo's own measured comparison:

> "SemiAnalysis InferenceX, DeepSeek-R1, 8K/1K, FP4: at 73 tok/s/user, GB300 NVL72 posts…"
> — [`gpus/b300.md` §fabric](../gpus/b300.md), which also records vLLM's GB300 DeepSeek
> result [src](https://vllm.ai/blog/2026-02-13-gb300-deepseek) and concludes "the NVL72
> advantage is [conditional]". Read that section before assuming NVL72 wins; it does not
> win uniformly, and [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) prices GB300 off a
> **single published price** (OCI $18/GPU-hr), which is open question 3 in
> [`README.md`](../README.md).

**Decision rule.** For *this repo's* five models on B300: NVL72 is not required — every
model fits an 8-GPU domain. Buy NVL72 when (a) you are running a model whose EP width
genuinely exceeds 8 and whose experts are bandwidth-starved at EP8, **and** (b) you can
take 135 kW-class liquid-cooled racks (§1.9). Otherwise HGX B300 is the cheaper, air-
coolable, more widely-priced unit.

### 1.8 Storage tiers

Four tiers, each with one job. Getting the job assignments wrong is the most common
architectural mistake in a bare-metal inference cluster.

| Tier | Medium | Job | Sizing rule for this repo |
|---|---|---|---|
| **T0 — HBM** | 2,144 GB/node | weights + KV | [METHODOLOGY §3](../METHODOLOGY.md#3-fit) |
| **T1 — host DRAM** | ≥ 2 TB/node | page cache for weights, CPU KV offload tier | ≥ 1× largest checkpoint if you want warm restarts (Kimi-K3 alone = 1.56 TB, see §5.5) |
| **T2 — local NVMe** | RAID0 | **the only tier a model load should ever read from** | ≥ 2.40 TiB for all five + headroom → §8 specifies 4 × 7.68 TB |
| **T3 — shared/parallel FS or object store** | Lustre / Weka / VAST / GPFS, or S3 | **source of truth**, fills T2 | sized for all model versions you keep |

**T3 → T2 → T0, never T3 → T0 at serve time.** A model load that reads from a parallel
filesystem puts a multi-hundred-GB random-read storm on a shared resource at exactly the
moment an autoscaler is trying to add capacity — and it does so from every replica at
once. §5 is entirely about making that not happen.

**Parallel filesystem choice**, when you need T3 to be a filesystem rather than an object
store:

| | Lustre | WekaFS | VAST | IBM Storage Scale (GPFS) |
|---|---|---|---|---|
| GDS support | yes — listed as a GDS-supported distributed FS | yes (WekaFS listed) | yes (VAST-NFS listed) | ⚠️ not named on the GDS overview page's list |
| Typical fit | HPC shops that already run it | lowest-latency metadata, NVMe-native | NFS-over-RDMA, simple ops | existing IBM estates |

GDS-supported filesystem list quoted: "Local storage: NVMe (with or without DOCA SNAP);
Distributed filesystems: Lustre, NFS (NFSoRDMA), WekaFS, DDN-EXAScaler, VAST-NFS; Standard
Linux filesystems: EXT4, XFS" [src](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html).

**GPUDirect Storage** itself: "enables a direct data path for direct memory access (DMA)
transfers between GPU memory and storage, which avoids a bounce buffer through the CPU",
via the `cuFile` API whose calls "closely mimic POSIX `pread` and `pwrite`"; verify with
`gdscheck -p`; NVMe no longer needs `nvidia-fs.ko` as of CUDA 12.8
[src](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html). NVIDIA
explicitly does **not** publish a quantified bandwidth improvement and notes benefits are
"most apparent with small transfers" and platform-dependent — so treat GDS as an enabler
for the loaders in §5, not as a number you can put in a plan.

**Object store as source of truth.** S3 (or MinIO/Ceph RGW on-prem) holds the canonical,
content-addressed, immutable copy of every checkpoint. It is the only tier where
versioning, retention and provenance (§7.4) are cheap. Both loaders in §5 read S3 natively.

### 1.9 Power and cooling

| | HGX/DGX B300 (air) | GB300 NVL72 (liquid) |
|---|---|---|
| Per-GPU power, **planning basis** | **1,100 W** | **1,100 W** |
| Node/rack power | ~14 kW per DGX B300 in 10U | 135 kW rack TDP; **"up to 155 kW peak depending on workload and EDP behavior"** [src](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) |
| Rack density | "up to four DGX B300 systems per rack" | 72 GPUs, 1 rack |
| Cooling | air (with rear-door heat exchanger at density) | direct-to-chip liquid, required |

Per-GPU 1,100 W and the withdrawal of the widely-circulated 1,400 W figure are resolved in
[`gpus/b300.md` §1](../gpus/b300.md): Lenovo's GB300 NVL72 product guide states
**"Total Graphics Power (TGP): 1100W"** per B300 GPU
[src](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai)
*(verbatim wording corrected 2026-09-19; earlier drafts rendered it as "1100W total graphics
power per GPU")*, and 72 × 1.1 kW = 79.2 kW is the only GPU-only draw consistent with the
published 135 kW rack TDP (155 kW peak). The DGX B300 SuperPOD RA's "up to four DGX B300 systems per rack"
[src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html)
is the density constraint that follows.

**Arithmetic for §8's BOMs** (`est.`, from the pinned 1.1 kW/GPU plus the ~14 kW/node
system figure in [`gpus/b300.md` §1](../gpus/b300.md)):

| Cluster | Compute nodes | GPU draw | Node draw incl. CPU/NIC/fans | Racks at 4 nodes/rack |
|---|---:|---:|---:|---:|
| 4-node | 4 | 35.2 kW | ~56 kW | 1 (needs ≥ 60 kW rack) |
| 16-node | 16 | 140.8 kW | ~224 kW | 4 (≥ 60 kW each) |

**Power capping.** `nvidia-smi -pl <watts>` sets a board power limit; it is the blunt
instrument for staying inside a rack budget. The trade-off is direct and measurable: decode
is memory-bandwidth bound ([METHODOLOGY §4](../METHODOLOGY.md#4-throughput-and-latency-roofline)),
so capping power throttles SM clocks first and HBM clocks later — meaning a modest cap
costs prefill (compute-bound) more than decode. **⚠️ TO BE VERIFIED:** no measurement of
the B300 power-vs-throughput curve exists in this tree or in any source found. Method if
you need it: sweep `-pl` from 1,100 W down in 100 W steps and measure decode tok/s and
prefill tok/s separately; do not assume the two degrade together.

---

## 2. OS and driver stack

### 2.1 Pinned stack, 2026-09-19

| Layer | Pin | Why this one | Source |
|---|---|---|---|
| Host OS | **Ubuntu 24.04 LTS** | supported by GPU Operator 26.7 (26.04/24.04/22.04), Network Operator 26.7 (same list), and AMD GPU Operator (22.04/24.04) — the only OS on all three lists | [GPU Op platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html), [Net Op platform support](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html) |
| Kernel | ≥ 5.12 (DMA-BUF), ≥ 5.15 recommended (io_uring for InstantTensor) | §1.5, §5.3 | [GPU Op RDMA](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html), [InstantTensor README](https://github.com/scitix/InstantTensor) |
| NVIDIA driver | **595.91.07** | the GPU Operator v26.7.0 *default/recommended* — deviating means you own the compatibility matrix | [GPU Op platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) |
| CUDA | **13.x** | what everything ships against in 2026; R580+ supports "CUDA Toolkit 13: 13.x" | [R580 release notes](https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-178-04/index.html), [`gpus/b300.md`](../gpus/b300.md) |
| Fabric Manager | must match driver exactly | §2.3 | [FM user guide](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html) |
| Container runtime | **containerd 2.0–2.3** | GPU Operator 26.7 supported range | [GPU Op platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) |
| NVIDIA Container Toolkit | **1.20.0** | bundled with GPU Operator 26.7.0 | ibid. |
| NCCL | 2.31.x | the version NVIDIA's current env-var docs describe | [NCCL env vars](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html) |
| DOCA-OFED | `doca3.5.0-26.07-0.7.7.0-0` (GA) or `doca3.2.2-25.10-2.4.1.0-4` (LTS) | Network Operator 26.7.0's supported pair | [Net Op platform support](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html) |

**Driver branch landscape on 2026-09-19** (from the NVIDIA data-center driver docs index
[src](https://docs.nvidia.com/datacenter/tesla/)): R615 (615.71.09), R610 (610.57.04),
**R595 (595.91.07)**, R590 (590.48.01), R580 (580.178.04, Linux release date 08/03/2026),
R575 (575.57.08), R570 (570.211.01), R565, R560, R550 (550.163.01), R535 (535.309.01) and
older LTSB branches. *(R590 added 2026-09-19 — it sits between R595 and R580 and was missing
from earlier drafts; the GPU Operator's "also supported" list is 610.57.04 / 580.173.02 /
535.309.01 alongside the 595.91.07 default.)*

**Decision rule on driver version:** take the GPU Operator's default (595.91.07), not the
newest branch (R615). The Operator's default is the one its DCGM, device-plugin, toolkit
and MIG-manager images were built and tested against. Move to a newer branch only to fix a
specific Xid you have reproduced, and then pin the whole Operator chart to a release that
lists it. Note R580 is the floor for a different reason: the **NVIDIA DRA driver requires
"NVIDIA GPU driver version 580 or later"** [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html).

R580.178.04's release notes also carry a fix worth knowing about on this hardware:
"Fixed a bug that could cause Xid 32 errors and application crashes when scaling via
CUDA_SCALE_LAUNCH_QUEUES (2x or 4x) with CUDA graphs that have a large number of chained
kernel nodes" [src](https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-178-04/index.html)
— CUDA graphs with many chained nodes is exactly what a vLLM/SGLang decode loop captures.

### 2.2 Fabric Manager and NVSwitch

On any NVSwitch baseboard — which every HGX B300 is — **Fabric Manager is not optional**.
It "configures NVSwitch memory fabrics to create unified memory across participating GPUs,
monitoring NVLinks for errors and coordinating GPU initialization", and explicitly supports
"HGX B200/B300 and DGX B200/B300 (B200/B300 GPUs, fourth-generation NVSwitches)"
[src](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html).

```bash
sudo systemctl enable --now nvidia-fabricmanager
sudo systemctl status  nvidia-fabricmanager
```

Config lives at `/usr/share/nvidia/nvswitch/fabricmanager.cfg`; the fields that matter:

| Setting | Meaning |
|---|---|
| `FABRIC_MODE` | **0 = bare metal** (or full passthrough virtualization), 1 = shared NVSwitch multi-tenancy, 2 = vGPU multi-tenancy |
| `LOG_LEVEL` | 0 = disabled, 1 = CRITICAL+, 2 = ERROR+, 3 = WARNING+, **4 = INFO+ (default)** |
| `FM_CMD_PORT_NUMBER` | API port, **default TCP 6666** |

[src](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html)

**Failure mode you must automate around.** The recovery procedure for a fatal SXid is
(1) stop all CUDA applications and GPU-related services, (2) stop the FM service,
(3) `nvidia-smi -r` (without `-i`/`--id`) to reset GPUs, (4) restart FM, (5) resume services
[src](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html).

> **⚠️ Corrected 2026-09-19 — this procedure does not apply to B300.** The same Fabric
> Manager guide scopes the SXid fatal/non-fatal recovery flow to **pre-fourth-generation**
> NVSwitch systems and states that *"NVSwitch Driver SXid fatal and non-fatal based error
> reporting does not apply on DGX B200/B300 and NVIDIA HGX B200/B300 systems"*
> [src](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html).
> HGX B300 uses **fourth-generation NVSwitches** (same page). Earlier drafts of this document
> presented the reset flow, and the "non-fatal errors allow continued operation with
> performance degradation" framing, as the B300 operating model; both are inherited from the
> earlier NVSwitch generations. What replaces it on B300 is **⚠️ TO BE VERIFIED** — NVIDIA's
> guide says the old reporting path does not apply but this pass did not retrieve the
> fourth-generation replacement. Method: read the Fabric Manager guide's fourth-generation
> NVSwitch section in full and confirm against the driver branch's release notes before
> writing any automated FM remediation. §6.3's SXid alerting inherits the same caveat.

Two hard consequences:

1. **The FM version must equal the driver version.** Driver 595.91.07 ⇒ Fabric Manager
   595.91.07. A mismatched FM refuses to start and all 8 GPUs come up without the NVLink
   fabric, which presents as "TP8 is 10× slower than expected", not as an error.
2. **Non-fatal NVSwitch errors need a metric, not a log line.** The DCGM field is
   `DCGM_FI_DEV_NVSWITCH_LINK_NON_FATAL_ERRORS` (id **783**), not the
   `DCGM_FI_DEV_NVSWITCH_NON_FATAL_ERRORS` name earlier drafts used — **corrected
   2026-09-19**, see §6.3. On B300 read it together with the ⚠️ above: NVIDIA says the
   SXid-based reporting path does not apply to fourth-generation NVSwitch.

`nvidia-persistenced` should also be enabled (`systemctl enable --now nvidia-persistenced`)
so the driver stays loaded between processes; without it, every engine restart pays kernel
module initialization, and on an 8-GPU NVSwitch box that is tens of seconds added to every
cold start — which §5 is trying to minimize.

### 2.3 Driver container vs host driver

| | GPU Operator driver container | Driver installed on the host |
|---|---|---|
| Upgrade unit | Helm chart; node reboot avoidable with the Operator's drain flow | OS package + reboot |
| Kernel coupling | Operator builds/loads modules against the running kernel | you own the DKMS dance |
| RDMA | needs `driver.rdma.useHostMofed=true` if OFED is on the host | OFED and GPU driver naturally co-resident |
| Immutable OS (Talos) | Talos uses **system extensions** baked into the node image instead | n/a |
| Failure blast radius | a bad chart version can take out every node at once | per-node, slower, more controllable |

**Decision rule.** For a **homogeneous** fleet of ≤ ~32 identical B300 nodes: install the
driver + Fabric Manager **on the host** via your image pipeline, and run the GPU Operator
with `driver.enabled=false` for everything else (toolkit, device plugin / DRA, DCGM, NFD,
MIG manager). You get the Operator's Kubernetes integration without handing your entire
fleet's kernel-module state to a Helm upgrade. For a **heterogeneous or rapidly-changing**
fleet, or on an immutable OS where you cannot easily bake drivers, take the driver
container.

The trade-off you are accepting with the host driver: driver upgrades become an image-roll,
not a `helm upgrade`, so they are slower — which is a feature at 3 a.m. and a nuisance at
2 p.m.

### 2.4 Firmware

Firmware is the layer nobody versions until it bites. On an HGX B300 node the set is:
system BIOS/UEFI, BMC, the HGX baseboard's NVSwitch/PCIe-retimer firmware, GPU VBIOS,
ConnectX-8 firmware, BlueField-3 DPU firmware, and NVMe firmware. **⚠️ TO BE VERIFIED:** no
consolidated NVIDIA-published firmware compatibility matrix for HGX B300 was retrievable on
2026-09-19. Method: take the OEM's own firmware bundle for your chassis as the authority,
pin the bundle version in the node image manifest (§8), and treat `nvidia-smi -q | grep
"VBIOS Version"` plus `mlxfwmanager --query` as the two facts to assert in a node
conformance check before a node joins the pool.

### 2.5 MIG, MPS and time-slicing

Three different ways to put more than one workload on a GPU, and they are not
interchangeable.

| | MIG | MPS | Time-slicing |
|---|---|---|---|
| Memory isolation | **yes**, hardware | no | **no** |
| Fault isolation | **yes**, hardware | no | **no** |
| Granularity | fixed profiles (1g.x, 2g.x…) | per-process share | N replicas of the whole GPU |
| Blackwell B300 | supported | supported | supported |
| Kubernetes | GPU Operator MIG manager; `mig.nvidia.com` DeviceClass under DRA | via device plugin | ConfigMap, below |

The GPU Operator's own words: **"Unlike Multi-Instance GPU (MIG), there is no memory or
fault-isolation between replicas"**, and time-slicing "trades the memory and fault-isolation
that is provided by MIG for the ability to share a GPU by a larger number of users"
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html).

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: time-slicing-config
data:
  any: |-
    version: v1
    flags:
      migStrategy: none
    sharing:
      timeSlicing:
        renameByDefault: false
        failRequestsGreaterThanOne: false
        resources:
          - name: nvidia.com/gpu
            replicas: 4
```
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html)

Two limitations from the same page that matter operationally: "DCGM-Exporter cannot
associate metrics to containers when time-slicing is enabled" (so §6 loses per-tenant
attribution), and "the operator does not monitor ConfigMap changes; manual pod restart is
required for updates".

**Decision rule for this repo: do not use any of the three for the LLM workloads.**

- An LLM server already multiplexes many users inside one process via continuous batching —
  that *is* the sharing mechanism, and it is strictly better than time-slicing because the
  KV cache is shared and the weights are loaded once.
- Two vLLM replicas time-sliced on one B300 each reserve `gpu_memory_utilization` of the
  same 268 GB and both thrash. Even Marlin-2B (5.4 GB weights) wants the *rest* of the HBM
  for KV, not to give half of it away.
- The one defensible case is **Marlin-2B or Qwen3.8-27B in a dev/CI namespace** where many
  small, idle-most-of-the-time endpoints must coexist and nobody is holding an SLO. There,
  MIG (not time-slicing) is the right tool, because a crashed dev workload must not take out
  a neighbour. Cost: MIG profiles fragment the 268 GB into fixed slices you cannot resize
  without draining the GPU.

### 2.6 ROCm equivalents for MI355X

If the fleet includes MI355X ([`gpus/mi355x.md`](../gpus/mi355x.md)), the stack maps like this:

| NVIDIA | AMD |
|---|---|
| NVIDIA driver + Fabric Manager | `amdgpu` kernel driver (no fabric manager equivalent; Infinity Fabric is not externally managed) |
| CUDA | ROCm |
| NCCL | RCCL |
| GPU Operator | **AMD GPU Operator** |
| DCGM / DCGM exporter | AMD Device Metrics Exporter (deployed by the operator) |
| Node Feature Discovery | node labeller (part of the operator) |
| `nvidia-smi` | `amd-smi` / `rocm-smi` |

The AMD GPU Operator "simplifies the deployment and management of AMD Instinct™ and AMD
Radeon™ GPU accelerators within Kubernetes clusters", supports **MI355X** among
MI355X/MI350X/MI350P/MI325X/MI300X/MI250/MI210, supports **Kubernetes 1.29–1.36** on
Ubuntu 22.04/24.04, Debian 12 and SLES 15/16 (OpenShift 4.16–4.22), and deploys "automated
driver installation and management", a "device plugin", "metrics collection and export" and
worker node labelling; prerequisites are Helm v3.2.0+ and kubectl
[src](https://instinct.docs.amd.com/projects/gpu-operator/en/latest/index.html).

**Note the version ceiling:** AMD GPU Operator tops out at **Kubernetes 1.36**, the same
ceiling as the NVIDIA Network Operator (§3.6). A mixed NVIDIA+AMD fleet is therefore pinned
to ≤ 1.36 today — see §3.1's decision.

This repo's MI355X open question (Engram hash-table sharding on ROCm,
[`matrix/fit-matrix.md` §6.9](../matrix/fit-matrix.md)) is a *model* question, not an infra
one, and is unaffected by anything here.

---

## 3. Orchestration choices for bare metal

### 3.1 Kubernetes distribution

| Distro | Install model | Node OS | GPU story | Fit for this cluster |
|---|---|---|---|---|
| **kubeadm** | you build everything | any | you wire GPU Operator yourself | maximum control, maximum toil |
| **RKE2** | single binary, CIS-hardened defaults, embedded etcd | any RPM/deb distro | documented GPU Operator add-on [src](https://docs.rke2.io/add-ons/gpu_operators) | **recommended default for on-prem** |
| **Talos Linux** | immutable, API-driven, no SSH | Talos only | NVIDIA drivers via **system extensions** baked into the node image [src](https://www.talos.dev/v1.7/talos-guides/configuration/nvidia-gpu-proprietary/) | best security posture; hardest to debug a driver at 3 a.m. |
| **k3s** | lightweight | any | works | edge/dev, not an 8×B300 fleet |

**Decision rule.** Take **RKE2** unless you already run Talos. Reasons: it is a supported
production distro with a documented GPU Operator path, it does not force an OS change (so
you keep the Ubuntu 24.04 that every operator in §2.1 lists), and its embedded etcd + HA
control plane is a solved problem on three small nodes. Talos is genuinely better for
supply-chain and immutability (§7.4) and is the right choice if your team is already fluent
in it; the cost is that every driver/firmware question becomes an image-build question, and
you cannot shell into a node to run `dcgmi diag`.

**Version pin: Kubernetes 1.36.x.** Not 1.37, even though 1.37.0 shipped 2026-08-26
[src](https://kubernetes.io/releases/). Reason: the **NVIDIA Network Operator v26.7.0
supports ">=1.32 and <=1.36"** [src](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html)
while the GPU Operator 26.7 supports 1.33–1.37
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html).
The intersection is **1.33–1.36**, and the AMD GPU Operator's 1.29–1.36 does not widen it.
Pick the top of the intersection: **1.36.4** (latest patch, released 2026-08-11, EOL
2027-06-28 [src](https://kubernetes.io/releases/)).

The cost of that pin is real and worth naming: you give up the 1.37 DRA graduations
(§3.7) — DRA extended-resource support going GA and `DeviceTaintRule` going stable — until
Network Operator adds 1.37. Revisit at the next Network Operator release.

### 3.2 Slurm

Slurm's model is a queue of jobs against a node inventory, with first-class gang scheduling
(`--nodes`, `--gres=gpu:8`), topology-aware placement (`topology.conf`), backfill, fair-share
accounting and preemption. It is the default in HPC and training shops.

What it is bad at for **inference**: there is no service abstraction. A Slurm job is a
process with a start and an end, not a replica with a readiness probe behind a load
balancer. Rolling updates, health-based restarts, per-replica autoscaling and request
routing all have to be built.

### 3.3 Hybrid: Slurm on Kubernetes

CoreWeave's **SUNK** runs Slurm *as* Kubernetes workloads: "Slurm nodes run as Kubernetes
Pods on CoreWeave Kubernetes Service (CKS), so Slurm jobs and Kubernetes workloads share the
same compute" [src](https://docs.coreweave.com/products/sunk). Components: a `slurm` Helm
chart for control plane, login pods and accounting DB; **NodeSets** declaring groups of
compute nodes (image, resource requests, affinity, replicas); each compute node is "a
dedicated Pod executing `slurmd`, with a one-to-one placement on Kubernetes Nodes"; a
**SUNK Pod Scheduler** where pods "are placed through Slurm's own scheduling logic… under
Slurm's priority and preemption rules"; a **Syncer** that mirrors state so "unready Pods
appear as drained Slurm nodes"; and a `SlurmCluster` resource that "generates topology
configurations from node labels" [src](https://docs.coreweave.com/products/sunk).

CoreWeave's blog states SUNK is "used on more than 100,000 GPUs across different customers,
allowing for jobs larger than 32,000 GPUs each" [src](https://www.coreweave.com/blog/sunk-slurm-on-kubernetes-implementations)
— **vendor-claimed**, not independently measured.

**⚠️ TO BE VERIFIED (added 2026-09-19):** every SUNK quotation in this subsection is
unre-verified — `docs.coreweave.com/products/sunk` returned **HTTP 500** on the fact-check
pass, so neither the architecture description nor the blog claim could be re-opened at
source. Nothing in this repo's BOMs depends on SUNK (§3.4 picks Kubernetes), so this is a
citation gap, not a planning gap. Method: retry the docs page, or read the `slurm` Helm chart
values directly.

### 3.4 Which one for inference — decision rule

| Your situation | Choose | Why |
|---|---|---|
| Inference only, no training on this hardware | **Kubernetes** | services, probes, rolling updates, HPA/KEDA, Gateway API — all the things a 24×7 endpoint needs, and none of them exist in Slurm |
| Training is the primary workload, inference is occasional | **Slurm** | do not run a second control plane for a secondary workload |
| Both, on the same fleet, with training bursts that should preempt idle inference capacity | **SUNK / Slurm-on-K8s** | one inventory, Slurm's preemption, Kubernetes' service model |
| You have an existing Slurm cluster and want inference on it *now* | Slurm + a static set of long-running jobs behind an external LB | works; you will rebuild it as Kubernetes within a year |

**For this repo: Kubernetes.** Every workload here is a request-serving endpoint with an
SLO ([METHODOLOGY §6](../METHODOLOGY.md#6-cost)'s S1–S4 scenarios), and the whole
scaling programme (autoscaling, routing, PD-disaggregation) assumes a service abstraction.

### 3.5 NVIDIA GPU Operator

**Version 26.7.x** is the current supported line (26.3.x deprecated, ≤25.10.x end of
support). What it ships at v26.7.0:

| Component | Version |
|---|---|
| NVIDIA GPU Driver | **595.91.07** (default/recommended) |
| NVIDIA Container Toolkit | **1.20.0** |
| NVIDIA Device Plugin | **0.20.0** |
| DCGM Exporter | **v4.6.0–4.8.3** |
| Node Feature Discovery | **v0.19.0** |

Supported: Kubernetes **1.33–1.37**; containerd 2.0–2.3 or CRI-O; Ubuntu 26.04/24.04/22.04
LTS, RHEL/Rocky 8.8–10.2, RHCOS 4.18–4.22; **HGX B200, HGX B300, DGX B300/B200, HGX GB200
NVL72, GB300 NVL72** among Blackwell platforms
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html).

Install shape for this cluster (host driver per §2.3, RDMA per §1.5):

```bash
helm install --wait --generate-name -n gpu-operator --create-namespace \
  nvidia/gpu-operator --version=v26.7.0 \
  --set driver.enabled=false \            # driver + fabric-manager baked into the node image
  --set toolkit.enabled=true \
  --set dcgmExporter.enabled=true \
  --set nfd.enabled=true \
  --set migManager.enabled=false          # no MIG on the serving pool (§2.5)
```
(flag names from the GPU Operator install/RDMA docs
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html);
the `--version=v26.7.0` and helm invocation are quoted from that page.)

### 3.6 Network Operator and Node Feature Discovery

**Network Operator v26.7.0** manages "Networking related Components in order to enable Fast
networking, RDMA and GPUDirect for workloads in a Kubernetes cluster"
[src](https://github.com/Mellanox/network-operator), deploying Multus CNI, the SR-IOV
Network Device Plugin, the RDMA Shared Device Plugin, the NVIDIA IPAM plugin and the
DOCA-OFED driver (container or host-installed). Supported NICs include **ConnectX-7 (400
Gb/s) and ConnectX-8 SuperNIC (800 Gb/s)** plus BlueField-3 in NIC mode; Kubernetes
**">=1.32 and <=1.36"**; DOCA-OFED `doca3.5.0-26.07-0.7.7.0-0` (GA) /
`doca3.2.2-25.10-2.4.1.0-4` (LTS)
[src](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html).

**Node Feature Discovery v0.19.0** (shipped by the GPU Operator) labels nodes with PCI
device IDs, CPU features, kernel version and more. It is what makes `nvidia.com/gpu.product`
and friends exist, which §3.9's taints/labels depend on.

### 3.7 DRA vs device plugin

Dynamic Resource Allocation replaces the "count of opaque `nvidia.com/gpu`" model with
typed, claimable devices that can carry attributes, be shared with declared capacity, and
be tainted individually.

**Status on 2026-09-19:**

| Milestone | Where |
|---|---|
| Core DRA APIs GA (`resource.k8s.io/v1`) | Kubernetes **1.34** [src](https://kubernetes.io/blog/2026/09/03/kubernetes-v1-37-dra-updates/) (referencing the v1.34 announcement) — **⚠️ TO BE VERIFIED**: the v1.37 blog was truncated on fetch and did not restate the 1.34/1.35 milestones; confirm against the v1.34 release announcement |
| DRA feature gate locked on | Kubernetes 1.35 — same ⚠️ |
| Device taints **first available** | **1.33** (*corrected 2026-09-19; earlier drafts said "beta in 1.36"*) [src](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/) |
| `DeviceTaintRule` **first available** | **1.35** [src](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/) |
| **Device taints + `DeviceTaintRule` stable**, gates `DRADeviceTaints` / `DRADeviceTaintRules` locked on (set a value and "Kubernetes ignores it but does not report any error") | **1.37** [src](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/) |
| **DRA extended-resource support GA** — a DRA driver can satisfy `example.com/gpu`-style requests, "eliminat[ing] need for separate device plugin alongside DRA driver" | **1.37** [src](https://kubernetes.io/blog/2026/09/03/kubernetes-v1-37-dra-updates/) |
| NVIDIA DRA driver prerequisites | "Kubernetes v1.34.2 or later with a `resource.k8s.io` `DeviceClass` API available" and "NVIDIA GPU driver version 580 or later" [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html) |
| DeviceClasses auto-created on install | `gpu.nvidia.com`, `mig.nvidia.com`, `vfio.gpu.nvidia.com`, plus `compute-domain-daemon.nvidia.com` and `compute-domain-default-channel.nvidia.com` when ComputeDomain support is enabled (*last two added 2026-09-19*) [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html) |
| Governance | NVIDIA donated the DRA Driver for GPUs to the Kubernetes community under CNCF governance at KubeCon Europe 2026 — **⚠️ TO BE VERIFIED**, reported by secondary sources only; no NVIDIA or CNCF primary announcement was retrieved |

DRA install, quoted mechanics: set `clusterPolicy.deployCR=false` and
`gpuCluster.deployCR=true`, and a container runtime "supporting CDI configuration for DRA
device injection"; there must be "no existing `ClusterPolicy` resource in the cluster"
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html).

**Decision rule.**

- **Today, on the 1.36 pin from §3.1: stay on the device plugin (0.20.0).** It is what the
  GPU Operator 26.7 defaults to, every engine's Helm chart assumes `nvidia.com/gpu` limits,
  and the DRA features that would actually change your life (extended-resource GA, stable
  device taints) land in 1.37.
- **Move to DRA when you move to 1.37**, and move for one concrete reason:
  `DeviceTaintRule`. It is the difference between "one bad GPU cordons an 8-GPU node worth
  $x/hour" and "one bad GPU is taken out of the pool". See §6.5.
- **The trade-off:** DRA's ResourceClaim model is a genuinely different API surface, and
  third-party charts (KServe, llm-d, Ray) are still catching up. Running DRA early means
  patching `resourceClaims` into charts that expect `resources.limits`.

`DeviceTaintRule`, the thing worth upgrading for, verbatim:

```yaml
apiVersion: resource.k8s.io/v1
kind: DeviceTaintRule
metadata:
  name: example
spec:
  # The entire hardware installation for this
  # particular driver is broken.
  # Evict all pods and don't schedule new ones.
  deviceSelector:
    driver: dra.example.com
  taint:
    key: dra.example.com/unhealthy
    value: Broken
    effect: NoExecute
```
[src](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/)

Pods already using a tainted device are evicted unless their ResourceClaim tolerates the
taint, and "eviction can be delayed by tolerating a taint for a certain duration" — which is
how you let an in-flight Kimi-K3 request drain before the replica dies
[src](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/).

### 3.8 Topology-aware and gang scheduling

Two separate problems, two separate tools; the common mistake is expecting one to do both.

**Gang (all-or-nothing) placement — Volcano.** The gang plugin "considers that tasks not in
the `Ready` state (including Binding, Bound, Running, Allocated, Succeed, and Pipelined)
have a higher priority" [src](https://volcano.sh/en/docs/schduler_introduction/) (quote
re-confirmed 2026-09-19; page last updated 2026-05-26). The version pin **v1.15.0** is
**⚠️ TO BE VERIFIED** — that page serves as "latest" and did not print a version number on
re-fetch; confirm against the project's GitHub releases before pinning. This is what stops a
Kimi-K3 replica from acquiring 6 of 8 GPUs and deadlocking.

**Network-topology placement — Volcano HyperNode.** Volcano models the fabric as a tree of
`HyperNode` CRs where "`spec.tier`… the lower the tier, the higher the communication
efficiency between nodes within the HyperNode", with members matched by `exactMatch`,
`regexMatch` or `labelMatch`, and jobs/PodGroups carrying a `networkTopology` with
`mode: hard|soft` and `highestTierAllowed`
[src](https://volcano.sh/en/docs/network_topology_aware_scheduling/).

**Quota + topology admission — Kueue.** TAS is **beta since Kueue v0.14, enabled by
default**, gated by `TopologyAwareScheduling`
[src](https://github.com/kubernetes-sigs/kueue/blob/main/site/content/en/docs/concepts/topology_aware_scheduling.md).
Admin side:

```yaml
apiVersion: kueue.x-k8s.io/v1beta2
kind: ResourceFlavor
metadata:
  name: "tas-flavor"
spec:
  nodeLabels:
    cloud.provider.com/node-group: "tas-group"
  topologyName: "default"
```

User side, on the PodTemplate:

```yaml
spec:
  template:
    metadata:
      annotations:
        kueue.x-k8s.io/podset-preferred-topology: "cloud.provider.com/topology-block"
```

Both quoted verbatim [src](https://github.com/kubernetes-sigs/kueue/blob/main/site/content/en/docs/concepts/topology_aware_scheduling.md).
The annotation set is `podset-preferred-topology` (best-effort, walks up the tree),
`podset-required-topology` (hard), `podset-unconstrained-topology` (fill gaps, minimise
fragmentation), `podset-group-name` (keep several PodSets in one domain) and
`podset-slice-required-topology-constraints` (JSON array, ≤3 layers, needs the
`TASMultiLayerTopology` gate) — same source.

Note TAS's capacity model, which is the part people get wrong: it computes free capacity per
domain by "including Node allocatable capacity… of only ready… and schedulable… Nodes,
subtracting the usage coming from all other admitted TAS workloads, subtracting the usage
coming from all other non-TAS Pods" — i.e. **DaemonSets count against your domain
capacity**. On a GPU node running GPU Operator, Network Operator, DCGM exporter, node
exporter and a CNI, that is not a rounding error for CPU/memory (it is for GPUs).

**NUMA alignment.** Set the kubelet Topology Manager to `single-numa-node` for the serving
pool so a replica's CPU, host memory and GPU land on one socket. The reported cost of
getting this wrong is large — one secondary source puts misalignment at "30–50 % or more"
performance loss **⚠️ TO BE VERIFIED** (no primary measurement found; treat as a reason to
align, not as a number to plan with).

**Decision rule for this repo.**

| Workload | Tool | Setting |
|---|---|---|
| Kimi-K3 (8 GPUs = whole node) | Volcano gang, `minMember` = 1 pod requesting 8 GPUs | node-exclusive via taint (§3.9) |
| DeepSeek-V4.1-Flash TP4/TP8, single pod | none needed — one pod, 4 or 8 GPUs from one node | `nvidia.com/gpu: 8` on one container |
| DeepSeek PD-disaggregated across nodes | Volcano gang + `networkTopology: {mode: soft}` | prefill and decode pods must land in one rack |
| Qwen3.8-27B, Marlin-2B | plain Deployment | nothing |
| Dev/CI namespaces competing for spare GPUs | **Kueue** ClusterQueue quota | admission control, not placement |

A single-pod TP8 replica needs **no** gang scheduler — the pod is the gang. Gang scheduling
becomes necessary only when a replica is *several* pods (Ray-based deployments, LeaderWorkerSet,
PD-disaggregation). Do not install Volcano until you have one of those.

### 3.9 Taints and labels for GPU generations

With a mixed fleet ([`matrix/fit-matrix.md`](../matrix/fit-matrix.md) covers 8 GPU types),
the scheduler must never place an NVFP4 checkpoint on an H100 — that pair is `not-runnable`
in this tree, not merely slow.

```yaml
# Node labels — NFD supplies nvidia.com/gpu.product; add your own semantic ones.
nvidia.com/gpu.product: NVIDIA-B300
inference.internal/gpu-arch: sm_103          # sm_90 | sm_100 | sm_103 | sm_120 | gfx950
inference.internal/fp4-native: "true"        # false on H100/H200/A100 -> gates NVFP4 workloads
inference.internal/nvlink-domain: "8"        # 8 on HGX, 72 on NVL72
inference.internal/node-rack: rack-01        # feeds Kueue Topology / Volcano HyperNode
inference.internal/node-block: block-a
```

```yaml
# Taint the big-model pool so nothing small squats on it.
kubectl taint nodes <node> inference.internal/whole-node-model=kimik3:NoSchedule
```

Then a Kimi-K3 replica tolerates that taint and requests all 8 GPUs; everything else cannot
land there. The alternative — relying on the scheduler to leave 8 free GPUs on some node —
fails the first time a Marlin-2B replica lands on the last node with capacity.

`fp4-native` is the label that encodes this tree's hardest-won fact: NVFP4/MXFP4 checkpoints
execute natively only on `sm_100`/`sm_103` (and disputedly `sm_120`), and fall back to
Marlin W4A16 elsewhere — a memory win, not a speed win
([`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md),
[`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md)).
Encoding it as a node label and a `nodeSelector` is how you stop that fact from having to
live in someone's head.

---

## 4. Networking inside the cluster for inference

### 4.1 What actually crosses the network

| Traffic | Volume | Latency sensitivity | Path |
|---|---|---|---|
| Client HTTP/gRPC in, tokens out | small (KB) | **very high** — it is TTFT/TPOT | ingress → CNI → pod |
| TP/EP collectives | huge | extreme | **NVLink, never the network** (§1.2) |
| PD-disaggregation KV transfer | **large** — MB–GB per request | high | RDMA, GPU→GPU (§4.6) |
| KV-cache offload/lookup (LMCache, Mooncake) | large | medium | RDMA or NVMe-oF |
| Weight pull at cold start | **very large** — up to 1.56 TB | one-shot, but gates cold start | §5 |
| Metrics, logs | small | none | management network |

Two conclusions: the *request* path is tiny and wants low latency, so overlay overhead is
about latency not bandwidth; the *KV and weight* paths are huge and want raw RDMA
bandwidth, so they must bypass the overlay entirely.

### 4.2 CNI choice

| CNI | Datapath | Why here |
|---|---|---|
| **Cilium** (eBPF) | eBPF, optional kube-proxy replacement, native routing mode | lowest added latency when run in **native routing** (no VXLAN/Geneve encapsulation), plus network policy and observability |
| Calico | eBPF or iptables, BGP or VXLAN | fine; BGP native routing equally good |
| Flannel VXLAN | encapsulation | avoid — encapsulation on the request path for no benefit |

**Decision rule:** Cilium (or Calico) in **native routing / BGP mode**, not overlay. On a
bare-metal cluster you control the fabric, so you can route pod CIDRs natively and skip
encapsulation. The trade-off is that you must coordinate pod CIDR allocation with your
network team — which is a one-time cost, versus a per-packet cost forever.

### 4.3 hostNetwork vs overlay for RDMA

RDMA does not traverse the CNI. The verbs path goes user-space → `ib_uverbs` → HCA, and the
HCA needs a device the pod can see plus (for RoCE) an IP the fabric can route.

Three shapes:

| Shape | Mechanism | Pros | Cons |
|---|---|---|---|
| **hostNetwork: true** | pod shares host netns, sees all HCAs | simplest thing that works; NCCL/UCX see the real interfaces; no IPAM problem | no network isolation; port collisions between replicas (see the NIXL side-channel port note in §4.6) |
| **RDMA shared device plugin** | Network Operator exposes `rdma/rdma_shared_device_a`; pod keeps its CNI IP, gets `/dev/infiniband` | keeps pod networking; several pods share one HCA | shared, not isolated — one tenant can starve another |
| **SR-IOV + Multus** | VF per pod as a secondary interface via Multus, SR-IOV device plugin, NV-IPAM | real isolation, per-pod VF, line rate | VF count is finite (per-NIC limit); more moving parts; requires SR-IOV enabled in BIOS and firmware |

All three are deployed by the Network Operator, which ships Multus CNI, the SR-IOV Network
Device Plugin, the RDMA Shared Device Plugin and the NVIDIA IPAM plugin
[src](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html).
The `NICClusterPolicy` CRD additionally covers OFED driver containers, InfiniBand
Kubernetes, IPoIB CNI and the NIC Configuration Operator, "any sub-state may be omitted if
it is not required for the cluster" [src](https://github.com/Mellanox/network-operator).
**⚠️ TO BE VERIFIED:** earlier drafts quoted that page as supporting *"RoCE shared mode,
SR-IOV, and NIC PF passthrough"*; that exact phrasing was **not** retrievable from the repo
README on 2026-09-19. The three shapes in the table above are still the three the deployed
components implement — only the quotation is unsourced.

**Decision rule.**

- **Single-tenant inference cluster (this repo's case): `hostNetwork: true` for the engine
  pods.** It is the lowest-friction configuration, it is what every engine's multi-node
  documentation assumes, and there is no tenant to isolate from. You must then manage ports
  explicitly — one replica per node per port, or an explicit port-per-replica scheme.
- **Multi-tenant, or you need per-pod bandwidth accounting: SR-IOV + Multus.** Accept the VF
  ceiling and the extra CRDs.
- **Never:** RDMA over an encapsulated overlay. It does not work, and the failure is a
  silent fallback to TCP that halves your disaggregation throughput without erroring.

### 4.4 MTU and jumbo frames

Set MTU 9000 end to end on the Ethernet/storage fabric (NIC, switch, pod interface); on
InfiniBand the equivalent is the partition MTU (4096 B is the IB maximum). The failure mode
is a mismatch: one device at 1500 in a 9000-byte path causes fragmentation or black-holing
that presents as intermittent slowness under load. Assert MTU in the node conformance check
(§8.4), do not assume it.

### 4.5 Service mesh overhead

A sidecar mesh (Istio/Linkerd in sidecar mode) adds two extra proxy hops per request. For a
typical LLM response — TTFT in the hundreds of ms, TPOT tens of ms — a sub-millisecond proxy
hop is genuinely negligible **for the token stream**. Where it is not negligible:

- **Streaming.** Any proxy that buffers breaks SSE token streaming. Verify your mesh streams
  rather than buffers.
- **Long-lived connections.** Mesh idle timeouts shorter than a long generation will cut
  requests mid-stream. This is the single most common mesh-plus-LLM bug.
- **The RDMA path.** The mesh must not be in it at all.

**Decision rule:** if you need mTLS between services, use an ambient/eBPF mesh or plain mTLS
at the gateway rather than sidecars on GPU pods. A sidecar on a pod that costs $15/GPU-hour
× 8 is an expensive place to put a proxy. **⚠️ TO BE VERIFIED:** no measurement of mesh
overhead on LLM streaming workloads was found; the reasoning above is structural.

### 4.6 KV transfer for PD-disaggregation: NIXL / UCX

Disaggregated prefill/decode moves the KV cache from prefill GPUs to decode GPUs after
prefill completes. **NIXL** (NVIDIA Inference Xfer Library) is the transport abstraction:
"targeted for accelerating point to point communications in AI inference frameworks such as
NVIDIA Dynamo, while providing an abstraction over various types of memory (e.g., CPU and
GPU) and storage (e.g., file, block and object store) through a modular plug-in
architecture. Internally it uses UCX as its transport library."
[src](https://docs.nvidia.com/dynamo/archive/0.7.1/backends/trtllm/kv-cache-transfer.html)

vLLM's `NixlConnector`, with flags quoted from the docs:

```bash
# Prefill instance
vllm serve <model> \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer"}'

# Decode instance
vllm serve <model> \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer"}'
```

Environment, all quoted [src](https://docs.vllm.ai/en/stable/features/nixl_connector_usage/):

| Variable | Meaning / default |
|---|---|
| `VLLM_NIXL_SIDE_CHANNEL_PORT` | handshake port, **default 5600**; "each worker needs a unique port per host" |
| `VLLM_NIXL_SIDE_CHANNEL_HOST` | default `localhost`; **required for cross-machine setups** |
| `UCX_TLS` | e.g. `all` or `rc,ud,sm,^cuda_ipc` |
| `UCX_NET_DEVICES` | e.g. `mlx5_0:1,mlx5_1:1` |
| `UCX_CUDA_IPC_ENABLE_MNNVL` | `y` for GB-series NVLink, with `--enable-cumem-allocator` or `--enable-sleep-mode` |

Extra config in `kv_connector_extra_config`: `backends+ LIBFABRIC` (default is UCX),
`kv_load_failure_policy: "fail"` ("Reject failed transfers immediately (recommended)"),
`kv_lease_duration` (default 30 s), `decoder_kv_blocks_ttl` (default 480),
`bidirectional_kv_xfer: true` for multi-turn KV reuse — same source.

Two infrastructure requirements fall straight out of those flags:

1. **`VLLM_NIXL_SIDE_CHANNEL_PORT` default 5600, unique per worker per host.** With
   `hostNetwork: true` (§4.3) two replicas on one node collide on 5600 and the handshake
   fails. Allocate ports deterministically from the replica ordinal.
2. **`UCX_NET_DEVICES` must be set.** Left unset, UCX picks devices by its own heuristics
   and can select the management NIC. Pin it to the same HCAs as `NCCL_IB_HCA` (§1.6).

Engine/version pins from this repo: Dynamo 1.5 with **NIXL v1.3.1** (vLLM v0.28.0 runtime)
and **NIXL v1.3.0** (SGLang v0.5.17 runtime) for the Kimi-K3 containers
([`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)).

**Sizing the KV transfer.** Per request, bytes moved = `prompt_tokens ×
kv_bytes_per_token` ([METHODOLOGY §2](../METHODOLOGY.md#2-kv-cache-and-per-sequence-state)).
For DeepSeek-V4.1-Flash on B300 (890 B/token, the FP4-KV kernel path that exists on
`sm_103` — [METHODOLOGY §8](../METHODOLOGY.md#models)):

| Scenario | Prompt | Tokens | KV bytes/request | At 800 Gb/s (100 GB/s) | Requests per NIC-second |
|---|---:|---:|---:|---:|---:|
| S1 | 4 K | 4,096 | 3.65 MB | 0.036 ms | ~27,400 |
| S2 | 32 K | 32,768 | 29.2 MB | 0.292 ms | ~3,430 |
| S3 | 128 K | 131,072 | 116.7 MB | 1.167 ms | **~857** |

`est.` from METHODOLOGY §2's formula at the pinned 890 B/token; wire time only, no protocol
overhead, no contention. **Recomputed 2026-09-19** on two counts: earlier drafts used
decimal K (4,000 / 32,000 / 128,000 tokens → 3.56 / 28.5 / 114 MB), which contradicts
[METHODOLOGY §1](../METHODOLOGY.md#1-weight-memory)'s "do all memory arithmetic in bytes"
discipline; and the concluding ratio read **"~8.8 requests"**, which was wrong by a factor of
100 — 100 GB/s ÷ 116.7 MB = **~857 requests per NIC-second at 128 K**. The conclusion the
table was drawn for is unchanged but far stronger than it was stated: a single 800 Gb/s rail
supports a large prefill:decode ratio before the fabric, rather than the GPUs, becomes the
limit. On a GPU
where the FP4-KV kernel does not resolve, budget **FP8 1,650 B/token** and scale the table
by 1.85× ([METHODOLOGY §8 pin log](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)).

---

## 5. Weight distribution

This is where cold-start time is won or lost, and where a naive design produces a
**self-inflicted denial of service** on the shared filesystem every time the autoscaler
reacts.

### 5.1 The four ways to get weights onto a node

| Method | Cold start | Pros | Cons |
|---|---|---|---|
| **Baked into the container image** | image pull | immutable, content-addressed, one artifact | a 1.5 TB image; registry and every node's disk carry it; a model bump is an image rebuild |
| **PVC / shared volume (RWX on parallel FS)** | mount, then read at serve time | no copy step | **every replica reads the FS at load time**; a scale-out event is a coordinated random-read storm |
| **Object store → local NVMe cache** | one-time fill, then local reads | fast steady state, cheap source of truth | needs a cache manager + eviction |
| **Direct streaming object store → GPU** | no local copy | no disk needed | repeated pulls on every restart; bound by object-store bandwidth |

**Recommendation: object store as source of truth (§1.8 T3) → local NVMe cache (T2) →
stream to GPU with a parallel loader.** The container image carries the engine, never the
weights. The reason is the second row's failure mode: with a PVC, `n` replicas starting
together read `n × checkpoint_bytes` from the same filesystem in the same seconds. At
Kimi-K3's 1.56 TB that is 1.56 TB × n of correlated demand — the exact opposite of what you
want during a scale-out.

### 5.2 The safetensors-mmap trap (measured, on a real 805 GiB checkpoint)

The default safetensors path memory-maps the file and faults pages in on demand. For a large
MoE checkpoint this turns into a random-read storm, and it has a *tail*, not an average:

> "each rank reports `Loaded shard X/8 (XX.X GiB)` within ~3-5 minutes, then 5 of 8 workers
> complete weight materialization in ~6-9 min while **3 of 8 workers stall in
> `safetensors._safetensors_rust.safe_open` random reads for >60 min** (no progress in
> `iotop`, but ~30-50 MB/s random-read load on the NVMe). The collective barrier never
> closes; `vllm serve` never reports `Application startup complete`."
> — vLLM issue #40988, filed 2026-04-27, on `deepseek-ai/DeepSeek-V4-Pro` (1.6T MoE,
> **805 GiB checkpoint, ~102 GiB/rank**), EXT4 on local NVMe
> [src](https://github.com/vllm-project/vllm/issues/40988)

The fix, same source: `--safetensors-load-strategy prefetch` → "full cold-start in ~12 min".
And the threshold, from the same reporter: V4-Flash at ~37 GiB/rank "does **not** reproduce
the hang — it loads in ~290s via the default lazy mmap path, no straggler, no failure".

**This is the single most important operational fact in this section**, because the failure
is not an error. It is a barrier that never closes, on 3 of 8 ranks, non-deterministically.
Your readiness probe times out and the orchestrator restarts the pod, which starts the storm
again.

The documented strategies [src](https://docs.vllm.ai/en/stable/configuration/engine_args/):

| `--safetensors-load-strategy` | Behaviour |
|---|---|
| *(None, default)* | "memory-mapped (lazy) loading. When an NFS filesystem is detected and the total checkpoint size fits within 90% of available RAM, prefetching is enabled automatically" |
| `lazy` | "Weights are memory-mapped from the file… highly efficient for models on local storage" |
| `eager` | "the entire file is read into CPU memory upfront… recommended for models on network filesystems (e.g., Lustre, NFS) as it avoids inefficient random reads… However, it uses more CPU RAM" |
| `prefetch` | "Checkpoint files are read into the OS page cache before workers load them… Useful on network or high-latency storage" |
| `torchao` | — |

`--safetensors-prefetch-num-threads` defaults to **8** [src](https://docs.vllm.ai/en/stable/configuration/engine_args/).

Note the default's auto-prefetch heuristic only triggers on **NFS** and only when the
checkpoint fits in 90 % of RAM. On **EXT4/XFS local NVMe** — exactly this repo's T2 — it does
not trigger, which is precisely the configuration issue #40988 hit.

**Rule for this repo:** any model with **> 50 GiB per rank** gets an explicit load strategy.
Per-rank shard sizes at the recommended shapes:

| Model | Checkpoint | Shape | Per-rank shard `est.` | Default mmap safe? |
|---|---:|---|---:|---|
| Kimi-K3 | 1,560.9 GB | TP8/EP8 | **195 GB** | **no** — well past the observed danger zone |
| DeepSeek-V4.1-Flash | 510.29 GB | TP8 | **63.8 GB** | **no** — above the ~50 GiB line |
| DeepSeek-V4.1-Flash | 510.29 GB | TP4 | **127.6 GB** | **no** |
| DeepSeek-V4.1-Flash-NVFP4 | 527.27 GB | TP4–TP8 | 65.9–131.8 GB | **no** |
| Qwen3.8-27B | 30.87 GB FP8 | TP1 | 30.87 GB | yes |
| Marlin-2B | 5.444 GB | TP1 | 5.4 GB | yes |

(`checkpoint_bytes / n_gpus`, the naive share; see
[`matrix/fit-matrix.md`](../matrix/fit-matrix.md) for the resident-weight multipliers that
make actual per-GPU residency larger.)

### 5.3 Parallel loaders

Three options, all first-class in vLLM's `--load-format`
(choices: `auto, pt, safetensors, instanttensor, npcache, dummy, tensorizer, runai_streamer,
runai_streamer_sharded, sharded_state, mistral, modelexpress`
[src](https://docs.vllm.ai/en/stable/configuration/engine_args/)):

**Run:ai Model Streamer** — reads tensors concurrently and streams to GPU as they arrive.

```bash
vllm serve s3://core-llm/Llama-3-8b --load-format runai_streamer
# tunables
--model-loader-extra-config '{"concurrency":16}'                  # OS threads reading tensors
--model-loader-extra-config '{"memory_limit":5368709120}'         # CPU buffer bytes
--model-loader-extra-config '{"distributed":true}'                # distributed streaming
# sharded checkpoints
--load-format runai_streamer_sharded
--model-loader-extra-config '{"pattern":"custom-model-rank-{rank}-part-{part}.safetensors"}'
# S3-compatible (MinIO/Ceph/GCS)
RUNAI_STREAMER_S3_USE_VIRTUAL_ADDRESSING=0
AWS_EC2_METADATA_DISABLED=true
AWS_ENDPOINT_URL=https://storage.googleapis.com
```
All verbatim [src](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/).

**InstantTensor** — `pip install instanttensor`, then `--load-format instanttensor`
[src](https://docs.vllm.ai/en/latest/models/extensions/instanttensor/). Direct I/O by
default, io_uring/libaio/cuFile backends, and a `torch.distributed` NCCL process group so
ranks cooperate rather than each hammering the disk. Published benchmark:

| Model | GPUs | Backend | Load time | Throughput | Speedup |
|---|---|---|---:|---:|---:|
| Qwen3-30B-A3B | 1×H200 | Safetensors | 57.4 s | 1.1 GB/s | 1× |
| Qwen3-30B-A3B | 1×H200 | **InstantTensor** | **1.77 s** | **35 GB/s** | **32.4×** |
| DeepSeek-R1 | 8×H200 | Safetensors | 160 s | 4.3 GB/s | 1× |
| DeepSeek-R1 | 8×H200 | **InstantTensor** | **15.3 s** | **45 GB/s** | **10.5×** |

[src](https://github.com/scitix/InstantTensor) — **vendor-published benchmark**, not
independently reproduced. Requirements: CUDA or ROCm, PyTorch, Linux kernel ≥ 5.6 for
io_uring (≥ 5.15 recommended). Backends: `AIO, AIO_BUFFERED, URING, URING_BUFFERED, CUFILE,
MMAP`, selectable via `INSTANTTENSOR_BACKEND` — same source.

The project's own "when to use" list maps almost exactly onto this repo: "High storage
bandwidth (>= 5 GB/s)"; "Unable to keep the model cached in host memory, for example…
Limited free memory for model caching (for example, when most memory is used for KV cache
offloading in LLM serving)"; "The model is heavily sharded (for example, TP=8), resulting in
small, non-contiguous I/O per GPU"; and a fourth entry, "Loading from tmpfs", that earlier
drafts omitted [src](https://github.com/scitix/InstantTensor). The list is *any-of*, not
all-of.

The same vLLM issue reports InstantTensor loading V4-Flash "in ~24s (~12× speedup vs
prefetch)" on that 8-node DGX Spark cluster [src](https://github.com/vllm-project/vllm/issues/40988)
— a third-party data point, on ARM/GB10 hardware, not B300.

**Decision rule.**

| Situation | Loader |
|---|---|
| Weights on local NVMe, ≥ 5 GB/s, TP ≥ 4 | **`--load-format instanttensor`** — highest measured throughput, direct I/O so it does not evict your page cache |
| Weights in S3 and you do **not** want a local copy | **`--load-format runai_streamer`** with `s3://` URI |
| Neither installed / conservative baseline | **`--safetensors-load-strategy prefetch`** — the minimum acceptable configuration for any >50 GiB/rank model |
| Small model, local NVMe | default `lazy` is fine |

The trade-off with InstantTensor and Run:ai Streamer is a third-party dependency in the
critical path of every cold start. The trade-off with `prefetch` is one extra full pass over
the checkpoint — "The cost is ~one extra disk-bandwidth-bound pass; the benefit is
eliminating per-tensor disk-fault tail latency"
[src](https://github.com/vllm-project/vllm/issues/40988).

### 5.4 Local NVMe layout

```
/dev/nvme[0-3]n1   4 × 7.68 TB U.2, RAID0 (md or LVM stripe), XFS, mounted /var/lib/models
```

**RAID0, not RAID1/5/6.** The data is a cache; the source of truth is the object store. A
failed drive costs a re-pull, not data. RAID0 across 4 drives is what gets you into the
≥ 5 GB/s band InstantTensor wants; a single drive does not (issue #40988's cluster was
"per-node local NVMe, EXT4, **single-disk (no RAID)**", which is part of why its tail was so
bad [src](https://github.com/vllm-project/vllm/issues/40988)).

**XFS over EXT4** for large-file sequential and parallel-read workloads; both are
GDS-supported [src](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html).

**Capacity:** 2.40 TiB for one copy of all five models (§0, corrected 2026-09-19), × 2 for holding a new version
alongside the running one during a rollout, + headroom = **≥ 8 TB usable**. 4 × 7.68 TB
RAID0 ≈ 30 TB gives room for several model versions and a KV-offload scratch area.

### 5.5 Measured load times per GB/s — what to expect

`est.`, `checkpoint_bytes / effective_bandwidth`, ignoring post-load materialization
(which §5.2 shows can dominate on the wrong path):

| Model | Bytes | 1.2 GB/s (single-thread safetensors) | 3.5 GB/s | 10 GB/s (parallel NVMe) | 26.4 GB/s | 45 GB/s (InstantTensor 8-GPU) |
|---|---:|---:|---:|---:|---:|---:|
| **Kimi-K3** | 1,560.9 GB | **21.7 min** | 7.4 min | 2.6 min | 59 s | **35 s** |
| DeepSeek-V4.1-Flash | 510.3 GB | 7.1 min | 2.4 min | 51 s | 19 s | 11 s |
| DeepSeek-V4.1-Flash-NVFP4 | 527.3 GB | 7.3 min | 2.5 min | 53 s | 20 s | 12 s |
| Qwen3.8-27B (FP8) | 30.9 GB | 26 s | 9 s | 3 s | 1.2 s | 0.7 s |
| Marlin-2B | 5.4 GB | 4.5 s | 1.6 s | 0.5 s | 0.2 s | 0.1 s |

Bandwidth anchors: 1.1–1.2 GB/s is the published single-threaded safetensors rate
([InstantTensor benchmark](https://github.com/scitix/InstantTensor): 1.1 GB/s; the vLLM
issue's ~1.2 GB/s default); 35 and 45 GB/s are InstantTensor's published single-H200 and
8×H200 figures [src](https://github.com/scitix/InstantTensor), re-confirmed verbatim
2026-09-19. **⚠️ TO BE VERIFIED — the 26.4 GB/s column.** Earlier drafts attributed it to
"Run:ai Model Streamer's published Llama-70B/4-GPU GDS figure"; vLLM's Run:ai Model Streamer
page [src](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/) carries
**no benchmark figures at all**, and the Run:ai benchmark PDF returned HTTP 404 on
2026-09-19. Treat the 26.4 GB/s column as an **unsourced interpolation** between the 10 and
35 GB/s anchors, not as a measurement. Method: re-locate the Run:ai benchmark report, or
delete the column and interpolate explicitly.

**Weights are not the whole cold start.** The academic decomposition — "Breaking the Ice:
Analyzing Cold Start Latency in vLLM", Kabakibo, Trivedi & Wang, arXiv:2606.07362, submitted
2026-06-05 — breaks startup "into six foundational steps and demonstrate[s] that this process
is **predominantly CPU-bound**" [src](https://arxiv.org/abs/2606.07362) — title, authors,
the 2026-06-05 submission (v3 2026-06-29) and both quoted phrases re-confirmed against the
abstract page 2026-09-19. **⚠️ TO BE VERIFIED:** the "on H100 and L40S, for models up to
~20 B" scoping is **not in the abstract or arXiv metadata** and must be read out of the paper
body before it is relied on. Their tested sizes are believed far below this repo's, so the
*ratio* does not transfer, but the structural claim does: after weight load you still pay
`torch.compile`, CUDA-graph capture and KV-cache profiling, and those are CPU-bound and
cacheable. Persist the compile cache on the node's NVMe (`VLLM_CACHE_ROOT` /
`TORCHINDUCTOR_CACHE_DIR`) so only the first replica on a node pays them.

**Budget for a Kimi-K3 cold start on B300:** ~35 s–2.6 min weight load (loader-dependent) +
compile/graph capture + KV profiling. Treat **3–5 minutes** as the planning figure until
measured, and mark it ⚠️ — **no Kimi-K3 cold-start measurement exists in this tree**, and it
is the number that sets the floor for every predictive-scaling decision downstream.

### 5.6 P2P distribution: Dragonfly

When `n` nodes must fill their NVMe cache from one object store, the object store is the
bottleneck and the cost scales with `n`. Peer-to-peer distribution makes it scale with
`log n` instead.

**Dragonfly** is a **CNCF Graduated** project that "delivers efficient, stable, and secure
data distribution and acceleration powered by P2P technology… improving large-scale delivery
of files, container images, OCI artifacts, AI/ML models, caches, logs, dependencies, etc."
[src](https://github.com/dragonflyoss/dragonfly). It publishes SBOMs with releases and has a
third-party security audit by Trail of Bits — same source. Architecture is Manager +
Scheduler + Seed Peer + per-node Peer (`dfdaemon`); as of August 2026 the database-backed
control plane became opt-in rather than mandatory, which lowers the entry cost
**⚠️ TO BE VERIFIED** (reported by CNCF's blog, not confirmed against the project's own
release notes here).

Alternatives: Uber's **Kraken** (torrent-style, container images), plain BitTorrent, or a
per-rack seed cache.

**Decision rule.**

| Fleet size | Approach |
|---|---|
| **≤ 4 nodes** | **Skip P2P.** Pull directly from the object store. 4 × 1.56 TB = 6.2 TB of one-off reads is unremarkable for any S3-class store, and a Dragonfly control plane is another thing to operate. |
| **8–32 nodes** | Dragonfly, or a simpler per-rack seed node that the others rsync from. |
| **> 32 nodes, frequent model rollouts** | Dragonfly, with seed peers per rack. |

For this repo's 4-node BOM: no P2P. For the 16-node BOM: Dragonfly, because a model rollout
becomes 16 × 1.56 TB = 25 TB of correlated pull.

### 5.7 Checksums, versioning and atomicity

Non-negotiable, because a truncated 1.5 TB checkpoint produces a model that *runs* and emits
plausible-looking garbage:

1. **Content-addressed layout.** `s3://models/<repo>/<revision-sha>/…`. Never a mutable
   `latest/`. The revision is the HF commit SHA.
2. **Verify before use.** Safetensors headers give per-tensor offsets/dtypes but no content
   hash; compute and store a per-file SHA-256 manifest at publish time and verify on cache
   fill. `model.safetensors.index.json`'s `total_size` is the cheap first check — and is
   already this tree's ground truth for weight arithmetic
   ([METHODOLOGY §1](../METHODOLOGY.md#1-weight-memory)).
3. **Atomic cache fill.** Download to `…/<sha>.partial/`, verify, then `rename()` to
   `…/<sha>/`. A pod that finds `…/<sha>/` knows it is complete. Without this, a node that
   dies mid-pull leaves a half-checkpoint that the next pod happily mmaps.
4. **Pin the revision in the Deployment**, not just the repo name. A model rollout is then a
   normal Kubernetes rollout with a normal rollback.
5. **Eviction by LRU with a floor**: never evict a revision that a running pod references.

---

## 6. Observability baseline

### 6.1 The minimum viable stack

| Layer | Component | Version pin |
|---|---|---|
| GPU telemetry | **DCGM Exporter** | v4.6.0–4.8.3 (GPU Operator 26.7 range) [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) |
| Host telemetry | node_exporter | any current |
| Fabric | IB: UFM / `perfquery`; RoCE: switch telemetry + NIC counters | UFM 3.5 Enterprise per the B300 SuperPOD RA [src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html) |
| Kernel events | Xid/SXid from `dmesg`, via DCGM fields (§6.3) | — |
| Health remediation | Node Problem Detector, or **NVSentinel** | §6.5 |
| Engine metrics | vLLM/SGLang `/metrics` | out of scope here — see the serving docs |

### 6.2 DCGM exporter

Deployed "as a daemonset on GPU nodes in a Kubernetes cluster"; metric selection is a CSV
file, `-f` "Path to file containing DCGM fields to collect", default
`/etc/dcgm-exporter/default-counters.csv`, overridable with `$DCGM_EXPORTER_CONFIGMAP_DATA`
("ConfigMap namespace and name containing DCGM fields to collect");
`$DCGM_EXPORTER_KUBERNETES` "Enable[s] kubernetes mapping metrics to kubernetes pods"
(default false) [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html).

The counters worth alerting on, split by what they tell you:

| Field | What it answers |
|---|---|
| `DCGM_FI_PROF_GR_ENGINE_ACTIVE` | is the GPU doing *anything* |
| `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` | is it doing **matmul** — the difference between "busy" and "useful" |
| `DCGM_FI_PROF_DRAM_ACTIVE` | HBM pressure — the decode-bound signal (METHODOLOGY §4's MBU, empirically) |
| `DCGM_FI_DEV_FB_USED` | KV-pool occupancy proxy |
| `DCGM_FI_DEV_GPU_TEMP` / power | thermal/power headroom (§6.4) |
| `DCGM_FI_DEV_XID_ERRORS` | **hardware fault** (§6.3) |
| `DCGM_FI_DEV_NVSWITCH_FATAL_ERRORS` / `_NON_FATAL_ERRORS` | NVSwitch SXids (§6.3) |

Profiling field names quoted [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html).

**`DCGM_FI_PROF_GR_ENGINE_ACTIVE` is not utilization in the sense you want.** A vLLM decode
loop with CUDA graphs keeps the graphics engine near 100 % while doing very little useful
work at batch 1. Alert on `PIPE_TENSOR_ACTIVE` and on engine-reported throughput, not on
`GR_ENGINE_ACTIVE`. This matters directly for the autoscaling documents downstream: GPU
utilization is the wrong autoscaling signal for LLM serving, and DCGM is where people get
the wrong signal from.

### 6.3 Xid and SXid

Xids are driver-reported hardware/driver errors, surfaced in `dmesg`/syslog and exported as:

**Corrected 2026-09-19 against NVIDIA's own [Field Identifiers](https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html)
reference.** Two of the three names earlier drafts used do not exist:

| Field (as published) | ID | Meaning |
|---|---:|---|
| `DCGM_FI_DEV_XID_ERROR` | **230** | "XID errors. The value is the specific XID error" — *singular `_ERROR`; the DCGM-exporter metric is emitted as `DCGM_FI_DEV_XID_ERRORS`* |
| `DCGM_FI_DEV_SXID_FATAL_ERROR` | **856** | "NVSwitch fatal error information. Note: value field indicates the specific SXid reported" — **not** `DCGM_FI_DEV_NVSWITCH_FATAL_ERRORS` |
| `DCGM_FI_DEV_NVSWITCH_LINK_FATAL_ERRORS` | **782** | "NvSwitch fatal_errors for ports 0-17" |
| `DCGM_FI_DEV_NVSWITCH_LINK_NON_FATAL_ERRORS` | **783** | "NvSwitch non_fatal_errors for ports 0-17" |

There is **no field named `DCGM_FI_DEV_NVSWITCH_NON_FATAL_ERRORS`** on that page, and id
**857 was not confirmed** — earlier drafts invented the name and paired it with an
unverified id. Use **783** for the per-port non-fatal counter and **856** for the SXid-valued
fatal field; a `DCGM_FI_DEV_SXID_NON_FATAL_ERROR` companion at 857 is plausible but is
**⚠️ TO BE VERIFIED** (it did not appear in the retrieved portion of the reference).

Read this together with §2.2's ⚠️: NVIDIA's Fabric Manager guide states SXid fatal/non-fatal
reporting **does not apply on HGX/DGX B200/B300**, so on this repo's node shape the 856/857
pair may carry nothing at all and the port-level 782/783 counters are the ones to wire up.

NVIDIA's [Xid catalog](https://docs.nvidia.com/deploy/xid-errors/index.html) could not be
retrieved in full on 2026-09-19 (its landing page returned only a table of contents).
**Do not hard-code a fatal-Xid list from this document** — read the catalog.

A known gap worth designing around: some Xids are not exported at all
([NVIDIA/DCGM issue #235: "Some XID errors (e.g. XID 62) are not exported via
DCGM_FI_DEV_XID_ERRORS metric"](https://github.com/NVIDIA/DCGM/issues/235)). Scrape `dmesg`
as well as DCGM; a metric-only pipeline will miss faults.

**SXids are the NVSwitch ones and they are the ones that matter on an 8-GPU baseboard**,
because §2.2's non-fatal FM failure mode — degraded but running — shows up here and nowhere
else.

### 6.4 Thermal, power and fabric counters

- **Thermal/power:** `DCGM_FI_DEV_GPU_TEMP`, plus power draw and any clock-throttle reason
  fields. At 1.1 kW/GPU in an air-cooled rack (§1.9) thermal throttling is a live risk;
  alert on sustained clock reduction, not on temperature alone.
- **InfiniBand counters:** per-port `PortXmitData`/`PortRcvData` for bandwidth, and the error
  counters — `SymbolErrorCounter`, `LinkDownedCounter`, `PortRcvErrors`,
  `LinkErrorRecoveryCounter` — for link health, via `perfquery` or UFM. A link that
  renegotiates to a lower rate is the classic silent inference regression: nothing errors,
  p99 TTFT goes up. Assert link rate as well as link state. **⚠️ TO BE VERIFIED:** exact
  counter names were not confirmed against a primary IB spec page in this pass; they are
  standard IB performance counters, but check `perfquery` output on your fabric.
- **RoCE:** PFC pause frames sent/received per port, and ECN marks. Rising pause counts mean
  your lossless config is doing work — and pause frames are how a congested storage flow
  becomes an inference latency spike.

### 6.5 Health checks and automated remediation

| Tool | What it does |
|---|---|
| `nvidia-smi -q` | quick liveness, ECC state, VBIOS, throttle reasons |
| `dcgmi diag -r <1\|2\|3\|4>` | escalating diagnostic; level 3+ is a real stress test — **takes minutes and must not run on a serving GPU** |
| Node Problem Detector | translates kernel-log patterns into Node Conditions; **"doesn't take direct action to remediate"** — you pair it with a remediator |
| **NVSentinel** | NVIDIA's GPU health monitor: "watches GPU health using NVIDIA DCGM… and detects hardware failures", modes `operator-service` (default) / `external-hostengine` / `embedded-mode` |

NVSentinel's documented escalation is node conditions → cordon → drain & reboot via
`RebootNode` CRs when the recommended action is `RESTART_BM`, with a node drainer handling
pod evacuation; configuration is Helm values
(`global.gpuHealthMonitor.enabled: true`), and it names specific watches — `GpuPowerWatch`
on **Xid 54, 56, 58, 78** and `GpuThermalWatch` on **Xid 61**
[src](https://docs.nvidia.com/nvsentinel/configuration/gpu-health-monitor/). No version
number is published on that page — **⚠️ TO BE VERIFIED** before pinning it.

**The remediation design that matters.** Today (Kubernetes 1.36, device plugin) the unit of
remediation is the **node**: one bad GPU cordons all 8, which at B300 prices is expensive.
Kubernetes 1.37's stable `DeviceTaintRule` (§3.7) makes the unit the **device** — "platform
engineers [can] drain or isolate individual degraded GPUs without cordoning off an entire
multi-GPU node". That is the concrete payoff for the 1.37 + DRA migration, and it is worth
sequencing the upgrade around.

**Policy to run from day one:**

1. `dcgmi diag -r 3` on every node **before it joins the serving pool**, and after any
   reboot, driver change or Xid-triggered recovery. Never on a node serving traffic.
2. NPD (or NVSentinel) watching Xid/SXid → Node Condition.
3. A remediator that cordons + drains on a fatal condition, and **pages rather than
   auto-reboots** for non-fatal SXids — because §2.2's degraded-fabric case needs a human to
   decide whether a 10 % throughput loss is worth a reset that evicts every request on the
   node.
4. Every drain must respect a PodDisruptionBudget sized so that draining one node cannot
   take the last replica of a model — especially Kimi-K3, which has exactly one replica per
   node.

---

## 7. Security and multi-tenancy basics

### 7.1 Namespace and quota model

A workable minimum for an inference cluster:

| Namespace | Contents | GPU access |
|---|---|---|
| `gpu-operator`, `network-operator`, `nvidia-dra` | infra operators | privileged, node-level |
| `monitoring` | Prometheus, DCGM exporter scrape targets, Grafana | none |
| `inference-prod` | the five models' Deployments/LWS + gateway | full nodes via taint toleration |
| `inference-staging` | canary replicas | a small labelled node pool |
| `dev` | experiments | MIG slices or a fixed small pool, **Kueue quota** |

`ResourceQuota` on `requests.nvidia.com/gpu` per namespace is the crude backstop; Kueue
`ClusterQueue` quota is the one that queues instead of rejecting, which is what you want for
dev.

### 7.2 GPU isolation — what you actually get

Be honest about the guarantees, because the common assumption is wrong:

| Mechanism | Memory isolation | Fault isolation | Side-channel resistance |
|---|---|---|---|
| Whole GPU per pod | yes (it is the whole device) | yes | good |
| MIG | **yes, hardware** | **yes, hardware** | good |
| MPS | no | no | poor |
| Time-slicing | **no** ("no memory or fault-isolation between replicas" [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html)) | **no** | poor |

**Rule: across a trust boundary, whole GPU or MIG. Never MPS or time-slicing.** Two teams
sharing a time-sliced B300 means either can OOM the other, and a fault in either takes both
down.

Also: an engine pod does **not** need `privileged: true`. It needs the device (via the
device plugin or DRA/CDI), `/dev/infiniband` if it does RDMA, `IPC_LOCK` for memory pinning,
and enough `/dev/shm`. Grant those four; deny privileged.

### 7.3 Secrets — HF tokens and object-store credentials

- Gated models (Marlin-2B is gated in this repo) need a Hugging Face token. It belongs in a
  `Secret` mounted as a file, **not** an env var — env vars land in `kubectl describe`
  output, crash dumps and process listings.
- Better: **do not give the serving cluster a HF token at all.** A separate, isolated
  ingestion job fetches from HF and writes to the object store (§5.7); serving pods get a
  scoped, read-only object-store credential for their model prefix. The blast radius of a
  leaked serving credential is then "can read model weights they already have", not "can
  pull any gated model as us".
- Use short-lived credentials where the object store supports them (IRSA-style role
  assumption, or Vault/ESO with a short TTL). Avoid long-lived static keys in Secrets.
- Turn on **encryption at rest for etcd**; a Kubernetes Secret is base64, not encryption.

### 7.4 Image provenance

The engine images here are large, come from several registries (`nvcr.io`, `docker.io`,
vLLM's own), and run with device access. Minimum:

1. **Digest pinning**, never tags. `nvcr.io/nvidia/ai-dynamo/vllm-runtime@sha256:…`, not
   `:1.5.0`. This repo already records exact image tags for Dynamo/Kimi-K3
   ([`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)) — convert
   them to digests before production.
2. **A private mirror.** Do not pull from the public internet at scale-out time; it couples
   your capacity response to someone else's registry availability (and §5.6's P2P layer
   needs a local origin anyway).
3. **Signature verification** at admission (Sigstore/cosign via a policy controller), so an
   unsigned or unknown-publisher image cannot start on a GPU node.
4. **SBOM + scanning** in CI. Dragonfly, for example, "publish[es] SBOMs with all of our
   releases" [src](https://github.com/dragonflyoss/dragonfly) — expect the same of your own
   images.
5. **Weight provenance is separate from image provenance** and is often forgotten: §5.7's
   SHA-256 manifest plus the HF commit SHA is the model's supply chain. An image signature
   says nothing about the 1.5 TB of weights mounted into it.

**⚠️ TO BE VERIFIED:** no primary source was consulted in this pass for admission-controller
signature verification specifics; the recommendation is standard practice, the exact tool and
policy syntax should be pinned against the chosen controller's docs.

---

## 8. Reference bill of materials and cluster layout

Two sizes. Both serve the same five models; the 4-node is the smallest thing that can hold
all of them with redundancy, the 16-node is the shape where the P2P, quota and fabric
decisions above start paying for themselves.

### 8.1 Capacity arithmetic

One replica of each model, at the recommended B300 shapes from
[`matrix/fit-matrix.md`](../matrix/fit-matrix.md) / [`matrix/recommendations.md`](../matrix/recommendations.md):

| Model | GPUs/replica | Placement constraint |
|---|---:|---|
| Kimi-K3 | 8 | whole node |
| DeepSeek-V4.1-Flash | 4 (min 2) | NVLink-local |
| DeepSeek-V4.1-Flash-NVFP4 | 4 | NVLink-local |
| Qwen3.8-27B | 1 | any |
| Marlin-2B | 1 | any |
| **Total, one replica each** | **18 GPUs** | — |

→ **18 GPUs = 2.25 nodes minimum.** With N+1 for the whole-node model and room for a second
replica of the interactive models, **4 nodes (32 GPUs)** is the floor for a production
deployment that can lose a node.

### 8.2 4-node cluster

| Item | Qty | Spec | Notes |
|---|---:|---|---|
| **GPU nodes** | 4 | 8×B300 HGX, ≥96 CPU cores, 2 TB DRAM, 4×7.68 TB NVMe RAID0, 8×ConnectX-8, 1×BlueField-3 | §1.1; NVMe per §5.4 |
| **Control plane** | 3 | 1U, 16 cores, 64 GB, 2×1.92 TB NVMe (etcd) | RKE2 HA, no GPUs |
| **Compute fabric** | 2 | Spectrum-4 SN5600D 800 GbE **or** Quantum-X800 Q3400 | **RoCEv2 is defensible at this size** (§1.3) |
| **Storage/management** | 2 | 100/200 GbE ToR | in-band mgmt |
| **OOB management** | 1 | SN2201-class 48×1 GbE | BMC network |
| **Object store** | — | existing S3, or 3-node MinIO/Ceph, ≥100 TB usable | source of truth (§1.8 T3) |
| **Parallel FS** | — | **not required** | local NVMe + object store is enough at 4 nodes |
| **Power** | — | ≥ 60 kW, 1 rack | ~56 kW compute (§1.9) |
| **Cooling** | — | air + rear-door heat exchanger | at 4 nodes/rack this is the density limit |

**GPU allocation:**

| Nodes | Role | Taint |
|---|---|---|
| node-01 | Kimi-K3, 8 GPUs, one replica | `whole-node-model=kimik3:NoSchedule` |
| node-02 | DeepSeek-V4.1-Flash TP4 ×2 | — |
| node-03 | DeepSeek-NVFP4 TP4 + Qwen3.8-27B ×4 | — |
| node-04 | Marlin-2B ×4 + Qwen ×2 + spare/canary | — |

No Dragonfly (§5.6), no Volcano (§3.8 — every replica is a single pod), no SR-IOV (§4.3 —
`hostNetwork` on a single-tenant cluster). **Skipped deliberately; add when §8.3's
conditions appear.**

### 8.3 16-node cluster

| Item | Qty | Spec | Delta vs 4-node |
|---|---:|---|---|
| **GPU nodes** | 16 | as above | ×4 |
| **Control plane** | 3 | 1U, 32 cores, 128 GB | bigger etcd |
| **Compute fabric leaf** | 4 | **Quantum-X800 Q3400** (144×XDR 800G) | **InfiniBand now, not RoCE** (§1.3) |
| **Compute fabric spine** | 2 | Q3400 | rail-optimized, non-blocking (§1.4) |
| **UFM** | 1 | UFM 3.5 Enterprise appliance | subnet manager, per the B300 SuperPOD RA |
| **Storage fabric** | 2 | Quantum QM9700 NDR 400G **or** SN5610 800 GbE | RA storage-fabric options |
| **In-band mgmt** | 2 | SN5610/SN5600D | "200 Gbps… bonded for resiliency" per RA |
| **OOB mgmt** | 2 | SN2201 | — |
| **Parallel FS** | 1 | Weka / VAST / Lustre, ≥ 500 TB, GDS-capable | **now worth it** (§1.8) |
| **Object store** | — | ≥ 500 TB | more model versions |
| **Dragonfly** | — | Manager + Scheduler + 4 seed peers (1/rack) | §5.6 — 16 × 1.56 TB per rollout |
| **Power** | — | ≥ 240 kW across 4 racks | ~224 kW compute (§1.9) |
| **Cooling** | — | RDHX per rack, or liquid | 4 nodes/rack ceiling |

Switch/appliance model names from the B300 SuperPOD RA
[src](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html).
Note this is well below the RA's Scalable Unit of 72 DGX B300 systems — at 16 nodes you are
building a fraction of one SU, so take the topology pattern, not the port counts.

**What gets added at 16 nodes and why:**

| Addition | Trigger |
|---|---|
| Volcano | PD-disaggregated DeepSeek deployments = multi-pod replicas → gang scheduling required (§3.8) |
| Kueue | more than one team competing for capacity → admission quota (§3.8) |
| Dragonfly | 25 TB of correlated pull per rollout (§5.6) |
| Parallel FS | KV-offload tier and shared scratch beyond what local NVMe covers (§1.8) |
| InfiniBand | multi-node KV traffic tail latency (§1.3) |
| NVSentinel/NPD + remediator | 128 GPUs → a bad GPU every week is a statistical certainty (§6.5) |

### 8.4 Pinned software versions, 2026-09-19

| Layer | Pin | Source |
|---|---|---|
| Kubernetes | **1.36.4** (released 2026-08-11, EOL 2027-06-28) | [kubernetes.io/releases](https://kubernetes.io/releases/) — and **not 1.37**, per §3.1's operator intersection |
| Distribution | RKE2 (current stable for 1.36) | [docs.rke2.io](https://docs.rke2.io/add-ons/gpu_operators) |
| Host OS | Ubuntu 24.04 LTS | on all three operators' support lists |
| NVIDIA driver | **595.91.07** | GPU Operator 26.7.0 default [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) |
| Fabric Manager | **595.91.07** (must equal driver) | [FM guide](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html) |
| CUDA | 13.x | [R580 notes](https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-178-04/index.html), [`gpus/b300.md`](../gpus/b300.md) |
| containerd | 2.0–2.3 | [GPU Op platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) |
| GPU Operator | **v26.7.0** | ibid. |
| NVIDIA Container Toolkit | 1.20.0 | ibid. |
| NVIDIA Device Plugin | 0.20.0 | ibid. |
| Node Feature Discovery | v0.19.0 | ibid. |
| DCGM Exporter | v4.8.3 (top of the 4.6.0–4.8.3 supported range) | ibid. |
| Network Operator | **v26.7.0** | [Net Op platform support](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html) |
| DOCA-OFED | `doca3.5.0-26.07-0.7.7.0-0` (GA) | ibid. |
| NCCL | 2.31.x | [NCCL env docs](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html) |
| CNI | Cilium, native routing | §4.2 |
| Kueue *(16-node only)* | ≥ v0.14 (TAS beta, default-on) | [Kueue TAS](https://github.com/kubernetes-sigs/kueue/blob/main/site/content/en/docs/concepts/topology_aware_scheduling.md) |
| Volcano *(16-node only)* | v1.15.0 | [volcano.sh](https://volcano.sh/en/docs/schduler_introduction/) |
| Dragonfly *(16-node only)* | current CNCF Graduated release | [github.com/dragonflyoss/dragonfly](https://github.com/dragonflyoss/dragonfly) |
| vLLM | **0.29.0** (2026-09-09) — **`main`/nightly for DeepSeek-V4.1-Flash** (`min_vllm_version: 0.30.0`, `nightly_required: true`) | [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) |
| SGLang | 0.5.20 | ibid. |
| TensorRT-LLM | 1.2.1 stable / 1.3.0rc27 | ibid. |
| Dynamo | 1.5 (NIXL v1.3.1 / v1.3.0) | ibid. |
| InstantTensor | current PyPI release | [github.com/scitix/InstantTensor](https://github.com/scitix/InstantTensor) |

**Node conformance check** — assert before a node joins the serving pool, fail closed:

```bash
nvidia-smi --query-gpu=count,name,memory.total,vbios_version --format=csv   # 8 × B300, 268 GB-class
systemctl is-active nvidia-fabricmanager nvidia-persistenced                # both active
nvidia-smi -q | grep -A2 "Fabric"                                           # fabric state = Completed
dcgmi diag -r 3                                                             # full pass, never on a serving node
ibstat | grep -E "State|Rate"                                               # 8 ports Active, full rate
ip link show | grep -c "mtu 9000"                                           # MTU asserted, not assumed
mdadm --detail /dev/md0 | grep "State :"                                    # RAID0 clean
fio --name=seq --rw=read --bs=1M --numjobs=8 --size=10G --direct=1 \
    --filename=/var/lib/models/.fiotest                                     # ≥ 5 GB/s (§5.3 threshold)
mlxfwmanager --query                                                        # NIC firmware matches manifest
```

The `fio` line is the one people skip and should not: InstantTensor's own guidance keys off
"High storage bandwidth (>= 5 GB/s)" [src](https://github.com/scitix/InstantTensor), and a
node that silently dropped to single-drive performance will pass every other check while
turning a 35-second Kimi-K3 load into a 22-minute one (§5.5).

---

## Open questions

Consolidated ⚠️ items from every section above. Each states the estimation method used in
place of a source.

1. **B300 HBM per GPU: 288 GB (NVIDIA HGX AI Factory RA) vs 268 GB (METHODOLOGY §8 pin).**
   §1.1. Not a new disagreement — [`gpus/b300.md` §2](../gpus/b300.md) resolves it to the
   as-deployed 268 GB and records the unexplained 288→279→268→262.5 reconciliation as open.
   Noted here because the RA is a *primary NVIDIA* source printing 288, and anyone reading
   the RA alongside this tree will hit it.
2. **`NCCL_CROSS_NIC` on this fabric.** §1.4. No measurement exists here of default (2) vs
   0 on a rail-optimized XDR fabric. Method: A/B a multi-node all-reduce at message sizes
   matching this repo's KV-transfer sizes (§4.6's table), not at 1 GB.
3. **Magnitude of NCCL topology mis-detection in containers.** §1.6. The mechanism
   (`NCCL_TOPO_FILE` / `nvidia-topologyd`) is documented; the slowdown when it is missing is
   not. Method: run `nccl-tests` in a pod with and without the topology file mounted.
4. **B300 power-capping vs throughput curve.** §1.9. No published data. Method: sweep
   `nvidia-smi -pl` from 1,100 W downward in 100 W steps and measure prefill and decode
   throughput *separately* — they are bound by different resources
   ([METHODOLOGY §4](../METHODOLOGY.md#4-throughput-and-latency-roofline)) and will not
   degrade together.
5. **HGX B300 firmware compatibility matrix.** §2.4. No consolidated NVIDIA matrix was
   retrievable on 2026-09-19. Method: take the chassis OEM's firmware bundle as authoritative
   and pin the bundle version in the node image manifest.
6. **NVIDIA DRA driver CNCF donation.** §3.7. Reported by multiple secondary sources
   (conference coverage) with no primary NVIDIA or CNCF announcement retrieved. Affects
   governance and long-term support expectations, not today's behaviour.
7. **NUMA misalignment cost ("30–50 %").** §3.8. Secondary source only. Method: pin/unpin
   the Topology Manager policy and measure TTFT and decode throughput for a single-GPU
   Qwen3.8-27B replica, where NUMA effects are not masked by NVLink.
8. **Service-mesh overhead on streaming LLM traffic.** §4.5. No measurement found. Method:
   measure TTFT and inter-token gaps through the mesh vs direct, at p50 and p99 — the mesh
   cost, if any, is a tail effect.
9. **Kimi-K3 end-to-end cold start on 8×B300.** §5.5. **The most consequential gap in this
   document**, because it is the floor for every predictive-scaling and scale-to-zero
   decision downstream. Weight-load time is estimable (35 s–22 min depending on loader);
   `torch.compile` + CUDA-graph capture + KV profiling for a 2.8 T MoE is not — the only
   published decomposition (arXiv:2606.07362) tops out around 20 B parameters. Method:
   instrument a real cold start with per-phase timers and publish it.
10. **InstantTensor's 45 GB/s on B300.** §5.3/§5.5. The 35/45 GB/s figures are
    vendor-published on H200. B300 has PCIe 6.x (128 GB/s host link) vs H200's PCIe 5.0, so
    the ceiling should be higher — but nothing is measured. Method: load DeepSeek-V4.1-Flash
    TP8 with `--load-format instanttensor` and time it against `prefetch`.
11. **The >50 GiB/rank mmap danger threshold.** §5.2. The bound is empirical and loose:
    ~37 GiB/rank is safe, ~102 GiB/rank hangs, both on one reporter's hardware with
    single-disk EXT4 [src](https://github.com/vllm-project/vllm/issues/40988). The threshold
    on 4-drive RAID0 XFS is unknown and probably higher. Method: bisect with
    DeepSeek-V4.1-Flash at TP8 (63.8 GB/rank) and TP4 (127.6 GB/rank).
12. **Dragonfly's August-2026 database-optional control plane.** §5.6. Reported via CNCF
    blog; not confirmed against the project's own release notes in this pass.
13. **DCGM field IDs 230 / 856 / 857.** §6.3. From secondary summaries. Method: confirm
    against [NVIDIA's Field Identifiers reference](https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html).
14. **The fatal-Xid list.** §6.3. NVIDIA's Xid catalog page returned only a table of contents
    on 2026-09-19, so no authoritative code list is quoted here. Do not hard-code one from
    secondary sources; read the catalog. Also note [DCGM issue #235](https://github.com/NVIDIA/DCGM/issues/235):
    some Xids are not exported as metrics at all, so `dmesg` scraping is required in addition.
15. **NVSentinel version and production readiness.** §6.5. Its docs page publishes no version
    number. Method: check the project's releases before pinning it in the 16-node BOM.
16. **InfiniBand error-counter names.** §6.4. Standard IB performance counters, not confirmed
    against a primary spec page here. Method: `perfquery -x` on the deployed fabric.
17. **Image-signature admission policy.** §7.4. Standard practice; no primary source
    consulted. Method: pin syntax against the chosen policy controller's docs.

*Added by the 2026-09-19 adversarial fact-check:*

18. **What replaces SXid reporting on fourth-generation NVSwitch.** §2.2/§6.3. NVIDIA's
    Fabric Manager guide states *"NVSwitch Driver SXid fatal and non-fatal based error
    reporting does not apply on DGX B200/B300 and NVIDIA HGX B200/B300 systems"* and scopes
    the reset-and-restart recovery flow to earlier generations — but this pass did not
    retrieve the replacement mechanism. **This supersedes the framing in earlier drafts of
    §2.2 and is the most consequential correction in this document.** Method: read the FM
    guide's fourth-generation NVSwitch sections in full before writing any FM remediation.
19. **DCGM field id 857 / `DCGM_FI_DEV_SXID_NON_FATAL_ERROR`.** §6.3. 230, 782, 783 and 856
    are now confirmed against NVIDIA's Field Identifiers page; 857 was not found there.
    Method: grep `dcgm_fields.h` in the DCGM source tree.
20. **The 26.4 GB/s loader column.** §5.5. Attributed in earlier drafts to a Run:ai Model
    Streamer benchmark that does not exist on the vLLM page and whose PDF 404s. It is an
    interpolation. Method: find the Run:ai benchmark report, or drop the column.
21. **Volcano v1.15.0.** §3.8/§8.4. The docs page serves as "latest" with no version printed.
    Method: read the GitHub releases page.
22. **CoreWeave SUNK citations.** §3.3. `docs.coreweave.com/products/sunk` returned HTTP 500
    on 2026-09-19; none of the architecture quotes could be re-opened. No BOM depends on it.
23. **"Twin-plane" in the B300 SuperPOD topology.** §1.4. The B300-XDR RA components page
    says "rail-optimized, full-fat tree topology"; the twin-plane wording was not found on
    the components or architecture pages. Method: check the RA revision you build to.
24. **Binary vs decimal K in §4.6's KV-transfer table.** Recut to 2¹⁰ tokens on 2026-09-19.
    If a downstream document reuses the old 3.56 / 28.5 / 114 MB figures, it is on the
    decimal basis and should be recut.

---

## Sources

**NVIDIA hardware and reference architectures**
- [DGX SuperPOD with DGX B300, Quantum-X800 XDR — Key Components](https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html) — SU = 72 DGX B300, **"rail-optimized, full-fat tree topology"** (*corrected 2026-09-19 from "twin-plane"*), Q3400-RA, QM9700, SN5610/SN5600D, SN2201, UFM 3.5, 4 systems/rack
- [NVIDIA HGX AI Factory Enterprise RA — Components](https://docs.nvidia.com/enterprise-reference-architectures/hgx-ai-factory/latest/components.html) — 8×ConnectX-8 per baseboard, BlueField-3 B3240, ≥48 cores/socket, ≥2 TB DRAM, ≥2 TB NVMe/socket; also the 288 GB/2.30 TB figures noted as disagreeing with METHODOLOGY §8
- [NVIDIA HGX platform](https://www.nvidia.com/en-us/data-center/hgx/)
- [Lenovo NVIDIA GB300 NVL72 product guide](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) — "1100W total graphics power per GPU"

**Drivers, CUDA, Fabric Manager**
- [NVIDIA Data Center GPU Driver documentation index](https://docs.nvidia.com/datacenter/tesla) — branch/version list on 2026-09-19
- [Driver R580 580.178.04 release notes](https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-178-04/index.html) — 08/03/2026, CUDA 13.x, HGX B300 / GB300 NVL72 support, Xid 32 fix
- [Fabric Manager User Guide](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html) — NVSwitch generations incl. HGX B200/B300, `nvidia-fabricmanager`, `fabricmanager.cfg`, FABRIC_MODE, fatal/non-fatal recovery
- [NCCL Environment Variables (2.31.2)](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html) — `NCCL_IB_HCA`, `NCCL_SOCKET_IFNAME`, `NCCL_CROSS_NIC`, `NCCL_IB_GID_INDEX`, `NCCL_IB_TC`, `NCCL_NET_GDR_LEVEL`, `NCCL_P2P_LEVEL`, `NCCL_TOPO_FILE`

**Kubernetes GPU stack**
- [GPU Operator platform support](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html) — v26.7.x, K8s 1.33–1.37, containerd 2.0–2.3, driver 595.91.07, toolkit 1.20.0, device plugin 0.20.0, DCGM exporter 4.6.0–4.8.3, NFD v0.19.0, Blackwell platform list
- [GPU Operator — GPUDirect RDMA and GPUDirect Storage](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html) — helm flags, DMA-BUF vs nvidia-peermem prerequisite table
- [GPU Operator — GPU sharing / time-slicing](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html) — ConfigMap YAML, "no memory or fault-isolation between replicas"
- [GPU Operator — DRA driver install](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html) — K8s v1.34.2+, driver 580+, CDI requirement, auto-created DeviceClasses
- [NVIDIA Network Operator v26.7.0 platform support](https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html) — K8s ">=1.32 and <=1.36", ConnectX-7/8, DOCA-OFED versions, component list
- [Mellanox/network-operator (GitHub)](https://github.com/Mellanox/network-operator) — RDMA shared device plugin, SR-IOV device plugin, Multus, networking models
- [AMD GPU Operator](https://instinct.docs.amd.com/projects/gpu-operator/en/latest/index.html) — MI355X support, K8s 1.29–1.36, components
- [RKE2 GPU Operators add-on](https://docs.rke2.io/add-ons/gpu_operators)
- [Talos — NVIDIA GPU (proprietary drivers)](https://www.talos.dev/v1.7/talos-guides/configuration/nvidia-gpu-proprietary/)

**Kubernetes core and scheduling**
- [Kubernetes releases](https://kubernetes.io/releases/) — 1.37.0 (2026-08-26), 1.36.4, 1.35.8 and EOL dates
- [Kubernetes v1.37: DRA Updates](https://kubernetes.io/blog/2026/09/03/kubernetes-v1-37-dra-updates/) — DRA extended-resource GA
- [Device Taints and Tolerations](https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/) — `DeviceTaintRule` YAML, `DRADeviceTaints`/`DRADeviceTaintRules` stable in 1.37
- [Kueue — Topology-Aware Scheduling](https://github.com/kubernetes-sigs/kueue/blob/main/site/content/en/docs/concepts/topology_aware_scheduling.md) — beta since v0.14, ResourceFlavor/annotations YAML, capacity model
- [Kueue — Setup TAS](https://github.com/kubernetes-sigs/kueue/blob/main/site/content/en/docs/tasks/manage/setup_topology_aware_scheduling.md)
- [Volcano scheduler introduction](https://volcano.sh/en/docs/schduler_introduction/) — gang plugin, v1.15.0
- [Volcano network-topology-aware scheduling](https://volcano.sh/en/docs/network_topology_aware_scheduling/) — HyperNode, `networkTopology.mode`, `highestTierAllowed`
- [CoreWeave SUNK docs](https://docs.coreweave.com/products/sunk) — architecture, NodeSets, SUNK Pod Scheduler, Syncer
- [CoreWeave SUNK blog](https://www.coreweave.com/blog/sunk-slurm-on-kubernetes-implementations) — 100k+ GPUs, 32k-GPU jobs (vendor-claimed)

**Storage and weight loading**
- [GPUDirect Storage Overview Guide](https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html) — definition, supported filesystems, cuFile, `gdscheck -p`, CUDA 12.8 NVMe change
- [vLLM — Run:ai Model Streamer](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/) — `--load-format runai_streamer`, S3, concurrency/memory_limit/distributed, sharded pattern
- [vLLM — InstantTensor](https://docs.vllm.ai/en/latest/models/extensions/instanttensor/) — `--load-format instanttensor`
- [scitix/InstantTensor](https://github.com/scitix/InstantTensor) — benchmark table (32.4× / 10.5×, 35/45 GB/s), backends, kernel requirements, "when to use"
- [vLLM engine args](https://docs.vllm.ai/en/stable/configuration/engine_args/) — `--load-format` choices, `--safetensors-load-strategy` semantics, `--safetensors-prefetch-num-threads` default 8
- [vLLM issue #40988](https://github.com/vllm-project/vllm/issues/40988) — 805 GiB / 102 GiB-per-rank mmap stall >60 min, `prefetch` → ~12 min, V4-Flash 37 GiB/rank loads in ~290 s, InstantTensor ~24 s
- ["Breaking the Ice: Analyzing Cold Start Latency in vLLM"](https://arxiv.org/abs/2606.07362) — Kabakibo, Trivedi, Wang; arXiv:2606.07362, 2026-06-05 (v3 2026-06-29); six startup steps, predominantly CPU-bound. **H100/L40S and the ~20 B ceiling are not in the abstract — ⚠️, see §5.5**
- [dragonflyoss/dragonfly](https://github.com/dragonflyoss/dragonfly) — CNCF Graduated, P2P for images/artifacts/AI models, SBOMs, Trail of Bits audit

**Disaggregation / KV transfer**
- [vLLM NixlConnector usage guide](https://docs.vllm.ai/en/stable/features/nixl_connector_usage/) — `--kv-transfer-config`, side-channel host/port, `UCX_TLS`/`UCX_NET_DEVICES`, extra config
- [NVIDIA Dynamo — KV Cache Transfer in Disaggregated Serving](https://docs.nvidia.com/dynamo/archive/0.7.1/backends/trtllm/kv-cache-transfer.html) — NIXL definition, UCX transport

**Observability**
- [DCGM Exporter (GPU Telemetry docs)](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html) — daemonset deployment, `-f` counters CSV, `DCGM_EXPORTER_CONFIGMAP_DATA`, `DCGM_EXPORTER_KUBERNETES`, `DCGM_FI_PROF_*` fields
- [DCGM Field Identifiers](https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html) — authority to confirm field IDs against (open question 13)
- [NVIDIA Xid Errors guide](https://docs.nvidia.com/deploy/xid-errors/index.html) — catalog (body not retrievable 2026-09-19; open question 14)
- [NVIDIA/DCGM issue #235](https://github.com/NVIDIA/DCGM/issues/235) — some Xids not exported via `DCGM_FI_DEV_XID_ERRORS`
- [NVSentinel GPU Health Monitor](https://docs.nvidia.com/nvsentinel/configuration/gpu-health-monitor/) — modes, helm values, `GpuPowerWatch` Xid 54/56/58/78, `GpuThermalWatch` Xid 61, `RebootNode` / `RESTART_BM`

**This repo (linked, not re-derived)**
- [`METHODOLOGY.md`](../METHODOLOGY.md) — formulas, §8 pinned GPU/model inputs
- [`README.md`](../README.md) — tree index and the 14 open questions
- [`gpus/b300.md`](../gpus/b300.md) — B300 memory, NVLink, power (1,100 W resolution), software minimums
- [`gpus/gb300.md`](../gpus/gb300.md) · [`gpus/mi355x.md`](../gpus/mi355x.md)
- [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) — engine version pins, Dynamo/NIXL image tags
- [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — prefix caching, speculative decoding, disaggregation
- [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) — which format executes on which silicon
- [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) · [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) · [`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md) · [`matrix/recommendations.md`](../matrix/recommendations.md)
</content>
</invoke>

---

## Verification log (2026-09-19)

Adversarial re-check of the 25 most consequential claims in this document: every citation was
re-opened at source rather than trusted, every derivation was recomputed with `python3`, and
every `research/` cross-reference was opened and the number looked for in the target file.
**42 claims checked → 27 CONFIRMED, 9 CORRECTED, 6 UNVERIFIABLE.**

### Version numbers and feature-support statements

| # | Claim (§) | Verdict | Source opened |
|---:|---|---|---|
| 1 | GPU Operator 26.7.x: driver **595.91.07** default, Container Toolkit **1.20.0**, Device Plugin **0.20.0**, DCGM Exporter **v4.6.0–4.8.3**, NFD **v0.19.0**, K8s **1.33–1.37**, containerd **2.0–2.3**, Ubuntu 26.04/24.04/22.04, **HGX B300** listed (§2.1, §3.5, §8.4) | **CONFIRMED** — every field verbatim | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/platform-support.html |
| 2 | Network Operator **v26.7.0**: K8s `">=1.32 and <=1.36"`, DOCA-OFED `doca3.5.0-26.07-0.7.7.0-0` (GA) / `doca3.2.2-25.10-2.4.1.0-4` (LTS), CX-7 400 Gb/s, CX-8 SuperNIC 800 Gb/s (§2.1, §3.6, §8.4) | **CONFIRMED** (page also lists a ConnectX-9 SuperNIC at 800 Gb/s) | https://docs.nvidia.com/networking/display/kubernetes2670/platform-support.html |
| 3 | Operator intersection = **1.33–1.36**; pin **1.36.4** (released 2026-08-11, EOL 2027-06-28); 1.37.0 shipped 2026-08-26 (§3.1, §8.4) | **CONFIRMED** (1.37 EOL 2027-10-28, not previously printed) | https://kubernetes.io/releases/ |
| 4 | AMD GPU Operator: **MI355X** supported, K8s **1.29–1.36**, Ubuntu 22.04/24.04, Debian 12, SLES 15/16, OpenShift 4.16–4.22, Helm v3.2.0+ (§2.6) | **CONFIRMED** verbatim | https://instinct.docs.amd.com/projects/gpu-operator/en/latest/index.html |
| 5 | NVIDIA DRA driver prerequisites: "Kubernetes v1.34.2 or later", "NVIDIA GPU driver version 580 or later", CDI runtime, no existing `ClusterPolicy`; `clusterPolicy.deployCR=false` / `gpuCluster.deployCR=true` (§3.7) | **CONFIRMED**; DeviceClass list **CORRECTED** — 5 are created, not 3 (adds `compute-domain-daemon.nvidia.com`, `compute-domain-default-channel.nvidia.com` under ComputeDomain) | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/dra-intro-install.html |
| 6 | Device taints / `DeviceTaintRule` graduation timeline (§3.7) | **CORRECTED** — earlier draft said "beta in 1.36". Source: device taints **first available v1.33**, `DeviceTaintRule` **first available v1.35**, **both stable v1.37** with `DRADeviceTaints`/`DRADeviceTaintRules` locked on. The verbatim YAML and the "eviction can be delayed by tolerating a taint for a certain duration" quote are **CONFIRMED** | https://kubernetes.io/docs/concepts/resource-management/dynamic-resource-allocation/device-taints/ |
| 7 | DRA extended-resource support GA in 1.37, removing the need for a separate device plugin (§3.7) | **CONFIRMED** — "without requiring a separate device plugin alongside the DRA driver". The 1.34-GA / 1.35-gate-lock rows are **UNVERIFIABLE** here (page truncated on fetch); marked ⚠️ inline | https://kubernetes.io/blog/2026/09/03/kubernetes-v1-37-dra-updates/ |
| 8 | Driver branch landscape on 2026-09-19 (§2.1) | **CORRECTED** — **R590 (590.48.01)** was missing between R595 and R580; patch levels added for R575/R570/R550/R535 | https://docs.nvidia.com/datacenter/tesla/ |
| 9 | R580.178.04, Linux release 08/03/2026, "CUDA Toolkit 13: 13.x", and the Xid 32 / `CUDA_SCALE_LAUNCH_QUEUES` / chained-CUDA-graph-node fix (§2.1) | **CONFIRMED** verbatim | https://docs.nvidia.com/datacenter/tesla/tesla-release-notes-580-178-04/index.html |
| 10 | Kueue TAS **beta since v0.14, enabled by default**, gate `TopologyAwareScheduling`; ResourceFlavor YAML; the five podset annotations; "At most 3 layers"/`TASMultiLayerTopology`; the capacity model subtracting non-TAS Pods (§3.8) | **CONFIRMED** verbatim, all of it | https://raw.githubusercontent.com/kubernetes-sigs/kueue/main/site/content/en/docs/concepts/topology_aware_scheduling.md |
| 11 | Volcano gang-plugin quote about non-`Ready` tasks; page updated 2026-05-26 (§3.8) | **CONFIRMED** (quote); **UNVERIFIABLE** (the **v1.15.0** pin — no version printed on the page) | https://volcano.sh/en/docs/schduler_introduction/ |
| 12 | Engine pins: vLLM **0.29.0** (2026-09-09) with DeepSeek-V4.1-Flash needing `min_vllm_version: 0.30.0` + `nightly_required: true`; SGLang **0.5.20**; TRT-LLM **1.2.1** / **1.3.0rc27**; Dynamo **1.5** with NIXL **v1.3.1** (vLLM v0.28.0) / **v1.3.0** (SGLang v0.5.17) (§4.6, §8.4) | **CONFIRMED** — all six values present in the cross-referenced file | `research/cross-cutting/inference-engines.md` (lines 21–23, 632) |

### Configuration flags and YAML fields

| # | Claim (§) | Verdict | Source opened |
|---:|---|---|---|
| 13 | `NCCL_CROSS_NIC` **default 2**, with the 0/1/2 semantics quoted; `NCCL_TOPO_FILE` default `/var/run/nvidia-topologyd/virtualTopology.xml`; `NCCL_IB_GID_INDEX` default −1; `NCCL_IB_TC` default 0; `NCCL_NET_GDR_LEVEL` = LOC/PIX/PXB/PHB/SYS; `NCCL_IB_HCA` `<hca>[:<port>[:<rail>[:<plane>]]]`; NCCL **2.31.2** (§1.4, §1.6) | **CONFIRMED** — every value and quote verbatim | https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html |
| 14 | `--load-format` choices (12, incl. `instanttensor`, `modelexpress`); `--safetensors-load-strategy` None/lazy/eager/prefetch/torchao semantics incl. the **NFS + 90 %-of-RAM** auto-prefetch heuristic; `--safetensors-prefetch-num-threads` **default 8** (§5.2, §5.3) | **CONFIRMED** verbatim | https://docs.vllm.ai/en/stable/configuration/engine_args/ |
| 15 | NixlConnector: `VLLM_NIXL_SIDE_CHANNEL_PORT` **5600**, `VLLM_NIXL_SIDE_CHANNEL_HOST` `localhost`, `UCX_TLS`/`UCX_NET_DEVICES` examples, `UCX_CUDA_IPC_ENABLE_MNNVL: 'y'`, `kv_lease_duration` **30**, `decoder_kv_blocks_ttl` **480**, `bidirectional_kv_xfer`, the producer/consumer CLI (§4.6) | **CONFIRMED** (the page also documents `kv_recompute_threshold` default 64; `kv_load_failure_policy: "fail"` was not surfaced in the retrieved portion — retained as-is) | https://docs.vllm.ai/en/stable/features/nixl_connector_usage/ |
| 16 | Run:ai Model Streamer flags: `s3://` serve command, `concurrency: 16`, `memory_limit: 5368709120`, `distributed: true`, the sharded `pattern`, and the three S3-compatible env vars (§5.3) | **CONFIRMED** verbatim | https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/ |
| 17 | GPU Operator RDMA: DMA-BUF needs open kernel module / **CUDA 11.7+** / **kernel 5.12+** and makes OFED *optional*, `nvidia-peermem` requires OFED; helm `--version=v26.7.0`; `driver.rdma.enabled`, `driver.rdma.useHostMofed`, `driver.kernelModuleType=open` (§1.5, §3.5) | **CONFIRMED** — the whole prerequisite table verbatim | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html |
| 18 | Time-slicing: "Unlike Multi-Instance GPU (MIG), there is no memory or fault-isolation between replicas"; the trade-off sentence; the `time-slicing-config` ConfigMap YAML; DCGM-Exporter cannot attribute metrics per container; the Operator does not watch the ConfigMap (§2.5, §7.2) | **CONFIRMED** verbatim (remediation is `kubectl rollout restart` of the device-plugin pods) | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html |
| 19 | Fabric Manager: role, `/usr/share/nvidia/nvswitch/fabricmanager.cfg`, `FABRIC_MODE` 0/1/2, `LOG_LEVEL` 0–4, HGX B200/B300 on **fourth-generation NVSwitch** (§2.2) | **CONFIRMED**, and **CORRECTED** on scope: the same guide says the SXid recovery flow applies to *pre*-fourth-generation systems and that *"NVSwitch Driver SXid fatal and non-fatal based error reporting does not apply on DGX B200/B300 and NVIDIA HGX B200/B300 systems"*. `LOG_LEVEL` default 4 and `FM_CMD_PORT_NUMBER` default **6666** added | https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html |
| 20 | DCGM Exporter: daemonset deployment, `-f` default `/etc/dcgm-exporter/default-counters.csv`, `DCGM_EXPORTER_CONFIGMAP_DATA`, `DCGM_EXPORTER_KUBERNETES` default false, the `DCGM_FI_PROF_*` field names (§6.2) | **CONFIRMED** verbatim | https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/dcgm-exporter.html |
| 21 | DCGM field **ids** 230 / 856 / 857 and their names (§6.3) | **CORRECTED** — 230 is `DCGM_FI_DEV_XID_ERROR`; **856 is `DCGM_FI_DEV_SXID_FATAL_ERROR`**, not `DCGM_FI_DEV_NVSWITCH_FATAL_ERRORS`; **no `DCGM_FI_DEV_NVSWITCH_NON_FATAL_ERRORS` field exists** — the per-port pair is **782 / 783** (`..._NVSWITCH_LINK_{,NON_}FATAL_ERRORS`). Id **857 UNVERIFIABLE** | https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html |
| 22 | NVSentinel: DCGM-based health monitor, modes `operator-service` (default) / `external-hostengine` / `embedded-mode`, `global.gpuHealthMonitor.enabled: true`, **GpuPowerWatch → Xid 54/56/58/78**, **GpuThermalWatch → Xid 61**, `RebootNode` CRs, **no version published** (§6.5) | **CONFIRMED** on every point, including the absence of a version. Nuance: those watches *suppress* the clocks-event alerts (codes 12 / 10) while still reporting the Xids; mode is selected via `global.dcgm.mode` | https://docs.nvidia.com/nvsentinel/configuration/gpu-health-monitor/ |

### Measured numbers from papers, issues and vendor pages

| # | Claim (§) | Verdict | Source opened |
|---:|---|---|---|
| 23 | vLLM issue **#40988**: filed 2026-04-27, DeepSeek-V4-Pro, **805 GiB** checkpoint, **~102 GiB/rank**, "3 of 8 workers stall in `safetensors._safetensors_rust.safe_open` random reads for >60 min", ~30–50 MB/s random read, `prefetch` → **~12 min**, V4-Flash at **~37 GiB/rank loads in ~290 s**, InstantTensor **~24 s (~12×)**, **single-disk EXT4** local NVMe (§5.2, §5.3, §5.4) | **CONFIRMED** — the issue exists and every quoted figure matches | https://github.com/vllm-project/vllm/issues/40988 |
| 24 | InstantTensor benchmark: Qwen3-30B-A3B 1×H200 **57.4 s / 1.1 GB/s → 1.77 s / 35 GB/s (32.4×)**; DeepSeek-R1 8×H200 **160 s / 4.3 GB/s → 15.3 s / 45 GB/s (10.5×)**; kernel ≥5.6 (≥5.15 rec.); backends AIO/AIO_BUFFERED/URING/URING_BUFFERED/CUFILE/MMAP; the "when to use" list (§5.3, §5.5, §8.4) | **CONFIRMED** cell-for-cell. Minor **CORRECTION**: the "when to use" list has a fourth entry, "Loading from tmpfs", omitted earlier | https://github.com/scitix/InstantTensor |
| 25 | **26.4 GB/s** attributed to "Run:ai Model Streamer's published Llama-70B/4-GPU GDS figure" (§5.5) | **UNVERIFIABLE → demoted to an interpolation.** vLLM's Run:ai page carries no benchmark figures at all, and the Run:ai benchmark PDF returned HTTP 404 | https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/ |
| 26 | arXiv:**2606.07362**, "Breaking the Ice: Analyzing Cold Start Latency in vLLM", Kabakibo / Trivedi / Wang, 2026-06-05, "six foundational steps", "predominantly CPU-bound" (§5.5) | **CONFIRMED** (paper exists; v3 2026-06-29). The **"H100 and L40S, models up to ~20 B" scoping is UNVERIFIABLE** — absent from the abstract and metadata; now flagged inline | https://arxiv.org/abs/2606.07362 |
| 27 | Dragonfly: **CNCF Graduated**, P2P for "files, container images, OCI artifacts, AI/ML models, caches, logs, dependencies", SBOMs with all releases, Trail of Bits audit, Manager/Scheduler/Seed Peer/`dfdaemon` (§5.6, §7.4) | **CONFIRMED** on every point (the Aug-2026 database-optional claim stays ⚠️ — still not checked against release notes) | https://github.com/dragonflyoss/dragonfly |
| 28 | Lenovo GB300 NVL72: **1,100 W per GPU**, **135 kW rack** (§1.9) | **CONFIRMED**, quote **CORRECTED** to "Total Graphics Power (TGP): 1100W"; rack figure refined to "135 kW TDP; up to 155 kW peak depending on workload and EDP behavior" | https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai |
| 29 | CoreWeave SUNK architecture quotes and the "100,000 GPUs / 32,000-GPU jobs" claim (§3.3) | **UNVERIFIABLE** — `docs.coreweave.com/products/sunk` returned **HTTP 500**; flagged inline. No BOM depends on it | https://docs.coreweave.com/products/sunk |

### Reference-architecture and hardware figures

| # | Claim (§) | Verdict | Source opened |
|---:|---|---|---|
| 30 | HGX AI Factory RA: "Eight NVIDIA® ConnectX-8 SuperNICs per NVIDIA HGX B300 baseboard. Up to 800 Gbps per adapter"; BlueField-3 **B3240**; "Minimum of 48 physical CPU cores per socket"; "Minimum of 2TB system memory"; "Minimum 2 TB NVMe drive per CPU socket"; and the disagreeing **288 GB / 2.30 TB** HBM figures (§1.1) | **CONFIRMED** verbatim, the 288/2.30 TB disagreement included. Added: 56 cores/socket recommended, ≥500 GB/s memory bandwidth, 1 TB NVMe boot drive | https://docs.nvidia.com/enterprise-reference-architectures/hgx-ai-factory/latest/components.html |
| 31 | B300 SuperPOD RA: SU = **72 DGX B300**, Q3400-RA, QM9700 NDR 400G, SN5610/SN5600D ("64 port 800 Gbps"), SN2201, **UFM 3.5 Appliance Enterprise Edition**, "up to four DGX B300 systems per rack" (§1.3, §1.4, §6.1, §8.3) | **CONFIRMED** on every component and count; topology string **CORRECTED** (row 32) | https://docs.nvidia.com/dgx-superpod/reference-architecture/scalable-infrastructure-b300-xdr/latest/dgx-superpod-components.html |
| 32 | Topology quoted as "Rail-optimized, non-blocking, twin-plane, fat tree topology" (§1.4) | **CORRECTED** to **"rail-optimized, full-fat tree topology"**; "twin-plane" is not on the components page and was not found on the architecture page either — now marked ⚠️ | same, plus `…/dgx-superpod-architecture.html` |
| 33 | GPUDirect Storage: the GDS definition, cuFile, `gdscheck -p`, the supported-FS list (Lustre / NFSoRDMA / WekaFS / DDN-EXAScaler / VAST-NFS / EXT4 / XFS), **GPFS not listed**, `nvidia-fs.ko` unnecessary for NVMe as of CUDA 12.8, "most apparent with small transfers" (§1.8, §5.4) | **CONFIRMED** on every point, including that IBM Storage Scale is absent | https://docs.nvidia.com/gpudirect-storage/overview-guide/index.html |
| 34 | Network Operator GitHub: "manages Networking related Components in order to enable Fast networking, RDMA and GPUDirect for workloads in a Kubernetes cluster" (§3.6, §4.3) | **CONFIRMED** (description). **CORRECTED/UNVERIFIABLE**: the *"RoCE shared mode, SR-IOV, and NIC PF passthrough"* quotation was not retrievable there; replaced with the `NICClusterPolicy` component list | https://github.com/Mellanox/network-operator |
| 35 | B300 node physicals as restated here: **268 GB/GPU, 2,144 GB/node, 8.0 TB/s, 64 TB/s/node, NVLink 1.8 TB/s per GPU / 14.4 TB/s per baseboard, PCIe 6.x @ 128 GB/s, ~14 kW/node, 1.1 kW/GPU**; NVL72 **130 TB/s, 288 GB (≈279 usable)** (§1.1, §1.7, §1.9) | **CONFIRMED** — every value present in the cross-referenced GPU doc and METHODOLOGY §8 | `research/gpus/b300.md` (lines 47–48, 91, 296–299), `research/METHODOLOGY.md` §8 |

### Recomputed derivations (`python3`)

| # | Derivation (§) | Verdict |
|---:|---|---|
| 36 | **Sum of one copy of each model** = 1,560.9 + 510.29 + 527.27 + 30.87 + 5.444 = **2,634.774 GB** (§0, §5.4) | **CORRECTED** — the GB sum is right; the GiB conversion was not. 2,634.774 × 10⁹ ÷ 2⁴⁰ = **2.396 TiB**, printed as **2.45 TiB** in §0 and §5.4. Both fixed to **2.40 TiB (2,453.8 GiB)** |
| 37 | **KV-transfer table** at 890 B/token (§4.6) | **CORRECTED twice.** (a) tokens were decimal K; recut to 4,096 / 32,768 / 131,072 → **3.65 / 29.2 / 116.7 MB** and 0.036 / 0.292 / 1.167 ms at 100 GB/s. (b) "one NIC-second carries **~8.8 requests**' worth of KV" at 128 K is wrong by **100×** — 100 GB/s ÷ 116.7 MB = **~857 requests/s**. The 1,650 ÷ 890 = **1.85×** FP8 scaling factor is **CONFIRMED** |
| 38 | **Per-rank shard sizes** (§5.2): Kimi-K3/8 = 195.1; V4.1-Flash /8 = 63.8, /4 = 127.6; NVFP4 /8–/4 = 65.9–131.8; Qwen 30.87; Marlin 5.4 GB | **CONFIRMED** — all five rows reproduce exactly |
| 39 | **Load-time table** (§5.5): all 25 cells = `checkpoint_bytes / bandwidth` | **CONFIRMED** — every cell within rounding (21.68→21.7 min, 34.69→35 s, 59.13→59 s, etc.). The "22-minute" figure in §8.4 matches the 1.2 GB/s Kimi-K3 cell |
| 40 | **Power arithmetic** (§1.9, §8.2, §8.3): 4 × 8 × 1.1 = **35.2 kW**; 16 × 8 × 1.1 = **140.8 kW**; 72 × 1.1 = **79.2 kW** vs the 135 kW rack | **CONFIRMED** |
| 41 | **Capacity arithmetic** (§8.1): 8 + 4 + 4 + 1 + 1 = **18 GPUs = 2.25 nodes**; 16 × 1.5609 TB = **25.0 TB** per rollout; 4 × 7.68 TB = **30.7 TB** RAID0; node-02/03/04 GPU allocations sum to ≤ 8 each | **CONFIRMED** |
| 42 | **The ~18× NVLink→IB per-GPU bandwidth cliff** (§1.2): 1.8 TB/s ÷ 0.1 TB/s | **CONFIRMED** as arithmetic (per-GPU NVLink vs one 800 Gb/s NIC). It remains `est.` — no measurement backs the 20–40 % comm-overhead band it is used to motivate |

### Cross-references into `research/` (each file opened, each number looked for)

| Claim | Verdict |
|---|---|
| Checkpoint sizes 1,560.9 / 510.29 / 527.27 / 30.87 / 5.444 GB (§0) | **CONFIRMED** against METHODOLOGY §8's model table |
| Min GPUs on B300: Kimi-K3 **8**, V4.1-Flash **2**, NVFP4 **4**, Qwen **1**, Marlin **1** (§0, §8.1) | **CONFIRMED** against `matrix/fit-matrix.md` §1 (rec 8 / 4 / 4 / 1 / 1) |
| Kimi-K3 at **195 GB/GPU** on 8×B300 (§0, §1.2, §5.2) | **CONFIRMED** — `fit-matrix.md` prints 195.1 GB (TP8) |
| Kimi-K3 parallel shape printed as "TP8 / EP8" (§1.2) | **CORRECTED** to **TP8 + DCP8**, which is what `fit-matrix.md` and `models/kimik3/b300.md` recommend |
| **890 B/token** KV on B300 is the sm_100/sm_103-gated FP4 kernel path; **1,650 B/token** FP8 elsewhere (§4.6) | **CONFIRMED** against METHODOLOGY §8's pin log (`C5-890b-fp4-kv-kernel-gate`) and `fit-matrix.md` §6.3 |
| DeepSeek-V4.1-Flash = 552 B backbone (§1.7) | **CONFIRMED** — METHODOLOGY §8 gives 552.4 B backbone of 763.2 B total |
| OCI B300 at **$15/GPU-hour** (§4.5) | **CONFIRMED** against METHODOLOGY §8 Prices and `matrix/fit-matrix.md` |
| "NVFP4/MXFP4 execute natively only on sm_100/sm_103 (disputedly sm_120), else Marlin W4A16" (§3.9) | **CONFIRMED** against METHODOLOGY §8's RTX PRO 6000 row and `cross-cutting/quantization-formats.md` §9.6 |
| "Kimi-K3, the largest, fits one node" (§1.3) | **CONFIRMED** — `fit-matrix.md`: 8×B300 is the only single node in the tree that runs all five |

### What this pass did **not** resolve

Every ⚠️ already in §Open questions 1–17 stands — none of them were re-litigated here, and six
new ones (18–24) were opened. The structural gaps a reader should know about:

- **§1.9's power-vs-throughput curve, §5.5's Kimi-K3 cold start and §3.8's NUMA "30–50 %"
  remain the three unmeasured numbers this document's downstream scaling decisions rest on.**
  Open question 9 correctly calls the cold start the most consequential; nothing found in this
  pass changes that.
- **§2.2/§6.3's NVSwitch error handling is now known to be written for the wrong NVSwitch
  generation** (open question 18). That is a correctness gap in the runbook, not a citation gap.
- The document's brief covers "how to host a model in a bare-metal cluster"; **autoscaling,
  concurrency handling, throughput optimization and cold-start prediction are explicitly out of
  its scope** and live in the sibling documents (`04-throughput-and-utilization.md`,
  `07-cost-engineering.md`, `09-reference-architectures.md`). Sections 2, 3, 5, 6 and 8 of the
  user's brief — autoscaling, request handling, inference engineering, latency and predictive
  scaling — have **no document in `research/scaling/` at all** as of 2026-09-19: the directory
  holds 01, 04, 07 and 09, with 02, 03, 05, 06 and 08 missing.
- **No contradiction with another `research/` document was found.** The one divergence (Kimi-K3
  "EP8" vs `fit-matrix.md`'s "DCP8") was a slip in this file and is corrected above.
