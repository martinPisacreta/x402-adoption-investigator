#!/usr/bin/env python3
"""Login, SHOW SERVER VERSION, artifact-server reachability from the Zetaris driver; prints a pass/fail table. Owner: B3.

Checks, in order: .env.local loaded (names only), login, SHOW SERVER VERSION, databases/containers listing,
artifact server bound and answering locally, a one-row REST table created against the artifact server
(host.docker.internal:<port>) and SELECTed from the driver, an exploded SchemaStore view over it, and cleanup
(view, container, table, database). Exit 0 when every row passes. When the artifact-server row fails the run
falls back to `direct` + SQLite and README states it. Never prints credentials or tokens.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import load_env_local, read_env  # noqa: E402
from investigator.zetaris import ddl  # noqa: E402
from investigator.zetaris.artifact_server import ArtifactServer  # noqa: E402
from investigator.zetaris.client import ZetarisClient, ZetarisError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=None, help="override ZETARIS_BASE_URL")
    parser.add_argument("--host-for-driver", default=None, help="override ARTIFACT_SERVER_HOST (how the driver reaches this machine)")
    parser.add_argument("--json", action="store_true", help="print the result table as JSON")
    args = parser.parse_args(argv)

    loaded = load_env_local(REPO_ROOT / ".env.local")
    env = read_env()
    if args.base_url:
        env.zetaris_base_url = args.base_url.rstrip("/")
    if args.host_for_driver:
        env.artifact_server_host = args.host_for_driver
    rows: list[dict] = []

    def record(name: str, ok: bool, detail: str, ms: int = 0) -> None:
        rows.append({"check": name, "pass": ok, "detail": detail, "ms": ms})

    record("env", bool(env.zetaris_password or env.zetaris_api_key),
           f".env.local names: {', '.join(loaded) or '(none)'}; base_url {env.zetaris_base_url}")
    client = ZetarisClient(base_url=env.zetaris_base_url, username=env.zetaris_username, password=env.zetaris_password,
                           api_key=env.zetaris_api_key, org_id=env.zetaris_org_id, api_style=env.zetaris_api_style,
                           timeout=30.0)
    server: ArtifactServer | None = None
    run_id = f"preflight-{int(time.time())}"
    created_db = created_table = created_container = created_view = False
    container_id = None
    try:
        t = time.monotonic()
        try:
            client.login()
            record("login", True, f"org id {'present' if client.org_id else 'missing'}", int((time.monotonic() - t) * 1000))
        except ZetarisError as err:
            record("login", False, f"HTTP {err.http}: {err.message}", int((time.monotonic() - t) * 1000))
            return finish(rows, args.json)
        t = time.monotonic()
        try:
            version = client.server_version()
            record("version", True, version, int((time.monotonic() - t) * 1000))
        except ZetarisError as err:
            record("version", False, f"HTTP {err.http}: {err.message}")
        t = time.monotonic()
        try:
            dbs = client.databases()
            containers = client.containers()
            record("metadata", True, f"{len(dbs)} databases, {len(containers)} containers; leftovers: "
                   f"{[d.get('name') for d in dbs if str(d.get('name', '')).startswith('X402_')] or 'none'}",
                   int((time.monotonic() - t) * 1000))
        except ZetarisError as err:
            record("metadata", False, f"HTTP {err.http}: {err.message}")

        tmp = Path(tempfile.mkdtemp(prefix="x402-preflight-"))
        (tmp / "probe.json").write_text(json.dumps({"items": [{"k": "preflight", "n": 1}], "ok": True}) + "\n", encoding="utf-8")
        server = ArtifactServer(tmp, host_for_driver=env.artifact_server_host, port=env.artifact_server_port)
        try:
            port = server.start()
            from urllib.request import urlopen
            with urlopen(server.local_url_for("probe"), timeout=5) as resp:
                local_ok = resp.status == 200 and b"preflight" in resp.read()
            record("artifact-server-local", local_ok, f"bound 0.0.0.0:{port}; driver URL {server.url_for('probe')}")
        except OSError as err:
            record("artifact-server-local", False, f"could not bind/serve: {err}")
            return finish(rows, args.json)

        t = time.monotonic()
        try:
            client.ddl(ddl.create_database(run_id))
            created_db = True
            client.ddl(ddl.create_rest_table(run_id, "probe", server.url_for("probe")))
            created_table = True
            client.ddl(ddl.cache_table(run_id, "probe"))
            result = client.run_query(f"SELECT ok FROM {ddl.database_name(run_id)}.probe", limit=10)
            ok = bool(result.data) and str(result.data[0][0]).lower() == "true"
            record("artifact-server-from-driver", ok,
                   f"REST table over {server.url_for('probe')} returned {result.data}; server saw {len(server.requests)} request(s)",
                   int((time.monotonic() - t) * 1000))
        except ZetarisError as err:
            hint = " (driver cannot reach host.docker.internal:<port>? firewall? set ARTIFACT_SERVER_HOST)" if not server.requests else ""
            record("artifact-server-from-driver", False, f"HTTP {err.http}: {err.message}{hint}", int((time.monotonic() - t) * 1000))
        t = time.monotonic()
        try:
            client.ddl(ddl.create_container(run_id))
            created_container = True
            for c in client.containers():
                if c.get("name") == ddl.container_name(run_id):
                    container_id = c.get("containerId")
            client.ddl(ddl.create_view(run_id, "probe_items",
                                       f"SELECT i.k AS k, CAST(i.n AS BIGINT) AS n FROM {ddl.database_name(run_id)}.probe LATERAL VIEW explode(items) AS i"))
            created_view = True
            result = client.run_query(f"SELECT k, n FROM {ddl.container_name(run_id)}.probe_items", limit=10)
            record("schemastore-view", result.data == [["preflight", "1"]], f"rows {result.data}", int((time.monotonic() - t) * 1000))
        except ZetarisError as err:
            record("schemastore-view", False, f"HTTP {err.http}: {err.message}", int((time.monotonic() - t) * 1000))
    finally:
        problems: list[str] = []
        for enabled, statement in ((created_view, ddl.drop_view("probe_items", run_id)),
                                   (created_table, ddl.uncache_table(run_id, "probe")),
                                   (created_table, ddl.drop_table(run_id, "probe")),
                                   (created_db, ddl.drop_database(run_id))):
            if not enabled:
                continue
            try:
                client.ddl(statement)
            except ZetarisError as err:
                problems.append(f"{statement.split(' ')[0]}: HTTP {err.http}")
            if statement.startswith("DROP SCHEMASTORE VIEW") and created_container and container_id is not None:
                try:
                    client.delete_container(int(container_id))
                except ZetarisError as err:
                    problems.append(f"delete container: HTTP {err.http}")
        if created_db or created_container:
            record("cleanup", not problems, "; ".join(problems) or "database, table, view and container removed")
        if server is not None:
            server.stop()
    return finish(rows, args.json)


def finish(rows: list[dict], as_json: bool) -> int:
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        width = max(len(r["check"]) for r in rows)
        for r in rows:
            print(f"{'PASS' if r['pass'] else 'FAIL'}  {r['check']:<{width}}  {r['ms']:>6} ms  {r['detail']}")
    return 0 if all(r["pass"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
