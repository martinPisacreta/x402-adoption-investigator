# Live traces through Meterless Nightshift with NVIDIA Nemotron (2026-10-24)

Real runs of the five example inputs with Meterless Nightshift 1.2.3 as the generator. Every Planner, Writer and
Critic second-opinion request went to the local Nightshift runtime, which handed it to an NVIDIA Nemotron model:

- **Primary, `nemotron-cloud/`: NVIDIA Nemotron 3.5 Lightning 30B A3B on NVIDIA's hosted API** (the API catalog of
  build.nvidia.com, `https://integrate.api.nvidia.com/v1`), which Nightshift reaches with the NVIDIA API key entered
  in Nightshift's Setup > Providers. The key lives only inside Nightshift: it is in no file, variable or log of this
  repository, and the app never sees it. No GPU is needed for this route.
- **Also shown, `nemotron-local/`: NVIDIA Nemotron Nano 9B v2** (Q4_K_M GGUF), the one local lane of the active
  Nightshift fleet, every layer on the machine's NVIDIA GeForce RTX 5080 Laptop GPU.
- **Earlier, `llama-3.2-3b/`: the first Nightshift run** of the same day (16:00-16:32 UTC), answered by a local
  Llama 3.2 3B Q8 lane through `meterless/auto`; archived with its own README and `machine.txt`, and with the 25
  transcripts the committed examples replayed until the fixture set was re-recorded with Nemotron.

Nothing here is edited by hand: the run folders are unedited copies of files of `logs/<run_id>/` (which files, see
Files), and `machine.txt` is command output with the redactions and filters it states inline.

Also here: `nemotron-cloud/live-video-ex01-20261024/`, the run recorded for the demo video (below), `docker/`, one
Nightshift request made from a container (its own README), and `nightshift-ledger/`, the lines of Nightshift's own
Ed25519-signed receipt ledger for every receipt this repository cites (127, all signatures valid; its own README).

## What ran

Commands (one per row; `METERLESS_MODEL` pins the route, so each `generate` event names the model that did the work;
`meterless/auto` could pick either route):

```
METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b \
  python run.py --mode live --require-generator --print-output --input examples/exN.json --run-id nemotron-cloud-ex0N-20261024
METERLESS_MODEL=local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m \
  python run.py --mode live --require-generator --print-output --input examples/ex1.json --run-id nemotron-local-ex01-20261024
```

| folder | input | run_start (UTC) | requests | status | Nightshift receipts (`generate` events, in order) |
|---|---|---|---|---|---|
| `nemotron-cloud/nemotron-cloud-ex01-20261024/` (complete) | `examples/ex1.json` | 2026-10-24T17:27:20Z | 5 | `partial` | rcpt_req_ikp1bnwc, rcpt_req_14krm3ix, rcpt_req_7wahdjag, rcpt_req_qlxvvms9, rcpt_req_8d1qnkxs |
| `nemotron-cloud/nemotron-cloud-ex02-20261024/` | `examples/ex2.json` | 2026-10-24T17:28:22Z | 6 (planner x2) | `partial` | rcpt_req_glwat7w9, rcpt_req_5vp0170x, rcpt_req_ga0ytyas, rcpt_req_91f5d49a, rcpt_req_apoazph4, rcpt_req_chzl5p7z |
| `nemotron-cloud/nemotron-cloud-ex03-20261024/` | `examples/ex3.json` | 2026-10-24T17:29:15Z | 5 | `partial` | rcpt_req_cx7dp79o, rcpt_req_jixi2h9b, rcpt_req_macv9ww0, rcpt_req_f3glghah, rcpt_req_1cz4wbwv |
| `nemotron-cloud/nemotron-cloud-ex04-20261024/` | `examples/ex4.json` | 2026-10-24T17:29:51Z | 5 | `partial` | rcpt_req_rndytkid, rcpt_req_2e5tkn76, rcpt_req_u4kee2r4, rcpt_req_a2qt0mtx, rcpt_req_frnojjiy |
| `nemotron-cloud/nemotron-cloud-ex05-20261024/` | `examples/ex5.json` | 2026-10-24T17:30:28Z | 6 (planner x2) | `partial` | rcpt_req_ux64zdry, rcpt_req_bqatoqo7, rcpt_req_9i8bn59m, rcpt_req_qkqvqvj6, rcpt_req_8fvibwro, rcpt_req_zgfddbsp |
| `nemotron-local/nemotron-local-ex01-20261024/` (complete) | `examples/ex1.json` | 2026-10-24T17:31:26Z | 5 | `partial` | rcpt_req_2j2p2eb0, rcpt_req_zyml4lfk, rcpt_req_70mpt2bw, rcpt_req_f3ake0ah, rcpt_req_vu51m066 |
| `nemotron-cloud/live-video-ex01-20261024/` (complete; build a8650c4) | `examples/ex1.json` | 2026-10-24T18:55:18Z | 5 | `partial` | rcpt_req_t1f1teb8, rcpt_req_c0jovllr, rcpt_req_pl9z663t, rcpt_req_v4ma9ufl, rcpt_req_y94ho7dr |

No other Nightshift variable was set: Auto Detect found the runtime at `http://127.0.0.1:8088/v1` and the token was
read from the runtime's own `<stateDir>/proxy-token` (`run_start.payload.generator_mount`: `found_by: auto_detect`,
`token_source: <stateDir>/proxy-token (default stateDir)`). `NIGHTSHIFT_REASONING_EFFORT` was unset, so its default
`none` applied: `generator_mount.call_options` and every `generator.jsonl` line say
`{"reasoning_effort": "none", "system_turn": "/no_think"}` (next section). Each of the first six runs took 28-44 s
(`run_start` to `result`), the video run 41 s. The last row was made with `docs/pitch/video/capture_run.py --env
METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b -- python run.py --mode live --require-generator --input
examples/ex1.json --run-id live-video-ex01-20261024`; its stdout, as it streamed, is
`docs/pitch/video/live-capture-run.json` and equals the folder's `run.jsonl`.

## Which model handled each request

- **Cloud, 27 + 5 requests.** Every `generate` event of the five `nemotron-cloud-*` runs (and of
  `live-video-ex01-20261024`, five more, latency 1,955 to 12,867 ms) (`run.jsonl`, repeated with the
  call parameters in `generator.jsonl`) says `provider: meterless`, `requested_model` and `model`
  `nvidia/nemotron-3.5-lightning-30b-a3b`, and carries `routing`, the facts of the answer's Meterless compute
  receipt: `routed_model: nvidia/nemotron-3.5-lightning-30b-a3b`, `winning_route` (`label: "Nemotron 3.5 Lightning
  30B A3B"`, `provider: nvidia`, `kind: cloud`, `tier: A`), `privacy_path: cloud-ok`, `cache: miss`, the receipt's
  `latency_ms` (1,434 to 11,548 ms per request) and token usage (56,937 input and 8,382 output tokens over the 27).
- **Local, 5 requests.** The `nemotron-local-ex01-20261024` events say `routed_model:
  local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m`, `winning_route` (`label:
  "nvidia_NVIDIA-Nemotron-Nano-9B-v2-Q4_K_M · Runtime"`, `provider: local-runtime`, `kind: local`),
  `privacy_path: local-only`, latency 1,278 to 8,442 ms, 15,030 input and 958 output tokens. Where it ran is in
  `machine.txt`: the daemon's line `lane nemotron placed full on CUDA0 - 9523 MB device, 308 MB host (100% of layer
  weights on GPU)` on the RTX 5080 Laptop GPU, the lane's `-ngl 99`, the GGUF's sha256 (`be845282...`), and the
  llama-server timing lines of the run's five requests, whose output tokens (182, 436, 2, 336, 2) are the receipts'.
- `output.json` lists the same calls per role (`agents[].generator_calls`, with `route` and `route_kind`) and
  `report.md` has a "Model calls" table. The receipt's prompt preview, cost and host telemetry are not copied.

## Thinking off: why every request carries `reasoning_effort: "none"` and `/no_think`

Both Nemotron models think before they answer, and the thinking most likely counts against `max_tokens` (Planner
400, Writer 700, Critic 500); the logs show only the error, not a reasoning token count. Single requests made with
`scripts/nightshift_smoke.py` (`nemotron-cloud/smoke/`, `nemotron-local/smoke/`: `run.jsonl`, and for the answered
ones `generator.jsonl` and `transcripts/`) show it. The cloud ones (17:13-17:16 UTC) came before the cloud runs and
decided the default; the local one (17:37 UTC) was made after the local run (17:31 UTC), to confirm the same failure
on that route:

The prompt and `max_tokens` columns of the three failed smokes come from the commands as they were run: those logs
recorded only the error (no `generate` event, no transcript). `scripts/nightshift_smoke.py` now writes both into
`run_start.smoke_request`.

| run id | route | prompt, `max_tokens` | thinking control | result |
|---|---|---|---|---|
| `smoke-cloud-thinking-default-20261024` | cloud | example 01 critic-r1 (the archived Llama draft), 500 | none sent | `recovery{generator_fallback}`: HTTP 502 `nvidia/nemotron-3.5-lightning-30b-a3b_empty_response` |
| `smoke-cloud-critic-thinking-3000-20261024` | cloud | example 01 critic-r1 of `prompt-v1-ex01/`, 3000 | none sent | the same HTTP 502 empty_response |
| `smoke-local-thinking-default-20261024` | local | the local run's critic-r1, 500 | none sent | HTTP 502 `local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m_empty_response` |
| `smoke-cloud-thinking-none-20261024` | cloud | the first row's prompt, 500 | `none` | answered (rcpt_req_99wbhmtk): 500 output tokens, the array cut after 4 complete items, which flag supported sentences too |
| `smoke-cloud-planner-20261024`, `smoke-cloud-writer-r1-20261024` | cloud | example 01 planner, 400; writer-r1, 700 | `none` | answered: the Planner in 170 tokens but with a stray quote (not a JSON object); the Writer hit 700 |

The switch (`NIGHTSHIFT_REASONING_EFFORT`, alias `METERLESS_REASONING_EFFORT`, default `none`; `default` sends
nothing) works per request on both routes, each in its own way (`machine.txt` shows the code and the template):

- Hosted Nemotron 3.5: `reasoning_effort: "none"` in the body. Nightshift's proxy forwards it
  (`proxy.js` line 733) and its NVIDIA adapter turns it into `chat_template_kwargs.enable_thinking=false`
  (`providers.js` line 1288) for its Nemotron 3 / 3.5 rows.
- Local Nemotron Nano 9B v2: its chat template overwrites any `enable_thinking` argument and turns thinking off only
  when a system or user turn holds `/no_think` (then it opens the answer with `<think></think>`), so the request
  starts with a system turn `/no_think`, which the template deletes from the rendered text. The prompt stays the one
  user turn, byte for byte (its sha256 is the one logged).

## The prompts were tightened for Nemotron (`nemotron-cloud/prompt-v1-ex01/`)

With thinking off, the first fixture recording of example 01 (17:15 UTC, the prompts of f06bc36) filled every Writer
and Critic budget: writer-r1 and writer-r2 700 of 700 output tokens, critic-r1 and critic-r2 500 of 500, both Critic
arrays cut (4 and 5 complete items kept, `recovery{generator_reply_repaired}`). The drafts listed every claim as a
bullet although the Writer prompt allowed five, and the Critic flagged supported sentences with long reasons ("The
value is supported by c05, but the sentence itself is not a claim"). `prompt-v1-ex01/` keeps that replay
(`output.json`, `report.md`, `run.jsonl`, `generator.jsonl`) and its five transcripts. Two prompts changed before
the second recording (17:19 UTC)
(`investigator/agents/prompts/`): the Critic lists ONLY unsupported statements, never a statement that restates a
claim's value with its id, each reason in at most 15 words; the Writer picks at most 5 bullets, "never list every
claim", then at most two short paragraphs, under 300 words. The budgets did not change. The Planner prompt did not
change.

## What the model's answers did

- **Planner.** Its reply was the plan in all six runs (`plan.planner_source: "generator"`). In ex02 and ex05 the
  first reply was not valid JSON ("response is not a JSON object": a stray `}` closed the object right after the
  hypotheses list); the one re-ask is
  logged as a `retry` line (agent `planner`) and its answer was used. No `planner_fallback`, no repair.
- **Critic second opinion.** In the six runs of 17:27-17:31 UTC none of the 12 replies was cut: no
  `generator_reply_repaired` in those runs; the longest
  cloud reply was 498 of 500 tokens (ex04 critic-r2). The cloud Critic added 7/2, 2/6, 2/8, 11/8 and 10/6 issues
  (rounds 1/2, ex01-ex05; `critique` lines with `rule: "LLM"`); several are wrong (they call a restated claim value
  "not derivable from" another claim). The local Nano answered `[]` in both rounds (2 tokens each). The second
  opinion can only add issues.
- **Writer.** The drafts cite claim ids, but the rule-based Critic still sends them back: they do not carry the
  caveat codes in brackets (rule C3, every claim, both rounds) and some numbers or citations do not map (rule B).
  After round 2 the flagged sentences are cut (`recovery{narrative_trimmed}`: 2, 6, 8, 8 and 6 sentences in the
  cloud runs, 2 in the local one) and the code-owned sentences a draft lacks are added from the claims
  (`recovery{narrative_completed}`: conflicts and caveats in every run; the verdict sentence in the five cloud runs,
  while the local draft stated it). The drafts are in `transcripts/`.
- **What the model never decides.** Claims, values, rejections and the verdict come from the rules and are the same
  with any model or none: the cloud and local ex01 runs give `self_settled_mostly` with 19 claims; ex04 is
  `adoption_thin` with 9 claims here because its settlement is verified live, while the offline replay
  (`../04-payer-dossier-unverifiable/`) cannot verify it and rejects `verified_tx`.

## The run recorded for the demo video (`nemotron-cloud/live-video-ex01-20261024/`, 18:55 UTC)

Made with the build of a8650c4, after the review: a second-opinion issue on a sentence whose every number rule B maps
to a claim that sentence cites is logged as `critique{verdict: dismissed}` and not acted on, and the Nightshift timeout
is 90 s. Five requests, all `routed_model: nvidia/nemotron-3.5-lightning-30b-a3b`, kind `cloud`. Unlike the six runs
above, both Critic replies and the second Writer draft reached their budgets (500, 500 and 700 output tokens in the
receipts; the Critic arrays kept 8 and 9 complete items, `recovery{generator_reply_repaired}` at seq 132 and 175).
The second opinion raised 8 issues in round 1, 4 of them dismissed, and 6 in round 2; 6 sentences were cut after
round 2 and the conflicts, caveats and verdict sentence added from the claims (seq 182-184). Claims and verdict: the
same 19 claims and `self_settled_mostly` as the 17:27 run, with one value moved, the Bazaar 30-day counter of the
host (c16), from 63 to 64. Zetaris was stopped (seq 1), so its SQL ran on SQLite.

## Compared with the Llama 3.2 3B run (`llama-3.2-3b/`)

Same inputs, same budgets, Zetaris stopped in both; the Llama runs used the prompts before the tightening above.

| | Llama 3.2 3B, local (`llama-3.2-3b/nightshift-ex0N-20261024/`) | Nemotron 3.5 Lightning, hosted (`nemotron-cloud/`) |
|---|---|---|
| Planner | ex01: invalid JSON twice, rules plan kept (`planner_fallback`); ex03-05 brought inside the schema (`generator_reply_repaired`) | used in 5 of 5; ex02 and ex05 after one re-ask |
| Critic replies that were unterminated JSON arrays | 7 of 10 (5 of them at the 500-token limit) | 0 of 10 |
| Critic issues added (rounds 1/2, ex01-05) | 20/19, 11/10, 12/13, 13/8, 12/13 | 7/2, 2/6, 2/8, 11/8, 10/6 |
| Sentences cut after round 2 | 10, 10, 13, 8, 13 | 2, 6, 8, 8, 6 |
| Verdict sentence added by code | 5 of 5 | 5 of 5 |

The hosted Nemotron follows the formats far better; it still drops caveat codes and conflict ids, so the final
answers are still completed from the claims, and several of its Critic issues are wrong. Some of the difference is
the prompt change, not the model: with the old prompts the same model filled every budget (`prompt-v1-ex01/`).
The hosted endpoint is not deterministic at temperature 0: the same planner prompt gave valid JSON in the fixture
recording and invalid JSON in `smoke-cloud-planner-20261024`, and examples 03 and 05, whose prompts are identical,
got different writer-r1 replies.

## Zetaris fell back to SQLite

Zetaris was stopped on purpose (the machine was short of memory: about 4 GB free, a reading of the session that is
not committed). Every run starts with
`recovery{zetaris_unreachable}` at seq 1 ("connection refused"; `zetaris.jsonl` of the two complete folders shows
the failed login) and runs the whole SQL on SQLite (`mounts.executor: "sqlite"`, `derivation.backend:
"sqlite-fallback"` on every claim), which is why every status is `partial`. The live trace computed on Zetaris Lightning SQL is `../live-trace/live-ex01-20261022/` (2026-10-22,
before Nightshift was installed, generator `null`).

## Relation to the sample-mode replays

The committed examples replay 25 other Nemotron answers (`../../fixtures/transcripts/<example>/`, 17 recorded
17:15-17:24 UTC and the round-2 Writer and Critic answers of examples 01, 03, 04 and 05 (8) re-recorded 18:43-18:44
UTC, after the dismissal of rule-B-backed issues changed those prompts, both with `METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b python scripts/record_transcripts.py
<example> --on-fixtures --prune`; see that folder's README). A transcript is keyed by the sha256 of its exact
prompt, and the live prompts here carry live data, so they never occur offline.

## Files

The complete folders (`nemotron-cloud-ex01-20261024/`, `nemotron-local-ex01-20261024/`, `live-video-ex01-20261024/`): `output.json`,
`report.md`, `run.jsonl`, `generator.jsonl`, `sql.jsonl`, `hmem.trace.jsonl`, `fetch.jsonl`, `zetaris.jsonl` and
`transcripts/` (each call's reply with its routing and `request` {`max_tokens`, `call_options`}, and the exact
prompt beside it). The other run folders: `output.json`, `report.md`, `run.jsonl`, `generator.jsonl`,
`transcripts/`. The World Model event files, raw responses, exports and the SQLite file stay in `logs/`. Before
these files were added they were checked for absolute paths, for `Bearer`, `nvapi-`, `idToken`, `password` and
`authorization` strings, and for any literal of 32 or more characters that is not hex or a path or identifier.
