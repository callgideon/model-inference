#!/usr/bin/env python3
"""AP-05's candidate engine for the isolated proofs: `tests/integration/fake_vllm.py`'s app
(its chat, `/v1/models`, faults and `/_control`), serving the model name its vLLM flags name,
plus vLLM's `/metrics` in-flight gauge (`vllm:num_requests_running`), which drain reads.

    python candidate_engine.py --port 57557 --model-dir <installed dir> -- <vLLM flags>

Like vLLM it refuses to start without a model at `--model-dir` (a `config.json`). What the
hosting controller observes of it is real process state: its argv in /proc and the count of
chat requests in flight. Loopback only (fake_vllm's own rule).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fake_vllm  # noqa: E402


class Counting:
    """The app, counting chat requests in flight for `/metrics`."""

    def __init__(self, app: fake_vllm.FakeVllmApp) -> None:
        self.app, self.running = app, 0

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and scope["path"] == "/metrics":
            body = (f'vllm:num_requests_running{{model_name="{self.app.model}"}} '
                    f"{self.running}\n").encode()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": body})
            return
        chat = int(scope["type"] == "http" and scope["path"] == "/v1/chat/completions")
        self.running += chat
        try:
            await self.app(scope, receive, send)
        finally:
            self.running -= chat


def main(argv: list[str]) -> int:
    own, _, flags = _split(argv)
    options = dict(zip(own[::2], own[1::2]))
    port, model_dir = int(options["--port"]), Path(options["--model-dir"])
    if not (model_dir / "config.json").is_file():
        print(f"no model at {model_dir}", file=sys.stderr)
        return 2
    name = flags[flags.index("--served-model-name") + 1] \
        if "--served-model-name" in flags else "unnamed"
    fake_vllm.SERVE(Counting(fake_vllm.FakeVllmApp(model=name)), "127.0.0.1", port)
    return 0


def _split(argv: list[str]) -> tuple[list[str], str, list[str]]:
    if "--" not in argv:
        return argv, "", []
    at = argv.index("--")
    return argv[:at], "--", argv[at + 1:]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
