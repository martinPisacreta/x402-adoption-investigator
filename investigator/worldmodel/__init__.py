"""Meterless World Model, Python port of the reference (stdlib only). SPEC.md section 9.

Exports: WorldModel, entity_id_from_name, normalize_name, decide, canonical_json, external_id.
"""
from .model import WorldModel
from .resolve import decide
from .stable_ids import canonical_json, entity_id_from_name, external_id, normalize_name

__all__ = ["WorldModel", "entity_id_from_name", "normalize_name", "decide", "canonical_json", "external_id"]
