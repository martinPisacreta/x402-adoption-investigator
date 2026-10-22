# Memory ablation (example 01 with and without one seed memory)

Produced by `python scripts/memory_ablation.py --write examples/memory-ablation` (sample mode, same fixtures, same
code, same input; the only difference between the two runs is one line of `memory/seed/memories.jsonl` and its
ledger entry). Nothing in these files is edited by hand. Re-run the command to reproduce them.

| file | what it is |
|---|---|
| `with-memory.output.json` | example 01 (`ex01-seller-audit`) with the committed seed (byte-identical to `../01-seller-audit-textokit/output.json`) |
| `without-memory.output.json` | the same run with `mem-seed-003` ("receipts.json is self-reported by the seller ...") removed |
| `summary.json` | the differences between the two outputs, computed by the script: status, verdict, rounds, per-claim caveat and memory-id changes, answer sentences present in only one of the two, memory cited / feedback / retrieval counts |

What `summary.json` shows for this pair (read it rather than trust this paragraph): the `self_reported_receipts`
caveat disappears from the `settlements_verified` claim, which no registry rule attaches (it came from the memory,
whose entity `source:receipts` touches that claim's sources); the `[mem-seed-003]` citation disappears from the
caveat sentence and from the `caveat_memory_ids` of the receipts claims; the `helpful` feedback entry for the memory
and its retrievals disappear; status, verdict label, claim count and critique rounds do not change. That is the
honest size of the effect: in this example memory decides what the answer qualifies and cites, not the verdict.

Other pairs: `--without mem-seed-002 --example 03-payer-dossier-onchain`, `--without mem-seed-006 --example
02-catalog-concentration`, etc. The script exits 1 when removing the memory changes no claim.
