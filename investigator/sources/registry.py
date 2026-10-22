"""Source families, request builders and canonical request keys (SPEC.md sections 7.1 and 7.3). Owner: B1.

Canonical request key: "<METHOD> <url>" where the URL has a lowercase scheme and host, no fragment, query
parameters sorted by key and no trailing slash. A request with a JSON body (POST) appends
" <sha256(canonical_json(body))[:16]>" so two different bodies never share a key. JSON-RPC:
"RPC <method> <sha256(canonical_json(params))[:16]>" per batch element (a batch is answered element by
element). The catalog snapshot: "SNAPSHOT cdp-discovery-catalog-compact". These keys index
fixtures/raw/MANIFEST.json["requests"], which scripts/build_manifest_requests.py derives with the SAME
builders (request_for / rpc_request_key), so a key can never drift between a live run and its fixture.

request_for(family, name, **params) builds one fetch_list item; default_fetch_list(playbook, params) builds the
whole ordered list for a playbook (the Planner calls it; the Collector falls back to it when the plan carries no
fetch_list). Every item is a plain dict so it serialises into the `plan` handoff unchanged.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from ..contracts import SOURCE_FAMILIES
from ..worldmodel.stable_ids import canonical_json

SNAPSHOT_KEY = "SNAPSHOT cdp-discovery-catalog-compact"
SNAPSHOT_FILE = "cdp-discovery-catalog-compact.json.gz"
SNAPSHOT_SOURCE_URL = "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N"
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_RPC_URL = "https://mainnet.base.org"

CDP_PAGE_LIMIT_LIVE = 1000      # the API clamps limit to 1000
CDP_PAGE_LIMIT_FIXTURE = 100    # the committed p0/p1 pages were captured with limit=100
CDP_SEARCH_LIMIT = 20           # limit > 20 is HTTP 400; offset is ignored
TRUST_PAGE_LIMIT = 25           # x402-trust pages: limit 1-25
AGENT402_ROUTE_QUERY_DEFAULT = "x402 adoption"   # the committed route capture: demand evidence for the question itself

STORE_HOST_DEFAULT = "tienda.textokit.store"   # the pre-existing component's store; request_for(host=...) swaps it

# URL templates per family (public, no keys). Placeholders are filled by request_for() with URL-quoted values.
# The store templates are fixed URLs (the Planner formats them without a host); request_for() substitutes
# STORE_HOST_DEFAULT with the `host` parameter when one is given, so another store's receipts can be read.
URLS: dict[str, dict[str, str]] = {
    "receipts": {"store_receipts": f"https://{STORE_HOST_DEFAULT}/receipts.json"},
    "store_openapi": {"store_openapi": f"https://{STORE_HOST_DEFAULT}/openapi.json",
                      "store_llms": f"https://{STORE_HOST_DEFAULT}/llms.txt"},
    "cdp_bazaar": {"cdp_page": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit={limit}&offset={offset}"},
    "cdp_search": {"cdp_search_host": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring={host}&limit=20",
                   "cdp_search_query": "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query={query}&limit=20"},
    "cdp_catalog_snapshot": {"cdp_catalog_snapshot": SNAPSHOT_SOURCE_URL},
    "agent402": {"agent402_index": "https://agent402.tools/api/index?seller={host}",
                 "agent402_route": "https://agent402.tools/api/route?q={query}&include=external"},
    "x402_trust": {"trust_stats": "https://x402-trust.com/trust/stats",
                   "trust_search": "https://x402-trust.com/trust/search?q={host}&limit=25&offset=0",
                   "trust_verified": "https://x402-trust.com/verified.json?limit=25&offset=0",
                   "trust_facilitators": "https://x402-trust.com/v1/facilitators",
                   "trust_report": "https://x402-trust.com/trust/report.json",
                   "trust_leaderboard": "https://x402-trust.com/trust/leaderboard?limit=25&offset=0",
                   "trust_activity": "https://x402-trust.com/api/activity.json",
                   "trust_openapi": "https://x402-trust.com/openapi.json",
                   "trust_llms": "https://x402-trust.com/llms.txt",
                   "trust_semantic_search": "https://x402-trust.com/v1/semantic-search"},
    "vet402": {"vet402_resolve": "https://vet402.com/api/v1/resolve?q={host}"},
    "base_rpc": {"base_rpc": BASE_RPC_URL,          # skeleton name (the Planner's liveness probe uses it)
                 "base_rpc_batch": BASE_RPC_URL},   # deferred marker for the Verifier's settlement batch
}
NEVER_IN_A_RUN: tuple[str, ...] = ("https://x402-trust.com/v1/semantic-search",)   # 21 s; fixture only
POST_NAMES: frozenset[str] = frozenset({"trust_semantic_search", "base_rpc", "base_rpc_batch"})
DEFAULT_TIMEOUT_S = 10.0

# Fetch order inside a run (lower first). Receipts first: the Verifier's tx sample and most metrics need them.
PRIORITY: dict[str, int] = {
    "receipts": 10, "store_openapi": 20, "cdp_search": 30, "cdp_catalog_snapshot": 30, "cdp_bazaar": 35,
    "agent402": 40, "vet402": 50, "x402_trust": 60, "base_rpc": 90,
}


# ----------------------------------------------------------------------------------------------------------
# Keys
# ----------------------------------------------------------------------------------------------------------

def canonical_url(url: str) -> str:
    """Lowercase scheme and host, drop the fragment, sort query parameters by key, strip the trailing slash."""
    parts = urlsplit(url.strip())
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def body_digest(body: Any) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()[:16]


def canonical_request_key(method: str, url: str, body: Any = None) -> str:
    key = f"{method.upper()} {canonical_url(url)}"
    if body is not None and method.upper() != "GET":
        key += f" {body_digest(body)}"
    return key


def rpc_request_key(method: str, params: Any) -> str:
    digest = hashlib.sha256(canonical_json(params).encode("utf-8")).hexdigest()[:16]
    return f"RPC {method} {digest}"


def family_of_key(request_key: str) -> str:
    """Source family of a canonical request key (by host and path; never by substring of the whole key)."""
    key = request_key.strip()
    if key.startswith("SNAPSHOT "):
        return "cdp_catalog_snapshot"
    if key.startswith("RPC "):
        return "base_rpc"
    url = key.split(" ", 1)[1] if " " in key else key
    url = url.split(" ", 1)[0]
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path
    if host == "api.cdp.coinbase.com":
        return "cdp_search" if "/discovery/search" in path else "cdp_bazaar"
    if host == "agent402.tools":
        return "agent402"
    if host == "x402-trust.com":
        return "x402_trust"
    if host == "vet402.com":
        return "vet402"
    if host == "mainnet.base.org":
        return "base_rpc"
    if path.endswith("/receipts.json"):
        return "receipts"
    if path.endswith("/openapi.json") or path.endswith("/llms.txt"):
        return "store_openapi"
    return "unknown"


# ----------------------------------------------------------------------------------------------------------
# Request builders
# ----------------------------------------------------------------------------------------------------------

def _fill(template: str, params: dict[str, Any]) -> str:
    quoted = {k: quote(str(v), safe="") for k, v in params.items()}
    try:
        return template.format(**quoted)
    except KeyError as exc:
        raise ValueError(f"missing request parameter {exc.args[0]!r} for template {template!r}") from exc


def item_name(family: str, name: str, params: dict[str, Any]) -> str:
    """Observation / export name of a request (one per family-name per run; pages carry their index)."""
    if family == "cdp_bazaar" and name == "cdp_page":
        limit = int(params.get("limit") or CDP_PAGE_LIMIT_LIVE)
        offset = int(params.get("offset") or 0)
        return f"cdp_page_{offset // max(1, limit)}"
    return name


def request_for(family: str, name: str, *, optional: Optional[bool] = None, priority: Optional[int] = None,
                timeout: float = DEFAULT_TIMEOUT_S, body: Any = None, **params: Any) -> dict[str, Any]:
    """Build one fetch_list item {name, family, request_key, url, method, body?, optional, priority, timeout}.

    base_rpc items are deferred markers (the Verifier builds the real JSON-RPC calls from the receipts tx
    sample); the snapshot item carries SNAPSHOT_KEY and is served from fixtures in every mode."""
    if family not in SOURCE_FAMILIES:
        raise ValueError(f"unknown source family {family!r}; expected one of {SOURCE_FAMILIES}")
    templates = URLS.get(family, {})
    if name not in templates:
        raise ValueError(f"unknown request {name!r} for family {family!r}; expected one of {sorted(templates)}")
    if family == "cdp_bazaar":
        params.setdefault("limit", CDP_PAGE_LIMIT_LIVE)
        params.setdefault("offset", 0)
    if "host" in params and params["host"]:
        params["host"] = str(params["host"]).strip().lower()
    template = templates[name]
    if family in ("receipts", "store_openapi"):
        template = template.replace(STORE_HOST_DEFAULT, params.pop("host", None) or STORE_HOST_DEFAULT)
    url = _fill(template, params)
    method = "POST" if name in POST_NAMES else "GET"
    item: dict[str, Any] = {
        "name": item_name(family, name, params),
        "family": family,
        "request_key": "",
        "url": url,
        "method": method,
        "optional": True if optional is None else bool(optional),
        "priority": PRIORITY.get(family, 50) if priority is None else int(priority),
        "timeout": float(timeout),
    }
    if family == "cdp_catalog_snapshot":
        item["request_key"] = SNAPSHOT_KEY
        item["fixture_only"] = True
        item["url"] = SNAPSHOT_SOURCE_URL
    elif family == "base_rpc":
        item["request_key"] = "RPC batch (built by the verifier from the receipts tx sample)"
        item["deferred"] = True
    else:
        if body is not None:
            item["body"] = body
        item["request_key"] = canonical_request_key(method, url, body)
    if url in NEVER_IN_A_RUN:
        item["never_in_a_run"] = True
    if family == "cdp_bazaar":
        item["page"] = int(params["offset"]) // max(1, int(params["limit"]))
        item["limit"] = int(params["limit"])
        item["offset"] = int(params["offset"])
    return item


def page_items(pages: int, limit: int = CDP_PAGE_LIMIT_LIVE, start_offset: int = 0) -> list[dict[str, Any]]:
    """Discovery/resources page items offset start, start+limit, ... (pages of them)."""
    return [request_for("cdp_bazaar", "cdp_page", limit=limit, offset=start_offset + i * limit)
            for i in range(max(0, int(pages)))]


def default_fetch_list(playbook: str, params: dict[str, Any], *, catalog: str = "snapshot",
                       bazaar_pages: int = 3, fixture_mode: bool = False) -> list[dict[str, Any]]:
    """The ordered fetch_list for a playbook (SPEC.md 6.1/6.2 and section 11).

    - seller_profile(host): receipts, openapi, Bazaar search, Agent402 index + route, x402-trust search /
      verified / stats, vet402, deferred base_rpc.
    - catalog_concentration: the compact snapshot (catalog metrics) plus page-level views: the committed
      limit=100 pages p0/p1 when fixture_mode, otherwise <= bazaar_pages live pages of limit=1000 when
      catalog == "pages", or page 0 only when catalog == "snapshot"; x402-trust stats / leaderboard /
      facilitators for the ecosystem_size conflict.
    - payer_dossier(wallet, host?): receipts, Bazaar search for the host, deferred base_rpc.
    Items whose `optional` is False are the mandatory sources (E_NO_SOURCES only when ALL of them fail)."""
    host = (params.get("host") or "").strip().lower() or None
    query = params.get("route_query") or AGENT402_ROUTE_QUERY_DEFAULT
    items: list[dict[str, Any]] = []
    if playbook == "seller_profile":
        if not host:
            raise ValueError("seller_profile needs params.host")
        items += [
            request_for("receipts", "store_receipts", host=host, optional=False),
            request_for("store_openapi", "store_openapi", host=host),
            request_for("cdp_search", "cdp_search_host", host=host, optional=False),
            request_for("agent402", "agent402_index", host=host),
            request_for("agent402", "agent402_route", query=query),
            request_for("vet402", "vet402_resolve", host=host),
            request_for("x402_trust", "trust_search", host=host),
            request_for("x402_trust", "trust_verified"),
            request_for("x402_trust", "trust_stats"),
            request_for("base_rpc", "base_rpc_batch"),
        ]
    elif playbook == "catalog_concentration":
        items.append(request_for("cdp_catalog_snapshot", "cdp_catalog_snapshot", optional=False))
        if fixture_mode:
            items += page_items(2, limit=CDP_PAGE_LIMIT_FIXTURE)
        elif catalog == "pages":
            items += page_items(bazaar_pages, limit=CDP_PAGE_LIMIT_LIVE)
        else:
            items += page_items(1, limit=CDP_PAGE_LIMIT_LIVE)
        if items[1:]:
            items[1]["optional"] = False
        items += [
            request_for("x402_trust", "trust_stats"),
            request_for("x402_trust", "trust_leaderboard"),
            request_for("x402_trust", "trust_facilitators"),
        ]
    elif playbook == "payer_dossier":
        if not host:
            raise ValueError("payer_dossier needs params.host (the store whose receipts name the payer)")
        items += [
            request_for("receipts", "store_receipts", host=host, optional=False),
            request_for("cdp_search", "cdp_search_host", host=host),
            request_for("base_rpc", "base_rpc_batch"),
        ]
    else:
        raise ValueError(f"unknown playbook {playbook!r}")
    return items


def families_of(items: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for item in items:
        fam = item.get("family")
        if fam and fam not in seen:
            seen.append(fam)
    return seen
