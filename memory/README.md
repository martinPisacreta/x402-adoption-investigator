# H-MEM memory

- `seed/memories.jsonl`: the seven committed pitfall memories (SPEC.md section 8.3), on-disk camelCase shape of
  the Meterless H-MEM record (`engines/hmem/docs/memory-record.md`). The caveat code each memory requires is the
  tag `caveat:<code>`; the gating entities are `field:<name>` / `source:<family>`. `embedding` is null on disk:
  the store computes the deterministic FNV-1a embedding on load. A run never mutates this folder.
- `seed/ledger.jsonl`: one `create` ledger entry per seed record (actor `seed`).
- `state/`: live-mode persistent store (gitignored). Sample mode copies `seed/` into `logs/<run_id>/hmem/`.
