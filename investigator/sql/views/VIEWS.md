# View layer: source of truth (SPEC.md section 10.3)

Both backends expose exactly these view names and columns. On Zetaris they are SchemaStore views built by
`investigator/zetaris/views_zetaris.py` (the only place `LATERAL VIEW explode` and `parse_url` appear). On SQLite
they are Python-built tables loaded by `investigator/zetaris/views_sqlite.py` from the same `logs/<run_id>/exports/`.
Analytics queries in `../analytics/` read only these views and stay in the ANSI subset (no JSON or URL parsing).
`tests/test_sql_dual_backend.py` asserts equal typed rows for every query on both backends; the machine-readable
copy of this table is `views_sqlite.VIEWS` (name, family, columns, export candidates, REST table, builder).

| view | columns | export (observation name) / REST table | Zetaris definition | SQLite builder |
|---|---|---|---|---|
| `sales` | seller, agent_id, settled_at, payer, own_wallet (0/1), amount_atomic BIGINT, tx_hash, x402_version, service, resource, facilitator | `store_receipts.json` / `receipts_raw` | `LATERAL VIEW explode(sales) AS s`, `lower(seller)`, `lower(s.payer)`, `CASE WHEN s.ownWallet THEN 1 ELSE 0 END`, `CAST(s.amountAtomic AS BIGINT)` | `rows_sales` |
| `purchases` | at, url, price_usd DOUBLE, outcome, tx_hash | `store_receipts.json` / `receipts_raw` | `explode(purchases)` | `rows_purchases` |
| `bazaar_resources` | resource, host, service_name, resource_type, last_updated, calls_30d, payers_30d, last_called_at, pay_to, network, asset, amount_atomic, scheme, page | `cdp_page_<N>.json` / `bazaar_page<N>` | `explode(items) AS i` per page, `UNION ALL`, `<N> AS page` | `rows_bazaar(page=N)` |
| `bazaar_search` | same as `bazaar_resources` minus page | `cdp_search_host.json` / `bazaar_search_host` | `explode(resources) AS r` | `rows_bazaar_search` |
| `catalog_resources` | resource, host, calls_30d, payers_30d, last_called_at, pay_to, network, asset, amount_atomic | `catalog_snapshot.json` (decompressed gz) / `catalog_snapshot` | `explode(items) AS i`; verified: 32 MB registers in ~3 s, caches in ~4 s, catalog queries < 1 s | `rows_catalog` |
| `openapi_paid_ops` | path, operation_id, price_usd DOUBLE, network, asset, pay_to | `openapi_paid_ops.json` (rows flattened by the Collector) / `openapi_paid_ops_raw` | top-level array (one row per element) or `explode(rows)` | `rows_openapi_paid_ops` |
| `agent402_tools` | route, slug, paid (0/1), price DOUBLE, declared (0/1), last_verified_at | `agent402_index.json` / `agent402_index` | `explode(tools) AS t` | `rows_agent402_tools` |
| `agent402_seller` | origin, tool_count, paid_tool_count, paywall_ok (0/1), health DOUBLE, routable (0/1), router_dispatch_reason | `agent402_index.json` / `agent402_index` | top-level fields, `paywall.ok` | `rows_agent402_seller` |
| `vet402_endpoints` | canonical_url, method, payee_id, catalog_status, first_seen, last_seen | `vet402_resolve.json` / `vet402_resolve` | `explode(endpoints) AS e` | `rows_vet402_endpoints` |
| `trust_search` | resource, host, delisted (0/1), score, grade, provider_verified (0/1), total_reported | `trust_search.json` / `trust_search_raw` | `explode(results) AS r` + top-level `total` | `rows_trust_search` |
| `trust_stats` | endpoints_listed, endpoints_active, settlements_30d, settled_volume_usd_30d DOUBLE, distinct_payers_30d, distinct_receivers_30d | `trust_stats.json` / `trust_stats_raw` | top-level fields | `rows_trust_stats` |
| `settlements` | tx_hash, payer, seller, amount_atomic, block_number, block_time, facilitator_from, status | `settlements.json` (written by the Verifier) / `settlements_raw` | top-level array or `explode(rows)` | `rows_settlements` |
| `chain_facts` | wallet, predicate, value_atomic BIGINT, result_hex | `chain_facts.json` (written by the Verifier: the raw JSON-RPC `result` of balanceOf / getTransactionCount) / `chain_facts_raw` | top-level array or `explode(rows)`; `value_atomic` = `CAST(conv(substring(result_hex, 3), 16, 10) AS BIGINT)` when `result_hex` is `0x` + hex digits (added 2026-10-22 after the live session; not yet executed on Zetaris) | `rows_chain_facts` (decodes the hex in Python) |

Conventions (each one verified against Zetaris 2.4.3.1 on the fixtures, 2026-10-22):
- One row per listing: `bazaar_resources`, `bazaar_search` and `catalog_resources` take the PRIMARY accept
  (`accepts[0]`), so `COUNT(*)` counts resources (half the catalog items carry several accepts across networks).
- `host` is extracted in the view layer: `lower(parse_url(resource, 'HOST'))` on Zetaris, `views_sqlite.host_of`
  on SQLite, which mirrors `java.net.URI`: a URL with a second `#`, spaces, braces, pipes, quotes, stray brackets
  or a bad percent-escape has a NULL host on both backends (57 of 28,462 snapshot resources).
- `amount_atomic` casts only all-digit strings of `COALESCE(amount, maxAmountRequired)`; decimal USD prices in
  secondary accepts and one value beyond the BIGINT range are NULL on both backends.
- Wallet addresses (`payer`, `seller`, `pay_to`, `facilitator_from`) are lowercased in the view layer; analytics
  may still `LOWER()` defensively.
- Booleans become 0/1 with `CASE WHEN x THEN 1 ELSE 0 END` (NULL counts as 0); counters are `CAST ... AS BIGINT`.
- The Zetaris DDL is shape-aware: a field absent from the served JSON (Spark would not infer it) is emitted as
  `CAST(NULL AS <type>)`, so an export with fewer fields still produces the full column list.
- Zetaris returns every cell as a string; the executor casts per `qNN.meta.json` `columns`.
- Unique payers (`payers_30d`) are per endpoint and are never summed across rows (seed memory mem-seed-001).
- Analytics SQL never references the same view twice (Zetaris rejects a second reference to an exploded view with
  `MISSING_ATTRIBUTES`); totals come from window functions such as `SUM(COUNT(*)) OVER ()`.
- SchemaStore views are queried as `<container>.<view>`; `ddl.qualify_views` rewrites `FROM sales` to
  `x402_<run8>.sales` at execution time, leaving the committed SQL and its sha256 untouched.
