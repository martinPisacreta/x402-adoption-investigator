"""WorldModel: upsertEntity / upsertContext / relate / assertFact / tombstone / alias, ingest(), policy hooks (SPEC.md 9). Owner: B2.

Python port of engines/world-model/reference/src/index.ts + ingest.ts (stdlib only, single process, one writer):
- every write goes through normalize_provenance(): no provenance, no write (prov-001);
- externally keyed entities use the literal `type:system:id` grammar; name-keyed entities (UpsertEntity.id == "")
  go through the resolution pass (Levenshtein bands 0.85 auto-merge / [0.82, 0.85) review queue);
- relate is content-addressed (re-adding the same edge is a no-op), bitemporal closing lives in the fold;
- assertFact is content-addressed; the claim policy hook runs on every fact of kind "agent" BEFORE anything is
  appended (this is where "every claim links to evidence" becomes structural);
- ingest is idempotent: observation checksum unchanged -> skippedUnchanged; the second pass of the same list
  appends 0 events and reports changed false; adapter failures are quarantined, never fatal;
- nothing is ever deleted: tombstones archive, superseding edges close, losing facts get supersededBy.
Dropped on purpose (docs/METERLESS.md): stream clustering / story aggregates / score snapshots, embedding
search, migrations, control plane, write queues, mutable entity.aggregates.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Callable, Optional

from ..clock import Clock
from .domain import MULTI_VALUED_EDGE_TYPES, claim_policy
from .resolve import AUTO_MERGE_AT, REVIEW_LOWER, decide, name_similarity
from .stable_ids import (canonical_json, context_id_from_attrs, entity_id_from_name, external_id, fact_id,
                         ingest_run_id, observation_record_id, relationship_id, sha256_hex)
from .store import EventStore, WorldState
from .types import (AssertFact, Context, Entity, Fact, IngestObservation, IngestReport, ObservationRecord,
                    Provenance, Relate, Relationship, UpsertContext, UpsertEntity, WriteEvent, to_wire)

Adapter = Callable[[IngestObservation], list[WriteEvent]]
STALE_AFTER_DAYS = 30


def _iso_days_before(iso: str, days: int) -> str:
    text = iso[:-1] + "+00:00" if iso.endswith("Z") else iso
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    earlier = parsed - _dt.timedelta(days=days)
    return earlier.strftime("%Y-%m-%dT%H:%M:%S.") + f"{earlier.microsecond // 1000:03d}Z"


def observation_checksum(obs: IngestObservation) -> str:
    """Dedupe key: externalId + at + payload (+ title/text/entities for generic observations), canonical."""
    return sha256_hex(canonical_json({
        "externalId": obs.external_id, "at": obs.at, "title": obs.title, "text": obs.text,
        "entities": obs.entities, "payload": obs.payload, "sourceChecksum": obs.source.checksum}))


def generic_adapter(obs: IngestObservation) -> list[WriteEvent]:
    """The reference's own ingest shape: one item entity per observation plus `mentions` edges to the named
    entities (name-keyed, so the resolution pass applies). Used when no domain adapter claims the family."""
    source = Provenance(kind=obs.source.kind, at=obs.at, by=obs.source.by, url=obs.source.url,
                        checksum=obs.source.checksum, run_id=obs.source.run_id)
    item_type = obs.type if obs.type and obs.type != "observation" else "item"
    item_id = external_id(item_type, "ingest", obs.external_id)
    events: list[WriteEvent] = [UpsertEntity(type=item_type, id=item_id, attrs={"title": obs.title, "text": obs.text},
                                             source=source, name=obs.title or None)]
    for mention in obs.entities or []:
        mtype = str(mention.get("type") or "entity")
        mname = str(mention.get("name") or "")
        if not mname:
            continue
        events.append(UpsertEntity(type=mtype, id="", attrs={}, source=source, name=mname))
        events.append(Relate(from_id=item_id, type="mentions", to_id=entity_id_from_name(mtype, mname), source=source,
                             valid_from=source.at))
    return events


class WorldModel:
    def __init__(self, persist_dir: Optional[Path], clock: Clock, run_id: str, fact_policy: str = "most-recent-wins",
                 policy_hook: Optional[Callable[[Fact, str], None]] = claim_policy,
                 source_priority: tuple[str, ...] = (), auto_merge_at: float = AUTO_MERGE_AT,
                 review_lower: float = REVIEW_LOWER) -> None:
        self.store = EventStore(persist_dir, MULTI_VALUED_EDGE_TYPES, fact_policy, source_priority)
        self.clock = clock
        self.run_id = run_id
        self.fact_policy = fact_policy
        self.policy_hook = policy_hook
        self.auto_merge_at = auto_merge_at
        self.review_lower = review_lower
        self.policy_rejections: list[dict[str, Any]] = []

    # -- state ------------------------------------------------------------------------------------------
    @property
    def state(self) -> WorldState:
        return self.store.state

    @property
    def multi_valued_edge_types(self) -> frozenset[str]:
        return MULTI_VALUED_EDGE_TYPES

    def resolve(self, entity_id: str) -> str:
        return self.store.resolve_alias(entity_id)

    def events(self) -> list[Any]:
        return list(self.store.events)

    def audit(self) -> list[Any]:
        return list(self.store.audit_entries)

    def merge_queue(self) -> list[dict[str, Any]]:
        return list(self.store.merge_queue)

    def state_shape(self) -> dict[str, Any]:
        return self.store.state_shape()

    def state_shape_canonical(self) -> str:
        return self.store.state_shape_canonical()

    def counts(self) -> dict[str, int]:
        return self.store.counts()

    def flush(self) -> None:
        self.store.flush()

    def close(self) -> None:
        self.store.close()

    # -- provenance -------------------------------------------------------------------------------------
    @staticmethod
    def normalize_provenance(source: Optional[Provenance]) -> Provenance:
        if source is None or not isinstance(source, Provenance):
            raise ValueError("World Model: every write carries provenance (kind + at). No provenance, no write.")
        return source.normalized()

    # -- writes -----------------------------------------------------------------------------------------
    def apply(self, event: WriteEvent) -> Any:
        if isinstance(event, UpsertEntity):
            return self.upsert_entity(event)
        if isinstance(event, UpsertContext):
            return self.upsert_context(event)
        if isinstance(event, Relate):
            return self.relate(event)
        if isinstance(event, AssertFact):
            return self.assert_fact(event)
        raise TypeError(f"World Model: unknown write event {type(event).__name__}")

    def upsert_entity(self, event: UpsertEntity) -> str:
        """Returns the (alias-resolved) entity id. An empty id means name-keyed: resolution pass + hashed id."""
        source = self.normalize_provenance(event.source)
        if not event.id:
            return self.upsert_entity_by_name(event.type, event.name or "", event.attrs, source, event.state, event.aliases)
        return self._fold_entity_upsert(event.id, event, source, event.name)

    def upsert_entity_by_name(self, type_: str, name: str, attrs: Optional[dict[str, Any]] = None,
                              source: Optional[Provenance] = None, state: str = "active",
                              aliases: Optional[list[str]] = None) -> str:
        if not name:
            raise ValueError("World Model: name-keyed entities need a name (or provide an id)")
        source = self.normalize_provenance(source)
        new_id = entity_id_from_name(type_, name)
        event = UpsertEntity(type=type_, id=new_id, attrs=dict(attrs or {}), source=source, name=name,
                             aliases=list(aliases or []), state=state)  # type: ignore[arg-type]
        for other in list(self.state.entities.values()):
            if other.type != type_ or other.id == new_id or not other.name:
                continue
            similarity = name_similarity(name, other.name)
            decision = decide(similarity, self.auto_merge_at, self.review_lower)
            if decision == "auto-merge":
                self.store.append("alias", {"aliasId": new_id, "primaryId": other.id, "mergedAt": source.at,
                                            "mergedBy": "resolver", "reason": f"similarity {similarity:.3f}"}, source.at)
                self.store.audit("auto-merge", "resolver", f"{name} -> {other.name} ({similarity:.3f})", None, None, source.at)
                return self._fold_entity_upsert(other.id, event, source, name)
            if decision == "review":
                already = any((p["leftId"] == new_id and p["rightId"] == other.id) or (p["leftId"] == other.id and p["rightId"] == new_id)
                              for p in self.store.merge_queue)
                if not already:
                    self.store.merge_queue.append({
                        "id": f"merge_{len(self.store.merge_queue) + 1}", "leftId": new_id, "rightId": other.id,
                        "leftName": name, "rightName": other.name, "similarity": similarity,
                        "reason": "name similarity in review band [0.82, 0.85)", "status": "pending",
                        "proposedAt": source.at})
                    self.store.save_merge_queue()
                    self.store.audit("merge-proposed", "resolver", f"{name} ~ {other.name} ({similarity:.3f}) queued",
                                     None, None, source.at)
        return self._fold_entity_upsert(new_id, event, source, name)

    def _fold_entity_upsert(self, entity_id: str, event: UpsertEntity, source: Provenance, name: Optional[str]) -> str:
        resolved = self.store.resolve_alias(entity_id)
        aliases = [a for a in event.aliases if a]
        if name and name not in aliases:
            aliases.insert(0, name)
        entity = Entity(type=event.type, id=resolved, attrs=dict(event.attrs or {}), source=source, created_at=source.at,
                        updated_at=source.at, name=name, aliases=aliases, state=event.state)
        appended = self.store.append("upsertEntity", entity, source.at)
        self.store.audit("upsertEntity", source.by or "system", f"{resolved} ({event.type})", appended.id, source.run_id, source.at)
        return resolved

    def upsert_context(self, event: UpsertContext) -> str:
        source = self.normalize_provenance(event.source)
        context_id = event.id or context_id_from_attrs(event.type, event.attrs or {}, event.parent)
        context = Context(type=event.type, id=context_id, attrs=dict(event.attrs or {}), source=source, created_at=source.at,
                          updated_at=source.at, parent=event.parent, started_at=event.started_at, ended_at=event.ended_at)
        appended = self.store.append("upsertContext", context, source.at)
        self.store.audit("upsertContext", source.by or "system", f"{context_id} ({event.type})", appended.id, source.run_id, source.at)
        return context_id

    def relate(self, event: Relate) -> Relationship:
        source = self.normalize_provenance(event.source)
        from_id = self.store.resolve_alias(event.from_id)
        to_id = self.store.resolve_alias(event.to_id)
        rel_id = relationship_id(from_id, event.type, to_id, event.context, event.valid_from)
        existing = self.state.relationships.get(rel_id)
        if existing is not None:
            return existing                                   # content-addressed no-op
        rel = Relationship(id=rel_id, from_id=from_id, type=event.type, to_id=to_id, source=source, created_at=source.at,
                           context=event.context, attrs=dict(event.attrs) if event.attrs else None,
                           valid_from=event.valid_from or source.at, valid_to=event.valid_to)
        appended = self.store.append("relate", rel, source.at)
        self.store.audit("relate", source.by or "system", f"{from_id} -[{event.type}]-> {to_id}", appended.id, source.run_id, source.at)
        return self.state.relationships[rel_id]

    def assert_fact(self, event: AssertFact) -> Fact:
        source = self.normalize_provenance(event.source)
        about = self.store.resolve_alias(event.about)
        fid = fact_id(about, event.predicate, event.value, source.at, source.kind, source.url)
        existing = self.state.facts.get(fid)
        if existing is not None:
            return existing                                   # content-addressed no-op
        fact = Fact(id=fid, about=about, predicate=event.predicate, value=event.value, source=source, asserted_at=source.at,
                    confidence=0.5 if event.confidence is None else float(event.confidence), context=event.context)
        if self.policy_hook is not None:
            try:
                self.policy_hook(fact, self.run_id)
            except ValueError as exc:
                self.policy_rejections.append({"about": about, "predicate": event.predicate, "error": str(exc)})
                raise
        if about not in self.state.entities:
            inferred = about.split(":")[0] if ":" in about else "entity"
            placeholder = Entity(type=inferred, id=about, attrs={}, source=source, created_at=source.at, updated_at=source.at,
                                 state="emerging")
            self.store.append("upsertEntity", placeholder, source.at)
        appended = self.store.append("assertFact", fact, source.at)
        winner_value = self.state.entities[about].attrs.get(event.predicate)
        self.store.audit("assertFact", source.by or "system",
                         f"{about}.{event.predicate} = {json.dumps(winner_value, ensure_ascii=False, default=str)} ({self.fact_policy})",
                         appended.id, source.run_id, source.at)
        return self.state.facts[fid]

    def tombstone(self, target_id: str, reason: str, by: str, at: Optional[str] = None) -> None:
        when = at or self.clock.now_iso()
        appended = self.store.append("tombstone", {"targetId": target_id, "reason": reason, "at": when, "by": by}, when)
        self.store.audit("tombstone", by, f"{target_id}: {reason}", appended.id, self.run_id, when)

    def alias(self, alias_id: str, primary_id: str, reason: str, by: str, at: Optional[str] = None) -> None:
        when = at or self.clock.now_iso()
        appended = self.store.append("alias", {"aliasId": alias_id, "primaryId": primary_id, "mergedAt": when,
                                               "mergedBy": by, "reason": reason}, when)
        self.store.audit("alias", by, f"{alias_id} -> {primary_id}: {reason}", appended.id, self.run_id, when)

    # -- operator ---------------------------------------------------------------------------------------
    def approve_merge(self, proposal_id: str, by: str) -> None:
        for proposal in self.store.merge_queue:
            if proposal["id"] == proposal_id and proposal["status"] == "pending":
                at = self.clock.now_iso()
                proposal.update({"status": "approved", "resolvedAt": at, "resolvedBy": by})
                self.store.append("alias", {"aliasId": proposal["leftId"], "primaryId": proposal["rightId"], "mergedAt": at,
                                            "mergedBy": by, "reason": proposal["reason"]}, at)
                self.store.audit("merge-approved", by, f"{proposal['leftName']} -> {proposal['rightName']}", None, None, at)
                self.store.save_merge_queue()
                return
        raise ValueError(f"World Model: no pending merge proposal {proposal_id}")

    def reject_merge(self, proposal_id: str, by: str) -> None:
        for proposal in self.store.merge_queue:
            if proposal["id"] == proposal_id and proposal["status"] == "pending":
                at = self.clock.now_iso()
                proposal.update({"status": "rejected", "resolvedAt": at, "resolvedBy": by})
                self.store.audit("merge-rejected", by, f"{proposal['leftName']} stays separate from {proposal['rightName']}", None, None, at)
                self.store.save_merge_queue()
                return
        raise ValueError(f"World Model: no pending merge proposal {proposal_id}")

    # -- ingest -----------------------------------------------------------------------------------------
    def ingest(self, observations: list[IngestObservation], adapter_for: Optional[Callable[[str], Optional[Adapter]]] = None) -> IngestReport:
        """Batch ingest: checksum dedupe, adapter events, fold, observation records; returns the IngestReport.

        adapter_for(family) returns the adapter for an observation's `type` (None -> generic_adapter). Adapter
        exceptions quarantine that observation (listed in the report), never the batch."""
        run_id = ingest_run_id([o.external_id for o in observations])
        timings: dict[str, int] = {}
        before = self.store.state_shape_canonical()
        events_before = len(self.store.events)

        t0 = self.clock.now_ms()
        latest = max((o.at for o in observations), default="")
        cutoff = _iso_days_before(latest, STALE_AFTER_DAYS) if latest else ""
        for record in self.state.observations.values():
            record.stale = bool(cutoff) and record.at < cutoff
        timings["1-reconcile-stale"] = self.clock.now_ms() - t0

        t0 = self.clock.now_ms()
        fresh: list[tuple[IngestObservation, str]] = []
        skipped: list[IngestObservation] = []
        for obs in observations:
            checksum = observation_checksum(obs)
            existing = self.state.observations.get(obs.external_id)
            if existing is not None and existing.checksum == checksum:
                skipped.append(obs)
            else:
                fresh.append((obs, checksum))
        timings["2-classify"] = self.clock.now_ms() - t0

        t0 = self.clock.now_ms()
        quarantined: list[dict[str, Any]] = []
        details: list[dict[str, Any]] = [{"externalId": o.external_id, "status": "skipped_unchanged", "events": 0} for o in skipped]
        processed = 0
        for obs, checksum in fresh:
            adapter = adapter_for(obs.type) if adapter_for is not None else None
            if adapter is None:
                adapter = generic_adapter
            count_before = len(self.store.events)
            try:
                writes = adapter(obs)
                for write in writes:
                    self.apply(write)
            except Exception as exc:  # noqa: BLE001 - quarantined, visible in the report, never fatal
                quarantined.append({"externalId": obs.external_id, "error": f"{type(exc).__name__}: {exc}"})
                details.append({"externalId": obs.external_id, "status": "quarantined", "events": len(self.store.events) - count_before})
                continue
            appended = len(self.store.events) - count_before
            self.store.record_observation(ObservationRecord(id=observation_record_id(obs.external_id), external_id=obs.external_id,
                                                            checksum=checksum, at=obs.at, synced_run_id=run_id, stale=False))
            details.append({"externalId": obs.external_id, "status": "processed" if appended else "no_events", "events": appended})
            processed += 1
        timings["3-persist"] = self.clock.now_ms() - t0

        t0 = self.clock.now_ms()
        after = self.store.state_shape_canonical()
        timings["4-diff"] = self.clock.now_ms() - t0
        report = IngestReport(run_id=run_id, processed=processed, skipped_unchanged=len(skipped),
                              events_appended=len(self.store.events) - events_before, changed=before != after,
                              step_timings_ms=timings, quarantined=quarantined, details=details)
        self.store.audit("ingest", "pipeline", f"run {run_id}: {processed} processed, {len(skipped)} skipped, {len(quarantined)} quarantined",
                         None, run_id, observations[0].at if observations else self.clock.now_iso())
        self.store.flush()
        return report

    # -- exports ----------------------------------------------------------------------------------------
    def export(self, out_dir: Path) -> dict[str, str]:
        """world-entities.json, world-relationships.json, world-facts.json (canonical JSON, deterministic order)."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        shape = self.store.state_shape()
        paths: dict[str, str] = {}
        for name, key in (("world-entities.json", "entities"), ("world-relationships.json", "relationships"),
                          ("world-facts.json", "facts"), ("world-contexts.json", "contexts")):
            path = out / name
            path.write_text(canonical_json(shape[key]) + "\n", encoding="utf-8", newline="\n")
            paths[name] = str(path)
        return paths
