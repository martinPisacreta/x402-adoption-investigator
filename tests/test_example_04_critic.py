"""Example 04: the Critic rejects a number the fixtures cannot support, and the answer says so (SPEC.md 6.7).

examples/04-payer-dossier-unverifiable asks the payer-dossier question of examples/03 about another external payer
of the same store. Its one settlement is not in the committed RPC fixtures, so in sample mode the Verifier reports
it `unverifiable: fixture_miss` (it is verified live, see examples/live-trace/live-ex01-20261022). Nothing in the
registry or the input is written for this case: the Analyst mechanically computes `verified_tx` = 0 for the
wallet, SQL and the reducer agree on that zero (both count verified rows), and the Critic's rule C1 rejects it
because no settlement of THIS wallet was decoded: a zero over unverifiable data is missing evidence, not a
failed verification. The Writer's second round drops the claim, names it, and the verdict falls back to
`insufficient_evidence`. These tests read the committed output and re-run the input to prove both.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.chain import base_rpc  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.sources.registry import rpc_request_key  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402

FOLDER = REPO_ROOT / "examples" / "04-payer-dossier-unverifiable"
FIXTURES = REPO_ROOT / "fixtures" / "raw"


def lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class CommittedExample04(unittest.TestCase):
    """What a judge opens: the committed output, log and report."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.envelope = json.loads((FOLDER / "output.json").read_text(encoding="utf-8"))
        cls.output = cls.envelope["output"]
        cls.events = lines(FOLDER / "run.jsonl")
        cls.wallet = json.loads((FOLDER / "input.json").read_text(encoding="utf-8"))["input"]["focus"]["wallet"]

    def test_input_is_a_payer_with_one_sale_outside_the_rpc_fixtures(self) -> None:
        receipts = json.loads((FIXTURES / "store-receipts.json").read_text(encoding="utf-8"))
        sales = [s for s in receipts["sales"] if s["payer"].lower() == self.wallet]
        self.assertTrue(sales)
        self.assertTrue(all(not s["ownWallet"] for s in sales))
        requests = json.loads((FIXTURES / "MANIFEST.json").read_text(encoding="utf-8"))["requests"]
        for sale in sales:
            call = base_rpc.call_receipt(sale["transaction"])
            self.assertNotIn(rpc_request_key(call["method"], call["params"]), requests, "the receipt is not in the fixtures")
        verified_live = json.loads((REPO_ROOT / "examples" / "live-trace" / "live-ex01-20261022" / "output.json")
                                   .read_text(encoding="utf-8"))["output"]["verifications"]
        live = {v["tx"].lower(): v for v in verified_live}
        for sale in sales:                       # the same settlement decodes live: the gap is the fixtures, not the chain
            self.assertEqual(live[sale["transaction"].lower()]["status"], "verified")
            self.assertEqual(live[sale["transaction"].lower()]["payer"], self.wallet)

    def test_at_least_one_claim_is_rejected_by_a_named_rule_and_listed(self) -> None:
        self.assertEqual(self.envelope["status"], "partial")
        self.assertEqual(validate(self.envelope, load_schema("output")), [])
        rejected = {c["metric"]: c for c in self.output["rejected_claims"]}
        self.assertGreater(len(rejected), 0)
        claim = rejected["verified_tx"]
        self.assertEqual(claim["value"], 0)
        self.assertEqual(claim["subject"], f"wallet:base:{self.wallet}")
        self.assertTrue(claim["cross_checked"], "SQL and the reducer agreed on the zero; C1 rejects it anyway")
        reject = [k for k in claim["critique"] if k["verdict"] == "reject"]
        self.assertEqual(reject[0]["rule"], "C1")
        self.assertIn("rests on no decoded settlement of that subject", reject[0]["reason"])
        self.assertIn("fixture_miss", reject[0]["reason"])
        self.assertNotIn("verified_tx", {c["metric"] for c in self.output["claims"]})
        report = (FOLDER / "report.md").read_text(encoding="utf-8")
        section = report.split("## Rejected claims", 1)[1]
        self.assertIn(claim["id"], section)
        self.assertIn("C1 reject", section)

    def test_the_log_shows_the_rejection_the_revision_and_the_recoveries(self) -> None:
        rejected_id = next(c["id"] for c in self.output["rejected_claims"] if c["metric"] == "verified_tx")
        critiques = [e["payload"] for e in self.events if e["type"] == "critique"]
        self.assertTrue(any(p.get("rule") == "C1" and p.get("verdict") == "reject" and p.get("target") == rejected_id
                            and p.get("round") == 1 for p in critiques))
        handoffs = [e["payload"] for e in self.events if e["type"] == "handoff"]
        revision = [h for h in handoffs if h["from"] == "critic" and h["to"] == "writer"]
        self.assertEqual(len(revision), 1)
        self.assertGreater(revision[0]["artifact"]["count"], 0)
        recoveries = [e for e in self.events if e["type"] == "recovery"]
        self.assertTrue(any(e["agent"] == "writer" and e["payload"]["reason"] == "narrative_trimmed"
                            and any(c["claim_id"] == rejected_id and c["rule"] == "C1" for c in e["payload"].get("claims", []))
                            for e in recoveries))
        self.assertTrue(any(e["payload"]["reason"] == "fixture_miss" for e in recoveries))
        self.assertEqual(self.output["critique_rounds"], 2)
        # Round 2 re-checks every surviving claim and accepts them all: no reject, and each claim rule's accept
        # summary counts every surviving claim. What round 2 still asks of the replayed Nightshift draft (rules C3/C7
        # on the narrative, B, LLM) is answered after the last round by the trim and the completion (recoveries).
        round2 = [p for p in critiques if p.get("round") == 2]
        self.assertTrue(round2)
        self.assertFalse([p for p in round2 if p.get("verdict") == "reject"])
        summaries = {p["rule"]: p["count"] for p in round2 if "count" in p}
        for rule in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
            self.assertEqual(summaries[rule], len(self.output["claims"]), rule)
        if any(p.get("verdict") == "revise" for p in round2):
            reasons = [e["payload"]["reason"] for e in recoveries if e["agent"] == "writer"]
            self.assertIn("narrative_completed", reasons)

    def test_the_final_answer_stays_honest(self) -> None:
        answer = self.output["answer"]
        rejected_id = next(c["id"] for c in self.output["rejected_claims"] if c["metric"] == "verified_tx")
        self.assertNotIn(f"[{rejected_id}]", answer)
        self.assertIn("verified_tx (rule C1)", answer)
        self.assertEqual(self.output["verdict"]["label"], "insufficient_evidence")
        self.assertTrue(answer.startswith("There is not enough accepted evidence to answer"))
        unavailable = {u["metric"] for u in self.output["unavailable"]}
        self.assertTrue({"usdc_balance_atomic", "tx_count"} <= unavailable)
        for claim in self.output["claims"]:
            self.assertTrue(claim["cross_checked"], claim["metric"])
            self.assertIn(f"[{claim['id']}]", answer)
        # The seller-level count keeps its own decoded settlement (another payer's) and stays accepted.
        accepted = {c["metric"]: c for c in self.output["claims"]}
        self.assertEqual(accepted["settlements_verified"]["value"], 1)
        tx_evidence = [e for e in accepted["payer_sales_total"]["evidence"] if e["kind"] == "tx"]
        self.assertEqual(tx_evidence, [])


class FreshRunOfExample04(unittest.TestCase):
    def test_sample_run_reproduces_the_rejection_offline(self) -> None:
        payload = json.loads((FOLDER / "input.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            envelope = run_investigation(payload, config, echo_stdout=False)
            first = lines(Path(tmp) / envelope["run_id"] / "run.jsonl")[0]
        self.assertEqual(first["type"], "guard")
        self.assertEqual(envelope["status"], "partial")
        self.assertEqual([c["metric"] for c in envelope["output"]["rejected_claims"]], ["verified_tx"])


if __name__ == "__main__":
    unittest.main()
