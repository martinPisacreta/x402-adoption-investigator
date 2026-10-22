"""World Model record types: Provenance, Entity, Context, Relationship, Fact, Event, AuditEntry,
ObservationRecord, IngestObservation, IngestReport and the four write events (SPEC.md section 9.1). Owner: B2.

Wire keys are camelCase exactly as in the Meterless reference (engines/world-model/reference/src/types.ts);
Python fields are snake_case and to_wire()/from_wire() map between them (None members are dropped on the
wire). Every write carries provenance: Provenance.validate() raises the exact engine message otherwise.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

NO_PROVENANCE_MESSAGE = "World Model: every write carries provenance (kind + at). No provenance, no write."
PROVENANCE_KINDS: tuple[str, ...] = ("registry", "receipts", "chain", "agent", "manual")
EntityState = Literal["emerging", "active", "archived"]
EventKind = Literal["upsertEntity", "upsertContext", "relate", "assertFact", "snapshot", "tombstone", "alias", "view-rebuild"]
EVENT_KINDS: tuple[str, ...] = ("upsertEntity", "upsertContext", "relate", "assertFact", "snapshot", "tombstone", "alias", "view-rebuild")
FactPolicy = Literal["most-recent-wins", "highest-confidence-wins", "source-priority"]

_WIRE_RENAMES = {"from_id": "from", "to_id": "to", "run_id": "runId"}
_WIRE_REVERSE = {v: k for k, v in _WIRE_RENAMES.items()}


def snake_to_camel(name: str) -> str:
    if name in _WIRE_RENAMES:
        return _WIRE_RENAMES[name]
    head, *rest = name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def camel_to_snake(name: str) -> str:
    if name in _WIRE_REVERSE:
        return _WIRE_REVERSE[name]
    out = []
    for ch in name:
        out.append("_" + ch.lower() if ch.isupper() else ch)
    return "".join(out)


def to_wire(obj: Any) -> Any:
    """Dataclass -> camelCase dict, dropping None members (the reference drops undefined). Dict keys are kept."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result: dict[str, Any] = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            if value is None:
                continue
            result[snake_to_camel(f.name)] = to_wire(value)
        return result
    if isinstance(obj, dict):
        return {k: to_wire(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, (list, tuple)):
        return [to_wire(v) for v in obj]
    return obj


def from_wire(cls: type, data: dict[str, Any]) -> Any:
    """camelCase dict -> dataclass (nested Provenance handled for the `source` field)."""
    kwargs: dict[str, Any] = {}
    names = {f.name for f in dataclasses.fields(cls)}
    for key, value in data.items():
        name = camel_to_snake(key)
        if name not in names:
            continue
        if name == "source" and isinstance(value, dict):
            value = from_wire(Provenance, value)
        kwargs[name] = value
    return cls(**kwargs)


def iso_at(value: Any) -> str:
    """Normalise a provenance timestamp to an ISO-8601 string (datetimes are converted, strings kept)."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        value = value.astimezone(_dt.timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"
    return str(value)


@dataclass
class Provenance:
    kind: str
    at: str
    by: Optional[str] = None
    url: Optional[str] = None
    checksum: Optional[str] = None       # "sha256:<hex>"
    run_id: Optional[str] = None

    def validate(self) -> None:
        if not self.kind or self.at is None or self.at == "":
            raise ValueError(NO_PROVENANCE_MESSAGE)

    def normalized(self) -> "Provenance":
        """Validated copy with `at` as an ISO string (the engine stores provenance in this form)."""
        self.validate()
        return Provenance(kind=self.kind, at=iso_at(self.at), by=self.by, url=self.url, checksum=self.checksum,
                          run_id=self.run_id)


@dataclass
class Entity:
    type: str
    id: str
    attrs: dict[str, Any]
    source: Provenance
    created_at: str
    updated_at: str
    schema_version: int = 1
    name: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    state: EntityState = "active"


@dataclass
class Context:
    type: str
    id: str
    attrs: dict[str, Any]
    source: Provenance
    created_at: str
    updated_at: str
    parent: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    schema_version: int = 1


@dataclass
class Relationship:
    id: str
    from_id: str
    type: str
    to_id: str
    source: Provenance
    created_at: str
    context: Optional[str] = None
    attrs: Optional[dict[str, Any]] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    schema_version: int = 1
    superseded_by: Optional[str] = None


@dataclass
class Fact:
    id: str
    about: str
    predicate: str
    value: Any
    source: Provenance
    asserted_at: str
    confidence: float = 0.5
    context: Optional[str] = None
    superseded_by: Optional[str] = None


@dataclass
class Tombstone:
    target_id: str
    at: str
    reason: Optional[str] = None
    by: Optional[str] = None


@dataclass
class Alias:
    alias_id: str
    primary_id: str
    merged_at: str
    merged_by: str
    reason: str


@dataclass
class Event:
    id: str                      # "evt_<seq>"
    seq: int
    kind: EventKind
    payload: Any                 # wire-form dict of the record (camelCase keys)
    at: str
    schema_version: int = 1


@dataclass
class AuditEntry:
    seq: int
    at: str
    kind: str
    actor: str
    summary: str
    event_id: Optional[str] = None
    run_id: Optional[str] = None


@dataclass
class ObservationRecord:
    id: str                      # "obs_<sha256(externalId)[:12]>"
    external_id: str
    checksum: str
    at: str
    synced_run_id: str
    stale: bool = False


@dataclass
class IngestObservation:
    external_id: str             # "<family>:<request_key>"
    at: str
    source: Provenance
    payload: Any
    type: str = "observation"    # the source family (domain adapters) or the entity type (generic adapter)
    title: str = ""              # the Observation name (e.g. "rpc_balance_0x...") - adapters may read it
    text: str = ""
    entities: list[dict[str, str]] = field(default_factory=list)   # generic adapter: [{type, name}] mentions


@dataclass
class IngestReport:
    run_id: str                  # "run_<sha256(canonical_json(sorted externalIds))[:12]>"
    processed: int
    skipped_unchanged: int
    events_appended: int
    changed: bool
    step_timings_ms: dict[str, int] = field(default_factory=dict)
    quarantined: list[dict[str, Any]] = field(default_factory=list)     # [{externalId, error}]
    details: list[dict[str, Any]] = field(default_factory=list)         # per observation: externalId, status, events

    def summary(self) -> dict[str, Any]:
        """The five-key object written into output.world (no timings, so sample runs stay byte-identical)."""
        return {"runId": self.run_id, "processed": self.processed, "skippedUnchanged": self.skipped_unchanged,
                "eventsAppended": self.events_appended, "changed": self.changed}

    def to_wire(self) -> dict[str, Any]:
        return {"runId": self.run_id, "processed": self.processed, "skippedUnchanged": self.skipped_unchanged,
                "eventsAppended": self.events_appended, "changed": self.changed,
                "stepTimingsMs": dict(self.step_timings_ms), "quarantined": list(self.quarantined)}


# ---- write events emitted by adapters -------------------------------------------------------------------

@dataclass
class UpsertEntity:
    type: str
    id: str                      # "" -> name-keyed: the engine derives the id from `name` and runs resolution
    attrs: dict[str, Any]
    source: Provenance
    name: Optional[str] = None
    aliases: list[str] = field(default_factory=list)
    state: EntityState = "active"


@dataclass
class UpsertContext:
    type: str
    id: str
    attrs: dict[str, Any]
    source: Provenance
    parent: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None


@dataclass
class Relate:
    from_id: str
    type: str
    to_id: str
    source: Provenance
    context: Optional[str] = None
    attrs: Optional[dict[str, Any]] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


@dataclass
class AssertFact:
    about: str
    predicate: str
    value: Any
    source: Provenance
    confidence: float = 0.5
    context: Optional[str] = None


WriteEvent = Union[UpsertEntity, UpsertContext, Relate, AssertFact]
