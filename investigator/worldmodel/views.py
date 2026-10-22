"""World Model views: graph / facts / snapshot / timeline plus the domain reducer views (SPEC.md 9.4). Owner: B2.

Point-in-time folds respect validity (validFrom/validTo) and supersession. The domain views feed
agents/reducers.py and the Verifier's cross-checks: sales_by_payer(host), listings_by_host(registry),
external_settlements(host), registry_counts(host), catalog_hosts(). Every function is a pure projection of
the model state (no I/O) and returns plain JSON-ready dicts in deterministic order.
"""
from __future__ import annotations

from typing import Any, Optional

from .domain import registry_id, seller_id
from .model import WorldModel
from .store import WorldState
from .types import Relationship, to_wire


def _valid_at(rel: Relationship, at: str) -> bool:
    if rel.valid_from and at < rel.valid_from:
        return False
    if rel.valid_to and at >= rel.valid_to:
        return False
    return True


def _state_at(model: WorldModel, at: Optional[str]) -> WorldState:
    if at is None:
        return model.state
    iso = f"{at}T23:59:59.999Z" if len(at) == 10 else at
    return model.store.fold_until(iso)


def _entity_host(state: WorldState, entity_id: str) -> Optional[str]:
    entity = state.entities.get(entity_id)
    if entity is None:
        return None
    host = entity.attrs.get("host")
    if host:
        return str(host).lower()
    if entity_id.startswith("seller:host:"):
        return entity_id.split(":", 2)[2]
    return None


# ---- engine views --------------------------------------------------------------------------------------

def graph(model: WorldModel, at: Optional[str] = None, include_closed: bool = False) -> dict[str, Any]:
    """Nodes + bitemporal edges (open edges only unless include_closed or a point in time is given)."""
    state = _state_at(model, at)
    nodes = [{"id": e.id, "type": e.type, "name": e.name, "state": e.state, "properties": dict(e.attrs)}
             for e in sorted(state.entities.values(), key=lambda x: x.id)]
    edges = []
    for rel in sorted(state.relationships.values(), key=lambda x: x.id):
        if at is not None and not _valid_at(rel, at if len(at) > 10 else f"{at}T23:59:59.999Z"):
            continue
        if at is None and rel.valid_to and not include_closed:
            continue
        edges.append(to_wire(rel))
    return {"nodes": nodes, "edges": edges}


def entity(model: WorldModel, entity_id: str) -> Optional[dict[str, Any]]:
    found = model.state.entities.get(model.resolve(entity_id))
    return to_wire(found) if found is not None else None


def neighbors_of(model: WorldModel, entity_id: str, via: Optional[str] = None, in_context: Optional[str] = None,
                 at: Optional[str] = None) -> list[dict[str, Any]]:
    """Entities connected by open edges now, or by edges valid at `at` (bitemporal query)."""
    state = model.state
    rid = model.resolve(entity_id)
    out: list[dict[str, Any]] = []
    for rel in state.relationships.values():
        if via and rel.type != via:
            continue
        if in_context and rel.context != in_context:
            continue
        if at and not _valid_at(rel, at):
            continue
        if not at and rel.valid_to:
            continue
        other = rel.to_id if rel.from_id == rid else (rel.from_id if rel.to_id == rid else None)
        if other is None:
            continue
        found = state.entities.get(other)
        if found is not None:
            out.append(to_wire(found))
    return sorted(out, key=lambda e: e["id"])


def facts(model: WorldModel, about: Optional[str] = None, predicate: Optional[str] = None,
          at: Optional[str] = None, include_superseded: bool = True) -> list[dict[str, Any]]:
    state = model.state
    rid = model.resolve(about) if about else None
    out = []
    for fact in state.facts.values():
        if rid and fact.about != rid:
            continue
        if predicate and fact.predicate != predicate:
            continue
        if at and fact.asserted_at > at:
            continue
        if not include_superseded and fact.superseded_by:
            continue
        out.append(to_wire(fact))
    return sorted(out, key=lambda f: (f["about"], f["predicate"], f["assertedAt"], f["id"]))


def snapshot(model: WorldModel, at: str) -> dict[str, Any]:
    """Point-in-time fold (events with at <= timestamp); relationships filtered by validity at that instant."""
    iso = f"{at}T23:59:59.999Z" if len(at) == 10 else at
    state = model.store.fold_until(iso)
    return {
        "at": iso,
        "entities": [to_wire(e) for e in sorted(state.entities.values(), key=lambda x: x.id)],
        "contexts": [to_wire(c) for c in sorted(state.contexts.values(), key=lambda x: x.id)],
        "relationships": [to_wire(r) for r in sorted(state.relationships.values(), key=lambda x: x.id) if _valid_at(r, iso)],
        "facts": [to_wire(f) for f in sorted(state.facts.values(), key=lambda x: x.id) if f.asserted_at <= iso],
    }


def entity_at(model: WorldModel, entity_id: str, at: str) -> Optional[dict[str, Any]]:
    iso = f"{at}T23:59:59.999Z" if len(at) == 10 else at
    state = model.store.fold_until(iso)
    current = entity_id
    seen: set[str] = set()
    while current in state.aliases and current not in seen:
        seen.add(current)
        current = state.aliases[current]
    found = state.entities.get(current)
    return to_wire(found) if found is not None else None


def timeline(model: WorldModel, entity_id: str) -> list[dict[str, Any]]:
    """Every event that touches the entity (as subject, from, to or about), in append order."""
    rid = model.resolve(entity_id)
    out = []
    for event in model.store.events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        touched = {payload.get("id"), payload.get("from"), payload.get("to"), payload.get("about"),
                   payload.get("targetId"), payload.get("aliasId"), payload.get("primaryId")}
        if rid in touched or entity_id in touched:
            out.append({"seq": event.seq, "id": event.id, "kind": event.kind, "at": event.at, "payload": payload})
    return out


# ---- domain reducer views ------------------------------------------------------------------------------

def _paid_edges_for_host(state: WorldState, host: str) -> list[Relationship]:
    host = host.lower()
    edges = [r for r in state.relationships.values() if r.type == "paid" and _entity_host(state, r.to_id) == host]
    return sorted(edges, key=lambda r: (r.valid_from or "", r.id))


def sales_by_payer(model: WorldModel, host: str) -> list[dict[str, Any]]:
    """Per payer wallet of a seller host: sales, atomic USDC, window, services, own-wallet flag."""
    groups: dict[str, dict[str, Any]] = {}
    for rel in _paid_edges_for_host(model.state, host):
        attrs = rel.attrs or {}
        row = groups.setdefault(rel.from_id, {
            "payer": rel.from_id.split(":", 2)[2] if rel.from_id.count(":") >= 2 else rel.from_id,
            "wallet_id": rel.from_id, "own_wallet": bool(attrs.get("ownWallet")), "n_sales": 0, "usdc_atomic": 0,
            "first_at": rel.valid_from, "last_at": rel.valid_from, "services": [], "tx_hashes": []})
        row["n_sales"] += 1
        row["usdc_atomic"] += int(attrs.get("amountAtomic") or 0)
        if rel.valid_from and (row["first_at"] is None or rel.valid_from < row["first_at"]):
            row["first_at"] = rel.valid_from
        if rel.valid_from and (row["last_at"] is None or rel.valid_from > row["last_at"]):
            row["last_at"] = rel.valid_from
        service = attrs.get("service")
        if service and service not in row["services"]:
            row["services"].append(service)
        if attrs.get("tx"):
            row["tx_hashes"].append(attrs["tx"])
    rows = list(groups.values())
    for row in rows:
        row["services"].sort()
        row["n_services"] = len(row["services"])
    return sorted(rows, key=lambda r: (-r["n_sales"], r["payer"]))


def external_settlements(model: WorldModel, host: str) -> list[dict[str, Any]]:
    """One row per settlement whose payer is NOT an own wallet of the seller (newest first)."""
    rows = []
    for rel in _paid_edges_for_host(model.state, host):
        attrs = rel.attrs or {}
        if attrs.get("ownWallet"):
            continue
        rows.append({"at": rel.valid_from, "payer": rel.from_id.split(":", 2)[2], "endpoint": rel.to_id.split(":", 2)[2],
                     "service": attrs.get("service"), "tx": attrs.get("tx"), "amount_atomic": int(attrs.get("amountAtomic") or 0),
                     "facilitator": attrs.get("facilitator"), "x402_version": attrs.get("x402Version"), "day": rel.context})
    return sorted(rows, key=lambda r: (r["at"] or "", r["tx"] or ""), reverse=True)


def listings_by_host(model: WorldModel, registry: str) -> list[dict[str, Any]]:
    """Endpoints listed on a registry, grouped by host (search/page listings; the snapshot folds per host)."""
    state = model.state
    target = registry_id(registry)
    groups: dict[str, dict[str, Any]] = {}
    for rel in state.relationships.values():
        if rel.type != "listed_on" or rel.to_id != target:
            continue
        host = _entity_host(state, rel.from_id) or "unknown"
        row = groups.setdefault(host, {"host": host, "listings": 0, "endpoints": [], "calls_30d": 0, "payers_30d_max": 0})
        if rel.from_id.startswith("endpoint:"):
            row["listings"] += 1
            row["endpoints"].append(rel.from_id.split(":", 2)[2])
            subject = state.entities.get(rel.from_id)
            if subject is not None:
                row["calls_30d"] += int(subject.attrs.get("l30DaysTotalCalls") or 0)
                row["payers_30d_max"] = max(row["payers_30d_max"], int(subject.attrs.get("l30DaysUniquePayers") or 0))
        else:
            attrs = rel.attrs or {}
            row["listings"] += int(attrs.get("listings") or 0)
            subject = state.entities.get(rel.from_id)
            if subject is not None:
                row["calls_30d"] += int(subject.attrs.get("catalog.l30DaysTotalCalls") or 0)
                row["payers_30d_max"] = max(row["payers_30d_max"], int(subject.attrs.get("catalog.l30DaysUniquePayersMax") or 0))
    rows = list(groups.values())
    for row in rows:
        row["endpoints"].sort()
    return sorted(rows, key=lambda r: (-r["listings"], r["host"]))


def registry_counts(model: WorldModel, host: str) -> dict[str, Any]:
    """What each registry says about one host (the inputs of the endpoint_count_for_host Conflict)."""
    state = model.state
    host = host.lower()
    seller = state.entities.get(seller_id(host))
    attrs = seller.attrs if seller is not None else {}

    def listed(registry: str) -> list[Relationship]:
        target = registry_id(registry)
        return [r for r in state.relationships.values()
                if r.type == "listed_on" and r.to_id == target and r.from_id.startswith("endpoint:") and _entity_host(state, r.from_id) == host]

    bazaar = listed("cdp-bazaar")
    vet = listed("vet402")
    trust = listed("x402-trust")
    pay_tos = sorted({r.to_id for r in state.relationships.values() if r.type == "pays_to" and _entity_host(state, r.from_id) == host
                      and any(l.from_id == r.from_id for l in bazaar)})
    return {
        "host": host,
        "cdp_bazaar": {"listings": len(bazaar), "pay_to": [p.split(":", 2)[2] for p in pay_tos],
                       "calls_30d_sum": sum(int((state.entities.get(r.from_id).attrs.get("l30DaysTotalCalls") or 0)) for r in bazaar if state.entities.get(r.from_id)),
                       "payers_30d_max": max([int((state.entities.get(r.from_id).attrs.get("l30DaysUniquePayers") or 0)) for r in bazaar if state.entities.get(r.from_id)] or [0]),
                       "basis": "search capped at 20, one row per resource"},
        "store_openapi": {"declared_paid_ops": attrs.get("declaredPaidOps"), "basis": "seller's own OpenAPI, x-payment-info operations"},
        "agent402": {"tool_count": attrs.get("toolCount"), "paid_tool_count": attrs.get("paidToolCount"),
                     "paywall_ok": attrs.get("paywall.ok"), "health": attrs.get("health"), "routable": attrs.get("routable"),
                     "router_dispatch_reason": attrs.get("routerDispatchReason"), "basis": "crawled tools including free ones"},
        "vet402": {"endpoints": len(vet), "listed": sum(1 for r in vet if (r.attrs or {}).get("catalog_status") == "listed"),
                   "basis": "endpoints observed by vet402, delisted included"},
        "x402_trust": {"search_total": attrs.get("trustSearchTotal"), "search_returned": attrs.get("trustSearchReturned"),
                       "listed": len(trust), "provider_verified": attrs.get("providerVerified"), "host_score": attrs.get("hostScore"),
                       "basis": "keyword matches across x402-trust's own index, delisted included"},
        "receipts": {"pay_to": attrs.get("payTo"), "basis": "self-reported receipts.json"},
    }


def catalog_hosts(model: WorldModel) -> list[dict[str, Any]]:
    """Per-host catalog aggregates folded from the Bazaar snapshot (listings, 30-day calls, max unique payers)."""
    state = model.state
    rows = []
    for e in state.entities.values():
        if e.type != "seller" or "catalog.listings" not in e.attrs:
            continue
        rows.append({"host": _entity_host(state, e.id), "listings": int(e.attrs.get("catalog.listings") or 0),
                     "calls_30d": int(e.attrs.get("catalog.l30DaysTotalCalls") or 0),
                     "payers_30d_max": int(e.attrs.get("catalog.l30DaysUniquePayersMax") or 0),
                     "pay_to_count": int(e.attrs.get("catalog.payToCount") or 0),
                     "last_called_at": e.attrs.get("catalog.lastCalledAt")})
    return sorted(rows, key=lambda r: (-r["calls_30d"], -r["listings"], r["host"] or ""))


def registry_facts(model: WorldModel, registry: str) -> dict[str, Any]:
    """Winning facts asserted on a registry entity (ecosystem stats, catalog size, route demand)."""
    found = model.state.entities.get(registry_id(registry))
    return dict(found.attrs) if found is not None else {}
