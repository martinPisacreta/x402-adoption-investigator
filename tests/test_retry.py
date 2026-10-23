"""The bounded retry policy shared by live and sample mode, and example 05 that replays it (SPEC.md 7.2 and 7.4,
amended 2026-10-23). Owner: B1.

investigator/sources/retry.call_with_retries is the one retry loop: LiveFetcher runs it over HTTP attempts (here a
scripted urllib opener, so no socket is opened) and FixtureFetcher over replayed attempts, where a fault script
(fixtures/transient/<example>/faults.json) makes listed attempts fail. These tests pin the policy (what is
retried, how often, the backoff, the budget cap), prove that both modes go through the same function and produce
the same retry record, and check the committed example 05: two retries, each followed by a successful attempt,
the normal pipeline afterwards and the numbers of example 03 unchanged.
"""
from __future__ import annotations

import io
import json
import socket
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from investigator.clock import SampleClock, parse_iso_ms  # noqa: E402
from investigator.config import build_config  # noqa: E402
from investigator.contracts import to_json  # noqa: E402
from investigator.runner import run as run_investigation  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402
from investigator.sources import retry as retry_mod  # noqa: E402
from investigator.sources.fetcher import FallbackFetcher, FetchError, FixtureFetcher, LiveFetcher, build_fetcher  # noqa: E402
from investigator.sources.manifest import load_manifest  # noqa: E402
from investigator.sources.retry import (AttemptFailed, GaveUp, RetryPolicy, call_with_retries,  # noqa: E402
                                        http_failure, is_transient_http)
from investigator.sources.transient import load_faults, parse_faults  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures" / "raw"
TRANSIENT = REPO_ROOT / "fixtures" / "transient"
MANIFEST = load_manifest(FIXTURES / "MANIFEST.json")
EXAMPLE_05 = "05-payer-dossier-retry"
FOLDER_05 = REPO_ROOT / "examples" / EXAMPLE_05
FOLDER_03 = REPO_ROOT / "examples" / "03-payer-dossier-onchain"
RECEIPTS_KEY = "GET https://tienda.textokit.store/receipts.json"
RECEIPTS_URL = "https://tienda.textokit.store/receipts.json"


def lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = io.BytesIO(body)
        self.status = status
        self.headers = {"Content-Type": "application/json"}

    def read(self, n: int = -1) -> bytes:
        return self._body.read(n)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid/x", code, "", {}, io.BytesIO(b""))


class ScriptedOpener:
    """The fake HTTP layer: each call pops the next outcome (a response to return or an exception to raise)."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls = 0

    def __call__(self, request: Any, timeout: float = 0.0) -> FakeResponse:
        self.calls += 1
        outcome = self.script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def scripted(outcomes: list[Any]):
    """attempt(n) for call_with_retries: raise the n-th outcome when it is an AttemptFailed, else return it."""
    def attempt(number: int) -> Any:
        outcome = outcomes[number - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    return attempt


def run_policy(outcomes: list[Any], policy: RetryPolicy = retry_mod.DEFAULT_POLICY, time_left=None):
    sleeps: list[float] = []
    retries: list[dict[str, Any]] = []
    try:
        result = call_with_retries(scripted(outcomes), policy=policy, target="GET https://example.test/a",
                                   url="https://example.test/a", method="GET", sleep=sleeps.append,
                                   on_retry=retries.append, time_left_ms=time_left)
    except GaveUp as gave:
        result = gave
    return result, sleeps, retries


def fault_script(faults: list[dict[str, Any]], simulated: bool = True):
    return parse_faults({"simulated": simulated, "faults": faults}, TRANSIENT / "test" / "faults.json",
                        "fixtures/transient/test/faults.json")


# ----------------------------------------------------------------------------------------------------------

class Policy(unittest.TestCase):
    def test_only_429_and_5xx_are_transient_statuses(self) -> None:
        for code in (429, 500, 502, 503, 504, 599):
            self.assertTrue(is_transient_http(code), code)
            self.assertTrue(http_failure(code).transient, code)
        for code in (400, 401, 403, 404, 405, 408, 409, 410, 422, 425, 451, 499):
            self.assertFalse(is_transient_http(code), code)
            self.assertFalse(http_failure(code).transient, code)
        self.assertEqual(http_failure(503).reason, "http 503 Service Unavailable")
        self.assertEqual(http_failure(429).reason, "http 429 Too Many Requests")

    def test_retries_come_from_config_and_are_clamped(self) -> None:
        self.assertEqual(RetryPolicy.from_retries(None), retry_mod.DEFAULT_POLICY)
        self.assertEqual(retry_mod.DEFAULT_POLICY.max_retries, 2)
        self.assertEqual(retry_mod.DEFAULT_POLICY.max_attempts, 3)
        self.assertEqual(RetryPolicy.from_retries(-3).max_attempts, 1)
        self.assertEqual(RetryPolicy.from_retries(99).max_retries, retry_mod.MAX_RETRIES_CAP)
        args = type("Args", (), {"mode": "sample", "sample": True, "out": None, "fetch_retries": 1})()
        self.assertEqual(build_config(args, load_dotenv=False).fetch_retries, 1)
        default = type("Args", (), {"mode": "sample", "sample": True, "out": None})()
        self.assertEqual(build_config(default, load_dotenv=False).fetch_retries, 2)
        import run as run_module
        cli = run_module.build_parser().parse_args(["--sample", "--input", "examples/ex5.json", "--fetch-retries", "1"])
        self.assertEqual(build_config(cli, load_dotenv=False).fetch_retries, 1)

    def test_backoff_schedule_is_fixed_and_the_last_value_repeats(self) -> None:
        policy = RetryPolicy(max_retries=4)
        self.assertEqual([policy.backoff_for(n) for n in (1, 2, 3, 4)], [500, 1500, 1500, 1500])
        self.assertEqual(RetryPolicy(max_retries=2, backoff_ms=()).backoff_for(1), 0)


class CallWithRetries(unittest.TestCase):
    def test_a_transient_failure_is_retried_then_succeeds(self) -> None:
        (result, attempts), sleeps, retries = run_policy([http_failure(503), "body"])
        self.assertEqual((result, attempts), ("body", 2))
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(len(retries), 1)
        info = retries[0]
        self.assertEqual((info["attempt"], info["next_attempt"], info["max_attempts"]), (1, 2, 3))
        self.assertEqual((info["reason"], info["kind"], info["http"], info["backoff_ms"]),
                         ("http 503 Service Unavailable", "http", 503, 500))
        self.assertEqual((info["target"], info["url"], info["method"], info["replayed"]),
                         ("GET https://example.test/a", "https://example.test/a", "GET", False))

    def test_retries_stop_at_the_maximum(self) -> None:
        gave, sleeps, retries = run_policy([http_failure(503)] * 3)
        self.assertIsInstance(gave, GaveUp)
        self.assertEqual((gave.why, gave.attempts, gave.http), ("max_attempts", 3, 503))
        self.assertEqual(sleeps, [0.5, 1.5])
        self.assertEqual([r["attempt"] for r in retries], [1, 2])
        self.assertEqual([r["backoff_ms"] for r in retries], [500, 1500])
        self.assertIn("after 3 attempts", gave.message)
        gave, sleeps, retries = run_policy([http_failure(502)] * 2, RetryPolicy(max_retries=1))
        self.assertEqual((gave.why, gave.attempts, len(retries)), ("max_attempts", 2, 1))
        gave, sleeps, retries = run_policy([http_failure(502)], RetryPolicy(max_retries=0))
        self.assertEqual((gave.why, gave.attempts, sleeps, retries), ("max_attempts", 1, [], []))

    def test_a_permanent_4xx_is_never_retried(self) -> None:
        for code in (400, 401, 403, 404, 408, 425):
            gave, sleeps, retries = run_policy([http_failure(code), "never reached"])
            self.assertIsInstance(gave, GaveUp, code)
            self.assertEqual((gave.why, gave.attempts, gave.http), ("permanent", 1, code))
            self.assertEqual((sleeps, retries), ([], []), code)
        gave, _, retries = run_policy([AttemptFailed("body too large", kind="other", transient=False)])
        self.assertEqual((gave.why, retries), ("permanent", []))

    def test_a_backoff_that_does_not_fit_in_the_budget_is_not_taken(self) -> None:
        """budget.max_seconds caps the backoff: with 1 s left the 500 ms backoff is taken, the 1.5 s one is not."""
        gave, sleeps, retries = run_policy([http_failure(503)] * 3, time_left=lambda: 1000)
        self.assertEqual((gave.why, gave.attempts, gave.backoff_ms), ("deadline", 2, 1500))
        self.assertEqual(sleeps, [0.5])
        self.assertEqual([r["backoff_ms"] for r in retries], [500])
        self.assertIn("budget.max_seconds", gave.message)
        gave, sleeps, retries = run_policy([http_failure(503), "body"], time_left=lambda: 0)
        self.assertEqual((gave.why, gave.attempts, sleeps, retries), ("deadline", 1, [], []))

    def test_other_exceptions_propagate_untouched(self) -> None:
        class Boom(Exception):
            pass
        with self.assertRaises(Boom):
            run_policy([Boom("guard"), "body"])


class LiveUsesTheSamePolicy(unittest.TestCase):
    def live(self, script: list[Any], **kwargs: Any):
        opener = ScriptedOpener(script)
        sleeps: list[float] = []
        retries: list[dict[str, Any]] = []
        fetcher = LiveFetcher(SampleClock(MANIFEST.fetched_at), opener=opener, sleep=sleeps.append,
                              on_retry=retries.append, **kwargs)
        return fetcher, opener, sleeps, retries

    def test_live_and_sample_fetchers_call_the_same_function_and_log_the_same_record(self) -> None:
        body = (FIXTURES / "store-receipts.json").read_bytes()
        with mock.patch.object(retry_mod, "call_with_retries", wraps=retry_mod.call_with_retries) as spy:
            fetcher, opener, sleeps, live_retries = self.live([http_error(503), FakeResponse(body)])
            live_obs = fetcher.fetch(RECEIPTS_KEY, RECEIPTS_URL)
            self.assertEqual(spy.call_count, 1)
            sample_retries: list[dict[str, Any]] = []
            fixture = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at), on_retry=sample_retries.append,
                                     faults=fault_script([{"request_key": RECEIPTS_KEY, "attempt": 1, "kind": "http", "http": 503}]))
            sample_obs = fixture.fetch(RECEIPTS_KEY, RECEIPTS_URL)
            self.assertEqual(spy.call_count, 2)
        self.assertEqual((live_obs.status, sample_obs.status), ("ok", "ok"))
        self.assertEqual(live_obs.bytes_sha256, sample_obs.bytes_sha256, "same bytes, live or replayed")
        self.assertEqual((opener.calls, sleeps), (2, [0.5]))
        self.assertEqual((fetcher.attempts_used(RECEIPTS_KEY), fixture.attempts_used(RECEIPTS_KEY)), (2, 2))
        self.assertEqual(fetcher.attempts_used(RECEIPTS_KEY), 1, "read once")
        (live,), (sample,) = live_retries, sample_retries
        self.assertEqual(set(sample) - set(live), {"fault_file"})
        self.assertEqual({k: v for k, v in live.items() if k != "replayed"},
                         {k: v for k, v in sample.items() if k not in ("replayed", "fault_file")})
        self.assertEqual((live["replayed"], sample["replayed"]), (False, True))

    def test_live_429_is_retried_with_a_real_sleep_of_the_policy_backoff(self) -> None:
        fetcher, opener, sleeps, retries = self.live([http_error(429), FakeResponse(b'{"ok": 1}')])
        self.assertIs(LiveFetcher(SampleClock(MANIFEST.fetched_at))._sleep, time.sleep,
                      "without an injected sleep, live mode really waits")
        obs = fetcher.fetch("POST https://mainnet.base.org", "https://mainnet.base.org", "POST", {"id": 1})
        self.assertEqual((obs.status, obs.payload), ("ok", {"ok": 1}))
        self.assertEqual((sleeps, opener.calls), ([0.5], 2))
        self.assertEqual(retries[0]["reason"], "http 429 Too Many Requests")

    def test_connection_reset_and_timeout_are_retried(self) -> None:
        fetcher, opener, sleeps, retries = self.live([ConnectionResetError("reset by peer"), socket.timeout("t"),
                                                       FakeResponse(b"{}")])
        self.assertEqual(fetcher.fetch("GET https://example.test/r", "https://example.test/r").status, "ok")
        self.assertEqual([r["kind"] for r in retries], ["connection", "timeout"])
        self.assertEqual(sleeps, [0.5, 1.5])

    def test_408_and_425_fail_at_once_in_live_mode(self) -> None:
        for code in (408, 425):
            fetcher, opener, sleeps, retries = self.live([http_error(code), FakeResponse(b"{}")])
            with self.assertRaises(FetchError) as ctx:
                fetcher.fetch("GET https://example.test/s", "https://example.test/s")
            self.assertEqual((ctx.exception.http, ctx.exception.attempts, opener.calls, sleeps, retries),
                             (code, 1, 1, [], []))

    def test_live_backoff_respects_the_budget(self) -> None:
        fetcher, opener, sleeps, retries = self.live([http_error(503), FakeResponse(b"{}")], time_left_ms=lambda: 200)
        with self.assertRaises(FetchError) as ctx:
            fetcher.fetch("GET https://example.test/t", "https://example.test/t")
        self.assertEqual((opener.calls, sleeps, retries, ctx.exception.attempts), (1, [], [], 1))
        self.assertIn("budget.max_seconds", str(ctx.exception))

    def test_fallback_reports_the_live_attempts_behind_a_stale_fixture(self) -> None:
        fetcher, _, _, _ = self.live([http_error(503)] * 3)
        fallback = FallbackFetcher(fetcher, FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at)))
        obs = fallback.fetch(RECEIPTS_KEY, RECEIPTS_URL)
        self.assertEqual((obs.status, obs.stale), ("ok", True))
        self.assertEqual(fallback.attempts_used(RECEIPTS_KEY), 3)
        fallback.time_left_ms = lambda: 5
        self.assertIsNotNone(fetcher.time_left_ms)


class SampleReplay(unittest.TestCase):
    def test_a_replayed_fault_is_retried_and_the_fixture_is_served_unchanged(self) -> None:
        clean_clock, fault_clock = SampleClock(MANIFEST.fetched_at), SampleClock(MANIFEST.fetched_at)
        clean = FixtureFetcher(MANIFEST, FIXTURES, clean_clock).fetch(RECEIPTS_KEY, RECEIPTS_URL)
        retries: list[dict[str, Any]] = []
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, fault_clock, on_retry=retries.append,
                                 faults=fault_script([{"request_key": RECEIPTS_KEY, "attempt": 1, "kind": "http", "http": 503}]))
        replayed = fetcher.fetch(RECEIPTS_KEY, RECEIPTS_URL)
        self.assertEqual(to_json(replayed), to_json(clean), "a retry never changes what is served")
        self.assertEqual(len(retries), 1)
        self.assertEqual((retries[0]["replayed"], retries[0]["fault_file"]), (True, "fixtures/transient/test/faults.json"))
        self.assertEqual(fault_clock.now_ms() - clean_clock.now_ms(), 500,
                         "the backoff advanced the deterministic clock by exactly 500 ms")

    def test_a_fault_on_every_attempt_ends_unavailable_after_the_maximum(self) -> None:
        faults = [{"request_key": RECEIPTS_KEY, "attempt": n, "kind": "http", "http": 503} for n in (1, 2, 3)]
        retries: list[dict[str, Any]] = []
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at), faults=fault_script(faults),
                                 on_retry=retries.append)
        obs = fetcher.fetch(RECEIPTS_KEY, RECEIPTS_URL)
        self.assertEqual((obs.status, obs.http, obs.replayed, obs.payload), ("unavailable", 503, True, None))
        self.assertIn("after 3 attempts", obs.error or "")
        self.assertIn("fixtures/transient/test/faults.json", obs.error or "")
        self.assertEqual((len(retries), fetcher.attempts_used(RECEIPTS_KEY)), (2, 3))

    def test_a_replayed_permanent_4xx_is_not_retried(self) -> None:
        retries: list[dict[str, Any]] = []
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at), on_retry=retries.append,
                                 faults=fault_script([{"request_key": RECEIPTS_KEY, "attempt": 1, "kind": "http", "http": 404}]))
        obs = fetcher.fetch(RECEIPTS_KEY, RECEIPTS_URL)
        self.assertEqual((obs.status, obs.http, retries), ("unavailable", 404, []))
        self.assertIn("not retried", obs.error or "")
        self.assertEqual(fetcher.attempts_used(RECEIPTS_KEY), 1)

    def test_timeout_and_reset_faults_and_the_retry_limit_from_config(self) -> None:
        faults = [{"request_key": RECEIPTS_KEY, "attempt": 1, "kind": "timeout"},
                  {"request_key": RECEIPTS_KEY, "attempt": 2, "kind": "connection_reset"}]
        retries: list[dict[str, Any]] = []
        fetcher = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at), faults=fault_script(faults),
                                 on_retry=retries.append)
        self.assertEqual(fetcher.fetch(RECEIPTS_KEY, RECEIPTS_URL).status, "ok")
        self.assertEqual([r["kind"] for r in retries], ["timeout", "connection"])
        one_retry = FixtureFetcher(MANIFEST, FIXTURES, SampleClock(MANIFEST.fetched_at), faults=fault_script(faults),
                                   policy=RetryPolicy.from_retries(1))
        obs = one_retry.fetch(RECEIPTS_KEY, RECEIPTS_URL)
        self.assertEqual(obs.status, "unavailable", "--fetch-retries 1: the second failure is the last attempt")

    def test_malformed_fault_files_are_refused(self) -> None:
        bad = [
            {"faults": []},                                                                   # no `simulated`
            {"simulated": True, "faults": [{"attempt": 1, "kind": "http", "http": 503}]},       # no request_key
            {"simulated": True, "faults": [{"request_key": "k", "attempt": 0, "kind": "timeout"}]},
            {"simulated": True, "faults": [{"request_key": "k", "attempt": 1, "kind": "boom"}]},
            {"simulated": True, "faults": [{"request_key": "k", "attempt": 1, "kind": "http", "http": 200}]},
            {"simulated": True, "faults": [{"request_key": "k", "attempt": 1, "kind": "timeout"},
                                            {"request_key": "k", "attempt": 1, "kind": "timeout"}]},
        ]
        for data in bad:
            with self.assertRaises(ValueError, msg=json.dumps(data)):
                parse_faults(data, TRANSIENT / "x" / "faults.json", "fixtures/transient/x/faults.json")

    def test_only_example_05_has_a_fault_script_and_live_mode_never_loads_one(self) -> None:
        folders = sorted(p.name for p in (REPO_ROOT / "examples").iterdir() if p.is_dir() and (p / "input.json").is_file())
        for name in folders:
            script = load_faults(TRANSIENT, name, REPO_ROOT)
            self.assertEqual(script is not None, name == EXAMPLE_05, name)
        self.assertEqual(sorted(p.name for p in TRANSIENT.iterdir() if p.is_dir()), [EXAMPLE_05])
        script = load_faults(TRANSIENT, EXAMPLE_05, REPO_ROOT)
        self.assertTrue(script.simulated, "the committed faults were written by hand and must say so")
        self.assertEqual(script.rel_path, f"fixtures/transient/{EXAMPLE_05}/faults.json")
        for key in script.faults:
            self.assertIn(key, MANIFEST.requests, "a fault names a request key that has a committed fixture")
        with tempfile.TemporaryDirectory() as tmp:
            live = build_config(type("Args", (), {"mode": "live", "sample": False, "out": tmp})(), environ={}, load_dotenv=False)
            fetcher = build_fetcher(live, MANIFEST, SampleClock(MANIFEST.fetched_at), Path(tmp), EXAMPLE_05)
            self.assertIsInstance(fetcher, FallbackFetcher)
            self.assertIsNone(fetcher.fixture.faults)
            sample = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            self.assertIsNotNone(build_fetcher(sample, MANIFEST, SampleClock(MANIFEST.fetched_at), Path(tmp), EXAMPLE_05).faults)


class Example05(unittest.TestCase):
    """What a judge opens: examples/05-payer-dossier-retry (example 03's question with two replayed failures)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.envelope = json.loads((FOLDER_05 / "output.json").read_text(encoding="utf-8"))
        cls.output = cls.envelope["output"]
        cls.events = lines(FOLDER_05 / "run.jsonl")
        cls.ex03 = json.loads((FOLDER_03 / "output.json").read_text(encoding="utf-8"))
        cls.schema = load_schema("events")

    def test_input_is_example_03s_question(self) -> None:
        mine = json.loads((FOLDER_05 / "input.json").read_text(encoding="utf-8"))
        theirs = json.loads((FOLDER_03 / "input.json").read_text(encoding="utf-8"))
        self.assertEqual(mine["input"], theirs["input"])
        self.assertNotEqual(mine["run_id"], theirs["run_id"])
        self.assertEqual((REPO_ROOT / "examples" / "ex5.json").read_bytes(), (FOLDER_05 / "input.json").read_bytes())
        self.assertEqual((REPO_ROOT / "examples" / "outputs" / "ex5.output.json").read_bytes(),
                         (FOLDER_05 / "output.json").read_bytes())

    def test_each_retry_is_followed_by_a_successful_attempt(self) -> None:
        retries = [e for e in self.events if e["type"] == "retry"]
        self.assertGreaterEqual(len(retries), 1)
        self.assertEqual({r["payload"]["stage"] for r in retries}, {"collector", "verifier"})
        script = load_faults(TRANSIENT, EXAMPLE_05, REPO_ROOT)
        for retry in retries:
            payload = retry["payload"]
            self.assertEqual(validate(payload, self.schema["$defs"]["payloads"]["retry"], "$.payload", self.schema), [])
            self.assertEqual(retry["agent"], payload["stage"])
            self.assertEqual((payload["replayed"], payload["fault_file"]), (True, script.rel_path))
            self.assertIn(payload["attempt"], script.for_key(payload["target"]), "the failure is the scripted one")
            self.assertEqual(payload["backoff_ms"], retry_mod.DEFAULT_POLICY.backoff_for(payload["attempt"]))
            outcome = next(e for e in self.events if e["seq"] > retry["seq"] and e["type"] == "fetch"
                           and e["payload"]["request_key"] == payload["target"])
            self.assertEqual(outcome["payload"]["status"], "ok")
            self.assertEqual(outcome["payload"]["attempts"], payload["next_attempt"])
            self.assertEqual(outcome["payload"]["sha256"], MANIFEST.requests[payload["target"]].sha256)
            gap = parse_iso_ms(outcome["at"]) - parse_iso_ms(retry["at"])
            self.assertGreaterEqual(gap, payload["backoff_ms"], "the backoff shows on the deterministic clock")
        retried_fetches = [e for e in self.events if e["type"] == "fetch" and "attempts" in e["payload"]]
        self.assertEqual(len(retried_fetches), len(retries), "only the retried requests carry `attempts`")

    def test_the_normal_pipeline_runs_after_the_retries(self) -> None:
        last_retry = max(e["seq"] for e in self.events if e["type"] == "retry")
        after = [e for e in self.events if e["seq"] > last_retry]
        handoffs = [(e["payload"]["from"], e["payload"]["to"]) for e in after if e["type"] == "handoff"]
        for pair in (("verifier", "keeper"), ("keeper", "analyst"), ("analyst", "writer"), ("writer", "critic"),
                     ("critic", "keeper")):
            self.assertIn(pair, handoffs)
        rules = {e["payload"]["rule"] for e in after if e["type"] == "critique" and e["payload"]["verdict"] == "accept"}
        self.assertTrue({"C1", "C2", "C3", "C4", "C5", "C6", "C7"} <= rules)
        types = [e["type"] for e in after]
        for required in ("verify", "sql", "claim", "narrative_map", "memory", "ingest"):
            self.assertIn(required, types, required)
        self.assertEqual(self.events[-1]["type"], "result")
        self.assertEqual(self.events[-1]["payload"]["status"], "ok")

    def test_the_numbers_are_those_of_example_03(self) -> None:
        def numbers(output: dict[str, Any]) -> list[tuple[Any, ...]]:
            return [(c["id"], c["metric"], c["subject"], json.dumps(c["value"]), c["unit"], c["statement"],
                     tuple(c["caveats"]), c["cross_checked"], c["status"]) for c in output["claims"]]
        self.assertEqual(self.envelope["status"], self.ex03["status"])
        self.assertEqual(numbers(self.output), numbers(self.ex03["output"]))
        self.assertEqual(self.output["verdict"], self.ex03["output"]["verdict"])
        self.assertEqual(self.output["answer"], self.ex03["output"]["answer"])
        self.assertEqual(self.output["rejected_claims"], self.ex03["output"]["rejected_claims"])
        self.assertEqual([(s["name"], s["sha256"], s["status"]) for s in self.output["sources"]],
                         [(s["name"], s["sha256"], s["status"]) for s in self.ex03["output"]["sources"]])
        self.assertEqual(self.output["verifications"], self.ex03["output"]["verifications"])

    def test_the_output_says_the_failures_were_replayed(self) -> None:
        notice = self.output["sample_mode_notice"]
        self.assertIn(f"fixtures/transient/{EXAMPLE_05}/faults.json", notice)
        self.assertIn("simulated", notice)
        notes = {a["role"]: " ".join(a["notes"]) for a in self.output["agents"]}
        self.assertIn("store_receipts ok on attempt 2 of 3", notes["collector"])
        self.assertIn("ok on attempt 2 of 3", notes["verifier"])
        self.assertNotIn("retry", " ".join(" ".join(a["notes"]) for a in self.ex03["output"]["agents"]))

    def test_a_fresh_run_reproduces_example_05_byte_for_byte(self) -> None:
        payload = json.loads((FOLDER_05 / "input.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            envelope = run_investigation(payload, config, echo_stdout=False)
            self.assertEqual(envelope["status"], "ok", envelope.get("error"))
            run_dir = Path(tmp) / envelope["run_id"]
            for name in ("output.json", "run.jsonl", "sql.jsonl", "hmem.trace.jsonl", "report.md"):
                self.assertEqual((run_dir / name).read_bytes(), (FOLDER_05 / name).read_bytes(), name)
            self.assertIn('"type":"retry"', (run_dir / "run.jsonl").read_text(encoding="utf-8"))

    def test_without_its_fault_script_the_same_input_has_no_retry(self) -> None:
        payload = json.loads((FOLDER_05 / "input.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp,
                                                    "example": "03-payer-dossier-onchain"})(), load_dotenv=False)
            envelope = run_investigation(payload, config, echo_stdout=False)
            events = lines(Path(tmp) / envelope["run_id"] / "run.jsonl")
            self.assertNotIn("retry", [e["type"] for e in events])
            self.assertEqual(json.loads((Path(tmp) / envelope["run_id"] / "output.json").read_text(encoding="utf-8"))
                             ["output"]["sample_mode_notice"], self.ex03["output"]["sample_mode_notice"])


if __name__ == "__main__":
    unittest.main()
