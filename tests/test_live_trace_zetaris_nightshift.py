"""examples/live-trace-zetaris-nightshift/: the six live runs that used Zetaris and Meterless Nightshift together.

The run folders are unedited copies of logs/<run_id>/ of 2026-10-24 22:36-22:42 UTC. These tests check, offline,
that each copy is what its README says: the file set and one transcript pair per request, every log line against
the event schema, Zetaris as the executor of every query and the backend of every claim (both halves computed and
equal), Nightshift as the generator of every request with the pinned model, no Zetaris or generator fallback, a
zetaris.jsonl that logged in, ran one run-query per sql event plus SHOW SERVER VERSION and cleaned up (its one
failed call, the CACHE TABLE that ran the driver out of heap in example 02, included), and the README table rendered
from those logs. The receipts are checked against Nightshift's signed ledger in tests/test_nightshift_ledger.py.
Owner: integrator.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.config import parse_request  # noqa: E402
from investigator.contracts import to_json  # noqa: E402
from investigator.runner import sha256_json  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402

FOLDER = REPO_ROOT / "examples" / "live-trace-zetaris-nightshift"
README = FOLDER / "README.md"
CLOUD = ("nvidia/nemotron-3.5-lightning-30b-a3b", "cloud")
LOCAL = ("local-runtime/nvidia-nvidia-nemotron-nano-9b-v2-q4-k-m", "local")
RUNS = {  # run_id: (route folder, input file, (pinned model, route kind))
    "zetaris-nightshift-ex01-20261024": ("nemotron-cloud", "examples/ex1.json", CLOUD),
    "zetaris-nightshift-ex02-20261024": ("nemotron-cloud", "examples/ex2.json", CLOUD),
    "zetaris-nightshift-ex03-20261024": ("nemotron-cloud", "examples/ex3.json", CLOUD),
    "zetaris-nightshift-ex04-20261024": ("nemotron-cloud", "examples/ex4.json", CLOUD),
    "zetaris-nightshift-ex05-20261024": ("nemotron-cloud", "examples/ex5.json", CLOUD),
    "zetaris-nightshift-local-ex01-20261024": ("nemotron-local", "examples/ex1.json", LOCAL),
}
FILES = {"fetch.jsonl", "generator.jsonl", "hmem.trace.jsonl", "output.json", "report.md", "run.jsonl", "sql.jsonl",
         "zetaris.jsonl"}
# Recoveries these runs may log: the model's reply repaired or the answer completed from the claims. A Zetaris or
# generator fallback would make the run partial and is not allowed here.
ALLOWED_RECOVERIES = {"generator_reply_repaired", "narrative_trimmed", "narrative_completed"}


def read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_dir(run_id: str) -> Path:
    return FOLDER / RUNS[run_id][0] / run_id


def readme_row(run_id: str) -> str:
    """The README table row of one run, rendered from its logs only."""
    events = read_jsonl(run_dir(run_id) / "run.jsonl")
    start, result = events[0], events[-1]
    generates = [e for e in events if e["type"] == "generate"]
    model = start["payload"]["mounts"]["generator"].split(":", 1)[1]
    queries = sum(1 for z in read_jsonl(run_dir(run_id) / "zetaris.jsonl") if z["op"] == "run-query")
    res = result["payload"]
    route, input_file, _ = RUNS[run_id]
    return (f"| `{route}/{run_id}/` | `{input_file}` | {start['at'][:19]}Z | {len(generates)} | `{res['status']}` | "
            f"`{res['executor']}` | `{model}` | {res['accepted']} / {res['rejected']} / {res['conflicts']} / "
            f"{res['unavailable']} | {', '.join(g['payload']['routing']['receipt_id'] for g in generates)} | {queries} |")


class ZetarisNightshiftTraces(unittest.TestCase):
    def test_the_six_runs_and_their_files(self) -> None:
        folders = {p.parent.name: p.parent for p in FOLDER.rglob("run.jsonl")}
        self.assertEqual(set(folders), set(RUNS))
        for run_id, path in folders.items():
            self.assertEqual(path, run_dir(run_id))
            self.assertEqual({p.name for p in path.iterdir() if p.is_file()}, FILES, run_id)
            generates = [e for e in read_jsonl(path / "run.jsonl") if e["type"] == "generate"]
            expected = set()
            for g in generates:
                stem = f"{g['payload']['prompt_id']}-{g['payload']['prompt_sha256'][:12]}"
                expected |= {f"{stem}.json", f"{stem}.prompt.txt"}
                prompt = (path / "transcripts" / f"{stem}.prompt.txt").read_bytes()
                self.assertEqual(hashlib.sha256(prompt).hexdigest(), g["payload"]["prompt_sha256"], stem)
                record = json.loads((path / "transcripts" / f"{stem}.json").read_text(encoding="utf-8"))
                self.assertEqual(hashlib.sha256(record["response"].encode("utf-8")).hexdigest(),
                                 g["payload"]["response_sha256"], stem)
                self.assertEqual(record["routing"]["receipt_id"], g["payload"]["routing"]["receipt_id"], stem)
            self.assertEqual({p.name for p in (path / "transcripts").iterdir()}, expected, run_id)

    def test_every_log_line_matches_the_event_schema(self) -> None:
        schema = load_schema("events")
        for run_id in RUNS:
            lines = read_jsonl(run_dir(run_id) / "run.jsonl")
            self.assertEqual([l["seq"] for l in lines], list(range(1, len(lines) + 1)), run_id)
            for line in lines:
                errors = validate(line, schema)
                errors += validate(line["payload"], schema["$defs"]["payloads"][line["type"]], "$.payload", schema)
                self.assertEqual(errors, [], f"{run_id} seq {line['seq']}")

    def test_zetaris_computed_every_claim_and_nightshift_answered_every_request(self) -> None:
        for run_id, (_, input_file, (model, kind)) in RUNS.items():
            path = run_dir(run_id)
            events = read_jsonl(path / "run.jsonl")
            start, result = events[0], events[-1]
            payload = json.loads((REPO_ROOT / input_file).read_text(encoding="utf-8"))
            _, inp = parse_request(payload, run_id)
            self.assertEqual(start["payload"]["input_sha256"], sha256_json(to_json(inp)), run_id)
            mounts = start["payload"]["mounts"]
            self.assertEqual((mounts["mode"], mounts["executor"], mounts["generator"], mounts["zetaris_version"]),
                             ("live", "zetaris", f"nightshift:{model}", "2.4.3.1"), run_id)
            self.assertEqual(start["payload"]["generator_mount"]["routing_mode"], model, run_id)
            self.assertEqual(result["payload"]["status"], "ok", run_id)
            self.assertEqual(result["payload"]["executor"], "zetaris", run_id)
            output = json.loads((path / "output.json").read_text(encoding="utf-8"))
            self.assertEqual(output["status"], "ok", run_id)
            out = output["output"]
            self.assertEqual([result["payload"][k] for k in ("accepted", "rejected", "conflicts", "unavailable")],
                             [len(out["claims"]), len(out["rejected_claims"]), len(out["conflicts"]),
                              len(out["unavailable"])], run_id)
            self.assertEqual({c["derivation"]["backend"] for c in out["claims"]}, {"zetaris"}, run_id)
            claims = [e["payload"] for e in events if e["type"] == "claim"]
            self.assertEqual(len(claims), len(out["claims"]), run_id)
            for claim in claims:
                self.assertEqual((claim["backend"], claim["cross_checked"]), ("zetaris", True), claim["claim_id"])
                self.assertIsNotNone(claim["sql_value"], claim["claim_id"])
                self.assertEqual(claim["sql_value"], claim["reducer_value"], claim["claim_id"])
            sql = [e["payload"] for e in events if e["type"] == "sql"]
            self.assertTrue(sql, run_id)
            self.assertEqual({s["executor"] for s in sql}, {"zetaris"}, run_id)
            self.assertEqual({(s["executor"], s["fallback_reason"]) for s in read_jsonl(path / "sql.jsonl")},
                             {("zetaris", None)}, run_id)
            recoveries = {e["payload"]["reason"] for e in events if e["type"] == "recovery"}
            self.assertLessEqual(recoveries, ALLOWED_RECOVERIES, run_id)
            generates = [e["payload"] for e in events if e["type"] == "generate"]
            self.assertEqual([g["prompt_id"] for g in generates],
                             ["planner", "writer-r1", "critic-r1", "writer-r2", "critic-r2"], run_id)
            for g in generates:
                self.assertEqual((g["provider"], g["model"], g["recorded"]), ("meterless", model, False), run_id)
                self.assertEqual((g["routing"]["routed_model"], g["routing"]["winning_route"]["kind"]), (model, kind))
            lines = read_jsonl(path / "generator.jsonl")
            self.assertEqual([(l["seq"], l["routing"]["receipt_id"]) for l in lines],
                             [(e["seq"], e["payload"]["routing"]["receipt_id"]) for e in events if e["type"] == "generate"])

    def test_zetaris_log_one_run_query_per_sql_event_and_one_failed_call(self) -> None:
        failed = []
        for run_id in RUNS:
            events = read_jsonl(run_dir(run_id) / "run.jsonl")
            calls = read_jsonl(run_dir(run_id) / "zetaris.jsonl")
            self.assertEqual((calls[0]["op"], calls[0]["http"]), ("login", 200), run_id)
            queries = [c for c in calls if c["op"] == "run-query"]
            self.assertEqual(queries[0]["sql"], "SHOW SERVER VERSION", run_id)
            self.assertEqual(len(queries), 1 + sum(e["type"] == "sql" for e in events), run_id)
            self.assertEqual({c["http"] for c in queries}, {200}, run_id)
            self.assertEqual(sum(c["op"] == "cleanup" for c in calls), 1, run_id)
            self.assertEqual((calls[-1]["op"], calls[-1]["http"]), ("ddl", 200), run_id)
            self.assertTrue(calls[-1]["sql"].startswith("DROP LIGHTNING DATABASE X402_"), run_id)
            failed += [(run_id, c["op"], c["http"], c["sql"]) for c in calls
                       if c["op"] != "artifact-server" and c["http"] != 200]
        self.assertEqual(failed, [("zetaris-nightshift-ex02-20261024", "ddl", 400,
                                   "CACHE TABLE X402_B4B1827B.bazaar_page0")])

    def test_readme_table_is_what_the_logs_say(self) -> None:
        text = README.read_text(encoding="utf-8")
        for run_id in RUNS:
            self.assertIn(readme_row(run_id), text, f"README row of {run_id} does not match its logs")


if __name__ == "__main__":
    unittest.main()
