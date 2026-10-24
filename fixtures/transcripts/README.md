# Recorded generator transcripts

`<example>/<prompt_id>-<prompt_sha12>.json` with `{prompt_id, prompt_sha256, model, recorded_at, response, usage,
provider, requested_model, routing, prompt_context, request}` and the exact prompt beside it as
`<prompt_id>-<prompt_sha12>.prompt.txt`. `provider` is `meterless` for a Nightshift call, `requested_model` the
model id or routing mode asked for, `model` the model that answered, `routing` the facts of the answer's Meterless
compute receipt (`receipt_id`, `routed_model`, `winning_route`, phases, `latency_ms`, token usage, cache), never its
prompt preview, and `request` the call's `max_tokens` and `call_options` (the thinking switch). Sample mode replays
them through `RecordedGenerator`, keyed by the sha256 of the exact prompt; every replayed call is a `generate` event
with `generator: "recorded"`, `recorded: true` and the recorded provider, model and routing. A miss falls back to
rules and templates with a logged `recovery{reason: "generator_fallback"}`.

## What is here (2026-10-24, NVIDIA Nemotron)

Five transcripts per example (01-05): planner, writer-r1, critic-r1, writer-r2, critic-r2; 25 in all, every one a
real answer of Meterless Nightshift 1.2.3 with the model pinned to `nvidia/nemotron-3.5-lightning-30b-a3b`:
NVIDIA Nemotron 3.5 Lightning 30B A3B on NVIDIA's hosted API, reached by Nightshift with the NVIDIA API key
configured inside Nightshift (each `routing.winning_route` says `provider: nvidia`, `kind: cloud`). Every request
carried `call_options: {"reasoning_effort": "none", "system_turn": "/no_think"}` (thinking off, the default of
`NIGHTSHIFT_REASONING_EFFORT`). 17 were recorded between 17:15 and 17:24 UTC; the round-2 Writer and Critic
answers of examples 01, 03, 04 and 05 (8) were recorded again at 18:43-18:44 UTC, because the Critic now dismisses
model issues on sentences rule B backs, which changed the issues handed to the round-2 Writer and so those prompts
(`recorded_at` in each file; the 8 they replaced were pruned). All with

```
METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b python scripts/record_transcripts.py <example> --on-fixtures --prune
```

which runs the example in sample mode (network guard on, committed fixtures only), sends the first prompt that has
no transcript to Nightshift once the sample run is over (the same call `generate()` makes: temperature 0, the role's
`max_tokens`, the Nightshift timeout, the call options), saves the answer and starts over until a sample run finds every
prompt recorded; then `--prune` deletes the folder's transcripts that run did not use. Hence `prompt_context:
"sample mode on the committed fixtures"`. Example 01's planner transcript is from the first recording (17:15 UTC);
its other four were recorded again at 17:19-17:20 after the Writer and Critic prompts were tightened (the Planner
prompt did not change). A live run's own transcripts (`--record-transcripts`, labelled `recorded_in_run`) cannot be
replayed here: its prompts carry live data. The live runs of the same day are in `examples/live-trace-nightshift/`.

Examples 03 and 05 send byte-identical planner and writer-r1 prompts (same claims; only the replayed fault script
differs), but the hosted model did not answer them identically, so the two folders share those two file names with
different responses and receipt ids, and the later prompts, which carry the Writer's draft, differ. Four replies
reach their token limit: example 01's critic-r1 (500 of 500 tokens; its 10 complete items are kept, logged as
`recovery{generator_reply_repaired}`) and writer-r2 (700 of 700), and the critic-r2 of examples 04 and 05 (500 of
500; 8 and 9 complete items kept). The model's replies are imperfect and
replayed as they are: see `examples/live-trace-nightshift/README.md` for what the Planner, Writer and Critic do with
them.

## Earlier set

The 25 transcripts replayed before (Llama 3.2 3B Q8, Nightshift's local lane through `meterless/auto`, recorded
16:11-16:17 UTC the same day) are archived with their README in
`examples/live-trace-nightshift/llama-3.2-3b/fixtures-transcripts/`. They no longer replay: the Writer and Critic
prompts changed.
