# Demo script (3 minutes)

Owner: B4/integrator. The scripted judge path with the exact lines to point at. Event names below are the ones
`logs/<run_id>/run.jsonl` really carries (SPEC.md section 5); claim and conflict ids are those of the committed
examples once `scripts/regen_examples.py` has produced them.

## 0:00 Clean clone, one command
```
git clone <repo-url> && cd x402-adoption-investigator
python run.py --sample --input examples/01-seller-audit-textokit/input.json --print-output
```
Point at: the first stdout line is `guard` (network disabled, patched names listed), the second `run_start` with
`mounts` (`mode: sample`, `fetcher: fixture`, `executor: sqlite`, `generator`) and `input_sha256`.

## 0:30 The plan before any data moves
`plan` event: playbook `seller_profile`, the query ids (`q01`..`q18`), the source families, the metric ids and the
H-MEM memory ids retrieved for the Planner. It is logged before the first `fetch` line.

## 1:00 Handoffs, evidence, the World Model
`handoff` lines planner -> collector -> keeper -> verifier -> keeper -> analyst (each with an artifact sha256);
`fetch` lines with `sha256`, `fixture_file` and `replayed: true`; `ingest` pass 1 (`report.eventsAppended`,
`changed: true`); `verify` for the one settlement verifiable in sample mode (`0x62c0...58c3`, `receipt_match: true`)
and `unverifiable` + `fixture_miss` for the others; `sql` lines (`query_id`, `executor`, `sql_sha256`, `rowcount`);
`claim` lines with `sql_value` and `reducer_value` side by side (`cross_checked: true` when they agree);
`conflict` lines `endpoint_count_for_host` (five sources, five bases) and `calls_30d_for_host`.

## 1:45 Memory in the loop, measured
`claim` lines carry `caveats`; in `output.json` each caveat has the memory id behind it (`caveat_memory_ids`), e.g.
`settlements_verified` carries `self_reported_receipts` from `mem-seed-003` although no registry rule attaches it.
`critique` lines: one accept summary per rule with the claim count (C1-C7), `narrative_map` with every number
mapped to a claim and `unsourced: []`, one round (the Analyst and the Critic read the same H-MEM policy index, so
nothing is staged; C3 revises only when they disagree, `tests/test_critic.py`). Then open
`examples/memory-ablation/summary.json`: the same example 01 run without `mem-seed-003` loses that caveat on
`settlements_verified` and the `[mem-seed-003]` citation on fifteen claims, the feedback entry for the memory
disappears, and the verdict stays `self_settled_mostly`. Say that last part out loud: memory changes what the
answer qualifies and cites, not the verdict label, and the diff is committed. The scope rule against summing
`l30DaysUniquePayers` is code (C4); `tests/test_run_contract.py` shows the rejection path with a metric that exists
only inside the test (`rejected_claims[]`, status `partial`).

## 2:15 Honest output
`logs/ex01-seller-audit/output.json`: `mounts` first, `sample_mode_notice`, `claims[]` with evidence (URL + sha256,
`log://<run_id>#<seq>`, basescan tx), `derivation` (query, sql sha256, backend, reducer), `caveats` with the memory id
behind each code, the `conflicts[]` entry `endpoint_count_for_host` with five sources and the explanation,
`unavailable[]` (never a silent zero), `world.second_pass {eventsAppended: 0, changed: false}`, `memory.feedback`
with the exact H-MEM deltas, `agents[]` with the generator usage per role. `report.md` renders the same envelope.

## 2:30 The Critic says no (example 04)
```
python run.py --sample --input examples/ex4.json --print-output
```
The example 03 question about another external payer of the same store, whose one settlement is not in the RPC
fixtures. Point at, in `logs/ex04-unverified/run.jsonl` (the same lines as the committed
`examples/04-payer-dossier-unverifiable/run.jsonl`): the five `recovery{fixture_miss}` lines of the Base reads; the
`claim` line for `c07 verified_tx` with `sql_value: 0` and `reducer_value: 0` (seq 43); `critique` round 1 `C1`
`verdict: reject` on `c07` with the reason (seq 49); the narrative pass B `revise` lines for the sentences that
cite `c07` or print its 0 (seq 58-61); the `handoff` critic -> writer with 5 issues (seq 62); `recovery{narrative_trimmed}` from
the Writer naming `c07` and rule C1 (seq 63); round 2 accepting the six remaining claims; `result` with status
`partial`, 6 accepted, 1 rejected. `output.json` has `c07` in `rejected_claims[]` with its critique trail and the
verdict `insufficient_evidence`; the same transaction is `verified` in `examples/live-trace/live-ex01-20261022/`.

## 2:45 Same question live through Zetaris
```
python run.py --mode live --require-zetaris --input examples/01-seller-audit-textokit/input.json
```
Point at `logs/<run_id>/zetaris.jsonl` (DDL, views, run-query, timeUsed, cleanup) and `sql.jsonl` (executor
`zetaris`, `tables[]` with source URLs); `claim` lines now say `backend: zetaris`. Then `examples/live-trace/` for
the diff against the replay.
