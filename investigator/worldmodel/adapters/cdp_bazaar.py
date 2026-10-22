"""discovery/search pages and catalog snapshot -> endpoints, listed_on cdp-bazaar (context day), quality facts (l30DaysTotalCalls, l30DaysUniquePayers, lastCalledAt), pays_to. Owner: B2.

Three payload shapes share this adapter (families cdp_search, cdp_bazaar, cdp_catalog_snapshot):
- search  {resources[], partialResults, searchMethod}      -> one endpoint per resource (capped at 20, mem-seed-002)
- page    {items[], pagination{limit, offset, total}}       -> one endpoint per item (limit <= 1000 per page)
- snapshot {items[], pagination{pages, totalReportedByApi}, fetchedAt, source} -> folded PER HOST
  (28k endpoints x 7 events would be ~200k events per run; the per-endpoint rows stay in the raw export that the
  SQL views and the reducers read, and the World Model keeps the per-host aggregates and the catalog size).
Quality lives at item.quality (not metadata.quality); accepts[] gives payTo/network/asset/amount; provenance kind registry.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit

from ..domain import as_int, day_context_id, endpoint_id, host_of_url, registry_id, seller_id, wallet_id_for_network
from ..types import AssertFact, IngestObservation, Relate, WriteEvent
from ._common import day_ctx, endpoint_entity, listed_on, provenance, registry_entity, seller_entity, wallet_entity

REGISTRY = "cdp-bazaar"
QUALITY_PREDICATES = ("l30DaysTotalCalls", "l30DaysUniquePayers", "lastCalledAt")


def _mode(observation: IngestObservation, payload: dict[str, Any]) -> Optional[str]:
    if isinstance(payload.get("resources"), list):
        return "search"
    if isinstance(payload.get("items"), list):
        pagination = payload.get("pagination") or {}
        if observation.type == "cdp_catalog_snapshot" or "totalReportedByApi" in pagination or "pages" in pagination:
            return "snapshot"
        return "page"
    return None


def _search_host(source_url: Optional[str], items: list[dict[str, Any]]) -> Optional[str]:
    if source_url:
        params = parse_qs(urlsplit(source_url).query)
        sub = params.get("urlSubstring", [None])[0]
        if sub and "." in sub:
            return sub.lower()
    hosts: dict[str, int] = {}
    for item in items:
        host = host_of_url(item.get("resource"))
        if host:
            hosts[host] = hosts.get(host, 0) + 1
    return max(hosts, key=lambda h: (hosts[h], h)) if hosts else None


def _per_endpoint(items: list[dict[str, Any]], source: Any, day: str, page_attrs: dict[str, Any]) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    sellers_seen: set[str] = set()
    for item in items:
        resource = item.get("resource")
        if not isinstance(resource, str) or not resource:
            continue
        host = host_of_url(resource) or "unknown"
        if host not in sellers_seen:
            sellers_seen.add(host)
            events.append(seller_entity(host, source))
        events.append(endpoint_entity(resource, source, {
            "serviceName": item.get("serviceName"), "type": item.get("type"), "x402Version": item.get("x402Version"),
            "lastUpdated": item.get("lastUpdated"), "tags": item.get("tags") if isinstance(item.get("tags"), list) else None}))
        events.append(Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(resource), source=source))
        events.append(listed_on(endpoint_id(resource), REGISTRY, source, day, {"lastUpdated": item.get("lastUpdated"), **page_attrs}))
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        for predicate in QUALITY_PREDICATES:
            if predicate in quality and quality[predicate] is not None:
                events.append(AssertFact(about=endpoint_id(resource), predicate=predicate, value=quality[predicate], source=source,
                                         confidence=0.9, context=day))
        for accept in item.get("accepts") or []:
            if not isinstance(accept, dict) or not accept.get("payTo"):
                continue
            network = accept.get("network")
            events.append(wallet_entity(str(accept["payTo"]), source, network))
            events.append(Relate(from_id=endpoint_id(resource), type="pays_to", to_id=wallet_id_for_network(str(accept["payTo"]), network),
                                 source=source, attrs={"network": network, "asset": accept.get("asset"), "amount": accept.get("amount"),
                                                       "scheme": accept.get("scheme"), "maxTimeoutSeconds": accept.get("maxTimeoutSeconds")}))
    return events


def _per_host(items: list[dict[str, Any]], source: Any, day: str, snapshot_attrs: dict[str, Any]) -> list[WriteEvent]:
    hosts: dict[str, dict[str, Any]] = {}
    pay_tos_all: set[str] = set()
    without_host = 0
    for item in items:
        resource = item.get("resource")
        if not isinstance(resource, str):
            continue
        host = host_of_url(resource)
        if not host:
            without_host += 1          # NULL host on both SQL backends too; counted, never folded under a fake host
            continue
        row = hosts.setdefault(host, {"listings": 0, "calls": 0, "payers_max": 0, "pay_tos": set(), "last_called": None})
        row["listings"] += 1
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        row["calls"] += as_int(quality.get("l30DaysTotalCalls")) or 0
        row["payers_max"] = max(row["payers_max"], as_int(quality.get("l30DaysUniquePayers")) or 0)
        last = quality.get("lastCalledAt")
        if last and (row["last_called"] is None or str(last) > row["last_called"]):
            row["last_called"] = str(last)
        for accept in item.get("accepts") or []:
            if isinstance(accept, dict) and accept.get("payTo"):
                key = wallet_id_for_network(str(accept["payTo"]), accept.get("network"))
                row["pay_tos"].add(key)
                pay_tos_all.add(key)
    events: list[WriteEvent] = []
    for host in sorted(hosts):
        row = hosts[host]
        events.append(seller_entity(host, source))
        events.append(listed_on(seller_id(host), REGISTRY, source, day, {"basis": "catalog snapshot per host", "listings": row["listings"], **snapshot_attrs}))
        events.append(AssertFact(about=seller_id(host), predicate="catalog.listings", value=row["listings"], source=source, confidence=0.9, context=day))
        events.append(AssertFact(about=seller_id(host), predicate="catalog.l30DaysTotalCalls", value=row["calls"], source=source, confidence=0.9, context=day))
        events.append(AssertFact(about=seller_id(host), predicate="catalog.l30DaysUniquePayersMax", value=row["payers_max"], source=source, confidence=0.9, context=day))
        events.append(AssertFact(about=seller_id(host), predicate="catalog.payToCount", value=len(row["pay_tos"]), source=source, confidence=0.9, context=day))
        if row["last_called"]:
            events.append(AssertFact(about=seller_id(host), predicate="catalog.lastCalledAt", value=row["last_called"], source=source, confidence=0.9, context=day))
    events.append(AssertFact(about=registry_id(REGISTRY), predicate="catalogSize", source=source, confidence=0.9, context=day, value={
        "items": len(items), "hosts": len(hosts), "itemsWithoutHost": without_host, "distinctPayTo": len(pay_tos_all), **snapshot_attrs}))
    return events


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    """Quality lives at item.quality (not metadata.quality); accepts[] gives payTo/network/asset/amount; provenance kind registry."""
    payload = observation.payload
    if not isinstance(payload, dict):
        return []
    mode = _mode(observation, payload)
    if mode is None:
        return []
    source = provenance(observation)
    day = day_context_id(observation.at)
    events: list[WriteEvent] = [registry_entity(REGISTRY, source, {"url": "https://api.cdp.coinbase.com/platform/v2/x402/discovery"}),
                                day_ctx(observation.at, source)]
    if mode == "search":
        items = [i for i in payload["resources"] if isinstance(i, dict)]
        events.extend(_per_endpoint(items, source, day, {"searchMethod": payload.get("searchMethod"), "basis": "discovery search (limit 20)"}))
        host = _search_host(source.url, items)
        if host:
            events.append(seller_entity(host, source))
            events.append(AssertFact(about=seller_id(host), predicate="cdpSearchReturned", value=len(items), source=source, confidence=0.9, context=day))
            events.append(AssertFact(about=seller_id(host), predicate="cdpSearchPartialResults", value=bool(payload.get("partialResults")), source=source, confidence=0.9, context=day))
        return events
    items = [i for i in payload["items"] if isinstance(i, dict)]
    pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
    if mode == "page":
        page_attrs = {"basis": "discovery page", "pageOffset": pagination.get("offset"), "pageLimit": pagination.get("limit")}
        events.extend(_per_endpoint(items, source, day, page_attrs))
        if pagination.get("total") is not None:
            events.append(AssertFact(about=registry_id(REGISTRY), predicate="apiTotal", value=as_int(pagination.get("total")), source=source,
                                     confidence=0.9, context=day))
        return events
    snapshot_attrs = {"snapshotFetchedAt": payload.get("fetchedAt"), "totalReportedByApi": pagination.get("totalReportedByApi"),
                      "pages": pagination.get("pages")}
    events.extend(_per_host(items, source, day, {k: v for k, v in snapshot_attrs.items() if v is not None}))
    return events
