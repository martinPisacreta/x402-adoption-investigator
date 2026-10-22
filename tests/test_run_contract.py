"""run.py I/O contract: schemas, error codes, the partial rule, and the sample-mode end-to-end runs (SPEC.md 4). Owner: B4.

Written with unittest so it runs with `python -m unittest discover tests` (no pytest needed) and under pytest.
The three examples are `ok` in sample mode. The status rule (any rejected claim -> `partial`) and the Critic's C4
scope rule are exercised here with a metric registered only for the duration of one test, never shipped in the
registry (rule 2 of .cursor/rules/project.mdc: no hardcoded demo answer).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator import runner  # noqa: E402
from investigator.agents import reducers as reducers_mod  # noqa: E402
from investigator.agents.metrics import METRICS, MetricSpec  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.contracts import AgentResult  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402

EXAMPLES = {
    "01-seller-audit-textokit": ("ex01-seller-audit", "seller_profile"),
    "02-catalog-concentration": ("ex02-catalog-concentration", "catalog_concentration"),
    "03-payer-dossier-onchain": ("ex03-payer-dossier", "payer_dossier"),
}
PITCH_NUMBERS = ("28462", "2713", "824796", "44000", "85.7")


def sample_config(out_dir: Path):
    return build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out_dir)})(), load_dotenv=False)


def live_config_without_env(out_dir: Path, **flags):
    args = type("Args", (), {"mode": "live", "sample": False, "out": str(out_dir), **flags})()
    return build_config(args, environ={}, load_dotenv=False)


def example_payload(folder: str) -> dict:
    return json.loads((REPO_ROOT / "examples" / folder / "input.json").read_text(encoding="utf-8"))


def run_example(folder: str, out_dir: Path) -> dict:
    return run_investigation(example_payload(folder), sample_config(out_dir), echo_stdout=False)


class SampleRunContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_example_01_runs_end_to_end_in_sample_mode(self) -> None:
        envelope = run_example("01-seller-audit-textokit", self.out_dir)
        self.assertIn(envelope["status"], ("ok", "partial"), envelope.get("error"))
        self.assertEqual(envelope["run_id"], "ex01-seller-audit")
        output = envelope["output"]
        self.assertEqual(list(output.keys())[0], "mounts")
        self.assertEqual(output["mounts"]["mode"], "sample")
        self.assertEqual(output["mounts"]["fetcher"], "fixture")
        self.assertEqual(output["mounts"]["executor"], "sqlite")
        self.assertIn("sample_mode_notice", output)
        self.assertEqual(validate(envelope, load_schema("output")), [])
        self.assertTrue(output["claims"], "at least one accepted claim")
        for claim in output["claims"]:
            self.assertTrue(claim["evidence"], claim["id"])
            self.assertTrue(all(e["replayed"] for e in claim["evidence"] if e["kind"] == "url"), claim["id"])
        run_dir = self.out_dir / "ex01-seller-audit"
        self.assertTrue((run_dir / "output.json").is_file())
        self.assertTrue((run_dir / "report.md").is_file())
        lines = [json.loads(l) for l in (run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["type"], "guard")
        self.assertEqual(lines[0]["payload"]["network"], "disabled")
        self.assertEqual(lines[1]["type"], "run_start")
        self.assertEqual(lines[-1]["type"], "result")
        self.assertEqual([l["seq"] for l in lines], list(range(1, len(lines) + 1)))
        schema = load_schema("events")
        for line in lines:
            self.assertEqual(validate(line, schema), [], line)
        types = {l["type"] for l in lines}
        for required in ("plan", "handoff", "fetch", "sql", "claim", "conflict", "critique", "narrative_map", "memory", "recovery"):
            self.assertIn(required, types, required)
        self.assertNotIn("retry", types)
        self.assertEqual(output["report_path"], "logs/ex01-seller-audit/report.md")

    def test_all_examples_pick_their_playbook(self) -> None:
        for folder, (run_id, playbook) in EXAMPLES.items():
            envelope = run_example(folder, self.out_dir)
            self.assertIn(envelope["status"], ("ok", "partial"), (folder, envelope.get("error")))
            self.assertEqual(envelope["run_id"], run_id)
            self.assertEqual(envelope["output"]["plan"]["playbook"], playbook)
            self.assertEqual(validate(envelope, load_schema("output")), [])

    def test_example_02_is_ok_with_no_rejected_claim(self) -> None:
        envelope = run_example("02-catalog-concentration", self.out_dir)
        self.assertEqual(envelope["status"], "ok", envelope.get("error"))
        self.assertEqual(envelope["output"]["rejected_claims"], [])
        self.assertEqual(envelope["output"]["critique_rounds"], 1)
        self.assertTrue(envelope["output"]["claims"])

    def test_a_rejected_claim_makes_the_status_partial(self) -> None:
        """A metric that sums l30DaysUniquePayers is registered for this test only: the Critic's C4 rule rejects
        it (citing the pitfall memory that documents why), it lands in rejected_claims[] with its trail, and the
        status rule turns the run `partial`. Nothing like it exists in the shipped registry."""
        metric_id = "test_only_unique_payers_summed"
        spec = MetricSpec(id=metric_id, query_id="q08_catalog_size", reducer=f"reducers.{metric_id}", unit="count",
                          subject_template="catalog:cdp-bazaar", statement_template="Test-only sum of unique-payer counters: {value}.",
                          required_sources=("cdp_catalog_snapshot",), fields=("l30DaysUniquePayers",),
                          sources=("cdp_catalog_snapshot",), caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot",
                          period="l30d", sums=("l30DaysUniquePayers",), playbooks=("catalog_concentration",), sql_backed=False)
        reducers_mod.REDUCERS[spec.reducer] = lambda world, params: sum(r["payers_30d"] for r in reducers_mod.catalog_rows(world))
        METRICS[metric_id] = spec
        try:
            envelope = run_example("02-catalog-concentration", self.out_dir)
        finally:
            METRICS.pop(metric_id, None)
            reducers_mod.REDUCERS.pop(spec.reducer, None)
        self.assertEqual(envelope["status"], "partial", envelope.get("error"))
        rejected = {c["metric"]: c for c in envelope["output"]["rejected_claims"]}
        self.assertEqual(list(rejected), [metric_id])
        trail = rejected[metric_id]["critique"]
        self.assertTrue(any(k["rule"] == "C4" and k["verdict"] == "reject" and "mem-seed-001" in k["memory_ids"] for k in trail))
        self.assertNotIn(metric_id, {c["metric"] for c in envelope["output"]["claims"]})
        self.assertNotIn(f"[{rejected[metric_id]['id']}]", envelope["output"]["answer"])
        self.assertEqual(validate(envelope, load_schema("output")), [])

    def test_e_input_on_short_question(self) -> None:
        envelope = run_investigation({"input": {"question": "hi"}}, sample_config(self.out_dir), echo_stdout=False)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "E_INPUT")
        self.assertEqual(validate(envelope, load_schema("output")), [])

    def test_e_input_when_no_playbook_matches(self) -> None:
        payload = {"run_id": "no-playbook", "input": {"question": "Tell me something interesting about agents please"}}
        envelope = run_investigation(payload, sample_config(self.out_dir), echo_stdout=False)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "E_INPUT")
        self.assertEqual(envelope["error"]["stage"], "planner")
        self.assertIn("supported_playbooks", envelope["error"]["details"])
        lines = (self.out_dir / "no-playbook" / "run.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(lines[-1])["type"], "error")

    def test_cli_exit_code_and_quiet_output(self) -> None:
        proc = subprocess.run([sys.executable, "run.py", "--sample", "--input", "examples/ex1.json", "--quiet",
                               "--out", str(self.out_dir)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        envelope = json.loads(proc.stdout)
        self.assertIn(envelope["status"], ("ok", "partial"))

    def test_cli_error_exit_code(self) -> None:
        bad = self.out_dir / "bad.json"
        bad.write_text(json.dumps({"input": {"question": "hi"}}), encoding="utf-8", newline="\n")
        proc = subprocess.run([sys.executable, "run.py", "--sample", "--input", str(bad), "--quiet", "--out", str(self.out_dir)],
                              cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout)["error"]["code"], "E_INPUT")

    def test_partial_requires_an_accepted_claim(self) -> None:
        def no_claims(ctx, plan, observations, verifications, **kwargs):
            return AgentResult("analyst", "ok", {"claims": [], "conflicts": [], "unavailable": [
                {"metric": m, "reason": "simulated: source unavailable"} for m in plan.metrics]})
        with mock.patch.object(runner.analyst, "run_analyst", no_claims):
            envelope = run_example("01-seller-audit-textokit", self.out_dir)
        self.assertEqual(envelope["status"], "error")
        self.assertEqual(envelope["error"]["code"], "E_NO_SOURCES")
        self.assertEqual(validate(envelope, load_schema("output")), [])

    def test_error_codes(self) -> None:
        payload = example_payload("01-seller-audit-textokit")
        # E_TIMEOUT: the deadline is already gone when the collector would start
        with mock.patch.object(runner.RunContext, "time_left_ms", lambda self: -1):
            envelope = run_investigation(payload, sample_config(self.out_dir), echo_stdout=False)
        self.assertEqual(envelope["error"]["code"], "E_TIMEOUT")
        # E_NO_SOURCES: the collector reports every mandatory source down
        with mock.patch.object(runner.collector, "run_collector", lambda ctx, plan: AgentResult("collector", "failed", [], ["all down"])):
            envelope = run_investigation(payload, sample_config(self.out_dir), echo_stdout=False)
        self.assertEqual(envelope["error"]["code"], "E_NO_SOURCES")
        self.assertEqual(envelope["error"]["stage"], "collector")
        # E_INTERNAL: an uncaught exception inside an agent names the stage and the last log seq
        def boom(ctx):
            raise RuntimeError("planner exploded")
        with mock.patch.object(runner.planner, "run_planner", boom):
            envelope = run_investigation(payload, sample_config(self.out_dir), echo_stdout=False)
        self.assertEqual(envelope["error"]["code"], "E_INTERNAL")
        self.assertEqual(envelope["error"]["stage"], "planner")
        self.assertIn("last_log_seq", envelope["error"]["details"])
        self.assertIn("traceback_sha256", envelope["error"]["details"])
        traceback_text = (self.out_dir / envelope["run_id"] / "traceback.txt").read_text(encoding="utf-8")
        self.assertIn("planner exploded", traceback_text)
        for form in (str(REPO_ROOT.resolve()), REPO_ROOT.resolve().as_posix()):
            self.assertNotIn(form, traceback_text, "traceback.txt must not carry the machine path of the repo")
        # E_GENERATOR / E_ZETARIS_REQUIRED: live mode with --require-* and nothing configured fails at mount time
        envelope = run_investigation(payload, live_config_without_env(self.out_dir, require_generator=True), echo_stdout=False)
        self.assertEqual(envelope["error"]["code"], "E_GENERATOR")
        envelope = run_investigation(payload, live_config_without_env(self.out_dir, require_zetaris=True), echo_stdout=False)
        self.assertEqual(envelope["error"]["code"], "E_ZETARIS_REQUIRED")
        self.assertEqual(envelope["error"]["details"]["reason"], "zetaris_required")
        for env in (envelope,):
            self.assertEqual(validate(env, load_schema("output")), [])

    def test_sample_mode_honours_log_dir_and_reads_no_credential(self) -> None:
        environ = {"LOG_DIR": str(self.out_dir / "from-env"), "OPENAI_API_KEY": "not-a-real-key",
                   "ZETARIS_PASSWORD": "not-a-real-password"}
        no_out = type("Args", (), {"mode": "sample", "sample": True, "out": None})()
        config = build_config(no_out, environ=environ, load_dotenv=False)
        self.assertEqual(config.mode, "sample")
        self.assertEqual(config.out_dir, self.out_dir / "from-env")
        self.assertFalse(config.env.has_generator())
        self.assertFalse(config.env.has_zetaris())
        with_out = type("Args", (), {"mode": "sample", "sample": True, "out": str(self.out_dir / "cli")})()
        self.assertEqual(build_config(with_out, environ=environ, load_dotenv=False).out_dir, self.out_dir / "cli")
        self.assertEqual(build_config(no_out, environ={}, load_dotenv=False).out_dir, REPO_ROOT / "logs")


class HttpRunContract(unittest.TestCase):
    """run.py --serve: the statuses README.md documents for POST /run and GET /health."""

    @classmethod
    def setUpClass(cls) -> None:
        import threading
        sys.path.insert(0, str(REPO_ROOT))
        import run as run_module
        cls.tmp = tempfile.TemporaryDirectory()
        config = sample_config(Path(cls.tmp.name))
        config.quiet = True                     # keep the test output free of the event stream
        cls.httpd = run_module.build_server(0, config, host="127.0.0.1")
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def _call(self, method: str, path: str, body: bytes | None = None) -> tuple[int, dict]:
        import urllib.error
        import urllib.request
        from investigator import guard
        guard.uninstall_network_guard()         # a sample run served earlier in this process installed it
        request = urllib.request.Request(self.base + path, data=body, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def test_health(self) -> None:
        status, body = self._call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])

    def test_body_that_is_not_json_is_400(self) -> None:
        status, body = self._call("POST", "/run", b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(body["status"], "error")
        self.assertEqual(body["error"]["code"], "E_INPUT")

    def test_schema_invalid_body_is_422(self) -> None:
        status, body = self._call("POST", "/run", json.dumps({"input": {"question": "hi"}}).encode("utf-8"))
        self.assertEqual(status, 422)
        self.assertEqual(body["error"]["code"], "E_INPUT")

    def test_example_run_is_200(self) -> None:
        status, body = self._call("POST", "/run", json.dumps(example_payload("03-payer-dossier-onchain")).encode("utf-8"))
        self.assertEqual(status, 200)
        self.assertIn(body["status"], ("ok", "partial"))
        self.assertEqual(validate(body, load_schema("output")), [])

    def test_unknown_path_is_404(self) -> None:
        status, _ = self._call("POST", "/nope", b"{}")
        self.assertEqual(status, 404)


class NoHardcodedNumbers(unittest.TestCase):
    def test_pitch_numbers_are_not_in_code(self) -> None:
        offenders = []
        for path in (REPO_ROOT / "investigator").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for number in PITCH_NUMBERS:
                if re.search(rf"(?<![\w.]){re.escape(number)}(?![\w.])", text):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {number}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
