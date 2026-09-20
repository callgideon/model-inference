#!/usr/bin/env python3
"""Reference path: run Marlin-2B through transformers with its own .caption()/.find()
helpers. This is the vendor-supported route; use it to (a) confirm the weights
and video stack work on a box, and (b) produce ground-truth outputs to diff
against the vLLM server. Also dumps the canonical prompts the helpers use so
smoke.py can send the identical text to vLLM.

    /opt/pytorch/bin/python marlin2b/reference.py video.mp4
    /opt/pytorch/bin/python marlin2b/reference.py video.mp4 --find "a person enters the room"
    /opt/pytorch/bin/python marlin2b/reference.py --dump-prompts
"""
import argparse, inspect, json, os, sys, time

ap = argparse.ArgumentParser()
ap.add_argument("video", nargs="?")
ap.add_argument("--model", default=os.environ.get("WEIGHTS", "/opt/dlami/nvme/marlin2b"))
ap.add_argument("--find", metavar="EVENT", help="find mode instead of caption mode")
ap.add_argument("--max-new-tokens", type=int, default=2048)
ap.add_argument("--compile", action="store_true")
ap.add_argument("--dump-prompts", action="store_true", help="print the canonical prompts and exit")
a = ap.parse_args()

import torch
from transformers import AutoModelForCausalLM

t0 = time.time()
m = AutoModelForCausalLM.from_pretrained(a.model, trust_remote_code=True, dtype=torch.bfloat16, device_map={"": "cuda"})
print(f"loaded in {time.time()-t0:.1f}s; {torch.cuda.memory_allocated()/2**30:.2f} GiB allocated", file=sys.stderr)

if a.dump_prompts:
    # The prompts are constants in the custom module; find them by name rather than
    # hard-coding, so a checkpoint update cannot silently desync smoke.py.
    mod = sys.modules[type(m).__module__]
    found = {k: v for k, v in vars(mod).items() if "PROMPT" in k.upper() and isinstance(v, str)}
    if not found:  # fall back to string constants inside the helper methods
        for name in ("caption", "find"):
            src = inspect.getsource(getattr(m, name))
            found[name + "_source"] = src
    print(json.dumps(found, indent=2))
    sys.exit(0)

if not a.video:
    ap.error("video path required unless --dump-prompts")
if a.compile:
    m.compile()

t0 = time.time()
with torch.inference_mode():
    r = m.find(a.video, event=a.find, max_new_tokens=a.max_new_tokens) if a.find else m.caption(a.video, max_new_tokens=a.max_new_tokens)
dt = time.time() - t0
print(json.dumps(r, indent=2, default=str))
print(f"\nwall {dt:.2f}s; peak {torch.cuda.max_memory_allocated()/2**30:.2f} GiB", file=sys.stderr)
