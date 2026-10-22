"""H-MEM record types: MemoryRecord (docs shape), MemoryProvenance, LedgerEntry, FEEDBACK_DELTAS and the other
spec constants (SPEC.md section 8.1 and 8.2; engines/hmem/docs/memory-record.md). Owner: B2.

Python fields are snake_case; on disk (memories.jsonl, ledger.jsonl) keys are camelCase exactly as the docs:
to_wire()/from_wire() map them. `score` is runtime only and never persisted; `embedding` may be null on disk
and is recomputed by the store.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

MemoryType = Literal["personal", "factual", "preference", "general"]
MemoryLayer = Literal["short_term", "working", "long_term"]
MemoryOrigin = Literal["chat", "import", "web", "decision", "curiosity", "goal_run", "user_explicit", "inference",
                       "plan_run", "mission", "swarm_run", "tool", "brain"]
FeedbackKind = Literal["helpful", "not_helpful", "wrong"]

MEMORY_TYPES: tuple[str, ...] = ("personal", "factual", "preference", "general")
MEMORY_LAYERS: tuple[str, ...] = ("short_term", "working", "long_term")
LEDGER_ACTIONS: tuple[str, ...] = (
    "create", "read", "update", "delete", "feedback", "promote", "demote", "merge", "conflict_detected",
    "conflict_resolved", "sleep_consolidate", "sleep_archive", "sleep_synthesize", "dream_proposed",
    "dream_approved", "dream_rejected", "restore",
)
FEEDBACK_DELTAS: dict[str, float] = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20}
RETRIEVAL_WEIGHTS: dict[str, float] = {"semantic": 0.35, "keyword": 0.20, "tag": 0.10, "domain": 0.10,
                                       "entity": 0.15, "layer": 0.05, "recency": 0.05, "superseded": -0.20}
LAYER_WEIGHTS: dict[str, float] = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}
DEFAULT_THRESHOLD = 0.35
DEFAULT_TOP_N = 5
DEFAULT_CONFIDENCE = 0.7
CONFLICT_CONFIDENCE_GAP = 0.70
SUPERSEDES_COSINE = 0.85
ENTITIES_CAP = 10
RELATED_TO_CAP = 5
RECENCY_DAYS = 14
CONSOLIDATION_DAYS = 7
ARCHIVE_MIN_DAYS = 30
ARCHIVE_MAX_ACCESS = 2
SYNTHESIS_COSINE = 0.82
DREAM_CLUSTER_MIN_SIZE = 2
DREAM_RELATEDNESS = 0.35
MAX_DREAM_PROPOSALS_PER_TYPE = 8
DAY_MS = 24 * 60 * 60 * 1000
MINING_EVENT_TYPES: tuple[str, ...] = ("chat_message", "user_correction", "file_save", "file_open", "file_edit",
                                       "model_response", "plan_completion")
MINING_TYPE_BY_EVENT: dict[str, str] = {"chat_message": "general", "user_correction": "preference", "file_save": "factual",
                                        "file_open": "general", "file_edit": "factual", "model_response": "factual",
                                        "plan_completion": "factual"}


def clamp01(value: float) -> float:
    return 0.0 if value < 0 else (1.0 if value > 1 else value)


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _snake(name: str) -> str:
    return "".join("_" + ch.lower() if ch.isupper() else ch for ch in name)


def to_wire(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {_camel(f.name): to_wire(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: to_wire(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_wire(v) for v in obj]
    return obj


@dataclass
class MemoryProvenance:
    origin: str
    learned_at: int
    label: str
    refs: dict[str, Any] = field(default_factory=dict)   # sourceUrl?, goalRunId?, chatId?, ...


@dataclass
class Entity:
    name: str
    type: str


@dataclass
class MemoryRecord:
    id: str
    content: str
    type: str
    layer: str
    timestamp: int
    last_accessed: int
    access_count: int
    confidence: float                    # MANDATORY, clamp01, default 0.7
    source: str                          # MANDATORY; the store rejects empty/whitespace
    tags: list[str] = field(default_factory=list)
    domain: Optional[str] = None
    namespace: Optional[str] = None
    embedding: Optional[list[float]] = None
    score: Optional[float] = None        # runtime only, never persisted
    provenance: Optional[MemoryProvenance] = None
    entities: list[Entity] = field(default_factory=list)
    related_to: list[str] = field(default_factory=list)
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    derived_from: list[str] = field(default_factory=list)
    goal_run_id: Optional[str] = None

    def caveat_codes(self) -> list[str]:
        """Caveat codes this (pitfall) memory requires: tags of the form caveat:<code>."""
        return [t.split(":", 1)[1] for t in self.tags if t.startswith("caveat:")]

    def entity_names(self) -> list[str]:
        return [e.name for e in self.entities]

    def to_wire(self) -> dict[str, Any]:
        data = to_wire(self)
        data.pop("score", None)
        return data

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "MemoryRecord":
        kwargs: dict[str, Any] = {}
        names = {f.name for f in dataclasses.fields(cls)}
        for key, value in data.items():
            name = _snake(key)
            if name not in names:
                continue
            if name == "provenance" and isinstance(value, dict):
                value = MemoryProvenance(origin=value.get("origin", "import"), learned_at=int(value.get("learnedAt", 0)),
                                         label=value.get("label", ""), refs=dict(value.get("refs") or {}))
            if name == "entities":
                value = [Entity(name=e["name"], type=e.get("type", "")) if isinstance(e, dict) else Entity(name=str(e), type="")
                         for e in (value or [])]
            kwargs[name] = value
        for list_name in ("tags", "related_to", "derived_from", "entities"):
            if kwargs.get(list_name) is None:
                kwargs[list_name] = []
        kwargs.setdefault("confidence", DEFAULT_CONFIDENCE)
        kwargs.setdefault("access_count", 0)
        kwargs.setdefault("timestamp", 0)
        kwargs.setdefault("last_accessed", kwargs["timestamp"])
        kwargs.setdefault("type", "general")
        kwargs.setdefault("layer", "short_term")
        return cls(**kwargs)


def snapshot(record: MemoryRecord) -> dict[str, Any]:
    """Ledger state snapshot: the record minus the embedding (and the runtime score)."""
    data = record.to_wire()
    data.pop("embedding", None)
    return data


@dataclass
class LedgerEntry:
    memory_id: str
    action: str
    actor: str
    timestamp: int
    previous_state: Optional[dict[str, Any]] = None
    new_state: Optional[dict[str, Any]] = None
    details: Optional[dict[str, Any]] = None

    def to_wire(self) -> dict[str, Any]:
        data = {"memoryId": self.memory_id, "action": self.action, "actor": self.actor, "timestamp": self.timestamp}
        if self.previous_state is not None:
            data["previousState"] = self.previous_state
        if self.new_state is not None:
            data["newState"] = self.new_state
        if self.details is not None:
            data["details"] = self.details
        return data

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "LedgerEntry":
        return cls(memory_id=str(data.get("memoryId", "")), action=str(data.get("action", "")), actor=str(data.get("actor", "")),
                   timestamp=int(data.get("timestamp", 0)), previous_state=data.get("previousState"),
                   new_state=data.get("newState"), details=data.get("details"))


@dataclass
class RetrievalHit:
    memory: MemoryRecord
    score: float
    signals: dict[str, float]


@dataclass
class RetrievalResult:
    memories: list[RetrievalHit]
    reinjection: str
    trace: dict[str, Any]

    def ids(self) -> list[str]:
        return [h.memory.id for h in self.memories]


@dataclass
class ConflictRecord:
    id: str
    a_id: str
    b_id: str
    similarity: float
    confidence_gap: float                # |confidence(a) - confidence(b)|
    status: str = "detected"             # detected | resolved | queued
    reason: str = ""
    confidence: float = 0.0              # detection confidence (section 10.1 modifiers applied)
    resolution: Optional[str] = None     # keep_a | keep_b
    winner: Optional[str] = None
    decision_confidence: Optional[float] = None


@dataclass
class DreamProposal:
    id: str
    type: str                       # insight | domain | invariant
    content: str
    derived_from: list[str]
    status: str = "proposed"        # proposed | approved | rejected
    suggested_domain: Optional[str] = None
