#!/usr/bin/env python3
"""The served model's artifacts as a digest manifest (I8 slice 5): what the durable mirror
holds and what a restore must reproduce. Stdlib only (runs on the box's host python3).

    artifacts.py manifest --weights /opt/dlami/nvme/marlin2b --serving-version \
        models/marlin2b/serving-version.json --release <sha> [--env-file /etc/marlin2b-gateway.env] \
        [--serve-script models/marlin2b/serve.sh] --out <dir>      # manifest.json + SHA256SUMS
    artifacts.py verify --weights <dir> --manifest <dir>/manifest.json

`manifest` hashes every file under the weights directory (weights, processor, tokenizer,
chat template, configs), checks the digests W3 pinned in serving-version.json (shards,
tokenizer, chat template, config, generation config) and REFUSES (exit 2) a directory that
does not serve the pinned bytes - a mirror of the wrong model is worse than none. The
processor files have no pin there yet (F10 / P-06): their digests are recorded here and
reported as unpinned (WR-I8-5 proposes the serving-version.json addition). The engine image
is recorded by reference (its registry digest), the runtime image by id and by the release
bundle it is built from, the env file by NAMES only.

`verify` checks a restored directory against a manifest: every file present, same size,
same sha256, nothing extra. Exit 0 equal, 1 different.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

SCHEMA = "infrx-artifacts/1"
# serving-version.json model field -> the file it pins
PINNED = {"tokenizer_digest": "tokenizer.json", "chat_template_digest": "chat_template.jinja",
          "config_digest": "config.json", "generation_config_digest": "generation_config.json"}
PROCESSOR = ("processor_config.json", "preprocessor_config.json",
             "video_preprocessor_config.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def files(root: Path) -> list[dict]:
    out = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root))
        if rel.startswith(".cache/"):          # hf download's lock/metadata, not served
            continue
        out.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path)})
    return out


def role(rel: str) -> str:
    name = Path(rel).name
    if name.endswith(".safetensors"):
        return "weights"
    if name in PROCESSOR:
        return "processor"
    if name.startswith("tokenizer") or name in ("vocab.json", "merges.txt", "special_tokens_map.json"):
        return "tokenizer"
    if name.endswith(".jinja") or name == "chat_template.json":
        return "template"
    if name.endswith(".json"):
        return "config"
    return "other"


def pin_problems(entries: list[dict], serving: dict) -> tuple[list[str], list[str]]:
    """(problems, unpinned processor files) against W3's served-bytes record."""
    model = serving["model"]
    by_name = {Path(e["path"]).name: e["sha256"] for e in entries}
    problems = []
    shards = sorted(e["sha256"] for e in entries if e["path"].endswith(".safetensors"))
    if shards != sorted(model["weight_shard_digests"]):
        problems.append("the weight shards are not the pinned ones (serving-version.json)")
    for field, name in PINNED.items():
        if by_name.get(name) != model[field]:
            problems.append(f"{name} is not the pinned {field}")
    unpinned = [name for name in PROCESSOR if name in by_name]
    return problems, unpinned


def manifest(a) -> int:
    weights = Path(a.weights)
    serving = json.loads(Path(a.serving_version).read_text())
    entries = files(weights)
    for entry in entries:
        entry["role"] = role(entry["path"])
    problems, unpinned = pin_problems(entries, serving)
    if problems:
        for problem in problems:
            print(f"REFUSED: {problem}", file=sys.stderr)
        return 2
    env_names = []
    runtime_image = None
    if a.env_file:
        for line in Path(a.env_file).read_text().splitlines():
            if line and not line.startswith("#") and "=" in line:
                name, _, value = line.partition("=")
                env_names.append(name)
                if name == "INFRX_IMAGE":
                    runtime_image = value            # an image id, not a secret
    doc = {
        "schema": SCHEMA, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "release": a.release,
        "model": {"repo": serving["model"]["repo"], "commit": serving["model"]["commit"]},
        "files": entries,
        "unpinned_processor_files": unpinned,
        "engine_image": {"ref": serving["runtime_image"]["ref"],
                         "digest": serving["runtime_image"]["digest"]},
        "engine_options_digest": serving.get("engine_options_digest"),
        "runtime_image": {"id": runtime_image,
                          "rebuild_from": f"release bundle {a.release} (releases/ prefix)"},
        "serving_version_sha256": sha256(Path(a.serving_version)),
        "serve_script_sha256": sha256(Path(a.serve_script)) if a.serve_script else None,
        "env_names": env_names,
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(doc, indent=1) + "\n")
    (out / "SHA256SUMS").write_text("".join(f"{e['sha256'][7:]}  {e['path']}\n" for e in entries))
    total = sum(e["bytes"] for e in entries)
    print(f"manifest {sha256(out / 'manifest.json')} files={len(entries)} bytes={total} "
          f"unpinned_processor={','.join(unpinned) or 'none'}")
    return 0


def verify(a) -> int:
    doc = json.loads(Path(a.manifest).read_text())
    want = {e["path"]: (e["bytes"], e["sha256"]) for e in doc["files"]}
    have = {e["path"]: (e["bytes"], e["sha256"]) for e in files(Path(a.weights))}
    missing = sorted(set(want) - set(have))
    extra = sorted(set(have) - set(want))
    changed = sorted(p for p in set(want) & set(have) if want[p] != have[p])
    for label, paths in (("missing", missing), ("extra", extra), ("changed", changed)):
        for path in paths:
            print(f"{label}: {path}")
    equal = not (missing or extra or changed)
    print(f"{'EQUAL' if equal else 'DIFFERENT'} files={len(have)} manifest_files={len(want)}")
    return 0 if equal else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    m = sub.add_parser("manifest")
    m.add_argument("--weights", required=True)
    m.add_argument("--serving-version", required=True)
    m.add_argument("--release", required=True)
    m.add_argument("--env-file")
    m.add_argument("--serve-script")
    m.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--weights", required=True)
    v.add_argument("--manifest", required=True)
    a = ap.parse_args(argv)
    return manifest(a) if a.command == "manifest" else verify(a)


if __name__ == "__main__":
    sys.exit(main())
