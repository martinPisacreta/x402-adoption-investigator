#!/usr/bin/env python3
"""Record generator transcripts under fixtures/transcripts/<example>/ so sample mode replays real model answers
(SPEC.md 7.4 rule 5 and section 11). Owner: B4.

Needs a generator: Meterless Nightshift online (auto-detected, or NIGHTSHIFT_BASE_URL / METERLESS_BASE_URL; the
token from NIGHTSHIFT_TOKEN / METERLESS_RUNTIME_TOKEN / METERLESS_TOKEN, NIGHTSHIFT_TOKEN_FILE or the runtime's
<stateDir>/proxy-token), in .env.local or the shell; values are never printed. Two ways:

    python scripts/record_transcripts.py 01-seller-audit-textokit --on-fixtures --prune   # what sample mode replays
    python scripts/record_transcripts.py 01-seller-audit-textokit                         # one live run
    python scripts/record_transcripts.py 01-seller-audit-textokit --require-zetaris       # live, also via Zetaris

--on-fixtures (how the committed transcripts were made): a transcript is keyed by sha256 of the exact prompt, and a
live run's prompts carry live data (fresh counters, a new receipts file), so its transcripts do not match the
prompts of a sample-mode run on the committed fixtures. This mode therefore runs the example IN SAMPLE MODE (network
guard installed, fixtures only) with a recorder that replays what is already recorded and notes the first prompt
that has no transcript; after the run the guard is lifted, that exact prompt is sent once to Nightshift (the same
call generate() makes: temperature 0, the role's max_tokens, Nightshift's 90 s timeout) and the answer is saved
with its compute receipt; then the sample run starts over. It stops when a sample run finds every prompt recorded
(each answer feeds the next prompt, so the calls are made in pipeline order: Planner, Writer round 1, Critic round
1, ...). Every saved transcript is a real Nightshift answer to the prompt sample mode sends, labelled
`prompt_context: "sample mode on the committed fixtures"`. --prune then deletes the transcripts of that folder the
final replay did not use (for example ones a live run with --record-transcripts left there), so the folder holds
exactly what sample mode replays. Only Nightshift is accepted as the recorder (no OpenAI fallback).

Without --on-fixtures: one live run with --record-transcripts and --require-generator; its transcripts are copied
to the folder labelled `recorded_in_run: <run_id>`. They replay only where a sample-mode prompt is byte-identical.
--first-draft-plain (live only) renders the round-1 Writer prompt without the caveat list, so a clean first draft
still leaves the pitfall gate something to flag.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.contracts import GeneratorResult  # noqa: E402
from investigator.generator import (GENERATOR_TEMPERATURE, NightshiftGenerator, RecordedGenerator,  # noqa: E402
                                    build_generator, save_transcript, transcript_filename)
from investigator.guard import uninstall_network_guard  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402

EXAMPLES = ("01-seller-audit-textokit", "02-catalog-concentration", "03-payer-dossier-onchain",
            "04-payer-dossier-unverifiable", "05-payer-dossier-retry")
ON_FIXTURES_CONTEXT = "sample mode on the committed fixtures"
MAX_ROUNDS = 13          # budget.max_generator_calls is 12: one more sample run proves every prompt is recorded


class MissRecorder(RecordedGenerator):
    """RecordedGenerator that remembers the first prompt it has no transcript for (and which files it replayed)."""

    def __init__(self, transcripts_dir: Path) -> None:
        super().__init__(transcripts_dir)
        self.first_miss: Optional[tuple[str, str, int]] = None
        self.used: list[str] = []

    def complete(self, prompt_id: str, prompt: str, *, max_tokens: int, temperature: float,
                 timeout: float) -> Optional[GeneratorResult]:
        result = super().complete(prompt_id, prompt, max_tokens=max_tokens, temperature=temperature, timeout=timeout)
        if result is None:
            if self.first_miss is None:
                self.first_miss = (prompt_id, prompt, max_tokens)
        else:
            self.used.append(transcript_filename(prompt_id, prompt))
        return result


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def sample_replay(payload: dict[str, Any], recorder: MissRecorder, out: Path) -> dict[str, Any]:
    """One sample-mode run of the example with `recorder` as the mounted generator (network guard on, then off)."""
    config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out)})(), load_dotenv=False)
    try:
        with mock.patch.object(runner, "build_generator", lambda *args, **kwargs: recorder):
            return run_investigation(payload, config, echo_stdout=False)
    finally:
        uninstall_network_guard()     # the recorder's next Nightshift call happens outside any sample run


def record_on_fixtures(example: str, prune: bool) -> int:
    payload = json.loads((REPO_ROOT / "examples" / example / "input.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        live = build_config(type("Args", (), {"mode": "live", "sample": False, "out": tmp})())
        target = live.transcripts_dir / example
        mounted = build_generator(live, example)          # live detection, before any sample run installs the guard
        if not isinstance(mounted, NightshiftGenerator):
            why = getattr(mounted, "nightshift_unavailable", {}).get("detail", "Nightshift is not the mounted generator")
            print(f"{example}: Nightshift is required to record: {why}", file=sys.stderr)
            return 1
        print(f"{example}: recording with {mounted.name} at {mounted.base_url} (token: "
              f"{mounted.mount_info.get('token_source', 'none')}; call options: {mounted.call_options or 'none'})")
        recorder: Optional[MissRecorder] = None
        for round_no in range(1, MAX_ROUNDS + 1):
            recorder = MissRecorder(target)
            envelope = sample_replay(payload, recorder, Path(tmp) / f"replay-{round_no}")
            if envelope["status"] == "error":
                print(f"{example}: sample run failed: {envelope['error']['code']} {envelope['error']['message']}",
                      file=sys.stderr)
                return 1
            if recorder.first_miss is None:
                break
            prompt_id, prompt, max_tokens = recorder.first_miss
            result = mounted.complete(prompt_id, prompt, max_tokens=max_tokens, temperature=GENERATOR_TEMPERATURE,
                                      timeout=mounted.timeout_s)
            request = {"max_tokens": max_tokens,
                       **({"call_options": dict(mounted.call_options)} if mounted.call_options else {})}
            path = save_transcript(target, prompt_id, prompt, result, now_iso(),
                                   extra={"prompt_context": ON_FIXTURES_CONTEXT, "request": request})
            routing = result.routing or {}
            print(f"  {path.name}: {result.provider} {result.requested_model} -> {routing.get('routed_model') or result.model} "
                  f"receipt {routing.get('receipt_id', '-')} ({routing.get('latency_ms', '-')} ms)")
        else:
            print(f"{example}: still missing a transcript after {MAX_ROUNDS} sample runs", file=sys.stderr)
            return 1
    assert recorder is not None
    used = set(recorder.used)
    print(f"{example}: the sample replay uses {len(used)} transcript(s); status {envelope['status']}")
    if prune and target.is_dir():
        for path in sorted(target.glob("*.json")):
            if path.name not in used:
                path.unlink()
                prompt_file = path.with_name(path.name.replace(".json", ".prompt.txt"))
                if prompt_file.is_file():
                    prompt_file.unlink()
                print(f"  pruned {path.name} (not replayed in sample mode)")
    return 0


def record_live(example: str, require_zetaris: bool, out: str, first_draft_plain: bool = False) -> int:
    folder = REPO_ROOT / "examples" / example
    payload = json.loads((folder / "input.json").read_text(encoding="utf-8"))
    config = build_config(type("Args", (), {"mode": "live", "sample": False, "out": out, "record_transcripts": True,
                                            "require_generator": True, "require_zetaris": require_zetaris,
                                            "example": example})())
    if first_draft_plain:
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
        print(f"{example}: {envelope['error']['code']} {envelope['error']['message']}", file=sys.stderr)
        return 1
    target = config.transcripts_dir / example
    files = sorted(p.name for p in target.glob("*.json")) if target.is_dir() else []
    print(f"{example}: {envelope['status']}, {len(files)} transcript(s) under {target.relative_to(REPO_ROOT).as_posix()}")
    for name in files:
        print(f"  {name}")
    print("They replay in sample mode only where the prompt is byte-identical; use --on-fixtures for the replay set.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("example", choices=EXAMPLES, help="examples/<folder> name")
    parser.add_argument("--on-fixtures", dest="on_fixtures", action="store_true",
                        help="record Nightshift answers to the prompts of a sample-mode run (what sample mode replays)")
    parser.add_argument("--prune", action="store_true",
                        help="with --on-fixtures: delete the folder's transcripts the final replay did not use")
    parser.add_argument("--require-zetaris", dest="require_zetaris", action="store_true")
    parser.add_argument("--first-draft-plain", dest="first_draft_plain", action="store_true",
                        help="live: render the round-1 Writer prompt without the caveat list (keeps the critique loop visible)")
    parser.add_argument("--out", default=str(REPO_ROOT / "logs"))
    args = parser.parse_args(argv)
    if args.on_fixtures:
        return record_on_fixtures(args.example, args.prune)
    return record_live(args.example, args.require_zetaris, args.out, args.first_draft_plain)


if __name__ == "__main__":
    sys.exit(main())
