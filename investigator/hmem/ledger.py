"""TrustLedger: append-only ledger.jsonl with the canonical 17 actions; log / history / all / stats (SPEC.md 8.2). Owner: B2.

Never pruned (no rotation implemented; documented as such). Entries carry previousState / newState snapshots
(record minus embedding) so confidence changes can be reconstructed (vector la-001). Existing entries (the
committed seed ledger copied into the run dir) are loaded on start and kept.
"""
from __future__ import annotations

import json
import weakref
from pathlib import Path
from typing import IO, Any, Optional

from ..clock import Clock
from .types import LEDGER_ACTIONS, LedgerEntry


class TrustLedger:
    FILE = "ledger.jsonl"

    def __init__(self, persist_dir: Optional[Path], clock: Optional[Clock] = None) -> None:
        self.persist_dir = Path(persist_dir) if persist_dir is not None else None
        self.clock = clock
        self.path: Optional[Path] = None
        self._entries: list[LedgerEntry] = []
        self._handle: Optional[IO[str]] = None
        self._handles: list[IO[str]] = []
        self._finalizer = weakref.finalize(self, TrustLedger._close_all, self._handles)
        if self.persist_dir is not None:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self.path = self.persist_dir / self.FILE
            if self.path.is_file():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        self._entries.append(LedgerEntry.from_wire(json.loads(line)))

    def _now(self) -> int:
        return self.clock.now_ms() if self.clock is not None else 0

    def log(self, memory_id: str, action: str, actor: str, *, previous_state: Optional[dict[str, Any]] = None,
            new_state: Optional[dict[str, Any]] = None, details: Optional[dict[str, Any]] = None,
            timestamp: Optional[int] = None) -> LedgerEntry:
        if action not in LEDGER_ACTIONS:
            raise ValueError(f"unknown ledger action {action!r}")
        entry = LedgerEntry(memory_id=memory_id, action=action, actor=actor,
                            timestamp=int(timestamp) if timestamp is not None else self._now(),
                            previous_state=previous_state, new_state=new_state, details=details)
        self._entries.append(entry)
        if self.path is not None:
            if self._handle is None:
                self._handle = open(self.path, "a", encoding="utf-8", newline="\n")
                self._handles.append(self._handle)
            self._handle.write(json.dumps(entry.to_wire(), ensure_ascii=False, separators=(",", ":")) + "\n")
            self._handle.flush()
        return entry

    @staticmethod
    def _close_all(handles: list[IO[str]]) -> None:
        for handle in handles:
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass
        handles.clear()

    def history(self, memory_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if e.memory_id == memory_id]

    def all(self) -> list[LedgerEntry]:
        return list(self._entries)

    def stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for entry in self._entries:
            counts[entry.action] = counts.get(entry.action, 0) + 1
        return {"entries": len(self._entries), "by_action": dict(sorted(counts.items())),
                "memories": len({e.memory_id for e in self._entries})}

    def close(self) -> None:
        TrustLedger._close_all(self._handles)
        self._handle = None
