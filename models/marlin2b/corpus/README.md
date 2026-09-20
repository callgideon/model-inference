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
# $CORPUS_CACHE overrides; the default is now <main checkout>/.claude/corpus-cache,
# resolved with `git rev-parse --git-common-dir`, so every worktree shares one cache
python models/marlin2b/corpus/build.py plan      # deterministic recipes, status unbuilt
python models/marlin2b/corpus/build.py build     # fetch sources, derive clips, fill hashes
python models/marlin2b/corpus/build.py verify    # recompute every sha256 and reprobe
python models/marlin2b/corpus/build.py validate  # schema/distinctness only, no media needed
```

`build` needs ~2.5 GB of cache (2.11 GB sources, 206 MB clips, plus 210 MB of pinned
tools) and about 12 minutes of CPU at `--jobs 6` from cold. `verify --rederive`
re-cuts clips that are missing from the cache instead of failing. `plan` keeps the
measured fields — and the source pins — of anything whose recipe and URL did not
change; a changed recipe or id drops back to `unbuilt` until rebuilt, and `build`
re-derives it even when a stale file sits at that path. A cached source or clip whose bytes
no longer match the pinned sha256 is reported as `sha256_mismatch` (the pin is kept
and `validate` fails), never silently re-pinned; `--force` re-derives and re-pins on
purpose. A changed id leaves the old file in the cache as an unreferenced orphan;
delete it or ignore it, nothing reads it.

`FFMPEG_PIN.tarball_url` is a **versioned** URL,
`johnvansickle.com/ffmpeg/releases/ffmpeg-7.0.2-amd64-static.tar.xz`, re-downloaded
on 2026-09-20 and confirmed to serve exactly the pinned `tarball_sha256`
(41,888,096 bytes, upstream md5 `7fa72b65…cdf3`). `tarball_url_fallbacks` keeps the
unversioned `ffmpeg-release-amd64-static.tar.xz` alias (the same bytes today, until
upstream publishes 7.1) and the `old-releases/` path the file moves to when 7.0.2 is
retired; `ensure_ffmpeg()` tries them in order and the sha256 decides, whichever
answered. The pinned binary hashes stay the real pin either way.

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
| The Aurora Named STEVE | public domain (US Gov), see caveat | <https://images.nasa.gov/docs/images.nasa.gov_Guide_v1.0.pdf> | NASA's Goddard Space Flight Center |

Each licence page was read, and the sentence relied on is quoted in the manifest's
`license_evidence_quote`. `download.blender.org/demo/movies/ToS/copyright.txt` puts
the *soundtrack* under CC-BY-ND-3.0; every derived clip is video-only (`-an`), so no
soundtrack is redistributed. No private, customer or unlicensed media is used.

**NASA STEVE caveat.** The generic "NASA content is not copyrighted" sentence is a
site-wide guideline, and this item's own NASA description credits *"amateur
photographers from the Alberta Aurora Chasers"*, so parts of the video are very
likely third-party stills it does not cover. Nothing is redistributed (no media in
git; clips stay in the local cache), so the risk is local only — but treat this
source as attribution-only and swap in NASA-produced footage before any public
redistribution. The manifest records the caveat and the item page
(<https://svs.gsfc.nasa.gov/12865>) in the source's `note`.

## What the 64 clips cover

Every claim below is the **probed** geometry of the built files, asserted by
`models/marlin2b/tests/test_corpus.py` against `manifest.json` (and by
`build.py validate`), not the recipe's intent: a clip id or `geometry_label` that
does not match the probed width/height/aspect is a validation error.

`geometry_label` names the **pre-rotation** recipe scale, so the three rotated clips
(`c020`, `c025`, `c030`) carry a label whose axes are swapped relative to the file.
Read `display_width` / `display_height` instead: they are the on-disk, post-rotation
pixels, and `validate` fails if they disagree with either the recipe or the probe.

- **Content:** 64 non-overlapping segments (the test asserts non-overlap), 6–24 per
  source, so no two clips share footage.
- **Resolutions:** 16 distinct probed geometries, 1920×1080 and 2560×1080 among
  them — 1080p decode is the measured bottleneck (notes.md finding 7).
- **Aspect/orientation:** 16:9, 9:16 portrait (including a real 1080×1920), 1:1
  square, 21:9 (64:27) ultrawide, 2.4:1 scope, 4:3, 4:1 extreme wide (1920×480),
  1:4 extreme tall (480×1920), 240p tiny.
- **Rotation:** three clips carry real rotated pixels and say so in their id
  (`-rot90cw`, `-rot180`, `-rot90ccw`). Rotation is applied to **one** clip per
  rotated geometry, not all four, so the unrotated portrait and extreme-tall
  clips still exist; a `-rot90…` clip's probed axes are the label's, swapped.
- **Durations:** 2 s to 112 s, 13 distinct values, all inside the 120 s API cap.
- **Frame rates:** 10, 15, 24, 30, 60 fps (no 5 fps clip; 10 fps is the floor).
- **Prompts:** 16 distinct prompts (caption, grounding, counting, camera motion,
  on-screen text, orientation …), paired per clip so prompt text varies with media.
- **Subsets:** clips 0–31 are `fast` and already cover every geometry, duration,
  source and prompt, including the portrait and extreme-tall extremes; `full` is all 64.
- **`source_upscaled`** marks the 28 clips whose pre-rotation target exceeds their
  source on **either** axis (`force_original_aspect_ratio=increase` scales by
  `max(W/sw, H/sh)`, so a width-only upsize counts), so nobody quotes them as
  native-resolution parity evidence.

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
- The lowest frame rate is 10 fps: no 5 fps or 1 fps clip, and no variable frame
  rate. Adding one changes the `FPS[i % 5]` assignment and rebuilds all 64 clips.
- Segment content is animation-heavy (three of four sources are animated films).
