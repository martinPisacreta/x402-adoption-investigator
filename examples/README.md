# Examples

Canonical layout (SPEC.md section 11): one folder per example with `input.json` and, produced by
`scripts/regen_examples.py` in sample mode, `output.json`, `report.md`, `run.jsonl`, `sql.jsonl`,
`hmem.trace.jsonl`. Outputs are never edited by hand; `tests/test_examples_reproducible.py` re-runs the four
inputs and compares `output.json`, `run.jsonl`, `sql.jsonl`, `hmem.trace.jsonl` and `report.md` byte for byte (no
committed file carries a machine path: `sql.jsonl` names exports as `logs/<run_id>/exports/<file>`).

| folder | run_id | playbook | status | why |
|---|---|---|---|---|
| `01-seller-audit-textokit/` | `ex01-seller-audit` | seller_profile | `ok` | 19 claims, 2 conflicts, 1 settlement verified from the RPC fixtures |
| `02-catalog-concentration/` | `ex02-catalog-concentration` | catalog_concentration | `ok` | 11 claims with coverage, Conflict `ecosystem_size`, the per-endpoint unique-payer counter reported as a max with the caveat from mem-seed-001 (never summed) |
| `03-payer-dossier-onchain/` | `ex03-payer-dossier` | payer_dossier | `ok` | 9 claims, settlement decoded from the Transfer log, balance and nonce from the RPC fixtures (decoded twice: by the view layer for SQL, by the Verifier for the reducer) |
| `04-payer-dossier-unverifiable/` | `ex04-unverified` | payer_dossier | `partial` | the same question about another external payer, whose one settlement is not in the RPC fixtures: the Analyst computes `verified_tx` = 0 (SQL and reducer agree), the Critic's rule C1 rejects it because no settlement of that wallet was decoded, the Writer's second round drops and names it, verdict `insufficient_evidence`; balance and nonce `unavailable` (`fixture_miss`) |

All 45 accepted claims of the four examples are computed twice (committed SQL on SQLite and a Python reducer) and
agree; example 04's rejected claim agreed too, which is why its rejection comes from C1 (evidence), not C2.
Example 04 is not staged: its input differs from example 03 only in the wallet, the registry has no entry written
for it, and the same transaction is `verified` in `live-trace/live-ex01-20261022/` (block 52,914,075), so the gap
is the offline fixtures, exactly what the answer says. `tests/test_example_04_critic.py` checks the committed files
and re-runs the input offline.

Short paths: `ex1.json` to `ex4.json` are byte-identical copies of the four `input.json` files
(`python run.py --sample --input examples/ex1.json`), and `outputs/exN.output.json` / `outputs/exN.report.md` are
byte-identical copies of the corresponding `output.json` / `report.md`. The folders are the source of truth.

`live-trace/<run_id>/` holds real runs of the same questions on the demo machine (live fetches, live Zetaris
Lightning SQL, live Base JSON-RPC), labelled live so they can be diffed against the replays; see its README.

`memory-ablation/` holds the output of `scripts/memory_ablation.py`: example 01 run twice in sample mode, with the
committed H-MEM seed and with `mem-seed-003` removed, plus `summary.json` with the exact differences between the
two `output.json` files (see its README). It is how the effect of memory is shown instead of asserted.
