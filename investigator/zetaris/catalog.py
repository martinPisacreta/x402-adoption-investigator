"""Analytics SQL catalog loader: sql/analytics/qNN_name.sql + .meta.json, sha256 per query, parameter binding
(SPEC.md section 10.4). Owner: B3.

Parameters (:host, :wallet, :since) are bound here with quoting rules, never string-formatted by agents. The
loader computes sql_sha256 over the file bytes (and copies it into meta["sql_sha256"] so executors can report
it) and the Analyst logs it in every claim derivation. lint() enforces the ANSI subset: no explode, from_json,
LATERAL, parse_url, get_json_object; one statement; no trailing semicolon. referenced_views() lists the view
names a query reads (FROM / JOIN), which the Zetaris executor uses to decide per-query fallback.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

ANSI_FORBIDDEN: tuple[str, ...] = ("explode", "from_json", "LATERAL", "parse_url", "get_json_object")
PARAM_RE = re.compile(r":(host|wallet|since)\b")
_FROM_JOIN_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


@dataclass
class QuerySpec:
    id: str
    path: Path
    sql: str
    sql_sha256: str
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def params(self) -> list[str]:
        return list(self.meta.get("params", []))

    @property
    def columns(self) -> dict[str, str]:
        return dict(self.meta.get("columns", {}))

    @property
    def sources(self) -> list[str]:
        return list(self.meta.get("sources", []))

    @property
    def expected_rows_bound(self) -> int:
        return int(self.meta.get("expected_rows_bound", 0) or 0)

    @property
    def views(self) -> list[str]:
        return referenced_views(self.sql)


def load_catalog(sql_dir: Path) -> dict[str, QuerySpec]:
    """Every sql/analytics/qNN_*.sql with its .meta.json, keyed by query id (file stem). The sha256 is computed
    over the committed file bytes and copied into meta["sql_sha256"]."""
    analytics = Path(sql_dir) / "analytics"
    catalog: dict[str, QuerySpec] = {}
    for sql_path in sorted(analytics.glob("q*.sql")):
        raw = sql_path.read_bytes()
        meta_path = sql_path.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
        sha = hashlib.sha256(raw).hexdigest()
        meta["sql_sha256"] = sha
        catalog[sql_path.stem] = QuerySpec(id=sql_path.stem, path=sql_path, sql=raw.decode("utf-8").strip(),
                                           sql_sha256=sha, meta=meta)
    return catalog


def sql_sha256_for(sql: str, meta: Optional[dict[str, Any]] = None) -> str:
    """meta["sql_sha256"] when the loader provided it, else the sha256 of the SQL text (ad-hoc statements)."""
    if meta and isinstance(meta.get("sql_sha256"), str) and len(meta["sql_sha256"]) == 64:
        return meta["sql_sha256"]
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def quote_literal(value: Any) -> str:
    """SQL string literal with single quotes doubled; ints/floats unquoted; None -> NULL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def bind_params(sql: str, params: dict[str, Any], meta: dict[str, Any]) -> str:
    """Replace :name placeholders declared in meta["params"] with quoted literals; missing -> KeyError."""
    declared = list(meta.get("params", []))

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in declared:
            raise KeyError(f"parameter :{name} is not declared in meta.params {declared}")
        if name not in params:
            raise KeyError(f"parameter :{name} has no value")
        return quote_literal(params[name])

    return PARAM_RE.sub(repl, sql)


def lint(sql: str) -> list[str]:
    """ANSI-subset lint: forbidden tokens, trailing semicolon, more than one statement."""
    problems: list[str] = []
    lowered = sql.lower()
    for token in ANSI_FORBIDDEN:
        if token.lower() in lowered:
            problems.append(f"forbidden token {token}")
    if sql.rstrip().endswith(";"):
        problems.append("trailing semicolon")
    if ";" in sql.rstrip().rstrip(";"):
        problems.append("more than one statement")
    return problems


def referenced_views(sql: str, view_names: Optional[Iterable[str]] = None) -> list[str]:
    """View names read by a query (every identifier after FROM / JOIN that is not a subquery), in order of
    first appearance; filtered to `view_names` when given."""
    allowed = set(view_names) if view_names is not None else None
    found: list[str] = []
    for match in _FROM_JOIN_RE.finditer(sql):
        name = match.group(1)
        if allowed is not None and name not in allowed:
            continue
        if name.lower() in ("select",):
            continue
        if name not in found:
            found.append(name)
    return found
