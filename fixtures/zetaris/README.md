# Recorded Zetaris result sets

One `qNN_<name>.json` per analytics query, recorded from the live Zetaris instance (2.4.3.1, local Docker) by
`scripts/record_zetaris_results.py --from-fixtures`: the run exports are built straight from `fixtures/raw`
(`investigator/zetaris/fixture_exports.py`), served to the Zetaris driver by the artifact server, registered as
REST tables, flattened by the SchemaStore views and queried with the committed ANSI SQL. Shape:

    { "query_id": "q01_sales_by_wallet_class", "sql_sha256": "<sha256 of the committed .sql file>",
      "zetaris_version": "2.4.3.1", "recorded_at": "<iso>", "backend": "zetaris", "fallback_reason": null,
      "params": {"host": "..."}, "headers": [...], "rows": [[...]], "rowcount": n, "time_used_ms": ms,
      "tables": [{"name": "X402_<RUN8>.<table>", "source_url": "http://host.docker.internal:<port>/<token>/<export>.json"}],
      "exports_sha256": {"<export>": "<sha256>"} }

Rows are typed per the query `.meta.json`. `tests/test_sql_dual_backend.py` compares them with the SQLite
backend row by row, and fails (does not skip) when `sql_sha256` or an export sha256 no longer matches the
repository, so a stale recording is caught. Re-record after changing a query, a view or a raw fixture:

    python scripts/record_zetaris_results.py --from-fixtures

`ZETARIS_*` come from `.env.local` (never printed). Credentials, tokens and headers are never written here.
