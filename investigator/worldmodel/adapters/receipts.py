"""receipts.json -> seller, wallets (own + external), endpoints, paid edges (one per settlement, context day), pays_to. Owner: B2.

Payload shape (fixtures/raw/store-receipts.json): {generatedAt, seller, agentId, network, totals, sales[], purchases[]};
sale keys: at, service, resource, amountAtomic (decimal string), payer (mixed case), ownWallet, transaction,
facilitator?, x402Version. Payer is lowercased; amountAtomic becomes int; ownWallet rides on the paid edge attrs
(the Analyst/Critic treat own-wallet rows as self-settlement, mem-seed-003). Provenance kind receipts.
"""
from __future__ import annotations

from typing import Any

from ..domain import USDC_BASE, as_int, day_context_id, endpoint_id, facilitator_id, host_of_url, seller_id, wallet_id
from ..types import AssertFact, IngestObservation, Relate, WriteEvent
from ._common import day_ctx, endpoint_entity, facilitator_entity, provenance, seller_entity, wallet_entity


def _endpoint_for(sale: dict[str, Any], host: str) -> tuple[str, str]:
    resource = sale.get("resource")
    service = sale.get("service")
    if resource:
        if not service:
            service = str(resource).rstrip("/").rsplit("/", 1)[-1]
        return str(resource), str(service)
    if service:
        return f"https://{host}/api/{service}", str(service)
    return f"https://{host}/", "unknown"


def to_events(observation: IngestObservation) -> list[WriteEvent]:
    """Payer lowercased; amountAtomic as int; ownWallet flag on the paid edge attrs; provenance kind receipts."""
    payload = observation.payload
    if not isinstance(payload, dict) or not isinstance(payload.get("sales"), list):
        return []
    source = provenance(observation)
    sales: list[dict[str, Any]] = [s for s in payload["sales"] if isinstance(s, dict)]
    host = host_of_url(source.url)
    if not host:
        for sale in sales:
            host = host_of_url(sale.get("resource"))
            if host:
                break
    host = (host or "unknown").lower()
    network = str(payload.get("network") or "eip155:8453")
    seller_wallet = str(payload.get("seller") or "").lower() or None

    events: list[WriteEvent] = [seller_entity(host, source, {
        "agentId": payload.get("agentId"), "network": network, "sellerWallet": seller_wallet,
        "receiptsGeneratedAt": payload.get("generatedAt")})]
    if seller_wallet:
        events.append(wallet_entity(seller_wallet, source, network, {"role": "seller"}))
        events.append(AssertFact(about=seller_id(host), predicate="payTo", value=seller_wallet, source=source, confidence=0.9))
        events.append(AssertFact(about=seller_id(host), predicate="agentId", value=payload.get("agentId"), source=source, confidence=0.9))

    endpoints_seen: set[str] = set()
    facilitators_seen: set[tuple[str, str]] = set()
    days_seen: set[str] = set()
    for sale in sales:
        payer_raw = sale.get("payer")
        if not payer_raw or not sale.get("at"):
            continue
        payer = str(payer_raw).lower()
        own = bool(sale.get("ownWallet"))
        at = str(sale["at"])
        resource, service = _endpoint_for(sale, host)
        if resource not in endpoints_seen:
            endpoints_seen.add(resource)
            events.append(endpoint_entity(resource, source, {"service": service, "host": host}))
            events.append(Relate(from_id=seller_id(host), type="sells", to_id=endpoint_id(resource), source=source))
            if seller_wallet:
                events.append(Relate(from_id=endpoint_id(resource), type="pays_to", to_id=wallet_id(seller_wallet), source=source,
                                     attrs={"network": network, "asset": USDC_BASE, "basis": "receipts seller wallet"}))
        events.append(wallet_entity(payer, source, network, {"ownWallet": own, "role": "payer"}))
        day = day_context_id(at)
        if day not in days_seen:
            days_seen.add(day)
            events.append(day_ctx(at, source))
        facilitator = sale.get("facilitator")
        if facilitator and (str(facilitator), resource) not in facilitators_seen:
            facilitators_seen.add((str(facilitator), resource))
            events.append(facilitator_entity(str(facilitator), source))
            events.append(Relate(from_id=endpoint_id(resource), type="settled_via", to_id=facilitator_id(str(facilitator)), source=source))
        events.append(Relate(
            from_id=wallet_id(payer), type="paid", to_id=endpoint_id(resource), source=source, context=day, valid_from=at,
            attrs={"tx": sale.get("transaction"), "amountAtomic": as_int(sale.get("amountAtomic")), "asset": USDC_BASE,
                   "network": network, "facilitator": facilitator, "x402Version": sale.get("x402Version"),
                   "ownWallet": own, "service": service}))
    return events
