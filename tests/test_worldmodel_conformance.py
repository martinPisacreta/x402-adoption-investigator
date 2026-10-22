"""Port of the 8 Meterless World Model conformance checks: sid-001, sid-002, band-001, prov-001, idem-001,
idem-002, bitemp-001, audit-001 (SPEC.md 9; engines/world-model/conformance/runner.ts). Owner: B2.

The ingest-level checks use the runner's own OBSERVATIONS (generic item + mentions ingest) and the same
assertions as the TypeScript runner; the domain-specific behaviour (adapters, claim policy) is covered by
tests/test_adapters.py and tests/test_meterless_keeper.py.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock  # noqa: E402
from investigator.worldmodel import WorldModel, entity_id_from_name, normalize_name  # noqa: E402
from investigator.worldmodel import views  # noqa: E402
from investigator.worldmodel.domain import claim_policy  # noqa: E402
from investigator.worldmodel.resolve import decide, name_similarity  # noqa: E402
from investigator.worldmodel.stable_ids import (canonical_json, context_id_from_attrs, external_id, fact_id,  # noqa: E402
                                                 relationship_id)
from investigator.worldmodel.types import (NO_PROVENANCE_MESSAGE, AssertFact, Fact, IngestObservation, Provenance,  # noqa: E402
                                           Relate, UpsertEntity)

OBSERVATIONS = [
    IngestObservation(external_id="a1", title="Fed holds rates", at="2026-05-17T14:00:00.000Z",
                      source=Provenance(kind="rss", at="2026-05-17T14:00:00.000Z"), payload=None,
                      entities=[{"type": "person", "name": "Jerome Powell"}]),
    IngestObservation(external_id="a2", title="Powell signals patience", at="2026-05-17T16:30:00.000Z",
                      source=Provenance(kind="rss", at="2026-05-17T16:30:00.000Z"), payload=None,
                      entities=[{"type": "person", "name": "Jerome Powell"}]),
]


def fresh_world(run_id: str = "conf") -> WorldModel:
    return WorldModel(Path(tempfile.mkdtemp(prefix="wm-conf-")), SampleClock("2026-05-18T00:00:00.000Z"), run_id)


class StableIds(unittest.TestCase):
    def test_sid_001_same_input_same_id(self) -> None:
        self.assertEqual(normalize_name("José  García"), normalize_name("Jose Garcia"))
        self.assertEqual(entity_id_from_name("person", "José García"), entity_id_from_name("person", "jose garcia"))
        self.assertEqual(entity_id_from_name("person", "Jose Garcia"), entity_id_from_name("person", "josé  garcía"))
        self.assertTrue(entity_id_from_name("person", "x").startswith("ent_"))
        self.assertEqual(len(entity_id_from_name("person", "x")), 20)
        self.assertEqual(external_id("season", "gridiron", "145"), "season:gridiron:145")

    def test_sid_002_different_names_different_ids(self) -> None:
        self.assertNotEqual(entity_id_from_name("org", "Acme Corp"), entity_id_from_name("org", "Acme Corporation"))
        self.assertNotEqual(entity_id_from_name("person", "x"), entity_id_from_name("org", "x"))
        self.assertNotEqual(context_id_from_attrs("week", {"n": 14}, "season:145"), context_id_from_attrs("week", {"n": 14}, "season:144"))
        self.assertEqual(relationship_id("a", "paid", "b"), relationship_id("a", "paid", "b", None, None))
        self.assertNotEqual(fact_id("a", "p", 1, "t", "registry"), fact_id("a", "p", 2, "t", "registry"))

    def test_normalize_and_canonical_json(self) -> None:
        self.assertEqual(normalize_name("  José García​ "), "jose garcia")
        self.assertEqual(canonical_json({"b": 1, "a": None, "c": [{"z": 1, "y": 2}]}), '{"b":1,"c":[{"y":2,"z":1}]}')


class ResolutionBands(unittest.TestCase):
    def test_band_001(self) -> None:
        self.assertEqual(decide(0.85), "auto-merge")
        self.assertEqual(decide(0.84), "review")
        self.assertEqual(decide(0.82), "review")
        self.assertEqual(decide(0.81), "new")
        self.assertEqual(decide(0.8199), "new")
        self.assertEqual(name_similarity("abc", "abc"), 1.0)


class Provenance001(unittest.TestCase):
    def test_prov_001_no_provenance_no_write(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            Provenance(kind="", at="2026-10-22T00:05:00.000Z").validate()
        self.assertEqual(str(ctx.exception), NO_PROVENANCE_MESSAGE)
        world = fresh_world()
        with self.assertRaises(ValueError):
            world.upsert_entity(UpsertEntity(type="person", id="", attrs={}, source=None, name="No Provenance"))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            world.upsert_entity(UpsertEntity(type="person", id="person:x:1", attrs={}, source=Provenance(kind="manual", at="")))
        self.assertEqual(world.counts()["events"], 0)

    def test_claim_policy_rejects_url_less_agent_fact(self) -> None:
        bad = Fact(id="f", about="seller:host:x", predicate="external_payers_total", value=1,
                   source=Provenance(kind="agent", at="t", run_id="r1"), asserted_at="t")
        with self.assertRaises(ValueError):
            claim_policy(bad, "r1")
        good = Fact(id="f", about="seller:host:x", predicate="external_payers_total", value=1,
                    source=Provenance(kind="agent", at="t", url="log://r1#12", run_id="r1"), asserted_at="t")
        claim_policy(good, "r1")
        registry = Fact(id="f", about="x", predicate="p", value=1, source=Provenance(kind="registry", at="t"), asserted_at="t")
        claim_policy(registry, "r1")

    def test_claim_policy_is_wired_into_assert_fact(self) -> None:
        world = fresh_world("r1")
        with self.assertRaises(ValueError):
            world.assert_fact(AssertFact(about="seller:host:x", predicate="external_payers_total", value=1,
                                         source=Provenance(kind="agent", at="2026-10-22T00:05:00.000Z", run_id="r1")))
        self.assertEqual(world.counts()["events"], 0, "a refused fact appends nothing")
        self.assertEqual(len(world.policy_rejections), 1)
        fact = world.assert_fact(AssertFact(about="seller:host:x", predicate="external_payers_total", value=1,
                                            source=Provenance(kind="agent", at="2026-10-22T00:05:00.000Z", url="log://r1#7", run_id="r1")))
        self.assertEqual(fact.predicate, "external_payers_total")
        self.assertEqual(world.state.entities["seller:host:x"].state, "emerging")


class IngestChecks(unittest.TestCase):
    def test_idem_001(self) -> None:
        world = fresh_world()
        first = world.ingest(OBSERVATIONS)
        self.assertTrue(first.changed)
        self.assertEqual(first.processed, 2)
        shape1 = world.state_shape_canonical()
        second = world.ingest(OBSERVATIONS)
        self.assertFalse(second.changed, "second run must report changed=false")
        self.assertEqual(second.events_appended, 0)
        self.assertEqual(second.skipped_unchanged, 2)
        self.assertEqual(world.state_shape_canonical(), shape1)

    def test_idem_002(self) -> None:
        world = fresh_world()
        world.ingest(OBSERVATIONS)
        shape1 = world.state_shape_canonical()
        mutated = [IngestObservation(external_id="a1", title="Fed holds rates (edited)", at=OBSERVATIONS[0].at,
                                     source=OBSERVATIONS[0].source, payload=None, entities=OBSERVATIONS[0].entities), OBSERVATIONS[1]]
        run = world.ingest(mutated)
        self.assertTrue(run.changed, "an edited observation must report changed=true")
        self.assertEqual(run.processed, 1)
        self.assertEqual(run.skipped_unchanged, 1)
        self.assertNotEqual(world.state_shape_canonical(), shape1)

    def test_bitemp_001(self) -> None:
        world = fresh_world()

        def at(s: str) -> Provenance:
            return Provenance(kind="manual", at=s)

        world.upsert_entity(UpsertEntity(type="player", id="player:g:p1", attrs={}, source=at("2087-01-01T00:00:00.000Z")))
        world.upsert_entity(UpsertEntity(type="team", id="team:g:a", attrs={}, source=at("2087-01-01T00:00:00.000Z")))
        world.upsert_entity(UpsertEntity(type="team", id="team:g:b", attrs={}, source=at("2088-03-01T00:00:00.000Z")))
        world.relate(Relate(from_id="player:g:p1", type="plays-for", to_id="team:g:a", valid_from="2087-01-01T00:00:00.000Z",
                            source=at("2087-01-01T00:00:00.000Z")))
        world.relate(Relate(from_id="player:g:p1", type="plays-for", to_id="team:g:b", valid_from="2088-03-01T00:00:00.000Z",
                            source=at("2088-03-01T00:00:00.000Z")))
        now = views.neighbors_of(world, "player:g:p1", via="plays-for")
        self.assertEqual([e["id"] for e in now], ["team:g:b"])
        then = views.neighbors_of(world, "player:g:p1", via="plays-for", at="2087-06-01T00:00:00.000Z")
        self.assertEqual([e["id"] for e in then], ["team:g:a"])
        closed = [r for r in world.state.relationships.values() if r.to_id == "team:g:a"][0]
        self.assertEqual(closed.valid_to, "2088-03-01T00:00:00.000Z")
        self.assertIsNotNone(closed.superseded_by)
        # multi-valued types never close: two `paid` edges to different endpoints both stay open
        world.relate(Relate(from_id="wallet:base:0xabc", type="paid", to_id="endpoint:url:https://a/x", valid_from="2087-01-01T00:00:00.000Z",
                            source=at("2087-01-01T00:00:00.000Z")))
        world.relate(Relate(from_id="wallet:base:0xabc", type="paid", to_id="endpoint:url:https://a/y", valid_from="2088-01-01T00:00:00.000Z",
                            source=at("2088-01-01T00:00:00.000Z")))
        paid = [r for r in world.state.relationships.values() if r.type == "paid"]
        self.assertEqual(len(paid), 2)
        self.assertTrue(all(r.valid_to is None for r in paid))

    def test_audit_001(self) -> None:
        world = fresh_world()
        world.upsert_entity_by_name("x", "Audit One", {}, Provenance(kind="manual", at="2026-01-01T00:00:00.000Z"))
        world.upsert_entity_by_name("x", "Audit Two", {}, Provenance(kind="manual", at="2026-01-02T00:00:00.000Z"))
        audit = world.audit()
        self.assertGreaterEqual(len(audit), 2)
        for i in range(1, len(audit)):
            self.assertGreater(audit[i].seq, audit[i - 1].seq, "audit seq must be strictly increasing")
        self.assertEqual(audit[0].kind, "upsertEntity")
        self.assertEqual(audit[0].at, "2026-01-01T00:00:00.000Z")

    def test_resolution_auto_merge_and_review_queue(self) -> None:
        world = fresh_world()
        src = Provenance(kind="manual", at="2026-01-01T00:00:00.000Z")
        first = world.upsert_entity_by_name("person", "Jerome Powell", {}, src)
        merged = world.upsert_entity_by_name("person", "Jerome Powel", {}, src)     # similarity 12/13 = 0.923 -> auto-merge
        self.assertEqual(first, merged)
        self.assertTrue(world.state.aliases)
        self.assertIn("Jerome Powel", world.state.entities[first].aliases)
        self.assertEqual(world.merge_queue(), [])

    def test_fold_on_load_reproduces_state(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="wm-conf-"))
        clock = SampleClock("2026-05-18T00:00:00.000Z")
        world = WorldModel(tmp, clock, "conf")
        world.ingest(OBSERVATIONS)
        shape = world.state_shape_canonical()
        world.close()
        reloaded = WorldModel(tmp, clock, "conf")
        self.assertEqual(reloaded.state_shape_canonical(), shape)
        self.assertEqual(reloaded.counts()["events"], world.counts()["events"])
        again = reloaded.ingest(OBSERVATIONS)
        self.assertFalse(again.changed)


if __name__ == "__main__":
    unittest.main()
