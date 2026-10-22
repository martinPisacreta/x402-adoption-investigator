# x402 Adoption Investigator report: `ex01-seller-audit`

**Status:** ok  
**Mode:** sample (fetcher fixture, executor sqlite, generator null, fixtures captured 2026-10-22T00:05:00Z)

> This run replayed committed fixtures captured 2026-10-22T00:05:00Z; no network, no model calls.

## Question

How real is the adoption of tienda.textokit.store? Who pays, how much, do they come back, and why do registries disagree about it?

## Plan

Playbook `seller_profile` (planner source: rules).  
Sources: receipts, store_openapi, cdp_search, agent402, vet402, x402_trust, base_rpc.  
Queries: q01_sales_by_wallet_class, q02_external_payers, q03_repeat_external_payers, q04_top_payer_share, q05_external_sales_by_service, q06_bazaar_host_listings, q07_bazaar_payto_vs_receipts_seller, q12_registry_count_openapi, q13_registry_count_agent402, q14_registry_count_vet402, q15_registry_count_trust_search, q16_settlements_vs_receipts, q18_external_sales_by_day.  
Memories retrieved by the Planner: none.

## Verdict

**self_settled_mostly** (confidence 0.9; why: c07)

## Answer

Most settlements are the seller paying itself (verdict self_settled_mostly) based on [c07]. The receipts of tienda.textokit.store list 77 settled sales in total [c01]. 56 of those sales were paid from the seller's own wallets [c02]. 3 distinct own wallets settled those self-payments [c03]. 21 sales were paid by external wallets [c04]. External sales total 44000 atomic USDC (0.044 USDC) [c05]. 4 distinct external payers appear in the receipts [c06]. Self-settlement share: 56 of 77 sales (72.7 %) [c07]. 1 external payer(s) bought more than once [c08]. The top external payer 0xec2abd3eda89bed90124736e317e847d5fb6d034 made 18 of 21 external sales (85.7 %) [c09]. External sales span 2026-10-20T04:51:24.352Z..2026-10-21T21:18:17.136Z [c10]. External sales by service: cuit-ar=3, normativa-ar=2, x402-ping=2, bcra-indicadores=1, cartas-ar=1, comparar-textos=1, crypto-prices=1, dev-utils=1, direcciones-ar=1, dolar-ar=1, fx-rates=1, polymarket-markets=1, source-bounded-summary=1, translate-es=1, url-text=1, web-read=1, x402-adoption=1 [c11]. External sales per day: 2026-10-20=20, 2026-10-21=1 [c12]. External sales settled inside the 30-day window ending 2026-10-22: 21 [c13]. The CDP Bazaar search returns 17 listings for tienda.textokit.store [c14]. Those listings pay to 1 distinct wallet(s) [c15]. Bazaar 30-day call counters summed over the listings of tienda.textokit.store: 60 [c16]. 17 Bazaar listings pay to the wallet the receipts name as seller [c17]. Registries disagree on the endpoint count for tienda.textokit.store: cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36 [c18]. 1 sampled settlement(s) verified on Base against the receipts [c19]. Where sources disagree [k01] on endpoint_count_for_host: cdp_search: 17 (CDP Bazaar discovery search, one row per listed resource, capped at twenty results); store_openapi: 17 (paid operations declared by the store's own openapi.json (x-payment-info)); agent402: paidToolCount=17, toolCount=34 (tools crawled by Agent402 from the store manifest; paidToolCount is the comparable figure); vet402: 19 (endpoints vet402 has observed for the host with catalog_status listed); x402_trust: 36 (matches reported by the x402-trust keyword search across its own index (listed and delisted)). Bazaar search is capped at twenty results and lists one row per resource; the store openapi declares paid operations; Agent402 counts tools including free ones (paidToolCount is the comparable figure); vet402 counts every endpoint it has observed for the host with catalog_status listed, which can include routes the store no longer declares; x402-trust search counts matches across its own index. In this capture vet402 lists /api/maintenance-helper and /api/preparar-solicitud, which the store openapi does not declare as paid operations; that accounts for the whole difference between the vet402 and openapi counts [c14] [c18]. Where sources disagree [k02] on calls_30d_for_host: cdp_search: 60 (l30DaysTotalCalls summed over the host's Bazaar listings (per-endpoint 30-day counters, search capped at twenty)); receipts: 77 (all receipts rows settled since 2026-09-22 (own-wallet rows included)); receipts: 21 (external receipts rows settled since 2026-09-22). Bazaar l30DaysTotalCalls is a per-endpoint 30-day counter maintained by the facilitator and includes the seller's own calls; receipts are the seller's own settlement log. Different windows and bases: compare only with the basis stated [c16] [c13]. Caveat [basis_mismatch]: Bazaar counters and receipts use different windows and bases [mem-seed-007]; applies to [c13] [c16]. Caveat [payer_from_transfer_log]: payer and seller were read from the Transfer log, not from tx.from [mem-seed-005]; applies to [c19]. Caveat [search_limit_20]: the Bazaar search is capped at twenty results, so its count is a floor [mem-seed-002]; applies to [c14] [c15] [c16] [c17] [c18]. Caveat [self_reported_receipts]: receipts are self-reported by the seller [mem-seed-003]; applies to [c01] [c02] [c03] [c04] [c05] [c06] [c07] [c08] [c09] [c10] [c11] [c12] [c13] [c17] [c19]. Caveat [third_party_aggregate]: x402-trust figures are third-party aggregates with their own window and index [mem-seed-004]; applies to [c18]. Sample mode: this answer replays committed fixtures captured 2026-10-22T00:05:00Z; no network, no model calls.

## Claims (accepted)

| id | metric | subject | value | unit | conf. | cross-checked | caveats | conflicts | evidence |
|---|---|---|---|---|---|---|---|---|---|
| c01 | sales_total | seller:host:tienda.textokit.store | 77 | count | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#44`<br>`log://ex01-seller-audit#57` |
| c02 | own_wallet_sales | seller:host:tienda.textokit.store | 56 | count | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#44`<br>`log://ex01-seller-audit#58` |
| c03 | own_wallets_total | seller:host:tienda.textokit.store | 3 | count | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#44`<br>`log://ex01-seller-audit#59` |
| c04 | external_sales_total | seller:host:tienda.textokit.store | 21 | count | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#44`<br>`log://ex01-seller-audit#60` |
| c05 | external_usdc_atomic | seller:host:tienda.textokit.store | 44000 | atomic_usdc | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#44`<br>`log://ex01-seller-audit#61` |
| c06 | external_payers_total | seller:host:tienda.textokit.store | 4 | count | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#45`<br>`log://ex01-seller-audit#62` |
| c07 | self_settlement_share | seller:host:tienda.textokit.store | 56 / 77 = 0.7272727272727273 | share | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#44`<br>`log://ex01-seller-audit#63` |
| c08 | repeat_external_payers | seller:host:tienda.textokit.store | 1 | count | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#46`<br>`log://ex01-seller-audit#64` |
| c09 | top_payer_share | seller:host:tienda.textokit.store | 18 / 21 = 0.8571428571428571 (payer=0xec2abd3eda89bed90124736e317e847d5fb6d034) | share | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#47`<br>`log://ex01-seller-audit#65` |
| c10 | external_window | seller:host:tienda.textokit.store | 2026-10-20T04:51:24.352Z..2026-10-21T21:18:17.136Z | iso8601 | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#45`<br>`log://ex01-seller-audit#66` |
| c11 | external_sales_by_service | seller:host:tienda.textokit.store | cuit-ar=3, normativa-ar=2, x402-ping=2, bcra-indicadores=1, cartas-ar=1, comparar-textos=1, crypto-prices=1, dev-utils=1, direcciones-ar=1, dolar-ar=1, fx-rates=1, polymarket-markets=1, source-bounded-summary=1, translate-es=1, url-text=1, web-read=1, x402-adoption=1 | text | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#48`<br>`log://ex01-seller-audit#67` |
| c12 | external_sales_by_day | seller:host:tienda.textokit.store | 2026-10-20=20, 2026-10-21=1 | text | 0.9 | True | self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#56`<br>`log://ex01-seller-audit#68` |
| c13 | external_sales_30d | seller:host:tienda.textokit.store | 21 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | k02 | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br>`log://ex01-seller-audit#56`<br>`log://ex01-seller-audit#69` |
| c14 | bazaar_listings_for_host | seller:host:tienda.textokit.store | 17 | count | 0.9 | True | search_limit_20 (mem-seed-002) | k01 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 da993b56bae8… [replayed]<br>`log://ex01-seller-audit#49`<br>`log://ex01-seller-audit#70` |
| c15 | bazaar_payto_for_host | seller:host:tienda.textokit.store | 1 | count | 0.9 | True | search_limit_20 (mem-seed-002) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 da993b56bae8… [replayed]<br>`log://ex01-seller-audit#49`<br>`log://ex01-seller-audit#71` |
| c16 | bazaar_calls_30d_for_host | seller:host:tienda.textokit.store | 60 | count | 0.9 | True | search_limit_20 (mem-seed-002), basis_mismatch (mem-seed-007) | k02 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 da993b56bae8… [replayed]<br>`log://ex01-seller-audit#49`<br>`log://ex01-seller-audit#72` |
| c17 | bazaar_payto_matches_seller | seller:host:tienda.textokit.store | 17 | count | 0.9 | True | search_limit_20 (mem-seed-002), self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br><https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 da993b56bae8… [replayed]<br>`log://ex01-seller-audit#50`<br>`log://ex01-seller-audit#73` |
| c18 | endpoint_count_for_host | seller:host:tienda.textokit.store | cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36 | text | 0.9 | True | search_limit_20 (mem-seed-002), third_party_aggregate (mem-seed-004) | k01 | <https://tienda.textokit.store/openapi.json> sha256 3cc61746315e… [replayed]<br><https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 da993b56bae8… [replayed]<br><https://agent402.tools/api/index?seller=tienda.textokit.store> sha256 e8a04c9322f4… [replayed]<br><https://agent402.tools/api/route?q=x402%20adoption&include=external> sha256 7bdec79ccccc… [replayed]<br><https://vet402.com/api/v1/resolve?q=tienda.textokit.store> sha256 53d8e517e95e… [replayed]<br><https://x402-trust.com/trust/search?q=tienda.textokit.store&limit=25&offset=0> sha256 d0afd0ef43c9… [replayed]<br><https://x402-trust.com/trust/stats> sha256 26ee64acb78a… [replayed]<br><https://x402-trust.com/verified.json?limit=25&offset=0> sha256 c52eacf29374… [replayed]<br>`log://ex01-seller-audit#51`<br>`log://ex01-seller-audit#74` |
| c19 | settlements_verified | seller:host:tienda.textokit.store | 1 | count | 0.9 | True | payer_from_transfer_log (mem-seed-005), self_reported_receipts (mem-seed-003) | - | <https://tienda.textokit.store/receipts.json> sha256 e179af8c2864… [replayed]<br><https://mainnet.base.org> sha256 c756d78d11b3… [replayed]<br><https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> [replayed, verified]<br>`log://ex01-seller-audit#55`<br>`log://ex01-seller-audit#75` |

### Derivations and critique trail

- **c01** The receipts of tienda.textokit.store list 77 settled sales in total. — query `q01_sales_by_wallet_class` on `sqlite` (sql sha256 `78da86f00660`), reducer `reducers.sales_total`, 2 row(s), sources receipts, fields ownWallet, amountAtomic; log `log://ex01-seller-audit#44`
  - critique: r1 A accept: C1-C7 passed
- **c02** 56 of those sales were paid from the seller's own wallets. — query `q01_sales_by_wallet_class` on `sqlite` (sql sha256 `78da86f00660`), reducer `reducers.own_wallet_sales`, 2 row(s), sources receipts, fields ownWallet; log `log://ex01-seller-audit#44`
  - critique: r1 A accept: C1-C7 passed
- **c03** 3 distinct own wallets settled those self-payments. — query `q01_sales_by_wallet_class` on `sqlite` (sql sha256 `78da86f00660`), reducer `reducers.own_wallets_total`, 2 row(s), sources receipts, fields ownWallet, payer; log `log://ex01-seller-audit#44`
  - critique: r1 A accept: C1-C7 passed
- **c04** 21 sales were paid by external wallets. — query `q01_sales_by_wallet_class` on `sqlite` (sql sha256 `78da86f00660`), reducer `reducers.external_sales_total`, 2 row(s), sources receipts, fields ownWallet; log `log://ex01-seller-audit#44`
  - critique: r1 A accept: C1-C7 passed
- **c05** External sales total 44000 atomic USDC (0.044 USDC). — query `q01_sales_by_wallet_class` on `sqlite` (sql sha256 `78da86f00660`), reducer `reducers.external_usdc_atomic`, 2 row(s), sources receipts, fields ownWallet, amountAtomic; log `log://ex01-seller-audit#44`
  - critique: r1 A accept: C1-C7 passed
- **c06** 4 distinct external payers appear in the receipts. — query `q02_external_payers` on `sqlite` (sql sha256 `af1c53c93fda`), reducer `reducers.external_payers_total`, 4 row(s), sources receipts, fields payer, ownWallet; log `log://ex01-seller-audit#45`
  - critique: r1 A accept: C1-C7 passed
- **c07** Self-settlement share: 56 of 77 sales (72.7 %). — query `q01_sales_by_wallet_class` on `sqlite` (sql sha256 `78da86f00660`), reducer `reducers.self_settlement_share`, 2 row(s), sources receipts, fields ownWallet; log `log://ex01-seller-audit#44`
  - critique: r1 A accept: C1-C7 passed
- **c08** 1 external payer(s) bought more than once. — query `q03_repeat_external_payers` on `sqlite` (sql sha256 `dc5467b281c7`), reducer `reducers.repeat_external_payers`, 1 row(s), sources receipts, fields payer, ownWallet; log `log://ex01-seller-audit#46`
  - critique: r1 A accept: C1-C7 passed
- **c09** The top external payer 0xec2abd3eda89bed90124736e317e847d5fb6d034 made 18 of 21 external sales (85.7 %). — query `q04_top_payer_share` on `sqlite` (sql sha256 `9b40ee7dbcad`), reducer `reducers.top_payer_share`, 1 row(s), sources receipts, fields payer, ownWallet; log `log://ex01-seller-audit#47`
  - critique: r1 A accept: C1-C7 passed
- **c10** External sales span 2026-10-20T04:51:24.352Z..2026-10-21T21:18:17.136Z. — query `q02_external_payers` on `sqlite` (sql sha256 `af1c53c93fda`), reducer `reducers.external_window`, 4 row(s), sources receipts, fields at, ownWallet; log `log://ex01-seller-audit#45`
  - critique: r1 A accept: C1-C7 passed
- **c11** External sales by service: cuit-ar=3, normativa-ar=2, x402-ping=2, bcra-indicadores=1, cartas-ar=1, comparar-textos=1, crypto-prices=1, dev-utils=1, direcciones-ar=1, dolar-ar=1, fx-rates=1, polymarket-markets=1, source-bounded-summary=1, translate-es=1, url-text=1, web-read=1, x402-adoption=1. — query `q05_external_sales_by_service` on `sqlite` (sql sha256 `566e3b5c519c`), reducer `reducers.external_sales_by_service`, 17 row(s), sources receipts, fields service, ownWallet; log `log://ex01-seller-audit#48`
  - critique: r1 A accept: C1-C7 passed
- **c12** External sales per day: 2026-10-20=20, 2026-10-21=1. — query `q18_external_sales_by_day` on `sqlite` (sql sha256 `6e443de0aa36`), reducer `reducers.external_sales_by_day`, 2 row(s), sources receipts, fields at, ownWallet; log `log://ex01-seller-audit#56`
  - critique: r1 A accept: C1-C7 passed
- **c13** External sales settled inside the 30-day window ending 2026-10-22: 21. — query `q18_external_sales_by_day` on `sqlite` (sql sha256 `6e443de0aa36`), reducer `reducers.external_sales_30d`, 2 row(s), sources receipts, fields at, ownWallet; log `log://ex01-seller-audit#56`
  - critique: r1 A accept: C1-C7 passed
- **c14** The CDP Bazaar search returns 17 listings for tienda.textokit.store. — query `q06_bazaar_host_listings` on `sqlite` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_listings_for_host`, 1 row(s), sources cdp_search, fields resource, payTo; coverage 17 of None (search capped at 20); log `log://ex01-seller-audit#49`
  - critique: r1 A accept: C1-C7 passed
- **c15** Those listings pay to 1 distinct wallet(s). — query `q06_bazaar_host_listings` on `sqlite` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_payto_for_host`, 1 row(s), sources cdp_search, fields payTo; coverage 17 of None (search capped at 20); log `log://ex01-seller-audit#49`
  - critique: r1 A accept: C1-C7 passed
- **c16** Bazaar 30-day call counters summed over the listings of tienda.textokit.store: 60. — query `q06_bazaar_host_listings` on `sqlite` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_calls_30d_for_host`, 1 row(s), sources cdp_search, fields l30DaysTotalCalls; coverage 17 of None (search capped at 20); log `log://ex01-seller-audit#49`
  - critique: r1 A accept: C1-C7 passed
- **c17** 17 Bazaar listings pay to the wallet the receipts name as seller. — query `q07_bazaar_payto_vs_receipts_seller` on `sqlite` (sql sha256 `793f72e81505`), reducer `reducers.bazaar_payto_matches_seller`, 17 row(s), sources cdp_search, receipts, fields payTo, seller; log `log://ex01-seller-audit#50`
  - critique: r1 A accept: C1-C7 passed
- **c18** Registries disagree on the endpoint count for tienda.textokit.store: cdp_search=17, store_openapi=17, agent402_paid=17, agent402_tools=34, vet402=19, x402_trust_search=36. — query `q12_registry_count_openapi` on `sqlite` (sql sha256 `c9c6c473767b`), reducer `reducers.endpoint_count_for_host`, 1 row(s), sources store_openapi, cdp_search, agent402, vet402, x402_trust, fields paths, paidToolCount, endpoints, total; log `log://ex01-seller-audit#51`
  - critique: r1 A accept: C1-C7 passed
- **c19** 1 sampled settlement(s) verified on Base against the receipts. — query `q16_settlements_vs_receipts` on `sqlite` (sql sha256 `82a4f8cbe2ab`), reducer `reducers.settlements_verified`, 1 row(s), sources receipts, base_rpc, fields transaction, payer, amountAtomic; log `log://ex01-seller-audit#55`
  - critique: r1 A accept: C1-C7 passed

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
- cdp_search: 60 (l30DaysTotalCalls summed over the host's Bazaar listings (per-endpoint 30-day counters, search capped at twenty)) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20>
- receipts: 77 (all receipts rows settled since 2026-09-22 (own-wallet rows included)) <https://tienda.textokit.store/receipts.json>
- receipts: 21 (external receipts rows settled since 2026-09-22) <https://tienda.textokit.store/receipts.json>

Bazaar l30DaysTotalCalls is a per-endpoint 30-day counter maintained by the facilitator and includes the seller's own calls; receipts are the seller's own settlement log. Different windows and bases: compare only with the basis stated.
Claims: c16, c13

## Unavailable

None.

## Verifications (Base)

| tx | status | payer | seller | amount | block | facilitator_from | receipt match | reason |
|---|---|---|---|---|---|---|---|---|
| <https://basescan.org/tx/0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3> | unverifiable | None | None | None | None | None | None | fixture_miss |
| <https://basescan.org/tx/0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2> | unverifiable | None | None | None | None | None | None | fixture_miss |
| <https://basescan.org/tx/0x068b1930266c6235ee31cbfa87690b8ef14c519bba4f1dafd0b36872b836fe95> | unverifiable | None | None | None | None | None | None | fixture_miss |
| <https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> | verified | 0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52841269 | 0x8f5cb67b49555e614892b7233cfddebfb746e531 | True | - |

## Sources

| name | family | status | http | replayed | stale | sha256 | url |
|---|---|---|---|---|---|---|---|
| store_receipts | receipts | ok | 200 | True | False | e179af8c2864 | <https://tienda.textokit.store/receipts.json> |
| store_openapi | store_openapi | ok | 200 | True | False | 3cc61746315e | <https://tienda.textokit.store/openapi.json> |
| cdp_search_host | cdp_search | ok | 200 | True | False | da993b56bae8 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> |
| agent402_index | agent402 | ok | 200 | True | False | e8a04c9322f4 | <https://agent402.tools/api/index?seller=tienda.textokit.store> |
| agent402_route | agent402 | ok | 200 | True | False | 7bdec79ccccc | <https://agent402.tools/api/route?q=x402%20adoption&include=external> |
| vet402_resolve | vet402 | ok | 200 | True | False | 53d8e517e95e | <https://vet402.com/api/v1/resolve?q=tienda.textokit.store> |
| trust_search | x402_trust | ok | 200 | True | False | d0afd0ef43c9 | <https://x402-trust.com/trust/search?q=tienda.textokit.store&limit=25&offset=0> |
| trust_stats | x402_trust | ok | 200 | True | False | 26ee64acb78a | <https://x402-trust.com/trust/stats> |
| trust_verified | x402_trust | ok | 200 | True | False | c52eacf29374 | <https://x402-trust.com/verified.json?limit=25&offset=0> |
| rpc_chain_id | base_rpc | ok | 200 | True | False | c756d78d11b3 | <https://mainnet.base.org> |
| rpc_receipt_0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3 | base_rpc | fixture_miss | None | True | False | - | <https://mainnet.base.org> |
| rpc_receipt_0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2 | base_rpc | fixture_miss | None | True | False | - | <https://mainnet.base.org> |
| rpc_receipt_0x068b1930266c6235ee31cbfa87690b8ef14c519bba4f1dafd0b36872b836fe95 | base_rpc | fixture_miss | None | True | False | - | <https://mainnet.base.org> |
| rpc_receipt_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3 | base_rpc | ok | 200 | True | False | f7d915c99074 | <https://mainnet.base.org> |
| rpc_tx_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3 | base_rpc | ok | 200 | True | False | 505491d34af3 | <https://mainnet.base.org> |
| rpc_balance_0xec2abd3eda89bed90124736e317e847d5fb6d034 | base_rpc | fixture_miss | None | True | False | - | <https://mainnet.base.org> |
| rpc_nonce_0xec2abd3eda89bed90124736e317e847d5fb6d034 | base_rpc | fixture_miss | None | True | False | - | <https://mainnet.base.org> |

## World Model (Meterless)

first pass: {'runId': 'run_bce5c176f222', 'processed': 9, 'skippedUnchanged': 0, 'eventsAppended': 917, 'changed': True}  
second pass: {'runId': 'run_bce5c176f222', 'processed': 0, 'skippedUnchanged': 9, 'eventsAppended': 0, 'changed': False}  
claims asserted: 19, quarantined: 0

## Memory (Meterless H-MEM)

retrieved: 7, cited: mem-seed-002, mem-seed-003, mem-seed-004, mem-seed-005, mem-seed-007, feedback entries: 5, mined: 10

- mem-seed-002: helpful (caveat search_limit_20 required and attached to c14)
- mem-seed-003: helpful (caveat self_reported_receipts required and attached to c01)
- mem-seed-004: helpful (caveat third_party_aggregate required and attached to c18)
- mem-seed-005: helpful (caveat payer_from_transfer_log required and attached to c19)
- mem-seed-007: helpful (caveat basis_mismatch required and attached to c13)

## Agents

| role | generator-backed | generator used | attempts | status | notes |
|---|---|---|---|---|---|
| planner | True | none | 1 | ok | - |
| collector | False | None | 1 | ok | 9 ok, 0 stale fixture fallback(s), 0 fixture miss(es), 0 unavailable, 1 deferred to the verifier; deferred: base_rpc_batch |
| keeper | False | None | 3 | ok | chain facts asserted: 1; claims asserted as agent facts: 19; ingested 9 observations, 917 events appended; second pass: {'runId': 'run_bce5c176f222', 'processed': 0, 'skippedUnchanged': 9, 'eventsAppended': 0, 'changed': False} |
| verifier | False | None | 1 | ok | 4 tx sampled (most recent external sale per distinct payer, newest first, capped at max_tx_verifications): 1 verified, 0 failed, 3 unverifiable (fixture_miss x3); chain facts: 1; cross-checks: 6; settlements export: yes; tx_count for 0xec2abd3eda89bed90124736e317e847d5fb6d034 not available: fixture_miss; usdc_balance_atomic for 0xec2abd3eda89bed90124736e317e847d5fb6d034 not available: fixture_miss |
| analyst | False | None | 1 | ok | views without export: bazaar_resources, catalog_resources, chain_facts |
| writer | True | none | 1 | ok | - |
| critic | True | none | 1 | ok | - |

Critique rounds: 1. Log files: fetch.jsonl, hmem.trace.jsonl, run.jsonl, sql.jsonl, world.audit.jsonl, world.events.jsonl, world.observations.jsonl.
