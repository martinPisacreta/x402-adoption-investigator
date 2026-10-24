# x402 Adoption Investigator report: `nemotron-cloud-ex03-20261024`

**Status:** partial  
**Mode:** live (fetcher live+fixture-fallback, executor sqlite, generator nightshift:nvidia/nemotron-3.5-lightning-30b-a3b, fixtures captured 2026-10-22T00:05:00Z)

## Question

What do we know about the payer 0x097e3F339D0b023605e12A6B81E2d6Cb7571475a: what it bought from tienda.textokit.store, and can the settlement be verified on Base?

## Plan

Playbook `payer_dossier` (planner source: generator).  
Sources: receipts, cdp_search, base_rpc.  
Queries: q16_settlements_vs_receipts, q17_payer_dossier, q06_bazaar_host_listings, q22_payer_chain_facts.  
Memories retrieved by the Planner: mem-49, mem-58, mem-77, mem-seed-005, mem-129.

Hypotheses:
- The payer purchased a specific item from tienda.textokit.store and the receipt is on Base.
- The settlement transaction can be located via the Transfer log from the x402 facilitator.
- The payer's USDC balance was sufficient to cover the atomic purchase amount.
- The transaction nonce matches the expected window for the seller's storefront.
- The receipt data can be cross-referenced with CDP Bazaar search results.

## Verdict

**adoption_thin** (confidence 0.9; why: c03, c07)

## Answer

The evidence shows thin adoption (verdict adoption_thin) based on [c03], [c07]. The CDP Bazaar search returns 17 listings for tienda.textokit.store [c01]. 5 sampled settlement(s) verified on Base against the receipts [c02]. The receipts list 1 purchase(s) by 0x097e3f339d0b023605e12a6b81e2d6cb7571475a [c03]. Those purchases total 1000 atomic USDC (0.001 USDC) [c04]. The purchases span 2026-10-20T04:51:24.352Z..2026-10-20T04:51:24.352Z [c05]. They cover 1 distinct service(s) [c06]. 1 settlement(s) of this payer verified on Base [c07]. USDC balance at capture: 1043148 atomic (1.043148 USDC) [c08]. Transaction count (nonce) at capture: 24 [c09]. Caveat [basis_mismatch]: Bazaar counters and receipts use different windows and bases [mem-seed-007]; applies to [c02] [c03] [c04] [c05] [c06] [c07]. Caveat [payer_from_transfer_log]: payer and seller were read from the Transfer log, not from tx.from [mem-seed-005]; applies to [c02] [c07] [c08] [c09]. Caveat [search_limit_20]: the Bazaar search is capped at twenty results, so its count is a floor [mem-seed-002]; applies to [c01]. Caveat [self_reported_receipts]: receipts are self-reported by the seller [mem-seed-003]; applies to [c02] [c03] [c04] [c05] [c06] [c07].

## Claims (accepted)

| id | metric | subject | value | unit | conf. | cross-checked | caveats | conflicts | evidence |
|---|---|---|---|---|---|---|---|---|---|
| c01 | bazaar_listings_for_host | seller:host:tienda.textokit.store | 17 | count | 0.9 | True | search_limit_20 (mem-seed-002) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> sha256 f0720c3770c5…<br>`log://nemotron-cloud-ex03-20261024#38`<br>`log://nemotron-cloud-ex03-20261024#40` |
| c02 | settlements_verified | seller:host:tienda.textokit.store | 5 | count | 0.9 | True | payer_from_transfer_log (mem-seed-005), self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 b8a420086c21…<br><https://mainnet.base.org> sha256 395c4282d446…<br><https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> [verified]<br><https://basescan.org/tx/0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d> [verified]<br><https://basescan.org/tx/0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934> [verified]<br><https://basescan.org/tx/0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3> [verified]<br><https://basescan.org/tx/0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2> [verified]<br>`log://nemotron-cloud-ex03-20261024#36`<br>`log://nemotron-cloud-ex03-20261024#41` |
| c03 | payer_sales_total | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 1 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 b8a420086c21…<br>`log://nemotron-cloud-ex03-20261024#37`<br>`log://nemotron-cloud-ex03-20261024#42` |
| c04 | payer_usdc_atomic | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 1000 | atomic_usdc | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 b8a420086c21…<br>`log://nemotron-cloud-ex03-20261024#37`<br>`log://nemotron-cloud-ex03-20261024#43` |
| c05 | payer_window | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 2026-10-20T04:51:24.352Z..2026-10-20T04:51:24.352Z | iso8601 | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 b8a420086c21…<br>`log://nemotron-cloud-ex03-20261024#37`<br>`log://nemotron-cloud-ex03-20261024#44` |
| c06 | payer_services | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 1 | count | 0.9 | True | self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 b8a420086c21…<br>`log://nemotron-cloud-ex03-20261024#37`<br>`log://nemotron-cloud-ex03-20261024#45` |
| c07 | verified_tx | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 1 | count | 0.9 | True | payer_from_transfer_log (mem-seed-005), self_reported_receipts (mem-seed-003), basis_mismatch (mem-seed-007) | - | <https://tienda.textokit.store/receipts.json> sha256 b8a420086c21…<br><https://mainnet.base.org> sha256 395c4282d446…<br><https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> [verified]<br>`log://nemotron-cloud-ex03-20261024#36`<br>`log://nemotron-cloud-ex03-20261024#46` |
| c08 | usdc_balance_atomic | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 1043148 | atomic_usdc | 0.9 | True | payer_from_transfer_log (mem-seed-005) | - | <https://mainnet.base.org> sha256 395c4282d446…<br><https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> [verified]<br>`log://nemotron-cloud-ex03-20261024#39`<br>`log://nemotron-cloud-ex03-20261024#47` |
| c09 | tx_count | wallet:base:0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 24 | count | 0.9 | True | payer_from_transfer_log (mem-seed-005) | - | <https://mainnet.base.org> sha256 395c4282d446…<br><https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> [verified]<br>`log://nemotron-cloud-ex03-20261024#39`<br>`log://nemotron-cloud-ex03-20261024#48` |

### Derivations and critique trail

- **c01** The CDP Bazaar search returns 17 listings for tienda.textokit.store. — query `q06_bazaar_host_listings` on `sqlite-fallback` (sql sha256 `db6f09d947db`), reducer `reducers.bazaar_listings_for_host`, 1 row(s), sources cdp_search, fields resource, payTo; coverage 17 of None (search capped at 20); log `log://nemotron-cloud-ex03-20261024#38`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c02** 5 sampled settlement(s) verified on Base against the receipts. — query `q16_settlements_vs_receipts` on `sqlite-fallback` (sql sha256 `82a4f8cbe2ab`), reducer `reducers.settlements_verified`, 5 row(s), sources receipts, base_rpc, fields transaction, payer, amountAtomic; log `log://nemotron-cloud-ex03-20261024#36`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c03** The receipts list 1 purchase(s) by 0x097e3f339d0b023605e12a6b81e2d6cb7571475a. — query `q17_payer_dossier` on `sqlite-fallback` (sql sha256 `0bb4552dd849`), reducer `reducers.payer_sales_total`, 1 row(s), sources receipts, fields payer; log `log://nemotron-cloud-ex03-20261024#37`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c04** Those purchases total 1000 atomic USDC (0.001 USDC). — query `q17_payer_dossier` on `sqlite-fallback` (sql sha256 `0bb4552dd849`), reducer `reducers.payer_usdc_atomic`, 1 row(s), sources receipts, fields payer, amountAtomic; log `log://nemotron-cloud-ex03-20261024#37`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c05** The purchases span 2026-10-20T04:51:24.352Z..2026-10-20T04:51:24.352Z. — query `q17_payer_dossier` on `sqlite-fallback` (sql sha256 `0bb4552dd849`), reducer `reducers.payer_window`, 1 row(s), sources receipts, fields payer, at; log `log://nemotron-cloud-ex03-20261024#37`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c06** They cover 1 distinct service(s). — query `q17_payer_dossier` on `sqlite-fallback` (sql sha256 `0bb4552dd849`), reducer `reducers.payer_services`, 1 row(s), sources receipts, fields payer, service; log `log://nemotron-cloud-ex03-20261024#37`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c07** 1 settlement(s) of this payer verified on Base. — query `q16_settlements_vs_receipts` on `sqlite-fallback` (sql sha256 `82a4f8cbe2ab`), reducer `reducers.verified_tx`, 5 row(s), sources receipts, base_rpc, fields transaction; log `log://nemotron-cloud-ex03-20261024#36`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c08** USDC balance at capture: 1043148 atomic (1.043148 USDC). — query `q22_payer_chain_facts` on `sqlite-fallback` (sql sha256 `6f24ed6d9dc3`), reducer `reducers.usdc_balance_atomic`, 2 row(s), sources base_rpc, fields balanceOf; log `log://nemotron-cloud-ex03-20261024#39`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c09** Transaction count (nonce) at capture: 24. — query `q22_payer_chain_facts` on `sqlite-fallback` (sql sha256 `6f24ed6d9dc3`), reducer `reducers.tx_count`, 2 row(s), sources base_rpc, fields eth_getTransactionCount; log `log://nemotron-cloud-ex03-20261024#39`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed

## Rejected claims

None.

## Where sources disagree and why

None recorded.

## Unavailable

None.

## Verifications (Base)

| tx | status | payer | seller | amount | block | facilitator_from | receipt match | reason |
|---|---|---|---|---|---|---|---|---|
| <https://basescan.org/tx/0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3> | verified | 0x097e3f339d0b023605e12a6b81e2d6cb7571475a | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52841269 | 0x8f5cb67b49555e614892b7233cfddebfb746e531 | True | - |
| <https://basescan.org/tx/0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d> | verified | 0xec2abd3eda89bed90124736e317e847d5fb6d034 | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 5000 | 53030579 | 0x625d8a65134079f8faaac39a7947c73d93c6ac39 | True | - |
| <https://basescan.org/tx/0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934> | verified | 0x7722dfae6bbed2fd02dc07455c2f5bf14be25e4a | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 53002133 | 0x14fda13953fc30428938e6bf950d036e77214e52 | True | - |
| <https://basescan.org/tx/0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3> | verified | 0xa1d1c0cf717402ff3651aa4873ccf96410b01978 | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52914075 | 0x4c934c63c786157fefd990945b25ea60a0fb0205 | True | - |
| <https://basescan.org/tx/0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2> | verified | 0xc9c7b38c0942914fc8ea12063bc92dcd3b581670 | 0x6b5af9924f4cd35643c11a9fd1c983f77261a6e1 | 1000 | 52860464 | 0x93f6601151ccb08f333ab4b1cccfb1e188c0be44 | True | - |

## Sources

| name | family | status | http | replayed | stale | sha256 | url |
|---|---|---|---|---|---|---|---|
| store_receipts | receipts | ok | 200 | False | False | b8a420086c21 | <https://tienda.textokit.store/receipts.json> |
| cdp_search_host | cdp_search | ok | 200 | False | False | f0720c3770c5 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=tienda.textokit.store&limit=20> |
| rpc_chain_id | base_rpc | ok | 200 | False | False | 395c4282d446 | <https://mainnet.base.org> |
| rpc_receipt_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3 | base_rpc | ok | 200 | False | False | f7d915c99074 | <https://mainnet.base.org> |
| rpc_tx_0x62c01526c5beb13cad96634ec80e837e576df496905f1bac8019c6ffa3e358c3 | base_rpc | ok | 200 | False | False | 2520d7e76b83 | <https://mainnet.base.org> |
| rpc_receipt_0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d | base_rpc | ok | 200 | False | False | d36a4f103efc | <https://mainnet.base.org> |
| rpc_tx_0xf93149b946d87295924977c2d46f76f163b4d93354bc3fa7aa1f5c04e392736d | base_rpc | ok | 200 | False | False | cc5bc01ffca3 | <https://mainnet.base.org> |
| rpc_receipt_0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934 | base_rpc | ok | 200 | False | False | 1d2b99fbb3a4 | <https://mainnet.base.org> |
| rpc_tx_0x8d2ccd6a3e04a769ecc680f298999dd082a0a2eba936f00a96c0e4ec5902b934 | base_rpc | ok | 200 | False | False | 6f3e6075106d | <https://mainnet.base.org> |
| rpc_receipt_0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3 | base_rpc | ok | 200 | False | False | cde0cae88882 | <https://mainnet.base.org> |
| rpc_tx_0x58ddb2e77e574ddda75fd74df6f3dbf94e2795e22c36f8a01a506c544ff4ada3 | base_rpc | ok | 200 | False | False | 11848e6ca24f | <https://mainnet.base.org> |
| rpc_receipt_0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2 | base_rpc | ok | 200 | False | False | e8f130b3d3fd | <https://mainnet.base.org> |
| rpc_tx_0x3ca4f2791f4f851a574f4abd7cb949e1b05fb00dadeb77b83fb3d0e36a608fb2 | base_rpc | ok | 200 | False | False | c249333b8335 | <https://mainnet.base.org> |
| rpc_balance_0x097e3f339d0b023605e12a6b81e2d6cb7571475a | base_rpc | ok | 200 | False | False | ba169e6f3e2e | <https://mainnet.base.org> |
| rpc_nonce_0x097e3f339d0b023605e12a6b81e2d6cb7571475a | base_rpc | ok | 200 | False | False | 95ec6500f934 | <https://mainnet.base.org> |

## World Model (Meterless)

first pass: {'runId': 'run_af5f68fd9bd3', 'processed': 2, 'skippedUnchanged': 0, 'eventsAppended': 378, 'changed': True}  
second pass: {'runId': 'run_af5f68fd9bd3', 'processed': 0, 'skippedUnchanged': 2, 'eventsAppended': 0, 'changed': False}  
claims asserted: 9, quarantined: 0

## Memory (Meterless H-MEM)

retrieved: 35, cited: mem-seed-002, mem-seed-003, mem-seed-005, mem-seed-007, feedback entries: 30, mined: 9

- mem-129: helpful (remembered value 5.0 matches c02)
- mem-138: helpful (remembered value 5.0 matches c02)
- mem-147: helpful (remembered value 5.0 matches c02)
- mem-179: not_helpful (retrieved for c05 but not cited)
- mem-197: not_helpful (retrieved for c05 but not cited)
- mem-28: helpful (remembered value 17.0 matches c01)
- mem-29: helpful (remembered value 1.0 matches c03)
- mem-30: helpful (remembered value 1000.0 matches c04)
- mem-32: helpful (remembered value 1.0 matches c06)
- mem-43: helpful (remembered value 17.0 matches c01)
- mem-45: helpful (remembered value 1.0 matches c03)
- mem-46: helpful (remembered value 1000.0 matches c04)
- mem-48: helpful (remembered value 1.0 matches c06)
- mem-49: helpful (remembered value 1.0 matches c07)
- mem-50: helpful (remembered value 1043148.0 matches c08)
- mem-51: helpful (remembered value 24.0 matches c09)
- mem-52: helpful (remembered value 17.0 matches c01)
- mem-54: helpful (remembered value 1.0 matches c03)
- mem-55: helpful (remembered value 1000.0 matches c04)
- mem-57: helpful (remembered value 1.0 matches c06)
- mem-58: helpful (remembered value 1.0 matches c07)
- mem-59: helpful (remembered value 1043148.0 matches c08)
- mem-60: helpful (remembered value 24.0 matches c09)
- mem-77: helpful (remembered value 1.0 matches c07)
- mem-78: helpful (remembered value 1043148.0 matches c08)
- mem-79: helpful (remembered value 24.0 matches c09)
- mem-seed-002: helpful (caveat search_limit_20 required and attached to c01)
- mem-seed-003: helpful (caveat self_reported_receipts required and attached to c02)
- mem-seed-005: helpful (caveat payer_from_transfer_log required and attached to c02)
- mem-seed-007: helpful (caveat basis_mismatch required and attached to c02)

## Agents

| role | generator-backed | generator used | attempts | status | notes |
|---|---|---|---|---|---|
| planner | True | nightshift:nvidia/nemotron-3.5-lightning-30b-a3b | 1 | ok | - |
| collector | False | None | 1 | ok | 2 ok, 0 stale fixture fallback(s), 0 fixture miss(es), 0 unavailable, 1 deferred to the verifier; deferred: base_rpc_batch |
| keeper | False | None | 3 | ok | chain facts asserted: 7; claims asserted as agent facts: 9; ingested 2 observations, 378 events appended; second pass: {'runId': 'run_af5f68fd9bd3', 'processed': 0, 'skippedUnchanged': 2, 'eventsAppended': 0, 'changed': False} |
| verifier | False | None | 1 | ok | 5 tx sampled (sales of focus.wallet first, then most recent external sale per distinct payer, capped at max_tx_verifications): 5 verified, 0 failed, 0 unverifiable (none); chain facts: 7; cross-checks: 2; settlements export: yes |
| analyst | False | None | 1 | ok | views without export: bazaar_resources, catalog_resources, openapi_paid_ops, agent402_tools, agent402_seller, vet402_endpoints, trust_search, trust_stats |
| writer | True | nightshift:nvidia/nemotron-3.5-lightning-30b-a3b | 2 | ok | - |
| critic | True | nightshift:nvidia/nemotron-3.5-lightning-30b-a3b | 2 | ok | - |

## Model calls

Which model handled each generator request (`generate` events in run.jsonl; Nightshift receipts).

| role | prompt | provider | requested | model that answered | route | receipt | recorded | log |
|---|---|---|---|---|---|---|---|---|
| planner | planner | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_cx7dp79o | False | log://nemotron-cloud-ex03-20261024#6 |
| writer | writer-r1 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_jixi2h9b | False | log://nemotron-cloud-ex03-20261024#52 |
| critic | critic-r1 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_macv9ww0 | False | log://nemotron-cloud-ex03-20261024#81 |
| writer | writer-r2 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_f3glghah | False | log://nemotron-cloud-ex03-20261024#86 |
| critic | critic-r2 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_1cz4wbwv | False | log://nemotron-cloud-ex03-20261024#106 |

Critique rounds: 2. Log files: fetch.jsonl, generator.jsonl, hmem.trace.jsonl, run.jsonl, sql.jsonl, world.audit.jsonl, world.events.jsonl, world.observations.jsonl, zetaris.jsonl.
