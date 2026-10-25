"""SqlExecutor protocol implementations: ZetarisExecutor and SqliteExecutor; typed row casting (SPEC.md 10). Owner: B3.

Both executors expose the SAME view names and columns (sql/views/VIEWS.md) and run the SAME committed ANSI SQL
from sql/analytics/. Zetaris returns every cell as a string: cast_rows() applies the .meta.json column types.

ZetarisExecutor: login + SHOW SERVER VERSION at mount time (build_executor), then per run X402_<RUN8> database
+ x402_<run8> container, one REST table per export served by the artifact server (captured) or by the public
URL (direct, three tables), CACHE TABLE after creation, shape-aware SchemaStore views, run-query for analytics
(view names qualified with the container, the committed SQL and its sha256 untouched), cleanup at finalize.
Failures follow the fallback matrix (SPEC.md 10.5): login failure -> SqliteExecutor for the whole run
(fallback_reason set; the runner logs recovery zetaris_unreachable); a table or view that cannot be built ->
every query reading it runs on SQLite with fallback_reason; a query HTTP 400 -> that query on SQLite; the
artifact server unreachable from the driver -> direct URLs for receipts/Bazaar, SQLite for the rest;
--require-zetaris turns any of these into E_ZETARIS_REQUIRED. Every fallback is a logged recovery event
(through on_recovery when the runner installs ctx.recover, else straight to run.jsonl) and never a crash.

The SQLite backend is always built too (it is cheap), so a per-query fallback costs nothing extra and the
"degraded" path is visible in sql.jsonl as executor "sqlite" + fallback_reason, labelled backend
"sqlite-fallback" by backend_label().
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ..config import RunConfig
from ..contracts import SqlExecutor, SqlResult
from ..errors import InvestigatorError
from ..eventlog import EventLog
from . import ddl
from .artifact_server import ArtifactServer
from .catalog import bind_params, referenced_views, sql_sha256_for
from .client import ZetarisClient, ZetarisError, ZetarisUnreachable
from .views_sqlite import PAGES, VIEWS, VIEW_NAMES, build_views, load_export, page_exports, resolve_export
from .views_zetaris import shape_of, view_select

ROW_LIMIT = 1000                 # run-query limit; SQLite applies the same cap for parity
PAGE_LIMIT = 100                 # paginated start/page/close page size
QUERY_TIMEOUT_S = 30.0           # per analytics query (SPEC.md 6.5)
DDL_TIMEOUT_S = 30.0
CATALOG_TIMEOUT_S = 120.0        # the 32 MB snapshot registers in ~3 s and caches in ~4 s on the demo machine
LOGIN_TIMEOUT_S = 10.0
DIRECT_TABLES: dict[str, tuple[str, ...]] = {   # logical table -> export keys whose public URL may be registered directly
    "receipts_raw": ("store_receipts", "receipts"),
    "bazaar_page0": ("cdp_page_0", "cdp_page0"),
    "bazaar_search_host": ("cdp_search_host", "cdp_search"),
}


# ----------------------------------------------------------------------------------------------------------
# Typed casting
# ----------------------------------------------------------------------------------------------------------

def cast_cell(value: Any, type_name: str) -> Any:
    """Cast one cell per .meta.json types: int | float | str | bool. Empty strings and None stay None."""
    if value is None or (isinstance(value, str) and value == ""):
        return None
    if type_name == "int":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        text = str(value).strip()
        try:
            return int(text)
        except ValueError:
            return int(float(text))
    if type_name == "float":
        return float(value)
    if type_name == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "t", "yes")
        return bool(value)
    return str(value)


def cast_rows(headers: list[str], rows: list[list[Any]], meta: dict[str, Any]) -> list[list[Any]]:
    """Apply meta["columns"] types by header name; unknown columns stay as they are."""
    columns = meta.get("columns", {})
    casted: list[list[Any]] = []
    for row in rows:
        casted.append([cast_cell(cell, columns.get(header, "str")) if header in columns else cell
                       for header, cell in zip(headers, row)])
    return casted


def backend_label(result: SqlResult) -> str:
    """Derivation.backend for a SqlResult: zetaris | sqlite | sqlite-fallback."""
    if result.executor == "zetaris":
        return "zetaris"
    return "sqlite-fallback" if result.fallback_reason else "sqlite"


def tables_summary(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The {name, source_url} rows for sql.jsonl, plus status and reason when the table carries them (a table
    that could not be built on Zetaris is listed with status failed and its reason, not as if it were fine)."""
    rows = []
    for t in tables:
        row = {"name": t.get("name"), "source_url": t.get("source_url")}
        if t.get("status"):
            row["status"] = t["status"]
        if t.get("reason"):
            row["reason"] = t["reason"]
        rows.append(row)
    return rows


# ----------------------------------------------------------------------------------------------------------
# SQLite backend
# ----------------------------------------------------------------------------------------------------------

class SqliteExecutor:
    """Embedded backend: Python-built views (views_sqlite.py) over the run exports, in a per-run SQLite file."""

    name = "sqlite"

    def __init__(self, run_dir: Path, log: Optional[EventLog] = None, fallback_reason: Optional[str] = None) -> None:
        self.run_dir = Path(run_dir)
        self.log = log
        self.db_path = self.run_dir / "analytics.sqlite"
        self._conn: Optional[sqlite3.Connection] = None
        self.tables: list[dict[str, Any]] = []
        self.exports: dict[str, str] = {}
        self.fallback_reason = fallback_reason     # set when this executor replaces an unreachable Zetaris
        self.version: Optional[str] = None
        self.views_ready = False
        self.queries = 0

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
        return self._conn

    def ensure_views(self, exports: dict[str, Any], urls: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
        """Build every view in VIEWS.md from the export files; return tables [{name, source_url, rows, ...}]."""
        self.cleanup()
        try:
            if self.db_path.exists():
                self.db_path.unlink()
        except OSError:
            pass
        conn = self.connect()
        self.exports = {str(k): str(v) for k, v in (exports or {}).items() if v}
        self.tables = build_views(conn, self.exports)
        self.views_ready = True
        return self.tables

    def execute(self, query_id: str, sql: str, params: dict[str, Any], meta: dict[str, Any]) -> SqlResult:
        """Bind params through zetaris.catalog.bind_params, run, cast rows, return SqlResult(executor sqlite)."""
        if not self.views_ready:
            self.ensure_views(self.exports)
        bound = bind_params(sql, params or {}, meta or {})
        started = time.monotonic()
        cursor = self.connect().execute(bound)
        headers = [d[0] for d in (cursor.description or [])]
        rows = [list(r) for r in cursor.fetchmany(ROW_LIMIT)]
        elapsed = int((time.monotonic() - started) * 1000)
        self.queries += 1
        return SqlResult(query_id=query_id, executor="sqlite", headers=headers, rows=cast_rows(headers, rows, meta or {}),
                         rowcount=len(rows), time_used_ms=elapsed, sql_sha256=sql_sha256_for(sql, meta),
                         fallback_reason=self.fallback_reason, zetaris_version=None, tables=tables_summary(self.tables))

    def cleanup(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self.views_ready = False


# ----------------------------------------------------------------------------------------------------------
# Zetaris backend
# ----------------------------------------------------------------------------------------------------------

class ZetarisExecutor:
    """Live federation backend over the Zetaris REST proxy (client.py) with SQLite as per-query fallback."""

    name = "zetaris"

    def __init__(self, config: RunConfig, run_id: str, run_dir: Path, log: Optional[EventLog],
                 zetaris_source: str = "captured", keep_objects: bool = False) -> None:
        self.config = config
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.log = log
        self.zetaris_source = zetaris_source if zetaris_source in ("captured", "direct") else "captured"
        self.keep_objects = keep_objects
        self.version: Optional[str] = None
        env = config.env
        self.client = ZetarisClient(base_url=env.zetaris_base_url, username=env.zetaris_username,
                                    password=env.zetaris_password, api_key=env.zetaris_api_key,
                                    org_id=env.zetaris_org_id, api_style=env.zetaris_api_style,
                                    timeout=QUERY_TIMEOUT_S, logger=self._zlog)
        self.fallback = SqliteExecutor(run_dir, log)
        self.server: Optional[ArtifactServer] = None
        self.tables: list[dict[str, Any]] = []
        self.exports: dict[str, str] = {}
        self.source_urls: dict[str, str] = {}          # export key -> public URL (for direct registration)
        self.on_recovery: Optional[Callable[..., Any]] = None   # the runner may install ctx.recover here
        self.recoveries: list[dict[str, Any]] = []
        self.degraded = False
        self.degraded_reason: Optional[str] = None
        self.db_created = False
        self.container_id: Optional[int] = None
        self.created_tables: list[str] = []            # logical table names registered on Zetaris
        self.created_views: list[str] = []
        self.table_status: dict[str, dict[str, Any]] = {}
        self.view_status: dict[str, dict[str, Any]] = {}
        self.queries = 0
        self.zetaris_queries = 0
        self.fallback_queries = 0
        self.views_ready = False

    # -- logging ----------------------------------------------------------------------------------------------
    def _zlog(self, line: dict[str, Any]) -> None:
        if self.log is not None:
            try:
                self.log.side("zetaris", line)
            except Exception:  # noqa: BLE001
                pass

    def _recover(self, reason: str, detail: str, **extra: Any) -> None:
        payload = {"reason": reason, "detail": detail, **extra}
        self.recoveries.append(payload)
        if self.on_recovery is not None:
            try:
                self.on_recovery("analyst", reason, detail, **extra)
                return
            except Exception:  # noqa: BLE001
                pass
        if self.log is not None:
            self.log.emit("recovery", "analyst", payload)

    def _require(self, stage: str, message: str, **details: Any) -> None:
        if self.config.require_zetaris:
            raise InvestigatorError("E_ZETARIS_REQUIRED", stage, f"{message} and --require-zetaris is set",
                                    retryable=True, details={"reason": "zetaris_required", **details})

    # -- connect (mount time) -----------------------------------------------------------------------------------
    def connect(self) -> str:
        """Login and SHOW SERVER VERSION with a short timeout; raises ZetarisError when unreachable."""
        previous = self.client.timeout
        self.client.timeout = LOGIN_TIMEOUT_S
        try:
            self.client.login()
            self.version = self.client.server_version()
        finally:
            self.client.timeout = previous
        return self.version

    # -- views --------------------------------------------------------------------------------------------------
    def ensure_views(self, exports: dict[str, Any], urls: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
        """Login, SHOW SERVER VERSION, create X402_<RUN8> db + container, REST tables (artifact server or
        direct URLs), CACHE TABLE, SchemaStore views; log every call to zetaris.jsonl (never headers/tokens)."""
        self.exports = {str(k): str(v) for k, v in (exports or {}).items() if v}
        if urls:
            self.source_urls.update({str(k): str(v) for k, v in urls.items() if v})
        self.fallback.ensure_views(self.exports)          # always available, instant per-query fallback
        self.tables = []
        self.table_status = {}
        self.view_status = {}
        self.views_ready = True
        try:
            if self.version is None:
                self.connect()
            self._prepare_objects()
        except ZetarisError as err:
            self._degrade("zetaris_unreachable" if isinstance(err, ZetarisUnreachable) else "zetaris_ddl_failed",
                          f"{err.message}")
            return self._fallback_tables()
        self._start_server()
        for spec in VIEWS.values():
            self._build_view(spec)
        return self.tables

    def _degrade(self, reason: str, detail: str) -> None:
        self.degraded = True
        self.degraded_reason = f"{reason}: {detail}"
        self._require("analyst", f"Zetaris unavailable ({detail})", detail=detail)
        self._recover(reason, detail, to="sqlite", degraded=True)
        for view in VIEW_NAMES:
            self.view_status[view] = {"backend": "sqlite", "reason": self.degraded_reason}

    def _fallback_tables(self) -> list[dict[str, Any]]:
        self.tables = [{"name": t["name"], "source_url": t.get("source_url"), "backend": "sqlite",
                        "rows": t.get("rows", 0)} for t in self.fallback.tables]
        return self.tables

    def _prepare_objects(self) -> None:
        db = ddl.database_name(self.run_id)
        container = ddl.container_name(self.run_id)
        existing = {d.get("name") for d in self.client.databases()}
        if db in existing:                                  # a crashed earlier run with the same run_id
            self.client.ddl(ddl.drop_database(self.run_id), timeout=DDL_TIMEOUT_S)
        self.client.ddl(ddl.create_database(self.run_id), timeout=DDL_TIMEOUT_S)
        self.db_created = True
        for c in self.client.containers():
            if c.get("name") == container and c.get("containerId") is not None:
                self._drop_container_views(container)
                self.client.delete_container(int(c["containerId"]))
        self.client.ddl(ddl.create_container(self.run_id), timeout=DDL_TIMEOUT_S)
        for c in self.client.containers():
            if c.get("name") == container and c.get("containerId") is not None:
                self.container_id = int(c["containerId"])

    def _drop_container_views(self, container: str) -> None:
        """Best effort: drop our known view names from a leftover container so it can be deleted."""
        for view in VIEW_NAMES:
            try:
                self.client.ddl(f"DROP SCHEMASTORE VIEW {view} FROM {container}", timeout=DDL_TIMEOUT_S)
            except ZetarisError:
                continue

    def _start_server(self) -> None:
        if self.server is not None and self.server.running:
            return
        exports_dir = None
        for path in self.exports.values():
            exports_dir = Path(path).resolve().parent
            break
        if exports_dir is None:
            exports_dir = self.run_dir / "exports"
            exports_dir.mkdir(parents=True, exist_ok=True)
        env = self.config.env
        self.server = ArtifactServer(exports_dir, host_for_driver=env.artifact_server_host,
                                     port=env.artifact_server_port)
        try:
            port = self.server.start()
            self._zlog({"op": "artifact-server", "path": f"/{self.server.run_token[:8]}.../", "http": 0,
                        "time_used_ms": 0, "msg": f"serving {exports_dir.name}/ on port {port} for {env.artifact_server_host}"})
        except OSError as err:
            self.server = None
            self._recover("artifact_server_unreachable", f"artifact server could not bind: {err}", to="direct")
            self.zetaris_source = "direct"

    # -- tables -------------------------------------------------------------------------------------------------
    def _export_name_for(self, export_path: str) -> str:
        return Path(export_path).name[:-5] if export_path.endswith(".json") else Path(export_path).stem

    def _url_for_table(self, logical: str, export_key: str, export_path: str) -> tuple[str, str]:
        """(url, kind) where kind is artifact | direct."""
        if self.zetaris_source == "direct" and logical in DIRECT_TABLES:
            for key in DIRECT_TABLES[logical]:
                if key in self.source_urls:
                    return self.source_urls[key], "direct"
            if export_key in self.source_urls:
                return self.source_urls[export_key], "direct"
        if self.server is not None and self.server.running:
            name = self._export_name_for(export_path)
            if Path(export_path).resolve().parent != self.server.exports_dir:
                raise ZetarisError(0, f"export {export_key} is outside the served exports directory", "artifact")
            return self.server.url_for(name), "artifact"
        raise ZetarisError(0, f"no artifact server and no public URL for {logical}", "artifact")

    def _register_table(self, logical: str, export_key: str, export_path: str, is_catalog: bool = False) -> bool:
        """CREATE LIGHTNING REST TABLE + CACHE TABLE for one export; records status; returns success."""
        if logical in self.table_status:
            return self.table_status[logical]["ok"]
        try:
            url, kind = self._url_for_table(logical, export_key, export_path)
        except ZetarisError as err:
            self.table_status[logical] = {"ok": False, "reason": err.message, "export": export_key}
            self._recover("zetaris_table_failed", f"table {logical} could not be built on Zetaris: {err.message[:200]}",
                          table=logical, export=export_key, to="sqlite")
            return False
        timeout = CATALOG_TIMEOUT_S if is_catalog else DDL_TIMEOUT_S
        before = len(self.server.requests) if self.server is not None else 0
        try:
            self.client.ddl(ddl.create_rest_table(self.run_id, logical, url), timeout=timeout)
            self.created_tables.append(logical)
            self.client.ddl(ddl.cache_table(self.run_id, logical), timeout=timeout)
        except ZetarisError as err:
            reason = err.message
            if is_catalog:
                reason = f"catalog_too_large_for_rest_table: {err.message}"
            elif kind == "artifact" and self.server is not None and len(self.server.requests) == before:
                reason = f"artifact_server_unreachable: {err.message}"
                if self.zetaris_source != "direct":
                    self.zetaris_source = "direct"
                    self._recover("artifact_server_unreachable",
                                  "the Zetaris driver did not reach the artifact server; direct URLs for receipts/Bazaar, SQLite for the rest",
                                  to="direct")
                    if logical in DIRECT_TABLES:
                        if logical in self.created_tables:          # CREATE passed, CACHE failed: drop before re-creating
                            self._quiet(ddl.drop_table(self.run_id, logical))
                            self.created_tables.remove(logical)
                        self.table_status.pop(logical, None)
                        return self._register_table(logical, export_key, export_path, is_catalog)
            self.table_status[logical] = {"ok": False, "reason": reason, "export": export_key, "source_url": url}
            self.tables.append({"name": f"{ddl.database_name(self.run_id)}.{logical}", "source_url": url,
                                "backend": "sqlite", "status": "failed", "reason": reason, "export": export_key})
            # Logged even when no query reads this table, so a failed build never passes silently (example 02 of
            # 24 Oct: the driver ran out of heap on CACHE TABLE of a Bazaar page that no query read, and only
            # zetaris.jsonl showed it). Queries that do read it still log their own zetaris_query_fallback.
            self._recover("zetaris_table_failed", f"table {logical} could not be built on Zetaris: {reason[:200]}",
                          table=logical, export=export_key, to="sqlite")
            return False
        self.table_status[logical] = {"ok": True, "reason": None, "export": export_key, "source_url": url, "kind": kind}
        self.tables.append({"name": f"{ddl.database_name(self.run_id)}.{logical}", "source_url": url,
                            "backend": "zetaris", "status": "ok", "export": export_key, "kind": kind})
        return True

    # -- views ---------------------------------------------------------------------------------------------------
    def _build_view(self, spec: Any) -> None:
        db = ddl.database_name(self.run_id)
        logical_tables: dict[str, str] = {}
        shapes: dict[str, Any] = {}
        if PAGES in spec.exports:
            pages = page_exports(self.exports)
            if not pages:
                self.view_status[spec.name] = {"backend": "sqlite", "reason": "export_missing:cdp_page_<N>"}
                return
            for page_no, key, path in pages:
                logical = spec.table.format(n=page_no)
                if self._register_table(logical, key, path):
                    logical_tables[logical] = f"{db}.{logical}"
                    shapes[logical] = shape_of(load_export(path))
            if not logical_tables:
                self.view_status[spec.name] = {"backend": "sqlite", "reason": "table_unavailable:bazaar_page"}
                return
        else:
            resolved = resolve_export(spec, self.exports)
            if resolved is None:
                self.view_status[spec.name] = {"backend": "sqlite", "reason": f"export_missing:{spec.exports[0]}"}
                return
            key, path = resolved
            is_catalog = spec.name == "catalog_resources"
            if not self._register_table(spec.table, key, path, is_catalog=is_catalog):
                reason = self.table_status.get(spec.table, {}).get("reason", "table_unavailable")
                self.view_status[spec.name] = {"backend": "sqlite", "reason": f"table_unavailable:{spec.table}:{reason}"}
                return
            logical_tables[spec.table] = f"{db}.{spec.table}"
            shapes[spec.table] = shape_of(load_export(path))
        try:
            select_sql = view_select(spec.name, self.run_id, logical_tables, shapes)
            self.client.ddl(ddl.create_view(self.run_id, spec.name, select_sql), timeout=DDL_TIMEOUT_S)
        except (ZetarisError, ValueError, KeyError) as err:
            message = getattr(err, "message", str(err))
            self.view_status[spec.name] = {"backend": "sqlite", "reason": f"view_failed:{message[:200]}"}
            self._recover("zetaris_query_fallback", f"view {spec.name} could not be created on Zetaris: {message[:200]}",
                          view=spec.name, to="sqlite", degraded=True)
            return
        self.created_views.append(spec.name)
        self.view_status[spec.name] = {"backend": "zetaris", "reason": None, "tables": list(logical_tables.values())}

    # -- execute -------------------------------------------------------------------------------------------------
    def execute(self, query_id: str, sql: str, params: dict[str, Any], meta: dict[str, Any]) -> SqlResult:
        """run-query (or paginated start/page/close when expected_rows_bound > 1000); cast rows; HTTP 400 ->
        same query on the SQLite fallback with fallback_reason."""
        if not self.views_ready:
            self.ensure_views(self.exports)
        self.queries += 1
        if self.degraded:
            return self._fallback_query(query_id, sql, params, meta, self.degraded_reason or "zetaris_unreachable")
        missing = []
        for view in referenced_views(sql, VIEW_NAMES):
            status = self.view_status.get(view)
            if status is None or status.get("backend") != "zetaris":
                missing.append(f"{view}({(status or {}).get('reason', 'not built')})")
        if missing:
            return self._fallback_query(query_id, sql, params, meta, "view_unavailable:" + ",".join(missing))
        bound = bind_params(sql, params or {}, meta or {})
        executed = ddl.qualify_views(bound, self.run_id, VIEW_NAMES)
        bound_rows = int((meta or {}).get("expected_rows_bound", 0) or 0)
        try:
            if bound_rows > ROW_LIMIT:
                headers, data, elapsed = self._paged(executed, bound_rows)
            else:
                result = self.client.run_query(executed, limit=ROW_LIMIT, timeout=QUERY_TIMEOUT_S)
                headers, data, elapsed = result.headers, result.data, result.time_used_ms
        except ZetarisError as err:
            return self._fallback_query(query_id, sql, params, meta, f"zetaris_http_{err.http}: {err.message[:200]}")
        self.zetaris_queries += 1
        rows = cast_rows(headers, data, meta or {})
        return SqlResult(query_id=query_id, executor="zetaris", headers=headers, rows=rows, rowcount=len(rows),
                         time_used_ms=elapsed, sql_sha256=sql_sha256_for(sql, meta), fallback_reason=None,
                         zetaris_version=self.version, tables=tables_summary(self.tables))

    def _paged(self, executed: str, bound_rows: int) -> tuple[list[str], list[list[Any]], int]:
        """start/page/close for result sets above the run-query limit; pages are bounded by expected_rows_bound."""
        started = time.monotonic()
        token = self.client.start(executed, page_limit=PAGE_LIMIT)
        headers: list[str] = []
        data: list[list[Any]] = []
        try:
            max_pages = max(1, -(-bound_rows // PAGE_LIMIT))
            for page_no in range(1, max_pages + 1):
                rows = self.client.page(token, page_no, PAGE_LIMIT)
                if not rows:
                    break
                if not headers:
                    headers = list(rows[0].keys())
                data.extend([[row.get(h) for h in headers] for row in rows])
                if len(rows) < PAGE_LIMIT:
                    break
        finally:
            try:
                self.client.close(token)
            except ZetarisError:
                pass
        return headers, data, int((time.monotonic() - started) * 1000)

    def _fallback_query(self, query_id: str, sql: str, params: dict[str, Any], meta: dict[str, Any],
                        reason: str) -> SqlResult:
        self._require("analyst", f"query {query_id} cannot run on Zetaris ({reason})", query_id=query_id, detail=reason)
        self.fallback_queries += 1
        self._recover("zetaris_query_fallback", f"{query_id}: {reason}", query_id=query_id, to="sqlite", degraded=True)
        result = self.fallback.execute(query_id, sql, params, meta)
        result.fallback_reason = reason
        result.zetaris_version = self.version
        result.tables = tables_summary(self.tables) or result.tables
        return result

    # -- cleanup --------------------------------------------------------------------------------------------------
    def cleanup(self) -> None:
        """UNCACHE, DROP views, DELETE container, DROP tables, DROP database (skipped with --keep-zetaris-objects);
        the artifact server is always stopped. Errors are logged, never raised."""
        if self.keep_objects and (self.db_created or self.created_views):
            self._zlog({"op": "cleanup", "path": "", "http": 0, "time_used_ms": 0,
                        "msg": f"kept {ddl.database_name(self.run_id)} and container {ddl.container_name(self.run_id)} (--keep-zetaris-objects); REST tables over the artifact server will not re-fetch once it stops"})
        elif self.db_created or self.created_views or self.container_id is not None:
            for view in list(self.created_views):
                self._quiet(ddl.drop_view(view, self.run_id))
            self.created_views = []
            if self.container_id is not None:
                try:
                    self.client.delete_container(self.container_id)
                except ZetarisError:
                    pass
                self.container_id = None
            for logical in list(self.created_tables):
                self._quiet(ddl.uncache_table(self.run_id, logical))
                self._quiet(ddl.drop_table(self.run_id, logical))
            self.created_tables = []
            if self.db_created:
                self._quiet(ddl.drop_database(self.run_id))
                self.db_created = False
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:  # noqa: BLE001
                pass
            self.server = None
        self.fallback.cleanup()
        self.views_ready = False

    def _quiet(self, statement: str) -> None:
        try:
            self.client.ddl(statement, timeout=DDL_TIMEOUT_S)
        except ZetarisError:
            pass


# ----------------------------------------------------------------------------------------------------------
# Mounting
# ----------------------------------------------------------------------------------------------------------

def build_executor(config: RunConfig, run_dir: Path, log: Optional[EventLog], run_id: str = "",
                   zetaris_source: Optional[str] = None) -> SqlExecutor:
    """sample -> SqliteExecutor; live with Zetaris credentials -> ZetarisExecutor (login + version probed here,
    bounded by LOGIN_TIMEOUT_S); live without, or login failure -> SqliteExecutor with fallback_reason
    (the runner logs recovery zetaris_unreachable); --require-zetaris -> E_ZETARIS_REQUIRED."""
    if config.is_sample() or not config.env.has_zetaris():
        return SqliteExecutor(run_dir, log)
    rid = run_id or Path(run_dir).name
    source = zetaris_source or config.zetaris_source or "captured"
    executor = ZetarisExecutor(config, rid, run_dir, log, zetaris_source=source, keep_objects=config.keep_zetaris_objects)
    try:
        executor.connect()
    except ZetarisError as err:
        message = f"Zetaris login failed: {err.message}" if err.http else f"Zetaris unreachable: {err.message}"
        if config.require_zetaris:
            raise InvestigatorError("E_ZETARIS_REQUIRED", "mounts", f"{message} and --require-zetaris is set",
                                    retryable=True, details={"reason": "zetaris_required", "http": err.http}) from None
        return SqliteExecutor(run_dir, log, fallback_reason=f"zetaris_unreachable: {message}")
    return executor
