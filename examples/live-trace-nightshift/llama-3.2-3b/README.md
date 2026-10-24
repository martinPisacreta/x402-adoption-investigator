# First live traces through Meterless Nightshift: Llama 3.2 3B (2026-10-24, 16:00-16:32 UTC)

_Archived on 2026-10-24, just before the Nemotron runs (17:13 UTC onwards), when the primary model became NVIDIA Nemotron 3.5 Lightning 30B A3B on NVIDIA's
hosted API through the same Nightshift (`../README.md`). This folder is the first Nightshift run, kept as it was:
the live runs and their `machine.txt` (moved here from `examples/live-trace-nightshift/`), and in
`fixtures-transcripts/` the 25 transcripts the committed examples replayed until then (moved from
`fixtures/transcripts/`, with that folder's README of the day). Those 25 no longer replay: the Writer and Critic
prompts were tightened later that day, so their prompts no longer occur, and the fixture set was re-recorded with
Nemotron. Paths below are relative to this folder._

Real runs of the five example inputs with Meterless Nightshift as the generator: every Planner, Writer and Critic
second-opinion request went to the local Nightshift runtime and was answered by its one local lane. Nothing here is
edited by hand; the files are copies of `logs/<run_id>/`.

## What ran

| folder | input | started (run_start, UTC) | requests through Nightshift | status | Nightshift receipts (`generate` events, in order) |
|---|---|---|---|---|---|
| `nightshift-ex01-20261024/` (complete) | `examples/ex1.json` | 2026-10-24T16:27:26Z | 6 (planner x2, writer-r1, critic-r1, writer-r2, critic-r2) | `partial` | rcpt_req_n6ld2jmx, rcpt_req_4xmsl6fh, rcpt_req_xcpf3ovw, rcpt_req_iw4bs9uh, rcpt_req_r9fhdfhq, rcpt_req_48hbujka |
| `nightshift-ex02-20261024/` | `examples/ex2.json` | 2026-10-24T16:28:27Z | 5 | `partial` | rcpt_req_4gaykwhx, rcpt_req_nrbpm6lr, rcpt_req_huwcpnw4, rcpt_req_jj07sc7x, rcpt_req_ralueqme |
| `nightshift-ex03-20261024/` | `examples/ex3.json` | 2026-10-24T16:29:10Z | 5 | `partial` | rcpt_req_o577nmtc, rcpt_req_k64qjc4n, rcpt_req_8y6d7q6y, rcpt_req_3cv27pey, rcpt_req_5bt6jleg |
| `nightshift-ex04-20261024/` | `examples/ex4.json` | 2026-10-24T16:29:47Z | 5 | `partial` | rcpt_req_vnkqhem6, rcpt_req_8sewde79, rcpt_req_5hnikrg9, rcpt_req_z6021966, rcpt_req_60qs0f24 |
| `nightshift-ex05-20261024/` | `examples/ex5.json` | 2026-10-24T16:30:21Z | 5 | `partial` | rcpt_req_fridkspg, rcpt_req_f5zq8jwm, rcpt_req_tghsvyux, rcpt_req_p154g99q, rcpt_req_q4bempy3 |
| `live-ns-ex01-20261024/` (first attempt) | `examples/ex1.json` | 2026-10-24T16:00:44Z | 6 | `partial` | rcpt_req_wtqluy7w, rcpt_req_v3gwd5jz, rcpt_req_cw3zytok, rcpt_req_72fzddjl, rcpt_req_4vgv0g2y, rcpt_req_9tw1bunz |

Command (one per row, `NN` and the example folder changing):

```
python run.py --mode live --require-generator --record-transcripts --example 01-seller-audit-textokit \
  --input examples/ex1.json --run-id nightshift-ex01-20261024 --print-output
```

No Nightshift variable was set. Auto Detect found the runtime at `http://127.0.0.1:8088/v1` and the token was read
from the runtime's own `<stateDir>/proxy-token` (`run_start.payload.generator_mount`: `found_by: auto_detect`,
`token_source: <stateDir>/proxy-token (default stateDir)`, the 18 `meterless/*` routing modes `/v1/models` listed).
The token is in no file here.

## Which model handled each request

All 32 `generate` events of the six runs (`run.jsonl`, repeated with the call parameters in `generator.jsonl`) say
`provider: meterless`, `requested_model: meterless/auto` and `model: local-runtime/llama-3.2-3b-instruct-q8`, and
carry `routing`, the facts of the answer's Meterless compute receipt (`meterless.compute_receipt.v1`): `receipt_id`,
`routed_model`, `winning_route` (`label: "Llama 3.2 3B Q8 Verifier (Runtime GGUF)"`, `provider: local-runtime`,
`kind: local`, `tier: A`), the phases with the model of each, `latency_ms`, token usage, `cache: miss` and
`privacy_path: local-only`. `output.json` lists the same calls per role (`agents[].generator_calls`) and `report.md`
has a "Model calls" table. The receipt's prompt preview, cost and host telemetry are not copied.

## The machine

`machine.txt` holds the commands and their output, captured right after the runs: Windows 11, an NVIDIA GeForce RTX
5080 Laptop GPU (driver 617.42), Meterless Nightshift 1.2.3 (`ns status`, the official CLI), and Nightshift's
`llama-server.exe` listed by `nvidia-smi` as a compute process on that GPU with the CUDA backend loaded
(`ggml-cuda.dll`, `nvcuda.dll`, `cudart64_12.dll`; it also loads the Vulkan backend). The active Nightshift fleet,
named `quicktest` in the Nightshift app (the name is not in the logs), has one lane: Llama-3.2-3B-Instruct Q8_0 GGUF.
No Nemotron model and no NVIDIA-hosted endpoint was used: none was configured in that Nightshift yet (the NVIDIA
key and the Nemotron lane were added later that day; `../README.md`).

## Zetaris fell back to SQLite

Zetaris was stopped on purpose for these runs (the machine was short of memory). Every run starts with
`recovery{zetaris_unreachable}` at seq 1 ("connection refused"; `zetaris.jsonl` of ex01 shows the failed login)
and runs the whole SQL on SQLite (`mounts.executor: "sqlite"`, `derivation.backend: "sqlite-fallback"`), which is why
every status is `partial`. The live trace computed on Zetaris Lightning SQL is
`../../live-trace/live-ex01-20261022/` (2026-10-22, before Nightshift was installed, generator `null`).

## What the 3B model's answers did

- **Planner.** Ex01 (both attempts): the reply listed hypotheses until the token limit, so its JSON never closed;
  after the one re-ask the Planner kept the rules plan (`recovery{planner_fallback}`, `plan.planner_source:
  "rules"`). Ex02-05: the reply was used (`planner_source: "generator"`); in ex03-05 only after it was brought inside
  the schema's limits, logged as `recovery{generator_reply_repaired}` (ex03 and ex04: the same hypothesis five
  times, kept once; ex05: four extra metric ids that are not in the registry dropped).
- **Critic second opinion.** 7 of the 10 replies of the five final runs stopped at the 500-token limit in the
  middle of the JSON array; their complete items are kept (`recovery{generator_reply_repaired}`, "N complete
  item(s) were kept"), and they flag many sentences of the draft. The second opinion can only add issues.
- **Writer.** The drafts cite claims but leave out caveat codes and conflict ids, so the rule-based Critic sends
  them back (rules C3 and C7 on the narrative, B where a number or citation does not map). After round 2 the flagged sentences are
  cut (`recovery{narrative_trimmed}`) and the code-owned sentences the answer lacks (conflicts, caveats, the verdict
  sentence) are added from the claims (`recovery{narrative_completed}`). The final answers are therefore mostly
  template sentences; the drafts are in `transcripts/`.
- **What the model never decides.** Claims, values, rejections and the verdict come from the rules and are the
  same with or without the model: ex04 is `adoption_thin` with 9 claims here because its settlement is verified
  live, while the offline replay (`../../04-payer-dossier-unverifiable/`) cannot verify it and rejects `verified_tx`.

`live-ns-ex01-20261024/` is the first run, made before the reply handling above existed: the Planner fell back to
rules and both Critic replies were discarded as "not a JSON array" (`recovery{generator_fallback}`). Its
transcripts are the replies that made us add `salvage_json_list` and the Planner's `normalise_reply`
(`investigator/generator.py`, `investigator/agents/planner.py`).

## Relation to the sample-mode replays

The transcripts the committed examples replayed (`fixtures-transcripts/<example>/` here, then in
`fixtures/transcripts/<example>/`) are not these calls. A
transcript is keyed by the sha256 of its exact prompt, and these prompts carry live data (fresh receipts, moved
counters), so they never occur offline. The replay set was recorded the same day, 16:11-16:17 UTC, by
`python scripts/record_transcripts.py <example> --on-fixtures --prune`: real Nightshift calls (same lane, receipts
included) on the prompts that sample mode builds from the committed fixtures. `--record-transcripts` still copied
these live transcripts into `fixtures/transcripts/` during the runs; `--prune` removed them there because no
sample-mode prompt uses them, and this folder keeps them.

## Files

`nightshift-ex01-20261024/`: `output.json`, `report.md`, `run.jsonl`, `generator.jsonl`, `sql.jsonl`,
`hmem.trace.jsonl`, `fetch.jsonl`, `zetaris.jsonl` and `transcripts/` (each call's reply with its routing, and
the exact prompt beside it). The other folders: `output.json`, `report.md`, `run.jsonl`, `generator.jsonl`,
`transcripts/`. The World Model event files, raw responses, exports and the SQLite file stay in `logs/`. Before
these files were added they were checked for absolute paths, for `Bearer`, `idToken`, `password` and
`authorization` strings, and for any literal of 32 or more characters that is not hex or a path or identifier.
