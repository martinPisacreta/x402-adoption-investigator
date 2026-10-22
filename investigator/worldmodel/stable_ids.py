"""Stable, deterministic, content-addressable ids (port of engines/world-model/reference/src/stableIds.ts).

Same input, same id, on every machine and every re-run. Formulas (SPEC.md 9.1):
  normalize_name: NFKD -> strip combining marks -> strip U+200B..U+200D and U+FEFF -> lower -> collapse ws -> strip
  canonical_json: drop None, sort keys, compact separators, ensure_ascii=False
  external_id(type, system, id) = "type:system:id"
  entity_id_from_name = "ent_" + sha256(f"{type}:{normalize_name(name)}")[:16]
  context_id_from_attrs = "ctx_" + sha256(f"{type}:{canonical_json(attrs)}:{parent or ''}")[:16]
  relationship_id = "rel_" + sha256(f"{from}|{type}|{to}|{context or ''}|{valid_from or ''}")[:16]
  fact_id = "fact_" + sha256(f"{about}|{predicate}|{canonical_json(value)}|{asserted_at}|{source_kind}|{source_url or ''}")[:16]
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Optional

_ZERO_WIDTH = re.compile("[​‌‍﻿]")
_WS = re.compile(r"\s+")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = _ZERO_WIDTH.sub("", stripped)
    return _WS.sub(" ", stripped.lower()).strip()


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_drop_none(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_drop_none(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def external_id(type_: str, system: str, id_: str) -> str:
    return f"{type_}:{system}:{id_}"


def entity_id_from_name(type_: str, name: str) -> str:
    return "ent_" + sha256_hex(f"{type_}:{normalize_name(name)}")[:16]


def context_id_from_attrs(type_: str, attrs: dict[str, Any], parent: Optional[str] = None) -> str:
    return "ctx_" + sha256_hex(f"{type_}:{canonical_json(attrs)}:{parent or ''}")[:16]


def relationship_id(from_id: str, type_: str, to_id: str, context: Optional[str] = None,
                    valid_from: Optional[str] = None) -> str:
    return "rel_" + sha256_hex(f"{from_id}|{type_}|{to_id}|{context or ''}|{valid_from or ''}")[:16]


def fact_id(about: str, predicate: str, value: Any, asserted_at: str, source_kind: str,
            source_url: Optional[str] = None) -> str:
    return "fact_" + sha256_hex(
        f"{about}|{predicate}|{canonical_json(value)}|{asserted_at}|{source_kind}|{source_url or ''}")[:16]


def observation_record_id(external_id_: str) -> str:
    return "obs_" + sha256_hex(external_id_)[:12]


def ingest_run_id(external_ids: list[str]) -> str:
    return "run_" + sha256_hex(canonical_json(sorted(external_ids)))[:12]
