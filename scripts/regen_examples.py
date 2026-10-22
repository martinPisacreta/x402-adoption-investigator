#!/usr/bin/env python3
"""Re-run the 3 examples in sample mode and rewrite examples/<name>/ (the ONLY way to update them). Owner: B4.

Copies output.json, report.md, run.jsonl, sql.jsonl and hmem.trace.jsonl from a fresh sample run into each
example folder, and a byte-identical copy of output.json to examples/outputs/<alias>.output.json (the short
paths that pair with examples/ex1.json .. ex3.json). Never edit those files by hand. Refuses to run while any
agent is still a stub (the output would carry scaffold_notice) unless --allow-scaffold is given.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import build_config  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402

EXAMPLES = ("01-seller-audit-textokit", "02-catalog-concentration", "03-payer-dossier-onchain")
ALIASES = {"01-seller-audit-textokit": "ex1", "02-catalog-concentration": "ex2", "03-payer-dossier-onchain": "ex3"}
COPIED = ("output.json", "report.md", "run.jsonl", "sql.jsonl", "hmem.trace.jsonl")
OUTPUTS_DIR = REPO_ROOT / "examples" / "outputs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=EXAMPLES, help="regenerate one example")
    parser.add_argument("--allow-scaffold", action="store_true", help="accept outputs that carry scaffold_notice")
    args = parser.parse_args(argv)
    names = [args.only] if args.only else list(EXAMPLES)
    with tempfile.TemporaryDirectory() as tmp:
        config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
        for name in names:
            folder = REPO_ROOT / "examples" / name
            payload = json.loads((folder / "input.json").read_text(encoding="utf-8"))
            envelope = run_investigation(payload, config, echo_stdout=False)
            if envelope["status"] == "error":
                print(f"{name}: {envelope['error']['code']} {envelope['error']['message']}", file=sys.stderr)
                return 1
            if "scaffold_notice" in envelope["output"] and not args.allow_scaffold:
                print(f"{name}: output carries scaffold_notice; refusing to commit stub output", file=sys.stderr)
                return 2
            run_dir = Path(tmp) / envelope["run_id"]
            for fname in COPIED:
                src = run_dir / fname
                if src.is_file():
                    shutil.copyfile(src, folder / fname)
            OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run_dir / "output.json", OUTPUTS_DIR / f"{ALIASES[name]}.output.json")
            shutil.copyfile(run_dir / "report.md", OUTPUTS_DIR / f"{ALIASES[name]}.report.md")
            print(f"{name}: {envelope['status']} ({len(envelope['output']['claims'])} claims, "
                  f"{len(envelope['output']['rejected_claims'])} rejected, {len(envelope['output']['unavailable'])} unavailable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
