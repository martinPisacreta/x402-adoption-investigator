"""Build the run exports (logs/<run_id>/exports/*.json) straight from fixtures/raw, without the Collector. Owner: B3.

Used by tests/test_sql_dual_backend.py, scripts/record_zetaris_results.py and scripts/zetaris_preflight.py so
the view layer can be exercised and recorded before (and independently of) the Collector and the Verifier.
Export names are the observation names of SPEC.md 7.1 / sources/registry.py; files are canonical JSON
(sort_keys, compact separators, trailing newline), exactly like agents.collector.export_observation().

Two exports are derived here because their producers are other agents:
- openapi_paid_ops: the store OpenAPI paths flattened to rows {path, operation_id, price_usd, network, asset,
  pay_to} (the Collector does the same in live runs; OpenAPI paths are map keys Zetaris cannot explode).
- settlements: the Base RPC receipt fixture decoded (USDC Transfer log: payer = topics[1], seller =
  topics[2], amount = data; tx.from is the facilitator) and matched against the receipts rows, the shape
  the Verifier writes in a run.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Optional

FIXTURE_FILES: dict[str, str] = {
    "store_receipts": "store-receipts.json",
    "store_openapi": "store-openapi.json",
    "cdp_search_host": "cdp-discovery-search-urlSubstring-tienda.json",
    "cdp_page_0": "cdp-discovery-resources-p0.json",
    "cdp_page_1": "cdp-discovery-resources-p1.json",
    "agent402_index": "agent402-index-seller-tienda.json",
    "vet402_resolve": "vet402-resolve-tienda.json",
    "trust_search": "x402-trust-search-tienda.json",
    "trust_stats": "x402-trust-stats.json",
}
CATALOG_FILE = "cdp-discovery-catalog-compact.json.gz"
RPC_RECEIPT_FILE = "base-rpc-eth_getTransactionReceipt.json"
RPC_TX_FILE = "base-rpc-eth_getTransactionByHash.json"
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DERIVED_EXPORTS: tuple[str, ...] = ("openapi_paid_ops", "settlements", "catalog_snapshot")


def canonical_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def flatten_openapi_paid_ops(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    """Every operation carrying x-payment-info -> {path, operation_id, price_usd, network, asset, pay_to}."""
    rows: list[dict[str, Any]] = []
    for path, operations in (openapi.get("paths") or {}).items():
        if not isinstance(operations, dict):
            continue
        for method, op in operations.items():
            if not isinstance(op, dict) or "x-payment-info" not in op:
                continue
            info = op.get("x-payment-info") or {}
            price = info.get("price") or {}
            amount = price.get("amount")
            try:
                price_usd: Optional[float] = float(amount) if amount is not None else None
            except (TypeError, ValueError):
                price_usd = None
            rows.append({"path": path, "method": str(method).upper(), "operation_id": op.get("operationId"),
                         "price_usd": price_usd, "network": info.get("network"), "asset": info.get("asset"),
                         "pay_to": (info.get("payTo") or "").lower() or None})
    rows.sort(key=lambda r: (r["path"], r["method"]))
    return rows


def _hex_int(value: Any) -> Optional[int]:
    if isinstance(value, str) and value.startswith("0x"):
        return int(value, 16)
    if isinstance(value, int):
        return value
    return None


def _iso_from_hex_seconds(value: Any) -> Optional[str]:
    seconds = _hex_int(value)
    if seconds is None:
        return None
    return _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def decode_settlements(receipt_rpc: Any, tx_rpc: Any, receipts: dict[str, Any]) -> list[dict[str, Any]]:
    """Decode the USDC Transfer log of one eth_getTransactionReceipt response and match it to receipts.sales."""
    receipt = receipt_rpc.get("result") if isinstance(receipt_rpc, dict) else None
    tx = tx_rpc.get("result") if isinstance(tx_rpc, dict) else None
    if not isinstance(receipt, dict):
        return []
    transfer = None
    for log in receipt.get("logs") or []:
        topics = log.get("topics") or []
        if (log.get("address") or "").lower() == USDC_BASE and topics and topics[0].lower() == TRANSFER_TOPIC:
            transfer = log
            break
    if transfer is None:
        return []
    topics = transfer["topics"]
    payer = "0x" + topics[1][-40:].lower()
    seller = "0x" + topics[2][-40:].lower()
    amount = _hex_int(transfer.get("data")) or 0
    tx_hash = (receipt.get("transactionHash") or "").lower()
    match = None
    for sale in receipts.get("sales") or []:
        if (sale.get("transaction") or "").lower() == tx_hash:
            match = sale
            break
    ok = bool(match) and (match.get("payer") or "").lower() == payer and str(match.get("amountAtomic")) == str(amount) \
        and (receipts.get("seller") or "").lower() == seller and receipt.get("status") == "0x1"
    return [{"tx_hash": tx_hash, "payer": payer, "seller": seller, "amount_atomic": amount,
             "block_number": _hex_int(receipt.get("blockNumber")),
             "block_time": _iso_from_hex_seconds((tx or {}).get("blockTimestamp")),
             "facilitator_from": (receipt.get("from") or "").lower() or None,
             "status": "verified" if ok else "failed", "decoded_from": "Transfer log",
             "receipt_match": bool(ok)}]


def build_fixture_exports(fixtures_dir: Path, out_dir: Path, include_catalog: bool = True) -> dict[str, str]:
    """Write every export under out_dir and return {export_name: path}."""
    fixtures_dir = Path(fixtures_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}

    def write(name: str, payload: Any) -> None:
        path = out_dir / f"{name}.json"
        path.write_text(canonical_dump(payload), encoding="utf-8")
        exports[name] = str(path)

    loaded: dict[str, Any] = {}
    for name, filename in FIXTURE_FILES.items():
        src = fixtures_dir / filename
        if not src.is_file():
            continue
        loaded[name] = load_json(src)
        write(name, loaded[name])
    if "store_openapi" in loaded:
        write("openapi_paid_ops", flatten_openapi_paid_ops(loaded["store_openapi"]))
    receipt_path = fixtures_dir / RPC_RECEIPT_FILE
    tx_path = fixtures_dir / RPC_TX_FILE
    if receipt_path.is_file() and "store_receipts" in loaded:
        tx_rpc = load_json(tx_path) if tx_path.is_file() else {}
        write("settlements", decode_settlements(load_json(receipt_path), tx_rpc, loaded["store_receipts"]))
    catalog_path = fixtures_dir / CATALOG_FILE
    if include_catalog and catalog_path.is_file():
        with gzip.open(catalog_path, "rb") as handle:
            write("catalog_snapshot", json.loads(handle.read().decode("utf-8")))
    return exports


def exports_sha256(exports: dict[str, str]) -> dict[str, str]:
    return {name: sha256_file(Path(path)) for name, path in sorted(exports.items())}


def public_urls(fixtures_dir: Path) -> dict[str, str]:
    """Export name -> public URL from fixtures/raw/MANIFEST.json (for zetaris_source "direct")."""
    manifest_path = Path(fixtures_dir) / "MANIFEST.json"
    if not manifest_path.is_file():
        return {}
    manifest = load_json(manifest_path)
    sources = manifest.get("sources") or {}
    out: dict[str, str] = {}
    mapping = {"store_receipts": "store_receipts", "cdp_search_host": "cdp_bazaar_discovery_search",
               "cdp_page_0": "cdp_bazaar_discovery_resources"}
    for export_name, source_key in mapping.items():
        url = (sources.get(source_key) or {}).get("url")
        if url:
            out[export_name] = url
    return out


def default_params(exports: dict[str, str]) -> dict[str, Any]:
    """Query parameters derived from the exports (no identity lives in code): the host of the first Bazaar
    search listing and the first external payer of the receipts."""
    params: dict[str, Any] = {}
    search_path = exports.get("cdp_search_host")
    if search_path:
        from .views_sqlite import host_of
        resources = (load_json(Path(search_path)) or {}).get("resources") or []
        for item in resources:
            host = host_of(item.get("resource")) if isinstance(item, dict) else None
            if host:
                params["host"] = host
                break
    receipts_path = exports.get("store_receipts")
    if receipts_path:
        receipts = load_json(Path(receipts_path)) or {}
        for sale in receipts.get("sales") or []:
            if isinstance(sale, dict) and sale.get("ownWallet") is False and sale.get("payer"):
                params["wallet"] = str(sale["payer"]).lower()
                break
    return params
