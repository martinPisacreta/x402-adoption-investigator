# Recorded generator transcripts (archived: the Llama 3.2 3B set)

_Archived on 2026-10-24, just before the Nemotron recordings (17:13 UTC onwards): this is `fixtures/transcripts/README.md` as it stood with the 25 transcripts
beside it, moved here unchanged below this note. The committed examples replayed them from f06bc36 until the set was
re-recorded with NVIDIA Nemotron 3.5 Lightning 30B A3B (`fixtures/transcripts/README.md`); they no longer replay,
because the Writer and Critic prompts were tightened that day. "The live runs of the same day" below are in `../`._

`<example>/<prompt_id>-<prompt_sha12>.json` with `{prompt_id, prompt_sha256, model, recorded_at, response, usage,
provider, requested_model, routing, prompt_context}` and the exact prompt beside it as
`<prompt_id>-<prompt_sha12>.prompt.txt`. `provider` is `meterless` for a Nightshift call, `requested_model` its
routing mode (`meterless/auto`), `model` the model that answered, and `routing` the facts of the answer's Meterless
compute receipt (`receipt_id`, `routed_model`, `winning_route`, phases, `latency_ms`, token usage, cache), never its
prompt preview. Sample mode replays them through `RecordedGenerator`, keyed by the sha256 of the exact prompt; every
replayed call is a `generate` event with `generator: "recorded"`, `recorded: true` and the recorded provider, model
and routing. A miss falls back to rules and templates with a logged `recovery{reason: "generator_fallback"}`.

## What is here (2026-10-24)

Five transcripts per example (01-05): planner, writer-r1, critic-r1, writer-r2, critic-r2; 25 in all, every one a
real answer of Meterless Nightshift 1.2.3, routing mode `meterless/auto`, routed to its one local lane
`local-runtime/llama-3.2-3b-instruct-q8` ("Llama 3.2 3B Q8 Verifier (Runtime GGUF)") on the demo machine (see
`examples/live-trace-nightshift/machine.txt`). They were recorded between 16:11 and 16:17 UTC with

```
python scripts/record_transcripts.py <example> --on-fixtures --prune
```

which runs the example in sample mode (network guard on, committed fixtures only), sends the first prompt that has
no transcript to Nightshift once the sample run is over (the same call `generate()` makes: temperature 0, the role's
`max_tokens`, 180 s timeout), saves the answer and starts over until a sample run finds every prompt recorded; then
`--prune` deletes the folder's transcripts that run did not use. Hence `prompt_context: "sample mode on the
committed fixtures"`. A live run's own transcripts (`--record-transcripts`, labelled `recorded_in_run`) cannot be
replayed here: its prompts carry live data. The live runs of the same day are in
`examples/live-trace-nightshift/`.

Example 05's prompts are byte-identical to example 03's (same claims; only the replayed fault script differs), and
Nightshift answered them identically, so the two folders hold the same responses under the same file names with
different receipt ids. The model's replies are imperfect and replayed as they are: see the README of
`examples/live-trace-nightshift/` for what the Planner, Writer and Critic do with them.
