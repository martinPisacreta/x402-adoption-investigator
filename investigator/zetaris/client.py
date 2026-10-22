"""Zetaris REST client: login, headers, run_query, paginated start/page/close, metadata GETs, 401 re-login
(SPEC.md section 10.1, verified against Zetaris 2.4.3.1 on 2026-10-22). Owner: B3.

Headers on every call after login: Authorization: Bearer <idToken>, X-Org-ID: <orgId> (from the login response,
never hardcoded), X-Request-ID: <fresh uuid4>. Token lifetime 900 s -> re-login once on 401. Proxy style
(default) uses {base_url}/api/proxy/...; direct style maps the same paths onto /api/v1.0/... with the same JWT
(unverified on this instance; proxy is the tested path). Every call is logged to zetaris.jsonl as
{op, path, http, time_used_ms, sql?, msg?}: never headers, tokens or credentials. Port 8889 is never called.

Stdlib only (urllib). The client never raises on a logged error body containing secrets: scrub() removes the
configured username, password, api key and bearer token from any message before it reaches the logger.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGIN_PATH = "/api/auth/login"
RUN_QUERY_PATH = "/api/proxy/sql-editor/sqls/run-query"
SQL_START_PATH = "/api/proxy/query/sql/start"
SQL_PAGE_PATH = "/api/proxy/query/sql/page"
SQL_CLOSE_PATH = "/api/proxy/query/sql/close/{token}"
DATABASES_PATH = "/api/proxy/lightning-database/databases"
CONTAINERS_PATH = "/api/proxy/view/schema-store-view-containers"
TOKEN_LIFETIME_S = 900
MAX_MSG_CHARS = 500


class ZetarisError(Exception):
    """HTTP or protocol error from the Zetaris REST API (message body kept verbatim, headers never)."""

    def __init__(self, http: int, message: str, path: str) -> None:
        super().__init__(f"HTTP {http} on {path}: {message}")
        self.http = http
        self.message = message
        self.path = path


class ZetarisUnreachable(ZetarisError):
    """Connection-level failure (refused, timeout, DNS): the instance is not reachable at all."""

    def __init__(self, message: str, path: str) -> None:
        super().__init__(0, message, path)


@dataclass
class QueryResult:
    headers: list[str]
    data: list[list[Any]]
    total: int
    time_used_ms: int


@dataclass
class ZetarisClient:
    base_url: str
    username: Optional[str] = field(default=None, repr=False)
    password: Optional[str] = field(default=None, repr=False)
    api_key: Optional[str] = field(default=None, repr=False)
    org_id: Optional[str] = None
    api_style: str = "proxy"
    timeout: float = 30.0
    logger: Optional[Callable[[dict[str, Any]], None]] = None   # zetaris.jsonl side-file writer
    _token: Optional[str] = field(default=None, repr=False)
    _token_at: float = field(default=0.0, repr=False)
    calls: int = 0

    # -- helpers -----------------------------------------------------------------------------------------
    @staticmethod
    def request_id() -> str:
        return str(uuid.uuid4())

    def scrub(self, text: str) -> str:
        """Remove every configured secret from a message before it is logged or raised."""
        out = text or ""
        for secret in (self.password, self.api_key, self._token, self.username):
            if secret and len(secret) >= 3 and secret in out:
                out = out.replace(secret, "***")
        return out[:MAX_MSG_CHARS]

    def _path(self, path: str) -> str:
        """Proxy style keeps /api/proxy/...; direct style maps it onto /api/v1.0/... (same JWT)."""
        if self.api_style == "direct" and path.startswith("/api/proxy/"):
            return "/api/v1.0/" + path[len("/api/proxy/"):]
        return path

    def _log(self, op: str, path: str, http: int, time_used_ms: int, sql: Optional[str] = None,
             msg: Optional[str] = None) -> None:
        if self.logger is None:
            return
        line: dict[str, Any] = {"op": op, "path": path, "http": http, "time_used_ms": time_used_ms}
        if sql is not None:
            line["sql"] = sql if len(sql) <= 4000 else sql[:4000] + " ..."
        if msg is not None:
            line["msg"] = self.scrub(msg)
        try:
            self.logger(line)
        except Exception:  # noqa: BLE001 - logging must never break a call
            pass

    def _raw(self, method: str, path: str, body: Any = None, auth: bool = True,
             timeout: Optional[float] = None) -> tuple[int, Any, int]:
        """One HTTP call. Returns (http, parsed json | text | None, elapsed_ms). Raises ZetarisUnreachable
        on connection failures. Never logs headers."""
        headers: dict[str, str] = {"X-Request-ID": self.request_id(), "Accept": "application/json"}
        if auth:
            if not self._token:
                self.login()
            headers["Authorization"] = f"Bearer {self._token}"
            if self.org_id is not None:
                headers["X-Org-ID"] = str(self.org_id)
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = self.base_url.rstrip("/") + self._path(path)
        started = time.monotonic()
        self.calls += 1
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method),
                         timeout=timeout or self.timeout) as response:
                raw = response.read()
                status = response.status
        except HTTPError as err:
            raw = err.read()
            status = err.code
        except (URLError, OSError, TimeoutError) as err:
            elapsed = int((time.monotonic() - started) * 1000)
            reason = getattr(err, "reason", None) or err
            msg = f"{type(err).__name__}: {reason}"
            self._log("error", path, 0, elapsed, msg=msg)
            raise ZetarisUnreachable(self.scrub(msg), path) from None
        elapsed = int((time.monotonic() - started) * 1000)
        parsed: Any
        if not raw:
            parsed = None
        else:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError:
                parsed = raw[:MAX_MSG_CHARS].decode("utf-8", errors="replace")
        return status, parsed, elapsed

    def _request(self, op: str, method: str, path: str, body: Any = None, sql: Optional[str] = None,
                 timeout: Optional[float] = None, _retry: bool = True) -> Any:
        """Authenticated call with one re-login on 401 and verbatim (scrubbed) error logging."""
        status, parsed, elapsed = self._raw(method, path, body, auth=True, timeout=timeout)
        if status == 401 and _retry:
            self._log(op, path, status, elapsed, sql=sql, msg="401: re-login once")
            self._token = None
            self.login()
            return self._request(op, method, path, body, sql=sql, timeout=timeout, _retry=False)
        if status >= 400:
            msg = self._error_message(parsed)
            self._log(op, path, status, elapsed, sql=sql, msg=msg)
            raise ZetarisError(status, self.scrub(msg), path)
        time_used = elapsed
        if isinstance(parsed, dict) and isinstance(parsed.get("timeUsed"), (int, float)):
            time_used = int(parsed["timeUsed"])
        self._log(op, path, status, time_used, sql=sql)
        return parsed

    @staticmethod
    def _error_message(parsed: Any) -> str:
        if isinstance(parsed, dict):
            for key in ("msg", "message", "error", "detail"):
                if parsed.get(key):
                    return str(parsed[key])
            return json.dumps(parsed, ensure_ascii=False)[:MAX_MSG_CHARS]
        return str(parsed) if parsed is not None else "empty error body"

    # -- auth ----------------------------------------------------------------------------------------------
    def login(self) -> None:
        """POST /api/auth/login {username, password} -> {idToken, refreshToken, orgId}; stores token and org id.
        With an API key and no password the key is used as the bearer token directly (org id from env)."""
        if self.api_key and not self.password:
            self._token = self.api_key
            self._token_at = time.monotonic()
            if self.org_id is None:
                raise ZetarisError(400, "ZETARIS_API_KEY needs ZETARIS_ORG_ID (no login response to read it from)",
                                   LOGIN_PATH)
            self._log("login", LOGIN_PATH, 200, 0, msg="api key used as bearer token (no login call)")
            return
        if not self.username or not self.password:
            raise ZetarisError(401, "ZETARIS_USERNAME / ZETARIS_PASSWORD not configured", LOGIN_PATH)
        body = {"username": self.username, "password": self.password}
        status, parsed, elapsed = self._raw("POST", LOGIN_PATH, body, auth=False)
        if status >= 400 or not isinstance(parsed, dict):
            msg = self._error_message(parsed) if status >= 400 else "login response is not a JSON object"
            self._log("login", LOGIN_PATH, status, elapsed, msg=msg)
            raise ZetarisError(status if status >= 400 else 502, self.scrub(msg), LOGIN_PATH)
        token = parsed.get("idToken") or parsed.get("token") or parsed.get("accessToken")
        if not isinstance(token, str) or not token:
            self._log("login", LOGIN_PATH, status, elapsed, msg="login response carries no idToken")
            raise ZetarisError(502, "login response carries no idToken", LOGIN_PATH)
        self._token = token
        self._token_at = time.monotonic()
        if parsed.get("orgId") is not None:
            self.org_id = str(parsed["orgId"])
        self._log("login", LOGIN_PATH, status, elapsed, msg=f"ok; org id {'present' if self.org_id else 'missing'}")

    def headers(self) -> dict[str, str]:
        """Authorization, X-Org-ID, X-Request-ID (fresh uuid4), Content-Type. Never logged."""
        if not self._token:
            self.login()
        out = {"Authorization": f"Bearer {self._token}", "X-Request-ID": self.request_id(),
               "Content-Type": "application/json", "Accept": "application/json"}
        if self.org_id is not None:
            out["X-Org-ID"] = str(self.org_id)
        return out

    def token_age_s(self) -> float:
        return time.monotonic() - self._token_at if self._token else float("inf")

    # -- SQL -----------------------------------------------------------------------------------------------
    def run_query(self, sql: str, limit: int = 1000, op: str = "run-query",
                  timeout: Optional[float] = None) -> QueryResult:
        """POST run-query {sql (one statement, no trailing semicolon), limit} -> {headers, data, total, timeUsed}."""
        statement = sql.strip().rstrip(";").strip()
        parsed = self._request(op, "POST", RUN_QUERY_PATH, {"sql": statement, "limit": int(limit)},
                               sql=statement, timeout=timeout)
        if not isinstance(parsed, dict):
            raise ZetarisError(502, f"run-query returned {type(parsed).__name__}, expected an object", RUN_QUERY_PATH)
        headers = [str(h) for h in parsed.get("headers") or []]
        data = [list(row) for row in parsed.get("data") or []]
        total = int(parsed.get("total") or len(data))
        time_used = int(parsed.get("timeUsed") or 0)
        return QueryResult(headers=headers, data=data, total=total, time_used_ms=time_used)

    def ddl(self, sql: str, timeout: Optional[float] = None) -> QueryResult:
        """A DDL / CACHE / DROP statement through run-query, logged with op "ddl"."""
        return self.run_query(sql, limit=10, op="ddl", timeout=timeout)

    def start(self, select: str, page_limit: int = 100) -> str:
        parsed = self._request("start", "POST", SQL_START_PATH,
                               {"select": select.strip().rstrip(";"), "pageLimit": int(page_limit)}, sql=select)
        token = None
        if isinstance(parsed, dict):
            token = parsed.get("queryToken") or parsed.get("token") or parsed.get("id")
        if not token:
            raise ZetarisError(502, "start returned no queryToken", SQL_START_PATH)
        return str(token)

    def page(self, token: str, page_number: int, page_limit: int = 100) -> list[dict[str, Any]]:
        path = f"{SQL_PAGE_PATH}?queryToken={token}&pageLimit={int(page_limit)}&pageNumber={int(page_number)}"
        parsed = self._request("page", "GET", path)
        if isinstance(parsed, dict):
            rows = parsed.get("data") or parsed.get("rows") or []
        elif isinstance(parsed, list):
            rows = parsed
        else:
            rows = []
        return [dict(r) if isinstance(r, dict) else {"row": r} for r in rows]

    def close(self, token: str) -> None:
        self._request("close", "DELETE", SQL_CLOSE_PATH.format(token=token))

    # -- metadata --------------------------------------------------------------------------------------------
    def databases(self) -> list[dict[str, Any]]:
        parsed = self._request("metadata", "GET", DATABASES_PATH)
        return [dict(d) for d in parsed] if isinstance(parsed, list) else []

    def containers(self) -> list[dict[str, Any]]:
        parsed = self._request("metadata", "GET", CONTAINERS_PATH)
        return [dict(c) for c in parsed] if isinstance(parsed, list) else []

    def delete_container(self, container_id: int) -> None:
        self._request("cleanup", "DELETE", f"{CONTAINERS_PATH}/{int(container_id)}")

    def server_version(self) -> str:
        """run_query("SHOW SERVER VERSION") -> "2.4.3.1" (first cell of the `version` column)."""
        result = self.run_query("SHOW SERVER VERSION", limit=10, op="run-query")
        if result.data:
            if "version" in result.headers:
                return str(result.data[0][result.headers.index("version")])
            return str(result.data[0][0])
        raise ZetarisError(502, "SHOW SERVER VERSION returned no rows", RUN_QUERY_PATH)
