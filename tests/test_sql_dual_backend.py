"""Every catalog query on SqliteExecutor vs the recorded Zetaris result sets: equal typed rows (SPEC.md 10.4). Owner: B3.

The ANSI lint and the catalog/meta consistency checks run on the committed SQL. The dual-backend comparison
builds the run exports straight from fixtures/raw (investigator/zetaris/fixture_exports.py), runs every query
on the SQLite backend and compares headers and typed rows with fixtures/zetaris/<qNN>.json, which
scripts/record_zetaris_results.py recorded from the live Zetaris 2.4.3.1 over the SAME exports (the recording
carries the sha256 of every export and of the SQL file, so a stale recording fails loudly instead of passing
by accident). No network is needed: the recordings are committed.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.agents.metrics import METRICS, query_ids_for_metrics  # noqa: E402
from investigator.agents.reducers import REDUCERS, get_extractor  # noqa: E402
from investigator.zetaris.catalog import ANSI_FORBIDDEN, bind_params, lint, load_catalog, referenced_views  # noqa: E402
from investigator.zetaris.executor import SqliteExecutor  # noqa: E402
from investigator.zetaris.fixture_exports import build_fixture_exports, default_params, exports_sha256  # noqa: E402
from investigator.zetaris.views_sqlite import VIEW_NAMES  # noqa: E402

EXPECTED_QUERY_IDS = [f"q{n:02d}" for n in range(1, 23)]
# Queries added on 2026-10-22 after the Zetaris recording session, so that every registry metric has an SQL half
# (no Zetaris login was available afterwards). They run on SQLite in every sample run, where each claim compares
# them with its reducer, but fixtures/zetaris/ holds no recording of them yet. The set is explicit so a new query
# cannot silently skip the recording check; scripts/record_zetaris_results.py --from-fixtures records them.
NOT_YET_RECORDED = frozenset({"q20_catalog_unique_payers_max", "q21_catalog_top_hosts_by_listings", "q22_payer_chain_facts"})
RECORDINGS_DIR = REPO_ROOT / "fixtures" / "zetaris"
FLOAT_TOLERANCE = 1e-9


class CatalogLint(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_catalog(REPO_ROOT / "investigator" / "sql")

    def test_every_query_with_meta(self) -> None:
        prefixes = sorted(q[:3] for q in self.catalog)
        self.assertEqual(prefixes, EXPECTED_QUERY_IDS)
        for spec in self.catalog.values():
            self.assertEqual(spec.meta.get("id"), spec.id)
            self.assertTrue(spec.meta.get("ansi_only"), spec.id)
            self.assertTrue(spec.columns, spec.id)
            self.assertEqual(len(spec.sql_sha256), 64)
            self.assertEqual(spec.meta.get("sql_sha256"), spec.sql_sha256)

    def test_ansi_subset(self) -> None:
        for spec in self.catalog.values():
            self.assertEqual(lint(spec.sql), [], spec.id)
            for token in ANSI_FORBIDDEN:
                self.assertNotIn(token.lower(), spec.sql.lower(), spec.id)

    def test_every_query_reads_known_views_only(self) -> None:
        for spec in self.catalog.values():
            views = referenced_views(spec.sql)
            self.assertTrue(views, spec.id)
            for view in views:
                self.assertIn(view, VIEW_NAMES, f"{spec.id} reads {view}")

    def test_no_view_is_referenced_twice(self) -> None:
        """Zetaris rejects a second reference to the same exploded view (MISSING_ATTRIBUTES, verified live):
        totals must come from window functions, never from a scalar subquery over the same view."""
        for spec in self.catalog.values():
            lowered = spec.sql.lower()
            for view in referenced_views(spec.sql):
                hits = lowered.count(f"from {view.lower()}") + lowered.count(f"join {view.lower()}")
                self.assertEqual(hits, 1, f"{spec.id} references {view} {hits} times")

    def test_params_bind_with_quoting(self) -> None:
        q06 = self.catalog["q06_bazaar_host_listings"]
        bound = bind_params(q06.sql, {"host": "tienda.textokit.store"}, q06.meta)
        self.assertIn("host = 'tienda.textokit.store'", bound)
        self.assertNotIn(":host", bound)
        q17 = self.catalog["q17_payer_dossier"]
        bound = bind_params(q17.sql, {"wallet": "0x0'; DROP TABLE sales; --"}, q17.meta)
        self.assertIn("''", bound)
        with self.assertRaises(KeyError):
            bind_params(q17.sql, {}, q17.meta)

    def test_every_metric_names_a_catalog_query(self) -> None:
        for metric in METRICS.values():
            self.assertIn(metric.query_id, self.catalog, metric.id)

    def test_every_metric_is_computed_twice(self) -> None:
        """The structural half of "every metric is computed twice": each registry metric has a committed query,
        an SQL extractor and a pure-Python reducer. The Critic's C2 rejects any claim whose two values are
        missing or disagree (tests/test_dual_computation.py)."""
        for metric in METRICS.values():
            self.assertTrue(metric.sql_backed, metric.id)
            self.assertIsNotNone(get_extractor(metric.id), f"{metric.id} has no SQL extractor")
            self.assertIn(metric.reducer, REDUCERS, metric.id)
            self.assertIn(metric.query_id, query_ids_for_metrics([metric.id]), metric.id)


class DualBackend(unittest.TestCase):
    """SQLite over the fixture exports == Zetaris recordings over the same exports, query by query."""

    tmp: str
    exports: dict[str, str]
    executor: SqliteExecutor

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="x402-dual-")
        cls.exports = build_fixture_exports(REPO_ROOT / "fixtures" / "raw", Path(cls.tmp) / "exports")
        cls.executor = SqliteExecutor(Path(cls.tmp))
        cls.executor.ensure_views(cls.exports)
        cls.catalog = load_catalog(REPO_ROOT / "investigator" / "sql")
        cls.params = default_params(cls.exports)
        cls.shas = exports_sha256(cls.exports)
        cls.recordings = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECORDINGS_DIR.glob("q*.json"))}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.executor.cleanup()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _require_recordings(self) -> None:
        if not self.recordings:
            self.skipTest("fixtures/zetaris/ holds no recordings; run scripts/record_zetaris_results.py --from-fixtures")

    def test_all_views_built_from_exports(self) -> None:
        by_name = {t["name"]: t for t in self.executor.tables}
        self.assertEqual(sorted(by_name), sorted(VIEW_NAMES))
        for name, table in by_name.items():
            self.assertTrue(table["present"], f"view {name} has no export")
            self.assertGreater(table["rows"], 0, f"view {name} is empty")

    def test_every_query_has_a_recording(self) -> None:
        self._require_recordings()
        self.assertEqual(sorted(self.recordings), sorted(set(self.catalog) - NOT_YET_RECORDED))
        self.assertTrue(NOT_YET_RECORDED <= set(self.catalog))
        for qid, rec in self.recordings.items():
            self.assertEqual(rec.get("backend"), "zetaris", f"{qid} was not recorded on Zetaris: {rec.get('fallback_reason')}")
            self.assertTrue(rec.get("zetaris_version"), qid)

    def test_recordings_match_current_sql_and_exports(self) -> None:
        self._require_recordings()
        for qid, rec in self.recordings.items():
            self.assertEqual(rec["sql_sha256"], self.catalog[qid].sql_sha256,
                             f"{qid}: the SQL changed after the recording; re-record")
            for name, sha in rec.get("exports_sha256", {}).items():
                if name in self.shas:
                    self.assertEqual(sha, self.shas[name], f"{qid}: export {name} differs from the recording; re-record")

    def test_every_query_equal_rows_on_sqlite_and_zetaris(self) -> None:
        self._require_recordings()
        for qid, rec in self.recordings.items():
            spec = self.catalog[qid]
            result = self.executor.execute(qid, spec.sql, rec.get("params") or {}, spec.meta)
            self.assertEqual(result.headers, rec["headers"], qid)
            self.assertEqual(result.rowcount, rec["rowcount"], qid)
            self.assertEqual(len(result.rows), len(rec["rows"]), qid)
            for i, (mine, theirs) in enumerate(zip(result.rows, rec["rows"])):
                for header, a, b in zip(result.headers, mine, theirs):
                    if isinstance(a, float) or isinstance(b, float):
                        self.assertIsNotNone(a, f"{qid} row {i} {header}")
                        self.assertIsNotNone(b, f"{qid} row {i} {header}")
                        self.assertAlmostEqual(float(a), float(b), delta=FLOAT_TOLERANCE, msg=f"{qid} row {i} {header}")
                    else:
                        self.assertEqual(a, b, f"{qid} row {i} column {header}: sqlite={a!r} zetaris={b!r}")

    def test_default_params_come_from_exports(self) -> None:
        self.assertIn("host", self.params)
        self.assertIn("wallet", self.params)
        self.assertTrue(self.params["wallet"].startswith("0x"))
        self.assertEqual(self.params["wallet"], self.params["wallet"].lower())


if __name__ == "__main__":
    unittest.main()
