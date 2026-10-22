"""Pitfall memory index shared by the Analyst and the Critic (H-MEM policy memories, SPEC.md 8.3).

One index, two readers: the Analyst attaches every caveat the index requires when it builds a claim, and the
Critic's C3 gate re-reads the same index to verify the claim carries them. The gate therefore fires only when the
two disagree (a memory added at runtime, a generator-written claim that dropped a caveat, a registry entry out of
step with memory), never as a staged first round.

The index is `hmem.policy_memories(fields, sources, metrics)` when the H-MEM engine is mounted; otherwise the
run's `memories.jsonl` (a deterministic copy of memory/seed) is read directly, so the behaviour is the same.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional

if TYPE_CHECKING:
    from ..runner import RunContext


class SeedMemory:
    """Minimal MemoryRecord stand-in read from memories.jsonl when the H-MEM engine is not mounted."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id = str(data.get("id"))
        self.content = str(data.get("content", ""))
        self.type = str(data.get("type", "general"))
        self.tags = list(data.get("tags") or [])
        self.entities = [e.get("name") if isinstance(e, dict) else str(e) for e in (data.get("entities") or [])]
        self.confidence = float(data.get("confidence", 0.7))

    def caveat_codes(self) -> list[str]:
        return [t.split(":", 1)[1] for t in self.tags if t.startswith("caveat:")]

    def entity_names(self) -> list[str]:
        return list(self.entities)


def load_memory_file(path: Path) -> list[SeedMemory]:
    records: list[SeedMemory] = []
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(SeedMemory(json.loads(line)))
        except ValueError:
            continue
    return records


def wanted_entities(fields: Iterable[str], sources: Iterable[str], metrics: Iterable[str] = ()) -> set[str]:
    return ({f"field:{f}" for f in fields} | {f"source:{s}" for s in sources} | {f"metric:{m}" for m in metrics})


def _file_index(ctx: "RunContext") -> list[SeedMemory]:
    cache = ctx.scratch.get("pitfall_file_index")
    if cache is None:
        candidates = [Path(ctx.hmem_dir) / "memories.jsonl" if ctx.hmem_dir else None, ctx.config.seed_dir / "memories.jsonl"]
        cache = []
        for path in candidates:
            if path is not None and path.is_file():
                cache = [m for m in load_memory_file(path) if "pitfall" in m.tags]
                break
        ctx.scratch["pitfall_file_index"] = cache
    return cache


def pitfall_memories(ctx: "RunContext", fields: list[str], sources: list[str],
                     metrics: Optional[list[str]] = None) -> tuple[list[Any], str]:
    """(pitfall memories whose entities touch these fields/sources/metrics, index source)."""
    metrics = list(metrics or [])
    hmem = ctx.hmem
    if hmem is not None:
        try:
            return list(hmem.policy_memories(fields, sources, metrics)), "hmem.policy_memories"
        except NotImplementedError:
            pass
    wanted = wanted_entities(fields, sources, metrics)
    return [m for m in _file_index(ctx) if wanted.intersection(m.entity_names())], "memories.jsonl"


def all_pitfall_memories(ctx: "RunContext") -> list[Any]:
    hmem = ctx.hmem
    if hmem is not None:
        try:
            return sorted((m for m in hmem.store.all() if "pitfall" in getattr(m, "tags", [])), key=lambda m: m.id)
        except (NotImplementedError, AttributeError):
            pass
    return list(_file_index(ctx))


def memory_for_code(ctx: "RunContext", code: str) -> Optional[str]:
    """Memory id of the pitfall memory that requires `code` (any field/source/metric), or None."""
    for memory in all_pitfall_memories(ctx):
        if code in memory.caveat_codes():
            return memory.id
    return None


def required_caveats(ctx: "RunContext", fields: list[str], sources: list[str], metrics: list[str],
                     registry_caveats: Iterable[str]) -> tuple[list[str], dict[str, str], str]:
    """The caveat codes a claim must carry and the memory id behind each one.

    Union of the registry's caveat rules and every code a touching pitfall memory requires; a registry code with
    no touching memory still gets the id of the memory that defines that code (if any). Returns
    (codes in registry order then memory order, {code: memory_id}, index source)."""
    codes: list[str] = list(registry_caveats)
    ids: dict[str, str] = {}
    memories, index_source = pitfall_memories(ctx, fields, sources, metrics)
    for memory in memories:
        for code in memory.caveat_codes():
            if code not in codes:
                codes.append(code)
            ids.setdefault(code, memory.id)
    for code in codes:
        if code not in ids:
            memory_id = memory_for_code(ctx, code)
            if memory_id:
                ids[code] = memory_id
    return codes, ids, index_source
