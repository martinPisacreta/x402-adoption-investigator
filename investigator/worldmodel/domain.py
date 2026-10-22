"""Domain constants: entity grammars, MULTI_VALUED_EDGE_TYPES, predicates, the claim policy hook (SPEC.md 9.2). Owner: B2.

The claim policy hook is where "every claim links to evidence" becomes structural: a fact of kind "agent" is
refused unless its source.url is an https:// URL or log://<run_id>#<seq> AND source.runId is set.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

from .types import Fact, Provenance, UpsertContext

ENTITY_GRAMMARS: dict[str, str] = {
    "seller": "seller:host:<host>",
    "endpoint": "endpoint:url:<canonical url>",
    "wallet": "wallet:base:<0x lowercase> | wallet:solana:<base58> | wallet:<chain-slug>:<address>",
    "facilitator": "facilitator:name:<slug>",
    "registry": "registry:name:<cdp-bazaar|agent402|x402-trust|vet402>",
}
REGISTRIES: tuple[str, ...] = ("cdp-bazaar", "agent402", "x402-trust", "vet402")
CONTEXT_GRAMMARS: dict[str, str] = {"day": "day:utc:<YYYY-MM-DD>", "investigation": "investigation:run:<run_id>",
                                    "step": "step:run:<run_id>/<n>-<agent>"}

# ALL relationship types are multi-valued so payment history is never closed by a later edge.
MULTI_VALUED_EDGE_TYPES: frozenset[str] = frozenset({"paid", "sells", "pays_to", "listed_on", "verified_by", "settled_via"})

REGISTRY_PREDICATES: tuple[str, ...] = ("l30DaysTotalCalls", "l30DaysUniquePayers", "lastCalledAt", "toolCount",
                                        "paidToolCount", "paywall.ok", "health", "routable", "routerDispatchReason",
                                        "providerVerified", "hostScore", "catalog_status")
CHAIN_PREDICATES: tuple[str, ...] = ("onchain_tx", "usdc_balance_atomic", "tx_count")
CLAIM_PREDICATES: tuple[str, ...] = ("external_payers_total", "external_sales_total", "self_settlement_share",
                                     "repeat_external_payers", "top_payer_share", "endpoint_count_for_host", "verdict")
PROVENANCE_KINDS: tuple[str, ...] = ("registry", "receipts", "chain", "agent", "manual")
# Provenance kind per source family (SPEC.md 9.2; sample mode keeps the ORIGINAL kind and url).
PROVENANCE_KIND_BY_FAMILY: dict[str, str] = {
    "receipts": "receipts", "store_openapi": "receipts",
    "cdp_bazaar": "registry", "cdp_search": "registry", "cdp_catalog_snapshot": "registry",
    "agent402": "registry", "x402_trust": "registry", "vet402": "registry",
    "base_rpc": "chain",
}
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
BASE_NETWORK = "eip155:8453"

_HTTPS = re.compile(r"^https://")
_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def seller_id(host: str) -> str:
    return f"seller:host:{host.lower()}"


def endpoint_id(canonical_url: str) -> str:
    return f"endpoint:url:{canonical_url}"


def wallet_id(address: str, chain: str = "base") -> str:
    return f"wallet:{chain}:{address.lower() if chain == 'base' or _EVM_ADDRESS.match(address) else address}"


def wallet_id_for_network(address: str, network: Optional[str]) -> str:
    """Bazaar `accepts[].network` -> wallet id: eip155:8453 -> base; solana:* -> solana; other -> <slugged network>."""
    net = (network or BASE_NETWORK).strip()
    if net == BASE_NETWORK:
        return wallet_id(address, "base")
    if net.startswith("solana:"):
        return f"wallet:solana:{address}"
    slug = re.sub(r"[^a-z0-9]+", "-", net.lower()).strip("-") or "unknown"
    return wallet_id(address, slug)


def facilitator_id(slug: str) -> str:
    return f"facilitator:name:{slug}"


def registry_id(name: str) -> str:
    return f"registry:name:{name}"


def day_context_id(date: str) -> str:
    return f"day:utc:{date[:10]}"


def investigation_context_id(run_id: str) -> str:
    return f"investigation:run:{run_id}"


_HOST_OF: Any = None


def host_of_url(url: Optional[str]) -> Optional[str]:
    """Lowercase host without port, NULL for strings a URI parser rejects: the SAME definition as the SQL view
    layer (investigator/zetaris/views_sqlite.host_of, B3), so World Model views and SQL agree on `host`.
    Falls back to urllib's hostname when that module is unavailable."""
    global _HOST_OF
    if not url or not isinstance(url, str):
        return None
    if _HOST_OF is None:
        try:
            from ..zetaris.views_sqlite import host_of as _impl   # B3-owned; imported, never edited
            _HOST_OF = _impl
        except Exception:  # noqa: BLE001 - optional dependency on the view layer
            _HOST_OF = False
    if _HOST_OF:
        try:
            return _HOST_OF(url)
        except Exception:  # noqa: BLE001
            return None
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def canonical_endpoint_url(url: str) -> str:
    """Lowercase scheme + host, no fragment, no trailing slash (query kept as is)."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}{path}{query}"


def day_context(at: str, source: Provenance) -> UpsertContext:
    date = at[:10]
    return UpsertContext(type="day", id=day_context_id(date), attrs={"date": date}, source=source,
                         started_at=f"{date}T00:00:00.000Z", ended_at=f"{date}T23:59:59.999Z")


def log_ref_pattern(run_id: str) -> re.Pattern[str]:
    return re.compile(rf"^log://{re.escape(run_id)}#\d+$")


def claim_policy(fact: Fact, run_id: Optional[str] = None) -> None:
    """Refuse agent facts without an evidence URL (https:// or log://<run_id>#<seq>) and a runId."""
    if fact.source.kind != "agent":
        return
    url = fact.source.url or ""
    rid = fact.source.run_id or run_id
    ok_url = bool(_HTTPS.match(url)) or (rid is not None and bool(log_ref_pattern(rid).match(url)))
    if not ok_url or not fact.source.run_id:
        raise ValueError(
            f"World Model claim policy: agent fact {fact.predicate!r} about {fact.about!r} needs source.url "
            f"(https:// or log://<run_id>#<seq>) and source.runId; got url={url!r} runId={fact.source.run_id!r}")


def as_int(value: Any) -> Optional[int]:
    """Decimal strings / numbers -> int (atomic amounts); None when not parseable."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None
