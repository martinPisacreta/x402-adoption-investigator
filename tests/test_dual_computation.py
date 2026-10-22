""""Every metric is computed twice and a claim survives only if both agree" as behaviour, not as a sentence.

Each registry metric has a committed SQL query and a pure-Python reducer (tests/test_sql_dual_backend.py checks
the registry). These tests run the real sample pipeline and break one half on purpose:
- a reducer that disagrees with its SQL query -> an open Conflict <metric>:backend_disagreement, the claim
  rejected by the Critic's rule C2, the Writer's second round leaving it out and naming it, status partial;
- an SQL budget too small for the plan -> the metrics whose query never ran have one value only, and C2
  rejects every one of them instead of reporting a single-computed number;
- no SQL executor at all -> nothing survives, and the run is an error rather than a reducer-only answer.
Expected values are read from the fixtures, never typed here (SPEC.md invariant I1).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.agents import reducers  # noqa: E402
from investigator.agents.metrics import METRICS  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402
from investigator.zetaris import executor as executor_mod  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"


def example_payload(folder: str = "01-seller-audit-textokit") -> dict:
    return json.loads((REPO_ROOT / "examples" / folder / "input.json").read_text(encoding="utf-8"))


def run_sample(payload: dict, out_dir: Path) -> dict:
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out_dir)})(), load_dotenv=False)
    return runner.run(payload, config, echo_stdout=False)


def events(out_dir: Path, run_id: str) -> list[dict]:
    path = out_dir / run_id / "run.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def n_sales() -> int:
    return len(json.loads((FIXTURES / "store-receipts.json").read_text(encoding="utf-8"))["sales"])


class MismatchingReducer(unittest.TestCase):
    """A reducer that is off by one against its committed SQL: the claim must not survive."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        honest = reducers.REDUCERS["reducers.sales_total"]
        with mock.patch.dict(reducers.REDUCERS, {"reducers.sales_total": lambda world, params: honest(world, params) + 1}):
            cls.envelope = run_sample(example_payload(), cls.out)
        cls.output = cls.envelope["output"]
        cls.events = events(cls.out, cls.envelope["run_id"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_disagreement_is_an_open_conflict_with_both_values(self) -> None:
        conflicts = {k["predicate"]: k for k in self.output["conflicts"]}
        conflict = conflicts["sales_total:backend_disagreement"]
        self.assertEqual(conflict["status"], "open")
        values = {v["source"].split(":")[0]: v["value"] for v in conflict["values"]}
        self.assertEqual(values, {"sql": n_sales(), "reducer": n_sales() + 1})

    def test_claim_is_rejected_by_c2_and_listed(self) -> None:
        self.assertNotIn("sales_total", {c["metric"] for c in self.output["claims"]})
        rejected = {c["metric"]: c for c in self.output["rejected_claims"]}
        self.assertIn("sales_total", rejected)
        reasons = [k for k in rejected["sales_total"]["critique"] if k["verdict"] == "reject"]
        self.assertEqual(reasons[0]["rule"], "C2")
        self.assertIn("disagree", reasons[0]["reason"])
        critique_lines = [e["payload"] for e in self.events if e["type"] == "critique" and e["payload"].get("verdict") == "reject"]
        self.assertTrue(any(p.get("rule") == "C2" and p.get("target") == rejected["sales_total"]["id"] for p in critique_lines))
        self.assertEqual(self.envelope["status"], "partial")
        self.assertEqual(validate(self.envelope, load_schema("output")), [])

    def test_writer_revises_names_the_rejection_and_keeps_the_conflict(self) -> None:
        rejected_id = next(c["id"] for c in self.output["rejected_claims"] if c["metric"] == "sales_total")
        conflict_id = next(k["id"] for k in self.output["conflicts"] if k["predicate"] == "sales_total:backend_disagreement")
        answer = self.output["answer"]
        self.assertNotIn(f"[{rejected_id}]", answer)
        self.assertIn(f"[{conflict_id}]", answer)
        self.assertIn("sales_total (rule C2)", answer)
        self.assertEqual(self.output["critique_rounds"], 2)
        handoffs = [e["payload"] for e in self.events if e["type"] == "handoff"]
        self.assertTrue(any(h["from"] == "critic" and h["to"] == "writer" for h in handoffs))
        recoveries = [e["payload"] for e in self.events if e["type"] == "recovery" and e["agent"] == "writer"]
        self.assertTrue(any(r["reason"] == "narrative_trimmed" and any(c["metric"] == "sales_total" for c in r.get("claims", []))
                            for r in recoveries))

    def test_other_claims_are_still_dual_computed(self) -> None:
        self.assertTrue(self.output["claims"])
        for claim in self.output["claims"]:
            self.assertTrue(claim["cross_checked"], claim["metric"])


class SingleComputation(unittest.TestCase):
    """An SQL budget smaller than the plan: claims whose query never ran are rejected, never reported."""

    def test_claims_without_an_sql_value_are_rejected(self) -> None:
        payload = example_payload()
        payload["input"]["budget"]["max_sql"] = 5
        with tempfile.TemporaryDirectory() as tmp:
            envelope = run_sample(payload, Path(tmp))
            lines = events(Path(tmp), envelope["run_id"])
        output = envelope["output"]
        sql_run = [e["payload"]["query_id"] for e in lines if e["type"] == "sql"]
        self.assertEqual(len(sql_run), 5)
        self.assertTrue(any(e["type"] == "recovery" and e["payload"]["reason"] == "budget_exhausted" for e in lines))
        self.assertTrue(output["rejected_claims"])
        for claim in output["rejected_claims"]:
            self.assertNotIn(METRICS[claim["metric"]].query_id, sql_run, claim["metric"])
            reject = next(k for k in claim["critique"] if k["verdict"] == "reject")
            self.assertEqual(reject["rule"], "C2")
            self.assertIn("single computation", reject["reason"])
        self.assertTrue(output["claims"])
        for claim in output["claims"]:
            self.assertTrue(claim["cross_checked"], claim["metric"])
            self.assertIn(METRICS[claim["metric"]].query_id, sql_run, claim["metric"])
        self.assertEqual(envelope["status"], "partial")


class NoSqlExecutor(unittest.TestCase):
    def test_reducers_alone_never_produce_an_answer(self) -> None:
        def broken(self, exports, urls=None):
            raise RuntimeError("view layer down (simulated)")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(executor_mod.SqliteExecutor, "ensure_views", broken):
            envelope = run_sample(example_payload("03-payer-dossier-onchain"), Path(tmp))
            lines = events(Path(tmp), envelope["run_id"])
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "E_INTERNAL")
        self.assertTrue(envelope["error"]["details"]["rejected"])
        rejects = [e["payload"] for e in lines if e["type"] == "critique" and e["payload"].get("verdict") == "reject"]
        self.assertTrue(rejects)
        self.assertTrue(all(p["rule"] == "C2" for p in rejects))
        recoveries = [e["payload"]["detail"] for e in lines if e["type"] == "recovery"]
        self.assertTrue(any("SQLite fallback failed too" in d for d in recoveries))


if __name__ == "__main__":
    unittest.main()
