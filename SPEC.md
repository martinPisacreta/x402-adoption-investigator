# x402 Adoption Investigator — Technical Specification

Status: FROZEN for the build (2026-10-22 00:00 UTC to 2026-10-27 23:45 UTC). Changes go through the integrator and are recorded in `docs/BUILD_LOG.md`.
Track: Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why". Team: Automata (solo, Martin Pisacreta, Argentina).
Sponsor tech, designed in from day one: Meterless (H-MEM + World Model engines, implemented from spec), Zetaris (Lightning SQL federation, live path), Cursor (editor; see `.cursor/rules/project.mdc` and `docs/BUILD_LOG.md`).
Pre-existing component declared: Automata (self-hosted agent on Base, ERC-8004 id 98748, x402 store `https://tienda.textokit.store` with `/receipts.json`, `/openapi.json`, `/llms.txt`, `/mcp`). Nothing from the Automata codebase is copied into this repo; the store is only a public data source here.

---

## 0. One-paragraph pitch (every number sourced)

Agents already ask "how real is x402 adoption?" and get four answers for one host. For `tienda.textokit.store` the CDP Bazaar search returns 17 listings (`fixtures/raw/cdp-discovery-search-urlSubstring-tienda.json`, captured 2026-10-22 00:05Z), the store's own `openapi.json` declares 17 paid operations (`store-openapi.json`), Agent402 indexes 34 tools of which 17 are paid (`agent402-index-seller-tienda.json`), vet402 lists 19 endpoints (`vet402-resolve-tienda.json`) and x402-trust's search reports 36 matches (`x402-trust-search-tienda.json`). The store's receipts say 77 sales, 56 from the seller's own 3 wallets and 21 from 4 external payers worth 44,000 atomic USDC (0.044 USDC), one payer making 18 of the 21 (`store-receipts.json`, generatedAt 2026-10-22T00:03:04.810Z). The Bazaar catalog snapshot holds 28,462 unique resources on 2,713 hosts (`cdp-discovery-catalog-compact.json.gz`; the API reported a live total of 28,469-28,472 during the capture, so the snapshot is dated and says so). Demand evidence for the question itself: Agent402's router matched 49,035 tools from 1,931 sellers for the query "x402 adoption" (`agent402-route-x402-adoption.json`), and the store's receipts contain one external paid call to `/api/x402-adoption` (sale at 2026-10-20T09:22:56.140Z, 5,000 atomic). The investigator is a bounded multi-agent run that collects these public sources, folds them into a Meterless World Model with provenance on every row, computes every number with committed Lightning SQL through Zetaris (or the same SQL on an embedded SQLite backend when offline), verifies settlements on Base, and lets a Critic reject any claim that cannot be traced to a URL, a Base tx hash or a line in its own log. Where sources disagree, the disagreement is a first-class Conflict object with an explanation of the measurement basis. That explanation is the Track 02 deliverable.

Rule for the README and the video: a number that is not in a committed fixture (with file name and capture date) or in a committed example output is not printed. "105 callers" for the Agent402 demand radar is NOT in any fixture; it is not used unless a fixture capturing it is added.

---

## 1. Design invariants (non-negotiable)

I1. No number lives in code. Every figure in `output.json` is a Claim computed by a catalog query (SQL) and cross-checked by a pure-Python reducer over the World Model exports. Pitch numbers in README cite a fixture file + capture date.
I2. Every Claim links to evidence: `https://` URL (with sha256 of the bytes fetched this run or from the fixture manifest), `https://basescan.org/tx/<hash>` (decoded by the Verifier), or `log://<run_id>#<seq>` (a line in this run's `run.jsonl`). The World Model's claim policy hook refuses `kind: "agent"` facts without such a URL; this is where "every claim links to evidence" becomes structural.
I3. Generator-backed roles are exactly three: Planner, Writer, and the Critic's second-opinion pass. Collector, Keeper, Verifier and Analyst are tool-only. The generator can never approve a claim, only add issues (Critic) or propose structure (Planner, Writer). Verdict labels are chosen by registry rules from accepted claims, never by the model.
I4. Sample mode is the primary path: zero network (a socket guard is installed and logged as the first `run.jsonl` event), zero keys, deterministic clock, fixtures captured 2026-10-22 ~00:05Z, recorded generator transcripts, SQLite executing the SAME committed SQL. The three committed examples are produced by sample mode and are byte-for-byte reproducible (`tests/test_examples_reproducible.py`).
I5. Exactly three mount points differ between live and sample: the Fetcher (LiveFetcher | FixtureFetcher), the SQL executor (ZetarisExecutor | SqliteExecutor), and the Generator (OpenAIGenerator | RecordedGenerator/NullGenerator). `output.json` states them in its first key, `mounts`.
I6. A source that is down becomes a visible `unavailable` entry (with the reason), never a silent zero. Metrics that need it are listed in `output.unavailable[]`.
I7. Conflicts are first-class output objects `{id, predicate, subject, values[], explanation, status}`. A claim about a predicate with an open conflict must cite the conflict id or the Critic rejects it.
I8. H-MEM is in the loop: seeded pitfall memories gate the Critic (a claim whose derivation touches a pitfall entity must carry the caveat that cites the memory id); memories receive `helpful | not_helpful | wrong` feedback with the spec's exact deltas; every retrieval writes a `read` ledger entry and a trace line.
I9. World Model ingest is idempotent: the Keeper re-ingests the same observation list at the end of every run and writes `second_pass {eventsAppended: 0, changed: false}` into `output.world`.
I10. Bounded everything: deadline (default 180 s, hard cap 600 s), max fetches 40, max SQL 60, max generator calls 12, max critic rounds 2, max tx verifications 5. Exceeding a bound is a logged `recovery` event, never a crash.
I11. Every analytics SQL statement stays in the ANSI subset (no `explode`, `from_json`, `LATERAL VIEW`, struct dots, Spark-only functions). All JSON flattening lives in the view layer: SchemaStore views on Zetaris, Python-built tables on SQLite. `tests/test_sql_dual_backend.py` runs every catalog query on both backends over the fixtures and asserts equal rows.
I12. Status `partial` requires at least one accepted claim; otherwise the run is `error`. "Any exception becomes partial" is forbidden (it masks bugs): an uncaught exception inside an agent is `E_INTERNAL` naming the stage and the last log seq, after flushing logs.
I13. Coverage is explicit: every catalog-wide claim carries `coverage {items_seen, total_reported, share, basis}` because live mode reads at most 3 Bazaar pages and Zetaris REST tables cannot paginate 28k items.
I14. Secrets only via env vars (`.env.local`, gitignored). The code never prints a secret, never logs request headers, never commits keys. Sample mode needs no env at all.
I15. Code, README and logs are in English. Replayed data is flagged on every evidence item (`replayed: true`) and in `mounts`.

---

## 2. Repository file tree (one line of purpose per file)

```
x402-adoption-investigator/
├── run.py                              # Programmatic entry: {run_id, input} -> {run_id, status, output} | structured error; CLI flags; optional --serve (HTTP POST /run)
├── README.md                           # Setup, usage, 3-minute judge path, agent roster, how Zetaris/Meterless/Cursor are used, dependencies
├── SPEC.md                             # This document
├── LICENSE                             # MIT
├── .env.example                        # Documented env vars (no values)
├── .gitignore                          # .env, .env.local, logs/*, __pycache__, .venv, memory/state
├── .cursor/rules/project.mdc           # Architecture rules Cursor follows while editing this repo
├── pyproject.toml                      # Metadata only (stdlib runtime, pytest optional for tests)
├── Dockerfile                          # python:3.11-slim, copies repo, ENTRYPOINT ["python","run.py"]; no GPU, no deps
├── docker-compose.yml                  # investigator service + env_file .env.local (optional); extra_hosts host.docker.internal
├── docs/
│   ├── BUILD_LOG.md                    # Daily build log: what was built with Cursor, decisions, verification results
│   ├── ZETARIS.md                      # Verified REST calls, DDL, SQL catalog rationale, fallback matrix
│   ├── METERLESS.md                    # H-MEM and World Model mapping, conformance results, what was dropped and why
│   └── DEMO_SCRIPT.md                  # The scripted 3-minute path with the exact lines to point at
├── investigator/
│   ├── __init__.py                     # Package version
│   ├── contracts.py                    # SHARED dataclasses and protocols (integrator-owned; builders import, never edit)
│   ├── clock.py                        # Clock protocol: SystemClock, SampleClock (fixed instant + seq ms)
│   ├── guard.py                        # Network guard for sample mode (patches socket + urllib; raises NetworkDisabledError)
│   ├── eventlog.py                     # EventLog: run.jsonl writer (stdout + file), seq counter, log:// refs
│   ├── config.py                       # Env + CLI -> RunConfig; loads .env.local without printing values
│   ├── errors.py                       # InvestigatorError(code, stage, message, retryable, details)
│   ├── schemas/
│   │   ├── input.schema.json           # run.py input contract
│   │   ├── output.schema.json          # run.py output contract
│   │   ├── plan.schema.json            # Planner output contract (also validates generator JSON)
│   │   └── events.schema.json          # run.jsonl line contract
│   ├── runner.py                       # Deterministic state machine: the pipeline, budgets, deadline, handoffs, finalize
│   ├── generator.py                    # Generator protocol: OpenAIGenerator (urllib), RecordedGenerator, NullGenerator
│   ├── sources/
│   │   ├── __init__.py
│   │   ├── registry.py                 # Source families, request builders, canonical request keys
│   │   ├── fetcher.py                  # Fetcher protocol: LiveFetcher (urllib, retries), FixtureFetcher (MANIFEST), FallbackFetcher (live -> fixture, stale=true)
│   │   ├── manifest.py                 # Loads fixtures/raw/MANIFEST.json; maps request key -> file, capturedAt, sha256
│   │   ├── retry.py                    # (added 2026-10-23) the one bounded retry policy both Fetchers run their attempts through
│   │   ├── transient.py                # (added 2026-10-23) loads fixtures/transient/<example>/faults.json for sample replay
│   │   └── catalog.py                  # Compact catalog snapshot loader (gz) and live page crawler (<= 3 pages)
│   ├── chain/
│   │   ├── __init__.py
│   │   ├── base_rpc.py                 # JSON-RPC client (batch), fixture-backed in sample mode
│   │   └── decode.py                   # Transfer / AuthorizationUsed log decoding, transferWithAuthorization input decoding, balanceOf calldata
│   ├── worldmodel/                     # Meterless World Model, Python port of the reference (stdlib only)
│   │   ├── __init__.py                 # Exports WorldModel, entity_id_from_name, normalize_name, decide, canonical_json, external_id
│   │   ├── types.py                    # Provenance, Entity, Context, Relationship, Fact, Event, AuditEntry, IngestObservation, IngestReport
│   │   ├── stable_ids.py               # sha256_hex, normalize_name, canonical_json, entity/context/relationship/fact id formulas
│   │   ├── resolve.py                  # levenshtein, name_similarity, decide (0.85 auto / 0.82 review), alias resolution
│   │   ├── store.py                    # Append-only JSONL event log, audit log, observations log, fold-on-load
│   │   ├── model.py                    # WorldModel: upsertEntity/upsertContext/relate/assertFact/tombstone/alias, ingest(), policy hooks
│   │   ├── views.py                    # graph/facts/snapshot/timeline + custom reducer views used by the Analyst's reducers
│   │   ├── domain.py                   # Domain constants: entity grammars, MULTI_VALUED_EDGE_TYPES, claim policy hook
│   │   ├── schema/world-event.schema.json
│   │   └── adapters/
│   │       ├── __init__.py             # adapter(family) -> callable(observation) -> list[WriteEvent]
│   │       ├── receipts.py             # receipts.json -> seller, wallets, endpoints, paid edges, pays_to
│   │       ├── cdp_bazaar.py           # discovery/search pages -> endpoints, listed_on cdp-bazaar, quality facts
│   │       ├── agent402.py             # index/route -> listed_on agent402, toolCount/paidToolCount/paywall.ok/health facts
│   │       ├── x402_trust.py           # stats/search/verified/facilitators -> verified_by, hostScore, providerVerified facts
│   │       ├── vet402.py               # resolve -> listed_on vet402, catalog_status facts
│   │       └── base_rpc.py             # decoded tx -> onchain_tx, usdc_balance_atomic, tx_count facts (kind chain)
│   ├── hmem/                           # Meterless H-MEM, Python port of the reference subset (stdlib only)
│   │   ├── __init__.py                 # Exports HMEM facade
│   │   ├── types.py                    # MemoryRecord (docs shape), Provenance, LedgerEntry, FEEDBACK_DELTAS
│   │   ├── embeddings.py               # Deterministic FNV-1a 64-dim hash embedder, cosine
│   │   ├── ledger.py                   # TrustLedger: append-only ledger.jsonl, log/history/all/stats
│   │   ├── store.py                    # MemoryStore: add/get/all/update/delete/promote/touch, memories.jsonl, enrichment
│   │   ├── retrieval.py                # 8-signal formula, threshold on final score, reinjection block, trace, feedback
│   │   ├── mining.py                   # MemoryMining: minerFn injection, strict JSON array parser, model-free fallback
│   │   ├── sleep.py                    # Sleep preview()/execute()/restore() with guardrails and backupId
│   │   ├── conflicts.py                # Conflict scan()/autoResolve() with the 0.70 gate, supersededBy, never delete
│   │   ├── dreaming.py                 # dream()/approve()/reject(); nothing materializes before approve
│   │   └── facade.py                   # HMEM(clock, persist_dir, miner_fn) exposing store/ledger/retrieval/sleep/conflicts/dreaming
│   ├── zetaris/
│   │   ├── __init__.py
│   │   ├── client.py                   # REST client: login, headers, run_query, paginated start/page/close, metadata GETs, 401 re-login
│   │   ├── ddl.py                      # DDL builders: CREATE LIGHTNING DATABASE/REST TABLE, SCHEMASTORE CONTAINER/VIEW, CACHE/UNCACHE/DROP
│   │   ├── artifact_server.py          # Stdlib HTTP server serving logs/<run_id>/exports/ read-only to the Zetaris driver via host.docker.internal
│   │   ├── executor.py                 # SqlExecutor protocol; ZetarisExecutor; SqliteExecutor; typed row casting from .meta.json
│   │   ├── views_sqlite.py             # Python-built flat tables with the SAME names/columns as the SchemaStore views
│   │   ├── views_zetaris.py            # SchemaStore view DDL (LATERAL VIEW explode) producing the same names/columns
│   │   └── catalog.py                  # Loads sql/analytics/*.sql + .meta.json, sha256 per query, parameter binding
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── planner.py                  # Question -> Plan (deterministic router; generator refines, validated by schema)
│   │   ├── collector.py                # Plan.fetch_list -> Observations via Fetcher; evidence index; raw exports
│   │   ├── keeper.py                   # Observations -> World Model via adapters; IngestReport; exports; second-pass proof
│   │   ├── verifier.py                 # Chain verification sample, cross-registry checks, Verification[] + Conflict candidates
│   │   ├── analyst.py                  # Metric registry: SQL via executor + Python reducer; Claims[] + Conflicts[] + unavailable[]
│   │   ├── writer.py                   # Claims -> Answer (generator with H-MEM reinjection | template renderer)
│   │   ├── critic.py                   # Deterministic checks C1-C7 + narrative pass B + optional generator second opinion
│   │   ├── metrics.py                  # Metric registry: id -> {query_id, reducer, unit, required_sources, caveat rules, coverage rule}
│   │   ├── reducers.py                 # Pure-Python reducers over World Model exports (one per metric)
│   │   ├── verdict.py                  # Verdict rules: label from accepted claims (never from the model)
│   │   ├── report.py                   # report.md renderer (sections, [cN] citations, conflicts section)
│   │   └── prompts/
│   │       ├── planner.txt             # Planner prompt with {{hmem_reinjection}} slot
│   │       ├── writer.txt              # Writer prompt: claims JSON table, must cite [cN], must mention every conflict
│   │       └── critic.txt              # Critic second-opinion prompt: list unsupported statements only
│   └── sql/
│       ├── views/
│       │   └── VIEWS.md                # Source of truth: view names, columns, source family, both backends
│       └── analytics/                  # Committed ANSI-subset queries; qNN_name.sql + qNN_name.meta.json (column types, params, expected rows bound)
│           ├── q01_sales_by_wallet_class.sql
│           ├── q02_external_payers.sql
│           ├── q03_repeat_external_payers.sql
│           ├── q04_top_payer_share.sql
│           ├── q05_external_sales_by_service.sql
│           ├── q06_bazaar_host_listings.sql
│           ├── q07_bazaar_payto_vs_receipts_seller.sql
│           ├── q08_catalog_size.sql
│           ├── q09_catalog_top_hosts_by_calls.sql
│           ├── q10_catalog_active_share.sql
│           ├── q11_catalog_payto_concentration.sql
│           ├── q12_registry_count_openapi.sql
│           ├── q13_registry_count_agent402.sql
│           ├── q14_registry_count_vet402.sql
│           ├── q15_registry_count_trust_search.sql
│           ├── q16_settlements_vs_receipts.sql
│           ├── q17_payer_dossier.sql
│           ├── q18_external_sales_by_day.sql
│           └── q19_trust_ecosystem_stats.sql
├── fixtures/
│   ├── raw/                            # 27 raw public responses + MANIFEST.json (captured 2026-10-22 ~00:05Z); B1 adds the requests map
│   ├── zetaris/                        # Recorded Zetaris result sets per query id (used by the dual-backend test)
│   ├── transient/                      # (added 2026-10-23) scripted transient failures per example: <example>/faults.json + README.md
│   └── transcripts/                    # Recorded generator transcripts per example: <example>/<prompt_id>-<prompt_sha12>.json
├── memory/
│   ├── seed/memories.jsonl             # Seeded H-MEM records (pitfall memories), committed, with provenance
│   ├── seed/ledger.jsonl               # Ledger entries for the seed creates
│   └── state/                          # Live-mode persistent H-MEM (gitignored)
├── examples/
│   ├── 01-seller-audit-textokit/       # input.json, output.json, report.md, run.jsonl, sql.jsonl, hmem.trace.jsonl
│   ├── 02-catalog-concentration/
│   ├── 03-payer-dossier-onchain/
│   └── live-trace/<run_id>/            # Real Zetaris + Base RPC traces of the same questions, labelled live, for diffing
├── scripts/
│   ├── regen_examples.py               # Re-runs the 3 examples in sample mode and rewrites examples/*/ (the only way to update them)
│   ├── assert_idempotent.py            # Re-ingests an example's observations and asserts changed == false
│   ├── build_manifest_requests.py      # Generates MANIFEST.requests (request key -> file, sha256, capturedAt) from the fixture files
│   ├── record_zetaris_results.py       # Runs the catalog on the live Zetaris and stores typed result sets under fixtures/zetaris/
│   ├── zetaris_preflight.py            # Login, SHOW SERVER VERSION, artifact-server reachability from the driver; prints a pass/fail table
│   ├── zetaris_cleanup.py              # Drops leftover X402_* databases / x402_* containers from crashed runs
│   ├── record_transcripts.py           # Runs an example live with --record-transcripts and stores generator transcripts
│   ├── hmem_dream.py                   # Offline dreaming over memory/state (proposals need approve)
│   ├── one_command.sh                  # The clean-clone command for judges (bash)
│   └── one_command.ps1                 # The clean-clone command for judges (PowerShell)
├── tests/
│   ├── conftest.py                     # Fixture paths, sample clock, temp run dirs
│   ├── test_worldmodel_conformance.py  # Port of the 8 Meterless World Model checks (sid-001, sid-002, band-001, prov-001, idem-001, idem-002, bitemp-001, audit-001)
│   ├── test_hmem_conformance.py        # Replays engines/hmem/conformance/vectors/vectors.json (13 checks) against the Python port
│   ├── vectors/hmem-vectors.json       # Verbatim copy of Meterless vectors.json (with attribution)
│   ├── test_sql_dual_backend.py        # Every catalog query on SqliteExecutor vs recorded Zetaris result sets: equal rows
│   ├── test_examples_reproducible.py   # Sample mode run of the 3 examples == committed output.json byte-for-byte
│   ├── test_sample_offline.py          # Run with guard installed; any socket use fails the test
│   ├── test_fetcher.py                 # Request keys, fixture resolution for all 27 files, fallback stale flag
│   ├── test_retry.py                   # (added 2026-10-23) retry policy in both modes on a fake HTTP layer; example 05
│   ├── test_critic.py                  # C1-C7 + narrative pass B unit tests (token -> claim map)
│   ├── test_adapters.py                # Each adapter over its fixture: expected entity/fact counts
│   ├── test_decode.py                  # Transfer / AuthorizationUsed / transferWithAuthorization decoding against the RPC fixture
│   └── test_run_contract.py            # run.py I/O schema, error codes, partial rule
└── logs/
    └── .gitkeep                        # Runtime: logs/<run_id>/{run,fetch,sql,zetaris,hmem.trace,world.events,world.audit,world.observations}.jsonl, output.json, report.md, exports/, raw/
```

Dependencies: Python 3.11 standard library only at runtime (json, urllib, sqlite3, hashlib, unicodedata, gzip, threading, http.server, dataclasses). `pytest` is the only dev dependency.

---

## 3. Shared contracts (`investigator/contracts.py`, integrator-owned)

All builders import from this file. Field names are wire names (camelCase inside JSON written by the Meterless engines, snake_case in investigator JSON; `to_json()` methods handle the mapping).

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Optional

Mode = Literal["sample", "live"]
Status = Literal["ok", "partial", "error"]
AgentStatus = Literal["ok", "degraded", "failed"]

@dataclass(frozen=True)
class Budget:
    max_seconds: int = 180          # hard cap 600
    max_fetches: int = 40
    max_sql: int = 60
    max_generator_calls: int = 12
    max_critic_rounds: int = 2
    max_tx_verifications: int = 5
    bazaar_pages: int = 3           # live pages of limit=1000

@dataclass
class Focus:
    host: Optional[str] = None      # lowercased
    wallet: Optional[str] = None    # 0x + 40 hex, lowercased
    service: Optional[str] = None
    query: Optional[str] = None

@dataclass
class RunInput:
    question: str
    focus: Focus
    budget: Budget
    options: dict[str, Any]         # catalog: "pages"|"snapshot"; zetaris_source: "captured"|"direct"

@dataclass
class Mounts:
    mode: Mode
    fetcher: Literal["live", "fixture", "live+fixture-fallback"]
    executor: Literal["zetaris", "sqlite"]
    generator: str                  # "openai:<model>" | "recorded" | "null"
    fixtures_captured_at: Optional[str]   # MANIFEST.fetchedAt when any fixture was used
    zetaris_version: Optional[str]  # "2.4.3.1" when live
    zetaris_source: Optional[Literal["captured", "direct"]]

@dataclass
class Evidence:
    kind: Literal["url", "tx", "log_line"]
    ref: str                        # https://..., https://basescan.org/tx/0x..., log://<run_id>#<seq>
    sha256: Optional[str] = None    # bytes digest for url evidence
    captured_at: Optional[str] = None
    replayed: bool = False
    stale: bool = False
    http: Optional[int] = None
    verified: Optional[bool] = None # tx evidence only
    fixture_file: Optional[str] = None

@dataclass
class Coverage:
    items_seen: int
    total_reported: Optional[int]
    share: Optional[float]
    basis: str                      # "live pages (3 x 1000)" | "compact snapshot 2026-10-22" | "search capped at 20" | "full source"

@dataclass
class Derivation:
    agent: str                      # "analyst" | "verifier"
    query_id: Optional[str]         # "q04_top_payer_share"
    sql_sha256: Optional[str]
    backend: Optional[str]          # "zetaris" | "sqlite" | "sqlite-fallback"
    reducer: Optional[str]          # "reducers.top_payer_share"
    rows: int = 0
    fields: list[str] = field(default_factory=list)    # source fields touched, e.g. ["l30DaysUniquePayers"]
    sources: list[str] = field(default_factory=list)   # source families touched, e.g. ["receipts", "cdp_search"]
    log_seq: Optional[int] = None   # seq of the sql/verify event

@dataclass
class Critique:
    round: int
    rule: str                       # "C1".."C7", "B", "LLM"
    verdict: Literal["accept", "reject", "revise"]
    reason: str
    memory_ids: list[str] = field(default_factory=list)

@dataclass
class Claim:
    id: str                         # "c01"
    metric: str
    subject: str                    # "seller:host:tienda.textokit.store" | "catalog:cdp-bazaar" | "wallet:base:0x..."
    statement: str                  # templated from the registry
    value: Any                      # number | {"numerator": n, "denominator": d, "share": f} | str
    unit: str                       # "count" | "atomic_usdc" | "share" | "iso8601" | "text"
    period: Optional[str] = None    # "all_time" | "l30d" | "2026-10-20..2026-10-21"
    evidence: list[Evidence] = field(default_factory=list)
    derivation: Optional[Derivation] = None
    coverage: Optional[Coverage] = None
    caveats: list[str] = field(default_factory=list)     # codes; each has a memory_id when it comes from H-MEM
    caveat_memory_ids: dict[str, str] = field(default_factory=dict)
    conflict_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    status: Literal["proposed", "accepted", "rejected", "revised"] = "proposed"
    cross_checked: bool = False
    critique: list[Critique] = field(default_factory=list)

@dataclass
class ConflictValue:
    source: str                     # family or registry name
    value: Any
    url: str
    basis: str                      # "30-day window" | "all-time" | "per endpoint" | "search capped at 20" | ...
    captured_at: Optional[str] = None

@dataclass
class Conflict:
    id: str                         # "k01"
    predicate: str                  # "endpoint_count_for_host"
    subject: str
    values: list[ConflictValue]
    explanation: str                # why they differ (measurement basis); rule-written, the Writer may rephrase but never change the substance
    status: Literal["open", "explained", "resolved"]
    claim_ids: list[str] = field(default_factory=list)

@dataclass
class Observation:
    name: str                       # "store_receipts", "cdp_search_host", "cdp_page_0", "rpc_receipt_<hash>"
    family: str                     # "receipts" | "cdp_bazaar" | "cdp_search" | "cdp_catalog_snapshot" | "store_openapi" | "agent402" | "x402_trust" | "vet402" | "base_rpc"
    request_key: str                # canonical key (section 7.3)
    url: str
    method: str
    http: Optional[int]
    bytes_sha256: Optional[str]
    latency_ms: int
    captured_at: str
    replayed: bool
    stale: bool
    status: Literal["ok", "unavailable", "fixture_miss"]
    error: Optional[str]
    payload: Any                    # parsed JSON (or text)
    fixture_file: Optional[str] = None
    export_path: Optional[str] = None   # logs/<run_id>/exports/<name>.json

@dataclass
class Verification:
    tx: str
    status: Literal["verified", "failed", "unverifiable"]
    payer: Optional[str]
    seller: Optional[str]
    amount_atomic: Optional[int]
    block: Optional[int]
    block_time: Optional[str]
    facilitator_from: Optional[str]
    basescan_url: str
    decoded_from: str               # "Transfer log" | "AuthorizationUsed log" | "input transferWithAuthorization"
    receipt_match: Optional[bool]
    reason: Optional[str] = None    # "fixture_miss" | "rpc_unavailable" | "decode_mismatch:<field>"

@dataclass
class Plan:
    playbook: Literal["seller_profile", "catalog_concentration", "payer_dossier"]
    params: dict[str, Any]
    fetch_list: list[dict]          # [{name, family, request_key, url, method, body?, optional: bool, priority: int}]
    query_ids: list[str]
    metrics: list[str]
    tx_sample_rule: str
    hypotheses: list[str]
    planner_source: Literal["rules", "generator", "recorded-generator"]
    hmem_memory_ids: list[str]

@dataclass
class AgentResult:
    agent: str
    status: AgentStatus
    payload: Any
    notes: list[str] = field(default_factory=list)

class Fetcher(Protocol):
    mode: str
    def fetch(self, request_key: str, url: str, method: str = "GET", body: Any = None, timeout: float = 10.0) -> Observation: ...

@dataclass
class GeneratorResult:
    text: str
    model: str
    recorded: bool
    prompt_sha256: str
    usage: dict[str, int]

class Generator(Protocol):
    name: str
    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float, timeout: float) -> Optional[GeneratorResult]: ...

@dataclass
class SqlResult:
    query_id: str
    executor: str
    headers: list[str]
    rows: list[list[Any]]           # typed per .meta.json
    rowcount: int
    time_used_ms: int
    sql_sha256: str
    fallback_reason: Optional[str] = None
    zetaris_version: Optional[str] = None
    tables: list[dict] = field(default_factory=list)

class SqlExecutor(Protocol):
    name: str                       # "zetaris" | "sqlite"
    def ensure_views(self, exports: dict[str, str]) -> list[dict]: ...   # returns tables:[{name, source_url}]
    def execute(self, query_id: str, sql: str, params: dict[str, Any], meta: dict) -> SqlResult: ...
    def cleanup(self) -> None: ...
```

Other shared pieces:

```python
# investigator/clock.py
class Clock(Protocol):
    def now_iso(self) -> str: ...        # "YYYY-MM-DDTHH:MM:SS.mmmZ"
    def now_ms(self) -> int: ...
class SystemClock: ...
class SampleClock:                       # fixed start (MANIFEST.fetchedAt) + 1 ms per call, so logs are deterministic
    def __init__(self, start_iso: str): ...

# investigator/eventlog.py
class EventLog:
    def __init__(self, run_id: str, run_dir: Path, clock: Clock, echo_stdout: bool = True): ...
    def emit(self, type: str, agent: str, payload: dict) -> int: ...   # returns seq; writes run.jsonl line + stdout
    def ref(self, seq: int) -> str: ...                                 # "log://<run_id>#<seq>"
    def side(self, name: str, line: dict) -> None: ...                  # fetch.jsonl / sql.jsonl / zetaris.jsonl / hmem.trace.jsonl, seq from the same counter
    def flush(self) -> None: ...

# investigator/errors.py
class InvestigatorError(Exception):
    code: Literal["E_INPUT", "E_TIMEOUT", "E_NO_SOURCES", "E_GENERATOR", "E_ZETARIS_REQUIRED", "E_INTERNAL"]
    stage: str; message: str; retryable: bool; details: dict

# investigator/guard.py
class NetworkDisabledError(RuntimeError): ...
def install_network_guard() -> list[str]: ...   # returns the patched names, e.g. ["socket.socket", "socket.create_connection", "urllib.request.urlopen"]
```

---

## 4. `run.py` I/O contract

### 4.1 Invocation

```
python run.py --input examples/01-seller-audit-textokit/input.json            # sample mode by default when no keys/Zetaris configured
python run.py --input <file|-> [--run-id ID] [--mode sample|live|auto]
              [--require-zetaris] [--require-generator]
              [--zetaris-source captured|direct] [--catalog pages|snapshot]
              [--out logs] [--record-transcripts] [--print-output] [--quiet] [--keep-zetaris-objects]
python run.py --serve 8080                                                      # optional HTTP: POST /run with the same JSON body
cat input.json | python run.py --input -
```

_Amended 2026-10-23:_ `--fetch-retries N` (0-5, default 2) sets the retries per source request (section 7.2).

Mode resolution (`--mode auto`, the default): `sample` if `SAMPLE_MODE=1`, or if neither `OPENAI_API_KEY` nor `ZETARIS_PASSWORD` is set; else `live`. `--mode live` with missing pieces degrades per mount (fetcher live, executor sqlite, generator null) unless `--require-*` is set. `--mode sample` ignores all env and installs the network guard.

Exit code: 0 for `ok` and `partial`, 1 for `error`. Stdout carries the JSONL event stream (one line per event, identical to `logs/<run_id>/run.jsonl`); with `--print-output` the final `output.json` is printed after a line `---output---`; with `--quiet` only the output JSON is printed. In `--serve` mode the HTTP response body is the output JSON and the event stream goes to the server's stdout.

### 4.2 Input (`investigator/schemas/input.schema.json`)

```json
{
  "run_id": "ex01-seller-audit",
  "input": {
    "question": "How real is the adoption of tienda.textokit.store? Who pays, do they come back, and why do registries disagree?",
    "focus": { "host": "tienda.textokit.store", "wallet": null, "service": null, "query": null },
    "budget": { "max_seconds": 180, "max_fetches": 40, "max_tx_verifications": 5, "max_critic_rounds": 2, "bazaar_pages": 3 },
    "options": { "catalog": "snapshot", "zetaris_source": "captured" }
  }
}
```

`run_id` is optional (default `run-<utc compact>-<4 hex>`; examples pin it). `question` is required (3..1000 chars). `focus` is optional but the Planner's router needs a host, a wallet or a catalog keyword to pick a playbook; otherwise `E_INPUT` with `details.supported_playbooks`.

### 4.3 Output, success

```json
{
  "run_id": "ex01-seller-audit",
  "status": "ok",
  "output": {
    "mounts": { "mode": "sample", "fetcher": "fixture", "executor": "sqlite", "generator": "recorded", "fixtures_captured_at": "2026-10-22T00:05:00Z", "zetaris_version": null, "zetaris_source": null },
    "sample_mode_notice": "This run replayed committed fixtures captured 2026-10-22 ~00:05Z; no network, no model calls.",
    "question": "...",
    "plan": { "playbook": "seller_profile", "query_ids": ["q01_sales_by_wallet_class", "..."], "sources": ["receipts", "cdp_search", "..."], "planner_source": "recorded-generator" },
    "verdict": { "label": "self_settled_mostly", "confidence": 0.86, "why": ["c01", "c03", "c05"] },
    "answer": "...text where every sentence with a number cites [cNN]...",
    "claims": [ { "...Claim..." } ],
    "rejected_claims": [ { "...Claim with status rejected and critique[]..." } ],
    "conflicts": [ { "...Conflict..." } ],
    "unavailable": [ { "metric": "usdc_balance_atomic", "reason": "base_rpc unavailable: timeout after 2 retries" } ],
    "sources": [ { "name": "store_receipts", "family": "receipts", "url": "...", "status": "ok", "http": 200, "sha256": "...", "captured_at": "...", "replayed": true, "stale": false } ],
    "verifications": [ { "...Verification..." } ],
    "world": { "first_pass": { "runId": "run_...", "processed": 9, "skippedUnchanged": 0, "eventsAppended": 412, "changed": true }, "second_pass": { "processed": 9, "skippedUnchanged": 9, "eventsAppended": 0, "changed": false }, "quarantined": 0, "claims_asserted": 14 },
    "memory": { "retrieved": [{ "memory_id": "mem-seed-001", "actor": "critic", "score": 0.61 }], "cited": ["mem-seed-001", "mem-seed-003"], "feedback": [{ "memory_id": "mem-seed-001", "kind": "helpful", "reason": "caveat required and attached to c06" }], "mined": 2 },
    "agents": [ { "role": "planner", "generator_backed": true, "generator_used": "recorded", "attempts": 1, "status": "ok" } ],
    "critique_rounds": 2,
    "report_path": "logs/ex01-seller-audit/report.md",
    "log_files": ["run.jsonl", "fetch.jsonl", "sql.jsonl", "hmem.trace.jsonl", "world.events.jsonl", "world.audit.jsonl", "world.observations.jsonl"]
  }
}
```

`sample_mode_notice` is present only in sample mode. `output.json` contains no wall-clock durations (they live in the logs) so sample runs are byte-identical. Key order is fixed by the schema; written with `json.dumps(indent=2, sort_keys=False, ensure_ascii=False)` plus a trailing newline.

### 4.4 Output, error

```json
{ "run_id": "...", "status": "error",
  "error": { "code": "E_ZETARIS_REQUIRED", "stage": "analyst", "message": "Zetaris login failed: HTTP 401 and --require-zetaris is set", "retryable": true, "details": { "reason": "zetaris_required", "last_log_seq": 57 } } }
```

Codes: `E_INPUT` (schema/playbook), `E_TIMEOUT` (deadline hit before any claim was accepted), `E_NO_SOURCES` (every mandatory source unavailable), `E_GENERATOR` (only with `--require-generator`), `E_ZETARIS_REQUIRED` (only with `--require-zetaris`; `details.reason = "zetaris_required"`), `E_INTERNAL` (uncaught exception; `details.agent`, `details.last_log_seq`, `details.traceback_sha256`).

Status rule: `ok` = every planned mandatory step completed, no fallback mount was used against the requested mode, and no claim was rejected; `partial` = at least one accepted claim AND (a source unavailable, or a deadline skip, or rejected claims remain, or a fallback executor/generator was used while live was requested); `error` otherwise. Logs are always flushed before returning.

---

## 5. Logs: JSONL event schema

All files live in `logs/<run_id>/`. `run.jsonl` is mirrored to stdout line by line. Every line: `{"seq": int, "at": iso, "run_id": str, "type": str, "agent": str, "payload": {...}}`. `seq` is one counter shared by all side files, so `log://<run_id>#<seq>` is unambiguous; side files repeat the seq of their companion `run.jsonl` line.

| type | agent | payload (required keys) | when |
|---|---|---|---|
| `guard` | runner | `{network: "disabled", patched: ["socket.socket", "socket.create_connection", "urllib.request.urlopen"]}` | FIRST line in sample mode, before `run_start` |
| `run_start` | runner | `{mounts, input_sha256, budget, deadline_at}` | first line in live mode, second in sample |
| `plan` | planner | `{playbook, query_ids, sources, fetch_count, hmem_memory_ids, planner_source}` | before any data moves |
| `handoff` | runner | `{from, to, artifact: {type, count, sha256}}` | every transition |
| `fetch` | collector | `{name, family, url, method, http, bytes, sha256, latency_ms, replayed, stale, fixture_file?}` | per request (full line in fetch.jsonl) |
| `retry` | collector / verifier / planner | `{stage, target, attempt, max_attempts, reason, backoff_ms}` plus, for a source request, `url, method, next_attempt, kind, http, replayed, fault_file?` (_amended 2026-10-23_: `attempt` is the attempt that failed; the outcome is the next `fetch` line of the same request key, which carries `attempts` when more than one was needed) | each retry, before its backoff |
| `recovery` | any | `{reason, from?, to?, detail}` | every fallback or skip; reasons: `planner_fallback`, `fixture_fallback`, `source_unavailable`, `fixture_miss`, `zetaris_unreachable`, `zetaris_query_fallback`, `artifact_server_unreachable`, `rpc_unavailable`, `generator_fallback`, `narrative_trimmed`, `deadline_skip`, `budget_exhausted` |
| `ingest` | keeper | `{pass: 1|2, report: IngestReport, quarantined: [{externalId, error}]}` | after each ingest |
| `verify` | verifier | `{tx, status, payer, seller, amount_atomic, basescan_url, receipt_match, reason?}` | per tx |
| `crosscheck` | verifier | `{name, a: {source, value, url}, b: {source, value, url}, agree, note}` | per cross-check |
| `sql` | analyst | `{query_id, executor, sql_sha256, params, rowcount, time_used_ms, fallback_reason?}` (full line in sql.jsonl) | per query |
| `claim` | analyst / verifier | `{claim_id, metric, subject, value, unit, backend, cross_checked, caveats, coverage?}` | per proposed claim |
| `conflict` | analyst / verifier | `{conflict_id, predicate, subject, values: [{source, value, url, basis}], explanation}` | per conflict |
| `critique` | critic / keeper | `{round, rule, target: claim_id|sentence_index, verdict, reason, memory_ids, kind?}`; `kind: "provenance_rejected"` when the World Model refused an agent fact | per check that fires, plus one `{rule, verdict: "accept", count}` summary per rule |
| `narrative_map` | critic | `{round, tokens: [{token, claim_id|null, kind: "value"|"share"|"numerator"|"denominator"|"formatted"}], unsourced: [...]}` | pass B |
| `memory` | planner / writer / critic / runner | `{op: "retrieve"|"inject"|"feedback"|"mine"|"sleep_preview", actor, memory_ids, details}` (trace in hmem.trace.jsonl; `inject` added 2026-10-22: a generator prompt that carries the reinjection block, with prompt and block sha256) | per H-MEM op |
| `timeout` | runner | `{stage, elapsed_s, budget_s}` | deadline hit |
| `result` | runner | `{status, accepted, rejected, conflicts, unavailable, executor, generator}` | last line |
| `error` | runner | `{code, stage, message, retryable, details}` | before returning an error |

Side files:

- `fetch.jsonl`: `{seq, name, family, request_key, url, method, http, bytes, sha256, latency_ms, replayed, stale, fixture_file, export_path, error}`.
- `sql.jsonl`: `{seq, query_id, executor: "zetaris"|"sqlite", sql, sql_sha256, params, headers, rowcount, rows: rows[:50], time_used_ms, zetaris_version, fallback_reason, tables: [{name, source_url}]}`.
- `zetaris.jsonl` (live only): `{seq, op: "login"|"ddl"|"run-query"|"start"|"page"|"close"|"metadata"|"cleanup", path, http, time_used_ms, sql?, msg?}`; never headers, never tokens, never credentials.
- `hmem.trace.jsonl`: `{seq, actor, query, threshold, topN, hits: [{memoryId, score, signals: {semantic, keyword, tag, domain, entity, layer, recency, superseded}, confidence}], reinjection_sha256, retrievalReason, strategy}` plus feedback lines `{seq, actor, memoryId, kind, previous_confidence, new_confidence}`.
- `world.events.jsonl`, `world.audit.jsonl`, `world.observations.jsonl`: engine formats (section 9).

Human-readable progress goes to stderr only with `--verbose`.

---

## 6. Agent roster and pipeline

| Role | Module | Generator-backed | Inputs | Outputs | Refuses / never does |
|---|---|---|---|---|---|
| Runner (not an agent) | `runner.py` | no | validated input, config | handoffs, status, output.json | never retries the whole run; never swallows exceptions into `partial` |
| Planner | `agents/planner.py` | yes (live: OpenAI; sample: recorded transcript, else rules) | question, focus, H-MEM retrieval (actor `planner`) | `Plan` validated by `plan.schema.json` | never emits free-form SQL or unknown query ids; never guesses a playbook (E_INPUT) |
| Collector | `agents/collector.py` | no | `Plan.fetch_list`, Fetcher, budget | `Observation[]`, exports, fetch.jsonl | never fabricates a missing source; never exceeds max_fetches |
| Keeper | `agents/keeper.py` | no | Observations, chain facts, accepted claims | IngestReport x2, world views, exports | never writes without provenance; never deletes history (supersession only) |
| Verifier | `agents/verifier.py` | no | world views, Fetcher (RPC), budget | `Verification[]`, cross-checks, conflict candidates, chain facts | never takes `tx.from` as the payer; never hides a decode mismatch |
| Analyst | `agents/analyst.py` | no | Plan.query_ids, SqlExecutor, exports, reducers | `Claim[]` (proposed), `Conflict[]`, `unavailable[]`, sql.jsonl | never emits a zero for an unavailable source; never sums l30DaysUniquePayers across endpoints |
| Writer | `agents/writer.py` | yes (template fallback) | claims, conflicts, unavailable, H-MEM reinjection (actor `writer`), critic issues | `Answer {verdict, summary, sentences[], memory_ids_cited}` | never introduces a number that is not a claim value; never chooses the verdict label |
| Critic | `agents/critic.py` | second-opinion pass only (adds issues, cannot approve) | answer, claims, verifications, observations, log seqs, memories | `Critique`, confidence adjustments, memory feedback | never passes silently on an internal error (treated as `revise`, code `critic_internal`) |

Pipeline (fixed order, one `handoff` per arrow):

```
planner -> collector -> keeper(ingest pass 1) -> verifier -> keeper(ingest chain facts) -> analyst
-> [writer -> critic] x <= max_critic_rounds -> keeper(assert accepted claims as agent facts; second-pass idempotency proof)
-> hmem(feedback, mining, sleep preview) -> finalize(report.md, output.json)
```

Deadline handling: the runner checks `clock.now_ms() < deadline` before every step; Collector, Analyst and Verifier check it inside their loops. When short on time, optional steps are skipped in this fixed order, each as a `recovery{reason:"deadline_skip"}`: (1) Verifier chain calls, (2) Zetaris cross-check (claims keep `cross_checked: false`), (3) generator passes (template/rules). A step that overruns returns `degraded`; the run finishes `partial`.

Generator usage per run (max 12 calls): Planner <= 2 (one + one retry with the validator error appended), Writer <= 3 (initial + 2 rewrites), Critic <= 3 (one per round). Every call: `temperature 0`, `timeout 60 s`, prompt and response sha256 logged, text saved to `logs/<run_id>/transcripts/` and, with `--record-transcripts`, copied into `fixtures/transcripts/<example>/`.

### 6.1 Planner

- Router (deterministic, runs first): wallet regex `^0x[0-9a-fA-F]{40}$` in `focus.wallet` or in the question -> `payer_dossier`; `focus.host` or a host-looking token -> `seller_profile`; keywords `concentration|catalog|bazaar|who sells|top hosts|active share` -> `catalog_concentration`. Ambiguous and a generator is mounted -> one call (`prompts/planner.txt`, max_tokens 400) must return JSON `{playbook, hypotheses[], extra_metrics[]}` validated against `plan.schema.json`; invalid -> 1 retry with the validator message appended -> rules. No match at all -> `E_INPUT`.
- H-MEM: `hmem.query(question + " " + focus text, threshold=0.35, top_n=5, actor="planner")`; the reinjection block fills `{{hmem_reinjection}}`; memory ids go into `Plan.hmem_memory_ids`; the trace goes to `hmem.trace.jsonl` even when no generator is mounted.
- Playbook -> fetch_list and query_ids (section 11 per example; section 10.4 catalog). The `plan` event lists the exact query ids and source families before any data moves.

### 6.2 Collector

- Executes `fetch_list` in priority order via the Fetcher; per request timeout 10 s, 2 retries (0.5 s / 1.5 s) on timeout/5xx; identifying `User-Agent: x402-adoption-investigator/<version> (+<repo url>)`. _Amended 2026-10-23:_ the retries are one policy shared by every Fetcher (section 7.2); the Collector binds it to the run so each retry is a `retry` event logged by the stage that made the request (collector, or verifier for Base RPC) and every backoff is capped by the deadline.
- Writes raw bytes to `logs/<run_id>/raw/<name>.<ext>` and the parsed payload to `logs/<run_id>/exports/<name>.json` (what both SQL backends and the reducers read; what the artifact server serves).
- Live failure after retries -> `FallbackFetcher` tries the fixture with `stale: true` and `recovery{reason:"fixture_fallback"}`; no fixture -> `status: "unavailable"`, dependent metrics later go to `unavailable[]`.
- Catalog: `options.catalog = "pages"` crawls <= `budget.bazaar_pages` pages of `limit=1000` live (coverage basis "live pages (N x 1000)"); `"snapshot"` loads `cdp-discovery-catalog-compact.json.gz` (coverage basis "compact snapshot 2026-10-22", `replayed: true` even in live mode). Sample mode always uses the snapshot for catalog metrics and the committed `p0/p1` pages for page-level views.
- All mandatory sources unavailable -> `AgentResult failed` -> `E_NO_SOURCES`.

### 6.3 Keeper

- `Observation` -> `IngestObservation {externalId: "<family>:<request_key>", at: captured_at, source: Provenance{kind, at, by, url, checksum: "sha256:<bytes>", runId}, payload}`; adapters emit the write events (section 9.3).
- Pass 1 after the Collector; chain facts after the Verifier; accepted claims after the Critic as facts of `kind: "agent"` scoped to `investigation:run:<run_id>`, each with `source.url` = the claim's first evidence ref (URL, basescan or `log://`), confidence = the Critic's final confidence. The claim policy hook refuses anything else and the Keeper logs `critique{kind:"provenance_rejected"}` (if it ever fires on an accepted claim that is a builder bug; the examples test asserts it never does).
- Second pass: re-ingest the same observation list; `output.world.second_pass` must show `eventsAppended: 0, changed: false`.
- Exports: `logs/<run_id>/exports/world-{entities,relationships,facts}.json` (canonical JSON) consumed by the reducers and the Critic's recompute.

### 6.4 Verifier

- Tx sample (deterministic): most recent external sale per distinct payer, newest first, capped at `max_tx_verifications`; when `focus.wallet` is set, that payer's sales come first.
- One JSON-RPC batch: `eth_getTransactionReceipt` x k, `eth_getTransactionByHash` x k, `eth_call balanceOf(focus or top payer)`, `eth_getTransactionCount(payer)`. Decoding in `chain/decode.py`: Transfer `topic0 0xddf252ad...` -> payer `topics[1]`, seller `topics[2]`, amount `data`; AuthorizationUsed `topic0 0x98de5035...` -> payer `topics[1]`; input selector `0xe3ee160e` (transferWithAuthorization) -> from/to/value cross-check. `tx.from` is recorded as `facilitator_from`.
- Receipt row must match decoded payer (case-insensitive), seller and amount -> `verified`; mismatch -> `failed` with `reason: "decode_mismatch:<field>"` AND a Conflict `{predicate: "settlement_<field>"}`; RPC unavailable -> `unverifiable`, chain claims omitted, settlement claims keep receipts evidence with confidence capped at 0.7; sample mode: only hashes present in fixtures (today `0x62c0...58c3`) are verifiable, the rest are `unverifiable` with `reason: "fixture_miss"`, never guessed.
- Cross-checks (each a `crosscheck` event; disagreement -> Conflict with basis): receipts seller == Bazaar `payTo` for the host; Bazaar search count (capped 20) vs openapi paid ops vs Agent402 `paidToolCount` vs vet402 endpoints vs x402-trust search `total`; Bazaar `l30DaysTotalCalls` summed over the host's listings vs receipts sales in the same 30-day window (expected to disagree: different measurement basis).
- The Verifier also writes `exports/settlements.json` (rows for the `settlements` view).

### 6.5 Analyst

- `metrics.py` registry: `metric_id -> {query_id, reducer, unit, required_sources, caveat_rules, coverage_rule, statement_template, fields, sources}`. For every metric in `Plan.metrics`: run the SQL through the executor (section 10), run the reducer over the exports, compare: counts/sums exact, shares within 1e-6. Agree -> `cross_checked: true`, confidence from the registry (0.9 default); disagree -> Conflict `{predicate: "<metric>:backend_disagreement"}` and confidence 0.5; executor unavailable -> reducer value with `backend: "sqlite"` and `output.mounts.executor` says so; required source unavailable -> `unavailable[]`, no claim.
- `derivation.fields` and `derivation.sources` come from the metric registry (static per metric) so the Critic's pitfall gate is deterministic.
- Conflict composition: registry counts (q06, q12-q15) for the same host -> one Conflict `endpoint_count_for_host` with per-source values, URLs and basis strings; Bazaar quality vs receipts -> `calls_30d_for_host`; backend disagreement -> as above; ecosystem size (q08 vs API total vs q19) -> `ecosystem_size`. Explanations are rule-written strings from `metrics.py` (e.g. "Bazaar search is capped at 20 results and lists one row per resource; Agent402 counts tools including free ones (paidToolCount is the comparable figure); vet402 lists endpoints it has observed, including delisted ones; x402-trust search counts matches across its own index of 286k endpoints"). _Amended 2026-10-22:_ every vet402 row in the fixtures has `catalog_status: listed`, so "including delisted ones" was wrong; the vet402 clause now says it counts every endpoint it has observed with `catalog_status` listed, and the Analyst appends the routes that separate vet402 from the store openapi when the exports show them (in the fixtures `/api/maintenance-helper` and `/api/preparar-solicitud`, the whole 19 vs 17 difference).
- Bounds: <= 25 metrics per run, each query <= 30 s, total SQL <= 60 s.

### 6.6 Writer

- The template renderer always exists and is the sample-mode default when no transcript is recorded; it produces the same `Answer` structure from accepted claims: verdict sentence, <= 5 summary bullets, one "Where sources disagree and why" paragraph per conflict, caveats, sample-mode notice.
- Generator path: `prompts/writer.txt` receives the claims as a JSON table (id, statement, value, unit, caveats, conflict_ids), the conflicts, the H-MEM reinjection block (actor `writer`), and the previous round's critic issues; max_tokens 700. Output is parsed into sentences; each sentence is tagged with the `[cNN]` ids it contains.
- The verdict label is computed by `verdict.py` (section 6.8) and injected; the model only phrases it.

### 6.7 Critic

Pass A (claims), each check logged as a `critique` event:

- C1 evidence-present: every claim has >= 1 evidence item that resolves (url sha256 matches a `fetch` event in this run or the MANIFEST; tx is a Verifier `verified` entry; `log://` seq exists).
- C2 recompute: reducer value equals SQL value (exact for counts/sums, 1e-6 for shares); both missing -> reject.
  _Amended 2026-10-22 (docs/BUILD_LOG.md, "central claims made true"):_ either value missing or a disagreement ->
  reject (the `backend_disagreement` Conflict stays open in the answer); every registry metric has a committed
  query (q20-q22 added), and an executor that cannot build its views is replaced by SQLite for the Analyst.
  C1 also requires, for `settlements_verified` / `verified_tx`, at least one sampled settlement of the claim's own
  subject that was decoded (verified or failed); a count over unverifiable settlements is rejected.
- C3 pitfall gate (H-MEM): for every pitfall memory whose `entities` intersect `derivation.fields` or `derivation.sources`, the claim must carry the caveat code the memory names (`caveat_memory_ids[code] == memory.id`); missing -> `revise` with `memory_ids`; present -> candidate `helpful` feedback.
- C4 scope rules: `l30DaysUniquePayers` never summed across endpoints (any claim whose derivation declares a sum over that field is rejected outright); shares carry numerator and denominator; receipts-only claims carry `self_reported_receipts`; catalog-wide claims carry `coverage`.
- C5 staleness: replayed evidence older than 7 days at run time -> confidence cap 0.8 and a mandatory disclaimer sentence; `stale: true` evidence -> cap 0.7.
- C6 memory-consistency: a factual memory (mined from a previous run) about the same predicate and subject: equal value -> `helpful`; differs by > 5 % with claim confidence >= 0.9 -> `wrong`; retrieved but uncited -> `not_helpful`.
- C7 conflict citation: a claim whose predicate has an open Conflict must list the conflict id; missing -> `revise`.

Pass B (narrative): tokenize every numeric token in the summary and sentences, including formatted variants (`85.7 %`, `85.7%`, `0.044 USDC`, `44,000`, `44000`, `44 000`, `1,043,148`, `52,841,269`), and map each to an accepted claim value, a share of one, or a numerator/denominator of one; every `[cNN]` must exist and be accepted. The map is logged as `narrative_map`. Unmapped tokens or dangling citations -> `revise`; after the last round offending sentences are cut with `recovery{reason:"narrative_trimmed"}` and the template renderer fills the gap from accepted claims only.

Optional generator second opinion (`prompts/critic.txt`): returns a JSON list of unsupported statements; each becomes an issue; it can never remove issues.

Loop: `revise` -> Writer re-runs with the issues (max `max_critic_rounds`); still failing -> rejected claims move to `rejected_claims[]` with their critique trail; status `partial`.

### 6.8 Verdict rules (`agents/verdict.py`)

Labels: `adoption_real | adoption_thin | self_settled_mostly | concentrated | diffuse | insufficient_evidence`. Rules over accepted claims only (first match wins; `why` lists the claim ids used):

1. fewer than 3 accepted claims or no claim with `cross_checked: true` -> `insufficient_evidence`.
2. seller_profile: `self_settlement_share >= 0.5` -> `self_settled_mostly`; else `external_payers_total >= 10 and repeat_external_payers >= 3` -> `adoption_real`; else `adoption_thin`.
3. catalog_concentration: `top10_hosts_calls_share >= 0.5` -> `concentrated`; else `diffuse`.
4. payer_dossier: `payer_sales_total >= 2 and verified_tx >= 1` -> `adoption_real` (for that payer); `verified_tx >= 1` -> `adoption_thin`; else `insufficient_evidence`.

Confidence = min over the `why` claims' confidence.

---

## 7. Sources, fetcher and fixtures

### 7.1 Source families (all public, no keys; verified 2026-10-22 00:02-00:08 UTC)

| family | request(s) | notes |
|---|---|---|
| `receipts` | `GET https://tienda.textokit.store/receipts.json` | self-reported; payer casing mixed (lowercase it); `amountAtomic` decimal string (USDC, 6 decimals) |
| `store_openapi` | `GET https://tienda.textokit.store/openapi.json` | 17 paid ops with `x-payment-info` |
| `cdp_bazaar` | `GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources?limit=1000&offset=N` (N = 0, 1000, 2000 in live) | limit max 1000; offset snaps down to a multiple of limit; `total` moves; quality at `item.quality` (not `metadata.quality`) |
| `cdp_search` | `GET https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?urlSubstring=<host>&limit=20` | array is `resources` (not `items`); capped at 20; offset ignored |
| `cdp_catalog_snapshot` | fixture `cdp-discovery-catalog-compact.json.gz` | 28,462 items, 2,713 hosts; dated 2026-10-22 |
| `agent402` | `GET https://agent402.tools/api/index?seller=<host>`; `GET https://agent402.tools/api/route?q=<query>&include=external` | route returns top 25; `containsUntrustedContent: true` (seller-supplied text, never fed to the generator unescaped) |
| `x402_trust` | `GET https://x402-trust.com/trust/stats`, `/trust/search?q=<host>&limit=25`, `/verified.json?limit=25`, `/v1/facilitators`, `/trust/report.json`, `/trust/leaderboard?limit=25&offset=0` | never call `/v1/semantic-search` in a run (21 s); per-endpoint page is HTML only |
| `vet402` | `GET https://vet402.com/api/v1/resolve?q=<host>` | `endpoints[]` with `catalog_status` |
| `base_rpc` | `POST https://mainnet.base.org` JSON-RPC 2.0 batch | `tx.from` is the facilitator; USDC `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

### 7.2 Fetcher implementations (`sources/fetcher.py`)

```python
class LiveFetcher:      # urllib; timeout; 2 retries; gzip accepted; records sha256 of raw bytes
class FixtureFetcher:   # request_key -> fixtures/raw/<file> via MANIFEST.requests; replayed=True; captured_at from MANIFEST; latency_ms=0
class FallbackFetcher:  # LiveFetcher then FixtureFetcher with stale=True on failure; mode "live+fixture-fallback"
```

_Amended 2026-10-23:_ both LiveFetcher and FixtureFetcher run their attempts through one function,
`sources/retry.py` `call_with_retries`. Transient = HTTP 429 or 5xx, timeout, connection error; any other 4xx
(408 and 425 included) and an oversize body stop at once. At most `RunConfig.fetch_retries` retries (default 2,
`--fetch-retries`, clamped to 0-5); backoff 500 ms then 1500 ms (the last value repeats), slept for real in live
mode and advanced on the SampleClock in sample mode; a backoff that does not fit in what is left of
`budget.max_seconds` is not taken and the request gives up (`FetchError` live, `status: "unavailable"` replayed).
Retries do not count against `max_fetches`.

### 7.3 Canonical request key

`"<METHOD> <url>"` where the URL has lowercase scheme and host, no fragment, query parameters sorted by key, no trailing slash. For JSON-RPC: `"RPC <method> <sha256(canonical_json(params))[:16]>"` per batch element; a batch is answered element by element and assembled in order (fixtures hold single responses). For the snapshot: `"SNAPSHOT cdp-discovery-catalog-compact"`.

B1 adds to `fixtures/raw/MANIFEST.json` a `"requests"` map `request_key -> {file, capturedAt, sha256, http, contentType}` covering all 27 files, generated by `scripts/build_manifest_requests.py`, never by hand. The existing `sources` section stays as the human capture record.

### 7.4 Sample mode replay rules

1. `--mode sample` calls `guard.install_network_guard()` first; the `guard` line is the first `run.jsonl` event. Any socket use raises `NetworkDisabledError`, which surfaces as `E_INTERNAL` (a bug), never as a recovery.
2. Fetcher = `FixtureFetcher`; a request key with no fixture -> `status: "fixture_miss"` + `recovery{reason:"fixture_miss"}`; the family is skipped; nothing is fabricated.
3. Clock = `SampleClock(MANIFEST.fetchedAt)`; all `at` fields derive from it.
4. Executor = `SqliteExecutor` executing the committed SQL over Python-built views from the same exports.
5. Generator = `RecordedGenerator` keyed by `(prompt_id, sha256(prompt))` under `fixtures/transcripts/<example>/`; miss -> `NullGenerator` -> deterministic fallback (rules/template) with `recovery{reason:"generator_fallback", detail:"no recorded transcript"}`. A transcript file stores `{prompt_id, prompt_sha256, model, recorded_at, response}`; the prompt text is kept beside it for audit.
6. H-MEM persist dir = `logs/<run_id>/hmem/`, initialised by copying `memory/seed/`; the committed seed is never mutated by a run.
7. `output.sample_mode_notice` and `mounts` state the replay; every evidence item has `replayed: true`.
8. Base RPC: only fixture-covered calls succeed (`0x62c0...58c3` receipt and tx, balanceOf and nonce for `0x097e...475a`, blockNumber/chainId batch).
9. _Added 2026-10-23:_ transient failures are replayed only where an example scripts them: `fixtures/transient/<example>/faults.json` (example chosen like the transcripts folder) lists `{request_key, attempt, kind: http|timeout|connection_reset, http?}`; FixtureFetcher raises that failure on that attempt and serves the fixture on every other one, through the same retry function as live mode, so whether and how long to retry is decided by the policy, never by the file. Every file states `simulated: true|false`, and `output.sample_mode_notice` names it. Live mode never reads the folder. Only example 05 has a script, simulated (`fixtures/transient/README.md`).

---

## 8. Meterless H-MEM mapping (`investigator/hmem/`)

Faithful to `engines/hmem` docs (the spec) and the reference; done when `tests/test_hmem_conformance.py` replays all 13 vectors green (`rv-001/002, fd-001..005, ro-001/002, sg-001, cg-001, la-001, db-001`). The TS runner cannot load Python, so the pytest replays `vectors.json` verbatim; README says so.

### 8.1 Record schema (docs shape; snake_case in Python, camelCase on disk)

```python
MemoryType = Literal["personal", "factual", "preference", "general"]
MemoryLayer = Literal["short_term", "working", "long_term"]
MemoryOrigin = Literal["chat","import","web","decision","curiosity","goal_run","user_explicit","inference","plan_run","mission","swarm_run","tool","brain"]

@dataclass
class MemoryProvenance:
    origin: MemoryOrigin; learned_at: int; label: str; refs: dict = field(default_factory=dict)   # refs: sourceUrl?, goalRunId?, ...

@dataclass
class Entity:
    name: str; type: str

@dataclass
class MemoryRecord:
    id: str; content: str; type: MemoryType; layer: MemoryLayer
    timestamp: int; last_accessed: int; access_count: int
    confidence: float            # MANDATORY, clamp01, default 0.7
    source: str                  # MANDATORY; add() raises ValueError on empty/whitespace
    tags: list[str]; domain: Optional[str]; namespace: Optional[str]
    embedding: Optional[list[float]]; score: Optional[float]   # score is runtime only, never persisted
    provenance: Optional[MemoryProvenance]                       # mandatory for new writes made by this app
    entities: list[Entity]; related_to: list[str]
    supersedes: Optional[str]; superseded_by: Optional[str]; derived_from: list[str]
    goal_run_id: Optional[str]   # = run_id for records mined from a run
```

### 8.2 Services and contracts

- `TrustLedger(persist_dir)`: `log(memory_id, action, actor, *, previous_state=None, new_state=None, details=None) -> LedgerEntry`; `history(memory_id)`, `all()`, `stats()`. Actions: the canonical 17 (`create, read, update, delete, feedback, promote, demote, merge, conflict_detected, conflict_resolved, sleep_consolidate, sleep_archive, sleep_synthesize, dream_proposed, dream_approved, dream_rejected, restore`). Append-only `ledger.jsonl`, never pruned (no rotation implemented; documented as such).
- `MemoryStore(persist_dir, clock, ledger, embedder)`: `add(content, source, confidence=0.7, layer="short_term", type="general", tags=(), entities=(), provenance=None, derived_from=(), goal_run_id=None) -> MemoryRecord` (returns the LIVE object; writes `create` with `newState = snapshot(record)` = record minus embedding), `get`, `all`, `update` (`update` entry with previous/new), `delete(id, reason)` (`delete` entry before removal), `promote(id, layer)`, `touch(id)` (no ledger entry). Enrichment: `classify_domain` (tags `project:*`/`tech:*` first, keyword scoring tech/work/personal, else `general`), namespace `<domain>/<profile|facts|notes>`, entities cap 10, `related_to` cap 5 bidirectional, `supersedes` when cosine > 0.85 and the correction regex matches. Persist: `memories.jsonl` (one record per line, rewritten atomically on mutation).
- `MemoryRetrieval(store, ledger, embedder, clock)`: `query(text, *, threshold=0.35, top_n=5, actor, tags=(), entities=()) -> RetrievalResult{memories: [{memory, score, signals}], reinjection: str, trace: dict}`. Score = `clamp01(0.35*semantic + 0.20*keyword + 0.10*tag + 0.10*domain + 0.15*entity + 0.05*layer + 0.05*recency - 0.20*superseded) * confidence`; the threshold applies to the FINAL score; one `read` ledger entry per hit; reinjection text grouped by domain. `feedback(memory_id, kind, actor, reason)`: `helpful +0.05 and access_count += 1`, `not_helpful -0.03`, `wrong -0.20 and tag "review"` (once), clamp01, `feedback` ledger entry with previous/new state and `details {kind, delta, reason}`.
- `policy_memories(fields, sources) -> list[MemoryRecord]`: deterministic index over records tagged `pitfall` whose `entities` contain `field:<f>` or `source:<s>`; used by Critic C3 (no embedding involved, so the gate is reproducible).
- `MemoryMining(store, miner_fn=None)`: `mine(event_type, text, *, source, goal_run_id, provenance) -> list[MemoryRecord]`; `miner_fn(prompt) -> str` must return a strict JSON array (fence-stripping tolerant parser); the default model-free fallback extracts one `factual` record per accepted claim statement (sample mode), which IS the spec's default when no generator is given.
- `Sleep`: `preview() -> {to_consolidate, to_archive, to_synthesize}` (no mutation), `execute(preview) -> {backup_id, consolidated, archived, synthesized, action_log}` (derived records never archived; backup under `persist_dir/backups/<backup_id>/`), `restore(backup_id)` writes a `restore` entry.
- `Conflicts`: `scan() -> list[ConflictRecord]`, `auto_resolve(c) -> "resolved"|"queued"` with the 0.70 confidence-gap gate; the loser gets `superseded_by`; both records remain.
- `Dreaming`: `dream() -> proposals` (nothing materializes), `approve(id) -> MemoryRecord` (layer `long_term`, `derived_from` set), `reject(id)`.
- `HMEM(clock, persist_dir, miner_fn=None)` facade with `.add/.query/.feedback/.policy_memories` and `.store/.ledger/.retrieval/.sleep/.conflicts/.dreaming`.
- Embedder: deterministic FNV-1a 64-dim hash embedder (as in the reference); no provider.

### 8.3 How the investigator uses H-MEM (visible in logs and output)

| moment | actor | call | written where |
|---|---|---|---|
| plan | planner | `query(question+focus, 0.35, 5)` -> reinjection block in the prompt (or logged only, when rules plan) | `hmem.trace.jsonl`, `memory` event, `Plan.hmem_memory_ids` |
| critique | critic | `policy_memories(fields, sources)` for C3; `query(claim.statement, 0.35, 3)` for factual memories (C6) | `critique` events carry `memory_ids` |
| write | writer | `query(question, 0.35, 5)` -> reinjection; cited ids -> `Answer.memory_ids_cited` | trace |
| finalize | critic | `feedback()` per memory per the C3/C6 outcomes (one entry per memory per run; highest-severity kind wins: wrong > helpful > not_helpful) | `ledger.jsonl`, `output.memory.feedback` |
| finalize | runner | `mine("plan_completion", summary of accepted claims, source="run:<run_id>", provenance{origin:"goal_run", label: question, refs{goalRunId}})` -> factual records (layer `short_term`, confidence 0.8, entities = subjects) | `output.memory.mined` |
| finalize | runner | `sleep.preview()` logged; `execute()` only in live mode with `--hmem-sleep` | `memory` event |

Seeded pitfall memories (`memory/seed/memories.jsonl`; type `general`, layer `long_term`, confidence 0.95, source `"seed:spec-2026-10-22"`, tag `pitfall`, provenance `{origin:"user_explicit", label:"SPEC.md section 8.3"}`):

| id | entities | content | caveat code it requires |
|---|---|---|---|
| mem-seed-001 | `field:l30DaysUniquePayers` | Bazaar `l30DaysUniquePayers` is per endpoint and cannot be deduplicated across endpoints; never sum it, report the per-endpoint max or a range. | `unique_payers_not_dedupable` |
| mem-seed-002 | `source:cdp_search` | CDP discovery search is capped at 20 results and ignores offset; a count of 20 is a floor, not a total. | `search_limit_20` |
| mem-seed-003 | `source:receipts` | `receipts.json` is self-reported by the seller; rows with `ownWallet: true` are self-settlement, not external demand. | `self_reported_receipts` |
| mem-seed-004 | `source:x402_trust` | x402-trust ecosystem stats are third-party 30-day aggregates across chains; they cannot be reconciled with Bazaar per-endpoint counters or with one seller's receipts. | `third_party_aggregate` |
| mem-seed-005 | `source:base_rpc` | On an x402 settlement `tx.from` is the facilitator; the payer and seller come from the Transfer log. | `payer_from_transfer_log` |
| mem-seed-006 | `source:cdp_catalog_snapshot` | The catalog snapshot is a dated capture (2026-10-22); the live total moves by the minute; claims must carry coverage. | `snapshot_dated` |
| mem-seed-007 | `metric:bazaar_calls_30d_for_host`, `metric:external_sales_30d` (_amended 2026-10-22_: was `field:l30DaysTotalCalls`, `source:receipts`, which made every receipts claim carry a caveat about a comparison it does not make) | Bazaar 30-day call counters and receipts are different windows and bases; compare only inside a Conflict with the basis stated. | `basis_mismatch` |

Live mode persists `memory/state/` across runs (gitignored); sample mode copies the seed into the run dir, so examples are reproducible and the committed seed never changes.

---

## 9. Meterless World Model mapping (`investigator/worldmodel/`)

Faithful Python port of `engines/world-model/reference/src` (types, stableIds, resolve, store, ingest, index); done when `tests/test_worldmodel_conformance.py` is green for the 8 checks: `sid-001`, `sid-002`, `band-001`, `prov-001`, `idem-001`, `idem-002`, `bitemp-001`, `audit-001`.

### 9.1 Record schemas (wire keys camelCase, exactly the spec)

- `Provenance {kind: str, at: ISO, by?: str, url?: str, checksum?: "sha256:<hex>", runId?: str}`. Kinds used: `registry | receipts | chain | agent | manual`. Missing `kind` or `at` -> `ValueError("World Model: every write carries provenance (kind + at). No provenance, no write.")`.
- `Entity {type, id, attrs, schemaVersion: 1, source, createdAt, updatedAt, name?, aliases: [], state: emerging|active|archived}`.
- `Context {type, id, attrs, parent?, startedAt?, endedAt?, schemaVersion: 1, source, createdAt, updatedAt}`.
- `Relationship {id, from, type, to, context?, attrs?, validFrom?, validTo?, schemaVersion: 1, source, createdAt, supersededBy?}`.
- `Fact {id, about, predicate, value, confidence (default 0.5), context?, source, assertedAt, supersededBy?}`.
- `Event {id: "evt_<seq>", seq, kind: upsertEntity|upsertContext|relate|assertFact|snapshot|tombstone|alias|view-rebuild, payload, schemaVersion: 1, at}` — one per line in `world.events.jsonl`.
- `AuditEntry {seq, at, kind, actor, summary, eventId?, runId?}` — `world.audit.jsonl`, strictly increasing seq, one per mutation.
- `ObservationRecord {id: "obs_<sha256(externalId)[:12]>", externalId, checksum, at, syncedRunId, stale}` — `world.observations.jsonl`.
- `IngestReport {runId: "run_<sha256(canonical_json(sorted externalIds))[:12]>", processed, skippedUnchanged, eventsAppended, changed, stepTimingsMs}`.

Stable ids (`stable_ids.py`, exact formulas): `sha256_hex`; `normalize_name` (NFKD, strip combining marks, strip U+200B..U+200D and U+FEFF, lower, collapse whitespace, strip); `canonical_json` (drop None, sort keys, compact separators, `ensure_ascii=False`); `external_id(type, system, id) = "type:system:id"`; `entity_id_from_name = "ent_" + sha256(f"{type}:{normalize_name(name)}")[:16]`; `context_id_from_attrs = "ctx_" + sha256(f"{type}:{canonical_json(attrs)}:{parent or ''}")[:16]`; `relationship_id = "rel_" + sha256(f"{from}|{type}|{to}|{context or ''}|{valid_from or ''}")[:16]`; `fact_id = "fact_" + sha256(f"{about}|{predicate}|{canonical_json(value)}|{asserted_at}|{source_kind}|{source_url or ''}")[:16]`. Resolve: Levenshtein similarity with bands 0.85 auto-merge / [0.82, 0.85) review / else new; alias events; merge queue persisted as `world.merge-queue.json`.

Fold rules: entity upsert shallow-merges attrs; tombstone sets `state: archived`; relate is a no-op on an existing id and, for types NOT in `MULTI_VALUED_EDGE_TYPES`, closes open edges with the same `(from, type, context)` and a different `to` (`validTo = new.validFrom`, `supersededBy = new.id`); assertFact dedupes by id, picks a winner by policy (`most-recent-wins` default; `highest-confidence-wins`; `source-priority`), sets `supersededBy` on losers, writes `entity.attrs[predicate] = winner.value`, auto-upserts a missing subject with `state: emerging`.

### 9.2 Domain mapping (`domain.py`)

- Entities: `seller:host:<host>`, `endpoint:url:<canonical url>`, `wallet:base:<0x lowercase>` (`wallet:solana:<base58>` for Solana payTo), `facilitator:name:<slug>`, `registry:name:{cdp-bazaar|agent402|x402-trust|vet402}`.
- Contexts: `day:utc:<YYYY-MM-DD>`, `investigation:run:<run_id>` (attrs: input sha256, mode, status), optional `step:run:<run_id>/<n>-<agent>`.
- Relationships (ALL declared in `MULTI_VALUED_EDGE_TYPES` so payment history is never closed): `paid` (wallet -> endpoint, one per settlement, `validFrom` = sale `at`, context day, attrs tx/amountAtomic/asset/network/facilitator/x402Version/ownWallet/service), `sells` (seller -> endpoint), `pays_to` (endpoint -> wallet, attrs network/asset/amount/scheme), `listed_on` (endpoint -> registry, context day, registry attrs), `verified_by` (endpoint|seller -> registry, attrs status/grade/score/probedAt), `settled_via` (endpoint -> facilitator).
- Facts: registry predicates verbatim (`l30DaysTotalCalls`, `l30DaysUniquePayers`, `lastCalledAt`, `toolCount`, `paidToolCount`, `paywall.ok`, `health`, `routable`, `providerVerified`, `hostScore`, `catalog_status`), chain predicates (`onchain_tx`, `usdc_balance_atomic`, `tx_count`), investigator claims (`external_payers_total`, `external_sales_total`, `self_settlement_share`, `repeat_external_payers`, `top_payer_share`, `endpoint_count_for_host`, `verdict`, ...) with `kind: "agent"`, scoped to the investigation context.
- Claim policy hook (`domain.claim_policy(fact)`): `kind == "agent"` requires `source.url` matching `^https://` or `^log://<run_id>#\d+$` AND `source.runId`; otherwise the engine raises and the Keeper logs `critique{kind:"provenance_rejected"}`.
- Provenance in sample mode keeps the ORIGINAL kind and url; `checksum` = sha256 of the fixture bytes; `by = "fixture:fixtures/raw/<file>"`; `at` = `MANIFEST.requests[key].capturedAt`. A live crawl and its fixture replay fold to byte-identical world state when the bytes are identical, so the idempotency check doubles as the sample-vs-live proof.

### 9.3 Adapter contracts

`adapter(family)(observation: IngestObservation) -> list[WriteEvent]` where `WriteEvent` is one of `UpsertEntity | UpsertContext | Relate | AssertFact` (dataclasses in `worldmodel/types.py`). Unknown payload shape -> `[]` and an `adapter_skip` note listing the top-level keys seen (visible, never a silent zero). Expected counts over the fixtures (asserted by `tests/test_adapters.py` by reading the fixture files, not by typed constants): receipts -> 1 seller, 3 own wallets + 4 external wallets, 77 `paid` edges; cdp_search -> 17 endpoints `listed_on cdp-bazaar` with 3 quality facts each; vet402 -> 19 `listed_on vet402`; agent402 -> facts `toolCount`, `paidToolCount`, `paywall.ok`, `health`, `routable`, `routerDispatchReason`; base_rpc -> 1 `onchain_tx`, 1 `usdc_balance_atomic`, 1 `tx_count`.

### 9.4 Views (`views.py`)

`graph()`, `facts(about=None, predicate=None, at=None)`, `snapshot(at)` (point-in-time fold respecting validity), `timeline(entity_id)`, and the domain reducer views used by `agents/reducers.py`: `sales_by_payer(host)`, `listings_by_host(registry)`, `external_settlements(host)`, `registry_counts(host)`, `catalog_hosts()`. Exports: `exports/world-entities.json`, `world-relationships.json`, `world-facts.json` (canonical JSON, deterministic order).

Dropped on purpose (documented in `docs/METERLESS.md`): stream-cluster cycle, embedding search view, migrations/replay engine, control-plane HTML, write coalescing/queues (single process + one lock), mutable `entity.aggregates`.

---

## 10. Zetaris integration (`investigator/zetaris/`)

Verified against the local instance (Zetaris 2.4.3.1, build cb88df0b; containers ui:3000, api:8888, driver:10000, postgres; all healthy) on 2026-10-22. Everything below is what the code does, in order.

### 10.1 REST calls (exact)

Headers on every call after login: `Authorization: Bearer <idToken>`, `X-Org-ID: <orgId>` (integer string from the login response; never hardcoded), `X-Request-ID: <fresh uuid4>` (a non-UUID or a wrong org id gives HTTP 400). Token lifetime 900 s -> re-login once on 401.

| step | call | body / notes | response |
|---|---|---|---|
| login | `POST {ZETARIS_BASE_URL}/api/auth/login` | `{"username": ZETARIS_USERNAME, "password": ZETARIS_PASSWORD}` + `X-Request-ID` | `{idToken, refreshToken, orgId}` |
| version | run-query `SHOW SERVER VERSION` | | `2.4.3.1` -> `mounts.zetaris_version` |
| db exists? | `GET /api/proxy/lightning-database/databases` | | `[{databaseId, name, description, tableCount}]`; there is no `IF NOT EXISTS` |
| create db | run-query `CREATE LIGHTNING DATABASE X402_<RUN8> DESCRIBE BY "x402 adoption investigator run <run_id>"` | `RUN8` = first 8 hex of sha256(run_id), uppercased; DESCRIBE BY allows letters, digits, spaces and `_ . - , ;` | headers `["Result"]`, 0 rows |
| rest table | run-query `CREATE LIGHTNING REST TABLE <name> FROM X402_<RUN8> REQUEST(endpoint "<url>", method "get", response_type "json", http_encoding "URLENCODED") HEADER (user-agent "x402-adoption-investigator") BODY ()` | CREATE fetches the URL once for schema inference; one table = one URL | `Result` |
| cache | run-query `CACHE TABLE X402_<RUN8>.<name>` | every uncached SELECT re-fetches the URL | `Result` |
| container exists? | `GET /api/proxy/view/schema-store-view-containers` | | `[{containerId, name, viewCount, description}]` |
| create container | run-query `CREATE SCHEMASTORE CONTAINER x402_<run8>` | | `Result` |
| create view | run-query `CREATE OR REPLACE SCHEMASTORE VIEW <view> WITH CONTAINER x402_<run8> AS SELECT ... FROM X402_<RUN8>.<table> LATERAL VIEW explode(...) AS ...` | the ONLY place Spark/JSON syntax appears | `Result` |
| analytics | `POST /api/proxy/sql-editor/sqls/run-query` `{"sql": "<one statement, no trailing semicolon>", "limit": 1000}` | all cells come back as strings -> cast per `.meta.json` | `{headers, data, total, timeUsed}` |
| big results | `POST /api/proxy/query/sql/start {"select": "...", "pageLimit": 100}` -> `GET /api/proxy/query/sql/page?queryToken=...&pageLimit=100&pageNumber=k` -> `DELETE /api/proxy/query/sql/close/{queryToken}` | only when `.meta.json.expected_rows_bound > 1000` (no catalog query does today) | object rows, still strings |
| cleanup | run-query `UNCACHE TABLE ...`, `DROP SCHEMASTORE VIEW ...`, `DELETE /api/proxy/view/schema-store-view-containers/{containerId}` (empty container), `DROP LIGHTNING TABLE ...`, `DROP LIGHTNING DATABASE X402_<RUN8>` | skipped with `--keep-zetaris-objects`; `scripts/zetaris_cleanup.py` removes leftovers by prefix | |

Errors: HTTP 400 `{"msg": "..."}` (e.g. `LightningDdlParseException ... expecting 'METADATA'` when Spark syntax lands in the wrong place) is logged verbatim in `zetaris.jsonl`; that single query is re-run on SQLite with `fallback_reason` and `backend: "sqlite-fallback"`; login/connect failure -> `recovery{reason:"zetaris_unreachable"}` and the whole run uses SQLite (`mounts.executor = "sqlite"`, status `partial` when `--mode live`). `--require-zetaris` turns any of these into `E_ZETARIS_REQUIRED`. Port 8889 is never called. The direct API at `:8888/api/v1.0/<path>` accepts the same JWT and is supported as `ZETARIS_API_STYLE=direct` (default `proxy`); `ZETARIS_API_KEY` is an optional alternative to login.

### 10.2 Where the data comes from: `captured` (default) vs `direct`

Zetaris REST tables are GET-only and re-fetch per uncached SELECT, while the investigator must compute on exactly the bytes the Collector hashed (otherwise the Analyst's SQL and the reducer disagree because the Bazaar moves by the minute, and the JSON-RPC settlements are POST and cannot be a REST table at all). Therefore:

- `zetaris_source: "captured"` (default): the Collector's `logs/<run_id>/exports/*.json` are served read-only by `artifact_server.py` (stdlib `http.server` in a thread, bound to `0.0.0.0:<random free port>`, path prefix `/<run_token>/` with a 32-hex token, GET only, no directory listing, stopped at finalize) and each REST table's endpoint is `http://host.docker.internal:<port>/<run_token>/<name>.json`. This covers every family including the Base RPC settlements (`exports/settlements.json`, written by the Verifier) and the catalog snapshot, and the SQL sha256 chain matches the evidence sha256 chain. The Zetaris reader verified that the driver container reaches `host.docker.internal:<port>`; `scripts/zetaris_preflight.py` re-verifies it on day 1 (creates a one-row REST table against the artifact server and SELECTs it). If that preflight fails on the demo machine, the run falls back to `direct` for receipts/Bazaar and SQLite for the rest (`recovery{reason:"artifact_server_unreachable"}`), and README states exactly that.
- `zetaris_source: "direct"` (demo flag): registers the public URLs directly for `receipts_raw`, `bazaar_page0` and `bazaar_search_<host>` (the three tables verified live by the reader), everything else via the artifact server. Because Zetaris fetches its own copy, the cross-check tolerates drift: a mismatch is recorded as a Conflict with basis `"two fetches N s apart"` rather than a rejection, and the claim keeps `backend: "zetaris"`, `cross_checked: false`. The catalog is never registered directly (28k items cannot be paginated through REST tables: one URL per table, limit max 1000).

`sql.jsonl` lines carry `tables:[{name, source_url}]` so the whole SQL story is readable from `logs/` alone; the `plan` event lists query ids and source families before any data moves.

### 10.3 View layer (same names and columns on both backends; `sql/views/VIEWS.md` is the source of truth)

| view | columns | Zetaris definition (`views_zetaris.py`) | SQLite (`views_sqlite.py`) |
|---|---|---|---|
| `sales` | seller, agent_id, settled_at, payer (lowercased), own_wallet (0/1), amount_atomic BIGINT, tx_hash, x402_version, service, resource, facilitator | `FROM X402_<RUN8>.receipts_raw LATERAL VIEW explode(sales) AS s` (+ `lower(s.payer)`, `CAST(s.amountAtomic AS BIGINT)`) | rows from `exports/store_receipts.json` |
| `purchases` | at, url, price_usd, outcome, tx_hash | `explode(purchases)` | same |
| `bazaar_resources` | resource, host, service_name, resource_type, last_updated, calls_30d, payers_30d, last_called_at, pay_to, network, asset, amount_atomic, scheme, page | `explode(items) AS i LATERAL VIEW explode(i.accepts) AS a` over `bazaar_page<N>` tables, UNION ALL | pages p0..pN |
| `bazaar_search` | same as `bazaar_resources` minus page | `explode(resources)` over `bazaar_search_<host>` | search export |
| `catalog_resources` | resource, host, calls_30d, payers_30d, last_called_at, pay_to, network, asset, amount_atomic | `explode(items)` over `catalog_snapshot` (artifact server); size to be verified day 2; on failure the catalog family runs SQLite with `fallback_reason: "catalog_too_large_for_rest_table"` | snapshot (decompressed) |
| `openapi_paid_ops` | path, operation_id, price_usd, network, asset, pay_to | over `exports/openapi_paid_ops.json` (Python-flattened by the Collector because OpenAPI paths are map keys) | same export |
| `agent402_tools` | route, slug, paid, price, declared, last_verified_at | `explode(tools)` over `agent402_index` | export |
| `agent402_seller` | origin, tool_count, paid_tool_count, paywall_ok, health, routable, router_dispatch_reason | top-level fields | export |
| `vet402_endpoints` | canonical_url, method, payee_id, catalog_status, first_seen, last_seen | `explode(endpoints)` | export |
| `trust_search` | resource, host, delisted, score, grade, provider_verified, total_reported | `explode(results)` + `total` | export |
| `trust_stats` | endpoints_listed, endpoints_active, settlements_30d, settled_volume_usd_30d, distinct_payers_30d, distinct_receivers_30d | top-level fields | export |
| `settlements` | tx_hash, payer, seller, amount_atomic, block_number, block_time, facilitator_from, status | REST table over `exports/settlements.json` (artifact server) | export |

Host extraction happens in the view layer: `parse_url(resource, 'HOST')` on Zetaris, `urllib.parse` when building SQLite rows. The `host` column exists in both so analytics SQL never parses URLs.

### 10.4 Analytics SQL catalog (ANSI subset; one statement, no semicolon; parameters `:host`, `:wallet`, `:since` bound by the catalog loader with quoting rules, never string-formatted by agents)

Each `qNN.sql` has a `qNN.meta.json`: `{"id", "params": ["host"], "columns": {"n_sales": "int", "usdc_atomic": "int", "share": "float", "payer": "str"}, "expected_rows_bound": 100, "sources": ["receipts"], "fields": ["amountAtomic", "ownWallet"], "ansi_only": true}`. The loader computes `sql_sha256` over the file bytes; the Analyst logs it in every claim derivation.

| id | computes | real result via Zetaris 2026-10-22 (reader's run; `fixtures/zetaris/` holds the full rows) |
|---|---|---|
| q01_sales_by_wallet_class | `SELECT own_wallet, COUNT(*) AS n_sales, COUNT(DISTINCT payer) AS n_payers, SUM(amount_atomic) AS usdc_atomic FROM sales GROUP BY own_wallet` | own: 56 / 3 / 354000; external: 21 / 4 / 44000 |
| q02_external_payers | per external payer: n_sales, usdc_atomic, first_at, last_at, n_services | 0xec2a... 18 sales; three payers with 1 |
| q03_repeat_external_payers | external payers `HAVING COUNT(*) >= 2` | 1 of 4 |
| q04_top_payer_share | top external payer sales and external total | 18 / 21 |
| q05_external_sales_by_service | external sales grouped by service | cuit-ar 3, x402-ping 2, normativa-ar 2, ..., x402-adoption 1 |
| q06_bazaar_host_listings | `COUNT(*)`, `COUNT(DISTINCT pay_to)`, `SUM(calls_30d)`, `MAX(payers_30d)` FROM bazaar_search WHERE host = :host | 17 listings, 1 payTo; payers NOT summed |
| q07_bazaar_payto_vs_receipts_seller | join `bazaar_search.pay_to = sales.seller` (lowercased both sides) | 17 rows match 0x6b5a...a6e1 |
| q08_catalog_size | `COUNT(*)`, `COUNT(DISTINCT host)`, `COUNT(DISTINCT pay_to)`, `SUM(calls_30d)` FROM catalog_resources | 28462 / 2713 / 2177 / 824796 (snapshot) |
| q09_catalog_top_hosts_by_calls | top 10 hosts by `SUM(calls_30d)` with the snapshot total for the share | www.ax1.vc 243580 first |
| q10_catalog_active_share | resources with calls_30d > 0 and all | 28347 / 28462 |
| q11_catalog_payto_concentration | top payTo share of calls (snapshot, and page 0 separately) | page 0: leader 52.56 % of the page's calls |
| q12_registry_count_openapi | `COUNT(*) FROM openapi_paid_ops` | 17 |
| q13_registry_count_agent402 | `paid_tool_count, tool_count FROM agent402_seller` | 17 / 34 |
| q14_registry_count_vet402 | `COUNT(*) FROM vet402_endpoints WHERE catalog_status = 'listed'` | 19 |
| q15_registry_count_trust_search | `MAX(total_reported), COUNT(*) FROM trust_search` | 36 / 25 returned |
| q16_settlements_vs_receipts | join `settlements.tx_hash = sales.tx_hash`, flag payer/amount/seller equality | 1 row in sample (0x62c0...), all equal |
| q17_payer_dossier | sales for `:wallet`: n, atomic, first/last, services | 0x097e...: 1 sale, 1000 atomic |
| q18_external_sales_by_day | `SUBSTR(settled_at, 1, 10)` groups | 2026-10-20, 2026-10-21 |
| q19_trust_ecosystem_stats | `SELECT * FROM trust_stats` | 286358 listed, 2661042 settlements 30d, 32625 distinct payers 30d |

Every metric in `metrics.py` names one of these ids and one reducer in `reducers.py`. `tests/test_sql_dual_backend.py` runs all 19 on `SqliteExecutor` over the fixtures and compares with `fixtures/zetaris/<qNN>.json` (typed result sets recorded by `scripts/record_zetaris_results.py` from the live instance over the same exports) and asserts equal typed rows; that test is also the Zetaris-flakiness guard. A lint test asserts no `explode|from_json|LATERAL|parse_url|get_json_object` token appears under `sql/analytics/`.

### 10.5 Fallback matrix

| condition | behaviour | output marks |
|---|---|---|
| sample mode | never touches Zetaris | `mounts.executor: "sqlite"` |
| live, login/connect fails | SQLite for the whole run | `recovery{zetaris_unreachable}`, status `partial` |
| live, one query HTTP 400 | that query on SQLite | `backend: "sqlite-fallback"`, `fallback_reason` |
| live, 401 | re-login once, then as above | `zetaris.jsonl` |
| live, artifact server unreachable from the driver | `direct` for receipts/Bazaar, SQLite for the rest | `mounts.zetaris_source: "direct"` + `recovery{artifact_server_unreachable}` |
| live, catalog snapshot too large for a REST table | catalog family on SQLite | `fallback_reason: "catalog_too_large_for_rest_table"` |
| `--require-zetaris` | any of the above -> `E_ZETARIS_REQUIRED` | error object |

---

## 11. The three committed examples

Each `examples/<name>/` holds `input.json`, `output.json`, `report.md`, `run.jsonl`, `sql.jsonl`, `hmem.trace.jsonl`, all produced by `scripts/regen_examples.py` in sample mode (never edited by hand). `examples/live-trace/<run_id>/` holds the same questions run live on the demo machine (`--mode live --require-zetaris --zetaris-source captured`), labelled live in its own `README.md`, so judges can diff live vs replay instead of finding a mismatch.

Expected key claims below are acceptance values derived from the fixtures; `tests/test_examples_reproducible.py` compares the committed output byte for byte and `tests/test_run_contract.py` checks the presence and type of these claims, reading expected values from the fixture files rather than from typed constants.

### 01 — seller audit (`run_id: ex01-seller-audit`)
Input: question "How real is the adoption of tienda.textokit.store? Who pays, how much, do they come back, and why do registries disagree about it?", focus host `tienda.textokit.store`, catalog `snapshot`.
Playbook `seller_profile`. Sources: receipts, store_openapi, cdp_search, agent402 (index + route), x402_trust (search, verified, stats), vet402, base_rpc (1 tx verifiable in sample). Queries: q01-q07, q12-q16, q18.
Claims: sales total 77; own-wallet sales 56 from 3 wallets (354,000 atomic); external sales 21 from 4 payers (44,000 atomic = 0.044 USDC); self-settlement share 56/77 = 72.7 %; repeat external payers 1 of 4; top payer share 18/21 = 85.7 % (`0xec2abd...d034`); external window 2026-10-20T04:51Z..2026-10-21T21:18Z; Bazaar listings for the host 17 with payTo `0x6b5a...a6e1` (caveat `search_limit_20`, mem-seed-002); Bazaar calls_30d summed over the listings vs receipts sales (Conflict k02 `calls_30d_for_host`, basis mismatch, mem-seed-007); one settlement verified on chain (`0x62c0...58c3`, payer `0x097e...475a`, 1,000 atomic, block 52,841,269; caveat `payer_from_transfer_log`, mem-seed-005); Agent402 paywall ok, routable, routerDispatchReason `settlement_self_funded`; x402-trust providerVerified true.
Conflict k01 `endpoint_count_for_host`: Bazaar search 17 / openapi 17 / Agent402 17 paid of 34 / vet402 19 / x402-trust search 36, with the explanation of each basis.
Verdict: `self_settled_mostly` (self-settlement share >= 0.5). _Amended 2026-10-22 (review fixes, docs/BUILD_LOG.md):_ the critic trail is NOT required to show a staged `revise` or a `retry`. The Analyst applies the same H-MEM policy index the Critic reads, so a clean round 1 is the expected trail; C3 fires only when the two disagree, which the unit tests exercise. Nothing is recorded "without the caveat list" to make a loop visible; what memory changes is measured by `scripts/memory_ablation.py` and committed under `examples/memory-ablation/`.

### 02 — catalog concentration (`run_id: ex02-catalog-concentration`)
Input: question "Who sells on the CDP Bazaar and how concentrated are the paid calls? What share of listed endpoints saw any call in the last 30 days?", focus query "cdp bazaar", catalog `snapshot`.
Playbook `catalog_concentration`. Sources: cdp_catalog_snapshot, cdp_bazaar pages p0/p1 (page-level view), x402_trust stats/leaderboard/facilitators (for the Conflict on ecosystem size). Queries: q08-q11, q19.
Claims (each with `coverage {items_seen: 28462, total_reported: 28472, share: 0.9996, basis: "compact snapshot 2026-10-22"}`): resources 28,462; hosts 2,713; distinct payTo 2,177; sum calls_30d 824,796; active share 28,347/28,462 = 99.6 %; top host www.ax1.vc 243,580 calls = 29.5 % of the snapshot; top-10 hosts share (computed by the run); top hosts by listings (saylorinnovations.com 1,568; agentshelf.syntexa.ch 1,236; dns.intel.rallylive.ca 694; agent402.tools 613); unique payers reported per endpoint only (caveat `unique_payers_not_dedupable`, mem-seed-001). _Amended 2026-10-22:_ the earlier plan to have the Analyst "deliberately propose" a claim that sums `l30DaysUniquePayers` so the Critic could reject it was a hardcoded demo (rule 2 of `.cursor/rules/project.mdc`) and was removed; the registry carries no such metric, example 02 is `ok`, and the C4 rejection path is proven in `tests/test_run_contract.py` with a metric registered inside the test only.
Conflict k01 `ecosystem_size`: Bazaar snapshot 28,462 resources / API total 28,472 / x402-trust `endpointsListed` 286,358 (`endpointsActive` 210,375) with the explanation (different indexes, 30-day probe window, delisted included).
Verdict: `concentrated` or `diffuse` per the top-10 share computed by the run (not typed here).

### 03 — payer dossier with on-chain verification (`run_id: ex03-payer-dossier`)
Input: question "What do we know about the payer 0x097e3F339D0b023605e12A6B81E2d6Cb7571475a: what it bought from tienda.textokit.store, and can the settlement be verified on Base?", focus wallet `0x097e3f339d0b023605e12a6b81e2d6cb7571475a`, host `tienda.textokit.store`.
Playbook `payer_dossier`. Sources: receipts, cdp_search, base_rpc (receipt, tx, balanceOf, nonce). Queries: q16, q17, q06.
Claims: 1 purchase (service as recorded in receipts), 1,000 atomic, at the receipts timestamp; settlement verified: Transfer-log payer = receipt payer, seller `0x6b5a...a6e1`, amount 1,000, block 52,841,269, facilitator_from `0x8f5c...e531`, basescan URL; USDC balance 1,043,148 atomic (1.043148 USDC) at the fixture capture; nonce 24; `receipt_match: true` (q16).
Verdict: `adoption_thin` (1 sale, 1 verified tx). Shows the Verifier path end to end and `unverifiable: fixture_miss` for any other tx the plan would have sampled.

_Amended 2026-10-23:_ two more examples were committed after this section was written: `04-payer-dossier-unverifiable` (`run_id: ex04-unverified`, see the README) and `05-payer-dossier-retry` (`run_id: ex05-retry`): example 03's input replayed with the simulated faults of `fixtures/transient/05-payer-dossier-retry/faults.json` (attempt 1 of the store's `receipts.json` answers 503, attempt 1 of the Base RPC receipt of `0x62c0...58c3` answers 429). Expected: two `retry` events (stages collector and verifier), each followed by the `fetch` of the same request key with `attempts: 2`, status `ok`, and claims, verdict and answer identical to example 03 (`tests/test_retry.py`).

---

## 12. Build order and ownership (4 parallel builders + integrator)

Day 0 (integrator, before builders start): `investigator/contracts.py`, `clock.py`, `eventlog.py`, `errors.py`, `config.py`, `schemas/*.json`, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`, `LICENSE`, this SPEC, README skeleton, `.cursor/rules`, `tests/conftest.py` (fixture paths, sample clock), `memory/seed/*` (from section 8.3), `scripts/one_command.*`. Builders import `contracts.py` and never edit it; interface change requests go to the integrator, who records them in `docs/BUILD_LOG.md`.

| builder | owns (disjoint) | definition of done |
|---|---|---|
| B1 collector + sources + chain | `investigator/sources/*`, `investigator/chain/*`, `investigator/agents/collector.py`, `investigator/agents/verifier.py`, `fixtures/raw/MANIFEST.json` (requests map only), `scripts/build_manifest_requests.py`, `tests/test_decode.py`, `tests/test_fetcher.py`, `tests/test_sample_offline.py` | FixtureFetcher resolves all 27 fixtures; LiveFetcher with retries; FallbackFetcher stale flag; RPC batch + decoding green against the fixture; Verifier yields `verified` for 0x62c0... and `fixture_miss` for others; `exports/settlements.json`; guard test green |
| B2 Meterless engines | `investigator/worldmodel/**`, `investigator/hmem/**`, `investigator/agents/keeper.py`, `tests/test_worldmodel_conformance.py`, `tests/test_hmem_conformance.py`, `tests/vectors/`, `tests/test_adapters.py`, `scripts/assert_idempotent.py`, `scripts/hmem_dream.py`, `memory/seed/*` content review, `docs/METERLESS.md` | 8/8 World Model checks and 13/13 H-MEM vectors green; adapters produce the section 9.3 counts; second pass `changed: false`; claim policy hook rejects a URL-less agent fact; seed memories load and `policy_memories()` returns the right ids for each field/source |
| B3 Zetaris layer | `investigator/zetaris/**`, `investigator/sql/**`, `fixtures/zetaris/*`, `scripts/zetaris_preflight.py`, `scripts/zetaris_cleanup.py`, `scripts/record_zetaris_results.py`, `tests/test_sql_dual_backend.py`, `docs/ZETARIS.md` | preflight passes (login, version, artifact server reachable from the driver, one REST table over it); all views created on Zetaris and SQLite; 19 queries equal rows on both backends; cleanup leaves no objects; `--require-zetaris` error path; ANSI lint test |
| B4 agents + run.py + report | `run.py`, `investigator/runner.py`, `investigator/generator.py`, `investigator/guard.py`, `investigator/agents/{planner,analyst,writer,critic,metrics,reducers,verdict,report}.py`, `investigator/agents/prompts/*`, `examples/**` (only via `scripts/regen_examples.py`), `scripts/regen_examples.py`, `scripts/record_transcripts.py`, `tests/test_critic.py`, `tests/test_run_contract.py`, `tests/test_examples_reproducible.py`, `docs/DEMO_SCRIPT.md` | `python run.py --input examples/01.../input.json` runs end to end in sample mode (against B2/B3 interfaces, stubbed until merged); Critic C1-C7 + pass B with the logged token map; verdict rules; report renderer; error codes and the partial rule tested |

Order inside the window:
- Day 1 (22/10): integrator day-0 files by 02:00 UTC; B1 fetchers + manifest requests; B2 World Model core + conformance; B3 client + preflight (artifact server go/no-go by end of day); B4 runner skeleton, eventlog usage, planner rules, template writer, Critic C1/C2/C7.
- Day 2 (23/10): B1 chain decode + verifier; B2 H-MEM core + conformance + adapters; B3 views on both backends + first 10 queries + dual test; B4 analyst + metrics + reducers + Critic pass B + pitfall gate using B2's `policy_memories`.
- Day 3 (24/10): integration 1: end-to-end sample run of ex01 with real modules; B3 remaining queries + recorded Zetaris result sets; B2 keeper second pass + claim assertion; B4 writer generator path + transcripts recorded live (needs `OPENAI_API_KEY`, Martin's env) + examples 02/03.
- Day 4 (25/10): live runs through Zetaris with `--require-zetaris`; `examples/live-trace/`; README full; docs; Dockerfile verified; `--serve`.
- Day 5 (26/10): freeze; regen examples; full test suite; demo video per `docs/DEMO_SCRIPT.md`; Cursor usage documented from the build log.
- Buffer (27/10 until 23:45 UTC): fixes only.

Integrator responsibilities: merges in the order B2 -> B3 -> B1 -> B4 each day, keeps `contracts.py` stable, runs `scripts/regen_examples.py` and the full suite after each merge, owns README and pitch numbers (each sourced), records the live traces, and maintains `docs/BUILD_LOG.md` (what was built in Cursor, which rules fired, decisions, preflight results).

---

## 13. Acceptance checklist (definition of done)

Clean clone and command
- [ ] `git clone <repo> && cd x402-adoption-investigator && python run.py --input examples/01-seller-audit-textokit/input.json` works on Python 3.11 with no dependencies, no network, no env file.
- [ ] `docker build -t x402-investigator . && docker run --rm x402-investigator --input examples/01-seller-audit-textokit/input.json` works (sample mode inside the container).
- [ ] `python run.py --serve 8080` answers `POST /run` with the same contract.

Contract
- [ ] Input/output validate against `schemas/`; error objects for `E_INPUT`, `E_TIMEOUT`, `E_NO_SOURCES`, `E_GENERATOR`, `E_ZETARIS_REQUIRED`, `E_INTERNAL` are tested.
- [ ] `partial` only with >= 1 accepted claim; no "exception -> partial".
- [ ] A run finishes within `budget.max_seconds` (default 180 s) in both modes on the demo machine; every loop is bounded. _Amended 2026-10-22:_ the deadline is measured with a monotonic wall clock in both modes; in sample mode the deterministic SampleClock (1 ms per call) only stamps the logs and never reaches the deadline by itself (`tests/test_run_contract.py`).

Logs
- [ ] `run.jsonl` is mirrored to stdout and contains `handoff`, `critique` (accept summaries per rule; revise/reject lines whenever a rule fires), `recovery`, `ingest` x2, `sql`, `claim`, `conflict`, `narrative_map`, `memory`, `result`; `retry` appears only when something was really retried (live Fetcher backoff), never in a sample replay (_amended 2026-10-22_). _Amended 2026-10-23:_ a sample replay carries `retry` only when its example has a fault script (section 7.4 rule 9); then the retry is real (the shared policy decides it) and the failure it answers is scripted, and the output says so. Examples 01-04 have none; example 05 has two.
- [ ] Sample runs start with the `guard` line; `tests/test_sample_offline.py` proves no socket use.
- [ ] `sql.jsonl` lines carry executor, sql, params, rowcount, time_used_ms, zetaris_version, tables.
- [ ] No secret, token or header value appears in any log (test greps the logs of a live run for the configured password and token prefix).

Sample mode and examples
- [ ] Three examples committed with input + output + report + logs, produced by `scripts/regen_examples.py`; `tests/test_examples_reproducible.py` byte-for-byte green.
- [ ] `output.mounts` is the first key; `sample_mode_notice` present; every evidence item `replayed: true`.
- [ ] `examples/live-trace/<run_id>/` contains a real Zetaris + Base RPC run labelled live.

Meterless
- [ ] 8/8 World Model conformance checks and 13/13 H-MEM vectors green in pytest; `docs/METERLESS.md` explains the port and the TS-runner limitation.
- [ ] Every World Model write carries provenance; the claim policy hook rejects URL-less agent facts (test).
- [ ] `output.world.second_pass == {eventsAppended: 0, changed: false}` in all examples; `scripts/assert_idempotent.py` green.
- [ ] H-MEM: the pitfall gate visibly changes a verdict in ex01/ex02 (critique with `memory_ids`); feedback entries with exact deltas in `ledger.jsonl`; retrieval traces present even without a generator.

Zetaris
- [ ] `scripts/zetaris_preflight.py` passes on the demo machine (login, 2.4.3.1, artifact server reachable from the driver).
- [ ] A live run with `--require-zetaris` completes; `zetaris.jsonl` shows DDL, views, queries, timeUsed, cleanup.
- [ ] `tests/test_sql_dual_backend.py` green for all 19 queries; the ANSI lint test is green.
- [ ] Fallback matrix behaviours tested (unreachable -> sqlite + partial; 400 -> single-query fallback; `--require-zetaris` -> error).

Honesty and Track 02
- [ ] No hardcoded numbers in code (a test greps `investigator/` for the pitch numbers: 28462, 2713, 824796, 44000, 85.7).
- [ ] Conflicts are first-class with explanations; claims cite conflict ids (C7 tested).
- [ ] Coverage on every catalog-wide claim.
- [ ] Narrative pass B token map logged; unsourced sentences trimmed with a recovery event.
- [ ] README: setup, usage, dependencies, 3-minute judge path, agent roster table, how Zetaris and Meterless are integrated (with the "where every claim links to evidence becomes structural" paragraph), how Cursor was used, `.env.example`, `.gitignore` with `.env` and `.env.local`; every pitch number sourced to a fixture + capture date.

---

## 14. Cursor usage (to document, not to claim)

- `.cursor/rules/project.mdc` is the architecture contract Cursor follows; it is committed from day 0.
- `docs/BUILD_LOG.md` records, per day, which modules were written or refactored in Cursor (Composer/agent sessions), which rules were useful, and what was corrected by tests; screenshots optional. Only what actually happened is written there.
- The README section "How Cursor was used" summarises the log in five lines and links to it.
- _Amended 2026-10-22:_ Cursor became optional in the HackOS rules update of 12:45 UTC. What was done in Cursor is
  two Cursor Agent CLI sessions (the viewer and a first draft of the README Pitch section, later rewritten by Claude Code); the rest was built by Claude Code agents
  (docs/BUILD_LOG.md, "Tools"). The README section is now "AI coding tools: Claude Code and Cursor", and
  `metadata.json` carries the same facts with the other sponsor technologies.

---

## 15. Open items the integrator decides on day 1 (and records)

1. Artifact server reachable from the Zetaris driver on the demo machine -> `captured` stays the default; otherwise `direct` + SQLite and README states it.
2. Catalog snapshot over a REST table (size) -> if Zetaris cannot ingest it, catalog metrics run on SQLite in live mode with the fallback reason, and README states it.
3. Transcripts for the generator-backed roles are recorded with Martin's `OPENAI_API_KEY` on day 3; until then sample mode uses rules/template and the examples say `generator: "null"`. The committed examples at freeze must say `"recorded"` for Planner and Writer, or README must explain why not.
