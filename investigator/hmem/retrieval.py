"""MemoryRetrieval: the 8-signal formula, threshold on the FINAL score, reinjection block, trace, feedback (SPEC.md 8.2). Owner: B2.

score = clamp01(0.35*semantic + 0.20*keyword + 0.10*tag + 0.10*domain + 0.15*entity + 0.05*layer + 0.05*recency
        - 0.20*superseded) * confidence. One `read` ledger entry per hit; reinjection grouped by domain; the trace
line goes to hmem.trace.jsonl. feedback(): helpful +0.05 and accessCount += 1; not_helpful -0.03; wrong -0.20 and
tag "review" (once); clamp01; `feedback` ledger entry with previous/new state and details {kind, delta, reason}.
policy_memories(fields, sources, metrics): deterministic index over records tagged pitfall whose entities contain
field:<f>, source:<s> or metric:<id> (no embedding involved), used by the Analyst (caveats) and Critic C3.
"""
from __future__ import annotations

import hashlib
import math
from typing import Any, Callable, Iterable

from ..clock import Clock
from .embeddings import cosine, jaccard, tokenize
from .ledger import TrustLedger
from .store import MemoryStore
from .types import (DAY_MS, DEFAULT_THRESHOLD, DEFAULT_TOP_N, FEEDBACK_DELTAS, LAYER_WEIGHTS, RECENCY_DAYS,
                    RETRIEVAL_WEIGHTS, MemoryRecord, RetrievalHit, RetrievalResult, clamp01, snapshot)

Embedder = Callable[[str], list[float]]
LAYER_MARKERS = {"long_term": "LT", "working": "WK", "short_term": "ST"}


class MemoryRetrieval:
    def __init__(self, store: MemoryStore, ledger: TrustLedger, embedder: Embedder, clock: Clock) -> None:
        self.store = store
        self.ledger = ledger
        self.embedder = embedder
        self.clock = clock
        self.weights = dict(RETRIEVAL_WEIGHTS)

    # -- scoring ----------------------------------------------------------------------------------------
    def signals(self, memory: MemoryRecord, query_embedding: list[float], query_tokens: list[str], query_entities: list[str],
                query_domain: str, query_tags: list[str], now_ms: int) -> dict[str, float]:
        age_days = max(0.0, (now_ms - memory.last_accessed) / DAY_MS)
        return {
            "semantic": clamp01(cosine(query_embedding, memory.embedding or self.embedder(memory.content))),
            "keyword": jaccard(query_tokens, tokenize(memory.content)),
            "tag": jaccard([t.lower() for t in query_tokens + query_tags], [t.lower() for t in memory.tags]),
            "domain": 1.0 if memory.domain == query_domain else 0.0,
            "entity": jaccard(query_entities, memory.entity_names()),
            "layer": LAYER_WEIGHTS.get(memory.layer, 0.6),
            "recency": math.exp(-age_days / RECENCY_DAYS),
            "superseded": 1.0 if memory.superseded_by else 0.0,
        }

    def score_from_signals(self, signals: dict[str, float], confidence: float) -> float:
        raw = sum(self.weights[name] * signals[name] for name in self.weights)
        return clamp01(raw) * confidence

    def score_one(self, memory: MemoryRecord, query: str) -> float:
        prepared = self._prepare(query, (), ())
        return self.score_from_signals(self.signals(memory, *prepared, self.clock.now_ms()), memory.confidence)

    def _prepare(self, text: str, tags: Iterable[str], entities: Iterable[str]) -> tuple[list[float], list[str], list[str], str, list[str]]:
        query_tokens = tokenize(text)[:8]
        query_entities = list(dict.fromkeys([*self.store.extract_entities(text), *[str(e) for e in entities]]))
        return self.embedder(text), query_tokens, query_entities, self.store.classify_domain(text, [], "general"), [str(t) for t in tags]

    # -- query ------------------------------------------------------------------------------------------
    def query(self, text: str, *, threshold: float = DEFAULT_THRESHOLD, top_n: int = DEFAULT_TOP_N, actor: str,
              tags: Iterable[str] = (), entities: Iterable[str] = ()) -> RetrievalResult:
        query_embedding, query_tokens, query_entities, query_domain, query_tags = self._prepare(text, tags, entities)
        now_ms = self.clock.now_ms()
        scored: list[RetrievalHit] = []
        for memory in self.store.all():
            signals = self.signals(memory, query_embedding, query_tokens, query_entities, query_domain, query_tags, now_ms)
            score = self.score_from_signals(signals, memory.confidence)
            if score >= threshold:
                scored.append(RetrievalHit(memory=memory, score=score, signals=signals))
        scored.sort(key=lambda h: (-h.score, h.memory.id))
        top = scored[:top_n]
        for hit in top:
            hit.memory.score = hit.score
            self.store.touch(hit.memory.id)
            self.ledger.log(hit.memory.id, "read", actor, details={"query": text, "score": round(hit.score, 6)})
        if top:
            self.store.persist()
        reinjection = self.reinjection_block(top)
        strategy = "minimal" if len(top) <= 1 else ("personal" if any(h.memory.type in ("personal", "preference") for h in top) else "comprehensive")
        trace = {
            "actor": actor, "query": text, "threshold": threshold, "topN": top_n,
            "hits": [{"memoryId": h.memory.id, "score": round(h.score, 6),
                      "signals": {k: round(v, 6) for k, v in h.signals.items()}, "confidence": round(h.memory.confidence, 6)} for h in top],
            "candidates": len(scored),
            "reinjection_sha256": hashlib.sha256(reinjection.encode("utf-8")).hexdigest(),
            "retrievalReason": f"hybrid ranking for {text!r} (domain: {query_domain})",
            "strategy": strategy,
        }
        return RetrievalResult(memories=top, reinjection=reinjection, trace=trace)

    # -- feedback ---------------------------------------------------------------------------------------
    def feedback(self, memory_id: str, kind: str, actor: str, reason: str = "") -> MemoryRecord:
        if kind not in FEEDBACK_DELTAS:
            raise ValueError(f"unknown feedback kind {kind!r}")
        memory = self.store.must_get(memory_id)
        previous = snapshot(memory)
        delta = FEEDBACK_DELTAS[kind]
        if kind == "helpful":
            memory.access_count += 1
        memory.confidence = clamp01(memory.confidence + delta)
        if kind == "wrong" and "review" not in memory.tags:
            memory.tags.append("review")
        self.ledger.log(memory_id, "feedback", actor, previous_state=previous, new_state=snapshot(memory),
                        details={"kind": kind, "delta": delta, "reason": reason})
        self.store.persist()
        return memory

    # -- policy index -----------------------------------------------------------------------------------
    def policy_memories(self, fields: Iterable[str], sources: Iterable[str],
                        metrics: Iterable[str] = ()) -> list[MemoryRecord]:
        wanted = {f"field:{f}" for f in fields} | {f"source:{s}" for s in sources} | {f"metric:{m}" for m in metrics}
        out = [m for m in self.store.all() if "pitfall" in m.tags and wanted & set(m.entity_names())]
        return sorted(out, key=lambda m: m.id)

    # -- reinjection ------------------------------------------------------------------------------------
    @staticmethod
    def reinjection_block(hits: list[Any]) -> str:
        if not hits:
            return ""
        by_domain: dict[str, list[MemoryRecord]] = {}
        for hit in hits:
            memory = hit.memory if isinstance(hit, RetrievalHit) else hit
            by_domain.setdefault(memory.domain or "general", []).append(memory)
        lines = ["[MEMORY CONTEXT]"]
        for domain in sorted(by_domain):
            lines.append(f"## {domain}")
            for memory in by_domain[domain]:
                marker = LAYER_MARKERS.get(memory.layer, "ST")
                names = memory.entity_names()
                ents = f" (entities: {', '.join(names)})" if names else ""
                lines.append(f"- [{marker}] {memory.content}{ents} <{memory.id}>")
        lines.append("[/MEMORY CONTEXT]")
        return "\n".join(lines)
