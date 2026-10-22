"""Normalized records with provenance (SPEC.md 10.3 view columns). Owner: B1.

Pure functions that flatten one raw payload into rows whose column names and order are EXACTLY the view
columns listed in investigator/sql/views/VIEWS.md (the SQLite builder and the Zetaris SchemaStore views
produce the same columns; these helpers are the Python reference the SQLite side may reuse). Every table
carries the provenance of the bytes it came from (url, captured_at, sha256, replayed, stale, fixture_file).
No metric is computed here: rows are reshaped, wallets lowercased, amounts cast, hosts extracted. Nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit

from ..contracts import Observation

VIEW_COLUMNS: dict[str, list[str]] = {
    "sales": ["seller", "agent_id", "settled_at", "payer", "own_wallet", "amount_atomic", "tx_hash", "x402_version",
              "service", "resource", "facilitator"],
    "purchases": ["at", "url", "price_usd", "outcome", "tx_hash"],
    "bazaar_resources": ["resource", "host", "service_name", "resource_type", "last_updated", "calls_30d", "payers_30d",
                         "last_called_at", "pay_to", "network", "asset", "amount_atomic", "scheme", "page"],
    "bazaar_search": ["resource", "host", "service_name", "resource_type", "last_updated", "calls_30d", "payers_30d",
                      "last_called_at", "pay_to", "network", "asset", "amount_atomic", "scheme"],
    "catalog_resources": ["resource", "host", "calls_30d", "payers_30d", "last_called_at", "pay_to", "network", "asset",
                          "amount_atomic"],
    "openapi_paid_ops": ["path", "operation_id", "price_usd", "network", "asset", "pay_to", "method"],
    "agent402_tools": ["route", "slug", "paid", "price", "declared", "last_verified_at"],
    "agent402_seller": ["origin", "tool_count", "paid_tool_count", "paywall_ok", "health", "routable",
                        "router_dispatch_reason"],
    "vet402_endpoints": ["canonical_url", "method", "payee_id", "catalog_status", "first_seen", "last_seen"],
    "trust_search": ["resource", "host", "delisted", "score", "grade", "provider_verified", "total_reported"],
    "trust_stats": ["endpoints_listed", "endpoints_active", "settlements_30d", "settled_volume_usd_30d",
                    "distinct_payers_30d", "distinct_receivers_30d"],
}


@dataclass
class NormalizedTable:
    view: str
    columns: list[str]
    rows: list[dict[str, Any]]
    source: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"view": self.view, "columns": list(self.columns), "row_count": len(self.rows),
                "source": dict(self.source), "rows": [{c: r.get(c) for c in self.columns} for r in self.rows]}


# -- helpers ----------------------------------------------------------------------------------------------

def host_of(url: Any) -> str:
    try:
        return (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def lower_addr(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text.lower() if text else None


def to_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None if value is None else int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        try:
            return int(float(str(value).strip()))
        except ValueError:
            return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_flag(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes") else 0
    return 1 if value else 0


def source_of(obs: Observation) -> dict[str, Any]:
    return {"name": obs.name, "family": obs.family, "url": obs.url, "captured_at": obs.captured_at,
            "sha256": obs.bytes_sha256, "replayed": obs.replayed, "stale": obs.stale, "fixture_file": obs.fixture_file,
            "request_key": obs.request_key}


def _row(view: str, **values: Any) -> dict[str, Any]:
    return {column: values.get(column) for column in VIEW_COLUMNS[view]}


# -- receipts ----------------------------------------------------------------------------------------------

def sales_rows(receipts: dict[str, Any]) -> list[dict[str, Any]]:
    seller = lower_addr(receipts.get("seller"))
    agent_id = receipts.get("agentId")
    rows = []
    for sale in receipts.get("sales") or []:
        rows.append(_row("sales", seller=seller, agent_id=agent_id, settled_at=sale.get("at"),
                         payer=lower_addr(sale.get("payer")), own_wallet=to_flag(sale.get("ownWallet")),
                         amount_atomic=to_int(sale.get("amountAtomic")), tx_hash=lower_addr(sale.get("transaction")),
                         x402_version=sale.get("x402Version"), service=sale.get("service"),
                         resource=sale.get("resource"), facilitator=sale.get("facilitator")))
    return rows


def purchases_rows(receipts: dict[str, Any]) -> list[dict[str, Any]]:
    return [_row("purchases", at=p.get("at"), url=p.get("url"), price_usd=to_float(p.get("priceUsd")),
                 outcome=p.get("outcome"), tx_hash=lower_addr(p.get("transaction")))
            for p in receipts.get("purchases") or []]


# -- CDP Bazaar ---------------------------------------------------------------------------------------------

def _bazaar_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if items is None:
        items = payload.get("resources")
    return [i for i in (items or []) if isinstance(i, dict)]


def bazaar_rows(payload: Any, page: Optional[int]) -> list[dict[str, Any]]:
    """One row per (item, accepts[]) pair, like `explode(items) ... LATERAL VIEW explode(i.accepts)`; items
    without an accepts entry produce no row (explode semantics). page=None -> bazaar_search columns."""
    view = "bazaar_resources" if page is not None else "bazaar_search"
    rows = []
    for item in _bazaar_items(payload):
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        for accept in item.get("accepts") or []:
            if not isinstance(accept, dict):
                continue
            rows.append(_row(view, resource=item.get("resource"), host=host_of(item.get("resource")),
                             service_name=item.get("serviceName"), resource_type=item.get("type"),
                             last_updated=item.get("lastUpdated"), calls_30d=to_int(quality.get("l30DaysTotalCalls")),
                             payers_30d=to_int(quality.get("l30DaysUniquePayers")), last_called_at=quality.get("lastCalledAt"),
                             pay_to=lower_addr(accept.get("payTo")), network=accept.get("network"),
                             asset=lower_addr(accept.get("asset")), amount_atomic=to_int(accept.get("amount")),
                             scheme=accept.get("scheme"), page=page))
    return rows


def catalog_rows(snapshot: Any) -> list[dict[str, Any]]:
    """One row per snapshot item (first accepts entry for the payment columns), like `explode(items)`."""
    rows = []
    for item in _bazaar_items(snapshot):
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        accepts = [a for a in (item.get("accepts") or []) if isinstance(a, dict)]
        accept = accepts[0] if accepts else {}
        rows.append(_row("catalog_resources", resource=item.get("resource"), host=host_of(item.get("resource")),
                         calls_30d=to_int(quality.get("l30DaysTotalCalls")), payers_30d=to_int(quality.get("l30DaysUniquePayers")),
                         last_called_at=quality.get("lastCalledAt"), pay_to=lower_addr(accept.get("payTo")),
                         network=accept.get("network"), asset=lower_addr(accept.get("asset")),
                         amount_atomic=to_int(accept.get("amount"))))
    return rows


# -- store OpenAPI ------------------------------------------------------------------------------------------

def openapi_paid_ops_rows(openapi: Any) -> list[dict[str, Any]]:
    """Paid operations (those with x-payment-info), one row per (path, method); sorted by path then method."""
    rows = []
    paths = openapi.get("paths") if isinstance(openapi, dict) else None
    for path, operations in sorted((paths or {}).items()):
        if not isinstance(operations, dict):
            continue
        for method, op in sorted(operations.items()):
            if not isinstance(op, dict):
                continue
            info = op.get("x-payment-info")
            if not isinstance(info, dict):
                continue
            price = info.get("price") if isinstance(info.get("price"), dict) else {}
            rows.append(_row("openapi_paid_ops", path=path, operation_id=op.get("operationId"),
                             price_usd=to_float(price.get("amount")), network=info.get("network"),
                             asset=lower_addr(info.get("asset")), pay_to=lower_addr(info.get("payTo")),
                             method=str(method).upper()))
    return rows


# -- Agent402 -------------------------------------------------------------------------------------------------

def agent402_tools_rows(index: Any) -> list[dict[str, Any]]:
    if not isinstance(index, dict):
        return []
    return [_row("agent402_tools", route=t.get("route"), slug=t.get("slug"), paid=to_flag(t.get("paid")),
                 price=t.get("price"), declared=to_flag(t.get("declared")), last_verified_at=t.get("lastVerifiedAt"))
            for t in index.get("tools") or [] if isinstance(t, dict)]


def agent402_seller_rows(index: Any) -> list[dict[str, Any]]:
    if not isinstance(index, dict) or "origin" not in index:
        return []
    paywall = index.get("paywall") if isinstance(index.get("paywall"), dict) else {}
    return [_row("agent402_seller", origin=index.get("origin"), tool_count=to_int(index.get("toolCount")),
                 paid_tool_count=to_int(index.get("paidToolCount")), paywall_ok=to_flag(paywall.get("ok")),
                 health=index.get("health"), routable=to_flag(index.get("routable")),
                 router_dispatch_reason=index.get("routerDispatchReason"))]


# -- vet402 ------------------------------------------------------------------------------------------------

def vet402_endpoint_rows(resolve: Any) -> list[dict[str, Any]]:
    if not isinstance(resolve, dict):
        return []
    return [_row("vet402_endpoints", canonical_url=e.get("canonical_url"), method=e.get("method"),
                 payee_id=lower_addr(e.get("payee_id")), catalog_status=e.get("catalog_status"),
                 first_seen=e.get("first_seen"), last_seen=e.get("last_seen"))
            for e in resolve.get("endpoints") or [] if isinstance(e, dict)]


# -- x402-trust ---------------------------------------------------------------------------------------------

def trust_search_rows(search: Any) -> list[dict[str, Any]]:
    if not isinstance(search, dict):
        return []
    total = to_int(search.get("total"))
    return [_row("trust_search", resource=r.get("resource"), host=(r.get("host") or host_of(r.get("resource")) or "").lower(),
                 delisted=to_flag(r.get("delisted")), score=to_float(r.get("score")), grade=r.get("grade"),
                 provider_verified=to_flag(r.get("providerVerified")), total_reported=total)
            for r in search.get("results") or [] if isinstance(r, dict)]


def trust_stats_rows(stats: Any) -> list[dict[str, Any]]:
    if not isinstance(stats, dict) or "endpointsListed" not in stats:
        return []
    return [_row("trust_stats", endpoints_listed=to_int(stats.get("endpointsListed")),
                 endpoints_active=to_int(stats.get("endpointsActive")), settlements_30d=to_int(stats.get("settlements30d")),
                 settled_volume_usd_30d=to_float(stats.get("settledVolumeUsd30d")),
                 distinct_payers_30d=to_int(stats.get("distinctPayers30d")),
                 distinct_receivers_30d=to_int(stats.get("distinctReceivers30d")))]


# -- dispatch ---------------------------------------------------------------------------------------------

def _page_of(obs: Observation) -> Optional[int]:
    if obs.name.startswith("cdp_page_"):
        try:
            return int(obs.name.rsplit("_", 1)[-1])
        except ValueError:
            return None
    return None


def normalize(obs: Observation) -> list[NormalizedTable]:
    """Every normalized table one ok Observation yields (empty for families without a flat view)."""
    if obs.status != "ok" or obs.payload is None:
        return []
    src = source_of(obs)
    payload = obs.payload
    tables: list[tuple[str, list[dict[str, Any]]]] = []
    if obs.family == "receipts" and isinstance(payload, dict):
        tables += [("sales", sales_rows(payload)), ("purchases", purchases_rows(payload))]
    elif obs.family == "cdp_bazaar":
        tables.append(("bazaar_resources", bazaar_rows(payload, _page_of(obs) or 0)))
    elif obs.family == "cdp_search":
        tables.append(("bazaar_search", bazaar_rows(payload, None)))
    elif obs.family == "cdp_catalog_snapshot":
        tables.append(("catalog_resources", catalog_rows(payload)))
    elif obs.family == "store_openapi" and isinstance(payload, dict) and "paths" in payload:
        tables.append(("openapi_paid_ops", openapi_paid_ops_rows(payload)))
    elif obs.family == "agent402" and isinstance(payload, dict) and "tools" in payload:
        tables += [("agent402_tools", agent402_tools_rows(payload)), ("agent402_seller", agent402_seller_rows(payload))]
    elif obs.family == "vet402":
        tables.append(("vet402_endpoints", vet402_endpoint_rows(payload)))
    elif obs.family == "x402_trust" and isinstance(payload, dict):
        if payload.get("schemaType") == "search" or "results" in payload:
            tables.append(("trust_search", trust_search_rows(payload)))
        elif payload.get("schemaType") == "stats" or "endpointsListed" in payload:
            tables.append(("trust_stats", trust_stats_rows(payload)))
    return [NormalizedTable(view=view, columns=list(VIEW_COLUMNS[view]), rows=rows, source=dict(src))
            for view, rows in tables]
