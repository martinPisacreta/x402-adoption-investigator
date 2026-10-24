#!/usr/bin/env python3
"""One generator request through Meterless Nightshift, made and logged the way a run makes it (a smoke test).

    python scripts/nightshift_smoke.py --prompt-file <name>.prompt.txt --prompt-id critic-r1 --agent critic \
        --max-tokens 500 [--reasoning-effort none|default|low|...] [--run-id ID] [--out logs]

It builds the live run context of examples/ex1.json (runner.build_context: the mounts only; no source is fetched and
no SQL runs), logs a run_start with the generator mount, sends the prompt file's text through
investigator.generator.generate() (the call the Planner, Writer and Critic make: temperature 0, the given max_tokens,
Nightshift's 90 s timeout) and closes the log. Its run_start also records the request (`smoke_request`: agent,
prompt id, max_tokens, the prompt's sha256 and repository path, the --reasoning-effort given). logs/<run_id>/run.jsonl then holds the real `generate` event, or the
`recovery{generator_fallback}` with the error when the call failed; generator.jsonl the call parameters; transcripts/
the reply and the prompt. The model is METERLESS_MODEL / NIGHTSHIFT_MODEL, as in a run; --reasoning-effort sets
NIGHTSHIFT_REASONING_EFFORT for this one call (unset: the environment's value, default none). Prints one JSON line:
the routed model, the route kind, the receipt, latency, tokens and whether the reply parses as the role needs it
(critic: a JSON array, planner: a JSON object, writer: non-empty text). The token is never printed or logged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.clock import iso_from_ms  # noqa: E402
from investigator.config import build_config, default_run_id, parse_request  # noqa: E402
from investigator.contracts import to_json  # noqa: E402
from investigator.generator import NightshiftGenerator, generate, parse_json_object, salvage_json_list  # noqa: E402


def reply_shape(agent: str, text: str) -> str:
    """What the role would make of the reply: whole, cut (n complete items kept) or unusable."""
    if agent == "critic":
        items, salvaged = salvage_json_list(text)
        if items is None:
            return "no JSON array"
        return f"JSON array, {len(items)} item(s)" + (f", cut: {salvaged} complete item(s) salvaged" if salvaged else "")
    if agent == "planner":
        return "JSON object" if parse_json_object(text) is not None else "no JSON object"
    return f"text, {len(text.strip())} characters" if text.strip() else "empty"


def repo_relative(path: str) -> str:
    """The prompt file as a repository path, or its bare name outside the repository (never a machine path)."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt-file", required=True, help="the exact prompt text (a transcript's .prompt.txt)")
    parser.add_argument("--prompt-id", required=True, help="planner | writer-rN | critic-rN")
    parser.add_argument("--agent", required=True, choices=("planner", "writer", "critic"))
    parser.add_argument("--max-tokens", type=int, required=True, help="the role's budget: planner 400, writer 700, critic 500")
    parser.add_argument("--reasoning-effort", default=None, help="NIGHTSHIFT_REASONING_EFFORT for this call")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--out", default=str(REPO_ROOT / "logs"))
    args = parser.parse_args(argv)
    if args.reasoning_effort is not None:
        os.environ["NIGHTSHIFT_REASONING_EFFORT"] = args.reasoning_effort
    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    config = build_config(type("Args", (), {"mode": "live", "sample": False, "out": args.out})())
    payload = json.loads((REPO_ROOT / "examples" / "ex1.json").read_text(encoding="utf-8"))
    run_id, inp = parse_request(payload, args.run_id or "smoke-" + default_run_id()[len("run-"):])
    ctx = runner.build_context(run_id, inp, config, echo_stdout=False)
    try:
        if not isinstance(ctx.generator, NightshiftGenerator):
            why = getattr(ctx.generator, "nightshift_unavailable", {}).get("detail", "Nightshift is not mounted")
            print(json.dumps({"run_id": run_id, "error": why}))
            return 1
        ctx.wall_start_s = runner.wall_seconds()
        ctx.start_ms = ctx.clock.now_ms()
        ctx.deadline_ms = ctx.start_ms + inp.budget.max_seconds * 1000
        ctx.log.emit("run_start", "runner", {
            "mounts": to_json(ctx.mounts), "input_sha256": runner.sha256_json(to_json(inp)), "smoke_test": True,
            "budget": to_json(inp.budget), "deadline_at": iso_from_ms(ctx.deadline_ms), "generator_mount": ctx.generator.mount_info,
            # What was asked, so a failed request (no generate event, no transcript) still records it.
            "smoke_request": {"agent": args.agent, "prompt_id": args.prompt_id, "max_tokens": args.max_tokens,
                              "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                              "prompt_file": repo_relative(args.prompt_file),
                              "reasoning_effort": args.reasoning_effort}})
        result = generate(ctx, args.agent, args.prompt_id, prompt, max_tokens=args.max_tokens)
        summary: dict = {"run_id": run_id, "requested_model": ctx.generator.model,
                         "call_options": ctx.generator.call_options, "max_tokens": args.max_tokens}
        if result is None:
            failed = [r for r in ctx.recoveries if r.get("reason") == "generator_fallback"]
            summary["failed"] = failed[-1]["detail"] if failed else "no answer"
        else:
            routing = result.routing or {}
            route = routing.get("winning_route") or {}
            summary.update({"routed_model": routing.get("routed_model") or result.model, "route": route.get("label"),
                            "kind": route.get("kind"), "receipt_id": routing.get("receipt_id"),
                            "latency_ms": routing.get("latency_ms"), "usage": routing.get("usage") or result.usage,
                            "reply": reply_shape(args.agent, result.text)})
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        runner._cleanup(ctx)
        ctx.log.close()


if __name__ == "__main__":
    sys.exit(main())
