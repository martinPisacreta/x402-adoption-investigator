# Live traces on Zetaris with Meterless Nightshift and NVIDIA Nemotron (2026-10-24)

Six live runs made between 22:36 and 22:42 UTC on 24 Oct in which both integrations did their part in the same run:
every analytics query ran on Zetaris Lightning 2.4.3.1 (local Docker, REST API) and every Planner, Writer and Critic
second-opinion request went through Meterless Nightshift 1.2.3 to NVIDIA Nemotron.

- `nemotron-cloud/`: the five example inputs, answered by NVIDIA Nemotron 3.5 Lightning 30B A3B on NVIDIA's hosted
  API (Nightshift holds the NVIDIA key; the app never sees it).
- `nemotron-local/`: example 01, answered by NVIDIA Nemotron Nano 9B v2 (Q4_K_M GGUF), the local lane of Nightshift,
  whose layer weights the daemon placed 100% on the machine's RTX 5080 Laptop GPU (that placement line, and the
  lane's own timing lines for these five requests, are in `machine.txt`).

All six end with `result` status `ok`, executor `zetaris` and generator `nightshift:<model>`. They are the only
committed live runs whose status is `ok`. Every earlier committed live run that wrote a `result` line is `partial`
(the smoke requests write none):

- the runs of 22 Oct (`../live-trace/`) had no model (`recovery{generator_fallback}`), and one of them had no
  Zetaris either;
- the Nightshift runs of 24 Oct, 16:00-18:55 UTC (`../live-trace-nightshift/`), ran their SQL on SQLite because
  Zetaris was stopped (`recovery{zetaris_unreachable}`).

Nothing here is edited by hand. The run folders are unedited copies of files of `logs/<run_id>/` (which files: see
Files), each byte-identical (sha256) to its source when copied. `machine.txt` is command output, with the
redactions it states inline.

## What ran

The commands, run from the repository root, one per line. `METERLESS_MODEL` pins the route, so each `generate`
event names the model that did the work:

```
METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b \
  python run.py --mode live --require-zetaris --require-generator --input examples/exN.json --run-id zetaris-nightshift-ex0N-20261024   # N = 1..5
METERLESS_MODEL=local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m \
  python run.py --mode live --require-zetaris --require-generator --input examples/ex1.json --run-id zetaris-nightshift-local-ex01-20261024
```

The two `--require-*` flags are not written to the logs. What the logs do show is what the flags demand:
`run_start.payload.mounts` has executor `zetaris` and generator `nightshift:<model>`, and no run logged a Zetaris or
generator fallback. Without the flags, a fallback that a query or a generator call actually used would have made
the run `partial`; with them, it would have been an error envelope. A Zetaris table or view that no query reads is
not covered: example 02 below lost one and still ended `ok`.

Zetaris ran as the Docker Compose project `zetaris`: four containers, ui, api and driver from images
`2.4.3-v2.4.3.1-hackathon.<hash>` (driver build `cb88df0b`) and postgres from `postgres:16-alpine`, with the
driver's heap at 2 GB (`-Xmx2g`, `spark.driver.memory=2g`, `spark.executor.memory=1g`). The containers were started
for these runs between 22:35:40 and 22:36:22 UTC: postgres, api and ui per `docker inspect`, and the driver per its
log line "Starting LightningHiveServer2" at 22:35:49, because `docker inspect` shows its later restart. All of this
is in `machine.txt`.

Each run did the following, every call logged in `zetaris.jsonl`, which never holds an HTTP header, the login
credentials or the Zetaris `idToken` (the artifact server's per-run path token does appear in its URLs; see Files):

- logged in with the `ZETARIS_*` values of `.env.local`, whose values appear in no log;
- ran `SHOW SERVER VERSION` (`mounts.zetaris_version: "2.4.3.1"`);
- registered the exports it had hashed as Lightning REST tables, served to the driver by its own artifact server
  (`zetaris_source: captured`);
- built the SchemaStore views and ran its analytics SQL on them;
- dropped everything at the end (the `cleanup` line).

Nightshift was found by Auto Detect at `http://127.0.0.1:8088/v1`, with the token read from
`<stateDir>/proxy-token` (`run_start.payload.generator_mount`). Thinking was off on every request
(`call_options {"reasoning_effort": "none", "system_turn": "/no_think"}`), with the 90 s timeout per request.

| folder | input | run_start (UTC) | requests | status | executor | generator model | accepted / rejected / conflicts / unavailable | Nightshift receipts (`generate` events, in order) | Zetaris `run-query` calls |
|---|---|---|---|---|---|---|---|---|---|
| `nemotron-cloud/zetaris-nightshift-ex01-20261024/` | `examples/ex1.json` | 2026-10-24T22:36:45Z | 5 | `ok` | `zetaris` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 19 / 0 / 2 / 0 | rcpt_req_ontiekpl, rcpt_req_v7ob7i3s, rcpt_req_k9c21z33, rcpt_req_xnsi573n, rcpt_req_9z5yna9n | 14 |
| `nemotron-cloud/zetaris-nightshift-ex02-20261024/` | `examples/ex2.json` | 2026-10-24T22:37:57Z | 5 | `ok` | `zetaris` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 11 / 0 / 1 / 0 | rcpt_req_rtq7j8ik, rcpt_req_ujkssqd7, rcpt_req_xc3qhxuz, rcpt_req_3hpmlkfk, rcpt_req_b2fpw8cl | 8 |
| `nemotron-cloud/zetaris-nightshift-ex03-20261024/` | `examples/ex3.json` | 2026-10-24T22:39:32Z | 5 | `ok` | `zetaris` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 9 / 0 / 0 / 0 | rcpt_req_ta17xqso, rcpt_req_sp15rnuh, rcpt_req_d2l7d1pq, rcpt_req_qarcsk4q, rcpt_req_7d9cxt02 | 5 |
| `nemotron-cloud/zetaris-nightshift-ex04-20261024/` | `examples/ex4.json` | 2026-10-24T22:40:06Z | 5 | `ok` | `zetaris` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 9 / 0 / 0 / 0 | rcpt_req_7jfja0q4, rcpt_req_ldqedxoq, rcpt_req_m49og1xf, rcpt_req_jttdhusb, rcpt_req_r14r7ilf | 5 |
| `nemotron-cloud/zetaris-nightshift-ex05-20261024/` | `examples/ex5.json` | 2026-10-24T22:40:43Z | 5 | `ok` | `zetaris` | `nvidia/nemotron-3.5-lightning-30b-a3b` | 9 / 0 / 0 / 0 | rcpt_req_q9w8s0zt, rcpt_req_szndljtv, rcpt_req_991ywwmq, rcpt_req_2sqq6kth, rcpt_req_bgmad4z9 | 5 |
| `nemotron-local/zetaris-nightshift-local-ex01-20261024/` | `examples/ex1.json` | 2026-10-24T22:41:19Z | 5 | `ok` | `zetaris` | `local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m` | 19 / 0 / 2 / 0 | rcpt_req_g0pekcag, rcpt_req_fafah22a, rcpt_req_zebf3f61, rcpt_req_emboosui, rcpt_req_xclfmiju | 14 |

How to read the table:

- Input: each run's `run_start.input_sha256` is the hash of that input file as `run.py` parses it.
- Requests: one `generate` event per request, in the order planner, writer-r1, critic-r1, writer-r2, critic-r2. No
  run has a `retry` line.
- Counts: the four counts are those of the `result` line, and they equal the lengths of the arrays in `output.json`.
- Zetaris `run-query` calls: lines of `zetaris.jsonl` with `op: "run-query"`. Each count is the run's `sql` events
  plus the `SHOW SERVER VERSION`.
- Durations, `run_start` to `result`: 53.6, 89.8, 28.4, 31.3, 30.2 and 42.5 s, inside the 180 s budget.

## Zetaris: every number's SQL half

- **Queries.** Every `sql` event names executor `zetaris`, and every line of `sql.jsonl` has `fallback_reason:
  null`:
  - examples 01 and local 01: 13 queries, q01-q07, q12-q16 and q18;
  - example 02: 7 queries, q08-q11 and q19-q21;
  - examples 03-05: 4 queries each, q16, q17, q06 and q22.

  The analytics queries took 131 to 1,064 ms each on Zetaris (`time_used_ms` in `zetaris.jsonl`).
- **Claims.** Every claim has `derivation.backend: "zetaris"` in `output.json`. Its `claim` event has
  `cross_checked: true`, and its `sql_value` equals its `reducer_value`, for all 76 claims (19, 11, 9, 9, 9 and 19).
  The Critic's C2 logged `accept` over all claims in both rounds of every run. Example 01: seq 102 and 145, local
  run seq 103 and 138. Examples 02-05: seq 58/93, 63/90, 63/95 and 63/94.
- **New queries and view.** q20, q21 and q22, and the `chain_facts` view, had been added after the Zetaris session
  of 22 Oct. These are the first runs on record that ran them on Zetaris: no committed run and no run folder in
  `logs/` has them there before, and the driver's log, kept since its container was created on 21 Oct, first names
  `chain_facts` at 22:37:08 on 24 Oct (`machine.txt`).
  - Example 02 ran q20 and q21 at seq 23-24.
  - `chain_facts`, with its `conv()` decoding of the JSON-RPC hex, was built in every run but example 02, first in
    example 01 at 22:37 UTC: each `CREATE OR REPLACE SCHEMASTORE VIEW chain_facts` returned HTTP 200. q22, which
    reads it, ran in examples 03-05 and returned 2 rows at seq 38 of each.

  `fixtures/zetaris/` still has no recorded result set for q20-q22: `tests/test_sql_dual_backend.py` lists them as
  not recorded. Example 02's catalog is the committed snapshot (below), and its export has the sha256 of the export
  that sample mode builds and that the q08-q11 recordings name (`fb53b73f...`). So q20 and q21 ran there on the same
  bytes as sample mode: their rows, and those of q08-q11, equal the SQLite rows of
  `../02-catalog-concentration/sql.jsonl`, with the same `sql_sha256`.
- **REST calls.** `zetaris.jsonl` has 81, 33, 40, 40, 40 and 81 lines. Each starts with the login (HTTP 200)
  and ends with `DROP LIGHTNING DATABASE X402_<RUN8>` (HTTP 200), after the `cleanup` line (the DELETE of the
  SchemaStore container). Each also holds one `artifact-server` line, which is not a REST call. Every `run-query`
  answered HTTP 200.

### The one failed call: the Zetaris driver ran out of heap in example 02

In example 02, line 10 of `zetaris.jsonl` is `CACHE TABLE X402_B4B1827B.bazaar_page0`. After 16,686 ms it returned
HTTP 400 with "Could not create ResultSet: java.net.SocketException: Connection reset". The `CREATE LIGHTNING REST
TABLE` before it had succeeded in 1,409 ms. No other Zetaris call of the six runs failed.

The driver's own log (`machine.txt`, filtered as stated there) shows what happened:

- at 22:38:15 UTC the driver submitted "ResultStage 107 (In-memory table X402_B4B1827B.bazaar_page0 ...)";
- the stage failed with `java.lang.OutOfMemoryError: Java heap space` after 11.291 s;
- at 22:38:26 the driver logged "SparkContext is stopping";
- Docker started the driver container again at 22:38:27.848Z (`restarts 1`, restart policy `unless-stopped`);
- at 22:38:29 the driver logged "Starting LightningHiveServer2".

The run went on with the restarted driver. It registered and cached the 32 MB catalog snapshot (4,499 and
5,099 ms; the driver submitted that cache at 22:38:52), built `catalog_resources` and `trust_stats`, and ran its
seven queries from seq 18 (22:38:57Z).

Why the heap ran out is not shown. The page's export is 3.4 MB (3,424,473 bytes), about a tenth of the catalog
snapshot that the restarted driver cached right after with the same 2 GB heap, and the driver that failed had served
example 01 first (its 9 `CACHE TABLE` calls, since the start at 22:35:49). The same driver container ran out of heap
once before, on 2026-10-22 at 02:08-02:09 UTC, also while caching a first Bazaar page (stage 463, in a session whose
log is not committed), while the same kind of table cached in 4.969 s at 01:54 (the recording session of
`fixtures/zetaris/`) and in 5.573 s at 03:16 that day, every start with `-Xmx2g` (`machine.txt`). Neither a retry
nor a larger heap was tried.

The run logged no `recovery` for the failed cache, and its status is `ok`. The reason is that none of its seven
queries reads the Bazaar page: they read `catalog_resources` and `trust_stats`. So no query fell back to SQLite, and
the status rule counts only fallbacks that were used. The failure shows only in `zetaris.jsonl` and in the driver
log. `sql.jsonl` does not show it either way: its `tables` key, the same on every line, lists the tables the run
registered, not the ones each query read, and without their status (`tables_summary()` in
`investigator/zetaris/executor.py` keeps only `name` and `source_url`). So the seven lines of example 02 still name
`X402_B4B1827B.bazaar_page0`, with no mark that its cache failed.

The catalog snapshot of example 02 is the committed one (2026-10-22): its `sources[]` entry says `replayed: true`,
as in every run, live or sample. The other sources of the six runs were fetched live.

## Nightshift: who answered each request

- **Cloud, 25 requests.** Every `generate` event of the five `nemotron-cloud` runs, repeated in `generator.jsonl`,
  says `provider: meterless`, with `model` and `routing.routed_model` both `nvidia/nemotron-3.5-lightning-30b-a3b`.
  Its `winning_route` is `{label: "Nemotron 3.5 Lightning 30B A3B", provider: nvidia, kind: cloud, tier: A}`.
  - Receipt latency: 1,750 to 9,299 ms per request.
  - Tokens: 51,507 input and 9,908 output over the 25.
- **Local, 5 requests.** In the local run, `routed_model` is `local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m`,
  `kind: local`, `privacy_path: local-only`.
  - Latency: 1,287 to 8,180 ms.
  - Tokens: 15,109 input and 976 output.
- **Nightshift's ledger.** All 30 receipt ids are in Nightshift's own Ed25519-signed ledger, each signature valid,
  issued to app `x402-adoption-investigator` between 22:36:48 and 22:42:01 UTC. The ledger names the same winner
  model as the run. Egress is `nvidia` for the 25 cloud requests and `none` for the 5 local ones. The lines are in
  `../live-trace-nightshift/nightshift-ledger/receipts.jsonl` (157 lines now: these 30 were appended unedited after
  the 127 lines already there; its README says how).
- **Transcripts.** `transcripts/` holds each reply with its routing and `request` (`max_tokens`, `call_options`),
  and the exact prompt beside it. The prompt's sha256 is the `prompt_sha256` of its `generate` event.

## What the model's answers did

- **Planner.** Its first reply became the plan in all six runs (`plan.planner_source: "generator"`), with no re-ask.
- **Critic second opinion: cut or invalid replies.** Five replies were cut or invalid JSON arrays, and the code kept
  their complete items (`recovery{generator_reply_repaired}`):
  - example 01: critic-r1 and critic-r2, 9 and 11 items kept, seq 130 and 176;
  - examples 03 and 04: critic-r2, 14 and 10 items kept, seq 108 and 113.

  These four reached the 500-token budget. The fifth is example 02's critic-r2, at seq 114: 156 of 500 tokens. Its
  reply was not valid JSON, because two items lack the `"reason":` key. The code kept the 2 complete items, and the
  recovery's detail calls the reply "a truncated JSON array". Example 01's writer-r2 also reached its budget (700
  tokens).
- **Critic second opinion: issues raised.** The hosted Critic raised these issues (rounds 1/2, `critique` lines with
  `rule: "LLM"`; in brackets, the ones dismissed because rule B maps every number of the sentence to a claim it
  cites):

  | run | round 1 | round 2 |
  |---|---|---|
  | ex01 | 9 (9) | 5 (0) |
  | ex02 | 9 (5) | 2 (0) |
  | ex03 | 4 (3) | 14 (5) |
  | ex04 | 9 (6) | 10 (8) |
  | ex05 | 8 (6) | 8 (3) |

  Several of the issues are wrong: they flag a restated claim value as "not derivable" or "not a standalone claim".
  The local Nano answered `[]` in both rounds, 2 tokens each.
- **Writer.** After round 2, the flagged sentences were cut (`recovery{narrative_trimmed}`): 5, 2, 9, 2 and 4 in the
  cloud runs, 1 in the local run. The code-owned sentences a draft lacked were added from the claims
  (`recovery{narrative_completed}`): caveats in every run, the conflict sentences in the three runs that have
  conflicts (examples 01, 02 and the local 01), and the verdict sentence in the five cloud runs. The local run needed no verdict sentence, because its final answer already stated the verdict.
- **What the model never decides.** Claims, values and verdicts come from the rules:
  - example 01: `self_settled_mostly`, the same 19 values on both routes, and the same as the 18:55 UTC run of
    `../live-trace-nightshift/nemotron-cloud/live-video-ex01-20261024/`, whose SQL ran on SQLite;
  - example 02: `concentrated`;
  - examples 03-05: `adoption_thin`.

  Against the SQLite runs of examples 02-05 at 17:28-17:30 UTC (`../live-trace-nightshift/nemotron-cloud/`), two
  values differ, because their sources moved: the USDC balance read live from Base in example 04 (c08: 168,575,488
  atomic, against 169,070,488 then) and the live x402-trust ecosystem stats in example 02 (c11). Example 01 differs
  from its run of 17:27 UTC only in c16, the Bazaar 30-day call counter of the host (63 then, 64 here), as the 18:55
  run already did.
- **H-MEM.** `memory` `op: "inject"` lines precede the `generate` events with the same `prompt_sha256`: 1, 3, 3, 3,
  3 and 1 per run.

## How to check it

From the repository root, offline:

```
python scripts/nightshift_ledger.py examples/live-trace-zetaris-nightshift/nemotron-cloud/zetaris-nightshift-ex0* \
    examples/live-trace-zetaris-nightshift/nemotron-local/zetaris-nightshift-local-ex01-20261024 \
    --ledger examples/live-trace-nightshift/nightshift-ledger/receipts.jsonl
python -m unittest tests.test_live_trace_zetaris_nightshift tests.test_nightshift_ledger
grep -c '"op":"run-query"' examples/live-trace-zetaris-nightshift/*/*/zetaris.jsonl
```

The first command prints one line per request as Nightshift recorded it. Its last line says `"requests":30,
"found_in_nightshift_ledger":30,"signatures_valid":30,"winner_is_run_routed_model":30`. Run without `--ledger` on
the demo machine, on 2026-10-24, it read the runtime's live `<stateDir>/receipts.jsonl` and gave the same 30 of 30.

`tests/test_live_trace_zetaris_nightshift.py` checks the following against the logs:

- the file set, and the transcript pair of every request with its hashes;
- every log line against the event schema;
- Zetaris for every query and claim, with both halves equal;
- the pinned Nightshift model for every request, and no fallback recovery;
- one `run-query` per `sql` event plus the version query, and the one failed call above;
- this README's table, row by row.

## What it does not prove

- **Not reproducible offline.** These are records of live runs. The store's receipts, the Bazaar counters, the chain
  and x402-trust move, so a new run gives other bytes and some other values. Sample mode, the documented command,
  always runs on SQLite.
- **Zetaris against SQLite on the same bytes, only in part.** The agreement shown in every run is the one inside
  it: the Zetaris SQL value against the Python reducer over the same exports (C2). The test-guarded comparison with
  SQLite on the same bytes covers q01-q19 (`fixtures/zetaris/`, `tests/test_sql_dual_backend.py`). Example 02 adds
  q20 and q21 (and q08-q11 again) over the committed catalog snapshot, with rows equal to the SQLite rows of
  `../02-catalog-concentration/sql.jsonl`, but no test guards that; q22 ran only over live chain facts. q20-q22 have
  no recording.
- **Status `ok` is not "every Zetaris call succeeded".** In example 02 a table could not be cached and the driver
  restarted (above); the run's `run.jsonl` does not show it, and its `sql.jsonl` still lists that table. Why the
  2 GB heap ran out was not isolated: right after the restart the same heap cached the 32 MB catalog snapshot, so the
  page's size alone does not explain it, and neither a retry nor a larger heap was tried.
- **The Zetaris path from a container is still untested.** Zetaris and the investigator ran on the host, not from
  the `investigator-live` container.
- **The model's weak spots remain.** Its drafts still drop caveat codes and some conflict ids, so the final answers
  are partly completed from the claims. Five Critic replies were cut or malformed, and several Critic issues are
  wrong. The hosted endpoint is not deterministic at temperature 0.
- **What the ledger proves.** The lines are unchanged since Nightshift's daemon signed them, under the public key
  the envelope carries. They do not prove which machine holds the private key, which was never read
  (`../live-trace-nightshift/nightshift-ledger/README.md`). The Nightshift Console is not shown.

## Files

Each of the six run folders holds `output.json`, `report.md`, `run.jsonl`, `generator.jsonl`, `sql.jsonl`,
`hmem.trace.jsonl`, `fetch.jsonl`, `zetaris.jsonl` and `transcripts/` (10 files, two per request). This is the set
of the complete folders of `../live-trace-nightshift/`, used here for every run, because each run's Zetaris evidence
is in `sql.jsonl` and `zetaris.jsonl`. These stay in `logs/`: the World Model event files, `raw/`, `exports/` and the
SQLite file each run also writes.

The 108 files were copied with `cp` and compared by sha256 with `logs/<run_id>/`: all equal, no CR byte. Before
they were added, they were checked for the following, and none was found:

- absolute paths;
- `Bearer`, `nvapi-`, `idToken`, `password` and `authorization` strings;
- the `ZETARIS_USERNAME`, `ZETARIS_PASSWORD` and `ZETARIS_BASE_URL` values of `.env.local`;
- any literal of 32 or more characters that is not hex, a path or an identifier.

The artifact-server host (`host.docker.internal`) and each run's 32-hex path token appear in `sql.jsonl` and
`zetaris.jsonl`, as in `../live-trace/`. The token is random per run, and its server is gone.
