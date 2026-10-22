"""Append-only JSONL event log, audit log, observations log, fold-on-load (SPEC.md 9.1). Owner: B2.

Files under persist_dir: world.events.jsonl (Event per line, seq by append order), world.audit.jsonl
(AuditEntry per mutation, strictly increasing seq), world.observations.jsonl (ObservationRecord),
world.merge-queue.json (review proposals). State is a fold over events; views are projections of the fold.
persist_dir=None keeps everything in memory (scratch folds for point-in-time views, tests).

Fold rules (SPEC.md 9.1, port of engines/world-model/reference/src/store.ts plus the fact logic of index.ts):
  upsertEntity   shallow-merges attrs, updates updatedAt/source, records the name as an alias; a placeholder
                 created by a fact (state emerging) becomes active when a real upsert arrives
  upsertContext  shallow-merges attrs
  relate         no-op on an existing id; for types NOT in multi_valued_edge_types it closes open edges with the
                 same (from, type, context) and a different `to` (validTo = new.validFrom, supersededBy = new.id)
  assertFact     dedupes by id, picks the winner by policy (most-recent-wins | highest-confidence-wins |
                 source-priority), marks losers supersededBy, writes entity.attrs[predicate] = winner.value and
                 creates a missing subject with state emerging
  tombstone      sets state archived on an entity (or validTo on a relationship); never deletes
  alias          records aliasId -> primaryId
"""
from __future__ import annotations

import json
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Optional

from .stable_ids import canonical_json
from .types import (EVENT_KINDS, AuditEntry, Context, Entity, Event, Fact, ObservationRecord, Relationship,
                    from_wire, to_wire)


@dataclass
class WorldState:
    entities: dict[str, Entity] = field(default_factory=dict)
    contexts: dict[str, Context] = field(default_factory=dict)
    relationships: dict[str, Relationship] = field(default_factory=dict)
    facts: dict[str, Fact] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)      # alias id -> primary id
    observations: dict[str, ObservationRecord] = field(default_factory=dict)
    facts_index: dict[tuple[str, str], list[str]] = field(default_factory=dict)   # (about, predicate) -> fact ids


def _priority_of(kind: str, ranking: tuple[str, ...]) -> int:
    try:
        return ranking.index(kind)
    except ValueError:
        return 2 ** 31


class EventStore:
    EVENTS = "world.events.jsonl"
    AUDIT = "world.audit.jsonl"
    OBSERVATIONS = "world.observations.jsonl"
    MERGE_QUEUE = "world.merge-queue.json"

    def __init__(self, persist_dir: Optional[Path], multi_valued_edge_types: frozenset[str] = frozenset(),
                 fact_policy: str = "most-recent-wins", source_priority: tuple[str, ...] = ()) -> None:
        self.persist_dir = Path(persist_dir) if persist_dir is not None else None
        self.multi_valued_edge_types = frozenset(multi_valued_edge_types)
        self.fact_policy = fact_policy
        self.source_priority = tuple(source_priority)
        self.seq = 0
        self.audit_seq = 0
        self.events: list[Event] = []
        self.audit_entries: list[AuditEntry] = []
        self.merge_queue: list[dict[str, Any]] = []
        self.state = WorldState()
        self._files: dict[str, IO[str]] = {}
        self._finalizer = weakref.finalize(self, EventStore._close_handles, self._files)
        if self.persist_dir is not None:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    @staticmethod
    def _close_handles(files: dict[str, IO[str]]) -> None:
        for handle in list(files.values()):
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass
        files.clear()

    # -- persistence ------------------------------------------------------------------------------------
    def _path(self, name: str) -> Path:
        assert self.persist_dir is not None
        return self.persist_dir / name

    def _file(self, name: str) -> Optional[IO[str]]:
        if self.persist_dir is None:
            return None
        handle = self._files.get(name)
        if handle is None:
            handle = open(self._path(name), "a", encoding="utf-8", newline="\n")
            self._files[name] = handle
        return handle

    def _write_line(self, name: str, record: dict[str, Any]) -> None:
        handle = self._file(name)
        if handle is not None:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def flush(self) -> None:
        for handle in self._files.values():
            handle.flush()

    def close(self) -> None:
        self.flush()
        EventStore._close_handles(self._files)

    def _load(self) -> None:
        """Fold-on-load: replay world.events.jsonl, then restore audit / observations / merge queue."""
        for event in self.load_events():
            self.seq = max(self.seq, event.seq)
            self.events.append(event)
            self._fold(event)
        audit_path = self._path(self.AUDIT)
        if audit_path.is_file():
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = from_wire(AuditEntry, json.loads(line))
                    self.audit_entries.append(entry)
                    self.audit_seq = max(self.audit_seq, entry.seq)
        obs_path = self._path(self.OBSERVATIONS)
        if obs_path.is_file():
            for line in obs_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = from_wire(ObservationRecord, json.loads(line))
                    self.state.observations[record.external_id] = record
        queue_path = self._path(self.MERGE_QUEUE)
        if queue_path.is_file():
            self.merge_queue = json.loads(queue_path.read_text(encoding="utf-8"))

    def load_events(self) -> list[Event]:
        if self.persist_dir is None or not self._path(self.EVENTS).is_file():
            return []
        events: list[Event] = []
        for line in self._path(self.EVENTS).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            events.append(Event(id=data["id"], seq=int(data["seq"]), kind=data["kind"], payload=data["payload"],
                                at=data["at"], schema_version=int(data.get("schemaVersion", 1))))
        return events

    # -- writes -----------------------------------------------------------------------------------------
    def append(self, kind: str, payload: Any, at: str) -> Event:
        """Append one event line (seq by append order), fold it into the state and return it."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"World Model: unknown event kind {kind!r}")
        wire = payload if isinstance(payload, dict) else to_wire(payload)
        event = Event(id=f"evt_{self.seq + 1}", seq=self.seq + 1, kind=kind, payload=wire, at=at)
        self.seq = event.seq
        self.events.append(event)
        self._fold(event)
        self._write_line(self.EVENTS, {"id": event.id, "seq": event.seq, "kind": event.kind, "payload": event.payload,
                                       "schemaVersion": event.schema_version, "at": event.at})
        return event

    def audit(self, kind: str, actor: str, summary: str, event_id: Optional[str] = None,
              run_id: Optional[str] = None, at: Optional[str] = None) -> AuditEntry:
        """One audit entry per mutation; seq strictly increasing; `at` is the observation time, never wall clock."""
        self.audit_seq += 1
        entry = AuditEntry(seq=self.audit_seq, at=at or "1970-01-01T00:00:00.000Z", kind=kind, actor=actor,
                           summary=summary, event_id=event_id, run_id=run_id)
        self.audit_entries.append(entry)
        self._write_line(self.AUDIT, to_wire(entry))
        return entry

    def record_observation(self, record: ObservationRecord) -> None:
        self.state.observations[record.external_id] = record
        self._write_line(self.OBSERVATIONS, to_wire(record))

    def save_merge_queue(self) -> None:
        if self.persist_dir is not None:
            self._path(self.MERGE_QUEUE).write_text(json.dumps(self.merge_queue, indent=2, ensure_ascii=False) + "\n",
                                                    encoding="utf-8", newline="\n")

    # -- fold -------------------------------------------------------------------------------------------
    def _fold(self, event: Event) -> None:
        kind = event.kind
        payload = event.payload
        if kind == "upsertEntity":
            self._fold_entity(from_wire(Entity, payload))
        elif kind == "upsertContext":
            self._fold_context(from_wire(Context, payload))
        elif kind == "relate":
            self._fold_relationship(from_wire(Relationship, payload))
        elif kind == "assertFact":
            self._fold_fact(from_wire(Fact, payload))
        elif kind == "alias":
            self.state.aliases[payload["aliasId"]] = payload["primaryId"]
        elif kind == "tombstone":
            target = payload.get("targetId")
            if target in self.state.entities:
                self.state.entities[target].state = "archived"
                self.state.entities[target].updated_at = event.at
            elif target in self.state.relationships:
                rel = self.state.relationships[target]
                if not rel.valid_to:
                    rel.valid_to = event.at
        # snapshot and view-rebuild carry no state

    def _fold_entity(self, e: Entity) -> None:
        existing = self.state.entities.get(e.id)
        if existing is None:
            self.state.entities[e.id] = e
            return
        existing.attrs = {**existing.attrs, **e.attrs}
        existing.updated_at = e.updated_at
        existing.source = e.source
        if e.name and e.name not in existing.aliases:
            existing.aliases.append(e.name)
        for alias in e.aliases:
            if alias and alias not in existing.aliases:
                existing.aliases.append(alias)
        if e.name:
            existing.name = e.name
        if existing.state == "emerging" and e.state == "active":
            existing.state = "active"

    def _fold_context(self, c: Context) -> None:
        existing = self.state.contexts.get(c.id)
        if existing is None:
            self.state.contexts[c.id] = c
            return
        existing.attrs = {**existing.attrs, **c.attrs}
        existing.updated_at = c.updated_at
        existing.source = c.source
        if c.started_at and not existing.started_at:
            existing.started_at = c.started_at
        if c.ended_at and not existing.ended_at:
            existing.ended_at = c.ended_at

    def _fold_relationship(self, r: Relationship) -> None:
        if r.type not in self.multi_valued_edge_types:
            for other in self.state.relationships.values():
                if (other.from_id == r.from_id and other.type == r.type and (other.context or "") == (r.context or "")
                        and other.to_id != r.to_id and not other.valid_to and not other.superseded_by):
                    other.valid_to = r.valid_from or r.created_at
                    other.superseded_by = r.id
        if r.id not in self.state.relationships:
            self.state.relationships[r.id] = r

    def _rival_wins(self, rival: Fact, winner: Fact) -> bool:
        if self.fact_policy == "highest-confidence-wins":
            return rival.confidence > winner.confidence
        if self.fact_policy == "source-priority":
            return _priority_of(rival.source.kind, self.source_priority) < _priority_of(winner.source.kind, self.source_priority)
        return rival.asserted_at > winner.asserted_at      # most-recent-wins

    def _fold_fact(self, f: Fact) -> None:
        if f.id in self.state.facts:
            return                                          # content-addressed dedupe
        self.state.facts[f.id] = f
        key = (f.about, f.predicate)
        rivals = [self.state.facts[i] for i in self.state.facts_index.get(key, []) if not self.state.facts[i].superseded_by]
        self.state.facts_index.setdefault(key, []).append(f.id)
        winner = f
        for rival in rivals:
            if self._rival_wins(rival, winner):
                winner = rival
        for candidate in [f, *rivals]:
            if candidate.id != winner.id:
                candidate.superseded_by = winner.id
        entity = self.state.entities.get(f.about)
        if entity is None:
            inferred = f.about.split(":")[0] if ":" in f.about else "entity"
            entity = Entity(type=inferred, id=f.about, attrs={}, source=f.source, created_at=f.asserted_at,
                            updated_at=f.asserted_at, state="emerging")
            self.state.entities[f.about] = entity
        entity.attrs[f.predicate] = winner.value
        entity.updated_at = f.asserted_at

    # -- reads ------------------------------------------------------------------------------------------
    def fold(self, events: list[Event], multi_valued_edge_types: Optional[frozenset[str]] = None,
             policy: Optional[str] = None) -> WorldState:
        """Fold an event list into a fresh state (pure; nothing is persisted)."""
        scratch = EventStore(None, multi_valued_edge_types if multi_valued_edge_types is not None else self.multi_valued_edge_types,
                             policy or self.fact_policy, self.source_priority)
        for event in events:
            scratch._fold(event)
        return scratch.state

    def fold_until(self, at: str) -> WorldState:
        """Replay the log up to and including `at`: the point-in-time (snapshot) view."""
        return self.fold([e for e in self.events if e.at <= at])

    def resolve_alias(self, entity_id: str) -> str:
        current = entity_id
        seen: set[str] = set()
        while current in self.state.aliases and current not in seen:
            seen.add(current)
            current = self.state.aliases[current]
        return current

    def state_shape(self) -> dict[str, Any]:
        """The idempotency diff surface: everything derived from observations, sorted, in wire form."""
        return {
            "entities": [to_wire(e) for e in sorted(self.state.entities.values(), key=lambda x: x.id)],
            "contexts": [to_wire(c) for c in sorted(self.state.contexts.values(), key=lambda x: x.id)],
            "relationships": [to_wire(r) for r in sorted(self.state.relationships.values(), key=lambda x: x.id)],
            "facts": [to_wire(f) for f in sorted(self.state.facts.values(), key=lambda x: x.id)],
            "observations": [to_wire(o) for o in sorted(self.state.observations.values(), key=lambda x: x.external_id)],
        }

    def state_shape_canonical(self) -> str:
        return canonical_json(self.state_shape())

    def counts(self) -> dict[str, int]:
        return {"events": len(self.events), "audit": len(self.audit_entries), "entities": len(self.state.entities),
                "contexts": len(self.state.contexts), "relationships": len(self.state.relationships),
                "facts": len(self.state.facts), "observations": len(self.state.observations),
                "aliases": len(self.state.aliases)}
