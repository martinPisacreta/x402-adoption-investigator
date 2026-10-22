#!/usr/bin/env python3
"""Run an example live with --record-transcripts and store the generator transcripts under
fixtures/transcripts/<example>/ (SPEC.md 7.4 rule 5 and section 11). Owner: B4.

Needs OPENAI_API_KEY (and optionally OPENAI_BASE_URL / MODEL) in .env.local; values are never printed. Every
generator call of the run (Planner, Writer rounds, Critic second opinions) is saved as
<prompt_id>-<prompt_sha12>.json with the prompt text beside it, so a later sample-mode run of the same example
replays them through RecordedGenerator (planner_source / answer source "recorded-generator").

    python scripts/record_transcripts.py 01-seller-audit-textokit
    python scripts/record_transcripts.py 01-seller-audit-textokit --require-zetaris   # also through Zetaris

The critique loop stays visible in sample mode only if the recorded first Writer attempt carried an issue the
Critic flags (a dropped caveat or an unmapped number). When the live Writer produces a clean first draft, record
with --first-draft-plain: the Writer prompt of round 1 is rendered without the caveat list, so the recorded
transcript omits the caveats and the pitfall gate fires on replay (the README says this is how the example was
produced).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import build_config  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402

EXAMPLES = ("01-seller-audit-textokit", "02-catalog-concentration", "03-payer-dossier-onchain")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("example", choices=EXAMPLES, help="examples/<folder> name")
    parser.add_argument("--require-zetaris", dest="require_zetaris", action="store_true")
    parser.add_argument("--first-draft-plain", dest="first_draft_plain", action="store_true",
                        help="render the round-1 Writer prompt without the caveat list (keeps the critique loop visible)")
    parser.add_argument("--out", default=str(REPO_ROOT / "logs"))
    args = parser.parse_args(argv)
    folder = REPO_ROOT / "examples" / args.example
    payload = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    config = build_config(type("Args", (), {"mode": "live", "sample": False, "out": args.out, "record_transcripts": True,
                                            "require_generator": True, "require_zetaris": args.require_zetaris,
                                            "example": args.example})())
    if not config.env.has_generator():
        print("OPENAI_API_KEY is not configured (.env.local); nothing recorded", file=sys.stderr)
        return 2
    if args.first_draft_plain:
        from investigator.agents import writer as writer_mod
        original = writer_mod.claims_table

        def plain_table(claims):   # round-1 prompt without the caveat column
            rows = json.loads(original(claims))
            for row in rows:
                row.pop("caveats", None)
            return json.dumps(rows, ensure_ascii=False, indent=1)
        writer_mod.claims_table = plain_table
    envelope = run_investigation(payload, config, echo_stdout=False)
    if envelope["status"] == "error":
        print(f"{args.example}: {envelope['error']['code']} {envelope['error']['message']}", file=sys.stderr)
        return 1
    target = config.transcripts_dir / args.example
    files = sorted(p.name for p in target.glob("*.json")) if target.is_dir() else []
    print(f"{args.example}: {envelope['status']}, {len(files)} transcript(s) under {target.relative_to(REPO_ROOT).as_posix()}")
    for name in files:
        print(f"  {name}")
    print("Re-run in sample mode to replay them: python run.py --sample --input "
          f"examples/{args.example}/input.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
