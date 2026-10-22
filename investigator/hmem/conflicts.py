"""Conflict detection: scan() / auto_resolve() with the 0.70 decision-confidence gate; the loser gets supersededBy; never delete. Owner: B2.

Port of engines/hmem/reference/src/conflictDetection.ts: opposing phrase pairs on similar content (cosine >
0.5) or conflicting numeric values on near-identical content (cosine > 0.8); modifiers +0.1 shared entities,
+0.05 same domain, -0.1 explicit relation. auto_resolve scores both records (recency, access, confidence, length,
origin), decisionConfidence = clamp01(0.6 * detection confidence + 0.8 * margin); >= 0.70 resolves (loser marked
supersededBy, both kept), otherwise the conflict is queued for an operator. Every step is a ledger entry.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from ..clock import Clock
from .embeddings import cosine
from .ledger import TrustLedger
from .store import MemoryStore
from .types import CONFLICT_CONFIDENCE_GAP, ConflictRecord, MemoryRecord, clamp01, snapshot

Embedder = Callable[[str], list[float]]
OPPOSING_PAIRS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"\balways\b", re.I), re.compile(r"\bnever\b", re.I)),
    (re.compile(r"\benable[ds]?\b", re.I), re.compile(r"\bdisable[ds]?\b", re.I)),
    (re.compile(r"\bprefer[s]?\b", re.I), re.compile(r"\bavoid[s]?\b|\bdislike[s]?\b", re.I)),
    (re.compile(r"\bon\b", re.I), re.compile(r"\boff\b", re.I)),
    (re.compile(r"\byes\b", re.I), re.compile(r"\bno\b", re.I)),
)
_NUMBER = re.compile(r"\d+(\.\d+)?")


class Conflicts:
    def __init__(self, store: MemoryStore, ledger: TrustLedger, embedder: Embedder, gap: float = CONFLICT_CONFIDENCE_GAP,
                 clock: Optional[Clock] = None, actor: str = "conflicts") -> None:
        self.store = store
        self.ledger = ledger
        self.embedder = embedder
        self.gap = gap
        self.clock = clock
        self.actor = actor
        self.conflicts: list[ConflictRecord] = []
        self._seq = 0

    def _now(self) -> int:
        return self.clock.now_ms() if self.clock is not None else self.store.clock.now_ms()

    def check(self, a: MemoryRecord, b: MemoryRecord) -> Optional[ConflictRecord]:
        if a.supersedes == b.id or b.supersedes == a.id or a.superseded_by == b.id or b.superseded_by == a.id:
            return None          # an intended replacement is not a conflict
        similarity = cosine(a.embedding or self.embedder(a.content), b.embedding or self.embedder(b.content))
        reason, confidence = "", 0.0
        for p, q in OPPOSING_PAIRS:
            if (p.search(a.content) and q.search(b.content)) or (q.search(a.content) and p.search(b.content)):
                if similarity > 0.5:
                    reason, confidence = "opposing phrase pair on similar content", 0.5 + 0.3 * similarity
        if not reason and similarity > 0.8:
            nums_a = [m.group(0) for m in _NUMBER.finditer(a.content)]
            nums_b = [m.group(0) for m in _NUMBER.finditer(b.content)]
            if nums_a and nums_b and nums_a != nums_b:
                reason, confidence = "high-similarity conflicting numeric values", 0.55 + 0.25 * similarity
        if not reason:
            return None
        if set(a.entity_names()) & set(b.entity_names()):
            confidence += 0.1
        if a.domain == b.domain:
            confidence += 0.05
        if b.id in a.related_to:
            confidence -= 0.1
        self._seq += 1
        return ConflictRecord(id=f"conflict-{self._seq}", a_id=a.id, b_id=b.id, similarity=round(similarity, 6),
                              confidence_gap=round(abs(a.confidence - b.confidence), 6), status="detected", reason=reason,
                              confidence=clamp01(confidence))

    def scan(self) -> list[ConflictRecord]:
        found: list[ConflictRecord] = []
        records = self.store.all()
        for i, a in enumerate(records):
            for b in records[i + 1:]:
                conflict = self.check(a, b)
                if conflict is None:
                    continue
                found.append(conflict)
                self.conflicts.append(conflict)
                self.ledger.log(a.id, "conflict_detected", self.actor, details={
                    "conflictId": conflict.id, "with": b.id, "reason": conflict.reason, "confidence": round(conflict.confidence, 6)})
        return found

    def _score(self, memory: MemoryRecord) -> float:
        now = max(1, self._now())
        origin = memory.provenance.origin if memory.provenance else ""
        return (0.3 * (memory.last_accessed / now) + 0.2 * min(1.0, memory.access_count / 10) + 0.25 * memory.confidence
                + 0.1 * min(1.0, len(memory.content) / 200) + 0.15 * (1.0 if origin in ("user_correction", "user_explicit") else 0.5))

    def auto_resolve(self, conflict: ConflictRecord) -> str:
        """Returns "resolved" when the decision confidence >= 0.70 (loser supersededBy winner, both kept), else "queued"."""
        a = self.store.must_get(conflict.a_id)
        b = self.store.must_get(conflict.b_id)
        sa, sb = self._score(a), self._score(b)
        margin = abs(sa - sb) / max(sa, sb, 1e-9)
        decision = clamp01(conflict.confidence * 0.6 + margin * 0.8)
        conflict.decision_confidence = round(decision, 6)
        if decision < self.gap:
            conflict.status = "queued"
            return "queued"
        winner, loser = (a, b) if sa >= sb else (b, a)
        previous = snapshot(loser)
        loser.superseded_by = winner.id       # kept and penalised in ranking, never deleted
        conflict.status = "resolved"
        conflict.winner = winner.id
        conflict.resolution = "keep_a" if winner.id == conflict.a_id else "keep_b"
        self.ledger.log(loser.id, "conflict_resolved", self.actor, previous_state=previous, new_state=snapshot(loser),
                        details={"conflictId": conflict.id, "winner": winner.id, "decisionConfidence": conflict.decision_confidence})
        self.store.persist()
        return "resolved"

    def queue(self) -> list[ConflictRecord]:
        return [c for c in self.conflicts if c.status != "resolved"]
