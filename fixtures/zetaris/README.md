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

Not recorded yet: q20_catalog_unique_payers_max, q21_catalog_top_hosts_by_listings and q22_payer_chain_facts (with
the `chain_facts` view) were added on 2026-10-22 after the recording session, so that every registry metric has an
SQL half; no Zetaris login was available afterwards. They run on SQLite in every sample run and every claim compares
them with its reducer, and `tests/test_sql_dual_backend.py` names them in `NOT_YET_RECORDED`, so a missing recording
of any other query still fails. The command above records them as well.

Line endings. The 19 result sets were recorded on Windows before the exporters wrote LF explicitly, so the export
files Zetaris read ended in CRLF. Every export is a single line of canonical JSON, so the CRLF and LF files hold the
same document; only the terminator differs. On 2026-10-22 the exporters were fixed to write LF on every OS
(`newline="\n"`) and `exports_sha256` was re-hashed over the LF files. The hashes of the bytes Zetaris actually
read are kept in `exports_sha256_recorded_crlf`: each one equals the sha256 of the LF export with its final `\n`
replaced by `\r\n`, checked for all 228 entries when the hashes were rewritten. Rows, headers, params and
`sql_sha256` were not touched, and `tests/test_sql_dual_backend.py` still compares them with SQLite row by row.
