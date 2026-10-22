"""openapi.json -> the seller's own declaration: endpoints with x-payment-info (declaredPrice facts), declaredPaidOps on the seller. Owner: B2.

Not a registry: the OpenAPI is self-reported by the seller (provenance kind receipts), so there is no listed_on
edge; registry_counts(host) reads `declaredPaidOps` for the endpoint_count_for_host Conflict. llms.txt (text
payload) produces no events.
"""
from __future__ import annotations

from typing import Any

from ..domain import day_context_id, endpoint_id, host_of_url, seller_id
from ..types import AssertFact, IngestObservation, Relate, WriteEvent
from ._common import day_ctx, endpoint_entity, provenance, seller_entity, truncate

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    payload = observation.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        return []
    source = provenance(observation)
    servers = payload.get("servers") if isinstance(payload.get("servers"), list) else []
    base = str(servers[0].get("url")) if servers and isinstance(servers[0], dict) and servers[0].get("url") else None
    host = host_of_url(base) or host_of_url(source.url) or "unknown"
    base = (base or f"https://{host}").rstrip("/")
    day = day_context_id(observation.at)
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    events: list[WriteEvent] = [day_ctx(observation.at, source),
                                seller_entity(host, source, {"openapiTitle": truncate(info.get("title")), "openapiVersion": info.get("version")})]
    paid = 0
    for path, operations in sorted(payload["paths"].items()):
        if not isinstance(operations, dict):
            continue
        for method, op in operations.items():
            if method.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue
            payment = op.get("x-payment-info")
            if not isinstance(payment, dict):
                continue
            url = f"{base}{path}"
            price = payment.get("price") if isinstance(payment.get("price"), dict) else {}
            events.append(endpoint_entity(url, source, {"host": host, "operationId": op.get("operationId"), "method": method.upper(),
                                                         "summary": truncate(op.get("summary"))}))
            events.append(Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(url), source=source))
            events.append(AssertFact(about=endpoint_id(url), predicate="declaredPrice", source=source, confidence=0.9, context=day, value={
                "amount": price.get("amount"), "currency": price.get("currency"), "mode": price.get("mode"),
                "network": payment.get("network"), "protocols": payment.get("protocols")}))
            paid += 1
    events.append(AssertFact(about=seller_id(host), predicate="declaredPaidOps", value=paid, source=source, confidence=0.9, context=day))
    return events
