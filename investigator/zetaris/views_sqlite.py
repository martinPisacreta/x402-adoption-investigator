"""Python-built flat tables with the SAME names and columns as the SchemaStore views (SPEC.md 10.3). Owner: B3.

Each builder reads one export (logs/<run_id>/exports/<name>.json), flattens it in Python with the same
semantics Spark applies in views_zetaris.py (host extraction, lowercased wallets, BIGINT casts, booleans as
0/1, one row per listing using the primary accept) and loads a SQLite table named exactly like the view.
sql/views/VIEWS.md is the source of truth for names and columns; VIEWS below is the machine-readable copy that
both backends share (views_zetaris imports it).

Parity rules (verified against Zetaris 2.4.3.1 on the fixtures, see tests/test_sql_dual_backend.py):
- host: Spark parse_url(resource, 'HOST') is java.net.URI; it returns NULL for URLs that URI rejects (spaces,
  braces, pipes, backslashes, quotes, control characters, bad percent-escapes, unbalanced brackets). host_of()
  applies the same rejection before urllib.parse, then lowercases.
- amount_atomic: cast only all-digit strings (COALESCE(amount, maxAmountRequired)); anything else is NULL.
- quality counters: JSON integers; null stays NULL.
- booleans: CASE WHEN x THEN 1 ELSE 0 END, so NULL and false both give 0.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

PAGE_KEY_RE = re.compile(r"^cdp_page_?(\d+)$")
DIGITS_RE = re.compile(r"^[0-9]+$")
INT_RE = re.compile(r"^-?[0-9]+$")
DECIMAL_RE = re.compile(r"^-?[0-9]+\.[0-9]*$")
BIGINT_MAX = 2**63 - 1
BIGINT_MIN = -(2**63)
# Characters java.net.URI rejects anywhere in a URL string (Spark's parse_url returns NULL for them).
_URI_ILLEGAL = re.compile(r'[\x00-\x20\x7f"<>\\^`{|}]')
_PCT_BAD = re.compile(r"%(?![0-9A-Fa-f]{2})")


@dataclass(frozen=True)
class ViewSpec:
    name: str
    family: str                      # source family (SPEC.md 7.1)
    columns: tuple[tuple[str, str], ...]   # (column, sqlite type)
    exports: tuple[str, ...]         # candidate export names (observation names); PAGES = cdp_page_<N> keys
    table: str                       # Zetaris REST table name (bazaar_page{n} for pages)
    builder: str                     # rows_* function name in this module

    @property
    def column_names(self) -> list[str]:
        return [c for c, _ in self.columns]


PAGES = "<pages>"

VIEWS: dict[str, ViewSpec] = {spec.name: spec for spec in [
    ViewSpec("sales", "receipts",
             (("seller", "TEXT"), ("agent_id", "TEXT"), ("settled_at", "TEXT"), ("payer", "TEXT"),
              ("own_wallet", "INTEGER"), ("amount_atomic", "INTEGER"), ("tx_hash", "TEXT"), ("x402_version", "TEXT"),
              ("service", "TEXT"), ("resource", "TEXT"), ("facilitator", "TEXT")),
             ("store_receipts", "receipts"), "receipts_raw", "rows_sales"),
    ViewSpec("purchases", "receipts",
             (("at", "TEXT"), ("url", "TEXT"), ("price_usd", "REAL"), ("outcome", "TEXT"), ("tx_hash", "TEXT")),
             ("store_receipts", "receipts"), "receipts_raw", "rows_purchases"),
    ViewSpec("bazaar_resources", "cdp_bazaar",
             (("resource", "TEXT"), ("host", "TEXT"), ("service_name", "TEXT"), ("resource_type", "TEXT"),
              ("last_updated", "TEXT"), ("calls_30d", "INTEGER"), ("payers_30d", "INTEGER"), ("last_called_at", "TEXT"),
              ("pay_to", "TEXT"), ("network", "TEXT"), ("asset", "TEXT"), ("amount_atomic", "INTEGER"), ("scheme", "TEXT"),
              ("page", "INTEGER")),
             (PAGES,), "bazaar_page{n}", "rows_bazaar"),
    ViewSpec("bazaar_search", "cdp_search",
             (("resource", "TEXT"), ("host", "TEXT"), ("service_name", "TEXT"), ("resource_type", "TEXT"),
              ("last_updated", "TEXT"), ("calls_30d", "INTEGER"), ("payers_30d", "INTEGER"), ("last_called_at", "TEXT"),
              ("pay_to", "TEXT"), ("network", "TEXT"), ("asset", "TEXT"), ("amount_atomic", "INTEGER"), ("scheme", "TEXT")),
             ("cdp_search_host", "cdp_search"), "bazaar_search_host", "rows_bazaar_search"),
    ViewSpec("catalog_resources", "cdp_catalog_snapshot",
             (("resource", "TEXT"), ("host", "TEXT"), ("calls_30d", "INTEGER"), ("payers_30d", "INTEGER"),
              ("last_called_at", "TEXT"), ("pay_to", "TEXT"), ("network", "TEXT"), ("asset", "TEXT"),
              ("amount_atomic", "INTEGER")),
             ("catalog_snapshot", "cdp_catalog_snapshot", "cdp_catalog"), "catalog_snapshot", "rows_catalog"),
    ViewSpec("openapi_paid_ops", "store_openapi",
             (("path", "TEXT"), ("operation_id", "TEXT"), ("price_usd", "REAL"), ("network", "TEXT"), ("asset", "TEXT"),
              ("pay_to", "TEXT")),
             ("openapi_paid_ops",), "openapi_paid_ops_raw", "rows_openapi_paid_ops"),
    ViewSpec("agent402_tools", "agent402",
             (("route", "TEXT"), ("slug", "TEXT"), ("paid", "INTEGER"), ("price", "REAL"), ("declared", "INTEGER"),
              ("last_verified_at", "TEXT")),
             ("agent402_index",), "agent402_index", "rows_agent402_tools"),
    ViewSpec("agent402_seller", "agent402",
             (("origin", "TEXT"), ("tool_count", "INTEGER"), ("paid_tool_count", "INTEGER"), ("paywall_ok", "INTEGER"),
              ("health", "REAL"), ("routable", "INTEGER"), ("router_dispatch_reason", "TEXT")),
             ("agent402_index",), "agent402_index", "rows_agent402_seller"),
    ViewSpec("vet402_endpoints", "vet402",
             (("canonical_url", "TEXT"), ("method", "TEXT"), ("payee_id", "TEXT"), ("catalog_status", "TEXT"),
              ("first_seen", "TEXT"), ("last_seen", "TEXT")),
             ("vet402_resolve",), "vet402_resolve", "rows_vet402_endpoints"),
    ViewSpec("trust_search", "x402_trust",
             (("resource", "TEXT"), ("host", "TEXT"), ("delisted", "INTEGER"), ("score", "INTEGER"), ("grade", "TEXT"),
              ("provider_verified", "INTEGER"), ("total_reported", "INTEGER")),
             ("trust_search",), "trust_search_raw", "rows_trust_search"),
    ViewSpec("trust_stats", "x402_trust",
             (("endpoints_listed", "INTEGER"), ("endpoints_active", "INTEGER"), ("settlements_30d", "INTEGER"),
              ("settled_volume_usd_30d", "REAL"), ("distinct_payers_30d", "INTEGER"), ("distinct_receivers_30d", "INTEGER")),
             ("trust_stats",), "trust_stats_raw", "rows_trust_stats"),
    ViewSpec("settlements", "base_rpc",
             (("tx_hash", "TEXT"), ("payer", "TEXT"), ("seller", "TEXT"), ("amount_atomic", "INTEGER"),
              ("block_number", "INTEGER"), ("block_time", "TEXT"), ("facilitator_from", "TEXT"), ("status", "TEXT")),
             ("settlements",), "settlements_raw", "rows_settlements"),
]}

VIEW_NAMES: tuple[str, ...] = tuple(VIEWS.keys())


# ----------------------------------------------------------------------------------------------------------
# Scalar helpers (Spark parity)
# ----------------------------------------------------------------------------------------------------------

def host_of(url: Any) -> Optional[str]:
    """Lowercase host of a resource URL with the semantics of Spark's parse_url(resource, 'HOST')
    (java.net.URI): NULL when URI would reject the string, else the host without port or userinfo."""
    if not isinstance(url, str) or not url:
        return None
    if _URI_ILLEGAL.search(url) or _PCT_BAD.search(url):
        return None
    if url.count("#") > 1:          # a second '#' is illegal in a URI (verified: 56 of 57 NULL hosts on Zetaris)
        return None
    if ("[" in url or "]" in url) and not url.split("://", 1)[-1].startswith("["):
        return None                 # brackets are legal only in an IPv6 host literal
    if url.count("[") != url.count("]"):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    try:
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    return host.lower()


def lower(value: Any) -> Optional[str]:
    return value.lower() if isinstance(value, str) else (None if value is None else str(value).lower())


def text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if isinstance(value, str) else str(value)


def _in_bigint_range(n: int) -> Optional[int]:
    """Spark CAST to BIGINT returns NULL on overflow (non-ANSI mode); SQLite cannot store it either."""
    return n if BIGINT_MIN <= n <= BIGINT_MAX else None


def bigint(value: Any) -> Optional[int]:
    """CAST(x AS BIGINT) over JSON scalars: ints pass, floats truncate, integer strings parse, else NULL."""
    if value is None or isinstance(value, bool):
        return (1 if value else 0) if isinstance(value, bool) else None
    if isinstance(value, int):
        return _in_bigint_range(value)
    if isinstance(value, float):
        return _in_bigint_range(int(value))
    if isinstance(value, str):
        s = value.strip()
        if INT_RE.match(s):
            return _in_bigint_range(int(s))
        if DECIMAL_RE.match(s):     # Spark 3 truncates the fraction when casting '5.7' to BIGINT
            return _in_bigint_range(int(float(s)))
        return None
    return None


def digits_bigint(value: Any) -> Optional[int]:
    """CASE WHEN x RLIKE '^[0-9]+$' THEN CAST(x AS BIGINT) END: only all-digit strings (or ints) become ints;
    values beyond the BIGINT range are NULL (one catalog amount overflows; Zetaris returns NULL for it too)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return _in_bigint_range(value) if value >= 0 else None
    s = str(value).strip() if not isinstance(value, str) else value
    return _in_bigint_range(int(s)) if DIGITS_RE.match(s) else None


def double(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def flag(value: Any) -> int:
    """CASE WHEN x THEN 1 ELSE 0 END (NULL -> 0)."""
    return 1 if value is True else 0


def primary_accept(item: dict[str, Any]) -> dict[str, Any]:
    accepts = item.get("accepts")
    if isinstance(accepts, list) and accepts and isinstance(accepts[0], dict):
        return accepts[0]
    return {}


def amount_of(accept: dict[str, Any]) -> Optional[int]:
    value = accept.get("amount")
    if value is None:
        value = accept.get("maxAmountRequired")
    return digits_bigint(value)


def rows_of(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """A top-level JSON array, or an object wrapping the array under one of `keys` (rows, settlements, ...)."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return [r for r in payload[key] if isinstance(r, dict)]
    return []


# ----------------------------------------------------------------------------------------------------------
# Row builders (one per view)
# ----------------------------------------------------------------------------------------------------------

def rows_sales(receipts: dict[str, Any]) -> list[tuple[Any, ...]]:
    seller = lower(receipts.get("seller"))
    agent_id = text(receipts.get("agentId"))
    rows: list[tuple[Any, ...]] = []
    for s in receipts.get("sales") or []:
        if not isinstance(s, dict):
            continue
        rows.append((seller, agent_id, text(s.get("at")), lower(s.get("payer")), flag(s.get("ownWallet")),
                     bigint(s.get("amountAtomic")), text(s.get("transaction")), text(s.get("x402Version")),
                     text(s.get("service")), text(s.get("resource")), text(s.get("facilitator"))))
    return rows


def rows_purchases(receipts: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for p in receipts.get("purchases") or []:
        if not isinstance(p, dict):
            continue
        rows.append((text(p.get("at")), text(p.get("url")), double(p.get("priceUsd")), text(p.get("outcome")),
                     text(p.get("transaction"))))
    return rows


def _listing_row(item: dict[str, Any]) -> tuple[Any, ...]:
    quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
    accept = primary_accept(item)
    return (text(item.get("resource")), host_of(item.get("resource")), text(item.get("serviceName")),
            text(item.get("type")), text(item.get("lastUpdated")), bigint(quality.get("l30DaysTotalCalls")),
            bigint(quality.get("l30DaysUniquePayers")), text(quality.get("lastCalledAt")),
            lower(accept.get("payTo")), text(accept.get("network")), text(accept.get("asset")),
            amount_of(accept), text(accept.get("scheme")))


def rows_bazaar(items: list[dict[str, Any]], page: int | None) -> list[tuple[Any, ...]]:
    """One row per listing (primary accept); `page` appended when not None."""
    rows: list[tuple[Any, ...]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        row = _listing_row(item)
        rows.append(row + (page,) if page is not None else row)
    return rows


def rows_bazaar_search(search: dict[str, Any]) -> list[tuple[Any, ...]]:
    return rows_bazaar(search.get("resources") or [], None)


def rows_catalog(snapshot: Any) -> list[tuple[Any, ...]]:
    items = snapshot.get("items") if isinstance(snapshot, dict) else snapshot
    rows: list[tuple[Any, ...]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        quality = item.get("quality") if isinstance(item.get("quality"), dict) else {}
        accept = primary_accept(item)
        rows.append((text(item.get("resource")), host_of(item.get("resource")),
                     bigint(quality.get("l30DaysTotalCalls")), bigint(quality.get("l30DaysUniquePayers")),
                     text(quality.get("lastCalledAt")), lower(accept.get("payTo")), text(accept.get("network")),
                     text(accept.get("asset")), amount_of(accept)))
    return rows


def rows_openapi_paid_ops(payload: Any) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for r in rows_of(payload, "rows", "operations", "paid_ops"):
        rows.append((text(r.get("path")), text(r.get("operation_id", r.get("operationId"))),
                     double(r.get("price_usd", r.get("priceUsd"))), text(r.get("network")), text(r.get("asset")),
                     lower(r.get("pay_to", r.get("payTo")))))
    return rows


def rows_agent402_tools(index: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for t in index.get("tools") or []:
        if not isinstance(t, dict):
            continue
        rows.append((text(t.get("route")), text(t.get("slug")), flag(t.get("paid")), double(t.get("price")),
                     flag(t.get("declared")), text(t.get("lastVerifiedAt"))))
    return rows


def rows_agent402_seller(index: dict[str, Any]) -> list[tuple[Any, ...]]:
    paywall = index.get("paywall") if isinstance(index.get("paywall"), dict) else {}
    return [(text(index.get("origin")), bigint(index.get("toolCount")), bigint(index.get("paidToolCount")),
             flag(paywall.get("ok")), double(index.get("health")), flag(index.get("routable")),
             text(index.get("routerDispatchReason")))]


def rows_vet402_endpoints(resolve: dict[str, Any]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for e in resolve.get("endpoints") or []:
        if not isinstance(e, dict):
            continue
        rows.append((text(e.get("canonical_url")), text(e.get("method")), text(e.get("payee_id")),
                     text(e.get("catalog_status")), text(e.get("first_seen")), text(e.get("last_seen"))))
    return rows


def rows_trust_search(search: dict[str, Any]) -> list[tuple[Any, ...]]:
    total = bigint(search.get("total"))
    rows: list[tuple[Any, ...]] = []
    for r in search.get("results") or []:
        if not isinstance(r, dict):
            continue
        rows.append((text(r.get("resource")), lower(r.get("host")), flag(r.get("delisted")), bigint(r.get("score")),
                     text(r.get("grade")), flag(r.get("providerVerified")), total))
    return rows


def rows_trust_stats(stats: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(bigint(stats.get("endpointsListed")), bigint(stats.get("endpointsActive")),
             bigint(stats.get("settlements30d")), double(stats.get("settledVolumeUsd30d")),
             bigint(stats.get("distinctPayers30d")), bigint(stats.get("distinctReceivers30d")))]


def rows_settlements(payload: Any) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for r in rows_of(payload, "rows", "settlements", "verifications"):
        rows.append((lower(r.get("tx_hash", r.get("tx"))), lower(r.get("payer")), lower(r.get("seller")),
                     bigint(r.get("amount_atomic")), bigint(r.get("block_number", r.get("block"))),
                     text(r.get("block_time")), lower(r.get("facilitator_from")), text(r.get("status"))))
    return rows


BUILDERS = {
    "rows_sales": rows_sales, "rows_purchases": rows_purchases, "rows_bazaar": rows_bazaar,
    "rows_bazaar_search": rows_bazaar_search, "rows_catalog": rows_catalog,
    "rows_openapi_paid_ops": rows_openapi_paid_ops, "rows_agent402_tools": rows_agent402_tools,
    "rows_agent402_seller": rows_agent402_seller, "rows_vet402_endpoints": rows_vet402_endpoints,
    "rows_trust_search": rows_trust_search, "rows_trust_stats": rows_trust_stats, "rows_settlements": rows_settlements,
}


# ----------------------------------------------------------------------------------------------------------
# Export resolution and table loading
# ----------------------------------------------------------------------------------------------------------

def page_exports(exports: dict[str, Any]) -> list[tuple[int, str, str]]:
    """[(page_no, export_key, path)] for cdp_page_<N> exports, ordered by page."""
    found: list[tuple[int, str, str]] = []
    for key, path in exports.items():
        match = PAGE_KEY_RE.match(str(key))
        if match:
            found.append((int(match.group(1)), str(key), str(path)))
    return sorted(found)


def resolve_export(spec: ViewSpec, exports: dict[str, Any]) -> Optional[tuple[str, str]]:
    """(export_key, path) of the first candidate present in `exports`, else None."""
    for candidate in spec.exports:
        if candidate == PAGES:
            continue
        if candidate in exports and exports[candidate]:
            return candidate, str(exports[candidate])
    return None


def load_export(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def export_shape(payload: Any) -> str:
    """"array" (top-level JSON array), "object" (JSON object) or "scalar"."""
    if isinstance(payload, list):
        return "array"
    if isinstance(payload, dict):
        return "object"
    return "scalar"


def create_table(conn: sqlite3.Connection, spec: ViewSpec) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {spec.name}")
    cols = ", ".join(f"{c} {t}" for c, t in spec.columns)
    conn.execute(f"CREATE TABLE {spec.name} ({cols})")


def insert_rows(conn: sqlite3.Connection, spec: ViewSpec, rows: list[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    width = len(spec.columns)
    bad = [r for r in rows if len(r) != width]
    if bad:
        raise ValueError(f"view {spec.name}: row width {len(bad[0])} != {width} columns")
    conn.executemany(f"INSERT INTO {spec.name} VALUES ({', '.join('?' * width)})", rows)
    return len(rows)


def portable_source_url(path: Any) -> str:
    """The `source_url` of a SQLite-backed view: the export's path relative to the run directory, written as
    `logs/<run_id>/exports/<file>` (the documented layout) so sql.jsonl carries no machine-specific path and the
    committed examples reproduce byte for byte. Zetaris-backed views carry the artifact-server URL instead."""
    resolved = Path(path).resolve()
    for parent in resolved.parents:
        if parent.name == "exports":
            run_dir = parent.parent
            return f"logs/{run_dir.name}/{resolved.relative_to(run_dir).as_posix()}"
    return resolved.name


def build_view(conn: sqlite3.Connection, spec: ViewSpec, exports: dict[str, Any]) -> dict[str, Any]:
    """Create one view table from its export(s); an absent export leaves an empty table (reported as such)."""
    create_table(conn, spec)
    entry: dict[str, Any] = {"name": spec.name, "source_url": None, "rows": 0, "present": False,
                             "exports": [], "family": spec.family}
    if PAGES in spec.exports:
        total = 0
        for page_no, key, path in page_exports(exports):
            payload = load_export(path)
            items = payload.get("items") if isinstance(payload, dict) else payload
            total += insert_rows(conn, spec, rows_bazaar(items or [], page_no))
            entry["exports"].append(key)
            entry["source_url"] = entry["source_url"] or portable_source_url(path)
        entry["rows"] = total
        entry["present"] = bool(entry["exports"])
        return entry
    resolved = resolve_export(spec, exports)
    if resolved is None:
        return entry
    key, path = resolved
    payload = load_export(path)
    builder = BUILDERS[spec.builder]
    entry["rows"] = insert_rows(conn, spec, builder(payload))
    entry["present"] = True
    entry["exports"].append(key)
    entry["source_url"] = portable_source_url(path)
    return entry


def build_views(conn: sqlite3.Connection, exports: dict[str, Any]) -> list[dict[str, Any]]:
    """Create every view table from the exports; return [{name, source_url, rows, present, exports, family}]
    (source_url = `logs/<run_id>/exports/<file>` here). Missing exports give empty tables, never an exception."""
    tables: list[dict[str, Any]] = []
    for spec in VIEWS.values():
        tables.append(build_view(conn, spec, exports))
    conn.commit()
    return tables
