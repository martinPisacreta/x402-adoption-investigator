"""Helpers shared by the adapters: provenance pass-through, entity builders, day contexts, safe parsing. Owner: B2."""
from __future__ import annotations

import re
from typing import Any, Optional

from ..domain import (BASE_NETWORK, day_context, endpoint_id, facilitator_id, host_of_url, registry_id, seller_id,
                      wallet_id, wallet_id_for_network)
from ..types import IngestObservation, Provenance, Relate, UpsertContext, UpsertEntity, WriteEvent

EVM_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
TEXT_CAP = 200


def provenance(obs: IngestObservation) -> Provenance:
    """Keep the ORIGINAL kind and url (sample mode replays with by=fixture:..., checksum of the fixture bytes)."""
    src = obs.source
    return Provenance(kind=src.kind, at=obs.at, by=src.by, url=src.url, checksum=src.checksum, run_id=src.run_id)


def truncate(text: Any, cap: int = TEXT_CAP) -> Optional[str]:
    if text is None:
        return None
    value = str(text)
    return value if len(value) <= cap else value[:cap] + "..."


def clean_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in attrs.items() if v is not None}


def registry_entity(name: str, source: Provenance, attrs: Optional[dict[str, Any]] = None) -> UpsertEntity:
    return UpsertEntity(type="registry", id=registry_id(name), attrs=clean_attrs({"name": name, **(attrs or {})}),
                        source=source, name=name)


def seller_entity(host: str, source: Provenance, attrs: Optional[dict[str, Any]] = None) -> UpsertEntity:
    host = host.lower()
    return UpsertEntity(type="seller", id=seller_id(host), attrs=clean_attrs({"host": host, **(attrs or {})}),
                        source=source, name=host)


def endpoint_entity(url: str, source: Provenance, attrs: Optional[dict[str, Any]] = None) -> UpsertEntity:
    host = host_of_url(url) or "unknown"
    return UpsertEntity(type="endpoint", id=endpoint_id(url), attrs=clean_attrs({"host": host, "url": url, **(attrs or {})}),
                        source=source, name=url)


def wallet_entity(address: str, source: Provenance, network: Optional[str] = None,
                  attrs: Optional[dict[str, Any]] = None) -> UpsertEntity:
    wid = wallet_id_for_network(address, network or BASE_NETWORK)
    chain = wid.split(":", 2)[1]
    return UpsertEntity(type="wallet", id=wid, attrs=clean_attrs({"address": wid.split(":", 2)[2], "chain": chain,
                                                                  "network": network or BASE_NETWORK, **(attrs or {})}),
                        source=source, name=wid.split(":", 2)[2])


def facilitator_entity(slug: str, source: Provenance, attrs: Optional[dict[str, Any]] = None) -> UpsertEntity:
    return UpsertEntity(type="facilitator", id=facilitator_id(slug), attrs=clean_attrs({"slug": slug, **(attrs or {})}),
                        source=source, name=slug)


def day_ctx(at: str, source: Provenance) -> UpsertContext:
    return day_context(at, source)


def listed_on(endpoint_or_seller_id: str, registry: str, source: Provenance, context: Optional[str],
              attrs: Optional[dict[str, Any]] = None) -> Relate:
    return Relate(from_id=endpoint_or_seller_id, type="listed_on", to_id=registry_id(registry), source=source,
                  context=context, attrs=clean_attrs(attrs or {}) or None, valid_from=source.at)


def sells(host: str, endpoint: str, source: Provenance) -> Relate:
    return Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(endpoint), source=source)


def wallet_hint(obs: IngestObservation) -> Optional[str]:
    """A 0x address named in the observation title (e.g. rpc_balance_0x...), lowercased."""
    match = EVM_ADDRESS_RE.search(obs.title or "")
    return match.group(0).lower() if match else None


__all__ = ["provenance", "truncate", "clean_attrs", "registry_entity", "seller_entity", "endpoint_entity", "wallet_entity",
           "facilitator_entity", "day_ctx", "listed_on", "sells", "wallet_hint", "wallet_id", "WriteEvent"]
