"""AP-04d: the compatibility report - whether verified bytes fit the one supported profile.

The supported profile is the measured Marlin serving version (`models/marlin2b/
serving-version.json`, the fixture of truth; `tests/ap04` pins these constants to it): the
Marlin architecture served through vLLM's NATIVE `Qwen3_5ForConditionalGeneration` (the
`--hf-overrides` flag; the repository's `auto_map` remote code is never executed), the pinned
vLLM image by digest, bfloat16, on an L40S. Anything else is refused with a specific reason
code per field - never a silent "unsupported".
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ...contracts import errors
from ...contracts.api import FieldError, Wire
from ..control import SUPPORTED_SCHEMAS
from .manifest import Manifest

PROFILE_ID = "marlin2b-vllm-l40s-bf16-v1"
RUNTIME_IMAGE = ("vllm/vllm-openai@sha256:"
                 "4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42")
ENGINE_OPTIONS_DIGEST = "sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147"
# config `architectures` -> the native implementation the pinned image serves it with
ARCHITECTURES = {"MarlinForConditionalGeneration": "Qwen3_5ForConditionalGeneration"}
MODEL_TYPE = "qwen3_5"
PRECISION = "bfloat16"
HARDWARE = "L40S"
REQUIRED = ("config.json", "generation_config.json", "tokenizer.json", "chat_template.jinja",
            "processor_config.json", "preprocessor_config.json")
INDEX = "model.safetensors.index.json"
#: the bounded files a report reads (never a weight shard)
READ = ("config.json", INDEX)


class Compatibility(Wire):
    supported: bool
    profile: str = PROFILE_ID
    reasons: tuple[FieldError, ...] = ()


class ProfileRequest(Wire):
    """What a serving revision asks to run: compared with the supported profile."""

    runtime_image_ref: str = Field(default=RUNTIME_IMAGE, max_length=300)
    precision: str = Field(default=PRECISION, max_length=50)
    hardware: str = Field(default=HARDWARE, max_length=50)
    input_schema_ref: str = Field(default="infrx.request.chat.v1", max_length=200)
    output_schema_ref: str = Field(default="infrx.response.chat.v1", max_length=200)


def _r(field: str, code: str, message: str) -> FieldError:
    return FieldError(field=field, code=code, message=message)


def shards(manifest: Manifest) -> list[str]:
    """The weight shards in shard order (`model-00001-of-00002...` sorts as it numbers)."""
    return sorted(f.relative_path for f in manifest.files
                  if f.relative_path.endswith(".safetensors"))


def artifact_reasons(manifest: Manifest, config: Any, index: Any) -> list[FieldError]:
    """What the verified bytes themselves lack for the profile. `config`/`index` are the
    parsed files (`{}` when absent, a non-dict when unreadable); None = not read (an
    adoption by reference: its bytes are already the production pins)."""
    reasons = [_r(f"files.{name}", "missing_file", f"{name} is required")
               for name in REQUIRED if name not in manifest.by_path()]
    weights = shards(manifest)
    if not weights:
        reasons.append(_r("files", "missing_weights", "no .safetensors weight shard"))
    if index is not None and (len(weights) > 1 or INDEX in manifest.by_path()):
        mapped = index.get("weight_map") if isinstance(index, dict) else None
        if not isinstance(mapped, dict) or set(mapped.values()) != set(weights):
            reasons.append(_r(f"files.{INDEX}", "shard_index_mismatch",
                              "the shard index does not name exactly the manifest's shards"))
    if isinstance(config, dict):
        architectures = config.get("architectures")
        if (not isinstance(architectures, list) or len(architectures) != 1
                or architectures[0] not in ARCHITECTURES):
            reasons.append(_r("config.architectures", "unsupported_architecture",
                              f"supported: {sorted(ARCHITECTURES)}"))
        if config.get("model_type") != MODEL_TYPE:
            reasons.append(_r("config.model_type", "unsupported_model_type",
                              f"supported: {MODEL_TYPE}"))
        if (config.get("dtype") or config.get("torch_dtype")) != PRECISION:
            reasons.append(_r("config.dtype", "unsupported_dtype", f"supported: {PRECISION}"))
    elif config is not None and "config.json" in manifest.by_path():
        reasons.append(_r("files.config.json", "unreadable_config", "config.json is not a "
                          "JSON object"))
    return reasons


def request_reasons(request: ProfileRequest) -> list[FieldError]:
    """What a revision request asks for that the profile does not serve."""
    reasons = []
    if request.runtime_image_ref != RUNTIME_IMAGE:
        reasons.append(_r("runtime_image_ref", "unsupported_runtime",
                          "the pinned vLLM image by digest is the supported runtime"))
    if request.precision != PRECISION:
        reasons.append(_r("precision", "unsupported_precision", f"supported: {PRECISION}"))
    if request.hardware != HARDWARE:
        reasons.append(_r("hardware", "unsupported_hardware", f"supported: {HARDWARE}"))
    if (request.input_schema_ref, request.output_schema_ref) not in SUPPORTED_SCHEMAS:
        reasons.append(_r("input_schema_ref", "unsupported_input_format",
                          "the gateway serves the chat request/response schemas only"))
    return reasons


def report(reasons: list[FieldError]) -> Compatibility:
    return Compatibility(supported=not reasons, reasons=tuple(reasons))


# ================================================================ the bytes themselves ===
READ_LIMIT = 1 << 20


async def byte_reasons(objects: Any, prefix: str, manifest: Manifest,
                       advance: Any) -> tuple[list[FieldError], list[str]]:
    """Every declared file rehashed from the STORED bytes (never the declaration): missing,
    extra and mismatched paths are reasons. Returns (reasons, the paths found)."""
    found = {key[len(prefix):] for key in await objects.keys(prefix)}
    declared = manifest.by_path()
    reasons = [_r(f"files.{p}", "missing_file", "declared and not stored")
               for p in sorted(declared.keys() - found)]
    reasons += [_r(f"files.{p}", "extra_path", "stored and not declared")
                for p in sorted(found - declared.keys())]
    for path in sorted(declared.keys() & found):
        await advance(f"hashing:{path}")
        if await objects.digest(prefix + path) != (declared[path].bytes, declared[path].sha256):
            reasons.append(_r(f"files.{path}", "digest_mismatch",
                              "the stored bytes are not the declared size and sha256"))
    return reasons, sorted(found)


async def compatibility(objects: Any, prefix: str, manifest: Manifest) -> Compatibility:
    """The report over the verified bytes: config.json and the shard index are read back
    (bounded) from the store, never taken from the declaration."""
    parsed: dict[str, Any] = {}
    for name in READ:
        parsed[name] = {}
        if name in manifest.by_path():
            try:
                parsed[name] = json.loads(await objects.read(prefix + name, READ_LIMIT) or b"")
            except (ValueError, errors.RequestTooLarge):
                parsed[name] = "unreadable"
    return report(artifact_reasons(manifest, parsed["config.json"], parsed[INDEX]))
