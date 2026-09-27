"""The local judge fake on the j2 task-local port (57512): the only host J2 may send to until P-10.

POST /batches {submit_key, items} -> {"id"} (one batch per submit key); GET /batches?submit_key
-> {"id"} | 404; GET /batches/<id> -> {"done", "items": [[sample_id, output]], "cost"}.
`mode`: ok, reject (422), slow (accept, then answer after `delay_s`: a lost answer), error
(accept, then answer 500 carrying the id), drop (accept nothing, answer late).
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from infrx.contracts.tasklocal import local_services

PORT = local_services("j2")["judge-fake"].host_port


class JudgeFake:
    def __init__(self, port: int = PORT) -> None:
        self.mode, self.delay_s = "ok", 0.0
        self.posts: list[dict] = []
        self.batches: dict[str, str] = {}
        self.outputs: dict[str, list[list[str]]] = {}
        self.cost = "0.01000000"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _send(self, status: int, body: object) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.posts.append(body)
                if fake.mode == "reject":
                    return self._send(422, {"error": "invalid batch"})
                if fake.mode == "drop":
                    time.sleep(fake.delay_s)
                    return self._send(504, {})
                batch = fake.batches.setdefault(body["submit_key"],
                                                f"batch-{len(fake.batches) + 1}")
                if fake.mode == "slow":
                    time.sleep(fake.delay_s)
                self._send(500 if fake.mode == "error" else 200, {"id": batch})

            def do_GET(self) -> None:
                url = urlsplit(self.path)
                if url.path == "/batches":
                    key = parse_qs(url.query).get("submit_key", [""])[0]
                    batch = fake.batches.get(key)
                    return self._send(200, {"id": batch}) if batch else self._send(404, {})
                batch = url.path.rsplit("/", 1)[-1]
                self._send(200, {"done": True, "items": fake.outputs.get(batch, []),
                                 "cost": fake.cost})

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
