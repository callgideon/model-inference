# marlin2b benchmark corpus (task E1)

64 distinct clips plus a 32-clip fast subset, cut from four clearly licensed long
sources, plus four corrupt-media fixtures. It exists because every number in
[`../results/notes.md`](../results/notes.md) came from **two** clips sent over and
over: finding 5 there says vLLM's multimodal cache may have absorbed the decode
cost, so warm-cache throughput was not a traffic result. PERF-PILOT and
MEDIA-PARITY in [`research/plan/04-verification.md`](../../../research/plan/04-verification.md)
need distinct media, orientations, durations and prompts instead.

**No media is committed.** `manifest.json` holds recipes, hashes and probed
metadata; the clips live under the git-ignored cache.

## Build and verify

```bash
export CORPUS_CACHE=/path/to/model-inference/.claude/corpus-cache   # required from a worktree
python models/marlin2b/corpus/build.py plan      # deterministic recipes, status unbuilt
python models/marlin2b/corpus/build.py build     # fetch sources, derive clips, fill hashes
python models/marlin2b/corpus/build.py verify    # recompute every sha256 and reprobe
python models/marlin2b/corpus/build.py validate  # schema/distinctness only, no media needed
```

`build` needs ~2.6 GB of cache (2.1 GB sources, 197 MB clips) and about 12 minutes
of CPU at `--jobs 6`. `verify --rederive` re-cuts clips that are missing from the
cache instead of failing. `plan` keeps the measured fields of any clip whose recipe
did not change; a changed recipe drops back to `unbuilt` until rebuilt.

Derivations are byte-reproducible: `-threads 1 -fflags +bitexact -flags:v +bitexact`
with the pinned ffmpeg, so the sha256 in the manifest is a real pin, not a label.
The pinned build is `ffmpeg 7.0.2-amd64-static` (johnvansickle.com), tarball sha256
`abda8d77…cf67`, ffmpeg `e7e7fb30…eb99`, ffprobe `4f231a19…450d`; `build.py` fetches
it into `$CORPUS_CACHE/tools` and refuses any other binary. This host has no system
ffmpeg, and none is used.

## Sources and attribution

| Source | Licence | Evidence checked | Attribution |
|---|---|---|---|
| Big Buck Bunny (Sunflower 1080p30) | CC-BY-3.0 | <https://peach.blender.org/about/> | (CC) Blender Foundation \| peach.blender.org |
| Tears of Steel (720p) | CC-BY-3.0 | <https://mango.blender.org/about/> | (CC) Blender Foundation \| mango.blender.org |
| Sintel (2010 1080p) | CC-BY-3.0 | <https://durian.blender.org/sharing/> | (CC) Blender Foundation \| durian.blender.org |
| The Aurora Named STEVE | public domain (US Gov) | <https://images.nasa.gov/docs/images.nasa.gov_Guide_v1.0.pdf> | NASA's Goddard Space Flight Center |

Each licence page was read, and the sentence relied on is quoted in the manifest's
`license_evidence_quote`. `download.blender.org/demo/movies/ToS/copyright.txt` puts
the *soundtrack* under CC-BY-ND-3.0; every derived clip is video-only (`-an`), so no
soundtrack is redistributed. No private, customer or unlicensed media is used.

## What the 64 clips cover

- **Content:** 64 non-overlapping segments (the test asserts non-overlap), 6–24 per
  source, so no two clips share footage.
- **Resolutions:** 14 distinct geometries, 1920×1080 and 2560×1080 among them —
  1080p decode is the measured bottleneck (notes.md finding 7).
- **Aspect/orientation:** 16:9, 9:16 portrait, 1:1 square, 21:9 (64:27) ultrawide,
  2.4:1 scope, 4:3, 4:1 extreme wide, 1:4 extreme tall, plus real rotated pixels
  (`transpose`) on three geometries.
- **Durations:** 2 s to 112 s, 13 distinct values, all inside the 120 s API cap.
- **Frame rates:** 5, 10, 15, 24, 30, 60 fps.
- **Prompts:** 16 distinct prompts (caption, grounding, counting, camera motion,
  on-screen text, orientation …), paired per clip so prompt text varies with media.
- **Subsets:** clips 0–31 are `fast` and already cover every geometry, duration,
  source and prompt; `full` is all 64.
- **`source_upscaled`** marks the 16 clips whose target height exceeds their
  source's, so nobody quotes them as native-resolution parity evidence.

Manifest order is stable; benchmark clients reorder with their own `--seed`.

## Negative fixtures

`negatives/` covers the MEDIA-PARITY failure cases: `neg-truncated-mp4`
(first 64 KiB of a real clip), `neg-wrong-container` (mp4 bytes with an `.mkv`
name), `neg-zero-length`, `neg-not-video` (text payload named `.mp4`). Each has a
real sha256 and an `expected_failure` class; none is expected to decode.

## Known gaps

- Container display-matrix rotation is **not** covered: ffmpeg 7's mp4 muxer drops
  `-metadata rotate=`, verified by reprobe, so no recipe claims it. Phone-style
  rotation metadata needs `-display_rotation` and a follow-up fixture.
- All clips are re-encoded H.264/yuv420p in MP4: container and codec diversity
  (HEVC, VP9, AV1, WebM) is not covered.
- Audio is dropped; this corpus cannot test audio handling.
- Segment content is animation-heavy (three of four sources are animated films).
