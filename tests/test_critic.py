"""Critic C1-C7 + narrative pass B unit tests (token -> claim map) and the verdict rules (SPEC.md 6.7, 6.8). Owner: B4.

The checks run against a real sample-mode RunContext (guard installed, seed memories copied into the run dir) so
the pitfall gate reads the committed memory/seed through the H-MEM policy index exactly as a run does.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.agents import critic  # noqa: E402
from investigator.agents.metrics import METRICS, MetricSpec  # noqa: E402
from investigator.agents.reducers import WorldExports  # noqa: E402
from investigator.agents.verdict import compute_verdict  # noqa: E402
from investigator.agents.writer import Answer, render_template  # noqa: E402
from investigator.config import build_config, parse_request  # noqa: E402
from investigator.contracts import Claim, Conflict, ConflictValue, Coverage, Derivation, Evidence, Observation  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"


def make_ctx(tmp: str) -> runner.RunContext:
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
    run_id, inp = parse_request({"run_id": "critic-unit", "input": {"question": "How real is tienda.textokit.store adoption?",
                                                                     "focus": {"host": "tienda.textokit.store"}}})
    ctx = runner.build_context(run_id, inp, config, echo_stdout=False)
    ctx.start_ms = ctx.clock.now_ms()
    ctx.start_iso = ctx.clock.start_iso
    ctx.deadline_ms = ctx.start_ms + 180_000
    return ctx


def receipts_observation(ctx: runner.RunContext) -> Observation:
    import hashlib
    raw = (FIXTURES / "store-receipts.json").read_bytes()
    return Observation(name="store_receipts", family="receipts", request_key="GET https://tienda.textokit.store/receipts.json",
                       url="https://tienda.textokit.store/receipts.json", method="GET", http=200,
                       bytes_sha256=hashlib.sha256(raw).hexdigest(), latency_ms=0, captured_at=ctx.clock.start_iso,
                       replayed=True, stale=False, status="ok", error=None, payload=json.loads(raw.decode("utf-8")),
                       fixture_file="fixtures/raw/store-receipts.json")


def receipts_claim(obs: Observation, caveats: tuple[str, ...] = ("self_reported_receipts", "basis_mismatch"),
                   memory_ids: dict[str, str] | None = None) -> Claim:
    total = len(obs.payload["sales"])
    claim = Claim("c01", "sales_total", "seller:host:tienda.textokit.store",
                  f"The receipts of tienda.textokit.store list {total} settled sales in total.", total, "count",
                  period="all_time",
                  evidence=[Evidence(kind="url", ref=obs.url, sha256=obs.bytes_sha256, captured_at=obs.captured_at, replayed=True)],
                  derivation=Derivation(agent="analyst", query_id="q01_sales_by_wallet_class", sql_sha256="0" * 64, backend="sqlite",
                                        reducer="reducers.sales_total", rows=2, fields=["ownWallet", "amountAtomic"], sources=["receipts"]),
                  caveats=list(caveats), caveat_memory_ids=dict(memory_ids or {}), confidence=0.9, cross_checked=True)
    return claim


class VerdictRules(unittest.TestCase):
    def test_fewer_than_three_claims_is_insufficient(self) -> None:
        self.assertEqual(compute_verdict("seller_profile", []).label, "insufficient_evidence")

    def test_self_settled_mostly(self) -> None:
        claims = [
            Claim("c01", "self_settlement_share", "s", "x", {"numerator": 56, "denominator": 77, "share": 0.727}, "share", status="accepted", cross_checked=True, confidence=0.9),
            Claim("c02", "external_payers_total", "s", "x", 4, "count", status="accepted", cross_checked=True, confidence=0.85),
            Claim("c03", "repeat_external_payers", "s", "x", 1, "count", status="accepted", cross_checked=True, confidence=0.9),
        ]
        verdict = compute_verdict("seller_profile", claims)
        self.assertEqual(verdict.label, "self_settled_mostly")
        self.assertEqual(verdict.why, ["c01"])
        self.assertEqual(verdict.confidence, 0.9)

    def test_catalog_and_payer_rules(self) -> None:
        base = [Claim(f"c0{i}", f"m{i}", "s", "x", i, "count", status="accepted", cross_checked=True) for i in range(1, 4)]
        top10 = Claim("c04", "top10_hosts_calls_share", "catalog:cdp-bazaar", "x", {"numerator": 7, "denominator": 10, "share": 0.7}, "share",
                      status="accepted", cross_checked=True, confidence=0.9)
        self.assertEqual(compute_verdict("catalog_concentration", base + [top10]).label, "concentrated")
        top10.value = {"numerator": 2, "denominator": 10, "share": 0.2}
        self.assertEqual(compute_verdict("catalog_concentration", base + [top10]).label, "diffuse")
        sales = Claim("c05", "payer_sales_total", "w", "x", 1, "count", status="accepted", cross_checked=True)
        verified = Claim("c06", "verified_tx", "w", "x", 1, "count", status="accepted", cross_checked=True)
        self.assertEqual(compute_verdict("payer_dossier", base + [sales, verified]).label, "adoption_thin")
        sales.value = 2
        self.assertEqual(compute_verdict("payer_dossier", base + [sales, verified]).label, "adoption_real")

    def test_template_cites_claim_ids(self) -> None:
        claims = [Claim("c01", "sales_total", "s", "The receipts list N sales.", 7, "count", status="accepted")]
        answer = render_template(claims, [], [], compute_verdict("seller_profile", claims))
        self.assertIn("[c01]", answer.text)
        self.assertEqual(answer.citations(), ["c01"])


class CriticChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = make_ctx(self.tmp.name)
        self.obs = receipts_observation(self.ctx)

    def tearDown(self) -> None:
        runner._cleanup(self.ctx)
        self.ctx.log.close()
        self.tmp.cleanup()

    def test_c1_evidence_must_resolve(self) -> None:
        claim = receipts_claim(self.obs)
        self.assertEqual(critic.check_c1_evidence(self.ctx, claim, [self.obs], []), [])
        claim.evidence = [Evidence(kind="url", ref=self.obs.url, sha256="f" * 64)]
        issues = critic.check_c1_evidence(self.ctx, claim, [self.obs], [])
        self.assertEqual(issues[0]["verdict"], "reject")
        seq = self.ctx.log.emit("claim", "analyst", {"claim_id": "c01", "metric": "x", "subject": "s", "value": 1, "unit": "count",
                                                     "backend": None, "cross_checked": False, "caveats": []})
        claim.evidence = [Evidence(kind="log_line", ref=self.ctx.log.ref(seq))]
        self.assertEqual(critic.check_c1_evidence(self.ctx, claim, [], []), [])
        claim.evidence = [Evidence(kind="log_line", ref=self.ctx.log.ref(seq + 500))]
        self.assertEqual(critic.check_c1_evidence(self.ctx, claim, [], [])[0]["verdict"], "reject")

    def test_c2_recompute_against_the_exports(self) -> None:
        claim = receipts_claim(self.obs)
        world = WorldExports(raw={"store_receipts": self.obs.payload}, now_iso=self.ctx.clock.start_iso)
        self.ctx.scratch["world_exports"] = world
        self.ctx.scratch["recompute"] = {"c01": {"sql": claim.value, "reducer": claim.value, "metric": "sales_total", "params": {}}}
        self.assertEqual(critic.check_c2_recompute(self.ctx, claim, []), [])
        claim.value = claim.value + 1
        self.assertEqual(critic.check_c2_recompute(self.ctx, claim, [])[0]["verdict"], "reject")
        self.ctx.scratch["recompute"]["c01"]["sql"] = None
        self.ctx.scratch["recompute"]["c01"]["reducer"] = None
        self.assertTrue(critic.check_c2_recompute(self.ctx, Claim("c09", "sales_total", "s", "x", 1, "count"), []))

    def test_c3_pitfall_gate_requires_the_memory_caveat(self) -> None:
        claim = receipts_claim(self.obs, caveats=())
        issues, helpful = critic.check_c3_pitfall_gate(self.ctx, claim)
        codes = {i["fix"]["add_caveat"] for i in issues}
        self.assertIn("self_reported_receipts", codes)
        self.assertNotIn("basis_mismatch", codes, "mem-seed-007 names the two compared metrics, not every receipts claim")
        compared = Claim("c13", "external_sales_30d", claim.subject, "x", 3, "count",
                         derivation=Derivation("analyst", None, None, None, "reducers.external_sales_30d", fields=["at", "ownWallet"], sources=["receipts"]))
        gate_codes = {i["fix"]["add_caveat"]: i["memory_ids"][0] for i, in [(i,) for i in critic.check_c3_pitfall_gate(self.ctx, compared)[0]]}
        self.assertEqual(gate_codes.get("basis_mismatch"), "mem-seed-007")
        self.assertTrue(all(i["verdict"] == "revise" and i["memory_ids"] for i in issues))
        self.assertEqual(helpful, {})
        for issue in issues:
            critic._apply_fix(claim, issue["fix"])
        issues2, helpful2 = critic.check_c3_pitfall_gate(self.ctx, claim)
        self.assertEqual(issues2, [])
        self.assertIn("mem-seed-003", helpful2)
        self.assertEqual(claim.caveat_memory_ids["self_reported_receipts"], "mem-seed-003")

    def test_c4_scope_rules(self) -> None:
        # A spec that sums l30DaysUniquePayers exists only inside this test; the shipped registry has none.
        spec = MetricSpec(id="test_only_summed", query_id="q08_catalog_size", reducer="reducers.test_only_summed", unit="count",
                          subject_template="catalog:cdp-bazaar", statement_template="{value}", required_sources=("cdp_catalog_snapshot",),
                          fields=("l30DaysUniquePayers",), sources=("cdp_catalog_snapshot",), sums=("l30DaysUniquePayers",))
        summed = Claim("c02", spec.id, "catalog:cdp-bazaar", "x", 5, "count",
                       derivation=Derivation("analyst", None, None, None, spec.reducer, fields=["l30DaysUniquePayers"],
                                             sources=["cdp_catalog_snapshot"]),
                       coverage=Coverage(1, 1, 1.0, "compact snapshot"))
        METRICS[spec.id] = spec
        try:
            issues = critic.check_c4_scope(self.ctx, summed)
        finally:
            METRICS.pop(spec.id, None)
        self.assertTrue(any(i["verdict"] == "reject" and "mem-seed-001" in i["memory_ids"] for i in issues))
        self.assertFalse([m.id for m in METRICS.values() if "l30DaysUniquePayers" in m.sums], "no shipped metric sums the counter")
        bad_share = Claim("c03", "self_settlement_share", "s", "x", 0.5, "share",
                          derivation=Derivation("analyst", None, None, None, "r", sources=["receipts"]), caveats=["self_reported_receipts"])
        self.assertTrue(any(i["verdict"] == "reject" for i in critic.check_c4_scope(self.ctx, bad_share)))
        receipts_only = receipts_claim(self.obs, caveats=())
        issues = critic.check_c4_scope(self.ctx, receipts_only)
        self.assertEqual(issues[0]["fix"]["add_caveat"], "self_reported_receipts")
        no_coverage = Claim("c04", "catalog_resources", "catalog:cdp-bazaar", "x", 1, "count", caveats=["snapshot_dated"],
                            derivation=Derivation("analyst", None, None, None, "r", sources=["cdp_catalog_snapshot"]))
        self.assertTrue(any("coverage" in i["reason"] for i in critic.check_c4_scope(self.ctx, no_coverage)))

    def test_c5_staleness_caps(self) -> None:
        from investigator.clock import iso_from_ms
        claim = receipts_claim(self.obs)
        self.assertEqual(critic.check_c5_staleness(self.ctx, claim), ([], None))
        claim.evidence[0].captured_at = iso_from_ms(self.ctx.start_ms - 10 * 86_400_000)
        issues, cap = critic.check_c5_staleness(self.ctx, claim)
        self.assertEqual(cap, 0.8)
        claim.evidence[0].stale = True
        self.assertEqual(critic.check_c5_staleness(self.ctx, claim)[1], 0.7)

    def test_c7_conflict_citation(self) -> None:
        claim = receipts_claim(self.obs)
        conflict = Conflict("k01", "endpoint_count_for_host", claim.subject,
                            [ConflictValue("a", 1, "https://a", "basis"), ConflictValue("b", 2, "https://b", "basis")],
                            "why", "explained", claim_ids=["c01"])
        issues = critic.check_c7_conflict_citation(self.ctx, claim, [conflict])
        self.assertEqual(issues[0]["fix"]["add_conflict"], "k01")
        critic._apply_fix(claim, issues[0]["fix"])
        self.assertEqual(critic.check_c7_conflict_citation(self.ctx, claim, [conflict]), [])

    def test_run_critic_revises_then_accepts(self) -> None:
        claim = receipts_claim(self.obs, caveats=())
        world = WorldExports(raw={"store_receipts": self.obs.payload}, now_iso=self.ctx.clock.start_iso)
        self.ctx.scratch["world_exports"] = world
        self.ctx.scratch["recompute"] = {"c01": {"sql": claim.value, "reducer": claim.value, "metric": "sales_total", "params": {}}}
        answer = render_template([claim], [], [], compute_verdict("seller_profile", []))
        result = critic.run_critic(self.ctx, answer, [claim], [], [self.obs], 1, conflicts=[]).payload
        self.assertTrue(result.revise)
        self.assertEqual(claim.status, "revised")
        self.assertIn("self_reported_receipts", claim.caveats)
        answer2 = render_template([claim], [], [], compute_verdict("seller_profile", []))
        result2 = critic.run_critic(self.ctx, answer2, [claim], [], [self.obs], 2, conflicts=[]).payload
        self.assertFalse(result2.revise, result2.issues)
        self.assertEqual(claim.status, "accepted")
        self.assertEqual(result2.narrative_map["unsourced"], [])
        self.assertEqual(self.ctx.scratch["memory_feedback"].get("mem-seed-003"), "helpful")


class PassBTokens(unittest.TestCase):
    def test_tokenizer_handles_formatted_variants_and_skips_identifiers(self) -> None:
        text = ("The top external payer 0xec2abd3eda89bed90124736e317e847d5fb6d034 made 18 of 21 external sales (85.7 %) worth "
                "44,000 atomic USDC (0.044 USDC) between 2026-10-20T04:51:24.352Z and 2026-10-21 [c03]; see l30DaysTotalCalls, "
                "the top ten hosts, the 30-day window, x402, ERC-8004, q04_top_payer_share, k01 and [mem-seed-002]; balance 1 043 148.")
        tokens = critic.tokenize_numbers(text)
        normalized = [t["normalized"] for t in tokens]
        self.assertIn("2026-10-20T04:51:24.352Z", normalized)
        self.assertIn("2026-10-21", normalized)
        for expected in ("18", "21", "85.7", "44000", "0.044", "1043148"):
            self.assertIn(expected, normalized, expected)
        self.assertNotIn("30", normalized)
        self.assertNotIn("402", normalized)
        self.assertNotIn("8004", normalized)
        self.assertNotIn("03", normalized)
        self.assertNotIn("04", normalized)
        self.assertNotIn("002", normalized)
        kinds = {t["normalized"]: t["kind_hint"] for t in tokens}
        self.assertEqual(kinds["85.7"], "percent")
        self.assertEqual(kinds["0.044"], "usdc")

    def test_numbers_inside_text_values_are_claim_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx(tmp)
            try:
                by_service = Claim("c01", "external_sales_by_service", "s", "x", "cuit-ar=3, normativa-ar=2, x402-ping=2", "text", status="accepted")
                hosts = Claim("c02", "top_hosts_by_listings", "catalog:cdp-bazaar", "x", "a.example=1568, b.example=694", "text", status="accepted")
                answer = Answer(verdict="x", sentences=[
                    "External sales by service: cuit-ar=3, normativa-ar=2, x402-ping=2 [c01].",
                    "Hosts with the most listings in the snapshot: a.example=1568, b.example=694 [c02].",
                    "A stray 77 here."])
                narrative_map, issues = critic.narrative_pass_b(ctx, answer, [by_service, hosts], [], 1)
                kinds = {(t["sentence_index"], t["token"]): (t["claim_id"], t["kind"]) for t in narrative_map["tokens"]}
                self.assertEqual(kinds[(0, "3")], ("c01", "value"))
                self.assertEqual(kinds[(0, "2")], ("c01", "value"))
                self.assertEqual(kinds[(1, "1568")], ("c02", "value"))
                self.assertEqual(kinds[(1, "694")], ("c02", "value"))
                self.assertEqual({u["token"] for u in narrative_map["unsourced"]}, {"77"})
                self.assertEqual({i["target"] for i in issues}, {2})
            finally:
                runner._cleanup(ctx)
                ctx.log.close()

    def test_narrative_map_maps_every_number_or_flags_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx(tmp)
            try:
                share = Claim("c01", "top_payer_share", "s", "x", {"numerator": 18, "denominator": 21, "share": 18 / 21, "payer": "0xabc"},
                              "share", status="accepted", coverage=Coverage(17, None, None, "search capped at 20"))
                usdc = Claim("c02", "external_usdc_atomic", "s", "x", 44000, "atomic_usdc", status="accepted")
                conflict = Conflict("k01", "endpoint_count_for_host", "s", [ConflictValue("a", 17, "u", "b"), ConflictValue("b", 36, "u", "b")],
                                    "why", "explained", ["c01"])
                answer = Answer(verdict="x", sentences=[
                    "The top payer made 18 of 21 sales (85.7 %) [c01].",
                    "External sales total 44,000 atomic USDC (0.044 USDC) [c02].",
                    "Where sources disagree [k01]: a says 17 and b says 36, capped at 20.",
                    "A stray number 99 appears here.",
                    "Dangling citation [c07]."])
                narrative_map, issues = critic.narrative_pass_b(ctx, answer, [share, usdc], [conflict], 1)
                kinds = {(t["sentence_index"], t["token"]): t["kind"] for t in narrative_map["tokens"]}
                self.assertEqual(kinds[(0, "18")], "numerator")
                self.assertEqual(kinds[(0, "21")], "denominator")
                self.assertEqual(kinds[(0, "85.7 %")], "formatted")
                self.assertEqual(kinds[(1, "44,000")], "value")
                self.assertEqual(kinds[(1, "0.044 USDC")], "formatted")
                self.assertIsNotNone(kinds[(2, "17")])            # also a coverage field of c01: any mapping is fine
                self.assertEqual(kinds[(2, "36")], "conflict_value")
                self.assertEqual(kinds[(2, "20")], "coverage")
                self.assertIsNone(kinds[(3, "99")])
                self.assertEqual({u["token"] for u in narrative_map["unsourced"]}, {"99", "[c07]"})
                self.assertEqual({i["target"] for i in issues}, {3, 4})
            finally:
                runner._cleanup(ctx)
                ctx.log.close()


if __name__ == "__main__":
    unittest.main()
