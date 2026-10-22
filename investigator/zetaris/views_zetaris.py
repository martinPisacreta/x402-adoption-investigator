"""SchemaStore view DDL (LATERAL VIEW explode) producing the same names and columns as views_sqlite (SPEC.md 10.3). Owner: B3.

This is the ONLY place Spark/JSON syntax appears. Analytics queries never parse JSON or URLs: the `host`
column is produced here with parse_url(resource, 'HOST'). Views are one row per listing (primary accept,
`accepts[0]`), wallets lowercased, counters CAST AS BIGINT, booleans as 0/1.

The DDL is shape-aware: Spark infers the struct schema from the JSON actually served, so a field that no row
carries does not exist and a reference to it fails the whole view. shape_of() scans the export once and
view_select() emits CAST(NULL AS <type>) for absent fields instead. Verified live on Zetaris 2.4.3.1
(2026-10-22): the full catalog snapshot export (32 MB) registers as a REST table in ~3 s, caches in ~4 s and
answers the catalog queries in under a second; a top-level JSON array registers as one row per element.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from .views_sqlite import PAGES, VIEWS, VIEW_NAMES, ViewSpec

__all__ = ["VIEW_NAMES", "view_select", "view_tables", "shape_of", "Shape"]

Shape = dict[str, Any]     # {"top": "object"|"array"|"scalar", "keys": set, "nested": {field: set(keys)}}


# ----------------------------------------------------------------------------------------------------------
# Shape scan
# ----------------------------------------------------------------------------------------------------------

def _keys_union(rows: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            out.update(row.keys())
    return out


def shape_of(payload: Any) -> Shape:
    """Field inventory of an export: top-level keys, keys of every list-of-objects field, and for listing
    arrays (items / resources / rows) the keys of accepts[0] and quality. Used to emit NULL columns for
    fields Spark's inferred schema will not have."""
    shape: Shape = {"top": "scalar", "keys": set(), "nested": {}}
    if isinstance(payload, list):
        shape["top"] = "array"
        shape["keys"] = _keys_union(payload)
        return shape
    if not isinstance(payload, dict):
        return shape
    shape["top"] = "object"
    shape["keys"] = set(payload.keys())
    for key, value in payload.items():
        if isinstance(value, list):
            shape["nested"][key] = _keys_union(value)
            if key in ("items", "resources"):
                accepts: set[str] = set()
                quality: set[str] = set()
                for item in value:
                    if not isinstance(item, dict):
                        continue
                    acc = item.get("accepts")
                    if isinstance(acc, list) and acc and isinstance(acc[0], dict):
                        accepts.update(acc[0].keys())
                    q = item.get("quality")
                    if isinstance(q, dict):
                        quality.update(q.keys())
                shape["nested"][key + ".accepts"] = accepts
                shape["nested"][key + ".quality"] = quality
        elif isinstance(value, dict):
            shape["nested"][key] = set(value.keys())
    return shape


def _has(shape: Optional[Shape], path: str, key: str) -> bool:
    """True when the field is present (or when no shape is known, in which case the DDL is emitted in full)."""
    if shape is None:
        return True
    if path == "":
        return key in shape.get("keys", set())
    return key in shape.get("nested", {}).get(path, set())


def _col(expr: str, alias: str, present: bool, sql_type: str = "STRING") -> str:
    return f"{expr} AS {alias}" if present else f"CAST(NULL AS {sql_type}) AS {alias}"


def _flag(expr: str) -> str:
    return f"CASE WHEN {expr} THEN 1 ELSE 0 END"


def _digits_bigint(expr: str) -> str:
    return f"CASE WHEN {expr} RLIKE '^[0-9]+$' THEN CAST({expr} AS BIGINT) ELSE NULL END"


# ----------------------------------------------------------------------------------------------------------
# Tables a view reads
# ----------------------------------------------------------------------------------------------------------

def view_tables(view: str, pages: Iterable[int] = ()) -> list[str]:
    """REST table names (without the database prefix) a view reads from."""
    spec = VIEWS[view]
    if PAGES in spec.exports:
        return [spec.table.format(n=n) for n in pages]
    return [spec.table]


# ----------------------------------------------------------------------------------------------------------
# SELECT bodies
# ----------------------------------------------------------------------------------------------------------

def _listing_columns(alias: str, array_key: str, shape: Optional[Shape], with_service: bool) -> list[str]:
    """Columns shared by bazaar_resources, bazaar_search and catalog_resources (listing = item + accepts[0])."""
    a = f"{alias}.accepts[0]"
    acc = array_key + ".accepts"
    qual = array_key + ".quality"
    has_quality = _has(shape, array_key, "quality")
    amount_expr = f"{a}.amount"
    if _has(shape, acc, "maxAmountRequired") and _has(shape, acc, "amount"):
        amount_expr = f"COALESCE({a}.amount, {a}.`maxAmountRequired`)"
    elif _has(shape, acc, "maxAmountRequired"):
        amount_expr = f"{a}.`maxAmountRequired`"
    cols = [
        _col(f"{alias}.resource", "resource", _has(shape, array_key, "resource")),
        _col(f"lower(parse_url({alias}.resource, 'HOST'))", "host", _has(shape, array_key, "resource")),
    ]
    if with_service:
        cols += [
            _col(f"{alias}.`serviceName`", "service_name", _has(shape, array_key, "serviceName")),
            _col(f"{alias}.`type`", "resource_type", _has(shape, array_key, "type")),
            _col(f"{alias}.`lastUpdated`", "last_updated", _has(shape, array_key, "lastUpdated")),
        ]
    cols += [
        _col(f"CAST({alias}.quality.`l30DaysTotalCalls` AS BIGINT)", "calls_30d",
             has_quality and _has(shape, qual, "l30DaysTotalCalls"), "BIGINT"),
        _col(f"CAST({alias}.quality.`l30DaysUniquePayers` AS BIGINT)", "payers_30d",
             has_quality and _has(shape, qual, "l30DaysUniquePayers"), "BIGINT"),
        _col(f"{alias}.quality.`lastCalledAt`", "last_called_at", has_quality and _has(shape, qual, "lastCalledAt")),
        _col(f"lower({a}.`payTo`)", "pay_to", _has(shape, acc, "payTo")),
        _col(f"{a}.network", "network", _has(shape, acc, "network")),
        _col(f"{a}.asset", "asset", _has(shape, acc, "asset")),
        _col(_digits_bigint(amount_expr), "amount_atomic",
             _has(shape, acc, "amount") or _has(shape, acc, "maxAmountRequired"), "BIGINT"),
    ]
    if with_service:
        cols.append(_col(f"{a}.scheme", "scheme", _has(shape, acc, "scheme")))
    return cols


def _rows_source(table: str, shape: Optional[Shape], wrapper_keys: tuple[str, ...]) -> tuple[str, str, str]:
    """(row alias prefix, FROM clause, nested path) for exports that are a top-level array or {rows: [...]}."""
    if shape is None or shape.get("top") == "array":
        return "", table, ""
    for key in wrapper_keys:
        if key in shape.get("nested", {}):
            return "r.", f"{table} LATERAL VIEW explode({key}) AS r", key
    return "", table, ""


def view_select(view: str, run_id: str, tables: dict[str, str], shapes: Optional[dict[str, Shape]] = None) -> str:
    """The SELECT ... FROM X402_<RUN8>.<table> LATERAL VIEW explode(...) body for ddl.create_view().

    `tables` maps logical table names (ViewSpec.table, pages as bazaar_page<N>) to their qualified Zetaris
    names; `shapes` maps the same logical names to shape_of(export) results."""
    spec: ViewSpec = VIEWS[view]
    shapes = shapes or {}

    if view == "sales":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        s = "sales"
        cols = [
            _col("lower(seller)", "seller", _has(sh, "", "seller")),
            _col("CAST(`agentId` AS STRING)", "agent_id", _has(sh, "", "agentId")),
            _col("s.`at`", "settled_at", _has(sh, s, "at")),
            _col("lower(s.payer)", "payer", _has(sh, s, "payer")),
            _col(_flag("s.`ownWallet`"), "own_wallet", _has(sh, s, "ownWallet"), "INT"),
            _col("CAST(s.`amountAtomic` AS BIGINT)", "amount_atomic", _has(sh, s, "amountAtomic"), "BIGINT"),
            _col("s.`transaction`", "tx_hash", _has(sh, s, "transaction")),
            _col("CAST(s.`x402Version` AS STRING)", "x402_version", _has(sh, s, "x402Version")),
            _col("s.service", "service", _has(sh, s, "service")),
            _col("s.resource", "resource", _has(sh, s, "resource")),
            _col("s.facilitator", "facilitator", _has(sh, s, "facilitator")),
        ]
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(sales) AS s"

    if view == "purchases":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        p = "purchases"
        cols = [
            _col("p.`at`", "`at`", _has(sh, p, "at")),
            _col("p.url", "url", _has(sh, p, "url")),
            _col("CAST(p.`priceUsd` AS DOUBLE)", "price_usd", _has(sh, p, "priceUsd"), "DOUBLE"),
            _col("p.outcome", "outcome", _has(sh, p, "outcome")),
            _col("p.`transaction`", "tx_hash", _has(sh, p, "transaction")),
        ]
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(purchases) AS p"

    if view == "bazaar_resources":
        branches: list[str] = []
        for logical, qualified in sorted(tables.items(), key=lambda kv: int(kv[0].rsplit("page", 1)[-1] or 0)):
            if not logical.startswith("bazaar_page"):
                continue
            page_no = int(logical.rsplit("page", 1)[-1])
            cols = _listing_columns("i", "items", shapes.get(logical), with_service=True) + [f"{page_no} AS page"]
            branches.append(f"SELECT {', '.join(cols)} FROM {qualified} LATERAL VIEW explode(items) AS i")
        if not branches:
            raise ValueError("bazaar_resources needs at least one bazaar_page<N> table")
        return " UNION ALL ".join(branches)

    if view == "bazaar_search":
        t = tables[spec.table]
        cols = _listing_columns("r", "resources", shapes.get(spec.table), with_service=True)
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(resources) AS r"

    if view == "catalog_resources":
        t = tables[spec.table]
        cols = _listing_columns("i", "items", shapes.get(spec.table), with_service=False)
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(items) AS i"

    if view == "openapi_paid_ops":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        prefix, source, path = _rows_source(t, sh, ("rows", "operations", "paid_ops"))
        keyset = (sh or {}).get("keys", set()) if not path else (sh or {}).get("nested", {}).get(path, set())
        known = sh is not None

        def pick(snake: str, camel: str) -> tuple[str, bool]:
            if not known or snake in keyset:
                return f"{prefix}{snake}", True
            if camel in keyset:
                return f"{prefix}`{camel}`", True
            return "", False

        p_path = pick("path", "path")
        p_op = pick("operation_id", "operationId")
        p_price = pick("price_usd", "priceUsd")
        p_net = pick("network", "network")
        p_asset = pick("asset", "asset")
        p_pay = pick("pay_to", "payTo")
        cols = [
            _col(p_path[0], "path", p_path[1]),
            _col(p_op[0], "operation_id", p_op[1]),
            _col(f"CAST({p_price[0]} AS DOUBLE)", "price_usd", p_price[1], "DOUBLE"),
            _col(p_net[0], "network", p_net[1]),
            _col(p_asset[0], "asset", p_asset[1]),
            _col(f"lower({p_pay[0]})", "pay_to", p_pay[1]),
        ]
        return f"SELECT {', '.join(cols)} FROM {source}"

    if view == "agent402_tools":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        k = "tools"
        cols = [
            _col("t.route", "route", _has(sh, k, "route")),
            _col("t.slug", "slug", _has(sh, k, "slug")),
            _col(_flag("t.paid"), "paid", _has(sh, k, "paid"), "INT"),
            _col("CAST(t.price AS DOUBLE)", "price", _has(sh, k, "price"), "DOUBLE"),
            _col(_flag("t.declared"), "declared", _has(sh, k, "declared"), "INT"),
            _col("t.`lastVerifiedAt`", "last_verified_at", _has(sh, k, "lastVerifiedAt")),
        ]
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(tools) AS t"

    if view == "agent402_seller":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        cols = [
            _col("origin", "origin", _has(sh, "", "origin")),
            _col("CAST(`toolCount` AS BIGINT)", "tool_count", _has(sh, "", "toolCount"), "BIGINT"),
            _col("CAST(`paidToolCount` AS BIGINT)", "paid_tool_count", _has(sh, "", "paidToolCount"), "BIGINT"),
            _col(_flag("paywall.ok"), "paywall_ok", _has(sh, "", "paywall") and _has(sh, "paywall", "ok"), "INT"),
            _col("CAST(health AS DOUBLE)", "health", _has(sh, "", "health"), "DOUBLE"),
            _col(_flag("routable"), "routable", _has(sh, "", "routable"), "INT"),
            _col("`routerDispatchReason`", "router_dispatch_reason", _has(sh, "", "routerDispatchReason")),
        ]
        return f"SELECT {', '.join(cols)} FROM {t}"

    if view == "vet402_endpoints":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        k = "endpoints"
        cols = [
            _col("e.canonical_url", "canonical_url", _has(sh, k, "canonical_url")),
            _col("e.method", "method", _has(sh, k, "method")),
            _col("e.payee_id", "payee_id", _has(sh, k, "payee_id")),
            _col("e.catalog_status", "catalog_status", _has(sh, k, "catalog_status")),
            _col("e.first_seen", "first_seen", _has(sh, k, "first_seen")),
            _col("e.last_seen", "last_seen", _has(sh, k, "last_seen")),
        ]
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(endpoints) AS e"

    if view == "trust_search":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        k = "results"
        cols = [
            _col("r.resource", "resource", _has(sh, k, "resource")),
            _col("lower(r.host)", "host", _has(sh, k, "host")),
            _col(_flag("r.delisted"), "delisted", _has(sh, k, "delisted"), "INT"),
            _col("CAST(r.score AS BIGINT)", "score", _has(sh, k, "score"), "BIGINT"),
            _col("r.grade", "grade", _has(sh, k, "grade")),
            _col(_flag("r.`providerVerified`"), "provider_verified", _has(sh, k, "providerVerified"), "INT"),
            _col("CAST(total AS BIGINT)", "total_reported", _has(sh, "", "total"), "BIGINT"),
        ]
        return f"SELECT {', '.join(cols)} FROM {t} LATERAL VIEW explode(results) AS r"

    if view == "trust_stats":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        cols = [
            _col("CAST(`endpointsListed` AS BIGINT)", "endpoints_listed", _has(sh, "", "endpointsListed"), "BIGINT"),
            _col("CAST(`endpointsActive` AS BIGINT)", "endpoints_active", _has(sh, "", "endpointsActive"), "BIGINT"),
            _col("CAST(`settlements30d` AS BIGINT)", "settlements_30d", _has(sh, "", "settlements30d"), "BIGINT"),
            _col("CAST(`settledVolumeUsd30d` AS DOUBLE)", "settled_volume_usd_30d", _has(sh, "", "settledVolumeUsd30d"), "DOUBLE"),
            _col("CAST(`distinctPayers30d` AS BIGINT)", "distinct_payers_30d", _has(sh, "", "distinctPayers30d"), "BIGINT"),
            _col("CAST(`distinctReceivers30d` AS BIGINT)", "distinct_receivers_30d", _has(sh, "", "distinctReceivers30d"), "BIGINT"),
        ]
        return f"SELECT {', '.join(cols)} FROM {t}"

    if view == "settlements":
        t = tables[spec.table]
        sh = shapes.get(spec.table)
        prefix, source, path = _rows_source(t, sh, ("rows", "settlements", "verifications"))
        keyset = (sh or {}).get("keys", set()) if not path else (sh or {}).get("nested", {}).get(path, set())
        known = sh is not None

        def has(key: str) -> bool:
            return (not known) or key in keyset

        tx_expr, tx_ok = (f"{prefix}tx_hash", True) if has("tx_hash") else ((f"{prefix}tx", True) if has("tx") else ("", False))
        blk_expr, blk_ok = (f"{prefix}block_number", True) if has("block_number") else ((f"{prefix}block", True) if has("block") else ("", False))
        cols = [
            _col(f"lower({tx_expr})", "tx_hash", tx_ok),
            _col(f"lower({prefix}payer)", "payer", has("payer")),
            _col(f"lower({prefix}seller)", "seller", has("seller")),
            _col(f"CAST({prefix}amount_atomic AS BIGINT)", "amount_atomic", has("amount_atomic"), "BIGINT"),
            _col(f"CAST({blk_expr} AS BIGINT)", "block_number", blk_ok, "BIGINT"),
            _col(f"{prefix}block_time", "block_time", has("block_time")),
            _col(f"lower({prefix}facilitator_from)", "facilitator_from", has("facilitator_from")),
            _col(f"{prefix}status", "status", has("status")),
        ]
        return f"SELECT {', '.join(cols)} FROM {source}"

    raise KeyError(f"unknown view {view!r}")
