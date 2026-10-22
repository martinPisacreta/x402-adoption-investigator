"""Pure-Python reducers over the run exports, one per metric, plus the SQL extractors that read the same value out
of the committed catalog queries (SPEC.md 6.5 and metrics.py). Owner: B4.

Every reducer has the signature reducer(world: WorldExports, params: dict) -> value and must compute the same
figure as its SQL query from the SAME exports (counts/sums exact, shares within 1e-6). Shares return
{"numerator": n, "denominator": d, "share": n / d}. Reducers never read the network and never read fixtures
directly: they read what the Collector wrote to logs/<run_id>/exports/ (the raw per-source exports, the Verifier's
settlements.json and the Keeper's world-*.json when present).

Row semantics mirror sql/views/VIEWS.md exactly: `sales` is one row per receipts sale with the payer lowercased
and amountAtomic cast to int; `bazaar_search` is one row per (resource, accepts[]) pair (LATERAL VIEW explode
drops resources with no accepts); `catalog_resources` is one row per snapshot item with pay_to from accepts[0];
wallets are lowercased in the view layer; `host` is urllib's hostname lowercased.

SQL extractors have the signature extractor(results: dict[query_id, SqlResult], params) -> value | None, where
None means "the query did not run"; they only read typed rows (no arithmetic beyond what the metric defines).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..contracts import SqlResult
from ..zetaris.views_sqlite import host_of as _view_host_of


@dataclass
class WorldExports:
    """Loaded exports: World Model views plus the raw per-source exports keyed by observation name."""

    entities: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    chain_facts: list[dict[str, Any]] = field(default_factory=list)   # Verifier chain facts (dicts: about, predicate, value)
    now_iso: str = ""                                                  # run clock at start (sample: MANIFEST.fetchedAt)


Reducer = Callable[[WorldExports, dict[str, Any]], Any]
Extractor = Callable[[dict[str, SqlResult], dict[str, Any]], Any]
REDUCERS: dict[str, Reducer] = {}
SQL_EXTRACTORS: dict[str, Extractor] = {}


def reducer(name: str) -> Callable[[Reducer], Reducer]:
    def register(fn: Reducer) -> Reducer:
        REDUCERS[f"reducers.{name}"] = fn
        return fn
    return register


def extractor(metric_id: str) -> Callable[[Extractor], Extractor]:
    def register(fn: Extractor) -> Extractor:
        SQL_EXTRACTORS[metric_id] = fn
        return fn
    return register


def share(numerator: int, denominator: int) -> dict[str, Any]:
    return {"numerator": numerator, "denominator": denominator,
            "share": (numerator / denominator) if denominator else None}


def format_pairs(pairs: list[tuple[Any, Any]]) -> str:
    """Canonical text for "key=value" lists ("cuit-ar=3, x402-ping=2"); shared by both halves of a text metric."""
    return ", ".join(f"{k}={v}" for k, v in pairs)


def host_of(url: Any) -> Optional[str]:
    """Host column semantics of the view layer (B3's parity reference: Spark parse_url -> java.net.URI, NULL when
    the URI is rejected), so reducers and SQL group by exactly the same host values."""
    return _view_host_of(url)


RAW_ALIASES: dict[str, tuple[str, ...]] = {
    "catalog_snapshot": ("catalog_snapshot", "cdp_catalog_snapshot", "cdp_catalog"),
    "cdp_search_host": ("cdp_search_host", "cdp_search"),
    "store_receipts": ("store_receipts", "receipts"),
}


def raw(world: "WorldExports", name: str) -> Any:
    """A raw export by observation name, tolerant of the aliases the Collector and the view layer accept."""
    for key in RAW_ALIASES.get(name, (name,)):
        if key in world.raw:
            return world.raw[key]
    return None


def window_since(now_iso: str, days: int = 30) -> str:
    """Date (YYYY-MM-DD) `days` before the run clock, computed with the stdlib only."""
    import datetime as _dt
    base = _dt.datetime.fromisoformat(now_iso.replace("Z", "+00:00")) if now_iso else _dt.datetime.now(_dt.timezone.utc)
    return (base - _dt.timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------------------------------------
# Row builders (mirror of the view layer; see VIEWS.md)
# ---------------------------------------------------------------------------------------------------------

def _int(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    return int(float(str(value)))


def receipts_payload(world: WorldExports) -> Optional[dict[str, Any]]:
    payload = raw(world, "store_receipts")
    return payload if isinstance(payload, dict) else None


def sales_rows(world: WorldExports) -> list[dict[str, Any]]:
    payload = receipts_payload(world)
    if not payload:
        return []
    seller = str(payload.get("seller") or "").lower()
    rows: list[dict[str, Any]] = []
    for sale in payload.get("sales") or []:
        if not isinstance(sale, dict):
            continue
        rows.append({
            "seller": seller,
            "settled_at": str(sale.get("at") or ""),
            "payer": str(sale.get("payer") or "").lower(),
            "own_wallet": 1 if sale.get("ownWallet") else 0,
            "amount_atomic": _int(sale.get("amountAtomic")),
            "tx_hash": str(sale.get("transaction") or "").lower(),
            "service": sale.get("service"),
            "resource": sale.get("resource"),
            "facilitator": sale.get("facilitator"),
        })
    return rows


def external_sales(world: WorldExports) -> list[dict[str, Any]]:
    return [r for r in sales_rows(world) if r["own_wallet"] == 0]


def _primary_accept(item: dict[str, Any]) -> dict[str, Any]:
    accepts = item.get("accepts")
    if isinstance(accepts, list) and accepts and isinstance(accepts[0], dict):
        return accepts[0]
    return {}


def _bazaar_rows(items: list[Any]) -> list[dict[str, Any]]:
    """One row per listing with the primary accept (VIEWS.md / B3 parity: bazaar_* views do not explode accepts)."""
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        accept = _primary_accept(item)
        quality = item.get("quality") or {}
        rows.append({
            "resource": item.get("resource"),
            "host": host_of(item.get("resource")),
            "calls_30d": _int(quality.get("l30DaysTotalCalls")),
            "payers_30d": _int(quality.get("l30DaysUniquePayers")),
            "last_called_at": quality.get("lastCalledAt"),
            "pay_to": str(accept.get("payTo") or "").lower() or None,
            "network": accept.get("network"),
            "asset": accept.get("asset"),
            "amount_atomic": _int(accept.get("amount") or accept.get("maxAmountRequired")),
        })
    return rows


def bazaar_search_rows(world: WorldExports, host: Optional[str]) -> list[dict[str, Any]]:
    payload = raw(world, "cdp_search_host")
    if not isinstance(payload, dict):
        return []
    rows = _bazaar_rows(payload.get("resources") or [])
    if host:
        rows = [r for r in rows if r["host"] == host.lower()]
    return rows


def catalog_rows(world: WorldExports) -> list[dict[str, Any]]:
    payload = raw(world, "catalog_snapshot")
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        first = _primary_accept(item)
        quality = item.get("quality") or {}
        rows.append({
            "resource": item.get("resource"),
            "host": host_of(item.get("resource")),
            "calls_30d": _int(quality.get("l30DaysTotalCalls")),
            "payers_30d": _int(quality.get("l30DaysUniquePayers")),
            "pay_to": str(first.get("payTo") or "").lower() or None,
        })
    return rows


def settlement_rows(world: WorldExports) -> Optional[list[dict[str, Any]]]:
    """Rows of exports/settlements.json (written by the Verifier); None when the export does not exist."""
    payload = world.raw.get("settlements")
    if payload is None:
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            out.append({k: (str(v).lower() if k in ("tx_hash", "payer", "seller") and v is not None else v)
                        for k, v in row.items()})
    return out


def chain_fact_value(world: WorldExports, predicate: str, about: Optional[str] = None) -> Any:
    """Value of a chain fact (Verifier payload or world-facts.json) by predicate, optionally about an entity id."""
    candidates = list(world.chain_facts) + [f for f in world.facts if isinstance(f, dict)]
    for fact in candidates:
        if fact.get("predicate") != predicate:
            continue
        if about and str(fact.get("about") or "").lower() != about.lower():
            continue
        return fact.get("value")
    return None


def _rows(results: dict[str, SqlResult], query_id: str) -> Optional[list[dict[str, Any]]]:
    result = results.get(query_id)
    if result is None:
        return None
    return [dict(zip(result.headers, row)) for row in result.rows]


# ---------------------------------------------------------------------------------------------------------
# seller_profile
# ---------------------------------------------------------------------------------------------------------

@reducer("sales_total")
def sales_total(world: WorldExports, params: dict[str, Any]) -> int:
    return len(sales_rows(world))


@extractor("sales_total")
def _x_sales_total(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q01_sales_by_wallet_class")
    return None if rows is None else sum(_int(r["n_sales"]) for r in rows)


@reducer("own_wallet_sales")
def own_wallet_sales(world: WorldExports, params: dict[str, Any]) -> int:
    return sum(1 for r in sales_rows(world) if r["own_wallet"] == 1)


@extractor("own_wallet_sales")
def _x_own_wallet_sales(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q01_sales_by_wallet_class")
    return None if rows is None else sum(_int(r["n_sales"]) for r in rows if _int(r["own_wallet"]) == 1)


@reducer("own_wallets_total")
def own_wallets_total(world: WorldExports, params: dict[str, Any]) -> int:
    return len({r["payer"] for r in sales_rows(world) if r["own_wallet"] == 1})


@extractor("own_wallets_total")
def _x_own_wallets_total(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q01_sales_by_wallet_class")
    return None if rows is None else sum(_int(r["n_payers"]) for r in rows if _int(r["own_wallet"]) == 1)


@reducer("external_sales_total")
def external_sales_total(world: WorldExports, params: dict[str, Any]) -> int:
    return len(external_sales(world))


@extractor("external_sales_total")
def _x_external_sales_total(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q01_sales_by_wallet_class")
    return None if rows is None else sum(_int(r["n_sales"]) for r in rows if _int(r["own_wallet"]) == 0)


@reducer("external_usdc_atomic")
def external_usdc_atomic(world: WorldExports, params: dict[str, Any]) -> int:
    return sum(r["amount_atomic"] for r in external_sales(world))


@extractor("external_usdc_atomic")
def _x_external_usdc_atomic(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q01_sales_by_wallet_class")
    return None if rows is None else sum(_int(r["usdc_atomic"]) for r in rows if _int(r["own_wallet"]) == 0)


@reducer("external_payers_total")
def external_payers_total(world: WorldExports, params: dict[str, Any]) -> int:
    return len({r["payer"] for r in external_sales(world)})


@extractor("external_payers_total")
def _x_external_payers_total(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q02_external_payers")
    return None if rows is None else len(rows)


@reducer("self_settlement_share")
def self_settlement_share(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    rows = sales_rows(world)
    return share(sum(1 for r in rows if r["own_wallet"] == 1), len(rows))


@extractor("self_settlement_share")
def _x_self_settlement_share(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q01_sales_by_wallet_class")
    if rows is None:
        return None
    own = sum(_int(r["n_sales"]) for r in rows if _int(r["own_wallet"]) == 1)
    return share(own, sum(_int(r["n_sales"]) for r in rows))


@reducer("repeat_external_payers")
def repeat_external_payers(world: WorldExports, params: dict[str, Any]) -> int:
    counts: dict[str, int] = {}
    for r in external_sales(world):
        counts[r["payer"]] = counts.get(r["payer"], 0) + 1
    return sum(1 for n in counts.values() if n >= 2)


@extractor("repeat_external_payers")
def _x_repeat_external_payers(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q03_repeat_external_payers")
    return None if rows is None else len(rows)


def _top_payer(world: WorldExports) -> tuple[Optional[str], int, int]:
    counts: dict[str, int] = {}
    for r in external_sales(world):
        counts[r["payer"]] = counts.get(r["payer"], 0) + 1
    if not counts:
        return None, 0, 0
    payer = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return payer[0], payer[1], sum(counts.values())


@reducer("top_payer_share")
def top_payer_share(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    payer, top, total = _top_payer(world)
    value = share(top, total)
    value["payer"] = payer
    return value


@extractor("top_payer_share")
def _x_top_payer_share(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q04_top_payer_share")
    if rows is None:
        return None
    if not rows:
        value = share(0, 0)
        value["payer"] = None
        return value
    row = rows[0]
    value = share(_int(row["top_payer_sales"]), _int(row["external_sales_total"]))
    value["payer"] = (str(row["payer"]).lower() if row.get("payer") is not None else None)
    return value


@reducer("external_window")
def external_window(world: WorldExports, params: dict[str, Any]) -> Optional[str]:
    ats = sorted(r["settled_at"] for r in external_sales(world))
    return f"{ats[0]}..{ats[-1]}" if ats else None


@extractor("external_window")
def _x_external_window(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[str]:
    rows = _rows(results, "q02_external_payers")
    if rows is None:
        return None
    if not rows:
        return None
    return f"{min(str(r['first_at']) for r in rows)}..{max(str(r['last_at']) for r in rows)}"


@reducer("external_sales_by_service")
def external_sales_by_service(world: WorldExports, params: dict[str, Any]) -> str:
    counts: dict[Any, int] = {}
    for r in external_sales(world):
        counts[r["service"]] = counts.get(r["service"], 0) + 1
    pairs = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return format_pairs(pairs)


@extractor("external_sales_by_service")
def _x_external_sales_by_service(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[str]:
    rows = _rows(results, "q05_external_sales_by_service")
    return None if rows is None else format_pairs([(r["service"], _int(r["n_sales"])) for r in rows])


@reducer("external_sales_by_day")
def external_sales_by_day(world: WorldExports, params: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for r in external_sales(world):
        day = r["settled_at"][:10]
        counts[day] = counts.get(day, 0) + 1
    return format_pairs(sorted(counts.items()))


@extractor("external_sales_by_day")
def _x_external_sales_by_day(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[str]:
    rows = _rows(results, "q18_external_sales_by_day")
    return None if rows is None else format_pairs([(r["day"], _int(r["n_sales"])) for r in rows])


@reducer("external_sales_30d")
def external_sales_30d(world: WorldExports, params: dict[str, Any]) -> int:
    since = params.get("since") or window_since(world.now_iso)
    return sum(1 for r in external_sales(world) if r["settled_at"][:10] >= since)


@extractor("external_sales_30d")
def _x_external_sales_30d(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q18_external_sales_by_day")
    if rows is None:
        return None
    since = params.get("since") or ""
    return sum(_int(r["n_sales"]) for r in rows if str(r["day"]) >= since)


@reducer("all_sales_30d")
def all_sales_30d(world: WorldExports, params: dict[str, Any]) -> int:
    """All receipts rows (own-wallet included) inside the 30-day window; used by the calls_30d conflict."""
    since = params.get("since") or window_since(world.now_iso)
    return sum(1 for r in sales_rows(world) if r["settled_at"][:10] >= since)


@reducer("bazaar_listings_for_host")
def bazaar_listings_for_host(world: WorldExports, params: dict[str, Any]) -> int:
    return len(bazaar_search_rows(world, params.get("host")))


@extractor("bazaar_listings_for_host")
def _x_bazaar_listings_for_host(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q06_bazaar_host_listings")
    return None if rows is None else (_int(rows[0]["n_listings"]) if rows else 0)


@reducer("bazaar_payto_for_host")
def bazaar_payto_for_host(world: WorldExports, params: dict[str, Any]) -> int:
    return len({r["pay_to"] for r in bazaar_search_rows(world, params.get("host")) if r["pay_to"]})


@extractor("bazaar_payto_for_host")
def _x_bazaar_payto_for_host(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q06_bazaar_host_listings")
    return None if rows is None else (_int(rows[0]["n_pay_to"]) if rows else 0)


@reducer("bazaar_calls_30d_for_host")
def bazaar_calls_30d_for_host(world: WorldExports, params: dict[str, Any]) -> int:
    return sum(r["calls_30d"] for r in bazaar_search_rows(world, params.get("host")))


@extractor("bazaar_calls_30d_for_host")
def _x_bazaar_calls_30d_for_host(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q06_bazaar_host_listings")
    return None if rows is None else (_int(rows[0]["calls_30d_sum"]) if rows else 0)


@reducer("bazaar_payto_matches_seller")
def bazaar_payto_matches_seller(world: WorldExports, params: dict[str, Any]) -> int:
    sellers = {r["seller"] for r in sales_rows(world) if r["seller"]}
    pairs = {(r["resource"], r["pay_to"]) for r in bazaar_search_rows(world, params.get("host"))}
    return sum(1 for resource, pay_to in pairs if pay_to in sellers)


@extractor("bazaar_payto_matches_seller")
def _x_bazaar_payto_matches_seller(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q07_bazaar_payto_vs_receipts_seller")
    return None if rows is None else len(rows)


def _openapi_paid_ops(world: WorldExports) -> Optional[int]:
    payload = raw(world, "store_openapi")
    if not isinstance(payload, dict) or not isinstance(payload.get("paths"), dict):
        return None
    count = 0
    for ops in payload["paths"].values():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if isinstance(op, dict) and "x-payment-info" in op:
                count += 1
    return count


def vet402_routes_not_declared(world: WorldExports) -> Optional[dict[str, list[str]]]:
    """Paths vet402 lists for the host (catalog_status listed) that the store's openapi does not declare as paid
    operations, and the reverse; None when either export is missing. Feeds the endpoint_count_for_host explanation."""
    from urllib.parse import urlsplit
    vet = raw(world, "vet402_resolve")
    payload = raw(world, "store_openapi")
    if not (isinstance(vet, dict) and isinstance(vet.get("endpoints"), list)
            and isinstance(payload, dict) and isinstance(payload.get("paths"), dict)):
        return None
    declared = {path for path, ops in payload["paths"].items()
                if isinstance(ops, dict) and any(isinstance(op, dict) and "x-payment-info" in op for op in ops.values())}
    listed = {urlsplit(e["canonical_url"]).path for e in vet["endpoints"]
              if isinstance(e, dict) and e.get("catalog_status") == "listed" and isinstance(e.get("canonical_url"), str)}
    return {"only_vet402": sorted(listed - declared), "only_openapi": sorted(declared - listed)}


@reducer("endpoint_count_for_host")
def endpoint_count_for_host(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    """Per-registry endpoint counts for the host (keys present only for sources that were fetched)."""
    value: dict[str, Any] = {}
    if raw(world, "cdp_search_host") is not None:
        value["cdp_search"] = len(bazaar_search_rows(world, params.get("host")))
    openapi = _openapi_paid_ops(world)
    if openapi is not None:
        value["store_openapi"] = openapi
    index = raw(world, "agent402_index")
    if isinstance(index, dict) and index.get("paidToolCount") is not None:
        value["agent402_paid"] = _int(index.get("paidToolCount"))
        value["agent402_tools"] = _int(index.get("toolCount"))
    vet = raw(world, "vet402_resolve")
    if isinstance(vet, dict) and isinstance(vet.get("endpoints"), list):
        value["vet402"] = sum(1 for e in vet["endpoints"] if isinstance(e, dict) and e.get("catalog_status") == "listed")
    trust = raw(world, "trust_search")
    if isinstance(trust, dict) and trust.get("total") is not None:
        value["x402_trust_search"] = _int(trust.get("total"))
    return value


@extractor("endpoint_count_for_host")
def _x_endpoint_count_for_host(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    value: dict[str, Any] = {}
    rows = _rows(results, "q06_bazaar_host_listings")
    if rows is not None:
        value["cdp_search"] = _int(rows[0]["n_listings"]) if rows else 0
    rows = _rows(results, "q12_registry_count_openapi")
    if rows is None:
        return None
    value["store_openapi"] = _int(rows[0]["n_paid_ops"]) if rows else 0
    rows = _rows(results, "q13_registry_count_agent402")
    if rows is not None and rows:
        value["agent402_paid"] = _int(rows[0]["paid_tool_count"])
        value["agent402_tools"] = _int(rows[0]["tool_count"])
    rows = _rows(results, "q14_registry_count_vet402")
    if rows is not None:
        value["vet402"] = _int(rows[0]["n_listed"]) if rows else 0
    rows = _rows(results, "q15_registry_count_trust_search")
    if rows is not None and rows and rows[0].get("total_reported") is not None:
        value["x402_trust_search"] = _int(rows[0]["total_reported"])
    return value


def _verified_settlements(world: WorldExports, payer: Optional[str] = None) -> Optional[int]:
    settlements = settlement_rows(world)
    if settlements is None:
        return None                      # no settlements export: the Verifier did not run (never a zero)
    sales = {r["tx_hash"]: r for r in sales_rows(world) if r["tx_hash"]}
    count = 0
    for st in settlements:
        sale = sales.get(str(st.get("tx_hash") or "").lower())
        if sale is None or st.get("status") != "verified":
            continue
        if payer and str(st.get("payer") or "") != payer.lower():
            continue
        if (st.get("payer") == sale["payer"] and st.get("seller") == sale["seller"]
                and _int(st.get("amount_atomic")) == sale["amount_atomic"]):
            count += 1
    return count


@reducer("settlements_verified")
def settlements_verified(world: WorldExports, params: dict[str, Any]) -> Optional[int]:
    return _verified_settlements(world)


def _x_settlements(results: dict[str, SqlResult], payer: Optional[str]) -> Optional[int]:
    rows = _rows(results, "q16_settlements_vs_receipts")
    if rows is None:
        return None
    count = 0
    for r in rows:
        if payer and str(r.get("chain_payer") or "").lower() != payer.lower():
            continue
        if (r.get("status") == "verified" and _int(r["payer_match"]) == 1 and _int(r["seller_match"]) == 1
                and _int(r["amount_match"]) == 1):
            count += 1
    return count


@extractor("settlements_verified")
def _x_settlements_verified(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    return _x_settlements(results, None)


# ---------------------------------------------------------------------------------------------------------
# catalog_concentration
# ---------------------------------------------------------------------------------------------------------

@reducer("catalog_resources")
def catalog_resources(world: WorldExports, params: dict[str, Any]) -> int:
    return len(catalog_rows(world))


@extractor("catalog_resources")
def _x_catalog_resources(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q08_catalog_size")
    return None if rows is None else (_int(rows[0]["n_resources"]) if rows else 0)


@reducer("catalog_hosts")
def catalog_hosts(world: WorldExports, params: dict[str, Any]) -> int:
    return len({r["host"] for r in catalog_rows(world) if r["host"]})


@extractor("catalog_hosts")
def _x_catalog_hosts(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q08_catalog_size")
    return None if rows is None else (_int(rows[0]["n_hosts"]) if rows else 0)


@reducer("catalog_distinct_payto")
def catalog_distinct_payto(world: WorldExports, params: dict[str, Any]) -> int:
    return len({r["pay_to"] for r in catalog_rows(world) if r["pay_to"]})


@extractor("catalog_distinct_payto")
def _x_catalog_distinct_payto(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q08_catalog_size")
    return None if rows is None else (_int(rows[0]["n_pay_to"]) if rows else 0)


@reducer("catalog_calls_30d_sum")
def catalog_calls_30d_sum(world: WorldExports, params: dict[str, Any]) -> int:
    return sum(r["calls_30d"] for r in catalog_rows(world))


@extractor("catalog_calls_30d_sum")
def _x_catalog_calls_30d_sum(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q08_catalog_size")
    return None if rows is None else (_int(rows[0]["calls_30d_sum"]) if rows else 0)


@reducer("catalog_active_share")
def catalog_active_share(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    rows = catalog_rows(world)
    return share(sum(1 for r in rows if r["calls_30d"] > 0), len(rows))


@extractor("catalog_active_share")
def _x_catalog_active_share(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q10_catalog_active_share")
    if rows is None:
        return None
    return share(_int(rows[0]["n_active"]), _int(rows[0]["n_resources"])) if rows else share(0, 0)


def _hosts_by_calls(world: WorldExports) -> list[tuple[Any, int]]:
    sums: dict[Any, int] = {}
    for r in catalog_rows(world):
        sums[r["host"]] = sums.get(r["host"], 0) + r["calls_30d"]
    return sorted(sums.items(), key=lambda kv: (-kv[1], kv[0] is not None, kv[0] or ""))   # NULL host sorts first on ties


@reducer("top_host_calls_share")
def top_host_calls_share(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    ranked = _hosts_by_calls(world)
    total = sum(r["calls_30d"] for r in catalog_rows(world))
    value = share(ranked[0][1] if ranked else 0, total)
    value["host"] = ranked[0][0] if ranked else None
    return value


@extractor("top_host_calls_share")
def _x_top_host_calls_share(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q09_catalog_top_hosts_by_calls")
    if rows is None:
        return None
    if not rows:
        value = share(0, 0)
        value["host"] = None
        return value
    value = share(_int(rows[0]["calls_30d_sum"]), _int(rows[0]["catalog_calls_30d_sum"]))
    value["host"] = rows[0]["host"]
    return value


@reducer("top10_hosts_calls_share")
def top10_hosts_calls_share(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    ranked = _hosts_by_calls(world)[:10]
    total = sum(r["calls_30d"] for r in catalog_rows(world))
    return share(sum(n for _, n in ranked), total)


@extractor("top10_hosts_calls_share")
def _x_top10_hosts_calls_share(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q09_catalog_top_hosts_by_calls")
    if rows is None:
        return None
    if not rows:
        return share(0, 0)
    return share(sum(_int(r["calls_30d_sum"]) for r in rows[:10]), _int(rows[0]["catalog_calls_30d_sum"]))


@reducer("top_hosts_by_listings")
def top_hosts_by_listings(world: WorldExports, params: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for r in catalog_rows(world):
        if r["host"]:
            counts[r["host"]] = counts.get(r["host"], 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
    return format_pairs(ranked)


@extractor("top_hosts_by_listings")
def _x_top_hosts_by_listings(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[str]:
    rows = _rows(results, "q21_catalog_top_hosts_by_listings")
    if rows is None:
        return None
    return format_pairs([(r["host"], _int(r["n_resources"])) for r in rows])


@reducer("top_payto_calls_share")
def top_payto_calls_share(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    sums: dict[Any, int] = {}
    for r in catalog_rows(world):
        sums[r["pay_to"]] = sums.get(r["pay_to"], 0) + r["calls_30d"]
    ranked = sorted(sums.items(), key=lambda kv: (-kv[1], str(kv[0] or "")))
    total = sum(r["calls_30d"] for r in catalog_rows(world))
    value = share(ranked[0][1] if ranked else 0, total)
    value["pay_to"] = ranked[0][0] if ranked else None
    return value


@extractor("top_payto_calls_share")
def _x_top_payto_calls_share(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q11_catalog_payto_concentration")
    if rows is None:
        return None
    if not rows:
        value = share(0, 0)
        value["pay_to"] = None
        return value
    value = share(_int(rows[0]["calls_30d_sum"]), _int(rows[0]["catalog_calls_30d_sum"]))
    value["pay_to"] = (str(rows[0]["pay_to"]).lower() if rows[0].get("pay_to") is not None else None)
    return value


@reducer("unique_payers_per_endpoint_max")
def unique_payers_per_endpoint_max(world: WorldExports, params: dict[str, Any]) -> int:
    return max((r["payers_30d"] for r in catalog_rows(world)), default=0)


@extractor("unique_payers_per_endpoint_max")
def _x_unique_payers_per_endpoint_max(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q20_catalog_unique_payers_max")
    return None if rows is None else (_int(rows[0]["max_payers_30d"]) if rows else 0)


@reducer("trust_ecosystem_stats")
def trust_ecosystem_stats(world: WorldExports, params: dict[str, Any]) -> dict[str, Any]:
    stats = raw(world, "trust_stats")
    if not isinstance(stats, dict):
        return {}
    return {"endpoints_listed": _int(stats.get("endpointsListed")), "endpoints_active": _int(stats.get("endpointsActive")),
            "settlements_30d": _int(stats.get("settlements30d")), "distinct_payers_30d": _int(stats.get("distinctPayers30d"))}


@extractor("trust_ecosystem_stats")
def _x_trust_ecosystem_stats(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[dict[str, Any]]:
    rows = _rows(results, "q19_trust_ecosystem_stats")
    if rows is None:
        return None
    if not rows:
        return {}
    r = rows[0]
    return {"endpoints_listed": _int(r["endpoints_listed"]), "endpoints_active": _int(r["endpoints_active"]),
            "settlements_30d": _int(r["settlements_30d"]), "distinct_payers_30d": _int(r["distinct_payers_30d"])}


# ---------------------------------------------------------------------------------------------------------
# payer_dossier
# ---------------------------------------------------------------------------------------------------------

def _payer_sales(world: WorldExports, params: dict[str, Any]) -> list[dict[str, Any]]:
    wallet = str(params.get("wallet") or "").lower()
    return [r for r in sales_rows(world) if r["payer"] == wallet]


@reducer("payer_sales_total")
def payer_sales_total(world: WorldExports, params: dict[str, Any]) -> int:
    return len(_payer_sales(world, params))


@extractor("payer_sales_total")
def _x_payer_sales_total(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q17_payer_dossier")
    return None if rows is None else (_int(rows[0]["n_sales"]) if rows else 0)


@reducer("payer_usdc_atomic")
def payer_usdc_atomic(world: WorldExports, params: dict[str, Any]) -> int:
    return sum(r["amount_atomic"] for r in _payer_sales(world, params))


@extractor("payer_usdc_atomic")
def _x_payer_usdc_atomic(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q17_payer_dossier")
    return None if rows is None else (_int(rows[0]["usdc_atomic"]) if rows else 0)


@reducer("payer_window")
def payer_window(world: WorldExports, params: dict[str, Any]) -> Optional[str]:
    ats = sorted(r["settled_at"] for r in _payer_sales(world, params))
    return f"{ats[0]}..{ats[-1]}" if ats else None


@extractor("payer_window")
def _x_payer_window(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[str]:
    rows = _rows(results, "q17_payer_dossier")
    if rows is None:
        return None
    if not rows or rows[0].get("first_at") is None:
        return None
    return f"{rows[0]['first_at']}..{rows[0]['last_at']}"


@reducer("payer_services")
def payer_services(world: WorldExports, params: dict[str, Any]) -> int:
    return len({r["service"] for r in _payer_sales(world, params) if r["service"] is not None})


@extractor("payer_services")
def _x_payer_services(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    rows = _rows(results, "q17_payer_dossier")
    return None if rows is None else (_int(rows[0]["n_services"]) if rows else 0)


@reducer("verified_tx")
def verified_tx(world: WorldExports, params: dict[str, Any]) -> Optional[int]:
    return _verified_settlements(world, payer=str(params.get("wallet") or ""))


@extractor("verified_tx")
def _x_verified_tx(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    return _x_settlements(results, str(params.get("wallet") or "") or None)


@reducer("usdc_balance_atomic")
def usdc_balance_atomic(world: WorldExports, params: dict[str, Any]) -> Optional[int]:
    wallet = str(params.get("wallet") or "").lower()
    value = chain_fact_value(world, "usdc_balance_atomic", f"wallet:base:{wallet}" if wallet else None)
    return None if value is None else _int(value)


@reducer("tx_count")
def tx_count(world: WorldExports, params: dict[str, Any]) -> Optional[int]:
    wallet = str(params.get("wallet") or "").lower()
    value = chain_fact_value(world, "tx_count", f"wallet:base:{wallet}" if wallet else None)
    return None if value is None else _int(value)


def _x_chain_fact(results: dict[str, SqlResult], predicate: str) -> Optional[int]:
    rows = _rows(results, "q22_payer_chain_facts")
    if rows is None:
        return None
    for r in rows:
        if r.get("predicate") == predicate and r.get("value_atomic") is not None:
            return _int(r["value_atomic"])
    return None


@extractor("usdc_balance_atomic")
def _x_usdc_balance_atomic(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    return _x_chain_fact(results, "usdc_balance_atomic")


@extractor("tx_count")
def _x_tx_count(results: dict[str, SqlResult], params: dict[str, Any]) -> Optional[int]:
    return _x_chain_fact(results, "tx_count")


def get_reducer(name: str) -> Reducer:
    """Look up "reducers.<name>" (the string stored in MetricSpec.reducer)."""
    return REDUCERS[name if name.startswith("reducers.") else f"reducers.{name}"]


def get_extractor(metric_id: str) -> Optional[Extractor]:
    return SQL_EXTRACTORS.get(metric_id)
