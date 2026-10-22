"""index/route -> listed_on agent402, facts toolCount / paidToolCount / paywall.ok / health / routable / routerDispatchReason. Owner: B2.

index payload (api/index?seller=<host>): origin, toolCount, paidToolCount, health, routable, paywall{ok,...},
routerDispatchReason, payToByNetwork, tools[] (route, slug, paid, price, declared, lastVerifiedAt, ...).
route payload (api/route?q=...): query, results[] (seller, url, price, bazaar{calls30d, payers30d, payTos}),
matched, sellersMatched, returned. containsUntrustedContent: seller-supplied text is stored as data (truncated,
flagged untrustedContent) and never fed to a generator unescaped.
"""
from __future__ import annotations

from typing import Any

from ..domain import day_context_id, endpoint_id, host_of_url, registry_id, seller_id, wallet_id_for_network
from ..types import AssertFact, IngestObservation, Relate, WriteEvent
from ._common import day_ctx, endpoint_entity, listed_on, provenance, registry_entity, seller_entity, truncate, wallet_entity

REGISTRY = "agent402"
SELLER_PREDICATES = ("toolCount", "paidToolCount", "health", "routable", "routerDispatchReason")


def _index_events(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    origin = str(payload.get("origin") or payload.get("homepage") or "")
    host = host_of_url(origin) or str(payload.get("displayName") or "").lower()
    if not host:
        return []
    events: list[WriteEvent] = [seller_entity(host, source, {
        "displayName": truncate(payload.get("displayName")), "homepage": payload.get("homepage"),
        "discoveryPath": payload.get("discoveryPath"), "untrustedContent": True})]
    events.append(listed_on(seller_id(host), REGISTRY, source, day, {
        "fetchedAt": payload.get("fetchedAt"), "toolsReturned": payload.get("toolsReturned"), "health": payload.get("health"),
        "routable": payload.get("routable"), "basis": "agent402 crawl of the seller origin"}))
    for predicate in SELLER_PREDICATES:
        if predicate in payload and payload[predicate] is not None:
            events.append(AssertFact(about=seller_id(host), predicate=predicate, value=payload[predicate], source=source, confidence=0.9, context=day))
    paywall = payload.get("paywall") if isinstance(payload.get("paywall"), dict) else {}
    if "ok" in paywall:
        events.append(AssertFact(about=seller_id(host), predicate="paywall.ok", value=bool(paywall.get("ok")), source=source, confidence=0.9, context=day))
        events.append(AssertFact(about=seller_id(host), predicate="paywall.status", value=paywall.get("status"), source=source, confidence=0.9, context=day))
    pay_to_by_network = payload.get("payToByNetwork") if isinstance(payload.get("payToByNetwork"), dict) else {}
    for network, address in sorted(pay_to_by_network.items()):
        if isinstance(address, str) and address:
            events.append(wallet_entity(address, source, network))
            events.append(AssertFact(about=seller_id(host), predicate="agent402.payTo", value={"network": network, "payTo": address.lower() if address.startswith("0x") else address},
                                     source=source, confidence=0.9, context=day))
    for tool in payload.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("route"):
            continue
        url = origin.rstrip("/") + str(tool["route"])
        events.append(endpoint_entity(url, source, {
            "host": host, "slug": tool.get("slug"), "method": tool.get("method"), "name": truncate(tool.get("name")),
            "untrustedContent": True}))
        events.append(Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(url), source=source))
        events.append(listed_on(endpoint_id(url), REGISTRY, source, day, {
            "paid": tool.get("paid"), "price": tool.get("price"), "priceKnown": tool.get("priceKnown"), "declared": tool.get("declared"),
            "source": tool.get("source"), "lastVerifiedAt": tool.get("lastVerifiedAt"), "routerDispatchReason": tool.get("routerDispatchReason"),
            "basis": "agent402 tool row"}))
    return events


def _route_events(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    events: list[WriteEvent] = [AssertFact(about=registry_id(REGISTRY), predicate="route.matched", source=source, confidence=0.9, context=day, value={
        "query": truncate(payload.get("query")), "matched": payload.get("matched"), "sellersMatched": payload.get("sellersMatched"),
        "returned": payload.get("returned"), "count": payload.get("count"), "complete": payload.get("complete"),
        "truncated": payload.get("truncated"), "include": payload.get("include")})]
    for result in payload.get("results") or []:
        if not isinstance(result, dict) or not result.get("url"):
            continue
        url = str(result["url"])
        host = host_of_url(result.get("seller") or url) or host_of_url(url) or "unknown"
        events.append(seller_entity(host, source, {"sellerName": truncate(result.get("sellerName")), "untrustedContent": True}))
        events.append(endpoint_entity(url, source, {"host": host, "slug": result.get("slug"), "method": result.get("method"),
                                                     "name": truncate(result.get("name")), "untrustedContent": True}))
        events.append(Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(url), source=source))
        bazaar = result.get("bazaar") if isinstance(result.get("bazaar"), dict) else {}
        events.append(listed_on(endpoint_id(url), REGISTRY, source, day, {
            "priceUsd": result.get("priceUsd"), "payable": result.get("payable"), "score": result.get("score"), "health": result.get("health"),
            "routerDispatchReason": result.get("routerDispatchReason"), "bazaarCalls30d": bazaar.get("calls30d"),
            "bazaarPayers30d": bazaar.get("payers30d"), "basis": "agent402 route result"}))
        for pay_to in bazaar.get("payTos") or []:
            if isinstance(pay_to, str) and pay_to:
                events.append(wallet_entity(pay_to, source))
                events.append(Relate(from_id=endpoint_id(url), type="pays_to", to_id=wallet_id_for_network(pay_to, None), source=source,
                                     attrs={"basis": "agent402 route result bazaar.payTos"}))
    return events


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    """containsUntrustedContent: seller-supplied text is stored as data and never fed to a generator unescaped."""
    payload = observation.payload
    if not isinstance(payload, dict):
        return []
    source = provenance(observation)
    day = day_context_id(observation.at)
    head: list[WriteEvent] = [registry_entity(REGISTRY, source, {"url": "https://agent402.tools"}), day_ctx(observation.at, source)]
    if isinstance(payload.get("tools"), list) or "toolCount" in payload:
        body = _index_events(payload, source, day)
    elif isinstance(payload.get("results"), list) and "query" in payload:
        body = _route_events(payload, source, day)
    else:
        return []
    return head + body if body else []
