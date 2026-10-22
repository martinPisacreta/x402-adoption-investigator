#!/usr/bin/env python3
"""Drop leftover X402_* Lightning databases and x402_* SchemaStore containers from crashed runs. Owner: B3.

Lists databases and containers through the REST proxy, drops those matching the per-run prefix (DROP SCHEMASTORE
VIEW <view> FROM <container> for every known view, DELETE container, UNCACHE + DROP LIGHTNING DATABASE TABLE
for every table reported by SHOW LIGHTNING TABLES, DROP LIGHTNING DATABASE) and prints what it removed.
--dry-run lists only. Never prints credentials.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import load_env_local, read_env  # noqa: E402
from investigator.zetaris.client import ZetarisClient, ZetarisError  # noqa: E402
from investigator.zetaris.views_sqlite import VIEW_NAMES  # noqa: E402

DB_PREFIX = "X402_"
CONTAINER_PREFIX = "x402_"


def list_tables(client: ZetarisClient, database: str) -> list[str]:
    try:
        result = client.run_query(f"SHOW LIGHTNING TABLES {database}", limit=1000)
    except ZetarisError:
        return []
    names: list[str] = []
    for row in result.data:
        for cell in row:
            if isinstance(cell, str) and cell and " " not in cell:
                names.append(cell)
                break
    return names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db-prefix", default=DB_PREFIX)
    parser.add_argument("--container-prefix", default=CONTAINER_PREFIX)
    args = parser.parse_args(argv)

    load_env_local(REPO_ROOT / ".env.local")
    env = read_env()
    client = ZetarisClient(base_url=env.zetaris_base_url, username=env.zetaris_username, password=env.zetaris_password,
                           api_key=env.zetaris_api_key, org_id=env.zetaris_org_id, api_style=env.zetaris_api_style)
    try:
        client.login()
    except ZetarisError as err:
        print(f"login failed: HTTP {err.http}: {err.message}")
        return 1
    databases = [d for d in client.databases() if str(d.get("name", "")).startswith(args.db_prefix)]
    containers = [c for c in client.containers() if str(c.get("name", "")).startswith(args.container_prefix)]
    print(f"leftover databases: {[d.get('name') for d in databases] or 'none'}")
    print(f"leftover containers: {[c.get('name') for c in containers] or 'none'}")
    if args.dry_run:
        return 0
    removed = 0
    for c in containers:
        name = c.get("name")
        for view in VIEW_NAMES:
            try:
                client.ddl(f"DROP SCHEMASTORE VIEW {view} FROM {name}")
                print(f"dropped view {name}.{view}")
            except ZetarisError:
                pass
        try:
            client.delete_container(int(c["containerId"]))
            print(f"deleted container {name}")
            removed += 1
        except (ZetarisError, KeyError, ValueError) as err:
            print(f"could not delete container {name}: {err}")
    for d in databases:
        name = str(d.get("name"))
        for table in list_tables(client, name):
            for statement in (f"UNCACHE TABLE {name}.{table}", f"DROP LIGHTNING DATABASE TABLE {name}.{table}"):
                try:
                    client.ddl(statement)
                except ZetarisError:
                    pass
            print(f"dropped table {name}.{table}")
        try:
            client.ddl(f"DROP LIGHTNING DATABASE {name}")
            print(f"dropped database {name}")
            removed += 1
        except ZetarisError as err:
            print(f"could not drop database {name}: HTTP {err.http}: {err.message}")
    print(f"removed {removed} object(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
