#!/usr/bin/env python3
"""Memory ablation: run one example twice in sample mode, once with the committed H-MEM seed and once with one
pitfall memory removed, and report exactly what changed in the output. Owner: integrator.

This is the honest way to show that memory changes the output: nothing is planted in the registry, the same
input and the same fixtures run through the same code, and the only difference between the two runs is one
line of memory/seed/memories.jsonl (plus its ledger entry). The differences the script reports are read from the
two output.json files, never typed here.

    python scripts/memory_ablation.py                                   # ex01 without mem-seed-003, summary on stdout
    python scripts/memory_ablation.py --without mem-seed-002 --example 03-payer-dossier-onchain
    python scripts/memory_ablation.py --write examples/memory-ablation  # also writes summary.json there

Exit code 0 when the two runs differ in at least one claim caveat (memory did something), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import build_config  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402

EXAMPLES = ("01-seller-audit-textokit", "02-catalog-concentration", "03-payer-dossier-onchain")


def seed_without(memory_id: str, target: Path) -> dict[str, Any]:
    """Copy memory/seed into `target` minus one memory (memories.jsonl line and ledger entries)."""
    target.mkdir(parents=True, exist_ok=True)
    removed: dict[str, Any] = {"memory_id": memory_id, "content": None}
    for name in ("memories.jsonl", "ledger.jsonl"):
        kept: list[str] = []
        for line in (REPO_ROOT / "memory" / "seed" / name).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            key = record.get("id") if name == "memories.jsonl" else record.get("memoryId")
            if key == memory_id:
                if name == "memories.jsonl":
                    removed["content"] = record.get("content")
                    removed["entities"] = [e.get("name") for e in record.get("entities", [])]
                    removed["caveat_codes"] = [t.split(":", 1)[1] for t in record.get("tags", []) if t.startswith("caveat:")]
                continue
            kept.append(line)
        (target / name).write_text("\n".join(kept) + "\n", encoding="utf-8")
    if removed["content"] is None:
        raise SystemExit(f"{memory_id} is not in memory/seed/memories.jsonl")
    return removed


def run_once(example: str, out_dir: Path, seed_dir: Path | None) -> dict[str, Any]:
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out_dir)})(), load_dotenv=False)
    if seed_dir is not None:
        config.seed_dir_override = seed_dir
    payload = json.loads((REPO_ROOT / "examples" / example / "input.json").read_text(encoding="utf-8"))
    envelope = run_investigation(payload, config, echo_stdout=False)
    if envelope["status"] == "error":
        raise SystemExit(f"{example}: {envelope['error']['code']} {envelope['error']['message']}")
    return envelope


def claims_by_id(envelope: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in envelope["output"]["claims"] + envelope["output"]["rejected_claims"]}


def compare(full: dict[str, Any], without: dict[str, Any]) -> dict[str, Any]:
    a, b = claims_by_id(full), claims_by_id(without)
    claim_changes: list[dict[str, Any]] = []
    for cid in sorted(set(a) | set(b)):
        ca, cb = a.get(cid), b.get(cid)
        if ca is None or cb is None:
            claim_changes.append({"claim_id": cid, "metric": (ca or cb)["metric"], "present_with_memory": ca is not None,
                                  "present_without_memory": cb is not None})
            continue
        lost = [code for code in ca["caveats"] if code not in cb["caveats"]]
        gained = [code for code in cb["caveats"] if code not in ca["caveats"]]
        ids_lost = {code: mid for code, mid in ca["caveat_memory_ids"].items() if cb["caveat_memory_ids"].get(code) != mid}
        if lost or gained or ids_lost or ca["status"] != cb["status"] or ca["confidence"] != cb["confidence"]:
            claim_changes.append({"claim_id": cid, "metric": ca["metric"], "caveats_lost": lost, "caveats_gained": gained,
                                  "memory_ids_lost": ids_lost, "status": [ca["status"], cb["status"]],
                                  "confidence": [ca["confidence"], cb["confidence"]]})
    sa = set(full["output"]["answer"].split(". "))
    sb = set(without["output"]["answer"].split(". "))
    mem_a, mem_b = full["output"]["memory"], without["output"]["memory"]
    return {
        "status": [full["status"], without["status"]],
        "verdict": [full["output"]["verdict"]["label"], without["output"]["verdict"]["label"]],
        "critique_rounds": [full["output"]["critique_rounds"], without["output"]["critique_rounds"]],
        "claims": [len(full["output"]["claims"]), len(without["output"]["claims"])],
        "claim_changes": claim_changes,
        "answer_sentences_only_with_memory": sorted(sa - sb),
        "answer_sentences_only_without_memory": sorted(sb - sa),
        "memory_cited": [mem_a["cited"], mem_b["cited"]],
        "memory_feedback": [mem_a["feedback"], mem_b["feedback"]],
        "memory_retrieved_count": [len(mem_a["retrieved"]), len(mem_b["retrieved"])],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--example", choices=EXAMPLES, default="01-seller-audit-textokit")
    parser.add_argument("--without", default="mem-seed-003", help="seed memory id to remove for the second run")
    parser.add_argument("--write", metavar="DIR", help="write summary.json (and both output.json files) into DIR")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        removed = seed_without(args.without, tmp_path / "seed-without")
        full = run_once(args.example, tmp_path / "full", None)
        without = run_once(args.example, tmp_path / "without", tmp_path / "seed-without")
        summary = {"example": args.example, "removed_memory": removed, "mode": "sample (same fixtures, same code, same input)",
                   "diff": compare(full, without)}
        text = json.dumps(summary, indent=1, ensure_ascii=False)
        print(text)
        if args.write:
            out = REPO_ROOT / args.write if not Path(args.write).is_absolute() else Path(args.write)
            out.mkdir(parents=True, exist_ok=True)
            (out / "summary.json").write_text(text + "\n", encoding="utf-8")
            shutil.copyfile(tmp_path / "full" / full["run_id"] / "output.json", out / "with-memory.output.json")
            shutil.copyfile(tmp_path / "without" / without["run_id"] / "output.json", out / "without-memory.output.json")
            print(f"written: {out}", file=sys.stderr)
    return 0 if summary["diff"]["claim_changes"] else 1


if __name__ == "__main__":
    sys.exit(main())
