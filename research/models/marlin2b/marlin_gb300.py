#!/usr/bin/env python3
"""Marlin-2B on GB300 NVL72 - all derived numbers. METHODOLOGY.md SS1-S6."""

GiB = 2**30; GB = 10**9

# ---- model (models/marlin2b/architecture.md SS1.2, SS3.3, SS5.1, SS5.2, SS6.1, SS6.4) ----
P_UNIQUE      = 2_213_241_664
W_BF16        = P_UNIQUE * 2                 # 4,426,483,328 B resident, tie honoured
CKPT_DISK     = 5_443_677_224                # shards on disk (lm_head duplicated)
P_DECODE      = 1_881_825_088                # language layers + lm_head
W_READ_DEC    = P_DECODE * 2                 # BF16 bytes read per decode step
KV_BF16       = 12_288                       # B/token, 6 full-attn layers
KV_FP8        = 6_144
KV_FP4        = 3_072
GDN_STATE     = 19_537_920                   # B/seq, fp32 recurrent + bf16 conv, S=1
FLOP_TOK      = 2 * P_DECODE                 # 3.7637 GFLOP/token, linear part
ATTN_C        = 24_576                       # attention FLOPs = ATTN_C * T^2 (causal, 6 layers)
VIT_PER_FRAME = 272.2e9                      # FLOP/frame  (0.2722 TFLOP)
TOK_PER_FRAME = 98
SCAFFOLD      = 40

# ---- GPU: GB300 NVL72, per B300 (METHODOLOGY SS8 + gpus/gb300.md SS1.1, SS2.2, SS3) ----
HBM_DEPLOYED  = 279 * GB      # 288 GB nominal, ~279 usable - gpus/gb300.md plans with 279
USABLE        = HBM_DEPLOYED * 0.90
HBM_BW        = 8.0e12
BF16_DENSE    = 2.50e15
FP8_DENSE     = 5.00e15
NVFP4_DENSE   = 15.0e15
MBU           = 0.60          # METHODOLOGY SS4 Blackwell band 0.5-0.7; gb300.md SS9.4 defends 0.60-0.75
MFU           = 0.40          # BF16 prefill, METHODOLOGY SS4 band 0.35-0.50
ACT_WS        = 4 * GiB
NVLINK_GPUS   = 72

KV_BUDGET = USABLE - W_BF16 - ACT_WS

def fmt(x, n=1): return f"{x:,.{n}f}"
def maxconc(ctx, kv=KV_BF16, budget=KV_BUDGET): return int(budget // (ctx*kv + GDN_STATE))

print("="*100)
print("0. PINNED INPUTS")
print("="*100)
print(f"weights BF16 resident         {W_BF16:,} B = {W_BF16/GB:.3f} GB = {W_BF16/GiB:.3f} GiB")
print(f"checkpoint on disk            {CKPT_DISK:,} B = {CKPT_DISK/GB:.3f} GB")
print(f"decode weight read            {W_READ_DEC:,} B = {W_READ_DEC/GB:.3f} GB")
print(f"HBM as deployed               {HBM_DEPLOYED/GB:.0f} GB ; usable x0.90 = {USABLE/GB:.2f} GB = {USABLE/GiB:.2f} GiB")
print(f"KV budget per GPU             {KV_BUDGET/GB:.2f} GB = {KV_BUDGET/GiB:.2f} GiB   (usable - weights - {ACT_WS/GiB:.0f} GiB act ws)")
print(f"KV bytes/token                BF16 {KV_BF16:,}  FP8 {KV_FP8:,}  FP4 {KV_FP4:,}")
print(f"GDN fixed state / seq         {GDN_STATE:,} B = {GDN_STATE/2**20:.2f} MiB  (S=1)")

# ---------------- SS1 FIT ----------------
print()
print("="*100)
print("1a. FIT vs GPU COUNT  (TP1 replicas; weights REPLICATED on every GPU, not sharded)")
print("="*100)
hdr = f"{'GPUs':>5} {'shape':>16} {'W/GPU GB':>9} {'actws GiB':>10} {'KV/GPU GiB':>11} " \
      f"{'8K':>8} {'32K':>8} {'128K':>8} {'1M':>7} | {'8K f8':>8} {'32K f8':>8} {'128K f8':>8} {'1M f8':>7} {'fabric':>9}"
print(hdr); print("-"*len(hdr))
for n in [1,2,4,8,16,18,36,72]:
    shape = f"DP{n} x TP1"
    row = f"{n:>5} {shape:>16} {W_BF16/GB:>9.3f} {ACT_WS/GiB:>10.0f} {KV_BUDGET/GiB:>11.2f} "
    for kv in (KV_BF16, KV_FP8):
        cells = [maxconc(c, kv)*n for c in (8192, 32768, 131072, 1048576)]
        row += "".join(f"{c:>8,} " if kv==KV_BF16 else f"{c:>8,} " for c in cells)
        if kv==KV_BF16: row += "| "
    row += f"{'NVLink' if n<=NVLINK_GPUS else 'IB/rack':>9}"
    print(row)
print(f"\n(1M ctx is arithmetic only: max_position_embeddings = 262,144, rope_type 'default'.)")
print(f"Per-GPU max concurrency, BF16 KV:  8K {maxconc(8192):,} | 32K {maxconc(32768):,} | "
      f"128K {maxconc(131072):,} | 262K {maxconc(262144):,} | 1M {maxconc(1048576):,}")
print(f"Per-GPU max concurrency, FP8  KV:  8K {maxconc(8192,KV_FP8):,} | 32K {maxconc(32768,KV_FP8):,} | "
      f"128K {maxconc(131072,KV_FP8):,} | 262K {maxconc(262144,KV_FP8):,} | 1M {maxconc(1048576,KV_FP8):,}")
print(f"Video op-point 23,520 tok:  BF16 {maxconc(23520):,}/GPU  FP8 {maxconc(23520,KV_FP8):,}/GPU  "
      f"-> rack(72) {maxconc(23520)*72:,} / {maxconc(23520,KV_FP8)*72:,}")

print()
print("1b. IF YOU SHARDED IT ANYWAY (TP>1) - what each GPU holds")
print("-"*100)
print(f"{'TP':>3} {'W/GPU GB':>9} {'KV heads/GPU':>13} {'KV B/tok/GPU':>13} {'GDN state/GPU MiB':>18} {'verdict'}")
for tp in [1,2,4,8,16]:
    kvh = 2/tp if tp<=2 else 2          # vLLM replicates KV heads when tp > n_kv_heads
    kvb = KV_BF16/tp if tp<=2 else KV_BF16
    gdn = GDN_STATE/min(tp,16)/2**20
    v = "optimal" if tp==1 else ("KV shards" if tp<=2 else "KV heads REPLICATED - no KV win")
    print(f"{tp:>3} {W_BF16/GB/tp:>9.3f} {kvh:>13.2f} {kvb:>13,.0f} {gdn:>18.2f} {v}")

# ---------------- SS3 THROUGHPUT / LATENCY ----------------
def decode_step(batch, ctx, kv=KV_BF16, wread=W_READ_DEC, mbu=MBU, mfu=MFU, peak=BF16_DENSE):
    bw  = (wread + batch*ctx*kv) / (HBM_BW*mbu)
    cmp = 2*P_DECODE*batch / (peak*mfu)
    return max(bw, cmp), bw, cmp

def prefill_s(T_new, T_total, peak=BF16_DENSE, mfu=MFU, vit_frames=0):
    lin  = FLOP_TOK * T_new
    attn = 6 * 4 * 8 * 256 * T_new * (T_total - T_new/2)
    vit  = VIT_PER_FRAME * vit_frames
    return (lin + attn + vit) / (peak*mfu), (lin+attn)/(peak*mfu), vit/(peak*mfu)

SCEN = [("S1", 4096, 512), ("S2", 32768, 1024), ("S3", 131072, 2048)]
print()
print("="*100)
print(f"3a. DECODE ROOFLINE, per GB300 GPU, BF16 weights + BF16 KV, MBU={MBU}, MFU={MFU}")
print("="*100)
for sid, isl, osl in SCEN:
    ctx = isl + osl//2
    mc  = maxconc(ctx)
    print(f"\n{sid}: {isl//1024}K in / {osl} out   (mean ctx {ctx:,}; max_concurrency BF16 KV = {mc:,}, FP8 KV = {maxconc(ctx,KV_FP8):,})")
    print(f"{'batch':>6} {'TPOT ms':>9} {'out tok/s/GPU':>14} {'agg 72 GPU':>13} "
          f"{'TTFT ms 0%':>11} {'TTFT ms 90%':>12} {'bound':>7}")
    for b in [1,8,32,64,128,256]:
        if b > mc:
            print(f"{b:>6} {'infeasible (KV)':>60}"); continue
        st, bw, cm = decode_step(b, ctx)
        t0,_,_ = prefill_s(isl, isl)
        t9,_,_ = prefill_s(int(isl*0.10), isl)
        print(f"{b:>6} {st*1e3:>9.2f} {b/st:>14,.0f} {b/st*72:>13,.0f} "
              f"{t0*1e3 + st*1e3:>11.1f} {t9*1e3 + st*1e3:>12.1f} {'BW' if bw>=cm else 'FLOP':>7}")

print()
print("3b. SAME, FP8 KV  (weights stay BF16 - no FP8 checkpoint exists)")
print("-"*100)
for sid, isl, osl in SCEN:
    ctx = isl + osl//2; mc = maxconc(ctx, KV_FP8)
    line = f"{sid:>3} maxconc {mc:>6,} | "
    for b in [1,8,32,64,128,256]:
        if b > mc: line += f"b{b}: infeas  "; continue
        st,_,_ = decode_step(b, ctx, KV_FP8)
        line += f"b{b}: {st*1e3:.2f}ms/{b/st:,.0f} "
    print(line)

print()
print("3c. VIDEO OPERATING POINT - one 2-minute (or longer) clip, 240 frames, 23,560 prefill tokens")
print("-"*100)
tot, llm, vit = prefill_s(23560, 23560, vit_frames=240)
print(f"  ViT 240 frames        {240*VIT_PER_FRAME/1e12:>8.2f} TFLOP -> {vit*1e3:>7.1f} ms")
print(f"  LLM prefill 23,560 t  {(FLOP_TOK*23560 + 6*4*8*256*23560**2/2)/1e12:>8.2f} TFLOP -> {llm*1e3:>7.1f} ms")
print(f"  TOTAL prefill         {(240*VIT_PER_FRAME + FLOP_TOK*23560 + 6*4*8*256*23560**2/2)/1e12:>8.2f} TFLOP -> {tot*1e3:>7.1f} ms")
for b in [1,8,32,64,128,256,785]:
    if b > maxconc(23520): tag=" (> maxconc, infeasible KV)"; 
    else: tag=""
    st,_,_ = decode_step(b, 23520)
    print(f"  batch {b:>4}: TPOT {st*1e3:>6.2f} ms  decode-only {b/st:>9,.0f} tok/s/GPU  "
          f"768-tok caption {768*st:>6.2f} s  2048-tok {2048*st:>6.2f} s{tag}")

# ---------------- SUSTAINED (prefill + decode share one GPU) ----------------
def sustained(isl, osl, batch, vit_frames=0, kv=KV_BF16, peak=BF16_DENSE, mfu=MFU, mbu=MBU):
    """serial prefill+decode on one GPU, per METHODOLOGY SS6 / architecture.md SS11.2"""
    ctx = isl + osl//2
    pf,_,_ = prefill_s(isl, isl, peak, mfu, vit_frames)
    st,_,_ = decode_step(batch, ctx, kv, mbu=mbu, mfu=mfu, peak=peak)
    gpu_s_per_req = pf + osl*st/batch          # prefill serial + decode amortised over batch
    req_s   = 1.0/gpu_s_per_req
    return req_s, req_s*osl, req_s*isl, pf, st

print()
print("="*100)
print("3d. SUSTAINED end-to-end (prefill NOT free), per GPU")
print("="*100)
print(f"{'scenario':>28} {'batch':>6} {'req/s':>8} {'out tok/s':>10} {'in tok/s':>10} "
      f"{'prefill ms':>11} {'TPOT ms':>8} {'limiter':>9}")
rows={}
for sid, isl, osl, vf, lbl in [("S1",4096,512,0,"S1 4K/512"),("S2",32768,1024,0,"S2 32K/1K"),
                               ("S3",131072,2048,0,"S3 128K/2K"),("S4",4096,512,0,"S4 4K/512 batch"),
                               ("V",23560,768,240,"video 2min/768"),("V",23560,2048,240,"video 2min/2048")]:
    mc = maxconc(isl+osl//2)
    b = 256 if sid in ("S1","S2") else (128 if sid=="S3" else 256)
    if sid=="S4": b = min(1024, mc)
    if sid=="V":  b = min(256, mc)
    b = min(b, mc)
    r, ot, it, pf, st = sustained(isl, osl, b, vf)
    lim = "prefill" if pf > osl*st/b else "decode"
    print(f"{lbl:>28} {b:>6} {r:>8.2f} {ot:>10,.0f} {it:>10,.0f} {pf*1e3:>11.1f} {st*1e3:>8.2f} {lim:>9}")
    rows[lbl]=(b,r,ot,it,pf,st)

# ---------------- SS4 COST ----------------
# cloud-pricing.md SS5.9 + SS10 planning table: gb300 low = high = $18.00 (OCI BM.GPU.GB300.4);
# res1y none published -> use high. Non-rental anchors from gpus/gb300.md SS8.1/SS8.3.
PRICES = [("OCI on-demand (low = high)", 18.00), ("SemiAnalysis retail anchor", 5.00),
          ("colo-amortised (gb300.md 8.3)", 2.57), ("SemiAnalysis hyperscaler-volume", 2.31)]

print()
print("="*100)
print("4a. COST per 1M tokens, per METHODOLOGY SS6.  blended = 0.75*(0.5*in + 0.5*0.1*in) + 0.25*out")
print("="*100)
print(f"{'scenario':>28} {'$/GPU-h':>9} {'$/1M out':>10} {'$/1M in':>10} {'$/1M blended':>13}")
for lbl in rows:
    b,r,ot,it,pf,st = rows[lbl]
    for pname, p in PRICES:
        c_out = p/(ot*3600)*1e6
        c_in  = p/(it*3600)*1e6
        blend = 0.75*(0.5*c_in + 0.5*0.1*c_in) + 0.25*c_out
        star = "  <- published rental" if p==18.00 else ""
        print(f"{lbl:>28} {p:>9.2f} {c_out:>10.4f} {c_in:>10.4f} {blend:>13.4f}{star}")
    print()

print("4b. VIDEO COST  (frames = clamp(2 fps * duration, 4, 240); 200,704 px/frame; 98 LLM tok/frame)")
print("-"*100)
print(f"{'duration':>10} {'frames':>7} {'eff fps':>8} {'prefill tok':>12} {'TFLOP':>8} "
      f"{'s/video @b=256':>15} {'$/video':>10} {'$/1k videos':>12} {'$/video-min':>12}")
for dur, label in [(2,"2 s"),(30,"30 s"),(60,"60 s"),(120,"2 min"),(600,"10 min"),(3600,"60 min")]:
    frames = max(4, min(240, int(2*dur)))
    toks   = frames*TOK_PER_FRAME + SCAFFOLD
    tf     = (FLOP_TOK*toks + ATTN_C*toks**2 + VIT_PER_FRAME*frames)/1e12
    b = min(256, maxconc(toks+384))
    r, ot, it, pf, st = sustained(toks, 768, b, frames)
    s_per = 1.0/r
    cost  = 18.00/3600 * s_per
    print(f"{label:>10} {frames:>7} {frames/dur:>8.3f} {toks:>12,} {tf:>8.2f} "
          f"{s_per:>15.4f} {cost:>10.6f} {cost*1000:>12.4f} {cost/(dur/60):>12.6f}")

print()
print("4c. Break-even vs. a hypothetical hosted API, S1 blended, OCI $18.00/GPU-h")
b,r,ot,it,pf,st = rows["S1 4K/512"]
c_out = 18.00/(ot*3600)*1e6; c_in = 18.00/(it*3600)*1e6
blend = 0.75*(0.5*c_in+0.5*0.1*c_in)+0.25*c_out
print(f"  self-host blended $/1M = {blend:.4f} at 100% utilisation")
for api in [0.05, 0.10, 0.20, 0.50]:
    print(f"  vs API ${api:.2f}/1M : break-even utilisation = {blend/api*100:>6.1f} %"
          f"  ({'self-host wins above that' if blend<api else 'API always cheaper'})")

print()
print("4d. LOADING TIME, 5.444 GB checkpoint from NVMe")
for bw in (5, 10, 20):
    print(f"  @ {bw:>2} GB/s : {CKPT_DISK/GB/bw:.2f} s weights only "
          f"(+ CUDA-graph capture + torch.compile, engine-dependent)")
