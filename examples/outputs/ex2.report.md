# x402 Adoption Investigator report: `ex02-catalog-concentration`

**Status:** ok  
**Mode:** sample (fetcher fixture, executor sqlite, generator null, fixtures captured 2026-10-22T00:05:00Z)

> This run replayed committed fixtures captured 2026-10-22T00:05:00Z; no network, no model calls.

## Question

Who sells on the CDP Bazaar and how concentrated are the paid calls? What share of listed endpoints saw any call in the last 30 days?

## Plan

Playbook `catalog_concentration` (planner source: rules).  
Sources: cdp_catalog_snapshot, cdp_bazaar, x402_trust.  
Queries: q08_catalog_size, q09_catalog_top_hosts_by_calls, q10_catalog_active_share, q11_catalog_payto_concentration, q19_trust_ecosystem_stats.  
Memories retrieved by the Planner: mem-seed-005.

## Verdict

**concentrated** (confidence 0.9; why: c07)

## Answer

Paid calls are concentrated in a few hosts (verdict concentrated) based on [c07]. The Bazaar catalog snapshot holds 28462 unique resources [c01]. Those resources are served by 2680 distinct hosts [c02]. 1785 distinct payTo wallets receive the payments [c03]. The 30-day call counters sum to 824796 across the snapshot [c04]. 28347 of 28462 resources saw at least one call in the 30-day window (99.6 %) [c05]. The top host www.ax1.vc accounts for 243580 of 824796 30-day calls (29.5 %) [c06]. The top ten hosts account for 604248 of 824796 30-day calls (73.3 %) [c07]. Hosts with the most listings in the snapshot: saylorinnovations.com=1568, agentshelf.syntexa.ch=1236, dns.intel.rallylive.ca=694, agent402.tools=613 [c08]. The top payTo wallet 0x7284d41b5b852f2bd4c99bdf95043d84452d299c receives 243601 of 824796 30-day calls (29.5 %) [c09]. The largest per-endpoint 30-day unique-payer counter is 2708 (not deduplicable across endpoints) [c10]. x402-trust reports endpoints_listed=286358, endpoints_active=210375, settlements_30d=2661042, distinct_payers_30d=32625 [c11]. Where sources disagree [k01] on ecosystem_size: cdp_catalog_snapshot: 28462 (unique resources in the compact snapshot (deduplicated by resource)); cdp_bazaar: 28472 (pagination.total reported live by the discovery API at capture (moves by the minute)); x402_trust: endpointsListed=286358, endpointsActive=210375 (x402-trust index across its own sources, 30-day probe window, delisted endpoints counted in listed). The Bazaar snapshot is a dated capture of one index deduplicated by resource; the API total moves by the minute; x402-trust indexes a different and larger set with its own 30-day probe window and counts delisted endpoints [c01] [c11]. Caveat [snapshot_dated]: the catalog snapshot is a dated capture and the live total moves by the minute [mem-seed-006]; applies to [c01] [c02] [c03] [c04] [c05] [c06] [c07] [c08] [c09] [c10]. Caveat [third_party_aggregate]: x402-trust figures are third-party aggregates with their own window and index [mem-seed-004]; applies to [c11]. Caveat [unique_payers_not_dedupable]: per-endpoint unique-payer counters cannot be deduplicated across endpoints [mem-seed-001]; applies to [c10]. Sample mode: this answer replays committed fixtures captured 2026-10-22T00:05:00Z; no network, no model calls.

## Claims (accepted)

| id | metric | subject | value | unit | conf. | cross-checked | caveats | conflicts | evidence |
|---|---|---|---|---|---|---|---|---|---|
| c01 | catalog_resources | catalog:cdp-bazaar | 28462 | count | 0.9 | True | snapshot_dated (mem-seed-006) | k01 | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#18`<br>`log://ex02-catalog-concentration#23` |
| c02 | catalog_hosts | catalog:cdp-bazaar | 2680 | count | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#18`<br>`log://ex02-catalog-concentration#24` |
| c03 | catalog_distinct_payto | catalog:cdp-bazaar | 1785 | count | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#18`<br>`log://ex02-catalog-concentration#25` |
| c04 | catalog_calls_30d_sum | catalog:cdp-bazaar | 824796 | count | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#18`<br>`log://ex02-catalog-concentration#26` |
| c05 | catalog_active_share | catalog:cdp-bazaar | 28347 / 28462 = 0.995959524980676 | share | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#20`<br>`log://ex02-catalog-concentration#27` |
| c06 | top_host_calls_share | catalog:cdp-bazaar | 243580 / 824796 = 0.2953215098036363 (host=www.ax1.vc) | share | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#19`<br>`log://ex02-catalog-concentration#28` |
| c07 | top10_hosts_calls_share | catalog:cdp-bazaar | 604248 / 824796 = 0.7326029709164448 | share | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#19`<br>`log://ex02-catalog-concentration#29` |
| c08 | top_hosts_by_listings | catalog:cdp-bazaar | saylorinnovations.com=1568, agentshelf.syntexa.ch=1236, dns.intel.rallylive.ca=694, agent402.tools=613 | text | 0.7 | False | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#30` |
| c09 | top_payto_calls_share | catalog:cdp-bazaar | 243601 / 824796 = 0.2953469706448625 (pay_to=0x7284d41b5b852f2bd4c99bdf95043d84452d299c) | share | 0.9 | True | snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#21`<br>`log://ex02-catalog-concentration#31` |
| c10 | unique_payers_per_endpoint_max | catalog:cdp-bazaar | 2708 | count | 0.7 | False | unique_payers_not_dedupable (mem-seed-001), snapshot_dated (mem-seed-006) | - | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N> sha256 e1b0040a2265… [replayed]<br>`log://ex02-catalog-concentration#32` |
| c11 | trust_ecosystem_stats | registry:name:x402-trust | endpoints_listed=286358, endpoints_active=210375, settlements_30d=2661042, distinct_payers_30d=32625 | text | 0.9 | True | third_party_aggregate (mem-seed-004) | k01 | <https://x402-trust.com/v1/facilitators> sha256 b4f1d54d5feb… [replayed]<br><https://x402-trust.com/trust/leaderboard?limit=25&offset=0> sha256 2d9d40a8e9e6… [replayed]<br><https://x402-trust.com/trust/stats> sha256 26ee64acb78a… [replayed]<br>`log://ex02-catalog-concentration#22`<br>`log://ex02-catalog-concentration#33` |

### Derivations and critique trail

- **c01** The Bazaar catalog snapshot holds 28462 unique resources. — query `q08_catalog_size` on `sqlite` (sql sha256 `569da2298280`), reducer `reducers.catalog_resources`, 1 row(s), sources cdp_catalog_snapshot, fields resource; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#18`
  - critique: r1 A accept: C1-C7 passed
- **c02** Those resources are served by 2680 distinct hosts. — query `q08_catalog_size` on `sqlite` (sql sha256 `569da2298280`), reducer `reducers.catalog_hosts`, 1 row(s), sources cdp_catalog_snapshot, fields resource; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#18`
  - critique: r1 A accept: C1-C7 passed
- **c03** 1785 distinct payTo wallets receive the payments. — query `q08_catalog_size` on `sqlite` (sql sha256 `569da2298280`), reducer `reducers.catalog_distinct_payto`, 1 row(s), sources cdp_catalog_snapshot, fields payTo; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#18`
  - critique: r1 A accept: C1-C7 passed
- **c04** The 30-day call counters sum to 824796 across the snapshot. — query `q08_catalog_size` on `sqlite` (sql sha256 `569da2298280`), reducer `reducers.catalog_calls_30d_sum`, 1 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#18`
  - critique: r1 A accept: C1-C7 passed
- **c05** 28347 of 28462 resources saw at least one call in the 30-day window (99.6 %). — query `q10_catalog_active_share` on `sqlite` (sql sha256 `f2dc2b1af77f`), reducer `reducers.catalog_active_share`, 1 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#20`
  - critique: r1 A accept: C1-C7 passed
- **c06** The top host www.ax1.vc accounts for 243580 of 824796 30-day calls (29.5 %). — query `q09_catalog_top_hosts_by_calls` on `sqlite` (sql sha256 `cd7605046bb6`), reducer `reducers.top_host_calls_share`, 10 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#19`
  - critique: r1 A accept: C1-C7 passed
- **c07** The top ten hosts account for 604248 of 824796 30-day calls (73.3 %). — query `q09_catalog_top_hosts_by_calls` on `sqlite` (sql sha256 `cd7605046bb6`), reducer `reducers.top10_hosts_calls_share`, 10 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#19`
  - critique: r1 A accept: C1-C7 passed
- **c08** Hosts with the most listings in the snapshot: saylorinnovations.com=1568, agentshelf.syntexa.ch=1236, dns.intel.rallylive.ca=694, agent402.tools=613. — query `None` on `None` (sql sha256 `-`), reducer `reducers.top_hosts_by_listings`, 0 row(s), sources cdp_catalog_snapshot, fields resource; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#30`
  - critique: r1 A accept: C1-C7 passed
- **c09** The top payTo wallet 0x7284d41b5b852f2bd4c99bdf95043d84452d299c receives 243601 of 824796 30-day calls (29.5 %). — query `q11_catalog_payto_concentration` on `sqlite` (sql sha256 `2307816ca37c`), reducer `reducers.top_payto_calls_share`, 10 row(s), sources cdp_catalog_snapshot, fields l30DaysTotalCalls, payTo; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#21`
  - critique: r1 A accept: C1-C7 passed
- **c10** The largest per-endpoint 30-day unique-payer counter is 2708 (not deduplicable across endpoints). — query `None` on `None` (sql sha256 `-`), reducer `reducers.unique_payers_per_endpoint_max`, 0 row(s), sources cdp_catalog_snapshot, fields l30DaysUniquePayers; coverage 28462 of 28472 (compact snapshot 2026-10-22); log `log://ex02-catalog-concentration#32`
  - critique: r1 A accept: C1-C7 passed
- **c11** x402-trust reports endpoints_listed=286358, endpoints_active=210375, settlements_30d=2661042, distinct_payers_30d=32625. — query `q19_trust_ecosystem_stats` on `sqlite` (sql sha256 `4035f80e1f99`), reducer `reducers.trust_ecosystem_stats`, 1 row(s), sources x402_trust, fields endpointsListed, endpointsActive, settlements30d; log `log://ex02-catalog-concentration#22`
  - critique: r1 A accept: C1-C7 passed

## Rejected claims

None.

## Where sources disagree and why

### k01: ecosystem_size (catalog:cdp-bazaar) — explained
- cdp_catalog_snapshot: 28462 (unique resources in the compact snapshot (deduplicated by resource)) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N>
- cdp_bazaar: 28472 (pagination.total reported live by the discovery API at capture (moves by the minute)) <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=0>
- x402_trust: endpointsListed=286358, endpointsActive=210375 (x402-trust index across its own sources, 30-day probe window, delisted endpoints counted in listed) <https://x402-trust.com/trust/stats>

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
| cdp_page_0 | cdp_bazaar | ok | 200 | True | False | 828c301c7a7b | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=0> |
| cdp_page_1 | cdp_bazaar | ok | 200 | True | False | 5566627d5c9f | <https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=100&offset=100> |
| trust_facilitators | x402_trust | ok | 200 | True | False | b4f1d54d5feb | <https://x402-trust.com/v1/facilitators> |
| trust_leaderboard | x402_trust | ok | 200 | True | False | 2d9d40a8e9e6 | <https://x402-trust.com/trust/leaderboard?limit=25&offset=0> |
| trust_stats | x402_trust | ok | 200 | True | False | 26ee64acb78a | <https://x402-trust.com/trust/stats> |

## World Model (Meterless)

first pass: {'runId': 'run_7ce74f98914f', 'processed': 6, 'skippedUnchanged': 0, 'eventsAppended': 21105, 'changed': True}  
second pass: {'runId': 'run_7ce74f98914f', 'processed': 0, 'skippedUnchanged': 6, 'eventsAppended': 0, 'changed': False}  
claims asserted: 11, quarantined: 0

## Memory (Meterless H-MEM)

retrieved: 7, cited: mem-seed-001, mem-seed-004, mem-seed-006, feedback entries: 4, mined: 10

- mem-seed-001: helpful (caveat unique_payers_not_dedupable required and attached to c10)
- mem-seed-004: helpful (caveat third_party_aggregate required and attached to c11)
- mem-seed-005: not_helpful (retrieved for planner but not cited)
- mem-seed-006: helpful (caveat snapshot_dated required and attached to c01)

## Agents

| role | generator-backed | generator used | attempts | status | notes |
|---|---|---|---|---|---|
| planner | True | none | 1 | ok | - |
| collector | False | None | 1 | ok | 6 ok, 0 stale fixture fallback(s), 0 fixture miss(es), 0 unavailable, 0 deferred to the verifier |
| keeper | False | None | 3 | ok | chain facts asserted: 0; claims asserted as agent facts: 11; ingested 6 observations, 21105 events appended; second pass: {'runId': 'run_7ce74f98914f', 'processed': 0, 'skippedUnchanged': 6, 'eventsAppended': 0, 'changed': False} |
| verifier | False | None | 1 | ok | no receipts observation in this plan: no settlement to sample, no RPC call made |
| analyst | False | None | 1 | ok | views without export: sales, purchases, bazaar_search, openapi_paid_ops, agent402_tools, agent402_seller, vet402_endpoints, trust_search, settlements |
| writer | True | none | 1 | ok | - |
| critic | True | none | 1 | ok | - |

Critique rounds: 1. Log files: fetch.jsonl, hmem.trace.jsonl, run.jsonl, sql.jsonl, world.audit.jsonl, world.events.jsonl, world.observations.jsonl.
