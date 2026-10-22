"""resolve -> endpoints listed_on vet402 with catalog_status facts (listed | delisted ...). Owner: B2.

Payload (api/v1/resolve?q=<host>): {query{kind, value}, disclaimer, endpoints[]} with canonical_url, method,
payee_id ("eip155:8453:0x..."), catalog_status, first_seen, last_seen, endpoint_id, resource_id.
"""
from __future__ import annotations

from typing import Any, Optional

from ..domain import day_context_id, endpoint_id, host_of_url, seller_id, wallet_id_for_network
from ..types import AssertFact, IngestObservation, Relate, WriteEvent
from ._common import day_ctx, endpoint_entity, listed_on, provenance, registry_entity, seller_entity, wallet_entity

REGISTRY = "vet402"


def _payee(payee_id: Any) -> tuple[Optional[str], Optional[str]]:
    """'eip155:8453:0xabc...' -> (network, address)."""
    if not isinstance(payee_id, str) or payee_id.count(":") < 2:
        return None, None
    namespace, reference, address = payee_id.split(":", 2)
    return f"{namespace}:{reference}", address


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    """endpoints[] with canonical_url, method, payee_id, catalog_status, first_seen, last_seen."""
    payload = observation.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("endpoints"), list):
        return []
    source = provenance(observation)
    day = day_context_id(observation.at)
    events: list[WriteEvent] = [registry_entity(REGISTRY, source, {"url": "https://vet402.com"}), day_ctx(observation.at, source)]
    query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
    query_host = str(query.get("value") or "").lower() if query.get("kind") == "domain" else None
    sellers_seen: set[str] = set()
    counted = 0
    for row in payload["endpoints"]:
        if not isinstance(row, dict) or not row.get("canonical_url"):
            continue
        url = str(row["canonical_url"])
        host = host_of_url(url) or query_host or "unknown"
        if host not in sellers_seen:
            sellers_seen.add(host)
            events.append(seller_entity(host, source))
        events.append(endpoint_entity(url, source, {"host": host, "method": row.get("method"), "vet402EndpointId": row.get("endpoint_id"),
                                                     "vet402ResourceId": row.get("resource_id"), "payeeId": row.get("payee_id")}))
        events.append(Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(url), source=source))
        events.append(listed_on(endpoint_id(url), REGISTRY, source, day, {
            "catalog_status": row.get("catalog_status"), "first_seen": row.get("first_seen"), "last_seen": row.get("last_seen"),
            "method": row.get("method"), "payee_id": row.get("payee_id"), "basis": "vet402 resolve row (delisted included)"}))
        if row.get("catalog_status") is not None:
            events.append(AssertFact(about=endpoint_id(url), predicate="catalog_status", value=row["catalog_status"], source=source,
                                     confidence=0.9, context=day))
        network, address = _payee(row.get("payee_id"))
        if address:
            events.append(wallet_entity(address, source, network))
            events.append(Relate(from_id=endpoint_id(url), type="pays_to", to_id=wallet_id_for_network(address, network), source=source,
                                 attrs={"network": network, "basis": "vet402 payee_id"}))
        counted += 1
    if query_host:
        events.append(seller_entity(query_host, source))
        events.append(AssertFact(about=seller_id(query_host), predicate="vet402Endpoints", value=counted, source=source, confidence=0.9, context=day))
    return events if counted else []
