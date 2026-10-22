"""Keeper end to end over the committed fixtures with a stand-in RunContext (SPEC.md 6.3, 9.2, I8/I9). Owner: B2.

Covers: pass 1 ingest event + exports + world-ingest.json, chain facts (dict and Verification shapes), accepted
claims asserted as agent facts, the claim policy refusing a claim without evidence (critique
{kind: provenance_rejected} logged), the second pass {eventsAppended: 0, changed: false}, output.world shape,
and scripts/assert_idempotent.py replaying the run's exports green.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.agents import keeper  # noqa: E402
from investigator.clock import SampleClock  # noqa: E402
from investigator.contracts import Claim, Derivation, Evidence, Focus, Mounts, Observation, RunInput, Verification, Budget  # noqa: E402
from investigator.eventlog import EventLog  # noqa: E402
from investigator.worldmodel import views  # noqa: E402
from investigator.worldmodel.domain import investigation_context_id  # noqa: E402
from tests.test_adapters import FIXTURE_REQUESTS, PAYER, load_fixture  # noqa: E402

CAPTURED_AT = "2026-10-22T00:05:00.000Z"


def contract_observations() -> list[Observation]:
    out = []
    for name, (family, url, obs_name) in FIXTURE_REQUESTS.items():
        payload, digest = load_fixture(name)
        key = f"GET {url}" if family != "base_rpc" else f"RPC {obs_name}"
        out.append(Observation(name=obs_name, family=family, request_key=key, url=url, method="GET", http=200, bytes_sha256=digest,
                               latency_ms=0, captured_at=CAPTURED_AT, replayed=True, stale=False, status="ok", error=None,
                               payload=payload, fixture_file=name))
    out.append(Observation(name="trust_leaderboard_p1", family="x402_trust", request_key="GET https://x402-trust.com/trust/leaderboard?limit=25&offset=25",
                           url="https://x402-trust.com/trust/leaderboard?limit=25&offset=25", method="GET", http=None, bytes_sha256=None,
                           latency_ms=0, captured_at=CAPTURED_AT, replayed=True, stale=False, status="fixture_miss", error="no fixture", payload=None))
    return out


class Ctx(SimpleNamespace):
    @property
    def exports_dir(self) -> Path:
        path = self.run_dir / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path


def make_ctx(run_id: str = "keeper-test") -> Ctx:
    run_dir = Path(tempfile.mkdtemp(prefix="keeper-")) / run_id
    run_dir.mkdir(parents=True)
    clock = SampleClock(CAPTURED_AT)
    log = EventLog(run_id, run_dir, clock, echo_stdout=False, stdout=io.StringIO())
    inp = RunInput(question="How real is the adoption of tienda.textokit.store?", focus=Focus(host="tienda.textokit.store"),
                   budget=Budget(), options={"catalog": "snapshot", "zetaris_source": "captured"})
    mounts = Mounts(mode="sample", fetcher="fixture", executor="sqlite", generator="null", fixtures_captured_at=CAPTURED_AT,
                    zetaris_version=None, zetaris_source=None)
    return Ctx(run_id=run_id, run_dir=run_dir, clock=clock, log=log, world=None, hmem=None, inp=inp, mounts=mounts,
               counters={"critic_rounds": 2})


def read_events(ctx: Ctx) -> list[dict]:
    ctx.log.flush()
    return [json.loads(l) for l in (ctx.run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


class KeeperPipeline(unittest.TestCase):
    def test_full_keeper_cycle(self) -> None:
        ctx = make_ctx()
        observations = contract_observations()

        first = keeper.run_keeper_ingest(ctx, observations, pass_no=1)
        self.assertEqual(first.status, "ok", first.notes)
        self.assertTrue(first.payload["changed"])
        self.assertEqual(first.payload["processed"], len(observations) - 1)
        self.assertEqual(set(first.payload), {"runId", "processed", "skippedUnchanged", "eventsAppended", "changed"})
        self.assertTrue(any("fixture_miss" in n for n in first.notes), "a missing source is visible, never silent")
        self.assertTrue(any("adapter_skip" in n for n in first.notes))
        exports = ctx.run_dir / "exports"
        for name in ("world-entities.json", "world-relationships.json", "world-facts.json", "world-ingest.json"):
            self.assertTrue((exports / name).is_file(), name)
        for name in ("world.events.jsonl", "world.audit.jsonl", "world.observations.jsonl"):
            self.assertTrue((ctx.run_dir / name).is_file(), name)

        verification = Verification(tx="0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3", status="verified", payer=PAYER,
                                    seller="0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1", amount_atomic=1000, block=52841269,
                                    block_time="2026-10-20T04:51:25.000Z", facilitator_from="0x8f5cb67b49555e614892b7233cfddebfb746e531",
                                    basescan_url="https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3",
                                    decoded_from="Transfer log", receipt_match=True)
        unverifiable = Verification(tx="0xdead", status="unverifiable", payer=None, seller=None, amount_atomic=None, block=None, block_time=None,
                                    facilitator_from=None, basescan_url="https://basescan.org/tx/0xdead", decoded_from="", receipt_match=None,
                                    reason="fixture_miss")
        chain = keeper.run_keeper_chain_facts(ctx, [verification, unverifiable, {
            "about": f"wallet:base:{PAYER}", "predicate": "usdc_balance_atomic", "value": 1043148, "url": "https://mainnet.base.org",
            "at": CAPTURED_AT, "checksum": "ab" * 32, "confidence": 0.95}])
        self.assertEqual(chain.status, "ok", chain.notes)
        self.assertEqual(chain.payload["chain_facts_asserted"], 2)
        self.assertEqual(chain.payload["ignored"], 1)
        onchain = views.facts(ctx.world, about=f"wallet:base:{PAYER}", predicate="onchain_tx")
        self.assertTrue(all(f["source"]["kind"] == "chain" for f in onchain))

        good = Claim(id="c01", metric="external_payers_total", subject="seller:host:tienda.textokit.store", statement="x", value=4, unit="count",
                     evidence=[Evidence(kind="url", ref="https://tienda.textokit.store/receipts.json", sha256="ab" * 32, replayed=True)],
                     derivation=Derivation(agent="analyst", query_id="q02", sql_sha256=None, backend="sqlite", reducer="reducers.external_payers_total"),
                     confidence=0.9, status="accepted", cross_checked=True)
        logged = Claim(id="c02", metric="top_payer_share", subject="seller:host:tienda.textokit.store", statement="y",
                       value={"numerator": 18, "denominator": 21, "share": 18 / 21}, unit="share",
                       evidence=[Evidence(kind="log_line", ref=f"log://{ctx.run_id}#3")], confidence=0.85, status="accepted")
        bad = Claim(id="c03", metric="verdict", subject="seller:host:tienda.textokit.store", statement="z", value="self_settled_mostly", unit="text",
                    evidence=[], confidence=0.8, status="accepted")
        claims = keeper.run_keeper_claims(ctx, [good, logged, bad], observations)
        self.assertEqual(claims.payload["claims_asserted"], 2)
        self.assertEqual(claims.payload["quarantined"], 1)
        self.assertEqual(claims.payload["second_pass"]["eventsAppended"], 0)
        self.assertFalse(claims.payload["second_pass"]["changed"])
        self.assertEqual(claims.payload["second_pass"]["skippedUnchanged"], len(observations) - 1)
        self.assertEqual(claims.status, "degraded", "a refused claim is visible as degraded")

        events = read_events(ctx)
        ingest = [e for e in events if e["type"] == "ingest"]
        self.assertEqual([e["payload"]["pass"] for e in ingest], [1, 2])
        self.assertEqual(ingest[1]["payload"]["report"]["eventsAppended"], 0)
        self.assertFalse(ingest[1]["payload"]["report"]["changed"])
        critiques = [e for e in events if e["type"] == "critique"]
        self.assertEqual(len(critiques), 1)
        self.assertEqual(critiques[0]["payload"]["kind"], "provenance_rejected")
        self.assertEqual(critiques[0]["payload"]["target"], "c03")
        self.assertEqual(critiques[0]["payload"]["verdict"], "reject")

        inv = investigation_context_id(ctx.run_id)
        agent_facts = [f for f in views.facts(ctx.world, about="seller:host:tienda.textokit.store") if f["source"]["kind"] == "agent"]
        self.assertEqual(sorted(f["predicate"] for f in agent_facts), ["external_payers_total", "top_payer_share"])
        self.assertTrue(all(f["context"] == inv and f["source"]["runId"] == ctx.run_id for f in agent_facts))
        self.assertIn(inv, ctx.world.state.contexts)

        summary = keeper.world_summary(first.payload, claims.payload, ctx.world.counts())
        self.assertEqual(summary["second_pass"], {"runId": first.payload["runId"], "processed": 0,
                                                  "skippedUnchanged": len(observations) - 1, "eventsAppended": 0, "changed": False})
        self.assertEqual(summary["claims_asserted"], 2)
        self.assertEqual(summary["quarantined"], 1)
        self.assertIn("counts", summary)

        # the committed-example contract: the keeper never writes timings into output.world
        self.assertNotIn("stepTimingsMs", json.dumps(summary))

        # scripts/assert_idempotent.py replays the run's exports and must exit 0
        ctx.world.close()
        ctx.log.close()
        proc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "assert_idempotent.py"), str(ctx.run_dir)],
                              capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"changed": false', proc.stdout)

    def test_empty_observation_list_is_honest(self) -> None:
        ctx = make_ctx("keeper-empty")
        self.addCleanup(ctx.log.close)
        result = keeper.run_keeper_ingest(ctx, [], pass_no=1)
        self.assertEqual(result.payload["processed"], 0)
        self.assertFalse(result.payload["changed"])
        claims = keeper.run_keeper_claims(ctx, [], [])
        self.assertEqual(claims.payload["claims_asserted"], 0)
        self.assertFalse(claims.payload["second_pass"]["changed"])
        self.assertEqual(claims.status, "ok")


if __name__ == "__main__":
    unittest.main()
