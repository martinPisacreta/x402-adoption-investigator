# Meterless integration (H-MEM + World Model)

Owner: B2. How the two Meterless reference architectures are mapped into the investigator, what the conformance
suites check, what was dropped and why (SPEC.md sections 8 and 9). Both engines are Python 3.11 standard-library
ports of `engines/world-model/reference/src` and `engines/hmem/reference/src` (Apache-2.0 upstream; this
repository is MIT, see "Licensing").

## In short

| engine | what it does here | feature it powers | tests |
|---|---|---|---|
| World Model (`investigator/worldmodel/`) | the run's shared state: entities, contexts, bitemporal relationships and facts, each with provenance | a claim cannot exist without evidence (the claim policy inside `assert_fact`), and every output proves idempotent ingest | 8/8 upstream checks (`tests/test_worldmodel_conformance.py`), `tests/test_meterless_keeper.py`, `tests/test_adapters.py` |
| H-MEM (`investigator/hmem/`) | seven pitfall memories, retrieval with the spec formula, trust ledger, mining, sleep preview | the caveat gate (the Analyst attaches the caveats, the Critic's C3 checks them) and the `[MEMORY CONTEXT]` block in the Planner and Writer prompts sent to Nightshift | 13/13 upstream vectors (`tests/test_hmem_conformance.py`), `tests/test_meterless_hmem.py`, `tests/test_generator_hmem.py`, `tests/test_critic.py` |

Markovian and Scout Intent are not used (runs are bounded to seconds or minutes by `max_seconds`). Both engines
work in every run, sample and live, and the suite passes (`python -m unittest discover -s tests`).

## Where the engines sit in the pipeline

```
collector -> keeper(ingest pass 1) -> verifier -> keeper(chain facts) -> analyst
          -> [writer -> critic] x <= 2 -> keeper(assert accepted claims; ingest pass 2 = idempotency proof)
          -> hmem(feedback, mining, sleep preview) -> finalize
```

- **World Model** (`investigator/worldmodel/`): the shared state. Every observation the Collector fetched is folded
  through a per-family adapter into entities (`seller`, `endpoint`, `wallet`, `facilitator`, `registry`), contexts
  (`day:utc:<date>`, `investigation:run:<run_id>`), bitemporal relationships (`paid`, `sells`, `pays_to`, `listed_on`,
  `verified_by`, `settled_via`) and facts (registry predicates verbatim, chain predicates, investigator claims). The
  exports `logs/<run_id>/exports/world-{entities,relationships,facts,contexts}.json` are what the Analyst's reducers
  and the Critic's recompute read; `world.events.jsonl` / `world.audit.jsonl` / `world.observations.jsonl` are the
  engine's own logs in the run folder.
- **H-MEM** (`investigator/hmem/`): the memory that gates the Critic. Seven committed pitfall memories
  (`memory/seed/memories.jsonl`) are copied into `logs/<run_id>/hmem/` for every sample run (live runs persist
  `memory/state/`); the Planner and Writer retrieve with the spec formula (trace lines in `hmem.trace.jsonl`) and,
  when a generator is mounted, their prompts carry the `[MEMORY CONTEXT]` reinjection block, logged before each call
  as a `memory` event `op: "inject"` (prompt and block sha256, the prompt's being the `prompt_sha256` of the
  `generate` event that follows; 9 such lines in the committed examples 02, 03 and 05, which replay Nemotron answers
  recorded through Nightshift, 17 in the seven Nemotron live runs of `examples/live-trace-nightshift/` and 14 in the
  six of `examples/live-trace-zetaris-nightshift/`;
  `tests/test_generator_hmem.py` checks the block inside the prompts actually sent with an in-process
  OpenAI-compatible double), the Analyst and the Critic's C3
  gate read the same deterministic `policy_memories(fields, sources, metrics)` index (the Analyst attaches the
  caveats, the Critic verifies them), feedback writes the exact deltas to `ledger.jsonl`,
  and the runner mines one factual record per accepted claim at the end of the run. `scripts/memory_ablation.py`
  measures what one seed memory changes in an example's output (`examples/memory-ablation/`).

## World Model (`investigator/worldmodel/`)

| module | port of | notes |
|---|---|---|
| `stable_ids.py` | `stableIds.ts` | exact formulas: `sha256_hex`, `normalize_name` (NFKD, strip marks and zero-width, lower, collapse), `canonical_json`, `type:system:id`, `ent_/ctx_/rel_/fact_` + 16 hex |
| `resolve.py` | `resolve.ts` | Levenshtein similarity on normalised names; bands 0.85 auto-merge (0.85 exactly merges), [0.82, 0.85) review (0.82 exactly queues), else new |
| `types.py` | `types.ts` | camelCase on the wire (`from`, `to`, `runId`, `validFrom`, ...), snake_case in Python; `to_wire()/from_wire()` |
| `store.py` | `store.ts` | append-only `world.events.jsonl` (seq by append order), `world.audit.jsonl` (one entry per mutation, strictly increasing seq, `at` = observation time), `world.observations.jsonl`, `world.merge-queue.json`; fold-on-load; `fold_until(at)` for point-in-time views |
| `model.py` | `index.ts` + `ingest.ts` | `upsert_entity` (externally keyed) / `upsert_entity_by_name` (resolution pass + alias events), `upsert_context`, `relate` (content-addressed), `assert_fact` (content-addressed, policy hook, winner by policy), `tombstone`, `alias`, `approve_merge/reject_merge`, `ingest()`, `export()` |
| `views.py` | `GraphView/FactsView/SnapshotView/TimelineView` | `graph`, `neighbors_of` (bitemporal), `facts`, `snapshot`, `entity_at`, `timeline` + the domain reducer views `sales_by_payer`, `external_settlements`, `listings_by_host`, `registry_counts`, `catalog_hosts`, `registry_facts` |
| `domain.py` | SPEC 9.2 | entity grammars, `MULTI_VALUED_EDGE_TYPES` (ALL six domain edge types, so payment history is never closed), provenance kind per family, the claim policy hook, `host_of_url` |
| `adapters/` | SPEC 9.3 | one `to_events(observation) -> list[WriteEvent]` per family (receipts, store_openapi, cdp_bazaar / cdp_search / cdp_catalog_snapshot, agent402, x402_trust, vet402, base_rpc) |

Fold rules (SPEC 9.1): `upsertEntity` shallow-merges attrs and records the name as an alias (a placeholder created by
a fact, state `emerging`, becomes `active` on a real upsert); `relate` is a no-op on an existing id and, for types not
in `MULTI_VALUED_EDGE_TYPES`, closes open edges with the same `(from, type, context)` and a different `to`
(`validTo = new.validFrom`, `supersededBy = new.id`); `assertFact` dedupes by id, picks the winner by policy
(`most-recent-wins` default, `highest-confidence-wins`, `source-priority`), marks losers `supersededBy`, writes
`entity.attrs[predicate] = winner.value` and creates a missing subject with state `emerging`; `tombstone` archives,
never deletes. The winner logic lives in the fold (not only in the write path as in the reference) so that
fold-on-load reproduces the exact state from `world.events.jsonl` alone.

### Where "every claim links to evidence" becomes structural

`domain.claim_policy(fact, run_id)` runs inside `WorldModel.assert_fact` before anything is appended: a fact of kind
`agent` is refused (`ValueError`, nothing written, listed in `world.policy_rejections`) unless `source.url` is an
`https://` URL or `log://<run_id>#<seq>` AND `source.runId` is set. The Keeper asserts every accepted claim as such a
fact (source.url = the claim's first evidence ref, context `investigation:run:<run_id>`) and logs
`critique{rule: "provenance", verdict: "reject", kind: "provenance_rejected"}` for anything the hook refuses;
`tests/test_meterless_keeper.py` proves a claim without evidence is refused and counted in `output.world.quarantined`.

### Idempotent ingest (I9)

`WorldModel.ingest(observations, adapter_for)` computes a checksum per observation (`externalId + at + payload +
source checksum`, canonical JSON); an unchanged observation is `skippedUnchanged`, a changed one is re-folded, an
adapter exception quarantines that observation (visible in the report, never fatal). `changed` compares the canonical
state shape (entities, contexts, relationships, facts, observations, sorted) before and after. The Keeper re-ingests
the same observation list at the end of every run and writes `second_pass {eventsAppended: 0, changed: false}` into
`output.world`; `scripts/assert_idempotent.py logs/<run_id>` replays `exports/world-ingest.json` into a fresh World
Model twice and exits 1 on any difference. Sample mode keeps the ORIGINAL provenance kind and url
(`by = "fixture:fixtures/raw/<file>"`, `checksum` = sha256 of the fixture bytes), so a live crawl and its replay fold
to byte-identical world state when the bytes are identical.

### Adapters over the committed fixtures (asserted by `tests/test_adapters.py`, counts read from the files)

| family | events | 2026-10-22 fixture result |
|---|---|---|
| `receipts` | seller, seller wallet (+ `payTo`/`agentId` facts), one endpoint per resource with `sells` and `pays_to`, one payer wallet per address (`ownWallet` attr), one `paid` edge per settlement (context day, `validFrom` = sale `at`, attrs tx/amountAtomic/asset/network/facilitator/x402Version/ownWallet/service), `settled_via` facilitator | 1 seller, 3 own + 4 external wallets, 77 paid edges |
| `cdp_search` / `cdp_bazaar` | per resource: endpoint, `sells`, `listed_on cdp-bazaar` (context day), facts `l30DaysTotalCalls` / `l30DaysUniquePayers` / `lastCalledAt`, `pays_to` per `accepts[]` (Solana payTo -> `wallet:solana:`); search adds `cdpSearchReturned` / `cdpSearchPartialResults` on the seller, pages add `apiTotal` on the registry | 17 listings, 3 quality facts each, payTo 0x6b5a...a6e1 |
| `cdp_catalog_snapshot` | folded PER HOST: seller, `listed_on` (attrs listings), facts `catalog.listings`, `catalog.l30DaysTotalCalls`, `catalog.l30DaysUniquePayersMax` (never summed), `catalog.payToCount`, `catalog.lastCalledAt`; registry fact `catalogSize {items, hosts, itemsWithoutHost, distinctPayTo, totalReportedByApi, snapshotFetchedAt}` | 28,462 items, 2,680 hosts by the SQL `host` definition, which is also `catalogSize.hosts` in the example 02 World Model (hostname without port; 57 URI-illegal resources have a NULL host on both SQL backends and are counted, not folded under a fake host) |
| `store_openapi` | endpoint per `x-payment-info` operation with `declaredPrice`, seller fact `declaredPaidOps` (self-declared, kind receipts, no `listed_on`) | 17 paid operations |
| `agent402` | index: seller `listed_on agent402`, facts `toolCount`, `paidToolCount`, `paywall.ok`, `paywall.status`, `health`, `routable`, `routerDispatchReason`, `agent402.payTo`; one endpoint per tool (`listed_on` with paid/price/declared/lastVerifiedAt); route: registry fact `route.matched {query, matched, sellersMatched, returned}` + one endpoint per result | 34 tools / 17 paid; paywall ok; `settlement_self_funded` |
| `x402_trust` | by `schemaType`: stats -> registry facts (`endpointsListed`, `settlements30d`, ..., `statsBasis` naming the 30-day third-party window); search -> endpoints `listed_on` with `trustScore` / `grade` / `delisted` facts, seller facts `trustSearchTotal` / `trustSearchReturned`, `verified_by` when providerVerified; verified -> `verified_by` + `providerVerified` / `hostScore` / `grade`; facilitators -> facilitator entities; leaderboard -> `hostScore` per host; report -> `report.*` facts; activity / semantic-search / openapi / llms.txt -> no events (adapter_skip note) | search total 36, 25 returned, providerVerified true |
| `vet402` | endpoint per row, `listed_on vet402` (catalog_status, first/last seen), fact `catalog_status`, `pays_to` from `payee_id`, seller fact `vet402Endpoints` | 19 listed |
| `base_rpc` | receipt -> `onchain_tx` about `wallet:base:<payer>` (payer/seller/amount from the USDC Transfer log, AuthorizationUsed fallback, `tx.from` recorded as `facilitatorFrom`, never as the payer); tx -> `onchain_tx_input` (transferWithAuthorization from/to/value cross-check); balanceOf -> `usdc_balance_atomic`; nonce -> `tx_count`; the wallet subject of balanceOf/nonce comes from the observation name (`rpc_balance_<0x...>`, `rpc_nonce_<0x...>`); a batch list or a response without a subject produces nothing | 1 `onchain_tx`, 1 `usdc_balance_atomic` (1,043,148), 1 `tx_count` (24) |

Unknown payload shapes produce `[]` and the Keeper logs an `adapter_skip` note with the top-level keys seen (never a
silent zero). The `host` column semantics are shared with the SQL view layer (`domain.host_of_url` delegates to
`zetaris/views_sqlite.host_of` when importable) so World Model views and SQL agree on what a host is.

### Conformance (`tests/test_worldmodel_conformance.py`)

8/8 upstream checks green, replayed with the runner's own observations: `sid-001`, `sid-002`, `band-001`, `prov-001`,
`idem-001`, `idem-002`, `bitemp-001`, `audit-001`; plus the claim policy hook wired into `assert_fact`, auto-merge via
alias events, and fold-on-load reproducing the canonical state.

### Dropped on purpose

Stream-cluster cycle (stories, score snapshots, velocity/trend), embedding search view, migrations/replay engine,
control-plane HTML, write coalescing / per-instance write queues (single process + one writer), mutable
`entity.aggregates`. The catalog snapshot is folded per host rather than per endpoint (28k endpoints would mean
~200k events per run; the per-endpoint rows stay in the raw export that both SQL backends and the reducers read).

## H-MEM (`investigator/hmem/`)

| module | port of | notes |
|---|---|---|
| `types.py` | `types.ts` + `docs/memory-record.md` | record in the docs shape (camelCase on disk: `memories.jsonl`, `ledger.jsonl`); `score` runtime only; constants: `FEEDBACK_DELTAS`, `RETRIEVAL_WEIGHTS`, threshold 0.35, top 5, conflict gate 0.70, supersedes cosine 0.85, caps 10/5 |
| `embeddings.py` | `embeddings.ts` | deterministic FNV-1a 64-dim hash embedder, unit length, cosine, jaccard; no provider |
| `ledger.py` | `trustLedger.ts` | append-only `ledger.jsonl`, the canonical 17 actions, `log/history/all/stats`; existing entries (the seed ledger) are loaded and kept; never pruned (no rotation implemented) |
| `store.py` | `memoryStore.ts` | `add` rejects an empty source by construction; enrichment: `classify_domain` (tags `project:*`/`tech:*` first, keyword scoring, else general), namespace `<domain>/<profile|facts|notes>`, entities cap 10 (explicit first, then extracted), `related_to` cap 5 bidirectional, `supersedes` when cosine > 0.85 and the correction regex matches; atomic rewrite of `memories.jsonl` |
| `retrieval.py` | `memoryRetrieval.ts` | `score = clamp01(0.35 semantic + 0.20 keyword + 0.10 tag + 0.10 domain + 0.15 entity + 0.05 layer + 0.05 recency - 0.20 superseded) * confidence`; threshold on the FINAL score; one `read` ledger entry per hit; reinjection grouped by domain with `[LT]/[WK]/[ST]` markers; the trace line (`actor, query, threshold, topN, hits[{memoryId, score, signals, confidence}], reinjection_sha256, retrievalReason, strategy`); `feedback` with the exact deltas, `review` tag once, previous/new state and `details {kind, delta, reason}`; `policy_memories(fields, sources, metrics)` |
| `mining.py` | `memoryMining.ts` | `miner_fn` injection with the strict JSON array parser (fence-stripping tolerant); a non-array answer falls back to the model-free path (visible in `last_note`); model-free: one factual candidate per statement line (`"<subject> | <statement>"` lines yield the subject as an entity), 20..300 chars, cap 10 |
| `sleep.py` | `sleepCycle.ts` | `preview()` mutates nothing; `execute(preview)` backs up to `backups/<id>/memories.jsonl` first, consolidates 7-day-idle short-term records, archives (deletes with `sleep_archive` + `delete` entries) old low-access unreferenced records, synthesises cosine >= 0.82 groups into a derived long-term record; guardrails: superseding records, derived records and relationship hubs are never archived; `restore(backup_id)` |
| `conflicts.py` | `conflictDetection.ts` | opposing phrase pairs / conflicting numbers on similar content, the three confidence modifiers, `auto_resolve` with the 0.70 decision-confidence gate: the loser is marked `supersededBy` and kept, otherwise the conflict is queued |
| `dreaming.py` | `dreaming.ts` | insight / domain / invariant proposals persisted in `dreams.json`; nothing materialises before `approve` (long_term record with `derived_from` lineage); `reject` is ledger-only |
| `facade.py` | `index.ts` | `HMEM(clock, persist_dir, miner_fn)`, `mount_hmem()`, shortcuts `add/query/feedback/policy_memories/mine/sleep_preview`, trace helpers |

### How the investigator uses it (SPEC 8.3)

- Seed: seven pitfall memories, type `general`, layer `long_term`, confidence 0.95, source `seed:spec-2026-10-22`,
  tag `pitfall` + `caveat:<code>`, entities `field:<name>` / `source:<family>` / `metric:<id>`. The caveat code a
  memory requires is its `caveat:` tag; `policy_memories(["l30DaysUniquePayers"], ["receipts"])` returns
  `mem-seed-001` and `mem-seed-003`, and `policy_memories([], [], ["external_sales_30d"])` returns `mem-seed-007`,
  deterministically (no embedding involved), which is what the Analyst and the Critic's C3 gate need to agree and
  be reproducible.
- Retrieval: the formula is kept exactly, so the signal strength comes from enrichment. Two domain extensions (not
  formula changes) make the investigator's text score where it should: `extract_entities` also recognises
  `field:`/`source:` tokens, camelCase source fields (`l30DaysUniquePayers` -> `field:l30DaysUniquePayers`) and source
  family aliases (`receipts.json` -> `source:receipts`, `Bazaar search` -> `source:cdp_search`, `basescan` ->
  `source:base_rpc`, ...), and the `tech` keyword list includes the x402 vocabulary so claim statements and questions
  land in the seeds' domain. Measured over the seed in a development run (clock fixed at the fixture capture; these
  four scores are not in a committed trace: the committed `examples/*/hmem.trace.jsonl` show `mem-seed-005` at
  0.35-0.39 and `mem-seed-003` up to 0.49, 0.490189 on a Critic query of example 01): a claim statement that
  names `l30DaysUniquePayers` retrieves `mem-seed-001` at 0.43, a self-settlement statement retrieves `mem-seed-003`
  at 0.39, a Transfer-log statement retrieves `mem-seed-005` at 0.49, and an unrelated question retrieves nothing at
  the 0.35 threshold. A generic planner question scores below 0.35 by the formula; callers that know their sources
  can pass `query(..., entities=["source:receipts", ...])` to steer the entity signal deterministically.
- Feedback: `helpful +0.05 (+1 accessCount)`, `not_helpful -0.03`, `wrong -0.20 (+ review tag once)`, clamped;
  the ledger entry carries previous/new state and the reason (`output.memory.feedback`).
- Mining at finalize: `mine("plan_completion", "<subject> | <statement>\n...", source="run:<run_id>",
  goal_run_id=<run_id>, provenance{origin: goal_run, label: question, refs{goalRunId}})` -> factual records, layer
  `short_term`, confidence 0.8, entities = the subjects. Sample mode mounts no `miner_fn`, so no model is ever called.
- Sleep: `sleep.preview()` is logged every run; `execute()` only in live mode with `--hmem-sleep`.

### Conformance (`tests/test_hmem_conformance.py`)

13/13 vectors of `engines/hmem/conformance/vectors/vectors.json` green (verbatim copy in
`tests/vectors/hmem-vectors.json`, attribution in `tests/vectors/ATTRIBUTION.md`): `rv-001/002`, `fd-001..005`,
`ro-001/002`, `sg-001`, `cg-001`, `la-001`, `db-001`. The upstream runner is TypeScript and loads an `index.ts`; it
cannot load a Python module, so the pytest/unittest module replays the vectors with the same assertions
(formula arithmetic, deltas, gates, boundaries, store immutability during preview, ledger reconstruction, dream
boundary). `tests/test_meterless_hmem.py` covers the investigator-specific surface (seed, policy index, trace,
feedback reasons, mining, sleep/restore, conflicts, dreaming).

### Not implemented (documented)

Ledger rotation (append-only, never pruned), external embedding providers, chat/mission scoping (`chatId`,
`missionId`), the reference's story/velocity analytics (World Model side).

## Nightshift (primary AI provider)

Not an engine port: Meterless Nightshift is the runtime the generator-backed roles call first in live mode
(`NightshiftGenerator` in `investigator/generator.py`, OpenAI-compatible provider `meterless`, model
`METERLESS_MODEL` with `meterless/auto` as the portable default, Auto Detect, fallback to the OpenAI-compatible env
and then to rules). Behind it, with the model pinned (`METERLESS_MODEL`), NVIDIA Nemotron did the work on 2026-10-24: primary, Nemotron 3.5 Lightning 30B A3B on
NVIDIA's hosted API (`METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b`; the NVIDIA API key is configured inside
Nightshift, not in the app), and also Nemotron Nano 9B v2 as Nightshift's local lane on the RTX 5080 Laptop GPU
(`local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m`); thinking is off per request
(`NIGHTSHIFT_REASONING_EFFORT`, default `none`). The first runs that day used a local Llama 3.2 3B lane. The H-MEM
reinjection block reaches its prompts exactly as described above (the `memory` inject lines of those runs precede
the `generate` events with the same prompt sha256). README section "Meterless Nightshift (primary AI
provider)" lists what is implemented, what ran (37 Nemotron requests in seven live runs on 2026-10-24, 32 hosted and 5 local,
`examples/live-trace-nightshift/`, whose SQL ran on SQLite; 30 more in six live runs of 22:36-22:42 UTC that day,
25 hosted and 5 local, with Zetaris as the SQL executor and status `ok`, `examples/live-trace-zetaris-nightshift/`;
25 recorded Nemotron answers replayed by the committed examples,
`fixtures/transcripts/`; the earlier six Llama runs, 32 requests, and the 25 recordings of the first replay set in
`examples/live-trace-nightshift/llama-3.2-3b/`) and what is not claimed; tests in `tests/test_nightshift.py`. Every
receipt those logs cite is in the committed excerpt of Nightshift's Ed25519-signed ledger, each signature valid under
the public key the lines carry (`scripts/nightshift_ledger.py`, excerpt `examples/live-trace-nightshift/nightshift-ledger/`,
`tests/test_nightshift_ledger.py`): that proves the lines are unchanged since signing, not which machine signed them.

## Licensing

Meterless engines are Apache License 2.0 upstream; this repository is MIT. The vectors copy carries attribution in
`tests/vectors/ATTRIBUTION.md`. No upstream source file is copied; the Python modules are ports written for this
repository.
