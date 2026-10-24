# x402 Adoption Investigator report: `zetaris-nightshift-ex02-20261024`

**Status:** ok  
**Mode:** live (fetcher live+fixture-fallback, executor zetaris, generator nightshift:nvidia/nemotron-3.5-lightning-30b-a3b, fixtures captured 2026-10-22T00:05:00Z)

## Question

Who sells on the CDP Bazaar and how concentrated are the paid calls? What share of listed endpoints saw any call in the last 30 days?

## Plan

Playbook `catalog_concentration` (planner source: generator).  
Sources: cdp_catalog_snapshot, cdp_bazaar, x402_trust.  
Queries: q08_catalog_size, q09_catalog_top_hosts_by_calls, q10_catalog_active_share, q11_catalog_payto_concentration, q19_trust_ecosystem_stats, q20_catalog_unique_payers_max, q21_catalog_top_hosts_by_listings.  
Memories retrieved by the Planner: mem-seed-005, mem-28, mem-43, mem-52, mem-20.

Hypotheses (written by the Planner, source generator; untested: no rule checks them against the claims below, and they are not findings):
- A small number of hosts generate the majority of paid calls on the CDP Bazaar.
- The share of listed endpoints with zero activity in the last 30 days is greater than 50%.
- Top hosts by call volume have disproportionately more listings than average hosts.
- A single wallet or facilitator controls a significant fraction of all payTo addresses.
- The concentration of calls across hosts follows a power-law distribution.

## Verdict

**concentrated** (confidence 0.9; why: c07)

## Answer

Paid calls are concentrated in a few hosts (verdict concentrated) based on [c07]. 28462 unique resources form the CDP Bazaar catalog snapshot [c01] 2680 distinct hosts serve those resources [c02] 1785 distinct payTo wallets receive payments [c03] 824796 total 30-day calls across the snapshot [c04] 99.6% of listed endpoints saw at least one call in the last 30 days [c05] In the 30-day window, 99.6% of listed endpoints received at least one call. Paid-call concentration is extreme: one host (www.ax1.vc) and one payTo wallet each generate roughly 29.5% of all 30-day calls, and the top ten hosts together account for 73.3% of calls. The top host www.ax1.vc accounts for 243580 of 824796 30-day calls (29.5 %) [c06]. The top ten hosts account for 604248 of 824796 30-day calls (73.3 %) [c07]. Hosts with the most listings in the snapshot: saylorinnovations.com=1568, agentshelf.syntexa.ch=1236, dns.intel.rallylive.ca=694, agent402.tools=613 [c08]. The top payTo wallet 0x7284d41b5b852f2bd4c99bdf95043d84452d299c receives 243601 of 824796 30-day calls (29.5 %) [c09]. The largest per-endpoint 30-day unique-payer counter is 2708 (not deduplicable across endpoints) [c10]. x402-trust reports endpoints_listed=301727, endpoints_active=227830, settlements_30d=2559694, distinct_payers_30d=31592 [c11]. Where sources disagree [k01] on ecosystem_size: cdp_catalog_snapshot: 28462 (unique resources in the compact snapshot (deduplicated by resource)); cdp_bazaar: 28472 (pagination.total reported live by the discovery API at capture (moves by the minute)); x402_trust: endpointsListed=301727, endpointsActive=227830 (x402-trust index across its own sources, 30-day probe window, delisted endpoints counted in listed). The Bazaar snapshot is a dated capture of one index deduplicated by resource; the API total moves by the minute; x402-trust indexes a different and larger set with its own 30-day probe window and counts delisted endpoints [c01] [c11]. Caveat [basis_mismatch]: Bazaar counters and receipts use different windows and bases [mem-seed-007]; applies to [c04] [c05] [c06] [c07] [c09]. Caveat [snapshot_dated]: the catalog snapshot is a dated capture and the live total moves by the minute [mem-seed-006]; applies to [c01] [c02] [c03] [c04] [c05] [c06] [c07] [c08] [c09] [c10]. Caveat [third_party_aggregate]: x402-trust figures are third-party aggregates with their own window and index [mem-seed-004]; applies to [c11]. Caveat [unique_payers_not_dedupable]: per-endpoint unique-payer counters cannot be deduplicated across endpoints [mem-seed-001]; applies to [c10].

## Claims (accepted)

| id | metric | subject | value | unit | conf. | cross-checked | caveats | conflicts | evidence |
|---|---|---|---|---|---|---|---|---|---|
| c01 | catalog_resources | catalog:cdp-bazaar | 28462 | count | 0.9 | True | snapshot_dated (mem-seed-006) | k01 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#18`<br>`log://zetaris-nightshift-ex02-20261024#25` |
| c02 | catalog_hosts | catalog:cdp-bazaar | 2680 | count | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#18`<br>`log://zetaris-nightshift-ex02-20261024#26` |
| c03 | catalog_distinct_payto | catalog:cdp-bazaar | 1785 | count | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#18`<br>`log://zetaris-nightshift-ex02-20261024#27` |
| c04 | catalog_calls_30d_sum | catalog:cdp-bazaar | 824796 | count | 0.9 | True | snapshot_dated (mem-seed-006), basis_mismatch (mem-seed-007) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#18`<br>`log://zetaris-nightshift-ex02-20261024#28` |
| c05 | catalog_active_share | catalog:cdp-bazaar | 28347 / 28462 = 0.995959524980676 | share | 0.9 | True | snapshot_dated (mem-seed-006), basis_mismatch (mem-seed-007) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#20`<br>`log://zetaris-nightshift-ex02-20261024#29` |
| c06 | top_host_calls_share | catalog:cdp-bazaar | 243580 / 824796 = 0.2953215098036363 (host=www.ax1.vc) | share | 0.9 | True | snapshot_dated (mem-seed-006), basis_mismatch (mem-seed-007) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#19`<br>`log://zetaris-nightshift-ex02-20261024#30` |
| c07 | top10_hosts_calls_share | catalog:cdp-bazaar | 604248 / 824796 = 0.7326029709164448 | share | 0.9 | True | snapshot_dated (mem-seed-006), basis_mismatch (mem-seed-007) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#19`<br>`log://zetaris-nightshift-ex02-20261024#31` |
| c08 | top_hosts_by_listings | catalog:cdp-bazaar | saylorinnovations.com=1568, agentshelf.syntexa.ch=1236, dns.intel.rallylive.ca=694, agent402.tools=613 | text | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#24`<br>`log://zetaris-nightshift-ex02-20261024#32` |
| c09 | top_payto_calls_share | catalog:cdp-bazaar | 243601 / 824796 = 0.2953469706448625 (pay_to=0x7284d41b5b852f2bd4c99bdf95043d84452d299c) | share | 0.9 | True | snapshot_dated (mem-seed-006), basis_mismatch (mem-seed-007) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#21`<br>`log://zetaris-nightshift-ex02-20261024#33` |
| c10 | unique_payers_per_endpoint_max | catalog:cdp-bazaar | 2708 | count | 0.9 | True | unique_payers_not_dedupable (mem-seed-001), snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://zetaris-nightshift-ex02-20261024#23`<br>`log://zetaris-nightshift-ex02-20261024#34` |
| c11 | trust_ecosystem_stats | registry:name:x402-trust | endpoints_listed=301727, endpoints_active=227830, settlements_30d=2559694, distinct_payers_30d=31592 | text | 0.9 | True | third_party_aggregate (mem-seed-004) | k01 | <https://x402-trust.com/v1/facilitators> sha256 19d33f49d024…<br><https://x402-trust.com/trust/leaderboard?limit=25&offset=0> sha256 9d988c8cfcd2…<br><https://x402-trust.com/trust/stats> sha256 35e7b56a15d7…<br>`log://zetaris-nightshift-ex02-20261024#22`<br>`log://zetaris-nightshift-ex02-20261024#35` |

### Derivations and critique trail

- **c01** The Bazaar catalog snapshot holds 28462 unique resources. — query `q08_catalog_size` on `zetaris` (sql sha256 `569da2298280`), reducer `reducers.catalog_resources`, 1 row(s), sources cdp_catalog_snapshot, fields resource; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#18`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c02** Those resources are served by 2680 distinct hosts. — query `q08_catalog_size` on `zetaris` (sql sha256 `569da2298280`), reducer `reducers.catalog_hosts`, 1 row(s), sources cdp_catalog_snapshot, fields resource; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#18`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c03** 1785 distinct payTo wallets receive the payments. — query `q08_catalog_size` on `zetaris` (sql sha256 `569da2298280`), reducer `reducers.catalog_distinct_payto`, 1 row(s), sources cdp_catalog_snapshot, fields payTo; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#18`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c04** The 30-day call counters sum to 824796 across the snapshot. — query `q08_catalog_size` on `zetaris` (sql sha256 `569da2298280`), reducer `reducers.catalog_calls_30d_sum`, 1 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#18`
  - critique: r1 C6 accept: memory mem-215 contradicted by c04; marked wrong [mem-215]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c05** 28347 of 28462 resources saw at least one call in the 30-day window (99.6 %). — query `q10_catalog_active_share` on `zetaris` (sql sha256 `f2dc2b1af77f`), reducer `reducers.catalog_active_share`, 1 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#20`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c06** The top host www.ax1.vc accounts for 243580 of 824796 30-day calls (29.5 %). — query `q09_catalog_top_hosts_by_calls` on `zetaris` (sql sha256 `cd7605046bb6`), reducer `reducers.top_host_calls_share`, 10 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#19`
  - critique: r1 C6 accept: memory mem-217 contradicted by c06; marked wrong [mem-217]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c07** The top ten hosts account for 604248 of 824796 30-day calls (73.3 %). — query `q09_catalog_top_hosts_by_calls` on `zetaris` (sql sha256 `cd7605046bb6`), reducer `reducers.top10_hosts_calls_share`, 10 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#19`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c08** Hosts with the most listings in the snapshot: saylorinnovations.com=1568, agentshelf.syntexa.ch=1236, dns.intel.rallylive.ca=694, agent402.tools=613. — query `q21_catalog_top_hosts_by_listings` on `zetaris` (sql sha256 `84906723659a`), reducer `reducers.top_hosts_by_listings`, 4 row(s), sources cdp_catalog_snapshot, fields resource; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#24`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c09** The top payTo wallet 0x7284d41b5b852f2bd4c99bdf95043d84452d299c receives 243601 of 824796 30-day calls (29.5 %). — query `q11_catalog_payto_concentration` on `zetaris` (sql sha256 `2307816ca37c`), reducer `reducers.top_payto_calls_share`, 10 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls, payTo; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#21`
  - critique: r1 C6 accept: memory mem-220 contradicted by c09; marked wrong [mem-220]; r1 C6 accept: memory mem-173 contradicted by c09; marked wrong [mem-173]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c10** The largest per-endpoint 30-day unique-payer counter is 2708 (not deduplicable across endpoints). — query `q20_catalog_unique_payers_max` on `zetaris` (sql sha256 `d37a5473afae`), reducer `reducers.unique_payers_per_endpoint_max`, 1 row(s), sources cdp_catalog_snapshot, fields l30DaysUniquePayers; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://zetaris-nightshift-ex02-20261024#23`
  - critique: r1 C6 accept: memory mem-221 contradicted by c10; marked wrong [mem-221]; r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed
- **c11** x402-trust reports endpoints_listed=301727, endpoints_active=227830, settlements_30d=2559694, distinct_payers_30d=31592. — query `q19_trust_ecosystem_stats` on `zetaris` (sql sha256 `4035f80e1f99`), reducer `reducers.trust_ecosystem_stats`, 1 row(s), sources x402_trust, fields endpointsListed, endpointsActive, settlements30d; log `log://zetaris-nightshift-ex02-20261024#22`
  - critique: r1 A accept: C1-C7 passed; r2 A accept: C1-C7 passed

## Rejected claims

None.

## Where sources disagree and why

### k01: ecosystem_size (catalog:cdp-bazaar) — explained
- cdp_catalog_snapshot: 28462 (unique resources in the compact snapshot (deduplicated by resource)) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N>
- cdp_bazaar: 28472 (pagination.total reported live by the discovery API at capture (moves by the minute)) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=0>
- x402_trust: endpointsListed=301727, endpointsActive=227830 (x402-trust index across its own sources, 30-day probe window, delisted endpoints counted in listed) <https://x402-trust.com/trust/stats>

The Bazaar snapshot is a dated capture of one index deduplicated by resource; the API total moves by the minute; x402-trust indexes a different and larger set with its own 30-day probe window and counts delisted endpoints.
Claims: c01, c11

## Unavailable

None.

## Verifications (Base)

None.

## Sources

| name | family | status | http | replayed | stale | sha256 | url |
|---|---|---|---|---|---|---|---|
| cdp_catalog_snapshot | cdp_catalog_snapshot | ok | 200 | True | False | e1b0040a2265 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> |
| cdp_page_0 | cdp_bazaar | ok | 200 | False | False | 8b80ad387fac | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=0> |
| trust_facilitators | x402_trust | ok | 200 | False | False | 19d33f49d024 | <https://x402-trust.com/v1/facilitators> |
| trust_leaderboard | x402_trust | ok | 200 | False | False | 9d988c8cfcd2 | <https://x402-trust.com/trust/leaderboard?limit=25&offset=0> |
| trust_stats | x402_trust | ok | 200 | False | False | 35e7b56a15d7 | <https://x402-trust.com/trust/stats> |

## World Model (Meterless)

first pass: {'runId': 'run_dc7590f207d2', 'processed': 5, 'skippedUnchanged': 0, 'eventsAppended': 29767, 'changed': True}  
second pass: {'runId': 'run_dc7590f207d2', 'processed': 0, 'skippedUnchanged': 5, 'eventsAppended': 0, 'changed': False}  
claims asserted: 11, quarantined: 0

## Memory (Meterless H-MEM)

retrieved: 32, cited: mem-seed-001, mem-seed-004, mem-seed-006, mem-seed-007, feedback entries: 29, mined: 10

- mem-118: helpful (remembered value 28462.0 matches c01)
- mem-119: helpful (remembered value 2680.0 matches c02)
- mem-120: helpful (remembered value 1785.0 matches c03)
- mem-124: helpful (remembered value 604248.0 matches c07)
- mem-125: not_helpful (retrieved for c08 but not cited)
- mem-165: helpful (remembered value 28462.0 matches c01)
- mem-166: helpful (remembered value 2680.0 matches c02)
- mem-167: helpful (remembered value 1785.0 matches c03)
- mem-171: helpful (remembered value 604248.0 matches c07)
- mem-172: not_helpful (retrieved for c08 but not cited)
- mem-173: wrong (remembered value 0.0 differs by more than 5 % from c09 (243601.0))
- mem-18: helpful (remembered value 28462.0 matches c01)
- mem-19: helpful (remembered value 2680.0 matches c02)
- mem-20: helpful (remembered value 1785.0 matches c03)
- mem-215: wrong (remembered value 30.0 differs by more than 5 % from c04 (824796.0))
- mem-217: wrong (remembered value 1.0 differs by more than 5 % from c06 (243580.0))
- mem-219: not_helpful (retrieved for c08 but not cited)
- mem-220: wrong (remembered value 0.0 differs by more than 5 % from c09 (243601.0))
- mem-221: wrong (remembered value 30.0 differs by more than 5 % from c10 (2708.0))
- mem-24: helpful (remembered value 604248.0 matches c07)
- mem-28: not_helpful (retrieved for planner but not cited)
- mem-43: not_helpful (retrieved for planner but not cited)
- mem-52: not_helpful (retrieved for planner but not cited)
- mem-seed-001: helpful (caveat unique_payers_not_dedupable required and attached to c10)
- mem-seed-003: not_helpful (retrieved for writer but not cited)
- mem-seed-004: helpful (caveat third_party_aggregate required and attached to c11)
- mem-seed-005: not_helpful (retrieved for planner but not cited)
- mem-seed-006: helpful (caveat snapshot_dated required and attached to c01)
- mem-seed-007: helpful (caveat basis_mismatch required and attached to c04)

## Agents

| role | generator-backed | generator used | attempts | status | notes |
|---|---|---|---|---|---|
| planner | True | nightshift:nvidia/nemotron-3.5-lightning-30b-a3b | 1 | ok | - |
| collector | False | None | 1 | ok | 5 ok, 0 stale fixture fallback(s), 0 fixture miss(es), 0 unavailable, 0 deferred to the verifier |
| keeper | False | None | 3 | ok | chain facts asserted: 0; claims asserted as agent facts: 11; ingested 5 observations, 29767 events appended; second pass: {'runId': 'run_dc7590f207d2', 'processed': 0, 'skippedUnchanged': 5, 'eventsAppended': 0, 'changed': False} |
| verifier | False | None | 1 | ok | no receipts observation in this plan: no settlement to sample, no RPC call made |
| analyst | False | None | 1 | ok | - |
| writer | True | nightshift:nvidia/nemotron-3.5-lightning-30b-a3b | 2 | ok | - |
| critic | True | nightshift:nvidia/nemotron-3.5-lightning-30b-a3b | 2 | ok | - |

## Model calls

Which model handled each generator request (`generate` events in run.jsonl; Nightshift receipts).

| role | prompt | provider | requested | model that answered | route | receipt | recorded | log |
|---|---|---|---|---|---|---|---|---|
| planner | planner | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_rtq7j8ik | False | log://zetaris-nightshift-ex02-20261024#5 |
| writer | writer-r1 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_ujkssqd7 | False | log://zetaris-nightshift-ex02-20261024#40 |
| critic | critic-r1 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_xc3qhxuz | False | log://zetaris-nightshift-ex02-20261024#78 |
| writer | writer-r2 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_3hpmlkfk | False | log://zetaris-nightshift-ex02-20261024#90 |
| critic | critic-r2 | meterless | nvidia/nemotron-3.5-lightning-30b-a3b | nvidia/nemotron-3.5-lightning-30b-a3b | Nemotron 3.5 Lightning 30B A3B (cloud) | rcpt_req_b2fpw8cl | False | log://zetaris-nightshift-ex02-20261024#113 |

Critique rounds: 2. Log files: fetch.jsonl, generator.jsonl, hmem.trace.jsonl, run.jsonl, sql.jsonl, world.audit.jsonl, world.events.jsonl, world.observations.jsonl, zetaris.jsonl.
