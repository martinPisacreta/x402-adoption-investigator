# Live traces

Each `<run_id>/` folder is a real run on the demo machine (Windows 11, Zetaris 2.4.3.1 in Docker, 2026-10-22):
live fetches of every public source, live Base JSON-RPC (`https://mainnet.base.org`), and, where noted, live
Zetaris Lightning SQL through the REST API. They are labelled live so judges can diff them against the
sample-mode replays in `../0N-*/` instead of finding a mismatch. Copied files: `output.json`, `report.md`,
`run.jsonl`, `sql.jsonl`, `zetaris.jsonl` (Zetaris runs only), `hmem.trace.jsonl`, `fetch.jsonl`. The World Model
event files (hundreds of KB per run) stay in `logs/`. No secret, token or header value appears in a trace: the
copies were grepped for the configured username, password and `Bearer`/`idToken` strings before being added.
The runs wrote their folders outside the repository (`--out` pointing at a scratch directory), and the
investigator then logged absolute export and raw paths in `fetch.jsonl`. On 2026-10-22 those 93 `export_path` /
`raw_path` values were rewritten to the run-relative form `logs/<run_id>/exports|raw/<file>` that the code now
writes for any run folder; nothing else in the files changed, and no hash covers those two fields (handoff hashes
strip them).

| folder | command | mounts | status | what it shows |
|---|---|---|---|---|
| `live-ex01-20261022/` | `python run.py --mode live --input examples/ex1.json --run-id live-ex01-20261022` | fetcher `live+fixture-fallback`, executor `zetaris` (2.4.3.1, source `captured`), generator `null` | `partial` | every claim computed on Zetaris (`derivation.backend: zetaris`), 4 of 4 sampled settlements verified against Base, `zetaris.jsonl` with login, DDL (database, REST tables, cache, container, views), 14 run-query calls (13 analytics queries plus `SHOW SERVER VERSION`), cleanup; the only recovery is `generator_fallback` (no model key), which is what makes the status `partial` |
| `live-ex03-20261022/` | `python run.py --mode live --require-zetaris --input examples/ex3.json --run-id live-ex03-20261022` | same, Zetaris required | `partial` | payer dossier live: 4 of 4 verified, USDC balance and nonce read live, `--require-zetaris` honoured |
| `live-nozetaris-ex03/` | same input with `ZETARIS_*` and `OPENAI_API_KEY` withheld, `--mode live` | fetcher `live+fixture-fallback`, executor `sqlite`, generator `null` | `partial` | graceful degradation: `recovery{zetaris_unreachable}` (whole run on SQLite), `recovery{generator_fallback}`, every SQL-backed number still computed and cross-checked, 4 of 4 settlements verified live |

The three traces were re-recorded on 2026-10-22 at 03:38 UTC, after the review fixes described in
`docs/BUILD_LOG.md` (the earlier recordings carried a staged `retry` of the Writer and a `basis_mismatch` caveat on
every receipts claim; neither exists any more). Each trace now finishes in one Critic round with no `retry` line:
the only `recovery` lines are the real ones named in the table (`generator_fallback`, and `zetaris_unreachable`
when the credentials were withheld), and `fetch.jsonl` shows every live request with its sha256.

The traces predate two later changes (2026-10-22 evening, `docs/BUILD_LOG.md`): in both example 03 traces the
balance and nonce claims are reducer-only (`derivation.backend: null`, `cross_checked: false`), because q22 and the
`chain_facts` view did not exist yet; and the Critic of that build accepted such single-computed claims, which its
rule C2 now rejects. The traces are kept as recorded, not re-run. The same holds for the text of Conflict k01 in
`live-ex01-20261022`: its explanation says vet402 lists endpoints "including delisted ones", which was wrong (the
vet402 count only takes rows with `catalog_status: listed`); the current code instead names the routes vet402 lists
that the store openapi does not declare (for the fixtures, `/api/maintenance-helper` and `/api/preparar-solicitud`;
see `../01-seller-audit-textokit/`).

Differences a reader will find between the replay and the live traces are real and expected: the receipts bytes
have a different sha256 (the store's `receipts.json` carries a `generatedAt` timestamp), the Bazaar 30-day call
counter for the host moved from 60 (fixture) to 61 (live), and the three transactions that are `unverifiable:
fixture_miss` in sample mode are `verified` live because the chain answered for them.
