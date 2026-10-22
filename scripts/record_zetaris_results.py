#!/usr/bin/env python3
"""Run the analytics catalog on the live Zetaris and store typed result sets under fixtures/zetaris/. Owner: B3.

Reads ZETARIS_* from .env.local (never printed), builds the per-run objects over the exports of a finished run
(logs/<run_id>/exports via the artifact server) or, with --from-fixtures, over exports built straight from
fixtures/raw (investigator/zetaris/fixture_exports.py), runs all catalog queries through ZetarisExecutor, casts
rows per .meta.json and writes fixtures/zetaris/<qNN>.json with sql_sha256, zetaris_version, recorded_at, the
bound params and the sha256 of every export (so tests/test_sql_dual_backend.py can tell when a recording is
stale). Cleans up its Zetaris objects. Exit 1 when any query did not run on Zetaris.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import RunConfig, load_env_local, read_env  # noqa: E402
from investigator.zetaris.catalog import load_catalog  # noqa: E402
from investigator.zetaris.client import ZetarisError  # noqa: E402
from investigator.zetaris.executor import ZetarisExecutor  # noqa: E402
from investigator.zetaris.fixture_exports import build_fixture_exports, default_params, exports_sha256, public_urls  # noqa: E402


def exports_of_run(run_dir: Path) -> dict[str, str]:
    exports_dir = Path(run_dir) / "exports"
    return {p.stem: str(p) for p in sorted(exports_dir.glob("*.json")) if not p.name.startswith("world-")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", nargs="?", help="logs/<run_id> whose exports feed the REST tables")
    parser.add_argument("--from-fixtures", action="store_true", help="build the exports from fixtures/raw instead")
    parser.add_argument("--host", default=None, help=":host parameter (default: derived from the exports)")
    parser.add_argument("--wallet", default=None, help=":wallet parameter (default: first external payer)")
    parser.add_argument("--since", default=None, help=":since parameter (unused by the current catalog)")
    parser.add_argument("--out", default=str(REPO_ROOT / "fixtures" / "zetaris"))
    parser.add_argument("--zetaris-source", choices=("captured", "direct"), default="captured")
    parser.add_argument("--only", default=None, help="comma-separated query ids to record")
    parser.add_argument("--keep-zetaris-objects", action="store_true")
    args = parser.parse_args(argv)
    if not args.run_dir and not args.from_fixtures:
        parser.error("give a run_dir or --from-fixtures")

    load_env_local(REPO_ROOT / ".env.local")
    env = read_env()
    if not env.has_zetaris():
        print("ZETARIS_PASSWORD (or ZETARIS_API_KEY) is not configured; nothing recorded")
        return 1
    config = RunConfig(requested_mode="live", mode="live", env=env, keep_zetaris_objects=args.keep_zetaris_objects)
    tmp = None
    if args.from_fixtures:
        tmp = Path(tempfile.mkdtemp(prefix="x402-record-"))
        exports = build_fixture_exports(REPO_ROOT / "fixtures" / "raw", tmp / "exports")
        urls = public_urls(REPO_ROOT / "fixtures" / "raw")
        run_dir = tmp
    else:
        run_dir = Path(args.run_dir)
        exports = exports_of_run(run_dir)
        urls = {}
    params = default_params(exports)
    if args.host:
        params["host"] = args.host.lower()
    if args.wallet:
        params["wallet"] = args.wallet.lower()
    if args.since:
        params["since"] = args.since
    catalog = load_catalog(REPO_ROOT / "investigator" / "sql")
    only = set(args.only.split(",")) if args.only else None
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    recorded_at = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    shas = exports_sha256(exports)

    run_id = f"record-{recorded_at.replace(':', '').replace('-', '')}"
    executor = ZetarisExecutor(config, run_id, run_dir, None, zetaris_source=args.zetaris_source,
                               keep_objects=args.keep_zetaris_objects)
    zetaris_log: list[dict] = []
    executor.client.logger = zetaris_log.append
    failures = 0
    try:
        try:
            version = executor.connect()
        except ZetarisError as err:
            print(f"Zetaris unreachable: HTTP {err.http}: {err.message}")
            return 1
        print(f"Zetaris {version}; run objects {run_id}; exports: {', '.join(sorted(exports))}")
        executor.ensure_views(exports, urls)
        for view, status in executor.view_status.items():
            print(f"  view {view:<18} {status.get('backend'):<8} {status.get('reason') or ''}")
        for qid, spec in catalog.items():
            if only and qid not in only:
                continue
            bound_params = {k: params[k] for k in spec.params if k in params}
            missing = [k for k in spec.params if k not in bound_params]
            if missing:
                print(f"  {qid}: skipped (no value for {missing})")
                failures += 1
                continue
            result = executor.execute(qid, spec.sql, bound_params, spec.meta)
            on_zetaris = result.executor == "zetaris"
            if not on_zetaris:
                failures += 1
            record = {
                "query_id": qid, "sql_sha256": spec.sql_sha256, "zetaris_version": result.zetaris_version,
                "recorded_at": recorded_at, "backend": "zetaris" if on_zetaris else "sqlite-fallback",
                "fallback_reason": result.fallback_reason, "params": bound_params, "headers": result.headers,
                "rows": result.rows, "rowcount": result.rowcount, "time_used_ms": result.time_used_ms,
                "tables": result.tables, "exports_sha256": shas,
                "note": "typed per .meta.json; recorded by scripts/record_zetaris_results.py over the fixture exports",
            }
            (out_dir / f"{qid}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"  {qid:<36} {record['backend']:<16} rows={result.rowcount:<4} {result.time_used_ms:>6} ms  "
                  f"{result.fallback_reason or ''}")
    finally:
        executor.cleanup()
        if tmp is not None:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
    print(f"recorded {len(list(out_dir.glob('q*.json')))} result sets under {out_dir}; {failures} not on Zetaris; "
          f"{len(zetaris_log)} REST calls")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
