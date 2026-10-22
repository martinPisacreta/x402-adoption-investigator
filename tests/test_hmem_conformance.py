"""Replays tests/vectors/hmem-vectors.json (13 checks) against the Python H-MEM port (SPEC.md 8). Owner: B2.

The upstream TypeScript runner (engines/hmem/conformance/runner.ts) cannot load Python, so this test replays
the vectors verbatim with the same assertions as the runner, section by section: record-validation (rv),
feedback-deltas (fd), ranking-order (ro), sleep-guardrails (sg), conflict-gate (cg), ledger-audit (la),
dream-boundary (db). The clock is the vectors' fixed instant; each case runs on a fresh temp persist dir.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import iso_from_ms  # noqa: E402
from investigator.hmem import HMEM  # noqa: E402
from investigator.hmem.embeddings import cosine, embed, fnv1a, tokenize  # noqa: E402
from investigator.hmem.types import FEEDBACK_DELTAS, LEDGER_ACTIONS, MemoryRecord  # noqa: E402

VECTORS = REPO_ROOT / "tests" / "vectors" / "hmem-vectors.json"
EXPECTED_IDS = {"rv-001", "rv-002", "fd-001", "fd-002", "fd-003", "fd-004", "fd-005", "ro-001", "ro-002",
                "sg-001", "cg-001", "la-001", "db-001"}
DAY_MS = 24 * 60 * 60 * 1000


def load_vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


class FixedClock:
    """The runner's `clock: () => T0`; `now` can be moved (sleep-guardrails)."""

    name = "fixed"

    def __init__(self, now_ms: int) -> None:
        self.value = now_ms

    def now_ms(self) -> int:
        return self.value

    def now_iso(self) -> str:
        return iso_from_ms(self.value)


def approx(a: float, b: float, eps: float = 1e-9) -> bool:
    return abs(a - b) < eps


class VectorsFile(unittest.TestCase):
    def test_vectors_are_the_verbatim_upstream_copy(self) -> None:
        data = load_vectors()
        self.assertEqual(data["engine"], "hmem")
        ids = {v["id"] for section in data["sections"].values() for v in section}
        self.assertEqual(ids, EXPECTED_IDS)
        self.assertEqual(len(hashlib.sha256(VECTORS.read_bytes()).hexdigest()), 64)

    def test_spec_constants(self) -> None:
        self.assertEqual(FEEDBACK_DELTAS, {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.20})
        self.assertEqual(len(LEDGER_ACTIONS), 17)

    def test_embedder_is_deterministic_and_unit_length(self) -> None:
        self.assertEqual(fnv1a("docker"), fnv1a("docker"))
        self.assertEqual(tokenize("We deploy the API, with Docker!"), ["we", "deploy", "the", "api", "with", "docker"])
        vec = embed("we deploy the api with docker on mondays")
        self.assertEqual(len(vec), 64)
        self.assertAlmostEqual(cosine(vec, vec), 1.0, places=9)
        self.assertEqual(vec, embed("we deploy the api with docker on mondays"))

    def test_seed_memories_load_in_docs_shape(self) -> None:
        lines = (REPO_ROOT / "memory" / "seed" / "memories.jsonl").read_text(encoding="utf-8").splitlines()
        records = [MemoryRecord.from_wire(json.loads(l)) for l in lines]
        self.assertEqual([r.id for r in records], [f"mem-seed-{i:03d}" for i in range(1, 8)])
        for record in records:
            self.assertTrue(record.source)
            self.assertIn("pitfall", record.tags)
            self.assertEqual(len(record.caveat_codes()), 1)
            self.assertTrue(record.entity_names())
            self.assertEqual(record.provenance.origin, "user_explicit")


class Replay(unittest.TestCase):
    """One method per vector section; every case id is a subTest so the scorecard is readable."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vectors = load_vectors()
        cls.t0 = int(cls.vectors["clock"])

    def fresh(self, clock: FixedClock) -> HMEM:
        tmp = Path(tempfile.mkdtemp(prefix="hmem-conf-"))
        self.addCleanup(lambda: None)
        hmem = HMEM(clock, tmp)
        hmem.load()
        return hmem

    def test_record_validation(self) -> None:
        for case in self.vectors["sections"]["record-validation"]:
            with self.subTest(case["id"]):
                hmem = self.fresh(FixedClock(self.t0))
                rejected = False
                try:
                    hmem.add(case["input"]["content"], case["input"]["source"])
                except ValueError:
                    rejected = True
                self.assertEqual(rejected, case["expect"] == "reject", case["description"])

    def test_feedback_deltas(self) -> None:
        for case in self.vectors["sections"]["feedback-deltas"]:
            with self.subTest(case["id"]):
                hmem = self.fresh(FixedClock(self.t0))
                record = hmem.add("feedback target record", "conformance", confidence=case["start"])
                access_before = record.access_count
                hmem.feedback(record.id, case["kind"], actor="conformance")
                after = hmem.store.get(record.id)
                self.assertTrue(approx(after.confidence, case["expectConfidence"]),
                                f"confidence {after.confidence}, expected {case['expectConfidence']}")
                if "expectAccessDelta" in case:
                    self.assertEqual(after.access_count - access_before, case["expectAccessDelta"])
                if case.get("expectTag"):
                    self.assertIn(case["expectTag"], after.tags)

    def test_ranking_order(self) -> None:
        for case in self.vectors["sections"]["ranking-order"]:
            with self.subTest(case["id"]):
                hmem = self.fresh(FixedClock(self.t0))
                by_key = {}
                for rec in case["records"]:
                    by_key[rec["key"]] = hmem.add(rec["content"], "conformance", confidence=rec["confidence"], layer=rec["layer"])
                for rec in case["records"]:
                    if rec.get("supersededByKey"):
                        by_key[rec["key"]].superseded_by = by_key[rec["supersededByKey"]].id
                result = hmem.query(case["query"], threshold=0, top_n=10, actor="conformance")
                order = result.ids()
                expected = [by_key[k].id for k in case["expectOrder"]]
                for i in range(len(expected) - 1):
                    self.assertLess(order.index(expected[i]), order.index(expected[i + 1]),
                                    f"expected {case['expectOrder'][i]} before {case['expectOrder'][i + 1]}, got {order}")

    def test_sleep_guardrails(self) -> None:
        for case in self.vectors["sections"]["sleep-guardrails"]:
            with self.subTest(case["id"]):
                clock = FixedClock(self.t0)
                hmem = self.fresh(clock)
                by_key = {}
                for rec in case["records"]:
                    by_key[rec["key"]] = hmem.add(rec["content"], rec["source"], derived_from=rec.get("derivedFrom") or [])
                clock.value = self.t0 + case["ageDays"] * DAY_MS
                before = sorted(m.id for m in hmem.store.all())
                preview = hmem.sleep.preview()
                self.assertEqual(sorted(m.id for m in hmem.store.all()), before, "preview mutated the store")
                for key in case["expectArchived"]:
                    self.assertIn(by_key[key].id, preview["to_archive"], f"{key} should be archivable")
                for key in case["expectProtected"]:
                    self.assertNotIn(by_key[key].id, preview["to_archive"], f"{key} is guardrail-protected")
                report = hmem.sleep.execute(preview)
                for key in case["expectProtected"]:
                    self.assertIsNotNone(hmem.store.get(by_key[key].id), f"{key} was deleted despite guardrails")
                self.assertTrue(isinstance(report["backup_id"], str) and report["backup_id"])

    def test_conflict_gate(self) -> None:
        for case in self.vectors["sections"]["conflict-gate"]:
            with self.subTest(case["id"]):
                hmem = self.fresh(FixedClock(self.t0))
                a = hmem.add(case["a"]["content"], "conformance", confidence=case["a"]["confidence"])
                b = hmem.add(case["b"]["content"], "conformance", confidence=case["b"]["confidence"])
                found = hmem.conflicts.scan()
                self.assertTrue(found, "opposing always/never pair on similar content must be detected")
                outcome = hmem.conflicts.auto_resolve(found[0])
                self.assertIn(outcome, ("resolved", "queued"))
                if outcome == "resolved":
                    loser = [m for m in (hmem.store.get(a.id), hmem.store.get(b.id)) if m and m.superseded_by]
                    self.assertTrue(loser, "resolved conflicts must mark the loser supersededBy")
                self.assertIsNotNone(hmem.store.get(a.id))
                self.assertIsNotNone(hmem.store.get(b.id))

    def test_ledger_audit(self) -> None:
        for case in self.vectors["sections"]["ledger-audit"]:
            with self.subTest(case["id"]):
                hmem = self.fresh(FixedClock(self.t0))
                record = hmem.add("audited record", "conformance", confidence=case["start"])
                hmem.feedback(record.id, case["kind"], actor="conformance")
                history = hmem.ledger.history(record.id)
                self.assertEqual([h.action for h in history], case["expectActions"])
                fb = history[-1]
                self.assertTrue(approx(fb.previous_state["confidence"], case["expectPrev"]))
                self.assertTrue(approx(fb.new_state["confidence"], case["expectNew"]))

    def test_dream_boundary(self) -> None:
        for case in self.vectors["sections"]["dream-boundary"]:
            with self.subTest(case["id"]):
                hmem = self.fresh(FixedClock(self.t0))
                for rec in case["records"]:
                    hmem.add(rec["content"], "conformance", type=rec["type"])
                count_before = len(hmem.store.all())
                proposals = hmem.dreaming.dream()
                self.assertTrue(proposals, "two related preferences must produce at least one proposal")
                self.assertEqual(len(hmem.store.all()), count_before, "dreaming must not materialize anything before approval")
                created = hmem.dreaming.approve(proposals[0].id)
                if created is not None:
                    self.assertEqual(created.layer, "long_term")
                    self.assertTrue(created.derived_from)


if __name__ == "__main__":
    unittest.main()
