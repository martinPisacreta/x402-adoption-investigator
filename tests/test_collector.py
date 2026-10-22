"""Collector agent over the committed fixtures: exports, raw copies, events, bounds, misses, provenance
(SPEC.md 6.2). Owner: B1.

The unit tests build Plan objects directly (the Collector only reads playbook, params and fetch_list), so they do
not depend on the Planner's internals; one test runs whatever the Planner currently builds through the Collector
and checks the generic invariants only."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import guard  # noqa: E402
from investigator.agents import collector  # noqa: E402
from investigator.config import build_config, parse_request  # noqa: E402
from investigator.contracts import Plan  # noqa: E402
from investigator.runner import RunContext, build_context  # noqa: E402
from investigator.sources import catalog as catalog_mod  # noqa: E402
from investigator.sources.manifest import load_manifest  # noqa: E402
from investigator.sources.registry import default_fetch_list, request_for  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"
MANIFEST = load_manifest(FIXTURES / "MANIFEST.json")
HOST = "tienda.textokit.store"


def read_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


OPEN_CONTEXTS: list[RunContext] = []


def make_ctx(tmp: str, example: str = "ex1.json", budget: Optional[dict[str, Any]] = None) -> RunContext:
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
    payload = json.loads((REPO_ROOT / "examples" / example).read_text(encoding="utf-8"))
    if budget:
        payload["input"]["budget"].update(budget)
    run_id, inp = parse_request(payload)
    ctx = build_context(run_id, inp, config, echo_stdout=False)
    ctx.start_ms = ctx.clock.now_ms()
    ctx.deadline_ms = ctx.start_ms + inp.budget.max_seconds * 1000
    if not hasattr(ctx, "scratch"):          # agents owned by B4 keep per-run notes here
        ctx.scratch = {}                     # type: ignore[attr-defined]
    OPEN_CONTEXTS.append(ctx)
    return ctx


def make_plan(playbook: str, params: dict[str, Any], fetch_list: Optional[list[dict[str, Any]]] = None) -> Plan:
    return Plan(playbook=playbook, params=params, fetch_list=list(fetch_list or []), query_ids=[], metrics=[],  # type: ignore[arg-type]
                tx_sample_rule="", hypotheses=[], planner_source="rules", hmem_memory_ids=[])


def events(ctx: RunContext, type_: Optional[str] = None) -> list[dict[str, Any]]:
    ctx.log.flush()
    lines = [json.loads(l) for l in (ctx.run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines()]
    return [e for e in lines if type_ is None or e["type"] == type_]


def side_lines(ctx: RunContext, name: str) -> list[dict[str, Any]]:
    ctx.log.flush()
    path = ctx.run_dir / f"{name}.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []


def export_file(ctx: RunContext, portable: str) -> Path:
    path = Path(portable)
    return path if path.is_absolute() else ctx.config.repo_root / path


class CollectorOverFixtures(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self) -> None:
        guard.uninstall_network_guard()
        while OPEN_CONTEXTS:
            OPEN_CONTEXTS.pop().log.close()
        self._tmp.cleanup()

    def test_seller_profile_fetches_every_source_from_fixtures(self) -> None:
        ctx = make_ctx(self.tmp)
        plan = make_plan("seller_profile", {"host": HOST})
        result = collector.run_collector(ctx, plan)
        self.assertEqual(result.status, "ok", result.notes)
        observations = result.payload
        expected = [it["name"] for it in default_fetch_list("seller_profile", plan.params, fixture_mode=True) if not it.get("deferred")]
        self.assertEqual([o.name for o in observations], expected)
        self.assertTrue(plan.fetch_list, "the collector filled the empty plan with the default list")
        for obs in observations:
            entry = MANIFEST.requests[obs.request_key]
            self.assertEqual(obs.status, "ok", obs.name)
            self.assertTrue(obs.replayed)
            self.assertFalse(obs.stale)
            self.assertEqual(obs.bytes_sha256, entry.sha256)
            export = export_file(ctx, obs.export_path)
            self.assertTrue(export.is_file(), obs.export_path)
            self.assertEqual(json.loads(export.read_text(encoding="utf-8")), obs.payload)
            raw = ctx.run_dir / "raw" / f"{obs.name}{Path(entry.file).suffix}"
            self.assertTrue(raw.is_file(), raw)
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), entry.sha256)
        fetch_events = events(ctx, "fetch")
        self.assertEqual(len(fetch_events), len(observations))
        for event in fetch_events:
            for key in ("name", "family", "url", "method", "http", "bytes", "sha256", "latency_ms", "replayed", "stale"):
                self.assertIn(key, event["payload"])
            self.assertTrue(event["payload"]["replayed"])
            self.assertGreater(event["payload"]["bytes"], 0)
        recoveries = [e["payload"]["reason"] for e in events(ctx, "recovery")]
        self.assertEqual(recoveries, ["planner_fallback"])
        self.assertEqual(ctx.counters["fetches"], len(observations))
        side = side_lines(ctx, "fetch")
        self.assertEqual(len(side), len(observations) + 1, "one side line per request plus the derived openapi_paid_ops")
        self.assertTrue(all("export_path" in l for l in side))
        self.assertTrue(any("deferred" in n for n in result.notes))

    def test_openapi_paid_ops_is_flattened_from_the_openapi_fixture(self) -> None:
        ctx = make_ctx(self.tmp)
        observations = collector.run_collector(ctx, make_plan("seller_profile", {"host": HOST})).payload
        openapi = read_fixture("store-openapi.json")
        paid = [(p, m) for p, ops in openapi["paths"].items() for m, op in ops.items() if isinstance(op, dict) and op.get("x-payment-info")]
        rows = json.loads((ctx.run_dir / "exports" / "openapi_paid_ops.json").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), len(paid))
        self.assertEqual(sorted(r["path"] for r in rows), sorted(p for p, _ in paid))
        self.assertTrue(all(set(r) >= {"path", "operation_id", "price_usd", "network", "asset", "pay_to"} for r in rows))
        self.assertTrue(all(r["pay_to"] == r["pay_to"].lower() for r in rows))
        exports = collector.exports_map(ctx, observations)
        self.assertIn("openapi_paid_ops", exports)
        self.assertIn("store_receipts", exports)
        self.assertTrue(all(Path(p).is_file() for p in exports.values()))

    def test_catalog_playbook_uses_the_snapshot_and_the_committed_pages(self) -> None:
        ctx = make_ctx(self.tmp, "ex2.json")
        result = collector.run_collector(ctx, make_plan("catalog_concentration", {"query": "cdp bazaar"}))
        self.assertEqual(result.status, "ok", result.notes)
        by_name = {o.name: o for o in result.payload}
        self.assertIn("cdp_catalog_snapshot", by_name)
        self.assertEqual([n for n in by_name if n.startswith("cdp_page_")], ["cdp_page_0", "cdp_page_1"])
        snapshot = by_name["cdp_catalog_snapshot"]
        self.assertTrue(snapshot.replayed)
        self.assertEqual(snapshot.captured_at, snapshot.payload["fetchedAt"])
        self.assertEqual(snapshot.url, snapshot.payload["source"])
        summary = catalog_mod.snapshot_summary(snapshot.payload)
        self.assertEqual(summary["items_seen"], len(snapshot.payload["items"]))
        self.assertEqual(summary["total_reported"], snapshot.payload["pagination"]["totalReportedByApi"])
        self.assertEqual(summary["basis"], catalog_mod.SNAPSHOT_BASIS)
        self.assertLessEqual(summary["share"], 1.0)
        self.assertEqual(ctx.counters["fetches"], len(result.payload) - 1, "the snapshot is not a fetch")
        self.assertTrue((ctx.run_dir / "raw" / "cdp_catalog_snapshot.json.gz").is_file())
        page0 = read_fixture("cdp-discovery-resources-p0.json")
        self.assertEqual(len(by_name["cdp_page_0"].payload["items"]), len(page0["items"]))
        coverage = catalog_mod.pages_coverage([by_name["cdp_page_0"], by_name["cdp_page_1"]])
        self.assertEqual(coverage["pages"], 2)
        self.assertEqual(coverage["basis"], "live pages (2 x 100)")
        self.assertEqual(coverage["total_reported"], page0["pagination"]["total"])
        merged = catalog_mod.merge_pages([by_name["cdp_page_0"], by_name["cdp_page_1"]])
        self.assertEqual(len(merged["items"]), coverage["items_seen"])
        self.assertIn("crawl", merged["note"])

    def test_planner_built_list_runs_through_the_collector(self) -> None:
        """Whatever the Planner builds today must fetch cleanly from fixtures (generic invariants only)."""
        from investigator.agents import planner
        ctx = make_ctx(self.tmp)
        try:
            plan = planner.run_planner(ctx).payload
        except (AttributeError, KeyError) as exc:   # Planner mid-build (B4); the Collector is not at fault
            self.skipTest(f"planner not runnable yet: {exc!r}")
        result = collector.run_collector(ctx, plan)
        self.assertIn(result.status, ("ok", "degraded"), result.notes)
        attempted = [o for o in result.payload if o.error != "deadline_skip"]
        self.assertTrue(attempted)
        bad = [(o.name, o.status, o.error) for o in attempted if o.status != "ok"]
        self.assertEqual(bad, [], "every planner item for the committed example must have a fixture")
        self.assertTrue(all(o.replayed for o in attempted))

    def test_fixture_miss_is_visible_and_degrades(self) -> None:
        ctx = make_ctx(self.tmp)
        plan = make_plan("seller_profile", {"host": HOST}, [
            request_for("receipts", "store_receipts", host=HOST, optional=False),
            request_for("vet402", "vet402_resolve", host="nobody.example")])
        result = collector.run_collector(ctx, plan)
        self.assertEqual(result.status, "degraded")
        miss = [o for o in result.payload if o.name == "vet402_resolve"][0]
        self.assertEqual(miss.status, "fixture_miss")
        self.assertIsNone(miss.payload)
        self.assertIsNone(miss.export_path)
        self.assertFalse((ctx.run_dir / "exports" / "vet402_resolve.json").exists())
        reasons = [e["payload"]["reason"] for e in events(ctx, "recovery")]
        self.assertIn("fixture_miss", reasons)
        self.assertTrue(any("fixture_miss" in n for n in result.notes))

    def test_max_fetches_is_a_hard_bound(self) -> None:
        ctx = make_ctx(self.tmp, budget={"max_fetches": 2})
        result = collector.run_collector(ctx, make_plan("seller_profile", {"host": HOST}))
        fetched = [o for o in result.payload if o.status == "ok"]
        skipped = [o for o in result.payload if o.status == "unavailable"]
        self.assertEqual(len(fetched), 2)
        self.assertEqual(len(events(ctx, "fetch")), 2)
        self.assertTrue(skipped)
        self.assertTrue(all("budget_exhausted" in (o.error or "") for o in skipped))
        self.assertIn("budget_exhausted", [e["payload"]["reason"] for e in events(ctx, "recovery")])
        self.assertEqual(ctx.counters["fetches"], 2)
        self.assertEqual(result.status, "degraded")

    def test_deadline_skips_every_remaining_request(self) -> None:
        ctx = make_ctx(self.tmp)
        ctx.deadline_ms = ctx.start_ms          # already passed
        result = collector.run_collector(ctx, make_plan("seller_profile", {"host": HOST}))
        self.assertEqual(events(ctx, "fetch"), [])
        self.assertTrue(all(o.status == "unavailable" and o.error == "deadline_skip" for o in result.payload))
        self.assertEqual(len(events(ctx, "timeout")), 1)
        self.assertGreaterEqual(ctx.counters["deadline_skips"], 1)
        self.assertEqual(result.status, "failed", "receipts and the Bazaar search are mandatory")

    def test_all_mandatory_sources_missing_fails_the_agent(self) -> None:
        """failed -> the runner raises E_NO_SOURCES (runner.py maps a failed collector to that code). Host-bound
        sources miss for an unknown host; host-independent ones (trust stats, route) still replay, honestly."""
        ctx = make_ctx(self.tmp)
        plan = make_plan("seller_profile", {"host": "nobody.example"})
        result = collector.run_collector(ctx, plan)
        self.assertEqual(result.status, "failed")
        self.assertTrue(any("every mandatory source is unavailable" in n for n in result.notes))
        mandatory = [it["name"] for it in plan.fetch_list if not it.get("optional", True)]
        self.assertEqual(sorted(mandatory), ["cdp_search_host", "store_receipts"])
        by_name = {o.name: o for o in result.payload}
        self.assertTrue(all(by_name[n].status == "fixture_miss" for n in mandatory))
        self.assertFalse(any((ctx.run_dir / "exports" / f"{n}.json").exists() for n in mandatory))
        self.assertEqual(by_name["trust_stats"].status, "ok", "host-independent sources are not faked as missing")

    def test_planner_style_rpc_probe_is_fetched_as_post(self) -> None:
        """The Planner's liveness probe item (method "RPC", not deferred) resolves to the batch fixture element."""
        from investigator.chain.base_rpc import call_chain_id, rpc_call
        from investigator.sources.registry import rpc_request_key
        call = call_chain_id()
        item = {"name": "rpc_chain_id", "family": "base_rpc", "request_key": rpc_request_key(call["method"], call["params"]),
                "url": "https://mainnet.base.org", "method": "RPC", "body": rpc_call(call["method"], call["params"], 1),
                "optional": True, "priority": 20}
        ctx = make_ctx(self.tmp)
        plan = make_plan("seller_profile", {"host": HOST}, [request_for("receipts", "store_receipts", optional=False), item])
        result = collector.run_collector(ctx, plan)
        probe = [o for o in result.payload if o.name == "rpc_chain_id"][0]
        self.assertEqual(probe.status, "ok")
        self.assertEqual(probe.method, "POST")
        self.assertEqual(probe.payload.get("result"), "0x2105")

    def test_never_in_a_run_endpoint_is_refused(self) -> None:
        ctx = make_ctx(self.tmp)
        plan = make_plan("seller_profile", {"host": HOST}, [
            request_for("receipts", "store_receipts", host=HOST, optional=False),
            request_for("x402_trust", "trust_semantic_search", body={"query": "x", "limit": 10})])
        result = collector.run_collector(ctx, plan)
        semantic = [o for o in result.payload if o.name == "trust_semantic_search"][0]
        self.assertEqual(semantic.status, "unavailable")
        self.assertIn("never fetched", semantic.error or "")

    def test_provenance_and_source_summary(self) -> None:
        ctx = make_ctx(self.tmp, "ex3.json")
        observations = collector.run_collector(ctx, make_plan("payer_dossier", {"host": HOST, "wallet": "0x" + "0" * 40})).payload
        receipts = [o for o in observations if o.family == "receipts"][0]
        prov = collector.provenance_for(receipts, ctx.run_id)
        self.assertEqual(prov["kind"], "receipts")
        self.assertEqual(prov["url"], "https://tienda.textokit.store/receipts.json")
        self.assertEqual(prov["checksum"], f"sha256:{receipts.bytes_sha256}")
        self.assertEqual(prov["by"], "fixture:fixtures/raw/store-receipts.json")
        self.assertEqual(prov["runId"], ctx.run_id)
        self.assertEqual(prov["at"], receipts.captured_at)
        search = [o for o in observations if o.family == "cdp_search"][0]
        self.assertEqual(collector.provenance_for(search, ctx.run_id)["kind"], "registry")
        row = collector.source_summary(receipts)
        for key in ("name", "family", "url", "status", "http", "sha256", "captured_at", "replayed", "stale"):
            self.assertIn(key, row)
        self.assertTrue(row["replayed"])

    def test_unwritable_export_path_is_a_clear_error(self) -> None:
        import errno
        from investigator.errors import InvestigatorError
        ctx = make_ctx(self.tmp)
        target = ctx.exports_dir / "store_receipts.json"

        def refuse(path: Path) -> None:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path))

        with self.assertRaises(InvestigatorError) as caught:
            collector._write_or_explain(target, refuse, "collector")
        self.assertEqual(caught.exception.code, "E_INTERNAL")
        self.assertEqual(caught.exception.stage, "collector")
        self.assertIn("cannot write store_receipts.json", caught.exception.message)
        self.assertEqual(caught.exception.details["path"], str(target.resolve()))
        self.assertEqual(caught.exception.details["path_length"], len(str(target.resolve())))
        self.assertEqual(caught.exception.details["errno"], errno.ENOENT)
        long_path = Path(str(target.parent) + "/" + "x" * 300 + ".json")
        with self.assertRaises(InvestigatorError) as caught:
            collector._write_or_explain(long_path, refuse, "collector")
        if sys.platform == "win32":
            self.assertIn("MAX_PATH", caught.exception.message)

    def test_normalized_exports_are_opt_in(self) -> None:
        ctx = make_ctx(self.tmp)
        observations = collector.run_collector(ctx, make_plan("seller_profile", {"host": HOST})).payload
        folder = ctx.run_dir / "exports" / "normalized"
        self.assertFalse(folder.exists(), "fetch_all never writes normalized exports by itself")
        written = collector.write_normalized_exports(ctx, observations)
        self.assertIn("sales", written)
        sales = json.loads((folder / "sales.json").read_text(encoding="utf-8"))
        receipts = read_fixture("store-receipts.json")
        self.assertEqual(sales["row_count"], len(receipts["sales"]))
        self.assertEqual(sales["sources"][0]["sha256"], MANIFEST.requests["GET https://tienda.textokit.store/receipts.json"].sha256)
        self.assertEqual(sales["columns"][:4], ["seller", "agent_id", "settled_at", "payer"])
        self.assertTrue((folder / "bazaar_search.json").is_file())
        self.assertTrue((folder / "openapi_paid_ops.json").is_file())


if __name__ == "__main__":
    unittest.main()
