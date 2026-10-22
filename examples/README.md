# Examples

Canonical layout (SPEC.md section 11): one folder per example with `input.json` and, produced by
`scripts/regen_examples.py` in sample mode, `output.json`, `report.md`, `run.jsonl`, `sql.jsonl`,
`hmem.trace.jsonl`. Outputs are never edited by hand; `tests/test_examples_reproducible.py` re-runs the three
inputs and compares `output.json`, `run.jsonl`, `sql.jsonl`, `hmem.trace.jsonl` and `report.md` byte for byte (no
committed file carries a machine path: `sql.jsonl` names exports as `logs/<run_id>/exports/<file>`).

| folder | run_id | playbook | status | why |
|---|---|---|---|---|
| `01-seller-audit-textokit/` | `ex01-seller-audit` | seller_profile | `ok` | 19 claims, 2 conflicts, 1 settlement verified from the RPC fixtures |
| `02-catalog-concentration/` | `ex02-catalog-concentration` | catalog_concentration | `ok` | 11 claims with coverage, Conflict `ecosystem_size`, the per-endpoint unique-payer counter reported as a max with the caveat from mem-seed-001 (never summed) |
| `03-payer-dossier-onchain/` | `ex03-payer-dossier` | payer_dossier | `ok` | 9 claims, settlement decoded from the Transfer log, balance and nonce from the RPC fixtures |

Short paths: `ex1.json`, `ex2.json`, `ex3.json` are byte-identical copies of the three `input.json` files
(`python run.py --sample --input examples/ex1.json`), and `outputs/exN.output.json` / `outputs/exN.report.md` are
byte-identical copies of the corresponding `output.json` / `report.md`. The folders are the source of truth.

`live-trace/<run_id>/` holds real runs of the same questions on the demo machine (live fetches, live Zetaris
Lightning SQL, live Base JSON-RPC), labelled live so they can be diffed against the replays; see its README.

`memory-ablation/` holds the output of `scripts/memory_ablation.py`: example 01 run twice in sample mode, with the
committed H-MEM seed and with `mem-seed-003` removed, plus `summary.json` with the exact differences between the
two `output.json` files (see its README). It is how the effect of memory is shown instead of asserted.
