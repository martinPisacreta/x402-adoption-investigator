# x402 Adoption Investigator report: `live-ns-ex01-20261024`

**Status:** partial  
**Mode:** live (fetcher live+fixture-fallback, executor sqlite, generator nightshift:meterless/auto, fixtures captured 2026-10-22T00:05:00Z)

## Question

How real is the adoption of tienda.textokit.store? Who pays, how much, do they come back, and why do registries disagree about it?

## Plan

Playbook `seller_profile` (planner source: rules).  
Sources: receipts, store_openapi, cdp_search, agent402, vet402, x402_trust, base_rpc.  
Queries: q01_sales_by_wallet_class, q02_external_payers, q03_repeat_external_payers, q04_top_payer_share, q05_external_sales_by_service, q06_bazaar_host_listings, q07_bazaar_payto_vs_receipts_seller, q12_registry_count_openapi, q13_registry_count_agent402, q14_registry_count_vet402, q15_registry_count_trust_search, q16_settlements_vs_receipts, q18_external_sales_by_day.  
Memories retrieved by the Planner: mem-28, mem-43, mem-52.

## Verdict

**self_settled_mostly** (confidence 0.9; why: c07)

## Answer

Self-settled_mostly (confidence 0.90) The total number of settled sales is 80 [c01], with 56 of those sales paid from the seller's own wallets [c02], and 3 distinct own wallets settled those self-payments [c03]. The remaining 24 sales were paid by external wallets [c04], totaling 55000 atomic USDC (0.055 USDC) [c05]. External sales settled inside the 30-day window ending 2026-10-24: 24 [c13]. Registries disagree on the endpoint count for tienda.textokit.store: cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36 [c18]. The top external payer 0xec2abd3eda89bed90124736e317e847d5fb6d034 made 20 of 24 external sales (83.3 %) [c09]. - External sales per day: 2026-10-20=20, 2026-10-21=1, 2026-10-23=1, 2026-10-24=2 [c12]. - 5 sampled settlement(s) verified on Base against the receipts [c19]. - Where sources disagree, registries disagree on the endpoint count for tienda.textokit.store: cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36 [c18], and registries also disagree on the calls_30d_for_host: cdp_search=63, receipts=80, receipts=24 [k02].

## Claims (accepted)

| id | metric | subject | value | unit | conf. | cross-checked | caveats | conflicts | evidence |
|---|---|---|---|---|---|---|---|---|---|
| c01 | sales_total | seller:host:tienda.textokit.store | 80 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#52`<br>`log://live-ns-ex01-20261024#65` |
| c02 | own_wallet_sales | seller:host:tienda.textokit.store | 56 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#52`<br>`log://live-ns-ex01-20261024#66` |
| c03 | own_wallets_total | seller:host:tienda.textokit.store | 3 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#52`<br>`log://live-ns-ex01-20261024#67` |
| c04 | external_sales_total | seller:host:tienda.textokit.store | 24 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#52`<br>`log://live-ns-ex01-20261024#68` |
| c05 | external_usdc_atomic | seller:host:tienda.textokit.store | 55000 | atomic_usdc | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#52`<br>`log://live-ns-ex01-20261024#69` |
| c06 | external_payers_total | seller:host:tienda.textokit.store | 5 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#53`<br>`log://live-ns-ex01-20261024#70` |
| c07 | self_settlement_share | seller:host:tienda.textokit.store | 56 / 80 = 0.7 | share | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#52`<br>`log://live-ns-ex01-20261024#71` |
| c08 | repeat_external_payers | seller:host:tienda.textokit.store | 1 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#54`<br>`log://live-ns-ex01-20261024#72` |
| c09 | top_payer_share | seller:host:tienda.textokit.store | 20 / 24 = 0.8333333333333334 (payer=0xec2abd3eda89bed90124736e317e847d5fb6d034) | share | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#55`<br>`log://live-ns-ex01-20261024#73` |
| c10 | external_window | seller:host:tienda.textokit.store | 2026-10-20T04:51:24.352Z..2026-10-24T14:01:45.472Z | iso8601 | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#53`<br>`log://live-ns-ex01-20261024#74` |
| c11 | external_sales_by_service | seller:host:tienda.textokit.store | cuit-ar=4, normativa-ar=4, x402-ping=2, bcra-indicadores=1, cartas-ar=1, comparar-textos=1, crypto-prices=1, dev-utils=1, direcciones-ar=1, dolar-ar=1, fx-rates=1, polymarket-markets=1, source-bounded-summary=1, translate-es=1, url-text=1, web-read=1, x402-adoption=1 | text | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#56`<br>`log://live-ns-ex01-20261024#75` |
| c12 | external_sales_by_day | seller:host:tienda.textokit.store | 2026-10-20=20, 2026-10-21=1, 2026-10-23=1, 2026-10-24=2 | text | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#64`<br>`log://live-ns-ex01-20261024#76` |
| c13 | external_sales_30d | seller:host:tienda.textokit.store | 24 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | k02 | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br>`log://live-ns-ex01-20261024#64`<br>`log://live-ns-ex01-20261024#77` |
| c14 | bazaar_listings_for_host | seller:host:tienda.textokit.store | 17 | count | 0.9 | True | search_limit_20 (mem-seed-002) | k01 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 4bbf12074ed5…<br>`log://live-ns-ex01-20261024#57`<br>`log://live-ns-ex01-20261024#78` |
| c15 | bazaar_payto_for_host | seller:host:tienda.textokit.store | 1 | count | 0.9 | True | search_limit_20 (mem-seed-002) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 4bbf12074ed5…<br>`log://live-ns-ex01-20261024#57`<br>`log://live-ns-ex01-20261024#79` |
| c16 | bazaar_calls_30d_for_host | seller:host:tienda.textokit.store | 63 | count | 0.9 | True | search_limit_20 (mem-seed-002), basis_mismatch (mem-seed-007) | k02 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 4bbf12074ed5…<br>`log://live-ns-ex01-20261024#57`<br>`log://live-ns-ex01-20261024#80` |
| c17 | bazaar_payto_matches_seller | seller:host:tienda.textokit.store | 17 | count | 0.9 | True | search_limit_20 (mem-seed-002), self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br><https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 4bbf12074ed5…<br>`log://live-ns-ex01-20261024#58`<br>`log://live-ns-ex01-20261024#81` |
| c18 | endpoint_count_for_host | seller:host:tienda.textokit.store | cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36 | text | 0.9 | True | search_limit_20 (mem-seed-002), third_party_aggregate (mem-seed-004) | k01 | <https://tienda.textokit.store/openapi.json> sha256 c5c75d2969a0…<br><https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 4bbf12074ed5…<br><https://agent402.tools/api/index?seller=tienda.textokit.store> sha256 1d154cac8adf…<br><https://agent402.tools/api/route?q=x402%20adoption&include=external> sha256 e07ac9aca6e4…<br><https://vet402.com/api/v1/resolve?q=tienda.textokit.store> sha256 3f553a40c53e…<br><https://x402-trust.com/trust/search?q=tienda.textokit.store&limit=25&offset=0> sha256 f882a1b3144d…<br><https://x402-trust.com/trust/stats> sha256 c44967b492f9…<br><https://x402-trust.com/verified.json?limit=25&offset=0> sha256 73095657c01a…<br>`log://live-ns-ex01-20261024#59`<br>`log://live-ns-ex01-20261024#82` |
| c19 | settlements_verified | seller:host:tienda.textokit.store | 5 | count | 0.9 | True | payer_from_transfer_log (mem-seed-005), self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 2420e3b93c6c…<br><https://mainnet.base.org> sha256 395c4282d446…<br><https://basescan.org/tx/0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d> [verified]<br><https://basescan.org/tx/0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934> [verified]<br><https://basescan.org/tx/0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3> [verified]<br><https://basescan.org/tx/0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2> [verified]<br><https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> [verified]<br>`log://live-ns-ex01-20261024#63`<br>`log://live-ns-ex01-20261024#83` |

### Derivations and critique trail

- **c01** The receipts of tienda.textokit.store list 80 settled sales in total. — query `q01_sales_by_wallet_class` on `sqlite-fallback` (sql sha256 `78da86f00660`), reducer `reducers.sales_total`, 2 row(s), sources receipts, fields ownWallet, amountAtomic; log `log://live-ns-ex01-20261024#52`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c02** 56 of those sales were paid from the seller's own wallets. — query `q01_sales_by_wallet_class` on `sqlite-fallback` (sql sha256 `78da86f00660`), reducer `reducers.own_wallet_sales`, 2 row(s), sources receipts, fields ownWallet; log `log://live-ns-ex01-20261024#52`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c03** 3 distinct own wallets settled those self-payments. — query `q01_sales_by_wallet_class` on `sqlite-fallback` (sql sha256 `78da86f00660`), reducer `reducers.own_wallets_total`, 2 row(s), sources receipts, fields ownWallet, payer; log `log://live-ns-ex01-20261024#52`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c04** 24 sales were paid by external wallets. — query `q01_sales_by_wallet_class` on `sqlite-fallback` (sql sha256 `78da86f00660`), reducer `reducers.external_sales_total`, 2 row(s), sources receipts, fields ownWallet; log `log://live-ns-ex01-20261024#52`
  - critique: r1 C6 accept: memory mem-11 contradicted by c04; marked wrong [mem-11]; r1 C6 accept: memory mem-36 contradicted by c04; marked wrong [mem-36]; r1 C6 accept: memory mem-64 contradicted by c04; marked wrong [mem-64]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c05** External sales total 55000 atomic USDC (0.055 USDC). — query `q01_sales_by_wallet_class` on `sqlite-fallback` (sql sha256 `78da86f00660`), reducer `reducers.external_usdc_atomic`, 2 row(s), sources receipts, fields ownWallet, amountAtomic; log `log://live-ns-ex01-20261024#52`
  - critique: r1 C6 accept: memory mem-12 contradicted by c05; marked wrong [mem-12]; r1 C6 accept: memory mem-37 contradicted by c05; marked wrong [mem-37]; r1 C6 accept: memory mem-65 contradicted by c05; marked wrong [mem-65]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c06** 5 distinct external payers appear in the receipts. — query `q02_external_payers` on `sqlite-fallback` (sql sha256 `af1c53c93fda`), reducer `reducers.external_payers_total`, 5 row(s), sources receipts, fields payer, ownWallet; log `log://live-ns-ex01-20261024#53`
  - critique: r1 C6 accept: memory mem-13 contradicted by c06; marked wrong [mem-13]; r1 C6 accept: memory mem-38 contradicted by c06; marked wrong [mem-38]; r1 C6 accept: memory mem-66 contradicted by c06; marked wrong [mem-66]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c07** Self-settlement share: 56 of 80 sales (70.0 %). — query `q01_sales_by_wallet_class` on `sqlite-fallback` (sql sha256 `78da86f00660`), reducer `reducers.self_settlement_share`, 2 row(s), sources receipts, fields ownWallet; log `log://live-ns-ex01-20261024#52`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c08** 1 external payer(s) bought more than once. — query `q03_repeat_external_payers` on `sqlite-fallback` (sql sha256 `dc5467b281c7`), reducer `reducers.repeat_external_payers`, 1 row(s), sources receipts, fields payer, ownWallet; log `log://live-ns-ex01-20261024#54`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c09** The top external payer 0xec2abd3eda89bed90124736e317e847d5fb6d034 made 20 of 24 external sales (83.3 %). — query `q04_top_payer_share` on `sqlite-fallback` (sql sha256 `9b40ee7dbcad`), reducer `reducers.top_payer_share`, 1 row(s), sources receipts, fields payer, ownWallet; log `log://live-ns-ex01-20261024#55`
  - critique: r1 C6 accept: memory mem-69 contradicted by c09; marked wrong [mem-69]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c10** External sales span 2026-10-20T04:51:24.352Z..2026-10-24T14:01:45.472Z. — query `q02_external_payers` on `sqlite-fallback` (sql sha256 `af1c53c93fda`), reducer `reducers.external_window`, 5 row(s), sources receipts, fields at, ownWallet; log `log://live-ns-ex01-20261024#53`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c11** External sales by service: cuit-ar=4, normativa-ar=4, x402-ping=2, bcra-indicadores=1, cartas-ar=1, comparar-textos=1, crypto-prices=1, dev-utils=1, direcciones-ar=1, dolar-ar=1, fx-rates=1, polymarket-markets=1, source-bounded-summary=1, translate-es=1, url-text=1, web-read=1, x402-adoption=1. — query `q05_external_sales_by_service` on `sqlite-fallback` (sql sha256 `566e3b5c519c`), reducer `reducers.external_sales_by_service`, 17 row(s), sources receipts, fields service, ownWallet; log `log://live-ns-ex01-20261024#56`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c12** External sales per day: 2026-10-20=20, 2026-10-21=1, 2026-10-23=1, 2026-10-24=2. — query `q18_external_sales_by_day` on `sqlite-fallback` (sql sha256 `6e443de0aa36`), reducer `reducers.external_sales_by_day`, 4 row(s), sources receipts, fields at, ownWallet; log `log://live-ns-ex01-20261024#64`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c13** External sales settled inside the 30-day window ending 2026-10-24: 24. — query `q18_external_sales_by_day` on `sqlite-fallback` (sql sha256 `6e443de0aa36`), reducer `reducers.external_sales_30d`, 4 row(s), sources receipts, fields at, ownWallet; log `log://live-ns-ex01-20261024#64`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c14** The CDP Bazaar search returns 17 listings for tienda.textokit.store. — query `q06_bazaar_host_listings` on `sqlite-fallback` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_listings_for_host`, 1 row(s), sources cdp_search, fields resource, payTo; coverage 17 of None (search capped at 20); log `log://live-ns-ex01-20261024#57`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c15** Those listings pay to 1 distinct wallet(s). — query `q06_bazaar_host_listings` on `sqlite-fallback` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_payto_for_host`, 1 row(s), sources cdp_search, fields payTo; coverage 17 of None (search capped at 20); log `log://live-ns-ex01-20261024#57`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c16** Bazaar 30-day call counters summed over the listings of tienda.textokit.store: 63. — query `q06_bazaar_host_listings` on `sqlite-fallback` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_calls_30d_for_host`, 1 row(s), sources cdp_search, fields l30DaysTotalCalls; coverage 17 of None (search capped at 20); log `log://live-ns-ex01-20261024#57`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c17** 17 Bazaar listings pay to the wallet the receipts name as seller. — query `q07_bazaar_payto_vs_receipts_seller` on `sqlite-fallback` (sql sha256 `793f72e81505`), reducer `reducers.bazaar_payto_matches_seller`, 17 row(s), sources cdp_search, receipts, fields payTo, seller; log `log://live-ns-ex01-20261024#58`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c18** Registries disagree on the endpoint count for tienda.textokit.store: cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36. — query `q12_registry_count_openapi` on `sqlite-fallback` (sql sha256 `c9c6c473767b`), reducer `reducers.endpoint_count_for_host`, 1 row(s), sources store_openapi, cdp_search, agent402, vet402, x402_trust, fields paths, paidToolCount, endpoints, total; log `log://live-ns-ex01-20261024#59`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c19** 5 sampled settlement(s) verified on Base against the receipts. — query `q16_settlements_vs_receipts` on `sqlite-fallback` (sql sha256 `82a4f8cbe2ab`), reducer `reducers.settlements_verified`, 5 row(s), sources receipts, base_rpc, fields transaction, payer, amountAtomic; log `log://live-ns-ex01-20261024#63`
  - critique: r1 C6 accept: memory mem-44 contradicted by c19; marked wrong [mem-44]; r1 C6 accept: memory mem-53 contradicted by c19; marked wrong [mem-53]; r1 C6 accept: memory mem-72 contradicted by c19; marked wrong [mem-72]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed

## Rejected claims

None.

## Where sources disagree and why

### k01: endpoint_count_for_host (seller:host:tienda.textokit.store) — explained
- cdp_search: 17 (CDP Bazaar discovery search, one row per listed resource, capped at twenty results) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20>
- store_openapi: 17 (paid operations declared by the store's own openapi.json (x-payment-info)) <https://tienda.textokit.store/openapi.json>
- agent402: paidToolCount=17, toolCount=34 (tools crawled by Agent402 from the store manifest; paidToolCount is the comparable figure) <https://agent402.tools/api/index?seller=tienda.textokit.store>
- vet402: 19 (endpoints vet402 has observed for the host with catalog_status listed) <https://vet402.com/api/v1/resolve?q=tienda.textokit.store>
- x402_trust: 36 (matches reported by the x402-trust keyword search across its own index (listed and delisted)) <https://x402-trust.com/trust/search?q=tienda.textokit.store&limit=25&offset=0>

Bazaar search is capped at twenty results and lists one row per resource; the store openapi declares paid operations; Agent402 counts tools including free ones (paidToolCount is the comparable figure); vet402 counts every endpoint it has observed for the host with catalog_status listed, which can include routes the store no longer declares; x402-trust search counts matches across its own index. In this capture vet402 lists /api/maintenance-helper and /api/preparar-solicitud, which the store openapi does not declare as paid operations; that accounts for the whole difference between the vet402 and openapi counts.
Claims: c14, c18

### k02: calls_30d_for_host (seller:host:tienda.textokit.store) — explained
- cdp_search: 63 (l30DaysTotalCalls summed over the host's Bazaar listings (per-endpoint 30-day counters, search capped at twenty)) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20>
- receipts: 80 (all receipts rows settled since 2026-09-24 (own-wallet rows included)) <https://tienda.textokit.store/receipts.json>
- receipts: 24 (external receipts rows settled since 2026-09-24) <https://tienda.textokit.store/receipts.json>

Bazaar l30DaysTotalCalls is a per-endpoint 30-day counter maintained by the facilitator and includes the seller's own calls; receipts are the seller's own settlement log. Different windows and bases: compare only with the basis stated.
Claims: c16, c13

## Unavailable

None.

## Verifications (Base)

| tx | status | payer | seller | amount | block | facilitator_from | receipt match | reason |
|---|---|---|---|---|---|---|---|---|
| <https://basescan.org/tx/0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d> | verified | 0xec2abd3eda89bed90124736e317e847d5fb6d034 | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 5000 | 53030579 | 0x625d8a65134079f8faaac39a7947c73d93c6ac39 | True | - |
| <https://basescan.org/tx/0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934> | verified | 0x7722dfae6bbed2fd02dc07455c2f5bf14be25e4a | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 53002133 | 0x14fda13953fc30428938e6bf950d036e77214e52 | True | - |
| <https://basescan.org/tx/0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3> | verified | 0xa1d1c0cf717402ff3651aa4873ccf96410b01978 | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52914075 | 0x4c934c63c786157fefd990945b25ea60a0fb0205 | True | - |
| <https://basescan.org/tx/0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2> | verified | 0xc9c7b38c0942914fc8ea12063bc92dcd3b581670 | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52860464 | 0x93f6601151ccb08f333ab4b1cccfb1e188c0be44 | True | - |
| <https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> | verified | 0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52841269 | 0x8f5cb67b49555e614892b7233cfddebfb746e531 | True | - |

## Sources

| name | family | status | http | replayed | stale | sha256 | url |
|---|---|---|---|---|---|---|---|
| store_receipts | receipts | ok | 200 | False | False | 2420e3b93c6c | <https://tienda.textokit.store/receipts.json> |
| store_openapi | store_openapi | ok | 200 | False | False | c5c75d2969a0 | <https://tienda.textokit.store/openapi.json> |
| cdp_search_host | cdp_search | ok | 200 | False | False | 4bbf12074ed5 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> |
| agent402_index | agent402 | ok | 200 | False | False | 1d154cac8adf | <https://agent402.tools/api/index?seller=tienda.textokit.store> |
| agent402_route | agent402 | ok | 200 | False | False | e07ac9aca6e4 | <https://agent402.tools/api/route?q=x402%20adoption&include=external> |
| vet402_resolve | vet402 | ok | 200 | False | False | 3f553a40c53e | <https://vet402.com/api/v1/resolve?q=tienda.textokit.store> |
| trust_search | x402_trust | ok | 200 | False | False | f882a1b3144d | <https://x402-trust.com/trust/search?q=tienda.textokit.store&limit=25&offset=0> |
| trust_stats | x402_trust | ok | 200 | False | False | c44967b492f9 | <https://x402-trust.com/trust/stats> |
| trust_verified | x402_trust | ok | 200 | False | False | 73095657c01a | <https://x402-trust.com/verified.json?limit=25&offset=0> |
| rpc_chain_id | base_rpc | ok | 200 | False | False | 395c4282d446 | <https://mainnet.base.org> |
| rpc_receipt_0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d | base_rpc | ok | 200 | False | False | ecc3c6c0bde9 | <https://mainnet.base.org> |
| rpc_tx_0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d | base_rpc | ok | 200 | False | False | 497c64028977 | <https://mainnet.base.org> |
| rpc_receipt_0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934 | base_rpc | ok | 200 | False | False | 2b5ad69b32db | <https://mainnet.base.org> |
| rpc_tx_0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934 | base_rpc | ok | 200 | False | False | b8bb3221db2f | <https://mainnet.base.org> |
| rpc_receipt_0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3 | base_rpc | ok | 200 | False | False | 9300efb80974 | <https://mainnet.base.org> |
| rpc_tx_0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3 | base_rpc | ok | 200 | False | False | d3302e29f206 | <https://mainnet.base.org> |
| rpc_receipt_0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2 | base_rpc | ok | 200 | False | False | 5a368b92b573 | <https://mainnet.base.org> |
| rpc_tx_0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2 | base_rpc | ok | 200 | False | False | 8cc39ffbb917 | <https://mainnet.base.org> |
| rpc_receipt_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3 | base_rpc | ok | 200 | False | False | 0d0835c75f4a | <https://mainnet.base.org> |
| rpc_tx_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3 | base_rpc | ok | 200 | False | False | 7e3c4f07991d | <https://mainnet.base.org> |
| rpc_balance_0xec2abd3eda89bed90124736e317e847d5fb6d034 | base_rpc | ok | 200 | False | False | d350b003c1f7 | <https://mainnet.base.org> |
| rpc_nonce_0xec2abd3eda89bed90124736e317e847d5fb6d034 | base_rpc | ok | 200 | False | False | f8a766f4e78d | <https://mainnet.base.org> |

## World Model (Meterless)

first pass: {'runId': 'run_bce5c176f222', 'processed': 9, 'skippedUnchanged': 0, 'eventsAppended': 922, 'changed': True}  
second pass: {'runId': 'run_bce5c176f222', 'processed': 0, 'skippedUnchanged': 9, 'eventsAppended': 0, 'changed': False}  
claims asserted: 19, quarantined: 0

## Memory (Meterless H-MEM)

retrieved: 47, cited: mem-seed-002, mem-seed-003, mem-seed-004, mem-seed-005, mem-seed-007, feedback entries: 39, mined: 10

- mem-10: helpful (remembered value 3.0 matches c03)
- mem-11: wrong (remembered value 21.0 differs by more than 5 % from c04 (24.0))
- mem-12: wrong (remembered value 44000.0 differs by more than 5 % from c05 (55000.0))
- mem-13: wrong (remembered value 4.0 differs by more than 5 % from c06 (5.0))
- mem-14: helpful (remembered value 56.0 matches c07)
- mem-15: helpful (remembered value 1.0 matches c08)
- mem-17: not_helpful (retrieved for c10 but not cited)
- mem-28: helpful (remembered value 17.0 matches c14)
- mem-33: helpful (remembered value 77.0 matches c01)
- mem-34: helpful (remembered value 56.0 matches c02)
- mem-35: helpful (remembered value 3.0 matches c03)
- mem-36: wrong (remembered value 21.0 differs by more than 5 % from c04 (24.0))
- mem-37: wrong (remembered value 44000.0 differs by more than 5 % from c05 (55000.0))
- mem-38: wrong (remembered value 4.0 differs by more than 5 % from c06 (5.0))
- mem-39: helpful (remembered value 56.0 matches c07)
- mem-40: helpful (remembered value 1.0 matches c08)
- mem-42: not_helpful (retrieved for c10 but not cited)
- mem-43: helpful (remembered value 17.0 matches c14)
- mem-44: wrong (remembered value 4.0 differs by more than 5 % from c19 (5.0))
- mem-52: helpful (remembered value 17.0 matches c14)
- mem-53: wrong (remembered value 4.0 differs by more than 5 % from c19 (5.0))
- mem-61: helpful (remembered value 77.0 matches c01)
- mem-62: helpful (remembered value 56.0 matches c02)
- mem-63: helpful (remembered value 3.0 matches c03)
- mem-64: wrong (remembered value 21.0 differs by more than 5 % from c04 (24.0))
- mem-65: wrong (remembered value 44000.0 differs by more than 5 % from c05 (55000.0))
- mem-66: wrong (remembered value 4.0 differs by more than 5 % from c06 (5.0))
- mem-67: helpful (remembered value 56.0 matches c07)
- mem-68: helpful (remembered value 1.0 matches c08)
- mem-69: wrong (remembered value 0.0 differs by more than 5 % from c09 (20.0))
- mem-70: not_helpful (retrieved for c10 but not cited)
- mem-72: wrong (remembered value 4.0 differs by more than 5 % from c19 (5.0))
- mem-8: helpful (remembered value 77.0 matches c01)
- mem-9: helpful (remembered value 56.0 matches c02)
- mem-seed-002: helpful (caveat search_limit_20 required and attached to c14)
- mem-seed-003: helpful (caveat self_reported_receipts required and attached to c01)
- mem-seed-004: helpful (caveat third_party_aggregate required and attached to c18)
- mem-seed-005: helpful (caveat payer_from_transfer_log required and attached to c19)
- mem-seed-007: helpful (caveat basis_mismatch required and attached to c01)

## Agents

| role | generator-backed | generator used | attempts | status | notes |
|---|---|---|---|---|---|
| planner | True | nightshift:meterless/auto | 1 | ok | - |
| collector | False | None | 1 | ok | 1 retry: agent402_route ok on attempt 2 of 3 (attempt 1: http 503 Service Unavailable); 9 ok, 0 stale fixture fallback(s), 0 fixture miss(es), 0 unavailable, 1 deferred to the verifier; deferred: base_rpc_batch |
| keeper | False | None | 3 | ok | chain facts asserted: 7; claims asserted as agent facts: 19; ingested 9 observations, 922 events appended; second pass: {'runId': 'run_bce5c176f222', 'processed': 0, 'skippedUnchanged': 9, 'eventsAppended': 0, 'changed': False} |
| verifier | False | None | 1 | ok | 5 tx sampled (most recent external sale per distinct payer, newest first, capped at max_tx_verifications): 5 verified, 0 failed, 0 unverifiable (none); chain facts: 7; cross-checks: 6; settlements export: yes |
| analyst | False | None | 1 | ok | views without export: bazaar_resources, catalog_resources |
| writer | True | nightshift:meterless/auto | 2 | ok | - |
| critic | True | nightshift:meterless/auto | 2 | ok | - |

## Model calls

Which model handled each generator request (`generate` events in run.jsonl; Nightshift receipts).

| role | prompt | provider | requested | model that answered | route | receipt | recorded | log |
|---|---|---|---|---|---|---|---|---|
| planner | planner | meterless | meterless/auto | local-runtime/llama-3.2-3b-instruct-q8 | Llama 3.2 3B Q8 Verifier (Runtime GGUF) (local) | rcpt_req_wtqluy7w | False | log://live-ns-ex01-20261024#6 |
| planner | planner | meterless | meterless/auto | local-runtime/llama-3.2-3b-instruct-q8 | Llama 3.2 3B Q8 Verifier (Runtime GGUF) (local) | rcpt_req_v3gwd5jz | False | log://live-ns-ex01-20261024#9 |
| writer | writer-r1 | meterless | meterless/auto | local-runtime/llama-3.2-3b-instruct-q8 | Llama 3.2 3B Q8 Verifier (Runtime GGUF) (local) | rcpt_req_cw3zytok | False | log://live-ns-ex01-20261024#88 |
| critic | critic-r1 | meterless | meterless/auto | local-runtime/llama-3.2-3b-instruct-q8 | Llama 3.2 3B Q8 Verifier (Runtime GGUF) (local) | rcpt_req_72fzddjl | False | log://live-ns-ex01-20261024#149 |
| writer | writer-r2 | meterless | meterless/auto | local-runtime/llama-3.2-3b-instruct-q8 | Llama 3.2 3B Q8 Verifier (Runtime GGUF) (local) | rcpt_req_4vgv0g2y | False | log://live-ns-ex01-20261024#153 |
| critic | critic-r2 | meterless | meterless/auto | local-runtime/llama-3.2-3b-instruct-q8 | Llama 3.2 3B Q8 Verifier (Runtime GGUF) (local) | rcpt_req_9tw1bunz | False | log://live-ns-ex01-20261024#184 |

Critique rounds: 2. Log files: fetch.jsonl, generator.jsonl, hmem.trace.jsonl, run.jsonl, sql.jsonl, world.audit.jsonl, world.events.jsonl, world.observations.jsonl, zetaris.jsonl.
