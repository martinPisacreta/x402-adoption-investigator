"""Sleep cycle: preview() (no mutation) / execute(preview) with guardrails and backupId / restore(backupId). Owner: B2.

Port of engines/hmem/reference/src/sleepCycle.ts. preview computes {to_consolidate, to_archive, to_synthesize}
and mutates nothing; execute takes a backup first (in memory + persist_dir/backups/<backup_id>/memories.jsonl),
promotes short_term records idle for 7 days to long_term, archives (deletes, ledger sleep_archive + delete) old
low-access unreferenced records, and synthesizes cosine >= 0.82 groups into one derived long_term record.
Guardrails: superseding/current records, derived records (derivedFrom set, vector sg-001) and relationship hubs
(>= 3 related_to) are NEVER archived.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..clock import Clock
from .embeddings import cosine
from .ledger import TrustLedger
from .store import MemoryStore
from .types import (ARCHIVE_MAX_ACCESS, ARCHIVE_MIN_DAYS, CONSOLIDATION_DAYS, DAY_MS, SYNTHESIS_COSINE, MemoryProvenance,
                    MemoryRecord)

HUB_THRESHOLD = 3


class Sleep:
    def __init__(self, store: MemoryStore, ledger: TrustLedger, clock: Clock, archive_after_days: int = ARCHIVE_MIN_DAYS,
                 actor: str = "sleep") -> None:
        self.store = store
        self.ledger = ledger
        self.clock = clock
        self.archive_after_days = archive_after_days
        self.actor = actor
        self.backups_dir = Path(store.persist_dir) / "backups" if store.persist_dir is not None else None
        self._backups: dict[str, dict[str, Any]] = {}

    def preview(self) -> dict[str, Any]:
        """{to_consolidate, to_archive, to_synthesize}; mutates nothing."""
        now = self.clock.now_ms()
        records = self.store.all()
        referenced: set[str] = set()
        for memory in records:
            referenced.update(memory.related_to)
            if memory.supersedes:
                referenced.add(memory.supersedes)
            referenced.update(memory.derived_from)
        to_consolidate = [m.id for m in records if m.layer == "short_term" and now - m.last_accessed >= CONSOLIDATION_DAYS * DAY_MS]
        to_archive: list[str] = []
        protected: list[dict[str, str]] = []
        for memory in records:
            old_enough = now - memory.timestamp >= self.archive_after_days * DAY_MS
            low_access = memory.access_count <= ARCHIVE_MAX_ACCESS
            unreferenced = memory.id not in referenced
            if not (old_enough and low_access and unreferenced):
                continue
            if memory.supersedes:
                protected.append({"memory_id": memory.id, "guardrail": "superseding record"})
            elif memory.derived_from:
                protected.append({"memory_id": memory.id, "guardrail": "derived record"})
            elif len(memory.related_to) >= HUB_THRESHOLD:
                protected.append({"memory_id": memory.id, "guardrail": "relationship hub"})
            else:
                to_archive.append(memory.id)
        to_synthesize: list[list[str]] = []
        used: set[str] = set()
        for i, a in enumerate(records):
            if a.id in used:
                continue
            group = [a]
            for b in records[i + 1:]:
                if b.id in used:
                    continue
                if cosine(a.embedding or [], b.embedding or []) >= SYNTHESIS_COSINE:
                    group.append(b)
            if len(group) >= 2:
                used.update(m.id for m in group)
                to_synthesize.append([m.id for m in group])
        return {"to_consolidate": to_consolidate, "to_archive": to_archive, "to_synthesize": to_synthesize, "protected": protected,
                "now": now}

    def _backup(self) -> str:
        existing = len(self._backups)
        if self.backups_dir is not None and self.backups_dir.is_dir():
            existing = max(existing, len([p for p in self.backups_dir.iterdir() if p.is_dir()]))
        backup_id = f"backup-{existing + 1}"
        state = self.store.snapshot_all()
        self._backups[backup_id] = state
        if self.backups_dir is not None:
            folder = self.backups_dir / backup_id
            folder.mkdir(parents=True, exist_ok=True)
            with open(folder / MemoryStore.FILE, "w", encoding="utf-8", newline="\n") as handle:
                for record in state["records"]:
                    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            (folder / "meta.json").write_text(json.dumps({"seq": state["seq"], "at": self.clock.now_ms()}) + "\n", encoding="utf-8", newline="\n")
        return backup_id

    def execute(self, preview: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """{backup_id, consolidated, archived, synthesized, action_log}."""
        plan = preview or self.preview()
        backup_id = self._backup()
        action_log = [f"backup {backup_id} created"]
        consolidated = 0
        for memory_id in plan.get("to_consolidate", []):
            if self.store.get(memory_id) is None:
                continue
            self.store.promote(memory_id, "long_term", actor=self.actor)
            self.ledger.log(memory_id, "sleep_consolidate", self.actor, details={"from": "short_term", "to": "long_term"})
            consolidated += 1
            action_log.append(f"consolidated {memory_id}")
        archived = 0
        for memory_id in plan.get("to_archive", []):
            memory = self.store.get(memory_id)
            if memory is None or memory.derived_from or memory.supersedes:
                continue        # guardrails re-checked at execute time
            self.ledger.log(memory_id, "sleep_archive", self.actor, details={"age_ms": self.clock.now_ms() - memory.timestamp,
                                                                            "accessCount": memory.access_count, "backupId": backup_id})
            self.store.delete(memory_id, "sleep archive", actor=self.actor)
            archived += 1
            action_log.append(f"archived {memory_id}")
        synthesized = 0
        for group in plan.get("to_synthesize", []):
            members = [m for m in (self.store.get(i) for i in group) if m is not None]
            if len(members) < 2:
                continue
            merged = self.store.add(
                "Synthesis: " + " | ".join(m.content for m in members), "sleep:synthesis", type="factual", layer="long_term",
                tags=sorted({t for m in members for t in m.tags}), confidence=max(m.confidence for m in members),
                provenance=MemoryProvenance(origin="inference", learned_at=self.clock.now_ms(), label=f"synthesized from {len(members)} memories"),
                derived_from=[m.id for m in members])
            self.ledger.log(merged.id, "sleep_synthesize", self.actor, details={"sources": list(group)})
            for memory in members:
                self.store.delete(memory.id, "merged by sleep synthesis", actor=self.actor)
            synthesized += 1
            action_log.append(f"synthesized {merged.id} from {', '.join(group)}")
        self.store.persist()
        return {"backup_id": backup_id, "consolidated": consolidated, "archived": archived, "synthesized": synthesized,
                "action_log": action_log}

    def restore(self, backup_id: str) -> None:
        state = self._backups.get(backup_id)
        if state is None and self.backups_dir is not None and (self.backups_dir / backup_id / MemoryStore.FILE).is_file():
            folder = self.backups_dir / backup_id
            records = [json.loads(line) for line in (folder / MemoryStore.FILE).read_text(encoding="utf-8").splitlines() if line.strip()]
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8")) if (folder / "meta.json").is_file() else {}
            state = {"records": records, "seq": int(meta.get("seq", len(records)))}
        if state is None:
            raise KeyError(f"H-MEM: unknown backup {backup_id}")
        self.store.restore_all(state["records"], state["seq"])
        self.ledger.log("*", "restore", self.actor, details={"backupId": backup_id, "records": len(state["records"])})


def preview_for_log(preview: dict[str, Any]) -> dict[str, Any]:
    """Compact, deterministic summary for the `memory` event (op sleep_preview)."""
    return {"to_consolidate": list(preview.get("to_consolidate", [])), "to_archive": list(preview.get("to_archive", [])),
            "to_synthesize": [list(g) for g in preview.get("to_synthesize", [])], "protected": list(preview.get("protected", []))}


_ = MemoryRecord
