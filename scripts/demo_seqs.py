#!/usr/bin/env python3
"""The seq numbers docs/DEMO_SCRIPT.md points at, read from the committed logs (never typed by hand). Owner: integrator.

    python scripts/demo_seqs.py            # print the table
    python scripts/demo_seqs.py --write    # replace the table between the markers in docs/DEMO_SCRIPT.md

tests/test_demo_script.py fails when the table in docs/DEMO_SCRIPT.md is not what this script prints, so a
regenerated example cannot leave the demo script pointing at old lines. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_SCRIPT = REPO_ROOT / "docs" / "DEMO_SCRIPT.md"
BEGIN, END = "<!-- demo-seqs:begin (scripts/demo_seqs.py --write) -->", "<!-- demo-seqs:end -->"
EX01 = "examples/01-seller-audit-textokit/run.jsonl"
EX04 = "examples/04-payer-dossier-unverifiable/run.jsonl"
LIVE = "examples/live-trace-nightshift/nemotron-cloud/live-video-ex01-20261024/run.jsonl"
ZETARIS = "examples/live-trace/live-ex01-20261022/run.jsonl"

Event = dict[str, Any]


def is_(kind: str, **fields: Any) -> Callable[[Event], bool]:
    def pred(e: Event) -> bool:
        return e["type"] == kind and all(e["payload"].get(k) == v for k, v in fields.items())
    return pred


def handoff(src: str, dst: str) -> Callable[[Event], bool]:
    return lambda e: e["type"] == "handoff" and e["payload"]["from"] == src and e["payload"]["to"] == dst


def routing(e: Event) -> str:
    r = e["payload"].get("routing") or {}
    route = r.get("winning_route") or {}
    return (f"{e['payload']['prompt_id']}: {r.get('routed_model')}, kind {route.get('kind')}, {r.get('receipt_id')}"
            + (", recorded" if e["payload"].get("recorded") else ""))


# (log, label, predicate, all matches or the first, detail of a match)
POINTS: list[tuple[str, str, Callable[[Event], bool], bool, Callable[[Event], str]]] = [
    (EX01, "guard (network disabled)", is_("guard"), False, lambda e: f"{len(e['payload']['patched'])} calls patched"),
    (EX01, "run_start (mounts)", is_("run_start"), False, lambda e: f"generator {e['payload']['mounts']['generator']}"),
    (EX01, "plan", is_("plan"), False, lambda e: f"playbook {e['payload']['playbook']}, {len(e['payload']['query_ids'])} queries"),
    (EX01, "generate (replayed Nemotron answers)", is_("generate"), True, routing),
    (EX01, "verify (the settlement the fixtures cover)", is_("verify", status="verified"), False, lambda e: e["payload"]["tx"][:10] + "..."),
    (EX01, "conflict", is_("conflict"), True, lambda e: f"{e['payload'].get('conflict_id') or e['payload'].get('id')} {e['payload'].get('predicate')}"),
    (EX01, "narrative_map", is_("narrative_map"), True,
     lambda e: f"round {e['payload']['round']}: {len(e['payload']['tokens'])} tokens, {len(e['payload']['unsourced'])} unsourced"),
    (EX01, "critique LLM dismissed (rule B backs the sentence)", is_("critique", rule="LLM", verdict="dismissed"), True,
     lambda e: f"round {e['payload']['round']}, sentence {e['payload']['target']}, {', '.join(e['payload']['claim_ids'])}"),
    (EX01, "handoff critic -> writer", handoff("critic", "writer"), True, lambda e: f"issues {e['payload']['artifact'].get('count')}"),
    (EX01, "recovery narrative_trimmed / narrative_completed",
     lambda e: e["type"] == "recovery" and e["payload"]["reason"] in ("narrative_trimmed", "narrative_completed"), True,
     lambda e: e["payload"]["detail"][:60]),
    (EX01, "result", is_("result"), False, lambda e: f"{e['payload']['status']}, {e['payload']['accepted']} accepted, {e['payload']['critique_rounds']} rounds"),
    (EX04, "recovery fixture_miss (Base reads)", is_("recovery", reason="fixture_miss"), True, lambda e: e["payload"]["detail"][:24] + "..."),
    (EX04, "claim c07 verified_tx", is_("claim", claim_id="c07"), False,
     lambda e: f"value {e['payload']['value']}, sql {e['payload']['sql_value']}, reducer {e['payload']['reducer_value']}"),
    (EX04, "critique C1 reject", is_("critique", rule="C1", verdict="reject"), False, lambda e: f"target {e['payload']['target']}"),
    (EX04, "critique B revise, round 1", is_("critique", rule="B", round=1, verdict="revise"), True, lambda e: f"sentence {e['payload']['target']}"),
    (EX04, "critique LLM dismissed", is_("critique", rule="LLM", verdict="dismissed"), True,
     lambda e: f"round {e['payload']['round']}, sentence {e['payload']['target']}"),
    (EX04, "handoff critic -> writer", handoff("critic", "writer"), False, lambda e: f"issues {e['payload']['artifact'].get('count')}"),
    (EX04, "recovery narrative_trimmed", is_("recovery", reason="narrative_trimmed"), True, lambda e: e["payload"]["detail"][:60]),
    (EX04, "result", is_("result"), False,
     lambda e: f"{e['payload']['status']}, {e['payload']['accepted']} accepted, {e['payload']['rejected']} rejected"),
    (LIVE, "recovery zetaris_unreachable", is_("recovery", reason="zetaris_unreachable"), False, lambda e: "SQL on SQLite"),
    (LIVE, "run_start (generator_mount)", is_("run_start"), False,
     lambda e: f"{e['payload']['generator_mount']['base_url']}, found_by {e['payload']['generator_mount']['found_by']}"),
    (LIVE, "generate (live Nightshift requests)", is_("generate"), True, routing),
    (LIVE, "result", is_("result"), False, lambda e: f"{e['payload']['status']}, executor {e['payload']['executor']}"),
    (ZETARIS, "run_start (executor zetaris, generator null)", is_("run_start"), False,
     lambda e: f"Zetaris {e['payload']['mounts']['zetaris_version']}, generator {e['payload']['mounts']['generator']}"),
    (ZETARIS, "sql on Zetaris (the first; `grep '\"type\":\"sql\"'` lists all)", is_("sql", executor="zetaris"), False,
     lambda e: f"{e['payload']['query_id']}, executor {e['payload']['executor']}"),
]


def render() -> str:
    cache: dict[str, list[Event]] = {}
    rows = ["| log | what | seq | detail |", "|---|---|---|---|"]
    for log, label, pred, every, detail in POINTS:
        events = cache.setdefault(log, [json.loads(line) for line in (REPO_ROOT / log).read_text(encoding="utf-8").splitlines()])
        hits = [e for e in events if pred(e)]
        if not hits:
            raise SystemExit(f"{log}: no line for {label!r}")
        hits = hits if every else hits[:1]
        for e in hits:
            rows.append(f"| `{log.split('/')[-2]}` | {label} | {e['seq']} | {detail(e)} |")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the table in docs/DEMO_SCRIPT.md")
    args = parser.parse_args(argv)
    table = render()
    if not args.write:
        print(table)
        return 0
    text = DEMO_SCRIPT.read_text(encoding="utf-8")
    start, end = text.index(BEGIN) + len(BEGIN), text.index(END)
    DEMO_SCRIPT.write_text(text[:start] + "\n" + table + "\n" + text[end:], encoding="utf-8", newline="\n")
    print(f"wrote {len(table.splitlines()) - 2} rows into {DEMO_SCRIPT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
