"""Executor and client tests: the fallback matrix (SPEC.md 10.5), --require-zetaris, 401 re-login, secret
scrubbing in zetaris.jsonl, artifact server behaviour. Owner: B3.

The Zetaris HTTP layer is faked at ZetarisClient._raw so the real login parsing, error handling, DDL ordering
and per-query fallback logic run without a live instance. The live path is exercised by
scripts/zetaris_preflight.py and scripts/record_zetaris_results.py.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock  # noqa: E402
from investigator.config import Env, RunConfig  # noqa: E402
from investigator.errors import InvestigatorError  # noqa: E402
from investigator.eventlog import EventLog  # noqa: E402
from investigator.zetaris import ddl  # noqa: E402
from investigator.zetaris.artifact_server import ArtifactServer  # noqa: E402
from investigator.zetaris.catalog import load_catalog  # noqa: E402
from investigator.zetaris.client import (  # noqa: E402
    CONTAINERS_PATH, DATABASES_PATH, LOGIN_PATH, RUN_QUERY_PATH, ZetarisClient, ZetarisError, ZetarisUnreachable,
)
from investigator.zetaris.executor import SqliteExecutor, ZetarisExecutor, backend_label, build_executor, tables_summary  # noqa: E402

PASSWORD = "pw-secret-ZZ9"
TOKEN = "tok-secret-JWT-abc"


class FakeHttp(ZetarisClient):
    """ZetarisClient with the HTTP layer replaced: scripted answers keyed by path and SQL prefix."""

    def __init__(self, *args: Any, fail_login: bool = False, fail_sql: Optional[dict[str, tuple[int, str]]] = None,
                 unreachable: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fail_login = fail_login
        self.fail_sql = dict(fail_sql or {})
        self.unreachable = unreachable
        self.calls: list[tuple[str, str, Any]] = []
        self.statements: list[str] = []
        self.containers_state: list[dict[str, Any]] = []
        self.unauthorized_once = False
        self.answers: list[tuple[str, list[str], list[list[Any]]]] = []

    def _raw(self, method: str, path: str, body: Any = None, auth: bool = True, timeout: Optional[float] = None):
        self.calls.append((method, path, body))
        if self.unreachable:
            raise ZetarisUnreachable("URLError: [WinError 10061] connection refused", path)
        if path == LOGIN_PATH:
            if self.fail_login:
                return 401, {"msg": "Invalid credentials for " + PASSWORD}, 1
            return 200, {"idToken": TOKEN, "refreshToken": "r", "orgId": 1}, 1
        if auth and self.unauthorized_once:
            self.unauthorized_once = False
            return 401, {"msg": "token expired"}, 1
        if path == RUN_QUERY_PATH:
            sql = body["sql"]
            self.statements.append(sql)
            for needle, (status, msg) in self.fail_sql.items():
                if needle in sql:
                    return status, {"msg": msg}, 1
            if sql == "SHOW SERVER VERSION":
                return 200, {"headers": ["version", "build_hash"], "data": [["2.4.3.1", "cb88df0b"]], "total": 1, "timeUsed": 3}, 1
            if sql.startswith("CREATE SCHEMASTORE CONTAINER"):
                self.containers_state.append({"name": sql.split()[-1], "containerId": 884, "viewCount": 0})
            if sql.upper().startswith(("CREATE", "CACHE", "UNCACHE", "DROP")):
                return 200, {"headers": ["Result"], "data": [], "total": 0, "timeUsed": 2}, 1
            if sql.upper().startswith("SELECT"):
                for needle, headers, rows in self.answers:
                    if needle in sql:
                        return 200, {"headers": headers, "data": rows, "total": len(rows), "timeUsed": 5}, 1
                return 200, {"headers": ["n"], "data": [["0"]], "total": 1, "timeUsed": 1}, 1
            return 400, {"msg": "LightningDdlParseException: unknown statement"}, 1
        if path == DATABASES_PATH:
            return 200, [{"name": "NY_TAXI", "tableCount": 15}], 1
        if path == CONTAINERS_PATH:
            return 200, list(self.containers_state), 1
        if path.startswith(CONTAINERS_PATH + "/") and method == "DELETE":
            self.containers_state = []
            return 200, None, 1
        return 404, {"msg": "no route"}, 1


def live_config(require_zetaris: bool = False, keep: bool = False) -> RunConfig:
    env = Env(zetaris_username="user@example.com", zetaris_password=PASSWORD, zetaris_base_url="http://127.0.0.1:9",
              artifact_server_host="host.docker.internal")
    return RunConfig(requested_mode="live", mode="live", env=env, require_zetaris=require_zetaris, keep_zetaris_objects=keep)


def small_exports(tmp: Path) -> dict[str, str]:
    exports_dir = tmp / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    receipts = {"seller": "0xSELLER", "agentId": "1", "sales": [
        {"at": "2026-10-20T00:00:00.000Z", "amountAtomic": "1000", "payer": "0xPAYER", "ownWallet": False, "transaction": "0xT1", "x402Version": 2, "service": "ping"},
        {"at": "2026-10-19T00:00:00.000Z", "amountAtomic": "50", "payer": "0xseller", "ownWallet": True, "transaction": "0xT2", "x402Version": 2}],
        "purchases": []}
    search = {"resources": [{"resource": "https://h.example/api/x", "serviceName": "S", "type": "http", "lastUpdated": "t",
                             "quality": {"l30DaysTotalCalls": 4, "l30DaysUniquePayers": 2, "lastCalledAt": "c"},
                             "accepts": [{"payTo": "0xSELLER", "network": "eip155:8453", "asset": "0xU", "amount": "5000", "scheme": "exact"}]}]}
    out: dict[str, str] = {}
    for name, payload in (("store_receipts", receipts), ("cdp_search_host", search)):
        path = exports_dir / f"{name}.json"
        path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
        out[name] = str(path)
    return out


class ClientBehaviour(unittest.TestCase):
    def test_login_parses_token_and_org_id(self) -> None:
        c = FakeHttp("http://x", username="u", password=PASSWORD)
        c.login()
        self.assertEqual(c.org_id, "1")
        self.assertEqual(c.headers()["X-Org-ID"], "1")
        self.assertTrue(c.headers()["Authorization"].startswith("Bearer "))
        self.assertEqual(c.server_version(), "2.4.3.1")

    def test_relogin_once_on_401(self) -> None:
        c = FakeHttp("http://x", username="u", password=PASSWORD)
        c.login()
        c.unauthorized_once = True
        result = c.run_query("SELECT 1 AS n")
        self.assertEqual(result.data, [["0"]])
        self.assertEqual(sum(1 for m, p, _ in c.calls if p == LOGIN_PATH), 2)

    def test_error_bodies_are_scrubbed_and_never_carry_secrets(self) -> None:
        lines: list[dict[str, Any]] = []
        c = FakeHttp("http://x", username="u", password=PASSWORD, fail_login=True, logger=lines.append)
        with self.assertRaises(ZetarisError) as ctx:
            c.login()
        self.assertNotIn(PASSWORD, str(ctx.exception))
        self.assertIn("***", str(ctx.exception))
        dump = json.dumps(lines)
        self.assertNotIn(PASSWORD, dump)
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn("Bearer", dump)

    def test_unreachable_is_distinct(self) -> None:
        c = FakeHttp("http://x", username="u", password=PASSWORD, unreachable=True)
        with self.assertRaises(ZetarisUnreachable):
            c.login()


class FallbackMatrix(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="x402-exec-"))
        self.exports = small_exports(self.tmp)
        self.catalog = load_catalog(REPO_ROOT / "investigator" / "sql")
        self.log = EventLog("t-run", self.tmp, SampleClock("2026-10-22T00:05:00Z"), echo_stdout=False)

    def tearDown(self) -> None:
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _executor(self, **fake_kwargs: Any) -> ZetarisExecutor:
        config = live_config(require_zetaris=fake_kwargs.pop("require_zetaris", False), keep=fake_kwargs.pop("keep", False))
        ex = ZetarisExecutor(config, "t-run", self.tmp, self.log, zetaris_source="captured", keep_objects=config.keep_zetaris_objects)
        ex.client = FakeHttp(config.env.zetaris_base_url, username="u", password=PASSWORD, logger=ex._zlog, **fake_kwargs)
        return ex

    def test_sample_mode_mounts_sqlite(self) -> None:
        cfg = RunConfig(requested_mode="sample", mode="sample", env=Env(sample_mode_flag=True))
        self.assertIsInstance(build_executor(cfg, self.tmp, None), SqliteExecutor)
        cfg = RunConfig(requested_mode="live", mode="live", env=Env())
        self.assertIsInstance(build_executor(cfg, self.tmp, None), SqliteExecutor)

    def test_unreachable_at_mount_falls_back_to_sqlite(self) -> None:
        original = ZetarisExecutor.connect
        ZetarisExecutor.connect = lambda self: (_ for _ in ()).throw(ZetarisUnreachable("connection refused", LOGIN_PATH))  # type: ignore[assignment]
        try:
            ex = build_executor(live_config(), self.tmp, self.log)
            self.assertIsInstance(ex, SqliteExecutor)
            self.assertTrue(ex.fallback_reason.startswith("zetaris_unreachable"))
            with self.assertRaises(InvestigatorError) as ctx:
                build_executor(live_config(require_zetaris=True), self.tmp, self.log)
            self.assertEqual(ctx.exception.code, "E_ZETARIS_REQUIRED")
            self.assertEqual(ctx.exception.details["reason"], "zetaris_required")
        finally:
            ZetarisExecutor.connect = original  # type: ignore[assignment]

    def test_happy_path_runs_on_zetaris(self) -> None:
        ex = self._executor()
        ex.client.answers.append(("GROUP BY own_wallet", ["own_wallet", "n_sales", "n_payers", "usdc_atomic"], [["0", "1", "1", "1000"], ["1", "1", "1", "50"]]))
        tables = ex.ensure_views(self.exports)
        self.assertEqual(ex.version, "2.4.3.1")
        self.assertEqual(ex.view_status["sales"]["backend"], "zetaris")
        self.assertEqual(ex.view_status["bazaar_search"]["backend"], "zetaris")
        self.assertEqual(ex.view_status["catalog_resources"]["reason"], "export_missing:catalog_snapshot")
        stmts = ex.client.statements
        self.assertTrue(any(s.startswith("CREATE LIGHTNING DATABASE X402_") for s in stmts))
        self.assertTrue(any(s.startswith("CREATE LIGHTNING REST TABLE receipts_raw") and "host.docker.internal" in s for s in stmts))
        self.assertTrue(any(s.startswith("CACHE TABLE X402_") for s in stmts))
        self.assertTrue(any(s.startswith("CREATE OR REPLACE SCHEMASTORE VIEW sales WITH CONTAINER x402_") for s in stmts))
        self.assertTrue(any(t["name"].endswith(".receipts_raw") and t["status"] == "ok" for t in tables))
        spec = self.catalog["q01_sales_by_wallet_class"]
        result = ex.execute("q01_sales_by_wallet_class", spec.sql, {}, spec.meta)
        self.assertEqual(result.executor, "zetaris")
        self.assertEqual(result.rows, [[0, 1, 1, 1000], [1, 1, 1, 50]])
        self.assertEqual(result.zetaris_version, "2.4.3.1")
        self.assertEqual(result.sql_sha256, spec.sql_sha256)
        self.assertIn(f"FROM {ddl.container_name('t-run')}.sales", ex.client.statements[-1])
        self.assertEqual(backend_label(result), "zetaris")
        before_cleanup = len(ex.client.statements)
        ex.cleanup()
        tail = ex.client.statements[before_cleanup:]
        self.assertEqual(len(tail), 3 + 2 * 2 + 1)      # 3 views, 2 tables (UNCACHE + DROP), 1 database
        self.assertTrue(any(s.startswith("DROP SCHEMASTORE VIEW sales FROM x402_") for s in tail))
        self.assertTrue(any(s.startswith("DROP LIGHTNING DATABASE TABLE X402_") for s in tail))
        self.assertTrue(tail[-1].startswith("DROP LIGHTNING DATABASE X402_"))
        self.assertTrue(any(m == "DELETE" and p.startswith(CONTAINERS_PATH + "/") for m, p, _ in ex.client.calls))
        self.assertFalse(ex.server and ex.server.running)

    def test_http_400_on_one_query_falls_back_to_sqlite_with_reason(self) -> None:
        ex = self._executor(fail_sql={"GROUP BY own_wallet": (400, "MISSING_ATTRIBUTES.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION")})
        recoveries: list[tuple[Any, ...]] = []
        ex.on_recovery = lambda agent, reason, detail, **extra: recoveries.append((agent, reason, detail, extra))
        ex.ensure_views(self.exports)
        spec = self.catalog["q01_sales_by_wallet_class"]
        result = ex.execute("q01_sales_by_wallet_class", spec.sql, {}, spec.meta)
        self.assertEqual(result.executor, "sqlite")
        self.assertTrue(result.fallback_reason.startswith("zetaris_http_400"))
        self.assertEqual(backend_label(result), "sqlite-fallback")
        self.assertEqual(result.rows, [[0, 1, 1, 1000], [1, 1, 1, 50]])
        self.assertEqual(result.zetaris_version, "2.4.3.1")
        self.assertEqual(recoveries[-1][1], "zetaris_query_fallback")
        self.assertEqual(recoveries[-1][3]["query_id"], "q01_sales_by_wallet_class")
        spec6 = self.catalog["q06_bazaar_host_listings"]
        ex.client.answers.append(("FROM x402_", ["n_listings", "n_pay_to", "calls_30d_sum", "payers_30d_max"], [["1", "1", "4", "2"]]))
        result6 = ex.execute("q06_bazaar_host_listings", spec6.sql, {"host": "h.example"}, spec6.meta)
        self.assertEqual(result6.executor, "zetaris")
        self.assertEqual(result6.rows, [[1, 1, 4, 2]])
        ex.cleanup()

    def test_view_failure_routes_its_queries_to_sqlite(self) -> None:
        ex = self._executor(fail_sql={"SCHEMASTORE VIEW sales": (400, "LightningDdlParseException expecting 'METADATA'")})
        ex.ensure_views(self.exports)
        self.assertEqual(ex.view_status["sales"]["backend"], "sqlite")
        self.assertTrue(ex.view_status["sales"]["reason"].startswith("view_failed:"))
        self.assertEqual(ex.view_status["bazaar_search"]["backend"], "zetaris")
        spec = self.catalog["q02_external_payers"]
        result = ex.execute("q02_external_payers", spec.sql, {}, spec.meta)
        self.assertEqual(result.executor, "sqlite")
        self.assertTrue(result.fallback_reason.startswith("view_unavailable:sales("))
        self.assertEqual(result.rows[0][0], "0xpayer")
        ex.cleanup()

    def test_failed_table_logs_recovery_and_keeps_its_status(self) -> None:
        ex = self._executor(fail_sql={"receipts_raw": (400, "Connection reset")})
        recoveries: list[tuple[Any, ...]] = []
        ex.on_recovery = lambda agent, reason, detail, **extra: recoveries.append((agent, reason, detail, extra))
        ex.ensure_views(self.exports)
        failed = [r for r in recoveries if r[1] == "zetaris_table_failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0][3]["table"], "receipts_raw")
        self.assertIn("Connection reset", failed[0][2])
        self.assertEqual(ex.view_status["sales"]["backend"], "sqlite")
        self.assertEqual(ex.view_status["bazaar_search"]["backend"], "zetaris")
        rows = {r["name"].split(".")[-1]: r for r in tables_summary(ex.tables)}
        self.assertEqual(rows["receipts_raw"]["status"], "failed")
        self.assertIn("Connection reset", rows["receipts_raw"]["reason"])
        others = [r for name, r in rows.items() if name != "receipts_raw"]
        self.assertTrue(others)
        self.assertTrue(all(r["status"] == "ok" and "reason" not in r for r in others))
        ex.cleanup()

    def test_require_zetaris_turns_query_fallback_into_error(self) -> None:
        ex = self._executor(fail_sql={"GROUP BY own_wallet": (400, "boom")}, require_zetaris=True)
        ex.ensure_views(self.exports)
        spec = self.catalog["q01_sales_by_wallet_class"]
        with self.assertRaises(InvestigatorError) as ctx:
            ex.execute("q01_sales_by_wallet_class", spec.sql, {}, spec.meta)
        self.assertEqual(ctx.exception.code, "E_ZETARIS_REQUIRED")
        self.assertEqual(ctx.exception.details["reason"], "zetaris_required")
        ex.cleanup()

    def test_ddl_failure_degrades_whole_run_and_logs_recovery(self) -> None:
        ex = self._executor(fail_sql={"CREATE LIGHTNING DATABASE": (400, "Administrators only")})
        ex.ensure_views(self.exports)
        self.assertTrue(ex.degraded)
        spec = self.catalog["q01_sales_by_wallet_class"]
        result = ex.execute("q01_sales_by_wallet_class", spec.sql, {}, spec.meta)
        self.assertEqual(result.executor, "sqlite")
        self.assertTrue(result.fallback_reason.startswith("zetaris_ddl_failed"))
        self.assertTrue(any(r["reason"] == "zetaris_ddl_failed" for r in ex.recoveries))
        run_lines = [json.loads(l) for l in (self.tmp / "run.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(l["type"] == "recovery" and l["payload"]["reason"] == "zetaris_ddl_failed" for l in run_lines))
        ex.cleanup()

    def test_keep_objects_skips_drops_but_stops_server(self) -> None:
        ex = self._executor(keep=True)
        ex.ensure_views(self.exports)
        self.assertTrue(ex.server.running)
        before = len(ex.client.statements)
        ex.cleanup()
        self.assertEqual(len(ex.client.statements), before)
        self.assertIsNone(ex.server)

    def test_zetaris_jsonl_has_no_secrets_and_the_expected_ops(self) -> None:
        ex = self._executor()
        ex.ensure_views(self.exports)
        spec = self.catalog["q01_sales_by_wallet_class"]
        ex.execute("q01_sales_by_wallet_class", spec.sql, {}, spec.meta)
        ex.cleanup()
        self.log.flush()
        text = (self.tmp / "zetaris.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("Authorization", text)
        ops = {json.loads(l)["op"] for l in text.splitlines()}
        self.assertTrue({"login", "run-query", "ddl", "metadata", "cleanup"} <= ops, ops)
        for line in text.splitlines():
            record = json.loads(line)
            self.assertIn("seq", record)
            self.assertIn("http", record)
            self.assertIn("time_used_ms", record)


class ArtifactServerBehaviour(unittest.TestCase):
    def test_serves_only_token_paths(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="x402-srv-"))
        (tmp / "a.json").write_text('{"ok":true}', encoding="utf-8", newline="\n")
        (tmp / "sub").mkdir()
        (tmp / "sub" / "b.json").write_text("{}", encoding="utf-8", newline="\n")
        srv = ArtifactServer(tmp, host_for_driver="host.docker.internal")
        port = srv.start()
        try:
            self.assertEqual(srv.url_for("a"), f"http://host.docker.internal:{port}/{srv.run_token}/a.json")
            with urlopen(srv.local_url_for("a"), timeout=5) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read(), b'{"ok":true}')
            for bad in (f"/{srv.run_token}/", f"/{srv.run_token}/missing.json", "/wrongtoken/a.json",
                        f"/{srv.run_token}/../a.json", f"/{srv.run_token}/sub/b.json", f"/{srv.run_token}/a.txt"):
                with self.assertRaises(HTTPError) as ctx:
                    urlopen(f"http://127.0.0.1:{port}{bad}", timeout=5)
                self.assertEqual(ctx.exception.code, 404, bad)
            with self.assertRaises(HTTPError) as ctx:
                urlopen(Request(srv.local_url_for("a"), data=b"x", method="POST"), timeout=5)
            self.assertEqual(ctx.exception.code, 405)
            self.assertTrue(any(r["http"] == 200 for r in srv.requests))
        finally:
            srv.stop()
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertFalse(srv.running)


if __name__ == "__main__":
    unittest.main()
