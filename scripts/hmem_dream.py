#!/usr/bin/env python3
"""Offline dreaming over memory/state: prints proposals; nothing materializes without --approve <id>. Owner: B2.

Usage:
  python scripts/hmem_dream.py                       # dream() over memory/state, list proposals (persisted in dreams.json)
  python scripts/hmem_dream.py --dir logs/<run_id>/hmem
  python scripts/hmem_dream.py --list                # pending proposals only, no new dreaming
  python scripts/hmem_dream.py --approve dream-3     # long_term record with derived_from lineage (ledger dream_approved)
  python scripts/hmem_dream.py --reject dream-3      # ledger dream_rejected, nothing created
Never run against memory/seed (the committed seed is read-only; copy it first).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SystemClock  # noqa: E402
from investigator.hmem.facade import mount_hmem  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=str(REPO_ROOT / "memory" / "state"), help="H-MEM persist dir (default memory/state)")
    parser.add_argument("--list", action="store_true", help="list pending proposals without dreaming")
    parser.add_argument("--approve", default=None, metavar="ID")
    parser.add_argument("--reject", default=None, metavar="ID")
    args = parser.parse_args(argv)
    persist_dir = Path(args.dir)
    if persist_dir.resolve() == (REPO_ROOT / "memory" / "seed").resolve():
        print("refusing to dream over memory/seed (committed, read-only); copy it to memory/state first", file=sys.stderr)
        return 2
    if not (persist_dir / "memories.jsonl").is_file():
        print(f"no memories.jsonl under {persist_dir}; nothing to dream about", file=sys.stderr)
        return 2
    hmem = mount_hmem(SystemClock(), persist_dir, actor="operator")
    print(f"loaded {len(hmem.store.all())} memories from {persist_dir}")
    if args.approve:
        created = hmem.dreaming.approve(args.approve, actor="operator")
        print(json.dumps({"approved": args.approve, "created": created.to_wire() if created else None}, indent=2, ensure_ascii=False))
        return 0
    if args.reject:
        hmem.dreaming.reject(args.reject, actor="operator")
        print(json.dumps({"rejected": args.reject}, indent=2))
        return 0
    proposals = hmem.dreaming.pending() if args.list else hmem.dreaming.dream()
    for proposal in proposals:
        print(json.dumps({"id": proposal.id, "type": proposal.type, "status": proposal.status, "derivedFrom": proposal.derived_from,
                          "content": proposal.content}, ensure_ascii=False))
    print(f"{len(proposals)} proposal(s); nothing materialized. Approve one with --approve <id>.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
