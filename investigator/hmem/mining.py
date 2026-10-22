"""MemoryMining: minerFn injection, strict JSON array parser, model-free fallback (SPEC.md 8.2). Owner: B2.

mine(event_type, text, *, source, goal_run_id, provenance) -> records. miner_fn(prompt) -> str must return a
strict JSON array (the parser strips code fences); a non-array answer falls back to the model-free path and the
fallback is visible in `last_note`. The default model-free fallback extracts one `factual` record per statement
line (sample mode: one line per accepted claim, "<subject> | <statement>"), which IS the spec default when no
generator is given. Sample mode never calls a model: the runner mounts no miner_fn there.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Optional

from .store import EntityLike, MemoryStore
from .types import MINING_EVENT_TYPES, MINING_TYPE_BY_EVENT, Entity, MemoryProvenance, MemoryRecord

_FENCE_OPEN = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_CLOSE = re.compile(r"\s*```\s*$")
_SUBJECT_LINE = re.compile(r"^(?P<subject>[a-z][a-z0-9_-]*:[a-z0-9_.-]+:[^\s|]+)\s*\|\s*(?P<statement>.+)$", re.IGNORECASE)
MIN_LEN = 5              # miner strings (reference parseStrictJsonArray)
FALLBACK_MIN_LEN = 20    # model-free candidates (reference mineModelFree: 20..300 chars)
MAX_LEN = 300
MAX_CANDIDATES = 10
MAX_TEXT = 8000


def parse_json_array(text: str) -> list[Any]:
    """Strict JSON array; tolerant of surrounding code fences and whitespace; anything else raises ValueError."""
    cleaned = _FENCE_CLOSE.sub("", _FENCE_OPEN.sub("", text.strip())).strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end > start:
        cleaned = cleaned[start:end + 1]
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise ValueError(f"miner output is not JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("miner output must be a JSON array")
    return data


def parse_strict_json_array(text: str) -> list[str]:
    """The reference's lenient variant: strings of >= 5 chars, [] on any failure."""
    try:
        data = parse_json_array(text)
    except ValueError:
        return []
    return [x for x in data if isinstance(x, str) and len(x.strip()) >= MIN_LEN]


class MemoryMining:
    def __init__(self, store: MemoryStore, miner_fn: Optional[Callable[[str], str]] = None) -> None:
        self.store = store
        self.miner_fn = miner_fn
        self.last_note: Optional[str] = None

    def mine(self, event_type: str, text: str, *, source: str, goal_run_id: Optional[str] = None,
             provenance: Optional[MemoryProvenance] = None, entities: Iterable[EntityLike] = (),
             layer: Optional[str] = None, confidence: float = 0.8, tags: Iterable[str] = ()) -> list[MemoryRecord]:
        if event_type not in MINING_EVENT_TYPES:
            raise ValueError(f"unknown mining event type {event_type!r}")
        self.last_note = None
        text = text[:MAX_TEXT]
        candidates: list[dict[str, Any]]
        used_model = False
        if self.miner_fn is not None:
            prompt = f"Extract durable memories from this {event_type} as a strict JSON array of strings:\n\n{text}"
            raw = self.miner_fn(prompt)
            try:
                items = parse_json_array(raw)
                candidates = [{"content": x.strip(), "entities": []} for x in items if isinstance(x, str) and len(x.strip()) >= MIN_LEN]
                used_model = True
                if not candidates:
                    self.last_note = "miner returned an empty array; nothing mined"
            except ValueError as exc:
                self.last_note = f"miner output rejected ({exc}); model-free fallback used"
                candidates = self.fallback_candidates(text)
        else:
            candidates = self.fallback_candidates(text)
        base_entities = [e for e in entities]
        records: list[MemoryRecord] = []
        for candidate in candidates[:MAX_CANDIDATES]:
            ents: list[EntityLike] = [*base_entities, *[Entity(name=n, type="subject") for n in candidate.get("entities", [])]]
            records.append(self.store.add(
                candidate["content"], source, confidence=confidence, layer=layer or ("working" if used_model else "short_term"),
                type=MINING_TYPE_BY_EVENT[event_type], tags=[f"event:{event_type}", *tags], entities=ents,
                provenance=provenance or MemoryProvenance(origin="goal_run" if event_type == "plan_completion" else "inference",
                                                          learned_at=self.store.clock.now_ms(), label=f"mined from {event_type}"),
                goal_run_id=goal_run_id))
        return records

    @staticmethod
    def fallback_candidates(text: str) -> list[dict[str, Any]]:
        """Model-free: one factual candidate per non-empty line (or sentence when the text is a single line);
        a "<subject> | <statement>" line yields the subject as an entity."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) <= 1:
            lines = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
        out: list[dict[str, Any]] = []
        for line in lines:
            match = _SUBJECT_LINE.match(line)
            content = match.group("statement").strip() if match else line
            subject = [match.group("subject")] if match else []
            if FALLBACK_MIN_LEN <= len(content) <= MAX_LEN:
                out.append({"content": content, "entities": subject})
        return out[:MAX_CANDIDATES]
