"""Recorded transient faults replayed in sample mode (SPEC.md 7.4 rule 9, fixtures/transient/README.md). Owner: B1.

`fixtures/transient/<example>/faults.json` lists, per request key, attempts that fail the way a live request can
fail: an HTTP status, a timeout or a connection reset. FixtureFetcher raises that failure on that attempt and
serves the committed fixture on any attempt the file does not list, so the retry policy (sources/retry.py) decides,
exactly as it does for live requests, whether to retry, how long to back off and when to give up. The folder is
chosen like the transcripts folder: the example whose input.json carries the run_id, or `--example`.

A fault never changes the bytes served: a request that succeeds after a retry returns the same Observation (same
sha256, payload and captured_at) as without the fault. Every file says whether its faults were captured from a
real source or written by hand (`simulated: true`), and the run's sample_mode_notice repeats it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .retry import AttemptFailed, connection_failure, http_failure, timeout_failure

FAULTS_FILE = "faults.json"
KINDS: tuple[str, ...] = ("http", "timeout", "connection_reset")


@dataclass(frozen=True)
class Fault:
    request_key: str
    attempt: int
    kind: str
    http: Optional[int] = None
    note: str = ""

    def failure(self) -> AttemptFailed:
        if self.kind == "http":
            return http_failure(int(self.http or 0))
        if self.kind == "timeout":
            return timeout_failure()
        return connection_failure("ConnectionResetError: connection reset by peer")


@dataclass
class FaultScript:
    path: Path
    rel_path: str                   # fixtures/transient/<example>/faults.json (what the logs name)
    simulated: bool
    note: str
    faults: dict[str, dict[int, Fault]] = field(default_factory=dict)

    def for_key(self, request_key: str) -> dict[int, Fault]:
        return self.faults.get(request_key, {})

    def count(self) -> int:
        return sum(len(v) for v in self.faults.values())


def parse_faults(data: Any, path: Path, rel_path: str) -> FaultScript:
    """Validate a faults.json document; a malformed file is a ValueError naming the entry (never ignored)."""
    if not isinstance(data, dict) or not isinstance(data.get("faults"), list):
        raise ValueError(f"{rel_path}: expected an object with a `faults` list")
    if not isinstance(data.get("simulated"), bool):
        raise ValueError(f"{rel_path}: `simulated` must be true or false (say whether the faults were captured)")
    script = FaultScript(path=path, rel_path=rel_path, simulated=data["simulated"], note=str(data.get("note", "")))
    for index, entry in enumerate(data["faults"]):
        where = f"{rel_path}: faults[{index}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("request_key"), str) or not entry["request_key"]:
            raise ValueError(f"{where}: `request_key` is required")
        attempt = entry.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError(f"{where}: `attempt` must be an integer >= 1")
        kind = entry.get("kind")
        if kind not in KINDS:
            raise ValueError(f"{where}: `kind` must be one of {', '.join(KINDS)}")
        code = entry.get("http")
        if kind == "http" and (not isinstance(code, int) or isinstance(code, bool) or not 400 <= code <= 599):
            raise ValueError(f"{where}: an http fault needs `http` between 400 and 599")
        by_attempt = script.faults.setdefault(entry["request_key"], {})
        if attempt in by_attempt:
            raise ValueError(f"{where}: attempt {attempt} of {entry['request_key']} is listed twice")
        by_attempt[attempt] = Fault(request_key=entry["request_key"], attempt=attempt, kind=kind,
                                    http=code if kind == "http" else None, note=str(entry.get("note", "")))
    return script


def load_faults(transient_dir: Path, example: Optional[str], repo_root: Optional[Path] = None) -> Optional[FaultScript]:
    """The fault script of `example`, or None when the example has none (examples 01-04 have none)."""
    if not example:
        return None
    path = Path(transient_dir) / example / FAULTS_FILE
    if not path.is_file():
        return None
    try:
        rel_path = path.resolve().relative_to(Path(repo_root).resolve()).as_posix() if repo_root else path.name
    except ValueError:
        rel_path = f"fixtures/transient/{example}/{FAULTS_FILE}"
    return parse_faults(json.loads(path.read_text(encoding="utf-8")), path, rel_path)
