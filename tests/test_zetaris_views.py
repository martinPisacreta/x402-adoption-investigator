"""View layer unit tests: Spark-parity scalar rules, SQLite row builders, shape-aware SchemaStore DDL, view
qualification, typed casting and the DDL builders (SPEC.md 10.1, 10.3). Owner: B3."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.zetaris import ddl  # noqa: E402
from investigator.zetaris.catalog import referenced_views, sql_sha256_for  # noqa: E402
from investigator.zetaris.executor import backend_label, cast_cell, cast_rows  # noqa: E402
from investigator.contracts import SqlResult  # noqa: E402
from investigator.zetaris.views_sqlite import (  # noqa: E402
    VIEWS, VIEW_NAMES, bigint, build_views, digits_bigint, double, flag, host_of, rows_bazaar, rows_sales,
    rows_settlements, rows_trust_search,
)
from investigator.zetaris.views_zetaris import shape_of, view_select, view_tables  # noqa: E402


class HostParity(unittest.TestCase):
    """host_of() must return NULL exactly where Spark's parse_url (java.net.URI) does."""

    def test_plain_host_lowercased_without_port(self) -> None:
        self.assertEqual(host_of("https://Tienda.Textokit.Store/api/x"), "tienda.textokit.store")
        self.assertEqual(host_of("https://numba2witcheese.tail7d8d44.ts.net:10000/x402/demand"), "numba2witcheese.tail7d8d44.ts.net")

    def test_second_hash_is_null(self) -> None:
        self.assertIsNone(host_of("https://api.scrapebench.dev/mcp#edgar-signals#edgar-signals"))
        self.assertEqual(host_of("https://api.scrapebench.dev/mcp#one"), "api.scrapebench.dev")

    def test_illegal_characters_are_null(self) -> None:
        self.assertIsNone(host_of("https://pro-api.coingecko.com/api/v3/token_price/i%20Af8E=&I%7Df]*'!Z$"))
        self.assertIsNone(host_of("https://example.com/a b"))
        self.assertIsNone(host_of("https://example.com/{id}"))
        self.assertIsNone(host_of("https://example.com/x%zz"))
        self.assertIsNone(host_of("example.com/api"))
        self.assertIsNone(host_of(""))
        self.assertIsNone(host_of(None))

    def test_percent_escapes_and_unicode_pass(self) -> None:
        self.assertEqual(host_of("https://example.com/a%20b?q=%7B"), "example.com")
        self.assertEqual(host_of("https://example.com/café"), "example.com")


class ScalarParity(unittest.TestCase):
    def test_bigint(self) -> None:
        self.assertEqual(bigint(5), 5)
        self.assertEqual(bigint("5"), 5)
        self.assertEqual(bigint(" -7 "), -7)
        self.assertEqual(bigint("5.7"), 5)
        self.assertEqual(bigint(5.9), 5)
        self.assertIsNone(bigint("abc"))
        self.assertIsNone(bigint(None))
        self.assertIsNone(bigint("1" + "0" * 30))
        self.assertEqual(bigint(True), 1)

    def test_digits_bigint(self) -> None:
        self.assertEqual(digits_bigint("5000"), 5000)
        self.assertIsNone(digits_bigint("0.002"))
        self.assertIsNone(digits_bigint("-1"))
        self.assertIsNone(digits_bigint("1" + "0" * 30))
        self.assertIsNone(digits_bigint(None))

    def test_double_and_flag(self) -> None:
        self.assertEqual(double("3209008.575"), 3209008.575)
        self.assertIsNone(double("x"))
        self.assertEqual(flag(True), 1)
        self.assertEqual(flag(False), 0)
        self.assertEqual(flag(None), 0)
        self.assertEqual(flag("true"), 0)


class RowBuilders(unittest.TestCase):
    def test_sales_rows(self) -> None:
        receipts = {"seller": "0xABC", "agentId": 98748, "sales": [
            {"at": "2026-10-20T00:00:00Z", "amountAtomic": "1000", "payer": "0xDEF", "ownWallet": False,
             "transaction": "0x1", "x402Version": 2, "service": "ping"},
            {"at": "2026-10-19T00:00:00Z", "amountAtomic": "50", "payer": "0xabc", "ownWallet": True, "transaction": "0x2",
             "x402Version": 2}]}
        rows = rows_sales(receipts)
        self.assertEqual(rows[0], ("0xabc", "98748", "2026-10-20T00:00:00Z", "0xdef", 0, 1000, "0x1", "2", "ping", None, None))
        self.assertEqual(rows[1][4], 1)
        self.assertEqual(len(rows[0]), len(VIEWS["sales"].columns))

    def test_bazaar_rows_use_primary_accept(self) -> None:
        items = [{"resource": "https://H.example/api", "serviceName": "S", "type": "http", "lastUpdated": "t",
                  "quality": {"l30DaysTotalCalls": 4, "l30DaysUniquePayers": 2, "lastCalledAt": "c"},
                  "accepts": [{"payTo": "0xAA", "network": "eip155:8453", "asset": "0xU", "maxAmountRequired": "5000", "scheme": "exact"},
                              {"payTo": "0xBB", "network": "solana", "asset": "x", "amount": "0.002"}]},
                 {"resource": "https://x.example/mcp#a#b", "accepts": []}]
        rows = rows_bazaar(items, 3)
        self.assertEqual(rows[0], ("https://H.example/api", "h.example", "S", "http", "t", 4, 2, "c", "0xaa", "eip155:8453", "0xU", 5000, "exact", 3))
        self.assertEqual(rows[1][1], None)
        self.assertEqual(rows[1][8], None)
        self.assertEqual(len(rows[0]), len(VIEWS["bazaar_resources"].columns))
        self.assertEqual(len(rows_bazaar(items, None)[0]), len(VIEWS["bazaar_search"].columns))

    def test_settlements_accept_array_or_wrapper(self) -> None:
        row = {"tx_hash": "0xAB", "payer": "0xP", "seller": "0xS", "amount_atomic": 1000, "block_number": 7,
               "block_time": "t", "facilitator_from": "0xF", "status": "verified"}
        self.assertEqual(rows_settlements([row]), rows_settlements({"rows": [row]}))
        self.assertEqual(rows_settlements([row])[0][0], "0xab")

    def test_trust_search_total_on_every_row(self) -> None:
        rows = rows_trust_search({"total": 36, "results": [{"resource": "r", "host": "H", "delisted": False, "score": 38,
                                                              "grade": "D", "providerVerified": True}]})
        self.assertEqual(rows, [("r", "h", 0, 38, "D", 1, 36)])

    def test_build_views_with_missing_exports_gives_empty_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipts = Path(tmp) / "store_receipts.json"
            receipts.write_text(json.dumps({"seller": "0xS", "agentId": "1", "sales": [], "purchases": []}), encoding="utf-8")
            conn = sqlite3.connect(":memory:")
            tables = build_views(conn, {"store_receipts": str(receipts)})
            by_name = {t["name"]: t for t in tables}
            self.assertEqual(sorted(by_name), sorted(VIEW_NAMES))
            self.assertTrue(by_name["sales"]["present"])
            self.assertFalse(by_name["catalog_resources"]["present"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM catalog_resources").fetchone()[0], 0)
            self.assertEqual([r[1] for r in conn.execute("PRAGMA table_info(sales)")], VIEWS["sales"].column_names)


class ZetarisDdl(unittest.TestCase):
    def test_run8_and_names(self) -> None:
        self.assertEqual(len(ddl.run8("ex01-seller-audit")), 8)
        self.assertEqual(ddl.database_name("x"), "X402_" + ddl.run8("x"))
        self.assertEqual(ddl.container_name("x"), "x402_" + ddl.run8("x").lower())

    def test_describe_by_sanitised(self) -> None:
        self.assertNotIn("(", ddl.create_database("run (1)/2:3"))

    def test_rest_table_and_drop_forms(self) -> None:
        stmt = ddl.create_rest_table("r", "receipts_raw", 'http://host.docker.internal:1/t/receipts.json')
        self.assertTrue(stmt.startswith("CREATE LIGHTNING REST TABLE receipts_raw FROM X402_"))
        self.assertIn('method "get", response_type "json", http_encoding "URLENCODED"', stmt)
        self.assertIn('HEADER (user-agent "x402-adoption-investigator") BODY ()', stmt)
        self.assertEqual(ddl.drop_view("sales", "r"), f"DROP SCHEMASTORE VIEW sales FROM {ddl.container_name('r')}")
        self.assertEqual(ddl.drop_table("r", "receipts_raw"), f"DROP LIGHTNING DATABASE TABLE {ddl.database_name('r')}.receipts_raw")
        with self.assertRaises(ValueError):
            ddl.create_rest_table("r", "bad name", "http://x")

    def test_qualify_views(self) -> None:
        c = ddl.container_name("r")
        sql = "SELECT t.payer, (SELECT COUNT(*) FROM sales WHERE own_wallet = 0) AS n FROM (SELECT payer FROM sales) t INNER JOIN settlements st ON 1 = 1 ORDER BY sales_total"
        out = ddl.qualify_views(sql, "r", VIEW_NAMES)
        self.assertEqual(out.count(f"FROM {c}.sales"), 2)
        self.assertIn(f"JOIN {c}.settlements", out)
        self.assertIn("ORDER BY sales_total", out)
        self.assertNotIn(f"{c}.sales_total", out)
        self.assertEqual(ddl.qualify_views(out, "r", VIEW_NAMES), out)

    def test_referenced_views(self) -> None:
        self.assertEqual(referenced_views("SELECT 1 FROM sales s JOIN settlements st ON 1=1 WHERE x IN (SELECT 1 FROM trust_stats)"),
                         ["sales", "settlements", "trust_stats"])
        self.assertEqual(referenced_views("SELECT * FROM (SELECT 1 FROM sales) t", VIEW_NAMES), ["sales"])


class ShapeAwareDdl(unittest.TestCase):
    def test_view_tables(self) -> None:
        self.assertEqual(view_tables("sales"), ["receipts_raw"])
        self.assertEqual(view_tables("bazaar_resources", [0, 2]), ["bazaar_page0", "bazaar_page2"])

    def test_sales_view_emits_nulls_for_absent_fields(self) -> None:
        shape = shape_of({"seller": "0x", "agentId": "1", "sales": [{"at": "t", "payer": "p", "ownWallet": True, "amountAtomic": "1", "transaction": "0x", "x402Version": 2}]})
        sql = view_select("sales", "r", {"receipts_raw": "DB.receipts_raw"}, {"receipts_raw": shape})
        self.assertIn("LATERAL VIEW explode(sales) AS s", sql)
        self.assertIn("CAST(NULL AS STRING) AS facilitator", sql)
        self.assertIn("lower(s.payer) AS payer", sql)
        self.assertIn("CASE WHEN s.`ownWallet` THEN 1 ELSE 0 END AS own_wallet", sql)

    def test_listing_amount_coalesce_only_when_present(self) -> None:
        with_max = shape_of({"items": [{"resource": "r", "quality": {"l30DaysTotalCalls": 1}, "accepts": [{"payTo": "p", "amount": "1"}]},
                                       {"resource": "r2", "accepts": [{"payTo": "p", "maxAmountRequired": "2"}]}]})
        sql = view_select("catalog_resources", "r", {"catalog_snapshot": "DB.catalog_snapshot"}, {"catalog_snapshot": with_max})
        self.assertIn("COALESCE(i.accepts[0].amount, i.accepts[0].`maxAmountRequired`)", sql)
        self.assertIn("RLIKE '^[0-9]+$'", sql)
        self.assertIn("lower(parse_url(i.resource, 'HOST')) AS host", sql)
        without = shape_of({"resources": [{"resource": "r", "quality": {}, "accepts": [{"payTo": "p", "amount": "1"}]}]})
        sql = view_select("bazaar_search", "r", {"bazaar_search_host": "DB.s"}, {"bazaar_search_host": without})
        self.assertNotIn("maxAmountRequired", sql)
        self.assertIn("CAST(NULL AS BIGINT) AS calls_30d", sql)

    def test_pages_union_all(self) -> None:
        sql = view_select("bazaar_resources", "r", {"bazaar_page0": "DB.bazaar_page0", "bazaar_page1": "DB.bazaar_page1"})
        self.assertEqual(sql.count(" UNION ALL "), 1)
        self.assertIn("0 AS page FROM DB.bazaar_page0", sql)
        self.assertIn("1 AS page FROM DB.bazaar_page1", sql)
        with self.assertRaises(ValueError):
            view_select("bazaar_resources", "r", {})

    def test_array_and_wrapped_sources(self) -> None:
        array_shape = shape_of([{"tx_hash": "0x", "payer": "p", "seller": "s", "amount_atomic": 1, "block_number": 1,
                                 "block_time": "t", "facilitator_from": "f", "status": "verified"}])
        sql = view_select("settlements", "r", {"settlements_raw": "DB.settlements_raw"}, {"settlements_raw": array_shape})
        self.assertTrue(sql.endswith("FROM DB.settlements_raw"), sql)
        self.assertNotIn("explode", sql)
        wrapped = shape_of({"rows": [{"path": "/a", "operationId": "a", "priceUsd": "0.1", "network": "n", "asset": "x", "payTo": "P"}]})
        sql = view_select("openapi_paid_ops", "r", {"openapi_paid_ops_raw": "DB.o"}, {"openapi_paid_ops_raw": wrapped})
        self.assertIn("LATERAL VIEW explode(rows) AS r", sql)
        self.assertIn("r.`operationId` AS operation_id", sql)
        self.assertIn("lower(r.`payTo`) AS pay_to", sql)

    def test_every_view_has_a_select(self) -> None:
        for view in VIEW_NAMES:
            tables = {t: f"DB.{t}" for t in view_tables(view, [0])}
            sql = view_select(view, "r", tables)
            self.assertTrue(sql.upper().startswith("SELECT "), view)
            for column in VIEWS[view].column_names:
                self.assertIn(f" AS {column}" if column != "at" else " AS `at`", sql, f"{view}.{column}")


class TypedCasting(unittest.TestCase):
    def test_cast_cells(self) -> None:
        self.assertEqual(cast_cell("28462", "int"), 28462)
        self.assertEqual(cast_cell("3.209008575E6", "float"), 3209008.575)
        self.assertEqual(cast_cell("12.0", "int"), 12)
        self.assertIsNone(cast_cell("", "int"))
        self.assertIsNone(cast_cell(None, "str"))
        self.assertTrue(cast_cell("true", "bool"))
        self.assertEqual(cast_cell(7, "str"), "7")

    def test_cast_rows_by_header(self) -> None:
        meta = {"columns": {"n": "int", "share": "float"}}
        self.assertEqual(cast_rows(["n", "share", "x"], [["1", "0.5", "k"]], meta), [[1, 0.5, "k"]])

    def test_backend_label_and_sha(self) -> None:
        r = SqlResult("q", "zetaris", [], [], 0, 0, "a" * 64)
        self.assertEqual(backend_label(r), "zetaris")
        r = SqlResult("q", "sqlite", [], [], 0, 0, "a" * 64)
        self.assertEqual(backend_label(r), "sqlite")
        r.fallback_reason = "zetaris_http_400: x"
        self.assertEqual(backend_label(r), "sqlite-fallback")
        self.assertEqual(sql_sha256_for("SELECT 1", {"sql_sha256": "b" * 64}), "b" * 64)
        self.assertEqual(len(sql_sha256_for("SELECT 1", {})), 64)


if __name__ == "__main__":
    unittest.main()
