"""AP-04: the artifact manifest - what a provider declares and what verification must find.

A manifest is a closed list of files `{relative_path, bytes, sha256, media_type}`. It is
refused before any byte moves when a path could escape its prefix (absolute, `..`, a hidden
segment, a backslash), when a file could carry code (only weights as safetensors, JSON,
templates and text are artifacts: no pickle, `.bin`, `.pt` or `.py` - remote code is never
enabled), or when it exceeds the bounds below. Its digest is the canonical (RFC 8785) hash
of its files sorted by path: the declared identity verification re-derives from real bytes.
"""
from __future__ import annotations

import hashlib
import re

from pydantic import Field, field_validator, model_validator

from ...contracts.api import Wire
from ...contracts.lab.records import canonical

MAX_FILES = 256
# ponytail: one PUT per file (S3's 5 GiB single-PUT ceiling); multipart per file when a
# supported model's shard exceeds it.
MAX_FILE_BYTES = 5 * 1024 ** 3
MAX_TOTAL_BYTES = 64 * 1024 ** 3
SAFE_SUFFIXES = (".safetensors", ".json", ".jinja", ".txt", ".md")
SAFE_NAMES = frozenset({"LICENSE", "NOTICE"})
SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
SHA256 = r"^sha256:[0-9a-f]{64}$"


def refuse_path(path: str) -> str | None:
    """Why `path` is not a manifest path, or None."""
    segments = path.split("/")
    if len(path) > 512 or not all(SEGMENT.match(s) for s in segments):
        return "unsafe_path"       # absolute, `..`, `.hidden`, backslash, empty segment
    name = segments[-1]
    if name not in SAFE_NAMES and not name.endswith(SAFE_SUFFIXES):
        return "code_bearing_file"
    return None


class FileEntry(Wire):
    relative_path: str
    bytes: int = Field(ge=0, le=MAX_FILE_BYTES)
    sha256: str = Field(pattern=SHA256)
    media_type: str = Field(pattern=r"^[a-z]+/[a-z0-9.+-]{1,100}$")

    @field_validator("relative_path")
    @classmethod
    def _path(cls, path: str) -> str:
        refused = refuse_path(path)
        if refused:
            raise ValueError(refused)
        return path


class Manifest(Wire):
    files: tuple[FileEntry, ...] = Field(min_length=1, max_length=MAX_FILES)

    @model_validator(mode="after")
    def _bounded(self) -> Manifest:
        paths = [f.relative_path for f in self.files]
        if len(set(paths)) != len(paths):
            raise ValueError("duplicate_path")
        if sum(f.bytes for f in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("manifest_too_large")
        return self

    @property
    def digest(self) -> str:
        files = sorted((f.model_dump() for f in self.files), key=lambda f: f["relative_path"])
        return "sha256:" + hashlib.sha256(canonical(files)).hexdigest()

    def by_path(self) -> dict[str, FileEntry]:
        return {f.relative_path: f for f in self.files}


class Card(Wire):
    """Untrusted display metadata: never an instruction, never a credential."""

    summary: str = Field(default="", max_length=2000)
    license: str = Field(default="", max_length=100)
    task: str = Field(default="", max_length=200)
