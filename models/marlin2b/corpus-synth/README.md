# `sop-synth-v1` — the owned synthetic SOP fixture

Twelve procedurally generated clips that exercise the SOP journey and the
temporal-event plumbing with **no copyrighted, customer or robotics-vendor
footage**, and with ground truth that exists by construction. Specified in
[`research/workloads/marlin-sop.md`](../../../research/workloads/marlin-sop.md)
§3.8; built by [`synth.py`](synth.py); `manifest.json` is the only artifact in git.

```bash
python models/marlin2b/corpus-synth/synth.py validate   # step logic only, no media
python models/marlin2b/corpus-synth/synth.py plan       # re-derive the recipes
python models/marlin2b/corpus-synth/synth.py build      # render into $CORPUS_CACHE
python models/marlin2b/corpus-synth/synth.py verify     # recompute hashes and probe
```

## What makes the labels trustworthy

The step script — `{step_id, canonical_index, label, start_s, end_s}` — is the
**input** to the renderer: each step's `drawbox` windows are `enable`d over exactly
its own `start_s`/`end_s`. The labels are therefore not annotations anybody has to
believe; they are the numbers the pixels were made from. Nothing here was labelled
after the fact, so there is no annotator, no agreement question and no rubric.

## What it is not

**A score on `sop-synth-v1` is never an accuracy claim about real robotics video.**
It has no scene richness, no camera motion, no real procedure and it is trivially
unlike the training distribution. It proves the API journey, the temporal-event
plumbing and the evaluator harness. `marlin-sop.md` §5.3 (P-07) lists what a real
accuracy claim would require — a rubric, tolerances, licensed ground truth and a
grouped episode split — and none of it exists in this repository.

The licensed 64-clip corpus in [`../corpus/`](../corpus/README.md) stays the
**performance** fixture. This one complements it; it does not replace it.

## The four declared cases

| Clip | Case | What it probes |
|---|---|---|
| `sop01-8s-854x480-steps_within_one_second` | two steps 0.4 s apart | timestamp resolution: the gap is **inside one profile-v1 frame period** (0.5 s at 2 fps), so the two steps can fall between sampled frames |
| `sop04-30s-854x480-steps_out_of_order` | 2 steps, canonical order reversed in time | a model (or a parser) that only ever emits canonical order is wrong here |
| `sop07-60s-854x480-step_absent` | canonical step 2 declared absent, never rendered | a rubric can be shown to punish a hallucinated span — `.find` "always emits some span" |
| `sop10-115s-854x480-step_spans_segment_boundary` | one step straddles 60 s | the §3.2 overlap problem: a step crossing the client's own segmentation edge is seen twice or truncated |

Every clip that crosses the declared 60 s segmentation edge lists it in
`boundary_spanning_step_ids`, declared case or not: a crossing nobody wrote down is
how correlated segments end up counted as independent samples.

## Spread

- durations **8 / 30 / 60 / 115 s**, three clips each, all inside the 120 s API cap;
  115 s is this fixture's frame-budget worst case (230 frames at profile v1, under
  the 240-frame ceiling)
- geometries 640×360, 854×480, 1280×720; source frame rates 10, 15, 30 fps
- 2–7 steps per clip (2 only for the declared out-of-order clip), 55 rendered steps
  and 1 declared-absent step in total
- each clip records `frames_profile_v1` and `longest_edge_profile_v1`, so the token
  budget the pinned profile derives is in the manifest rather than recomputed

## The pin, and one documented deviation

Rendered by the same **`ffmpeg 7.0.2-amd64-static`** build that
[`../corpus/build.py`](../corpus/build.py) fetches and verifies by sha256, with the
same bit-exact flags (`-threads 1 -fflags +bitexact -flags:v +bitexact`) and a
`lavfi` source, so every clip's sha256 is a real pin — re-rendering gives
byte-identical output, which `verify` and the tests hold the manifest to.

**Deviation from §3.8:** §3.8 asks for "a text overlay naming the current step" via
`drawtext`. The pinned build **has no `drawtext` filter** (it lists 494 filters and
none of them is `drawtext`; ffmpeg 7.0 made libharfbuzz a hard requirement and this
static build ships only libfreetype/fontconfig). Re-pinning the encoder to gain a
cosmetic overlay would invalidate the licensed corpus's verified hashes, so the step
is identified **in the pixels** instead: a per-step part colour plus a tally of
`canonical_index + 1` white marks along the bottom bar. The ground truth is
unaffected — it was never the overlay — and the fixture needs no font, so it
reproduces on any host that has the pinned binary. `synth.py` checks every filter it
uses against `ffmpeg -filters` and names a missing one instead of failing at
output-open time.

No media is committed. Clips live under `$CORPUS_CACHE` (default
`<main checkout>/.claude/corpus-cache/sop-synth-v1/`, git-ignored, 608 KB (624 KiB on disk) for all
twelve), beside the licensed corpus, so every worktree shares one cache.

## Verification log

- 2026-09-22 (E1B.a): Planned, rendered and verified with the pinned ffmpeg already
  present in the shared cache (`ffmpeg`/`ffprobe` sha256 match `FFMPEG_PIN`); 12/12
  clips built, `verify` reports 0 errors, and a re-render of clip `sop00` reproduced
  its committed sha256 byte for byte. `drawtext` was found to be absent from the
  pinned build during that run, which is why the recipe uses `drawbox` only. No
  network access, no GPU, no cloud call.
