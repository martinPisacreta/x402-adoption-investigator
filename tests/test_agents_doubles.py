"""Test doubles for the modules the agents builder (B4) does not own: a Collector that replays the committed
fixtures by request key, a SQLite executor that builds the VIEWS.md view layer, and a Verifier that decodes the
one settlement the Base RPC fixtures cover. They exist so the Planner/Analyst/Writer/Critic/runner pipeline can be
exercised end to end before B1/B2/B3 land; they mirror SPEC.md section 10.3 row semantics and never fabricate
data that is not in fixtures/raw/. This module defines no test cases.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.agents import collector as collector_mod  # noqa: E402
from investigator.agents import verifier as verifier_mod  # noqa: E402
from investigator.contracts import AgentResult, Observation, Plan, SqlResult, Verification  # noqa: E402
from investigator.zetaris.catalog import bind_params  # noqa: E402
from investigator.zetaris.executor import cast_rows  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
TRANSFER_TOPIC0 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# plan item name -> fixture file (the mapping the real MANIFEST.requests map provides by request key)
FIXTURE_BY_NAME: dict[str, str] = {
    "store_receipts": "store-receipts.json",
    "store_openapi": "store-openapi.json",
    "cdp_search_host": "cdp-discovery-search-urlSubstring-tienda.json",
    "agent402_index": "agent402-index-seller-tienda.json",
    "agent402_route": "agent402-route-x402-adoption.json",
    "trust_search": "x402-trust-search-tienda.json",
    "trust_verified": "x402-trust-verified-p0.json",
    "trust_stats": "x402-trust-stats.json",
    "trust_leaderboard": "x402-trust-leaderboard-p0.json",
    "trust_facilitators": "x402-trust-facilitators.json",
    "vet402_resolve": "vet402-resolve-tienda.json",
    "catalog_snapshot": "cdp-discovery-catalog-compact.json.gz",
    "cdp_page_0": "cdp-discovery-resources-p0.json",
    "cdp_page_1": "cdp-discovery-resources-p1.json",
    "rpc_chain_id": "base-rpc-batch-blockNumber-chainId.json",
}


def load_fixture(name: str) -> tuple[bytes, Any]:
    path = FIXTURES / name
    raw = path.read_bytes()
    data = gzip.decompress(raw) if name.endswith(".gz") else raw
    return raw, json.loads(data.decode("utf-8"))


def flatten_openapi_paid_ops(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, ops in (openapi.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if not isinstance(op, dict) or "x-payment-info" not in op:
                continue
            info = op["x-payment-info"] or {}
            price = (info.get("price") or {}).get("amount")
            rows.append({"path": path, "operation_id": op.get("operationId"), "price_usd": float(price) if price else None,
                         "network": info.get("network"), "asset": info.get("asset"), "pay_to": str(info.get("payTo") or "").lower()})
    return rows


# ---------------------------------------------------------------------------------------------------------
# Collector double
# ---------------------------------------------------------------------------------------------------------

def run_collector(ctx: Any, plan: Plan, unavailable: tuple[str, ...] = ()) -> AgentResult:
    observations: list[Observation] = []
    captured_at = ctx.manifest.fetched_at if ctx.manifest else "2026-10-22T00:05:00Z"
    for item in plan.fetch_list:
        name = item["name"]
        fixture = FIXTURE_BY_NAME.get(name)
        if fixture is None or name in unavailable:
            obs = Observation(name=name, family=item["family"], request_key=item["request_key"], url=item["url"],
                              method=item["method"], http=None, bytes_sha256=None, latency_ms=0, captured_at=captured_at,
                              replayed=True, stale=False, status="fixture_miss" if fixture is None else "unavailable",
                              error="no fixture for this request key" if fixture is None else "simulated outage", payload=None)
            ctx.recover("collector", "fixture_miss" if fixture is None else "source_unavailable", f"{name}: {obs.error}")
            observations.append(obs)
            continue
        raw, payload = load_fixture(fixture)
        if name == "rpc_chain_id" and isinstance(payload, list):
            payload = next((p for p in payload if p.get("id") == 6), payload[0])
        obs = Observation(name=name, family=item["family"], request_key=item["request_key"], url=item["url"],
                          method=item["method"], http=200, bytes_sha256=hashlib.sha256(raw).hexdigest(), latency_ms=0,
                          captured_at=captured_at, replayed=True, stale=False, status="ok", error=None, payload=payload,
                          fixture_file=f"fixtures/raw/{fixture}")
        if not ctx.budget_take("fetches", ctx.budget.max_fetches, "collector"):
            break
        collector_mod.export_observation(ctx, obs)
        seq = ctx.log.emit("fetch", "collector", {"name": name, "family": obs.family, "url": obs.url, "method": obs.method,
                                                  "http": 200, "bytes": len(raw), "sha256": obs.bytes_sha256, "latency_ms": 0,
                                                  "replayed": True, "stale": False, "fixture_file": obs.fixture_file})
        ctx.log.side("fetch", {"seq": seq, "name": name, "family": obs.family, "request_key": obs.request_key, "url": obs.url,
                               "method": obs.method, "http": 200, "bytes": len(raw), "sha256": obs.bytes_sha256, "latency_ms": 0,
                               "replayed": True, "stale": False, "fixture_file": obs.fixture_file,
                               "export_path": obs.export_path, "error": None})
        if name == "store_openapi":
            (ctx.exports_dir / "openapi_paid_ops.json").write_text(
                json.dumps(flatten_openapi_paid_ops(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8")
        observations.append(obs)
    mandatory_down = [o for o in observations if o.status != "ok" and not next(
        (it for it in plan.fetch_list if it["name"] == o.name), {"optional": True})["optional"]]
    if mandatory_down and len(mandatory_down) == len([it for it in plan.fetch_list if not it["optional"]]):
        return AgentResult("collector", "failed", observations, notes=["every mandatory source unavailable (double)"])
    return AgentResult("collector", "ok", observations, notes=["test double: fixtures replayed by plan item name"])


# ---------------------------------------------------------------------------------------------------------
# Verifier double
# ---------------------------------------------------------------------------------------------------------

def _topic_address(topic: str) -> str:
    return "0x" + topic[-40:].lower()


def run_verifier(ctx: Any, plan: Plan, observations: list[Observation]) -> AgentResult:
    payload = verifier_mod.empty_payload()
    receipts = next((o.payload for o in observations if o.name == "store_receipts" and o.status == "ok"), None)
    if not receipts:
        return AgentResult("verifier", "degraded", payload, notes=["no receipts; nothing to verify (double)"])
    _, receipt_fx = load_fixture("base-rpc-eth_getTransactionReceipt.json")
    _, tx_fx = load_fixture("base-rpc-eth_getTransactionByHash.json")
    _, balance_fx = load_fixture("base-rpc-eth_call-usdc-balanceOf-payer.json")
    _, nonce_fx = load_fixture("base-rpc-eth_getTransactionCount-payer.json")
    covered = {receipt_fx["result"]["transactionHash"].lower(): (receipt_fx["result"], tx_fx["result"])}
    external = [s for s in receipts.get("sales", []) if not s.get("ownWallet")]
    focus = plan.params.get("wallet")
    sample: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sale in sorted(external, key=lambda s: (0 if focus and s["payer"].lower() == focus else 1, s["at"]), reverse=False):
        pass
    ordered = sorted(external, key=lambda s: s["at"], reverse=True)
    if focus:
        ordered = [s for s in ordered if s["payer"].lower() == focus] + [s for s in ordered if s["payer"].lower() != focus]
    for sale in ordered:
        payer = sale["payer"].lower()
        if payer in seen:
            continue
        seen.add(payer)
        sample.append(sale)
        if len(sample) >= ctx.budget.max_tx_verifications:
            break
    seller = str(receipts.get("seller") or "").lower()
    settlements: list[dict[str, Any]] = []
    for sale in sample:
        tx_hash = str(sale["transaction"]).lower()
        basescan = f"https://basescan.org/tx/{tx_hash}"
        if tx_hash not in covered:
            v = Verification(tx=tx_hash, status="unverifiable", payer=None, seller=None, amount_atomic=None, block=None,
                             block_time=None, facilitator_from=None, basescan_url=basescan, decoded_from="Transfer log",
                             receipt_match=None, reason="fixture_miss")
        else:
            receipt, tx = covered[tx_hash]
            transfer = next(lg for lg in receipt["logs"] if lg["topics"][0] == TRANSFER_TOPIC0 and lg["address"].lower() == USDC)
            payer, to = _topic_address(transfer["topics"][1]), _topic_address(transfer["topics"][2])
            amount = int(transfer["data"], 16)
            match = payer == sale["payer"].lower() and to == seller and amount == int(sale["amountAtomic"])
            v = Verification(tx=tx_hash, status="verified" if match else "failed", payer=payer, seller=to, amount_atomic=amount,
                             block=int(receipt["blockNumber"], 16), block_time=str(int(tx.get("blockTimestamp", "0x0"), 16)),
                             facilitator_from=str(tx["from"]).lower(), basescan_url=basescan, decoded_from="Transfer log",
                             receipt_match=match, reason=None if match else "decode_mismatch:payer")
            settlements.append({"tx_hash": tx_hash, "payer": payer, "seller": to, "amount_atomic": amount,
                                "block_number": v.block, "block_time": v.block_time, "facilitator_from": v.facilitator_from,
                                "status": v.status})
        ctx.counters["tx_verifications"] += 1
        ctx.log.emit("verify", "verifier", {"tx": v.tx, "status": v.status, "payer": v.payer, "seller": v.seller,
                                            "amount_atomic": v.amount_atomic, "basescan_url": v.basescan_url,
                                            "receipt_match": v.receipt_match, "reason": v.reason})
        payload["verifications"].append(v)
    (ctx.exports_dir / "settlements.json").write_text(json.dumps(settlements, sort_keys=True, separators=(",", ":")) + "\n",
                                                      encoding="utf-8")
    payload["settlements"] = settlements
    wallet = focus or (sample[0]["payer"].lower() if sample else None)
    if wallet == "0x097e3f339d0b023605e12a6b81e2d6cb7571475a":   # the only payer the fixtures cover
        source = {"kind": "chain", "url": "https://mainnet.base.org", "checksum": "sha256:" + hashlib.sha256(
            (FIXTURES / "base-rpc-eth_call-usdc-balanceOf-payer.json").read_bytes()).hexdigest(), "replayed": True}
        payload["chain_facts"] = [
            {"about": f"wallet:base:{wallet}", "predicate": "usdc_balance_atomic", "value": int(balance_fx["result"], 16), "source": source},
            {"about": f"wallet:base:{wallet}", "predicate": "tx_count", "value": int(nonce_fx["result"], 16), "source": dict(source)},
        ]
    search = next((o.payload for o in observations if o.name == "cdp_search_host" and o.status == "ok"), None)
    if search:
        pay_tos = {str(a.get("payTo") or "").lower() for r in search.get("resources", []) for a in r.get("accepts", [])}
        ctx.log.emit("crosscheck", "verifier", {"name": "receipts_seller_vs_bazaar_payto",
                                                "a": {"source": "receipts", "value": seller, "url": "https://tienda.textokit.store/receipts.json"},
                                                "b": {"source": "cdp_search", "value": sorted(pay_tos), "url": "cdp search"},
                                                "agree": seller in pay_tos, "note": "test double"})
    return AgentResult("verifier", "ok", payload, notes=["test double: Transfer log decoded from the RPC fixtures"])


# ---------------------------------------------------------------------------------------------------------
# SQLite executor double (VIEWS.md view layer)
# ---------------------------------------------------------------------------------------------------------

def _host(url: Any) -> str:
    return (urlsplit(str(url or "")).hostname or "").lower()


def _int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(float(value))


def _bazaar_rows(items: list[Any], page: Optional[int]) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for item in items:
        q = item.get("quality") or {}
        for a in item.get("accepts") or []:
            row = (item.get("resource"), _host(item.get("resource")), item.get("serviceName"), item.get("type"), item.get("lastUpdated"),
                   _int(q.get("l30DaysTotalCalls")) or 0, _int(q.get("l30DaysUniquePayers")) or 0, q.get("lastCalledAt"),
                   str(a.get("payTo") or "").lower() or None, a.get("network"), a.get("asset"),
                   _int(a.get("amount") or a.get("maxAmountRequired")), a.get("scheme"))
            rows.append(row + ((page,) if page is not None else ()))
    return rows


class DoubleSqliteExecutor:
    name = "sqlite"

    def __init__(self, run_dir: Path, log: Any = None) -> None:
        self.run_dir = Path(run_dir)
        self.conn = sqlite3.connect(":memory:")
        self.tables: list[dict[str, Any]] = []

    def _load(self, exports: dict[str, str], name: str) -> Any:
        path = exports.get(name)
        return json.loads(Path(path).read_text(encoding="utf-8")) if path else None

    def _table(self, name: str, columns: list[str], rows: list[tuple[Any, ...]], source: str) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {name}")
        self.conn.execute(f"CREATE TABLE {name} ({', '.join(columns)})")
        if rows:
            self.conn.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in columns)})", rows)
        self.tables.append({"name": name, "source_url": source})

    def ensure_views(self, exports: dict[str, str]) -> list[dict[str, Any]]:
        receipts = self._load(exports, "store_receipts")
        if receipts:
            seller = str(receipts.get("seller") or "").lower()
            self._table("sales", ["seller", "agent_id", "settled_at", "payer", "own_wallet", "amount_atomic", "tx_hash",
                                  "x402_version", "service", "resource", "facilitator"],
                        [(seller, receipts.get("agentId"), s.get("at"), str(s.get("payer") or "").lower(), 1 if s.get("ownWallet") else 0,
                          _int(s.get("amountAtomic")), str(s.get("transaction") or "").lower(), s.get("x402Version"), s.get("service"),
                          s.get("resource"), s.get("facilitator")) for s in receipts.get("sales", [])], exports["store_receipts"])
        search = self._load(exports, "cdp_search_host")
        if search:
            self._table("bazaar_search", ["resource", "host", "service_name", "resource_type", "last_updated", "calls_30d", "payers_30d",
                                          "last_called_at", "pay_to", "network", "asset", "amount_atomic", "scheme"],
                        _bazaar_rows(search.get("resources", []), None), exports["cdp_search_host"])
        pages = sorted(k for k in exports if k.startswith("cdp_page_"))
        if pages:
            rows: list[tuple[Any, ...]] = []
            for key in pages:
                rows.extend(_bazaar_rows(self._load(exports, key).get("items", []), int(key.rsplit("_", 1)[1])))
            self._table("bazaar_resources", ["resource", "host", "service_name", "resource_type", "last_updated", "calls_30d", "payers_30d",
                                             "last_called_at", "pay_to", "network", "asset", "amount_atomic", "scheme", "page"], rows, ", ".join(pages))
        snapshot = self._load(exports, "catalog_snapshot")
        if snapshot:
            rows = []
            for item in snapshot.get("items", []):
                q = item.get("quality") or {}
                a = (item.get("accepts") or [{}])[0] or {}
                rows.append((item.get("resource"), _host(item.get("resource")), _int(q.get("l30DaysTotalCalls")) or 0,
                             _int(q.get("l30DaysUniquePayers")) or 0, q.get("lastCalledAt"), str(a.get("payTo") or "").lower() or None,
                             a.get("network"), a.get("asset"), _int(a.get("amount"))))
            self._table("catalog_resources", ["resource", "host", "calls_30d", "payers_30d", "last_called_at", "pay_to", "network", "asset",
                                              "amount_atomic"], rows, exports["catalog_snapshot"])
        ops = self._load(exports, "openapi_paid_ops")
        if ops is not None:
            self._table("openapi_paid_ops", ["path", "operation_id", "price_usd", "network", "asset", "pay_to"],
                        [(o["path"], o["operation_id"], o["price_usd"], o["network"], o["asset"], o["pay_to"]) for o in ops], exports["openapi_paid_ops"])
        index = self._load(exports, "agent402_index")
        if index:
            self._table("agent402_seller", ["origin", "tool_count", "paid_tool_count", "paywall_ok", "health", "routable", "router_dispatch_reason"],
                        [(index.get("origin"), index.get("toolCount"), index.get("paidToolCount"), 1 if (index.get("paywall") or {}).get("ok") else 0,
                          index.get("health"), 1 if index.get("routable") else 0, index.get("routerDispatchReason"))], exports["agent402_index"])
            self._table("agent402_tools", ["route", "slug", "paid", "price", "declared", "last_verified_at"],
                        [(t.get("route"), t.get("slug"), 1 if t.get("paid") else 0, t.get("price"), 1 if t.get("declared") else 0,
                          t.get("lastVerifiedAt")) for t in index.get("tools", [])], exports["agent402_index"])
        vet = self._load(exports, "vet402_resolve")
        if vet:
            self._table("vet402_endpoints", ["canonical_url", "method", "payee_id", "catalog_status", "first_seen", "last_seen"],
                        [(e.get("canonical_url"), e.get("method"), e.get("payee_id"), e.get("catalog_status"), e.get("first_seen"),
                          e.get("last_seen")) for e in vet.get("endpoints", [])], exports["vet402_resolve"])
        trust = self._load(exports, "trust_search")
        if trust:
            self._table("trust_search", ["resource", "host", "delisted", "score", "grade", "provider_verified", "total_reported"],
                        [(r.get("resource"), r.get("host"), 1 if r.get("delisted") else 0, r.get("score"), r.get("grade"),
                          1 if r.get("providerVerified") else 0, trust.get("total")) for r in trust.get("results", [])], exports["trust_search"])
        stats = self._load(exports, "trust_stats")
        if stats:
            self._table("trust_stats", ["endpoints_listed", "endpoints_active", "settlements_30d", "settled_volume_usd_30d",
                                        "distinct_payers_30d", "distinct_receivers_30d"],
                        [(stats.get("endpointsListed"), stats.get("endpointsActive"), stats.get("settlements30d"),
                          stats.get("settledVolumeUsd30d"), stats.get("distinctPayers30d"), stats.get("distinctReceivers30d"))], exports["trust_stats"])
        settlements = self._load(exports, "settlements")
        if settlements is not None:
            self._table("settlements", ["tx_hash", "payer", "seller", "amount_atomic", "block_number", "block_time", "facilitator_from", "status"],
                        [(s["tx_hash"], s["payer"], s["seller"], s["amount_atomic"], s["block_number"], s["block_time"], s["facilitator_from"],
                          s["status"]) for s in settlements], exports["settlements"])
        return list(self.tables)

    def execute(self, query_id: str, sql: str, params: dict[str, Any], meta: dict[str, Any]) -> SqlResult:
        bound = bind_params(sql, params, meta)
        cursor = self.conn.execute(bound)
        headers = [d[0] for d in cursor.description]
        rows = [list(r) for r in cursor.fetchall()]
        return SqlResult(query_id=query_id, executor="sqlite", headers=headers, rows=cast_rows(headers, rows, meta),
                         rowcount=len(rows), time_used_ms=0, sql_sha256=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
                         tables=list(self.tables))

    def cleanup(self) -> None:
        self.conn.close()


def build_double_executor(config: Any, run_dir: Path, log: Any, run_id: str = "") -> DoubleSqliteExecutor:
    return DoubleSqliteExecutor(run_dir, log)
