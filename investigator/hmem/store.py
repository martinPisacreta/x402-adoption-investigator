"""MemoryStore: add / get / all / update / delete / promote / touch over memories.jsonl, with enrichment (SPEC.md 8.2). Owner: B2.

add() returns the LIVE record and writes a `create` ledger entry (newState = snapshot). Enrichment (port of
engines/hmem/reference/src/memoryStore.ts): classify_domain (tags project:* / tech:* first, keyword scoring
tech/work/personal, else general), namespace <domain>/<profile|facts|notes>, entities cap 10 (explicit entities
first, then extracted terms), related_to cap 5 bidirectional, supersedes when cosine > 0.85 and the correction
regex matches. Persist: one record per line (insertion order), rewritten atomically on mutation.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

from ..clock import Clock
from .embeddings import cosine, tokenize
from .ledger import TrustLedger
from .types import (DEFAULT_CONFIDENCE, ENTITIES_CAP, MEMORY_LAYERS, MEMORY_TYPES, RELATED_TO_CAP, SUPERSEDES_COSINE,
                    Entity, MemoryProvenance, MemoryRecord, clamp01, snapshot)

Embedder = Callable[[str], list[float]]
CORRECTION_RE = re.compile(r"\b(actually|correction|instead|no longer|not anymore|update[sd]?|changed to|now)\b", re.IGNORECASE)
TECH_TERMS: tuple[str, ...] = ("react", "python", "typescript", "javascript", "rust", "sqlite", "postgres", "indexeddb",
                               "node", "docker", "graphql", "redis", "webrtc", "tauri", "electron", "llm", "embedding")
# Investigator vocabulary: the seed pitfall memories live in the `tech` domain, so questions and claim statements
# about x402 sources must classify as tech for the domain signal to fire (a keyword-list extension, not a formula change).
X402_TERMS: tuple[str, ...] = ("x402", "bazaar", "receipts", "receipt", "payer", "payers", "wallet", "wallets", "endpoint",
                               "endpoints", "registry", "registries", "settlement", "settlements", "usdc", "onchain", "catalog",
                               "seller", "sellers", "host", "hosts", "facilitator", "tx", "basescan", "listings", "adoption")
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tech": ("code", "bug", "api", "database", "deploy", "framework", "library", "server", *TECH_TERMS, *X402_TERMS),
    "work": ("meeting", "deadline", "client", "project", "sprint", "review", "report"),
    "personal": ("family", "hobby", "prefer", "like", "dislike", "morning", "evening"),
}
_CAPITALIZED = re.compile(r"\b([A-Z][a-zA-Z]{2,})\b")
_STOP = ("the", "this", "that", "with")
# Domain entities recognised verbatim in text (the seed memories key their gates on these names):
#   field:<name> / source:<family> tokens, camelCase source fields (l30DaysUniquePayers) and source family aliases.
_EXPLICIT_ENTITY = re.compile(r"\b((?:field|source):[A-Za-z0-9_.-]+)")
_CAMEL_FIELD = re.compile(r"\b([a-z]+[0-9]*[A-Z][A-Za-z0-9]*)\b")
SOURCE_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\breceipts?(?:\.json)?\b", re.I), "source:receipts"),
    (re.compile(r"\bcdp_search\b|\bdiscovery search\b|\bbazaar search\b", re.I), "source:cdp_search"),
    (re.compile(r"\bcdp_catalog_snapshot\b|\bcatalog snapshot\b|\bsnapshot\b", re.I), "source:cdp_catalog_snapshot"),
    (re.compile(r"\bcdp_bazaar\b|\bbazaar pages?\b", re.I), "source:cdp_bazaar"),
    (re.compile(r"\bagent402\b", re.I), "source:agent402"),
    (re.compile(r"\bx402[-_ ]trust\b", re.I), "source:x402_trust"),
    (re.compile(r"\bvet402\b", re.I), "source:vet402"),
    (re.compile(r"\bbase_rpc\b|\bbase rpc\b|\bbasescan\b|\bon-?chain\b|\btransfer log\b", re.I), "source:base_rpc"),
    (re.compile(r"\bstore_openapi\b|\bopenapi\b", re.I), "source:store_openapi"),
)

EntityLike = Union[Entity, dict[str, Any], str]


def _as_entity(value: EntityLike) -> Entity:
    if isinstance(value, Entity):
        return value
    if isinstance(value, dict):
        return Entity(name=str(value.get("name", "")), type=str(value.get("type", "")))
    return Entity(name=str(value), type="term")


class MemoryStore:
    FILE = "memories.jsonl"

    def __init__(self, persist_dir: Optional[Path], clock: Clock, ledger: TrustLedger, embedder: Embedder,
                 actor: str = "system") -> None:
        self.persist_dir = Path(persist_dir) if persist_dir is not None else None
        self.path: Optional[Path] = None
        if self.persist_dir is not None:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self.path = self.persist_dir / self.FILE
        self.clock = clock
        self.ledger = ledger
        self.embedder = embedder
        self.actor = actor
        self._records: dict[str, MemoryRecord] = {}
        self._seq = 0
        self._loaded = False

    # -- persistence ------------------------------------------------------------------------------------
    def load(self) -> int:
        """Read memories.jsonl (computing missing embeddings); return the record count. Idempotent."""
        if self._loaded:
            return len(self._records)
        self._loaded = True
        if self.path is not None and self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = MemoryRecord.from_wire(json.loads(line))
                if not record.embedding:
                    record.embedding = self.embedder(record.content)
                self._records[record.id] = record
        self._seq = len(self._records)
        return len(self._records)

    def persist(self) -> None:
        if self.path is None:
            return
        tmp = self.path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            for record in self._records.values():
                handle.write(json.dumps(record.to_wire(), ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, self.path)

    def next_id(self) -> str:
        while True:
            self._seq += 1
            candidate = f"mem-{self._seq}"
            if candidate not in self._records:
                return candidate

    # -- CRUD -------------------------------------------------------------------------------------------
    def add(self, content: str, source: str, confidence: float = DEFAULT_CONFIDENCE, layer: str = "short_term",
            type: str = "general", tags: Iterable[str] = (), entities: Iterable[EntityLike] = (),
            provenance: Optional[MemoryProvenance] = None, derived_from: Iterable[str] = (),
            goal_run_id: Optional[str] = None, supersedes: Optional[str] = None) -> MemoryRecord:
        if not source or not str(source).strip():
            raise ValueError("H-MEM: a memory record needs a non-empty source (rejected by construction)")
        if type not in MEMORY_TYPES:
            raise ValueError(f"H-MEM: unknown memory type {type!r}")
        if layer not in MEMORY_LAYERS:
            raise ValueError(f"H-MEM: unknown memory layer {layer!r}")
        self.load()
        now = self.clock.now_ms()
        tag_list = [str(t) for t in tags]
        domain = self.classify_domain(content, tag_list, type)
        explicit = [_as_entity(e) for e in entities if _as_entity(e).name]
        names = {e.name for e in explicit}
        extracted = [Entity(name=n, type="term") for n in self.extract_entities(content) if n not in names]
        record = MemoryRecord(
            id=self.next_id(), content=content, type=type, layer=layer, timestamp=now, last_accessed=now, access_count=0,
            confidence=clamp01(DEFAULT_CONFIDENCE if confidence is None else float(confidence)), source=str(source),
            tags=tag_list, domain=domain, namespace=self.namespace(domain, type), embedding=self.embedder(content),
            provenance=provenance or MemoryProvenance(origin="import", learned_at=now, label=str(source)),
            entities=(explicit + extracted)[:ENTITIES_CAP], related_to=[], supersedes=supersedes, superseded_by=None,
            derived_from=[str(d) for d in derived_from], goal_run_id=goal_run_id)
        self._discover_relationships(record)
        self._records[record.id] = record
        self.ledger.log(record.id, "create", self.actor, new_state=snapshot(record))
        self.persist()
        return record

    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        self.load()
        return self._records.get(memory_id)

    def must_get(self, memory_id: str) -> MemoryRecord:
        record = self.get(memory_id)
        if record is None:
            raise KeyError(f"H-MEM: unknown memory id {memory_id}")
        return record

    def all(self) -> list[MemoryRecord]:
        self.load()
        return list(self._records.values())

    def update(self, memory_id: str, actor: Optional[str] = None, **changes: Any) -> MemoryRecord:
        record = self.must_get(memory_id)
        previous = snapshot(record)
        for key, value in changes.items():
            if not hasattr(record, key):
                raise AttributeError(f"H-MEM: MemoryRecord has no field {key!r}")
            setattr(record, key, value)
        if "content" in changes:
            record.embedding = self.embedder(record.content)
        self.ledger.log(memory_id, "update", actor or self.actor, previous_state=previous, new_state=snapshot(record),
                        details={"fields": sorted(changes.keys())})
        self.persist()
        return record

    def delete(self, memory_id: str, reason: str, actor: Optional[str] = None) -> None:
        record = self.must_get(memory_id)
        self.ledger.log(memory_id, "delete", actor or self.actor, previous_state=snapshot(record), details={"reason": reason})
        del self._records[memory_id]
        self.persist()

    def promote(self, memory_id: str, layer: str, actor: Optional[str] = None) -> MemoryRecord:
        if layer not in MEMORY_LAYERS:
            raise ValueError(f"H-MEM: unknown memory layer {layer!r}")
        record = self.must_get(memory_id)
        previous = snapshot(record)
        order = {name: i for i, name in enumerate(MEMORY_LAYERS)}
        action = "promote" if order[layer] >= order[record.layer] else "demote"
        record.layer = layer
        self.ledger.log(memory_id, action, actor or self.actor, previous_state=previous, new_state=snapshot(record))
        self.persist()
        return record

    def touch(self, memory_id: str) -> None:
        """lastAccessed = now, accessCount += 1; no ledger entry (reads are logged by retrieval)."""
        record = self.must_get(memory_id)
        record.last_accessed = self.clock.now_ms()
        record.access_count += 1

    def snapshot_all(self) -> dict[str, Any]:
        self.load()
        return {"seq": self._seq, "records": [json.loads(json.dumps(r.to_wire())) for r in self._records.values()]}

    def restore_all(self, records: list[dict[str, Any]], seq: int) -> None:
        self._records = {}
        for data in records:
            record = MemoryRecord.from_wire(data)
            if not record.embedding:
                record.embedding = self.embedder(record.content)
            self._records[record.id] = record
        self._seq = max(seq, len(self._records))
        self._loaded = True
        self.persist()

    # -- enrichment -------------------------------------------------------------------------------------
    @staticmethod
    def classify_domain(content: str, tags: Iterable[str], type: str = "general") -> str:
        for tag in tags:
            if tag.startswith("project:"):
                return tag[len("project:"):]
            if tag.startswith("tech:"):
                return "tech"
        tokens = set(tokenize(content))
        best, best_score = "general", 0
        for domain, keywords in DOMAIN_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in tokens)
            if domain == "personal" and type in ("personal", "preference"):
                score += 1
            if score > best_score:
                best, best_score = domain, score
        return best if best_score > 0 else "general"

    @staticmethod
    def namespace(domain: str, type: str) -> str:
        bucket = "profile" if type in ("preference", "personal") else ("facts" if type == "factual" else "notes")
        return f"{domain}/{bucket}"

    @staticmethod
    def extract_entities(content: str) -> list[str]:
        """Reference heuristics (tech terms + capitalised words) plus the investigator's domain entities, so a claim
        statement or question that names a source field or family shares entities with the seed pitfall memories."""
        found: list[str] = []

        def add(name: str) -> None:
            if name and name not in found:
                found.append(name)

        for match in _EXPLICIT_ENTITY.finditer(content):
            add(match.group(1))
        for match in _CAMEL_FIELD.finditer(content):
            add(f"field:{match.group(1)}")
        for pattern, name in SOURCE_ALIASES:
            if pattern.search(content):
                add(name)
        for token in tokenize(content):
            if token in TECH_TERMS:
                add(token)
        for match in _CAPITALIZED.finditer(content):
            norm = match.group(1).lower()
            if norm not in _STOP:
                add(norm)
        return found[:ENTITIES_CAP]

    def _discover_relationships(self, record: MemoryRecord) -> None:
        names = set(record.entity_names())
        for other in self._records.values():
            if len(record.related_to) >= RELATED_TO_CAP:
                break
            shared = names & set(other.entity_names())
            similarity = cosine(record.embedding or [], other.embedding or [])
            if not record.supersedes and similarity > SUPERSEDES_COSINE and CORRECTION_RE.search(record.content):
                record.supersedes = other.id
                continue
            if shared and other.domain == record.domain:
                record.related_to.append(other.id)
                if len(other.related_to) < RELATED_TO_CAP and record.id not in other.related_to:
                    other.related_to.append(record.id)      # bidirectional link
