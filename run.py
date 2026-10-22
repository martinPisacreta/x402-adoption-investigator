#!/usr/bin/env python3
"""Programmatic entry for the x402 Adoption Investigator (SPEC.md section 4).

    python run.py --input examples/01-seller-audit-textokit/input.json       # sample mode when no keys configured
    python run.py --sample --input examples/ex1.json                         # force sample mode (offline, no keys)
    cat input.json | python run.py --input -                                 # stdin
    python run.py --serve 8080                                               # HTTP on 127.0.0.1: POST /run, same JSON contract
    python run.py --serve 8080 --host 0.0.0.0                                # inside a container / published port

Input: {"run_id": "...", "input": {"question": "...", "focus": {...}, "budget": {...}, "options": {...}}}.
Output: {"run_id", "status": "ok|partial|error", "output": {...}} or {"run_id", "status": "error", "error": {...}}.
Exit code 0 for ok/partial, 1 for error. Stdout carries the JSONL event stream (identical to
logs/<run_id>/run.jsonl); --print-output appends the final JSON after a `---output---` line; --quiet prints only
the final JSON. Secrets come only from environment variables (.env.local, gitignored); sample mode needs none.
"""
from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from investigator import __version__  # noqa: E402
from investigator.config import RunConfig, build_config  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description="x402 Adoption Investigator: bounded multi-agent run.")
    parser.add_argument("--input", "-i", metavar="FILE", help="JSON file with {run_id?, input}; use - for stdin")
    parser.add_argument("--run-id", dest="run_id", help="override the run_id (examples pin theirs)")
    parser.add_argument("--mode", choices=["sample", "live", "auto"], default="auto",
                        help="auto (default): sample when no keys are configured; live otherwise")
    parser.add_argument("--sample", action="store_true", help="shorthand for --mode sample (offline, fixtures only)")
    parser.add_argument("--require-zetaris", dest="require_zetaris", action="store_true",
                        help="live: fail with E_ZETARIS_REQUIRED instead of falling back to SQLite")
    parser.add_argument("--require-generator", dest="require_generator", action="store_true",
                        help="live: fail with E_GENERATOR instead of falling back to rules/templates")
    parser.add_argument("--zetaris-source", dest="zetaris_source", choices=["captured", "direct"],
                        help="override input.options.zetaris_source")
    parser.add_argument("--catalog", choices=["pages", "snapshot"], help="override input.options.catalog")
    parser.add_argument("--out", metavar="DIR", help="logs directory (default: logs/ or LOG_DIR)")
    parser.add_argument("--output", metavar="FILE", help="also write the final JSON envelope to this file")
    parser.add_argument("--record-transcripts", dest="record_transcripts", action="store_true",
                        help="live: copy generator transcripts into fixtures/transcripts/<example>/")
    parser.add_argument("--print-output", dest="print_output", action="store_true",
                        help="print the final JSON after the event stream, separated by ---output---")
    parser.add_argument("--quiet", action="store_true", help="print only the final JSON (no event stream)")
    parser.add_argument("--verbose", action="store_true", help="human-readable progress on stderr")
    parser.add_argument("--keep-zetaris-objects", dest="keep_zetaris_objects", action="store_true",
                        help="live: do not drop the per-run Zetaris database/container at finalize")
    parser.add_argument("--hmem-sleep", dest="hmem_sleep", action="store_true",
                        help="live: execute the H-MEM sleep cycle after the preview")
    parser.add_argument("--example", help="transcripts folder under fixtures/transcripts (default: by run_id)")
    parser.add_argument("--serve", type=int, metavar="PORT", help="start an HTTP server: POST /run with the same JSON body")
    parser.add_argument("--host", default="127.0.0.1", metavar="ADDR",
                        help="--serve bind address (default 127.0.0.1; use 0.0.0.0 inside a container)")
    parser.add_argument("--version", action="version", version=f"x402-adoption-investigator {__version__}")
    return parser


def read_input(source: str) -> Any:
    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    return json.loads(text)


def dump(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, indent=2, sort_keys=False, ensure_ascii=False)


MAX_REQUEST_BYTES = 1_000_000
DRAIN_LIMIT_BYTES = 32 * MAX_REQUEST_BYTES   # an oversize body is read and discarded up to this, then the socket closes


def build_server(port: int, config: RunConfig, host: str = "127.0.0.1") -> HTTPServer:
    """Minimal stdlib HTTP server. POST /run answers with the run envelope: 200 for ok/partial and 422 for status
    error (a JSON body that fails the input schema included); 400 for a body that is not JSON; 413 for a body over
    MAX_REQUEST_BYTES; 404 for any other path. GET /health -> {"ok": true}. Binds the loopback interface unless
    --host says otherwise."""

    class Handler(BaseHTTPRequestHandler):
        server_version = f"x402-adoption-investigator/{__version__}"

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = dump(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._send(200, {"ok": True, "version": __version__, "mode": config.mode})
            else:
                self._send(404, {"error": {"code": "E_INPUT", "message": "use POST /run"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/run":
                self._send(404, {"error": {"code": "E_INPUT", "message": "use POST /run"}})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_REQUEST_BYTES:
                remaining = min(length, DRAIN_LIMIT_BYTES)      # drain so the client sees the 413, not a reset
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                self.close_connection = True
                self._send(413, {"run_id": "invalid-input", "status": "error", "error": {
                    "code": "E_INPUT", "stage": "input", "message": f"body of {length} bytes exceeds {MAX_REQUEST_BYTES}",
                    "retryable": False, "details": {}}})
                return
            raw = self.rfile.read(length) if length else b""
            try:
                payload = json.loads(raw.decode("utf-8") or "{}")
            except ValueError as exc:
                self._send(400, {"run_id": "invalid-input", "status": "error", "error": {
                    "code": "E_INPUT", "stage": "input", "message": f"body is not JSON: {exc}",
                    "retryable": False, "details": {}}})
                return
            envelope = run_investigation(payload, config, echo_stdout=not config.quiet)
            self._send(200 if envelope["status"] != "error" else 422, envelope)

        def log_message(self, fmt: str, *args: Any) -> None:  # keep stdout clean for the event stream
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    return HTTPServer((host, port), Handler)


def serve(port: int, config: RunConfig, host: str = "127.0.0.1") -> int:
    """Run build_server() until interrupted."""
    httpd = build_server(port, config, host)
    sys.stderr.write(f"x402-adoption-investigator serving on http://{host}:{port}/run (mode {config.mode})\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    config = build_config(args)
    if args.serve:
        return serve(args.serve, config, host=args.host)
    if not args.input:
        parser.error("--input FILE (or -) is required unless --serve is given")
    try:
        payload = read_input(args.input)
    except (OSError, ValueError) as exc:
        envelope = {"run_id": args.run_id or "invalid-input", "status": "error", "error": {
            "code": "E_INPUT", "stage": "input", "message": f"cannot read input: {exc}", "retryable": False,
            "details": {"input": args.input}}}
        print(dump(envelope))
        return 1
    envelope = run_investigation(payload, config, run_id_override=args.run_id, echo_stdout=not config.quiet)
    if config.quiet:
        print(dump(envelope))
    elif config.print_output:
        print("---output---")
        print(dump(envelope))
    if config.output_path:
        config.output_path.parent.mkdir(parents=True, exist_ok=True)
        config.output_path.write_text(dump(envelope) + "\n", encoding="utf-8", newline="\n")
    return 0 if envelope.get("status") in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
