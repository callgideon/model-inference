#!/usr/bin/env python3
"""How many prompt tokens does a video cost, and with which frame grid?

Runs the checkpoint's own processor (the path .caption()/.find() use, with the
env defaults modeling_marlin.py sets) and prints input_ids length and
video_grid_thw, then repeats for candidate `video_kwargs` so vLLM's
mm_processor_kwargs can be matched to the vendor pipeline exactly.

    /opt/pytorch/bin/python marlin2b/tokens.py /opt/dlami/nvme/samples/sample-10s.mp4
    /opt/pytorch/bin/python marlin2b/tokens.py video.mp4 --kwargs '{"fps":2.0,"cap_pixels_per_frame":true}'
"""
import argparse, importlib.util, inspect, json, os, sys

ap = argparse.ArgumentParser()
ap.add_argument("video")
ap.add_argument("--weights", default=os.environ.get("WEIGHTS", "/opt/dlami/nvme/marlin2b"))
ap.add_argument("--kwargs", action="append", default=[], help="JSON video_kwargs variant to try (repeatable)")
ap.add_argument("--show-helper", action="store_true", help="print the caption() helper source")
a = ap.parse_args()

spec = importlib.util.spec_from_file_location("modeling_marlin", os.path.join(a.weights, "modeling_marlin.py"))
mm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm)  # sets FORCE_QWENVL_VIDEO_READER / VIDEO_MAX_PIXELS / FPS / FPS_*_FRAMES
print("env:", {k: os.environ.get(k) for k in ("FORCE_QWENVL_VIDEO_READER", "VIDEO_MAX_PIXELS", "FPS", "FPS_MAX_FRAMES", "FPS_MIN_FRAMES")})
if a.show_helper:
    print("\n".join(inspect.getsource(mm.MarlinForConditionalGeneration.caption).splitlines()[:80]))

from transformers import AutoProcessor

p = AutoProcessor.from_pretrained(a.weights, trust_remote_code=True)
vp = p.video_processor
print("processor:", type(p).__name__, "/", type(vp).__name__)
print("video_processor defaults:", {k: getattr(vp, k, None) for k in ("size", "fps", "min_frames", "max_frames", "do_sample_frames", "num_frames", "patch_size", "temporal_patch_size", "merge_size")})

msgs = [{"role": "user", "content": [{"type": "video", "video": a.video}, {"type": "text", "text": mm.CAPTION_PROMPT}]}]


def run(label, **kw):
    try:
        inp = p.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True, return_tensors="pt", return_dict=True, **kw)
        thw = inp.get("video_grid_thw")
        thw = thw.tolist() if thw is not None else None
        toks = sum(t * h * w for t, h, w in thw) // (vp.merge_size ** 2) if thw else None
        print(f"{label:28} input_ids={inp['input_ids'].shape[1]:6}  video_grid_thw={thw}  video_tokens={toks}")
    except Exception as e:
        print(f"{label:28} ERROR {type(e).__name__}: {str(e)[:300]}")


run("processor default")
for kw in a.kwargs or [
    '{"fps": 2.0, "min_frames": 4, "max_frames": 240, "size": {"shortest_edge": 65536, "longest_edge": 200704}, "cap_pixels_per_frame": true}',
    '{"fps": 2.0, "cap_pixels_per_frame": true}',
    '{"fps": 2.0, "size": {"shortest_edge": 65536, "longest_edge": 200704}}',
    '{"fps": 2.0, "max_pixels": 200704, "min_pixels": 65536}',
]:
    run(kw[:28], video_kwargs=json.loads(kw))
