# Examples

Canonical layout (SPEC.md section 11): one folder per example with `input.json` and, produced by
`scripts/regen_examples.py` in sample mode, `output.json`, `report.md`, `run.jsonl`, `sql.jsonl`,
`hmem.trace.jsonl`. Outputs are never edited by hand; `tests/test_examples_reproducible.py` re-runs the five
inputs and compares `output.json`, `run.jsonl`, `sql.jsonl`, `hmem.trace.jsonl` and `report.md` byte for byte (no
committed file carries a machine path: `sql.jsonl` names exports as `logs/<run_id>/exports/<file>`).

| folder | run_id | playbook | status | why |
|---|---|---|---|---|
| `01-seller-audit-textokit/` | `ex01-seller-audit` | seller_profile | `ok` | 19 claims, 2 conflicts, 1 settlement verified from the RPC fixtures |
| `02-catalog-concentration/` | `ex02-catalog-concentration` | catalog_concentration | `ok` | 11 claims with coverage, Conflict `ecosystem_size`, the per-endpoint unique-payer counter reported as a max with the caveat from mem-seed-001 (never summed) |
| `03-payer-dossier-onchain/` | `ex03-payer-dossier` | payer_dossier | `ok` | 9 claims, settlement decoded from the Transfer log, balance and nonce from the RPC fixtures (decoded twice: by the view layer for SQL, by the Verifier for the reducer) |
| `04-payer-dossier-unverifiable/` | `ex04-unverified` | payer_dossier | `partial` | the same question about another external payer, whose one settlement is not in the RPC fixtures: the Analyst computes `verified_tx` = 0 (SQL and reducer agree), the Critic's rule C1 rejects it because no settlement of that wallet was decoded, the Writer's second round drops and names it, verdict `insufficient_evidence`; balance and nonce `unavailable` (`fixture_miss`) |
| `05-payer-dossier-retry/` | `ex05-retry` | payer_dossier | `ok` | example 03's input with two transient failures replayed from `fixtures/transient/05-payer-dossier-retry/faults.json` (simulated: written by hand): the store's `receipts.json` answers 503 and the Base RPC receipt 429 on attempt 1; each is a `retry` line (500 ms backoff on the deterministic clock) and attempt 2 is served from the fixture; the same 9 claims and verdict as example 03 (the answer's wording differs: the hosted Nemotron answered the identical prompts differently) |

All 54 accepted claims of the five examples are computed twice (committed SQL on SQLite and a Python reducer) and
agree; example 04's rejected claim agreed too, which is why its rejection comes from C1 (evidence), not C2.
Example 04 is not staged: its input differs from example 03 only in the wallet, the registry has no entry written
for it, and the same transaction is `verified` in `live-trace/live-ex01-20261022/` (block 52,914,075), so the gap
is the offline fixtures, exactly what the answer says. `tests/test_example_04_critic.py` checks the committed files
and re-runs the input offline.

Example 05 is the committed log with retries. Its failures are scripted, not captured (no committed live trace
needed a retry, so there is no real transient failure to replay), and both the fault file and the output's
`sample_mode_notice` say so; the retry itself runs through `investigator/sources/retry.py`, the code live runs use.
Each `retry` line in `05-payer-dossier-retry/run.jsonl` names the stage, the request key, the failed attempt, the
reason and the backoff, and the next `fetch` line of that request key is the successful second attempt
(`attempts: 2`). Against example 03's `output.json`, what differs is the `log://` references and `derivation.log_seq`
(shifted by the two `retry` lines), the capture stamps of the three `fixture_miss` RPC elements (read from the clock after the
backoffs), `report_path`, the `sample_mode_notice` and the Collector's and Verifier's notes; every number is equal.
`tests/test_retry.py` checks the committed files and re-runs the input offline. The convention is in
[`../fixtures/transient/README.md`](../fixtures/transient/README.md).

Short paths: `ex1.json` to `ex5.json` are byte-identical copies of the five `input.json` files
(`python run.py --sample --input examples/ex1.json`), and `outputs/exN.output.json` / `outputs/exN.report.md` are
byte-identical copies of the corresponding `output.json` / `report.md`. The folders are the source of truth.

`live-trace/<run_id>/` holds real runs of the same questions on the demo machine (live fetches, live Zetaris
Lightning SQL, live Base JSON-RPC), labelled live so they can be diffed against the replays; see its README.
`live-trace-nightshift/` holds the live runs of 2026-10-24 whose every generator request went through Meterless
Nightshift (receipt per request; Zetaris was stopped, so SQLite): `nemotron-cloud/` on NVIDIA Nemotron 3.5 Lightning
30B A3B, NVIDIA's hosted API, `nemotron-local/` on Nemotron Nano 9B v2 on the local GPU, and `llama-3.2-3b/` the
first runs, on a local Llama 3.2 3B lane; see its README.
`live-trace-zetaris-nightshift/` holds the six live runs of 2026-10-24, 22:36-22:42 UTC, that used both in the same
run: every analytics query on Zetaris 2.4.3.1 and every generator request through Nightshift to Nemotron. Five of
them are hosted (`nemotron-cloud/`, examples 01-05) and one is local (`nemotron-local/`, example 01). All six have
status `ok`, the only committed live runs that do; see its README.

The generator-backed roles of the five examples replay recorded Meterless Nightshift answers
(`../fixtures/transcripts/<example>/`, recorded on the exact sample-mode prompts with
`scripts/record_transcripts.py <example> --on-fixtures --prune`): `mounts.generator` is `recorded`, each `generate`
line in `run.jsonl` names the provider, the routing mode, the model that answered and the receipt id with
`recorded: true`, and `report.md` lists them under "Model calls". The model is NVIDIA Nemotron 3.5 Lightning 30B A3B
on NVIDIA's hosted API, reached through Nightshift (`nvidia/nemotron-3.5-lightning-30b-a3b`, thinking off): its
drafts leave caveat codes and conflict ids out, so every example takes two Critic rounds and ends with
`recovery{narrative_trimmed}` and `recovery{narrative_completed}`; the claims, rejections and verdicts are the
rules', identical to a rules-only run.

`memory-ablation/` holds the output of `scripts/memory_ablation.py`: example 01 run twice in sample mode, with the
committed H-MEM seed and with `mem-seed-003` removed, plus `summary.json` with the exact differences between the
two `output.json` files (see its README). It is how the effect of memory is shown instead of asserted.
