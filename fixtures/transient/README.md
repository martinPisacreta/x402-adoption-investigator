# Replayed transient failures (sample mode)

Live requests sometimes fail for a moment: a 503 while a server restarts, a 429 from a rate-limited public RPC, a
timeout, a reset connection. The investigator retries those with one bounded policy,
`investigator/sources/retry.py` (`call_with_retries`): HTTP 429 and 5xx, timeouts and connection errors are
retried, any other 4xx is not; at most `--fetch-retries` retries (default 2, so 3 attempts); a fixed backoff of
500 ms and then 1500 ms; no backoff that would pass `budget.max_seconds`; one `retry` event per retry. Sample mode
replays committed fixtures, which never fail by themselves, so this folder lets an example replay a failure
through that same code.

## Convention

`fixtures/transient/<example>/faults.json`, where `<example>` is the folder under `examples/` (chosen like
`fixtures/transcripts/<example>/`: the example whose `input.json` carries the run's `run_id`, or `--example`):

```json
{
  "simulated": true,
  "note": "who wrote it, when, and why",
  "faults": [
    {"request_key": "GET https://tienda.textokit.store/receipts.json", "attempt": 1, "kind": "http", "http": 503,
     "note": "what this failure stands for"}
  ]
}
```

- `request_key` is a canonical request key from `fixtures/raw/MANIFEST.json` (SPEC.md 7.3).
- `attempt` (1-based) is the attempt that fails. Attempts the file does not list are served from the committed
  fixture, byte for byte the same as without the file.
- `kind` is `http` (with `http`, a status from 400 to 599), `timeout` or `connection_reset`. The file says what
  happens on the wire; whether it is retried is decided by the policy, exactly as for a live failure: a 503 or
  429 is retried, a 404 is not. Listing attempts 1, 2 and 3 makes the request give up after the third attempt
  and end `unavailable` (with `recovery{source_unavailable}`), as a live request would.
- `simulated` is required: `true` when the failures were written by hand, `false` only for failures actually
  observed on a source. The run's `output.sample_mode_notice` repeats it and names the file.
- A malformed file stops the run (`ValueError` naming the entry); it is never ignored.

`FixtureFetcher` loads the file only in sample mode; live runs never read this folder.

## What is here

| example | file | simulated | faults |
|---|---|---|---|
| `05-payer-dossier-retry` | `05-payer-dossier-retry/faults.json` | yes, written by hand on 2026-10-23 | attempt 1 of the store's `receipts.json` answers 503 (Collector); attempt 1 of the Base JSON-RPC `eth_getTransactionReceipt` for `0x62c0...58c3` answers 429 (Verifier) |

Neither failure was captured from the source: no committed live trace needed a retry, so there is no real
transient failure on record to replay. Example 05 shows what the same retry code does with one, and its numbers
are those of example 03 (same question, same fixtures): `tests/test_retry.py` checks both.
