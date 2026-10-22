"""Each World Model adapter over its fixture: expected entity/fact counts read from the fixture files (SPEC.md 9.3). Owner: B2.

Nothing here types a pitch number: every expectation is computed from the fixture bytes at test time.
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock  # noqa: E402
from investigator.worldmodel import WorldModel  # noqa: E402
from investigator.worldmodel import views  # noqa: E402
from investigator.worldmodel.adapters import ADAPTERS, adapter, adapter_or_skip, adapter_skip  # noqa: E402
from investigator.worldmodel.adapters.base_rpc import decode_receipt, decode_transfer_with_authorization  # noqa: E402
from investigator.worldmodel.domain import PROVENANCE_KIND_BY_FAMILY, host_of_url  # noqa: E402
from investigator.worldmodel.types import AssertFact, IngestObservation, Provenance, Relate, UpsertEntity  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"
CAPTURED_AT = "2026-10-22T00:05:00.000Z"
PAYER = "0x097e3f339d0b023605e12a6b81e2d6cb7571475a"
# file -> (family, url, observation name). Keys only; counts come from the files.
FIXTURE_REQUESTS: dict[str, tuple[str, str, str]] = {
    "store-receipts.json": ("receipts", "https://tienda.textokit.store/receipts.json", "store_receipts"),
    "store-openapi.json": ("store_openapi", "https://tienda.textokit.store/openapi.json", "store_openapi"),
    "store-llms.txt": ("store_openapi", "https://tienda.textokit.store/llms.txt", "store_llms"),
    "cdp-discovery-search-urlSubstring-tienda.json": ("cdp_search", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20", "cdp_search_host"),
    "cdp-discovery-search-query-textokit.json": ("cdp_search", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query=textokit&limit=20", "cdp_search_query"),
    "cdp-discovery-resources-p0.json": ("cdp_bazaar", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=0", "cdp_page_0"),
    "cdp-discovery-resources-p1.json": ("cdp_bazaar", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=100", "cdp_page_1"),
    "cdp-discovery-catalog-compact.json.gz": ("cdp_catalog_snapshot", "https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N", "cdp_catalog_snapshot"),
    "agent402-index-seller-tienda.json": ("agent402", "https://agent402.tools/api/index?seller=tienda.textokit.store", "agent402_index"),
    "agent402-route-x402-adoption.json": ("agent402", "https://agent402.tools/api/route?q=x402%20adoption&include=external", "agent402_route"),
    "x402-trust-stats.json": ("x402_trust", "https://x402-trust.com/trust/stats", "trust_stats"),
    "x402-trust-search-tienda.json": ("x402_trust", "https://x402-trust.com/trust/search?q=tienda.textokit.store&limit=25", "trust_search"),
    "x402-trust-verified-p0.json": ("x402_trust", "https://x402-trust.com/verified.json?limit=25", "trust_verified"),
    "x402-trust-facilitators.json": ("x402_trust", "https://x402-trust.com/v1/facilitators", "trust_facilitators"),
    "x402-trust-leaderboard-p0.json": ("x402_trust", "https://x402-trust.com/trust/leaderboard?limit=25&offset=0", "trust_leaderboard"),
    "x402-trust-report.json": ("x402_trust", "https://x402-trust.com/trust/report.json", "trust_report"),
    "x402-trust-activity.json": ("x402_trust", "https://x402-trust.com/activity", "trust_activity"),
    "vet402-resolve-tienda.json": ("vet402", "https://vet402.com/api/v1/resolve?q=tienda.textokit.store", "vet402_resolve"),
    "base-rpc-eth_getTransactionReceipt.json": ("base_rpc", "https://mainnet.base.org", "rpc_receipt_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3"),
    "base-rpc-eth_getTransactionByHash.json": ("base_rpc", "https://mainnet.base.org", "rpc_tx_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3"),
    "base-rpc-eth_call-usdc-balanceOf-payer.json": ("base_rpc", "https://mainnet.base.org", f"rpc_balance_{PAYER}"),
    "base-rpc-eth_getTransactionCount-payer.json": ("base_rpc", "https://mainnet.base.org", f"rpc_nonce_{PAYER}"),
    "base-rpc-batch-blockNumber-chainId.json": ("base_rpc", "https://mainnet.base.org", "rpc_batch_blockNumber_chainId"),
}


def load_fixture(name: str) -> tuple[Any, str]:
    raw = (FIXTURES / name).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if name.endswith(".gz"):
        return json.loads(gzip.decompress(raw).decode("utf-8")), digest
    if name.endswith(".txt"):
        return raw.decode("utf-8"), digest
    return json.loads(raw.decode("utf-8")), digest


def observation(name: str, run_id: str = "test") -> IngestObservation:
    family, url, obs_name = FIXTURE_REQUESTS[name]
    payload, digest = load_fixture(name)
    key = f"{family}:GET {url}" if family != "base_rpc" else f"{family}:RPC {obs_name}"
    return IngestObservation(external_id=key, at=CAPTURED_AT, payload=payload, type=family, title=obs_name,
                             source=Provenance(kind=PROVENANCE_KIND_BY_FAMILY[family], at=CAPTURED_AT, by=f"fixture:fixtures/raw/{name}",
                                               url=url, checksum=f"sha256:{digest}", run_id=run_id))


def by_type(events: list[Any]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = collections.defaultdict(list)
    for e in events:
        out[type(e).__name__].append(e)
    return out


class AdapterRegistry(unittest.TestCase):
    def test_every_family_has_an_adapter(self) -> None:
        for family in ("receipts", "store_openapi", "cdp_bazaar", "cdp_search", "cdp_catalog_snapshot", "agent402", "x402_trust", "vet402", "base_rpc"):
            self.assertTrue(callable(adapter(family)), family)
        with self.assertRaises(KeyError):
            adapter("unknown")
        self.assertEqual(adapter_or_skip("unknown")(observation("store-receipts.json")), [])
        self.assertEqual(set(ADAPTERS) - set(PROVENANCE_KIND_BY_FAMILY), set())

    def test_receipts_fixture_counts_are_read_not_typed(self) -> None:
        receipts = json.loads((FIXTURES / "store-receipts.json").read_text(encoding="utf-8"))
        sales = receipts["sales"]
        own = [s for s in sales if s.get("ownWallet")]
        external = [s for s in sales if not s.get("ownWallet")]
        self.assertEqual(len(sales), receipts["totals"]["sales"])
        self.assertEqual(len(own), receipts["totals"]["salesFromOwnWallets"])
        self.assertEqual(len({s["payer"].lower() for s in external}), receipts["totals"]["externalPayers"])

    def test_unknown_shape_gives_no_events_and_a_visible_skip_note(self) -> None:
        obs = IngestObservation(external_id="x402_trust:GET https://x402-trust.com/activity", at=CAPTURED_AT, payload={"enabled": True, "recent": []},
                                type="x402_trust", title="trust_activity", source=Provenance(kind="registry", at=CAPTURED_AT))
        self.assertEqual(adapter("x402_trust")(obs), [])
        note = adapter_skip(obs)
        self.assertEqual(note["top_level_keys"], ["enabled", "recent"])
        text_obs = observation("store-llms.txt")
        self.assertEqual(adapter("store_openapi")(text_obs), [])
        self.assertEqual(adapter_skip(text_obs)["top_level_keys"], ["str"])


class ReceiptsAdapter(unittest.TestCase):
    def test_receipts_adapter(self) -> None:
        obs = observation("store-receipts.json")
        sales = obs.payload["sales"]
        events = adapter("receipts")(obs)
        groups = by_type(events)
        sellers = {e.id for e in groups["UpsertEntity"] if e.type == "seller"}
        self.assertEqual(sellers, {"seller:host:tienda.textokit.store"})
        wallets = {e.id: e.attrs for e in groups["UpsertEntity"] if e.type == "wallet" and e.attrs.get("role") == "payer"}
        own_payers = {s["payer"].lower() for s in sales if s.get("ownWallet")}
        external_payers = {s["payer"].lower() for s in sales if not s.get("ownWallet")}
        self.assertEqual({w for w, a in wallets.items() if a.get("ownWallet")}, {f"wallet:base:{p}" for p in own_payers})
        self.assertEqual({w for w, a in wallets.items() if not a.get("ownWallet")}, {f"wallet:base:{p}" for p in external_payers})
        paid = [r for r in groups["Relate"] if r.type == "paid"]
        self.assertEqual(len(paid), len(sales))
        self.assertEqual(len({r.from_id + r.to_id + (r.valid_from or "") for r in paid}), len(sales), "one edge per settlement")
        for rel in paid:
            self.assertEqual(rel.source.kind, "receipts")
            self.assertTrue(rel.context.startswith("day:utc:"))
            self.assertIsInstance(rel.attrs["amountAtomic"], int)
            self.assertIn("ownWallet", rel.attrs)
        self.assertEqual(sum(r.attrs["amountAtomic"] for r in paid), sum(int(s["amountAtomic"]) for s in sales))
        pays_to = [r for r in groups["Relate"] if r.type == "pays_to"]
        self.assertTrue(all(r.to_id == f"wallet:base:{obs.payload['seller'].lower()}" for r in pays_to))
        self.assertTrue(any(isinstance(e, AssertFact) and e.predicate == "payTo" for e in events))


class CdpBazaarAdapter(unittest.TestCase):
    def test_cdp_search_adapter(self) -> None:
        obs = observation("cdp-discovery-search-urlSubstring-tienda.json")
        resources = obs.payload["resources"]
        events = adapter("cdp_search")(obs)
        groups = by_type(events)
        listed = [r for r in groups["Relate"] if r.type == "listed_on" and r.to_id == "registry:name:cdp-bazaar"]
        self.assertEqual(len(listed), len(resources))
        facts_per_endpoint = collections.Counter(f.about for f in groups["AssertFact"] if f.predicate in ("l30DaysTotalCalls", "l30DaysUniquePayers", "lastCalledAt"))
        expected = {f"endpoint:url:{r['resource']}": len([k for k in ("l30DaysTotalCalls", "l30DaysUniquePayers", "lastCalledAt") if k in r.get("quality", {})]) for r in resources}
        self.assertEqual(dict(facts_per_endpoint), expected)
        self.assertTrue(all(v == 3 for v in expected.values()))
        pay_tos = {r.to_id for r in groups["Relate"] if r.type == "pays_to"}
        self.assertEqual(pay_tos, {f"wallet:base:{a['payTo'].lower()}" for r in resources for a in r["accepts"]})
        self.assertTrue(all(e.source.kind == "registry" for e in events))
        returned = [f for f in groups["AssertFact"] if f.predicate == "cdpSearchReturned"]
        self.assertEqual(returned[0].value, len(resources))
        self.assertEqual(returned[0].about, "seller:host:tienda.textokit.store")

    def test_cdp_page_adapter(self) -> None:
        obs = observation("cdp-discovery-resources-p0.json")
        items = obs.payload["items"]
        events = adapter("cdp_bazaar")(obs)
        groups = by_type(events)
        listed = [r for r in groups["Relate"] if r.type == "listed_on"]
        self.assertEqual(len(listed), len(items))
        total = [f for f in groups["AssertFact"] if f.predicate == "apiTotal"]
        self.assertEqual(total[0].value, obs.payload["pagination"]["total"])
        solana = [r for r in groups["Relate"] if r.type == "pays_to" and r.to_id.startswith("wallet:solana:")]
        self.assertEqual(len(solana), sum(1 for it in items for a in it["accepts"] if str(a.get("network", "")).startswith("solana:") and a.get("payTo")))

    def test_cdp_catalog_snapshot_folds_per_host(self) -> None:
        obs = observation("cdp-discovery-catalog-compact.json.gz")
        items = obs.payload["items"]
        # the SAME host definition as the SQL view layer (hostname without port, NULL for URI-illegal strings)
        hosts = collections.Counter(h for h in (host_of_url(it["resource"]) for it in items) if h)
        without_host = sum(1 for it in items if not host_of_url(it["resource"]))
        events = adapter("cdp_catalog_snapshot")(obs)
        groups = by_type(events)
        sellers = [e for e in groups["UpsertEntity"] if e.type == "seller"]
        self.assertEqual(len(sellers), len(hosts))
        self.assertNotIn("seller:host:unknown", {e.id for e in sellers})
        listings = {f.about: f.value for f in groups["AssertFact"] if f.predicate == "catalog.listings"}
        self.assertEqual(listings, {f"seller:host:{h}": n for h, n in hosts.items()})
        self.assertEqual(sum(listings.values()) + without_host, len(items))
        calls = {f.about: f.value for f in groups["AssertFact"] if f.predicate == "catalog.l30DaysTotalCalls"}
        self.assertEqual(sum(calls.values()), sum(int(it.get("quality", {}).get("l30DaysTotalCalls") or 0) for it in items if host_of_url(it["resource"])))
        size = [f for f in groups["AssertFact"] if f.predicate == "catalogSize"][0]
        self.assertEqual(size.value["items"], len(items))
        self.assertEqual(size.value["hosts"], len(hosts))
        self.assertEqual(size.value["itemsWithoutHost"], without_host)
        self.assertEqual(size.value["totalReportedByApi"], obs.payload["pagination"]["totalReportedByApi"])
        self.assertFalse(any(e.type == "endpoint" for e in groups["UpsertEntity"]), "the snapshot never folds per endpoint")


class OtherRegistryAdapters(unittest.TestCase):
    def test_vet402_adapter(self) -> None:
        obs = observation("vet402-resolve-tienda.json")
        rows = obs.payload["endpoints"]
        events = adapter("vet402")(obs)
        groups = by_type(events)
        listed = [r for r in groups["Relate"] if r.type == "listed_on" and r.to_id == "registry:name:vet402"]
        self.assertEqual(len(listed), len(rows))
        statuses = collections.Counter(f.value for f in groups["AssertFact"] if f.predicate == "catalog_status")
        self.assertEqual(dict(statuses), dict(collections.Counter(r["catalog_status"] for r in rows)))
        self.assertEqual([f.value for f in groups["AssertFact"] if f.predicate == "vet402Endpoints"], [len(rows)])

    def test_agent402_adapter(self) -> None:
        obs = observation("agent402-index-seller-tienda.json")
        events = adapter("agent402")(obs)
        groups = by_type(events)
        facts = {f.predicate: f.value for f in groups["AssertFact"] if f.about == "seller:host:tienda.textokit.store"}
        for predicate in ("toolCount", "paidToolCount", "paywall.ok", "health", "routable", "routerDispatchReason"):
            self.assertIn(predicate, facts)
        self.assertEqual(facts["toolCount"], obs.payload["toolCount"])
        self.assertEqual(facts["paidToolCount"], obs.payload["paidToolCount"])
        self.assertEqual(facts["paywall.ok"], obs.payload["paywall"]["ok"])
        self.assertEqual(facts["routerDispatchReason"], obs.payload["routerDispatchReason"])
        tools = [r for r in groups["Relate"] if r.type == "listed_on" and r.from_id.startswith("endpoint:")]
        self.assertEqual(len(tools), len(obs.payload["tools"]))
        self.assertEqual(sum(1 for r in tools if r.attrs.get("paid")), sum(1 for t in obs.payload["tools"] if t.get("paid")))
        route = adapter("agent402")(observation("agent402-route-x402-adoption.json"))
        matched = [f for f in route if isinstance(f, AssertFact) and f.predicate == "route.matched"][0]
        self.assertEqual(matched.value["matched"], json.loads((FIXTURES / "agent402-route-x402-adoption.json").read_text(encoding="utf-8"))["matched"])

    def test_x402_trust_adapters(self) -> None:
        stats = adapter("x402_trust")(observation("x402-trust-stats.json"))
        facts = {f.predicate: f.value for f in stats if isinstance(f, AssertFact)}
        payload = load_fixture("x402-trust-stats.json")[0]
        self.assertEqual(facts["endpointsListed"], payload["endpointsListed"])
        self.assertEqual(facts["settlements30d"], payload["settlements30d"])
        self.assertIn("statsBasis", facts)
        search = adapter("x402_trust")(observation("x402-trust-search-tienda.json"))
        spayload = load_fixture("x402-trust-search-tienda.json")[0]
        sfacts = [f for f in search if isinstance(f, AssertFact) and f.about == "seller:host:tienda.textokit.store"]
        self.assertEqual({f.predicate: f.value for f in sfacts}["trustSearchTotal"], spayload["total"])
        self.assertEqual({f.predicate: f.value for f in sfacts}["trustSearchReturned"], len(spayload["results"]))
        listed = [r for r in search if isinstance(r, Relate) and r.type == "listed_on"]
        self.assertEqual(len(listed), len(spayload["results"]))
        verified = adapter("x402_trust")(observation("x402-trust-verified-p0.json"))
        vpayload = load_fixture("x402-trust-verified-p0.json")[0]
        self.assertEqual(len([r for r in verified if isinstance(r, Relate) and r.type == "verified_by"]), len(vpayload["providers"]))
        facilitators = adapter("x402_trust")(observation("x402-trust-facilitators.json"))
        fpayload = load_fixture("x402-trust-facilitators.json")[0]
        self.assertEqual(len([e for e in facilitators if isinstance(e, UpsertEntity) and e.type == "facilitator"]), len(fpayload["facilitators"]))
        self.assertEqual(adapter("x402_trust")(observation("x402-trust-activity.json")), [])

    def test_store_openapi_adapter(self) -> None:
        obs = observation("store-openapi.json")
        paid_ops = sum(1 for ops in obs.payload["paths"].values() for m, op in ops.items() if isinstance(op, dict) and "x-payment-info" in op)
        events = adapter("store_openapi")(obs)
        facts = [f for f in events if isinstance(f, AssertFact)]
        self.assertEqual([f.value for f in facts if f.predicate == "declaredPaidOps"], [paid_ops])
        self.assertEqual(len([f for f in facts if f.predicate == "declaredPrice"]), paid_ops)


class BaseRpcAdapter(unittest.TestCase):
    def test_base_rpc_adapter(self) -> None:
        receipt = observation("base-rpc-eth_getTransactionReceipt.json")
        events = adapter("base_rpc")(receipt)
        onchain = [f for f in events if isinstance(f, AssertFact) and f.predicate == "onchain_tx"]
        self.assertEqual(len(onchain), 1)
        decoded = decode_receipt(receipt.payload["result"])
        self.assertEqual(onchain[0].about, f"wallet:base:{decoded['payer']}")
        self.assertEqual(decoded["decodedFrom"], "Transfer log")
        self.assertEqual(decoded["facilitatorFrom"], receipt.payload["result"]["from"].lower())
        self.assertNotEqual(decoded["payer"], decoded["facilitatorFrom"], "tx.from is the facilitator, never the payer")
        self.assertEqual(decoded["blockNumber"], int(receipt.payload["result"]["blockNumber"], 16))
        self.assertEqual(onchain[0].source.kind, "chain")

        tx = observation("base-rpc-eth_getTransactionByHash.json")
        tx_events = adapter("base_rpc")(tx)
        self.assertEqual([f.predicate for f in tx_events if isinstance(f, AssertFact)], ["onchain_tx_input"])
        cross = decode_transfer_with_authorization(tx.payload["result"])
        self.assertEqual(cross["from"], decoded["payer"])
        self.assertEqual(cross["to"], decoded["seller"])
        self.assertEqual(cross["value"], decoded["amountAtomic"])

        balance = observation("base-rpc-eth_call-usdc-balanceOf-payer.json")
        bal_events = adapter("base_rpc")(balance)
        bal = [f for f in bal_events if isinstance(f, AssertFact)]
        self.assertEqual([(f.predicate, f.about) for f in bal], [("usdc_balance_atomic", f"wallet:base:{PAYER}")])
        self.assertEqual(bal[0].value, int(balance.payload["result"], 16))

        nonce = observation("base-rpc-eth_getTransactionCount-payer.json")
        nonce_facts = [f for f in adapter("base_rpc")(nonce) if isinstance(f, AssertFact)]
        self.assertEqual([(f.predicate, f.value) for f in nonce_facts], [("tx_count", int(nonce.payload["result"], 16))])

        self.assertEqual(adapter("base_rpc")(observation("base-rpc-batch-blockNumber-chainId.json")), [])
        hintless = IngestObservation(external_id="base_rpc:RPC eth_call x", at=CAPTURED_AT, payload=balance.payload, type="base_rpc",
                                     title="rpc_call_without_subject", source=balance.source)
        self.assertEqual(adapter("base_rpc")(hintless), [], "no wallet hint -> no fact (never guessed)")


class EndToEndFold(unittest.TestCase):
    def test_all_fixtures_fold_twice_idempotently(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="wm-adapters-"))
        clock = SampleClock(CAPTURED_AT)
        world = WorldModel(tmp, clock, "test")
        observations = [observation(name) for name in FIXTURE_REQUESTS]
        first = world.ingest(observations, adapter_or_skip)
        self.assertTrue(first.changed)
        self.assertEqual(first.quarantined, [])
        self.assertEqual(first.processed, len(observations))
        no_events = sorted(d["externalId"] for d in first.details if d["status"] == "no_events")
        self.assertEqual(len(no_events), 3, no_events)     # activity, llms.txt, rpc batch
        second = world.ingest(observations, adapter_or_skip)
        self.assertEqual(second.summary(), {"runId": first.run_id, "processed": 0, "skippedUnchanged": len(observations),
                                            "eventsAppended": 0, "changed": False})
        audit = world.audit()
        self.assertTrue(all(audit[i].seq > audit[i - 1].seq for i in range(1, len(audit))))
        counts = views.registry_counts(world, "tienda.textokit.store")
        search = load_fixture("cdp-discovery-search-urlSubstring-tienda.json")[0]
        self.assertEqual(counts["cdp_bazaar"]["listings"], len(search["resources"]))
        self.assertEqual(counts["vet402"]["endpoints"], len(load_fixture("vet402-resolve-tienda.json")[0]["endpoints"]))
        self.assertEqual(counts["agent402"]["paid_tool_count"], load_fixture("agent402-index-seller-tienda.json")[0]["paidToolCount"])
        self.assertEqual(counts["x402_trust"]["search_total"], load_fixture("x402-trust-search-tienda.json")[0]["total"])
        self.assertEqual(counts["receipts"]["pay_to"], load_fixture("store-receipts.json")[0]["seller"].lower())
        self.assertEqual(counts["cdp_bazaar"]["pay_to"], [load_fixture("store-receipts.json")[0]["seller"].lower()])
        sales = load_fixture("store-receipts.json")[0]["sales"]
        payers = views.sales_by_payer(world, "tienda.textokit.store")
        self.assertEqual(sum(p["n_sales"] for p in payers), len(sales))
        self.assertEqual(len(views.external_settlements(world, "tienda.textokit.store")), sum(1 for s in sales if not s.get("ownWallet")))
        exports = world.export(tmp / "exports")
        self.assertEqual(sorted(Path(p).name for p in exports.values()), ["world-contexts.json", "world-entities.json", "world-facts.json", "world-relationships.json"])
        world.close()
        reloaded = WorldModel(tmp, clock, "test")
        self.assertEqual(reloaded.state_shape_canonical(), world.state_shape_canonical())


if __name__ == "__main__":
    unittest.main()
