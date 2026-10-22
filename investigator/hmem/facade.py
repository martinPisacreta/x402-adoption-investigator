"""HMEM(clock, persist_dir, miner_fn) facade exposing store / ledger / retrieval / mining / sleep / conflicts / dreaming
and the shortcuts add / query / feedback / policy_memories / mine (SPEC.md 8.2). Owner: B2.

The runner mounts it with mount_hmem(clock, hmem_dir) after init_hmem_dir() copied memory/seed into the run dir
(sample mode) or into memory/state (live). Sample mode passes no miner_fn, so no model is ever called there.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ..clock import Clock
from .conflicts import Conflicts
from .dreaming import Dreaming
from .embeddings import embed
from .ledger import TrustLedger
from .mining import MemoryMining
from .retrieval import MemoryRetrieval
from .sleep import Sleep
from .store import MemoryStore
from .types import DEFAULT_THRESHOLD, DEFAULT_TOP_N, MemoryProvenance, MemoryRecord, RetrievalResult


class HMEM:
    def __init__(self, clock: Clock, persist_dir: Optional[Path], miner_fn: Optional[Callable[[str], str]] = None,
                 actor: str = "system") -> None:
        self.clock = clock
        self.persist_dir = Path(persist_dir) if persist_dir is not None else None
        self.embedder = embed
        self.actor = actor
        self.ledger = TrustLedger(self.persist_dir, clock)
        self.store = MemoryStore(self.persist_dir, clock, self.ledger, self.embedder, actor=actor)
        self.retrieval = MemoryRetrieval(self.store, self.ledger, self.embedder, clock)
        self.mining = MemoryMining(self.store, miner_fn)
        self.sleep = Sleep(self.store, self.ledger, clock)
        self.conflicts = Conflicts(self.store, self.ledger, self.embedder, clock=clock)
        self.dreaming = Dreaming(self.store, self.ledger, clock)

    def load(self) -> int:
        return self.store.load()

    def add(self, content: str, source: str, **kwargs: Any) -> MemoryRecord:
        return self.store.add(content, source, **kwargs)

    def query(self, text: str, *, threshold: float = DEFAULT_THRESHOLD, top_n: int = DEFAULT_TOP_N,
              actor: str = "system", tags: Iterable[str] = (), entities: Iterable[str] = ()) -> RetrievalResult:
        return self.retrieval.query(text, threshold=threshold, top_n=top_n, actor=actor, tags=tags, entities=entities)

    def feedback(self, memory_id: str, kind: str, actor: str = "system", reason: str = "") -> MemoryRecord:
        return self.retrieval.feedback(memory_id, kind, actor, reason)

    def policy_memories(self, fields: Iterable[str], sources: Iterable[str],
                        metrics: Iterable[str] = ()) -> list[MemoryRecord]:
        return self.retrieval.policy_memories(fields, sources, metrics)

    def mine(self, event_type: str, text: str, *, source: str, goal_run_id: Optional[str] = None,
             provenance: Optional[MemoryProvenance] = None, **kwargs: Any) -> list[MemoryRecord]:
        return self.mining.mine(event_type, text, source=source, goal_run_id=goal_run_id, provenance=provenance, **kwargs)

    def sleep_preview(self) -> dict[str, Any]:
        return self.sleep.preview()

    @staticmethod
    def trace_line(result: RetrievalResult) -> dict[str, Any]:
        """The hmem.trace.jsonl line for a retrieval (SPEC.md section 5)."""
        return dict(result.trace)

    @staticmethod
    def feedback_trace_line(actor: str, memory_id: str, kind: str, previous: float, new: float) -> dict[str, Any]:
        return {"actor": actor, "memoryId": memory_id, "kind": kind, "previous_confidence": round(previous, 6), "new_confidence": round(new, 6)}

    def close(self) -> None:
        self.ledger.close()


def mount_hmem(clock: Clock, persist_dir: Path, miner_fn: Optional[Callable[[str], str]] = None, actor: str = "system") -> HMEM:
    """Build and load an HMEM over a prepared persist dir (seed already copied there by the runner)."""
    hmem = HMEM(clock, persist_dir, miner_fn, actor=actor)
    hmem.load()
    return hmem
