"""Dreaming: dream() proposals, approve(id) -> long_term record with lineage, reject(id); nothing materializes before approve. Owner: B2.

Port of engines/hmem/reference/src/dreaming.ts. Proposals (insight clusters, domain reclassifications,
invariants from preferences) are kept in memory and in persist_dir/dreams.json so scripts/hmem_dream.py can
approve or reject across invocations. approve(): insight -> long_term factual, invariant -> long_term preference,
both with derived_from lineage; domain -> updates the domain of the source records (no new record).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..clock import Clock
from .embeddings import cosine, jaccard
from .ledger import TrustLedger
from .store import MemoryStore
from .types import (DREAM_CLUSTER_MIN_SIZE, DREAM_RELATEDNESS, MAX_DREAM_PROPOSALS_PER_TYPE, DreamProposal, MemoryProvenance,
                    MemoryRecord)


class Dreaming:
    FILE = "dreams.json"

    def __init__(self, store: MemoryStore, ledger: TrustLedger, clock: Clock, actor: str = "dreaming") -> None:
        self.store = store
        self.ledger = ledger
        self.clock = clock
        self.actor = actor
        self.proposals: dict[str, DreamProposal] = {}
        self._seq = 0
        self.path: Optional[Path] = Path(store.persist_dir) / self.FILE if store.persist_dir is not None else None
        if self.path is not None and self.path.is_file():
            for data in json.loads(self.path.read_text(encoding="utf-8")):
                proposal = DreamProposal(id=data["id"], type=data["type"], content=data["content"], derived_from=list(data.get("derivedFrom", [])),
                                         status=data.get("status", "proposed"), suggested_domain=data.get("suggestedDomain"))
                self.proposals[proposal.id] = proposal
                try:
                    self._seq = max(self._seq, int(proposal.id.rsplit("-", 1)[1]))
                except (IndexError, ValueError):
                    pass

    def _save(self) -> None:
        if self.path is None:
            return
        data = [{"id": p.id, "type": p.type, "content": p.content, "derivedFrom": p.derived_from, "status": p.status,
                 "suggestedDomain": p.suggested_domain} for p in self.proposals.values()]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    @staticmethod
    def relatedness(a: MemoryRecord, b: MemoryRecord) -> float:
        return (0.5 * max(0.0, cosine(a.embedding or [], b.embedding or [])) + 0.2 * jaccard(a.tags, b.tags)
                + 0.1 * (1.0 if a.domain == b.domain else 0.0) + 0.2 * jaccard(a.entity_names(), b.entity_names()))

    def _clusters(self) -> list[list[MemoryRecord]]:
        records = self.store.all()
        used: set[str] = set()
        clusters: list[list[MemoryRecord]] = []
        for seed in records:
            if seed.id in used:
                continue
            group = [seed]
            for other in records:
                if other.id == seed.id or other.id in used:
                    continue
                if self.relatedness(seed, other) >= DREAM_RELATEDNESS:
                    group.append(other)
            if len(group) >= DREAM_CLUSTER_MIN_SIZE:
                used.update(m.id for m in group)
                clusters.append(group)
        return clusters

    def _propose(self, type_: str, content: str, derived_from: list[str], suggested_domain: Optional[str] = None) -> DreamProposal:
        self._seq += 1
        proposal = DreamProposal(id=f"dream-{self._seq}", type=type_, content=content, derived_from=derived_from,
                                 suggested_domain=suggested_domain)
        self.proposals[proposal.id] = proposal
        self.ledger.log(proposal.id, "dream_proposed", self.actor, details={"type": type_, "derivedFrom": derived_from})
        return proposal

    def dream(self) -> list[DreamProposal]:
        created: list[DreamProposal] = []
        insights = 0
        for cluster in self._clusters():
            if insights >= MAX_DREAM_PROPOSALS_PER_TYPE:
                break
            domains = "/".join(dict.fromkeys(m.domain or "general" for m in cluster))
            created.append(self._propose("insight", f"Across {len(cluster)} related memories ({domains}): " + " + ".join(m.content for m in cluster),
                                         [m.id for m in cluster]))
            insights += 1
        domains_count = 0
        for memory in self.store.all():
            if domains_count >= MAX_DREAM_PROPOSALS_PER_TYPE:
                break
            if memory.domain != "general":
                continue
            suggested = self.store.classify_domain(memory.content, memory.tags, memory.type)
            if suggested != "general":
                created.append(self._propose("domain", f"Reclassify \"{memory.content[:60]}\" as {suggested}", [memory.id], suggested))
                domains_count += 1
        invariants = 0
        prefs = [m for m in self.store.all() if m.type in ("preference", "personal") or "event:user_correction" in m.tags]
        by_domain: dict[str, list[MemoryRecord]] = {}
        for memory in prefs:
            by_domain.setdefault(memory.domain or "general", []).append(memory)
        for domain, members in by_domain.items():
            if invariants >= MAX_DREAM_PROPOSALS_PER_TYPE:
                break
            if len(members) < 2:
                continue
            created.append(self._propose("invariant", f"Stable pattern in {domain}: " + "; ".join(m.content for m in members), [m.id for m in members]))
            invariants += 1
        self._save()
        return created

    def pending(self) -> list[DreamProposal]:
        return [p for p in self.proposals.values() if p.status == "proposed"]

    def approve(self, proposal_id: str, actor: Optional[str] = None) -> Optional[MemoryRecord]:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.status != "proposed":
            raise KeyError(f"H-MEM: no pending proposal {proposal_id}")
        proposal.status = "approved"
        self.ledger.log(proposal_id, "dream_approved", actor or self.actor, details={"type": proposal.type})
        self._save()
        if proposal.type == "domain":
            for memory_id in proposal.derived_from:
                if self.store.get(memory_id) is not None and proposal.suggested_domain:
                    self.store.update(memory_id, actor or self.actor, domain=proposal.suggested_domain)
            return None
        return self.store.add(
            proposal.content, "dreaming", type="factual" if proposal.type == "insight" else "preference", layer="long_term",
            tags=[f"dream:{proposal.type}"], confidence=0.75,
            provenance=MemoryProvenance(origin="inference", learned_at=self.clock.now_ms(), label=f"approved {proposal.type} proposal",
                                        refs={"proposalId": proposal_id}),
            derived_from=proposal.derived_from)

    def reject(self, proposal_id: str, actor: Optional[str] = None) -> None:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.status != "proposed":
            raise KeyError(f"H-MEM: no pending proposal {proposal_id}")
        proposal.status = "rejected"          # logged, never materialised
        self.ledger.log(proposal_id, "dream_rejected", actor or self.actor, details={"type": proposal.type})
        self._save()
