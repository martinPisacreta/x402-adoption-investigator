"""End-to-end pipeline tests for the agents builder (B4): Planner -> Collector (B1) -> Keeper (B2) -> Verifier
(double until B1 delivers it) -> Analyst -> Writer -> Critic loop -> finalize, in sample mode over the committed
fixtures with the real SQLite view layer (B3). Every expected number is read from the fixture files, never typed
as a constant (SPEC.md invariant I1).

The generator is pinned to NullGenerator: these tests cover the rules-and-templates path (what a live run without
any generator, or a sample run of an input with no recorded transcripts, executes). The committed examples replay
recorded Meterless Nightshift transcripts (fixtures/transcripts/<example>/); that path is covered by
tests/test_examples_reproducible.py, tests/test_example_04_critic.py, tests/test_retry.py and tests/test_nightshift.py.
"""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.agents import verifier as verifier_mod  # noqa: E402
from investigator.agents.metrics import METRICS  # noqa: E402
from investigator.agents.pitfalls import load_memory_file, wanted_entities  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.generator import NullGenerator  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402
from tests import test_agents_doubles as doubles  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"


def verifier_is_scaffold() -> bool:
    try:
        verifier_mod.select_tx_sample([], None, 1)
    except NotImplementedError:
        return True
    except Exception:  # noqa: BLE001 - a real implementation may reject the empty input differently
        return False
    return False


def sample_config(out_dir: Path):
    return build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out_dir)})(), load_dotenv=False)


def run_example(folder: str, out_dir: Path, collector=None, verifier=None, executor=None) -> dict:
    payload = json.loads((REPO_ROOT / "examples" / folder / "input.json").read_text(encoding="utf-8"))
    patches = [mock.patch.object(runner, "build_generator", lambda *args, **kwargs: NullGenerator())]
    if collector is not None:
        patches.append(mock.patch.object(runner.collector, "run_collector", collector))
    if verifier is not None or verifier_is_scaffold():
        patches.append(mock.patch.object(runner.verifier, "run_verifier", verifier or doubles.run_verifier))
    if executor is not None:
        patches.append(mock.patch.object(runner, "build_executor", executor))
    for p in patches:
        p.start()
    try:
        return runner.run(payload, sample_config(out_dir), echo_stdout=False)
    finally:
        for p in reversed(patches):
            p.stop()


def receipts() -> dict:
    return json.loads((FIXTURES / "store-receipts.json").read_text(encoding="utf-8"))


def read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_claims_carry_policy_caveats(test: unittest.TestCase, claims: list[dict]) -> None:
    """Every pitfall memory whose entities touch a claim (fields, sources, metric id) must have its caveat on the
    claim with the memory id behind it: the Analyst applies the H-MEM policy index and the Critic re-checks it."""
    memories = [m for m in load_memory_file(REPO_ROOT / "memory" / "seed" / "memories.jsonl") if "pitfall" in m.tags]
    for claim in claims:
        derivation = claim["derivation"] or {}
        wanted = wanted_entities(derivation.get("fields", []), derivation.get("sources", []), [claim["metric"]])
        for memory in memories:
            if not wanted.intersection(memory.entity_names()):
                continue
            for code in memory.caveat_codes():
                test.assertIn(code, claim["caveats"], f"{claim['id']} ({claim['metric']}) lacks {code} required by {memory.id}")
                test.assertEqual(claim["caveat_memory_ids"].get(code), memory.id, f"{claim['id']} caveat {code}")


class SellerAuditPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out_dir = Path(cls.tmp.name)
        cls.envelope = run_example("01-seller-audit-textokit", cls.out_dir)
        cls.run_dir = cls.out_dir / "ex01-seller-audit"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def claims(self) -> dict[str, dict]:
        return {c["metric"]: c for c in self.envelope["output"]["claims"]}

    def test_status_and_schema(self) -> None:
        self.assertIn(self.envelope["status"], ("ok", "partial"), self.envelope.get("error"))
        self.assertEqual(validate(self.envelope, load_schema("output")), [])
        out = self.envelope["output"]
        self.assertEqual(list(out.keys())[0], "mounts")
        self.assertIn("sample_mode_notice", out)
        self.assertEqual(out["plan"]["playbook"], "seller_profile")
        self.assertIn("receipts", out["plan"]["sources"])
        self.assertIn("base_rpc", out["plan"]["sources"])

    def test_claims_match_the_receipts_fixture(self) -> None:
        data = receipts()
        sales = data["sales"]
        own = [s for s in sales if s.get("ownWallet")]
        external = [s for s in sales if not s.get("ownWallet")]
        claims = self.claims()
        self.assertEqual(claims["sales_total"]["value"], len(sales))
        self.assertEqual(claims["own_wallet_sales"]["value"], len(own))
        self.assertEqual(claims["external_sales_total"]["value"], len(external))
        self.assertEqual(claims["external_usdc_atomic"]["value"], sum(int(s["amountAtomic"]) for s in external))
        self.assertEqual(claims["external_payers_total"]["value"], len({s["payer"].lower() for s in external}))
        share = claims["self_settlement_share"]["value"]
        self.assertEqual((share["numerator"], share["denominator"]), (len(own), len(sales)))
        for metric in ("sales_total", "self_settlement_share", "top_payer_share", "external_sales_by_service", "external_window"):
            self.assertTrue(claims[metric]["cross_checked"], metric)
            self.assertEqual(claims[metric]["derivation"]["backend"], "sqlite")
            self.assertTrue(any(e["kind"] == "url" and e["replayed"] for e in claims[metric]["evidence"]), metric)
            self.assertTrue(any(e["kind"] == "log_line" for e in claims[metric]["evidence"]), metric)
            self.assertEqual(claims[metric]["confidence"], 0.9)

    def test_registry_counts_and_conflict(self) -> None:
        openapi = json.loads((FIXTURES / "store-openapi.json").read_text(encoding="utf-8"))
        paid_ops = sum(1 for ops in openapi["paths"].values() for op in ops.values() if isinstance(op, dict) and "x-payment-info" in op)
        search = json.loads((FIXTURES / "cdp-discovery-search-urlSubstring-tienda.json").read_text(encoding="utf-8"))
        listings = sum(1 for r in search["resources"] if "tienda.textokit.store" in r["resource"])
        vet = json.loads((FIXTURES / "vet402-resolve-tienda.json").read_text(encoding="utf-8"))
        trust = json.loads((FIXTURES / "x402-trust-search-tienda.json").read_text(encoding="utf-8"))
        agent = json.loads((FIXTURES / "agent402-index-seller-tienda.json").read_text(encoding="utf-8"))
        claims = self.claims()
        value = claims["endpoint_count_for_host"]["value"]
        self.assertEqual(value["store_openapi"], paid_ops)
        self.assertEqual(value["cdp_search"], listings)
        self.assertEqual(value["vet402"], sum(1 for e in vet["endpoints"] if e["catalog_status"] == "listed"))
        self.assertEqual(value["x402_trust_search"], trust["total"])
        self.assertEqual(value["agent402_paid"], agent["paidToolCount"])
        self.assertTrue(claims["endpoint_count_for_host"]["cross_checked"])
        conflicts = {k["predicate"]: k for k in self.envelope["output"]["conflicts"]}
        self.assertIn("endpoint_count_for_host", conflicts)
        self.assertEqual(len(conflicts["endpoint_count_for_host"]["values"]), 5)
        self.assertIn(conflicts["endpoint_count_for_host"]["id"], claims["endpoint_count_for_host"]["conflict_ids"])
        self.assertIn("calls_30d_for_host", conflicts)
        self.assertTrue(conflicts["calls_30d_for_host"]["explanation"])
        self.assertEqual(claims["bazaar_listings_for_host"]["coverage"]["basis"], "search capped at 20")

    def test_endpoint_conflict_names_the_routes_behind_vet402_vs_openapi(self) -> None:
        """The explanation is computed from the fixtures: the routes vet402 lists that the openapi does not declare."""
        from urllib.parse import urlsplit
        openapi = json.loads((FIXTURES / "store-openapi.json").read_text(encoding="utf-8"))
        declared = {p for p, ops in openapi["paths"].items() for op in ops.values() if isinstance(op, dict) and "x-payment-info" in op}
        vet = json.loads((FIXTURES / "vet402-resolve-tienda.json").read_text(encoding="utf-8"))
        listed = [e for e in vet["endpoints"] if e["catalog_status"] == "listed"]
        extra = sorted({urlsplit(e["canonical_url"]).path for e in listed} - declared)
        self.assertTrue(extra, "the fixtures carry routes only vet402 lists")
        self.assertEqual(len(listed) - len(declared), len(extra))
        explanation = {k["predicate"]: k for k in self.envelope["output"]["conflicts"]}["endpoint_count_for_host"]["explanation"]
        for route in extra:
            self.assertIn(route, explanation)
        self.assertIn("accounts for the whole difference", explanation)
        self.assertNotIn("delisted", explanation.split("x402-trust")[0], "every vet402 row in the fixtures is listed")

    def test_verdict_from_rules(self) -> None:
        verdict = self.envelope["output"]["verdict"]
        data = receipts()
        own = sum(1 for s in data["sales"] if s.get("ownWallet"))
        expected = "self_settled_mostly" if own / len(data["sales"]) >= 0.5 else "adoption_thin"
        self.assertEqual(verdict["label"], expected)
        self.assertTrue(verdict["why"])

    def test_critic_loop_is_visible_in_the_log(self) -> None:
        lines = read_lines(self.run_dir / "run.jsonl")
        types = [l["type"] for l in lines]
        self.assertEqual(types[0], "guard")
        self.assertEqual(types[1], "run_start")
        self.assertEqual(types[-1], "result")
        for required in ("plan", "handoff", "fetch", "ingest", "verify", "sql", "claim", "conflict", "critique",
                         "narrative_map", "memory", "recovery"):
            self.assertIn(required, types, required)
        self.assertNotIn("retry", types, "example 01 has no fault script (fixtures/transient/): nothing is retried")
        self.assertEqual(types.count("ingest"), 2)
        critiques = [l for l in lines if l["type"] == "critique"]
        # The Analyst attaches every caveat the policy memories require, so round 1 is clean: the Critic logs one
        # accept summary per rule (with the claim count) and no revise/reject, and the loop ends after one round.
        self.assertFalse([c for c in critiques if c["payload"].get("verdict") in ("revise", "reject")])
        n_claims = len(self.envelope["output"]["claims"])
        summaries = {c["payload"]["rule"]: c["payload"]["count"] for c in critiques if "count" in c["payload"]}
        for rule in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
            self.assertEqual(summaries[rule], n_claims, rule)
        self.assertEqual(self.envelope["output"]["critique_rounds"], 1)
        handoffs = [(l["payload"]["from"], l["payload"]["to"]) for l in lines if l["type"] == "handoff"]
        self.assertIn(("writer", "critic"), handoffs)
        assert_claims_carry_policy_caveats(self, self.envelope["output"]["claims"])
        self.assertEqual([l["seq"] for l in lines], list(range(1, len(lines) + 1)))
        schema = load_schema("events")
        for line in lines:
            self.assertEqual(validate(line, schema), [], line)
        narrative = [l for l in lines if l["type"] == "narrative_map"][-1]
        self.assertEqual(narrative["payload"]["unsourced"], [])
        self.assertTrue(narrative["payload"]["tokens"])
        self.assertTrue(all(t["kind"] is not None for t in narrative["payload"]["tokens"]))
        plan_event = next(l for l in lines if l["type"] == "plan")
        fetch_events = [l for l in lines if l["type"] == "fetch"]
        self.assertLess(lines.index(plan_event), lines.index(fetch_events[0]))

    def test_settlement_verified_and_caveats(self) -> None:
        verifications = self.envelope["output"]["verifications"]
        self.assertTrue(any(v["status"] == "verified" for v in verifications))
        self.assertTrue(any(v["status"] == "unverifiable" and v["reason"] == "fixture_miss" for v in verifications))
        claims = self.claims()
        self.assertEqual(claims["settlements_verified"]["value"], sum(1 for v in verifications if v["status"] == "verified"))
        self.assertIn("payer_from_transfer_log", claims["settlements_verified"]["caveats"])
        # self_reported_receipts on settlements_verified comes from memory (mem-seed-003 touches source:receipts),
        # not from the registry: the visible effect of H-MEM on the output.
        self.assertIn("self_reported_receipts", claims["settlements_verified"]["caveats"])
        self.assertNotIn("self_reported_receipts", METRICS["settlements_verified"].caveat_rules)
        self.assertEqual(claims["settlements_verified"]["caveat_memory_ids"]["self_reported_receipts"], "mem-seed-003")
        self.assertTrue(any(k["rule"] == "A" and k["verdict"] == "accept" for k in claims["settlements_verified"]["critique"]))
        self.assertTrue(any(e["kind"] == "tx" and e["verified"] for e in claims["settlements_verified"]["evidence"]))
        for claim in self.envelope["output"]["claims"]:
            self.assertEqual(claim["status"], "accepted")
            for code in claim["caveats"]:
                self.assertIn(code, claim["caveat_memory_ids"], f"{claim['id']} caveat {code} without memory id")

    def test_world_and_memory_sections(self) -> None:
        world = self.envelope["output"]["world"]
        self.assertEqual(world["second_pass"]["eventsAppended"], 0)
        self.assertFalse(world["second_pass"]["changed"])
        self.assertEqual(world["claims_asserted"], len(self.envelope["output"]["claims"]))
        memory = self.envelope["output"]["memory"]
        self.assertTrue(memory["feedback"])
        self.assertTrue(all(fb["kind"] in ("helpful", "not_helpful", "wrong") for fb in memory["feedback"]))
        self.assertIn("mem-seed-003", memory["cited"])
        self.assertGreater(memory["mined"], 0)
        self.assertTrue((self.run_dir / "hmem.trace.jsonl").is_file())

    def test_report_and_side_files(self) -> None:
        self.assertTrue((self.run_dir / "report.md").is_file())
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("Where sources disagree", report)
        self.assertIn("endpoint_count_for_host", report)
        sql_lines = read_lines(self.run_dir / "sql.jsonl")
        self.assertTrue(sql_lines)
        for line in sql_lines:
            for key in ("seq", "query_id", "executor", "sql", "sql_sha256", "params", "headers", "rowcount", "rows", "time_used_ms", "tables"):
                self.assertIn(key, line)
        self.assertEqual(self.envelope["output"]["report_path"], "logs/ex01-seller-audit/report.md")
        # transcripts are written only when a generator ran; this sample run mounts none
        self.assertEqual(self.envelope["output"]["mounts"]["generator"], "null")
        self.assertFalse((self.run_dir / "transcripts").exists())


class CatalogPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out_dir = Path(cls.tmp.name)
        cls.envelope = run_example("02-catalog-concentration", cls.out_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_catalog_claims_with_coverage(self) -> None:
        self.assertEqual(self.envelope["status"], "ok", self.envelope.get("error"))
        snapshot = json.loads(gzip.decompress((FIXTURES / "cdp-discovery-catalog-compact.json.gz").read_bytes()).decode("utf-8"))
        items = snapshot["items"]
        claims = {c["metric"]: c for c in self.envelope["output"]["claims"]}
        self.assertEqual(claims["catalog_resources"]["value"], len(items))
        self.assertEqual(claims["catalog_calls_30d_sum"]["value"], sum(int((i.get("quality") or {}).get("l30DaysTotalCalls") or 0) for i in items))
        self.assertEqual(claims["catalog_active_share"]["value"]["numerator"],
                         sum(1 for i in items if int((i.get("quality") or {}).get("l30DaysTotalCalls") or 0) > 0))
        for metric in ("catalog_resources", "catalog_hosts", "catalog_distinct_payto", "catalog_calls_30d_sum", "catalog_active_share",
                       "top_host_calls_share", "top10_hosts_calls_share", "top_payto_calls_share", "trust_ecosystem_stats",
                       "top_hosts_by_listings", "unique_payers_per_endpoint_max"):
            self.assertIn(metric, claims)
            self.assertTrue(claims[metric]["cross_checked"], metric)
        # Every accepted claim was computed twice (committed SQL + reducer) and the two agreed.
        for claim in self.envelope["output"]["claims"]:
            self.assertTrue(claim["cross_checked"], claim["metric"])
            self.assertEqual(claim["derivation"]["backend"], "sqlite", claim["metric"])
        payers_max = max(int((i.get("quality") or {}).get("l30DaysUniquePayers") or 0) for i in items)
        self.assertEqual(claims["unique_payers_per_endpoint_max"]["value"], payers_max)
        self.assertEqual(claims["unique_payers_per_endpoint_max"]["derivation"]["query_id"], "q20_catalog_unique_payers_max")
        self.assertEqual(claims["top_hosts_by_listings"]["derivation"]["query_id"], "q21_catalog_top_hosts_by_listings")
        for claim in self.envelope["output"]["claims"]:
            if claim["subject"].startswith("catalog:"):
                self.assertIsNotNone(claim["coverage"], claim["metric"])
                self.assertEqual(claim["coverage"]["items_seen"], len(items))
                self.assertEqual(claim["coverage"]["total_reported"], snapshot["pagination"]["totalReportedByApi"])
                self.assertIn("snapshot_dated", claim["caveats"])
        self.assertEqual(self.envelope["output"]["rejected_claims"], [])
        # No registry metric sums the per-endpoint unique-payer counter; the one that reports it keeps the max and
        # carries the pitfall memory's caveat (the C4 rule itself is proven in tests/test_critic.py).
        self.assertFalse([m.id for m in METRICS.values() if "l30DaysUniquePayers" in m.sums])
        self.assertEqual(claims["unique_payers_per_endpoint_max"]["caveat_memory_ids"]["unique_payers_not_dedupable"], "mem-seed-001")
        self.assertEqual(self.envelope["output"]["critique_rounds"], 1)
        assert_claims_carry_policy_caveats(self, self.envelope["output"]["claims"])
        verdict = self.envelope["output"]["verdict"]
        top10 = claims["top10_hosts_calls_share"]["value"]["share"]
        self.assertEqual(verdict["label"], "concentrated" if top10 >= 0.5 else "diffuse")
        conflicts = {k["predicate"]: k for k in self.envelope["output"]["conflicts"]}
        self.assertIn("ecosystem_size", conflicts)
        self.assertGreaterEqual(len(conflicts["ecosystem_size"]["values"]), 3)
        self.assertEqual(self.envelope["output"]["unavailable"], [])


class PayerDossierPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out_dir = Path(cls.tmp.name)
        cls.envelope = run_example("03-payer-dossier-onchain", cls.out_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_payer_claims_and_chain_facts(self) -> None:
        self.assertIn(self.envelope["status"], ("ok", "partial"), self.envelope.get("error"))
        wallet = "0x097e3f339d0b023605e12a6b81e2d6cb7571475a"
        sales = [s for s in receipts()["sales"] if s["payer"].lower() == wallet]
        claims = {c["metric"]: c for c in self.envelope["output"]["claims"]}
        self.assertEqual(claims["payer_sales_total"]["value"], len(sales))
        self.assertEqual(claims["payer_usdc_atomic"]["value"], sum(int(s["amountAtomic"]) for s in sales))
        balance = json.loads((FIXTURES / "base-rpc-eth_call-usdc-balanceOf-payer.json").read_text(encoding="utf-8"))["result"]
        nonce = json.loads((FIXTURES / "base-rpc-eth_getTransactionCount-payer.json").read_text(encoding="utf-8"))["result"]
        self.assertEqual(claims["usdc_balance_atomic"]["value"], int(balance, 16))
        self.assertEqual(claims["tx_count"]["value"], int(nonce, 16))
        for metric in ("usdc_balance_atomic", "tx_count"):
            # SQL decodes the RPC hex in the view layer; the reducer reads the Verifier's decoding: two computations.
            self.assertTrue(claims[metric]["cross_checked"], metric)
            self.assertEqual(claims[metric]["derivation"]["query_id"], "q22_payer_chain_facts")
            self.assertEqual(claims[metric]["derivation"]["backend"], "sqlite")
        for claim in self.envelope["output"]["claims"]:
            self.assertTrue(claim["cross_checked"], claim["metric"])
        self.assertEqual(claims["verified_tx"]["value"], 1)
        self.assertEqual(self.envelope["output"]["verdict"]["label"], "adoption_thin")
        self.assertEqual(self.envelope["output"]["rejected_claims"], [])
        assert_claims_carry_policy_caveats(self, self.envelope["output"]["claims"])


class DegradedPaths(unittest.TestCase):
    def test_unavailable_source_is_visible_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def collector_down(ctx, plan):
                return doubles.run_collector(ctx, plan, unavailable=("cdp_search_host", "vet402_resolve"))
            envelope = run_example("01-seller-audit-textokit", Path(tmp), collector=collector_down, verifier=doubles.run_verifier)
            self.assertEqual(envelope["status"], "partial", envelope.get("error"))
            metrics_unavailable = {u["metric"] for u in envelope["output"]["unavailable"]}
            self.assertIn("bazaar_listings_for_host", metrics_unavailable)
            self.assertIn("bazaar_calls_30d_for_host", metrics_unavailable)
            claims = {c["metric"] for c in envelope["output"]["claims"]}
            self.assertNotIn("bazaar_listings_for_host", claims)
            self.assertIn("sales_total", claims)
            sources = {s["name"]: s for s in envelope["output"]["sources"]}
            self.assertEqual(sources["cdp_search_host"]["status"], "unavailable")

    def test_verifier_without_chain_access_leaves_chain_metrics_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            envelope = run_example("03-payer-dossier-onchain", Path(tmp),
                                   verifier=lambda ctx, plan, observations: runner.AgentResult("verifier", "ok", verifier_mod.empty_payload(),
                                                                                              ["rpc unavailable (simulated)"]))
            self.assertEqual(envelope["status"], "partial", envelope.get("error"))
            unavailable = {u["metric"]: u["reason"] for u in envelope["output"]["unavailable"]}
            for metric in ("verified_tx", "usdc_balance_atomic", "tx_count"):
                self.assertIn(metric, unavailable)
            claims = {c["metric"] for c in envelope["output"]["claims"]}
            self.assertNotIn("verified_tx", claims)
            self.assertIn("payer_sales_total", claims)
            self.assertEqual(envelope["output"]["verdict"]["label"], "insufficient_evidence")

    def test_every_source_down_is_e_no_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            def collector_down(ctx, plan):
                return doubles.run_collector(ctx, plan, unavailable=tuple(it["name"] for it in plan.fetch_list))
            envelope = run_example("01-seller-audit-textokit", Path(tmp), collector=collector_down, verifier=doubles.run_verifier)
            self.assertEqual(envelope["status"], "error")
            self.assertEqual(envelope["error"]["code"], "E_NO_SOURCES")
            self.assertEqual(validate(envelope, load_schema("output")), [])

    def test_executor_unavailable_falls_back_to_sqlite_and_still_computes_twice(self) -> None:
        class BrokenExecutor(doubles.DoubleSqliteExecutor):
            def ensure_views(self, exports, urls=None):
                raise NotImplementedError("B3 not merged")

        with tempfile.TemporaryDirectory() as tmp:
            envelope = run_example("01-seller-audit-textokit", Path(tmp), verifier=doubles.run_verifier,
                                   executor=lambda config, run_dir, log, run_id="", **kw: BrokenExecutor(run_dir, log))
            self.assertEqual(envelope["status"], "partial", envelope.get("error"))
            claims = {c["metric"]: c for c in envelope["output"]["claims"]}
            self.assertEqual(claims["sales_total"]["value"], len(receipts()["sales"]))
            for claim in envelope["output"]["claims"]:
                self.assertTrue(claim["cross_checked"], claim["metric"])
                self.assertEqual(claim["derivation"]["backend"], "sqlite-fallback", claim["metric"])
            self.assertEqual(envelope["output"]["rejected_claims"], [])
            recoveries = [l["payload"] for l in read_lines(Path(tmp) / "ex01-seller-audit" / "run.jsonl") if l["type"] == "recovery"]
            self.assertTrue(any(r["reason"] == "zetaris_query_fallback" and r.get("to") == "sqlite" for r in recoveries))

    def test_timeout_before_any_claim_is_e_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(runner.RunContext, "time_left_ms", lambda self: -1):
                envelope = run_example("01-seller-audit-textokit", Path(tmp), verifier=doubles.run_verifier)
            self.assertEqual(envelope["status"], "error")
            self.assertEqual(envelope["error"]["code"], "E_TIMEOUT")

    def test_deterministic_output_between_two_sample_runs(self) -> None:
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = run_example("01-seller-audit-textokit", Path(a), verifier=doubles.run_verifier)
            second = run_example("01-seller-audit-textokit", Path(b), verifier=doubles.run_verifier)
            self.assertEqual((Path(a) / "ex01-seller-audit" / "output.json").read_bytes(),
                             (Path(b) / "ex01-seller-audit" / "output.json").read_bytes())
            self.assertEqual((Path(a) / "ex01-seller-audit" / "run.jsonl").read_bytes(),
                             (Path(b) / "ex01-seller-audit" / "run.jsonl").read_bytes())
            self.assertEqual(first["status"], second["status"])


if __name__ == "__main__":
    unittest.main()
