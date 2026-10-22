"""H-MEM as the investigator uses it (SPEC.md 8.2 / 8.3): seed load, policy_memories index, retrieval trace and
read ledger entries, feedback deltas with reasons, model-free mining of claim statements, sleep preview without
mutation, conflicts and dreaming over the seed, and the seed folder never being mutated. Owner: B2.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock  # noqa: E402
from investigator.hmem import HMEM  # noqa: E402
from investigator.hmem.facade import mount_hmem  # noqa: E402
from investigator.hmem.mining import parse_json_array  # noqa: E402
from investigator.hmem.types import FEEDBACK_DELTAS, MemoryProvenance  # noqa: E402

SEED = REPO_ROOT / "memory" / "seed"


def seed_digest() -> str:
    return hashlib.sha256((SEED / "memories.jsonl").read_bytes() + (SEED / "ledger.jsonl").read_bytes()).hexdigest()


def seeded(run_id: str = "hmem-test") -> tuple[HMEM, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="hmem-")) / "hmem"
    tmp.mkdir(parents=True)
    for name in ("memories.jsonl", "ledger.jsonl"):
        shutil.copyfile(SEED / name, tmp / name)
    return mount_hmem(SampleClock("2026-10-22T00:05:00Z"), tmp), tmp


class SeedAndPolicy(unittest.TestCase):
    def test_seed_loads_and_policy_index_is_deterministic(self) -> None:
        before = seed_digest()
        hmem, _ = seeded()
        self.assertEqual(hmem.load(), 7)
        self.assertEqual(len(hmem.ledger.all()), 7)
        self.assertEqual(hmem.ledger.stats()["by_action"], {"create": 7})
        ids = lambda fields, sources: [m.id for m in hmem.policy_memories(fields, sources)]  # noqa: E731
        self.assertEqual(ids(["l30DaysUniquePayers"], []), ["mem-seed-001"])
        self.assertEqual(ids([], ["cdp_search"]), ["mem-seed-002"])
        self.assertEqual(ids([], ["receipts"]), ["mem-seed-003"])
        self.assertEqual(ids(["l30DaysTotalCalls"], ["receipts", "cdp_catalog_snapshot"]), ["mem-seed-003", "mem-seed-006"])
        self.assertEqual(ids(["l30DaysTotalCalls"], ["cdp_catalog_snapshot"]), ["mem-seed-006"])
        self.assertEqual(ids(["amountAtomic"], ["base_rpc", "x402_trust"]), ["mem-seed-004", "mem-seed-005"])
        self.assertEqual(ids([], []), [])
        # metric entities: mem-seed-007 names the two metrics that put Bazaar counters and receipts side by side
        by_metric = lambda metrics: [m.id for m in hmem.policy_memories([], [], metrics)]  # noqa: E731
        self.assertEqual(by_metric(["external_sales_30d"]), ["mem-seed-007"])
        self.assertEqual(by_metric(["bazaar_calls_30d_for_host"]), ["mem-seed-007"])
        self.assertEqual(by_metric(["sales_total"]), [])
        self.assertEqual([m.id for m in hmem.policy_memories([], ["receipts"], ["external_sales_30d"])], ["mem-seed-003", "mem-seed-007"])
        caveats = {m.id: m.caveat_codes()[0] for m in hmem.store.all()}
        self.assertEqual(caveats["mem-seed-002"], "search_limit_20")
        self.assertEqual(caveats["mem-seed-003"], "self_reported_receipts")
        self.assertEqual(seed_digest(), before, "the committed seed is never mutated")


class RetrievalTrace(unittest.TestCase):
    def test_query_traces_signals_and_writes_read_entries(self) -> None:
        hmem, _ = seeded()
        statement = "Bazaar l30DaysUniquePayers summed across the catalog endpoints is a count of unique payers"
        result = hmem.query(statement, threshold=0.35, top_n=5, actor="critic")
        self.assertTrue(result.memories, "a claim statement naming the field must surface its pitfall memory")
        self.assertEqual(result.ids()[0], "mem-seed-001")
        result = hmem.query("How real is the adoption of tienda.textokit.store? receipts.json payers and the Bazaar search", threshold=0.35, top_n=5, actor="planner")
        self.assertTrue(result.memories, "pitfall memories about receipts/search must be retrievable")
        self.assertIn("mem-seed-003", result.ids())
        self.assertLessEqual(len(result.memories), 5)
        for hit in result.memories:
            self.assertGreaterEqual(hit.score, 0.35)
            self.assertEqual(set(hit.signals), {"semantic", "keyword", "tag", "domain", "entity", "layer", "recency", "superseded"})
        trace = hmem.trace_line(result)
        for key in ("actor", "query", "threshold", "topN", "hits", "reinjection_sha256", "retrievalReason", "strategy"):
            self.assertIn(key, trace)
        self.assertEqual(trace["actor"], "planner")
        self.assertEqual([h["memoryId"] for h in trace["hits"]], result.ids())
        reads = [e for e in hmem.ledger.all() if e.action == "read" and e.actor == "planner"]
        self.assertEqual(sorted(e.memory_id for e in reads), sorted(result.ids()))
        self.assertTrue(any(e.actor == "critic" for e in hmem.ledger.all() if e.action == "read"))
        self.assertIn("[MEMORY CONTEXT]", result.reinjection)
        self.assertIn(f"<{result.ids()[0]}>", result.reinjection)
        again = hmem.query("How real is the adoption of tienda.textokit.store? receipts.json payers and the Bazaar search", threshold=0.35, top_n=5, actor="writer")
        self.assertEqual(again.ids(), result.ids(), "deterministic ranking")
        scores = [h.score for h in result.memories]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_threshold_applies_to_final_score(self) -> None:
        hmem, _ = seeded()
        unrelated = hmem.query("unrelated question about cooking pasta for dinner", threshold=0.35, top_n=10, actor="test")
        self.assertEqual(unrelated.memories, [], "an unrelated question retrieves nothing at the spec threshold")
        self.assertEqual(unrelated.trace["hits"], [])
        self.assertEqual(unrelated.trace["candidates"], 0)
        all_hits = hmem.query("unrelated question about cooking pasta for dinner", threshold=0.0, top_n=10, actor="test")
        self.assertEqual(len(all_hits.memories), 7)
        for hit in all_hits.memories:
            raw = sum(hmem.retrieval.weights[k] * hit.signals[k] for k in hmem.retrieval.weights)
            self.assertAlmostEqual(hit.score, max(0.0, min(1.0, raw)) * hit.memory.confidence, places=9, msg="confidence multiplies AFTER the sum")
        hinted = hmem.query("what should the critic check", threshold=0.35, top_n=10, actor="critic", entities=["source:base_rpc"])
        self.assertEqual(hinted.ids(), ["mem-seed-005"], "query entity hints steer the entity signal deterministically")


class Feedback(unittest.TestCase):
    def test_exact_deltas_with_reason_in_ledger(self) -> None:
        hmem, _ = seeded()
        m = hmem.feedback("mem-seed-002", "helpful", actor="critic", reason="caveat search_limit_20 attached to c06")
        self.assertAlmostEqual(m.confidence, 0.95 + FEEDBACK_DELTAS["helpful"] if 0.95 + 0.05 <= 1 else 1.0, places=9)
        self.assertEqual(m.access_count, 1)
        m = hmem.feedback("mem-seed-003", "not_helpful", actor="critic", reason="retrieved but uncited")
        self.assertAlmostEqual(m.confidence, 0.92, places=9)
        m = hmem.feedback("mem-seed-004", "wrong", actor="critic", reason="value differs by > 5 %")
        self.assertAlmostEqual(m.confidence, 0.75, places=9)
        self.assertIn("review", m.tags)
        hmem.feedback("mem-seed-004", "wrong", actor="critic")
        self.assertEqual(m.tags.count("review"), 1, "the review tag is added once")
        entries = [e for e in hmem.ledger.history("mem-seed-004") if e.action == "feedback"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].details, {"kind": "wrong", "delta": -0.2, "reason": "value differs by > 5 %"})
        self.assertAlmostEqual(entries[0].previous_state["confidence"], 0.95, places=9)
        self.assertAlmostEqual(entries[0].new_state["confidence"], 0.75, places=9)
        with self.assertRaises(ValueError):
            hmem.feedback("mem-seed-001", "meh", actor="critic")
        line = hmem.feedback_trace_line("critic", "mem-seed-004", "wrong", 0.95, 0.75)
        self.assertEqual(set(line), {"actor", "memoryId", "kind", "previous_confidence", "new_confidence"})


class Mining(unittest.TestCase):
    def test_model_free_mining_of_claim_statements(self) -> None:
        hmem, tmp = seeded()
        text = ("seller:host:tienda.textokit.store | external sales total is 21 from 4 payers\n"
                "seller:host:tienda.textokit.store | self-settlement share is 56 of 77\n"
                "short\n")
        records = hmem.mine("plan_completion", text, source="run:ex01", goal_run_id="ex01",
                            provenance=MemoryProvenance(origin="goal_run", learned_at=1, label="question", refs={"goalRunId": "ex01"}))
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record.type, "factual")
            self.assertEqual(record.layer, "short_term")
            self.assertEqual(record.goal_run_id, "ex01")
            self.assertAlmostEqual(record.confidence, 0.8)
            self.assertIn("seller:host:tienda.textokit.store", record.entity_names())
            self.assertEqual(record.provenance.origin, "goal_run")
            self.assertIn("event:plan_completion", record.tags)
        self.assertEqual(hmem.store.load(), 9)
        reloaded = mount_hmem(SampleClock("2026-10-22T00:05:00Z"), tmp)
        self.assertEqual(reloaded.load(), 9, "mined records persist to memories.jsonl")
        self.assertEqual(len([e for e in reloaded.ledger.all() if e.action == "create"]), 9)

    def test_miner_fn_strict_json_and_fallback(self) -> None:
        calls = []

        def miner(prompt: str) -> str:
            calls.append(prompt)
            return "```json\n[\"the seller settles mostly with its own wallets\", 42, \"x\"]\n```"

        tmp = Path(tempfile.mkdtemp(prefix="hmem-miner-"))
        hmem = HMEM(SampleClock("2026-10-22T00:05:00Z"), tmp, miner_fn=miner)
        hmem.load()
        records = hmem.mine("model_response", "anything", source="test")
        self.assertEqual([r.content for r in records], ["the seller settles mostly with its own wallets"])
        self.assertEqual(records[0].layer, "working")
        self.assertEqual(len(calls), 1)
        hmem.mining.miner_fn = lambda prompt: "not json at all"
        fallback = hmem.mine("model_response", "A durable statement about deploys on mondays. Another durable statement about fridays. Tiny.", source="test")
        self.assertEqual(len(fallback), 2, "two sentences of >= 20 chars; the 5-char one is dropped")
        self.assertIn("fallback", hmem.mining.last_note)
        with self.assertRaises(ValueError):
            parse_json_array("{\"a\": 1}")
        with self.assertRaises(ValueError):
            hmem.mine("unknown_event", "x", source="test")


class SleepConflictsDreams(unittest.TestCase):
    def test_sleep_preview_mutates_nothing_and_execute_backs_up(self) -> None:
        hmem, tmp = seeded()
        before = [m.to_wire() for m in hmem.store.all()]
        preview = hmem.sleep.preview()
        self.assertEqual(set(preview) >= {"to_consolidate", "to_archive", "to_synthesize"}, True)
        self.assertEqual([m.to_wire() for m in hmem.store.all()], before)
        report = hmem.sleep.execute(preview)
        self.assertTrue((tmp / "backups" / report["backup_id"] / "memories.jsonl").is_file())
        self.assertEqual(len(hmem.store.all()), 7 - report["archived"] - sum(len(g) for g in preview["to_synthesize"]) + report["synthesized"])
        hmem.sleep.restore(report["backup_id"])
        self.assertEqual(sorted(m.id for m in hmem.store.all()), [f"mem-seed-{i:03d}" for i in range(1, 8)])
        self.assertEqual(hmem.ledger.all()[-1].action, "restore")

    def test_conflicts_and_dreams_never_delete(self) -> None:
        hmem, _ = seeded()
        hmem.add("always sum l30DaysUniquePayers across endpoints to get a total", "test", confidence=0.3)
        hmem.add("never sum l30DaysUniquePayers across endpoints to get a total", "test", confidence=0.9)
        found = hmem.conflicts.scan()
        self.assertTrue(found)
        outcome = hmem.conflicts.auto_resolve(found[0])
        self.assertIn(outcome, ("resolved", "queued"))
        self.assertEqual(len(hmem.store.all()), 9)
        proposals = hmem.dreaming.dream()
        self.assertEqual(len(hmem.store.all()), 9, "nothing materialises before approve")
        if proposals:
            hmem.dreaming.reject(proposals[0].id)
            self.assertEqual(hmem.ledger.all()[-1].action, "dream_rejected")
            self.assertTrue((Path(hmem.persist_dir) / "dreams.json").is_file())


if __name__ == "__main__":
    unittest.main()
