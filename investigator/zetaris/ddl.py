"""DDL builders for Zetaris Lightning (SPEC.md section 10.1): CREATE LIGHTNING DATABASE / REST TABLE, SCHEMASTORE
CONTAINER / VIEW, CACHE / UNCACHE / DROP. Owner: B3.

Objects are per run: database X402_<RUN8> (RUN8 = first 8 hex of sha256(run_id), uppercased) and container
x402_<run8>. DESCRIBE BY allows letters, digits, spaces and _ . - , ; only. One REST table = one URL; CREATE
fetches it once for schema inference; CACHE TABLE right after creation (every uncached SELECT re-fetches).

Syntax verified live on Zetaris 2.4.3.1 (2026-10-22): DROP SCHEMASTORE VIEW <view> FROM <container> (the FROM is
mandatory), DROP LIGHTNING DATABASE TABLE <db>.<table>, DROP LIGHTNING DATABASE <db>. SchemaStore views are
queried as <container>.<view>; qualify_views() rewrites the committed ANSI SQL's bare view names accordingly.
"""
from __future__ import annotations

import hashlib
import re

_DESCRIBE_SAFE = re.compile(r"[^A-Za-z0-9 _.\-,;]")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def run8(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8].upper()


def database_name(run_id: str) -> str:
    return f"X402_{run8(run_id)}"


def container_name(run_id: str) -> str:
    return f"x402_{run8(run_id).lower()}"


def describe_by(text: str) -> str:
    return _DESCRIBE_SAFE.sub("_", text)


def check_identifier(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"invalid Lightning identifier {name!r}")
    return name


def quote_string(value: str) -> str:
    """Double-quoted DDL string (endpoint, header values): double quotes and backslashes are escaped."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def create_database(run_id: str) -> str:
    return (f'CREATE LIGHTNING DATABASE {database_name(run_id)} '
            f'DESCRIBE BY "{describe_by("x402 adoption investigator run " + run_id)}"')


def drop_database(run_id: str) -> str:
    return f"DROP LIGHTNING DATABASE {database_name(run_id)}"


def create_rest_table(run_id: str, table: str, url: str, user_agent: str = "x402-adoption-investigator") -> str:
    check_identifier(table)
    return (f'CREATE LIGHTNING REST TABLE {table} FROM {database_name(run_id)} '
            f'REQUEST(endpoint {quote_string(url)}, method "get", response_type "json", http_encoding "URLENCODED") '
            f'HEADER (user-agent {quote_string(user_agent)}) BODY ()')


def cache_table(run_id: str, table: str) -> str:
    return f"CACHE TABLE {database_name(run_id)}.{check_identifier(table)}"


def uncache_table(run_id: str, table: str) -> str:
    return f"UNCACHE TABLE {database_name(run_id)}.{check_identifier(table)}"


def drop_table(run_id: str, table: str) -> str:
    """DROP LIGHTNING DATABASE TABLE <db>.<table> (documented and verified form; the SPEC's shorthand
    `DROP LIGHTNING TABLE` is a parse error on 2.4.3.1)."""
    return f"DROP LIGHTNING DATABASE TABLE {database_name(run_id)}.{check_identifier(table)}"


def create_container(run_id: str) -> str:
    return f"CREATE SCHEMASTORE CONTAINER {container_name(run_id)}"


def create_view(run_id: str, view: str, select_sql: str) -> str:
    """The ONLY place Spark/JSON syntax (LATERAL VIEW explode) may appear is inside select_sql (views_zetaris)."""
    check_identifier(view)
    return f"CREATE OR REPLACE SCHEMASTORE VIEW {view} WITH CONTAINER {container_name(run_id)} AS {select_sql}"


def drop_view(view: str, run_id: str) -> str:
    """DROP SCHEMASTORE VIEW <view> FROM <container> (FROM is mandatory on 2.4.3.1)."""
    return f"DROP SCHEMASTORE VIEW {check_identifier(view)} FROM {container_name(run_id)}"


def qualified_view(run_id: str, view: str) -> str:
    return f"{container_name(run_id)}.{check_identifier(view)}"


def qualify_views(sql: str, run_id: str, view_names: tuple[str, ...] | list[str]) -> str:
    """Rewrite bare view references (FROM sales / JOIN sales) to <container>.<view> for Zetaris. Only the
    view names in `view_names` are touched; column names and aliases are left alone. The committed SQL and
    its sha256 stay untouched (the rewritten text is logged as sql_executed)."""
    container = container_name(run_id)
    pattern = re.compile(r"\b(FROM|JOIN)(\s+)(" + "|".join(re.escape(v) for v in view_names) + r")\b(?!\s*\.)",
                         re.IGNORECASE)
    return pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{container}.{m.group(3)}", sql)


SHOW_SERVER_VERSION = "SHOW SERVER VERSION"
