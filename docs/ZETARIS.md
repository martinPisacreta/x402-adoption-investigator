# Zetaris integration

Owner: B3. What the code in `investigator/zetaris/` does, in order, with the REST calls, DDL and SQL that were
verified against the local instance, the fallback matrix as it shows up in the logs, and the evidence. Every
timing and count below was measured on 2026-10-22 on the demo machine (Zetaris 2.4.3.1, build cb88df0b, Docker
containers ui:3000, api:8888, driver:10000, postgres; Windows 11, Docker Desktop). The exceptions are the runs of
2026-10-24 with Meterless Nightshift, dated where they appear (sections 3, 5 and 6). Credentials, login tokens and
headers never appear in logs, fixtures or this document.

## 1. Instance and REST surface (verified)

Login `POST /api/auth/login {username, password}` returns `{idToken, refreshToken, orgId}` (orgId is an integer;
the client stores it as the `X-Org-ID` header, never hardcoded). Every later call carries
`Authorization: Bearer <idToken>`, `X-Org-ID`, a fresh `X-Request-ID` (uuid4) and is logged to
`logs/<run_id>/zetaris.jsonl` as `{seq, op, path, http, time_used_ms, sql?, msg?}`. A 401 triggers exactly one
re-login. Error bodies are kept verbatim but scrubbed of the configured username, password, API key and token
before they reach the log (`ZetarisClient.scrub`). Port 8889 is never called; `ZETARIS_API_STYLE=direct` maps
`/api/proxy/...` onto `/api/v1.0/...` with the same JWT but was not exercised on this instance (proxy is the
tested path).

Figures in the "measured" column come from the B3 integration probe of 2026-10-22, whose log is not committed. The committed live traces (`examples/live-trace/*/zetaris.jsonl`) show the same steps at: REST table 52-157 ms, view 36-60 ms, database 18-23 ms, container 15-18 ms.

| step | call | measured (B3 probe, not committed) |
|---|---|---|
| login | `POST /api/auth/login` | 45-67 ms |
| version | run-query `SHOW SERVER VERSION` | headers `[version, build_target, build_time, build_tag, build_hash]`, row `2.4.3.1 ... cb88df0b`, 31 ms |
| metadata | `GET /api/proxy/lightning-database/databases`, `GET /api/proxy/view/schema-store-view-containers` | `[{name, tableCount, ...}]`, `[{name, viewCount, containerId}]` |
| database | `CREATE LIGHTNING DATABASE X402_<RUN8> DESCRIBE BY "..."` | 45 ms; `DESCRIBE BY` sanitised to letters, digits, space, `_ . - , ;` |
| REST table | `CREATE LIGHTNING REST TABLE <t> FROM X402_<RUN8> REQUEST(endpoint "...", method "get", response_type "json", http_encoding "URLENCODED") HEADER (user-agent "x402-adoption-investigator") BODY ()` | 93-125 ms for the small exports, 2.7 s for the 32 MB catalog snapshot; CREATE fetches the URL once (schema inference) |
| cache | `CACHE TABLE X402_<RUN8>.<t>` | 80 ms-1 s small, 3.5 s catalog; every uncached SELECT would re-fetch |
| container | `CREATE SCHEMASTORE CONTAINER x402_<run8>` | 45 ms; no `IF NOT EXISTS`, so the executor deletes a leftover container of the same name first |
| view | `CREATE OR REPLACE SCHEMASTORE VIEW <v> WITH CONTAINER x402_<run8> AS SELECT ... LATERAL VIEW explode(...)` | 60-110 ms |
| analytics | `POST /api/proxy/sql-editor/sqls/run-query {"sql", "limit": 1000}` | `{headers, data, total, timeUsed}`, all cells strings; 150-500 ms per catalog query |
| big results | `POST /api/proxy/query/sql/start` / `GET .../page` / `DELETE .../close/{token}` | implemented for `expected_rows_bound > 1000`; no catalog query needs it today, so it is untested live |
| cleanup | `DROP SCHEMASTORE VIEW <v> FROM x402_<run8>`, `DELETE /api/proxy/view/schema-store-view-containers/{id}`, `UNCACHE TABLE`, `DROP LIGHTNING DATABASE TABLE X402_<RUN8>.<t>`, `DROP LIGHTNING DATABASE X402_<RUN8>` | 30-65 ms each; `databases`/`containers` listings show no leftovers afterwards |

Syntax facts learned the hard way (each one is a test now):
- `DROP SCHEMASTORE VIEW v` without `FROM <container>` is a parse error (`expecting 'FROM'`).
- `DROP LIGHTNING TABLE` does not exist; the form is `DROP LIGHTNING DATABASE TABLE <db>.<table>`.
- SchemaStore views are only reachable as `<container>.<view>` (`TABLE_OR_VIEW_NOT_FOUND` otherwise), so
  `ddl.qualify_views()` rewrites `FROM sales` / `JOIN sales` into `x402_<run8>.sales` at execution time; the
  committed SQL and its sha256 are untouched and the rewritten text is in `zetaris.jsonl`.
- A query that references the same exploded view twice (scalar subquery + outer query) fails with
  `MISSING_ATTRIBUTES.RESOLVED_ATTRIBUTE_APPEAR_IN_OPERATION`. q04, q09 and q11 therefore take their totals from
  window functions (`SUM(COUNT(*)) OVER ()`), which work on both backends.
- A top-level JSON array registers as one row per element; an object registers as one row with array columns.

## 2. Where the data comes from: `captured` (default) vs `direct`

`captured`: `investigator/zetaris/artifact_server.py` (stdlib `ThreadingHTTPServer`, daemon thread, bound to
`0.0.0.0:<random port>`, path `/<32-hex run token>/<export>.json`, GET/HEAD only, 404 for anything else, no
listing, no traversal) serves `logs/<run_id>/exports/` to the Zetaris driver at
`http://host.docker.internal:<port>/<token>/<name>.json`. Zetaris therefore computes on exactly the bytes the
Collector hashed, including the Verifier's `settlements.json` (JSON-RPC results cannot be a REST table) and the
decompressed catalog snapshot. Preflight on the demo machine (`python scripts/zetaris_preflight.py`, 2026-10-22):

```
PASS  env                               0 ms  .env.local names: ZETARIS_BASE_URL, ZETARIS_USERNAME, ZETARIS_PASSWORD, ARTIFACT_SERVER_HOST, ARTIFACT_SERVER_PORT; base_url http://localhost:3000
PASS  login                            45 ms  org id present
PASS  version                          47 ms  2.4.3.1
PASS  metadata                         63 ms  2 databases, 0 containers; leftovers: none
PASS  artifact-server-local             0 ms  bound 0.0.0.0:53966; driver URL http://host.docker.internal:53966/<token>/probe.json
PASS  artifact-server-from-driver     452 ms  REST table over http://host.docker.internal:53966/<token>/probe.json returned [['true']]; server saw 3 request(s)
PASS  schemastore-view                311 ms  rows [['preflight', '1']]
PASS  cleanup                           0 ms  database, table, view and container removed
```

`direct` (demo flag `--zetaris-source direct` or `options.zetaris_source`): the public URLs of `receipts_raw`,
`bazaar_page0` and `bazaar_search_host` are registered instead (the executor takes them from the `urls` argument
of `ensure_views(exports, urls)`), everything else still goes through the artifact server. The catalog is never
registered directly. When the driver cannot reach the artifact server (the first REST table fails and the server
saw no request) the executor switches to `direct` for those three tables by itself and logs
`recovery{reason: "artifact_server_unreachable"}`.

## 3. View layer and the ANSI catalog

- Views: `investigator/sql/views/VIEWS.md` (names, columns, parity rules) and `views_sqlite.VIEWS` (the
  machine-readable copy both backends share). Thirteen views, one per source family plus `purchases`,
  `agent402_seller`, `trust_stats` and `chain_facts`. `chain_facts` and the queries q20-q22 were added on
  2026-10-22 after the live session below, so that every metric has an SQL half. They run on SQLite in every sample
  run, and `fixtures/zetaris/` holds no recording of them yet (`tests/test_sql_dual_backend.py` lists them as
  `NOT_YET_RECORDED`). On Zetaris a failure sends the query to SQLite (`recovery{zetaris_query_fallback}`).

  The first runs on record that ran them on Zetaris are those of 2026-10-24 in
  `examples/live-trace-zetaris-nightshift/` (no committed run or run folder in `logs/` has them there before, and the
  driver's log first names `chain_facts` that day; that folder's `machine.txt`):
  - q20 and q21 in example 02, at seq 23-24, over the committed catalog snapshot: their rows equal the SQLite rows
    of `examples/02-catalog-concentration/sql.jsonl`, but no recording or test holds that comparison;
  - `CREATE OR REPLACE SCHEMASTORE VIEW chain_facts ...` with its `conv(substring(result_hex, 3), 16, 10)` returned
    HTTP 200 in every run but example 02, first in example 01 at 22:37 UTC;
  - q22, which reads it, in examples 03-05: 2 rows at seq 38, over that day's live chain facts.

  All with no fallback, and with values equal to the Python reducer's (C2).
- One row per listing: `bazaar_resources`, `bazaar_search` and `catalog_resources` use the primary accept
  (`accepts[0]`). Half of the 28,462 snapshot items carry several accepts (up to 18, across Base, Solana and
  other chains); exploding them would make `COUNT(*)` count accepts, not resources.
- Host parity: Spark's `parse_url(resource, 'HOST')` is `java.net.URI`, which returns NULL for 57 snapshot
  resources (56 with a second `#` such as `.../mcp#tool#tool`, one with stray `]'$`). `views_sqlite.host_of`
  applies the same rejection rules, so both backends report 2,680 distinct hosts on the fixture snapshot.
- Shape-aware DDL: Spark infers the struct schema from the JSON it is served; a field no row carries does not
  exist and a reference to it fails the whole view. `views_zetaris.shape_of()` scans each export once and
  `view_select()` emits `CAST(NULL AS <type>)` for absent fields (for example `maxAmountRequired`, present in about 1,000
  primary catalog accepts (998 `accepts[0]`, 2,202 accepts in all) but in no Bazaar search result).
- Catalog size: the decompressed snapshot (32,471,519 bytes) registers as a REST table in 2.7 s, caches in 3.5 s
  and answers q08-q11 in 0.21-0.44 s (`time_used_ms` in `fixtures/zetaris/q08`-`q11`), so the catalog family stays on Zetaris; the
  `catalog_too_large_for_rest_table` fallback exists but did not fire.
- Analytics: `investigator/sql/analytics/qNN_*.sql` + `.meta.json`, ANSI subset only (lint test), parameters
  bound by the loader with quoting, `sql_sha256` over the file bytes copied into `meta` and reported on every
  `SqlResult`; string cells cast per `meta.columns`; SQLite applies the same 1000-row cap as run-query.

## 4. Fallback matrix (what the logs show)

| condition | behaviour | marks |
|---|---|---|
| sample mode | `SqliteExecutor` only; no socket is ever opened (the guard would raise) | `mounts.executor: sqlite` |
| live, login/connect fails (bounded 10 s) | `build_executor` returns `SqliteExecutor(fallback_reason="zetaris_unreachable: ...")` for the whole run | runner `recovery{zetaris_unreachable}`, status `partial` |
| live, DDL for database/container fails | executor degraded; every query on SQLite | `recovery{zetaris_ddl_failed}`, `sql.jsonl` executor `sqlite` + `fallback_reason` |
| live, one REST table or view cannot be built | queries reading that view run on SQLite, the others stay on Zetaris | `recovery{zetaris_query_fallback}` per query, `fallback_reason: view_unavailable:<view>(<reason>)`, claim `backend: sqlite-fallback` |
| live, a REST table that no query reads cannot be built | nothing falls back | only `zetaris.jsonl` (HTTP 400): no recovery, status unchanged, `--require-zetaris` does not fire (example 02 of `examples/live-trace-zetaris-nightshift/`) |
| live, one query HTTP 400 | that query on SQLite | `fallback_reason: zetaris_http_400: <msg>`, `backend: sqlite-fallback` |
| live, 401 | re-login once, then as above | `zetaris.jsonl` line `msg: "401: re-login once"` |
| live, artifact server cannot bind or driver cannot reach it | direct URLs for receipts/Bazaar, SQLite for the rest | `recovery{artifact_server_unreachable}`, `mounts.zetaris_source: direct` |
| live, catalog snapshot too large | catalog family on SQLite | `fallback_reason: catalog_too_large_for_rest_table` |
| `--require-zetaris` | any of the above that sends a query to SQLite -> `E_ZETARIS_REQUIRED` (`details.reason = "zetaris_required"`) | error object |

Every fallback recovery payload also carries `degraded: true`, so a log reader can grep one word for the degraded
path. `backend_label(result)` turns a `SqlResult` into the `Derivation.backend` value (`zetaris` / `sqlite` /
`sqlite-fallback`). Recovery events go through `executor.on_recovery` when the runner installs `ctx.recover`,
otherwise straight to `run.jsonl`; `tests/test_zetaris_executor.py` exercises each row with a faked HTTP layer.

Real `zetaris.jsonl` lines (from the live probe; the seq is added by the EventLog):

```
{"op": "login", "path": "/api/auth/login", "http": 200, "time_used_ms": 45, "msg": "ok; org id present"}
{"op": "run-query", "path": "/api/proxy/sql-editor/sqls/run-query", "http": 200, "time_used_ms": 19, "sql": "SHOW SERVER VERSION"}
{"op": "ddl", "path": "/api/proxy/sql-editor/sqls/run-query", "http": 200, "time_used_ms": 23, "sql": "CREATE LIGHTNING DATABASE X402_0ABA224D DESCRIBE BY \"x402 adoption investigator run probe-1792633308\""}
```

## 5. Evidence

- `scripts/zetaris_preflight.py`: 8/8 PASS on 2026-10-22 (table in section 2).
- `scripts/record_zetaris_results.py --from-fixtures` (2026-10-22 01:54 UTC; the recorder's own log is not committed, only its result sets): 12/12 views created on Zetaris,
  19/19 queries answered by Zetaris in 146-504 ms each, 96 REST calls, objects dropped afterwards
  (`zetaris_cleanup.py --dry-run` reports none). Results under `fixtures/zetaris/qNN_*.json` with
  `sql_sha256`, `params` and the sha256 of every export.
- `tests/test_sql_dual_backend.py`: every recorded result set equals the SQLite backend over the same exports,
  typed row by typed row; the test fails (not skips) when a recording is stale. Sample numbers that both
  backends agree on over the fixtures: q01 `21 external sales / 4 payers / 44,000 atomic` and `56 / 3 / 354,000`
  own-wallet; q06 `17 listings, 1 payTo`; q08 `28,462 resources, 2,680 hosts, 1,785 payTo, 824,796 calls`;
  q10 `28,347 of 28,462 active`; q12-q15 `17 / 17 of 34 / 19 / 36 (25 returned)`; q16 one verified settlement.
- `tests/test_zetaris_views.py`, `tests/test_zetaris_executor.py`: parity rules, shape-aware DDL, qualification,
  casting, fallback matrix, `--require-zetaris`, secret scrubbing, artifact server.
- Live traces of full runs (integrator, 2026-10-22): `examples/live-trace/live-ex01-20261022/` (example 01 on
  Zetaris: 8 REST tables and 10 views, one per export it produced (its 13 queries read 7 of them); 13 queries on the
  `zetaris` backend in 106-447 ms each, the `time_used_ms` of its `sql.jsonl`; 75 REST calls incl. login, DDL and
  cleanup in its `zetaris.jsonl`; 4/4 settlements verified on Base), `examples/live-trace/live-ex03-20261022/` (`--require-zetaris`), and
  `examples/live-trace/live-nozetaris-ex03/` (credentials withheld: `recovery{zetaris_unreachable}`, whole run on
  SQLite, status `partial`). Secrets were grepped for before copying; none appear.
- Live traces with a model (integrator, 2026-10-24, 22:36-22:42 UTC): `examples/live-trace-zetaris-nightshift/`,
  six runs with `--require-zetaris --require-generator`: the five example inputs with NVIDIA Nemotron 3.5 Lightning
  through Meterless Nightshift, and example 01 again with the local Nemotron Nano. In all six:
  - status `ok` and executor `zetaris`, with no Zetaris recovery;
  - all 76 claims with `derivation.backend: "zetaris"`;
  - `run-query` calls one per `sql` event plus `SHOW SERVER VERSION`, 14, 8, 5, 5, 5 and 14, the analytics queries
    taking 131-1,064 ms each (`time_used_ms` in `zetaris.jsonl`);
  - every object dropped at the end.

  The instance (Docker Compose project `zetaris`) ran with the driver heap at 2 GB (`-Xmx2g`, its `machine.txt`).
  In example 02, `CACHE TABLE X402_B4B1827B.bazaar_page0` returned HTTP 400 ("Connection reset") after 16,686 ms. The
  driver's log shows what happened: stage "In-memory table X402_B4B1827B.bazaar_page0", then
  `java.lang.OutOfMemoryError: Java heap space`. Docker restarted the driver container (`restarts 1`), and the run
  went on to cache the 32 MB catalog snapshot (5,099 ms) and answer its seven queries. No query of that run reads
  the Bazaar page, so no recovery was logged and the status stayed `ok`.

## 6. Known limits

- `direct` API style and the paginated `start/page/close` path are implemented from the SPEC but were not
  exercised on this instance (no catalog query exceeds 1000 rows).
- The SPEC's pitch paragraph quotes 2,713 hosts and 2,177 payTo for the snapshot; with the view semantics above
  (primary accept, lowercased wallets, Java-strict host parsing) both backends report 2,680 and 1,785. The README
  must cite the figures the committed example outputs produce.
- `CACHE TABLE` is a session cache; with `--keep-zetaris-objects` the kept REST tables point at an artifact
  server that no longer runs, so a later uncached SELECT on them fails (logged at cleanup).
- With the driver heap at 2 GB, `CACHE TABLE` of the first Bazaar page (`cdp_page_0`) ran the driver out of memory
  in example 02 of the 24 Oct runs above (once in those six runs). The same driver's log shows the same on 2026-10-22
  at 02:08-02:09 UTC (`X402_5E9C5F9C.bazaar_page0`, stage 463, a session whose log is not committed), while the same
  kind of table cached in about 5 s at 01:54 and 03:16 that day (`machine.txt` of
  `examples/live-trace-zetaris-nightshift/`). On 24 Oct the driver that failed had served example 01 first, and right
  after the restart the same heap cached the 32 MB catalog snapshot in 5,099 ms. Whether the page or the driver's
  accumulated state is the cause is not shown.
  - In the six runs of 24 Oct, a table that failed to cache only marked the views built on it as unavailable; a
    `recovery` line appeared only when a query then fell back, so `run.jsonl` of example 02 does not show the failure
    (its `zetaris.jsonl` does), and `sql.jsonl` listed `bazaar_page0` on every line without its status.
  - Since 25 Oct (code change after those runs) every table that cannot be built logs a `recovery` with reason
    `zetaris_table_failed` (table, export, `to: sqlite`), even when no query reads it, and `sql.jsonl` keeps each
    table's `status` and `reason`. It does not change the run status: only a query that falls back does that. The
    committed run folders are copies of their logs and keep the old behaviour.
  - Neither a retry nor a larger heap has been tried.
