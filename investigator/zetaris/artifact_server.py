"""Stdlib HTTP server serving logs/<run_id>/exports/ read-only to the Zetaris driver via host.docker.internal
(SPEC.md section 10.2, zetaris_source "captured"). Owner: B3.

Bound to 0.0.0.0:<random free port> (ARTIFACT_SERVER_PORT=0) with path prefix /<run_token>/ (32 hex), GET/HEAD
only, no directory listing, no path traversal, stopped at finalize. Each REST table endpoint is
http://<ARTIFACT_SERVER_HOST>:<port>/<run_token>/<name>.json, so Zetaris computes on exactly the bytes the
Collector hashed. scripts/zetaris_preflight.py verifies the driver container can reach it.

Never started in sample mode (the network guard is active there and nothing needs it).
"""
from __future__ import annotations

import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}$")


class ArtifactServer:
    def __init__(self, exports_dir: Path, host_for_driver: str = "host.docker.internal", port: int = 0,
                 bind_host: str = "0.0.0.0") -> None:
        self.exports_dir = Path(exports_dir).resolve()
        self.host_for_driver = host_for_driver
        self.port = int(port or 0)
        self.bind_host = bind_host
        self.run_token = secrets.token_hex(16)
        self._thread: Optional[threading.Thread] = None
        self._server: Optional[ThreadingHTTPServer] = None
        self.requests: list[dict[str, Any]] = []     # {path, http, bytes} per request (no headers)
        self._lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------------------------------------------
    def start(self) -> int:
        """Start the server thread; return the bound port."""
        if self._server is not None:
            return self.port
        server = ThreadingHTTPServer((self.bind_host, self.port), self._handler_class())
        server.daemon_threads = True
        self._server = server
        self.port = server.server_address[1]
        self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.25},
                                        name=f"artifact-server-{self.port}", daemon=True)
        self._thread.start()
        return self.port

    @property
    def running(self) -> bool:
        return self._server is not None

    def url_for(self, name: str) -> str:
        """http://<host_for_driver>:<port>/<run_token>/<name>.json"""
        return f"http://{self.host_for_driver}:{self.port}/{self.run_token}/{name}.json"

    def local_url_for(self, name: str) -> str:
        """Same resource as seen from this machine (used by the preflight self-check)."""
        return f"http://127.0.0.1:{self.port}/{self.run_token}/{name}.json"

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # -- request handling --------------------------------------------------------------------------------------
    def resolve(self, raw_path: str) -> Optional[Path]:
        """/<token>/<name>.json -> file inside exports_dir, or None (404). No listing, no traversal."""
        path = raw_path.split("?", 1)[0].split("#", 1)[0]
        parts = [p for p in path.split("/") if p]
        if len(parts) != 2 or parts[0] != self.run_token:
            return None
        filename = parts[1]
        if not filename.endswith(".json") or not _NAME_RE.match(filename) or ".." in filename:
            return None
        candidate = (self.exports_dir / filename).resolve()
        if candidate.parent != self.exports_dir or not candidate.is_file():
            return None
        return candidate

    def _record(self, path: str, http: int, size: int) -> None:
        with self._lock:
            if len(self.requests) < 1000:
                self.requests.append({"path": path, "http": http, "bytes": size})

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "x402-artifact-server/1.0"
            sys_version = ""

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - silence stderr
                return

            def _serve(self, head: bool) -> None:
                target = server.resolve(self.path)
                if target is None:
                    body = b'{"error":"not found"}'
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if not head:
                        self.wfile.write(body)
                    server._record(self.path, 404, 0)
                    return
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if not head:
                    self.wfile.write(data)
                server._record(self.path, 200, len(data))

            def do_GET(self) -> None:  # noqa: N802
                self._serve(head=False)

            def do_HEAD(self) -> None:  # noqa: N802
                self._serve(head=True)

            def do_POST(self) -> None:  # noqa: N802
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()
                server._record(self.path, 405, 0)

        return Handler
