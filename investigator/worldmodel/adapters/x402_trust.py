"""stats/search/verified/facilitators/leaderboard/report -> verified_by, hostScore, providerVerified, ecosystem facts on registry:name:x402-trust. Owner: B2.

Payloads are dispatched by `schemaType` (stats | search | verified | facilitators | leaderboard | report).
Third-party 30-day aggregates: the facts carry their basis/window in a `statsBasis` fact (mem-seed-004);
activity, semantic-search, openapi and llms.txt payloads produce no events (visible adapter_skip in the Keeper).
"""
from __future__ import annotations

from typing import Any

from ..domain import day_context_id, endpoint_id, host_of_url, registry_id, seller_id
from ..types import AssertFact, IngestObservation, Relate, WriteEvent
from ._common import (day_ctx, endpoint_entity, facilitator_entity, listed_on, provenance, registry_entity, seller_entity,
                      truncate)

REGISTRY = "x402-trust"
STATS_PREDICATES = ("endpointsListed", "endpointsActive", "endpointsDelisted", "reachablePct", "envelopeCompliantPct",
                    "settlements30d", "settledVolumeUsd30d", "distinctReceivers30d", "distinctPayers30d",
                    "ecosystemAllTimeUsd", "ecosystemAllTimeSettlements")


def _fact(about: str, predicate: str, value: Any, source: Any, day: str, confidence: float = 0.85) -> AssertFact:
    return AssertFact(about=about, predicate=predicate, value=value, source=source, confidence=confidence, context=day)


def _stats(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    reg = registry_id(REGISTRY)
    events: list[WriteEvent] = [_fact(reg, p, payload[p], source, day) for p in STATS_PREDICATES if payload.get(p) is not None]
    events.append(_fact(reg, "statsBasis", {"generatedAt": payload.get("generatedAt"), "coverageSinceTs": payload.get("coverageSinceTs"),
                                            "network": payload.get("network"), "window": "30d",
                                            "basis": "third-party 30-day aggregate across chains (x402-trust index)"}, source, day))
    return events


def _search(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    results = [r for r in payload.get("results") or [] if isinstance(r, dict)]
    query = str(payload.get("query") or "")
    host = query.lower() if "." in query and " " not in query else None
    events: list[WriteEvent] = []
    verified_hosts: set[str] = set()
    for result in results:
        resource = result.get("resource")
        if not isinstance(resource, str) or not resource:
            continue
        rhost = (result.get("host") or host_of_url(resource) or "unknown").lower()
        events.append(seller_entity(rhost, source))
        events.append(endpoint_entity(resource, source, {"host": rhost, "serviceName": truncate(result.get("serviceName")),
                                                          "trustEndpointId": result.get("id"), "endpointPage": result.get("endpointPage")}))
        events.append(Relate(from_id=seller_id(rhost), type="sells", to_id=endpoint_id(resource), source=source))
        events.append(listed_on(endpoint_id(resource), REGISTRY, source, day, {
            "delisted": result.get("delisted"), "score": result.get("score"), "grade": result.get("grade"),
            "providerVerified": result.get("providerVerified"), "providerName": truncate(result.get("providerName")),
            "endpointPage": result.get("endpointPage"), "basis": "x402-trust keyword search row"}))
        if result.get("score") is not None:
            events.append(_fact(endpoint_id(resource), "trustScore", result.get("score"), source, day))
        if result.get("grade") is not None:
            events.append(_fact(endpoint_id(resource), "grade", result.get("grade"), source, day))
        events.append(_fact(endpoint_id(resource), "delisted", bool(result.get("delisted")), source, day))
        if result.get("providerVerified"):
            verified_hosts.add(rhost)
    for vhost in sorted(verified_hosts):
        events.append(Relate(from_id=seller_id(vhost), type="verified_by", to_id=registry_id(REGISTRY), source=source, context=day,
                             attrs={"basis": "providerVerified on search rows"}))
        events.append(_fact(seller_id(vhost), "providerVerified", True, source, day))
    if host:
        events.append(seller_entity(host, source))
        events.append(_fact(seller_id(host), "trustSearchTotal", payload.get("total"), source, day))
        events.append(_fact(seller_id(host), "trustSearchReturned", len(results), source, day))
    return events


def _verified(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    events: list[WriteEvent] = [_fact(registry_id(REGISTRY), "verifiedProvidersTotal", payload.get("total"), source, day)]
    for provider in payload.get("providers") or []:
        if not isinstance(provider, dict) or not provider.get("host"):
            continue
        host = str(provider["host"]).lower()
        events.append(seller_entity(host, source, {"orgName": truncate(provider.get("orgName"))}))
        events.append(Relate(from_id=seller_id(host), type="verified_by", to_id=registry_id(REGISTRY), source=source, context=day, attrs={
            "score": provider.get("score"), "grade": provider.get("grade"), "endpoints": provider.get("endpoints"),
            "probes30d": provider.get("probes30d"), "providerPage": provider.get("providerPage"), "basis": "x402-trust verified providers list"}))
        events.append(_fact(seller_id(host), "providerVerified", True, source, day))
        if provider.get("score") is not None:
            events.append(_fact(seller_id(host), "hostScore", provider.get("score"), source, day))
        if provider.get("grade") is not None:
            events.append(_fact(seller_id(host), "grade", provider.get("grade"), source, day))
    return events


def _facilitators(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    rows = [f for f in payload.get("facilitators") or [] if isinstance(f, dict) and f.get("id")]
    events: list[WriteEvent] = [_fact(registry_id(REGISTRY), "facilitatorsTracked", len(rows), source, day)]
    for row in rows:
        events.append(facilitator_entity(str(row["id"]), source, {
            "url": row.get("url"), "networks": row.get("networks"), "addresses": row.get("addresses"), "volume30dUsd": row.get("volume30dUsd"),
            "settlements30d": row.get("settlements30d"), "volumeAllTimeUsd": row.get("volumeAllTimeUsd"),
            "settlementsAllTime": row.get("settlementsAllTime"), "lastActiveDay": row.get("lastActiveDay"), "trackedSince": row.get("trackedSince")}))
        events.append(listed_on(f"facilitator:name:{row['id']}", REGISTRY, source, day, {"basis": "x402-trust facilitators list"}))
    return events


def _leaderboard(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    events: list[WriteEvent] = [_fact(registry_id(REGISTRY), "leaderboardCoverage", {
        "endpointsTracked": coverage.get("endpointsTracked"), "endpointsCurrentlyListed": coverage.get("endpointsCurrentlyListed"),
        "total": payload.get("total"), "dataCompleteSince": payload.get("dataCompleteSince")}, source, day)]
    for rank, row in enumerate(payload.get("top") or [], start=1):
        if not isinstance(row, dict) or not row.get("host"):
            continue
        host = str(row["host"]).lower()
        events.append(seller_entity(host, source))
        events.append(listed_on(seller_id(host), REGISTRY, source, day, {"rank": rank + int(payload.get("offset") or 0), "probes30d": row.get("probes30d"),
                                                                          "endpoints": row.get("endpoints"), "basis": "x402-trust leaderboard row"}))
        if row.get("score") is not None:
            events.append(_fact(seller_id(host), "hostScore", row.get("score"), source, day))
        if row.get("grade") is not None:
            events.append(_fact(seller_id(host), "grade", row.get("grade"), source, day))
        if row.get("providerVerified") is not None:
            events.append(_fact(seller_id(host), "providerVerified", bool(row.get("providerVerified")), source, day))
    return events


def _report(payload: dict[str, Any], source: Any, day: str) -> list[WriteEvent]:
    reg = registry_id(REGISTRY)
    events: list[WriteEvent] = []
    for key in ("endpoints", "volume", "ecosystem", "grades"):
        if isinstance(payload.get(key), dict):
            events.append(_fact(reg, f"report.{key}", payload[key], source, day))
    if isinstance(payload.get("monthly"), list):
        events.append(_fact(reg, "report.monthly", payload["monthly"], source, day))
    return events


DISPATCH = {"stats": _stats, "search": _search, "verified": _verified, "facilitators": _facilitators,
            "leaderboard": _leaderboard, "report": _report}


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    """Third-party 30-day aggregates: facts carry the window in a statsBasis fact (mem-seed-004)."""
    payload = observation.payload
    if not isinstance(payload, dict):
        return []
    schema_type = payload.get("schemaType")
    if schema_type not in DISPATCH:
        return []
    source = provenance(observation)
    day = day_context_id(observation.at)
    head: list[WriteEvent] = [registry_entity(REGISTRY, source, {"url": "https://x402-trust.com"}), day_ctx(observation.at, source)]
    body = DISPATCH[schema_type](payload, source, day)
    return head + body if body else []
