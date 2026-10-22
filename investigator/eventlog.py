"""EventLog: the run.jsonl writer (SPEC.md section 5).

Every event is one JSON line {seq, at, run_id, type, agent, payload} appended to logs/<run_id>/run.jsonl and
mirrored to stdout. One seq counter is shared by the side files (fetch.jsonl, sql.jsonl, zetaris.jsonl,
hmem.trace.jsonl): a side line repeats the seq of its companion run.jsonl line, so log://<run_id>#<seq> is
unambiguous. Human-readable progress never goes to stdout; stderr only with --verbose (see runner.py).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any, Optional

from .clock import Clock

EVENT_TYPES: tuple[str, ...] = (
    "guard", "run_start", "plan", "handoff", "fetch", "retry", "recovery", "ingest", "verify", "crosscheck",
    "sql", "claim", "conflict", "critique", "narrative_map", "memory", "timeout", "result", "error",
)
SIDE_FILES: tuple[str, ...] = ("fetch", "sql", "zetaris", "hmem.trace", "world.events", "world.audit", "world.observations")


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        from .contracts import to_json
        return to_json(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


def dumps_line(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=_json_default)


class EventLog:
    """Append-only JSONL event writer with a single seq counter."""

    RUN_FILE = "run.jsonl"

    def __init__(self, run_id: str, run_dir: Path, clock: Clock, echo_stdout: bool = True,
                 stdout: Optional[IO[str]] = None) -> None:
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.clock = clock
        self.echo_stdout = echo_stdout
        self._stdout = stdout or sys.stdout
        self._files: dict[str, IO[str]] = {}
        self.seq = 0
        self.counts: dict[str, int] = {}
        self.run_dir.mkdir(parents=True, exist_ok=True)

    # -- files -------------------------------------------------------------------------------------------
    def _file(self, name: str) -> IO[str]:
        if not name.endswith(".jsonl"):
            name = f"{name}.jsonl"
        handle = self._files.get(name)
        if handle is None:
            handle = open(self.run_dir / name, "a", encoding="utf-8", newline="\n")
            self._files[name] = handle
        return handle

    def path(self, name: str = RUN_FILE) -> Path:
        return self.run_dir / (name if name.endswith(".jsonl") else f"{name}.jsonl")

    # -- events ------------------------------------------------------------------------------------------
    def emit(self, type: str, agent: str, payload: dict[str, Any]) -> int:
        """Append one run.jsonl line (and echo it to stdout). Returns the seq."""
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}; see SPEC.md section 5")
        self.seq += 1
        self.counts[type] = self.counts.get(type, 0) + 1
        line = {"seq": self.seq, "at": self.clock.now_iso(), "run_id": self.run_id,
                "type": type, "agent": agent, "payload": payload}
        text = dumps_line(line)
        handle = self._file(self.RUN_FILE)
        handle.write(text + "\n")
        handle.flush()
        if self.echo_stdout:
            self._stdout.write(text + "\n")
            self._stdout.flush()
        return self.seq

    def ref(self, seq: int) -> str:
        return f"log://{self.run_id}#{seq}"

    def side(self, name: str, line: dict[str, Any]) -> None:
        """Append to a side file; the seq defaults to the last emitted run.jsonl seq."""
        record = dict(line)
        record.setdefault("seq", self.seq)
        handle = self._file(name)
        handle.write(dumps_line(record) + "\n")
        handle.flush()

    def flush(self) -> None:
        for handle in self._files.values():
            handle.flush()
        if self.echo_stdout:
            self._stdout.flush()

    def close(self) -> None:
        self.flush()
        for handle in self._files.values():
            handle.close()
        self._files.clear()

    def files_written(self) -> list[str]:
        return sorted(p.name for p in self.run_dir.glob("*.jsonl"))
