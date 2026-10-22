"""Adapters: adapter(family) -> callable(IngestObservation) -> list[WriteEvent] (SPEC.md 9.3). Owner: B2.

Unknown payload shape -> [] and an adapter_skip note listing the top-level keys seen (visible, never a silent
zero). Expected counts over the fixtures are asserted by tests/test_adapters.py by reading the fixture files.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..types import IngestObservation, WriteEvent
from . import agent402, base_rpc, cdp_bazaar, receipts, store_openapi, vet402, x402_trust

Adapter = Callable[[IngestObservation], list[WriteEvent]]

ADAPTERS: dict[str, Adapter] = {
    "receipts": receipts.to_events,
    "store_openapi": store_openapi.to_events,
    "cdp_bazaar": cdp_bazaar.to_events,
    "cdp_search": cdp_bazaar.to_events,
    "cdp_catalog_snapshot": cdp_bazaar.to_events,
    "agent402": agent402.to_events,
    "x402_trust": x402_trust.to_events,
    "vet402": vet402.to_events,
    "base_rpc": base_rpc.to_events,
}


def adapter(family: str) -> Adapter:
    try:
        return ADAPTERS[family]
    except KeyError as exc:
        raise KeyError(f"no adapter for family {family!r}") from exc


def no_events(observation: IngestObservation) -> list[WriteEvent]:
    """Adapter for families without a mapping: nothing is folded; the Keeper logs the adapter_skip note."""
    return []


def adapter_or_skip(family: str) -> Adapter:
    """The lookup the Keeper passes to WorldModel.ingest(): unknown family -> no_events (never the generic adapter)."""
    return ADAPTERS.get(family, no_events)


def adapter_skip(observation: IngestObservation) -> dict[str, Any]:
    """The note logged when a payload shape is unknown."""
    payload = observation.payload
    if isinstance(payload, dict):
        keys: list[Any] = sorted(payload.keys())
    elif isinstance(payload, list):
        keys = [f"list[{len(payload)}]"]
    else:
        keys = [type(payload).__name__]
    return {"adapter_skip": observation.external_id, "family": observation.type, "name": observation.title, "top_level_keys": keys}


__all__ = ["ADAPTERS", "Adapter", "adapter", "adapter_or_skip", "adapter_skip", "no_events"]
