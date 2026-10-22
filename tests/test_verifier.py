"""Verifier: deterministic tx sample, Transfer-log verification against the RPC fixtures, decode mismatches as
Conflicts, cross-check events, chain facts and the settlements export (SPEC.md 6.4). Integrator-owned.

Runs the real Collector + Verifier on example 03 in sample mode (fixtures only, network guard installed) so
the assertions cover the exact code path the committed examples use.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import guard, runner  # noqa: E402
from investigator.agents import collector, planner, verifier  # noqa: E402
from investigator.config import build_config, parse_request  # noqa: E402
from investigator.contracts import to_json  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"
COVERED_TX = "0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3"
PAYER = "0x097e3f339d0b023605e12a6b81e2d6cb7571475a"


def receipts() -> dict:
    return json.loads((FIXTURES / "store-receipts.json").read_text(encoding="utf-8"))


def sample_context(tmp: str, example: str):
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
    payload = json.loads((REPO_ROOT / "examples" / example).read_text(encoding="utf-8"))
    run_id, inp = parse_request(payload)
    ctx = runner.build_context(run_id, inp, config, echo_stdout=False)
    ctx.start_ms = ctx.clock.now_ms()
    ctx.start_iso = ctx.clock.start_iso
    ctx.deadline_ms = ctx.start_ms + inp.budget.max_seconds * 1000
    return ctx


class TxSample(unittest.TestCase):
    def test_newest_external_sale_per_payer_focus_first(self) -> None:
        sales = receipts()["sales"]
        external_payers = {s["payer"].lower() for s in sales if not s["ownWallet"]}
        sample = verifier.select_tx_sample(sales, None, 5)
        self.assertEqual(len(sample), min(5, len(external_payers)))
        self.assertEqual(len({s["payer"].lower() for s in sample}), len(sample), "one tx per distinct payer")
        self.assertTrue(all(not s["ownWallet"] for s in sample))
        ats = [s["at"] for s in sample]
        self.assertEqual(ats, sorted(ats, reverse=True), "newest first")
        for s in sample:   # the newest sale of that payer
            newest = max(x["at"] for x in sales if x["payer"].lower() == s["payer"].lower() and not x["ownWallet"])
            self.assertEqual(s["at"], newest)
        focused = verifier.select_tx_sample(sales, PAYER, 5)
        self.assertEqual(focused[0]["payer"].lower(), PAYER)
        self.assertEqual(verifier.select_tx_sample(sales, None, 2).__len__(), 2)
        self.assertEqual(verifier.select_tx_sample([], None, 5), [])
        self.assertEqual(verifier.select_tx_sample(sales, None, 0), [])

    def test_top_external_payer(self) -> None:
        sales = receipts()["sales"]
        counts: dict[str, int] = {}
        for s in sales:
            if not s["ownWallet"]:
                counts[s["payer"].lower()] = counts.get(s["payer"].lower(), 0) + 1
        self.assertEqual(verifier.top_external_payer(sales), max(counts, key=counts.get))
        self.assertIsNone(verifier.top_external_payer([]))


class SampleModeVerification(unittest.TestCase):
    def tearDown(self) -> None:
        guard.uninstall_network_guard()

    def _run(self, tmp: str, example: str = "ex3.json", mutate=None):
        ctx = sample_context(tmp, example)
        try:
            plan = planner.run_planner(ctx).payload
            observations = collector.run_collector(ctx, plan).payload
            if mutate is not None:
                mutate(observations)
            result = verifier.run_verifier(ctx, plan, observations)
        finally:
            runner._cleanup(ctx)      # close the World Model / H-MEM / executor handles (Windows temp cleanup)
            ctx.log.close()
        return ctx, plan, observations, result

    def test_covered_tx_is_verified_and_the_rest_are_honest_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, plan, observations, result = self._run(tmp)
            self.assertEqual(result.status, "ok", result.notes)
            payload = result.payload
            by_tx = {v.tx: v for v in payload["verifications"]}
            self.assertIn(COVERED_TX, by_tx)
            verified = by_tx[COVERED_TX]
            self.assertEqual(verified.status, "verified")
            self.assertTrue(verified.receipt_match)
            self.assertEqual(verified.payer, PAYER)
            self.assertEqual(verified.decoded_from, "Transfer log")
            self.assertNotEqual(verified.facilitator_from, verified.payer)
            self.assertEqual(verified.basescan_url, f"https://basescan.org/tx/{COVERED_TX}")
            others = [v for v in payload["verifications"] if v.tx != COVERED_TX]
            self.assertTrue(others)
            self.assertTrue(all(v.status == "unverifiable" and v.reason == "fixture_miss" for v in others))
            # chain facts: onchain_tx for the verified settlement + balance and nonce of the focus wallet
            predicates = sorted(f["predicate"] for f in payload["chain_facts"])
            self.assertEqual(predicates, ["onchain_tx", "tx_count", "usdc_balance_atomic"])
            for fact in payload["chain_facts"]:
                self.assertEqual(fact["about"], f"wallet:base:{PAYER}")
                self.assertTrue(fact["url"])
                self.assertTrue(str(fact["checksum"]).startswith("sha256:"))
            balance = json.loads((FIXTURES / "base-rpc-eth_call-usdc-balanceOf-payer.json").read_text(encoding="utf-8"))["result"]
            nonce = json.loads((FIXTURES / "base-rpc-eth_getTransactionCount-payer.json").read_text(encoding="utf-8"))["result"]
            values = {f["predicate"]: f["value"] for f in payload["chain_facts"]}
            self.assertEqual(values["usdc_balance_atomic"], int(balance, 16))
            self.assertEqual(values["tx_count"], int(nonce, 16))
            # settlements export: one decoded row, the shape the `settlements` view reads
            rows = json.loads((ctx.exports_dir / "settlements.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(set(rows[0]), {"tx_hash", "payer", "seller", "amount_atomic", "block_number", "block_time",
                                            "facilitator_from", "status"})
            self.assertEqual(rows[0]["status"], "verified")
            # every RPC element was recorded as an observation with its own fetch event; misses are visible
            rpc = payload["observations"]
            self.assertTrue(all(o.family == "base_rpc" and o.replayed for o in rpc))
            self.assertTrue(any(o.status == "fixture_miss" for o in rpc))
            lines = [json.loads(l) for l in (Path(tmp) / ctx.run_id / "run.jsonl").read_text(encoding="utf-8").splitlines()]
            types = [l["type"] for l in lines]
            self.assertIn("verify", types)
            self.assertIn("crosscheck", types)
            self.assertTrue(any(l["type"] == "recovery" and l["payload"]["reason"] == "fixture_miss" for l in lines))
            schema = load_schema("events")
            for line in lines:
                self.assertEqual(validate(line, schema), [], line)
            self.assertEqual(ctx.counters["tx_verifications"], len(payload["verifications"]))
            self.assertTrue(guard.is_installed())

    def test_decode_mismatch_is_failed_with_a_conflict(self) -> None:
        def tamper(observations):
            for obs in observations:
                if obs.name == "store_receipts":
                    for sale in obs.payload["sales"]:
                        if sale["transaction"].lower() == COVERED_TX:
                            sale["amountAtomic"] = str(int(sale["amountAtomic"]) + 1)

        with tempfile.TemporaryDirectory() as tmp:
            ctx, plan, observations, result = self._run(tmp, mutate=tamper)
            failed = next(v for v in result.payload["verifications"] if v.tx == COVERED_TX)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.reason, "decode_mismatch:amount")
            self.assertFalse(failed.receipt_match)
            conflicts = result.payload["conflicts"]
            self.assertTrue(any(k.predicate == "settlement_amount" for k in conflicts))
            conflict = next(k for k in conflicts if k.predicate == "settlement_amount")
            self.assertEqual(conflict.id, "k01")
            sources = {v.source: v.value for v in conflict.values}
            self.assertEqual(sources["base_rpc"] + 1, sources["receipts"])
            rows = json.loads((ctx.exports_dir / "settlements.json").read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["status"], "failed")
            self.assertEqual(validate(to_json(conflict), load_schema("output")["$defs"]["conflict"]), [])

    def test_no_receipts_means_nothing_to_verify_and_no_rpc_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, plan, observations, result = self._run(tmp, example="ex2.json")
            self.assertEqual(result.status, "ok")
            self.assertEqual(result.payload["verifications"], [])
            self.assertEqual(result.payload["chain_facts"], [])
            self.assertEqual(result.payload["observations"], [])
            self.assertFalse((ctx.exports_dir / "settlements.json").exists())
            self.assertFalse(any(n.startswith("stub:") for n in result.notes))

    def test_cross_checks_name_every_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx, plan, observations, result = self._run(tmp, example="ex1.json")
            names = {c["name"] for c in result.payload["crosschecks"]}
            self.assertIn("receipts_seller_vs_bazaar_payto", names)
            self.assertIn("bazaar_calls_30d_vs_receipts_30d", names)
            self.assertTrue({n for n in names if n.startswith("endpoint_count_")})
            seller_check = next(c for c in result.payload["crosschecks"] if c["name"] == "receipts_seller_vs_bazaar_payto")
            self.assertTrue(seller_check["agree"])
            self.assertEqual(seller_check["a"]["value"], receipts()["seller"].lower())
            self.assertEqual(result.payload["conflicts"], [], "the fixtures agree on the seller wallet")


if __name__ == "__main__":
    unittest.main()
