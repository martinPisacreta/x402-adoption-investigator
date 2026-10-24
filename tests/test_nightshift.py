"""Meterless Nightshift as the primary generator (investigator/generator.py; hackathon rule: Nightshift is the
primary AI provider).

Nightshift is not needed: every test talks to an in-process fake of the Meterless Runtime 0.90.0 contract
(FakeRuntime below), passed as the urlopen-compatible transport, so no socket is opened and no real token exists.
The tests pin the contract the adapter implements:
- a pasted URL (with or without scheme, /v1, /v1/chat/completions, /v1/models, /healthz) normalises to <host>/v1,
  never /v1/v1;
- GET /v1/models decides the status: 401/403 unauthorized, 200 with meterless/* ids online (unknown meterless ids
  kept), anything else offline; Auto Detect probes ports 8088, 8090, 8787, 11434 on 127.0.0.1, localhost and [::1]
  and keeps the first online or unauthorized hit in that order;
- chat requests carry the Bearer token, `meterless-app: x402-adoption-investigator` and `meterless-scope: user`,
  ask for the routing mode meterless/auto and use a 180 s timeout;
- provider precedence in live mode: Nightshift > OpenAI-compatible env > rules/templates, with
  recovery{nightshift_unavailable} naming the reason; sample mode never runs detection;
- every answered call is a `generate` event in run.jsonl (provider, requested routing mode, model that answered)
  whose seq generator.jsonl repeats, and the token appears in no log, transcript or output.
"""
from __future__ import annotations

import functools
import io
import json
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from typing import Any, Optional
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from investigator import generator as gen  # noqa: E402
from investigator import runner  # noqa: E402
from investigator.config import Env, RunConfig, build_config, parse_request  # noqa: E402
from investigator.errors import InvestigatorError  # noqa: E402
from investigator.schemas import load_schema, validate  # noqa: E402
from test_generator_hmem import FakeChatCompletions  # noqa: E402

TOKEN = "nightshift-test-token-not-a-secret"
OPENAI_KEY = "openai-test-key-not-a-secret"
BACKEND_MODEL = "fake-local-backend-q4"          # what the fake runtime says answered behind meterless/auto
MODELS = ["meterless/auto", "meterless/fast", "meterless/deep", "meterless/cheap", "meterless/local",
          "meterless/fusion", "meterless/vision.deep", "meterless/some-future-class"]


def http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, f"HTTP {code}", {}, io.BytesIO(b'{"error":"nope"}'))


class FakeRuntime(FakeChatCompletions):
    """In-process Meterless Runtime: `routes` maps a base URL (<host>/v1) to what GET /models does there
    ("online" -> 200 with MODELS, an int -> that HTTP status, "not_meterless" -> 200 with non-meterless ids,
    "garbage" -> 200 that is not JSON); any other base is a refused connection. POST /chat/completions on an
    online base answers like FakeChatCompletions (Planner, Writer and Critic replies) with model BACKEND_MODEL.
    Every request is recorded with its headers (lowercased names) and timeout."""

    def __init__(self, routes: Optional[dict[str, Any]] = None, token: Optional[str] = TOKEN) -> None:
        super().__init__()
        self.routes = dict(routes or {})
        self.token = token
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: Optional[float] = None) -> io.BytesIO:
        url = request.full_url
        headers = {k.lower(): v for k, v in request.header_items()}
        self.calls.append({"url": url, "method": request.get_method(), "headers": headers, "timeout": timeout})
        base = url.rsplit("/models", 1)[0] if url.endswith("/models") else url.rsplit("/chat/completions", 1)[0]
        route = self.routes.get(base)
        if route is None:
            raise urllib.error.URLError(ConnectionRefusedError(10061, "connection refused"))
        if isinstance(route, int):
            raise http_error(url, route)
        if self.token and headers.get("authorization") != f"Bearer {self.token}":
            raise http_error(url, 401)
        if url.endswith("/models"):
            if route == "garbage":
                return io.BytesIO(b"<html>not json</html>")
            ids = ["llama3:8b", "qwen2.5"] if route == "not_meterless" else MODELS
            return io.BytesIO(json.dumps({"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}).encode())
        payload = json.loads(super().__call__(request, timeout).read().decode("utf-8"))
        payload["model"] = BACKEND_MODEL
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def model_probes(self) -> list[str]:
        return [c["url"] for c in self.calls if c["url"].endswith("/models")]

    def chats(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["url"].endswith("/chat/completions")]


def live_config(environ: dict[str, str], tmp: Path, **flags: Any) -> RunConfig:
    args = type("Args", (), {"mode": "live", "sample": False, "out": str(tmp), **flags})()
    return build_config(args, environ={"HMEM_DIR": str(tmp / "hmem"), **environ}, load_dotenv=False)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_no_secret(case: unittest.TestCase, folder: Path, *secrets: str) -> None:
    files = [p for p in folder.rglob("*") if p.is_file()]
    case.assertTrue(files)
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for secret in secrets:
            case.assertNotIn(secret, text, path.name)


class UrlNormalisation(unittest.TestCase):
    def test_pasted_forms_normalise_to_host_v1(self) -> None:
        cases = {
            None: "http://127.0.0.1:8088/v1",
            "": "http://127.0.0.1:8088/v1",
            "127.0.0.1:8088": "http://127.0.0.1:8088/v1",
            "http://127.0.0.1:8088": "http://127.0.0.1:8088/v1",
            "http://127.0.0.1:8088/": "http://127.0.0.1:8088/v1",
            "http://127.0.0.1:8088/v1": "http://127.0.0.1:8088/v1",
            "http://127.0.0.1:8088/v1/": "http://127.0.0.1:8088/v1",
            "127.0.0.1:8088/v1/chat/completions": "http://127.0.0.1:8088/v1",
            "http://localhost:8090/v1/models": "http://localhost:8090/v1",
            "http://localhost:8090/v1/models/": "http://localhost:8090/v1",
            "localhost:8787/healthz": "http://localhost:8787/v1",
            "http://127.0.0.1:8088/v1/v1": "http://127.0.0.1:8088/v1",
            "  http://127.0.0.1:8088/v1/v1/chat/completions  ": "http://127.0.0.1:8088/v1",
            "[::1]:11434/v1": "http://[::1]:11434/v1",
            "http://[::1]:8088/healthz": "http://[::1]:8088/v1",
            "HTTPS://Hotspot.Example:9000/V1/Models?x=1#frag": "https://hotspot.example:9000/v1",
            "https://box.example/nightshift/v1/chat/completions": "https://box.example/nightshift/v1",
            "http://user:pass@127.0.0.1:8088/v1": "http://127.0.0.1:8088/v1",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                normalised = gen.normalize_nightshift_url(raw)
                self.assertEqual(normalised, expected)
                self.assertNotIn("/v1/v1", normalised)
                self.assertEqual(gen.normalize_nightshift_url(normalised), normalised, "idempotent")


class StatusAndAutoDetect(unittest.TestCase):
    BASE = "http://127.0.0.1:8088/v1"

    def test_models_endpoint_decides_the_status(self) -> None:
        cases = [("online", "online"), (401, "unauthorized"), (403, "unauthorized"), (404, "offline"),
                 (500, "offline"), ("not_meterless", "offline"), ("garbage", "offline")]
        for route, expected in cases:
            with self.subTest(route=route):
                probe = gen.probe_nightshift(self.BASE, TOKEN, opener=FakeRuntime({self.BASE: route}))
                self.assertEqual(probe.status, expected)
                self.assertEqual(probe.base_url, self.BASE)
        refused = gen.probe_nightshift(self.BASE, TOKEN, opener=FakeRuntime({}))
        self.assertEqual(refused.status, "offline")
        self.assertIn("connection", refused.detail)

    def test_online_keeps_every_meterless_id_including_unknown_ones(self) -> None:
        probe = gen.probe_nightshift(self.BASE, TOKEN, opener=FakeRuntime({self.BASE: "online"}))
        self.assertEqual(probe.models, MODELS)
        self.assertIn("meterless/some-future-class", probe.models)

    def test_a_wrong_or_missing_token_is_unauthorized_not_offline(self) -> None:
        fake = FakeRuntime({self.BASE: "online"})
        self.assertEqual(gen.probe_nightshift(self.BASE, "wrong-token", opener=fake).status, "unauthorized")
        self.assertEqual(gen.probe_nightshift(self.BASE, None, opener=fake).status, "unauthorized")
        self.assertNotIn("authorization", fake.calls[-1]["headers"], "no token: no Authorization header at all")

    def test_probe_sends_the_app_header_and_the_token(self) -> None:
        fake = FakeRuntime({self.BASE: "online"})
        gen.probe_nightshift(self.BASE, TOKEN, opener=fake)
        call = fake.calls[0]
        self.assertEqual((call["method"], call["url"]), ("GET", f"{self.BASE}/models"))
        self.assertEqual(call["headers"]["meterless-app"], "x402-adoption-investigator")
        self.assertEqual(call["headers"]["authorization"], f"Bearer {TOKEN}")
        self.assertNotIn("meterless-scope", call["headers"], "meterless-scope is for chat requests")

    def test_candidate_order_is_port_then_host(self) -> None:
        urls = gen.nightshift_candidates()
        self.assertEqual(len(urls), 12)
        self.assertEqual(urls[:3], ["http://127.0.0.1:8088/v1", "http://localhost:8088/v1", "http://[::1]:8088/v1"])
        ports = [int(u.rsplit(":", 1)[1].split("/")[0]) for u in urls]
        self.assertEqual(sorted(set(ports), key=ports.index), [8088, 8090, 8787, 11434])

    def test_auto_detect_takes_the_first_online_or_unauthorized_hit_in_order(self) -> None:
        fake = FakeRuntime({"http://localhost:8787/v1": "online", "http://[::1]:11434/v1": "online",
                            "http://127.0.0.1:8090/v1": "not_meterless"})
        probe = gen.detect_nightshift(TOKEN, opener=fake)
        self.assertEqual((probe.status, probe.base_url), ("online", "http://localhost:8787/v1"))
        self.assertEqual(sorted(fake.model_probes()), sorted(f"{u}/models" for u in gen.nightshift_candidates()))
        fake = FakeRuntime({"http://[::1]:8090/v1": 401, "http://127.0.0.1:8787/v1": "online"})
        probe = gen.detect_nightshift(TOKEN, opener=fake)
        self.assertEqual((probe.status, probe.base_url), ("unauthorized", "http://[::1]:8090/v1"))

    def test_auto_detect_with_nothing_listening_is_offline(self) -> None:
        probe = gen.detect_nightshift(TOKEN, opener=FakeRuntime({}))
        self.assertEqual(probe.status, "offline")
        self.assertEqual(probe.base_url, gen.NIGHTSHIFT_DEFAULT_BASE_URL)
        self.assertIn("8088, 8090, 8787, 11434", probe.detail)

    def test_an_explicit_base_url_probes_that_runtime_only(self) -> None:
        fake = FakeRuntime({"http://127.0.0.1:8088/v1": "online", "http://127.0.0.1:9999/v1": "online"})
        probe = gen.detect_nightshift(TOKEN, base_url="127.0.0.1:9999/v1/chat/completions", opener=fake)
        self.assertEqual((probe.status, probe.base_url), ("online", "http://127.0.0.1:9999/v1"))
        self.assertEqual(fake.model_probes(), ["http://127.0.0.1:9999/v1/models"])


class TokenResolution(unittest.TestCase):
    def test_env_token_then_token_file_then_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "proxy-token"
            token_file.write_text(f"  {TOKEN}\n", encoding="utf-8")
            self.assertEqual(gen.resolve_nightshift_token(Env(nightshift_token="from-env", nightshift_token_file=str(token_file))),
                             ("from-env", "NIGHTSHIFT_TOKEN"))
            self.assertEqual(gen.resolve_nightshift_token(Env(nightshift_token_file=str(token_file))),
                             (TOKEN, "NIGHTSHIFT_TOKEN_FILE"))
            missing, why = gen.resolve_nightshift_token(Env(nightshift_token_file=str(Path(tmp) / "nope")))
            self.assertIsNone(missing)
            self.assertIn("NIGHTSHIFT_TOKEN_FILE could not be read", why)
            self.assertNotIn(tmp, why, "the reason names variables, never paths")
        none, why = gen.resolve_nightshift_token(Env())
        self.assertIsNone(none)
        self.assertIn("no token configured", why)

    def test_the_rt_local_placeholder_is_never_sent(self) -> None:
        token, why = gen.resolve_nightshift_token(Env(nightshift_token="rt-local"))
        self.assertIsNone(token)
        self.assertIn("placeholder rt-local", why)


class ChatRequests(unittest.TestCase):
    BASE = "http://127.0.0.1:8088/v1"

    def test_chat_carries_the_headers_routing_mode_and_timeout(self) -> None:
        fake = FakeRuntime({self.BASE: "online"})
        nightshift = gen.NightshiftGenerator(TOKEN, "127.0.0.1:8088/healthz", opener=fake)
        self.assertEqual(nightshift.name, "nightshift:meterless/auto")
        self.assertEqual(nightshift.provider, "meterless")
        self.assertEqual(nightshift.timeout_s, 180.0)
        result = nightshift.complete("critic-r1", "You are the second-opinion Critic. Reply [] if fine.",
                                     max_tokens=64, temperature=0.0, timeout=nightshift.timeout_s)
        call = fake.chats()[0]
        self.assertEqual((call["method"], call["url"]), ("POST", f"{self.BASE}/chat/completions"))
        self.assertEqual(call["headers"]["authorization"], f"Bearer {TOKEN}")
        self.assertEqual(call["headers"]["meterless-app"], "x402-adoption-investigator")
        self.assertEqual(call["headers"]["meterless-scope"], "user")
        self.assertEqual(call["timeout"], 180.0)
        self.assertEqual(fake.requests[0]["body"]["model"], "meterless/auto")
        self.assertIsInstance(result.text, str)
        self.assertEqual((result.provider, result.requested_model, result.model), ("meterless", "meterless/auto", BACKEND_MODEL))
        self.assertNotIn(TOKEN, json.dumps(nightshift.mount_info))

    def test_nightshift_model_selects_another_routing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = live_config({"NIGHTSHIFT_BASE_URL": self.BASE, "NIGHTSHIFT_TOKEN": TOKEN,
                                  "NIGHTSHIFT_MODEL": "meterless/deep"}, Path(tmp))
            mounted = gen.build_generator(config, opener=FakeRuntime({self.BASE: "online"}))
        self.assertEqual(mounted.name, "nightshift:meterless/deep")

    def test_an_error_body_never_carries_the_token_into_the_exception(self) -> None:
        def echo_token(request: Any, timeout: Optional[float] = None) -> io.BytesIO:
            raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, io.BytesIO(f"bad token {TOKEN}".encode()))
        nightshift = gen.NightshiftGenerator(TOKEN, self.BASE, opener=echo_token)
        with self.assertRaises(gen.GeneratorError) as caught:
            nightshift.complete("planner", "x", max_tokens=8, temperature=0.0, timeout=1.0)
        self.assertNotIn(TOKEN, str(caught.exception))


class ProviderSelection(unittest.TestCase):
    """Live mode: Nightshift > OPENAI_API_KEY > rules/templates; sample mode: no detection at all."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def mount(self, environ: dict[str, str], fake: FakeRuntime) -> Any:
        return gen.build_generator(live_config(environ, self.dir), opener=fake)

    def test_auto_detected_nightshift_wins_over_the_openai_env(self) -> None:
        fake = FakeRuntime({"http://localhost:8090/v1": "online"})
        mounted = self.mount({"NIGHTSHIFT_TOKEN": TOKEN, "OPENAI_API_KEY": OPENAI_KEY}, fake)
        self.assertIsInstance(mounted, gen.NightshiftGenerator)
        self.assertEqual(mounted.base_url, "http://localhost:8090/v1")
        self.assertEqual(mounted.mount_info["found_by"], "auto_detect")
        self.assertEqual(mounted.mount_info["models"], MODELS)
        self.assertFalse(hasattr(mounted, "nightshift_unavailable"))

    def test_nightshift_base_url_is_probed_and_mounted(self) -> None:
        fake = FakeRuntime({"http://127.0.0.1:8088/v1": "online"})
        mounted = self.mount({"NIGHTSHIFT_BASE_URL": "127.0.0.1:8088/v1/models", "NIGHTSHIFT_TOKEN": TOKEN,
                              "OPENAI_API_KEY": OPENAI_KEY}, fake)
        self.assertIsInstance(mounted, gen.NightshiftGenerator)
        self.assertEqual(mounted.mount_info["found_by"], "NIGHTSHIFT_BASE_URL")
        self.assertEqual(fake.model_probes(), ["http://127.0.0.1:8088/v1/models"])

    def test_nightshift_offline_falls_back_to_the_openai_env(self) -> None:
        mounted = self.mount({"NIGHTSHIFT_TOKEN": TOKEN, "OPENAI_API_KEY": OPENAI_KEY}, FakeRuntime({}))
        self.assertIsInstance(mounted, gen.OpenAIGenerator)
        self.assertNotIsInstance(mounted, gen.NightshiftGenerator)
        self.assertEqual(mounted.name, "openai:gpt-4.1-mini")
        self.assertEqual(mounted.nightshift_unavailable["status"], "offline")
        self.assertIn("Nightshift not found", mounted.nightshift_unavailable["detail"])

    def test_nightshift_unauthorized_and_no_key_falls_back_to_rules(self) -> None:
        fake = FakeRuntime({"http://127.0.0.1:8088/v1": "online"})
        mounted = self.mount({"NIGHTSHIFT_TOKEN": "a-wrong-token"}, fake)
        self.assertEqual(mounted.name, "null")
        reason = mounted.nightshift_unavailable
        self.assertEqual(reason["status"], "unauthorized")
        self.assertIn("NIGHTSHIFT_TOKEN_FILE", reason["detail"])
        self.assertNotIn("a-wrong-token", json.dumps(reason))

    def test_explicit_base_url_offline_is_reported_as_configured(self) -> None:
        mounted = self.mount({"NIGHTSHIFT_BASE_URL": "http://127.0.0.1:8088", "OPENAI_API_KEY": OPENAI_KEY},
                             FakeRuntime({"http://127.0.0.1:8088/v1": 503}))
        self.assertEqual(mounted.name, "openai:gpt-4.1-mini")
        self.assertIn("(NIGHTSHIFT_BASE_URL) is offline: HTTP 503", mounted.nightshift_unavailable["detail"])

    def test_autodetect_off_and_no_base_url_probes_nothing(self) -> None:
        fake = FakeRuntime({"http://127.0.0.1:8088/v1": "online"})
        mounted = self.mount({"NIGHTSHIFT_AUTODETECT": "0", "OPENAI_API_KEY": OPENAI_KEY}, fake)
        self.assertEqual(mounted.name, "openai:gpt-4.1-mini")
        self.assertEqual(fake.calls, [])
        self.assertEqual(mounted.nightshift_unavailable["status"], "not_configured")

    def test_mode_resolution_counts_nightshift_variables_without_any_probe(self) -> None:
        auto = type("Args", (), {"mode": "auto", "sample": False, "out": str(self.dir)})()
        with mock.patch.object(gen, "probe_nightshift", side_effect=AssertionError("no probe while resolving the mode")):
            self.assertEqual(build_config(auto, environ={}, load_dotenv=False).mode, "sample")
            for name in ("NIGHTSHIFT_BASE_URL", "NIGHTSHIFT_TOKEN", "NIGHTSHIFT_TOKEN_FILE"):
                self.assertEqual(build_config(auto, environ={name: "x"}, load_dotenv=False).mode, "live", name)
            self.assertEqual(build_config(auto, environ={"NIGHTSHIFT_MODEL": "meterless/deep"}, load_dotenv=False).mode,
                             "sample", "the routing mode alone configures no provider")
            self.assertEqual(build_config(auto, environ={"SAMPLE_MODE": "1", "NIGHTSHIFT_BASE_URL": "x"},
                                          load_dotenv=False).mode, "sample")

    def test_sample_mode_never_runs_detection(self) -> None:
        boom = AssertionError("Nightshift detection must not run in sample mode")
        environ = {"SAMPLE_MODE": "1", "NIGHTSHIFT_BASE_URL": "http://127.0.0.1:8088/v1", "NIGHTSHIFT_TOKEN": TOKEN}
        payload = json.loads((REPO_ROOT / "examples" / "01-seller-audit-textokit" / "input.json").read_text(encoding="utf-8"))
        auto = type("Args", (), {"mode": "auto", "sample": False, "out": str(self.dir)})()
        forced = type("Args", (), {"mode": "sample", "sample": True, "out": str(self.dir)})()
        with mock.patch.object(gen, "detect_nightshift", side_effect=boom), \
                mock.patch.object(gen, "probe_nightshift", side_effect=boom):
            for args in (auto, forced):
                config = build_config(args, environ=environ, load_dotenv=False)
                self.assertEqual(config.mode, "sample")
                self.assertEqual(gen.build_generator(config, "01-seller-audit-textokit").name, "null")
            envelope = runner.run(payload, build_config(auto, environ=environ, load_dotenv=False), echo_stdout=False)
        self.assertEqual(envelope["status"], "ok", envelope.get("error"))
        self.assertEqual(envelope["output"]["mounts"]["generator"], "null")
        events = read_jsonl(self.dir / envelope["run_id"] / "run.jsonl")
        self.assertEqual(events[0]["type"], "guard")
        self.assertFalse([e for e in events if e["type"] == "recovery" and e["payload"]["reason"] == "nightshift_unavailable"])
        self.assertNotIn("generator_mount", events[1]["payload"])


class RunnerMounts(unittest.TestCase):
    """build_context in live mode (no pipeline, so no source is fetched): what run.jsonl says about the mount. The
    executor is a stand-in mounted as Zetaris, so every recovery and fallback seen here is the generator's."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.payload = json.loads((REPO_ROOT / "examples" / "03-payer-dossier-onchain" / "input.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def context(self, environ: dict[str, str], fake: FakeRuntime, **flags: Any) -> Any:
        config = live_config(environ, self.dir, **flags)
        run_id, inp = parse_request(self.payload)
        executor = types.SimpleNamespace(name="zetaris", version="stand-in", fallback_reason=None, cleanup=lambda: None)
        with mock.patch.object(runner, "build_generator", functools.partial(gen.build_generator, opener=fake)),                 mock.patch.object(runner, "build_executor", lambda *args, **kwargs: executor):
            ctx = runner.build_context(run_id, inp, config, echo_stdout=False)
        ctx.log.close()
        return ctx

    def recoveries(self, ctx: Any) -> list[dict]:
        path = ctx.run_dir / "run.jsonl"     # live mode writes no line before run_start unless a mount recovers
        return [e for e in read_jsonl(path) if e["type"] == "recovery"] if path.is_file() else []

    def test_nightshift_online_is_the_mount_with_no_generator_recovery(self) -> None:
        ctx = self.context({"NIGHTSHIFT_TOKEN": TOKEN, "OPENAI_API_KEY": OPENAI_KEY},
                           FakeRuntime({"http://127.0.0.1:8088/v1": "online"}))
        self.assertEqual(ctx.mounts.generator, "nightshift:meterless/auto")
        reasons = [r["payload"]["reason"] for r in self.recoveries(ctx)]
        self.assertNotIn("nightshift_unavailable", reasons)
        self.assertNotIn("generator_fallback", reasons)

    def test_unauthorized_nightshift_logs_the_recovery_and_mounts_openai(self) -> None:
        ctx = self.context({"NIGHTSHIFT_TOKEN": "a-wrong-token", "OPENAI_API_KEY": OPENAI_KEY},
                           FakeRuntime({"http://127.0.0.1:8088/v1": "online"}))
        self.assertEqual(ctx.mounts.generator, "openai:gpt-4.1-mini")
        found = [r for r in self.recoveries(ctx) if r["payload"]["reason"] == "nightshift_unavailable"]
        self.assertEqual(len(found), 1)
        payload = found[0]["payload"]
        self.assertEqual((payload["from"], payload["to"], payload["status"]), ("nightshift", "openai:gpt-4.1-mini", "unauthorized"))
        self.assertEqual(validate(found[0], load_schema("events")), [])
        self.assertNotIn("generator_fallback", [r["payload"]["reason"] for r in self.recoveries(ctx)])
        self.assertFalse(ctx.fallback_used, "a model still runs: the OpenAI fallback alone does not make the run partial")
        assert_no_secret(self, ctx.run_dir, "a-wrong-token", OPENAI_KEY)

    def test_no_provider_at_all_logs_both_recoveries_and_require_generator_fails(self) -> None:
        ctx = self.context({}, FakeRuntime({}))
        self.assertEqual(ctx.mounts.generator, "null")
        reasons = [r["payload"]["reason"] for r in self.recoveries(ctx)]
        self.assertEqual(reasons, ["nightshift_unavailable", "generator_fallback"])
        self.assertTrue(ctx.fallback_used)
        with self.assertRaises(InvestigatorError) as caught:
            self.context({"NIGHTSHIFT_TOKEN": TOKEN}, FakeRuntime({"http://127.0.0.1:8088/v1": 401}), require_generator=True)
        self.assertEqual(caught.exception.code, "E_GENERATOR")
        self.assertIn("unauthorized", caught.exception.message)
        self.assertEqual(caught.exception.details["nightshift"], "unauthorized")
        self.assertNotIn(TOKEN, json.dumps(caught.exception.to_json()))


class TracedCalls(unittest.TestCase):
    """A full pipeline on example 03's fixtures with NightshiftGenerator mounted on the fake runtime: every call is
    traced with the provider and the model that answered, and the token is in no file."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.fake = FakeRuntime({"http://127.0.0.1:8088/v1": "online"})
        payload = json.loads((REPO_ROOT / "examples" / "03-payer-dossier-onchain" / "input.json").read_text(encoding="utf-8"))
        config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": str(out)})(), load_dotenv=False)
        mount = lambda *args, **kwargs: gen.NightshiftGenerator(TOKEN, "127.0.0.1:8088", opener=cls.fake,  # noqa: E731
                                                                 models=MODELS, found_by="auto_detect")
        with mock.patch.object(runner, "build_generator", mount):
            cls.envelope = runner.run(payload, config, echo_stdout=False)
        cls.run_dir = out / cls.envelope["run_id"]
        cls.events = read_jsonl(cls.run_dir / "run.jsonl")
        cls.calls = read_jsonl(cls.run_dir / "generator.jsonl")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_the_run_used_nightshift_for_every_generator_backed_role(self) -> None:
        self.assertIn(self.envelope["status"], ("ok", "partial"), self.envelope.get("error"))
        self.assertEqual(validate(self.envelope, load_schema("output")), [])
        output = self.envelope["output"]
        self.assertEqual(output["mounts"]["generator"], "nightshift:meterless/auto")
        used = {a["role"]: a["generator_used"] for a in output["agents"] if a["generator_backed"]}
        self.assertEqual(set(used.values()), {"nightshift:meterless/auto"})
        chats = self.fake.chats()
        self.assertEqual(len(chats), len(self.calls))
        for call in chats:
            self.assertEqual(call["headers"]["meterless-app"], "x402-adoption-investigator")
            self.assertEqual(call["headers"]["meterless-scope"], "user")
            self.assertEqual(call["headers"]["authorization"], f"Bearer {TOKEN}")
            self.assertLessEqual(call["timeout"], 180.0)

    def test_each_call_is_a_generate_event_naming_provider_and_model(self) -> None:
        schema = load_schema("events")
        for line in self.events:
            self.assertEqual(validate(line, schema), [], line)
        generated = [e for e in self.events if e["type"] == "generate"]
        self.assertEqual(len(generated), len(self.calls))
        self.assertEqual({e["agent"] for e in generated}, {"planner", "writer", "critic"})
        payload_schema = schema["$defs"]["payloads"]["generate"]
        for event, line in zip(generated, self.calls):
            self.assertEqual(validate(event["payload"], payload_schema, "$.payload", schema), [])
            self.assertEqual(event["payload"]["provider"], "meterless")
            self.assertEqual(event["payload"]["requested_model"], "meterless/auto")
            self.assertEqual(event["payload"]["model"], BACKEND_MODEL)
            self.assertEqual(line["seq"], event["seq"], "generator.jsonl repeats the seq of its generate event")
            self.assertEqual((line["provider"], line["model"]), ("meterless", BACKEND_MODEL))
            transcript = json.loads((self.run_dir / line["transcript"]).read_text(encoding="utf-8"))
            self.assertEqual((transcript["provider"], transcript["requested_model"], transcript["model"]),
                             ("meterless", "meterless/auto", BACKEND_MODEL))
        start = next(e for e in self.events if e["type"] == "run_start")
        self.assertEqual(start["payload"]["generator_mount"]["routing_mode"], "meterless/auto")
        self.assertEqual(start["payload"]["generator_mount"]["base_url"], "http://127.0.0.1:8088/v1")

    def test_the_token_is_in_no_log_transcript_or_output(self) -> None:
        assert_no_secret(self, self.run_dir, TOKEN)
        self.assertNotIn(TOKEN, json.dumps(self.envelope))

    def test_recorded_replay_keeps_the_provider_and_model_of_the_original_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = json.loads((REPO_ROOT / "examples" / "03-payer-dossier-onchain" / "input.json").read_text(encoding="utf-8"))
            config = build_config(type("Args", (), {"mode": "sample", "sample": True, "out": tmp})(), load_dotenv=False)
            replay = lambda *args, **kwargs: gen.RecordedGenerator(self.run_dir / "transcripts")  # noqa: E731
            with mock.patch.object(runner, "build_generator", replay):
                envelope = runner.run(payload, config, echo_stdout=False)
            events = [e for e in read_jsonl(Path(tmp) / envelope["run_id"] / "run.jsonl") if e["type"] == "generate"]
        self.assertEqual(envelope["output"]["mounts"]["generator"], "recorded")
        self.assertTrue(events)
        for event in events:
            self.assertTrue(event["payload"]["recorded"])
            self.assertEqual((event["payload"]["generator"], event["payload"]["provider"], event["payload"]["model"]),
                             ("recorded", "meterless", BACKEND_MODEL))


if __name__ == "__main__":
    unittest.main()
