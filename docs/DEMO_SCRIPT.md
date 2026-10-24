# Demo script (about 4 minutes)

Owner: integrator. The scripted judge path, with the lines to point at. Every seq number below is in the table at
the end, which `python scripts/demo_seqs.py --write` reads from the committed logs; `tests/test_demo_script.py`
fails when the table and the logs disagree, so this page cannot point at old lines after an example is regenerated.
Event names are the ones `logs/<run_id>/run.jsonl` carries (SPEC.md section 5).

## 0:00 Clean clone, one command, offline
```
git clone https://github.com/martinPisacreta/x402-adoption-investigator && cd x402-adoption-investigator
python run.py --sample --input examples/ex1.json --print-output
```
The stdout is `logs/ex01-seller-audit/run.jsonl`, the same lines as the committed
`examples/01-seller-audit-textokit/run.jsonl`. Point at: `guard` (the socket calls patched), `run_start` with
`mounts` (`mode: sample`, `executor: sqlite`, `generator: recorded`), and the `plan` (playbook, query ids, metric
ids, the H-MEM memories retrieved for the Planner).

## 0:30 The model's requests, replayed with their receipts
The five `generate` lines (planner, writer-r1, critic-r1, writer-r2, critic-r2): each names `provider: meterless`,
`routing.routed_model: nvidia/nemotron-3.5-lightning-30b-a3b`, `winning_route.kind: cloud`, the compute receipt id
and `recorded: true`. These are real answers Nemotron gave through Nightshift on 24 Oct to exactly these prompts
(`fixtures/transcripts/01-seller-audit-textokit/`, keyed by the prompt's sha256); sample mode opens no socket.

## 1:00 Evidence, the World Model, every number twice
`handoff` lines with an artifact sha256; `fetch` lines with `sha256` and `replayed: true`; `ingest` pass 1; `verify`
for the one settlement the fixtures cover (`receipt_match: true`) and `fixture_miss` recoveries for the others;
`sql` lines; `claim` lines with `sql_value` and `reducer_value` side by side; the two `conflict` lines
(`endpoint_count_for_host`, five sources and five bases, and `calls_30d_for_host`).

## 1:30 The Critic and the model's second opinion
Round 1: C1-C7 accept the 19 claims; C3 revises the narrative because the draft carries no caveat codes; pass B
flags the number 15 (17 services are listed); the model's second opinion adds issues, and the ones on sentences whose
numbers rule B maps to the claims they cite are logged `verdict: dismissed` with the claim ids and not acted on.
The `handoff` critic -> writer carries the rest. After round 2, `narrative_trimmed` and `narrative_completed`: the
flagged sentence is cut and the conflicts, caveats and verdict sentence are added from the claims. The verdict is
chosen by rules. Then `examples/memory-ablation/summary.json`: without `mem-seed-003` one caveat and fifteen
citations disappear and the verdict stays `self_settled_mostly`.

## 2:15 The Critic says no (example 04)
```
python run.py --sample --input examples/ex4.json --print-output
```
The example 03 question about another external payer of the same store, whose settlement is not in the RPC fixtures:
five `fixture_miss` recoveries for the Base reads; `claim` c07 `verified_tx` = 0 in SQL and in Python; `critique`
C1 `reject` on c07; pass B on the sentences that cite c07 or print its 0; dismissed model issues; the `handoff`
critic -> writer; `narrative_trimmed` naming c07; `result` partial, 6 accepted, 1 rejected; `output.json` has c07 in
`rejected_claims[]` with its critique trail and the verdict `insufficient_evidence`. The same transaction is
`verified` in `examples/live-trace/live-ex01-20261022/`.

## 3:00 One real request through Nightshift, live
```
METERLESS_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b python run.py --mode live --require-generator --input examples/ex1.json
```
Needs Meterless Nightshift running on the machine (Auto Detect finds it; the token is read from the runtime's own
`proxy-token`) with an NVIDIA API key in its Setup > Providers tab. Pinning the model makes every receipt name
Nemotron; the default `meterless/auto` may pick another lane. Point at: `run_start.payload.generator_mount`
(`base_url`, `found_by`, `routing_mode`, `call_options`, the token's source by variable name), then each `generate`
line: `routing.routed_model`, `routing.winning_route.kind`, `routing.receipt_id`, `latency_ms`; `generator.jsonl`
repeats each seq with the call parameters, and `transcripts/` holds each prompt and reply. The committed run of
this command (the one in the demo video) is `examples/live-trace-nightshift/nemotron-cloud/live-video-ex01-20261024/`.
With Zetaris stopped it starts with `recovery{zetaris_unreachable}` and runs the SQL on SQLite. Then Nightshift's
side: `python scripts/nightshift_ledger.py <that run folder>` finds each receipt in the runtime's own signed ledger
and verifies the Ed25519 signature (add `--ledger examples/live-trace-nightshift/nightshift-ledger/receipts.jsonl`
on a machine without that Nightshift).

## 3:30 Zetaris, the data layer of live runs
```
python run.py --mode live --require-zetaris --input examples/ex1.json
```
Needs the local Zetaris instance and its login in `.env.local` or `.env`. Point at `zetaris.jsonl` (login, DDL,
views, run-query, cleanup), `sql.jsonl` (executor `zetaris`, `tables[]` with source URLs) and `claim` lines with
`backend: zetaris`. The committed run on Zetaris is `examples/live-trace/live-ex01-20261022/` (22 Oct, before
Nightshift was connected, so generator `null`); no committed run has Zetaris and Nightshift together.

## The lines, by seq

<!-- demo-seqs:begin (scripts/demo_seqs.py --write) -->
| log | what | seq | detail |
|---|---|---|---|
| `01-seller-audit-textokit` | guard (network disabled) | 1 | 5 calls patched |
| `01-seller-audit-textokit` | run_start (mounts) | 2 | generator recorded |
| `01-seller-audit-textokit` | plan | 6 | playbook seller_profile, 13 queries |
| `01-seller-audit-textokit` | generate (replayed Nemotron answers) | 5 | planner: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_v4ul6h0r, recorded |
| `01-seller-audit-textokit` | generate (replayed Nemotron answers) | 81 | writer-r1: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_kptyl67r, recorded |
| `01-seller-audit-textokit` | generate (replayed Nemotron answers) | 118 | critic-r1: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_br1j413t, recorded |
| `01-seller-audit-textokit` | generate (replayed Nemotron answers) | 131 | writer-r2: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_oyjn2epx, recorded |
| `01-seller-audit-textokit` | generate (replayed Nemotron answers) | 162 | critic-r2: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_xqy5uvhi, recorded |
| `01-seller-audit-textokit` | verify (the settlement the fixtures cover) | 32 | 0x62c01526... |
| `01-seller-audit-textokit` | conflict | 77 | k01 endpoint_count_for_host |
| `01-seller-audit-textokit` | conflict | 78 | k02 calls_30d_for_host |
| `01-seller-audit-textokit` | narrative_map | 116 | round 1: 49 tokens, 1 unsourced |
| `01-seller-audit-textokit` | narrative_map | 160 | round 2: 46 tokens, 1 unsourced |
| `01-seller-audit-textokit` | critique LLM dismissed (rule B backs the sentence) | 120 | round 1, sentence 1, c02, c03 |
| `01-seller-audit-textokit` | critique LLM dismissed (rule B backs the sentence) | 121 | round 1, sentence 1, c02, c03 |
| `01-seller-audit-textokit` | critique LLM dismissed (rule B backs the sentence) | 122 | round 1, sentence 2, c04 |
| `01-seller-audit-textokit` | critique LLM dismissed (rule B backs the sentence) | 123 | round 1, sentence 3, c05 |
| `01-seller-audit-textokit` | critique LLM dismissed (rule B backs the sentence) | 124 | round 1, sentence 4, c06 |
| `01-seller-audit-textokit` | handoff critic -> writer | 130 | issues 25 |
| `01-seller-audit-textokit` | recovery narrative_trimmed / narrative_completed | 168 | 1 sentence(s) cut after the last critic round |
| `01-seller-audit-textokit` | recovery narrative_trimmed / narrative_completed | 169 | 7 code-owned sentence(s) added after the last critic round ( |
| `01-seller-audit-textokit` | recovery narrative_trimmed / narrative_completed | 170 | verdict sentence added: the final answer did not state self_ |
| `01-seller-audit-textokit` | result | 182 | ok, 19 accepted, 2 rounds |
| `04-payer-dossier-unverifiable` | recovery fixture_miss (Base reads) | 15 | rpc_receipt_0x58ddb2e77e... |
| `04-payer-dossier-unverifiable` | recovery fixture_miss (Base reads) | 18 | rpc_receipt_0x3ca4f2791f... |
| `04-payer-dossier-unverifiable` | recovery fixture_miss (Base reads) | 21 | rpc_receipt_0x068b193026... |
| `04-payer-dossier-unverifiable` | recovery fixture_miss (Base reads) | 27 | rpc_balance_0xa1d1c0cf71... |
| `04-payer-dossier-unverifiable` | recovery fixture_miss (Base reads) | 29 | rpc_nonce_0xa1d1c0cf7174... |
| `04-payer-dossier-unverifiable` | claim c07 verified_tx | 44 | value 0, sql 0, reducer 0 |
| `04-payer-dossier-unverifiable` | critique C1 reject | 51 | target c07 |
| `04-payer-dossier-unverifiable` | critique B revise, round 1 | 66 | sentence 5 |
| `04-payer-dossier-unverifiable` | critique B revise, round 1 | 67 | sentence 5 |
| `04-payer-dossier-unverifiable` | critique B revise, round 1 | 68 | sentence 5 |
| `04-payer-dossier-unverifiable` | critique B revise, round 1 | 69 | sentence 7 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 71 | round 1, sentence 0 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 72 | round 1, sentence 1 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 73 | round 1, sentence 2 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 75 | round 1, sentence 4 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 101 | round 2, sentence 0 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 102 | round 2, sentence 1 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 104 | round 2, sentence 3 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 106 | round 2, sentence 5 |
| `04-payer-dossier-unverifiable` | critique LLM dismissed | 108 | round 2, sentence 7 |
| `04-payer-dossier-unverifiable` | handoff critic -> writer | 80 | issues 16 |
| `04-payer-dossier-unverifiable` | recovery narrative_trimmed | 82 | round 2: 1 claim(s) rejected by the Critic left out of the a |
| `04-payer-dossier-unverifiable` | recovery narrative_trimmed | 109 | 3 sentence(s) cut after the last critic round |
| `04-payer-dossier-unverifiable` | result | 121 | partial, 6 accepted, 1 rejected |
| `live-video-ex01-20261024` | recovery zetaris_unreachable | 1 | SQL on SQLite |
| `live-video-ex01-20261024` | run_start (generator_mount) | 2 | http://127.0.0.1:8088/v1, found_by auto_detect |
| `live-video-ex01-20261024` | generate (live Nightshift requests) | 6 | planner: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_t1f1teb8 |
| `live-video-ex01-20261024` | generate (live Nightshift requests) | 83 | writer-r1: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_c0jovllr |
| `live-video-ex01-20261024` | generate (live Nightshift requests) | 131 | critic-r1: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_pl9z663t |
| `live-video-ex01-20261024` | generate (live Nightshift requests) | 142 | writer-r2: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_v4ma9ufl |
| `live-video-ex01-20261024` | generate (live Nightshift requests) | 174 | critic-r2: nvidia/nemotron-3.5-lightning-30b-a3b, kind cloud, rcpt_req_y94ho7dr |
| `live-video-ex01-20261024` | result | 231 | partial, executor sqlite |
| `live-ex01-20261022` | run_start (executor zetaris, generator null) | 2 | Zetaris 2.4.3.1, generator null |
| `live-ex01-20261022` | sql on Zetaris (the first; `grep '"type":"sql"'` lists all) | 42 | q01_sales_by_wallet_class, executor zetaris |
<!-- demo-seqs:end -->
