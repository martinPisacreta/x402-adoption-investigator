# x402 Adoption Investigator

> Open Agent Hackathon 2026, Track 02 "The Agent That Can Explain Why". Team Automata (solo, Martin Pisacreta, Argentina).

A bounded multi-agent investigator that answers "how real is x402 adoption?" (who sells, who buys, what is bought,
how concentrated it is, whether buyers come back) with numbers it can prove. Every claim links to a URL with the
sha256 of the bytes fetched, a Base transaction decoded from its receipt logs, or a line of the run's own JSONL log.
Where sources disagree, the disagreement is a first-class `Conflict` with the measurement basis of each number and
a rule-written explanation of why they differ. (x402 is pay-per-call over HTTP `402 Payment Required`, settled in
USDC, here on Base; the CDP Bazaar is Coinbase's x402 discovery catalog.)

## At a glance

| | |
|---|---|
| **User** | The operator of an autonomous agent that sells or buys over x402 and has to decide where the real demand is. |
| **Problem** | Ask four registries and the store itself how many paid endpoints one store has and you get five answers: 17, 17, 17, 19 and 36. Three agree, two differ, and none says why. Activity disagrees too: 60 Bazaar calls in 30 days against 77 settled sales in the receipts, 56 of them paid by the seller itself. |
| **What the agents do** | Seven roles in a bounded loop. Planner: playbook, sources, queries. Collector: fetch and hash. Keeper: Meterless World Model. Verifier: settlements decoded on Base. Analyst: every metric twice, in committed SQL and in a Python reducer. Writer: an answer where every number cites a claim. Critic: rules C1-C7 and a narrative pass; it rejects claims or sends the answer back. |
| **Try it** | `python run.py --sample --input examples/ex1.json` from a clean clone: offline, no keys, Python 3.11 standard library only. Docker: `docker run --rm --network none x402-investigator`. [Quick start](#quick-start-clean-clone-one-command). |
| **Critique and recovery** | Example 04 (`examples/ex4.json`): the Critic rejects a "0 verified" count that no decoded settlement supports, the Writer's second round drops and names it, status `partial`, verdict `insufficient_evidence`. [Examples](#examples). |
| **Sponsor tech** | Zetaris Lightning SQL is the live federation layer; Meterless World Model and H-MEM are mounted and conformance-tested; Cursor: two Cursor Agent sessions (optional since 22 Oct); NVIDIA: not used. [Summary](#sponsor-technology), machine-readable in [metadata.json](metadata.json). |
| **Honesty** | No language model ran in any committed run: the generator-backed roles fell back to rules and templates ([Limitations](#limitations-and-honesty-notes)). Pre-existing: the author's own Automata store, used only as a data source ([declared](#pre-existing-components-declared)). |

Full specification: [SPEC.md](SPEC.md). Build log: [docs/BUILD_LOG.md](docs/BUILD_LOG.md). Demo script:
[docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md). Sponsor integrations: [docs/METERLESS.md](docs/METERLESS.md),
[docs/ZETARIS.md](docs/ZETARIS.md), [metadata.json](metadata.json).

Every data figure in this README comes from a committed fixture (`fixtures/raw/`, captured 2026-10-22 ~00:05Z), a
committed example output (`examples/*/output.json`) or a committed live trace (`examples/live-trace/`).

## Pitch

Demo video: [docs/pitch/demo.mp4](docs/pitch/demo.mp4) (3 min 30 s), narrated with a Windows text-to-speech voice
from [docs/pitch/narration.txt](docs/pitch/narration.txt). Besides the slides it shows a recorded run of example 04:
the stdout of `docker run --rm --network none x402-demo --sample --input examples/ex4.json --print-output` on an
image built from a fresh clone, replayed line by line, unedited and slowed down to the narration, each line with its
real arrival time (the run took 0.77 s; raw capture in
[docs/pitch/video/run-capture.json](docs/pitch/video/run-capture.json)), then the report viewer opened on that run's
output. [docs/pitch/video/](docs/pitch/video/) has the scripts that rebuild it. Slide deck:
[docs/pitch/deck.pdf](docs/pitch/deck.pdf) (12 slides). Source: [docs/pitch/deck.html](docs/pitch/deck.html); slide
images in [docs/pitch/slides/](docs/pitch/slides/).

## Overview

**The problem.** Ask four public registries and the store itself how many paid x402 endpoints `tienda.textokit.store` has and you get
five answers: the CDP Bazaar search returns 17 listings (`fixtures/raw/cdp-discovery-search-urlSubstring-tienda.json`),
the store's own `openapi.json` declares 17 paid operations (`store-openapi.json`), Agent402 counts 17 paid tools out
of 34 (`agent402-index-seller-tienda.json`), vet402 lists 19 endpoints (`vet402-resolve-tienda.json`) and the
x402-trust keyword search reports 36 matches (`x402-trust-search-tienda.json`). Three agree, two differ, and none
of them is wrong: each measures something different (vet402, for instance, still lists `/api/maintenance-helper`
and `/api/preparar-solicitud`, two routes the store's `openapi.json` no longer declares; Conflict k01 of example 01
names them). The same happens with activity: the Bazaar's per-endpoint
30-day call counters sum to 60 for the host while the seller's own receipts list 77 settled sales, 56 of which were
paid from the seller's own wallets. An agent that reports one of these numbers without its basis is not explaining anything.

**What the investigator does.** It plans which sources answer the question, fetches them with retries and hashes
the bytes, folds them into a World Model with provenance, verifies sampled settlements on Base by decoding the USDC
`Transfer` log (never trusting `tx.from`, which is the facilitator), computes every metric twice (a committed SQL
query on Zetaris or SQLite, and a pure-Python reducer over the same exports) and keeps a number only when both
halves exist and agree (the Critic's rule C2 rejects a single-computed or disagreeing value and the disagreement
stays as an open `Conflict`; `tests/test_dual_computation.py`), writes an answer where every number cites a claim
id, and runs a Critic that checks evidence (including that a count of verified settlements rests on a decoded
settlement of its own subject), recomputation, scope, staleness, conflict citation and the pitfall memories in
H-MEM. Claims the Critic rejects are listed in `rejected_claims[]` with their critique trail and named in the
answer (example 04). Sources that are down become `unavailable[]` entries, never zeros.

**What it found (sample mode, fixtures of 2026-10-22; see [Examples](#examples)).** For the seller audit: 77 sales,
56 self-settled from 3 own wallets (72.7 %), 21 external sales from 4 payers worth 44,000 atomic USDC (0.044 USDC),
one repeat payer, the top payer making 18 of the 21 external sales (85.7 %); verdict `self_settled_mostly`. For the
Bazaar catalog: 28,462 listed resources on 2,680 hosts paying to 1,785 wallets, 824,796 calls in the 30-day
counters, 99.6 % of resources with at least one call, the top host `www.ax1.vc` taking 29.5 % of the calls and the
top ten hosts 73.3 %; verdict `concentrated`. For the payer dossier: one purchase of 1,000 atomic USDC, its
settlement verified on chain (block 52,841,269, payer and seller read from the Transfer log), USDC balance
1,043,148 atomic and nonce 24 at capture; verdict `adoption_thin`. For a second external payer whose settlement the
fixtures do not cover (example 04): one purchase of 1,000 atomic USDC, the "0 verified" count rejected by the
Critic as missing evidence, verdict `insufficient_evidence`.

## Sponsor technology

The same facts in machine-readable form, with file paths and tests, are in [metadata.json](metadata.json).

- **Zetaris (Lightning SQL, live federation layer).** In live mode the analytics queries run on Zetaris Lightning
  2.4.3.1 (local Docker, REST API) over REST tables registered for that run from the bytes the Collector hashed,
  flattened by SchemaStore views; a view or query that fails there falls back to SQLite with a logged recovery.
  Code: `investigator/zetaris/`, SQL: `investigator/sql/` (22 queries). The live seller audit computed all 19 of its
  claims on Zetaris (`examples/live-trace/live-ex01-20261022/`). Offline the same SQL runs on SQLite;
  `tests/test_sql_dual_backend.py` proves SQLite returns the rows Zetaris returned for q01-q19
  (`fixtures/zetaris/`). q20-q22 were added after the live session and have not run on Zetaris yet.
  [Details](#zetaris-integration-tables-sql-fallback).
- **Meterless (World Model and H-MEM mounted; Markovian and Scout Intent not used).** The World Model is the shared
  state: provenance on every row, a claim policy that refuses facts without evidence, idempotent ingest proved in
  every output. H-MEM pitfall memories add the caveats each claim must carry on top of the registry's own rules
  (checked by Critic rule C3) and are injected into the Planner and Writer prompts when a generator is mounted, with
  a trace in `logs/`. Conformance: 8/8 World
  Model checks, 13/13 upstream H-MEM vectors; `tests/test_generator_hmem.py` proves the injection offline;
  `examples/memory-ablation/` measures what one memory changes. [Details](#meterless-integration-h-mem--world-model).
- **Cursor.** Optional since the 22 Oct rules update. Two headless Cursor Agent CLI sessions built the report viewer
  (`viewer/index.html`) and the README Pitch section, under `.cursor/rules/project.mdc`; Claude Code wrote the rest.
  [Details](#ai-coding-tools-claude-code-and-cursor).
- **NVIDIA.** Not used: no NVIDIA model, API or GPU. The generator is OpenAI-compatible (`OPENAI_BASE_URL`, `MODEL`),
  but no NVIDIA endpoint was run or tested.

## Architecture: agents and handoffs

```
run.py -> runner (state machine, budgets, deadline, handoffs)
  planner -> collector -> keeper (World Model ingest, pass 1) -> verifier -> keeper (chain facts)
  -> analyst -> [writer -> critic] x <= max_critic_rounds (a second round only when the Critic says `revise`)
  -> keeper (assert accepted claims; ingest pass 2 = idempotency proof)
  -> hmem (feedback, mining, sleep preview) -> finalize (output.json, report.md)
```

| role | file | generator-backed | inputs | outputs | refuses |
|---|---|---|---|---|---|
| Planner | `investigator/agents/planner.py` | yes (rules fallback) | question, focus, H-MEM retrieval | `Plan` (playbook, fetch list, query ids, metrics, tx sample rule) | free-form SQL; guessing a playbook |
| Collector | `investigator/agents/collector.py` | no | fetch list, Fetcher | observations with sha256, raw bytes, exports, `fetch.jsonl` | fabricating a missing source |
| Keeper | `investigator/agents/keeper.py` | no | observations, chain facts, accepted claims | World Model events, two `IngestReport`s, world exports | writes without provenance |
| Verifier | `investigator/agents/verifier.py` | no | receipts, Base JSON-RPC | verifications, cross-checks, conflicts, chain facts, `settlements.json` | taking `tx.from` as the payer; zero rows when the chain is unreachable |
| Analyst | `investigator/agents/analyst.py` | no | query ids, SQL executor, reducers | claims, conflicts, `unavailable[]` | zeros for unavailable sources; summing per-endpoint unique payers |
| Writer | `investigator/agents/writer.py` | yes (template fallback) | claims, conflicts, H-MEM reinjection, critic issues | answer where every number cites `[cNN]` | numbers that are not claims; choosing the verdict |
| Critic | `investigator/agents/critic.py` | second opinion only | answer, claims, evidence, memories | critique per rule (C1-C7, narrative pass B), confidence, memory feedback | passing silently on an internal error |

Every transition is a `handoff` event with the sha256 of the artifact handed over. The Runner never retries a whole
run and never turns an exception into `partial`: an uncaught exception is `E_INTERNAL` naming the stage and the last
log line. The verdict label is chosen by rules (`investigator/agents/verdict.py`), never by a model. Budgets bound
everything: `max_seconds` (a wall-clock deadline measured with a monotonic clock in both modes and checked before
every step and inside the loops; in sample mode the logged timestamps come from the deterministic clock, which
advances 1 ms per call and never reaches the deadline by itself, so the monotonic clock is what stops a slow sample
run, `tests/test_run_contract.py`), `max_fetches`, `max_sql`,
`max_generator_calls`, `max_critic_rounds`, `max_tx_verifications`; retries use a fixed 0.5 s / 1.5 s backoff.

Exactly three mount points differ between live and sample mode and they are the first key of every output
(`mounts`): the Fetcher (`live+fixture-fallback` | `fixture`), the SQL executor (`zetaris` | `sqlite`) and the
generator (`openai:<model>` | `recorded` | `null`).

## Quick start (clean clone, one command)

Requirements: Python 3.11 (standard library only at runtime), git. No network, no keys and no env file are needed
for the default run. `python` below is Python 3.11; on systems where the binary is `python3` use that name
(`scripts/one_command.sh` picks `python3` when it exists).

```bash
git clone https://github.com/martinPisacreta/x402-adoption-investigator && cd x402-adoption-investigator
python run.py --sample --input examples/01-seller-audit-textokit/input.json --print-output
python run.py --sample --input examples/ex4.json --print-output    # the Critic rejects a claim (example 04)
```

The run is in sample mode: the first stdout line is `guard` (network disabled), then the JSONL event stream
(identical to `logs/ex01-seller-audit/run.jsonl`), then, after a `---output---` line, the `{run_id, status,
output}` envelope that is also written to `logs/ex01-seller-audit/output.json` (with `report.md` next to it).
`--sample` pins sample mode; without it the default `--mode auto` also runs sample mode when no credentials are
configured, but an `OPENAI_API_KEY` or `ZETARIS_PASSWORD` already exported in the shell (or present in
`.env.local`) switches `auto` to live mode, with real fetches and paid model calls. `scripts/one_command.sh` (or
`.ps1`) runs the example with `--print-output` and then the test suite. On Windows, clone into a short directory:
the run folder nests `logs/<run_id>/exports/<name>.json`, and the longest file the examples write
(`logs/ex03-payer-dossier/exports/rpc_receipt_0x<64 hex>.json`) is 115 characters below the repository root, so a
repository path longer than 143 characters (a logs directory longer than 148) goes over the `MAX_PATH` limit
(260 including the terminator) unless `LongPathsEnabled` is set; a longer `--run-id` lowers that bound. The run
then stops with an `E_INTERNAL` that names the run-relative path and the full length. `--out <short dir>` or the
`LOG_DIR` variable (honoured in sample mode too; `--out` wins) moves the run folders without moving the clone.
Docker:

```bash
docker build -t x402-investigator .
docker run --rm --network none x402-investigator                                   # sample mode, example 01
docker run --rm --network none x402-investigator --sample --input examples/ex3.json --quiet
```

The image carries no secrets (`.dockerignore` excludes `.env` files except `.env.example`, key, certificate and
wallet files, `logs/` and `memory/state/`); the two commands above were run on the demo machine with
`--network none` and finished with status `ok`.

## Run contract

```
python run.py --input <file|-> [--run-id ID] [--mode sample|live|auto] [--sample]
              [--require-zetaris] [--require-generator] [--zetaris-source captured|direct]
              [--catalog pages|snapshot] [--out DIR] [--output FILE] [--print-output] [--quiet]
python run.py --serve 8080        # POST /run with the same JSON body; GET /health
```

Input (`investigator/schemas/input.schema.json`):

```json
{"run_id": "ex01-seller-audit",
 "input": {"question": "How real is the adoption of tienda.textokit.store? ...",
           "focus": {"host": "tienda.textokit.store", "wallet": null, "service": null, "query": null},
           "budget": {"max_seconds": 180, "max_fetches": 40, "max_tx_verifications": 5, "max_critic_rounds": 2, "bazaar_pages": 3},
           "options": {"catalog": "snapshot", "zetaris_source": "captured"}}}
```

Output (`investigator/schemas/output.schema.json`): `{"run_id", "status": "ok" | "partial" | "error", "output": {...}}`
with `output.mounts`, `plan`, `verdict {label, confidence, why}`, `answer`, `claims[]`, `rejected_claims[]`,
`conflicts[]`, `unavailable[]`, `sources[]`, `verifications[]`, `world`, `memory`, `agents[]`, `critique_rounds`,
`report_path`, `log_files`; or `{"run_id", "status": "error", "error": {code, stage, message, retryable, details}}`
with codes `E_INPUT`, `E_TIMEOUT`, `E_NO_SOURCES`, `E_GENERATOR`, `E_ZETARIS_REQUIRED`, `E_INTERNAL`.

Status rule (SPEC.md 4.3): `ok` = every planned step completed, no fallback mount was used against the requested
mode and no claim was rejected; `partial` = at least one accepted claim AND (a source or metric unavailable, a
deadline skip, a rejected claim, or a fallback executor/generator in live mode); `error` otherwise. Exit code 0 for
`ok` and `partial`, 1 for `error`. `--serve` answers `POST /run` with 200 for `ok`/`partial` and 422 for an
`error` envelope (a JSON body that fails the input schema included); a body that is not JSON gets 400, a body over
1 MB gets 413 (both with an `E_INPUT` error envelope), and any other path 404. `tests/test_run_contract.py`
(`HttpRunContract`) checks `GET /health`, a non-JSON body, a schema-invalid body and example 03.

A run finishes in seconds to minutes: on the demo machine the sample runs of example 01 and 03 take about one
second and example 02 about 15 s (it folds the 32 MB catalog snapshot); the live Zetaris runs finished inside the
180 s budget (see `examples/live-trace/`).

## Sample mode (offline, no keys)

Sample mode is the default whenever no credentials are configured (`--sample` or `SAMPLE_MODE=1` force it). It is
proven offline, not asserted: `investigator/guard.py` patches `socket.socket.connect`, `connect_ex`,
`create_connection`, `getaddrinfo` and `urllib.request.urlopen` before the first event, and
`tests/test_sample_offline.py` runs the four examples with the guard installed (any socket use would surface as
`E_INTERNAL`). What is replayed:

- 27 request keys over 26 files in `fixtures/raw/` captured read-only on 2026-10-22 (`MANIFEST.json` maps the
  canonical request key to the file, its sha256 and capture time), including the compact CDP Bazaar catalog
  snapshot (28,462 resources, gzip) and five Base JSON-RPC responses for one settlement
  (`0x62c0...58c3`: receipt, transaction, USDC `balanceOf`, nonce, block number / chain id).
- A deterministic clock (every `at` in the logs starts at the capture time) so `output.json` and `run.jsonl` are
  byte-for-byte reproducible (`tests/test_examples_reproducible.py`).
- The seven seed pitfall memories (`memory/seed/`) copied into `logs/<run_id>/hmem/`; live runs use `memory/state/`.
- The committed SQL executed by the SQLite view layer; `fixtures/zetaris/qNN_*.json` are the result sets the real
  Zetaris instance produced for the same exports, and `tests/test_sql_dual_backend.py` proves both backends agree.
- No model call: the generator mount is `null`, Planner and Writer use rules and templates and the Critic runs its
  deterministic rules only. No transcript was recorded because no `OPENAI_API_KEY` was available during the build
  window (honesty note below); `RecordedGenerator` replays `fixtures/transcripts/` when they exist.
  `tests/test_generator_hmem.py` exercises the generator path offline with an in-process OpenAI-compatible double.

Anything the fixtures do not cover is reported, never invented: in the seller audit the Verifier samples four
transactions, verifies the one the fixtures cover and reports the other three as `unverifiable` with
`reason: "fixture_miss"` (each with its own `fetch` and `recovery{fixture_miss}` line). The same three are
`verified` in the live trace. `output.sample_mode_notice` says the run replayed fixtures.

## Live mode and graceful degradation

With `.env.local` present (copy `.env.example` and fill the values), `python run.py --mode live --input
examples/ex1.json` runs live: real fetches with retries and fixture fallback marked `stale: true`, Zetaris as the
SQL executor, and an OpenAI-compatible generator when `OPENAI_API_KEY` is set. The default `--mode auto` resolves
to live as soon as `OPENAI_API_KEY` or `ZETARIS_PASSWORD` is set (in `.env.local` or in the shell) and to sample
otherwise; `SAMPLE_MODE=1` forces sample mode. Each missing piece degrades to a logged recovery instead of a crash:

| missing | behaviour | log marks |
|---|---|---|
| `OPENAI_API_KEY` | Planner/Writer rules and templates, Critic rules only | `recovery{generator_fallback}`, `mounts.generator: "null"`, status `partial` |
| Zetaris credentials or instance | the same SQL on SQLite for the whole run | `recovery{zetaris_unreachable}`, `mounts.executor: "sqlite"`, status `partial` |
| one Zetaris view or query | that query on SQLite, the rest on Zetaris | `recovery{zetaris_query_fallback}`, `derivation.backend: "sqlite-fallback"` |
| the mounted executor cannot build its views | the same SQL on an embedded SQLite executor over the same exports | `recovery{zetaris_query_fallback, to: sqlite}`, `derivation.backend: "sqlite-fallback"` |
| a query skipped (SQL budget, deadline) | the claims that need it have one value only and the Critic's C2 rejects them | `recovery{budget_exhausted | deadline_skip}`, `rejected_claims[]`, status `partial` |
| a source after retries | fixture replayed as stale, or `unavailable` + `unavailable[]` metrics | `retry`, `recovery{fixture_fallback | source_unavailable}` |
| Base RPC | settlements `unverifiable`, chain metrics in `unavailable[]` | `recovery{rpc_unavailable}` |
| time | optional steps skipped in a fixed order | `timeout`, `recovery{deadline_skip}` |

Three live traces are committed under `examples/live-trace/` (see its README): example 01 and 03 on Zetaris 2.4.3.1
with 4 of 4 sampled settlements verified against `https://mainnet.base.org`, and example 03 with every credential
withheld (`--mode live`): `zetaris_unreachable` -> SQLite, `generator_fallback`, status `partial`, every SQL-backed
number still computed and cross-checked, 4 of 4 settlements verified on chain. The traces were recorded before the
balance and nonce got their SQL half, so in both example 03 traces those two claims are reducer-only
(`cross_checked: false`); the current build computes them in SQL too (q22) and its C2 would reject them otherwise.

## Meterless integration (H-MEM + World Model)

Both engines are Python 3.11 standard-library ports of the Meterless reference architectures, written from the
published specs and reference implementations and checked against the upstream conformance vectors (8/8 World Model
checks, 13/13 H-MEM vectors replayed in `tests/test_worldmodel_conformance.py` and `tests/test_hmem_conformance.py`;
the vectors are copied verbatim under `tests/vectors/` with attribution). Details and the exact fold rules are in
[docs/METERLESS.md](docs/METERLESS.md).

**World Model** (`investigator/worldmodel/`: `stable_ids.py`, `resolve.py`, `types.py`, `store.py`, `model.py`,
`views.py`, `domain.py`, `adapters/`) is the shared state of the run. The Keeper folds every observation through a
per-family adapter (`adapters/receipts.py`, `cdp_bazaar.py`, `store_openapi.py`, `agent402.py`, `x402_trust.py`,
`vet402.py`, `base_rpc.py`) into entities (`seller`, `endpoint`, `wallet`, `facilitator`, `registry`), contexts
(`day:utc:<date>`, `investigation:run:<run_id>`), bitemporal relationships (`paid`, `sells`, `pays_to`, `listed_on`,
`verified_by`, `settled_via`) and facts, each carrying `{kind, at, by, url, checksum, runId}` provenance. The engine
writes `logs/<run_id>/world.events.jsonl` (append-only, fold-on-load), `world.audit.jsonl` and
`world.observations.jsonl`, and exports `exports/world-{entities,relationships,facts,contexts}.json` that the
Analyst's reducers and the Critic's recompute read.

Where "every claim links to evidence" becomes structural: `domain.claim_policy` runs inside `WorldModel.assert_fact`
and refuses any fact of kind `agent` whose `source.url` is not an `https://` URL or a `log://<run_id>#<seq>` line, or
whose `source.runId` is missing. The Keeper asserts every accepted claim as such a fact; a refusal is logged as
`critique{rule: "provenance", kind: "provenance_rejected"}` and counted in `output.world.quarantined`
(`tests/test_meterless_keeper.py`). Idempotent ingest is proven in every run: the Keeper re-ingests the same
observations at the end and writes `output.world.second_pass = {eventsAppended: 0, changed: false}`;
`python scripts/assert_idempotent.py logs/<run_id>` replays the run's observation manifest into a fresh model twice
and exits 0 only when nothing differs (PASS on the four examples).

**H-MEM** (`investigator/hmem/`: `types.py`, `embeddings.py`, `ledger.py`, `store.py`, `retrieval.py`, `mining.py`,
`sleep.py`, `conflicts.py`, `dreaming.py`, `facade.py`) is the memory that adds the caveats a claim must carry.
Seven committed pitfall memories (`memory/seed/memories.jsonl`, tags `pitfall` + `caveat:<code>`, entities
`field:<name>` / `source:<family>` / `metric:<id>`) feed one deterministic index, `policy_memories(fields, sources,
metrics)`, read twice per claim: the Analyst attaches every caveat the touching memories require, with the memory
id behind each code (`claim.caveat_memory_ids`), and the Critic's rule C3 re-reads the same index and revises any
claim that arrives without one (`investigator/agents/pitfalls.py`). In the committed examples the two agree, so
C3 never fires (example 04's second round comes from a C1 rejection, not from memory); C3 is the safety net for a
memory added at run time or a generator-written claim that drops a caveat, and `tests/test_critic.py` exercises the
revise path with hand-built claims. What memory changes is measurable, not asserted: `scripts/memory_ablation.py` runs example 01 twice, with
the committed seed and with `mem-seed-003` removed, and `examples/memory-ablation/summary.json` records the
difference (the `self_reported_receipts` caveat disappears from `settlements_verified`, which no registry rule
attaches, and the `[mem-seed-003]` citation disappears from fifteen claims and from the answer; the verdict does
not change, and the summary says so). The scope rule that forbids summing `l30DaysUniquePayers` (C4) is code, not
memory: no registry metric does it, and the rule is proven by registering such a metric inside a test only
(`tests/test_run_contract.py`), where the Critic rejects it, lists it in `rejected_claims[]` and the status rule
makes the run `partial`. Retrieval uses the spec formula
(`0.35 semantic + 0.20 keyword + 0.10 tag + 0.10 domain + 0.15 entity + 0.05 layer + 0.05 recency - 0.20 superseded`,
times confidence, threshold on the final score) over a deterministic FNV-1a hash embedder; every retrieval writes a
trace line to `hmem.trace.jsonl`. When a generator is mounted, the Planner's and the Writer's retrievals are filled
into their prompts as a `[MEMORY CONTEXT]` block, and every prompt that carries one is logged before the call as a
`memory` event `op: "inject"` (and the same line in `hmem.trace.jsonl`) with the memory ids, the prompt's sha256
and the block's sha256, so retrieval, injection and the `generator.jsonl` line of the call link up by hash.
`tests/test_generator_hmem.py` checks the block inside the prompts actually sent (example 03: `mem-seed-005` for
both roles) against those hashes, and that the Writer's second-round prompt carries the block again with the
Critic's issues. Feedback applies the exact deltas (`helpful +0.05`, `not_helpful -0.03`,
`wrong -0.20`) to the trust ledger (`ledger.jsonl`, `output.memory.feedback`), the runner mines one short-term record
per accepted claim at finalize (`output.memory.mined`), and the sleep cycle is previewed every run (executed only in
live mode with `--hmem-sleep`).

## Zetaris integration (tables, SQL, fallback)

Zetaris Lightning (2.4.3.1, local Docker) is the federation layer the live run computes on; the code is in
`investigator/zetaris/` (`client.py` REST client, `ddl.py`, `artifact_server.py`, `views_zetaris.py`,
`views_sqlite.py`, `catalog.py`, `executor.py`) and the SQL in `investigator/sql/`. Full detail, measured timings and
the syntax facts learned on the instance are in [docs/ZETARIS.md](docs/ZETARIS.md).

- **Tables.** Each run creates its own objects: a Lightning database `X402_<RUN8>` with one REST table per export
  the playbook produced (in the live seller audit: `receipts_raw`, `bazaar_search_host`, `openapi_paid_ops_raw`,
  `agent402_index`, `vet402_resolve`, `trust_search_raw`, `trust_stats_raw`, `settlements_raw`; catalog runs add
  the Bazaar pages and the 32 MB catalog snapshot, which registered in 2.7 s during B3's recording) served to the
  Zetaris driver by a stdlib artifact server over `logs/<run_id>/exports/` (so Zetaris computes on exactly the
  bytes the Collector hashed), cached with `CACHE TABLE`, and a SchemaStore container `x402_<run8>` with the views
  that flatten the JSON (`sales`, `purchases`, `bazaar_resources`, `bazaar_search`, `catalog_resources`,
  `openapi_paid_ops`, `agent402_tools`, `agent402_seller`, `vet402_endpoints`, `trust_search`, `trust_stats`,
  `settlements`, `chain_facts`; the live seller audit built ten of the twelve views that existed then, before
  `chain_facts` was added; columns in `investigator/sql/views/VIEWS.md`). Everything is dropped at finalize and on every error path
  (`scripts/zetaris_cleanup.py` removes leftovers of crashed runs). `--zetaris-source direct` registers the public
  URLs of receipts and Bazaar pages instead of the captured exports.
- **SQL.** 22 analytics queries (`investigator/sql/analytics/qNN_*.sql` + `.meta.json` with the column types,
  parameters `:host` / `:wallet` / `:since` bound by the catalog loader) in an ANSI subset that runs unchanged on both
  backends; the sha256 of each committed query is reported on every claim (`derivation.sql_sha256`). Zetaris returns
  every cell as a string; cells are cast per the meta file. A lint test keeps Spark-only syntax out of the catalog.
  q20-q22 were added after the live Zetaris session so that every metric has an SQL half: q20 and q21 read the
  existing `catalog_resources` view; q22 reads the new `chain_facts` view, whose Zetaris definition decodes the
  JSON-RPC hex with `conv()` and has not run on an instance yet (a failure there sends that query to SQLite).
- **What worked on the demo machine (2026-10-22).** `scripts/zetaris_preflight.py` 8/8 PASS (login, version,
  metadata, artifact server bound and reachable from the driver container, SchemaStore view, cleanup);
  `scripts/record_zetaris_results.py --from-fixtures` recorded the 19 result sets of q01-q19 in `fixtures/zetaris/`
  and `tests/test_sql_dual_backend.py` proves SQLite returns the same typed rows (q20-q22 have no recording yet; the
  test names them explicitly); the live run of example 01 computed
  all 19 claims on Zetaris (`derivation.backend: "zetaris"`, queries in 157-590 ms, 75 REST calls including the
  cleanup, `examples/live-trace/live-ex01-20261022/zetaris.jsonl`), and example 03 ran with `--require-zetaris`.
- **Fallback.** Login failure or an unreachable instance -> SQLite for the whole run with
  `recovery{zetaris_unreachable}` (trace `examples/live-trace/live-nozetaris-ex03/`); a view or query that fails
  -> that query on SQLite with `fallback_reason` and `backend: "sqlite-fallback"`; `--require-zetaris` turns any of
  these into `E_ZETARIS_REQUIRED`. Sample mode never opens a socket, so it is always SQLite.

## Logs

`logs/<run_id>/run.jsonl` is mirrored to stdout line by line: `{"seq", "at", "run_id", "type", "agent", "payload"}`
with types `guard`, `run_start`, `plan`, `handoff`, `fetch`, `retry`, `recovery`, `ingest`, `verify`, `crosscheck`,
`sql`, `claim`, `conflict`, `critique`, `narrative_map`, `memory`, `timeout`, `result`, `error` (schema in
`investigator/schemas/events.schema.json`, validated line by line in the tests). `retry` is emitted only for a real
retry (the live Fetcher's 0.5 s / 1.5 s backoff on timeouts, connection errors and 5xx, `tests/test_fetcher.py`);
sample runs replay fixtures and carry none, and a second Writer round is a `handoff` from the Critic with its
issues, not a retry. Side files share the same `seq`
counter so `log://<run_id>#<seq>` is unambiguous: `fetch.jsonl` (request keys, sha256, export and raw paths written
run-relative as `logs/<run_id>/...` even when `--out` points elsewhere, never as a machine path), `sql.jsonl`
(SQL text, params, headers, first rows, executor, Zetaris version, tables with `source_url` = the export path
`logs/<run_id>/exports/<file>` on SQLite or the artifact-server URL on Zetaris), `zetaris.jsonl` (live only: every REST
call with status and timing, never a header or token), `hmem.trace.jsonl` (retrieval hits with their signals and
feedback deltas), `world.events.jsonl` / `world.audit.jsonl` / `world.observations.jsonl` (World Model). A run also
writes `output.json`, `report.md`, `exports/` (what the SQL backends and reducers read) and `raw/` (bytes as fetched).

What to look for in a stream: the `handoff` lines between agents (each with the sha256 of what was handed over),
`claim` lines with `sql_value` and `reducer_value` side by side, `critique` accept summaries per rule with the
claim count (and `verdict: revise` lines with `memory_ids` whenever the gate fires, `verdict: reject` lines when a
claim is rejected: example 04's C1 on `verified_tx`, followed by a `handoff` critic -> writer and the Writer's
`recovery{narrative_trimmed}` naming the claim), `memory` `op: "inject"` lines when a generator is mounted, a
`narrative_map` with
`unsourced: []`, `verify` lines with `receipt_match`, `recovery` lines for every fallback (`fixture_miss` in the
sample seller audit, `zetaris_unreachable` / `generator_fallback` in the degraded live trace), and `ingest` pass 2
with `eventsAppended: 0`.

## Examples

Four inputs with their outputs are committed under `examples/` (`exN.json` are short aliases of the folder inputs,
`examples/outputs/exN.output.json` byte-identical copies of the outputs); `scripts/regen_examples.py` is the only
way they are updated and `tests/test_examples_reproducible.py` keeps them honest.

| example | question | verdict | status | key claims (every one computed twice, SQL == reducer) |
|---|---|---|---|---|
| `01-seller-audit-textokit` (`ex1.json`) | How real is the adoption of tienda.textokit.store? Who pays, how much, do they come back, why do registries disagree? | `self_settled_mostly` (0.9) | `ok` | 77 sales; 56 own-wallet from 3 wallets; 21 external from 4 payers, 44,000 atomic USDC; self-settlement 56/77 = 72.7 %; 1 repeat payer; top payer 18/21 = 85.7 %; external window 2026-10-20..2026-10-21; Bazaar 17 listings, 1 payTo, 60 calls (30-day counters); endpoint counts 17 / 17 / 17 of 34 / 19 / 36 (Conflict k01); calls vs receipts basis mismatch (Conflict k02); 1 settlement verified on Base (3 `unverifiable: fixture_miss`) |
| `02-catalog-concentration` (`ex2.json`) | Who sells on the CDP Bazaar and how concentrated are the paid calls? What share of listed endpoints saw any call in the last 30 days? | `concentrated` (0.9) | `ok` | 28,462 resources (coverage 28,462 of 28,472 reported, 0.9996); 2,680 hosts; 1,785 payTo wallets; 824,796 calls; active 28,347/28,462 = 99.6 %; top host `www.ax1.vc` 243,580 = 29.5 %; top-10 hosts 604,248 = 73.3 %; top payTo 29.5 %; most listings `saylorinnovations.com` 1,568; largest per-endpoint unique-payer counter 2,708 (caveat `unique_payers_not_dedupable` from `mem-seed-001`); x402-trust 286,358 listed / 210,375 active (Conflict k01 `ecosystem_size`) |
| `03-payer-dossier-onchain` (`ex3.json`) | What do we know about payer 0x097e...475a, what it bought from tienda.textokit.store, can the settlement be verified on Base? | `adoption_thin` (0.9) | `ok` | 1 purchase, 1,000 atomic USDC, 1 service, 2026-10-20T04:51Z; settlement verified: payer and seller from the Transfer log, 1,000 atomic, block 52,841,269, facilitator `0x8f5c...e531`, `receipt_match: true`; USDC balance 1,043,148 atomic (1.043148 USDC) and nonce 24 at capture (SQL decodes the RPC hex in the view layer, the reducer reads the Verifier's decoding) |
| `04-payer-dossier-unverifiable` (`ex4.json`) | The same question about payer 0xA1d1...1978 | `insufficient_evidence` (0.0) | `partial` | 1 purchase, 1,000 atomic USDC, 1 service, 2026-10-21T21:18Z; its settlement is not in the RPC fixtures (`unverifiable: fixture_miss`), so `verified_tx` = 0 (SQL and reducer agree) is rejected by the Critic's rule C1 and dropped and named by the Writer's second round; balance and nonce `unavailable` (`fixture_miss`); the same settlement is `verified` in `examples/live-trace/live-ex01-20261022` |

Examples 01-03 are `ok`: every planned source answered (from fixtures), every claim passed C1-C7 in one round
and, in 01 and 03, the one settlement the fixtures cover decodes and matches. Example 04 is `partial` because the
Critic rejected a claim: its input differs from example 03 only in the wallet, and nothing in the registry is
written for it (`tests/test_example_04_critic.py` checks the committed files and re-runs it offline). The other
rejection paths are proven by tests with a deliberately broken half: `tests/test_dual_computation.py` (an
off-by-one reducer leaves an open `backend_disagreement` Conflict and C2 rejects the claim; an SQL budget of five
rejects every claim whose query never ran; with no SQL executor at all nothing survives) and
`tests/test_run_contract.py` (C4, with a metric that exists only inside that test). `examples/memory-ablation/` is
a further committed run pair: the same example 01 with and without one seed memory, and the diff between the two
outputs (`summary.json`).

## Viewer

A static, dependency-free report viewer lives at [`viewer/index.html`](viewer/index.html) (plain HTML + CSS +
vanilla JS; no build step, no CDN). From the repository root:

```bash
python -m http.server
```

Then open `http://127.0.0.1:8000/viewer/`. It loads `examples/01-seller-audit-textokit/output.json` by default
and accepts any `run.py` `output.json` via the file picker. It renders the run header (`mounts.mode`, `executor`,
`generator`, `fixtures_captured_at`, verdict), accepted claims (value, unit, coverage, evidence links), rejected
claims with the Critic reason, and conflicts with each source's value and explanation. Fields follow
`investigator/schemas/output.schema.json`.

Pitch numbers that differ from SPEC.md section 0 are the committed ones: the snapshot has 2,680 distinct hosts and
1,785 distinct payTo wallets under the view semantics both backends share (primary accept per listing, Java-strict
host parsing with 57 URI-illegal resources counted but not folded under a fake host), not 2,713 and 2,177.

## AI coding tools: Claude Code and Cursor

**Claude Code** (Anthropic's coding agent, model Claude Opus 5.5) was the main coding tool. On 2026-10-22 from
00:10 UTC one Claude Code multi-agent workflow read the Meterless and Zetaris material, produced three designs (two
judge agents picked one and grafted parts of the others into SPEC.md and the Cursor rules), scaffolded the
repository, ran four builders on disjoint directories (sources and chain, Meterless engines, Zetaris layer, agents
and runner), integrated them, ran the live checks, and reviewed and fixed the result. The pitch was made from the
same session, and so were the pre-submission verification and the fix rounds of the same afternoon. Commits carry
`Claude-Session` trailers.

**Cursor** was required when the build started and became optional on 2026-10-22 at 12:45 UTC. Two scoped tasks
were done in headless Cursor Agent CLI sessions: the static report viewer (`viewer/index.html`) and the README
Pitch section. The Cursor prompts said not to run git, so the commits that carry that work (901b109, e654f59) were
made from the Claude Code session and have its trailers. OpenAI Codex was not used.

`.cursor/rules/project.mdc` is the architecture contract (always applied, every file type): standard library only,
no number in code, evidence on every claim, generator-backed roles limited to Planner / Writer / Critic second
opinion, sample mode offline with a socket guard, exactly three mount points, ANSI-only analytics SQL with JSON
flattening confined to the view layer, visible `unavailable`/`recovery` instead of silent zeros, bounded loops,
secrets from env only, one event log, shared contracts nobody edits, Meterless formulas kept exactly, per-run Zetaris
objects, English everywhere, replayed data flagged. The rules were written on day 0 from SPEC.md before any module
existed, and they were given to every coding agent, Claude Code ones included.

[docs/BUILD_LOG.md](docs/BUILD_LOG.md) records, with UTC times, what was produced and with which tool, which rules
shaped decisions (rule 8 decided that an unreachable chain never becomes a `settlements` view with zero rows; rule 2
removed a registry metric that existed only to be rejected) and what the tests caught (appended logs on re-run,
wall-clock milliseconds in a "reproducible" log, a tokenizer that flagged the numbers of a text claim). Its
"Cursor sessions" section lists every piece of work done in Cursor itself with date, files and the rule that
applied; an entry is added only for work actually done there.

## Pre-existing components declared

- **Automata and its x402 store `https://tienda.textokit.store`, built and operated by the author** (Martin
  Pisacreta, Team Automata) before the build window. Automata is a self-hosted agent on Base (ERC-8004 id 98748);
  its store publishes `receipts.json` (every settled sale with payer, amount, transaction hash and an `ownWallet`
  flag), `openapi.json`, `llms.txt` and a remote MCP at `/mcp`. Here it is only a live, read-only public data
  source and the subject of examples 01, 03 and 04 and of the live traces: no code from it is in this repository,
  and its receipts are treated as self-reported (caveat `self_reported_receipts`, memory `mem-seed-003`) and
  verified against the chain rather than trusted. Example 01's finding (56 of 77 sales self-settled) is about the
  author's own store.
- **Meterless H-MEM conformance vectors**: `tests/vectors/hmem-vectors.json` is a verbatim upstream copy
  (Apache-2.0, `tests/vectors/ATTRIBUTION.md`). The engine ports were written in the window from the specs.
- **Zetaris Lightning 2.4.3.1**, the third-party platform, run locally in Docker; none of its code is here.
- **The Python 3.11 standard library**, the whole runtime.

Everything else was written in the build window (from 2026-10-22 00:00 UTC); the fixtures were captured read-only
on 2026-10-22 between 00:02 and 00:08 UTC.

## Tests

```
python -m unittest discover -s tests     # 225 tests, no third-party dependency
python -m pytest -q                      # same suite under pytest if installed
```

Suites: World Model conformance (8/8), H-MEM conformance (13/13 vectors), adapters over the fixtures, Keeper and
H-MEM investigator surface, fetcher and collector, settlement decoding, the Verifier, Zetaris views / executor /
fallback matrix (faked HTTP), SQL dual backend (recorded Zetaris result sets == SQLite), Critic rules, the agents
pipeline, the run contract (schemas, error codes, status rule, CLI exit codes, `--serve` HTTP statuses, `LOG_DIR`,
the no-hardcoded-numbers grep),
dual computation with a deliberately broken half (C2), example 04's organic C1 rejection, H-MEM injection into the
generator prompts through an in-process OpenAI-compatible double plus `RecordedGenerator` replay, sample-mode
offline proof, byte-for-byte reproducibility of the committed examples, and `metadata.json` (every path it names
exists, its test count is the discovered one, and README and deck quote the same count).

On 2026-10-22 the suite ran green (0 failures, 0 skips; the tree and the test count of each verification are in
docs/BUILD_LOG.md) in three places: a fresh Windows clone with the
Git for Windows default `core.autocrlf=true`, a fresh clone with `core.autocrlf=false`, and the Docker image built
from that clone (`docker run --network none ... -m unittest discover -s tests`, Python 3.11.17). In all three,
`python run.py --sample` on each of the eight example inputs (`examples/exN.json` and `examples/0N-*/input.json`)
exited 0 (status `ok` for 01-03, `partial` for 04) and rewrote `output.json`, `report.md`, `run.jsonl`,
`sql.jsonl` and `hmem.trace.jsonl` byte for byte as committed, and `scripts/memory_ablation.py` reproduced
`examples/memory-ablation/`; `scripts/one_command.sh` exited 0 in the image. Sample mode has no time-dependent field (the
clock is pinned to the fixture capture time). `.gitattributes` keeps every text file LF on checkout and the code
writes LF on every OS, so the recorded sha256 values match everywhere. The live traces in `examples/live-trace/`
are records of live runs and are not re-run offline.

## Limitations and honesty notes

- **No model ran in any committed run.** No `OPENAI_API_KEY` was available during the build, so every committed
  example and live trace says `generator: "null"`: the Planner routes by rules, the Writer renders templates, the
  Critic applies its deterministic rules only. The generator path is exercised by `tests/test_generator_hmem.py`
  with an in-process OpenAI-compatible endpoint (a test double: no network, no key) as the transport of the real
  `OpenAIGenerator`: it asserts that the Planner and Writer prompts carry the H-MEM `[MEMORY CONTEXT]` block with
  the retrieved memories, that `hmem.trace.jsonl` and `run.jsonl` record the injection (`memory` op `inject`,
  linked by `reinjection_sha256` and `prompt_sha256` to the retrieval and to `generator.jsonl`), that a Writer reply
  with an unsourced number comes back in a second round with the Critic's issue, and that `RecordedGenerator`
  replays the saved transcripts without a call. The double's replies are scripted: they prove the plumbing, not
  the quality of a model, and no real model output was recorded. Live runs without a key are `partial` for this
  reason.
- **Sample mode verifies one settlement.** The fixtures cover one transaction; the other sampled hashes are
  `unverifiable: fixture_miss`. Live runs verify 4 of 4 (`examples/live-trace/`).
- **Where critique and recovery show up.** Examples 01-03 finish in one Critic round: the Analyst already attaches
  what the pitfall memories require. Example 04 shows a real rejection (C1), a second Writer round and the
  recoveries behind it. Sample mode never emits `retry` (fixtures do not time out): the Fetcher's retries are
  covered by unit tests and can only occur in live runs (none of the committed live traces needed one), and the live
  traces show the real recoveries (`zetaris_unreachable`, `generator_fallback`). The C2 and C4 rejection paths are proven by tests with
  deliberately broken halves, not by a committed example.
- **Memory changes caveats and citations, not the verdict**, in these examples: `examples/memory-ablation/`
  measures exactly what one seed memory adds (a caveat on one claim, a citation on fifteen, feedback entries) and
  shows the verdict label unchanged.
- **The catalog snapshot is dated** (2026-10-22, 28,462 of 28,472 resources the API reported at capture) and every
  catalog-wide claim carries that coverage; the live total moves by the minute. `l30DaysUniquePayers` is per endpoint
  and is never summed.
- **Self-reported receipts.** `receipts.json` is published by the seller; the investigator flags own-wallet rows,
  verifies sampled settlements on chain and attaches the caveat to every receipts-based claim. Between the fixture
  capture and the live trace the receipts bytes changed (the file carries a `generatedAt` timestamp) and the
  Bazaar 30-day counter for the host moved from 60 to 61; both differences are visible in the traces.
- **Third-party registries** (Agent402, vet402, x402-trust) are read as published; their numbers are reported with
  their basis and never reconciled into one figure.
- **Zetaris paths not exercised live**: the `direct` API style and the paginated `start/page/close` route (no query
  exceeds 1,000 rows); `CACHE TABLE` is a session cache.
- **Zetaris coverage.** Sample mode, the one documented command, always runs on SQLite. q20-q22 and the
  `chain_facts` view were added after the live Zetaris session: the three queries have no Zetaris recording, and
  the Zetaris definition of `chain_facts` (`conv()` over the RPC hex) has not run on an instance. Both committed
  example 03 live traces predate them.
- **No NVIDIA.** No NVIDIA model, API or GPU is used.
- **No paid probe.** The investigator never spends: it reads receipts and public settlements instead of buying.

## Dependencies and licenses

Runtime: Python 3.11 standard library (no `pip install`). Tests: `unittest` (pytest optional). The Meterless
H-MEM and World Model ports are written from the published specifications (upstream Apache-2.0; no upstream source
file is copied; conformance vectors are copied verbatim with attribution in `tests/vectors/ATTRIBUTION.md`). Data
sources are public APIs used read-only without keys: CDP Bazaar discovery, `tienda.textokit.store`, Base JSON-RPC
(`https://mainnet.base.org`), x402-trust, Agent402, vet402. This repository is MIT.
